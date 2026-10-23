-- Migration 022 -- permanent and transient bounces (operator decision 2026-10-24).
--
-- Two rules, both about what a bounce means (Master Ref Section 9, Workflow 6):
--
--   * Only a PERMANENT bounce counts toward the Send safety brake's 3%. A delay
--     warning ("THIS IS A WARNING MESSAGE ONLY ... will be retried") is the
--     receiving server still trying, not a failed address, and must not pause
--     Send. The report's own verdict wins over the status code inside it: Zoho's
--     final report for lead 44 (2026-10-10) said "This is a permanent error."
--     about an SMTP "421 Host not reachable", and stays counted.
--
--   * A permanent bounce marks the contact it bounced from unverified
--     (contacts.verified = false). Every consumer already gates on that flag --
--     Drafting's and Follow-Ups' gate hold the draft `unverified-address`, Send
--     refuses `contact-unverified`, and migration 020's trigger refuses an auto
--     approval -- so the dead address is never used again until a person enters
--     a working one. Scoring never reads `contacts`.
--
-- Mailbox Watch's classifier decides the kind (bounceKind() in
-- code_classify_reply.js); this migration stores it and acts on it:
--
--   * inbound_messages.bounce_kind -- 'permanent' | 'transient' on a bounce row,
--     NULL on every other row (and on a bounce recorded before this migration
--     until backfill_bounce_kind.py classifies it).
--
--   * outreach_log.bounce_kind -- the kind of the bounce on that send, written by
--     Record Inbound beside outcome = 'bounced'. A permanent report sticks: a
--     later delay warning cannot downgrade it, and a permanent report after a
--     delay upgrades it (lead 44: a delay on 2026-10-06, the final failure on
--     2026-10-10).
--
--   * send_brake_stats() -- a bounced send counts unless its bounce is known to
--     be only a delay (bounce_kind = 'transient'). NULL still counts: a bounce
--     written by a Mailbox Watch older than this migration, or one nothing
--     classified, keeps erring towards pausing, as the brake did before.
--
--   * bounce_names_address() and the trigger unverify_contact_on_hard_bounce --
--     a permanent bounce matched to a lead unverifies that lead's contact only
--     when the bounce is about THAT address: one of the recipients of the sent
--     message it threads to (mailbox_sent), or the address written in the
--     report itself (the four bounces before 2026-10-09 carry no thread ids,
--     and each names its address). A contact whose address changed since the
--     send is left alone. On the table, not in the workflow, so the backfill of
--     the old bounces and every future writer get the same rule.
--
-- Additive and idempotent: the Mailbox Watch running before this migration keeps
-- working (it writes no kind, so its bounces count as before), and re-applying
-- the file changes nothing.

ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS bounce_kind text;
ALTER TABLE outreach_log     ADD COLUMN IF NOT EXISTS bounce_kind text;

-- Dropped and re-added rather than "if not exists", so re-applying the file
-- always leaves exactly these rules (the mutation tests depend on it).
ALTER TABLE inbound_messages DROP CONSTRAINT IF EXISTS inbound_bounce_kind_valid;
ALTER TABLE inbound_messages ADD CONSTRAINT inbound_bounce_kind_valid
  CHECK (bounce_kind IS NULL OR (bounce_kind IN ('permanent', 'transient') AND classification = 'bounce'));
ALTER TABLE outreach_log DROP CONSTRAINT IF EXISTS outreach_bounce_kind_valid;
ALTER TABLE outreach_log ADD CONSTRAINT outreach_bounce_kind_valid
  CHECK (bounce_kind IS NULL OR bounce_kind IN ('permanent', 'transient'));

-- Migration 021's function, with one change: the `counted` CTE. Everything else
-- -- the window, the claim rule, the clears -- is 021's text unchanged.
CREATE OR REPLACE FUNCTION send_brake_stats(as_of timestamptz, window_days int)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
  WITH since AS (
    SELECT greatest(as_of - make_interval(days => window_days),
                    coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'bounce-rate'),
                             '-infinity'::timestamptz)) AS bounce_from,
           coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'spam-complaint'),
                    '-infinity'::timestamptz) AS complaint_from
  ),
  sends AS (
    SELECT o.lead_id, o.outcome, o.bounce_kind, l.domain
      FROM outreach_log o
      JOIN leads l ON l.id = o.lead_id, since
     WHERE o.channel = 'email'
       AND o.message_id IS NOT NULL          -- a confirmed send, not a claim
       AND o.sent_at >= since.bounce_from
       AND o.sent_at <= as_of
  ),
  counted AS (
    -- Migration 022: a delay warning is not a bounce for the brake; an
    -- unclassified one (NULL) still is.
    SELECT * FROM sends WHERE outcome = 'bounced' AND bounce_kind IS DISTINCT FROM 'transient'
  ),
  complaints AS (
    SELECT i.message_id, i.from_addr, i.subject, i.lead_id, i.processed_at
      FROM inbound_messages i, since
     WHERE i.spam_complaint
       AND i.processed_at > since.complaint_from
       AND i.processed_at <= as_of
  )
  SELECT jsonb_build_object(
    'bounce_from', (SELECT bounce_from FROM since),
    'sends',       (SELECT count(*) FROM sends),
    'bounced',     (SELECT count(*) FROM counted),
    'bounced_domains', (SELECT coalesce(jsonb_agg(DISTINCT domain), '[]'::jsonb) FROM counted),
    'delayed',     (SELECT count(*) FROM sends WHERE outcome = 'bounced' AND bounce_kind = 'transient'),
    'complaints',  (SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'message_id', message_id, 'from', from_addr, 'subject', subject, 'lead_id', lead_id,
                       'at', processed_at) ORDER BY processed_at), '[]'::jsonb) FROM complaints))
$fn$;

-- Is this bounce about this address? Defined once: the trigger below acts on it
-- and Mailbox Watch's Record Inbound reports it to the operator.
CREATE OR REPLACE FUNCTION bounce_names_address(thread_ids text[], bounce_text text, address text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
  SELECT coalesce(btrim(address), '') <> '' AND (
       EXISTS (SELECT 1
                 FROM mailbox_sent ms, unnest(ms.recipients) r
                WHERE ms.message_id = ANY (coalesce(thread_ids, '{}'::text[]))
                  AND lower(r) = lower(btrim(address)))
    -- The address as a whole token in the report: "info@advinus.com, ERROR CODE"
    -- and "... to info@advinus.com." name info@advinus.com; "xinfo@advinus.com"
    -- and "info@advinus.com.au" do not.
    OR lower(coalesce(bounce_text, '')) ~ ('(^|[^a-z0-9._%+-])'
         || regexp_replace(lower(btrim(address)), '([.+*?^$(){}|\[\]\\-])', '\\\1', 'g')
         || '($|[^a-z0-9._%+-]|\.($|[^a-z0-9-]))'))
$fn$;

CREATE OR REPLACE FUNCTION unverify_contact_on_hard_bounce()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  UPDATE contacts c
     SET verified = false
   WHERE c.lead_id = NEW.lead_id
     AND c.verified
     AND bounce_names_address(NEW.thread_ids, NEW.body_excerpt, c.email);
  RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS unverify_contact_on_hard_bounce_trg ON inbound_messages;
CREATE TRIGGER unverify_contact_on_hard_bounce_trg
  AFTER INSERT OR UPDATE OF bounce_kind ON inbound_messages
  FOR EACH ROW
  WHEN (NEW.bounce_kind = 'permanent' AND NEW.lead_id IS NOT NULL)
  EXECUTE FUNCTION unverify_contact_on_hard_bounce();

COMMENT ON COLUMN inbound_messages.bounce_kind IS
  'Migration 022: permanent | transient on a bounce (Mailbox Watch''s bounceKind()); NULL otherwise. '
  'A permanent bounce marks the contact it names unverified (trigger unverify_contact_on_hard_bounce_trg).';
COMMENT ON COLUMN outreach_log.bounce_kind IS
  'Migration 022: the kind of the bounce on this send (permanent sticks). send_brake_stats() does not count '
  'a transient one; NULL counts.';

-- Migration 028 -- the operator is emailed about a held draft only when a person can decide it (2026-10-30).
--
-- WHY. On 2026-10-30 21:00-21:02 PKT the operator got 20+ emails "[Nova Scout] still
-- waiting: <company> -- NS-<code>", and the reply assistant's reminder lane would have
-- sent 20 more every half hour: the Daily Digest gave every pending EMAIL draft a
-- one-time code (migration 018's email-hold), and every open code was re-sent every 4
-- hours. 153 of the 166 open codes were Overture drafts held by migration 026 (a source
-- hold -- an APPROVE on one of those releases a draft the hold exists to stop), 9 had no
-- address (APPROVE can do nothing for those; CONTACT can), 4 were operator holds (already
-- decided). Not one was a decision about the words.
--
-- WHAT.
--   * hold_reason_class(text) -- the one definition of a hold reason's class: lowercase,
--     the first reason before any ';', no parenthetical, no dates, then the text before
--     the first ':' -- except a db-guard reason, which keeps its first problem
--     ("db-guard: source sites-overture is held by niche research_site"), because the
--     db-guard prefix alone says nothing about why.
--   * hold_reason_classes -- every class ever seen, and `needs_person`: THE ALLOWLIST.
--     Only a class with needs_person = true gets a per-draft code in the digest. A class
--     not in the table is new: the digest flags it on a line of its own, Record Digest
--     adds it with needs_person = false, so a new hold type is silent by default. To
--     allow one: UPDATE hold_reason_classes SET needs_person = true WHERE class = '...';
--   * hold_needs_person(text) -- true only for an allowlisted class.
--   * reply_approval_issue() -- refuses (returns NULL) an email-hold code for a draft that
--     is not a pending email whose hold needs a person. The digest's own filter says the
--     same; this is the table saying it for every caller.
--   * reply_approval_withdraw_holds() -- the sanctioned reap: deletes every open, unused
--     email-hold code whose draft no longer needs a person. 018's reasoning holds: an
--     unused code never acted on anything, and any attempt to use one is recorded in
--     operator_commands, so deleting it loses nothing. Load Digest runs it on the tick
--     that sends; this migration runs it once.
--
-- Seeded allowlist (needs_person = true): `auto-approve-off` (the operator's own switch:
-- every email waits for a person to read it) and `claim-check` (the composer's claim check,
-- retired 2026-10-19; kept so a revival is a decision, not a flood). Everything else --
-- no-address, unverified-address, no-send-clock, blocklisted, smtp-rejected, every db-guard,
-- operator holds, LinkedIn -- is counted in the digest and never coded.
--
-- Re-runnable: CREATE OR REPLACE / IF NOT EXISTS / ON CONFLICT DO NOTHING throughout.
BEGIN;

CREATE OR REPLACE FUNCTION hold_reason_class(p_reason text)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
  WITH r AS (
    SELECT btrim(regexp_replace(regexp_replace(regexp_replace(
             split_part(lower(coalesce(p_reason, '')), ';', 1),
             '\([^)]*\)', '', 'g'),                    -- "(precision audit, 2026-10-30)"
             '\d{4}-\d{2}-\d{2}', '', 'g'),            -- "operator hold 2026-10-30"
             '\s+', ' ', 'g')) AS t
  )
  SELECT CASE
           WHEN t = '' THEN 'none'
           WHEN t LIKE 'db-guard:%' THEN btrim(split_part(t, ',', 1))
           ELSE btrim(split_part(t, ':', 1))
         END
    FROM r;
$fn$;

COMMENT ON FUNCTION hold_reason_class(text) IS
  'The class of a drafts.hold_reason: what the Daily Digest counts by, and what hold_reason_classes '
  'allowlists (migration 028).';

CREATE TABLE IF NOT EXISTS hold_reason_classes (
  class          text PRIMARY KEY,
  needs_person   boolean NOT NULL DEFAULT false,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  note           text
);

COMMENT ON TABLE hold_reason_classes IS
  'Every hold-reason class seen (hold_reason_class()). needs_person = true is the allowlist: only those '
  'held emails get a per-draft code in the Daily Digest. A class not here is new: flagged once by the '
  'digest, then added with needs_person = false (migration 028).';

-- What is in the queue today, plus every reason the gate and the trigger can write:
-- all seen, none coded.
INSERT INTO hold_reason_classes (class, note)
SELECT DISTINCT hold_reason_class(hold_reason), 'seen when migration 028 was applied'
  FROM drafts
 WHERE hold_reason IS NOT NULL
ON CONFLICT (class) DO NOTHING;

INSERT INTO hold_reason_classes (class, note) VALUES
  ('no-address',         'Workflow 5: no deliverable address. CONTACT fixes it, APPROVE cannot (operator: no address, no email to me).'),
  ('unverified-address', 'Workflow 5: the address is not verified. CONTACT fixes it, APPROVE cannot.'),
  ('no-send-clock',      'Workflow 5: the country has no business-hours clock; it can never be sent.'),
  ('blocklisted',        'Workflow 5: never sent.'),
  ('smtp-rejected',      'Send: the mail server refused the address. An address problem, not the words.'),
  ('operator hold',      'Held by the operator by hand (migration 027 and later): already decided.'),
  ('linkedin',           'Sent by hand (Section 6).'),
  ('none',               'No hold reason: a pending reply (its own review email and reminder, Workflow 7) or a draft from before auto-approval.')
ON CONFLICT (class) DO NOTHING;

INSERT INTO hold_reason_classes (class, needs_person, note) VALUES
  ('auto-approve-off', true, 'settings.auto_approve_email is off: the operator reads every email before it goes.'),
  ('claim-check',      true, 'The composer''s claim check (retired 2026-10-19): a question about the words.')
ON CONFLICT (class) DO UPDATE SET needs_person = true;

CREATE OR REPLACE FUNCTION hold_needs_person(p_reason text)
RETURNS boolean LANGUAGE sql STABLE AS $fn$
  SELECT coalesce((SELECT k.needs_person FROM hold_reason_classes k WHERE k.class = hold_reason_class(p_reason)),
                  false);
$fn$;

COMMENT ON FUNCTION hold_needs_person(text) IS
  'True only when the hold reason''s class is allowlisted (hold_reason_classes.needs_person). Unknown = false: '
  'a new hold type is silent until someone allowlists it (migration 028).';

-- 018's function, with one refusal added at the top. Everything after it is unchanged.
CREATE OR REPLACE FUNCTION reply_approval_issue(
  p_kind     text,
  p_draft_id bigint,
  p_lead_id  bigint,
  p_inbound  text    DEFAULT NULL,
  p_hours    numeric DEFAULT 48
) RETURNS text LANGUAGE plpgsql AS $fn$
DECLARE
  found text;
  tries int := 0;
BEGIN
  -- Migration 028: an email-hold code only for a pending email a person can decide.
  -- A source hold, a missing address, an operator hold and every new hold type get
  -- none: there is nothing for an APPROVE to decide, or an APPROVE would undo a hold.
  IF p_kind = 'email-hold' AND NOT EXISTS (
       SELECT 1 FROM drafts d
        WHERE d.id = p_draft_id AND d.status = 'pending' AND d.channel = 'email'
          AND hold_needs_person(d.hold_reason)) THEN
    RETURN NULL;
  END IF;

  -- A code that expired without ever being used could never have acted on
  -- anything, and it holds the one-open-code slot for its draft until it goes.
  -- Any ATTEMPT to use it is recorded in operator_commands, which is the record
  -- that matters, so reaping it loses nothing and unblocks the draft.
  DELETE FROM reply_approvals
   WHERE draft_id = p_draft_id AND used_at IS NULL AND expires_at <= now();

  SELECT code INTO found
    FROM reply_approvals
   WHERE draft_id = p_draft_id AND used_at IS NULL AND expires_at > now()
   LIMIT 1;
  IF found IS NOT NULL THEN
    RETURN found;
  END IF;

  LOOP
    tries := tries + 1;
    INSERT INTO reply_approvals (code, kind, draft_id, lead_id, inbound_message_id, expires_at)
    SELECT reply_approval_new_code(), p_kind, p_draft_id, p_lead_id, p_inbound,
           now() + (p_hours || ' hours')::interval
    ON CONFLICT (code) DO NOTHING
    RETURNING code INTO found;
    EXIT WHEN found IS NOT NULL OR tries >= 5;
  END LOOP;
  IF found IS NULL THEN
    RAISE EXCEPTION 'could not mint an unused reply approval code in % tries', tries;
  END IF;
  RETURN found;
END;
$fn$;

COMMENT ON FUNCTION reply_approval_issue(text, bigint, bigint, text, numeric) IS
  'The code for this draft: the open one if there is one, otherwise a new one '
  '(reaping an expired, never-used code first -- it holds the one-open-code '
  'index slot). Every caller uses this rather than INSERTing its own. Since '
  'migration 028 it returns NULL for an email-hold whose draft is not a pending '
  'email with an allowlisted hold (hold_needs_person).';

CREATE OR REPLACE FUNCTION reply_approval_withdraw_holds()
RETURNS integer LANGUAGE sql AS $fn$
  WITH gone AS (
    DELETE FROM reply_approvals a
     WHERE a.kind = 'email-hold'
       AND a.used_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM drafts d
                        WHERE d.id = a.draft_id AND d.status = 'pending' AND d.channel = 'email'
                          AND hold_needs_person(d.hold_reason))
    RETURNING 1)
  SELECT count(*)::int FROM gone;
$fn$;

COMMENT ON FUNCTION reply_approval_withdraw_holds() IS
  'Deletes every unused email-hold code whose draft no longer needs a person (migration 028). An unused code '
  'never acted on anything; any attempt to use one is in operator_commands. Returns how many went.';

SELECT reply_approval_withdraw_holds() AS withdrawn_by_028;

COMMIT;

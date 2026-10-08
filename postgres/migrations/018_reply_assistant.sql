-- Migration 018 -- the reply assistant's rules on the table (2026-10-09).
--
-- Migration 017 built the reply assistant's foundations: the thread headers, the
-- one-time `reply_approvals` codes, and `operator_commands` with Section 5's
-- authentication rule as a CHECK. This migration adds the four things the
-- workflow that uses them needs, and it adds them HERE, on the table, for
-- migration 014's reason -- a rule on `drafts` holds for every writer, not only
-- for the workflow that was careful:
--
--   1. reply_approval_new_code() / reply_approval_issue() -- the one definition
--      of how a code is minted and reused. Two callers need a code (the Reply
--      Assistant for a drafted reply, the Daily Digest for a held email), and
--      the partial unique index 017 put on `draft_id WHERE used_at IS NULL`
--      means neither may simply INSERT: a code already open for that draft must
--      be REUSED, and an expired one that was never used must be reaped first or
--      it blocks its own replacement for ever. That is three rules a caller can
--      get wrong, so no caller gets to express them.
--
--      This supersedes 017's comment pointing at a `code_reply_code.js`: the
--      generator is SQL, because both callers are SQL statements and the
--      alphabet, the uniqueness and the 48-hour window belong to the table that
--      CHECKs them. No such JS file was written.
--
--   2. drafts_approval_rules() gains the reply branch -- exactly as migration
--      016 taught it the `no-send-clock` note. Two additions:
--        * a `reply/` draft may only be approved while a one-time code for it is
--          open, or has already been used to approve or edit it. This is the
--          whole security model of Workflow 7 expressed where it cannot be
--          bypassed: approving a drafted reply by hand in the NocoDB grid, or
--          from psql, is held, because the operator's APPROVE is the gate and
--          that gate is authenticated (SPF + DKIM + the operator's own address,
--          017's CHECK). Rejecting one by hand still works, and always should.
--        * `approved_by = 'auto'` on a reply is named as the problem it is. A
--          reply is never auto-approved: no claim check runs on one, so it would
--          otherwise be held for 'no passing claim check' -- the right answer
--          with a misleading reason, which is the shape 016 fixed.
--
--   3. A replied lead's pending LinkedIn DM is retired automatically. Section 13
--      has carried this as a decision to make since 2026-10-07: lead 26 answered
--      by email and DM 111 stayed `pending` in the exceptions queue, so a human
--      working that queue could send a cold DM to a company that had already
--      replied. A reply has always killed the follow-up SEQUENCE (Section 9,
--      Workflow 6) and was never specified to touch the first-touch DM. It is
--      decided here, as the first of the two options Section 13 offered, and it
--      is a trigger on `inbound_messages` rather than a line in Record Inbound's
--      statement, because the hazard is a human reading a stale queue and that
--      hazard does not care which writer recorded the reply.
--
--      An `approved` LinkedIn draft is retired too: it is sent by hand (Section
--      6), so "approved" there means "a person may send this at any moment",
--      which is exactly the thing to stop.
--
--   4. The one-off the trigger in 3 is too late for: lead 26's DM 111, and any
--      other pending or approved LinkedIn draft whose lead has already replied.
--
-- Re-running this file is a no-op.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Minting a code
-- ---------------------------------------------------------------------------

-- The alphabet migration 017's CHECK pins: upper case, no I, L, O, U, 0 or 1, so
-- a code survives a mail client's capitalisation and a human retyping it off a
-- phone screen.
--
-- Ten characters drawn uniformly: each takes one byte of md5(gen_random_uuid()),
-- and 256 is exactly 8 x 32, so `% 32` carries no modulo bias. gen_random_uuid()
-- is core in Postgres 13+ (no pgcrypto extension needed) and is a v4 UUID, so
-- two of them offer 244 bits to draw 50 bits from. That matters: this code is a
-- bearer token sitting in a mailbox, and `random()` -- seeded per session and
-- recoverable from a couple of outputs -- would not do.
CREATE OR REPLACE FUNCTION reply_approval_new_code()
RETURNS text LANGUAGE sql VOLATILE AS $fn$
  WITH h AS (
    SELECT md5(gen_random_uuid()::text || gen_random_uuid()::text) AS hex
  )
  SELECT 'NS-' || string_agg(
           substr('23456789ABCDEFGHJKMNPQRSTVWXYZ23',
                  1 + (('x' || substr(h.hex, 1 + (i - 1) * 2, 2))::bit(8)::int % 32), 1),
           '' ORDER BY i)
    FROM h, generate_series(1, 10) AS i;
$fn$;

COMMENT ON FUNCTION reply_approval_new_code() IS
  'One code in the alphabet reply_approvals_code_shaped CHECKs. Uniqueness is '
  'the primary key''s job -- reply_approval_issue() retries on a collision.';

-- Hand out the code for one draft: reuse the open one, reap a dead one, or mint.
--
-- `p_hours` is bounded by the table (CHECK reply_approvals_window, at most 48),
-- so this cannot widen its own window either; it is a parameter only so a test
-- can ask for a shorter one.
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
  'index slot). Every caller uses this rather than INSERTing its own.';

-- ---------------------------------------------------------------------------
-- 2. The approval rules learn about a reply
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION drafts_approval_rules()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
  was      text := CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END;
  problems text[] := '{}';
  codes    text[];
  missing  text[];
BEGIN
  -- Leaving approval: approved_by describes the current approval, nothing else.
  IF NEW.status NOT IN ('approved', 'sent') THEN
    NEW.approved_by := NULL;
    NEW.approved_at := NULL;
    -- Send's Revert Claim hands a recipient-refused draft back to a human with
    -- '+smtp-rejected' on its variant; say so where the digest looks.
    IF was = 'sent' AND NEW.status = 'pending' AND coalesce(NEW.variant, '') LIKE '%smtp-rejected%' THEN
      NEW.hold_reason := 'smtp-rejected: the mail server refused the address';
    END IF;
    RETURN NEW;
  END IF;

  -- A fresh approval: an insert, or a move into 'approved' from anything but
  -- 'sent'. (sent -> approved is Send handing a claim back after an account-
  -- level SMTP failure; the approval it had stands.)
  IF NEW.status = 'approved' AND (TG_OP = 'INSERT' OR coalesce(was, '') NOT IN ('approved', 'sent')) THEN
    -- Migration 018, checked before anything else: a drafted reply is approved
    -- by a one-time code the operator quotes back from an authenticated message
    -- (Section 9, Workflow 7) and by nothing else -- not by a click in the
    -- NocoDB grid, not by an UPDATE in psql, not by the workflow that wrote it.
    -- A code that is open, or that was already used to approve or edit THIS
    -- draft, is the only way through. (The "already used" half is what lets the
    -- operator-command statement mark the code used and flip the draft in one
    -- statement, in either order, and what lets Send hand a claim back later.)
    IF coalesce(NEW.variant, '') LIKE 'reply/%'
       AND NOT EXISTS (SELECT 1 FROM reply_approvals a
                        WHERE a.draft_id = NEW.id
                          AND ((a.used_at IS NOT NULL AND a.outcome IN ('approved', 'edited'))
                               OR (a.used_at IS NULL AND a.expires_at > now()))) THEN
      NEW.status      := 'pending';
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
      NEW.hold_reason := 'db-guard: a reply is approved only by its one-time code, from the operator '
                      || 'address with SPF and DKIM passing (Workflow 7)';
      RETURN NEW;
    END IF;

    IF NEW.approved_by = 'auto' THEN
      IF NOT auto_approve_email_enabled() THEN
        problems := array_append(problems, 'auto-approve is off');
      END IF;
      IF NEW.channel IS DISTINCT FROM 'email' THEN
        problems := array_append(problems, 'not an email');
      END IF;
      IF coalesce(NEW.variant, '') LIKE 'low-context%' THEN
        problems := array_append(problems, 'low-context');
      END IF;
      IF coalesce(NEW.variant, '') LIKE 'no-send-clock%' THEN
        problems := array_append(problems, 'no-send-clock');
      END IF;
      -- Migration 018: a reply is never approved by a workflow. No claim check
      -- runs on one -- the operator reads it -- so without this it would be held
      -- for 'no passing claim check': the right answer for the wrong reason,
      -- which is the same shape migration 016 fixed.
      IF coalesce(NEW.variant, '') LIKE 'reply/%' THEN
        problems := array_append(problems, 'reply');
      END IF;
      IF position('+' IN coalesce(NEW.variant, '')) > 0 THEN
        problems := array_append(problems, ('rule tags ' || split_part(NEW.variant, '+', 2)));
      END IF;
      IF NEW.edited_body IS NOT NULL THEN
        problems := array_append(problems, 'edited text was never checked');
      END IF;
      -- 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1' -> {D2,ANG-HOURS,BEN-SEE,PR-BOTH,A1}.
      -- A low-context variant ('low-context/role-inbox') records no claim codes.
      codes := CASE WHEN coalesce(NEW.variant, '') LIKE 'low-context%'
                      OR coalesce(NEW.variant, '') LIKE 'no-send-clock%' THEN '{}'::text[]
                    ELSE array_remove(string_to_array(
                           split_part(split_part(coalesce(NEW.variant, ''), '+', 1), '/', 2), '.'), '') END;
      IF coalesce(NEW.variant, '') LIKE 'low-context%'
         OR coalesce(NEW.variant, '') LIKE 'no-send-clock%' THEN
        NULL;  -- already held above; neither note has claims to look up
      ELSIF coalesce(cardinality(codes), 0) = 0 THEN
        problems := array_append(problems, 'no claim codes');
      ELSE
        SELECT array_agg(c ORDER BY c) INTO missing
          FROM unnest(codes) AS c
         WHERE NOT EXISTS (SELECT 1 FROM claims_library k WHERE k.code = c AND k.active AND k.confirmed);
        IF missing IS NOT NULL THEN
          problems := array_append(problems, ('unconfirmed ' || array_to_string(missing, ',')));
        END IF;
      END IF;
      IF coalesce(NEW.claim_check->>'result', '') <> 'pass' THEN
        problems := array_append(problems, 'no passing claim check');
      END IF;

      IF cardinality(problems) > 0 THEN
        NEW.status      := 'pending';
        NEW.approved_by := NULL;
        NEW.approved_at := NULL;
        NEW.hold_reason := 'db-guard: ' || array_to_string(problems, ', ');
        RETURN NEW;
      END IF;
      NEW.hold_reason := NULL;
    ELSE
      NEW.approved_by := 'human';
    END IF;
    NEW.approved_at := now();
    RETURN NEW;
  END IF;

  -- Still approved, and approved by the workflow: the text it checked must be
  -- the text that goes out. Generated text rewritten underneath it goes back to
  -- a human; a human's own edit (edited_body) makes the approval theirs.
  IF TG_OP = 'UPDATE' AND was = 'approved' AND NEW.status = 'approved' AND OLD.approved_by = 'auto' THEN
    IF NEW.body IS DISTINCT FROM OLD.body OR NEW.subject IS DISTINCT FROM OLD.subject THEN
      NEW.status      := 'pending';
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
      NEW.hold_reason := 'db-guard: text changed after auto-approval';
    ELSIF NEW.edited_body IS DISTINCT FROM OLD.edited_body THEN
      NEW.approved_by := 'human';
      NEW.approved_at := now();
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

-- ---------------------------------------------------------------------------
-- 3. A reply retires the lead's LinkedIn DM
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION retire_linkedin_on_reply()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  UPDATE drafts
     SET status        = 'rejected',
         reject_reason = 'already contacted'
   WHERE lead_id = NEW.lead_id
     AND channel = 'linkedin'
     AND status IN ('pending', 'approved');
  RETURN NULL;
END;
$fn$;

COMMENT ON FUNCTION retire_linkedin_on_reply() IS
  'Section 13, decided 2026-10-09: a lead that has answered keeps no cold '
  'LinkedIn DM in the exceptions queue. A human works that queue by hand '
  '(Section 6), so a pending or approved DM there is a cold message somebody '
  'may send to a company that has already replied.';

DROP TRIGGER IF EXISTS retire_linkedin_on_reply_trg ON inbound_messages;
CREATE TRIGGER retire_linkedin_on_reply_trg
  AFTER INSERT ON inbound_messages
  FOR EACH ROW
  WHEN (NEW.lead_id IS NOT NULL AND NEW.classification IN ('reply', 'opt-out'))
  EXECUTE FUNCTION retire_linkedin_on_reply();

-- ---------------------------------------------------------------------------
-- 4. The backlog the trigger above is too late for
-- ---------------------------------------------------------------------------
--
-- Lead 26 (Pharmahungary) replied on 2026-10-07 and its DM, draft 111, has been
-- `pending` ever since. Written as a set so a database with others in the same
-- state clears them all, and so re-running changes nothing.
UPDATE drafts d
   SET status        = 'rejected',
       reject_reason = 'already contacted'
 WHERE d.channel = 'linkedin'
   AND d.status IN ('pending', 'approved')
   AND EXISTS (SELECT 1 FROM inbound_messages i
                WHERE i.lead_id = d.lead_id
                  AND i.classification IN ('reply', 'opt-out'));

COMMIT;

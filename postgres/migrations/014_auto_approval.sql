-- Migration 014 -- auto-approval of email drafts, with guardrails (2026-10-03).
--
-- Until now every draft waited for a human in the review queue (Section 9,
-- Workflow 5). From here an EMAIL draft -- first touch or follow-up -- is
-- approved by the workflow that wrote it when, and only when, all of these hold:
--   * settings.auto_approve_email is on
--   * it carries no rule tag (nothing after '+' in `variant`)
--   * it is not a low-context note
--   * every claim code it records is active AND confirmed in claims_library
--   * a second Sonnet 5.5 call compared each product claim with the confirmed
--     claims, and each prospect fact with the enrichment record, and passed it
--     (a widened claim fails)
-- Anything else stays 'pending' with the reason in hold_reason. A LinkedIn
-- draft is never auto-approved (Section 6). The review queue becomes the
-- exceptions queue: what is pending is what auto-approval held.
--
-- The workflows decide. This migration makes the deterministic half of that
-- decision a property of the table, the same principle as migration 006: a
-- rule on `drafts` holds for every writer, not just the one that was careful.
-- An auto-approval that breaks a rule here is not refused (refusing would fail
-- the whole write, and Workflow 4 would redraft the lead -- a paid call -- every
-- 30 minutes forever); it is turned into a hold, with the reason recorded.
--
-- Additive only. Send (send0001) is untouched: it still sends any approved
-- email draft, under the same ceiling, hours and guards.

BEGIN;

-- 1. The flag. One more key in the operator's runtime settings (migration 008),
-- on by default. sync_settings.py writes it from .env's
-- NOVASCOUT_AUTO_APPROVE_EMAIL when that is set; otherwise the row below stands.
ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_key_check;
ALTER TABLE settings ADD CONSTRAINT settings_key_check
  CHECK (key IN ('operator_email', 'auto_approve_email'));
ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_auto_approve_email_shape;
ALTER TABLE settings ADD CONSTRAINT settings_auto_approve_email_shape
  CHECK (key <> 'auto_approve_email' OR value IN ('true', 'false'));
INSERT INTO settings (key, value) VALUES ('auto_approve_email', 'true')
ON CONFLICT (key) DO NOTHING;

-- Every reader asks this function, so the default has one definition: a
-- missing row is ON ("on by default"); only an explicit 'false' turns it off.
CREATE OR REPLACE FUNCTION auto_approve_email_enabled()
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce((SELECT value = 'true' FROM settings WHERE key = 'auto_approve_email'), true)
$$;

-- 2. Who approved, when, and why not.
--   approved_by  'auto' (the workflow, after every check above) or 'human'
--                (anyone else: NocoDB, psql). Kept through 'sent', so the
--                learning loop can tell which sends no human read. Cleared when
--                a draft leaves approved/sent.
--   approved_at  when it became approved. NULL on approvals made before this
--                migration -- that time was never recorded.
--   hold_reason  why auto-approval held it: '<code>[: detail]', several joined
--                by '; '. Kept when a human later approves or rejects it -- a
--                held draft a human approves anyway is the evidence for tuning
--                the checks.
--   claim_check  the second model call: its verdict on every statement, the
--                model, the usage. Never cleared, so an auto-approval a human
--                later rejected stays recoverable.
ALTER TABLE drafts
  ADD COLUMN IF NOT EXISTS approved_by TEXT,
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS hold_reason TEXT,
  ADD COLUMN IF NOT EXISTS claim_check JSONB;

-- Every approval that exists today was a human's: nothing approved itself
-- before this migration. The pending LinkedIn and low-context drafts get the
-- reason they would get from now on.
UPDATE drafts SET approved_by = 'human' WHERE status IN ('approved', 'sent') AND approved_by IS NULL;
UPDATE drafts SET hold_reason = 'linkedin: always reviewed and sent by hand'
 WHERE status = 'pending' AND channel = 'linkedin' AND hold_reason IS NULL;
UPDATE drafts SET hold_reason = 'low-context: a note to a person, not an email to send'
 WHERE status = 'pending' AND channel = 'email' AND coalesce(variant, '') LIKE 'low-context%' AND hold_reason IS NULL;

ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_approved_by_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_approved_by_check
  CHECK (approved_by IS NULL OR (approved_by IN ('auto', 'human') AND status IN ('approved', 'sent')));
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_auto_only_email;
ALTER TABLE drafts ADD CONSTRAINT drafts_auto_only_email
  CHECK (approved_by IS DISTINCT FROM 'auto' OR channel = 'email');

-- 3. The rules, on the table. Fires after draft_review_rules_trg (triggers of
-- one kind fire in name order: 'draft_review...' < 'drafts_approval...'), so the
-- status is already normalised when this reads it.
CREATE OR REPLACE FUNCTION drafts_approval_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
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
      IF position('+' IN coalesce(NEW.variant, '')) > 0 THEN
        problems := array_append(problems, ('rule tags ' || split_part(NEW.variant, '+', 2)));
      END IF;
      IF NEW.edited_body IS NOT NULL THEN
        problems := array_append(problems, 'edited text was never checked');
      END IF;
      -- 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1' -> {D2,ANG-HOURS,BEN-SEE,PR-BOTH,A1}.
      -- A low-context variant ('low-context/role-inbox') records no claim codes.
      codes := CASE WHEN coalesce(NEW.variant, '') LIKE 'low-context%' THEN '{}'::text[]
                    ELSE array_remove(string_to_array(
                           split_part(split_part(coalesce(NEW.variant, ''), '+', 1), '/', 2), '.'), '') END;
      IF coalesce(NEW.variant, '') LIKE 'low-context%' THEN
        NULL;  -- already held above; it has no claims to look up
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
$$;

DROP TRIGGER IF EXISTS drafts_approval_rules_trg ON drafts;
CREATE TRIGGER drafts_approval_rules_trg
  BEFORE INSERT OR UPDATE ON drafts
  FOR EACH ROW EXECUTE FUNCTION drafts_approval_rules();

-- 4. A confirmation is of the text that was read. With auto-approval, a
-- confirmed line reaches a prospect with no human in between, so editing a
-- confirmed line's text un-confirms it until someone confirms the new text.
-- (An UPDATE that changes the text and sets confirmed=true in one go is still
-- un-confirmed -- confirm in a second save, after reading it.)
CREATE OR REPLACE FUNCTION claims_confirmation_follows_text()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.confirmed AND NEW.confirmed AND (
       NEW.body         IS DISTINCT FROM OLD.body
    OR NEW.slot         IS DISTINCT FROM OLD.slot
    OR NEW.countries    IS DISTINCT FROM OLD.countries
    OR NEW.capabilities IS DISTINCT FROM OLD.capabilities) THEN
    NEW.confirmed := false;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS claims_confirmation_follows_text_trg ON claims_library;
CREATE TRIGGER claims_confirmation_follows_text_trg
  BEFORE UPDATE ON claims_library
  FOR EACH ROW EXECUTE FUNCTION claims_confirmation_follows_text();

-- 5. The daily digest's memory: one row per operator day (Asia/Karachi) on
-- which the digest went out. Written only after SMTP accepted it, so a failed
-- digest is retried on the next tick; the next digest covers from covers_to.
CREATE TABLE IF NOT EXISTS digest_log (
  digest_day  DATE PRIMARY KEY,
  covers_from TIMESTAMPTZ NOT NULL,
  covers_to   TIMESTAMPTZ NOT NULL,
  sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  summary     JSONB
);

COMMENT ON COLUMN drafts.approved_by IS
  'auto = approved by the workflow that wrote it, after the rule, claim and Sonnet 5.5 checks (migration 014); '
  'human = anyone else. Only on approved/sent drafts.';
COMMENT ON COLUMN drafts.hold_reason IS
  'Why auto-approval held this draft for a human (migration 014): <code>[: detail], joined by "; ".';
COMMENT ON TABLE digest_log IS
  'Workflow 6 Daily Digest: one row per operator day the digest was accepted by SMTP (migration 014).';

COMMIT;

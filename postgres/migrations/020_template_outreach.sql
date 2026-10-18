-- Migration 020 -- the approval rules for template outreach (2026-10-19).
--
-- The operator replaced AI-composed outreach with fixed templates (Master Ref
-- Section 9, Workflows 4-6): a first touch or a follow-up is now the approved
-- wording filled with verified facts, written by code with no model call. So
-- the checks that existed to police model-written copy -- the claim codes in
-- `variant`, their confirmation in `claims_library`, a passing Sonnet 5.5 claim
-- check -- have nothing left to police, and migration 014's trigger would hold
-- every template draft for lacking them ("no claim codes", "no passing claim
-- check").
--
-- What an `approved_by = 'auto'` approval needs from here, on the table, for
-- migration 014's reason -- a rule on `drafts` holds for every writer, not only
-- the workflow that was careful:
--
--   * settings.auto_approve_email is on                      (unchanged, 014)
--   * it is an email                                          (unchanged, 014)
--   * it is not a reply                                       (unchanged, 018)
--   * it is a template draft: 'template/...' or 'follow-up-N/template/...'.
--     New. A draft written any other way -- by the retired composer, by hand --
--     can no longer approve itself; a person approves it or nothing does.
--   * no rule tag after '+' (the template gate's own holds -- no-address,
--     unverified-address, no-send-clock, blocklisted -- are tags)
--   * its text was not edited                                 (unchanged, 014)
--   * its lead is a CRO. New: the template speaks to a CRO, and sites and SMOs
--     wait at contact_found (Section 9, Workflow 4). lead_company_type() is the
--     one definition (migration 019).
--   * its contact has a deliverable address and it is verified. New on the
--     table: Send gates on exactly this (contact-unverified, no-valid-address).
--   * neither the lead's domain nor the recipient's is blocklisted. New on the
--     table: Send's own expression.
--
-- The business-hours clock is not re-checked here: Section 12's table lives in
-- the workflows (code_decide.js and the build-time list the gate uses), and the
-- gate's 'no-send-clock' hold is a tag, which the rule above refuses. Send
-- refuses such a country again ('unknown-country').
--
-- Everything else in drafts_approval_rules() is migration 018's, unchanged:
-- leaving approval clears approved_by; a reply needs its one-time code; a
-- human approval is stamped 'human'; an auto-approved draft whose text changes
-- goes back to a person. claims_library stays where it is (the Reply Assistant
-- still reads it); only this trigger stops reading it.
--
-- Additive in effect and safe to re-run: CREATE OR REPLACE of one function.

BEGIN;

CREATE OR REPLACE FUNCTION drafts_approval_rules()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
  was      text := CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END;
  problems text[] := '{}';
  v        text := coalesce(NEW.variant, '');
  addr     text;
  ok_addr  boolean;
BEGIN
  -- Leaving approval: approved_by describes the current approval, nothing else.
  IF NEW.status NOT IN ('approved', 'sent') THEN
    NEW.approved_by := NULL;
    NEW.approved_at := NULL;
    -- Send's Revert Claim hands a recipient-refused draft back to a human with
    -- '+smtp-rejected' on its variant; say so where the digest looks.
    IF was = 'sent' AND NEW.status = 'pending' AND v LIKE '%smtp-rejected%' THEN
      NEW.hold_reason := 'smtp-rejected: the mail server refused the address';
    END IF;
    RETURN NEW;
  END IF;

  -- A fresh approval: an insert, or a move into 'approved' from anything but
  -- 'sent'. (sent -> approved is Send handing a claim back after an account-
  -- level SMTP failure; the approval it had stands.)
  IF NEW.status = 'approved' AND (TG_OP = 'INSERT' OR coalesce(was, '') NOT IN ('approved', 'sent')) THEN
    -- Migration 018, checked before anything else and unchanged: a drafted
    -- reply is approved by a one-time code the operator quotes back from an
    -- authenticated message (Section 9, Workflow 7) and by nothing else.
    IF v LIKE 'reply/%'
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
      IF v LIKE 'reply/%' THEN
        problems := array_append(problems, 'reply');
      ELSIF v !~ '^(template/|follow-up-[0-9]+/template/)' THEN
        problems := array_append(problems, 'not a template draft');
      END IF;
      IF position('+' IN v) > 0 THEN
        problems := array_append(problems, ('rule tags ' || split_part(v, '+', 2)));
      END IF;
      IF NEW.edited_body IS NOT NULL THEN
        problems := array_append(problems, 'edited text was never checked');
      END IF;
      IF NOT EXISTS (SELECT 1 FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id
                      WHERE l.id = NEW.lead_id AND lead_company_type(e.raw_extraction, l.source) = 'CRO') THEN
        problems := array_append(problems, 'not a CRO');
      END IF;
      SELECT c.email,
             coalesce(c.verified, false)
             AND c.email ~ '^[^[:space:]@<>(),;:"'']+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
        INTO addr, ok_addr
        FROM contacts c WHERE c.lead_id = NEW.lead_id;
      IF NOT coalesce(ok_addr, false) THEN
        problems := array_append(problems, 'no verified deliverable address');
      END IF;
      IF EXISTS (SELECT 1 FROM leads l, blocklist b
                  WHERE l.id = NEW.lead_id
                    AND (lower(l.domain) = b.domain OR lower(l.domain) LIKE '%.' || b.domain))
         OR EXISTS (SELECT 1 FROM blocklist b
                     WHERE lower(split_part(coalesce(addr, ''), '@', 2)) = b.domain
                        OR lower(split_part(coalesce(addr, ''), '@', 2)) LIKE '%.' || b.domain) THEN
        problems := array_append(problems, 'blocklisted');
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

  -- Still approved, and approved by the workflow: the text it wrote must be the
  -- text that goes out. Text rewritten underneath it goes back to a human; a
  -- human's own edit (edited_body) makes the approval theirs.
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

COMMENT ON COLUMN drafts.approved_by IS
  'auto = approved by the workflow that wrote it: a template draft (first touch or follow-up) with a verified '
  'deliverable address, a Send clock and no blocklisted domain (migration 020; before 2026-10-19, migration 014''s '
  'claim check); human = anyone else. Only on approved/sent drafts.';

COMMIT;

-- Migration 016 -- the table learns about the other kind of note (2026-10-07).
--
-- Workflow 4 has had two no-model branches since 2026-10-07: the low-context
-- note it has always written, and a `no-send-clock/` note for a lead whose
-- country has no Send business-hours clock (Section 12). Migration 014's
-- principle is that the auto-approval rules live on the TABLE as well as in the
-- workflows -- "a rule on `drafts` holds for every writer, not just the one that
-- was careful" -- and `drafts_approval_rules` only knew the first kind.
--
-- It already failed closed: a `no-send-clock/role-inbox` variant has no claim
-- codes, so the function read 'role-inbox' as a code, found it unconfirmed, and
-- held the draft. Correct outcome, misleading reason. This replaces the function
-- so the reason says `no-send-clock` and the claim-code lookup is skipped, the
-- same way it is for a low-context note.
--
-- Nothing else changes: same trigger, same rules, same direction (a rule here
-- can make the workflows approve less, never more). The Approval Gate holds
-- such a draft first, with a reason naming the country, so in practice this
-- function should never see one.

BEGIN;

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
      IF coalesce(NEW.variant, '') LIKE 'no-send-clock%' THEN
        problems := array_append(problems, 'no-send-clock');
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
$$;

COMMIT;

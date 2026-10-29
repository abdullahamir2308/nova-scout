-- Migration 026 -- a niche can hold one of its sources (2026-10-30, operator brief).
--
-- WHY. The operator asked for a precision audit of the research-site niche's
-- two open sources (docs/research_site_niche.md section 7.5): read each sampled
-- site and check that its domain really is that facility's. Overture-sourced
-- drafts are held until that source measures at least 90 % -- now, and every
-- one Drafting writes while the hold is on.
--
-- WHAT.
--   * niches.held_sources -- leads.source values whose drafts this niche never
--     auto-approves. Unlike excluded_sources the leads stay in the niche: they
--     are enriched, scored, looked up and drafted as before, and every draft
--     waits in the exceptions queue, so lifting the hold loses nothing.
--   * drafts_approval_rules() -- migration 025's function plus one auto-approval
--     problem, "source <s> is held by niche <n> (...)". Every other rule is
--     unchanged, word for word.
--   * research_site holds 'sites-overture', and every approved, unsent
--     Overture email draft is held with the same reason. (154 were held by
--     hand at 2026-10-29 19:52 UTC, minutes before this migration, with this
--     exact hold_reason; the UPDATE below finds none of those again.)
--
-- LIFT THE HOLD (after the audit says so). The re-approval goes through every
-- guard again, as any auto-approval does:
--   BEGIN;
--   UPDATE niches SET held_sources = '{}' WHERE code = 'research_site';
--   UPDATE drafts SET status = 'approved', approved_by = 'auto'
--    WHERE status = 'pending' AND hold_reason LIKE 'db-guard: source sites-overture is held%';
--   COMMIT;
BEGIN;

ALTER TABLE niches ADD COLUMN IF NOT EXISTS held_sources text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN niches.held_sources IS
  'leads.source values whose drafts this niche never auto-approves: the leads stay in the niche and are drafted, '
  'and every draft is held for a person (migration 026). Empty = no hold.';

CREATE OR REPLACE FUNCTION drafts_approval_rules()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
  was      text := CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END;
  problems text[] := '{}';
  v        text := coalesce(NEW.variant, '');
  addr     text;
  ok_addr  boolean;
  nch      niches%ROWTYPE;
  src      text;
BEGIN
  -- Leaving approval: approved_by describes the current approval, nothing else.
  IF NEW.status NOT IN ('approved', 'sent') THEN
    NEW.approved_by := NULL;
    NEW.approved_at := NULL;
    IF was = 'sent' AND NEW.status = 'pending' AND v LIKE '%smtp-rejected%' THEN
      NEW.hold_reason := 'smtp-rejected: the mail server refused the address';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.status = 'approved' AND (TG_OP = 'INSERT' OR coalesce(was, '') NOT IN ('approved', 'sent')) THEN
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
      -- Migration 020 asked "is the lead a CRO"; a niche row answers it now.
      SELECT n.* INTO nch
        FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id
        JOIN niches n ON n.code = lead_niche(e.raw_extraction, l.source)
       WHERE l.id = NEW.lead_id;
      IF nch.code IS NULL THEN
        problems := array_append(problems, 'no niche drafts this lead');
      ELSIF NOT nch.active THEN
        problems := array_append(problems, 'niche ' || nch.code || ' is not active');
      ELSIF NOT nch.auto_approve THEN
        problems := array_append(problems, 'niche ' || nch.code || ' does not auto-approve');
      ELSIF position(E'\n' || nch.sender_name || E'\n' || nch.sender_title || E'\n'
                     IN E'\n' || replace(coalesce(NEW.body, ''), E'\r', '') || E'\n') = 0 THEN
        problems := array_append(problems, 'not signed as niche ' || nch.code || ' signs');
      END IF;
      -- Migration 026: a source the niche holds (the 2026-10-30 precision audit).
      SELECT l.source INTO src FROM leads l WHERE l.id = NEW.lead_id;
      IF nch.code IS NOT NULL AND src = ANY (coalesce(nch.held_sources, '{}')) THEN
        problems := array_append(problems, 'source ' || src || ' is held by niche ' || nch.code
                                           || ' (precision audit, 2026-10-30)');
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

UPDATE niches SET held_sources = '{sites-overture}' WHERE code = 'research_site';

UPDATE drafts d
   SET status = 'pending',
       hold_reason = 'db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)'
  FROM leads l
 WHERE l.id = d.lead_id AND l.source = 'sites-overture' AND d.status = 'approved' AND d.channel = 'email';

COMMIT;

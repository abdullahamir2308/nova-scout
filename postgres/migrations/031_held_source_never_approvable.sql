-- Migration 031 -- a held source cannot be approved by anyone (operator, 2026-10-30, later).
--
-- WHY. Migration 026 held the sites-overture source: drafts are written and held, and "lifting the hold is
-- the operator's call" for the whole source. But drafts_approval_rules() checked the held source only for an
-- automatic approval (approved_by = 'auto'); a human approval took the ELSE branch with no check, and Send
-- never read held_sources. On 2026-10-30 that let three Overture drafts out of the hold by APPROVE during the
-- reminder flood (812, 867, 933; re-held by 030). Closed here, for every route:
--
--   * lead_held_source(lead_id) -- the one definition: 'its source <s> is held by niche <n>
--     (niches.held_sources)', or NULL. Used by the trigger, by Send (Load Send State leaves such a draft out of
--     its candidates, so it can never block a lane; Claim Send refuses it -- the last check before SMTP), by
--     Apply Operator Command (APPROVE / EDIT refused with that sentence, the code left unspent, the refusal
--     recorded and acknowledged), and by the code minting below.
--   * drafts_approval_rules() -- 026's function, word for word, plus: a human approval of a held-source draft
--     (a fresh approval, or an edit of an auto-approved one) RAISES with a plain message and a hint. An
--     automatic approval is still turned into 026's hold ("db-guard: source <s> is held by niche <n>
--     (precision audit, 2026-10-30)"), unchanged, so the workflows keep writing held drafts and 026's lift
--     recipe still matches them.
--   * reply_approval_issue() / reply_approval_withdraw_holds() -- 028's, plus: no email-hold code for a
--     held-source draft, whatever its hold reason (an auto-approve-off hold on an Overture draft included), and
--     an open one is withdrawn.
--
-- LIFTING STILL WORKS (026's recipe, unchanged): clear niches.held_sources, then re-approve as 'auto'; with
-- the source no longer held, lead_held_source() is NULL and every other rule applies as before. Proven by
-- n8n/sendtrack/test_held_source_sql.py.
--
-- Re-runnable: CREATE OR REPLACE throughout.
BEGIN;

CREATE OR REPLACE FUNCTION lead_held_source(p_lead_id bigint)
RETURNS text LANGUAGE sql STABLE AS $fn$
  SELECT 'its source ' || l.source || ' is held by niche ' || n.code || ' (niches.held_sources)'
    FROM leads l
    LEFT JOIN enrichments e ON e.lead_id = l.id
    JOIN niches n ON n.code = lead_niche(e.raw_extraction, l.source)
   WHERE l.id = p_lead_id
     AND l.source = ANY (coalesce(n.held_sources, '{}'))
   LIMIT 1;
$fn$;

COMMENT ON FUNCTION lead_held_source(bigint) IS
  'Why this lead''s drafts cannot be approved or sent -- its source is in its niche''s held_sources -- or NULL '
  '(migration 031). The one definition, for the approval trigger, Send, operator commands and code minting.';

-- 026's function; the three migration-031 blocks are the only additions.
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
  held     text;
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
      -- Migration 031: nobody approves a draft from a held source -- not APPROVE by email, not the NocoDB
      -- grid, not psql. Refused loudly, so the person doing it sees why; the hold is lifted for the whole
      -- source (026's recipe), never draft by draft.
      held := lead_held_source(NEW.lead_id);
      IF held IS NOT NULL THEN
        RAISE EXCEPTION 'Draft % cannot be approved: %. Nobody can approve a draft from a held source.', NEW.id, held
          USING ERRCODE = 'check_violation',
                HINT = 'The draft stays as it was. A source hold is lifted for the whole source with migration '
                    || '026''s recipe, after the precision audit says so; to stop this draft for good, reject it.';
      END IF;
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
      -- Migration 031: an edit is a person's approval too.
      held := lead_held_source(NEW.lead_id);
      IF held IS NOT NULL THEN
        RAISE EXCEPTION 'Draft % cannot be edited into an approval: %. Nobody can approve a draft from a held source.',
          NEW.id, held USING ERRCODE = 'check_violation',
          HINT = 'Lift the hold for the whole source (migration 026''s recipe), or reject the draft.';
      END IF;
      NEW.approved_by := 'human';
      NEW.approved_at := now();
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

-- 028's two functions; the lead_held_source() lines are the only additions.
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
          AND hold_needs_person(d.hold_reason)
          AND lead_held_source(d.lead_id) IS NULL) THEN      -- migration 031
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

CREATE OR REPLACE FUNCTION reply_approval_withdraw_holds()
RETURNS integer LANGUAGE sql AS $fn$
  WITH gone AS (
    DELETE FROM reply_approvals a
     WHERE a.kind = 'email-hold'
       AND a.used_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM drafts d
                        WHERE d.id = a.draft_id AND d.status = 'pending' AND d.channel = 'email'
                          AND hold_needs_person(d.hold_reason)
                          AND lead_held_source(d.lead_id) IS NULL)   -- migration 031
    RETURNING 1)
  SELECT count(*)::int FROM gone;
$fn$;

SELECT reply_approval_withdraw_holds() AS withdrawn_by_031;

COMMIT;

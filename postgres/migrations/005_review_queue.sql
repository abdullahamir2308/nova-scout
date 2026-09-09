-- Migration 005 -- the Workflow 5 review queue.
--
-- Section 9, Workflow 5: "NocoDB grid view over `drafts` joined to `leads`,
-- `scores`, `contacts`. Sort: fit_score descending. Actions: Approve, Edit,
-- Reject. Rejection requires a reason."
--
-- WHY A VIEW PLUS A TRIGGER, rather than pointing NocoDB at `drafts` and
-- hanging lookup fields off it:
--
--   1. fit_score is the sort key, and it lives in `scores`, two hops from
--      `drafts` (drafts -> leads -> scores). Sorting a grid by a lookup field
--      across two hops is exactly the kind of thing that either is not
--      supported or is quietly slow. A plain column sorts plainly.
--   2. "Rejection requires a reason" is a data integrity rule. Enforced in a
--      grid UI it is a suggestion; enforced here it is a constraint, and it
--      holds for psql, the n8n workflows and any future client too.
--
-- The trigger is what makes the view writable. Postgres will not auto-update a
-- view over four joined tables, and it should not: without an explicit rule,
-- "which table did the user mean" has no safe answer.

BEGIN;

CREATE OR REPLACE VIEW review_queue AS
SELECT
  d.id                AS draft_id,
  s.fit_score,
  l.company_name,
  l.domain,
  l.country,
  d.channel,
  d.variant,
  d.subject,
  d.body,
  d.status,
  d.reject_reason,
  d.edited_body,
  c.name              AS contact_name,
  c.title             AS contact_title,
  c.email             AS contact_email,
  c.linkedin_url      AS contact_linkedin,
  c.verified          AS contact_verified,
  s.rationale,
  d.lead_id,
  l.status            AS lead_status,
  d.created_at
FROM drafts d
JOIN leads      l ON l.id = d.lead_id
LEFT JOIN scores   s ON s.lead_id = d.lead_id
LEFT JOIN contacts c ON c.lead_id = d.lead_id;

COMMENT ON VIEW review_queue IS
  'Workflow 5 review queue. Updatable through review_queue_write() for status, '
  'reject_reason and edited_body only. Everything else is read-only by design: '
  'the queue is where a draft is judged, not where a lead is edited.';

-- The four reasons Section 9 names, and only these. They are the training data
-- for the learning loop, so a free-text reason would defeat the point -- "bad
-- fit" and "not a great fit" are the same signal spelled two ways, and nothing
-- downstream could group them.
CREATE OR REPLACE FUNCTION review_queue_reasons()
RETURNS text[] LANGUAGE sql IMMUTABLE AS
$$ SELECT ARRAY['bad fit', 'bad draft', 'already contacted', 'wrong contact'] $$;

CREATE OR REPLACE FUNCTION review_queue_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  allowed_status text[] := ARRAY['pending', 'approved', 'rejected', 'sent'];
BEGIN
  IF NEW.status IS NULL OR NOT (NEW.status = ANY (allowed_status)) THEN
    RAISE EXCEPTION
      'draft status must be one of %, got %', allowed_status, coalesce(NEW.status, 'NULL')
      USING ERRCODE = 'check_violation';
  END IF;

  -- Section 9: "Rejection requires a reason (bad fit / bad draft / already
  -- contacted / wrong contact) -- this is the training data for the learning
  -- loop." A rejection with no reason teaches nothing, so it is refused.
  IF NEW.status = 'rejected' THEN
    IF NEW.reject_reason IS NULL OR btrim(NEW.reject_reason) = '' THEN
      RAISE EXCEPTION
        'rejecting a draft requires a reason -- one of %', review_queue_reasons()
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (lower(btrim(NEW.reject_reason)) = ANY (review_queue_reasons())) THEN
      RAISE EXCEPTION
        'reject reason % is not one of %', NEW.reject_reason, review_queue_reasons()
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  UPDATE drafts
     SET status        = NEW.status,
         -- A reason only means anything on a rejection. Clearing it on any
         -- other status stops a stale "bad fit" riding along on a draft that
         -- was later approved, which would poison the learning loop quietly.
         reject_reason = CASE
                           WHEN NEW.status = 'rejected' THEN lower(btrim(NEW.reject_reason))
                           ELSE NULL
                         END,
         -- Section 9's Edit action. The original body is never overwritten:
         -- the pair (body, edited_body) is the signal for how much editing a
         -- draft needed, which is the other half of the learning loop and the
         -- evidence Section 3's "swap drafting to Sonnet 5" decision needs.
         edited_body   = NULLIF(btrim(coalesce(NEW.edited_body, '')), '')
   WHERE id = OLD.draft_id;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS review_queue_write_trg ON review_queue;
CREATE TRIGGER review_queue_write_trg
  INSTEAD OF UPDATE ON review_queue
  FOR EACH ROW EXECUTE FUNCTION review_queue_write();

-- Section 9, Workflow 3: "the review queue must surface scores.disqualify_reason
-- for disqualified leads, not just hide them -- ongoing visibility catches
-- misclassification as it happens." Disqualified leads never reach `drafts`, so
-- they cannot appear in the queue above; this is that visibility, and it is
-- read-only because there is nothing to action on it.
CREATE OR REPLACE VIEW disqualified_queue AS
SELECT
  l.id AS lead_id,
  l.company_name,
  l.domain,
  l.country,
  s.disqualify_reason,
  s.rationale,
  e.therapeutic_areas,
  e.employee_estimate,
  e.has_chatbot,
  e.chatbot_vendor,
  s.scored_at
FROM leads l
JOIN scores s ON s.lead_id = l.id
LEFT JOIN enrichments e ON e.lead_id = l.id
WHERE l.status = 'disqualified';

COMMIT;

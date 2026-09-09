-- Migration 006 -- move the review rules onto `drafts`, make `review_queue` read-only.
--
-- WHY, and this one was found the hard way.
--
-- Migration 005 made `review_queue` writable with an INSTEAD OF UPDATE trigger,
-- intending NocoDB to drive Approve/Edit/Reject through it. NocoDB imports a
-- Postgres view with NO primary key -- a view cannot declare one, and an attempt
-- to add one fails with SQLSTATE 42809 (wrong object type). Without a primary
-- key NocoDB has no WHERE clause to build, so a single-record PATCH for
-- draft_id 38 was issued as an unqualified
--
--     UPDATE review_queue SET status = 'approved'
--
-- The INSTEAD OF trigger is a ROW trigger, so it fired once per row and happily
-- approved all ten drafts in the queue. NocoDB then returned a generic
-- "Something didn't work as expected" -- so the call LOOKED like it had failed
-- while it had in fact already committed a mass update. Verified directly in
-- Postgres afterwards: every draft was 'approved'.
--
-- The lesson is not "NocoDB is broken". It is that an updatable view with no key
-- is a loaded gun: any client that cannot identify a row gets to rewrite the
-- whole table, and nothing in the view can tell the difference between that and
-- a deliberate bulk edit.
--
-- So:
--   * `drafts` is the writable surface. It has a real primary key, NocoDB
--     imported it as a proper table, and the Workflow 5 grid is built on it with
--     lookup fields for the joined context (verified: a two-hop lookup sorts).
--   * The review rules move ONTO `drafts`, where they hold for every writer --
--     the NocoDB grid, psql, and any future workflow -- rather than only for
--     traffic that happened to arrive through one view.
--   * `review_queue` loses its trigger and becomes read-only. Postgres then
--     refuses writes to it outright ("cannot update view"), which is a much
--     better failure than a silent unqualified update.

BEGIN;

DROP TRIGGER IF EXISTS review_queue_write_trg ON review_queue;
DROP FUNCTION IF EXISTS review_queue_write();

COMMENT ON VIEW review_queue IS
  'Workflow 5 review queue, READ-ONLY. The ranked reading surface: drafts joined '
  'to leads/scores/contacts, sortable by fit_score in one plain column. Writes go '
  'to `drafts` -- see migration 006 for why this view is deliberately not '
  'updatable.';

-- Section 9, Workflow 5: "Rejection requires a reason (bad fit / bad draft /
-- already contacted / wrong contact) -- this is the training data for the
-- learning loop."
--
-- Free text would defeat the point: "bad fit" and "not a great fit" are one
-- signal spelled two ways, and nothing downstream could group them. So the
-- reason is a closed set, and it is enforced in the database rather than in a
-- grid UI, where it would be a suggestion.
CREATE OR REPLACE FUNCTION draft_review_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  allowed_status  text[] := ARRAY['pending', 'approved', 'rejected', 'sent'];
  allowed_reason  text[] := ARRAY['bad fit', 'bad draft', 'already contacted', 'wrong contact'];
  reason          text   := lower(btrim(coalesce(NEW.reject_reason, '')));
BEGIN
  IF NEW.status IS NULL OR btrim(NEW.status) = '' THEN
    NEW.status := 'pending';
  END IF;

  IF NOT (NEW.status = ANY (allowed_status)) THEN
    RAISE EXCEPTION 'draft status must be one of %, got %', allowed_status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'rejected' THEN
    IF reason = '' THEN
      RAISE EXCEPTION
        'rejecting a draft requires a reason -- one of %', allowed_reason
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT (reason = ANY (allowed_reason)) THEN
      RAISE EXCEPTION 'reject reason % is not one of %', NEW.reject_reason, allowed_reason
        USING ERRCODE = 'check_violation';
    END IF;
    -- Stored normalised, so the learning loop groups on one spelling.
    NEW.reject_reason := reason;
  ELSE
    -- A reason only means anything on a rejection. Clearing it stops a stale
    -- 'bad fit' riding along on a draft that was later approved, which would
    -- poison the training data quietly.
    NEW.reject_reason := NULL;
  END IF;

  -- Section 9's Edit action. The original `body` is never overwritten: the pair
  -- (body, edited_body) is how much editing a draft actually needed, which is
  -- the other half of the learning loop -- and the evidence Section 3's "swap
  -- drafting to Sonnet 5 after two weeks of real drafts" decision runs on.
  NEW.edited_body := NULLIF(btrim(coalesce(NEW.edited_body, '')), '');

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS draft_review_rules_trg ON drafts;
CREATE TRIGGER draft_review_rules_trg
  BEFORE INSERT OR UPDATE ON drafts
  FOR EACH ROW EXECUTE FUNCTION draft_review_rules();

COMMIT;

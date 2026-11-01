-- Migration 032 -- the operator's verdict on a reply (dashboard v1, operator brief 2026-11-01).
--
-- WHY. Mailbox Watch classifies what came back (reply / opt-out / auto-reply / bounce / unmatched), and the
-- operator notification computes a positive/neutral keyword signal (positiveSignal()) that is stored nowhere
-- (Master Ref Section 9, Workflow 6). So "how many replies were positive" -- the number the A/B test is
-- really about -- is not in the database. This adds it as the operator's own tag, set by hand in NocoDB on
-- each reply:
--
--   inbound_messages.reply_outcome  positive | neutral | negative | meeting booked   (NULL = not tagged yet)
--
-- A Postgres enum, not text + CHECK like the rest of the schema, for one measured reason: NocoDB shows an
-- enum column as a single-select dropdown with exactly these four values, and a text column as free text
-- (every CHECKed text column in this schema is a LongText field in the grid). A dropdown cannot be mistyped.
--
-- Only a `reply` row can carry one (inbound_reply_outcome_on_reply): an opt-out, an out-of-office or a bounce
-- has no outcome to judge. Empty by default; nothing in any workflow reads or writes it -- Record Inbound
-- inserts with an explicit column list, so it never touches it, and an operator's edit of it fires no
-- trigger (unverify_contact_on_hard_bounce_trg is UPDATE OF bounce_kind only).
--
-- Additive and idempotent.
BEGIN;

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                  WHERE t.typname = 'reply_outcome' AND n.nspname = 'public') THEN
    CREATE TYPE reply_outcome AS ENUM ('positive', 'neutral', 'negative', 'meeting booked');
  END IF;
END
$do$;

ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS reply_outcome reply_outcome;

ALTER TABLE inbound_messages DROP CONSTRAINT IF EXISTS inbound_reply_outcome_on_reply;
ALTER TABLE inbound_messages ADD CONSTRAINT inbound_reply_outcome_on_reply
  CHECK (reply_outcome IS NULL OR classification = 'reply');

COMMENT ON COLUMN inbound_messages.reply_outcome IS
  'The operator''s verdict on a reply, set by hand in NocoDB: positive | neutral | negative | meeting booked. '
  'NULL = not tagged yet. Only on a reply row. Read by the dashboard (metrics schema, migration 033).';

COMMIT;

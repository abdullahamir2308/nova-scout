-- Migration 024 -- IMAP Health learns the Sent-mirror gap (2026-10-28).
--
-- Master Ref Section 9, Workflow 6. On 2026-10-28 the Sent trigger missed a
-- send (07:40 UTC) after its scheduled 30-minute connection replacement went
-- wrong, Send failed closed `sent-mirror-behind` from 07:55, and IMAP Health
-- stayed healthy: its `sent-missed` counted a message as recorded when
-- mailbox_sent had a row for it, and Send writes its own row the moment SMTP
-- accepts -- the row proved nothing about the folder. Two problem codes join
-- the four of migration 009:
--
--   * sent-mismatch -- a row the mirror marks seen_in_sent_folder whose
--     Message-ID the Sent folder (the checker's listing) does not hold.
--   * send-blocked  -- a workflow send unseen in the Sent folder for longer than
--     Send's own mirror grace plus 20 minutes: Send has been refusing everything
--     for that long. It alerts on the first check that finds it.
--
-- `sent-missed` keeps its name and now means: in the folder, not yet confirmed
-- seen by the mirror. The Sent reconcile in Mailbox Watch (every 10 minutes)
-- normally clears it before anyone is told.
--
-- Also: mailbox_sync gains a second row, folder 'Sent-reconcile', stamped by
-- that reconcile; the table has no CHECK on folder, so nothing changes here.
--
-- Additive and idempotent.

BEGIN;

ALTER TABLE mailbox_health DROP CONSTRAINT IF EXISTS mailbox_health_problems_check;
ALTER TABLE mailbox_health ADD CONSTRAINT mailbox_health_problems_check
  CHECK (problems <@ ARRAY['checker-unreachable', 'imap-failed', 'inbox-missed', 'sent-missed',
                           'sent-mismatch', 'send-blocked']);

COMMENT ON COLUMN mailbox_health.problems IS
  'Migrations 009 + 024: checker-unreachable | imap-failed | inbox-missed | sent-missed (in the Sent folder, not '
  'confirmed seen by the mirror) | sent-mismatch (mirror says seen, folder lacks it) | send-blocked (a workflow '
  'send unseen for longer than Send''s mirror grace + 20 min).';

COMMIT;

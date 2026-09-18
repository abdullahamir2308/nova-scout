-- Migration 009 -- the IMAP health check's state (Workflow 6, 2026-09-16).
--
-- Section 9: Mailbox Watch is proven live only when it fires; nothing proves its
-- IMAP connection is up between events. The "Send & Track - IMAP Health"
-- workflow checks every 20 minutes that a fresh IMAP session works and that
-- Mailbox Watch has recorded everything that landed in INBOX and Sent. This is
-- what it remembers between checks, so the operator gets one alert per problem
-- (plus reminders, and an all-clear) rather than an email every tick.
--
-- One row, check_name = 'imap'. Written only by that workflow's Record Health
-- node. Additive only.

BEGIN;

CREATE TABLE IF NOT EXISTS mailbox_health (
  check_name           TEXT PRIMARY KEY CHECK (check_name IN ('imap')),
  healthy              BOOLEAN NOT NULL,
  -- What the last check found; empty when healthy. code_health.js's PROBLEMS,
  -- Section 8's list -- the build refuses if the two drift.
  problems             TEXT[] NOT NULL DEFAULT '{}'
                       CHECK (problems <@ ARRAY['checker-unreachable', 'imap-failed', 'inbox-missed', 'sent-missed']),
  detail               TEXT,
  -- Failing checks in a row. An alert needs at least two: a host waking from
  -- sleep can tick the check before Mailbox Watch has reconnected.
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  failing_since        TIMESTAMPTZ,
  last_checked_at      TIMESTAMPTZ NOT NULL,
  last_ok_at           TIMESTAMPTZ,
  -- The last alert SMTP accepted in the current failing episode, and what it
  -- was about. NULL once healthy again. A failed send leaves these unchanged,
  -- so the next check tries again.
  last_alerted_at      TIMESTAMPTZ,
  alerted_problems     TEXT[],
  CONSTRAINT mailbox_health_consistent CHECK (
    (healthy AND cardinality(problems) = 0 AND consecutive_failures = 0 AND failing_since IS NULL)
    OR (NOT healthy AND cardinality(problems) > 0 AND consecutive_failures > 0 AND failing_since IS NOT NULL))
);

COMMENT ON TABLE mailbox_health IS
  'Workflow 6 IMAP health check state (Section 9): one row, written every check by the IMAP Health '
  'workflow, so alerts fire once per problem with reminders instead of every tick.';

COMMIT;

-- Migration 021 -- the Send safety brake (operator decision 2026-10-19).
--
-- The operator raised the daily send cap from 20 to 50 (Master Ref Section 5)
-- and asked for a brake to go with it (Section 9, Workflow 6): "if bounces over
-- the last 3 days exceed 3% of sends, or any spam complaint arrives, Send
-- pauses and I get an alert." Three pieces:
--
--   * inbound_messages.spam_complaint -- set by Mailbox Watch's classifier: a
--     mailbox provider's feedback-loop report (ARF), or a person saying our
--     email is spam in what they wrote above the quoted thread.
--
--   * send_pauses -- the brake's latch. A row with cleared_at NULL pauses Send
--     (Decide Send refuses and so does Claim Send, on the table). It stays until
--     the operator clears it; it never lifts on its own, because a complaint
--     does not expire and a bounce rate that falls only because nothing was sent
--     is not a recovery. One open row per reason at most.
--
--   * send_brake_stats(as_of, window_days) -- the numbers, defined once, read by
--     the Send workflow's Check Brake (which latches) and Load Send State
--     (which shows them). Bounces are counted on the sends of the window: of
--     the confirmed emails sent in it, how many came back. The window starts at
--     the later of `as_of - window_days` and the last time the operator cleared a
--     bounce-rate pause, so clearing one starts the count afresh rather than
--     re-tripping on the bounces just reviewed; complaints count from the last
--     cleared complaint pause, for the same reason.
--
-- Additive only: nothing existing changes meaning, so the workflows running
-- before this migration's Send and Mailbox Watch are published keep working.

ALTER TABLE inbound_messages
  ADD COLUMN IF NOT EXISTS spam_complaint boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS send_pauses (
  id           bigserial PRIMARY KEY,
  reason       text        NOT NULL CHECK (reason IN ('bounce-rate', 'spam-complaint')),
  detail       text        NOT NULL,
  evidence     jsonb       NOT NULL DEFAULT '{}'::jsonb,
  tripped_at   timestamptz NOT NULL DEFAULT now(),
  alerted_at   timestamptz,
  cleared_at   timestamptz,
  cleared_note text,
  CONSTRAINT send_pauses_cleared_after_tripped CHECK (cleared_at IS NULL OR cleared_at >= tripped_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS send_pauses_one_open_per_reason
  ON send_pauses (reason) WHERE cleared_at IS NULL;

CREATE OR REPLACE FUNCTION send_brake_stats(as_of timestamptz, window_days int)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
  WITH since AS (
    SELECT greatest(as_of - make_interval(days => window_days),
                    coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'bounce-rate'),
                             '-infinity'::timestamptz)) AS bounce_from,
           coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'spam-complaint'),
                    '-infinity'::timestamptz) AS complaint_from
  ),
  sends AS (
    SELECT o.lead_id, o.outcome, l.domain
      FROM outreach_log o
      JOIN leads l ON l.id = o.lead_id, since
     WHERE o.channel = 'email'
       AND o.message_id IS NOT NULL          -- a confirmed send, not a claim
       AND o.sent_at >= since.bounce_from
       AND o.sent_at <= as_of
  ),
  complaints AS (
    SELECT i.message_id, i.from_addr, i.subject, i.lead_id, i.processed_at
      FROM inbound_messages i, since
     WHERE i.spam_complaint
       AND i.processed_at > since.complaint_from
       AND i.processed_at <= as_of
  )
  SELECT jsonb_build_object(
    'bounce_from', (SELECT bounce_from FROM since),
    'sends',       (SELECT count(*) FROM sends),
    'bounced',     (SELECT count(*) FROM sends WHERE outcome = 'bounced'),
    'bounced_domains', (SELECT coalesce(jsonb_agg(DISTINCT domain), '[]'::jsonb) FROM sends WHERE outcome = 'bounced'),
    'complaints',  (SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'message_id', message_id, 'from', from_addr, 'subject', subject, 'lead_id', lead_id,
                       'at', processed_at) ORDER BY processed_at), '[]'::jsonb) FROM complaints))
$fn$;

COMMENT ON TABLE send_pauses IS
  'Send safety brake (Section 9, Workflow 6). An open row (cleared_at IS NULL) pauses Send; clear it to resume: '
  'UPDATE send_pauses SET cleared_at = now(), cleared_note = ''why'' WHERE cleared_at IS NULL;';

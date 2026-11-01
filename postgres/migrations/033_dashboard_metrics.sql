-- Migration 033 -- the dashboard's metric layer and its read-only role (dashboard v1, operator brief 2026-11-01).
--
-- WHAT. A schema `metrics` of views, one per dashboard question, and a login role `nova_dashboard` that can
-- read those views and nothing else. Grafana (docker-compose service `grafana`, dashboard/README.md) reads
-- only through that role. Every view carries `niche` (lead_niche(), migration 025, 'none' for a lead in no
-- niche -- the Daily Digest's convention), so a new niche is a new filter value, not new panels.
--
-- THE DEFINITIONS ARE THE SYSTEM'S OWN, not new ones (Master Ref Section 9, Workflow 6):
--   sends_daily        the warm-up ceiling's count: Load Send State's `counted` (every external message the
--                      Sent mirror holds, plus open claims), by the sender's day (Asia/Karachi), and the
--                      Section 5 ceiling for that day (week = days since the first external send / 7 + 1)
--   queue_runway       Load Send State's candidate filter (approved email, not a reply, source not held) over
--                      the cold sends of the last 7 days
--   deliverability     send_brake_stats(now(), 3) itself -- the numbers Check Brake trips on -- plus the same
--                      filter split by niche; open send_pauses; spam complaints
--   replies_by_type /  inbound_messages by classification, and the operator's reply_outcome tag (migration 032)
--   latest_replies
--   ab_arms            the Daily Digest's A/B block: template sends by niche and arm, the arm read from the
--                      variant exactly as the digest reads it (substring '(?:^|/)template/([AB])')
--   funnel             each stage counted from the record that stage writes, by niche and source
--   held_by_reason     every pending draft by hold_reason_class(), with the digest's "needs a person" rule
--
-- WHY THE PRIVATE SCHEMA. Postgres checks a view's tables against the view's owner, but runs the functions a
-- view calls as the user reading it -- and lead_niche(), lead_held_source(), hold_needs_person() and
-- send_brake_stats() read tables nova_dashboard cannot. So each is called through a SECURITY DEFINER wrapper
-- in `metrics_private`, a schema nova_dashboard has no USAGE on: the views can call them, the role cannot
-- name them. Each wrapper returns no more than its view shows (a niche code, a boolean, the brake's counts).
--
-- THE ROLE. nova_dashboard: LOGIN, no password here (a NULL password never authenticates) --
-- dashboard/provision_reader.py sets it from NOVASCOUT_DASHBOARD_DB_PASSWORD in .env. Not a superuser, creates
-- nothing, read-only transactions, 15 s statement timeout, five connections. Its only grants: CONNECT on this
-- database, USAGE on `metrics`, SELECT on the views, EXECUTE on the wrappers. And because the role would
-- otherwise inherit PUBLIC's defaults, PUBLIC loses CONNECT/TEMPORARY on every database and USAGE on schema
-- `public` here. Only the superuser `novascout` existed before this migration, and a superuser ignores all of
-- it, so nothing that runs today is affected.
--
-- Re-runnable: the two schemas are dropped and rebuilt (nothing outside them depends on them); the role and
-- the grants are idempotent.
BEGIN;

-- ---------------------------------------------------------------------------------------------------------
-- The role
-- ---------------------------------------------------------------------------------------------------------
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nova_dashboard') THEN
    CREATE ROLE nova_dashboard LOGIN;
  END IF;
END
$do$;
ALTER ROLE nova_dashboard LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 5;
ALTER ROLE nova_dashboard SET default_transaction_read_only = on;
ALTER ROLE nova_dashboard SET statement_timeout = '15s';
ALTER ROLE nova_dashboard SET search_path = metrics;

DO $do$
DECLARE
  db text;
BEGIN
  FOR db IN SELECT datname FROM pg_database WHERE datallowconn LOOP   -- template1 included
    EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', db);
  END LOOP;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO nova_dashboard', current_database());
END
$do$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Large objects are the one write Postgres grants every role by default: with its read-only default switched
-- off (a role may always do that for its own session), nova_dashboard could lo_create() -- measured
-- 2026-11-01 by dashboard/check_reader_role.py, which created object 505740 before this line existed (it was
-- unlinked). Nothing in Nova Scout uses large objects (pg_largeobject_metadata was empty), and every other
-- role is the superuser.
REVOKE EXECUTE ON FUNCTION pg_catalog.lo_create(oid), pg_catalog.lo_creat(integer),
                           pg_catalog.lo_from_bytea(oid, bytea) FROM PUBLIC;

-- ---------------------------------------------------------------------------------------------------------
-- The private schema: constants and the SECURITY DEFINER wrappers
-- ---------------------------------------------------------------------------------------------------------
DROP SCHEMA IF EXISTS metrics CASCADE;
DROP SCHEMA IF EXISTS metrics_private CASCADE;
CREATE SCHEMA metrics_private;
REVOKE ALL ON SCHEMA metrics_private FROM PUBLIC;

-- The numbers the dashboard judges by, each the system's own. dashboard/test_drift_guards.py holds every one
-- equal to its source: brake_days / brake_pct to Section 9's safety-brake sentence (Check Brake's
-- BRAKE_DAYS / BRAKE_BOUNCE_PCT), ab_sample_per_arm to Section 9's A/B sample sentence, sender_zone to
-- docker-compose's GENERIC_TIMEZONE (the day the ceiling counts).
CREATE VIEW metrics_private.constants AS
SELECT 'Asia/Karachi'::text AS sender_zone,
       3                    AS brake_days,
       10                   AS brake_pct,
       100                  AS ab_sample_per_arm,
       7                    AS runway_days;

-- Section 5's warm-up table (week from, max sends a day); the last row is open-ended. Held equal to the doc by
-- the drift guard, as code_decide.js's WARMUP is by the sendtrack build.
CREATE VIEW metrics_private.warmup (week_from, per_day) AS
VALUES (1, 5), (2, 10), (3, 15), (4, 20), (5, 50);

-- Every lead's niche, the one definition (lead_niche, migration 025), 'none' when it is in no niche.
CREATE FUNCTION metrics_private.lead_niches()
RETURNS TABLE (lead_id bigint, niche text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT l.id, coalesce(lead_niche(e.raw_extraction, l.source), 'none')
    FROM leads l
    LEFT JOIN enrichments e ON e.lead_id = l.id;
$fn$;

-- Is this lead's source held by its niche (lead_held_source, migration 031)?
CREATE FUNCTION metrics_private.is_held_source(p_lead_id bigint)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT lead_held_source(p_lead_id) IS NOT NULL;
$fn$;

-- Does a hold of this reason need a person (hold_needs_person, migration 028)?
CREATE FUNCTION metrics_private.needs_person(p_reason text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT hold_needs_person(p_reason);
$fn$;

-- The safety brake's numbers now: send_brake_stats(now(), brake_days), the function Check Brake trips on --
-- its counts only, and the lead of each complaint (for the per-niche split), never an address or a subject.
CREATE FUNCTION metrics_private.brake_now()
RETURNS TABLE (as_of timestamptz, window_days int, bounce_from timestamptz, sends int, bounced int,
               delayed int, complaints int, complaint_lead_ids bigint[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT now(), c.brake_days, (s.b->>'bounce_from')::timestamptz, (s.b->>'sends')::int, (s.b->>'bounced')::int,
         (s.b->>'delayed')::int, jsonb_array_length(s.b->'complaints'),
         ARRAY(SELECT (x->>'lead_id')::bigint FROM jsonb_array_elements(s.b->'complaints') x)
    FROM metrics_private.constants c
    CROSS JOIN LATERAL (SELECT send_brake_stats(now(), c.brake_days) AS b) s;
$fn$;

-- ---------------------------------------------------------------------------------------------------------
-- The views the dashboard reads
-- ---------------------------------------------------------------------------------------------------------
CREATE SCHEMA metrics;
REVOKE ALL ON SCHEMA metrics FROM PUBLIC;

-- The niche filter's values: every niche, and 'none'.
CREATE VIEW metrics.niche_list AS
SELECT n.code AS niche, n.label, n.active
  FROM niches n
UNION ALL
SELECT 'none', 'In no niche (Trialsites, manual, unclassified)', NULL::boolean;

-- One row per sender-day (the last 14, today included) and niche: what counted toward the warm-up ceiling,
-- the cold sends among it, and that day's ceiling. The ceiling belongs to the mailbox, so it is the same on
-- every niche's row of a day; the counts add up across niches to the mailbox's.
CREATE VIEW metrics.sends_daily AS
WITH c AS (
  SELECT * FROM metrics_private.constants
),
ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
-- Load Send State's `counted`, word for word, plus the lead (for the niche).
counted AS (
  SELECT m.sent_at AS at, m.lead_id
    FROM mailbox_sent m
   WHERE m.external
  UNION ALL
  SELECT o.sent_at, o.lead_id
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NULL
),
first_day AS (
  SELECT (min(counted.at) AT TIME ZONE c.sender_zone)::date AS day FROM counted, c GROUP BY c.sender_zone
),
days AS (
  SELECT ((now() AT TIME ZONE c.sender_zone)::date - g)::date AS sender_day, g AS days_ago
    FROM c, generate_series(0, 13) g
),
counted_by AS (
  SELECT (k.at AT TIME ZONE c.sender_zone)::date AS sender_day, coalesce(ln.niche, 'none') AS niche, count(*) AS n
    FROM counted k CROSS JOIN c LEFT JOIN ln ON ln.lead_id = k.lead_id
   GROUP BY 1, 2
),
-- Confirmed cold sends (the workflow's, not a reply): the Daily Digest's SENT without replies.
cold_by AS (
  SELECT (o.sent_at AT TIME ZONE c.sender_zone)::date AS sender_day, coalesce(ln.niche, 'none') AS niche,
         count(*) FILTER (WHERE coalesce(d.variant, '') NOT LIKE 'follow-up-%') AS first_touches,
         count(*) FILTER (WHERE d.variant LIKE 'follow-up-%')                  AS follow_ups
    FROM outreach_log o
    CROSS JOIN c
    LEFT JOIN drafts d ON d.id = o.draft_id
    LEFT JOIN ln ON ln.lead_id = o.lead_id
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL AND coalesce(d.variant, '') NOT LIKE 'reply/%'
   GROUP BY 1, 2
)
SELECT days.sender_day,
       days.days_ago,
       days.days_ago = 0                                                  AS is_today,
       nl.niche,
       coalesce(cb.n, 0)::int                                             AS counted_toward_cap,
       coalesce(cold.first_touches, 0)::int                               AS cold_first_touches,
       coalesce(cold.follow_ups, 0)::int                                  AS cold_follow_ups,
       w.week                                                             AS warmup_week,
       (SELECT wu.per_day FROM metrics_private.warmup wu
         WHERE wu.week_from <= w.week ORDER BY wu.week_from DESC LIMIT 1) AS daily_cap
  FROM days
  CROSS JOIN metrics.niche_list nl
  LEFT JOIN counted_by cb ON cb.sender_day = days.sender_day AND cb.niche = nl.niche
  LEFT JOIN cold_by cold ON cold.sender_day = days.sender_day AND cold.niche = nl.niche
  CROSS JOIN LATERAL (
    SELECT greatest(0, days.sender_day - coalesce((SELECT day FROM first_day), days.sender_day)) / 7 + 1 AS week
  ) w;

-- Per niche: the approved queue Send would draw from, and how long it lasts at the last 7 days' rate.
CREATE VIEW metrics.queue_runway AS
WITH c AS (
  SELECT * FROM metrics_private.constants
),
ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
-- Load Send State's candidate filter, without its 200-row limit.
q AS (
  SELECT coalesce(ln.niche, 'none') AS niche,
         count(*)                                             AS approved,
         count(*) FILTER (WHERE d.variant LIKE 'follow-up-%') AS approved_follow_ups
    FROM drafts d
    LEFT JOIN ln ON ln.lead_id = d.lead_id
   WHERE d.channel = 'email'
     AND d.status = 'approved'
     AND coalesce(d.variant, '') NOT LIKE 'reply/%'
     AND NOT metrics_private.is_held_source(d.lead_id)
   GROUP BY 1
),
s AS (
  SELECT coalesce(ln.niche, 'none') AS niche, count(*) AS sends
    FROM outreach_log o
    CROSS JOIN c
    LEFT JOIN drafts d ON d.id = o.draft_id
    LEFT JOIN ln ON ln.lead_id = o.lead_id
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL AND coalesce(d.variant, '') NOT LIKE 'reply/%'
     AND o.sent_at > now() - make_interval(days => c.runway_days)
   GROUP BY 1
)
SELECT nl.niche,
       coalesce(q.approved, 0)::int                                        AS approved_queue,
       coalesce(q.approved_follow_ups, 0)::int                             AS approved_follow_ups,
       c.runway_days                                                       AS rate_window_days,
       coalesce(s.sends, 0)::int                                           AS cold_sends_in_window,
       round(coalesce(s.sends, 0)::numeric / c.runway_days, 1)             AS sends_per_day,
       CASE WHEN coalesce(s.sends, 0) > 0
            THEN round(coalesce(q.approved, 0) / (s.sends::numeric / c.runway_days), 1) END AS days_left
  FROM metrics.niche_list nl
  CROSS JOIN c
  LEFT JOIN q ON q.niche = nl.niche
  LEFT JOIN s ON s.niche = nl.niche;

-- Per niche: the brake window's sends and bounces, the same filter as send_brake_stats(); and, on every row,
-- the mailbox's own numbers from send_brake_stats() itself -- the brake belongs to the mailbox, not a niche.
CREATE VIEW metrics.deliverability AS
WITH c AS (
  SELECT * FROM metrics_private.constants
),
b AS (
  SELECT * FROM metrics_private.brake_now()
),
ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
-- send_brake_stats()'s `sends` CTE, word for word, with the niche.
sends AS (
  SELECT coalesce(ln.niche, 'none') AS niche, o.outcome, o.bounce_kind
    FROM outreach_log o
    JOIN leads l ON l.id = o.lead_id
    LEFT JOIN ln ON ln.lead_id = o.lead_id, b
   WHERE o.channel = 'email'
     AND o.message_id IS NOT NULL
     AND o.sent_at >= b.bounce_from
     AND o.sent_at <= b.as_of
),
per AS (
  SELECT niche,
         count(*)                                                                             AS sends,
         count(*) FILTER (WHERE outcome = 'bounced' AND bounce_kind IS DISTINCT FROM 'transient') AS bounced,
         count(*) FILTER (WHERE outcome = 'bounced' AND bounce_kind = 'transient')             AS delayed
    FROM sends
   GROUP BY 1
),
complaints AS (
  SELECT coalesce(ln.niche, 'none') AS niche, count(*) AS n
    FROM b
    CROSS JOIN LATERAL unnest(b.complaint_lead_ids) AS x(lead_id)
    LEFT JOIN ln ON ln.lead_id = x.lead_id
   GROUP BY 1
),
complaints_ever AS (
  SELECT coalesce(ln.niche, 'none') AS niche, count(*) AS n
    FROM inbound_messages i
    LEFT JOIN ln ON ln.lead_id = i.lead_id
   WHERE i.spam_complaint
   GROUP BY 1
),
pauses AS (
  SELECT count(*)::int AS n,
         string_agg(p.reason || ' since ' || to_char(p.tripped_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI "UTC"'),
                    '; ' ORDER BY p.id) AS reasons
    FROM send_pauses p
   WHERE p.cleared_at IS NULL
)
SELECT nl.niche,
       b.bounce_from                                                      AS window_from,
       coalesce(per.sends, 0)::int                                        AS sends,
       coalesce(per.bounced, 0)::int                                      AS bounced_permanent,
       coalesce(per.delayed, 0)::int                                      AS delayed,
       coalesce(complaints.n, 0)::int                                     AS spam_complaints_counted,
       coalesce(complaints_ever.n, 0)::int                                AS spam_complaints_ever,
       -- the mailbox's, from send_brake_stats() itself (the same on every row)
       b.window_days                                                      AS brake_window_days,
       c.brake_pct                                                        AS brake_limit_pct,
       b.sends                                                            AS brake_sends,
       b.bounced                                                          AS brake_bounced,
       b.delayed                                                          AS brake_delayed,
       b.complaints                                                       AS brake_complaints,
       CASE WHEN b.sends > 0 THEN round(100.0 * b.bounced / b.sends, 1) ELSE 0 END AS brake_rate_pct,
       -- Check Brake's own test: strictly over the share, or any complaint
       (b.sends > 0 AND b.bounced * 100.0 > c.brake_pct * b.sends) OR b.complaints > 0 AS brake_would_trip,
       pauses.n                                                           AS open_pauses,
       coalesce(pauses.reasons, 'none')                                   AS open_pause_reasons
  FROM metrics.niche_list nl
  CROSS JOIN b
  CROSS JOIN c
  CROSS JOIN pauses
  LEFT JOIN per ON per.niche = nl.niche
  LEFT JOIN complaints ON complaints.niche = nl.niche
  LEFT JOIN complaints_ever ON complaints_ever.niche = nl.niche;

-- Per niche and type: what came back (inbound_messages, Mailbox Watch's classification), ever and in the last
-- 7 days, with the operator's reply_outcome tags on replies. 'out-of-office' is the `auto-reply` class (it
-- also holds DMARC reports and other machine mail); 'unmatched' is mail tied to no lead.
CREATE VIEW metrics.replies_by_type AS
WITH ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
types (type, classification, ord) AS (
  VALUES ('reply', 'reply', 1), ('opt-out', 'opt-out', 2), ('out-of-office', 'auto-reply', 3),
         ('bounce', 'bounce', 4), ('unmatched', 'unmatched', 5)
),
agg AS (
  SELECT coalesce(ln.niche, 'none') AS niche, i.classification,
         count(*)                                                                             AS total,
         count(*) FILTER (WHERE coalesce(i.received_at, i.processed_at) >= now() - interval '7 days') AS last_7_days,
         count(*) FILTER (WHERE i.reply_outcome = 'positive')                                 AS tagged_positive,
         count(*) FILTER (WHERE i.reply_outcome = 'meeting booked')                           AS tagged_meeting_booked,
         count(*) FILTER (WHERE i.reply_outcome = 'neutral')                                  AS tagged_neutral,
         count(*) FILTER (WHERE i.reply_outcome = 'negative')                                 AS tagged_negative,
         count(*) FILTER (WHERE i.classification = 'reply' AND i.reply_outcome IS NULL)      AS untagged,
         count(*) FILTER (WHERE i.bounce_kind = 'permanent')                                  AS bounce_permanent,
         count(*) FILTER (WHERE i.bounce_kind = 'transient')                                  AS bounce_transient,
         count(*) FILTER (WHERE i.spam_complaint)                                             AS spam_complaints
    FROM inbound_messages i
    LEFT JOIN ln ON ln.lead_id = i.lead_id
   GROUP BY 1, 2
)
SELECT nl.niche, t.type, t.ord AS type_order,
       coalesce(a.total, 0)::int                 AS total,
       coalesce(a.last_7_days, 0)::int           AS last_7_days,
       coalesce(a.tagged_positive, 0)::int       AS tagged_positive,
       coalesce(a.tagged_meeting_booked, 0)::int AS tagged_meeting_booked,
       coalesce(a.tagged_neutral, 0)::int        AS tagged_neutral,
       coalesce(a.tagged_negative, 0)::int       AS tagged_negative,
       coalesce(a.untagged, 0)::int              AS untagged,
       coalesce(a.bounce_permanent, 0)::int      AS bounce_permanent,
       coalesce(a.bounce_transient, 0)::int      AS bounce_transient,
       coalesce(a.spam_complaints, 0)::int       AS spam_complaints
  FROM metrics.niche_list nl
  CROSS JOIN types t
  LEFT JOIN agg a ON a.niche = nl.niche AND a.classification = t.classification;

-- Every human answer to an email (reply or opt-out) matched to a lead, newest first in the panel.
CREATE VIEW metrics.latest_replies AS
WITH ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
)
SELECT coalesce(ln.niche, 'none')                                          AS niche,
       coalesce(i.received_at, i.processed_at)                             AS received_at,
       i.classification                                                    AS type,
       i.reply_outcome::text                                               AS outcome,
       l.company_name,
       l.domain,
       l.country,
       (SELECT substring(d.variant from '(?:^|/)template/([AB])')
          FROM outreach_log o JOIN drafts d ON d.id = o.draft_id
         WHERE o.lead_id = i.lead_id AND d.variant ~ '(^|/)template/[AB]'
         ORDER BY o.sent_at LIMIT 1)                                       AS arm,
       i.subject,
       left(regexp_replace(coalesce(i.body_excerpt, ''), '\s+', ' ', 'g'), 240) AS excerpt
  FROM inbound_messages i
  JOIN leads l ON l.id = i.lead_id
  LEFT JOIN ln ON ln.lead_id = i.lead_id
 WHERE i.classification IN ('reply', 'opt-out');

-- The A/B test, per niche and arm: the Daily Digest's "every template email sent so far" block, plus the
-- operator's positive tags and the sample the comparison waits for. The arm is read from the variant
-- exactly as the digest reads it. No winner is computed here or anywhere on the dashboard.
CREATE VIEW metrics.ab_arms AS
WITH c AS (
  SELECT * FROM metrics_private.constants
),
ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
sends AS (
  SELECT coalesce(ln.niche, 'none')                        AS niche,
         substring(d.variant from '(?:^|/)template/([AB])') AS arm,
         o.lead_id, d.variant LIKE 'follow-up-%' AS follow_up, o.replied, o.outcome
    FROM outreach_log o
    JOIN drafts d ON d.id = o.draft_id
    LEFT JOIN ln ON ln.lead_id = o.lead_id
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL
     AND d.variant ~ '(^|/)template/[AB]'
),
positive AS (
  SELECT DISTINCT i.lead_id
    FROM inbound_messages i
   WHERE i.classification = 'reply' AND i.reply_outcome IN ('positive', 'meeting booked')
),
agg AS (
  SELECT s.niche, s.arm,
         count(*) FILTER (WHERE NOT s.follow_up)                                AS first_touches,
         count(*) FILTER (WHERE s.follow_up)                                    AS follow_ups,
         count(DISTINCT s.lead_id) FILTER (WHERE s.replied)                     AS replied_leads,
         count(DISTINCT s.lead_id) FILTER (WHERE s.lead_id IN (SELECT lead_id FROM positive)) AS positive_leads,
         count(*) FILTER (WHERE s.outcome = 'bounced')                          AS bounced
    FROM sends s
   GROUP BY 1, 2
),
grid AS (
  SELECT nl.niche, arm.arm,
         coalesce(a.first_touches, 0)::int  AS first_touches,
         coalesce(a.follow_ups, 0)::int     AS follow_ups,
         coalesce(a.replied_leads, 0)::int  AS replied_leads,
         coalesce(a.positive_leads, 0)::int AS positive_leads,
         coalesce(a.bounced, 0)::int        AS bounced
    FROM metrics.niche_list nl
    CROSS JOIN (VALUES ('A'), ('B')) AS arm (arm)
    LEFT JOIN agg a ON a.niche = nl.niche AND a.arm = arm.arm
)
SELECT g.niche, g.arm, g.first_touches, g.follow_ups, g.replied_leads, g.positive_leads, g.bounced,
       c.ab_sample_per_arm                                                     AS sample_needed,
       g.first_touches || ' of ' || c.ab_sample_per_arm || ' needed'           AS sample_progress,
       CASE WHEN g.first_touches > 0 THEN round(100.0 * g.replied_leads / g.first_touches, 1) END  AS reply_rate_pct,
       CASE WHEN g.first_touches > 0 THEN round(100.0 * g.positive_leads / g.first_touches, 1) END AS positive_rate_pct,
       CASE WHEN min(g.first_touches) OVER (PARTITION BY g.niche) >= c.ab_sample_per_arm
            THEN 'sample reached in both arms'
            ELSE 'collecting: no winner before ' || c.ab_sample_per_arm || ' first touches in each arm' END AS status
  FROM grid g
  CROSS JOIN c;

-- The funnel, per niche and source: a lead is counted at a stage when the record that stage writes exists.
CREATE VIEW metrics.funnel AS
WITH ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
dr AS (
  SELECT d.lead_id,
         true                                                                              AS drafted,
         bool_or(d.status IN ('approved', 'sent') OR d.approved_at IS NOT NULL)            AS approved
    FROM drafts d
   WHERE d.channel = 'email' AND coalesce(d.variant, '') NOT LIKE 'reply/%'
   GROUP BY 1
),
ol AS (
  SELECT o.lead_id, bool_or(o.replied) AS replied
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL
   GROUP BY 1
)
SELECT coalesce(ln.niche, 'none')                                                   AS niche,
       l.source,
       count(*)::int                                                                AS leads,
       count(e.lead_id)::int                                                        AS enriched,
       count(s.lead_id)::int                                                        AS scored,
       count(*) FILTER (WHERE s.disqualified)::int                                  AS disqualified,
       count(*) FILTER (WHERE nullif(btrim(ct.email), '') IS NOT NULL)::int         AS contact_found,
       count(dr.lead_id)::int                                                       AS drafted,
       count(*) FILTER (WHERE dr.approved)::int                                     AS approved,
       count(ol.lead_id)::int                                                       AS sent,
       count(*) FILTER (WHERE ol.replied)::int                                      AS replied
  FROM leads l
  LEFT JOIN ln ON ln.lead_id = l.id
  LEFT JOIN enrichments e ON e.lead_id = l.id
  LEFT JOIN scores s ON s.lead_id = l.id
  LEFT JOIN contacts ct ON ct.lead_id = l.id
  LEFT JOIN dr ON dr.lead_id = l.id
  LEFT JOIN ol ON ol.lead_id = l.id
 GROUP BY 1, 2;

-- The exceptions queue, as the Daily Digest counts it: every pending draft by hold_reason_class(), and how
-- many of them need a person (the digest's rule: an email, not a note, an allowlisted class, not a held
-- source).
CREATE VIEW metrics.held_by_reason AS
WITH ln AS MATERIALIZED (
  SELECT * FROM metrics_private.lead_niches()
),
h AS (
  SELECT coalesce(ln.niche, 'none')       AS niche,
         hold_reason_class(d.hold_reason) AS hold_class,
         d.channel,
         (d.channel = 'email'
          AND coalesce(d.variant, '') NOT LIKE 'low-context%'
          AND coalesce(d.variant, '') NOT LIKE 'no-send-clock%'
          AND metrics_private.needs_person(d.hold_reason)
          AND NOT metrics_private.is_held_source(d.lead_id)) AS needs_person
    FROM drafts d
    LEFT JOIN ln ON ln.lead_id = d.lead_id
   WHERE d.status = 'pending'
)
SELECT h.niche, h.hold_class, h.channel,
       count(*)::int                                   AS held,
       count(*) FILTER (WHERE h.needs_person)::int     AS needs_person,
       EXISTS (SELECT 1 FROM hold_reason_classes k WHERE k.class = h.hold_class) AS class_known
  FROM h
 GROUP BY 1, 2, 3;

-- ---------------------------------------------------------------------------------------------------------
-- Grants: the views, the wrappers they call, nothing else
-- ---------------------------------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA metrics TO nova_dashboard;
GRANT SELECT ON ALL TABLES IN SCHEMA metrics TO nova_dashboard;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA metrics_private FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA metrics_private TO nova_dashboard;

COMMIT;

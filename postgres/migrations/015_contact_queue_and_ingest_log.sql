-- Migration 015 -- the contact-lookup queue stops starving, and ingestion stops
-- depending on a clock (2026-10-07).
--
-- Two unrelated tables, one migration, because both are a workflow's own memory
-- of what it has already done -- the same role mailbox_sync, digest_log and
-- mailbox_health already play for Workflow 6.
--
-- 1. contact_attempts -- Workflow 3b (Contact Lookup).
--
-- THE BUG. The queue is `status='scored' AND fit_score >= 50 AND no contacts
-- row`, ordered `fit_score DESC` and capped at 10. A lead with no scraped
-- address, no first-party address on its own site and no founder LinkedIn goes
-- to Apollo, which this account's Free plan refuses (403 API_INACCESSIBLE,
-- Section 9) -- so nothing is written, the lead stays `scored`, and it comes
-- back at the top of the next hour's batch. Nine such leads were waiting on
-- 2026-10-07 (35, 53, 92, 94, 212, 498, 500, 658, 733); at ten, the batch is
-- full of them and no new lead is ever looked up again. The queue had no memory
-- of a lookup that found nothing, and `contacts` has nowhere to record one: a
-- tombstone row says "asked, nobody there", which is a different thing from
-- "asked, Apollo would not answer", and Section 8 locks that table's columns.
--
-- THE FIX, and why it is a count and not just a flag. `last_outcome` retires a
-- lead the moment every source has genuinely answered ('exhausted'), and
-- `attempts` bounds everything else: a refusal whose shape nobody recognised
-- still stops after max_attempts instead of starving the queue for ever. The
-- lead stays `scored` -- Section 8's status flow has no state for "asked and
-- found nothing", and `contact_found` would put a contactless lead in front of
-- Workflow 4's grounding guard -- and surfaces in `needs_manual_contact` below.
--
-- TO PUT A LEAD BACK IN THE QUEUE, delete its row. That is the one move to make
-- after an Apollo plan upgrade, alongside deleting the LinkedIn-only contacts
-- Section 9 names (`apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL`).
--
-- 2. ingest_log -- Workflow 1 (Ingestion). One row per version of
-- data/ichgcp_leads.csv that has been ingested, keyed on a fingerprint of the
-- rows themselves. Ingestion used to fire once a day at 23:00 Asia/Karachi --
-- a single fixed slot on a machine that is off at night, so on most nights it
-- never ran at all. It now ticks every 30 minutes like every other queue stage
-- and does its work when the file has changed since the last ingest, which is
-- what this table answers. Section 7's idempotency rule, applied to a workflow
-- whose input is a file rather than a queue.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. contact_attempts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS contact_attempts (
  lead_id      BIGINT PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
  attempts     INTEGER     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  -- 'exhausted' every source answered and there is no contact: a scraped
  --             address, a first-party address published on the company's own
  --             site, a founder LinkedIn profile, and Apollo, all asked.
  --             Permanent until somebody deletes the row.
  -- 'refused'   a call did not answer (plan gate, rate limit, timeout). Not
  --             evidence about the company, so it is retried -- but counted.
  last_outcome TEXT        NOT NULL CHECK (last_outcome IN ('exhausted', 'refused')),
  detail       TEXT,
  first_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE contact_attempts IS
  'Workflow 3b''s memory of a lookup that produced no contact (migration 015). '
  'The batch query skips a lead with last_outcome = ''exhausted'' or attempts at '
  'the stage''s max_attempts, so a lead nobody can find stops taking a slot in '
  'every batch. Delete a row to put that lead back in the queue.';

-- ---------------------------------------------------------------------------
-- 2. needs_manual_contact -- the NocoDB view
--
-- Section 7 prices this work: "when a qualifying lead has no contact, that's a
-- two-minute manual look-up in the review queue". This is that queue. It is
-- READ-ONLY, like replied_queue and disqualified_queue and for the reason
-- migration 006 records at length: a view has no primary key, so a write
-- through it cannot be scoped to one row. To action a lead here, insert the
-- contact you found into `contacts` and set the lead to 'contact_found'.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW needs_manual_contact AS
SELECT l.id                                        AS lead_id,
       s.fit_score,
       l.company_name,
       l.domain,
       l.country,
       a.last_outcome,
       a.attempts,
       a.detail                                    AS why,
       e.founder_name,
       e.raw_extraction->>'founder_title'          AS founder_title,
       e.founder_linkedin,
       e.site_quality_notes,
       s.rationale,
       a.first_at,
       a.last_at,
       l.status                                    AS lead_status
  FROM leads l
  JOIN contact_attempts a ON a.lead_id = l.id
  JOIN scores s           ON s.lead_id = l.id
  LEFT JOIN enrichments e ON e.lead_id = l.id
  LEFT JOIN contacts c    ON c.lead_id = l.id
 WHERE l.status = 'scored'
   AND s.disqualified = false
   -- A lead that has since been given a usable channel -- by hand, or by a
   -- later run after a plan upgrade -- is not waiting for anybody.
   AND (c.lead_id IS NULL OR (c.email IS NULL AND c.linkedin_url IS NULL));

COMMENT ON VIEW needs_manual_contact IS
  'Workflow 3b: qualifying leads the automatic sources could not reach, with why '
  '(migration 015). READ-ONLY -- see migration 006. To action one: add the contact '
  'to `contacts` and set the lead to ''contact_found''.';

-- ---------------------------------------------------------------------------
-- 3. ingest_log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_log (
  csv_fingerprint TEXT PRIMARY KEY,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  rows_in_csv     INTEGER     NOT NULL CHECK (rows_in_csv >= 0),
  leads_before    INTEGER     NOT NULL CHECK (leads_before >= 0),
  leads_after     INTEGER     NOT NULL CHECK (leads_after >= 0),
  CONSTRAINT ingest_log_no_shrink CHECK (leads_after >= leads_before)
);

COMMENT ON TABLE ingest_log IS
  'Workflow 1: one row per version of data/ichgcp_leads.csv that has been '
  'ingested, keyed on a fingerprint of its rows (migration 015). The workflow '
  'ticks every 30 minutes and works only when the fingerprint is new, so it '
  'catches up whenever the host is on instead of needing to be on at 23:00. '
  'leads_before/leads_after record what the ingest actually added.';

COMMIT;

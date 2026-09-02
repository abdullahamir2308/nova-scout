-- Nova Scout — one enrichment row per lead.
--
-- The enrichment workflow re-processes a lead whenever it is put back on the
-- queue (a re-queue after a bad extraction, a retried batch). The write step
-- was a plain INSERT, so each re-process silently added a SECOND row for the
-- same lead and every downstream join on lead_id started fanning out. Nothing
-- errored; the data just quietly doubled.
--
-- This makes the invariant structural rather than something the write step has
-- to remember, and gives its ON CONFLICT clause an arbiter to target.
--
-- Applying this to a database that already has duplicates fails loudly. That is
-- deliberate: collapse them by hand (keep the newest row per lead) rather than
-- let a migration guess which extraction was the good one.

ALTER TABLE enrichments
    ADD CONSTRAINT enrichments_lead_id_key UNIQUE (lead_id);

-- Nova Scout — one score row per lead.
--
-- Same lesson migration 002 learned on `enrichments`, applied before it can
-- bite: Workflow 3 is queue-driven and re-scoring is expected (a re-queued
-- lead, a reweighted formula, a re-run after a ClinicalTrials.gov outage). A
-- plain INSERT would add a second score row each time and every downstream
-- join on lead_id would start fanning out — silently, with no error.
--
-- The constraint also gives the write step's ON CONFLICT clause an arbiter to
-- target, which is what makes re-scoring safe by construction rather than by
-- remembering to DELETE first.
--
-- Applying this to a database that already has duplicates fails loudly, on
-- purpose: collapse them by hand rather than let a migration guess which score
-- was the good one.

ALTER TABLE scores
    ADD CONSTRAINT scores_lead_id_key UNIQUE (lead_id);

-- Nova Scout — one contact row per lead.
--
-- The third time this lesson has been paid for: migration 002 learned it on
-- `enrichments` after re-processing silently created second rows, migration 003
-- applied it pre-emptively to `scores`. `contacts` is written by a queue-driven
-- workflow with exactly the same re-run profile — a lead re-queried after an
-- Apollo outage, after a plan upgrade, or after the fit-score threshold moves —
-- so a plain INSERT would accumulate a row per attempt and every downstream join
-- on lead_id would fan out without erroring.
--
-- The constraint also gives Workflow 3b's write step an ON CONFLICT arbiter,
-- which is what makes re-running safe by construction rather than by remembering
-- to DELETE first.
--
-- The tradeoff this locks in: one contact per lead, not a contact list per
-- company. That matches the rest of the pipeline — `drafts` are per lead and
-- Section 8's status flow has a single `contact_found` state — and Section 12's
-- ICP names one buyer per company (founder, MD, or BD director). Storing a
-- roster would need a different downstream design, not just a looser constraint.
--
-- Applying this to a database that already has duplicates fails loudly, on
-- purpose: collapse them by hand rather than let a migration guess which contact
-- was the right one.

ALTER TABLE contacts
    ADD CONSTRAINT contacts_lead_id_key UNIQUE (lead_id);

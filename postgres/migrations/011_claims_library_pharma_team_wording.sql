-- Migration 011 -- carry the pharma-team terminology fix into the seed (2026-09-22).
--
-- MasterRef section 12, "Terminology -- decided 2026-09-21": the pharma-side
-- party is a "pharma team"; "sponsor" belongs only to the CRO's own registered
-- status ("You're sponsoring ..."). Migration 010's seed predates that decision
-- and still says "sponsor" for the pharma side in P1-P3, and O2 also still
-- promises "in seconds" where the one-pager only supports "in real time". That
-- edit was made live, in the table, by design (migration 010: "Written by
-- humans only ... ON CONFLICT DO NOTHING: re-running this migration never
-- overwrites a human's edit"), so a database seeded fresh from 010 alone would
-- start with the exact collision `audit_drafts_vs_onepager.py` was written to
-- catch: a hook that reads "You're sponsoring ..." next to a library line that
-- calls the pharma side "sponsor" too.
--
-- This migration only catches up rows that were never edited: it updates a row
-- to the current wording only WHEN its body still exactly matches migration
-- 010's original seed text. A row already carrying the new wording is a no-op
-- (body no longer matches the old text). A row a human customized to something
-- else entirely is left alone for the same reason -- this is not the row's
-- owner, the operator is.

BEGIN;

UPDATE claims_library SET
  body = $$When a pharma team shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?$$
WHERE code = 'P1'
  AND body = $$When a sponsor shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?$$;

UPDATE claims_library SET
  body = $$How many pharma-team inquiries reach you only after that team has already moved on?$$
WHERE code = 'P2'
  AND body = $$How many sponsor inquiries reach you only after the sponsor has already moved on?$$;

UPDATE claims_library SET
  body = $$When a pharma team evaluates you after hours, does a qualified lead land with your team — or a contact form nobody sees until morning?$$
WHERE code = 'P3'
  AND body = $$When a sponsor evaluates you after hours, does a qualified lead land with your team — or a contact form nobody sees until morning?$$;

UPDATE claims_library SET
  body = $$We built Nova so an 11pm pharma-team inquiry is answered in real time and qualified, not the next morning — and lands in the tools you already use.$$
WHERE code = 'O2'
  AND body = $$We built Nova so an 11pm sponsor inquiry is answered and qualified in seconds, not the next morning — and lands in the tools you already use.$$;

UPDATE claims_library SET
  body = $$Nova, which we built, answers pharma teams from your own SOPs and service pages and hands your team a qualified lead, in your system, not ours.$$
WHERE code = 'O3'
  AND body = $$Nova, which we built, answers sponsors from your own SOPs and service pages and hands your team a qualified lead, in your system, not ours.$$;

COMMIT;

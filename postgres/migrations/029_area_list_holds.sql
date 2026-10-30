-- Migration 029 -- holds decided by the operator on 2026-10-30 (later): curalta.com, and the areas facts
-- that came from a list no site wrote.
--
-- WHY.
--   * curalta.com (draft 773, ClinicalTrials.gov, approved) -- the precision audit's one ClinicalTrials.gov
--     identity miss (docs/research_site_niche.md section 7.5): a podiatry group's site, info@ is the podiatry
--     inbox. 027 left it approved; the operator holds it now.
--   * Four approved site drafts (466 diex.ca, 526 ezytrials.com, 768 apexresearchgroup.net, 771
--     titanclinicalresearch.com) say "<company> works in oncology and cardiovascular" because Enrichment
--     listed all 17 specific areas of the taxonomy (18 with Other), and the template names the two the
--     taxonomy ranks first. That is the extraction reciting the taxonomy, not a fact about the site.
--   * The same pattern among the Overture drafts migration 026 holds: an areas fact from a list of more than
--     six specific areas (HIDRA 16, Clinademi 9, ...; the root fix and the measurement behind six are
--     MAX_LISTED_AREAS in n8n/drafting/code_template.js). Lifting 026's hold would send them, so their reason
--     no longer starts "db-guard: source sites-overture is held" and 026's lift recipe leaves them held.
--     Drafting writes no new one: the root fix gives such a list no areas fact.
--
-- WHAT. Each draft goes to 'pending' with an "operator hold 2026-10-30: ..." reason -- 027's shape, which the
-- Daily Digest counts and never codes (migration 028). Sent drafts are never touched. Two drafts with the
-- pattern were approved by the operator's APPROVE on 2026-10-30 (812, 933) and are left exactly as they are;
-- so are the approved CRO and ClinicalTrials.gov drafts with 7-16 areas (not decided). Re-runnable: a second
-- run changes nothing. Applied with postgres/apply_drafts_migration.py, which snapshots every draft, runs this
-- in one transaction, and commits only if exactly the expected drafts changed and only in these columns.
BEGIN;

-- 1. The five decided holds, by domain (each lead has one unsent email draft).
UPDATE drafts d
   SET status = 'pending',
       hold_reason = h.reason
  FROM leads l,
       (VALUES
         ('curalta.com',               'operator hold 2026-10-30: a podiatry group''s site, not the research facility''s -- info@ is the podiatry inbox (precision audit)'),
         ('diex.ca',                   'operator hold 2026-10-30: areas fact from the full taxonomy list (17 specific areas), not from the site'),
         ('ezytrials.com',             'operator hold 2026-10-30: areas fact from the full taxonomy list (17 specific areas), not from the site'),
         ('apexresearchgroup.net',     'operator hold 2026-10-30: areas fact from the full taxonomy list (17 specific areas), not from the site'),
         ('titanclinicalresearch.com', 'operator hold 2026-10-30: areas fact from the full taxonomy list (17 specific areas), not from the site')
       ) AS h(domain, reason)
 WHERE l.id = d.lead_id
   AND lower(l.domain) = h.domain
   AND d.channel = 'email'
   AND d.status IN ('approved', 'pending')
   AND d.hold_reason IS DISTINCT FROM h.reason;

-- 2. Overture drafts held by 026 whose areas fact came from more than six specific areas.
UPDATE drafts d
   SET hold_reason = 'operator hold 2026-10-30: areas fact from an implausible list (' || x.n
                     || ' specific areas) -- redraft before releasing; kept out of 026''s lift'
  FROM (SELECT d2.id,
               (SELECT count(DISTINCT lower(a)) FROM unnest(e.therapeutic_areas) a WHERE lower(a) <> 'other') AS n
          FROM drafts d2
          JOIN leads l ON l.id = d2.lead_id
          JOIN enrichments e ON e.lead_id = l.id
         WHERE l.source = 'sites-overture'
           AND d2.channel = 'email'
           AND d2.status = 'pending'
           AND d2.variant ~ '(^|/)template/[AB]/areas'
           AND d2.hold_reason LIKE 'db-guard: source sites-overture is held%') x
 WHERE d.id = x.id
   AND x.n > 6;

COMMIT;

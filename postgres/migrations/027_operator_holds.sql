-- Migration 027 -- four drafts held by the operator after the precision audit (2026-10-30).
--
-- WHY. The audit (docs/research_site_niche.md section 7.5,
-- docs/precision_audit_2026-10-30.csv) found four drafts that must not send:
--   * zi-mannheim.de  (ClinicalTrials.gov, approved) -- a psychiatric hospital and
--     public research institute, not an independent research site;
--   * kgkscience.com  (ClinicalTrials.gov, approved) -- a CRO; kept for a later
--     CRO review, not the site template;
--   * honorhealth.com and centerstone.org (Overture, already held by 026) -- the
--     domain is the parent organisation's, not the facility's.
--
-- WHAT. Each draft goes to 'pending' with an "operator hold" reason. The reason
-- deliberately does not start "db-guard: source sites-overture is held", so
-- 026's lift recipe never re-approves the two Overture ones. A person can still
-- release one (APPROVE <code> from the Daily Digest, or the NocoDB grid).
-- Sent drafts are never touched. Re-runnable: a second run changes nothing.
BEGIN;

UPDATE drafts d
   SET status = 'pending',
       hold_reason = h.reason
  FROM leads l,
       (VALUES
         ('zi-mannheim.de',  'operator hold 2026-10-30: psychiatric hospital and public research institute, not a research site (precision audit)'),
         ('kgkscience.com',  'operator hold 2026-10-30: a CRO, not a research site -- kept for a later CRO review (precision audit)'),
         ('honorhealth.com', 'operator hold 2026-10-30: parent organisation''s domain, not the facility''s (precision audit)'),
         ('centerstone.org', 'operator hold 2026-10-30: parent organisation''s domain, not the facility''s (precision audit)')
       ) AS h(domain, reason)
 WHERE l.id = d.lead_id
   AND lower(l.domain) = h.domain
   AND d.channel = 'email'
   AND d.status IN ('approved', 'pending')
   AND d.hold_reason IS DISTINCT FROM h.reason;

COMMIT;

-- Migration 030 -- three Overture drafts back under the source hold (operator, 2026-10-30, later).
--
-- WHY. Drafts 812 (Apex Medical Research Amr), 867 (Intouch Clinical Research Center) and 933 (Liv Medical
-- and Research Center) were held by migration 026 (source sites-overture). On 2026-10-30 15:50-15:58 UTC the
-- operator answered their email-hold codes with APPROVE during the reminder flood (migration 028's WHY), and a
-- human approval did not check the held source (closed by migration 031). The operator puts them back.
--
-- WHAT. Each goes to 'pending' with exactly 026's hold reason, so 026's lift recipe releases them with every
-- other Overture draft and nothing else does. Only while still approved (never a sent draft). Re-runnable: a
-- second run changes nothing. Applied with postgres/apply_drafts_migration.py --expect 812,867,933.
BEGIN;

UPDATE drafts d
   SET status = 'pending',
       hold_reason = 'db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)'
  FROM leads l
 WHERE l.id = d.lead_id
   AND l.source = 'sites-overture'
   AND d.id IN (812, 867, 933)
   AND d.channel = 'email'
   AND d.status = 'approved';

COMMIT;

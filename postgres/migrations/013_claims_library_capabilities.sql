-- Migration 013 -- three claims settled, and the capability each line asserts (2026-10-02).
--
-- 1. THREE CLAIMS, settled by the operator on 2026-10-02 after the code reading
--    migration 012 recorded in each row's `note` (D:\projects\nova-agent-kit):
--
--    D2, BEN-BOOK  Nova does not book anything. capture_sponsor_lead returns the
--                  booking link (CALENDLY_BOOKING_URL, lib/tenants/loader.ts) once
--                  a lead is captured; the sponsor books through it
--                  (lib/agent/tools/capture-sponsor-lead.ts:104). Reworded to
--                  "sends ... your booking link".
--    BEN-247       No SOPs. A tenant's knowledge base is one text file,
--                  tenants/<id>/knowledge-base.md, loaded verbatim into the system
--                  prompt (lib/tenants/loader.ts, app/api/chat/route.ts:70). The
--                  only pipeline that fills it is the website scraper
--                  (tenants/noblepath/scripts/scraper.py), which skips PDFs. There
--                  is no code path for documents a client provides, so the line
--                  says "your own website" and nothing else.
--    BEN-ROUTE     "not in another dashboard" removed: Nova has its own leads
--                  dashboard (app/dashboard, BEN-SEE).
--
--    Same rule as migration 011: a row is reworded only WHILE ITS BODY STILL
--    EXACTLY MATCHES migration 012's seed. A row a human already edited is left
--    alone -- the operator owns the table, not this file. Two CHECKs keep the
--    first two decisions from coming back in a future edit.
--
-- 2. CAPABILITIES. Skill v3 section 3 now says the description and the benefits
--    must not repeat the same capability: D2 ("answers sponsors ... sends them
--    your booking link") next to BEN-247 ("It answers sponsors ...") and BEN-BOOK
--    says two things twice, and the model chose exactly that pairing for 3 of the
--    4 email drafts on 2026-10-02 (drafts 89, 93, 95). A rule about what a line
--    says needs the line to say what it is about, so every description and
--    benefit row now names its capabilities from a closed vocabulary. Workflow 4
--    tells the model which pairs collide and tags a draft whose claim codes repeat
--    a capability (`claim-repeat`); the follow-ups use the same check.
--
-- Re-running is a no-op: the rewordings match only the old text, capabilities
-- are filled only where NULL, and the constraints are dropped and re-added.
-- The pre-v3 (v2) rows that migration 012 replaced are preserved verbatim in
-- postgres/backups/claims_library_v2_pre_012.json.

BEGIN;

UPDATE claims_library SET
  body = $b$an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link$b$,
  note = 'SETTLED 2026-10-02 (migration 013). Was: "... answers sponsors, qualifies them, and books the call". Nova '
         'does not book: after capture_sponsor_lead succeeds it returns the booking link from CALENDLY_BOOKING_URL '
         '(lib/tenants/loader.ts; lib/agent/tools/capture-sponsor-lead.ts), only when that is set.'
WHERE code = 'D2'
  AND body = $b$an AI intake assistant for your website that answers sponsors, qualifies them, and books the call$b$;

UPDATE claims_library SET
  body = $b$It sends qualified sponsors your booking link, so they can book a call with your team.$b$,
  note = 'SETTLED 2026-10-02 (migration 013). Was: "Qualified sponsors book a call straight into your calendar." '
         'Nova sends the booking link (CALENDLY_BOOKING_URL) in its confirmation after a lead is captured, only when '
         'that is set; the sponsor books through the link. Nothing reaches a calendar from Nova.'
WHERE code = 'BEN-BOOK'
  AND body = $b$Qualified sponsors book a call straight into your calendar.$b$;

UPDATE claims_library SET
  body = $b$It answers sponsors from your own website, in real time, at any hour.$b$,
  note = 'SETTLED 2026-10-02 (migration 013). Was: "It answers sponsors from your own SOPs and service pages, in '
         'real time, at any hour." The knowledge base is one text file per tenant (tenants/<id>/knowledge-base.md), '
         'filled only by the website scraper (tenants/noblepath/scripts/scraper.py, which skips PDFs) and loaded '
         'verbatim into the system prompt (app/api/chat/route.ts). There is no code path for documents a client '
         'provides, so the line claims the website only.'
WHERE code = 'BEN-247'
  AND body = $b$It answers sponsors from your own SOPs and service pages, in real time, at any hour.$b$;

UPDATE claims_library SET
  body = $b$Leads land where your team already works.$b$,
  note = 'SETTLED 2026-10-02 (migration 013). Was: "Leads land where your team already works, not in another '
         'dashboard." deliverLead() emails the team and pushes the lead to a CRM and a spreadsheet when configured '
         '(lib/integrations/); Nova also has its own leads dashboard (app/dashboard, BEN-SEE), so the "not in '
         'another dashboard" clause was dropped.'
WHERE code = 'BEN-ROUTE'
  AND body = $b$Leads land where your team already works, not in another dashboard.$b$;

-- What each description and benefit line asserts. Two lines in one message that
-- share a capability say the same thing twice (skill v3 section 3).
ALTER TABLE claims_library ADD COLUMN IF NOT EXISTS capabilities text[];

UPDATE claims_library SET capabilities = c.caps
  FROM (VALUES
    ('D1',          ARRAY['qualifies', 'captures-lead']),
    ('D2',          ARRAY['answers', 'qualifies', 'booking-link']),
    ('BEN-247',     ARRAY['answers']),
    ('BEN-CAPTURE', ARRAY['captures-lead', 'study-details']),
    ('BEN-BRIEF',   ARRAY['study-details']),
    ('BEN-BOOK',    ARRAY['booking-link']),
    ('BEN-ROUTE',   ARRAY['routes-leads']),
    ('BEN-SEE',     ARRAY['dashboard']),
    ('BEN-DECK',    ARRAY['deck']),
    ('BEN-FIT',     ARRAY['configured'])
  ) AS c(code, caps)
 WHERE claims_library.code = c.code
   AND claims_library.capabilities IS NULL;

ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_capabilities_vocabulary;
ALTER TABLE claims_library ADD CONSTRAINT claims_capabilities_vocabulary
  CHECK (capabilities IS NULL OR capabilities <@ ARRAY['answers', 'qualifies', 'captures-lead', 'study-details',
                                                       'booking-link', 'deck', 'routes-leads', 'dashboard',
                                                       'configured']::text[]);

-- A description or benefit line with text must say what it is about, or the
-- no-repeat rule cannot see it; no other slot carries capabilities.
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_capabilities_where_needed;
ALTER TABLE claims_library ADD CONSTRAINT claims_capabilities_where_needed
  CHECK (CASE WHEN slot IN ('description', 'benefit')
              THEN body IS NULL OR coalesce(cardinality(capabilities), 0) >= 1
              ELSE capabilities IS NULL END);

-- The two settled claims, pinned. Nova does not book (it sends the link: a line
-- may say a sponsor books, never that Nova "books the call" or fills a
-- calendar), and it answers from the website, not from SOPs.
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_nova_does_not_book;
ALTER TABLE claims_library ADD CONSTRAINT claims_nova_does_not_book
  CHECK (body IS NULL OR body !~* '(\mbooks (the |a |qualified )?(calls?|meetings?)\M|\minto your calendar\M)');
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_no_sops;
ALTER TABLE claims_library ADD CONSTRAINT claims_no_sops
  CHECK (body IS NULL OR body !~* '(\mSOPs?\M|standard operating procedure)');

COMMENT ON COLUMN claims_library.capabilities IS
  'What a description or benefit line asserts, from a closed vocabulary. Skill v3 section 3: the description and '
  'the benefits must not repeat the same capability. Workflow 4 and the follow-ups tag a message whose claim codes '
  'share one (claim-repeat). Migration 013.';

COMMIT;

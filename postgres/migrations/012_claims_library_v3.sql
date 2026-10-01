-- Migration 012 -- the claims library for drafting skill v3 (2026-10-02).
--
-- NovaScout_DraftingSkill.md v3 replaces verbatim line-picking with
-- composition: the drafting model (Claude Sonnet 5.5) writes the whole email
-- from one prospect fact plus the approved claims in this table, and may
-- rephrase a claim but never widen it (skill section 0, rule 2). So the table
-- changes shape:
--
--   v2 slots  problem | outcome | proof | ask | link      (pasted in verbatim)
--   v3 slots  description | angle | benefit | proof | ask | link
--                                                        (the model's source)
--
-- and the CHECKs change with it. v2's "a problem ends in '?'" and "an outcome
-- says 'we built'" described a sentence that was pasted whole; neither means
-- anything for a line the model rephrases. What replaces them is skill section
-- 3's rules for what a CLAIM may say, wherever the model puts it:
--
--   * no line names the product -- the model adds "(we call it Nova)" once
--   * "AI" only in a description line, at most once; never "AI-powered"
--   * never "chatbot" (skill section 3: "Never describe it as a chatbot")
--   * no guarantees
--   * no line calls the prospect a sponsor ("You're sponsoring" is banned)
--   * an ask line holds exactly one question
--
-- The geography CHECKs, the link rules and the measured-proof rule carry over
-- unchanged.
--
-- THE RESEED. Skill section 4 is the seed. Every line starts confirmed=false.
-- The lines section 4 marked [verify] were checked against the Nova Agent Kit
-- source (D:\projects\nova-agent-kit) on 2026-10-02 before seeding; three were
-- narrowed to what the code supports, and each such row's `note` carries the
-- original wording and the evidence. Lines not marked [verify] are seeded
-- verbatim; where the code reading raised a question about one of them, its
-- `note` says so for the human who confirms it.
--
-- Re-running this migration must never overwrite a human's v3 edits, so the
-- reseed runs only while the table still holds v2-shaped rows (a 'problem' or
-- 'outcome' slot) or is empty. A database on v3 already is left untouched.

BEGIN;

ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_library_slot_check;
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_slot_v3;
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_problem_is_question;
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_outcome_says_we_built;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM claims_library WHERE slot IN ('problem', 'outcome'))
     OR NOT EXISTS (SELECT 1 FROM claims_library) THEN
    DELETE FROM claims_library;

    INSERT INTO claims_library (code, slot, body, countries, measured, active, note) VALUES
      -- Description -- how to introduce it (pick one)
      ('D1', 'description',
       $b$an AI assistant for your website that turns sponsor inquiries into qualified leads$b$,
       NULL, false, true, NULL),
      ('D2', 'description',
       $b$an AI intake assistant for your website that answers sponsors, qualifies them, and books the call$b$,
       NULL, false, true,
       'Code reading 2026-10-02 (not a [verify] line): Nova does not book calls itself. After capture_sponsor_lead '
       'succeeds it offers the booking link from CALENDLY_BOOKING_URL (lib/tenants/loader.ts), only if that is set. '
       'Confirm or narrow "books the call".'),

      -- Pain and stakes angles (pick one)
      ('ANG-HOURS', 'angle',
       $b$Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.$b$,
       NULL, false, true, NULL),
      ('ANG-SILENT', 'angle',
       $b$How many sponsors visit your site and leave without ever contacting you?$b$,
       NULL, false, true, NULL),
      ('ANG-STAKES', 'angle',
       $b$A single sponsor inquiry can be a multi-million-dollar study.$b$,
       NULL, false, true, 'Industry fact, not a Nova result (skill section 4).'),
      ('ANG-SPEED', 'angle',
       $b$Sponsors choosing a CRO notice how quickly you respond, and a small CRO can't staff a BD desk around the clock.$b$,
       NULL, false, true, NULL),
      ('ANG-TIME', 'angle',
       $b$Your BD time should go to qualified sponsors, not to sorting every inquiry that arrives.$b$,
       NULL, false, true,
       '[verify] SUPPORTED 2026-10-02: Nova routes non-sponsor visitors to their own flows -- investigators and sites '
       'to capture_investigator_registration, trainees to capture_course_enrollment (lib/agent/tools/; system prompt '
       '"Distinguishing Sponsor Intent from Investigator Intent"). Seeded verbatim.'),

      -- Benefits (pick one or two)
      ('BEN-247', 'benefit',
       $b$It answers sponsors from your own SOPs and service pages, in real time, at any hour.$b$,
       NULL, false, true,
       'Code reading 2026-10-02 (not a [verify] line): the NoblePath knowledge base is the crawled website '
       '(tenants/noblepath/scripts/scraper.py); no SOP content is in it. The loader accepts any knowledge-base text, '
       'so SOPs are possible per tenant, but none is live. Confirm or narrow "SOPs".'),
      ('BEN-CAPTURE', 'benefit',
       $b$A sponsor who shares their details becomes a named lead: company, contact, therapeutic area and study phase.$b$,
       NULL, false, true,
       '[verify] NARROWED 2026-10-02. Was: "Every sponsor who engages becomes a named lead: company, contact, and '
       'what they''re planning." capture_sponsor_lead requires company_name, therapeutic_area, study_phase, '
       'contact_name, contact_email (notes optional) and is only called once all five are confirmed; a visitor who '
       'declines is not captured, so "every sponsor who engages" was wider than the code.'),
      ('BEN-BRIEF', 'benefit',
       $b$It collects the therapeutic area and study phase before your first call.$b$,
       NULL, false, true,
       '[verify] NARROWED 2026-10-02. Was: "It collects the study brief before your first call." The RFP intake '
       '(capture_sponsor_lead) records therapeutic area and study phase, plus free-text notes; there are no fields '
       'for protocol, timelines, site count or budget, and pricing questions are escalated, not collected.'),
      ('BEN-BOOK', 'benefit',
       $b$Qualified sponsors book a call straight into your calendar.$b$,
       NULL, false, true,
       'Code reading 2026-10-02 (not a [verify] line): booking is a link (CALENDLY_BOOKING_URL) offered in the '
       'confirmation after a lead is captured, only when the variable is set. Confirm before use.'),
      ('BEN-ROUTE', 'benefit',
       $b$Leads land where your team already works, not in another dashboard.$b$,
       NULL, false, true,
       'Code reading 2026-10-02 (not a [verify] line): deliverLead() emails the team and pushes the lead to a CRM '
       'and a spreadsheet when configured (lib/integrations/). Nova also has its own leads dashboard '
       '(app/dashboard), so "not in another dashboard" means "not only". Confirm the wording.'),
      ('BEN-SEE', 'benefit',
       $b$You can see every sponsor lead it captured, and any question it passed to your team.$b$,
       NULL, false, true,
       '[verify] NARROWED 2026-10-02. Was: "You can see which sponsors engaged and what they asked." The dashboard '
       '(app/dashboard) lists captured leads with their fields and notes, and escalations with the visitor''s '
       'unanswered question. Conversations are not stored and analytics are anonymous counts, so a sponsor who '
       'engaged without leaving details, and what they asked, are not visible.'),
      ('BEN-DECK', 'benefit',
       $b$It sends your capabilities deck the moment a sponsor asks for it.$b$,
       NULL, false, true,
       'Code reading 2026-10-02: capture_capabilities_request emails the deck once the visitor gives an email '
       'address. Supported.'),
      ('BEN-FIT', 'benefit',
       $b$It's configured around your services and your process, not a template.$b$,
       NULL, false, true, NULL),

      -- Proof (one, matched to the lead's country). The mapping is unchanged
      -- from migration 010: Turkiye and nearby -> NoblePath, Latin America ->
      -- Vertex, everywhere else -> PR-BOTH (the line with no countries).
      ('PR-TR', 'proof',
       $b$It's live at NoblePath, an oncology CRO in Türkiye.$b$,
       ARRAY['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'], false, true,
       'Turkiye and nearby. Which countries count as nearby is a judgement -- edit countries to change it.'),
      ('PR-MX', 'proof',
       $b$It's live at Vertex Clinical Research in Mexico.$b$,
       ARRAY['Mexico', 'Brazil', 'Argentina'], false, true,
       'Mexico and Latin America. The Nova Agent Kit repo holds only the NoblePath tenant; the Vertex deployment '
       'is not visible in that source.'),
      ('PR-BOTH', 'proof',
       $b$It's live at two CROs, in Türkiye and Mexico.$b$,
       NULL, false, true,
       'No countries: serves every lead no other proof line serves.'),
      ('PR-TR-N', 'proof', NULL,
       ARRAY['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'], true, false,
       'Empty on purpose. Fill ONLY with a number measured from Nova''s own dashboard at NoblePath, then activate. '
       'Never from a web source. When active it replaces PR-TR.'),
      ('PR-MX-N', 'proof', NULL,
       ARRAY['Mexico', 'Brazil', 'Argentina'], true, false,
       'Empty on purpose. Fill ONLY with a number measured from Nova''s own dashboard at Vertex, then activate. '
       'Never from a web source. When active it replaces PR-MX.'),

      -- Ask (one)
      ('A1', 'ask',
       $b$Would a 48-hour demo built on your own material be worth a look? One word back is enough.$b$,
       NULL, false, true, NULL),
      ('A2', 'ask',
       $b$Worth a 48-hour demo on your own material? Reply yes and I'll set it up.$b$,
       NULL, false, true, NULL),

      -- The one plain URL the existing link policy allows after warm-up week 2.
      ('L-NP', 'link', NULL, NULL, false, false,
       'The one plain URL allowed after warm-up week 2: NoblePath''s site, as a full URL (https://...). Workflow 4 '
       'appends it after the composed body only from warm-up week 3 on. countries works as it does for proof.');
  END IF;
END
$$;

ALTER TABLE claims_library ADD CONSTRAINT claims_slot_v3
  CHECK (slot IN ('description', 'angle', 'benefit', 'proof', 'ask', 'link'));

-- Skill section 3, Naming the product: "The name appears at most once, in
-- brackets" -- the model adds it. A claim that names it would be a second one.
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_no_product_name;
ALTER TABLE claims_library ADD CONSTRAINT claims_no_product_name
  CHECK (body IS NULL OR body !~* '\mnova\M');

-- "The word 'AI' appears at most once, inside the description. Never 'AI-powered'."
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_ai_only_in_description;
ALTER TABLE claims_library ADD CONSTRAINT claims_ai_only_in_description
  CHECK (body IS NULL
         OR (slot = 'description' AND regexp_count(body, '\mAI\M', 1, 'i') <= 1)
         OR (slot <> 'description' AND body !~* '\mAI\M'));
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_no_ai_powered_no_chatbot;
ALTER TABLE claims_library ADD CONSTRAINT claims_no_ai_powered_no_chatbot
  CHECK (body IS NULL OR body !~* '(\mAI[- ]?powered\M|\mchat ?bots?\M|\mQ ?& ?A bot\M)');

-- "Forbidden: guarantees."
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_no_guarantee;
ALTER TABLE claims_library ADD CONSTRAINT claims_no_guarantee
  CHECK (body IS NULL OR body !~* '\mguarantee');

-- "Never describe the prospect as sponsoring anything."
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_prospect_never_sponsor;
ALTER TABLE claims_library ADD CONSTRAINT claims_prospect_never_sponsor
  CHECK (body IS NULL OR body !~* '\myou(''re| are) sponsoring\M');

-- "Exactly one ask." An ask line is one question.
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_ask_one_question;
ALTER TABLE claims_library ADD CONSTRAINT claims_ask_one_question
  CHECK (slot <> 'ask' OR body IS NULL OR regexp_count(body, '\?') = 1);

COMMENT ON TABLE claims_library IS
  'Workflow 4 approved claims (NovaScout_DraftingSkill.md v3, section 4). Human-owned. The drafting model '
  'composes each email from one prospect fact plus these lines; it may rephrase a claim, never widen it. Read at '
  'runtime, never baked into workflow JSON (migrations 010, 012).';

COMMIT;

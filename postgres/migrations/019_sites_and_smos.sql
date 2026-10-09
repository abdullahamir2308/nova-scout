-- Migration 019 -- clinical research sites and SMOs as a second lead source
-- (2026-10-09).
--
-- Until now every lead came from the ICH GCP CRO directory. This migration adds
-- what Workflow 1b (Trialsites) and the company-type-aware drafting need, and,
-- for migration 014's reason, puts the rules on the tables rather than only in
-- the workflows:
--
--   1. site_candidates   -- Workflow 1b's memory: every Trialsites location it
--                           has chosen, the website lookup's outcome, and the
--                           trial-activity numbers Scoring reads for the lead.
--   2. site_lookup_log   -- one row per paid website lookup (Claude + web
--                           search), so the monthly cap counts money actually
--                           spent, not money expected.
--   3. site_harvest_log  -- one row per weekly harvest, so "has this week's
--                           harvest run?" is a question about the table, not
--                           about a clock (Section 7's idempotency rule).
--   4. settings.site_lookup_monthly_cap_usd and site_lookup_budget_left() --
--                           the cap, and the one definition of what is left.
--   5. claims_library.company_types -- which kind of prospect a line is for.
--                           Every existing row is a CRO line and stays one.
--   6. The site and SMO claims, seeded confirmed = false AND active = false.
--   7. lead_company_type() -- the one definition of whether a lead is a CRO, a
--                           site or an SMO, which Drafting, Follow-Ups, the
--                           Reply Assistant and Mailbox Watch all read.
--
-- WHAT IS DELIBERATELY NOT HERE: anything about investigators. The Trialsites
-- site-detail endpoint can carry investigator records; Workflow 1b never calls
-- it, and no column below could hold a person's name or address. The standing
-- rule: never use personal investigator emails from registry data.
--
-- WHY THE SITE CLAIMS ARE SEEDED INACTIVE as well as unconfirmed. The Drafting
-- and Follow-Ups versions running in n8n when this was written read EVERY
-- active claims_library row for EVERY lead (`WHERE k.active`, no company type).
-- An active site row would be offered to CRO leads -- and the site proof line,
-- having no countries, would sit beside PR-BOTH as an "elsewhere" proof for most
-- of them -- so CRO drafts and follow-ups would start being held as
-- unconfirmed-claim until the new versions are published. Inactive, the old
-- versions never see them. Once Drafting and Follow-Ups are published, the
-- operator ticks `active` and `confirmed` on each site row in NocoDB after
-- reading it (Section 13). Until then the new Drafting does not pick a site
-- lead at all (its batch query skips a lead whose company type has no active
-- description line), so a site lead waits at contact_found without filling
-- the batch in front of CRO leads.
--
-- Re-running this migration is a no-op: every object is IF NOT EXISTS / CREATE
-- OR REPLACE, constraints are dropped and re-added by name, and every INSERT is
-- ON CONFLICT DO NOTHING -- so a human's edit to a seeded row is never undone.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. site_candidates
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS site_candidates (
  location_id        bigint PRIMARY KEY,           -- Trialsites' own key
  canonical_name     text NOT NULL,
  city               text,
  state              text,
  country            text NOT NULL,                 -- leads.country spelling (Section 12)
  trialsites_country text,                          -- as Trialsites spells it
  site_tier          text NOT NULL CHECK (site_tier IN ('A', 'B')),
  trial_count        integer NOT NULL DEFAULT 0 CHECK (trial_count >= 0),
  recent_trials_3yr  integer NOT NULL DEFAULT 0 CHECK (recent_trials_3yr >= 0),
  active_recruiting  integer NOT NULL DEFAULT 0 CHECK (active_recruiting >= 0),
  quality_score      numeric,
  facility_type      text,
  network_type       text,
  primary_ta         text,
  therapeutic_areas  text,
  priority           bigint NOT NULL DEFAULT 0,     -- lower is looked up sooner
  harvested_at       timestamptz NOT NULL DEFAULT now(),
  -- The website lookup (Claude with web search, then a fetch that must find the
  -- name and the city). Only 'pending', and 'call-failed' under the attempt
  -- limit, are looked up again; every other outcome is final, because a second
  -- paid lookup of the same name has no reason to answer differently.
  lookup_status      text NOT NULL DEFAULT 'pending' CHECK (lookup_status IN (
                       'pending',       -- not looked up yet
                       'resolved',      -- website confirmed; a lead was written
                       'duplicate',     -- website confirmed, but that domain is already a lead
                       'no-match',      -- no confident website found
                       'not-confirmed', -- a website was proposed and the fetch did not find name + city
                       'excluded',      -- an institutional or sanctioned-jurisdiction domain
                       'call-failed')), -- the API call failed; retried up to the limit
  lookup_attempts    integer NOT NULL DEFAULT 0 CHECK (lookup_attempts >= 0),
  lookup_detail      text,
  looked_up_at       timestamptz,
  proposed_url       text,
  website_url        text,
  domain             text,
  lead_id            bigint REFERENCES leads(id) ON DELETE SET NULL,
  -- A resolved candidate has the domain it resolved to, and only a resolved or
  -- duplicate one does: "never guess a domain" as a rule on the table.
  CONSTRAINT site_candidates_domain_only_when_confirmed CHECK (
    (lookup_status IN ('resolved', 'duplicate')) = (domain IS NOT NULL)),
  CONSTRAINT site_candidates_resolved_has_lead CHECK (
    lookup_status <> 'resolved' OR lead_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS site_candidates_queue
  ON site_candidates (priority, location_id) WHERE lookup_status IN ('pending', 'call-failed');
CREATE INDEX IF NOT EXISTS site_candidates_lead ON site_candidates (lead_id) WHERE lead_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. site_lookup_log -- what each paid lookup cost
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS site_lookup_log (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  location_id         bigint NOT NULL REFERENCES site_candidates(location_id) ON DELETE CASCADE,
  called_at           timestamptz NOT NULL DEFAULT now(),
  model               text NOT NULL,
  input_tokens        integer NOT NULL DEFAULT 0,
  output_tokens       integer NOT NULL DEFAULT 0,
  cache_read_tokens   integer NOT NULL DEFAULT 0,
  cache_write_tokens  integer NOT NULL DEFAULT 0,
  web_search_requests integer NOT NULL DEFAULT 0,
  cost_usd            numeric(10, 6) NOT NULL CHECK (cost_usd >= 0),
  outcome             text NOT NULL
);

CREATE INDEX IF NOT EXISTS site_lookup_log_called_at ON site_lookup_log (called_at);

-- ---------------------------------------------------------------------------
-- 3. site_harvest_log -- one row per weekly harvest
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS site_harvest_log (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  harvested_at  timestamptz NOT NULL DEFAULT now(),
  responses     integer NOT NULL,
  rows_seen     integer NOT NULL,
  eligible_new  integer NOT NULL,
  chosen        integer NOT NULL,
  stats         jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- 4. The monthly cap on website lookups
-- ---------------------------------------------------------------------------

ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_key_check;
ALTER TABLE settings ADD CONSTRAINT settings_key_check
  CHECK (key IN ('operator_email', 'auto_approve_email', 'site_lookup_monthly_cap_usd'));

ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_site_lookup_cap_shape;
ALTER TABLE settings ADD CONSTRAINT settings_site_lookup_cap_shape
  CHECK (key <> 'site_lookup_monthly_cap_usd' OR value ~ '^[0-9]{1,4}(\.[0-9]{1,2})?$');

-- $20 a month. Measured on 2026-10-09 (Section 4): a lookup costs $0.018 on
-- average and at most ~$0.035 (three searches), so a full 200-candidate week is
-- ~$3.60 and a month of them ~$15.50. The cap is headroom over that, not a
-- target: what it exists to stop is a bug that looks the same names up again.
INSERT INTO settings (key, value) VALUES ('site_lookup_monthly_cap_usd', '20')
ON CONFLICT (key) DO NOTHING;

-- The one definition of what may still be spent this calendar month (UTC, the
-- month Anthropic bills in). A missing setting reads as $0: no cap configured
-- means no lookups, never unlimited ones.
CREATE OR REPLACE FUNCTION site_lookup_budget_left() RETURNS numeric
LANGUAGE sql STABLE AS $$
  SELECT greatest(0,
           coalesce((SELECT value::numeric FROM settings WHERE key = 'site_lookup_monthly_cap_usd'), 0)
         - coalesce((SELECT sum(cost_usd) FROM site_lookup_log
                      WHERE called_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'), 0));
$$;

-- ---------------------------------------------------------------------------
-- 5. claims_library.company_types
-- ---------------------------------------------------------------------------

ALTER TABLE claims_library ADD COLUMN IF NOT EXISTS company_types text[] NOT NULL DEFAULT '{CRO}';

ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_company_types_vocabulary;
ALTER TABLE claims_library ADD CONSTRAINT claims_company_types_vocabulary
  CHECK (cardinality(company_types) >= 1 AND company_types <@ ARRAY['CRO', 'site', 'SMO']);

-- Section 12 / Workflow 7: Vertex Clinical Research is a clinical research
-- centre, never a CRO, and a site never gets the "two CROs" proof. Pinned on the
-- table so no edit can make a site or SMO line say either.
ALTER TABLE claims_library DROP CONSTRAINT IF EXISTS claims_site_lines_never_cro_proof;
ALTER TABLE claims_library ADD CONSTRAINT claims_site_lines_never_cro_proof
  CHECK (NOT (company_types && ARRAY['site', 'SMO']) OR body IS NULL OR (
           body !~* '\mtwo CROs\M'
       AND body !~* 'Vertex[^.]*\mCRO\M'
       AND body !~* '\mCRO websites?\M'));

-- A confirmation is of the text that was read AND of who it was read for: a CRO
-- line widened to sites is a new claim, so changing company_types un-confirms it
-- exactly as changing the body does (migration 014).
CREATE OR REPLACE FUNCTION claims_confirmation_follows_text() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.confirmed AND NEW.confirmed AND (
       NEW.body          IS DISTINCT FROM OLD.body
    OR NEW.slot          IS DISTINCT FROM OLD.slot
    OR NEW.countries     IS DISTINCT FROM OLD.countries
    OR NEW.capabilities  IS DISTINCT FROM OLD.capabilities
    OR NEW.company_types IS DISTINCT FROM OLD.company_types) THEN
    NEW.confirmed := false;
  END IF;
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. The site and SMO claims -- confirmed = false, active = false
-- ---------------------------------------------------------------------------
--
-- Each line was checked against the Nova Agent Kit source (D:\projects\nova-agent-kit)
-- on 2026-10-09; the note on each row says what supports it. The Vertex tenant is
-- not in that repository, so nothing here describes Vertex beyond the operator's
-- own sentence for PR-SITE. Sites hear from two kinds of visitor -- sponsors, and
-- CROs choosing sites for a study -- so the lines name both.

INSERT INTO claims_library (code, slot, body, countries, capabilities, company_types, measured, active, confirmed, note) VALUES
('D-SITE1', 'description',
 'an AI assistant for your website that answers sponsors and CROs looking for sites, qualifies them, and sends them your booking link',
 NULL, ARRAY['answers', 'qualifies', 'booking-link'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of D2, 2026-10-09. Answers: the tenant system prompt is built from knowledge-base.md, which tenants/<id>/scripts/scraper.py fills from the website. Qualifies + booking link: capture_sponsor_lead requires company, therapeutic area, phase, contact name and email, then returns config.booking.discoveryCallUrl. "Sponsors and CROs": the tool''s company_name field is "the sponsor''s company or organisation name" -- any organisation is captured, but its description says sponsor, so who counts as qualified is the tenant prompt''s job (operator to confirm).'),
('D-SITE2', 'description',
 'an AI assistant for your website that turns study inquiries from sponsors and CROs into qualified leads',
 NULL, ARRAY['qualifies', 'captures-lead'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of D1, 2026-10-09. capture_sponsor_lead (lib/agent/tools/capture-sponsor-lead.ts) saves a lead with company, contact, therapeutic area and phase via saveLead + deliverLead.'),
('ANG-SITE-HOURS', 'angle',
 'Sponsors and CROs often look for sites outside your working hours, frequently from another time zone. An inquiry sent at night waits until morning, and by then they may have moved on to the next site on their list.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Site version of ANG-HOURS, 2026-10-09: "11pm" dropped (a digit no site fact would ground), "CRO" replaced by "site". Industry framing, not a product claim.'),
('ANG-SITE-SPEED', 'angle',
 'CROs choosing sites notice how quickly you respond, and a research site can''t staff an inquiry desk around the clock.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Site version of ANG-SPEED, 2026-10-09. Industry framing, not a product claim.'),
('ANG-SITE-SILENT', 'angle',
 'How many sponsors and CROs visit your website and leave without ever contacting you?',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Site version of ANG-SILENT, 2026-10-09. "website", never "site": for a research site "your site" reads as the clinic.'),
('ANG-SITE-TIME', 'angle',
 'Your team''s time should go to running studies, not to sorting every inquiry that arrives.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Site version of ANG-TIME, 2026-10-09. Industry framing, not a product claim.'),
('BEN-SITE-247', 'benefit',
 'It answers sponsors and CROs from your own website, in real time, at any hour.',
 NULL, ARRAY['answers'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-247, 2026-10-09. Same evidence as BEN-247 (migration 013): the knowledge base is the website, scraped by tenants/<id>/scripts/scraper.py (PDFs skipped) and loaded into the system prompt; no code path for client documents.'),
('BEN-SITE-CAPTURE', 'benefit',
 'A sponsor or CRO who shares their details becomes a named lead: company, contact, therapeutic area and study phase.',
 NULL, ARRAY['captures-lead', 'study-details'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-CAPTURE, 2026-10-09. capture_sponsor_lead requires company_name, therapeutic_area, study_phase, contact_name, contact_email. "or CRO": company_name is "the sponsor''s company or organisation name" -- operator to confirm the tenant prompt qualifies a CRO the same way.'),
('BEN-SITE-BRIEF', 'benefit',
 'It collects the therapeutic area and study phase before your first call.',
 NULL, ARRAY['study-details'], ARRAY['site', 'SMO'], false, false, false,
 'Same text as BEN-BRIEF, 2026-10-09: therapeutic_area and study_phase are required fields of capture_sponsor_lead.'),
('BEN-SITE-BOOK', 'benefit',
 'It sends qualified sponsors and CROs your booking link, so they can book a call with your team.',
 NULL, ARRAY['booking-link'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-BOOK, 2026-10-09. capture_sponsor_lead returns config.booking.discoveryCallUrl to the visitor; Nova sends the link, the visitor books (migration 013).'),
('BEN-SITE-DECK', 'benefit',
 'It sends your capabilities deck the moment a sponsor or CRO asks for it.',
 NULL, ARRAY['deck'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-DECK, 2026-10-09. capture_capabilities_request emails assets.capabilitiesDeckUrl to any visitor who asks and gives an email address (sendCapabilitiesDeck, lib/integrations/resend.ts).'),
('BEN-SITE-SEE', 'benefit',
 'You can see every lead it captured, and any question it passed to your team.',
 NULL, ARRAY['dashboard'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-SEE, 2026-10-09 ("sponsor" dropped: a site''s leads are sponsors and CROs). app/dashboard lists captured leads and escalations.'),
('BEN-SITE-FIT', 'benefit',
 'It''s configured around your site and your process, not a template.',
 NULL, ARRAY['configured'], ARRAY['site', 'SMO'], false, false, false,
 'Site version of BEN-FIT, 2026-10-09. Per-tenant tenants/<id>/config.json, system-prompt.md and knowledge-base.md.'),
('BEN-SITE-ROUTE', 'benefit',
 'Leads land where your team already works.',
 NULL, ARRAY['routes-leads'], ARRAY['site', 'SMO'], false, false, false,
 'Same text as BEN-ROUTE, 2026-10-09: deliverLead (lib/integrations/index.ts) sends each lead to the tenant''s configured destinations (email via Resend, HubSpot, Google Sheets) -- never named in a draft.'),
('BEN-SMO-NETWORK', 'benefit',
 'Investigators who want to join your site network are captured with their site, location, specialty and contact details.',
 NULL, ARRAY['captures-lead'], ARRAY['SMO'], false, false, false,
 'SMO only, 2026-10-09. capture_investigator_registration (flow investigator_network) requires site_or_institution_name, location, therapeutic_specialty, contact_name, contact_email. Never offered to a single independent site, which has no network to join. The tool''s description still says "NoblePath''s clinical trial network" in code, so an SMO tenant needs it generalised before this is true for them (operator to confirm).'),
('PR-SITE', 'proof',
 'It''s live at Vertex Clinical Research, a research center in Mexico.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'The operator''s sentence, 2026-10-09, word for word. Vertex is a clinical research center, never a CRO, and a site never gets PR-BOTH''s "two CROs" (claims_site_lines_never_cro_proof). No countries: it is the proof for every site lead. The Vertex tenant is not in the Nova Agent Kit repo.'),
('A-SITE1', 'ask',
 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Same text as A1, 2026-10-09.'),
('A-SITE2', 'ask',
 'Worth a 48-hour demo on your own material? Reply yes and I''ll set it up.',
 NULL, NULL, ARRAY['site', 'SMO'], false, false, false,
 'Same text as A2, 2026-10-09.')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 7. lead_company_type() -- CRO, site or SMO, defined once
-- ---------------------------------------------------------------------------
--
-- Enrichment's company_type when it is one of the three; otherwise the source
-- decides -- the ICH GCP CRO directory means CRO, Trialsites means site. 'other'
-- never reaches a draft (Scoring disqualifies it) and 'unclear' or a lead
-- enriched before 2026-10-09 falls back to its source. Four queries read this
-- (which claims are offered, which wording is refused, which one-pager a reply
-- notification links), so it lives here rather than as four copies of a CASE.
CREATE OR REPLACE FUNCTION lead_company_type(raw_extraction jsonb, source text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN raw_extraction->>'company_type' IN ('CRO', 'site', 'SMO') THEN raw_extraction->>'company_type'
              WHEN source = 'trialsites' THEN 'site'
              ELSE 'CRO' END;
$$;

COMMIT;

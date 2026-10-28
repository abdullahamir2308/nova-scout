-- Migration 025 -- a niche is a row (2026-10-28, operator brief).
--
-- WHY. Until today "which leads get a template, and may it approve itself" was
-- hard-wired to one answer: a CRO (migration 020's trigger, Drafting's batch
-- query, Follow-Ups' due query). The operator's brief of 2026-10-28 adds a
-- second niche -- independent clinical research sites and SMOs -- with its own
-- wording, scoring and approval rule, and asks that a niche be a config row.
--
-- WHAT.
--   * niches -- one row per niche: which lead_company_type() values belong to
--     it (entity_types), which leads.source values it may draft (sources; NULL
--     = any) and never (excluded_sources), its fit-score floor, its scoring
--     rules (documentation the build checks), its approved wording (templates,
--     the exact strings the code sends -- n8n/drafting/niche_templates.js prints
--     them from the shipped sources), the identity its emails sign with, and
--     its approval rule (active, auto_approve).
--       cro            -- today's behaviour, unchanged: every CRO-typed lead
--                         from any source but the two site sources below.
--       research_site  -- sites and SMOs from the open site sources and from
--                         ICH GCP. NOT Trialsites: its API page says "Free for
--                         non-commercial use" (Master Ref Section 13), so its
--                         leads are not drafted until the operator adds
--                         'trialsites' to sources.
--   * lead_niche(raw_extraction, source) -- the one definition of which niche a
--     lead is in (NULL: none, so nothing drafts it).
--   * lead_company_type() -- a lead from a site source falls back to 'site',
--     never to 'CRO' (the ELSE branch was written when every non-Trialsites
--     lead came from the CRO directory), and a ClinicalTrials.gov recruiting
--     location is a site even when enrichment read its homepage as a CRO's.
--     ICH GCP and Trialsites unchanged.
--   * site_lead_sources -- where a site lead came from and what the source
--     proved: the ClinicalTrials.gov facility name (the key the draft-time
--     strict facility match uses), the recruiting count at harvest, the
--     Overture place and its licences, and what the verification fetch saw
--     (contact form, mailto, robots.txt).
--   * drafts_approval_rules() -- migration 020's rule "its lead is a CRO"
--     becomes "its lead's niche is active and auto-approves, and the text signs
--     with that niche's identity". For a CRO lead this is the same test as
--     before (the cro row is active, auto-approving, and signs "Founder,
--     Amitrix Labs" -- the template's own sign-off). Every other hold is
--     migration 020's, word for word: the switch, email only, a template
--     variant with no tag, unedited text, a verified deliverable address, no
--     blocklisted domain.
--
-- Additive and idempotent: the seed never overwrites an operator's edit.

BEGIN;

CREATE TABLE IF NOT EXISTS niches (
  code             text PRIMARY KEY CHECK (code ~ '^[a-z][a-z_]*$'),
  label            text NOT NULL,
  active           boolean NOT NULL DEFAULT false,
  entity_types     text[] NOT NULL CHECK (cardinality(entity_types) >= 1
                                          AND entity_types <@ ARRAY['CRO', 'site', 'SMO']),
  sources          text[],
  excluded_sources text[] NOT NULL DEFAULT '{}',
  min_fit_score    integer NOT NULL CHECK (min_fit_score BETWEEN 1 AND 100),
  scoring          jsonb NOT NULL DEFAULT '{}',
  templates        jsonb NOT NULL,
  sender_name      text NOT NULL,
  sender_title     text NOT NULL,
  auto_approve     boolean NOT NULL DEFAULT false,
  approval         jsonb NOT NULL DEFAULT '{}',
  updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE niches IS
  'One row per niche (migration 025). lead_niche() reads it; drafts_approval_rules() lets a template draft approve '
  'itself only for an active, auto-approving niche whose identity the text signs with. templates holds the exact '
  'approved wording the code sends (n8n/drafting/niche_templates.js prints it from the shipped sources).';

INSERT INTO niches (code, label, active, entity_types, sources, excluded_sources, min_fit_score, scoring,
                    templates, sender_name, sender_title, auto_approve, approval)
VALUES
  ('cro', 'Small and mid-size CROs', true, ARRAY['CRO'], NULL, ARRAY['sites-ctgov', 'sites-overture'], 40,
   '{"contact_gate": ">= 50, and >= 40 for a CRO (Master Ref Section 9, Workflow 3)", "weights": {"geography": 10, "trials": 20, "founder": 20, "oncology": 15, "employees": 25, "site": 10}}'::jsonb,
   $tpl${
  "subject": {
    "with_fact": "{company} + {fact} + sponsor inquiries",
    "no_fact": "{company} + sponsor inquiries",
    "facts": {
      "trials": "{n} recruiting trial(s)",
      "areas": "{areas}",
      "city": "{city}"
    }
  },
  "greeting": {
    "named": "Hi {first_name},",
    "unnamed": "Hi there,"
  },
  "intro": "We have yet to be properly introduced, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs.",
  "smykm": {
    "CRO": {
      "trials_A": "With {n} trial(s) recruiting right now, sponsors are clearly paying attention to {company}, and every one who visits your website is a potential next study worth turning into a conversation.",
      "trials_B": "{company} has {n} trial(s) recruiting right now. Each sponsor who checks your website after seeing that is a lead worth capturing.",
      "areas_A": "{company} covers {areas} from {phases}, so sponsors visiting your website arrive with very different questions, and each one is a lead worth capturing.",
      "areas_B": "A sponsor planning a {area1} study will usually check {company}'s website before they ever email you. That visit is a lead worth capturing.",
      "city": "Sponsors looking for a CRO in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.",
      "company": "Sponsors evaluating {company} will usually start on your website, and every one of those visits is a potential study worth turning into a conversation."
    }
  },
  "smykm_order": [
    "trials",
    "areas",
    "city",
    "company"
  ],
  "pitch": {
    "CRO": "That's precisely what we do at Amitrix Labs. We build AI agents for CRO websites that answer sponsor and visitor questions, collect their details, and turn more of your visitors into qualified leads, so you win more studies."
  },
  "close": {
    "A": "Do you have time over the next week or two to learn more? Let me know what works for you, and I'll send a calendar invite along accordingly.",
    "B": "Worth a look for {company}?"
  },
  "arms": {
    "even": "A",
    "odd": "B"
  },
  "sign_off": [
    "Cheers!",
    "Abdullah Amir",
    "Founder, Amitrix Labs"
  ],
  "follow_up": {
    "1": "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits {company}'s website is a potential study, and we help make sure those visits turn into conversations.",
    "2": "I'll keep this short and won't follow up again after this. If turning more of {company}'s website visitors into qualified leads is ever on your list, just reply and I'll send over the details."
  }
}$tpl$::jsonb,
   'Abdullah Amir', 'Founder, Amitrix Labs', true,
   '{"holds": ["no-address", "unverified-address", "no-send-clock", "blocklisted", "auto-approve-off"], "approved_by_operator": "2026-10-19"}'::jsonb),
  ('research_site', 'Independent clinical research sites and SMOs', true, ARRAY['site', 'SMO'],
   ARRAY['sites-ctgov', 'sites-overture', 'ichgcp'], ARRAY['trialsites'], 50,
   '{"contact_gate": ">= 50 (Master Ref Section 9, Workflow 3)", "weights": {"recruiting_trials": 25, "site_type": 15, "therapeutic_areas": 10, "inquiry_form": 10, "geography": 10, "employees": 10, "site_quality": 10, "director": 10}, "excluded": ["CRO", "sponsor", "hospital", "academic", "government", "unclear", "chat widget", "employees > 500", "no website", "blocklisted"]}'::jsonb,
   $tpl${
  "subject": {
    "with_fact": "{company} + {fact} + sponsor inquiries",
    "no_fact": "{company} + sponsor inquiries",
    "facts": {
      "trials": "{n} recruiting trial(s)",
      "areas": "{areas}",
      "city": "{city}"
    }
  },
  "greeting": {
    "named": "Hi {first_name},",
    "unnamed": "Hi there,"
  },
  "intro": "We have yet to be properly introduced, but I'm Abdullah Amir, a software engineer who builds AI assistants for clinical research websites.",
  "smykm": {
    "site": {
      "trials_A": "With {n} trial(s) recruiting at {company} right now, sponsors are clearly paying attention, and every one who visits your website is a potential next study worth turning into a conversation.",
      "trials_B": "{company} has {n} trial(s) recruiting right now. Each sponsor or CRO who checks your website after seeing that is a lead worth capturing.",
      "areas_A": "{company} works in {areas}, so sponsors and CROs visiting your website arrive with very different questions, and each one is a lead worth capturing.",
      "areas_B": "{company} works in {areas}, so sponsors searching for a site in that area will usually check your website before they ever email you. That visit is a lead worth capturing.",
      "city": "Sponsors and CROs looking for research sites in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.",
      "company": "Sponsors evaluating {company} will usually start on your website, and every one of those visits is a potential study worth turning into a conversation."
    },
    "SMO": {
      "trials_A": "With {n} trial(s) recruiting across {company}'s network right now, sponsors are clearly paying attention, and every one who visits your website is a potential next study worth turning into a conversation.",
      "trials_B": "{company}'s network has {n} trial(s) recruiting right now. Each sponsor or CRO who checks your website after seeing that is a lead worth capturing.",
      "areas_A": "{company}'s sites work in {areas}, so sponsors and CROs visiting your website arrive with very different questions, and each one is a lead worth capturing.",
      "areas_B": "{company}'s sites work in {areas}, so sponsors searching for sites in that area will usually check your website before they ever email you. That visit is a lead worth capturing.",
      "city": "Sponsors and CROs looking for research sites in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.",
      "company": "Sponsors evaluating {company}'s sites will usually start on your website, and every one of those visits is a potential study worth turning into a conversation."
    }
  },
  "smykm_order": [
    "trials",
    "areas",
    "city",
    "company"
  ],
  "pitch": {
    "site": "That's precisely what I build. My assistants answer sponsor and CRO questions on your website, collect their details, and turn more of your visitors into conversations about studies.",
    "SMO": "That's precisely what I build. My assistants answer sponsor and CRO questions on your website, collect their details, and turn more of your visitors into conversations about studies across your sites."
  },
  "close": {
    "A": "Do you have time over the next week or two to learn more? Let me know what works for you, and I'll send a calendar invite along accordingly.",
    "B": "Worth a look for {company}?"
  },
  "arms": {
    "even": "A",
    "odd": "B"
  },
  "sign_off": [
    "Cheers!",
    "Abdullah Amir",
    "Software Engineer, Amitrix Labs"
  ],
  "follow_up": {
    "1": "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits {company}'s website is a potential study, and I help make sure those visits turn into conversations.",
    "2": "I'll keep this short and won't follow up again after this. If turning more of {company}'s website visitors into sponsor conversations is ever on your list, just reply and I'll send over the details."
  }
}$tpl$::jsonb,
   'Abdullah Amir', 'Software Engineer, Amitrix Labs', true,
   '{"holds": ["no-address", "unverified-address", "no-send-clock", "blocklisted", "auto-approve-off"], "approved_by_operator": "2026-10-28"}'::jsonb)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS site_lead_sources (
  lead_id            bigint PRIMARY KEY REFERENCES leads (id) ON DELETE CASCADE,
  source             text NOT NULL CHECK (source IN ('sites-ctgov', 'sites-overture')),
  facility_name      text,
  city               text,
  state              text,
  country            text NOT NULL,
  ctgov_recruiting   integer NOT NULL DEFAULT 0 CHECK (ctgov_recruiting >= 0),
  ctgov_ncts         text[] NOT NULL DEFAULT '{}',
  overture_id        text,
  overture_licences  text[] NOT NULL DEFAULT '{}',
  name_kind          text,
  has_contact_form   boolean,
  has_mailto         boolean,
  robots_allowed     boolean NOT NULL,
  verify_detail      text,
  harvested_at       timestamptz NOT NULL DEFAULT now(),
  -- A lead is written only for a site whose own pages name it and its city,
  -- and whose robots.txt lets a crawler read the homepage.
  CONSTRAINT site_lead_sources_robots CHECK (robots_allowed)
);

COMMENT ON TABLE site_lead_sources IS
  'Where a research-site lead came from (migration 025): the ClinicalTrials.gov facility name and recruiting count '
  'at harvest, the Overture place and its licences, and what the verification fetch saw. Never a registry contact.';

CREATE OR REPLACE FUNCTION lead_company_type(raw_extraction jsonb, source text) RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$
  -- A ClinicalTrials.gov recruiting location is a site whatever its homepage
  -- reads like to the model (Scoring's classifySite() makes the same call).
  SELECT CASE WHEN source = 'sites-ctgov' AND raw_extraction->>'company_type' = 'CRO' THEN 'site'
              WHEN raw_extraction->>'company_type' IN ('CRO', 'site', 'SMO') THEN raw_extraction->>'company_type'
              WHEN source IN ('trialsites', 'sites-ctgov', 'sites-overture') THEN 'site'
              ELSE 'CRO' END;
$fn$;

CREATE OR REPLACE FUNCTION lead_niche(raw_extraction jsonb, source text) RETURNS text
LANGUAGE sql STABLE AS $fn$
  SELECT n.code
    FROM niches n
   WHERE lead_company_type(raw_extraction, source) = ANY (n.entity_types)
     AND (n.sources IS NULL OR source = ANY (n.sources))
     AND NOT (coalesce(source, '') = ANY (n.excluded_sources))
   ORDER BY n.code
   LIMIT 1;
$fn$;

COMMENT ON FUNCTION lead_niche(jsonb, text) IS
  'The niche a lead belongs to (migration 025), or NULL. Drafting, Follow-Ups, Send, the Digest and '
  'drafts_approval_rules() read it.';

CREATE OR REPLACE FUNCTION drafts_approval_rules()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
  was      text := CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END;
  problems text[] := '{}';
  v        text := coalesce(NEW.variant, '');
  addr     text;
  ok_addr  boolean;
  nch      niches%ROWTYPE;
BEGIN
  -- Leaving approval: approved_by describes the current approval, nothing else.
  IF NEW.status NOT IN ('approved', 'sent') THEN
    NEW.approved_by := NULL;
    NEW.approved_at := NULL;
    IF was = 'sent' AND NEW.status = 'pending' AND v LIKE '%smtp-rejected%' THEN
      NEW.hold_reason := 'smtp-rejected: the mail server refused the address';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.status = 'approved' AND (TG_OP = 'INSERT' OR coalesce(was, '') NOT IN ('approved', 'sent')) THEN
    IF v LIKE 'reply/%'
       AND NOT EXISTS (SELECT 1 FROM reply_approvals a
                        WHERE a.draft_id = NEW.id
                          AND ((a.used_at IS NOT NULL AND a.outcome IN ('approved', 'edited'))
                               OR (a.used_at IS NULL AND a.expires_at > now()))) THEN
      NEW.status      := 'pending';
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
      NEW.hold_reason := 'db-guard: a reply is approved only by its one-time code, from the operator '
                      || 'address with SPF and DKIM passing (Workflow 7)';
      RETURN NEW;
    END IF;

    IF NEW.approved_by = 'auto' THEN
      IF NOT auto_approve_email_enabled() THEN
        problems := array_append(problems, 'auto-approve is off');
      END IF;
      IF NEW.channel IS DISTINCT FROM 'email' THEN
        problems := array_append(problems, 'not an email');
      END IF;
      IF v LIKE 'reply/%' THEN
        problems := array_append(problems, 'reply');
      ELSIF v !~ '^(template/|follow-up-[0-9]+/template/)' THEN
        problems := array_append(problems, 'not a template draft');
      END IF;
      IF position('+' IN v) > 0 THEN
        problems := array_append(problems, ('rule tags ' || split_part(v, '+', 2)));
      END IF;
      IF NEW.edited_body IS NOT NULL THEN
        problems := array_append(problems, 'edited text was never checked');
      END IF;
      -- Migration 020 asked "is the lead a CRO"; a niche row answers it now.
      SELECT n.* INTO nch
        FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id
        JOIN niches n ON n.code = lead_niche(e.raw_extraction, l.source)
       WHERE l.id = NEW.lead_id;
      IF nch.code IS NULL THEN
        problems := array_append(problems, 'no niche drafts this lead');
      ELSIF NOT nch.active THEN
        problems := array_append(problems, 'niche ' || nch.code || ' is not active');
      ELSIF NOT nch.auto_approve THEN
        problems := array_append(problems, 'niche ' || nch.code || ' does not auto-approve');
      ELSIF position(E'\n' || nch.sender_name || E'\n' || nch.sender_title || E'\n'
                     IN E'\n' || replace(coalesce(NEW.body, ''), E'\r', '') || E'\n') = 0 THEN
        problems := array_append(problems, 'not signed as niche ' || nch.code || ' signs');
      END IF;
      SELECT c.email,
             coalesce(c.verified, false)
             AND c.email ~ '^[^[:space:]@<>(),;:"'']+@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
        INTO addr, ok_addr
        FROM contacts c WHERE c.lead_id = NEW.lead_id;
      IF NOT coalesce(ok_addr, false) THEN
        problems := array_append(problems, 'no verified deliverable address');
      END IF;
      IF EXISTS (SELECT 1 FROM leads l, blocklist b
                  WHERE l.id = NEW.lead_id
                    AND (lower(l.domain) = b.domain OR lower(l.domain) LIKE '%.' || b.domain))
         OR EXISTS (SELECT 1 FROM blocklist b
                     WHERE lower(split_part(coalesce(addr, ''), '@', 2)) = b.domain
                        OR lower(split_part(coalesce(addr, ''), '@', 2)) LIKE '%.' || b.domain) THEN
        problems := array_append(problems, 'blocklisted');
      END IF;

      IF cardinality(problems) > 0 THEN
        NEW.status      := 'pending';
        NEW.approved_by := NULL;
        NEW.approved_at := NULL;
        NEW.hold_reason := 'db-guard: ' || array_to_string(problems, ', ');
        RETURN NEW;
      END IF;
      NEW.hold_reason := NULL;
    ELSE
      NEW.approved_by := 'human';
    END IF;
    NEW.approved_at := now();
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' AND was = 'approved' AND NEW.status = 'approved' AND OLD.approved_by = 'auto' THEN
    IF NEW.body IS DISTINCT FROM OLD.body OR NEW.subject IS DISTINCT FROM OLD.subject THEN
      NEW.status      := 'pending';
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
      NEW.hold_reason := 'db-guard: text changed after auto-approval';
    ELSIF NEW.edited_body IS DISTINCT FROM OLD.edited_body THEN
      NEW.approved_by := 'human';
      NEW.approved_at := now();
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

COMMENT ON COLUMN drafts.approved_by IS
  'auto = approved by the workflow that wrote it: a template draft (first touch or follow-up) of an active, '
  'auto-approving niche, signed with that niche''s identity, with a verified deliverable address, a Send clock and '
  'no blocklisted domain (migration 025; 020 before it); human = anyone else. Only on approved/sent drafts.';

COMMIT;

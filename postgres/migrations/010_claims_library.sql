-- Migration 010 -- the approved claims library for Workflow 4 (2026-09-19).
--
-- NovaScout_DraftingSkill.md v2, section 4: "The only non-prospect sentences the
-- model may use. A human edits these; the model never does. Every line is a
-- claim Amitrix Labs stands behind."
--
-- Drafting v2 splits a first touch into parts. Only the hook and the subject are
-- generated, from enrichment facts, under the Section 9 grounding guard. The
-- problem, outcome, proof and ask are lines from this table, which Workflow 4
-- selects and inserts VERBATIM. The model never sees them and has no output
-- field to write them into. That is the skill's structural firewall against
-- "joining": the model cannot fuse a prospect fact with a problem into a false
-- causal claim when the problem is a fixed question it did not write.
--
-- Why a table and not a constant in the workflow: same reason as `settings`
-- (migration 008). The claims belong to the operator, not the code. A claim
-- changes when a customer or a metric changes, and that must not need a
-- rebuild, a re-import and a re-publish. Workflow 4 reads the active rows on
-- every run, so an edit reaches the next draft.
--
-- Written by humans only (NocoDB or psql). The seed below is the skill's section 4
-- as of 2026-09-19. From here on THIS TABLE is the library, and section 4 is
-- only the record of what it started as. ON CONFLICT DO NOTHING: re-running this
-- migration never overwrites a human's edit.
--
-- The skill's rules for each slot are CHECKs here rather than suggestions in a
-- grid, so they hold for every writer, and a broken line is refused when it is
-- saved rather than discovered in a draft. Same principle as migration 006.

BEGIN;

CREATE TABLE IF NOT EXISTS claims_library (
  code       TEXT PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
  -- problem, outcome, proof and ask are the skill's parts 2-5. link is the
  -- one plain URL the skill allows after warm-up (section 3, "Links").
  slot       TEXT NOT NULL CHECK (slot IN ('problem', 'outcome', 'proof', 'ask', 'link')),
  body       TEXT,
  -- proof and link only: the lead countries this line serves (leads.country,
  -- spelled as Section 12 spells it). NULL means "everywhere no other line
  -- serves" -- the skill's "either elsewhere".
  countries  TEXT[],
  -- proof only: a number measured from Nova's own analytics at that client
  -- (skill section 4, PR-TR-N / PR-MX-N). An active measured line replaces the
  -- plain line for the same countries. Never fill one from a web source.
  measured   BOOLEAN NOT NULL DEFAULT false,
  active     BOOLEAN NOT NULL DEFAULT true,
  -- The skill's section 4 is marked "DRAFT, human confirms before first use".
  -- A draft built from an unconfirmed line is still written, tagged
  -- `unconfirmed-claim`, so the reviewer sees it next to the text.
  confirmed  BOOLEAN NOT NULL DEFAULT false,
  note       TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT claims_active_has_body CHECK (NOT active OR coalesce(btrim(body), '') <> ''),
  CONSTRAINT claims_countries_only_geo CHECK (countries IS NULL OR slot IN ('proof', 'link')),
  -- Section 12's geographies, spelled as leads.country spells them. A typo
  -- here would quietly send that country to the fallback line instead.
  CONSTRAINT claims_countries_are_geographies CHECK (
    countries IS NULL
    OR (cardinality(countries) > 0
        AND countries <@ ARRAY['Turkey', 'Mexico', 'India', 'Pakistan', 'Egypt', 'Poland', 'Romania',
                               'Hungary', 'Czech Republic', 'UAE', 'South Africa', 'Brazil', 'Argentina'])),
  CONSTRAINT claims_measured_only_proof CHECK (NOT measured OR slot = 'proof'),
  -- Skill section 3, Problem: "Always a question."
  CONSTRAINT claims_problem_is_question CHECK (slot <> 'problem' OR body IS NULL OR btrim(body) LIKE '%?'),
  -- Skill section 3, Outcome: "Must contain 'we built' or 'Nova, which we built'."
  CONSTRAINT claims_outcome_says_we_built CHECK (slot <> 'outcome' OR body IS NULL OR body ~* '\mwe built\M'),
  -- Skill section 3, Ask/Links: no scheduling link, and no link anywhere except
  -- the link slot.
  CONSTRAINT claims_no_url_outside_link CHECK (slot = 'link' OR body IS NULL OR body !~* '(https?://|www\.)'),
  -- Skill section 3, Links: "at most one plain URL", never LinkedIn, never a PDF.
  CONSTRAINT claims_link_is_one_plain_url CHECK (
    slot <> 'link' OR body IS NULL
    OR (regexp_count(body, '(https?://|www\.)', 1, 'i') = 1
        AND body !~* 'linkedin\.com'
        AND body !~* '\.pdf\M'))
);

CREATE OR REPLACE FUNCTION claims_library_touch()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS claims_library_touch_trg ON claims_library;
CREATE TRIGGER claims_library_touch_trg
  BEFORE UPDATE ON claims_library
  FOR EACH ROW EXECUTE FUNCTION claims_library_touch();

COMMENT ON TABLE claims_library IS
  'Workflow 4 approved claims library (NovaScout_DraftingSkill.md v2, section 4). Human-owned: '
  'problem/outcome/proof/ask lines are inserted verbatim into first-touch drafts; the model '
  'writes only the hook and subject. Read at runtime, never baked into workflow JSON (migration 010).';

-- The seed: skill section 4, verbatim. Every line starts unconfirmed.
--
-- Proof geography (skill section 3, Master Ref section 12): "Vertex Clinical
-- Research for Mexico and Latin America; NoblePath for Turkiye and nearby;
-- either elsewhere." Latin America among the Section 12 geographies is Mexico,
-- Brazil and Argentina. "Nearby" is read as the EMEA geographies around
-- Turkiye (Egypt, UAE, Romania, Hungary, Poland, Czech Republic); that is a
-- judgement, not a rule, and it is one edit to change. "Elsewhere" (India,
-- Pakistan, South Africa) gets PR-BOTH, the line with no countries.
INSERT INTO claims_library (code, slot, body, countries, measured, active, note) VALUES
  ('P1', 'problem',
   $$When a sponsor shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?$$,
   NULL, false, true, NULL),
  ('P2', 'problem',
   $$How many sponsor inquiries reach you only after the sponsor has already moved on?$$,
   NULL, false, true, NULL),
  ('P3', 'problem',
   $$When a sponsor evaluates you after hours, does a qualified lead land with your team — or a contact form nobody sees until morning?$$,
   NULL, false, true, NULL),
  ('O1', 'outcome',
   $$We built Nova to answer from your own material, qualify the lead, and route it wherever you already work — no new dashboard.$$,
   NULL, false, true, NULL),
  ('O2', 'outcome',
   $$We built Nova so an 11pm sponsor inquiry is answered and qualified in seconds, not the next morning — and lands in the tools you already use.$$,
   NULL, false, true, NULL),
  ('O3', 'outcome',
   $$Nova, which we built, answers sponsors from your own SOPs and service pages and hands your team a qualified lead, in your system, not ours.$$,
   NULL, false, true, NULL),
  ('PR-TR', 'proof',
   $$It's live at NoblePath, an oncology CRO in Türkiye.$$,
   ARRAY['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'], false, true,
   'Turkiye and nearby. Which countries count as nearby is a judgement -- edit countries to change it.'),
  ('PR-MX', 'proof',
   $$It's live at Vertex Clinical Research in Mexico.$$,
   ARRAY['Mexico', 'Brazil', 'Argentina'], false, true,
   'Mexico and Latin America.'),
  ('PR-BOTH', 'proof',
   $$It's live at two CROs, in Türkiye and Mexico.$$,
   NULL, false, true,
   'No countries: serves every lead no other proof line serves.'),
  ('PR-TR-N', 'proof', NULL,
   ARRAY['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'], true, false,
   'Empty on purpose. Fill ONLY with a number measured from Nova''s own analytics at NoblePath '
   '(conversations handled, leads captured, calls booked, over a stated period), then activate. '
   'Never from a web source. When active it replaces PR-TR.'),
  ('PR-MX-N', 'proof', NULL,
   ARRAY['Mexico', 'Brazil', 'Argentina'], true, false,
   'Empty on purpose. Fill ONLY with a number measured from Nova''s own analytics at Vertex, then '
   'activate. Never from a web source. When active it replaces PR-MX.'),
  ('A1', 'ask',
   $$Worth a 48-hour demo built on your own material? Reply yes and I'll set it up.$$,
   NULL, false, true, NULL),
  ('A2', 'ask',
   $$Would a demo on your material be worth 48 hours of our time? One word back is enough.$$,
   NULL, false, true, NULL),
  ('A3', 'ask',
   $$Want the 90-second recording of it running at NoblePath? Reply yes.$$,
   NULL, false, true, NULL),
  ('L-NP', 'link', NULL, NULL, false, false,
   'The one plain URL the skill allows after warm-up week 2: NoblePath''s site, as a full URL '
   '(https://...). Workflow 4 appends it after the proof line only from warm-up week 3 on. '
   'countries works as it does for proof: NULL means every lead.')
ON CONFLICT (code) DO NOTHING;

COMMIT;

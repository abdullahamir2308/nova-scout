// Select Candidates -- n8n Code node (Run Once for All Items).
//
// Workflow 1b, Trialsites. Every item is one /sites response from the AIMR
// Global Clinical Research Site Database (one Trialsites country spelling, one
// tier). This node turns those rows into at most MAX_NEW candidates for the
// website lookup, by deterministic rules only (build rule 3):
//
//   1. the country must be one the ICH GCP scraper already includes -- Section
//      12's clock table, substituted at build time -- reached through an
//      explicit alias table, never a fuzzy match. A sanctioned jurisdiction
//      can never map: the build refuses an alias that targets one.
//   2. tier A or B, identified (not an area-only marker), and active: at least
//      one trial in the last three years or one recruiting now.
//   3. an independent research site or SMO: never a hospital, a university or
//      a government body. Trialsites' own facility_type is empty on 88 % of
//      tier A/B rows (measured on 5,145 rows across eight countries,
//      2026-10-09), so the type columns are used where they speak and the NAME
//      does the rest -- an exclusion screen in the languages of the included
//      countries, then a positive signal (a research word, a company legal
//      form, or a private-practice / phase-1-unit type). A name with neither
//      is not a candidate: a lookup costs real money, and a public institution
//      is far commoner in this data than an independent site.
//   4. not already a candidate (location_id), deduplicated on name + city
//      within the run, and ordered core country first, then tier, then trial
//      recency and volume.
//
// What this node never reads, on purpose: investigators. The site detail
// endpoint can carry investigator records; this workflow never requests it, and
// nothing here would store one (the standing rule: never use personal
// investigator emails from registry data).

// ---------------------------------------------------------------------------
// Constants. The country mapping (INCLUDED_COUNTRIES, CORE_COUNTRIES, the
// Trialsites aliases, str, fold, canonicalCountry, isCore) is code_countries.js,
// which build_workflow.py prepends to this source verbatim.
// ---------------------------------------------------------------------------

// At most this many NEW candidates per harvest (the operator's brief: 200 a week).
const MAX_NEW = __MAX_NEW__;

// Types that are a public or institutional body whatever the name says.
const EXCLUDED_FACILITY_TYPES = [
  'university hospital', "children's hospital", 'government/military hospital', 'community hospital',
];
const EXCLUDED_NETWORK_TYPES = ['academic medical center', 'hospital system', 'government'];
// Types that are themselves the positive signal.
const SITE_FACILITY_TYPES = ['private practice', 'cro / phase 1 unit'];

// Hospitals, universities and government bodies, in the languages of the
// included countries. Whole words of the accent-folded, lower-cased name, so
// "trust" never fires inside "trustworthy" nor "state" inside "estate".
const EXCLUDED_WORDS = [
  'hospital', 'hospitals', 'hospitalar', 'hospitalaria', 'hospitalario', 'hopital', 'ospedale', 'hastane', 'hastanesi', 'szpital',
  'szpitala', 'szpitalny', 'korhaz', 'nemocnice', 'spital', 'spitalul', 'krankenhaus', 'klinikum',
  'ziekenhuis', 'sjukhus', 'sygehus', 'sairaala', 'nosocomio', 'bolnica', 'policlinico', 'santa casa',
  'infirmary', 'medical college', 'college', 'school', 'academy',
  'university', 'universidad', 'universidade', 'universita', 'universitat', 'universite', 'universitesi',
  'universitas', 'universitario', 'universitaria', 'universitair', 'uniwersytet', 'uniwersytecki',
  'egyetem', 'egyetemi', 'univerzita', 'univerzitet', 'faculty', 'facultad', 'faculdade', 'fakultesi',
  'fakultat', 'ministry', 'ministerio', 'ministerstwo', 'government', 'national', 'nacional', 'nazionale',
  'state', 'federal', 'municipal', 'county', 'provincial', 'public health', 'health service', 'nhs',
  'trust', 'military', 'army', 'navy', 'veterans', 'seguro social', 'imss', 'issste', 'aiims', 'pgimer',
];

// The same institutions by STEM, because the included countries' languages
// inflect: the 2026-10-09 dry run chose "Uniwersyteckie Centrum Kliniczne"
// (Gdansk's university hospital) because only "uniwersytet" and "uniwersytecki"
// were listed, and paid a lookup for it. A word STARTING with one of these is an
// institution's word in any of its forms. Not "hospital" as a stem (a Brazilian
// research centre brands itself "Hospitalize"; the hospital words above are
// whole words), and not "univers" (it would take "Universal ... LLC").
const EXCLUDED_STEMS = [
  'universit', 'universid', 'uniwersyt', 'univerzit', 'egyetem', 'szpital', 'hastane', 'nemocnic', 'spital',
  'krankenhaus', 'ziekenhuis', 'sjukhus', 'sygehus', 'sairaal', 'ospedal', 'bolnic', 'korhaz', 'ministr',
  'minister', 'fakult', 'facult', 'faculd',
];

// A research word in the name -- the site says what it is. Polish "kliniczne"
// counts: KO-MED Centra Kliniczne is a private research-site network named that
// way, and a university clinic with the same word is caught by the stems above.
const RESEARCH_WORDS = [
  'research', 'investigacion', 'investigaciones', 'investigacao', 'investigacoes', 'pesquisa',
  'pesquisas', 'badan', 'badania', 'kliniczne', 'klinicznych', 'arastirma', 'arastirmalari', 'kutato',
  'kutatas', 'kutatokozpont', 'vyzkum', 'vyzkumu', 'cercetare', 'cercetari', 'recherche', 'forschung',
  'ricerca', 'onderzoek', 'trial', 'trials', 'smo', 'site management', 'estudios clinicos',
  'estudos clinicos', 'ensayos', 'clinical studies', 'studienzentrum',
];

// A company legal form at the end of the name -- a business, not a public body.
const LEGAL_FORMS = [
  'llc', 'inc', 'ltd', 'limited', 'gmbh', 'ag', 'sc', 's c', 'sa de cv', 's a de c v', 'sapi de cv',
  's a p i de c v', 'sapi', 's de rl de cv', 's de r l de c v', 's de rl', 'ltda', 'srl', 's r l', 'sro',
  's r o', 'sp z o o', 'sp zoo', 'kft', 'zrt', 'as', 'a s', 'sti', 'ltd sti', 'pvt ltd', 'pty ltd', 'bv',
  'b v', 'oy', 'aps', 'sas', 'sarl', 'sl', 's l', 'slu', 'pc', 'p c', 'pllc', 'llp', 'pa', 'p a', 'corp',
];

function wordsRe(list) {
  return new RegExp('(^| )(' + list.map(function (w) { return w.replace(/ /g, ' '); }).join('|') + ')( |$)');
}
const EXCLUDED_NAME = wordsRe(EXCLUDED_WORDS);
const EXCLUDED_STEM = new RegExp('(^| )(' + EXCLUDED_STEMS.join('|') + ')[a-z]*( |$)');
const RESEARCH_NAME = wordsRe(RESEARCH_WORDS);
const LEGAL_FORM = new RegExp('(^| )(' + LEGAL_FORMS.join('|') + ')$');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

// Why a row is not a candidate, or null if it is one. One reason, the first
// that applies, so the run's tally adds up to the rows seen.
function rejection(row) {
  const country = canonicalCountry(row.country);
  if (!country) return 'country-not-included';
  const tier = str(row.site_tier).toUpperCase();
  if (tier !== 'A' && tier !== 'B') return 'tier';
  if (str(row.site_identification) !== 'identified') return 'not-identified';
  if (num(row.recent_trials_3yr) < 1 && num(row.active_recruiting) < 1) return 'inactive';
  const ft = fold(row.facility_type);
  const nt = fold(row.network_type);
  if (EXCLUDED_FACILITY_TYPES.indexOf(ft) !== -1) return 'institution-type';
  if (EXCLUDED_NETWORK_TYPES.indexOf(nt) !== -1) return 'institution-network';
  const name = words(row.canonical_name);
  if (!name) return 'no-name';
  if (EXCLUDED_NAME.test(name) || EXCLUDED_STEM.test(name)) return 'institution-name';
  if (!positiveSignal(name, ft)) return 'no-site-signal';
  return null;
}

// The name as space-separated words: folded, punctuation gone, so
// "Arké SMO S.A de C.V" reads "arke smo s a de c v".
function words(v) {
  return fold(v).replace(/[^a-z0-9&]+/g, ' ').trim();
}

function positiveSignal(name, facilityType) {
  if (SITE_FACILITY_TYPES.indexOf(facilityType) !== -1) return true;
  if (RESEARCH_NAME.test(name)) return true;
  // Dotted forms collapse to the spaced spellings in LEGAL_FORMS
  // ("S.A. de C.V." -> "s a de c v"), so both are listed there.
  return LEGAL_FORM.test(name);
}

// Lower is sooner. Core countries first (they earn the full geography weight),
// then tier A, then the most recent trial activity, then volume.
function priority(row, country) {
  const core = isCore(country) ? 0 : 1;
  const tier = str(row.site_tier).toUpperCase() === 'A' ? 0 : 1;
  const recent = Math.max(0, 999 - Math.min(999, num(row.recent_trials_3yr)));
  return core * 10000000 + tier * 1000000 + recent * 1000 + Math.max(0, 999 - Math.min(999, num(row.trial_count)));
}

function candidate(row) {
  const country = canonicalCountry(row.country);
  return {
    location_id: num(row.location_id),
    canonical_name: str(row.canonical_name),
    city: str(row.city) || null,
    state: str(row.state) || null,
    country: country,
    trialsites_country: str(row.country),
    site_tier: str(row.site_tier).toUpperCase(),
    trial_count: Math.round(num(row.trial_count)),
    recent_trials_3yr: Math.round(num(row.recent_trials_3yr)),
    active_recruiting: Math.round(num(row.active_recruiting)),
    quality_score: row.quality_score === null || row.quality_score === undefined ? null : num(row.quality_score),
    facility_type: str(row.facility_type) || null,
    network_type: str(row.network_type) || null,
    primary_ta: str(row.primary_ta) || null,
    therapeutic_areas: str(row.therapeutic_areas) || null,
    priority: priority(row, country),
  };
}

// The whole selection, as a pure function of the responses and what is known.
function select(responses, knownIds, maxNew) {
  const known = {};
  (knownIds || []).forEach(function (id) { known[String(id)] = true; });
  const tally = {};
  const seen = {};
  const byNameCity = {};
  let rows = 0;
  let failed = 0;
  const unmapped = {};
  const out = [];
  responses.forEach(function (resp) {
    if (!resp || !Array.isArray(resp.results)) { failed++; return; }
    resp.results.forEach(function (row) {
      rows++;
      const id = String(num(row.location_id));
      if (seen[id]) { tally['repeat-in-run'] = (tally['repeat-in-run'] || 0) + 1; return; }
      seen[id] = true;
      const why = rejection(row);
      if (why) {
        tally[why] = (tally[why] || 0) + 1;
        if (why === 'country-not-included') unmapped[str(row.country)] = true;
        return;
      }
      if (known[id]) { tally['already-a-candidate'] = (tally['already-a-candidate'] || 0) + 1; return; }
      const key = fold(row.canonical_name).replace(/[^a-z0-9]+/g, '') + '|' + fold(row.city);
      if (byNameCity[key]) { tally['same-name-and-city'] = (tally['same-name-and-city'] || 0) + 1; return; }
      byNameCity[key] = true;
      out.push(candidate(row));
    });
  });
  out.sort(function (a, b) { return a.priority - b.priority || a.location_id - b.location_id; });
  const eligible = out.length;
  const chosen = out.slice(0, maxNew);
  tally['over-the-cap'] = eligible - chosen.length;
  return {
    candidates: chosen,
    stats: {
      responses: responses.length,
      failed_responses: failed,
      rows_seen: rows,
      eligible_new: eligible,
      chosen: chosen.length,
      rejected: tally,
      unmapped_countries: Object.keys(unmapped).sort(),
    },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const gate = $('Load Site State').first().json;
const responses = $input.all().map(function (i) { return i.json; });
const result = select(responses, gate.known_ids || [], MAX_NEW);

return [{ json: { candidates: result.candidates, stats: result.stats } }];

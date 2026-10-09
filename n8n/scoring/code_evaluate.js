// Evaluate Lead — n8n Code node (Run Once for Each Item).
//
// Everything in Workflow 3 that a deterministic rule can answer: the hard
// disqualifiers, and every weighted factor except the ClinicalTrials.gov
// lookup (which needs a network call, so it is an HTTP node downstream).
//
// Master Ref build rule 3 — never spend tokens on what a string match answers.
// The model is used for exactly one thing in this workflow: the prose rationale
// on leads that survived and that have real extraction data to ground it.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses each of these out of
// NovaScout_MasterRef.md and refuses to generate the workflow if this file has
// drifted from the spec, so the doc stays the single source of truth. An n8n
// Code node cannot import a shared module, which is the only reason these
// copies exist. When the doc changes, update these lists — never the reverse.
// ---------------------------------------------------------------------------

// Section 12, "Geographies".
const TARGET_GEOGRAPHIES = [
  'Turkey',
  'Mexico',
  'India',
  'Pakistan',
  'Egypt',
  'Poland',
  'Romania',
  'Hungary',
  'Czech Republic',
  'UAE',
  'South Africa',
  'Brazil',
  'Argentina',
];

// Section 12, "Extended geographies (N points)": what every OTHER included
// country earns of the geography weight below. The core 13 above earn all of it.
const EXTENDED_GEOGRAPHY_POINTS = 5;

// Section 12, "Excluded jurisdictions": sanctioned, never scraped, earn nothing.
// The scraper already keeps them out of the CSV; this is the second line of
// control for a row that arrives another way (a manual CSV import, say).
const EXCLUDED_JURISDICTIONS = [
  'Afghanistan',
  'Belarus',
  'Cuba',
  'Iran',
  'Myanmar',
  'Nicaragua',
  'North Korea',
  'Russia',
  'Syria',
  'Venezuela',
];

// Spellings of those names that a directory or a spreadsheet uses. A matching
// aid, not a locked list: every value must resolve to an EXCLUDED_JURISDICTIONS
// entry (test_fit_score.js checks it).
const EXCLUDED_ALIASES = {
  'burma': 'Myanmar',
  'russian federation': 'Russia',
  'islamic republic of iran': 'Iran',
  'iran, islamic republic of': 'Iran',
  'korea, north': 'North Korea',
  'north korea (dprk)': 'North Korea',
  'dprk': 'North Korea',
  'democratic people\'s republic of korea': 'North Korea',
  'korea, democratic people\'s republic of': 'North Korea',
  'syrian arab republic': 'Syria',
  'bolivarian republic of venezuela': 'Venezuela',
  'venezuela, bolivarian republic of': 'Venezuela',
  'byelorussia': 'Belarus',
};

// Section 9, Workflow 3, "Weighted fit score (0-100)".
const WEIGHTS = {
  geography: 10,
  trials: 20,
  founder: 20,
  oncology: 15,
  employees: 25,
  site: 10,
};

// Section 9, Workflow 3, "Active trials for a Trialsites lead" (2026-10-09).
// Highest band reached per component; the top bands sum to WEIGHTS.trials.
const TRIALSITES_TRIALS = {
  recent_trials_3yr: [[10, 10], [5, 7], [2, 4], [1, 2]],
  trial_count: [[50, 5], [20, 4], [10, 3], [5, 2], [1, 1]],
  site_tier: { A: 5, B: 3 },
};

// Section 9, Workflow 3, "Therapeutic area taxonomy". Only 'Oncology' is
// actually scored, but canonicalising against the whole locked list is what
// makes the match reliable: the live store holds pre-enum rows in lowercase
// free text and post-enum rows in Title Case, so a case-sensitive check would
// silently miss every old row.
const THERAPEUTIC_AREAS = [
  'Oncology',
  'Cardiovascular',
  'Central Nervous System',
  'Immunology',
  'Infectious Disease',
  'Endocrinology',
  'Metabolic Disorders',
  'Respiratory',
  'Rare Diseases',
  'Internal Medicine',
  'Anesthesiology',
  'Dermatology',
  'Rheumatology',
  'Ophthalmology',
  'Gastroenterology',
  'Nephrology',
  'Hematology',
  'Other',
];

// Employee band that earns the `employees` weight in full (Section 12: "5-100
// employees"), and the enterprise cutoff that hard-disqualifies (Section 9).
const EMPLOYEE_BAND = { min: 5, max: 100 };
const ENTERPRISE_OVER = 500;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GEO_BY_KEY = {};
for (const g of TARGET_GEOGRAPHIES) GEO_BY_KEY[g.toLowerCase()] = g;

const AREA_BY_KEY = {};
for (const a of THERAPEUTIC_AREAS) AREA_BY_KEY[a.toLowerCase()] = a;

// Case-insensitive exact match, no alias table — the same rule code_normalise.js
// applies. A value that is not on the list is not guessed into a neighbour.
function canonicalAreas(raw) {
  const kept = [];
  const list = Array.isArray(raw) ? raw : [];
  for (const v of list) {
    if (v === null || v === undefined) continue;
    const canon = AREA_BY_KEY[String(v).trim().toLowerCase()];
    if (canon && kept.indexOf(canon) === -1) kept.push(canon);
  }
  return kept;
}

function inTargetGeography(country) {
  if (country === null || country === undefined) return false;
  return Object.prototype.hasOwnProperty.call(GEO_BY_KEY, String(country).trim().toLowerCase());
}

const EXCLUDED_BY_KEY = {};
for (const e of EXCLUDED_JURISDICTIONS) EXCLUDED_BY_KEY[e.toLowerCase()] = e;
for (const k of Object.keys(EXCLUDED_ALIASES)) EXCLUDED_BY_KEY[k] = EXCLUDED_ALIASES[k];

// The excluded jurisdiction `country` names, or null. Case-insensitive exact
// match on the canonical name or a known alias; like canonicalAreas, a value
// that is not on the list is not guessed into a neighbour.
function excludedJurisdiction(country) {
  if (country === null || country === undefined) return null;
  const key = String(country).trim().toLowerCase().replace(/\s+/g, ' ');
  return Object.prototype.hasOwnProperty.call(EXCLUDED_BY_KEY, key) ? EXCLUDED_BY_KEY[key] : null;
}

// Did any fetch rung get an HTTP status line back?
//
// This is the WAF-vs-dead distinction Section 9 calls for. code_fetch.js records
// each failed rung as "<label> / -> <reason>", where the reason is either a
// transport error (ENOTFOUND, timeout, TLS) or "HTTP <code>". A status code at
// all means a server answered — the host is up and serving, it just refused us.
// A 403 from a WAF is the documented case; any status is the same evidence.
//
// Returns 'http_block' (site demonstrably exists), 'no_response' (nothing ever
// answered), or 'unknown' (no error record to judge from).
function siteEvidence(fetchInfo) {
  if (!fetchInfo) return 'unknown';
  if (fetchInfo.ok === true) return 'http_block'; // reached, but unreadable
  const errors = Array.isArray(fetchInfo.errors) ? fetchInfo.errors : [];
  if (!errors.length) return 'unknown';
  for (const e of errors) {
    if (/->\s*HTTP\s+\d{3}/i.test(String(e))) return 'http_block';
  }
  return 'no_response';
}

// ---------------------------------------------------------------------------
// Hard disqualifiers (Section 9, Workflow 3)
// ---------------------------------------------------------------------------
//
// Every matching rule is recorded, not just the first: Section 9's "visibility
// over spot-checking" decision means the review queue should show the whole
// picture, and a lead cut for two reasons is a different judgement call from
// one cut on a single borderline rule. The first entry is the primary reason,
// ordered most-decisive first.
//
// Null is never evidence. Section 9 is explicit: employee_estimate is null on
// 85% of leads because extraction correctly refuses to guess, and the same
// applies to company_type on leads whose site never yielded text. `> 500` fires
// only on a confirmed number; `not a CRO, site or SMO` only on an explicit
// 'other' (or, for a lead enriched before 2026-10-09, an explicit is_cro false).
//
// company_type, as enrichment classifies it since 2026-10-09: one of the closed
// enum CRO / site / SMO / other / unclear. Anything else (a pre-enum free-text
// label, or nothing) is null here, and the legacy is_cro rule speaks instead.
const COMPANY_TYPES = ['CRO', 'site', 'SMO', 'other', 'unclear'];
function companyType(raw) {
  const v = raw && typeof raw.company_type === 'string' ? raw.company_type.trim() : '';
  return COMPANY_TYPES.indexOf(v) !== -1 ? v : null;
}
// A pre-enum label that itself says site or SMO is not evidence of "not one".
const LEGACY_SITE_LABEL = /\b(smo|site management|research (site|centre|center|clinic)|clinical (research )?sites?|investigational site)\b/i;

function hardDisqualifiers(lead) {
  const raw = lead.raw_extraction || {};
  const reasons = [];

  if (lead.blocklisted === true) {
    reasons.push({
      code: 'blocklist',
      text:
        'Domain is on the blocklist' +
        (lead.blocklist_reason ? ' (' + lead.blocklist_reason + ')' : '') + '.',
    });
  }

  const ct = companyType(raw);
  const legacyNotOne = ct === null && raw.is_cro === false &&
    !LEGACY_SITE_LABEL.test(typeof raw.company_type === 'string' ? raw.company_type : '');
  if (ct === 'other' || legacyNotOne) {
    const label = ct === null && raw.company_type ? '"' + raw.company_type + '"' : 'another kind of organisation';
    reasons.push({
      code: 'not_cro_site_smo',
      text:
        'Not a CRO, site or SMO — enrichment read the site as ' + label +
        ' (a hospital, university, government body, agency, consultancy, vendor or supplier). ' +
        (lead.source === 'trialsites'
          ? 'Found through Trialsites, whose registry rows include institutions the selection rules did not catch by name.'
          : 'Sourced from the ICH GCP CRO directory, so this is a directory listing that has ' +
            'drifted (rebrand, vendor miscategorised, or defunct) rather than a scraping error.'),
    });
  }

  if (typeof lead.employee_estimate === 'number' && lead.employee_estimate > ENTERPRISE_OVER) {
    reasons.push({
      code: 'enterprise_scale',
      text:
        'Enterprise CRO — roughly ' + lead.employee_estimate + ' employees, above the ' +
        ENTERPRISE_OVER + '-employee cutoff. Confirmed figure stated on the site, not an estimate.',
    });
  }

  if (lead.has_chatbot === true) {
    reasons.push({
      code: 'has_chatbot',
      text:
        'Already runs a chat widget' +
        (lead.chatbot_vendor ? ' (' + lead.chatbot_vendor + ')' : '') +
        ' — detected by matching the vendor embed script, not inferred.',
    });
  }

  // "No functioning website" — and only when nothing ever answered.
  //
  // Section 9's design implication, applied: a confirmed HTTP response is
  // WEAKER evidence than a connection failure, because a 403 proves the site
  // exists. Those leads are not cut here; they fall through to scoring with
  // whatever is known (geography, trials) and carry a needs_recheck flag, so a
  // hostile WAF costs a lead points rather than killing it permanently.
  //
  // The bounded-retry-with-attempt-counter mechanism Section 9 sketches is
  // deliberately NOT built here — it needs an attempt counter written by
  // Workflow 2 and read by Workflow 3, i.e. new schema coordination between the
  // two. Worth building once there is real scoring output to learn from.
  const evidence = siteEvidence(raw.fetch);
  const status = raw.enrichment_status;
  if ((status === 'unreachable' || status === 'no_readable_text') && evidence === 'no_response') {
    const errs = (raw.fetch && Array.isArray(raw.fetch.errors) ? raw.fetch.errors : []).slice(0, 2);
    reasons.push({
      code: 'no_website',
      text:
        'No functioning website — every connection attempt failed outright (no HTTP response ' +
        'on any of the four retry rungs: direct HTTPS, www, TLS-relaxed, plain HTTP).' +
        (errs.length ? ' First failures: ' + errs.join('; ') + '.' : ''),
    });
  }

  return reasons;
}

// ---------------------------------------------------------------------------
// Weighted factors (Section 9, Workflow 3)
// ---------------------------------------------------------------------------
//
// Null-handling, per Section 9: "where employee_estimate or founder_name /
// founder_linkedin is null, that factor contributes a neutral partial score,
// not zero — a confirmed miss should score lower than an honest unknown. Don't
// let extraction's correct refusal to guess become a scoring penalty."
//
// So each of those two factors has three levels, not two: full credit, a
// half-weight neutral for an honest unknown, and a below-neutral score for a
// confirmed miss.

// Three tiers (Section 12), never unknown-neutral:
//   core 13            -> the full geography weight
//   excluded           -> 0 (a sanctioned jurisdiction is a miss, however it scores elsewhere)
//   any other country  -> EXTENDED_GEOGRAPHY_POINTS (it is on the directory, so it is in scope)
//   no country         -> 0
function scoreGeography(lead) {
  const country = lead.country === null || lead.country === undefined ? '' : String(lead.country).trim();
  if (inTargetGeography(country)) {
    return {
      points: WEIGHTS.geography,
      max: WEIGHTS.geography,
      basis: 'confirmed',
      detail: country + ' is a target geography',
    };
  }
  if (!country) {
    return { points: 0, max: WEIGHTS.geography, basis: 'miss', detail: 'no country recorded' };
  }
  const excluded = excludedJurisdiction(country);
  if (excluded) {
    return {
      points: 0,
      max: WEIGHTS.geography,
      basis: 'miss',
      detail: country + ' is an excluded jurisdiction (' + excluded + ')',
    };
  }
  return {
    points: EXTENDED_GEOGRAPHY_POINTS,
    max: WEIGHTS.geography,
    basis: 'confirmed',
    detail: country + ' is an extended geography (half credit; the core geographies earn the full weight)',
  };
}

// Three levels, because "we found the founder and they are demonstrably not on
// LinkedIn" is worse news than "the site never named a founder":
//   full     — named founder AND a LinkedIn URL resolved for them
//   unknown  — no founder named, OR no LinkedIn URLs on the site to match
//              against (the matcher never had a chance, so this is not a miss)
//   miss     — founder named, candidate URLs were harvested, none matched them
function scoreFounder(lead) {
  const raw = lead.raw_extraction || {};
  const candidates = Array.isArray(raw.founder_linkedin_candidates)
    ? raw.founder_linkedin_candidates
    : [];
  const half = WEIGHTS.founder / 2;
  const missPoints = Math.round(WEIGHTS.founder * 0.3);

  if (lead.founder_name && lead.founder_linkedin) {
    return {
      points: WEIGHTS.founder,
      max: WEIGHTS.founder,
      basis: 'confirmed',
      detail: lead.founder_name + ' identified with a LinkedIn profile',
    };
  }
  if (lead.founder_name && candidates.length > 0) {
    return {
      points: missPoints,
      max: WEIGHTS.founder,
      basis: 'miss',
      detail:
        lead.founder_name +
        ' identified, but none of the ' + candidates.length +
        ' LinkedIn profiles on the site matched them',
    };
  }
  if (lead.founder_name) {
    return {
      points: half,
      max: WEIGHTS.founder,
      basis: 'unknown',
      detail: lead.founder_name + ' identified; site carried no LinkedIn profiles to match against',
    };
  }
  return {
    points: half,
    max: WEIGHTS.founder,
    basis: 'unknown',
    detail: 'no founder or MD named on the site',
  };
}

function scoreOncology(lead) {
  const areas = canonicalAreas(lead.therapeutic_areas);
  const hit = areas.indexOf('Oncology') !== -1;
  return {
    points: hit ? WEIGHTS.oncology : 0,
    max: WEIGHTS.oncology,
    basis: hit ? 'confirmed' : (areas.length ? 'miss' : 'unknown'),
    detail: hit
      ? 'oncology among the stated therapeutic areas'
      : (areas.length
          ? 'therapeutic areas stated (' + areas.slice(0, 4).join(', ') + ') but no oncology'
          : 'no therapeutic areas extracted'),
    areas: areas,
  };
}

function scoreEmployees(lead) {
  const n = lead.employee_estimate;
  const half = WEIGHTS.employees / 2;
  if (typeof n !== 'number' || !isFinite(n)) {
    return {
      points: half,
      max: WEIGHTS.employees,
      basis: 'unknown',
      detail: 'headcount not stated on the site',
    };
  }
  const hit = n >= EMPLOYEE_BAND.min && n <= EMPLOYEE_BAND.max;
  return {
    points: hit ? WEIGHTS.employees : 0,
    max: WEIGHTS.employees,
    basis: hit ? 'confirmed' : 'miss',
    detail: hit
      ? 'roughly ' + n + ' employees, inside the ' + EMPLOYEE_BAND.min + '-' + EMPLOYEE_BAND.max + ' band'
      : 'roughly ' + n + ' employees, outside the ' + EMPLOYEE_BAND.min + '-' + EMPLOYEE_BAND.max + ' band',
  };
}

// "Site quality suggests budget", scored off what the fetch actually measured
// rather than off the model's prose notes: how much real text the site carries,
// whether it has the standard about/services/team structure, and whether it is
// served over a working certificate. A maintained, invested-in site scores high
// on all three; a one-page brochure behind an expired cert does not.
//
// Deliberately deterministic. The model's site_quality_notes go into the
// rationale prompt as colour, but they do not move the number.
function scoreSite(lead) {
  const raw = lead.raw_extraction || {};
  const f = raw.fetch;
  const half = WEIGHTS.site / 2;

  if (!f || typeof f.prompt_chars !== 'number') {
    return {
      points: half,
      max: WEIGHTS.site,
      basis: 'unknown',
      detail: 'site could not be read, so quality is unassessed',
    };
  }

  let points = 0;
  const notes = [];

  // Content depth. Thresholds sit on the measured spread of the live corpus
  // (p25 ~6k chars, p50 ~13k, capped at 24k by the fetcher).
  const chars = f.prompt_chars;
  if (chars >= 12000) {
    points += 6;
    notes.push('substantial content (' + chars + ' chars)');
  } else if (chars >= 6000) {
    points += 4;
    notes.push('moderate content (' + chars + ' chars)');
  } else if (chars >= 2500) {
    points += 2;
    notes.push('thin content (' + chars + ' chars)');
  } else {
    notes.push('very little readable content (' + chars + ' chars)');
  }

  // Structural depth — did the site have the standard about/services/team pages?
  const discovered = Array.isArray(f.discovered) ? f.discovered.length : 0;
  if (discovered >= 3) {
    points += 4;
    notes.push(discovered + ' standard subpages');
  } else if (discovered >= 1) {
    points += 2;
    notes.push(discovered + ' subpage' + (discovered === 1 ? '' : 's'));
  } else {
    notes.push('no about/services/team pages found');
  }

  // A site only reachable with certificate validation relaxed, or only over
  // plain HTTP, is not one anyone is maintaining. Only applied when the fetcher
  // actually recorded which rung won — null means the row predates that field,
  // which is an unknown, not a penalty.
  if (f.reached_via === 'https+insecure' || f.reached_via === 'http') {
    points = Math.max(0, points - 2);
    notes.push('reachable only via ' + f.reached_via + ' (broken or missing TLS)');
  }

  return {
    points: points,
    max: WEIGHTS.site,
    basis: 'confirmed',
    detail: notes.join('; '),
  };
}

// Active trials for a lead Workflow 1b found through Trialsites (Section 9,
// Workflow 3, 2026-10-09). Trialsites counts trials per LOCATION across 14
// registries, so for a site this measures something real -- unlike the
// ClinicalTrials.gov sponsor match, which a CRO or a site rarely is. Returns
// null when the lead has no Trialsites record; Compute Fit Score then uses the
// ClinicalTrials.gov lookup exactly as before.
function band(value, bands) {
  const v = Number(value);
  if (!Number.isFinite(v)) return 0;
  for (let i = 0; i < bands.length; i++) if (v >= bands[i][0]) return bands[i][1];
  return 0;
}

function scoreTrialsites(lead) {
  if (lead.ts_location_id === null || lead.ts_location_id === undefined) return null;
  const recent = Number(lead.ts_recent_trials_3yr) || 0;
  const total = Number(lead.ts_trial_count) || 0;
  const tier = String(lead.ts_site_tier || '').toUpperCase();
  const pRecent = band(recent, TRIALSITES_TRIALS.recent_trials_3yr);
  const pTotal = band(total, TRIALSITES_TRIALS.trial_count);
  const pTier = TRIALSITES_TRIALS.site_tier[tier] || 0;
  return {
    points: Math.min(WEIGHTS.trials, pRecent + pTotal + pTier),
    max: WEIGHTS.trials,
    basis: 'confirmed',
    detail: 'Trialsites: ' + recent + ' trial' + (recent === 1 ? '' : 's') + ' in the last 3 years (' + pRecent +
      '), ' + total + ' in all (' + pTotal + '), tier ' + (tier || '?') + ' (' + pTier + ')',
    source: 'trialsites',
    recent_trials_3yr: recent,
    trial_count: total,
    active_recruiting: Number(lead.ts_active_recruiting) || 0,
    site_tier: tier || null,
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const lead = $input.item.json;
const rawEx = lead.raw_extraction || {};
const reasons = hardDisqualifiers(lead);
const evidence = siteEvidence(rawEx.fetch);

// Grounding guard, the same rule Section 9 Workflow 4 locks: a small model
// fabricates when under-informed. A lead whose site never yielded text has no
// facts to write a rationale from, so it never reaches the model — it gets a
// deterministic rationale describing exactly what is and is not known.
const hasExtraction = rawEx.enrichment_status === 'ok';

if (reasons.length) {
  return {
    json: {
      lead_id: lead.lead_id,
      domain: lead.domain,
      company_name: lead.company_name,
      country: lead.country,
      disqualified: true,
      disqualify_codes: reasons.map(function (r) { return r.code; }),
      disqualify_reason: reasons.map(function (r) { return r.text; }).join(' '),
      site_evidence: evidence,
      needs_recheck: false,
      has_extraction: hasExtraction,
    },
  };
}

const factors = {
  geography: scoreGeography(lead),
  founder: scoreFounder(lead),
  oncology: scoreOncology(lead),
  employees: scoreEmployees(lead),
  site: scoreSite(lead),
};

// A lead that survived the disqualifiers but whose site we could not read: the
// host answered (or served an empty shell), so "no functioning website" does
// not apply, but there is nothing to ground a rationale in. Flagged for the
// review queue rather than quietly scored as if the data were real.
const needsRecheck = !hasExtraction;

return {
  json: {
    lead_id: lead.lead_id,
    domain: lead.domain,
    company_name: lead.company_name,
    country: lead.country,
    disqualified: false,
    factors: factors,
    // Everything the rationale prompt needs, so the downstream node does not
    // have to reach back across the workflow for the enrichment row.
    facts: {
      founder_name: lead.founder_name || null,
      founder_title: rawEx.founder_title || null,
      founder_linkedin: lead.founder_linkedin || null,
      city: rawEx.city || null,
      company_type: rawEx.company_type || null,
      source: lead.source || null,
      phases: Array.isArray(lead.phases) ? lead.phases : [],
      areas: factors.oncology.areas,
      employee_estimate: typeof lead.employee_estimate === 'number' ? lead.employee_estimate : null,
      site_quality_notes: lead.site_quality_notes || null,
    },
    site_evidence: evidence,
    needs_recheck: needsRecheck,
    has_extraction: hasExtraction,
    enrichment_status: rawEx.enrichment_status || null,
    // ClinicalTrials.gov is a per-candidate lookup keyed on a name we already
    // have (Section 9), so it runs only for leads that got this far.
    ctgov_sponsor: lead.company_name || lead.domain,
    // Set only for a Trialsites lead; Compute Fit Score then uses it for the
    // trials factor and ignores the ClinicalTrials.gov answer (and its failure).
    trialsites_trials: scoreTrialsites(lead),
  },
};

// Trialsites country mapping -- shared by Build Harvest Requests and Select
// Candidates. build_workflow.py prepends this file, verbatim, to both nodes'
// sources, so the two can never disagree about which countries are harvested.
// It must never read another node or its own input: it is not a node of its own.
//
// "Only countries the CRO scraper already includes" (the operator's brief) is
// Section 12's clock table: every country the ICH GCP scraper takes has a row
// there, and Send can reach no other. Both lists are substituted at build time
// from NovaScout_MasterRef.md by n8n/sites/build_workflow.py.

const INCLUDED_COUNTRIES = __INCLUDED_COUNTRIES__;
const CORE_COUNTRIES = __CORE_COUNTRIES__;

// Trialsites spellings that are not the leads.country spelling, read from the
// live /countries list on 2026-10-09 (236 spellings). A spelling neither here
// nor equal to an included name is simply not harvested -- nothing guesses. The
// build refuses an alias whose target is not an included country, so a
// sanctioned jurisdiction can never be reached through this table ("Korea,
// Democratic People's Republic Of" is in Trialsites and deliberately absent).
const TRIALSITES_ALIASES = {
  'united states': 'USA',
  'united states of america': 'USA',
  'czechia': 'Czech Republic',
  'korea, south': 'South Korea',
  'korea, republic of': 'South Korea',
  'turkey (turkiye)': 'Turkey',
  'united arab emirates': 'UAE',
  'tanzania, united republic of': 'Tanzania',
  'democratic republic of the congo': 'DR Congo',
  'congo (the democratic republic of the)': 'DR Congo',
  'congo, democratic republic': 'DR Congo',
  'republic of the congo': 'Congo',
  'north macedonia': 'Macedonia',
  'the gambia': 'Gambia',
  'the bahamas': 'Bahamas',
  'bosnia & herzegovina': 'Bosnia and Herzegovina',
};

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Accent-folded, lower-cased, curly apostrophes made straight: "Côte d’Ivoire"
// and "Türkiye" meet the table the way a person would expect.
function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[‘’]/g, "'")
    .toLowerCase().replace(/\s+/g, ' ');
}

const INCLUDED_BY_KEY = {};
INCLUDED_COUNTRIES.forEach(function (c) { INCLUDED_BY_KEY[fold(c)] = c; });
const CORE_KEYS = CORE_COUNTRIES.map(fold);

// leads.country for a Trialsites spelling, or null if it is not harvested.
function canonicalCountry(trialsitesName) {
  const k = fold(trialsitesName);
  if (Object.prototype.hasOwnProperty.call(INCLUDED_BY_KEY, k)) return INCLUDED_BY_KEY[k];
  if (Object.prototype.hasOwnProperty.call(TRIALSITES_ALIASES, k)) {
    const target = fold(TRIALSITES_ALIASES[k]);
    return Object.prototype.hasOwnProperty.call(INCLUDED_BY_KEY, target) ? INCLUDED_BY_KEY[target] : null;
  }
  return null;
}

function isCore(country) {
  return CORE_KEYS.indexOf(fold(country)) !== -1;
}

// Research-site candidates from the two open sources (migration 025).
//
//   node candidates.js <trialsites.json> <ctgov.jsonl> <overture.json> <out.json>
//
// No model, no network: deterministic screens and a name + city match.
//
//   ClinicalTrials.gov  every facility of a recruiting, industry-funded study,
//                       aggregated to facility + city + country, with the studies
//                       recruiting AT that location (the location's own status).
//   Overture places     every place with a website whose name or category says
//                       research site.
//
// The screens are the SHIPPED Trialsites rules, read out of trialsites.json so
// nothing is retyped: the included-country map (Section 12's clock table, no
// sanctioned jurisdiction), the hospital / university / government words and
// stems in the included languages, the research-word signal; and the shipped
// website screens of Verify Website (directory or social site, institutional
// domain, excluded ccTLD). On top, this niche's own:
//   * a sponsor's placeholder ("Research Site", "Novartis Investigative Site",
//     "Local Institution") is never a facility;
//   * a sponsor (pharma, biotech, device) or a CRO by name is not a site;
//   * an Overture place needs a research word in its name, or a matching
//     ClinicalTrials.gov facility.
// A ClinicalTrials.gov facility gets a website only from an Overture place of
// the same country whose name is the facility's (folded, legal forms off) and
// whose locality is the facility's city. Nothing is guessed: no match, no
// candidate. The website is then fetched and confirmed by verify.js.

const fs = require('fs');
const [wfPath, ctPath, ovPath, outPath] = process.argv.slice(2);
if (!outPath) { console.error('usage: node candidates.js <trialsites.json> <ctgov.jsonl> <overture.json> <out.json>'); process.exit(2); }

const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
function nodeLib(name, returns) {
  const n = wf.nodes.find((x) => x.name === name);
  if (!n) throw new Error('trialsites.json has no node ' + name);
  const src = n.parameters.jsCode;
  return new Function(src.slice(0, src.indexOf('// Node body')) + '\nreturn {' + returns.join(',') + '};')();
}
const SEL = nodeLib('Select Candidates', ['canonicalCountry', 'fold', 'str', 'words', 'positiveSignal', 'EXCLUDED_NAME',
  'EXCLUDED_STEM', 'RESEARCH_NAME', 'isCore', 'INCLUDED_COUNTRIES']);
const VER = nodeLib('Verify Website', ['hostRejection', 'hostOf', 'core']);

// Overture writes ISO 3166-1 alpha-2; leads.country is Section 12's spelling.
// Every included country has a code here and nothing else does
// (test_open_sources.js checks both directions against the shipped list).
const ISO2 = {
  AR: 'Argentina', BR: 'Brazil', CZ: 'Czech Republic', EG: 'Egypt', HU: 'Hungary', IN: 'India', MX: 'Mexico',
  PK: 'Pakistan', PL: 'Poland', RO: 'Romania', ZA: 'South Africa', TR: 'Turkey', AE: 'UAE', AL: 'Albania',
  DZ: 'Algeria', AO: 'Angola', AM: 'Armenia', AU: 'Australia', AT: 'Austria', AZ: 'Azerbaijan', BS: 'Bahamas',
  BH: 'Bahrain', BD: 'Bangladesh', BE: 'Belgium', BJ: 'Benin', BA: 'Bosnia and Herzegovina', BG: 'Bulgaria',
  BF: 'Burkina Faso', BI: 'Burundi', KH: 'Cambodia', CM: 'Cameroon', CA: 'Canada', CL: 'Chile', CN: 'China',
  CO: 'Colombia', CG: 'Congo', CR: 'Costa Rica', CI: "Cote d'Ivoire", HR: 'Croatia', CY: 'Cyprus', CD: 'DR Congo',
  DK: 'Denmark', DJ: 'Djibouti', DO: 'Dominican Republic', EC: 'Ecuador', SV: 'El Salvador', GQ: 'Equatorial Guinea',
  EE: 'Estonia', ET: 'Ethiopia', FI: 'Finland', FR: 'France', GA: 'Gabon', GM: 'Gambia', GE: 'Georgia', DE: 'Germany',
  GH: 'Ghana', GR: 'Greece', GT: 'Guatemala', GN: 'Guinea', HK: 'Hong Kong', ID: 'Indonesia', IE: 'Ireland',
  IL: 'Israel', IT: 'Italy', JM: 'Jamaica', JP: 'Japan', JO: 'Jordan', KZ: 'Kazakhstan', KE: 'Kenya', XK: 'Kosovo',
  KW: 'Kuwait', KG: 'Kyrgyzstan', LV: 'Latvia', LB: 'Lebanon', LR: 'Liberia', LT: 'Lithuania', LU: 'Luxembourg',
  MK: 'Macedonia', MG: 'Madagascar', MW: 'Malawi', MY: 'Malaysia', ML: 'Mali', MT: 'Malta', MR: 'Mauritania',
  MU: 'Mauritius', MD: 'Moldova', MN: 'Mongolia', ME: 'Montenegro', MA: 'Morocco', MZ: 'Mozambique', NP: 'Nepal',
  NL: 'Netherlands', NZ: 'New Zealand', NG: 'Nigeria', NO: 'Norway', OM: 'Oman', PA: 'Panama', PG: 'Papua New Guinea',
  PY: 'Paraguay', PE: 'Peru', PH: 'Philippines', PT: 'Portugal', QA: 'Qatar', RW: 'Rwanda', LC: 'Saint Lucia',
  SA: 'Saudi Arabia', SN: 'Senegal', RS: 'Serbia', SG: 'Singapore', SK: 'Slovakia', SI: 'Slovenia', KR: 'South Korea',
  ES: 'Spain', LK: 'Sri Lanka', SR: 'Suriname', SE: 'Sweden', CH: 'Switzerland', TW: 'Taiwan', TZ: 'Tanzania',
  TH: 'Thailand', TG: 'Togo', TT: 'Trinidad and Tobago', TN: 'Tunisia', TM: 'Turkmenistan', US: 'USA', UG: 'Uganda',
  UA: 'Ukraine', GB: 'United Kingdom', UY: 'Uruguay', UZ: 'Uzbekistan', VN: 'Vietnam', ZM: 'Zambia', ZW: 'Zimbabwe',
};

// Section 12: occupied territory is excluded as territory (scraper/geography.py's list).
const OCCUPIED = /\b(crimea|krym|sevastopol|donetsk|luhansk|lugansk|kherson|zaporizhzhia|zaporozhye)\b/;

const PLACEHOLDER = /(^| )(investigational|investigative|investigator|investigators|research|study|clinical research|clinical|local|trial) (site|sites|institution|center site|facility)( |$)|^(site|centre|center|institution)( |$)|(^| )site (no|number|#)?\s*\d/;
const SPONSOR = /(^| )(pharma|pharmaceutical|pharmaceuticals|therapeutics|biotech|biotechnology|bioscience|biosciences|biopharma|biologics|vaccines?|medical devices?|novartis|pfizer|lilly|astrazeneca|merck|sanofi|roche|genentech|bayer|janssen|abbvie|amgen|gsk|glaxosmithkline|boehringer|takeda|novo nordisk|bristol myers|moderna)( |$)/;
const CRO = /(^| )(cro|contract research|iqvia|icon|parexel|syneos|ppd|labcorp|covance|medpace|fortrea|worldwide clinical trials|psi cro)( |$)/;

function screenName(name) {
  const w = SEL.words(name);
  if (!w) return 'no-name';
  if (PLACEHOLDER.test(w)) return 'sponsor-placeholder';
  if (SEL.EXCLUDED_NAME.test(w) || SEL.EXCLUDED_STEM.test(w)) return 'institution-name';
  if (SPONSOR.test(w)) return 'sponsor-name';
  if (CRO.test(w)) return 'cro-name';
  return null;
}

// The match key: the name's words with legal forms off (Verify Website's core()).
function key(name) {
  return VER.core(name).replace(/^the /, '');
}
function cityKey(c) {
  return SEL.fold(c).replace(/[^a-z0-9]+/g, ' ').trim();
}
function sameCity(a, b) {
  const x = cityKey(a), y = cityKey(b);
  return !!x && !!y && (x === y || x.indexOf(y + ' ') === 0 || y.indexOf(x + ' ') === 0);
}

const tally = { ctgov: {}, overture: {}, match: {} };
function count(where, why) { tally[where][why] = (tally[where][why] || 0) + 1; }

// --- ClinicalTrials.gov facilities --------------------------------------------------
const fac = {};
for (const line of fs.readFileSync(ctPath, 'utf8').split('\n')) {
  if (!line) continue;
  const r = JSON.parse(line);
  if (!r.facility) continue;
  const k = key(r.facility) + '|' + cityKey(r.city) + '|' + SEL.fold(r.country);
  const f = fac[k] || (fac[k] = { facility: r.facility, city: r.city, state: r.state, country_raw: r.country, recruiting: {}, sponsors: {} });
  if (String(r.status).toUpperCase() === 'RECRUITING') f.recruiting[r.nct] = true;
  if (r.sponsor) f.sponsors[r.sponsor] = true;
}
const facilities = [];
for (const f of Object.values(fac)) {
  const country = SEL.canonicalCountry(f.country_raw);
  let why = !country ? 'country-not-included' : screenName(f.facility);
  if (!why && country === 'Ukraine' && OCCUPIED.test(SEL.fold(f.city + ' ' + f.state))) why = 'occupied-territory';
  if (!why && !Object.keys(f.recruiting).length) why = 'not-recruiting-here';
  count('ctgov', why || 'kept');
  if (!why) facilities.push({ facility: f.facility, city: f.city, state: f.state, country: country,
    recruiting: Object.keys(f.recruiting).sort(), sponsors: Object.keys(f.sponsors).slice(0, 5) });
}

// --- Overture places ---------------------------------------------------------------
const places = JSON.parse(fs.readFileSync(ovPath, 'utf8'));
const byKey = {};
const kept = [];
for (const p of places) {
  const country = ISO2[String(p.country || '').toUpperCase()] || null;
  let why = !country ? 'country-not-included' : screenName(p.name);
  if (!why && country === 'Ukraine' && OCCUPIED.test(SEL.fold([p.locality, p.region, p.freeform].join(' ')))) why = 'occupied-territory';
  if (!why && p.operating_status && p.operating_status !== 'open') why = 'not-operating';
  const host = VER.hostOf(p.website);
  if (!why) {
    const rej = VER.hostRejection(host);
    if (rej) why = 'website-' + rej.outcome;
  }
  count('overture', why || 'kept');
  if (why) continue;
  const row = { overture_id: p.id, name: p.name, city: p.locality || null, region: p.region || null, country: country,
    website: p.website, host: host, category: p.cat || null, licences: p.src || [],
    research_name: SEL.RESEARCH_NAME.test(SEL.words(p.name)) };
  kept.push(row);
  const k = country + '|' + key(p.name);
  (byKey[k] = byKey[k] || []).push(row);
}

// --- Match ClinicalTrials.gov facilities to Overture websites --------------------------
const out = [];
const usedPlace = {};
for (const f of facilities) {
  const options = byKey[f.country + '|' + key(f.facility)] || [];
  const same = options.filter((o) => sameCity(o.city, f.city));
  if (!options.length) { count('match', 'no-overture-place-of-that-name'); continue; }
  if (!same.length) { count('match', 'name-matches-but-not-the-city'); continue; }
  count('match', 'matched');
  const o = same[0];
  usedPlace[o.overture_id] = true;
  out.push(Object.assign({}, o, { source: 'sites-ctgov', facility_name: f.facility, city: f.city || o.city,
    state: f.state, ctgov_recruiting: f.recruiting.length, ctgov_ncts: f.recruiting.slice(0, 50),
    core: SEL.isCore(f.country) }));
}
// Overture places with a research word in the name and no ClinicalTrials.gov match.
for (const o of kept) {
  if (usedPlace[o.overture_id]) continue;
  if (!o.research_name) { count('overture', 'no-research-word-and-no-ctgov-match'); continue; }
  out.push(Object.assign({}, o, { source: 'sites-overture', facility_name: null, state: null, ctgov_recruiting: 0,
    ctgov_ncts: [], core: SEL.isCore(o.country) }));
}

// One candidate per website host: the one with the most recruiting trials, then a
// ClinicalTrials.gov match, then a core country, then the name.
const byHost = {};
for (const c of out) {
  const b = byHost[c.host];
  const better = !b || c.ctgov_recruiting > b.ctgov_recruiting ||
    (c.ctgov_recruiting === b.ctgov_recruiting && (c.core && !b.core || (c.core === b.core && c.name < b.name)));
  if (better) byHost[c.host] = Object.assign({}, c, { locations_on_host: (b ? b.locations_on_host : 0) + 1 });
  else b.locations_on_host += 1;
}
const final = Object.values(byHost).sort((a, b) => (b.ctgov_recruiting - a.ctgov_recruiting) || (b.core - a.core) || a.host.localeCompare(b.host));
fs.writeFileSync(outPath, JSON.stringify(final, null, 1));
console.log(JSON.stringify({ tally: tally, candidates: final.length,
  by_source: final.reduce((m, c) => (m[c.source] = (m[c.source] || 0) + 1, m), {}) }, null, 1));

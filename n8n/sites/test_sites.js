// Unit tests for Workflow 1b (Trialsites) -- run against the jsCode inside the
// GENERATED n8n/workflows/trialsites.json, so what is tested is what ships.
//
//     python n8n/sites/build_workflow.py && node n8n/sites/test_sites.js
//
// No network: the Trialsites rows are shapes from the 2026-10-09 sample, and
// helpers.httpRequest is mocked with a page table per URL.

const fs = require('fs');
const path = require('path');

const WF = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'trialsites.json'), 'utf8'));
const CODE = {};
WF.nodes.forEach(function (n) { if (n.parameters && n.parameters.jsCode) CODE[n.name] = n.parameters.jsCode; });

// Globals the n8n Code sandbox does not provide (verified on the live instance).
const SANDBOX_MISSING = ['URL', 'URLSearchParams', 'fetch', 'AbortController'];

let passed = 0;
let failed = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) { passed++; console.log('  ok   ' + label); } else {
    failed++; console.log('  FAIL ' + label + '\n         expected ' + e + '\n         actual   ' + a);
  }
}

// The functions above "// Node body", for direct tests.
function lib(node, names) {
  const src = CODE[node];
  const end = src.indexOf('// Node body');
  return new Function(...SANDBOX_MISSING, src.slice(0, end) + '\nreturn {' + names.join(',') + '};')();
}

function run(node, items, upstream, helpers) {
  const src = CODE[node];
  const $input = { all: () => items, first: () => items[0], item: items[0] };
  const $ = function (name) {
    const feed = (upstream || {})[name];
    if (!feed) throw new Error('no upstream ' + name);
    return { first: () => feed[0], all: () => feed, item: feed[0] };
  };
  const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;
  const fn = new AsyncFn('$input', '$', ...SANDBOX_MISSING, src);
  return fn.call({ helpers: helpers || {} }, $input, $, ...SANDBOX_MISSING.map(() => undefined));
}

function row(over) {
  return Object.assign({
    location_id: 1, canonical_name: 'Proactive Cr Mexico SA de Cv', city: 'Irapuato', state: 'Guanajuato',
    country: 'Mexico', site_tier: 'A', site_identification: 'identified', trial_count: 30,
    recent_trials_3yr: 12, active_recruiting: 3, facility_type: null, network_type: null,
    quality_score: 0.6, primary_ta: 'Oncology', therapeutic_areas: 'Oncology',
  }, over || {});
}

(async function main() {
  console.log('Workflow 1b -- Trialsites sites and SMOs\n');

  // ------------------------------------------------------------------ countries
  const C = lib('Select Candidates', ['canonicalCountry', 'isCore', 'rejection', 'select']);
  check('an included country maps to itself', C.canonicalCountry('Mexico'), 'Mexico');
  check('"United States Of America" -> USA', C.canonicalCountry('United States Of America'), 'USA');
  check('"Czechia" -> Czech Republic (core)', [C.canonicalCountry('Czechia'), C.isCore('Czech Republic')], ['Czech Republic', true]);
  check('"Turkey (Türkiye)" -> Turkey', C.canonicalCountry('Turkey (Türkiye)'), 'Turkey');
  check('"Côte d’Ivoire" -> Cote d\'Ivoire (accents and curly apostrophe folded)', C.canonicalCountry('Côte d’Ivoire'), "Cote d'Ivoire");
  check('North Korea is never harvested', C.canonicalCountry("Korea, Democratic People'S Republic Of"), null);
  check('Russia is never harvested', C.canonicalCountry('Russia'), null);
  check('Iran (sanctioned) is never harvested', C.canonicalCountry('Iran'), null);

  // ------------------------------------------------------------------ harvest requests
  const reqs = await run('Build Harvest Requests', [
    { json: { country: 'United States', sites: 32296 } }, { json: { country: 'Mexico', sites: 900 } },
    { json: { country: 'Russia', sites: 3000 } }, { json: { country: 'Czechia', sites: 132 } },
    { json: { country: 'Atlantis', sites: 5 } }, { json: { country: 'Bahamas', sites: 0 } },
  ]);
  check('one request per harvestable spelling per tier; Russia, unknown and zero-site rows cost nothing',
    reqs.map(r => r.json.trialsites_country + '/' + r.json.tier),
    ['Czechia/A', 'Czechia/B', 'Mexico/A', 'Mexico/B', 'United States/A', 'United States/B']);
  check('core countries are requested first', reqs.map(r => r.json.core), [true, true, true, true, false, false]);
  let threw = null;
  try { await run('Build Harvest Requests', [{ json: { error: 'rate_limited' } }]); } catch (e) { threw = e.message; }
  check('an unreadable /countries answer fails loudly instead of logging an empty harvest', /no harvestable country/.test(threw || ''), true);

  // ------------------------------------------------------------------ selection
  check('a tier-A company with a legal form is a candidate', C.rejection(row()), null);
  check('a research word is a candidate', C.rejection(row({ canonical_name: 'Centro de Investigacion Clinica Acelerada' })), null);
  check('a Private Practice type is a candidate on its own', C.rejection(row({ canonical_name: 'Dr Smith', facility_type: 'Private Practice' })), null);
  check('a hospital by name is not', C.rejection(row({ canonical_name: 'Hospital Angeles Lomas SA de CV' })), 'institution-name');
  check('a Polish hospital by name is not', C.rejection(row({ canonical_name: 'Szpital Wojewodzki Sp. z o.o.', country: 'Poland' })), 'institution-name');
  check('a university is not', C.rejection(row({ canonical_name: 'Universidad Autonoma de Nuevo Leon Research Unit' })), 'institution-name');
  check('a ministry / government body is not', C.rejection(row({ canonical_name: 'Ministry of Health Research Centre' })), 'institution-name');
  // 2026-10-09 dry run: an inflected form got through and cost a lookup.
  check('an inflected university word is not ("Uniwersyteckie Centrum Kliniczne")',
    C.rejection(row({ canonical_name: 'Uniwersyteckie Centrum Kliniczne', country: 'Poland' })), 'institution-name');
  check('nor an inflected hospital word ("Szpitale Pomorskie Sp. z o.o.")',
    C.rejection(row({ canonical_name: 'Szpitale Pomorskie Sp. z o.o.', country: 'Poland' })), 'institution-name');
  check('nor a German university clinic ("Universitätsklinikum Heidelberg")',
    C.rejection(row({ canonical_name: 'Universitätsklinikum Heidelberg', country: 'Germany' })), 'institution-name');
  check('a private network named "Centra Kliniczne" is still a candidate (KO-MED)',
    C.rejection(row({ canonical_name: 'KO-MED Centra Kliniczne Lublin II', country: 'Poland' })), null);
  check('a research centre branded "Hospitalize" is not taken for a hospital',
    C.rejection(row({ canonical_name: 'Centro de Pesquisa Clinica e Populacional Hospitalize', country: 'Brazil' })), null);
  check('"Centrum Badan Klinicznych" still is one', C.rejection(row({ canonical_name: 'Centrum Badań Klinicznych', country: 'Poland' })), null);
  check('a business called "Universal ..." is not mistaken for a university',
    C.rejection(row({ canonical_name: 'Universal Clinical Research LLC' })), null);
  check('a University Hospital type is not, whatever the name', C.rejection(row({ facility_type: 'University Hospital' })), 'institution-type');
  check('a Government network is not', C.rejection(row({ network_type: 'Government' })), 'institution-network');
  check('tier C is not', C.rejection(row({ site_tier: 'C' })), 'tier');
  check('an area-only marker is not', C.rejection(row({ site_identification: 'area_only' })), 'not-identified');
  check('no trial in 3 years and none recruiting is not active', C.rejection(row({ recent_trials_3yr: 0, active_recruiting: 0 })), 'inactive');
  check('a country outside the clock table is not', C.rejection(row({ country: 'Russia' })), 'country-not-included');
  check('a name with neither a research word nor a legal form is not (a lookup costs money)', C.rejection(row({ canonical_name: 'Brookside Medical' })), 'no-site-signal');
  check('"trust" in a name is an institution only as a whole word', C.rejection(row({ canonical_name: 'Trustworthy Research LLC' })), null);

  const sel = C.select([
    { results: [row({ location_id: 1 }), row({ location_id: 2, canonical_name: 'Hospital X' }),
                row({ location_id: 3, canonical_name: 'PROACTIVE CR MEXICO S.A. DE C.V.' }),
                row({ location_id: 4, country: 'United States', canonical_name: 'DM Clinical Research - Brookline', city: 'Brookline' }),
                row({ location_id: 5, site_tier: 'B', canonical_name: 'Arke SMO SA de CV', city: 'Veracruz' }),
                row({ location_id: 6, canonical_name: 'Known Research', city: 'Puebla' })] },
    { error: 'timeout' },
  ], [6], 3);
  check('selection: known ids skipped, same name+city deduplicated, hospital out, cap applied, core first',
    sel.candidates.map(c => c.location_id), [1, 5, 4]);
  check('selection stats add up', [sel.stats.rows_seen, sel.stats.failed_responses, sel.stats.rejected['institution-name'],
    sel.stats.rejected['already-a-candidate'], sel.stats.rejected['same-name-and-city'], sel.stats.eligible_new, sel.stats.chosen],
    [6, 1, 1, 1, 1, 3, 3]);
  check('a candidate carries no investigator field', Object.keys(sel.candidates[0]).filter(k => /investigator/.test(k)), []);
  const capped = C.select([{ results: [row({ location_id: 1 }), row({ location_id: 9, city: 'Leon' })] }], [], 1);
  check('over-the-cap is counted, not silently dropped', capped.stats.rejected['over-the-cap'], 1);

  // ------------------------------------------------------------------ verify: pure rules
  const V = lib('Verify Website', ['nameAppears', 'cityAppears', 'hostRejection', 'cost', 'readResponse', 'hostOf', 'words']);
  const page = (t) => V.words(t);
  check('"Proactive CR" on the page matches "Proactive Cr Mexico SA de Cv"', V.nameAppears('Proactive Cr Mexico SA de Cv', 'Irapuato', page('Welcome to Proactive CR, research in Irapuato')), true);
  check('a network brand matches before " - <location>"', V.nameAppears('DM Clinical Research - Brookline', 'Brookline', page('DM Clinical Research locations: Brookline, Houston')), true);
  check('geography alone never matches', V.nameAppears('Research Institute of South Florida', 'Miami', page('Clinics across South Florida, Miami')), false);
  check('a name made only of generic words must match as a whole phrase', V.nameAppears('Clinical Research Center', 'Pune', page('our clinical research team in Pune')), false);
  check('... and does when the phrase is there', V.nameAppears('Clinical Research Center', 'Pune', page('Clinical Research Center, Pune')), true);
  check('a city written in the local language counts (Warsaw / Warszawa)', V.cityAppears('Warsaw', page('ul. Prosta 1, Warszawa')), true);
  check('a city absent from the pages does not', V.cityAppears('Monterrey', page('Our office in Guadalajara')), false);
  check('a directory is not the organisation\'s own site', V.hostRejection('doctoralia.com.mx').outcome, 'no-match');
  check('LinkedIn is not the organisation\'s own site', V.hostRejection('mx.linkedin.com').outcome, 'no-match');
  check('a university domain is excluded', V.hostRejection('med.unam.edu').outcome, 'excluded');
  check('a Mexican government domain (.gob.mx) is excluded', V.hostRejection('imss.gob.mx').outcome, 'excluded');
  check('a sanctioned country-code domain is excluded (Section 12 screen)', V.hostRejection('clinic.ru').outcome, 'excluded');
  check('an ordinary company domain passes the pre-fetch screen', V.hostRejection('proactivecr.com'), null);
  check('host normalisation matches leads.domain (no scheme, no www, no path)', V.hostOf('https://WWW.ProactiveCR.com/en/contact?x=1'), 'proactivecr.com');
  check('cost: tokens at Section 4 prices plus $0.01 per search',
    V.cost({ input_tokens: 20000, output_tokens: 400, server_tool_use: { web_search_requests: 2 } }).cost_usd, 0.0222);
  check('an API error is a failed call, not a "no website"', V.readResponse({ type: 'error', error: { type: 'overloaded_error' } }).ok, false);
  check('a cut-off answer is a failed call', V.readResponse({ content: [], stop_reason: 'max_tokens' }).ok, false);
  check('a refusal is a failed call', V.readResponse({ content: [{ type: 'text', text: '{}' }], stop_reason: 'refusal' }).ok, false);

  // ------------------------------------------------------------------ verify: the node
  const cand = (id, name, city) => ({ json: { location_id: id, canonical_name: name, city: city, country: 'Mexico' } });
  const answer = (url, ev, hosts) => ({ json: {
    stop_reason: 'end_turn',
    usage: { input_tokens: 20000, output_tokens: 300, server_tool_use: { web_search_requests: 2 } },
    content: [{ type: 'web_search_tool_result', content: (hosts || []).map(h => ({ url: 'https://' + h + '/' })) },
              { type: 'text', text: JSON.stringify({ url: url, evidence_url: ev, confidence: 'high' }) }],
  } });
  const pages = {
    'https://proactivecr.com/': '<a href="/contacto">Contacto</a> Proactive CR',
    'https://proactivecr.com/contacto': 'Proactive CR -- Blvd. Diaz Ordaz, Irapuato, Gto.',
    'https://wrongco.com/': 'Some other clinic in Leon',
  };
  const helpers = { httpRequest: async function (req) {
    const body = pages[req.url];
    if (body === undefined) return { statusCode: 404, body: '' };
    return { statusCode: 200, body: body };
  } };
  const out = await run('Verify Website',
    [answer('https://proactivecr.com/', null, ['proactivecr.com']), answer('https://wrongco.com/', null, ['wrongco.com']),
     answer(null, null, []), { json: { type: 'error', error: { type: 'overloaded_error', message: 'busy' } } }],
    { 'Build Lookup Request': [cand(1, 'Proactive Cr Mexico SA de Cv', 'Irapuato'), cand(2, 'Iecsi S.C', 'Monterrey'),
                               cand(3, 'Zentrix Ortolan Clinical Research SC', 'Tepic'), cand(4, 'BKS Research Kft', 'Hatvan')] },
    helpers);
  check('outcomes in order: confirmed, wrong site, no URL, failed call',
    out.map(o => o.json.outcome), ['resolved', 'not-confirmed', 'no-match', 'call-failed']);
  check('a confirmed site carries the normalised domain and its homepage', [out[0].json.domain, out[0].json.website_url], ['proactivecr.com', 'https://proactivecr.com/']);
  check('the city was found on a linked contact page, not the homepage', /contacto ok/.test(out[0].json.detail), true);
  check('an unconfirmed proposal carries no domain (never a guess)', out[1].json.domain, undefined);
  check('every lookup is priced, whatever the outcome', out.map(o => o.json.cost_usd > 0), [true, true, true, false]);
  threw = null;
  try {
    await run('Verify Website', [answer(null, null, [])], { 'Build Lookup Request': [cand(1, 'A', 'B'), cand(2, 'C', 'D')] }, helpers);
  } catch (e) { threw = e.message; }
  check('a response count that differs from the lookups is refused, not paired by guesswork', /refusing to pair/.test(threw || ''), true);

  // ------------------------------------------------------------------ build lookup request
  const b = await run('Build Lookup Request', [cand(7, 'Lukmed 2 Sp. z o.o', 'Siedlce')]);
  const rq = b.json.request;
  check('the lookup request: Haiku 5.5, effort low, basic web search with max_uses, strict JSON schema',
    [rq.model, rq.output_config.effort, rq.tools[0].type, rq.tools[0].max_uses, rq.output_config.format.type],
    ['claude-haiku-5-5', 'low', 'web_search_20250305', 3, 'json_schema']);
  check('the user message carries the name and the place, and nothing about any person',
    /Lukmed 2 Sp\. z o\.o/.test(rq.messages[0].content) && /Siedlce/.test(rq.messages[0].content) && !/investigator/i.test(rq.messages[0].content), true);

  console.log('\n' + passed + ' passed, ' + failed + ' failed');
  if (failed) process.exit(1);
})().catch(function (e) { console.error(e); process.exit(1); });

// Verify Website -- n8n Code node (Run Once for All Items).
//
// Workflow 1b, website lookup. Claude (with web search) has proposed a website
// for one Trialsites candidate, or said it found none. This node decides,
// deterministically, whether that proposal is accepted -- the model proposes,
// code decides, which is the shape the claim check already has (Section 9,
// Workflow 5). The operator's rule: "No confident match means skip; never
// guess a domain."
//
//   1. The answer must be the schema's JSON and name a URL.
//   2. Whether the URL's host appeared in the web search results is RECORDED
//      (`from_search`), not enforced: the fetch in 4 is the confirmation, and a
//      site that names this organisation in this city is the right site however
//      the model learned its address (see hostRejection).
//   3. Not a directory, a social network or a registry, not an institutional
//      domain (.edu, .gov, .ac.xx, .nhs.uk, ...) -- the brief excludes
//      hospitals, universities and government bodies -- and not a sanctioned
//      jurisdiction's country-code domain (Section 12's row-level screen).
//   4. Fetched: the homepage and, if the model named one on the same host, the
//      page it says shows the address. The candidate's NAME and its CITY must
//      both appear in that text. Either missing is a skip, not a guess.
//
// Every call is priced here from its own `usage` block and recorded, whatever
// the outcome, because the monthly cap (settings.site_lookup_monthly_cap_usd)
// counts what was actually spent.

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// The lookup model's prices and the web search fee, substituted at build time
// from NovaScout_MasterRef.md Section 4 by n8n/sites/build_workflow.py.
const PRICES = __LOOKUP_PRICES__;

// Section 12's row-level screen: a site on an excluded jurisdiction's
// country-code domain is dropped (the scraper's EXCLUDED_TLDS, checked equal at
// build time).
const EXCLUDED_TLDS = __EXCLUDED_TLDS__;

// Not the organisation's own website, whatever the search ranked first.
const NOT_OWN_SITE = [
  'linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'youtube.com', 'tiktok.com',
  'wikipedia.org', 'google.com', 'bing.com', 'yelp.com', 'yellowpages.com', 'mapquest.com',
  'clinicaltrials.gov', 'aimronline.org', 'who.int', 'clinicaltrialsregister.eu', 'isrctn.com',
  'doctoralia.com', 'doctoralia.com.mx', 'doctoralia.com.br', 'znanylekarz.pl', 'healthgrades.com',
  'webmd.com', 'doximity.com', 'vitals.com', 'zocdoc.com', 'practo.com', 'justdial.com',
  'zoominfo.com', 'crunchbase.com', 'dnb.com', 'glassdoor.com', 'indeed.com', 'bloomberg.com',
  'opencorporates.com', 'emis.com', 'rocketreach.co', 'apollo.io', 'signalhire.com',
];

// Public institutions by domain: universities, governments, health services.
const INSTITUTIONAL = [
  /\.edu$/, /\.gov$/, /\.mil$/, /\.int$/, /\.(ac|edu|gov|gob|gouv|govt|mil|nic)\.[a-z]{2}$/,
  /\.nhs\.uk$/, /\.nhs\.net$/, /\.hscni\.net$/, /\.health\.nz$/,
];

// Words that say what a place is, not which one it is. A name made only of
// these ("Clinical Research Center") must match as a whole phrase.
const GENERIC = [
  'clinical', 'clinic', 'clinics', 'clinica', 'clinicas', 'klinik', 'research', 'center', 'centre',
  'centro', 'centrum', 'institute', 'instituto', 'institut', 'medical', 'medica', 'medico', 'medicine',
  'medicina', 'health', 'healthcare', 'group', 'grupo', 'site', 'sites', 'trial', 'trials', 'study',
  'studies', 'pesquisa', 'pesquisas', 'investigacion', 'investigaciones', 'investigacao', 'badan',
  'unit', 'services', 'associates', 'specialists', 'network', 'care', 'the', 'of', 'and', 'for', 'in',
  'de', 'del', 'la', 'el', 'los', 'las', 'y', 'e', 'do', 'da', 'dos', 'das', 'di', 'du', 'des', 'le',
  'et', 'und', 'fur', 'i', 'w', 'z', 'o', 'sp', 'zoo', 's', 'a', 'c', 'v', 'cv', 'sa', 'sc', 'srl', 'sro',
  'llc', 'inc', 'ltd', 'limited', 'gmbh', 'ag', 'kft', 'zrt', 'ltda', 'pvt', 'pty', 'bv', 'corp', 'co',
  'pllc', 'pc', 'pa', 'llp', 'sl', 'sas', 'sarl', 'sapi', 'rl', 'dr', 'dra', 'mr', 'ms', 'nr',
];

// Cities a site's own pages commonly write in the local language. A small,
// explicit table: a city not here must appear as the registry writes it.
const EXONYMS = {
  'mexico city': ['ciudad de mexico', 'cdmx', 'mexico d f', 'mexico df'],
  'warsaw': ['warszawa'], 'krakow': ['krakow', 'cracow'], 'prague': ['praha'], 'bucharest': ['bucuresti'],
  'munich': ['munchen'], 'cologne': ['koln'], 'vienna': ['wien'], 'rome': ['roma'], 'milan': ['milano'],
  'florence': ['firenze'], 'turin': ['torino'], 'naples': ['napoli'], 'lisbon': ['lisboa'],
  'athens': ['athina'], 'belgrade': ['beograd'], 'kyiv': ['kiev', 'kyiv'], 'lodz': ['lodz'],
  'brussels': ['bruxelles', 'brussel'], 'the hague': ['den haag'], 'gothenburg': ['goteborg'],
  'copenhagen': ['kobenhavn'], 'seville': ['sevilla'], 'cairo': ['al qahirah'], 'bangalore': ['bengaluru'],
  'bengaluru': ['bangalore'], 'bombay': ['mumbai'], 'mumbai': ['bombay'], 'chennai': ['madras'],
  'washington d c': ['washington dc', 'washington'], 'sao paulo': ['sao paulo'],
};

// Each fetch gives up after 10 s, and at most CONCURRENCY candidates are read
// at once (Section 3's parallelization principle: I/O concurrent, bounded).
// Worst case per candidate is five homepage rungs plus four further pages,
// ~90 s, so a batch of 10 finishes well inside the task runner's 300 s.
const FETCH_TIMEOUT_MS = 10000;
const CONCURRENCY = 4;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[‘’]/g, "'")
    .toLowerCase();
}

// Space-separated words, folded: the form every text comparison here uses.
function words(v) {
  return ' ' + fold(v).replace(/[^a-z0-9]+/g, ' ').trim() + ' ';
}

// leads.domain's own normalisation (the scraper's and Workflow 3b's): the host,
// lower-cased, with no scheme, no www. and no path.
function hostOf(url) {
  return str(url).toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/^www\./, '')
    .replace(/[/?#].*$/, '').replace(/:\d+$/, '').replace(/\.+$/, '');
}

function sameSite(a, b) {
  if (!a || !b) return false;
  return a === b || a.endsWith('.' + b) || b.endsWith('.' + a);
}

function cost(usage) {
  const u = usage || {};
  const inTok = Number(u.input_tokens) || 0;
  const outTok = Number(u.output_tokens) || 0;
  const cacheRead = Number(u.cache_read_input_tokens) || 0;
  const cacheWrite = Number(u.cache_creation_input_tokens) || 0;
  const searches = Number(u.server_tool_use && u.server_tool_use.web_search_requests) || 0;
  const usd = (inTok * PRICES.input_per_mtok + outTok * PRICES.output_per_mtok +
    cacheRead * PRICES.cache_read_per_mtok + cacheWrite * PRICES.cache_write_per_mtok) / 1e6 +
    searches * PRICES.per_search;
  return {
    input_tokens: inTok, output_tokens: outTok, cache_read_tokens: cacheRead,
    cache_write_tokens: cacheWrite, web_search_requests: searches,
    cost_usd: Math.round(usd * 1e6) / 1e6,
  };
}

// The model's answer and the hosts its searches returned.
function readResponse(resp) {
  if (!resp || typeof resp !== 'object' || resp.error || !Array.isArray(resp.content)) {
    const e = resp && (resp.error || resp.message);
    return { ok: false, detail: 'lookup call failed: ' + (e ? (typeof e === 'string' ? e : JSON.stringify(e)) : 'no content').slice(0, 300) };
  }
  if (resp.stop_reason && resp.stop_reason !== 'end_turn') {
    return { ok: false, detail: 'lookup call ended ' + resp.stop_reason };
  }
  const resultHosts = [];
  let answer = null;
  resp.content.forEach(function (b) {
    if (b && b.type === 'web_search_tool_result' && Array.isArray(b.content)) {
      b.content.forEach(function (r) { if (r && r.url) resultHosts.push(hostOf(r.url)); });
    }
    if (b && b.type === 'text' && str(b.text)) {
      try { answer = JSON.parse(b.text); } catch (e) { /* the last parseable text block wins */ }
    }
  });
  if (!answer || typeof answer !== 'object' || !('url' in answer)) {
    return { ok: false, detail: 'lookup answer was not the schema JSON' };
  }
  return { ok: true, answer: answer, result_hosts: resultHosts };
}

// Why a proposed host is not acceptable before anything is fetched, or null.
//
// Whether the host appeared in the search results is recorded, not enforced:
// the fetch below is the confirmation the operator asked for, and a site that
// names this organisation in this city is the right site however the model
// learned its address (often from a directory page in the results). What the
// rule "never guess a domain" forbids is accepting an address nobody checked,
// and nothing here is accepted unchecked.
function hostRejection(host) {
  if (!host || host.indexOf('.') === -1) return { outcome: 'no-match', detail: 'no usable URL' };
  if (NOT_OWN_SITE.some(function (d) { return host === d || host.endsWith('.' + d); })) {
    return { outcome: 'no-match', detail: host + ' is a directory or social site, not the organisation\'s own' };
  }
  if (INSTITUTIONAL.some(function (re) { return re.test(host); })) {
    return { outcome: 'excluded', detail: host + ' is an institutional domain (university, government or health service)' };
  }
  const tld = host.split('.').pop();
  if (EXCLUDED_TLDS.indexOf(tld) !== -1) {
    return { outcome: 'excluded', detail: host + ' is on an excluded jurisdiction\'s country-code domain (Section 12)' };
  }
  return null;
}

// Legal forms dropped from the END of a name before it is compared: a site
// rarely prints "Sp. z o.o." in its own heading.
const LEGAL_TAIL = /( (llc|inc|ltd|limited|gmbh|ag|sc|s c|sa|s a|cv|c v|de|sapi|rl|r l|ltda|srl|sro|sp|z|o|oo|zoo|kft|zrt|as|sti|pvt|pty|bv|oy|aps|sas|sarl|sl|slu|pc|pllc|llp|pa|corp|co|private|public))+ *$/;

function core(name) {
  let w = words(name).trim();
  for (let i = 0; i < 6; i++) {
    const next = (' ' + w).replace(LEGAL_TAIL, '').trim();
    if (next === w) break;
    w = next;
  }
  return w;
}

// The distinctive words of a phrase: not generic, not the city itself.
function distinctive(phrase, city) {
  const cityWords = words(city).trim().split(' ');
  return phrase.split(' ').filter(function (w) {
    return w.length >= 2 && GENERIC.indexOf(w) === -1 && cityWords.indexOf(w) === -1;
  });
}

// Does the site name this organisation? Three ways, strictest first:
//   1. the name (legal form dropped) as a phrase, or a leading part of it that
//      carries at least half of its distinctive words -- "Proactive CR" for
//      "Proactive Cr Mexico SA de Cv";
//   2. the same for the brand before " - " or ",": Trialsites appends the
//      location to a network's name ("DM Clinical Research - Brookline"), and
//      the city is checked separately;
//   3. a long name (three or more distinctive words) with all but one of them
//      present as words.
// A name made only of generic words must match as a whole phrase. Geography
// alone never matches: "south florida" on a page is not "Research Institute
// of South Florida" -- rule 1 needs the phrase.
function nameAppears(name, city, text) {
  const full = core(name);
  const brandRaw = str(name).split(/ [-\u2013] |,/)[0];
  const phrases = [full];
  if (brandRaw && brandRaw !== str(name)) phrases.push(core(brandRaw));
  for (const phrase of phrases) {
    if (!phrase) continue;
    const parts = phrase.split(' ');
    const dist = distinctive(phrase, city);
    if (!dist.length) {
      if (text.indexOf(' ' + phrase + ' ') !== -1) return true;
      continue;
    }
    for (let k = parts.length; k >= 1; k--) {
      const prefix = parts.slice(0, k).join(' ');
      const carried = distinctive(prefix, city);
      if (!carried.length || carried.length * 2 < dist.length) break;
      if (k === 1 && prefix.length < 5) break;
      if (text.indexOf(' ' + prefix + ' ') !== -1) return true;
    }
  }
  const dist = distinctive(full, city).filter(function (w, i, a) { return a.indexOf(w) === i; });
  if (dist.length >= 3) {
    const found = dist.filter(function (w) { return text.indexOf(' ' + w + ' ') !== -1; });
    if (found.length >= dist.length - 1) return true;
  }
  return false;
}

function cityAppears(city, text) {
  const c = words(city).trim();
  if (!c) return false;
  const forms = [c].concat(EXONYMS[c] || []);
  return forms.some(function (f) { return text.indexOf(' ' + f + ' ') !== -1; });
}

function htmlToText(html) {
  return str(html)
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&#0?39;|&apos;/gi, "'")
    .replace(/&quot;/gi, '"');
}

// ---------------------------------------------------------------------------
// The decision for one candidate. `helpers` is n8n's this.helpers, passed in
// because a nested function cannot reach `this`.
// ---------------------------------------------------------------------------

async function fetchText(helpers, url) {
  for (const insecure of [false, true]) {
    try {
      const res = await helpers.httpRequest({
        method: 'GET', url: url, timeout: FETCH_TIMEOUT_MS, returnFullResponse: true,
        ignoreHttpStatusErrors: true, skipSslCertificateValidation: insecure,
        headers: { 'User-Agent': 'NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)' },
      });
      if (res && res.statusCode >= 200 && res.statusCode < 400 && typeof res.body === 'string') {
        return { ok: true, html: res.body, text: htmlToText(res.body) };
      }
      return { ok: false, detail: 'HTTP ' + (res && res.statusCode) };
    } catch (e) {
      if (insecure) return { ok: false, detail: str(e && e.message).slice(0, 120) };
    }
  }
  return { ok: false, detail: 'unreachable' };
}

// Contact and location pages linked from the homepage -- the city is usually
// on one of those, not on the homepage itself.
const CONTACT_LINK = /(contact|contacto|contato|kontakt|kapcsolat|iletisim|location|locations|ubicacion|localizacao|sedes|unidades|about|nosotros|quienes|sobre|o-nas|impressum|find-us|visit)/i;

async function verify(helpers, cand, resp) {
  const spend = cost(resp && resp.usage);
  const base = {
    location_id: cand.location_id,
    canonical_name: cand.canonical_name,
    city: cand.city,
    country: cand.country,
    model: PRICES.model,
  };
  function out(outcome, detail, extra) {
    return Object.assign({}, base, spend, { outcome: outcome, detail: str(detail).slice(0, 500) }, extra || {});
  }

  const read = readResponse(resp);
  if (!read.ok) return out('call-failed', read.detail);

  const proposed = str(read.answer.url);
  const host = hostOf(proposed);
  const fromSearch = read.result_hosts.some(function (h) { return sameSite(host, h); });
  const early = hostRejection(host);
  if (early) return out(early.outcome, early.detail, { proposed_url: proposed || null, from_search: fromSearch });

  // The homepage, through the same rungs Workflow 2 uses (the URL as proposed,
  // then https and http, with and without www.), then the model's evidence page
  // and up to three contact or location pages on the same site.
  let text = ' ';
  const fetched = [];
  const tried = [];
  let homeHtml = null;
  for (const u of [proposed, 'https://' + host + '/', 'https://www.' + host + '/', 'http://' + host + '/', 'http://www.' + host + '/']) {
    if (!u || tried.indexOf(u) !== -1) continue;
    tried.push(u);
    const f = await fetchText(helpers, u);
    fetched.push(u + (f.ok ? ' ok' : ' ' + f.detail));
    if (f.ok) { homeHtml = f.html; text += words(f.text) + ' '; break; }
  }
  if (homeHtml === null) {
    return out('not-confirmed', 'could not read the site: ' + fetched.join('; '), { proposed_url: proposed, from_search: fromSearch });
  }
  const extra = [];
  const evidence = str(read.answer.evidence_url);
  if (evidence && sameSite(hostOf(evidence), host) && tried.indexOf(evidence) === -1) extra.push(evidence);
  const linkRe = /href\s*=\s*["']([^"'#]+)["']/gi;
  let m;
  while ((m = linkRe.exec(homeHtml)) && extra.length < 4) {
    let href = m[1].trim();
    if (/^(mailto|tel|javascript):/i.test(href)) continue;
    if (href.indexOf('//') === 0) href = 'https:' + href;
    else if (href.charAt(0) === '/') href = 'https://' + host + href;
    else if (!/^https?:/i.test(href)) continue;
    if (!sameSite(hostOf(href), host) || !CONTACT_LINK.test(href.replace(/^https?:\/\/[^/]+/i, ''))) continue;
    if (extra.indexOf(href) === -1 && tried.indexOf(href) === -1) extra.push(href);
  }
  for (const u of extra) {
    const f = await fetchText(helpers, u);
    fetched.push(u + (f.ok ? ' ok' : ' ' + f.detail));
    if (f.ok) text += words(f.text) + ' ';
  }
  const nameOk = nameAppears(cand.canonical_name, cand.city, text);
  const cityOk = cityAppears(cand.city, text);
  if (!nameOk || !cityOk) {
    return out('not-confirmed',
      (nameOk ? '' : 'name not on the site; ') + (cityOk ? '' : 'city "' + str(cand.city) + '" not on the site; ') +
      'read ' + fetched.join('; '), { proposed_url: proposed, from_search: fromSearch });
  }
  return out('resolved', 'name and city found on ' + fetched.join('; '), {
    proposed_url: proposed,
    from_search: fromSearch,
    domain: host,
    website_url: 'https://' + host + '/',
  });
}

// Run `work` over `list` with at most `n` in flight; results keep list order.
async function pool(list, n, work) {
  const results = new Array(list.length);
  let next = 0;
  async function lane() {
    while (next < list.length) {
      const k = next++;
      results[k] = await work(list[k], k);
    }
  }
  const lanes = [];
  for (let i = 0; i < Math.min(n, list.length); i++) lanes.push(lane());
  await Promise.all(lanes);
  return results;
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const nodeHelpers = this.helpers;
const responses = $input.all();
const requests = $('Build Lookup Request').all();

// The HTTP node emits exactly one item per request, in order (onError:
// continueRegularOutput turns a failed call into an item too). If the counts
// ever differ, pairing by position would attach one candidate's answer to
// another candidate's name -- so stop rather than guess.
if (responses.length !== requests.length) {
  throw new Error('Verify Website: ' + responses.length + ' responses for ' + requests.length + ' lookups; refusing to pair them');
}
const pairs = responses.map(function (r, k) {
  const p = r.pairedItem;
  const idx = p && typeof p === 'object' && Number.isInteger(p.item) ? p.item : (Number.isInteger(p) ? p : k);
  return { cand: requests[idx].json, resp: r.json };
});
const results = await pool(pairs, CONCURRENCY, function (pr) { return verify(nodeHelpers, pr.cand, pr.resp); });
return results.map(function (j, k) { return { json: j, pairedItem: { item: k } }; });

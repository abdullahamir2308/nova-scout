// Fetch Site Pages — n8n Code node (Run Once for All Items).
// Fetches homepage, then up to MAX_SUBPAGES internal about/services/team pages
// discovered from the homepage nav, falling back to the literal spec paths.
// Chatbot detection and LinkedIn harvesting are regex here, never an LLM call
// (Master Ref build rule 3).

const MAX_PAGE_CHARS = 6000;
const MAX_TOTAL_CHARS = 24000;
const MAX_SUBPAGES = 4;
const TIMEOUT_MS = 12000;
const RETRY_TIMEOUT_MS = 20000;
const UA = 'NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)';
const FALLBACK_PATHS = ['/about', '/services', '/team'];

// about / services / team in the ICP's languages (Section 12 geographies)
const LINK_KEYWORDS = [
  'about', 'about-us', 'aboutus', 'company', 'who-we-are', 'our-story',
  'sobre', 'quem-somos', 'quienes-somos', 'nosotros', 'empresa', 'institucional',
  'o-nas', 'onas', 'o-spolecnosti', 'hakkimizda', 'hakkinda', 'despre', 'rolunk',
  'service', 'services', 'servico', 'servicos', 'servicio', 'servicios',
  'sluzby', 'hizmet', 'hizmetler', 'uslugi', 'servicii', 'szolgaltatas',
  'solutions', 'expertise', 'what-we-do', 'capabilities',
  'team', 'equipe', 'equipo', 'tym', 'ekip', 'zespol', 'echipa', 'csapat',
  'leadership', 'management', 'our-team', 'people', 'staff',
];

// Each pattern names the chat embed itself, never the vendor's domain in
// general: a vendor's domain also serves its analytics, forms, cookie banners
// and help-centre assets, and a page can mention or link a vendor without
// running its widget. Measured 2026-10-24: the old HubSpot pattern matched
// js.hs-scripts.com (HubSpot's tracking loader, on every HubSpot customer's
// site) and "hs-banner" (its cookie banner) and wrongly disqualified 15 of 17
// leads. HubSpot's chat is decided by hubspotLoaderChat() below.
const CHATBOT_PATTERNS = [
  ['Intercom', /widget\.intercom\.io|js\.intercomcdn\.com|intercomSettings/i],
  ['Drift', /js\.driftt\.com|drift\.com\/include|driftt\.com/i],
  ['Tidio', /code\.tidio\.co|tidiochat/i],
  ['Tawk', /embed\.tawk\.to|Tawk_API/i],
  ['Crisp', /client\.crisp\.chat|CRISP_WEBSITE_ID/i],
  ['HubSpot Chat', /js(?:-[a-z]{2,3}\d)?\.usemessages\.com|conversations-embed\.js|hubspot-messages-iframe-container/i],
  ['LiveChat', /cdn\.livechatinc\.com|__lc\.license/i],
  ['Zendesk', /static\.zdassets\.com\/ekr\/snippet\.js|ze-snippet|zopim|zendesk\.com\/embeddable/i],
];

// HubSpot's loader, //js.hs-scripts.com/<portal>.js (or a regional host such as
// js-na2. / js-eu1.), injects js.usemessages.com/conversations-embed.js only
// when the portal has chat switched on; otherwise it loads analytics, the
// cookie banner and forms. So a loader is a question, not an answer: fetch it.
const HUBSPOT_LOADER_RE = /\/\/(js(?:-[a-z]{2,3}\d)?\.hs-scripts\.com)\/(\d+)\.js/gi;
const HUBSPOT_CHAT_IN_LOADER = /usemessages\.com\/conversations-embed/i;
// Asked for as a script: HubSpot answers the page fetch's HTML Accept header
// with 406 Not Acceptable (measured 2026-10-24, which is how the first n8n run
// read every loader as "no chat").
const LOADER_ACCEPT = 'application/javascript, */*;q=0.1';
const MAX_HUBSPOT_LOADERS = 2;

const LINKEDIN_RE = /https?:\/\/(?:[a-z]{2,3}\.)?linkedin\.com\/in\/[A-Za-z0-9\-_%]+/gi;

const SYSTEM_PROMPT = [
  'You extract structured facts about clinical research organisations from their own website text.',
  '',
  'Rules:',
  '- Use ONLY facts stated in the supplied website text. Never infer, guess, or use outside knowledge.',
  '- If a fact is not stated, return null (or an empty array). A null is correct; an invention is a bug.',
  '- therapeutic_areas: disease areas the company itself says it works in, as a CLOSED list. You may only output these exact values:',
  '    Oncology, Cardiovascular, Central Nervous System, Immunology, Infectious Disease, Endocrinology, Metabolic Disorders, Respiratory, Rare Diseases, Internal Medicine, Anesthesiology, Dermatology, Rheumatology, Ophthalmology, Gastroenterology, Nephrology, Hematology, Other',
  '  Read the site in whatever language it is written in and map to the closest value ("oncologia"/"onkologie" -> Oncology, "kardiologia" -> Cardiovascular, "clinica medica" -> Internal Medicine). Map narrower terms up: cardiology/heart failure -> Cardiovascular; neurology/psychiatry/Alzheimer\'s/epilepsy -> Central Nervous System; pulmonology/asthma/COPD -> Respiratory; autoimmune/allergy -> Immunology; vaccines/HIV/tuberculosis -> Infectious Disease; diabetes/obesity -> Metabolic Disorders; orphan/rare disease -> Rare Diseases; anaesthesiology/anaesthesia/perioperative/pain medicine -> Anesthesiology; skin/dermatology/cosmetic dermatology -> Dermatology; arthritis/lupus/rheumatology -> Rheumatology; eye/retina/vision/ophthalmology -> Ophthalmology; gastroenterology/GI/digestive/hepatology/liver/IBD -> Gastroenterology; nephrology/renal/kidney/dialysis -> Nephrology; haematology/blood disorders/anaemia/haemophilia -> Hematology. British and American spellings are the same area.',
  '  Blood, skin, eye, kidney, liver and joint CANCERS are Oncology, not the organ-specific area: "haematologic malignancies"/leukaemia/lymphoma/myeloma -> Oncology; melanoma -> Oncology; renal cell carcinoma -> Oncology.',
  '  Other is REQUIRED, not optional: if the site names a real disease area or medical specialty that none of the values above covers — urology, paediatrics, women\'s health, gynaecology, obstetrics, orthopaedics, dentistry, geriatrics, wound care — you MUST include Other rather than dropping it. Include Other at most once, however many such areas appear.',
  '  Industry and business descriptors are NOT therapeutic areas — omit them entirely, do not map them to Other: pharma, pharmaceuticals, biotech, medtech, medical devices, cosmetics, nutraceuticals, CRO, clinical research. (This rule is about therapeutic_areas only; it never suppresses a value in another field.)',
  '  Only areas the company itself works in — ignore areas mentioned only in a staff member\'s biography of a previous employer. Empty array if unstated.',
  '- phases: clinical trial phases named on the site. Use exactly "Phase I", "Phase II", "Phase III", "Phase IV", or "Bioequivalence". Empty array if unstated.',
  '- founder_name: the founder, owner, CEO, Managing Director or General Manager, if the site names one. Otherwise null.',
  '- founder_title: that person\'s title as written on the site. Otherwise null.',
  '- employee_estimate: only if the site states a headcount or team size. Never estimate from page length or tone. Otherwise null.',
  '- city: headquarters city if stated, as a plain city name only ("Cdad. Autonoma de Buenos Aires" -> "Buenos Aires", "Valinhos-SP" -> "Valinhos"). Otherwise null.',
  '- company_type: exactly one of "CRO", "site", "SMO", "other", "unclear".',
  '    CRO: a contract research organisation that runs or manages clinical trials for sponsors (monitoring, project or data management, regulatory, biostatistics, bioequivalence). A CRO that also runs its own clinic or phase I unit is still CRO.',
  '    site: an independent clinical research site or research centre where trials are carried out with patients and investigators, including a private clinic or practice that runs clinical trials. Not a hospital and not a university.',
  '    SMO: a site management organisation that runs or manages a network of research sites, or supplies sites with study coordinators and patient recruitment.',
  '    other: anything else -- a hospital, a university or medical school, a government body, a marketing agency, a consultancy, a software or technology vendor, a laboratory or product supplier, or a pharmaceutical or biotech company.',
  '    unclear: the text does not say what the company is.',
  '- site_quality_notes: one or two factual sentences on the site\'s depth, professionalism and apparent budget. No marketing adjectives.',
].join('\n');

const helpers = this.helpers;

function htmlToText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h[1-6]|tr|section|article|td)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#0?39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .split('\n')
    .map((l) => l.replace(/[ \t\u00a0]+/g, ' ').trim())
    .filter(Boolean)
    .join('\n');
}

async function get(url, opts) {
  const o = opts || {};
  return await helpers.httpRequest({
    method: 'GET',
    url,
    timeout: o.timeout || TIMEOUT_MS,
    headers: { 'User-Agent': UA, Accept: o.accept || 'text/html,application/xhtml+xml', 'Accept-Language': 'en,*;q=0.5' },
    json: false,
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
    skipSslCertificateValidation: o.insecure === true,
  });
}

// Reaching the homepage is worth more than one attempt. Measured over the full
// 107-lead table, 28 domains failed on a plain https://<domain> fetch, and 8 of
// those 28 were alive behind one of these rungs -- 2 needed the www host, 5 had
// a broken certificate chain (wrong altnames or an expired cert) while serving
// fine, and 1 only answers on http. That is ~11% more usable leads.
//
// TLS verification is relaxed only as a fallback rung, and only for reading
// public marketing pages: no credentials are ever sent to these hosts, so a
// bad certificate is a data-quality signal, not a confidentiality risk. The
// rung that succeeded is recorded so the compromise stays visible in the data.
function homepageLadder(domain) {
  const bare = domain.replace(/^www\./, '');
  const rungs = [
    { label: 'https', url: 'https://' + domain + '/', timeout: TIMEOUT_MS, insecure: false },
  ];
  if (domain === bare) {
    rungs.push({ label: 'https+www', url: 'https://www.' + bare + '/', timeout: RETRY_TIMEOUT_MS, insecure: false });
  }
  rungs.push({ label: 'https+insecure', url: 'https://' + domain + '/', timeout: RETRY_TIMEOUT_MS, insecure: true });
  rungs.push({ label: 'http', url: 'http://' + domain + '/', timeout: RETRY_TIMEOUT_MS, insecure: false });
  return rungs;
}

// The n8n Code sandbox does NOT expose the WHATWG `URL` constructor (verified
// on this instance: `URL is not defined`, alongside fetch/URLSearchParams/
// AbortController). Relative links therefore have to be resolved by hand --
// using `new URL()` here fails silently inside a try/catch and turns link
// discovery into a no-op, which is exactly the bug this replaced.
function parseAbsolute(u) {
  const m = String(u).match(/^(https?):\/\/([^/?#]+)([^?#]*)/i);
  if (!m) return null;
  const proto = m[1].toLowerCase();
  const hostPort = m[2].toLowerCase();
  return {
    protocol: proto,
    hostname: hostPort.replace(/:\d+$/, ''),
    origin: proto + '://' + hostPort,
    pathname: m[3] || '/',
  };
}

function resolveUrl(href, baseUrl) {
  let h = String(href).trim();
  if (!h || h.charAt(0) === '#') return null;
  if (/^(mailto:|tel:|javascript:|data:|sms:|whatsapp:)/i.test(h)) return null;
  h = h.split('#')[0].split('?')[0];
  if (!h) return null;
  if (/^https?:\/\//i.test(h)) return parseAbsolute(h);
  const b = parseAbsolute(baseUrl);
  if (!b) return null;
  if (h.slice(0, 2) === '//') return parseAbsolute(b.protocol + ':' + h);
  let path;
  if (h.charAt(0) === '/') {
    path = h;
  } else {
    path = b.pathname.replace(/[^/]*$/, '') + h;
  }
  const segs = [];
  for (const s of path.split('/')) {
    if (s === '' || s === '.') continue;
    if (s === '..') {
      segs.pop();
      continue;
    }
    segs.push(s);
  }
  return parseAbsolute(b.origin + '/' + segs.join('/'));
}

function discoverLinks(html, baseUrl, host) {
  const out = [];
  const seen = new Set();
  const re = /<a\b[^>]*?href\s*=\s*["']([^"']+)["']/gi;
  let m;
  let anchors = 0;
  while ((m = re.exec(html)) !== null) {
    anchors++;
    const abs = resolveUrl(m[1], baseUrl);
    if (!abs) continue;
    if (abs.hostname.replace(/^www\./, '') !== host) continue;
    const path = abs.pathname.replace(/\/+$/, '');
    if (!path) continue;
    const clean = abs.origin + path;
    if (seen.has(clean)) continue;
    const slug = path.toLowerCase();
    if (LINK_KEYWORDS.some((k) => slug.includes(k))) {
      seen.add(clean);
      out.push(clean);
    }
  }
  return { links: out, anchors };
}

// The text a pattern matched, with a little context, so the disqualifier's
// "detected by matching the vendor embed script" can be checked from the data.
function evidenceAround(re, text) {
  const m = re.exec(text);
  if (!m) return null;
  return text.slice(Math.max(0, m.index - 60), m.index + m[0].length + 60).replace(/\s+/g, ' ');
}

// Fetches each HubSpot portal loader on the page (at most MAX_HUBSPOT_LOADERS)
// and reports whether it injects the chat embed: true or false when it was read,
// false when HubSpot says the portal is gone (404/410), and null -- unknown --
// for any other answer or none. Unknown does not disqualify: a false positive
// deletes a real lead, a false negative only costs one email to a company with
// chat.
async function hubspotLoaderChat(html) {
  const seen = new Set();
  const out = [];
  let m;
  HUBSPOT_LOADER_RE.lastIndex = 0;
  while ((m = HUBSPOT_LOADER_RE.exec(html)) !== null && out.length < MAX_HUBSPOT_LOADERS) {
    const url = 'https://' + m[1].toLowerCase() + '/' + m[2] + '.js';
    if (seen.has(m[2])) continue;
    seen.add(m[2]);
    try {
      const r = await get(url, { accept: LOADER_ACCEPT });
      const body = String(r.body || '');
      const read = r.statusCode >= 200 && r.statusCode < 300;
      const gone = r.statusCode === 404 || r.statusCode === 410;
      out.push({ url, status: r.statusCode, chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : gone ? false : null });
    } catch (e) {
      out.push({ url, status: null, chat: null, error: (e.message || String(e)).slice(0, 120) });
    }
  }
  return out;
}

async function detectChat(blob) {
  const evidence = {};
  for (const [vendor, re] of CHATBOT_PATTERNS) {
    const hit = evidenceAround(re, blob);
    if (hit) evidence[vendor] = hit;
  }
  if (!evidence['HubSpot Chat']) {
    const loaders = await hubspotLoaderChat(blob);
    if (loaders.length) evidence.hubspot_loaders = loaders;
    const chatLoader = loaders.find((l) => l.chat === true);
    if (chatLoader) evidence['HubSpot Chat'] = 'portal loader ' + chatLoader.url + ' injects conversations-embed.js';
  }
  const vendors = CHATBOT_PATTERNS.map(([v]) => v).filter((v) => evidence[v]);
  return { vendors, evidence };
}

// Leads are fetched CONCURRENTLY. The serial constraint in Section 3 is about
// the GPU (OLLAMA_NUM_PARALLEL=1) and is enforced on the Ollama node instead;
// HTTP fetching is I/O-bound against unrelated hosts, so serialising it only
// burns wall-clock. It also burns it against a hard limit: with the retry
// ladder, a batch of ten dead domains fetched serially exceeded n8n's 300s
// task-runner timeout and failed the whole run. Five at a time keeps the worst
// case near one lead's ladder while staying polite (each lead is a different host).
const CONCURRENCY = 5;

async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const workers = [];
  for (let w = 0; w < Math.min(limit, items.length); w++) {
    workers.push(
      (async () => {
        for (;;) {
          const i = next++;
          if (i >= items.length) return;
          results[i] = await fn(items[i]);
        }
      })()
    );
  }
  await Promise.all(workers);
  return results;
}

async function processLead(item) {
  const lead = item.json;
  const domain = String(lead.domain).trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  const host = domain.replace(/^www\./, '');
  let base = 'https://' + domain;

  const rec = {
    lead_id: lead.id,
    domain,
    company_name: lead.company_name,
    country: lead.country,
    fetch_ok: false,
    fetch_errors: [],
    pages_fetched: 0,
    discovered: [],
    anchors_seen: 0,
    reached_via: null,
    has_chatbot: false,
    chatbot_vendor: null,
    linkedin_candidates: [],
  };

  let blob = '';
  const pages = [];

  let home = null;
  let insecureBase = false;
  for (const rung of homepageLadder(domain)) {
    try {
      const r = await get(rung.url, { timeout: rung.timeout, insecure: rung.insecure });
      if (r.statusCode >= 400) {
        rec.fetch_errors.push(rung.label + ' / -> HTTP ' + r.statusCode);
        continue;
      }
      home = r;
      rec.reached_via = rung.label;
      insecureBase = rung.insecure;
      base = rung.url.replace(/\/$/, '');
      break;
    } catch (e) {
      rec.fetch_errors.push(rung.label + ' / -> ' + (e.message || String(e)).slice(0, 120));
    }
  }

  if (home) {
    rec.fetch_ok = true;
    const homeHtml = String(home.body || '');
    blob += homeHtml;
    const finalUrl = (home.request && home.request.uri && home.request.uri.href) || base + '/';
    pages.push({ url: finalUrl, text: htmlToText(homeHtml) });

    const disc = discoverLinks(homeHtml, finalUrl, host);
    // Recorded so a future silent-discovery regression is visible in the data:
    // many anchors but zero discovered links means the resolver broke, not that
    // the site genuinely has no about/services/team page.
    rec.anchors_seen = disc.anchors;
    let targets = disc.links.slice(0, MAX_SUBPAGES);
    rec.discovered = targets.slice();
    // Union with the literal spec paths so a site whose nav hides /team is still covered.
    for (const p of FALLBACK_PATHS) {
      if (targets.length >= MAX_SUBPAGES) break;
      const u = base + p;
      if (!targets.includes(u)) targets.push(u);
    }

    for (const url of targets) {
      try {
        const rr = await get(url, { insecure: insecureBase });
        if (rr.statusCode >= 400) {
          rec.fetch_errors.push(url.replace(base, '') + ' -> HTTP ' + rr.statusCode);
          continue;
        }
        const h = String(rr.body || '');
        blob += h;
        pages.push({ url, text: htmlToText(h) });
      } catch (e) {
        rec.fetch_errors.push(url.replace(base, '') + ' -> ' + (e.message || String(e)).slice(0, 120));
      }
    }
  }

  rec.pages_fetched = pages.length;

  const chat = await detectChat(blob);
  const vendors = chat.vendors;
  rec.chat_evidence = chat.evidence;
  rec.has_chatbot = vendors.length > 0;
  rec.chatbot_vendor = vendors.length ? vendors.join(', ') : null;
  rec.linkedin_candidates = Array.from(new Set((blob.match(LINKEDIN_RE) || []).map((u) => u))).slice(0, 10);

  // Build the extraction prompt with a hard character budget (gotcha 2: keep
  // well inside the 16K num_ctx cap rather than relying on the 262K native window).
  const parts = [
    'COMPANY: ' + (rec.company_name || '(unknown)'),
    'DOMAIN: ' + rec.domain,
    'COUNTRY: ' + (rec.country || '(unknown)'),
    '',
    '--- WEBSITE TEXT ---',
  ];
  let total = 0;
  for (const p of pages) {
    let chunk = p.text.slice(0, MAX_PAGE_CHARS);
    if (total + chunk.length > MAX_TOTAL_CHARS) chunk = chunk.slice(0, MAX_TOTAL_CHARS - total);
    if (!chunk) break;
    total += chunk.length;
    parts.push('\n[' + p.url + ']\n' + chunk);
  }
  rec.prompt_chars = total;
  rec.prompt = parts.join('\n');
  rec.system_prompt = SYSTEM_PROMPT;

  // A homepage that loaded but yielded almost no text is a JS-rendered shell.
  // Flag it rather than asking the model to extract from nothing.
  rec.text_too_thin = rec.fetch_ok && total < 200;

  return rec;
}

const out = (await mapLimit($input.all(), CONCURRENCY, processLead)).map((rec) => ({ json: rec }));

return out;

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

const CHATBOT_PATTERNS = [
  ['Intercom', /widget\.intercom\.io|intercomcdn\.com|intercomSettings/i],
  ['Drift', /js\.driftt\.com|drift\.com\/include|driftt\.com/i],
  ['Tidio', /code\.tidio\.co|tidiochat/i],
  ['Tawk', /embed\.tawk\.to|tawk\.to/i],
  ['Crisp', /client\.crisp\.chat|crisp\.chat/i],
  ['HubSpot Chat', /js\.hs-scripts\.com|js\.usemessages\.com|hs-banner/i],
  ['LiveChat', /cdn\.livechatinc\.com|livechatinc\.com/i],
  ['Zendesk', /static\.zdassets\.com|zopim|zendesk\.com\/embeddable/i],
];

const LINKEDIN_RE = /https?:\/\/(?:[a-z]{2,3}\.)?linkedin\.com\/in\/[A-Za-z0-9\-_%]+/gi;

const SYSTEM_PROMPT = [
  'You extract structured facts about clinical research organisations from their own website text.',
  '',
  'Rules:',
  '- Use ONLY facts stated in the supplied website text. Never infer, guess, or use outside knowledge.',
  '- If a fact is not stated, return null (or an empty array). A null is correct; an invention is a bug.',
  '- therapeutic_areas: disease or specialty areas the company says it works in. ALWAYS translate to lowercase English, whatever language the site is in ("oncologia"/"onkologie" -> "oncology", "reumatologia" -> "rheumatology", "clinica medica" -> "internal medicine"). Only areas the company itself works in — ignore areas mentioned only in a staff member\'s biography of a previous employer. Empty array if unstated.',
  '- phases: clinical trial phases named on the site. Use exactly "Phase I", "Phase II", "Phase III", "Phase IV", or "Bioequivalence". Empty array if unstated.',
  '- founder_name: the founder, owner, CEO, Managing Director or General Manager, if the site names one. Otherwise null.',
  '- founder_title: that person\'s title as written on the site. Otherwise null.',
  '- employee_estimate: only if the site states a headcount or team size. Never estimate from page length or tone. Otherwise null.',
  '- city: headquarters city if stated, as a plain city name only ("Cdad. Autonoma de Buenos Aires" -> "Buenos Aires", "Valinhos-SP" -> "Valinhos"). Otherwise null.',
  '- is_cro: true only if this is a contract research organisation or clinical research services provider. False for marketing agencies, consultancies, software vendors, hospitals, or companies selling lab products.',
  '- company_type: short label, e.g. "CRO", "site management organisation", "consultancy", "lab services", "unclear".',
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
    headers: { 'User-Agent': UA, Accept: 'text/html,application/xhtml+xml', 'Accept-Language': 'en,*;q=0.5' },
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

  const vendors = CHATBOT_PATTERNS.filter(([, re2]) => re2.test(blob)).map(([v]) => v);
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

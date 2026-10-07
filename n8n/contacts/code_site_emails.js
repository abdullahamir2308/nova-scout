// Harvest Site Emails -- n8n Code node (Run Once for All Items).
//
// The third free contact source, added 2026-10-07: an e-mail address the
// company has published on its own website.
//
// Why this exists. Of the nine qualifying leads stuck with no channel on
// 2026-10-07, five publish an address on their own site -- ivrs.org.in
// (info@), rotrial.com (contact_us@), ee-cro.com (office@ plus two named
// mailboxes), delphiniumcro.com (info@, client@) and hvivo.com (bd@). Nothing
// had to be bought and nothing had to be guessed; the pages were simply never
// read for an address. Same shape as the finding that started this stage --
// every ichgcp profile carried an e-mail and ingestion dropped it.
//
// Why the fetch happens HERE and not in enrichment. Workflow 2 already fetches
// these very pages, but it keeps none of their text: the LinkedIn profiles are
// harvested from the blob by regex and the rest goes to the model and is
// discarded. Adding the harvest there would only help leads enriched from then
// on -- the 274 already in the table would each need a re-enrichment (a GPU
// call) and a re-score (a rationale call and a ClinicalTrials.gov lookup) to
// get at something that costs neither. So the stage that needs an address goes
// and looks for one, which also means it reads the site as it is today rather
// than as it was when the lead was enriched.
//
// WHAT IS AND IS NOT TAKEN -- the rule the whole node turns on:
//
//   * only an address that is literally on the page, as a mailto: link or as
//     text. Nothing is pattern-derived: no first.last@, no info@<domain> on the
//     grounds that most companies have one. A guessed address is a different
//     kind of thing from a published one, and `contacts.verified` draws exactly
//     that line (Section 9 / README).
//   * only the lead's own domain, or a subdomain of it. This is not fussiness:
//     mtz-clinical.pl publishes only @pratia.com addresses (it was acquired),
//     cebisinternational.com only @cebis-int.com, and archerresearch.eu only
//     Wix's @sentry.wixpress.com telemetry. All three are the wrong company or
//     no company, and all three are rejected by this one rule.
//   * never a mailbox that is not for business contact -- careers@, ir@,
//     privacy@, noreply@ and the rest of NEVER below. hvivo.com publishes
//     bd@, careers@ and ir@; only one of those is a person to write to.
//
// An address found here is written `verified = true`: it is a first-party
// address the company published about itself, which is the same ground the
// ichgcp profile address stands on (README, "What `verified` means").
//
// Master Ref build rule 3: all of this is string matching. No model call.

const helpers = this.helpers;

const cfg = $('Config').first().json;
const index = $('Index Scraped Emails').first().json;
const byDomain = (index && index.by_domain) || {};

// One homepage plus at most this many contact-ish subpages, per lead. The
// batch is bounded (Config.batch_size, 10), so a run is at most ~50 requests
// spread over as many different hosts -- each one the lead's own site.
const MAX_PAGES = Number(cfg.site_max_pages) || 5;
const TIMEOUT_MS = 12000;
const RETRY_TIMEOUT_MS = 20000;
const CONCURRENCY = 3;
const UA = 'NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)';

// Where a published address actually lives. Contact and imprint pages first --
// Germany, Austria and Switzerland put a legally required address on
// /impressum, which is the single most reliable page on a European CRO's site.
const PAGE_KEYWORDS = [
  'contact', 'contacts', 'contact-us', 'contactus', 'get-in-touch', 'reach-us', 'enquiry', 'enquiries',
  'impressum', 'imprint', 'legal-notice', 'kontakt', 'kontakty', 'contatti', 'contacto', 'contato',
  'iletisim', 'kapcsolat', 'contactati', 'contacte', 'nous-contacter', 'kontaktai', 'kontakta-oss',
  'about', 'about-us', 'aboutus', 'team', 'our-team', 'people', 'leadership', 'management',
];
const FALLBACK_PATHS = ['/contact', '/contact-us', '/kontakt', '/impressum', '/about'];

// Local parts that are never a business-contact mailbox. A cold email to
// careers@ or privacy@ is worse than no email at all -- it reaches the wrong
// desk and reads as a mass send, which is what Section 5's whole warm-up is
// built to avoid. noreply/postmaster-class addresses are not even read.
const NEVER = [
  'careers', 'career', 'jobs', 'job', 'recruitment', 'recruiting', 'recruit', 'hr', 'cv', 'cvs',
  'apply', 'applications', 'internship', 'volunteer',
  'ir', 'investor', 'investors', 'investorrelations', 'shareholders',
  'press', 'media', 'pr', 'newsroom', 'communications',
  'privacy', 'dpo', 'gdpr', 'kvkk', 'legal', 'compliance', 'ethics', 'whistleblower',
  'abuse', 'postmaster', 'hostmaster', 'webmaster', 'noreply', 'donotreply',
  'bounce', 'bounces', 'unsubscribe', 'notifications', 'notification',
  'billing', 'invoice', 'invoices', 'accounts', 'accounting', 'finance', 'payment', 'payments',
  'newsletter', 'subscribe', 'marketing',
  'test', 'example', 'sample', 'demo', 'sentry', 'wordpress', 'root',
];

// Preference, best first. Not a filter -- anything not in NEVER is usable; this
// only decides which of several published addresses to write. Section 12 names
// the buyer ("founder, Managing Director, or BD Director"), so a business
// development mailbox outranks a general one, and a general one outranks an
// operational inbox like support@ or client@ answered by a different team.
const PREFER = [
  ['bd', 'businessdevelopment', 'business', 'bizdev', 'sales', 'commercial',
    'partnership', 'partnerships', 'partner'],
  ['info', 'contact', 'contactus', 'hello', 'enquiry', 'enquiries', 'inquiry', 'inquiries',
    'office', 'mail', 'email', 'general', 'reception', 'welcome', 'connect', 'kontakt', 'iletisim'],
];

// A role inbox is a company address, not a person's. Flagged, never rejected --
// the same list and the same reason as code_resolve.js: the distinction has to
// survive into the report and the review queue, because the founder's name must
// never be presented as the owner of one (build rule 6).
const ROLE_LOCALPARTS = [
  'info', 'contact', 'hello', 'connect', 'office', 'admin', 'enquiry',
  'enquiries', 'inquiry', 'inquiries', 'mail', 'sales', 'support', 'general',
];

// Deliberately narrow: no spaces, no angle brackets, a dotted host, a 2+ letter
// TLD. An obfuscated address ("info [at] example.com") is NOT matched, because
// un-obfuscating one is a guess about what the page meant.
const EMAIL_RE = /[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+/g;
const MAILTO_RE = /mailto:([^"'>\s?&]+)/gi;

// `name@2x.png` and friends are how a sprite file looks to an e-mail regex.
const ASSET_TLDS = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'bmp', 'css', 'js',
  'json', 'woff', 'woff2', 'ttf', 'eot', 'pdf', 'webmanifest', 'map'];

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function normDomain(d) {
  return str(d).toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '')
    .replace(/[/].*$/, '').replace(/[:.]+$/, '');
}

// ---------------------------------------------------------------------------
// HTTP -- the n8n Code sandbox has no fetch, so this is the node's own helper,
// the same shape Workflow 2's code_fetch.js uses, TLS fallback rung included.
// Reading public marketing pages sends no credentials, so a broken certificate
// is a data-quality signal, not a confidentiality risk.
// ---------------------------------------------------------------------------

async function get(url, opts) {
  const o = opts || {};
  return await helpers.httpRequest({
    method: 'GET',
    url: url,
    timeout: o.timeout || TIMEOUT_MS,
    headers: { 'User-Agent': UA, Accept: 'text/html,application/xhtml+xml', 'Accept-Language': 'en,*;q=0.5' },
    json: false,
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
    skipSslCertificateValidation: o.insecure === true,
  });
}

function homepageLadder(domain) {
  const bare = domain.replace(/^www\./, '');
  const rungs = [{ label: 'https', url: 'https://' + domain + '/', timeout: TIMEOUT_MS, insecure: false }];
  if (domain === bare) {
    rungs.push({ label: 'https+www', url: 'https://www.' + bare + '/', timeout: RETRY_TIMEOUT_MS, insecure: false });
  }
  rungs.push({ label: 'https+insecure', url: 'https://' + domain + '/', timeout: RETRY_TIMEOUT_MS, insecure: true });
  rungs.push({ label: 'http', url: 'http://' + domain + '/', timeout: RETRY_TIMEOUT_MS, insecure: false });
  return rungs;
}

// The WHATWG URL constructor is not in the sandbox (verified during Workflow 2:
// using it fails inside a try/catch and turns link discovery into a silent
// no-op). Absolute and root-relative hrefs only -- enough for a nav link.
function sameHostLink(href, origin, host) {
  let h = str(href);
  if (!h || h.charAt(0) === '#') return null;
  if (/^(mailto:|tel:|javascript:|data:|sms:|whatsapp:)/i.test(h)) return null;
  h = h.split('#')[0].split('?')[0];
  if (!h) return null;
  if (/^https?:\/\//i.test(h)) {
    const m = h.match(/^(https?):\/\/([^/?#]+)([^?#]*)/i);
    if (!m) return null;
    if (m[2].toLowerCase().replace(/^www\./, '').replace(/:\d+$/, '') !== host) return null;
    return m[1].toLowerCase() + '://' + m[2].toLowerCase() + (m[3] || '/');
  }
  if (h.slice(0, 2) === '//') return null;
  if (h.charAt(0) !== '/') return null;
  return origin + h;
}

function discover(html, origin, host) {
  const out = [];
  const seen = {};
  const re = /<a\b[^>]*?href\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const abs = sameHostLink(m[1], origin, host);
    if (!abs) continue;
    const clean = abs.replace(/\/+$/, '');
    if (!clean || seen[clean]) continue;
    const slug = clean.toLowerCase();
    for (let i = 0; i < PAGE_KEYWORDS.length; i++) {
      if (slug.indexOf(PAGE_KEYWORDS[i]) !== -1) {
        seen[clean] = true;
        // The keyword's own rank orders the pages: a /contact page before an
        // /about page before a /team page.
        out.push({ url: clean, rank: i });
        break;
      }
    }
  }
  out.sort(function (a, b) { return a.rank - b.rank; });
  return out.map(function (x) { return x.url; });
}

// ---------------------------------------------------------------------------
// Harvesting
// ---------------------------------------------------------------------------

function onOwnDomain(address, domain) {
  const at = address.lastIndexOf('@');
  if (at <= 0 || !domain) return false;
  const host = address.slice(at + 1).toLowerCase().replace(/\.+$/, '');
  if (host === domain) return true;
  return host.length > domain.length + 1 &&
    host.slice(host.length - domain.length - 1) === '.' + domain;
}

function looksLikeAsset(address) {
  const tld = address.slice(address.lastIndexOf('.') + 1).toLowerCase();
  return ASSET_TLDS.indexOf(tld) !== -1;
}

function localPart(address) {
  return address.slice(0, address.lastIndexOf('@')).toLowerCase();
}

function isNever(address) {
  const lp = localPart(address);
  // Whole-localpart match, plus the common "careers.uk@" / "no-reply-2@"
  // shapes. Never a substring test: under one, "contact@" would match "act".
  if (NEVER.indexOf(lp) !== -1) return true;
  if (/^no[._-]?reply/.test(lp) || /^do[._-]?not[._-]?reply/.test(lp)) return true;
  const parts = lp.split(/[._+-]/).filter(Boolean);
  return parts.length > 1 && NEVER.indexOf(parts[0]) !== -1;
}

function preferenceTier(address) {
  const lp = localPart(address);
  for (let i = 0; i < PREFER.length; i++) {
    if (PREFER[i].indexOf(lp) !== -1) return i;
  }
  return PREFER.length;
}

// Two independent sources agreeing on the person beats any title string --
// the rule code_pick_contact.js already applies to an Apollo candidate. A
// mailbox whose local part carries the founder's own name
// (ilse.eder@ee-cro.com for "Ilse Eder") is that agreement, and it is the only
// case where a name is written as the owner of an address.
function matchesFounder(address, founderName) {
  const name = str(founderName).toLowerCase();
  if (!name) return false;
  const words = name.replace(/[^a-z\s'-]/g, ' ').split(/\s+/)
    .filter(function (w) { return w.length >= 3; });
  if (words.length < 2) return false;
  const lp = localPart(address).replace(/[^a-z]/g, '');
  const first = words[0].replace(/[^a-z]/g, '');
  const last = words[words.length - 1].replace(/[^a-z]/g, '');
  if (!first || !last || first === last) return false;
  return lp.indexOf(first) !== -1 && lp.indexOf(last) !== -1;
}

// Every address literally present in `html`, on this lead's own domain.
// `found` is carried across pages, and a mailto: beats a later text sighting of
// the same address because it is unambiguously an address the page offers.
function harvest(html, domain, found) {
  let m;
  MAILTO_RE.lastIndex = 0;
  while ((m = MAILTO_RE.exec(html)) !== null) {
    let a;
    try {
      a = decodeURIComponent(m[1]).trim().toLowerCase();
    } catch (e) {
      a = m[1].trim().toLowerCase();
    }
    EMAIL_RE.lastIndex = 0;
    const whole = EMAIL_RE.exec(a);
    EMAIL_RE.lastIndex = 0;
    if (!whole || whole[0] !== a) continue;
    if (onOwnDomain(a, domain) && !looksLikeAsset(a)) found[a] = 'mailto';
  }
  EMAIL_RE.lastIndex = 0;
  while ((m = EMAIL_RE.exec(html)) !== null) {
    const a = m[0].toLowerCase().replace(/^\.+/, '').replace(/\.+$/, '');
    if (!onOwnDomain(a, domain) || looksLikeAsset(a)) continue;
    if (!found[a]) found[a] = 'text';
  }
  return found;
}

function pick(addresses, founderName) {
  const usable = addresses.filter(function (a) { return !isNever(a); });
  if (!usable.length) return null;
  usable.sort(function (a, b) {
    const fa = matchesFounder(a, founderName) ? 0 : 1;
    const fb = matchesFounder(b, founderName) ? 0 : 1;
    if (fa !== fb) return fa - fb;
    const ta = preferenceTier(a);
    const tb = preferenceTier(b);
    if (ta !== tb) return ta - tb;
    // Shortest, then alphabetical: deterministic, so two runs of the same
    // batch write the same address.
    if (a.length !== b.length) return a.length - b.length;
    return a < b ? -1 : 1;
  });
  return usable[0];
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

async function processLead(item) {
  const lead = item.json;
  const out = Object.assign({}, lead, {
    site_email: null,
    site_email_how: null,
    site_email_role_inbox: false,
    site_email_is_founder: false,
    site_email_candidates: [],
    site_email_rejected: [],
    site_pages_fetched: 0,
    site_fetch_errors: [],
    site_lookup: 'skipped',
  });

  const domain = normDomain(lead.domain);
  if (!domain) return out;

  // A lead whose address was already in the committed CSV needs nothing from
  // its website: Resolve Contact prefers the scraped address anyway, so a fetch
  // here would be a request to a prospect's server for data we already hold.
  if (str(byDomain[domain])) {
    out.site_lookup = 'not-needed';
    return out;
  }

  out.site_lookup = 'no-address-found';

  let home = null;
  let origin = null;
  let insecure = false;
  const rungs = homepageLadder(domain);
  for (let i = 0; i < rungs.length; i++) {
    const rung = rungs[i];
    try {
      const r = await get(rung.url, { timeout: rung.timeout, insecure: rung.insecure });
      if (r.statusCode >= 400) {
        out.site_fetch_errors.push(rung.label + ' / -> HTTP ' + r.statusCode);
        continue;
      }
      home = r;
      origin = rung.url.replace(/\/$/, '');
      insecure = rung.insecure;
      break;
    } catch (e) {
      out.site_fetch_errors.push(rung.label + ' / -> ' + str(e.message || e).slice(0, 120));
    }
  }

  if (!home) {
    out.site_lookup = 'unreachable';
    return out;
  }

  const found = {};
  const homeHtml = str(home.body);
  out.site_pages_fetched = 1;
  harvest(homeHtml, domain, found);

  const host = domain.replace(/^www\./, '');
  // Links the site itself offers first; then the literal paths, so a site whose
  // nav hides its contact page is still covered. `guessed` marks the second
  // kind: a 404 on a path nobody linked is not a fault worth reporting, and
  // recording it would bury the real failures under four misses per lead.
  const targets = discover(homeHtml, origin, host).slice(0, MAX_PAGES - 1)
    .map(function (u) { return { url: u, guessed: false }; });
  for (let i = 0; i < FALLBACK_PATHS.length && targets.length < MAX_PAGES - 1; i++) {
    const u = origin + FALLBACK_PATHS[i];
    let have = false;
    for (let k = 0; k < targets.length; k++) {
      if (targets[k].url === u) have = true;
    }
    if (!have) targets.push({ url: u, guessed: true });
  }

  for (let i = 0; i < targets.length; i++) {
    const target = targets[i];
    try {
      const r = await get(target.url, { insecure: insecure });
      if (r.statusCode >= 400) {
        if (!(target.guessed && r.statusCode === 404)) {
          out.site_fetch_errors.push(target.url.replace(origin, '') + ' -> HTTP ' + r.statusCode);
        }
        continue;
      }
      out.site_pages_fetched++;
      harvest(str(r.body), domain, found);
    } catch (e) {
      out.site_fetch_errors.push(target.url.replace(origin, '') + ' -> ' + str(e.message || e).slice(0, 120));
    }
  }

  const all = Object.keys(found).sort();
  out.site_email_candidates = all;
  out.site_email_rejected = all.filter(function (a) { return isNever(a); });

  const chosen = pick(all, lead.founder_name);
  if (!chosen) return out;

  out.site_email = chosen;
  out.site_email_how = found[chosen];
  out.site_email_role_inbox = ROLE_LOCALPARTS.indexOf(localPart(chosen)) !== -1;
  out.site_email_is_founder = matchesFounder(chosen, lead.founder_name);
  out.site_lookup = 'found';
  return out;
}

const items = $input.all();
const results = new Array(items.length);
let next = 0;
const workers = [];
for (let w = 0; w < Math.min(CONCURRENCY, items.length); w++) {
  workers.push((async function () {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await processLead(items[i]);
    }
  })());
}
await Promise.all(workers);

return results.map(function (r) { return { json: r }; });

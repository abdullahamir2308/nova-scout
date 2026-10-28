// Confirm each candidate's website (migration 025).
//
//   node verify.js <trialsites.json> <candidates.json> <out.json> [concurrency]
//
// The decision is the SHIPPED Verify Website code of Workflow 1b, read out of
// trialsites.json: its verify() fetches the homepage and up to three contact or
// location pages on the same host and accepts the site only when the
// candidate's NAME and CITY both appear there. Here the URL comes from an
// Overture place instead of a model -- "the source proposes, code decides".
//
// What this file adds:
//   * robots.txt, first. Every request goes through allowed(): the site's
//     robots.txt (the group for NovaScoutBot, else '*') is read once per host,
//     and a path it disallows is never fetched. A site that disallows its
//     homepage is skipped outright (robots_allowed = false) and never becomes a
//     lead, so no later stage reads it either.
//   * what the pages read show: a contact form (an inquiry path that is a form),
//     and a mailto: on the site's own domain. Recorded as facts for Scoring;
//     never an address -- Contact Lookup reads the site's addresses later.
//
// Polite by construction: one request at a time per host, CONCURRENCY hosts at
// once, a 10-second timeout, a self-identifying User-Agent.

const fs = require('fs');
const http = require('http');
const https = require('https');
const [wfPath, candPath, outPath, conc] = process.argv.slice(2);
const CONCURRENCY = Number(conc) || 8;
const UA = 'NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)';
let V = null;
function loadVerifier() {
  const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
  const vsrc = wf.nodes.find((x) => x.name === 'Verify Website').parameters.jsCode;
  V = new Function(vsrc.slice(0, vsrc.indexOf('// Node body')) + '\nreturn { verify, hostOf, sameSite };')();
}

function rawGet(url, insecure, redirects) {
  return new Promise((resolve, reject) => {
    let u;
    try { u = new URL(url); } catch (e) { reject(new Error('bad url ' + url)); return; }
    const lib = u.protocol === 'http:' ? http : https;
    const req = lib.get(u, { headers: { 'User-Agent': UA, Accept: 'text/html,application/xhtml+xml,*/*;q=0.8' },
      rejectUnauthorized: !insecure, timeout: 10000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && redirects < 5) {
        res.resume();
        let next;
        // A malformed Location ("https:///", seen on noori.org) is a dead end, never a crash.
        try { next = new URL(res.headers.location, u).toString(); } catch (e) { reject(new Error('bad redirect ' + res.headers.location)); return; }
        resolve(rawGet(next, insecure, redirects + 1));
        return;
      }
      const chunks = []; let size = 0;
      res.on('data', (c) => { size += c.length; if (size <= 3e6) chunks.push(c); });
      res.on('end', () => resolve({ statusCode: res.statusCode, body: Buffer.concat(chunks).toString('utf8'), url: url }));
      res.on('error', reject);
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
  });
}

// --- robots.txt -------------------------------------------------------------------
function parseRobots(text) {
  const groups = []; let cur = null; let lastWasAgent = false;
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.replace(/#.*/, '').trim();
    const m = /^([A-Za-z-]+)\s*:\s*(.*)$/.exec(line);
    if (!m) continue;
    const field = m[1].toLowerCase(); const val = m[2].trim();
    if (field === 'user-agent') {
      if (!lastWasAgent || !cur) { cur = { agents: [], allow: [], disallow: [] }; groups.push(cur); }
      cur.agents.push(val.toLowerCase()); lastWasAgent = true;
    } else {
      lastWasAgent = false;
      if (!cur) continue;
      if (field === 'disallow' && val) cur.disallow.push(val);
      if (field === 'allow' && val) cur.allow.push(val);
    }
  }
  const mine = groups.filter((g) => g.agents.some((a) => a !== '*' && 'novascoutbot'.indexOf(a) === 0));
  return mine.length ? mine : groups.filter((g) => g.agents.indexOf('*') !== -1);
}
function ruleMatch(rule, path) {
  const re = new RegExp('^' + rule.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\\\$$/, '$'));
  return re.test(path);
}
function pathAllowed(groups, path) {
  let best = null;
  for (const g of groups) {
    for (const r of g.disallow) if (ruleMatch(r, path) && (!best || r.length > best.len)) best = { len: r.length, allow: false };
    for (const r of g.allow) if (ruleMatch(r, path) && (!best || r.length >= best.len)) best = { len: r.length, allow: true };
  }
  return !best || best.allow;
}
const robotsCache = {};
async function robotsFor(origin) {
  if (robotsCache[origin]) return robotsCache[origin];
  let groups = [];
  try {
    const r = await rawGet(origin + '/robots.txt', true, 0);
    if (r.statusCode >= 200 && r.statusCode < 300) groups = parseRobots(r.body);
  } catch (e) { /* unreachable robots.txt: no rules (RFC 9309 treats 4xx/unreachable as allow-all) */ }
  robotsCache[origin] = groups;
  return groups;
}

// --- one candidate --------------------------------------------------------------------
const FORM_RE = /<form\b[\s\S]{0,6000}?(type\s*=\s*["']?email|<textarea)|wpcf7|wpforms|gform_wrapper|hbspt\.forms|hs-form|typeform\.com|jotform|formspree|elementor-form|forms\.office\.com|ninja-forms|caldera_forms/i;

async function check(c) {
  const host = c.host;
  const homeOrigin = 'https://' + host;
  const groups = await robotsFor(homeOrigin);
  const base = { host: host, name: c.name, city: c.city, country: c.country, source: c.source };
  if (!pathAllowed(groups, '/')) return Object.assign(base, { outcome: 'robots-disallowed', robots_allowed: false });
  const pages = [];
  const helpers = {
    httpRequest: async (o) => {
      const u = new URL(o.url);
      const g = u.host.replace(/^www\./, '') === host ? groups : await robotsFor(u.protocol + '//' + u.host);
      if (!pathAllowed(g, u.pathname + u.search)) throw new Error('robots.txt disallows ' + u.pathname);
      const r = await rawGet(o.url, !!o.skipSslCertificateValidation, 0);
      if (r.statusCode >= 200 && r.statusCode < 400) pages.push(r.body);
      return r;
    },
  };
  const resp = { stop_reason: 'end_turn', usage: {}, content: [{ type: 'text', text: JSON.stringify({ url: c.website, evidence_url: null }) }] };
  const cand = { location_id: 0, canonical_name: c.facility_name || c.name, city: c.city, country: c.country };
  let v;
  try { v = await V.verify(helpers, cand, resp); } catch (e) { v = { outcome: 'not-confirmed', detail: String(e && e.message).slice(0, 200) }; }
  // Overture's own name may be the one the site prints when the registry's is not.
  if (v.outcome !== 'resolved' && c.facility_name && c.name !== c.facility_name) {
    pages.length = 0;
    try {
      const v2 = await V.verify(helpers, Object.assign({}, cand, { canonical_name: c.name }), resp);
      if (v2.outcome === 'resolved') v = Object.assign(v2, { detail: 'by its Overture name: ' + v2.detail });
    } catch (e) { /* keep the first answer */ }
  }
  const html = pages.join('\n');
  const mailtos = (html.match(/mailto:([^"'?>\s]+)/gi) || []).map((m) => m.slice(7).toLowerCase());
  const own = mailtos.filter((a) => V.sameSite((a.split('@')[1] || '').replace(/^www\./, ''), host));
  return Object.assign(base, {
    outcome: v.outcome, detail: String(v.detail || '').slice(0, 400), domain: v.domain || null, robots_allowed: true,
    has_contact_form: pages.length ? FORM_RE.test(html) : null,
    has_mailto: pages.length ? own.length > 0 : null,
  });
}

async function main() {
  if (!outPath) { console.error('usage: node verify.js <trialsites.json> <candidates.json> <out.json> [concurrency]'); process.exit(2); }
  loadVerifier();
  const cands = JSON.parse(fs.readFileSync(candPath, 'utf8'));
  const done = fs.existsSync(outPath) ? JSON.parse(fs.readFileSync(outPath, 'utf8')) : {};
  let next = 0; let n = 0;
  async function lane() {
    while (next < cands.length) {
      const c = cands[next++];
      if (done[c.host]) continue;
      done[c.host] = Object.assign(await check(c), { candidate: c });
      if (++n % 50 === 0) {
        fs.writeFileSync(outPath + '.tmp', JSON.stringify(done)); fs.renameSync(outPath + '.tmp', outPath);
        const t = {}; Object.values(done).forEach((d) => { t[d.outcome] = (t[d.outcome] || 0) + 1; });
        console.log(new Date().toISOString().slice(11, 19), Object.keys(done).length + '/' + cands.length, JSON.stringify(t));
      }
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, lane));
  fs.writeFileSync(outPath + '.tmp', JSON.stringify(done)); fs.renameSync(outPath + '.tmp', outPath);
  const t = {}; Object.values(done).forEach((d) => { t[d.outcome] = (t[d.outcome] || 0) + 1; });
  console.log('done', Object.keys(done).length, JSON.stringify(t));
}
if (require.main === module) main();
module.exports = { parseRobots, pathAllowed, FORM_RE };

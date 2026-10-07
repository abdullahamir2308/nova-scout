// Unit tests for Harvest Site Emails (code_site_emails.js) -- the third free
// contact source, added 2026-10-07.
//
//     node n8n/contacts/test_site_emails.js
//
// The node is where the money is NOT spent, so the failure mode is silent: a
// rule that is a shade too loose writes an address belonging to another company
// (or to nobody) and the pipeline emails it, verified, with nobody the wiser.
// Every case here is a shape taken from the nine leads that were actually stuck
// on 2026-10-07, or from what their pages actually served.
//
// No network: `helpers.httpRequest` is mocked with a page table per host.

const fs = require('fs');
const path = require('path');
const { runner } = require('./harness');

const SRC = fs.readFileSync(path.join(__dirname, 'code_site_emails.js'), 'utf8');
const t = runner('Workflow 3b -- addresses published on the lead own site');

// Runs the node body with a mocked HTTP layer. `pages` maps a full URL to
// either an HTML string, or {status} / {throws} to fail that rung.
function harvest(leads, pages, opts) {
  const o = opts || {};
  const requested = [];
  const helpers = {
    httpRequest: async function (req) {
      requested.push(req.url);
      const page = pages[req.url.replace(/\/$/, '')] === undefined
        ? pages[req.url]
        : pages[req.url.replace(/\/$/, '')];
      if (page === undefined) return { statusCode: 404, body: '' };
      if (typeof page === 'object' && page.throws) throw new Error(page.throws);
      if (typeof page === 'object' && page.status) return { statusCode: page.status, body: page.body || '' };
      return { statusCode: 200, body: page };
    },
  };
  const items = leads.map(function (l) { return { json: l }; });
  const $input = { all: function () { return items; } };
  const $ = function (name) {
    if (name === 'Config') return { first: function () { return { json: o.config || {} }; } };
    if (name === 'Index Scraped Emails') {
      return { first: function () { return { json: { by_domain: o.byDomain || {} } }; } };
    }
    throw new Error('no mocked upstream node named ' + JSON.stringify(name));
  };
  const fn = new Function('$input', '$', 'URL', 'fetch',
    '"use strict"; return (async () => {' + SRC + '})()');
  return fn.call({ helpers: helpers }, $input, $, undefined, undefined)
    .then(function (out) {
      return { out: out.map(function (i) { return i.json; }), requested: requested };
    });
}

function lead(over) {
  return Object.assign({
    lead_id: 1,
    domain: 'example-cro.com',
    company_name: 'Example CRO',
    country: 'Germany',
    fit_score: 55,
    founder_name: null,
    founder_linkedin: null,
    founder_title: null,
  }, over || {});
}

function page(body) {
  return '<html><body>' + body + '</body></html>';
}

const HOME = 'https://example-cro.com';

// A queue of promises, run in order, so the harness stays synchronous-looking.
const checks = [];
function expect(label, promise, pick, want) {
  checks.push(function () {
    return promise.then(function (r) { t.check(label, pick(r), want); })
      .catch(function (e) { t.check(label, 'threw: ' + e.message, want); });
  });
}

// ---------------------------------------------------------------------------
// The five leads this source was built for
// ---------------------------------------------------------------------------

expect(
  'a mailto: on the homepage is taken (rotrial.com: contact_us@)',
  harvest([lead({ domain: 'rotrial.com' })],
    { 'https://rotrial.com': page('<a href="mailto:contact_us@rotrial.com">write to us</a>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_how, r.out[0].site_lookup]; },
  ['contact_us@rotrial.com', 'mailto', 'found']
);
expect(
  'an address in plain text is taken too (ivrs.org.in: info@, text only)',
  harvest([lead({ domain: 'ivrs.org.in' })],
    { 'https://ivrs.org.in': page('<p>Reach us at info@ivrs.org.in</p>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_how]; },
  ['info@ivrs.org.in', 'text']
);
expect(
  'bd@ beats careers@ and ir@ (hvivo.com publishes all three)',
  harvest([lead({ domain: 'hvivo.com' })],
    { 'https://hvivo.com': page('<a href="mailto:careers@hvivo.com">jobs</a>' +
        '<a href="mailto:ir@hvivo.com">investors</a><a href="mailto:bd@hvivo.com">business</a>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_rejected]; },
  ['bd@hvivo.com', ['careers@hvivo.com', 'ir@hvivo.com']]
);
expect(
  'info@ beats client@ (delphiniumcro.com: a general inbox before a support one)',
  harvest([lead({ domain: 'delphiniumcro.com' })],
    { 'https://delphiniumcro.com': page('<a href="mailto:client@delphiniumcro.com">clients</a>' +
        '<a href="mailto:info@delphiniumcro.com">info</a>') }),
  function (r) { return r.out[0].site_email; },
  'info@delphiniumcro.com'
);
expect(
  'a mailbox carrying the founder name wins -- two sources agreeing on the person (ee-cro.com)',
  harvest([lead({ domain: 'ee-cro.com', founder_name: 'Ilse Eder' })],
    { 'https://ee-cro.com': page('<a href="mailto:office@ee-cro.com">office</a>' +
        '<a href="mailto:ilse.eder@ee-cro.com">Ilse Eder</a>' +
        '<a href="mailto:michael.freigassner@ee-cro.com">Michael Freigassner</a>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_is_founder, r.out[0].site_email_role_inbox]; },
  ['ilse.eder@ee-cro.com', true, false]
);
expect(
  'with no founder named, the role inbox wins over two personal ones -- nothing says whose desk is right',
  harvest([lead({ domain: 'ee-cro.com' })],
    { 'https://ee-cro.com': page('<a href="mailto:office@ee-cro.com">office</a>' +
        '<a href="mailto:ilse.eder@ee-cro.com">Ilse Eder</a>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_role_inbox]; },
  ['office@ee-cro.com', true]
);

// ---------------------------------------------------------------------------
// The four that must stay empty -- each one a real page from 2026-10-07
// ---------------------------------------------------------------------------

expect(
  'an address on ANOTHER domain is never taken (mtz-clinical.pl publishes only @pratia.com -- it was acquired)',
  harvest([lead({ domain: 'mtz-clinical.pl' })],
    { 'https://mtz-clinical.pl': page('<a href="mailto:info@pratia.com">info</a>' +
        '<p>agnieszka.dobaczewska@pratia.com</p>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_candidates, r.out[0].site_lookup]; },
  [null, [], 'no-address-found']
);
expect(
  'a near-miss domain is still another domain (cebisinternational.com publishes only @cebis-int.com)',
  harvest([lead({ domain: 'cebisinternational.com' })],
    { 'https://cebisinternational.com': page('<a href="mailto:office@cebis-int.com">office</a>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  'Wix telemetry is not a contact (archerresearch.eu serves only @sentry.wixpress.com)',
  harvest([lead({ domain: 'archerresearch.eu' })],
    { 'https://archerresearch.eu': page('<script>{"dsn":"https://8eb3@sentry.wixpress.com/1"}</script>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  'a site that publishes nothing yields nothing, and says so (clinexa.com)',
  harvest([lead({ domain: 'clinexa.com' })],
    { 'https://clinexa.com': page('<p>Contact us through the form.</p>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_lookup]; },
  [null, 'no-address-found']
);

// ---------------------------------------------------------------------------
// Nothing is derived, guessed or mis-read
// ---------------------------------------------------------------------------

expect(
  'a sprite filename is not an address (chosen-sprite@2x.png, from delphiniumcro.com)',
  harvest([lead()], { [HOME]: page('<img src="chosen-sprite@2x.png">' +
    '<p>hero@example-cro.com.webp</p>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_candidates]; },
  [null, []]
);
expect(
  'an obfuscated address is NOT un-obfuscated -- that would be a guess about what the page meant',
  harvest([lead()], { [HOME]: page('<p>info [at] example-cro [dot] com</p>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  'nothing is pattern-derived: a page naming a founder and no address yields no address',
  harvest([lead({ founder_name: 'Jane Roe' })],
    { [HOME]: page('<p>Founded by Jane Roe, Managing Director.</p>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  'a subdomain of the lead own domain counts',
  harvest([lead()], { [HOME]: page('<a href="mailto:hello@mail.example-cro.com">hi</a>') }),
  function (r) { return r.out[0].site_email; },
  'hello@mail.example-cro.com'
);
expect(
  'a domain that merely ENDS with the lead domain does not (notexample-cro.com)',
  harvest([lead()], { [HOME]: page('<a href="mailto:hello@notexample-cro.com">hi</a>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  'a percent-encoded mailto: is decoded',
  harvest([lead()], { [HOME]: page('<a href="mailto:in%66o@example-cro.com">hi</a>') }),
  function (r) { return r.out[0].site_email; },
  'info@example-cro.com'
);
expect(
  'a careers.uk@ style prefix is still careers',
  harvest([lead()], { [HOME]: page('<a href="mailto:careers.uk@example-cro.com">jobs</a>') }),
  function (r) { return [r.out[0].site_email, r.out[0].site_email_rejected.length]; },
  [null, 1]
);
expect(
  'no-reply, however it is spelled, is never a contact',
  harvest([lead()], { [HOME]: page('<a href="mailto:no-reply@example-cro.com">x</a>' +
    '<a href="mailto:noreply2@example-cro.com">y</a>' +
    '<a href="mailto:do_not_reply@example-cro.com">z</a>') }),
  function (r) { return r.out[0].site_email; },
  null
);
expect(
  '"contact@" is not read as containing a banned word -- the match is on the whole local part',
  harvest([lead()], { [HOME]: page('<a href="mailto:contact@example-cro.com">x</a>') }),
  function (r) { return r.out[0].site_email; },
  'contact@example-cro.com'
);
expect(
  'the pick is deterministic: two equally ranked addresses always resolve the same way',
  Promise.all([
    harvest([lead()], { [HOME]: page('<a href="mailto:zebra@example-cro.com">z</a>' +
      '<a href="mailto:alpha@example-cro.com">a</a>') }),
    harvest([lead()], { [HOME]: page('<a href="mailto:alpha@example-cro.com">a</a>' +
      '<a href="mailto:zebra@example-cro.com">z</a>') }),
  ]).then(function (rs) { return rs.map(function (r) { return r.out[0].site_email; }); }),
  function (x) { return x; },
  ['alpha@example-cro.com', 'alpha@example-cro.com']
);

// ---------------------------------------------------------------------------
// Which pages are read, and which requests are not made at all
// ---------------------------------------------------------------------------

expect(
  'a lead whose address is already in the CSV is never fetched -- no request to their server',
  harvest([lead()], { [HOME]: page('<a href="mailto:info@example-cro.com">x</a>') },
    { byDomain: { 'example-cro.com': 'listed@example-cro.com' } }),
  function (r) { return [r.requested.length, r.out[0].site_lookup, r.out[0].site_email]; },
  [0, 'not-needed', null]
);
expect(
  'a contact page linked from the homepage is read',
  harvest([lead()], {
    [HOME]: page('<a href="/contact-us">Contact</a>'),
    'https://example-cro.com/contact-us': page('<p>office@example-cro.com</p>'),
  }),
  function (r) { return [r.out[0].site_email, r.out[0].site_pages_fetched]; },
  ['office@example-cro.com', 2]
);
expect(
  'an /impressum is read -- where a German, Austrian or Swiss CRO is legally required to publish one',
  harvest([lead()], {
    [HOME]: page('<a href="/impressum">Impressum</a>'),
    'https://example-cro.com/impressum': page('<p>E-Mail: kontakt@example-cro.com</p>'),
  }),
  function (r) { return r.out[0].site_email; },
  'kontakt@example-cro.com'
);
expect(
  'the fallback paths are tried when the homepage links nowhere useful',
  harvest([lead()], {
    [HOME]: page('<a href="/services">Services</a>'),
    'https://example-cro.com/contact': page('<p>info@example-cro.com</p>'),
  }),
  function (r) { return [r.out[0].site_email, r.requested.indexOf('https://example-cro.com/contact') !== -1]; },
  ['info@example-cro.com', true]
);
expect(
  'a link to another host is never followed, however contact-ish it looks',
  harvest([lead()], { [HOME]: page('<a href="https://other.example/contact">Contact</a>') }),
  function (r) { return r.requested.filter(function (u) { return u.indexOf('other.example') !== -1; }).length; },
  0
);
expect(
  'at most Config.site_max_pages requests per lead',
  harvest([lead()], {
    [HOME]: page('<a href="/contact">a</a><a href="/contacts">b</a><a href="/kontakt">c</a>' +
      '<a href="/impressum">d</a><a href="/about">e</a><a href="/team">f</a>'),
  }, { config: { site_max_pages: 3 } }),
  function (r) { return r.requested.length; },
  3
);

// ---------------------------------------------------------------------------
// Reaching the site at all -- the same ladder Workflow 2 measured
// ---------------------------------------------------------------------------

expect(
  'the www host is tried when the bare host fails',
  harvest([lead()], {
    'https://example-cro.com': { throws: 'ENOTFOUND' },
    'https://www.example-cro.com': page('<p>info@example-cro.com</p>'),
  }),
  function (r) { return [r.out[0].site_email, r.out[0].site_fetch_errors.length]; },
  ['info@example-cro.com', 1]
);
expect(
  'a site that will not load is recorded as unreachable, not as "no address"',
  harvest([lead()], {}, {}),
  function (r) { return [r.out[0].site_lookup, r.out[0].site_email]; },
  ['unreachable', null]
);
expect(
  'a 403 on the homepage is an error on that rung, not a crash',
  harvest([lead()], {
    'https://example-cro.com': { status: 403 },
    'https://www.example-cro.com': { status: 403 },
    'http://example-cro.com': page('<p>info@example-cro.com</p>'),
  }),
  function (r) { return r.out[0].site_email; },
  'info@example-cro.com'
);
expect(
  'a subpage that fails does not lose the addresses the homepage gave',
  harvest([lead()], {
    [HOME]: page('<a href="/contact">c</a><p>info@example-cro.com</p>'),
    'https://example-cro.com/contact': { throws: 'ECONNRESET' },
  }),
  function (r) { return [r.out[0].site_email, r.out[0].site_fetch_errors.length]; },
  ['info@example-cro.com', 1]
);
expect(
  'the lead row is passed through untouched, so Resolve Contact still sees its own fields',
  harvest([lead({ lead_id: 733, fit_score: 53, founder_name: 'Jane Roe' })],
    { [HOME]: page('x') }),
  function (r) { return [r.out[0].lead_id, r.out[0].fit_score, r.out[0].founder_name, r.out[0].company_name]; },
  [733, 53, 'Jane Roe', 'Example CRO']
);
expect(
  'several leads in one batch each get their own answer',
  harvest([lead({ lead_id: 1, domain: 'a-cro.com' }), lead({ lead_id: 2, domain: 'b-cro.com' })], {
    'https://a-cro.com': page('<a href="mailto:info@a-cro.com">x</a>'),
    'https://b-cro.com': page('<p>nothing here</p>'),
  }),
  function (r) { return [r.out[0].site_email, r.out[1].site_email, r.out.length]; },
  ['info@a-cro.com', null, 2]
);

// ---------------------------------------------------------------------------

(function run(i) {
  if (i >= checks.length) {
    t.done();
    return;
  }
  checks[i]().then(function () { run(i + 1); });
})(0);

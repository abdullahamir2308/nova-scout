// Unit tests for Workflow 1's CSV fingerprint (code_fingerprint.js) and the
// fan-out that follows it (code_rows.js).
//
//     node n8n/ingestion/test_fingerprint.js
//
// The fingerprint decides whether ingestion does anything at all, so it has two
// opposite failure modes and both are silent:
//
//   too sensitive  every scrape looks like a change, and the upsert runs twice
//                  an hour for nothing. Wasteful, visible in ingest_log.
//   too insensitive a real change to a lead never reaches `leads` and nobody
//                  ever finds out. That is the one these cases are really for.

const fs = require('fs');
const path = require('path');

const FP = fs.readFileSync(path.join(__dirname, 'code_fingerprint.js'), 'utf8');
const ROWS = fs.readFileSync(path.join(__dirname, 'code_rows.js'), 'utf8');

let failed = 0;
function check(label, got, want) {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a === b) {
    console.log('  ok   ' + label);
  } else {
    failed++;
    console.log('  FAIL ' + label + '\n         expected ' + b + '\n         actual   ' + a);
  }
}

function fingerprint(rows) {
  const items = rows.map(function (r) { return { json: r }; });
  const $input = { all: function () { return items; } };
  const fn = new Function('$input', '"use strict"; return (() => {' + FP + '})()');
  return fn($input)[0].json;
}

function fanOut(fp) {
  const $ = function (name) {
    if (name === 'Fingerprint CSV') return { first: function () { return { json: fp }; } };
    throw new Error('no mocked upstream node named ' + JSON.stringify(name));
  };
  const fn = new Function('$', '"use strict"; return (() => {' + ROWS + '})()');
  return fn($).map(function (i) { return i.json; });
}

// Shapes taken from real rows of data/ichgcp_leads.csv.
const CSV = [
  { domain: 'cvbf.net', company_name: 'Consorzio per Valutazioni Biologiche e Farmacologiche (CVBF)',
    country: 'Albania', email: 'info@cvbf.net', phone: '+355 422 46082', address: 'Rruga Prokop Myzeqari, 9',
    description: 'A long description that changes whenever the page is re-scraped.' },
  { domain: 'bitrial.hu', company_name: 'BiTrial', country: 'Hungary', email: 'info@bitrial.hu' },
  { domain: 'hvivo.com', company_name: 'hVIVO', country: 'United Kingdom', email: '' },
];

const BASE = fingerprint(CSV);

console.log('Workflow 1 -- the CSV fingerprint\n');

check('a fingerprint is produced, with the algorithm and the row count visible in it',
  /^fnv1a64:[0-9a-f]{16}:3$/.test(BASE.fingerprint), true);
check('the row count is reported separately too', [BASE.rows_in_csv, BASE.usable], [3, true]);

check('the same file fingerprints the same way twice', fingerprint(CSV).fingerprint, BASE.fingerprint);
check('row order does not matter -- a shard merge rewrites the file in a different order',
  fingerprint([CSV[2], CSV[0], CSV[1]]).fingerprint, BASE.fingerprint);

// --- what MUST change the fingerprint ---------------------------------------

check('a new lead changes it',
  fingerprint(CSV.concat([{ domain: 'new-cro.com', company_name: 'New CRO', country: 'Poland' }]))
    .fingerprint !== BASE.fingerprint, true);
check('a removed lead changes it',
  fingerprint([CSV[0], CSV[1]]).fingerprint !== BASE.fingerprint, true);
check('a renamed company changes it -- the upsert writes company_name',
  fingerprint([Object.assign({}, CSV[0], { company_name: 'CVBF' }), CSV[1], CSV[2]])
    .fingerprint !== BASE.fingerprint, true);
check('a moved country changes it -- the upsert writes country',
  fingerprint([Object.assign({}, CSV[0], { country: 'Italy' }), CSV[1], CSV[2]])
    .fingerprint !== BASE.fingerprint, true);

// --- what must NOT change it ------------------------------------------------

check('a rewritten description does not -- the upsert never touches it',
  fingerprint([Object.assign({}, CSV[0], { description: 'Completely different prose.' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('a new phone or address does not either',
  fingerprint([Object.assign({}, CSV[0], { phone: '+1 555 0000', address: 'Somewhere else' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('nor does an e-mail -- Workflow 3b re-fetches the CSV and reads that column itself',
  fingerprint([Object.assign({}, CSV[0], { email: 'new@cvbf.net' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('nor a changed profile_url',
  fingerprint([Object.assign({}, CSV[0], { profile_url: 'https://ichgcp.net/other' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);

// --- canonicalisation: the dedupe key is a normalised domain ----------------

check('domain case and a www prefix are the same domain -- it is the dedupe key',
  fingerprint([Object.assign({}, CSV[0], { domain: 'WWW.CVBF.NET' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('surrounding whitespace is the same domain too',
  fingerprint([Object.assign({}, CSV[0], { domain: '  cvbf.net  ' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('a trailing slash is the same domain',
  fingerprint([Object.assign({}, CSV[0], { domain: 'cvbf.net/' }), CSV[1], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('whitespace around a company name is not a change',
  fingerprint([Object.assign({}, CSV[1], { company_name: '  BiTrial  ' }), CSV[0], CSV[2]])
    .fingerprint, BASE.fingerprint);
check('a row with no domain cannot be upserted, so it is not in the fingerprint either',
  fingerprint(CSV.concat([{ domain: '', company_name: 'Blank', country: 'Poland' }])).fingerprint,
  BASE.fingerprint);
check('... and is not counted', fingerprint(CSV.concat([{ domain: '' }])).rows_in_csv, 3);

// --- the empty / broken file ------------------------------------------------

const EMPTY = fingerprint([]);
check('an empty file is not usable, so New CSV? will not act on it',
  [EMPTY.usable, EMPTY.rows_in_csv], [false, 0]);
check('a file of nothing but blank rows is not usable either',
  fingerprint([{ domain: '' }, { domain: '   ' }]).usable, false);

// --- a collision would have to preserve the row count too -------------------

check('the row count is part of the fingerprint, so two different sizes can never collide',
  fingerprint(CSV).fingerprint.split(':')[2] !== fingerprint([CSV[0]]).fingerprint.split(':')[2], true);

// --- the fan-out ------------------------------------------------------------

console.log('');
const OUT = fanOut(BASE);
check('every usable row comes back out, one item each', OUT.length, 3);
check('each item carries exactly the three columns the upsert writes',
  OUT.map(function (r) { return Object.keys(r).sort(); }),
  [['company_name', 'country', 'domain'], ['company_name', 'country', 'domain'],
   ['company_name', 'country', 'domain']]);
check('the domain is the normalised one, which is what ON CONFLICT (domain) keys on',
  OUT.map(function (r) { return r.domain; }).sort(), ['bitrial.hu', 'cvbf.net', 'hvivo.com']);
check('an empty file fans out to nothing rather than throwing -- a throw would leave '
  + 'ingest_log unwritten and the next tick would retry the same file for ever',
  fanOut(EMPTY).length, 0);
check('a missing rows array is the same', fanOut({}).length, 0);

console.log('\n' + (failed ? failed + ' failed' : 'all checks passed'));
process.exit(failed ? 1 : 0);

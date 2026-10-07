// Fingerprint CSV -- n8n Code node (Run Once for All Items).
//
// Answers one question: has data/ichgcp_leads.csv changed, in a way that
// matters to `leads`, since the last ingest?
//
// WHY THIS NODE EXISTS. Ingestion used to be a Schedule Trigger set to
// "daily, triggerAtHour 23". On n8n 2.35.7 that compiles to a cron of
// `<s> <m> 23 * * *` with no recurrence gate, so it fires once a day at 23:xx
// Asia/Karachi and at no other time. The host is a laptop that is on in the
// PKT daytime and off at night, so most nights it simply never ran -- and
// Section 7's idempotency rule is explicit that "no workflow may assume its
// schedule fired". Every other stage obeys that by draining a queue. This one
// has no queue: its input is a file. So the file's own identity becomes the
// queue, and the workflow can tick as often as it likes.
//
// WHAT IS FINGERPRINTED, and why not the bytes. Only the three fields the
// upsert writes -- domain, company_name, country -- canonicalised and sorted by
// domain. A change to a description, a phone number or an address is a real
// change to the file and no change at all to `leads`, and re-running the upsert
// for it would be work with no outcome. The e-mail column is deliberately out
// too: Workflow 3b re-fetches the CSV for itself and reads the address there,
// so ingestion ignoring it changes nothing.
//
// THE HASH. FNV-1a, 64-bit, over that canonical text, in BigInt. The n8n Code
// sandbox refuses `require('crypto')` (measured on this instance 2026-10-07:
// "Module 'crypto' is disallowed") and has no `crypto.subtle`, so a real digest
// is not available here; BigInt, Buffer and TextEncoder are. FNV-1a is not a
// cryptographic hash and does not need to be: nobody is trying to forge a
// collision, the only question is whether two versions of a file we published
// ourselves differ. The row count goes in the fingerprint alongside the hash,
// so a collision would also have to preserve the row count exactly.

const FNV_OFFSET = 14695981039346656037n;
const FNV_PRIME = 1099511628211n;
const MASK = (1n << 64n) - 1n;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Matches the upsert's own idea of a domain: that column is the dedupe key
// (Section 9, "Dedupe key is normalised domain"), so two spellings of one
// domain must not read as two different files.
function normDomain(v) {
  return str(v).toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '')
    .replace(/[/].*$/, '').replace(/[.:]+$/, '');
}

function fnv1a64(text) {
  const bytes = new TextEncoder().encode(text);
  let h = FNV_OFFSET;
  for (let i = 0; i < bytes.length; i++) {
    h ^= BigInt(bytes[i]);
    h = (h * FNV_PRIME) & MASK;
  }
  let hex = h.toString(16);
  while (hex.length < 16) hex = '0' + hex;
  return hex;
}

const rows = [];
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  const r = items[i].json || {};
  const domain = normDomain(r.domain);
  // A row with no domain cannot be upserted (domain is the key) and must not
  // change the fingerprint either, or an empty line would look like new data.
  if (!domain) continue;
  rows.push({
    domain: domain,
    company_name: str(r.company_name),
    country: str(r.country),
  });
}

rows.sort(function (a, b) {
  if (a.domain !== b.domain) return a.domain < b.domain ? -1 : 1;
  if (a.company_name !== b.company_name) return a.company_name < b.company_name ? -1 : 1;
  return a.country < b.country ? -1 : (a.country > b.country ? 1 : 0);
});

// Tab-separated, newline-terminated: neither character can appear inside a
// value here (the parser strips them), so no two different files can serialise
// to the same text by rearranging field boundaries.
const canonical = rows.map(function (r) {
  return r.domain + '\t' + r.company_name + '\t' + r.country;
}).join('\n');

return [{
  json: {
    // The shape is readable on purpose: an operator looking at ingest_log can
    // see the algorithm and the row count without consulting this file.
    fingerprint: 'fnv1a64:' + fnv1a64(canonical) + ':' + rows.length,
    rows_in_csv: rows.length,
    // A file with no usable rows is not a new version of anything -- raw.github
    // serving an error page, or a scrape that wrote nothing, must not record an
    // ingest or wipe anything.
    usable: rows.length > 0,
    rows: rows,
  },
}];

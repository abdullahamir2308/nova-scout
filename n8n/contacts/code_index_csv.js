// Index Scraped Emails — n8n Code node (Run Once for All Items).
//
// Builds a domain -> email map from the committed ICH GCP CSV, so the Apollo
// stage can answer "do we already have an address for this lead?" without a
// per-lead fetch and without a credit.
//
// Why this node exists at all: the scrape captured a `Web:`-adjacent `E-mail:`
// field on every ichgcp company profile, but `leads` has no email column
// (Section 8, locked), so ingestion dropped it on the floor. The address was
// never lost -- it is still in data/ichgcp_leads.csv, which the ingestion
// workflow already publishes to raw.githubusercontent.com. 92 of the 123 rows
// carry one. Reading it back here is free; asking Apollo for it is not.
//
// The map is built once per batch rather than per lead: one fetch, one parse,
// then an O(1) lookup per lead in Resolve Contact.

const rows = $input.all();

// Matches the scraper's own normalisation (scrape_ichgcp.py) and the ingestion
// upsert, which writes leads.domain verbatim from this same column. Lowercase +
// trim + strip a leading www. is defensive, not corrective: an exact match is
// the contract, and a mismatch would silently cost a credit rather than fail.
function normDomain(d) {
  if (typeof d !== 'string') return '';
  return d.trim().toLowerCase().replace(/^www\./, '').replace(/[./]+$/, '');
}

// Deliberately permissive. The goal is to reject junk that would be written
// into contacts.email as if it were reachable -- not to adjudicate deliverability,
// which is the sending workflow's job and the mailbox's after that.
function looksLikeEmail(e) {
  if (typeof e !== 'string') return false;
  const s = e.trim();
  if (!s || s.length > 254) return false;
  if (/\s/.test(s)) return false;
  const at = s.indexOf('@');
  if (at < 1 || at !== s.lastIndexOf('@')) return false;
  const host = s.slice(at + 1);
  return host.indexOf('.') > 0 && !host.startsWith('.') && !host.endsWith('.');
}

const byDomain = {};
let withEmail = 0;
let malformed = 0;

for (const r of rows) {
  const j = r.json || {};
  const domain = normDomain(j.domain);
  if (!domain) continue;
  const raw = typeof j.email === 'string' ? j.email.trim() : '';
  if (!raw) continue;
  if (!looksLikeEmail(raw)) {
    malformed++;
    continue;
  }
  // First row wins. A domain appearing on several country pages carries the
  // same profile-page email each time; this only decides ties, it does not
  // merge anything.
  if (!byDomain[domain]) {
    byDomain[domain] = raw;
    withEmail++;
  }
}

return [
  {
    json: {
      by_domain: byDomain,
      csv_rows: rows.length,
      domains_with_email: withEmail,
      malformed_emails: malformed,
    },
  },
];

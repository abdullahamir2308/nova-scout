// Rows to Items -- n8n Code node (Run Once for All Items).
//
// Fans the fingerprinted rows back out, one item each, for the upsert.
//
// Fingerprint CSV collapses the whole file into ONE item so that Check Ingest
// Log and New CSV? each run once rather than once per row -- a Postgres node
// executes its query per item, and asking "have we seen this file?" 273 times
// would be 273 queries to answer one question. This node undoes that collapse.
//
// It reads the rows from Fingerprint CSV by name rather than from $json,
// because the Postgres node between them replaced the items with its own
// result. That is the silent-empty-field trap Workflow 4 found the hard way
// (Section 9, Workflow 4): n8n resolves a field nobody emitted to an empty
// string without erroring, so this would have upserted 273 blank rows and
// reported success.

const fp = $('Fingerprint CSV').first().json;
const rows = (fp && fp.rows) || [];

if (!rows.length) {
  // Cannot happen downstream of New CSV? (which gates on `usable`), but an
  // empty return is the only safe answer if it ever does: an upsert of nothing
  // is a no-op, while a thrown error would leave ingest_log unwritten and the
  // next tick would try the same file again for ever.
  return [];
}

return rows.map(function (r) {
  return {
    json: {
      domain: r.domain,
      company_name: r.company_name,
      country: r.country,
    },
  };
});

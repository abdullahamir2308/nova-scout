# Workflow 1 — ICH GCP ingestion

Sources for the two Code nodes in `../workflows/ingestion-ichgcp.json`, the tests
that cover them, and the generator that assembles the workflow JSON.

Same contract as every other stage: the workflow is **generated, not
hand-edited**, so the JS that was tested standalone is byte-identical to the JS
that ships inside the escaped JSON string. Regenerate after any change:

```powershell
python n8n/ingestion/build_workflow.py   # writes ../workflows/ingestion-ichgcp.json
```

Then re-import. The repo is not mounted into the container, so copy it in first:

```powershell
docker cp n8n\workflows\ingestion-ichgcp.json nova-scout-n8n-1:/tmp/ingestion-ichgcp.json
docker exec nova-scout-n8n-1 n8n import:workflow --input=/tmp/ingestion-ichgcp.json
```

`import:workflow` **deactivates** the workflow, and activation is a UI action —
re-activate it in the editor after importing.

This stage got a generator on 2026-10-07, later than the others, because until
then it was four nodes and no JS. It needed one when its schedule changed.

## What changed, and why

The trigger was `days` / `triggerAtHour: 23`. On n8n 2.35.7 that compiles to a
cron of `<s> <m> 23 * * *` with no recurrence gate, so it fired **once a day at
23:xx Asia/Karachi and at no other time** — on a laptop that is on in the PKT
daytime and off at night, which means most nights it never fired at all.

Master Ref Section 7 is explicit: *"the host machine will be off some of the
time. No workflow may assume its schedule fired."* Every other stage obeys that
by draining a queue. This one has no queue, because its input is a file.

**So the file's own identity became the queue.** The workflow ticks every 30
minutes and does its work only when `data/ichgcp_leads.csv` has changed since
the last ingest. A quiet tick is one HTTP GET of a committed file and one
`SELECT`.

```
Cron / Manual → Fetch ICH GCP CSV → Parse CSV → Fingerprint CSV
                                                      ↓
                                              Check Ingest Log
                                                      ↓
                                                  New CSV? ── no ──→ (nothing)
                                                      │ yes
                                                      ↓
                                               Rows to Items
                                                      ↓
                                             Upsert into leads
                                                      ↓
                                               Record Ingest
```

## What "changed" means

A fingerprint of **the three columns the upsert writes** — domain (normalised,
the dedupe key), company name, country — canonicalised, sorted by domain, and
hashed with FNV-1a/64, with the row count carried alongside it:
`fnv1a64:<16 hex>:<rows>`.

Deliberately not the bytes. A rewritten description, a new phone number or a
changed address is a real change to the file and no change at all to `leads`;
re-running the upsert for it would be work with no outcome. The e-mail column is
out too, because Workflow 3b re-fetches the CSV and reads that column itself.

The build **refuses** if the hashed columns and the upserted columns ever
diverge. Both directions are faults, and one is silent:

| Drift | Symptom |
|---|---|
| a column in the hash, not in the upsert | every scrape triggers a pointless re-ingest |
| a column in the upsert, not in the hash | a real change to it is **never** ingested, and nothing says so |

**Not a cryptographic hash, on purpose.** The n8n Code sandbox refuses
`require('crypto')` (measured 2026-10-07: `Module 'crypto' is disallowed`) and
has no `crypto.subtle`; `BigInt`, `Buffer` and `TextEncoder` are available.
Nobody is forging a collision against a file this repo publishes itself, and a
collision would have to preserve the row count exactly as well.

## Ordering, and what a crash costs

`Record Ingest` runs **after** the upsert, never before. A crash in between
leaves the fingerprint unrecorded and the next tick redoes the whole thing,
which is free because the upsert is idempotent. Recorded first, a crash would
mark a file ingested that never was.

`ingest_log` keeps `leads_before` and `leads_after`, so each row says what that
ingest actually added — the number the Master Ref used to record by hand.

A file that parses to **zero usable rows** is not a new version of anything:
`raw.githubusercontent` serving an error page, or a scrape that wrote nothing,
is refused by the same gate (`New CSV?` tests `usable` as well as `is_new`), so
it cannot record an ingest.

**To force a re-ingest** of a file — after a manual `leads` edit, say — delete
its `ingest_log` row.

## Files

| File | Role |
|---|---|
| `code_fingerprint.js` | **Fingerprint CSV** — collapses the file into one item carrying its fingerprint and rows |
| `code_rows.js` | **Rows to Items** — fans the rows back out, one per upsert |
| `build_workflow.py` | Generator, and the home of every guard |

## Tests

Offline, no network and no database:

```powershell
node   n8n/ingestion/test_fingerprint.js    # the hash and the fan-out
python n8n/ingestion/test_drift_guards.py   # 17 cases, each proving a guard fires
```

`test_fingerprint.js` is built around the two opposite failure modes, because
both are silent: a hash too sensitive re-ingests for nothing, and a hash too
insensitive drops a real change on the floor. So there is a case for each column
that **must** change it (a new lead, a renamed company, a moved country) and
each that must **not** (description, phone, address, e-mail, profile URL), plus
the canonicalisation ones — `WWW.CVBF.NET`, `  cvbf.net  ` and `cvbf.net/` are
one domain, because that is what `ON CONFLICT (domain)` thinks too.

`test_drift_guards.py` covers the schedule (back to a fixed slot, an
every-6-hours rule, a pinned minute, an out-of-range interval), the upsert (a
`DO UPDATE` that sets `status` — at a 30-minute tick that would drag every lead
in the file back to `ingested` twice an hour), Section 8's column lists, the
fingerprint/upsert agreement, and the wiring: every `$json.X` a node reads must
be emitted by the node above it. That last one is Workflow 4's
silent-empty-field bug, which here would have upserted 272 blank rows and
reported success.

## Running it by hand

```powershell
docker exec -e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false `
  nova-scout-n8n-1 n8n execute --id ingestionIchgcp01
```

Safe to repeat: the second run finds the fingerprint already recorded and stops
at `New CSV?`.

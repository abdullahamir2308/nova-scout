"""Generates n8n/workflows/ingestion-ichgcp.json -- Workflow 1, ICH GCP ingestion.

    python n8n/ingestion/build_workflow.py

Same contract as every other stage since Sprint 2: the workflow is generated,
never hand-edited, so the JS that was tested standalone is byte-identical to the
JS that ships inside the escaped JSON string. This stage was the last one still
hand-written; it got a generator on 2026-10-07, when its schedule changed.

WHAT CHANGED, 2026-10-07. The trigger was `days` / `triggerAtHour: 23`, which on
n8n 2.35.7 compiles to a cron of `<s> <m> 23 * * *` and fires once a day at 23:xx
Asia/Karachi -- a single fixed slot on a laptop that is off at night, so most
nights it never ran at all. Section 7's idempotency rule says plainly that no
workflow may assume its schedule fired; every other stage obeys that by draining
a queue, and this one has no queue because its input is a file. So the file's own
identity became the queue: the workflow ticks every 30 minutes and does its work
when data/ichgcp_leads.csv has changed since the last ingest (ingest_log,
migration 015). Nothing about the upsert changed.

Guards here, rather than in a comment:

    Section 7   idempotency              -> the trigger may not be a fixed slot
    Section 8   leads columns            -> what the upsert may touch
    Section 8   ingest_log columns       -> what the record may touch
    Section 9   status is never updated  -> re-ingesting cannot regress a lead
    wiring      every $json.X a node reads is emitted by the node above it
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.environ.get("NOVASCOUT_MASTER_REF", os.path.join(REPO, "NovaScout_MasterRef.md"))
OUT = os.environ.get("INGESTION_OUT", os.path.join(REPO, "n8n", "workflows", "ingestion-ichgcp.json"))

WORKFLOW_ID = "ingestionIchgcp01"
WORKFLOW_NAME = "Ingestion - ICH GCP"
PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}

# The committed artefact the GitHub Action writes (Section 9, Workflow 1).
# ichgcp.net 403s this machine, so the scrape runs on GitHub's runners and the
# workflow only ever reads the file they commit.
CSV_URL = ("https://raw.githubusercontent.com/abdullahamir2308/nova-scout/"
           "main/data/ichgcp_leads.csv")

# Every 30 minutes, like Workflow 3, 4 and the digest. Not hours or days: n8n
# 2.35.7 gates an "every N hours" rule (N > 1) on the CLOCK HOUR of the last run
# and an "every N days" rule on the day of the year, so a tick at the same clock
# value reads as no time elapsed -- the bug that cost three follow-ups on
# 2026-10-02 (Section 9, Workflow 6). A minutes rule is counted on absolute
# elapsed minutes and is safe.
TICK_MINUTES = 30


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def js(name):
    return _read(os.path.join(HERE, name))


DOC = _read(MASTER_REF)


def load_columns(doc, table, must):
    """A table's column list out of Section 8's fenced schema block."""
    m = re.search(r"\n%s\r?\n(.*?)\r?\n\r?\n" % re.escape(table), doc, re.S)
    if not m:
        raise AssertionError("%s not found in Section 8 of %s" % (table, MASTER_REF))
    cols = re.findall(r"[a-z_]+", m.group(1).split("**")[0])
    cols = [c for c in cols if c not in ("FK", "PK", "UNIQUE", "bool", "jsonb", "text", "int")]
    if must not in cols:
        raise AssertionError(
            "Section 8's %s block does not name %r -- the parse is wrong, or the "
            "schema changed" % (table, must))
    return cols


LEADS_COLUMNS = load_columns(DOC, "leads", must="domain")
INGEST_COLUMNS = load_columns(DOC, "ingest_log", must="csv_fingerprint")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

CHECK_SQL = """-- Has this version of the CSV already been ingested? (migration 015)
--
-- $1 Fingerprint CSV's `fingerprint`
--
-- One row, always, so New CSV? has something to read whatever the answer is --
-- a Postgres node that returns no rows ends the branch silently, and "nothing
-- happened" would be indistinguishable from "already ingested".
--
-- leads_before is counted HERE, before the upsert, so Record Ingest can say
-- what this ingest actually added rather than what the table holds.
SELECT $1::text                                                        AS fingerprint,
       NOT EXISTS (SELECT 1 FROM ingest_log g WHERE g.csv_fingerprint = $1) AS is_new,
       (SELECT count(*) FROM leads)::int                               AS leads_before,
       (SELECT max(ingested_at) FROM ingest_log)                       AS last_ingest_at;"""

UPSERT_SQL = """INSERT INTO leads (domain, company_name, country, source, status)
VALUES ($1, $2, $3, 'ichgcp', 'ingested')
ON CONFLICT (domain) DO UPDATE SET
  company_name = EXCLUDED.company_name,
  country = EXCLUDED.country,
  updated_at = now()"""

RECORD_SQL = """-- Record that this version of the CSV has been ingested (migration 015).
--
-- $1 the fingerprint   $2 rows in the file   $3 leads before the upsert
--
-- Written AFTER the upsert, never before: a crash in between then leaves the
-- fingerprint unrecorded and the next tick does the whole thing again, which is
-- free (the upsert is idempotent). Recorded first, a crash would mark a file
-- ingested that never was.
--
-- ON CONFLICT DO NOTHING so a re-run of the same file is a no-op rather than an
-- error -- and so the first run cannot be defeated by two ticks overlapping.
INSERT INTO ingest_log (csv_fingerprint, rows_in_csv, leads_before, leads_after)
SELECT $1::text, $2::int, $3::int, (SELECT count(*) FROM leads)::int
ON CONFLICT (csv_fingerprint) DO NOTHING
RETURNING csv_fingerprint, rows_in_csv, leads_before, leads_after,
          leads_after - leads_before AS leads_added;"""

# --- Guard: the upsert touches only Section 8's columns, and never `status` ---
_insert = re.search(r"INSERT INTO leads \(([^)]+)\)", UPSERT_SQL).group(1)
_insert_cols = [c.strip() for c in _insert.split(",")]
_unknown = [c for c in _insert_cols if c not in LEADS_COLUMNS]
if _unknown:
    raise AssertionError(
        "the leads INSERT writes %r, which Section 8's schema does not define. Update the "
        "SQL, not the doc." % _unknown)
_update = re.search(r"DO UPDATE SET\n(.*)", UPSERT_SQL, re.S).group(1)
if re.search(r"^\s*status\s*=", _update, re.M):
    raise AssertionError(
        "the upsert's DO UPDATE sets `status`. Section 9, Workflow 1: 'Only sets status/source "
        "on first INSERT. A lead that has already progressed past ingested is left alone on "
        "re-runs' -- and since 2026-10-07 the workflow ticks every 30 minutes, so this would "
        "drag every lead in the file back to 'ingested' twice an hour.")

_record = re.search(r"INSERT INTO ingest_log \(([^)]+)\)", RECORD_SQL).group(1)
_unknown = [c.strip() for c in _record.split(",") if c.strip() not in INGEST_COLUMNS]
if _unknown:
    raise AssertionError(
        "the ingest_log INSERT writes %r, which Section 8's schema does not define." % _unknown)

# --- Guard: the fingerprint covers what the upsert writes, and no more -------
#
# If the hash took a column the upsert ignores, every scrape that only touched a
# description would trigger a pointless re-ingest. If it MISSED one the upsert
# writes, a real change to that column would never be ingested at all -- the
# worse failure, and a silent one.
_fp = js("code_fingerprint.js")
_hashed = set(re.findall(r"^\s+(domain|company_name|country|source|status|email|phone|address|description):",
                         _fp, re.M))
_written = {c for c in _insert_cols} - {"source", "status"}
if _hashed != _written:
    raise AssertionError(
        "code_fingerprint.js hashes %r but the upsert writes %r. A column in the hash and not "
        "the upsert means pointless re-ingests; a column in the upsert and not the hash means a "
        "real change to it is never ingested." % (sorted(_hashed), sorted(_written)))


def boolean_condition(cid, expr, expect_true):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{
            "id": cid,
            "leftValue": expr,
            "rightValue": expect_true,
            "operator": {"type": "boolean", "operation": "true" if expect_true else "false",
                         "singleValue": True},
        }],
        "combinator": "and",
    }


nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": TICK_MINUTES}]}},
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-760, 40],
        "notes": (
            "Was 'daily at 23:00 Asia/Karachi' until 2026-10-07 -- a single fixed slot, on a "
            "host that is off at night, so most nights it never fired.\n\n"
            "Now it ticks every %d minutes and does its work only when the CSV has changed "
            "since the last ingest (ingest_log, migration 015). A quiet tick is one HTTP GET "
            "of a committed file plus one SELECT. Section 7: 'No workflow may assume its "
            "schedule fired.'\n\n"
            "Minutes, not hours or days: n8n 2.35.7 gates an hours rule on the clock hour of "
            "the last run and a days rule on the day of the year, so a tick at the same clock "
            "value reads as zero time elapsed -- the bug that cost three follow-ups on "
            "2026-10-02." % TICK_MINUTES
        ),
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-760, 220],
    },
    {
        "parameters": {
            "url": CSV_URL,
            "options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}}},
        },
        "name": "Fetch ICH GCP CSV",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-540, 130],
        "notes": (
            "ichgcp.net returns 403 to this machine (Section 9, Workflow 1), so the scrape runs "
            "as a GitHub Action and commits data/ichgcp_leads.csv. This workflow only ever "
            "reads that committed file. n8n/contacts/build_workflow.py parses this URL out of "
            "the shipped JSON so the two stages cannot drift onto different artefacts."
        ),
    },
    {
        "parameters": {"operation": "csv", "binaryPropertyName": "data"},
        "name": "Parse CSV",
        "type": "n8n-nodes-base.extractFromFile",
        "typeVersion": 1.1,
        "position": [-320, 130],
    },
    {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": js("code_fingerprint.js")},
        "name": "Fingerprint CSV",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-100, 130],
        "notes": (
            "Collapses the file into one item carrying a fingerprint of the three columns the "
            "upsert writes -- so the next two nodes each run once, not once per row.\n\n"
            "FNV-1a/64 in BigInt rather than a real digest: the Code sandbox refuses "
            "require('crypto') (measured 2026-10-07) and has no crypto.subtle. Nobody is "
            "forging a collision here; the only question is whether two versions of a file we "
            "published ourselves differ, and the row count rides along in the fingerprint too."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": CHECK_SQL,
            "options": {"queryReplacement": "={{ [$json.fingerprint] }}"},
        },
        "name": "Check Ingest Log",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [120, 130],
        "credentials": PG_CRED,
        "alwaysOutputData": False,
        "notes": (
            "Returns exactly one row whatever the answer, so New CSV? always has something to "
            "read: a Postgres node that returns nothing ends the branch silently, and "
            "'already ingested' would be indistinguishable from 'the query failed'.\n\n"
            "leads_before is counted here, before the upsert, so Record Ingest can report what "
            "this ingest actually added."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition(
                "isnew",
                "={{ $json.is_new && $('Fingerprint CSV').first().json.usable }}",
                True,
            ),
            "options": {},
        },
        "name": "New CSV?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [340, 130],
        "notes": (
            "true: this version of the file has not been ingested, and it has at least one "
            "usable row. false: nothing happens -- the normal outcome of most ticks.\n\n"
            "`usable` is the second half on purpose: raw.githubusercontent serving an error "
            "page, or a scrape that wrote nothing, parses to zero rows, and recording an "
            "ingest for that would mark a non-file as ingested."
        ),
    },
    {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": js("code_rows.js")},
        "name": "Rows to Items",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [560, 40],
        "notes": (
            "Undoes Fingerprint CSV's collapse, one item per row, for the upsert.\n\n"
            "It reads the rows from Fingerprint CSV BY NAME: the Postgres node above replaced "
            "the items with its own result, so $json here is the ingest-log check. That is "
            "Workflow 4's silent-empty-field bug -- n8n resolves a field nobody emitted to an "
            "empty string without erroring -- and it would have upserted 273 blank rows and "
            "reported success."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": UPSERT_SQL,
            "options": {"queryReplacement": "={{ [$json.domain, $json.company_name, $json.country] }}"},
        },
        "name": "Upsert into leads",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.4,
        "position": [780, 40],
        "credentials": PG_CRED,
        "notes": (
            "Only sets status/source on first INSERT. A lead that has already progressed past "
            "'ingested' (enriched/scored/...) is left alone on re-runs -- re-ingesting must "
            "never regress pipeline state, and at a 30-minute tick it would do so twice an "
            "hour.\n\n"
            "ON CONFLICT (domain) DO UPDATE, so a multi-country CRO keeps one row. The CSV "
            "already settles which country that is (Section 9, Workflow 1: core beats "
            "extended, otherwise the country the file already holds wins), so leads.country is "
            "stable across runs even though this statement overwrites it every time.\n\n"
            "Parameters go in as an array, so a company name containing a comma is never "
            "split. typeVersion stays at 2.4 for that reason."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": RECORD_SQL,
            "options": {
                "queryReplacement": (
                    "={{ [$('Fingerprint CSV').first().json.fingerprint, "
                    "$('Fingerprint CSV').first().json.rows_in_csv, "
                    "$('Check Ingest Log').first().json.leads_before] }}"
                )
            },
        },
        "name": "Record Ingest",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [1000, 40],
        "credentials": PG_CRED,
        "executeOnce": True,
        "notes": (
            "executeOnce, because the node above it ran once per row and this is one fact "
            "about the file.\n\n"
            "After the upsert, never before: a crash in between leaves the fingerprint "
            "unrecorded and the next tick redoes the whole thing, which is free. Recorded "
            "first, a crash would mark a file ingested that never was.\n\n"
            "It returns leads_added, which is the number worth reading in the execution log."
        ),
    },
]

connections = {
    "Every 30 Minutes": {"main": [[{"node": "Fetch ICH GCP CSV", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Fetch ICH GCP CSV", "type": "main", "index": 0}]]},
    "Fetch ICH GCP CSV": {"main": [[{"node": "Parse CSV", "type": "main", "index": 0}]]},
    "Parse CSV": {"main": [[{"node": "Fingerprint CSV", "type": "main", "index": 0}]]},
    "Fingerprint CSV": {"main": [[{"node": "Check Ingest Log", "type": "main", "index": 0}]]},
    "Check Ingest Log": {"main": [[{"node": "New CSV?", "type": "main", "index": 0}]]},
    # The false branch is deliberately empty: an unchanged file is the normal
    # outcome of a tick, and it is not an event.
    "New CSV?": {"main": [[{"node": "Rows to Items", "type": "main", "index": 0}], []]},
    "Rows to Items": {"main": [[{"node": "Upsert into leads", "type": "main", "index": 0}]]},
    "Upsert into leads": {"main": [[{"node": "Record Ingest", "type": "main", "index": 0}]]},
}

# --- Guard: the trigger cannot depend on the host being up at one moment -----
for _n in nodes:
    if _n["type"] != "n8n-nodes-base.scheduleTrigger":
        continue
    for _iv in _n["parameters"]["rule"]["interval"]:
        if _iv.get("field") != "minutes":
            raise AssertionError(
                "the %s trigger is %r. Section 7: 'No workflow may assume its schedule fired.' "
                "A days rule with triggerAtHour is one fixed slot a day and the host is off at "
                "night -- that is what this build changed. An hours rule above 1 is gated on "
                "the clock hour of the last run (n8n 2.35.7). Use minutes."
                % (_n["name"], _iv))
        if not 1 <= int(_iv.get("minutesInterval", 5)) <= 59:
            raise AssertionError("a minutes interval must be 1-59, got %r" % _iv)
        for _fixed in ("triggerAtHour", "triggerAtMinute", "triggerAtDay", "triggerAtDayOfMonth"):
            if _fixed in _iv:
                raise AssertionError(
                    "the %s trigger pins %s. A schedule that must catch one moment misses it on "
                    "a host that is off at night." % (_n["name"], _fixed))

# --- Guard: every $json.X a node reads is emitted by the node above it -------
#
# Workflow 4's class of bug: a Postgres node replaces its input items, and n8n
# resolves a field nobody emitted to an empty string without erroring. Nothing
# throws; the workflow reports success and does the wrong thing.
_EMITS = {
    "Fingerprint CSV": {"fingerprint", "rows_in_csv", "usable", "rows"},
    "Check Ingest Log": set(re.findall(r"AS (\w+)", CHECK_SQL)),
    "Rows to Items": {"domain", "company_name", "country"},
}
_BY_NAME = {n["name"]: n for n in nodes}
for _src, _dsts in connections.items():
    for _branch in _dsts["main"]:
        for _edge in _branch:
            _dst = _BY_NAME[_edge["node"]]
            _params = json.dumps(_dst["parameters"])
            _reads = set(re.findall(r"\$json\.(\w+)", _params))
            if not _reads or _src not in _EMITS:
                continue
            _missing = sorted(_reads - _EMITS[_src])
            if _missing:
                raise AssertionError(
                    "%s reads $json.%s, which %s does not emit. n8n would resolve that to an "
                    "empty string and report success."
                    % (_dst["name"], ", $json.".join(_missing), _src))

# Named reads ($('Node').first().json.X) are checked the same way.
for _n in nodes:
    for _node_name, _field in re.findall(r"\$\('([^']+)'\)\.first\(\)\.json\.(\w+)",
                                         json.dumps(_n["parameters"])):
        if _node_name not in _BY_NAME:
            raise AssertionError("%s reads $('%s'), which is not a node here" % (_n["name"], _node_name))
        if _node_name in _EMITS and _field not in _EMITS[_node_name]:
            raise AssertionError(
                "%s reads $('%s').first().json.%s, which %s does not emit"
                % (_n["name"], _node_name, _field, _node_name))

workflow = {
    "id": WORKFLOW_ID,
    "name": WORKFLOW_NAME,
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

out_dir = os.path.dirname(OUT)
if out_dir and not os.path.isdir(out_dir):
    os.makedirs(out_dir)
with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(workflow, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

print("wrote %s (%s)" % (OUT, WORKFLOW_ID))
print("  trigger:    every %d minutes; work happens only when the CSV fingerprint is new" % TICK_MINUTES)
print("  csv:        %s" % CSV_URL)
print("  fingerprint: FNV-1a/64 over %s, sorted by domain" % ", ".join(sorted(_written)))
print("  records:    ingest_log(%s)" % ", ".join(c.strip() for c in _record.split(",")))

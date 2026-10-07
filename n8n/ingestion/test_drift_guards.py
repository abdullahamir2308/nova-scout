"""Checks that n8n/ingestion/build_workflow.py's guards actually FIRE.

    python n8n/ingestion/test_drift_guards.py

A guard that has never been seen to fail is not a guard. Each case copies the
generator and the Master Ref to a scratch directory, introduces exactly one
divergence, and asserts the build refuses.

The two that matter most here:

  * the trigger. This whole stage got a generator because its schedule was one
    fixed slot a day (23:00 Asia/Karachi) on a host that is off at night, so it
    mostly never ran. Any schedule that has to catch one moment must refuse.
  * `status` in the upsert's DO UPDATE. The workflow now ticks every 30 minutes
    rather than once a day; a DO UPDATE that set `status` would drag every lead
    in the file back to 'ingested' twice an hour, undoing the whole pipeline.

Exits non-zero on the first guard that failed to fire.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")

PASSED = []
FAILED = []


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


def scratch(mutate_doc=None, mutate_file=None, label=""):
    tmp = tempfile.mkdtemp(prefix="novascout-ingestion-drift-")
    shutil.copytree(HERE, os.path.join(tmp, "ingestion"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    doc = read(MASTER_REF)
    if mutate_doc:
        new = mutate_doc(doc)
        assert new != doc, "mutation %r did not change the doc" % label
        doc = new
    doc_path = os.path.join(tmp, "MasterRef.md")
    write(doc_path, doc)
    if mutate_file:
        name, fn = mutate_file
        p = os.path.join(tmp, "ingestion", name)
        src = read(p)
        new = fn(src)
        assert new != src, "mutation %r did not change %s" % (label, name)
        write(p, new)
    return tmp, doc_path


def run_build(tmp, doc_path):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["INGESTION_OUT"] = os.path.join(tmp, "out", "ingestion-ichgcp.json")
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen([sys.executable, os.path.join(tmp, "ingestion", "build_workflow.py")],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    _, err = p.communicate()
    return p.returncode, err.decode("utf-8", "replace"), os.path.join(tmp, "out", "ingestion-ichgcp.json")


def case(label, expect_in, mutate_doc=None, mutate_file=None):
    tmp, doc_path = scratch(mutate_doc, mutate_file, label)
    try:
        rc, err, _ = run_build(tmp, doc_path)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but the message lacked %r:\n%s" % (expect_in, err[-500:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def positive(label, check):
    tmp, doc_path = scratch(label=label)
    try:
        rc, err, out = run_build(tmp, doc_path)
        if rc != 0:
            FAILED.append((label, "build refused:\n" + err[-500:]))
            return
        why = check(json.loads(read(out)))
        if why:
            FAILED.append((label, why))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def node(wf, name):
    return [n for n in wf["nodes"] if n["name"] == name][0]


# --- baseline ---------------------------------------------------------------
positive("BASELINE: unmodified build succeeds", lambda wf: None)


def ships_a_catching_up_schedule(wf):
    rule = node(wf, "Every 30 Minutes")["parameters"]["rule"]["interval"]
    if rule != [{"field": "minutes", "minutesInterval": 30}]:
        return "the shipped trigger is %r, not a plain 30-minute interval" % rule
    return None


positive("the shipped trigger is a minutes interval with no fixed slot", ships_a_catching_up_schedule)


def ships_the_gate(wf):
    names = [n["name"] for n in wf["nodes"]]
    for needed in ("Fingerprint CSV", "Check Ingest Log", "New CSV?", "Record Ingest"):
        if needed not in names:
            return "the shipped workflow has no %s node" % needed
    # Record Ingest must run once, not once per CSV row.
    if node(wf, "Record Ingest").get("executeOnce") is not True:
        return "Record Ingest is not executeOnce, so it would run once per row"
    # The upsert is reached only through the gate.
    if wf["connections"]["New CSV?"]["main"][0][0]["node"] != "Rows to Items":
        return "New CSV?'s true branch does not lead to the upsert"
    if wf["connections"]["New CSV?"]["main"][1] != []:
        return "New CSV?'s false branch does something; an unchanged file is not an event"
    return None


positive("the shipped workflow gates the upsert on a new fingerprint", ships_the_gate)


def js_is_byte_identical(wf):
    for name, f in (("Fingerprint CSV", "code_fingerprint.js"), ("Rows to Items", "code_rows.js")):
        if node(wf, name)["parameters"]["jsCode"] != read(os.path.join(HERE, f)):
            return "%s's shipped JS is not byte-identical to %s" % (name, f)
    return None


positive("the shipped JS is byte-identical to the tested files", js_is_byte_identical)

# --- the schedule -----------------------------------------------------------
case(
    "back to a fixed daily slot -> refuses (this is the bug the stage was changed for)",
    "assume its schedule fired",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        '{"field": "minutes", "minutesInterval": TICK_MINUTES}',
        '{"field": "days", "daysInterval": 1, "triggerAtHour": 23}', 1)),
)
case(
    "an 'every 6 hours' rule -> refuses (n8n gates it on the clock hour of the last run)",
    "assume its schedule fired",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        '{"field": "minutes", "minutesInterval": TICK_MINUTES}',
        '{"field": "hours", "hoursInterval": 6}', 1)),
)
case(
    "a minutes rule pinned to one minute of the hour -> refuses",
    "misses it on a host",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        '{"field": "minutes", "minutesInterval": TICK_MINUTES}',
        '{"field": "minutes", "minutesInterval": TICK_MINUTES, "triggerAtMinute": 7}', 1)),
)
case(
    "a minutes interval of 90 -> refuses (n8n validates 1-59 and would throw at runtime)",
    "must be 1-59",
    mutate_file=("build_workflow.py", lambda s: s.replace("TICK_MINUTES = 30", "TICK_MINUTES = 90", 1)),
)

# --- the upsert -------------------------------------------------------------
case(
    "the upsert starts setting status -> refuses",
    "drag every lead in the file back",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        "  company_name = EXCLUDED.company_name,",
        "  status = 'ingested',\n  company_name = EXCLUDED.company_name,", 1)),
)
case(
    "the upsert writes a column Section 8 does not define -> refuses",
    "Section 8's schema does not define",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        "INSERT INTO leads (domain, company_name, country, source, status)",
        "INSERT INTO leads (domain, company_name, country, source, status, email)", 1)),
)
case(
    "the record writes an ingest_log column Section 8 does not define -> refuses",
    "Section 8's schema does not define",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        "INSERT INTO ingest_log (csv_fingerprint, rows_in_csv, leads_before, leads_after)",
        "INSERT INTO ingest_log (csv_fingerprint, rows_in_csv, leads_before, leads_after, notes)", 1)),
)
case(
    "Section 8 drops ingest_log altogether -> refuses loudly",
    "not found in Section 8",
    mutate_doc=lambda d: d.replace(
        "ingest_log\n  csv_fingerprint (PK), ingested_at, rows_in_csv, leads_before, leads_after\n", "", 1),
)

# --- the fingerprint has to cover exactly what the upsert writes ------------
case(
    "the fingerprint stops covering a column the upsert writes -> refuses",
    "never ingested",
    mutate_file=("code_fingerprint.js", lambda s: s.replace(
        "    country: str(r.country),", "", 1)),
)
case(
    "the fingerprint starts covering a column the upsert ignores -> refuses",
    "pointless re-ingests",
    mutate_file=("code_fingerprint.js", lambda s: s.replace(
        "    country: str(r.country),",
        "    country: str(r.country),\n    description: str(r.description),", 1)),
)

# --- the wiring guard (Workflow 4's silent-empty-field class of bug) --------
case(
    "a node reads a field the node above it does not emit -> refuses",
    "report success",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        '"queryReplacement": "={{ [$json.fingerprint] }}"',
        '"queryReplacement": "={{ [$json.csv_fingerprint] }}"', 1)),
)
case(
    "a named read points at a field that node does not emit -> refuses",
    "does not emit",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        "$('Fingerprint CSV').first().json.rows_in_csv, ",
        "$('Fingerprint CSV').first().json.row_count, ", 1)),
)
case(
    "a named read points at a node that is not there -> refuses",
    "is not a node here",
    mutate_file=("build_workflow.py", lambda s: s.replace(
        "$('Check Ingest Log').first().json.leads_before",
        "$('Count Leads').first().json.leads_before", 1)),
)

print("drift guards for Workflow 1 (ingestion)\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why.replace("\n", "\n         "))
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)

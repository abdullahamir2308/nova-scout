"""Checks that build_workflow.py's spec-drift guards actually FIRE.

A guard that has never been seen to fail is not a guard. Each case here takes a
real copy of the Master Ref (or of the shipped JS, or of the ingestion workflow),
introduces exactly one divergence, and asserts the build refuses to run.

    python n8n/contacts/test_drift_guards.py

Exits non-zero on the first guard that failed to fire.

Workflow 3b's guards protect spend, not just correctness. The score gate (>= 50
since 2026-10-06, >= 60 before) and the status='scored' filter are the whole of
what keeps Apollo on the free tier; if either drifts without a build failure,
the first symptom is a bill. The expected value below is the literal number in
Section 9's gate sentence, so changing the doc means changing it here too.
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
INGESTION = os.path.join(REPO, "n8n", "workflows", "ingestion-ichgcp.json")

PASSED = []
FAILED = []


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


def run_build(workdir, doc_path, ingestion_path):
    """Run the generator against a scratch copy, return (rc, stderr)."""
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["NOVASCOUT_INGESTION"] = ingestion_path
    env["CONTACTS_OUT"] = os.path.join(workdir, "out", "apollo-contacts.json")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(workdir, "contacts", "build_workflow.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    _, err = proc.communicate()
    return proc.returncode, err.decode("utf-8", "replace")


def _scratch(mutate_doc, mutate_js, js_file, mutate_ingestion, label):
    tmp = tempfile.mkdtemp(prefix="novascout-contacts-drift-")
    shutil.copytree(HERE, os.path.join(tmp, "contacts"))

    doc_path = os.path.join(tmp, "MasterRef.md")
    doc = read(MASTER_REF)
    if mutate_doc:
        new_doc = mutate_doc(doc)
        assert new_doc != doc, "mutation %r did not change the doc" % label
        doc = new_doc
    write(doc_path, doc)

    ing_path = os.path.join(tmp, "ingestion-ichgcp.json")
    ing = read(INGESTION)
    if mutate_ingestion:
        new_ing = mutate_ingestion(ing)
        assert new_ing != ing, "mutation %r did not change the ingestion workflow" % label
        ing = new_ing
    write(ing_path, ing)

    if mutate_js:
        p = os.path.join(tmp, "contacts", js_file)
        src = read(p)
        new_src = mutate_js(src)
        assert new_src != src, "mutation %r did not change %s" % (label, js_file)
        write(p, new_src)

    return tmp, doc_path, ing_path


def case(label, mutate_doc=None, mutate_js=None, js_file="code_pick_contact.js",
         mutate_ingestion=None, expect_in=""):
    """Apply one mutation to a scratch copy and assert the build refuses."""
    tmp, doc_path, ing_path = _scratch(mutate_doc, mutate_js, js_file, mutate_ingestion, label)
    try:
        rc, err = run_build(tmp, doc_path, ing_path)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in and expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but message lacked %r:\n%s" % (expect_in, err[-400:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- the build must succeed unmodified, or every case below is meaningless --
def baseline():
    tmp, doc_path, ing_path = _scratch(None, None, None, None, "baseline")
    try:
        rc, err = run_build(tmp, doc_path, ing_path)
        if rc != 0:
            FAILED.append(("BASELINE: unmodified build succeeds", err[-600:]))
        else:
            PASSED.append("BASELINE: unmodified build succeeds")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


baseline()


# --- the Apollo score gate -------------------------------------------------
#
# The single number that keeps Apollo on the free tier. It is generated into the
# workflow's Config node, so a doc change must reach the shipped workflow.
def _threshold_in_build(doc_mutator):
    """Build against a mutated doc and read back the generated threshold."""
    tmp, doc_path, ing_path = _scratch(doc_mutator, None, None, None, "threshold")
    try:
        rc, err = run_build(tmp, doc_path, ing_path)
        if rc != 0:
            return None, err
        out = os.path.join(tmp, "out", "apollo-contacts.json")
        wf = json.loads(read(out))
        cfg = [n for n in wf["nodes"] if n["name"] == "Config"][0]
        vals = {a["name"]: a["value"] for a in cfg["parameters"]["assignments"]["assignments"]}
        return vals.get("min_fit_score"), err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_base_threshold, _ = _threshold_in_build(None)
if _base_threshold != 50:
    FAILED.append(("Section 9's >= 50 gate reaches Config",
                   "generated min_fit_score was %r, expected 50" % (_base_threshold,)))
else:
    PASSED.append("Section 9's >= 50 gate reaches Config")

_moved, _ = _threshold_in_build(
    lambda d: d.replace("Apollo lookup for leads scoring ≥ 50 only",
                        "Apollo lookup for leads scoring ≥ 45 only", 1)
)
if _moved != 45:
    FAILED.append(("moving the gate in the doc moves it in the workflow",
                   "generated min_fit_score was %r, expected 45" % (_moved,)))
else:
    PASSED.append("moving the gate in the doc moves it in the workflow")

case(
    "the Apollo gate sentence is reworded away -> build refuses loudly",
    mutate_doc=lambda d: d.replace(
        "**Then** Apollo lookup for leads scoring ≥ 50 only.",
        "**Then** look up contacts for the good ones.", 1),
    expect_in="apollo score gate not found",
)

# --- the contacts schema ---------------------------------------------------
case(
    "doc adds a contacts column the INSERT does not write -> build refuses",
    mutate_doc=lambda d: d.replace(
        "  apollo_id, verified (bool)",
        "  apollo_id, verified (bool), phone", 1),
    expect_in="no longer matches section 8",
)
case(
    "doc drops a contacts column the INSERT still writes -> build refuses",
    mutate_doc=lambda d: d.replace(
        "  id, lead_id FK, name, title, email, linkedin_url,\n  apollo_id, verified (bool)",
        "  id, lead_id FK, name, title, email,\n  apollo_id, verified (bool)", 1),
    expect_in="no longer matches section 8",
)
case(
    "the contacts block disappears from Section 8 -> build refuses loudly",
    mutate_doc=lambda d: d.replace("\ncontacts\n", "\ncontacts_v2\n", 1),
    expect_in="contacts table not found",
)

# --- the ICP buyer roles ---------------------------------------------------
case(
    "the ranker stops covering 'founder' -> build refuses",
    mutate_js=lambda s: s.replace(
        "['founder', 'co-founder', 'cofounder', 'owner', 'proprietor']",
        "['co-founder', 'cofounder', 'owner', 'proprietor']", 1),
    expect_in="no longer covers the icp role 'founder'",
)
case(
    "the ranker stops covering 'managing director' -> build refuses",
    mutate_js=lambda s: s.replace("'managing director', ", "", 1),
    expect_in="no longer covers the icp role 'managing director'",
)
case(
    "the ranker stops covering business development -> build refuses",
    mutate_js=lambda s: s.replace("['business development', ", "['bizdev', ", 1),
    expect_in="no longer covers business development",
)
case(
    "the doc reorders the buyer roles -> build refuses rather than silently reranking",
    mutate_doc=lambda d: d.replace(
        "**Target:** CRO founder, Managing Director, or BD Director.",
        "**Target:** CRO BD Director, Managing Director, or founder.", 1),
    expect_in="reordered the buyer roles",
)
case(
    "the Section 12 target line is renamed -> build refuses loudly",
    mutate_doc=lambda d: d.replace(
        "**Target:** CRO founder, Managing Director, or BD Director.",
        "**Buyer:** CRO founder, Managing Director, or BD Director.", 1),
    expect_in="icp target line not found",
)
case(
    "TITLE_TIERS collapses below four tiers -> build refuses",
    mutate_js=lambda s: s.replace(
        "  ['business development', 'bd director', 'bd manager', 'commercial director'],\n", "", 1),
    expect_in="title tiers",
)

# --- the shared CSV artefact -----------------------------------------------
# This one is not a refusal. Moving the CSV in ingestion must MOVE it here, which
# is the point of reading the URL instead of restating it -- a build that refused
# would just be a second place to edit.
def _csv_url_in_build(ingestion_mutator):
    tmp, doc_path, ing_path = _scratch(None, None, None, ingestion_mutator, "csv url")
    try:
        rc, err = run_build(tmp, doc_path, ing_path)
        if rc != 0:
            return None, err
        wf = json.loads(read(os.path.join(tmp, "out", "apollo-contacts.json")))
        return [n["parameters"]["url"] for n in wf["nodes"] if n["name"] == "Fetch ICH GCP CSV"][0], err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_moved_csv, _err = _csv_url_in_build(
    lambda s: s.replace("/main/data/ichgcp_leads.csv", "/main/data/ichgcp_leads_v2.csv", 1)
)
if _moved_csv is None or not _moved_csv.endswith("ichgcp_leads_v2.csv"):
    FAILED.append(("moving the CSV in ingestion moves it here too",
                   "generated URL was %r\n%s" % (_moved_csv, _err[-300:])))
else:
    PASSED.append("moving the CSV in ingestion moves it here too")

# And this proves the shipped workflow points at the same artefact Workflow 1
# writes -- the guard that matters, since an address list for a different lead
# set would silently skip the wrong Apollo calls.
_wf = json.loads(read(os.path.join(REPO, "n8n", "workflows", "apollo-contacts.json")))
_ing = json.loads(read(INGESTION))
_contacts_url = [n["parameters"]["url"] for n in _wf["nodes"] if n["name"] == "Fetch ICH GCP CSV"][0]
_ing_url = [
    n["parameters"]["url"] for n in _ing["nodes"]
    if n["type"].endswith("httpRequest") and n["parameters"].get("url", "").endswith(".csv")
][0]
if _contacts_url != _ing_url:
    FAILED.append(("the shipped CSV URL matches ingestion's",
                   "%r != %r" % (_contacts_url, _ing_url)))
else:
    PASSED.append("the shipped CSV URL matches ingestion's")

case(
    "ingestion stops publishing a CSV at all -> build refuses loudly",
    mutate_ingestion=lambda s: s.replace("ichgcp_leads.csv", "ichgcp_leads.parquet", 1),
    expect_in="expected exactly one .csv fetch",
)

# --- the starvation fix (migration 015) -------------------------------------
#
# The guards that matter most here are the ones that keep the queue moving. A
# lead Apollo will never answer for has to LEAVE the queue; if it does not, it
# occupies a slot in every batch of 10 for ever and new leads are never reached.
# Nine leads were in exactly that state on 2026-10-07.
case(
    "the batch query stops excluding retired leads -> build refuses",
    mutate_js=lambda s: s.replace("FROM contact_attempts a", "FROM contacts a", 1),
    js_file="build_workflow.py",
    expect_in="occupies a slot in every batch",
)
case(
    "the queue keeps the flag but drops the attempt count -> build refuses",
    mutate_js=lambda s: s.replace("OR a.attempts >= $3", "", 1),
    js_file="build_workflow.py",
    expect_in="cannot be defeated by an unfamiliar error shape",
)
case(
    "a permanent refusal stops being told apart from a transient one -> build refuses",
    mutate_js=lambda s: s.replace("isPlanGate", "planGateCheck"),
    expect_in="isPlanGate",
)
case(
    "the refusal branch stops recording the attempt -> build refuses",
    mutate_js=lambda s: s.replace("attempt_row: {", "unused_row: {", 1),
    expect_in="attempt_row",
)
case(
    "a reached lead stops clearing its earlier record -> build refuses",
    mutate_js=lambda s: s.replace("attempt: null,", "", 1),
    js_file="code_payload_scrape.js",
    expect_in="waiting for a human",
)
case(
    "the write statement stops recording the tombstone's attempt -> build refuses",
    mutate_js=lambda s: s.replace("p->'attempt'->>'outcome'", "p->>'outcome'", 1),
    js_file="build_workflow.py",
    expect_in="one statement",
)
case(
    "'exhausted' stops being sticky, so a blip could re-queue a retired lead -> build refuses",
    mutate_js=lambda s: s.replace("ELSE EXCLUDED.last_outcome END," + chr(10) + "  detail",
                                  "ELSE 'refused' END," + chr(10) + "  detail", 1),
    js_file="build_workflow.py",
    expect_in="sticky",
)

# --- the site-published source ----------------------------------------------
case(
    "the same-domain rule disappears -> build refuses",
    mutate_js=lambda s: s.replace("onOwnDomain", "anyDomain"),
    js_file="code_site_emails.js",
    expect_in="another company",
)
case(
    "the never-a-contact list disappears -> build refuses",
    mutate_js=lambda s: s.replace("NEVER", "ALLOWED"),
    js_file="code_site_emails.js",
    expect_in="careers@",
)
case(
    "the mailto: half of 'literally on the page' disappears -> build refuses",
    mutate_js=lambda s: s.replace("MAILTO_RE", "HREF_RE"),
    js_file="code_site_emails.js",
    expect_in="literally on the page",
)
case(
    "Resolve Contact stops reading the harvest -> build refuses",
    mutate_js=lambda s: s.replace("site_email", "unused_email"),
    js_file="code_resolve.js",
    expect_in="nothing uses the answer",
)
case(
    "a published address stops being written verified -> build refuses",
    mutate_js=lambda s: s.replace("""        apollo_id: null,
        verified: true,
      },
      evidence:
        'email published""", """        apollo_id: null,
        verified: false,
      },
      evidence:
        'email published""", 1),
    js_file="code_resolve.js",
    expect_in="IS confirmed",
)

# --- the schedule must survive a host that is off at night ------------------
case(
    "Contact Lookup moved to 'every 6 hours' -> build refuses (n8n's clock-hour check)",
    mutate_js=lambda s: s.replace('{"field": "hours", "hoursInterval": 1}',
                                  '{"field": "hours", "hoursInterval": 6}', 1),
    js_file="build_workflow.py",
    expect_in="misses it on a host",
)
case(
    "Contact Lookup moved to a fixed daily slot -> build refuses",
    mutate_js=lambda s: s.replace('{"field": "hours", "hoursInterval": 1}',
                                  '{"field": "days", "daysInterval": 1, "triggerAtHour": 23}', 1),
    js_file="build_workflow.py",
    expect_in="misses it on a host",
)
case(
    "Contact Lookup pinned to one minute of the hour -> build refuses",
    mutate_js=lambda s: s.replace('{"field": "hours", "hoursInterval": 1}',
                                  '{"field": "hours", "hoursInterval": 1, "triggerAtMinute": 30}', 1),
    js_file="build_workflow.py",
    expect_in="misses it on a host",
)

print("Workflow 3b spec-drift guards\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why.replace("\n", "\n         "))
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)

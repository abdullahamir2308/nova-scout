"""Checks that build_workflow.py's spec-drift guards actually FIRE.

A guard that has never been seen to fail is not a guard. Each case here takes a
real copy of the Master Ref, of code_template.js or of the build script itself,
introduces exactly one divergence, and asserts the build refuses to run.

    python n8n/drafting/test_drift_guards.py

Since 2026-10-19 Workflow 4 writes the operator's approved template and nothing
else, so the cases worth reading twice are the wording ones: a word changed in
the doc or in the code, and the build stops until both say the same thing --
plus the sender guard (a template signed by anyone but the sender Send checks
for would be refused at send time, or sent under the wrong name).

Exits non-zero on the first guard that failed to fire.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BS = chr(92)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")

PASSED = []
FAILED = []

# The template carries the sender's name and title as approved text, so every
# case builds as that sender. The cases must not depend on a gitignored .env
# existing, so NOVASCOUT_ENV_FILE points at a path that is never created.
FIXTURE_SENDER = "Abdullah Amir"
FIXTURE_TITLE = "Founder, Amitrix Labs"


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


def run_build(workdir, doc_path, env_overrides=None):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["DRAFTING_OUT"] = os.path.join(workdir, "out", "drafting.json")
    env["NOVASCOUT_ENV_FILE"] = os.path.join(workdir, "no-such.env")
    env["NOVASCOUT_SENDER_NAME"] = FIXTURE_SENDER
    env["NOVASCOUT_SENDER_TITLE"] = FIXTURE_TITLE
    env["PYTHONIOENCODING"] = "utf-8"
    for k, v in (env_overrides or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    proc = subprocess.Popen(
        [sys.executable, os.path.join(workdir, "drafting", "build_workflow.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    _, err = proc.communicate()
    return proc.returncode, err.decode("utf-8", "replace")


def staged(mutate_doc=None, mutate_files=None):
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    shutil.copytree(HERE, os.path.join(tmp, "drafting"), ignore=shutil.ignore_patterns("__pycache__"))
    doc_path = os.path.join(tmp, "MasterRef.md")
    doc = read(MASTER_REF)
    if mutate_doc:
        new_doc = mutate_doc(doc)
        assert new_doc != doc, "a doc mutation did not change the doc"
        doc = new_doc
    write(doc_path, doc)
    for name, fn in (mutate_files or {}).items():
        p = os.path.join(tmp, "drafting", name)
        src = read(p)
        new = fn(src)
        assert new != src, "a mutation did not change %s" % name
        write(p, new)
    return tmp, doc_path


def case(label, expect_in, mutate_doc=None, mutate_js=None, mutate_build=None, env_overrides=None):
    files = {}
    if mutate_js:
        files["code_template.js"] = mutate_js
    if mutate_build:
        files["build_workflow.py"] = mutate_build
    tmp, doc_path = staged(mutate_doc, files)
    try:
        rc, err = run_build(tmp, doc_path, env_overrides)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but message lacked %r:\n%s" % (expect_in, err[-500:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def one(src, old, new):
    assert src.count(old) == 1, "anchor %r occurs %d times" % (old, src.count(old))
    return src.replace(old, new)


def shipped(env_overrides=None, mutate_doc=None):
    """Build an unmodified copy and return (rc, err, workflow)."""
    tmp, doc_path = staged(mutate_doc)
    try:
        rc, err = run_build(tmp, doc_path, env_overrides)
        wf = json.loads(read(os.path.join(tmp, "out", "drafting.json"))) if rc == 0 else None
        return rc, err, wf
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- baseline -------------------------------------------------------------------

rc, err, WF = shipped()
if rc != 0:
    FAILED.append(("BASELINE: unmodified build succeeds", err[-600:]))
else:
    PASSED.append("BASELINE: unmodified build succeeds")
    names = [n["name"] for n in WF["nodes"]]
    if names == ["Every 30 Minutes", "Manual Trigger", "Config", "Get Draft Batch", "ClinicalTrials.gov Lookup",
                 "Build First Touch", "Drop Failed Lookups", "Write Drafts & Advance"]:
        PASSED.append("BASELINE: the shipped workflow is the template chain, no model node")
    else:
        FAILED.append(("BASELINE: the shipped workflow is the template chain, no model node", repr(names)))
    if "anthropic" in json.dumps(WF).lower():
        FAILED.append(("BASELINE: nothing in the shipped workflow names Anthropic", "it does"))
    else:
        PASSED.append("BASELINE: nothing in the shipped workflow names Anthropic")

# --- the sender ---------------------------------------------------------------------

case("no sender name anywhere -> refuses (there is no default)", "NOVASCOUT_SENDER_NAME is not set",
     env_overrides={"NOVASCOUT_SENDER_NAME": None})
case("the sender is someone else -> refuses (Send would refuse every draft)", "signs",
     env_overrides={"NOVASCOUT_SENDER_NAME": "Fatima"})
case("the sender's title differs from the template's -> refuses", "signs",
     env_overrides={"NOVASCOUT_SENDER_TITLE": "CEO, Amitrix Labs"})


def env_file_supplies_sender():
    label = ".env supplies the sender name and title when the environment does not"
    tmp, doc_path = staged()
    try:
        env_file = os.path.join(tmp, "test.env")
        write(env_file, '# other keys are ignored\nOTHER=1\nNOVASCOUT_SENDER_NAME="Abdullah Amir"\n'
                        "NOVASCOUT_SENDER_TITLE=Founder, Amitrix Labs\n")
        rc, err = run_build(tmp, doc_path, {"NOVASCOUT_SENDER_NAME": None, "NOVASCOUT_SENDER_TITLE": None,
                                            "NOVASCOUT_ENV_FILE": env_file})
        if rc == 0:
            PASSED.append(label)
        else:
            FAILED.append((label, "build refused:\n" + err[-400:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


env_file_supplies_sender()

# --- the approved wording, doc side and code side ---------------------------------------

INTRO = "We have yet to be properly introduced, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs."
case("the doc's introduction changes and the JS does not follow -> refuses", "drifted",
     mutate_doc=lambda d: one(d, "\n" + INTRO + "\n", "\nWe have not met yet, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs.\n"))
case("the JS pitch changes and the doc does not -> refuses", "drifted",
     mutate_js=lambda s: one(s, "so you win more studies.\";", "so you win more trials.\";"))
case("arm B's close changes in the JS -> refuses", "drifted",
     mutate_js=lambda s: one(s, 'const CLOSE_B = "Worth a look for {company}?";', 'const CLOSE_B = "Worth a look?";'))
case("the subject changes in the doc -> refuses", "drifted",
     mutate_doc=lambda d: one(d, "Subject: {company} + {fact} + sponsor inquiries\n", "Subject: {company} + {fact}\n"))
case("an SMYKM sentence changes in the doc -> refuses", "drifted",
     mutate_doc=lambda d: one(d, "areas B: A sponsor planning a {area1} study will usually check",
                              "areas B: A sponsor planning a {area1} study will check"))
case("the subject's trial fact changes in the JS -> refuses", "drifted",
     mutate_js=lambda s: one(s, 'const SUBJECT_FACT_TRIALS = "{n} recruiting trial(s)";',
                             'const SUBJECT_FACT_TRIALS = "{n} trial(s)";'))
case("follow-up #2 changes in the doc -> refuses (Drafting owns the constants)", "drifted",
     mutate_doc=lambda d: one(d, "and won't follow up again after this.", "and won't write again."))
case("the sign-off gains a phone line in the JS -> refuses", "drifted",
     mutate_js=lambda s: one(s, 'const SIGN_OFF = ["Cheers!", "Abdullah Amir", "Founder, Amitrix Labs"];',
                             'const SIGN_OFF = ["Cheers!", "Abdullah Amir", "Founder, Amitrix Labs", "+923178485713"];'))
case("the doc loses the first-touch template heading -> refuses loudly", "not found",
     mutate_doc=lambda d: one(d, "**First-touch template — LOCKED", "**The first touch — LOCKED"))
case("a template constant stops being a plain string -> refuses", "no longer declares",
     mutate_js=lambda s: one(s, 'const PITCH_LINE = "', "const PITCH_LINE = 'x' + \""))
case("the doc stops saying 'Hi there,' for a role inbox -> refuses", "Hi there",
     mutate_doc=lambda d: d.replace('"Hi there,"', '"Hello,"'))

# --- the rules that pick the arm and the fact ---------------------------------------------

case("the doc flips the arms and the JS does not follow -> refuses", "selection rules drifted",
     mutate_doc=lambda d: one(d, "Two arms, assigned by lead_id: even = A, odd = B.", "Two arms, assigned by lead_id: even = B, odd = A."))
case("the JS reorders the SMYKM facts -> refuses", "selection rules drifted",
     mutate_js=lambda s: one(s, "const SMYKM_ORDER = ['trials', 'areas', 'city'];", "const SMYKM_ORDER = ['areas', 'trials', 'city'];"))
case("the doc allows three areas -> refuses", "selection rules drifted",
     mutate_doc=lambda d: one(d, 'At most two areas, lowercase, joined with "and"', 'At most three areas, lowercase, joined with "and"'))
case("the doc loses the arm sentence -> refuses loudly", "no longer states",
     mutate_doc=lambda d: one(d, "Two arms, assigned by lead_id: even = A, odd = B.", "Two arms."))
case("the doc brings a composing model back ('No model call' gone) -> refuses", "No model call",
     mutate_doc=lambda d: d.replace("**No model call** in Drafting", "A model call in Drafting"))
case("the doc's taxonomy gains an area the JS does not have -> refuses", "taxonomy drifted",
     mutate_doc=lambda d: one(d, "\nHematology\nOther\n", "\nHematology\nUrology\nOther\n"))

# --- the code side: no model, the shared rules stay pure, the wiring ----------------------

case("code_template.js builds a model request -> refuses", "builds a model request",
     mutate_js=lambda s: one(s, "const AUTO_APPROVE_OFF = ", "const URL_X = 'https://api.anthropic.com/v1/messages';\nconst AUTO_APPROVE_OFF = "))
case("node-body code moves above the 'Node body' marker -> refuses", "node-body code has moved",
     mutate_js=lambda s: one(s, "function str(v) {", "const early = $input;\nfunction str(v) {"))
case("the clock placeholder disappears from code_template.js -> refuses", "exactly once",
     mutate_js=lambda s: one(s, "const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;", "const SEND_CLOCK_COUNTRIES = [];"))
case("Build First Touch reads a column Get Draft Batch does not return -> refuses", "returns no such column",
     mutate_js=lambda s: one(s, "  verified: lead.verified === true,", "  verified: lead.email_verified === true,"))
case("Build First Touch stops emitting the payload Write Drafts & Advance sends -> refuses", "does not emit",
     mutate_js=lambda s: one(s, "    payload: {\n      lead_id: lead.lead_id,", "    body_payload: {\n      lead_id: lead.lead_id,"))
case("Write Drafts & Advance reads a draft field Build First Touch never sets -> refuses", "does not set",
     mutate_build=lambda s: one(s, "         nullif(d->>'hold_reason', '')\n",
                                "         nullif(d->>'hold_reason', '') || coalesce(d->>'reviewed_by', '')\n"))
case("the CT.gov lookup stops returning the sponsors -> refuses (totalCount is a full-text match)", "sponsors",
     mutate_build=lambda s: one(s, '{"name": "fields", "value": "NCTId,LeadSponsorName,CollaboratorName"}',
                                '{"name": "fields", "value": "NCTId"}'))
case("Build First Touch prints totalCount instead of the verified count -> refuses", "company's own trials",
     mutate_js=lambda s: one(s, "const facts = leadFacts(lead, verifiedTrials(lead.company_name, ct));",
                             "const facts = leadFacts(lead, ct.totalCount);"))
case("the batch query stops skipping sites and SMOs -> refuses", "skips sites",
     mutate_build=lambda s: one(s, "   AND ct.company_type = 'CRO'\n", "\n"))
case("Drafting reads the claims library again -> refuses", "claims library",
     mutate_build=lambda s: one(s, "       auto_approve_email_enabled() AS auto_approve_email\n",
                                "       auto_approve_email_enabled() AS auto_approve_email,\n"
                                "       (SELECT count(*) FROM claims_library) AS library_rows\n"))
case("Write Drafts & Advance would insert a LinkedIn draft -> refuses", "email drafts",
     mutate_build=lambda s: one(s, "   WHERE d->>'channel' = 'email'\n", "\n"))
case("Write Drafts & Advance would retire LinkedIn drafts -> refuses", "email drafts",
     mutate_build=lambda s: one(s, "     AND d.channel = 'email'\n     AND d.status IN ('pending', 'approved')\n",
                                "     AND d.status IN ('pending', 'approved')\n"))
case("the drafts INSERT writes a column Section 8 does not define -> refuses", "Section 8",
     mutate_doc=lambda d: one(d, "  approved_by (auto|human), approved_at, hold_reason, claim_check jsonb",
                              "  approved_by (auto|human), approved_at, claim_check jsonb"))
case("a node carries the Anthropic credential -> refuses", "Anthropic credential",
     mutate_build=lambda s: one(s, '        "name": "Build First Touch",\n',
                                '        "name": "Build First Touch",\n'
                                '        "credentials": {"anthropicApi": {"id": "novascoutAnthropic01", "name": "x"}},\n'))
case("Drafting goes to 'every 6 hours' -> refuses (n8n's clock-hour recurrence check)", "recurrenceCheck",
     mutate_build=lambda s: one(s, '{"field": "minutes", "minutesInterval": 30}', '{"field": "hours", "hoursInterval": 6}'))
case("Drafting is pinned to a fixed hour -> refuses", "pins triggerAtHour",
     mutate_build=lambda s: one(s, '{"field": "minutes", "minutesInterval": 30}', '{"field": "days", "daysInterval": 1, "triggerAtHour": 9}'))


def clock_list_flows():
    """The positive half of the clock guard: drop a country from Section 12's
    table and the shipped gate's list follows -- it is read, not restated."""
    label = "dropping a country from the clock table changes what ships"
    rc, err, wf = shipped(mutate_doc=lambda d: one(d, "| Germany | UTC+1:00 | Sat-Sun | |\n", ""))
    if rc != 0:
        FAILED.append((label, "build refused:\n" + err[-400:]))
        return
    code = [n for n in wf["nodes"] if n["name"] == "Build First Touch"][0]["parameters"]["jsCode"]
    m = re.search("const SEND_CLOCK_COUNTRIES = (" + BS + "[[^" + BS + "]]*" + BS + "]);", code)
    if not m:
        FAILED.append((label, "Build First Touch carries no clock list"))
        return
    names = json.loads(m.group(1))
    if "Germany" in names or "India" not in names:
        FAILED.append((label, "the shipped list still has Germany, or lost India: %r" % names[:5]))
        return
    PASSED.append(label + " -- it is read, not restated")


clock_list_flows()

print("drift guards for Workflow 4\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    sys.exit(1)

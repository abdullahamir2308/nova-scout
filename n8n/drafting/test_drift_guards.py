"""Checks that build_workflow.py's spec-drift guards actually FIRE.

A guard that has never been seen to fail is not a guard. Each case here takes a
real copy of the Master Ref (or of a Code-node source), introduces exactly one
divergence, and asserts the build refuses to run.

    python n8n/drafting/test_drift_guards.py

Workflow 4's guards matter more than the others'. A scoring drift produces a
wrong number in a table someone can re-derive; a drafting drift produces a
sentence in a stranger's inbox, sent under a real person's name, that nobody can
un-send. The two cases worth reading twice are the opt-out sentence (Section 5
makes it the entire GDPR/KVKK opt-out mechanism) and the fact vocabulary (widen
it and the grounding guard passes everything while still looking correct).

Exits non-zero on the first guard that failed to fire.
"""
import io
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


def run_build(workdir, doc_path):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["DRAFTING_OUT"] = os.path.join(workdir, "out", "drafting.json")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(workdir, "drafting", "build_workflow.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    _, err = proc.communicate()
    return proc.returncode, err.decode("utf-8", "replace")


def case(label, mutate_doc=None, mutate_js=None, js_file="code_assess.js", expect_in=""):
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        doc_path = os.path.join(tmp, "MasterRef.md")
        doc = read(MASTER_REF)
        if mutate_doc:
            new_doc = mutate_doc(doc)
            assert new_doc != doc, "mutation %r did not change the doc" % label
            doc = new_doc
        write(doc_path, doc)

        if mutate_js:
            p = os.path.join(tmp, "drafting", js_file)
            src = read(p)
            new_src = mutate_js(src)
            assert new_src != src, "mutation %r did not change %s" % (label, js_file)
            write(p, new_src)

        rc, err = run_build(tmp, doc_path)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in and expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but message lacked %r:\n%s" % (expect_in, err[-400:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def baseline():
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        doc_path = os.path.join(tmp, "MasterRef.md")
        write(doc_path, read(MASTER_REF))
        rc, err = run_build(tmp, doc_path)
        if rc != 0:
            FAILED.append(("BASELINE: unmodified build succeeds", err[-600:]))
        else:
            PASSED.append("BASELINE: unmodified build succeeds")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


baseline()

# --- the grounding threshold ------------------------------------------------
case(
    "doc raises the fact threshold -> build refuses",
    mutate_doc=lambda d: d.replace(
        "lacks at least two specific facts", "lacks at least three specific facts", 1),
    expect_in="grounding threshold",
)
case(
    "JS lowers MIN_FACTS below the doc -> build refuses",
    mutate_js=lambda s: s.replace("const MIN_FACTS = 2;", "const MIN_FACTS = 1;", 1),
    expect_in="grounding threshold",
)
case(
    "the grounding-guard sentence is reworded past recognition -> build refuses loudly",
    mutate_doc=lambda d: d.replace(
        "lacks at least two specific facts", "has insufficient specific facts", 1),
    expect_in="grounding threshold not found",
)

# --- the fact vocabulary ----------------------------------------------------
#
# The failure this pair guards against is the quiet one: widen the vocabulary
# and every lead clears a two-fact threshold, so the guard keeps passing and
# stops meaning anything.
case(
    "doc adds a fact category the JS cannot collect -> build refuses",
    mutate_doc=lambda d: d.replace(
        "(therapeutic area, named trial, city, founder name)",
        "(therapeutic area, named trial, city, founder name, employee count)", 1),
    expect_in="fact vocabulary",
)
case(
    "doc removes a fact category the JS still collects -> build refuses",
    mutate_doc=lambda d: d.replace(
        "(therapeutic area, named trial, city, founder name)",
        "(therapeutic area, named trial, city)", 1),
    expect_in="fact vocabulary",
)
case(
    "JS invents a fact category the doc does not license -> build refuses",
    mutate_js=lambda s: s.replace(
        "kind: 'city',", "kind: 'site_quality',", 1),
    expect_in="fact vocabulary",
)
case(
    "the missing-fact report drifts from the doc's list -> build refuses",
    mutate_js=lambda s: s.replace(
        "const missing = ['therapeutic_area', 'named_trial', 'city', 'founder_name']",
        "const missing = ['therapeutic_area', 'named_trial', 'city']", 1),
    expect_in="missing-fact report",
)

# --- the word ceiling -------------------------------------------------------
case(
    "doc raises the word ceiling -> build refuses",
    mutate_doc=lambda d: d.replace("Under 80 words for first touch.",
                                   "Under 120 words for first touch.", 1),
    expect_in="more than one word ceiling",
)
case(
    "doc's two statements of the ceiling disagree -> build refuses rather than guessing",
    mutate_doc=lambda d: d.replace("**Prompt constraints:** under 80 words",
                                   "**Prompt constraints:** under 100 words", 1),
    expect_in="more than one word ceiling",
)
case(
    "JS raises MAX_WORDS above the doc -> build refuses",
    mutate_js=lambda s: s.replace("const MAX_WORDS = 80;", "const MAX_WORDS = 150;", 1),
    expect_in="word ceiling",
)
case(
    "the assembler's ceiling drifts from the doc -> build refuses",
    mutate_js=lambda s: s.replace("const MAX_WORDS = 80;", "const MAX_WORDS = 150;", 1),
    js_file="code_assemble.js",
    expect_in="word ceiling",
)

# --- the opt-out sentence: Section 5's compliance mechanism ------------------
case(
    "the opt-out sentence is paraphrased in the JS -> build refuses",
    mutate_js=lambda s: s.replace(
        "reply 'no' and I won't follow up.", "reply 'no' and I will not follow up.", 1),
    js_file="code_assemble.js",
    expect_in="verbatim",
)
case(
    "even a punctuation change to the opt-out is refused",
    mutate_js=lambda s: s.replace(
        "reply 'no' and I won't follow up.", "reply 'no' and I won't follow up!", 1),
    js_file="code_assemble.js",
    expect_in="verbatim",
)
case(
    "the doc changes the opt-out and the JS does not follow -> build refuses",
    mutate_doc=lambda d: d.replace(
        "If this isn't relevant, reply 'no' and I won't follow up.",
        "Reply 'stop' and you will not hear from me again.", 1),
    expect_in="verbatim",
)
case(
    "the opt-out anchor is renamed -> build refuses loudly",
    mutate_doc=lambda d: d.replace("Use a plain sentence:", "Use this line:", 1),
    expect_in="opt-out sentence not found",
)

# --- banned adjectives ------------------------------------------------------
case(
    "doc names an adjective the JS does not ban -> build refuses",
    mutate_doc=lambda d: d.replace(
        'no adjectives like "revolutionary" or "cutting-edge"',
        'no adjectives like "revolutionary", "paradigm-shifting" or "cutting-edge"', 1),
    expect_in="does not ban",
)
case(
    "JS stops banning an adjective the doc names -> build refuses",
    mutate_js=lambda s: s.replace("  'revolutionary',\n", "", 1),
    js_file="code_assemble.js",
    expect_in="does not ban",
)

# --- the URL cap ------------------------------------------------------------
case(
    "doc changes the URL cap -> build refuses",
    mutate_doc=lambda d: d.replace("Maximum one plain URL.", "Maximum two plain URL.", 1),
    expect_in="url cap",
)
case(
    "JS raises the URL cap above the doc -> build refuses",
    mutate_js=lambda s: s.replace("const MAX_URLS = 1;", "const MAX_URLS = 3;", 1),
    js_file="code_assemble.js",
    expect_in="url cap",
)

# --- the therapeutic-area taxonomy -----------------------------------------
case(
    "doc adds a taxonomy category the JS does not have -> build refuses",
    mutate_doc=lambda d: d.replace("\nHematology\nOther\n", "\nHematology\nUrology\nOther\n", 1),
    expect_in="taxonomy",
)
case(
    "JS adds a taxonomy category the doc does not have -> build refuses",
    mutate_js=lambda s: s.replace("  'Hematology',\n", "  'Hematology',\n  'Urology',\n", 1),
    expect_in="taxonomy",
)
case(
    "the taxonomy fence disappears -> build refuses loudly",
    mutate_doc=lambda d: d.replace("**Therapeutic area taxonomy", "**Areas taxonomy", 1),
    expect_in="taxonomy section not found",
)

# --- Section 3's drafting inference settings --------------------------------
#
# Section 3 marks these UNVERIFIED and says to test them in Sprint 4. Parsing
# them is what makes a retest that changes the doc land in the workflow on the
# next rebuild instead of being remembered -- or not.
case(
    "the drafting inference line is renamed -> build refuses loudly",
    # NOTE: this anchor hardcodes the doc's CURRENT temperature. Retuning the
    # drafting settings changes that literal and breaks this case until it is
    # bumped -- expected maintenance on every retune, not a real drift failure.
    mutate_doc=lambda d: d.replace("For drafting, `temperature: 0.45`",
                                   "When drafting use `temperature: 0.45`", 1),
    expect_in="drafting inference settings not found",
)

# --- the wiring guard -------------------------------------------------------
#
# Not a spec drift, but the same class of bug: something the build can prove and
# runtime cannot. n8n resolves a missing `$json.X` to an empty string without
# erroring, so a prompt field the upstream node stops emitting produces drafts
# that look fine, an execution that reports success, and no signal anywhere.
# This happened for real on the first live run.
case(
    "the Code node stops emitting a field the Ollama body reads -> build refuses",
    mutate_js=lambda s: s.replace("    system_prompt: SYSTEM_PROMPT,\n", "", 1),
    expect_in="does not emit",
)
case(
    "the same guard covers the fact-sheet prompt, not just the system prompt",
    mutate_js=lambda s: s.replace("    prompt: prompt,\n", "", 1),
    expect_in="does not emit",
)

# --- Section 8's drafts schema ---------------------------------------------
case(
    "the drafts table loses a column the INSERT writes -> build refuses",
    mutate_doc=lambda d: d.replace(
        "  id, lead_id FK, channel (email|linkedin), variant,",
        "  id, lead_id FK, channel (email|linkedin),", 1),
    expect_in="section 8",
)

print("drift guards for Workflow 4\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    sys.exit(1)

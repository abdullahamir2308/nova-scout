"""Checks that build_workflow.py's spec-drift guards actually FIRE.

A guard that has never been seen to fail is not a guard. Each case here takes a
real copy of the Master Ref (or of code_evaluate.js), introduces exactly one
divergence, and asserts the build refuses to run.

    python n8n/scoring/test_drift_guards.py

Exits non-zero on the first guard that failed to fire.
"""
import io
import os
import re
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
    """Run the generator against a scratch copy, return (rc, stderr)."""
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["SCORING_OUT"] = os.path.join(workdir, "out", "scoring.json")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(workdir, "scoring", "build_workflow.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    _, err = proc.communicate()
    return proc.returncode, err.decode("utf-8", "replace")


def case(label, mutate_doc=None, mutate_js=None, js_file="code_evaluate.js", expect_in=""):
    """Apply one mutation to a scratch copy and assert the build refuses."""
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "scoring"))
        doc_path = os.path.join(tmp, "MasterRef.md")
        doc = read(MASTER_REF)
        if mutate_doc:
            new_doc = mutate_doc(doc)
            assert new_doc != doc, "mutation %r did not change the doc" % label
            doc = new_doc
        write(doc_path, doc)

        if mutate_js:
            p = os.path.join(tmp, "scoring", js_file)
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


# --- the build must succeed unmodified, or every case below is meaningless --
def baseline():
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "scoring"))
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

# --- geography -------------------------------------------------------------
case(
    "doc drops a geography -> build refuses",
    mutate_doc=lambda d: d.replace(
        "Turkey, Mexico, India", "Turkey, India", 1),
    expect_in="geograph",
)
case(
    "doc adds a geography the JS does not have -> build refuses",
    mutate_doc=lambda d: d.replace(
        "UAE, South Africa", "UAE, Vietnam, South Africa", 1),
    expect_in="geograph",
)
case(
    "JS adds a geography the doc does not have -> build refuses",
    mutate_js=lambda s: s.replace("  'Argentina',\n", "  'Argentina',\n  'Vietnam',\n", 1),
    expect_in="geograph",
)
case(
    "the '**Geographies:**' anchor is renamed -> build refuses loudly",
    mutate_doc=lambda d: d.replace("**Geographies:**", "**Target markets:**", 1),
    expect_in="geography list not found",
)

# --- weights ---------------------------------------------------------------
case(
    "doc reweights a factor -> build refuses",
    mutate_doc=lambda d: d.replace("| Target geography | 25 |", "| Target geography | 30 |", 1),
    expect_in="sum to 105",
)
case(
    "doc reweights two factors so they still sum to 100 -> build STILL refuses",
    mutate_doc=lambda d: d.replace(
        "| Target geography | 25 |", "| Target geography | 30 |", 1
    ).replace("| Oncology focus (strongest case study) | 15 |",
              "| Oncology focus (strongest case study) | 10 |", 1),
    expect_in="weights drifted",
)
case(
    "JS reweights a factor -> build refuses",
    mutate_js=lambda s: s.replace("  oncology: 15,", "  oncology: 20,", 1),
    expect_in="weights drifted",
)
case(
    "the trials weight drifting in code_score.js is caught too",
    mutate_js=lambda s: s.replace("const WEIGHT_TRIALS = 20;", "const WEIGHT_TRIALS = 25;", 1),
    js_file="code_score.js",
    expect_in="trials weight drifted",
)
case(
    "doc grows a factor the code does not implement -> build refuses",
    mutate_doc=lambda d: d.replace(
        "| Site quality suggests budget | 10 |",
        "| Site quality suggests budget | 10 |\n| Publishes a blog | 0 |", 1),
    expect_in="matches no known factor",
)

# --- taxonomy --------------------------------------------------------------
case(
    "doc adds a therapeutic area the JS does not have -> build refuses",
    mutate_doc=lambda d: d.replace("\nHematology\nOther\n", "\nHematology\nUrology\nOther\n", 1),
    expect_in="taxonomy drifted",
)
case(
    "JS drops a therapeutic area -> build refuses",
    mutate_js=lambda s: s.replace("  'Nephrology',\n", "", 1),
    expect_in="taxonomy drifted",
)

# --- employee band / enterprise cutoff -------------------------------------
case(
    "JS widens the employee band away from Section 12 -> build refuses",
    mutate_js=lambda s: s.replace(
        "const EMPLOYEE_BAND = { min: 5, max: 100 };",
        "const EMPLOYEE_BAND = { min: 5, max: 250 };", 1),
    expect_in="employee band",
)
case(
    "JS moves the enterprise cutoff away from Section 9 -> build refuses",
    mutate_js=lambda s: s.replace(
        "const ENTERPRISE_OVER = 500;", "const ENTERPRISE_OVER = 1000;", 1),
    expect_in="enterprise cutoff",
)

print("Workflow 3 spec-drift guards\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why.replace("\n", "\n         "))
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)

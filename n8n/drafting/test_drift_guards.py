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
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")
SKILL = os.path.join(REPO, "NovaScout_DraftingSkill.md")
CLAIMS_MIGRATION = os.path.join(REPO, "postgres", "migrations", "010_claims_library.sql")

PASSED = []
FAILED = []


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


# Every case builds as this sender, never the real one. The cases must not
# depend on a gitignored .env existing, so NOVASCOUT_ENV_FILE points at a path
# that is never created -- the real .env is out of every case unless a case
# opts in with a file of its own.
FIXTURE_SENDER = "Drift Test Sender"


def stage_companions(workdir, mutate_skill=None, mutate_migration=None):
    """The drafting skill and migration 010 are parsed by the build too. Every
    case gets its own copy, so a case can mutate one without touching the repo."""
    for label, src, name, mutate in (("skill", SKILL, "DraftingSkill.md", mutate_skill),
                                     ("migration", CLAIMS_MIGRATION, "010_claims_library.sql",
                                      mutate_migration)):
        text = read(src)
        if mutate:
            new = mutate(text)
            assert new != text, "mutation did not change the %s" % label
            text = new
        write(os.path.join(workdir, name), text)


def run_build(workdir, doc_path, env_overrides=None):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = doc_path
    env["NOVASCOUT_DRAFTING_SKILL"] = os.path.join(workdir, "DraftingSkill.md")
    env["NOVASCOUT_CLAIMS_MIGRATION"] = os.path.join(workdir, "010_claims_library.sql")
    env["DRAFTING_OUT"] = os.path.join(workdir, "out", "drafting.json")
    env["NOVASCOUT_ENV_FILE"] = os.path.join(workdir, "no-such.env")
    env["NOVASCOUT_SENDER_NAME"] = FIXTURE_SENDER
    for k in ("NOVASCOUT_SENDER_TITLE", "NOVASCOUT_SENDER_PHONE", "NOVASCOUT_DEMO_URL"):
        env.pop(k, None)
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


def case(label, mutate_doc=None, mutate_js=None, js_file="code_assess.js", expect_in="",
         env_overrides=None, mutate_skill=None, mutate_migration=None):
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        stage_companions(tmp, mutate_skill, mutate_migration)
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

        rc, err = run_build(tmp, doc_path, env_overrides)
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
        stage_companions(tmp)
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


def env_file_supplies_sender():
    """The positive half of the sender guard: with the variable absent from the
    environment, the name in .env is what lands in the shipped Code node."""
    label = ".env supplies the sender name when the environment does not"
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        stage_companions(tmp)
        doc_path = os.path.join(tmp, "MasterRef.md")
        write(doc_path, read(MASTER_REF))
        env_file = os.path.join(tmp, "test.env")
        write(env_file, '# other keys are ignored\nOTHER=1\nNOVASCOUT_SENDER_NAME="Env File Sender"\n')
        rc, err = run_build(tmp, doc_path, {"NOVASCOUT_SENDER_NAME": None,
                                            "NOVASCOUT_ENV_FILE": env_file})
        if rc != 0:
            FAILED.append((label, "build refused:\n" + err[-400:]))
            return
        wf = json.loads(read(os.path.join(tmp, "out", "drafting.json")))
        code = [n for n in wf["nodes"] if n["name"] == "Assemble Drafts"][0]["parameters"]["jsCode"]
        if 'const SIGNATURE = "Env File Sender";' in code:
            PASSED.append(label)
        else:
            FAILED.append((label, "Assemble Drafts does not carry the name from .env"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


env_file_supplies_sender()

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

# --- the sender identity ----------------------------------------------------
#
# The signature is baked into the Code node at build time. The build used to
# default the name to "Fatima", so a rebuild from any shell without the
# variable set silently signed every future draft as the wrong person. There is
# no default now; this is the case that proves a missing name refuses.
case(
    "no sender name in the environment or .env -> build refuses, no default",
    env_overrides={"NOVASCOUT_SENDER_NAME": None},
    expect_in="NOVASCOUT_SENDER_NAME is not set",
)

# --- drafting skill v2 --------------------------------------------------------
#
# The one to read twice is the first pair. The skill's whole design is "only the
# hook and subject are freely generated; everything else is selected from the
# library". The model's JSON schema is that rule made physical -- a field it
# does not have is a part it cannot compose. Either side moving alone refuses.
case(
    "the skill lets the model write another part -> build refuses",
    mutate_skill=lambda s: s.replace("**Only the hook and subject are freely generated**",
                                     "**Only the hook, subject and problem are freely generated**", 1),
    expect_in="does not match the drafting skill",
)
case(
    "the model schema gains a body field the skill does not license -> build refuses",
    mutate_js=lambda s: s.replace('        "linkedin_hook": {"type": "string"},\n',
                                  '        "linkedin_hook": {"type": "string"},\n'
                                  '        "email_body": {"type": "string"},\n', 1),
    js_file="build_workflow.py",
    expect_in="does not match the drafting skill",
)
case(
    "the generated-parts sentence is reworded past recognition -> build refuses loudly",
    mutate_skill=lambda s: s.replace("**Only the hook and subject are freely generated**",
                                     "**The hook and subject get generated**", 1),
    expect_in="generated-parts rule not found",
)
case(
    "the skill changes the subject length -> build refuses",
    mutate_skill=lambda s: s.replace("- 30–50 characters.", "- 25–50 characters.", 1),
    expect_in="subject length drifted",
)
case(
    "the JS loosens the subject floor -> build refuses",
    mutate_js=lambda s: s.replace("const SUBJECT_MIN_CHARS = 30;", "const SUBJECT_MIN_CHARS = 20;", 1),
    js_file="code_assemble.js",
    expect_in="subject length drifted",
)
case(
    "the skill bans a subject word the JS does not -> build refuses",
    mutate_skill=lambda s: s.replace('No "free," "demo," "offer," "opportunity."',
                                     'No "free," "demo," "offer," "urgent," "opportunity."', 1),
    expect_in="bans subject words",
)
case(
    "the JS stops banning a subject word the skill bans -> build refuses",
    mutate_js=lambda s: s.replace("['free', 'demo', 'offer', 'opportunity']",
                                  "['free', 'offer', 'opportunity']", 1),
    js_file="code_assemble.js",
    expect_in="bans subject words",
)
case(
    "the skill stops forbidding fake-reply subjects the JS checks -> build refuses",
    mutate_skill=lambda s: s.replace('No "Re:" or "Fwd:"', 'No "Re:" or "Fw:"', 1),
    expect_in="fake-reply subject prefixes drifted",
)
case(
    "the skill extends the link-free warm-up -> build refuses",
    mutate_skill=lambda s: s.replace("Warm-up weeks 1–2: zero links", "Warm-up weeks 1–3: zero links", 1),
    expect_in="link-free warm-up drifted",
)
case(
    "the JS shortens the link-free warm-up -> build refuses",
    mutate_js=lambda s: s.replace("const LINK_FREE_WEEKS = 2;", "const LINK_FREE_WEEKS = 1;", 1),
    js_file="code_assemble.js",
    expect_in="link-free warm-up drifted",
)
case(
    "the skill's body budget disagrees with the Master Ref -> build refuses",
    mutate_skill=lambda s: s.replace("stays **under 80 words**", "stays **under 90 words**", 1),
    expect_in="disagree on the word ceiling",
)
case(
    "Section 12's company-size band changes -> build refuses",
    mutate_doc=lambda d: d.replace("**Company:** 5–100 employees", "**Company:** 5–50 employees", 1),
    expect_in="small-team band drifted",
)
case(
    "migration 010's countries CHECK loses a Section 12 geography -> build refuses",
    mutate_migration=lambda s: s.replace("'South Africa', 'Brazil', 'Argentina']", "'South Africa', 'Brazil']", 1),
    expect_in="claims_countries_are_geographies",
)
case(
    "a subject worked example is dropped from the system prompt -> build refuses",
    mutate_js=lambda s: s.replace('->  Pharma-team inquiries after hours",', '->  Pharma-team leads after hours",', 1),
    js_file="build_workflow.py",
    expect_in="worked example",
)

# --- the batch row -> Assess Grounding wiring ---------------------------------
#
# Same class as the Ollama-body guard: a column the query stops returning is
# `undefined` in the Code node, silently. A lost `library` holds every lead back
# as if the operator's table were empty.
case(
    "the batch query stops returning the claims library -> build refuses",
    mutate_js=lambda s: s.replace("AS library,", "AS claims,", 1),
    js_file="build_workflow.py",
    expect_in="returns no such column",
)
case(
    "the batch query stops returning the warm-up week -> build refuses",
    mutate_js=lambda s: s.replace("AS warmup_week", "AS week", 1),
    js_file="build_workflow.py",
    expect_in="returns no such column",
)

print("drift guards for Workflow 4\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    sys.exit(1)

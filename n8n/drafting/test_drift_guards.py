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
import re
import shutil
import subprocess
import sys
import tempfile

BS = chr(92)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")
SKILL = os.path.join(REPO, "NovaScout_DraftingSkill.md")
CLAIMS_MIGRATION = os.path.join(REPO, "postgres", "migrations", "010_claims_library.sql")
CLAIMS_V3_MIGRATION = os.path.join(REPO, "postgres", "migrations", "012_claims_library_v3.sql")

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


def stage_companions(workdir, mutate_skill=None, mutate_migration=None, mutate_v3_migration=None):
    """The drafting skill and migrations 010 and 012 are parsed by the build too.
    Every case gets its own copy, so a case can mutate one without touching the repo."""
    for label, src, name, mutate in (("skill", SKILL, "DraftingSkill.md", mutate_skill),
                                     ("migration", CLAIMS_MIGRATION, "010_claims_library.sql",
                                      mutate_migration),
                                     ("v3 migration", CLAIMS_V3_MIGRATION, "012_claims_library_v3.sql",
                                      mutate_v3_migration)):
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
    env["NOVASCOUT_CLAIMS_V3_MIGRATION"] = os.path.join(workdir, "012_claims_library_v3.sql")
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
         env_overrides=None, mutate_skill=None, mutate_migration=None, mutate_v3_migration=None):
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        stage_companions(tmp, mutate_skill, mutate_migration, mutate_v3_migration)
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

# --- the body length (skill v3: 70-110 words, never more than 125) -----------
#
# Stated in the skill and twice in the Master Ref (Section 5, Section 9). All
# three must agree and the assembler must enforce exactly that.
case(
    "Section 5 raises the ceiling alone -> build refuses",
    mutate_doc=lambda d: d.replace("First touch: body 70–110 words, never more than 125",
                                   "First touch: body 70–110 words, never more than 150", 1),
    expect_in="more than one body length rule",
)
case(
    "both Master Ref statements move together, the skill does not -> build refuses",
    mutate_doc=lambda d: d.replace("70–110 words, never more than 125", "70–120 words, never more than 140"),
    expect_in="disagree on the body length",
)
case(
    "the skill raises the ceiling, the Master Ref does not -> build refuses",
    mutate_skill=lambda s: s.replace("70–110 words and never more than 125", "70–110 words and never more than 150", 1),
    expect_in="disagree on the body length",
)
case(
    "the assembler raises its ceiling above the skill -> build refuses",
    mutate_js=lambda s: s.replace("const BODY_CEILING = 125;", "const BODY_CEILING = 150;", 1),
    js_file="code_assemble.js",
    expect_in="body length drifted",
)
case(
    "the assembler moves its target range -> build refuses",
    mutate_js=lambda s: s.replace("const BODY_TARGET_MAX = 110;", "const BODY_TARGET_MAX = 120;", 1),
    js_file="code_assemble.js",
    expect_in="body length drifted",
)
case(
    "the skill's length sentence is reworded past recognition -> build refuses loudly",
    mutate_skill=lambda s: s.replace("should be 70–110 words and never more than 125", "should be short", 1),
    expect_in="body length rule not found",
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

# --- Section 3's drafting model ----------------------------------------------
#
# The model id is in Section 3 and in skill section 6. The request ships exactly
# that id; a change in one place alone refuses.
case(
    "Section 3 names another drafting model -> build refuses",
    mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Drafting model: `claude-sonnet-5`**", 1),
    expect_in="different drafting models",
)
case(
    "the skill names another drafting model -> build refuses",
    mutate_skill=lambda s: s.replace("- Drafting node: `claude-sonnet-5-5`", "- Drafting node: `claude-opus-5-5`", 1),
    expect_in="different drafting models",
)
case(
    "the Section 3 model line is renamed -> build refuses loudly",
    mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Model for drafting: `claude-sonnet-5-5`**", 1),
    expect_in="drafting model not found",
)

# --- the wiring guard -------------------------------------------------------
#
# Not a spec drift, but the same class of bug: something the build can prove and
# runtime cannot. n8n resolves a missing `$json.X` to an empty string without
# erroring, so a prompt field the upstream node stops emitting produces drafts
# that look fine, an execution that reports success, and no signal anywhere.
# This happened for real on the first live run.
case(
    "the Code node stops emitting the request the Claude body reads -> build refuses",
    mutate_js=lambda s: s.replace("    request: request,\n", "", 1),
    expect_in="does not emit",
)
case(
    "the Claude body reads a field Assess Grounding never emits -> build refuses",
    mutate_js=lambda s: s.replace("JSON.stringify($json.request)", "JSON.stringify($json.claude_request)", 1),
    js_file="build_workflow.py",
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

# --- drafting skill v3 --------------------------------------------------------
#
# The one to read twice is the first pair. v3's whole design is "the model
# composes the whole email from the hook fact plus approved claims". The schema
# is that rule made physical: it needs a body and an ask of its own, or "exactly
# one ask" cannot be checked. Either side moving alone refuses.
case(
    "the skill goes back to line-picking -> build refuses",
    mutate_skill=lambda s: s.replace("model composes the whole email from the hook fact plus approved claims",
                                     "model writes the hook; library lines pasted in verbatim", 1),
    expect_in="composition rule not found",
)
case(
    "the model schema loses the ask field -> build refuses",
    mutate_js=lambda s: s.replace('        "email_ask": {"type": "string"},\n', "", 1),
    js_file="build_workflow.py",
    expect_in="does not match the drafting skill",
)
case(
    "Assemble Drafts stops requiring a field the schema promises -> build refuses",
    mutate_js=lambda s: s.replace("const FIELDS = ['email_subject', 'email_body', 'email_ask', 'linkedin_body', 'linkedin_ask'];",
                                  "const FIELDS = ['email_subject', 'email_body', 'email_ask', 'linkedin_body'];", 1),
    js_file="code_assemble.js",
    expect_in="FIELDS do not match",
)
case(
    "the skill changes the subject length -> build refuses",
    mutate_skill=lambda s: s.replace("**Subject.** 30–55 characters.", "**Subject.** 30–60 characters.", 1),
    expect_in="subject length drifted",
)
case(
    "the JS loosens the subject floor -> build refuses",
    mutate_js=lambda s: s.replace("const SUBJECT_MIN_CHARS = 30;", "const SUBJECT_MIN_CHARS = 20;", 1),
    js_file="code_assemble.js",
    expect_in="subject length drifted",
)
case(
    "the skill stops forbidding fake-reply subjects the JS checks -> build refuses",
    mutate_skill=lambda s: s.replace('no "Fwd:"', 'no "Fw:"', 1),
    expect_in="fake-reply subject prefixes drifted",
)
case(
    "the skill drops the subject's product-name / AI rule -> build refuses loudly",
    mutate_skill=lambda s: s.replace('Never the product name. Never "AI". No "Re:"', 'No "Re:"', 1),
    expect_in="no longer say",
)
case(
    "the skill bans an adjective the JS does not -> build refuses",
    mutate_skill=lambda s: s.replace("No banned adjectives (revolutionary,", "No banned adjectives (disruptive, revolutionary,", 1),
    expect_in="does not ban",
)
case(
    "the JS stops banning an adjective the skill names -> build refuses",
    mutate_js=lambda s: s.replace("  'seamless',\n", "", 1),
    js_file="code_assemble.js",
    expect_in="does not ban",
)
case(
    "the skill allows the product name twice -> build refuses",
    mutate_skill=lambda s: s.replace("The name appears at most once, in brackets", "The name appears at most twice, in brackets", 1),
    expect_in="limits drifted",
)
case(
    "the JS allows \"AI\" twice -> build refuses",
    mutate_js=lambda s: s.replace("const AI_MAX = 1;", "const AI_MAX = 2;", 1),
    js_file="code_assemble.js",
    expect_in="limits drifted",
)
case(
    "the skill's fixed opt-out is paraphrased -> build refuses",
    mutate_skill=lambda s: s.replace("""- Opt-out: "If this isn't relevant, reply 'no' and I won't follow up.\"""",
                                     """- Opt-out: "If this isn't relevant, just reply 'no'.\"""", 1),
    expect_in="fixed opt-out is not Section 5's",
)
case(
    "the Master Ref extends the link-free warm-up -> build refuses",
    mutate_doc=lambda d: d.replace("**Links:** none in warm-up weeks 1–2", "**Links:** none in warm-up weeks 1–3", 1),
    expect_in="link-free warm-up drifted",
)
case(
    "the JS shortens the link-free warm-up -> build refuses",
    mutate_js=lambda s: s.replace("const LINK_FREE_WEEKS = 2;", "const LINK_FREE_WEEKS = 1;", 1),
    js_file="code_assemble.js",
    expect_in="link-free warm-up drifted",
)
case(
    "migration 012 allows a slot Assess Grounding never offers -> build refuses",
    mutate_v3_migration=lambda s: s.replace("CHECK (slot IN ('description', 'angle', 'benefit', 'proof', 'ask', 'link'))",
                                            "CHECK (slot IN ('description', 'angle', 'benefit', 'proof', 'ask', 'link', 'stat'))", 1),
    expect_in="claims-library slots drifted",
)
case(
    "Assess Grounding stops resolving a v3 slot -> build refuses",
    mutate_js=lambda s: s.replace("const LIBRARY_SLOTS = ['description', 'angle', 'benefit', 'proof', 'ask'];",
                                  "const LIBRARY_SLOTS = ['description', 'angle', 'proof', 'ask'];", 1),
    expect_in="claims-library slots drifted",
)
case(
    "the skill's worked example is reworded so a narrowing no longer applies -> build refuses",
    mutate_skill=lambda s: s.replace("collects their study brief", "gathers their study brief", 1),
    expect_in="EXAMPLE_NARROWINGS",
)
case(
    "the skill's worked example disappears -> build refuses loudly",
    mutate_skill=lambda s: s.replace("**v3 (same facts, nothing invented):**", "**v3:**", 1),
    expect_in="worked example not found",
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

# --- auto-approval (migration 014) -------------------------------------------
case(
    "the batch query stops returning auto_approve_email -> build refuses",
    mutate_js=lambda s: s.replace("AS auto_approve_email", "AS auto_approve", 1),
    js_file="build_workflow.py",
    expect_in="returns no such column",
)
case(
    "the claim check moves to a cheaper model than Section 3's -> build refuses",
    mutate_js=lambda s: s.replace("const CHECK_MODEL = 'claude-sonnet-5-5';", "const CHECK_MODEL = 'claude-haiku-4-5';", 1),
    js_file="code_approval.js",
    expect_in="is not Section 3's drafting model",
)
case(
    "Section 3 changes model and the claim check does not follow -> build refuses",
    mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Drafting model: `claude-test-9`**", 1),
    mutate_skill=lambda s: s.replace("- Drafting node: `claude-sonnet-5-5`", "- Drafting node: `claude-test-9`", 1),
    expect_in="is not Section 3's drafting model",
)
case(
    "the claim check sends a sampling parameter -> build refuses",
    mutate_js=lambda s: s.replace("    model: CHECK_MODEL,\n", "    model: CHECK_MODEL,\n    temperature: 0,\n", 1),
    js_file="code_approval.js",
    expect_in="sets a sampling or thinking-budget parameter",
)
case(
    "the Approval Gate stops holding non-email drafts first (Section 6) -> build refuses",
    mutate_js=lambda s: s.replace("if (!draft || draft.channel !== 'email') {", "if (!draft) {", 1),
    js_file="code_approval.js",
    expect_in="LinkedIn is reviewed and sent by hand",
)
case(
    "node-body code moves above code_approval.js's 'Node body' marker -> build refuses",
    mutate_js=lambda s: s.replace("const KINDS = ['claim', 'prospect', 'none'];",
                                  "const KINDS = ['claim', 'prospect', 'none'];\nconst early = $input.item.json;", 1),
    js_file="code_approval.js",
    expect_in="node-body code has moved",
)
case(
    "the gate reads an approval field Assemble Drafts never builds -> build refuses",
    mutate_js=lambda s: s.replace("if (ctx.auto_approve !== true)", "if (ctx.auto_approve !== true || ctx.reviewer)", 1),
    js_file="code_approval.js",
    expect_in="Assemble Drafts' `approval` has no reviewer",
)
case(
    "Assemble Drafts stops building `approval` -> build refuses",
    mutate_js=lambda s: s.replace("    approval: {\n      kind: 'first-touch',", "    approval_ctx: {\n      kind: 'first-touch',", 1),
    js_file="code_assemble.js",
    expect_in="does not emit ['approval']",
)
case(
    "Write Drafts & Advance reads a decision the gate never sets -> build refuses",
    mutate_js=lambda s: s.replace("nullif(d->>'hold_reason', ''),", "nullif(d->>'hold_reason', ''), d->>'reviewed_by',", 1),
    js_file="build_workflow.py",
    expect_in="neither Approval Gate nor Apply Claim Check sets",
)
case(
    "a draft reaches Write Drafts & Advance without the Approval Gate -> build refuses",
    mutate_js=lambda s: s.replace(
        '"Drop Failed Generations": {\n        "main": [[{"node": "Approval Gate", "type": "main", "index": 0}]]',
        '"Drop Failed Generations": {\n        "main": [[{"node": "Write Drafts & Advance", "type": "main", "index": 0}]]', 1),
    js_file="build_workflow.py",
    expect_in="without the Approval Gate",
)
case(
    "the repair goes to a cheaper model than the claim check -> build refuses",
    mutate_js=lambda s: s.replace("function repairRequest(ctx, flagged) {\n  return {\n    model: CHECK_MODEL,",
                                  "function repairRequest(ctx, flagged) {\n  return {\n    model: 'claude-haiku-4-5',", 1),
    js_file="code_approval.js",
    expect_in="the repair request no longer uses",
)
case(
    "Apply Repair stops reading the Apply Claim Check copy it follows -> build refuses",
    mutate_js=lambda s: s.replace("const prev = $('__CHECKED__').item.json;", "const prev = $('Apply Claim Check').item.json;", 1),
    js_file="code_approval_repair.js",
    expect_in="must read $('__CHECKED__') exactly once",
)
case(
    "MAX_REPAIRS set beyond what the chain is meant to unroll -> build refuses",
    mutate_js=lambda s: s.replace("const MAX_REPAIRS = 2;", "const MAX_REPAIRS = 9;", 1),
    js_file="code_approval.js",
    expect_in="keep it small",
)
case(
    "Assemble Drafts stops handing on the composition the repair edits -> build refuses",
    mutate_js=lambda s: s.replace("    composition: parsed,\n", "", 1),
    js_file="code_assemble.js",
    expect_in="does not emit ['composition']",
)
case(
    "Apply Repair stops setting the hold on a draft it gives up on -> build refuses",
    mutate_js=lambda s: s.replace("    d.hold_reason = historyText(history);", "    void 0;", 1),
    js_file="code_approval_repair.js",
    expect_in="Apply Repair does not set on a held draft",
)
case(
    "a repaired draft reaches Write Drafts & Advance without the gate copy -> build refuses",
    mutate_js=lambda s: s.replace("    **_chain_conns,\n}", "    **_chain_conns,\n    \"Apply Repair 1\": {\"main\": [[{\"node\": \"Write Drafts & Advance\", \"type\": \"main\", \"index\": 0}]]},\n}", 1),
    js_file="build_workflow.py",
    expect_in="without the Approval Gate",
)
case(
    "the drafts INSERT writes an approval column Section 8 does not define -> build refuses",
    mutate_doc=lambda d: d.replace("  approved_by (auto|human), approved_at, hold_reason, claim_check jsonb\n",
                                   "  approved_by (auto|human), approved_at, claim_check jsonb\n", 1),
    expect_in="which Section 8",
)

# --- Section 12's business-hours clocks (2026-10-07) ------------------------
#
# Drafting only needs to know WHICH countries have a clock: a lead in one that
# does not can never be sent to, so Assess Grounding and the Approval Gate both
# stop it before any model call. The sendtrack build is what checks the table's
# numbers; these cases check that drafting reads the same table and refuses when
# it cannot.
case(
    "Section 12's clock table disappears -> build refuses",
    mutate_doc=lambda d: d.replace("| Country | Standard offset | Weekend | Tier |", "| Country | Offset |", 1),
    expect_in="no clock table follows",
)
case(
    "the clock heading disappears -> build refuses",
    mutate_doc=lambda d: d.replace("**Business-hours clocks", "**Business hours, roughly", 1),
    expect_in="'Business-hours clocks' heading not found",
)
case(
    "a clock row loses a cell -> build refuses rather than guess",
    mutate_doc=lambda d: d.replace("| India | UTC+5:30 | Sat-Sun | core |", "| India | UTC+5:30 | Sat-Sun |", 1),
    expect_in="does not have 4 cells",
)
case(
    "a clock row's offset is unreadable -> build refuses",
    mutate_doc=lambda d: d.replace("| India | UTC+5:30 | Sat-Sun | core |", "| India | +5:30 | Sat-Sun | core |", 1),
    expect_in="unreadable offset",
)
case(
    "a country is listed twice -> build refuses",
    mutate_doc=lambda d: d.replace("| India | UTC+5:30 | Sat-Sun | core |",
                                   "| India | UTC+5:30 | Sat-Sun | core |" + chr(10)
                                   + "| India | UTC+6:30 | Sat-Sun | core |", 1),
    expect_in="listed twice",
)
case(
    "Assess Grounding loses the clock placeholder -> build refuses",
    mutate_js=lambda s: s.replace("const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;",
                                  "const SEND_CLOCK_COUNTRIES = [];", 1),
    expect_in="__SEND_CLOCK_COUNTRIES__",
)
case(
    "the Approval Gate loses the clock placeholder -> build refuses",
    mutate_js=lambda s: s.replace("const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;",
                                  "const SEND_CLOCK_COUNTRIES = [];", 1),
    js_file="code_approval.js",
    expect_in="__SEND_CLOCK_COUNTRIES__",
)


def clock_list_flows():
    tmp = tempfile.mkdtemp(prefix="novascout-drift-")
    try:
        shutil.copytree(HERE, os.path.join(tmp, "drafting"))
        stage_companions(tmp)
        doc_path = os.path.join(tmp, "MasterRef.md")
        write(doc_path, read(MASTER_REF).replace(
            "| Germany | UTC+1:00 | Sat-Sun | |" + chr(10), "", 1))
        rc, err = run_build(tmp, doc_path)
        if rc != 0:
            FAILED.append(("dropping a country from the clock table changes what ships", "build refused:" + chr(10)
                           + err[-400:]))
            return
        wf = json.loads(read(os.path.join(tmp, "out", "drafting.json")))
        for name in ("Assess Grounding", "Approval Gate"):
            code = [n for n in wf["nodes"] if n["name"] == name][0]["parameters"]["jsCode"]
            m = re.search("const SEND_CLOCK_COUNTRIES = (" + BS + "[[^" + BS + "]]*" + BS + "]);", code)
            if not m:
                FAILED.append(("dropping a country from the clock table changes what ships",
                               "%s carries no clock list" % name))
                return
            names = json.loads(m.group(1))
            if "Germany" in names or "India" not in names:
                FAILED.append(("dropping a country from the clock table changes what ships",
                               "%s still lists Germany, or lost India: %r" % (name, names[:5])))
                return
        PASSED.append("dropping a country from the clock table changes what ships -- it is read, not restated")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


clock_list_flows()

print("drift guards for Workflow 4\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    sys.exit(1)

"""Checks that build_workflow.py's spec-drift and wiring guards actually FIRE.

A guard that has never been seen to fail is not a guard. Each case takes a real
copy of the Master Ref, docker-compose.yml, the Code-node sources or the build
script itself, introduces exactly one divergence, and asserts the build refuses.

    python n8n/sendtrack/test_drift_guards.py

The cases worth reading twice: the warm-up table (the ceiling IS the product of
this workflow), Section 6 (a send query that can see LinkedIn drafts), the
opt-out sentence and keywords (Section 5's GDPR/KVKK mechanism), and the wiring
cases (Workflow 4's silent-empty-field bug, for every node boundary here).

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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")
COMPOSE = os.path.join(REPO, "docker-compose.yml")
# The follow-ups are composed under drafting skill v3 (section 8): the build
# parses the skill and embeds drafting's code_assemble.js rules, so each case
# gets its own copy of both.
SKILL = os.path.join(REPO, "NovaScout_DraftingSkill.md")
RULES = os.path.join(REPO, "n8n", "drafting", "code_assemble.js")
# Auto-approval (migration 014): Follow-Ups embeds drafting's Approval Gate and
# Apply Claim Check verbatim, so each case gets its own copy of those too.
APPROVAL = os.path.join(REPO, "n8n", "drafting", "code_approval.js")
APPROVAL_APPLY = os.path.join(REPO, "n8n", "drafting", "code_approval_apply.js")
# The repair loop (2026-10-03): Apply Repair's body and the chain generator.
APPROVAL_REPAIR = os.path.join(REPO, "n8n", "drafting", "code_approval_repair.js")
APPROVAL_CHAIN = os.path.join(REPO, "n8n", "drafting", "approval_chain.py")
# Section 12's clock table must cover every country the scraper includes, so each
# case gets its own copy of the scraper tree -- geography.py classifies the slugs
# and index_slugs.json is the /cro-list index as it was last read.
SCRAPER = os.path.join(REPO, "scraper")

def index_countries():
    """The included countries of scraper/index_slugs.json, as the display names
    that land in leads.country -- the same question the build asks, asked here
    against the shipped workflow rather than the source."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from scraper import geography as geo

    snap = json.loads(read(os.path.join(SCRAPER, "index_slugs.json")))
    return sorted({geo.canonical_name(s) for s in snap["slugs"] if not geo.excluded_as(s)})


PASSED = []
FAILED = []

FIXTURE_SENDER = "Drift Test Sender"
FIXTURE_MAILBOX = "sender@drift-test.example"


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


def run_build(tmp, env_overrides=None):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = os.path.join(tmp, "MasterRef.md")
    env["NOVASCOUT_COMPOSE"] = os.path.join(tmp, "docker-compose.yml")
    env["NOVASCOUT_DRAFTING_SKILL"] = os.path.join(tmp, "DraftingSkill.md")
    env["NOVASCOUT_DRAFTING_DIR"] = os.path.join(tmp, "drafting")
    env["SENDTRACK_OUT"] = os.path.join(tmp, "out")
    env["NOVASCOUT_SCRAPER_ROOT"] = tmp
    # The real .env is out of every case unless a case brings its own file.
    env["NOVASCOUT_ENV_FILE"] = os.path.join(tmp, "no-such.env")
    env["NOVASCOUT_SENDER_NAME"] = FIXTURE_SENDER
    env["NOVASCOUT_MAILBOX_ADDRESS"] = FIXTURE_MAILBOX
    for k in ("NOVASCOUT_SENDER_TITLE", "NOVASCOUT_SENDER_PHONE", "NOVASCOUT_OPERATOR_EMAIL",
              "SENDTRACK_VARIANTS_OUT", "SENDTRACK_DRYRUN_NOW", "SENDTRACK_FIXTURES"):
        env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    for k, v in (env_overrides or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v.replace("{tmp}", tmp)
    p = subprocess.run([sys.executable, os.path.join(tmp, "sendtrack", "build_workflow.py")],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    return p.returncode, p.stderr.decode("utf-8", "replace")


def setup(mutate_doc=None, mutate_compose=None, mutate_file=None, mutate_skill=None, mutate_rules=None,
          mutate_approval=None, mutate_index=None):
    tmp = tempfile.mkdtemp(prefix="novascout-sendtrack-drift-")
    for src, dst, fn, label in ((SKILL, "DraftingSkill.md", mutate_skill, "the skill"),
                                (RULES, os.path.join("drafting", "code_assemble.js"), mutate_rules, "code_assemble.js"),
                                (APPROVAL, os.path.join("drafting", "code_approval.js"), mutate_approval,
                                 "code_approval.js"),
                                (APPROVAL_APPLY, os.path.join("drafting", "code_approval_apply.js"), None,
                                 "code_approval_apply.js"),
                                (APPROVAL_REPAIR, os.path.join("drafting", "code_approval_repair.js"), None,
                                 "code_approval_repair.js"),
                                (APPROVAL_CHAIN, os.path.join("drafting", "approval_chain.py"), None,
                                 "approval_chain.py")):
        text = read(src)
        if fn:
            new = fn(text)
            assert new != text, "mutation did not change %s" % label
            text = new
        os.makedirs(os.path.dirname(os.path.join(tmp, dst)), exist_ok=True)
        write(os.path.join(tmp, dst), text)
    shutil.copytree(HERE, os.path.join(tmp, "sendtrack"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SCRAPER, os.path.join(tmp, "scraper"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    if mutate_index:
        q = os.path.join(tmp, "scraper", "index_slugs.json")
        snap = json.loads(read(q))
        new = mutate_index(snap)
        assert new != snap, "index mutation did not change index_slugs.json"
        write(q, json.dumps(new, indent=2) + chr(10))
    doc = read(MASTER_REF)
    if mutate_doc:
        new = mutate_doc(doc)
        assert new != doc, "doc mutation did not change the doc"
        doc = new
    write(os.path.join(tmp, "MasterRef.md"), doc)
    compose = read(COMPOSE)
    if mutate_compose:
        new = mutate_compose(compose)
        assert new != compose, "compose mutation did not change docker-compose.yml"
        compose = new
    write(os.path.join(tmp, "docker-compose.yml"), compose)
    if mutate_file:
        name, fn = mutate_file
        p = os.path.join(tmp, "sendtrack", name)
        src = read(p)
        new = fn(src)
        assert new != src, "mutation did not change %s" % name
        write(p, new)
    return tmp


def case(label, expect_in, mutate_doc=None, mutate_compose=None, mutate_file=None, env_overrides=None,
         mutate_skill=None, mutate_rules=None, mutate_approval=None, mutate_index=None):
    tmp = setup(mutate_doc, mutate_compose, mutate_file, mutate_skill, mutate_rules, mutate_approval,
                mutate_index)
    try:
        rc, err = run_build(tmp, env_overrides)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but the message lacked %r:\n%s" % (expect_in, err[-500:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def positive(label, check, mutate_doc=None, env_overrides=None, files=None, mutate_skill=None, mutate_approval=None,
             mutate_index=None):
    tmp = setup(mutate_doc, mutate_skill=mutate_skill, mutate_approval=mutate_approval,
                mutate_index=mutate_index)
    try:
        for name, content in (files or {}).items():
            write(os.path.join(tmp, name), content)
        rc, err = run_build(tmp, env_overrides)
        if rc != 0:
            FAILED.append((label, "build refused:\n" + err[-600:]))
            return
        why = check(tmp)
        if why:
            FAILED.append((label, why))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load(tmp, name):
    return json.loads(read(os.path.join(tmp, "out", name)))


def node(wf, name):
    return [n for n in wf["nodes"] if n["name"] == name][0]


# --- baselines --------------------------------------------------------------
positive("BASELINE: unmodified build succeeds", lambda tmp: None)
positive(
    "BASELINE: the dry-run variants build too, and differ only where allowed",
    lambda tmp: None if os.path.isfile(os.path.join(tmp, "variants", "send-dryrun.json")) else "no variant written",
    env_overrides={"SENDTRACK_VARIANTS_OUT": "{tmp}/variants", "SENDTRACK_DRYRUN_NOW": "2026-09-14T10:00:00Z"},
)


def secrets_stay_out(tmp):
    for root, _, files in os.walk(os.path.join(tmp, "out")):
        for f in files:
            if "never-in-a-workflow" in read(os.path.join(root, f)):
                return "the mailbox password reached " + f
    send = load(tmp, "send.json")
    code = node(send, "Decide Send")["parameters"]["jsCode"]
    if 'const SIGNATURE = "Env File Sender";' not in code:
        return "the signature from .env did not reach Decide Send"
    if node(send, "Send Email")["parameters"]["fromEmail"] != '"Env File Sender" <me@env-file.example>':
        return "the From header is not built from .env"
    return None


positive(
    ".env supplies sender and mailbox -- and the mailbox PASSWORD never reaches a workflow",
    secrets_stay_out,
    env_overrides={"NOVASCOUT_SENDER_NAME": None, "NOVASCOUT_MAILBOX_ADDRESS": None,
                   "NOVASCOUT_ENV_FILE": "{tmp}/test.env"},
    files={"test.env": 'NOVASCOUT_SENDER_NAME="Env File Sender"\nNOVASCOUT_MAILBOX_ADDRESS=me@env-file.example\n'
                       "NOVASCOUT_MAILBOX_PASSWORD=never-in-a-workflow\n"},
)
positive(
    "Section 9's follow-up delay flows straight into the Follow-Ups Config",
    lambda tmp: None if [a["value"] for a in node(load(tmp, "follow-ups.json"), "Config")["parameters"]
                         ["assignments"]["assignments"] if a["name"] == "follow_up_days"] == [9]
    else "follow_up_days is not 9",
    mutate_doc=lambda d: d.replace("if no reply after 6 days", "if no reply after 9 days", 1),
)

# --- Section 5: the warm-up ceiling -----------------------------------------
case("doc lowers week 1 to 4/day and the JS does not follow -> refuses", "warm-up schedule drifted",
     mutate_doc=lambda d: d.replace("| Week 1 | 5 |", "| Week 1 | 4 |", 1))
case("JS raises week 1 to 6/day -> refuses", "warm-up schedule drifted",
     mutate_file=("code_decide.js", lambda s: s.replace("  [1, 5],", "  [1, 6],", 1)))
case("JS drops the open-ended week 4+ row -> refuses", "warm-up schedule drifted",
     mutate_file=("code_decide.js", lambda s: s.replace("  [4, 20],\n", "", 1)))
case("the warm-up table disappears -> refuses loudly", "warm-up table not found",
     mutate_doc=lambda d: d.replace("| Week ", "| Wk ", 4))
case("a middle row made open-ended -> refuses as ambiguous", "only the last row may be open-ended",
     mutate_doc=lambda d: d.replace("| Week 2 | 10 |", "| Week 2+ | 10 |", 1))

# --- Section 5: the opt-out sentence and keywords ---------------------------
case("the send path's opt-out sentence is paraphrased -> refuses", "VERBATIM",
     mutate_file=("code_decide.js", lambda s: s.replace("I won't follow up.", "I will not follow up.", 1)))
case("the reply classifier strips a different sentence -> refuses", "strips out of replies",
     mutate_file=("code_classify_reply.js", lambda s: s.replace("I won't follow up.", "I will not follow up.", 1)))
case("Build Follow-Up carries a different opt-out sentence -> refuses", "VERBATIM",
     mutate_file=("code_followup.js", lambda s: s.replace("I won't follow up.", "I will not follow up.", 1)))
case("the opt-out Assemble Follow-Up appends (drafting's rules) is paraphrased -> refuses", "VERBATIM",
     mutate_rules=lambda s: s.replace("I won't follow up.", "I will not follow up.", 1))
case("the doc changes the opt-out sentence and the code does not follow -> refuses", "VERBATIM",
     mutate_doc=lambda d: d.replace("If this isn't relevant, reply 'no' and I won't follow up.",
                                    "Reply 'stop' and you will not hear from me again.", 1))
case("Section 5 and Section 9 list different opt-out keywords -> refuses", "two different opt-out keyword lists",
     mutate_doc=lambda d: d.replace('"no", "unsubscribe", "remove", "stop"',
                                    '"no", "unsubscribe", "remove", "stop", "cancel"', 1))
case("both sections add a keyword the classifier does not know -> refuses", "opt-out keywords drifted",
     mutate_doc=lambda d: d.replace('"no", "unsubscribe", "remove", "stop"',
                                    '"no", "unsubscribe", "remove", "stop", "cancel"', 1)
     .replace("reply matching no/unsubscribe/remove/stop", "reply matching no/unsubscribe/remove/stop/cancel", 1))
case("the classifier stops detecting 'stop' -> refuses", "opt-out keywords drifted",
     mutate_file=("code_classify_reply.js",
                  lambda s: s.replace("['no', 'unsubscribe', 'remove', 'stop']", "['no', 'unsubscribe', 'remove']", 1)))
case("the doc changes the URL cap -> refuses", "URL cap drifted",
     mutate_doc=lambda d: d.replace("Maximum one plain URL.", "Maximum two plain URL.", 1))

# --- Section 9: follow-ups --------------------------------------------------
case("the doc allows three follow-ups -> refuses", "follow-up maximum drifted",
     mutate_doc=lambda d: d.replace("Maximum two follow-ups", "Maximum three follow-ups", 1))
case("Build Follow-Up allows a third follow-up -> refuses", "follow-up maximum drifted",
     mutate_file=("code_followup.js", lambda s: s.replace("const MAX_FOLLOW_UPS = 2;", "const MAX_FOLLOW_UPS = 3;", 1)))

# --- drafting skill v3 section 8: composed follow-ups ------------------------
case("the skill moves follow-up #1 to 50-80 words and the JS does not follow -> refuses", "follow-up lengths drifted",
     mutate_skill=lambda k: k.replace("**Follow-up #1.** 40–70 words", "**Follow-up #1.** 50–80 words", 1))
case("the skill lowers follow-up #2 to 30 words and the JS does not follow -> refuses", "follow-up lengths drifted",
     mutate_skill=lambda k: k.replace("short final note.** At most 40 words", "short final note.** At most 30 words", 1))
case("Assemble Follow-Up's ceiling drifts from the skill -> refuses", "follow-up lengths drifted",
     mutate_file=("code_followup_assemble.js", lambda s: s.replace("const FOLLOW_UP_1_MAX = 70;", "const FOLLOW_UP_1_MAX = 90;", 1)))
case("the skill's follow-up rules disappear -> refuses loudly", "follow-up #1 length not found",
     mutate_skill=lambda k: k.replace("**Follow-up #1.**", "**First follow-up.**", 1))
case("the skill drops 'add one angle or benefit the first email did not use' -> refuses", "new-claim check depends on it",
     mutate_skill=lambda k: k.replace("Add one angle or benefit from §4 that the first email did not use",
                                      "Add something new", 1))
case("drafting's code_assemble.js loses a rule function the follow-ups call -> refuses", "no longer defines",
     mutate_rules=lambda s: s.replace("function repeatFlags(", "function repeatFlagz(", 1))
case("node-body code moves above drafting's 'Node body' marker -> refuses", "node-body code has moved",
     mutate_rules=lambda s: s.replace("function unique(list) {", "const leaked = $input.item;\nfunction unique(list) {", 1))
case("the skill bans an adjective the embedded rules do not -> refuses", "which the embedded rules do not",
     mutate_skill=lambda k: k.replace("No banned adjectives (revolutionary,", "No banned adjectives (groundbreaking, revolutionary,", 1))
case("Follow-Ups goes back to 'Every 6 Hours' -> refuses (n8n's clock-hour recurrence check)", "recurrenceCheck",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"field": "minutes", "minutesInterval": 30}', '{"field": "hours", "hoursInterval": 6}', 1)))
case("Follow-Ups on 'every 2 days' -> refuses (same clock-value check, day of year)", "recurrenceCheck",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"field": "minutes", "minutesInterval": 30}', '{"field": "days", "daysInterval": 2}', 1)))
# A fixed slot is the other half of the idempotency rule, added 2026-10-07 after
# Ingestion was found firing once a day at 23:00 PKT on a host that is off at
# night -- an interval of 1 passes the clock-value test but a pinned hour or
# minute still leaves one moment a day to fire in.
case("Send pinned to one minute of the hour -> refuses", "one moment a day (or a week) to fire in",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"field": "minutes", "minutesInterval": 10}',
         '{"field": "minutes", "minutesInterval": 10, "triggerAtMinute": 7}', 1)))
case("the Daily Digest moved to a real daily slot -> refuses", "one moment a day (or a week) to fire in",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"field": "minutes", "minutesInterval": 30}',
         '{"field": "days", "daysInterval": 1, "triggerAtHour": 8}', 1)))
case("the follow-up schema loses a field Assemble Follow-Up requires -> refuses", "do not match the follow-up schema",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"required": ["body", "ask", "added_claim", "claims", "first_email_covers"],',
         '"required": ["body", "ask", "claims", "first_email_covers"],', 1)))
case("Section 3 loses the drafting model line -> refuses", "drafting model not found",
     mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Drafting: `claude-sonnet-5-5`**", 1))


def _fu_code(tmp, name):
    return node(load(tmp, "follow-ups.json"), name)["parameters"]["jsCode"]


positive(
    "Section 3's drafting model flows straight into the follow-up request (and the claim check, once it agrees)",
    lambda tmp: None if '"model": "claude-test-9"' in _fu_code(tmp, "Build Follow-Up")
    and "const CHECK_MODEL = 'claude-test-9';" in _fu_code(tmp, "Apply Claim Check")
    else "the follow-up request or the claim check does not name the doc's model",
    mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Drafting model: `claude-test-9`**", 1),
    mutate_approval=lambda s: s.replace("const CHECK_MODEL = 'claude-sonnet-5-5';", "const CHECK_MODEL = 'claude-test-9';", 1),
)

# --- auto-approval (migration 014): the shared gate in Follow-Ups -------------
case("Section 3 changes model but the claim check does not -> refuses", "the claim check is not Section 3's",
     mutate_doc=lambda d: d.replace("**Drafting model: `claude-sonnet-5-5`**", "**Drafting model: `claude-test-9`**", 1))
case("the claim check is moved to a cheaper model -> refuses", "the claim check is not Section 3's",
     mutate_approval=lambda s: s.replace("const CHECK_MODEL = 'claude-sonnet-5-5';", "const CHECK_MODEL = 'claude-haiku-4-5';", 1))
case("node-body code moves above code_approval.js's 'Node body' marker -> refuses", "node-body code has moved",
     mutate_approval=lambda s: s.replace("const KINDS = ['claim', 'prospect', 'none'];",
                                         "const KINDS = ['claim', 'prospect', 'none'];\nconst early = $input.item.json;", 1))
case("a composed follow-up reaches Write Follow-Up without the Approval Gate -> refuses",
     "something reaches Write Follow-Up without the Approval Gate",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"Drop Failed Generations": edge("Approval Gate"),', '"Drop Failed Generations": edge("Write Follow-Up"),', 1)))
case("Write Follow-Up reads a draft field nothing upstream produces -> refuses", "never emits ['reviewed_by']",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "nullif(p.p->'draft'->>'hold_reason', ''),", "nullif(p.p->'draft'->>'hold_reason', ''), p.p->'draft'->>'reviewed_by',", 1)))
case("the shared gate reads an approval field Assemble Follow-Up never builds -> refuses",
     "Assemble Follow-Up's `approval` has no",
     mutate_approval=lambda s: s.replace("if (ctx.auto_approve !== true)", "if (ctx.auto_approve !== true || ctx.reviewer)", 1))
def _approval_src():
    """code_approval.js as the build ships it: with Section 12's clock table
    substituted for __SEND_CLOCK_COUNTRIES__ (rule 3b, 2026-10-07). Comparing
    against the raw file would report the shipped code as not verbatim when the
    only difference is the one constant both generators bake in."""
    doc = read(MASTER_REF)
    at = doc.index("**Business-hours clocks")
    head = "| Country | Standard offset | Weekend | Tier |"
    body = doc[doc.index(head, at):].split(chr(10) + chr(10))[0].splitlines()[2:]
    names = sorted(ln.strip().strip("|").split("|")[0].strip() for ln in body)
    return read(APPROVAL).replace("__SEND_CLOCK_COUNTRIES__", json.dumps(names, ensure_ascii=False))


def _rules():
    src = _approval_src()
    return src[:src.index("// Node body")]


positive(
    "Follow-Ups ships drafting's Approval Gate verbatim in every round, and Apply Claim Check / Apply Repair as its "
    "rules + their bodies, each reading its own round's node",
    lambda tmp: None if all(_fu_code(tmp, g) == _approval_src()
                            for g in ("Approval Gate", "Approval Gate R1", "Approval Gate R2"))
    and _fu_code(tmp, "Apply Claim Check") == _rules() + "\n" + read(APPROVAL_APPLY).replace("$('__GATE__')", "$('Approval Gate')")
    and _fu_code(tmp, "Apply Claim Check R2") == _rules() + "\n" + read(APPROVAL_APPLY).replace("$('__GATE__')", "$('Approval Gate R2')")
    and _fu_code(tmp, "Apply Repair 1") == _rules() + "\n" + read(APPROVAL_REPAIR).replace("$('__CHECKED__')", "$('Apply Claim Check')")
    and _fu_code(tmp, "Apply Repair 2") == _rules() + "\n" + read(APPROVAL_REPAIR).replace("$('__CHECKED__')", "$('Apply Claim Check R1')")
    and _fu_code(tmp, "Assemble Follow-Up R1") == _fu_code(tmp, "Assemble Follow-Up")
    else "the follow-up approval and repair nodes are not drafting's code, verbatim, each reading its own round",
)
positive(
    "MAX_REPAIRS decides how many repair rounds Follow-Ups unrolls",
    lambda tmp: None if "Apply Claim Check R3" in {n["name"] for n in load(tmp, "follow-ups.json")["nodes"]}
    else "MAX_REPAIRS = 3 did not unroll a third round",
    mutate_approval=lambda s: s.replace("const MAX_REPAIRS = 2;", "const MAX_REPAIRS = 3;", 1),
)
case("the repair is sent to a cheaper model than the claim check -> refuses", "the repair request no longer uses",
     mutate_approval=lambda s: s.replace("function repairRequest(ctx, flagged) {\n  return {\n    model: CHECK_MODEL,",
                                         "function repairRequest(ctx, flagged) {\n  return {\n    model: 'claude-haiku-4-5',", 1))
case("Apply Claim Check stops reading its own round's gate -> refuses", "must read $('__GATE__') exactly once",
     mutate_file=("build_workflow.py", lambda s: s.replace("import approval_chain  # noqa: E402",
                                                           "import approval_chain  # noqa: E402\n"
                                                           "approval_chain_load = approval_chain.load\n"
                                                           "def _load(d, *a, **k):\n"
                                                           "    import io as _io\n"
                                                           "    p = os.path.join(d, 'code_approval_apply.js')\n"
                                                           "    src = _io.open(p, encoding='utf-8').read()\n"
                                                           "    _io.open(p, 'w', encoding='utf-8').write(src.replace(\"$('__GATE__')\", \"$('Approval Gate')\"))\n"
                                                           "    return approval_chain_load(d, *a, **k)\n"
                                                           "approval_chain.load = _load", 1)))
case("Assemble Follow-Up stops handing the composition to the repair loop -> refuses",
     "the repair loop (composition / repair / repair_fields)",
     mutate_file=("code_followup_assemble.js", lambda s: s.replace("    composition: parsed,\n", "", 1)))
case("a repaired follow-up reaches Write Follow-Up without the Approval Gate -> refuses",
     "something reaches Write Follow-Up without the Approval Gate",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '    "Drop Failed Generations": edge("Approval Gate"),\n    **_fu_chain_conns,\n}',
         '    "Drop Failed Generations": edge("Approval Gate"),\n    **_fu_chain_conns,\n'
         '    "Apply Repair 1": edge("Write Follow-Up"),\n}', 1)))
case("'build the note around it' comes back into the follow-up prompt -> refuses", "build the note around it",
     mutate_file=("code_followup.js", lambda s: s.replace("almost word for word,", "almost word for word, and build the note around it,", 1)))
case("skill section 8 drops the word-for-word rule -> refuses", "almost word for word",
     mutate_skill=lambda s: s.replace("in that line's own words: almost word for word", "in its own words", 1))

# --- the Daily Digest --------------------------------------------------------
case("Build Digest reads a field Load Digest does not return -> refuses", "Load Digest does not produce",
     mutate_file=("code_digest.js", lambda s: s.replace("row.already_sent === true", "row.already_sent_today === true", 1)))
case("Record Digest writes a digest_log column Section 8 does not define -> refuses", "Section 8 does not define",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "INSERT INTO digest_log (digest_day, covers_from, covers_to, summary)",
         "INSERT INTO digest_log (digest_day, covers_from, covers_to, summary, message_id)", 1)))
case("the digest's clock override is left set in the shipped workflow -> refuses", "Daily Digest workflow has a clock override",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         'DIGEST_CONFIG = {"now_override": "", "digest_hour": 8}',
         'DIGEST_CONFIG = {"now_override": "2026-10-05T03:00:00Z", "digest_hour": 8}', 1)))
case("the digest goes back to a once-a-day schedule tick of 2 days -> refuses", "recurrenceCheck",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},\n     "name": DIGEST_TRIGGER',
         '{"parameters": {"rule": {"interval": [{"field": "days", "daysInterval": 2}]}},\n     "name": DIGEST_TRIGGER', 1)))
positive(
    "the skill's follow-up numbers reach the prompt the model sees",
    lambda tmp: None if "40-70 words" in _fu_code(tmp, "Build Follow-Up") and "at most 40 words" in _fu_code(tmp, "Build Follow-Up")
    else "the follow-up prompt does not state the skill's numbers",
)
positive(
    "Assemble Follow-Up ships drafting's rules verbatim, then the follow-up body",
    lambda tmp: None if _fu_code(tmp, "Assemble Follow-Up").find(read(RULES)[:read(RULES).index("// Node body")]) != -1
    and "const src = $('Build Follow-Up').item.json;" in _fu_code(tmp, "Assemble Follow-Up")
    else "the shipped Assemble Follow-Up is not drafting's rules + the follow-up body",
)

# --- Section 12's business-hours clocks -------------------------------------
#
# Since 2026-10-07 the clock table covers every country the scraper includes,
# not only the core 13, and the doc carries each country's offset and weekend.
# Four ways that can go wrong, and all must refuse: a country with no clock (its
# leads get drafted and then never sent), a clock whose numbers disagree with
# the doc (sends out of hours, silently), a row the build cannot parse (which
# would otherwise just be skipped), and a country the directory lists that
# nobody added at all.
case("Section 12's clock table loses a country the JS still has -> refuses",
     "not Section 12's 'Business-hours clocks' table",
     mutate_doc=lambda d: d.replace("| Nepal | UTC+5:45 | Sat | |\n", "", 1))
case("the JS loses a clock the doc's table has -> refuses",
     "not Section 12's 'Business-hours clocks' table",
     mutate_file=("code_decide.js", lambda s: s.replace(
         "  'Egypt':                   { utc_offset_min: 120,  weekend: [5, 6] },\n", "", 1)))
case("a clock's offset drifts between the doc and the JS -> refuses",
     "differing rows",
     mutate_doc=lambda d: d.replace("| India | UTC+5:30 | Sat-Sun | core |",
                                    "| India | UTC+6:30 | Sat-Sun | core |", 1))
case("a clock's weekend drifts between the doc and the JS -> refuses",
     "differing rows",
     mutate_doc=lambda d: d.replace("| Saudi Arabia | UTC+3:00 | Fri-Sat | |",
                                    "| Saudi Arabia | UTC+3:00 | Sat-Sun | |", 1))
case("a clock row is written in a shape the build cannot read -> refuses rather than skip it",
     "is not in the one shape the build reads",
     mutate_file=("code_decide.js", lambda s: s.replace(
         "  'Kenya':                   { utc_offset_min: 180,  weekend: [6, 0] },",
         "  'Kenya': { weekend: [6, 0], utc_offset_min: 180 },", 1)))
case("the clock table marks a non-core country 'core' -> refuses",
     "marks a different set of countries 'core'",
     mutate_doc=lambda d: d.replace("| Germany | UTC+1:00 | Sat-Sun | |",
                                    "| Germany | UTC+1:00 | Sat-Sun | core |", 1))
case("the clock table disappears -> refuses loudly",
     "no clock table follows",
     mutate_doc=lambda d: d.replace("| Country | Standard offset | Weekend | Tier |\n|---|---|---|---|\n", "", 1))
case("the directory lists a country nobody gave a clock -> refuses",
     "have no business-hours clock",
     mutate_index=lambda s: dict(s, slugs=sorted(s["slugs"] + ["greenland"])))
positive(
    "an excluded jurisdiction in the index needs no clock -- it is never scraped",
    lambda tmp: None,
    mutate_index=lambda s: dict(s, slugs=sorted(s["slugs"] + ["north_korea"])),
)


def _shipped_clock_keys(tmp):
    code = node(load(tmp, "send.json"), "Decide Send")["parameters"]["jsCode"]
    block = re.search(r"const COUNTRY_CLOCKS = \{(.*?)\n\};", code, re.S).group(1)
    return set(re.findall(r"""^\s*(?:'([^']+)'|"([^"]+)"):\s*\{\s*utc_offset_min""", block, re.M)) and {
        (a or b) for a, b in re.findall(
            r"""^\s*(?:'([^']+)'|"([^"]+)"):\s*\{\s*utc_offset_min""", block, re.M)}


def _every_index_country_ships(tmp):
    missing = sorted(c for c in index_countries() if c not in _shipped_clock_keys(tmp))
    return None if not missing else "no clock reached the shipped Decide Send for: %r" % missing


positive(
    "every country in the committed index snapshot reaches the shipped Decide Send",
    _every_index_country_ships,
)

# --- the sender clock -------------------------------------------------------
case("Section 12 promotes a country to core without the clock table agreeing -> refuses",
     "marks a different set of countries 'core'",
     mutate_doc=lambda d: d.replace("Brazil, Argentina.", "Brazil, Argentina, Chile.", 1))
case("docker-compose moves the operator to a DST zone -> refuses", "no fixed offset",
     mutate_compose=lambda c: c.replace("GENERIC_TIMEZONE: Asia/Karachi", "GENERIC_TIMEZONE: Europe/Istanbul", 1))
case("the JS sender clock disagrees with docker-compose -> refuses", "sender clock drifted",
     mutate_file=("code_decide.js", lambda s: s.replace(
         "const SENDER_UTC_OFFSET_MIN = 300;", "const SENDER_UTC_OFFSET_MIN = 330;", 1)))

# --- Section 6: LinkedIn is never sent by the system ------------------------
case("the send query is widened to LinkedIn drafts -> refuses", "LinkedIn sending is manual",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "   WHERE d.channel = 'email'     -- Section 6", "   WHERE d.channel IN ('email', 'linkedin')", 1)))
case("the claim stops checking the channel -> refuses", "LinkedIn sending is manual",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "     AND d.channel = 'email'\n     AND d.status = 'approved'", "     AND d.status = 'approved'", 1)))
case("the send query stops requiring approval -> refuses", "human gate",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "     AND d.status = 'approved'   -- Workflow 5", "     AND d.status <> 'rejected'   -- Workflow 5", 1)))
case("the claim stops re-counting the ceiling -> refuses", "re-counts the Section 5 ceiling",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "     AND (SELECT count(*) FROM counted) < (p.p->>'ceiling')::int\n", "", 1)))

# --- Section 8 --------------------------------------------------------------
case("the doc drops a column the claim INSERT writes -> refuses", "section 8",
     mutate_doc=lambda d: d.replace("  id, lead_id FK, draft_id FK, channel,", "  id, lead_id FK, channel,", 1))
case("the SQL writes a lead status outside the LOCKED list -> refuses", "LOCKED status",
     mutate_file=("build_workflow.py", lambda s: s.replace("SET status = 'lost', updated_at", "SET status = 'abandoned', updated_at", 1)))

# --- wiring: every read has a producer --------------------------------------
case("Decide reads a state field Load Send State does not return -> refuses", "does not produce",
     mutate_file=("code_decide.js", lambda s: s.replace(
         "ms(state.first_external_send_at)", "ms(state.first_send_at)", 1)))
case("Decide reads a candidate field the query does not select -> refuses", "does not produce",
     mutate_file=("code_decide.js", lambda s: s.replace("if (!c.verified)", "if (!c.is_verified)", 1)))
case("Check SMTP Result stops emitting the Message-ID Confirm writes -> refuses", "never emits",
     mutate_file=("code_check_smtp.js", lambda s: s.replace("      message_id: ok ? messageId : null,\n", "", 1)))
case("the classifier stops emitting a field Record Inbound reads -> refuses", "never emits",
     mutate_file=("code_classify_reply.js", lambda s: s.replace(
         "    freemail: FREEMAIL.indexOf(fromDomain) !== -1,\n", "", 1)))
case("Build Follow-Up reads a field Find Due Follow-Ups does not return -> refuses", "does not produce",
     mutate_file=("code_followup.js", lambda s: s.replace("r.first_variant", "r.first_touch_variant", 1)))
case("Find Due Follow-Ups stops returning the claims library -> refuses", "does not produce",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "         WHERE k.active)                                                AS library",
         "         WHERE k.active)                                                AS claims", 1)))
case("Assemble Follow-Up reads a field Build Follow-Up never emits -> refuses", "never emits",
     mutate_file=("code_followup_assemble.js", lambda s: s.replace("src.absent_areas", "src.missing_areas", 1)))
case("Build Follow-Up stops emitting the request the Claude node sends -> refuses", "never emits",
     mutate_file=("code_followup.js", lambda s: s.replace(
         "      request: Object.assign({}, CLAUDE_REQUEST, {", "      req: Object.assign({}, CLAUDE_REQUEST, {", 1)))
case("Assemble Follow-Up stops emitting `write` (Drop Failed Generations reads it) -> refuses", "never emits",
     mutate_file=("code_followup_assemble.js", lambda s: s.replace("      write: false,", "      written: false,", 1)
                  .replace("    write: true,", "    written: true,", 1)))
case("the notification reads a field Record Inbound does not return -> refuses", "does not produce",
     mutate_file=("code_notify.js", lambda s: s.replace("r.fit_score === null", "r.score === null", 1)))
case("a Code node keeps an unsubstituted build placeholder -> refuses", "unsubstituted placeholders",
     mutate_file=("code_notify.js", lambda s: s.replace("const LABEL =", "const X = __FORGOTTEN__;\nconst LABEL =", 1)))

# --- the email nodes ---------------------------------------------------------
case("an email node would carry n8n's attribution footer -> refuses", "attribution",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"options": {"appendAttribution": False},', '"options": {},', 1)))
case("an email node goes HTML -> refuses", "plain text",
     mutate_file=("build_workflow.py", lambda s: s.replace('"emailFormat": "text",', '"emailFormat": "html",', 1)))

# --- identity ---------------------------------------------------------------
case("no sender name anywhere -> refuses, no default", "NOVASCOUT_SENDER_NAME is not set",
     env_overrides={"NOVASCOUT_SENDER_NAME": None})
case("no usable mailbox address -> refuses", "NOVASCOUT_MAILBOX_ADDRESS",
     env_overrides={"NOVASCOUT_MAILBOX_ADDRESS": "not-an-address"})


# --- the operator's address is runtime data, never a literal -----------------
def operator_stays_out(tmp):
    for root, _, files in os.walk(os.path.join(tmp, "out")):
        for f in files:
            text = read(os.path.join(root, f))
            for addr in ("op@env-var.example", "op@env-file.example"):
                if addr in text:
                    return "the operator address %s reached %s" % (addr, f)
    mw = load(tmp, "mailbox-watch.json")
    for name in ("Load Settings", "Record Inbound"):
        if "FROM settings WHERE key = 'operator_email'" not in node(mw, name)["parameters"]["query"]:
            return "%s does not read the address from the settings table" % name
    return None


positive(
    "an operator address in .env or the environment never reaches a workflow -- it is runtime data",
    operator_stays_out,
    env_overrides={"NOVASCOUT_OPERATOR_EMAIL": "op@env-var.example", "NOVASCOUT_ENV_FILE": "{tmp}/test.env"},
    files={"test.env": "NOVASCOUT_OPERATOR_EMAIL=op@env-file.example\n"},
)
case("a Code node carries a literal email address -> refuses", "literal email address",
     mutate_file=("code_notify.js", lambda s: s.replace(
         "const LABEL =", "// questions: someone@example.org\nconst LABEL =", 1)))
case("Record Inbound stops returning the operator address -> refuses", "does not produce",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "AS operator_email\n  FROM p", "AS operator_address\n  FROM p", 1)))
case("Normalise Sent reads a field Load Settings does not return -> refuses", "does not produce",
     mutate_file=("code_mirror_sent.js", lambda s: s.replace("settings.operator_email", "settings.operator", 1)))
case("Normalise Sent reads its messages from a node that is not there -> refuses", "no node named",
     mutate_file=("code_mirror_sent.js", lambda s: s.replace("$('Sent Folder')", "$('Sent')", 1)))

# --- Detect Positive Signal / Build Notification wiring ----------------------
case("Detect Positive Signal reads a field Record Inbound does not produce -> refuses", "does not produce",
     mutate_file=("code_detect_signal.js", lambda s: s.replace("r.classification", "r.classification_x", 1)))
case("Build Notification reads a signal field Detect Positive Signal does not emit -> refuses", "does not produce",
     mutate_file=("code_notify.js", lambda s: s.replace("SIGNAL_LABEL[r.signal]", "SIGNAL_LABEL[r.sentiment]", 1)))

# --- IMAP Health ---------------------------------------------------------------
# Two process boundaries (pre-flight JSON file -> checker HTTP answer) before
# the node boundaries -- each guarded like one.
case("Assess Health reads a field Load Health State does not return -> refuses", "does not produce",
     mutate_file=("code_health.js", lambda s: s.replace("ms(db.sent_synced_at)", "ms(db.sent_synced)", 1)))
case("Assess Health reads a field the checker never sends -> refuses", "does not produce",
     mutate_file=("imap_health_server.py", lambda s: s.replace('"hint": None,', '"advice": None,', 1)))
case("Load Health State reads a field the pre-flight never writes -> refuses", "does not produce",
     mutate_file=("imap_preflight.py", lambda s: s.replace('"subject": header_text', '"title": header_text', 1)))
case("Assess Health stops emitting a state field Record Health writes -> refuses", "never emits",
     mutate_file=("code_health.js", lambda s: s.replace("      consecutive_failures: consecutive,\n", "", 1)))
case("Load Health State's payload expression drops grace_min -> refuses", "never sets it",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "\"grace_min: $('Config').first().json.grace_min })] }}\"", "\"})] }}\"", 1)))
case("Section 8 adds a health problem code the JS does not know -> refuses", "problem codes drifted",
     mutate_doc=lambda d: d.replace("inbox-missed|sent-missed)", "inbox-missed|sent-missed|smtp-failed)", 1))
case("Section 8 drops a mailbox_health column Record Health writes -> refuses", "does not define",
     mutate_doc=lambda d: d.replace("failing_since, last_checked_at, last_ok_at,", "failing_since, last_checked_at,", 1))
case("docker-compose has no imap-health service -> refuses", "no 'imap-health' service",
     mutate_compose=lambda c: c.replace("\n  imap-health:\n", "\n  imap-checker:\n", 1))
case("the checker service publishes a port -> refuses", "publishes a port",
     mutate_compose=lambda c: c.replace("    read_only: true\n", "    read_only: true\n    ports:\n      - \"8765:8765\"\n", 1))
case("Section 9 sets the health check to every 5 minutes -> refuses", "15-30 minutes",
     mutate_doc=lambda d: d.replace("health check runs every 20 minutes", "health check runs every 5 minutes", 1))
case("IMAP Health would alert on a single failing check -> refuses", "single failing check",
     mutate_file=("build_workflow.py", lambda s: s.replace('"confirm_after": 2,', '"confirm_after": 1,', 1)))
positive(
    "Section 9's health-check interval flows straight into the IMAP Health schedule",
    lambda tmp: None if node(load(tmp, "imap-health.json"), "Every 25 Minutes")["parameters"]["rule"]["interval"]
    == [{"field": "minutes", "minutesInterval": 25}] else "the schedule is not every 25 minutes",
    mutate_doc=lambda d: d.replace("health check runs every 20 minutes", "health check runs every 25 minutes", 1),
)

# --- the dry run tests what ships -------------------------------------------
case("the dry-run variant keeps its schedule trigger (an extra difference) -> refuses",
     "differs from the shipped workflow",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"send_probability": 1},\n                           {"Every 10 Minutes"})',
         '"send_probability": 1},\n                           set())', 1)),
     env_overrides={"SENDTRACK_VARIANTS_OUT": "{tmp}/variants", "SENDTRACK_DRYRUN_NOW": "2026-09-14T10:00:00Z"})
case("the dry-run IMAP Health variant keeps its schedule trigger -> refuses", "IMAP Health variant drifted",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{},\n                             {"Every %d Minutes" % HEALTH_INTERVAL_MIN})',
         '{},\n                             set())', 1)),
     env_overrides={"SENDTRACK_VARIANTS_OUT": "{tmp}/variants", "SENDTRACK_DRYRUN_NOW": "2026-09-14T10:00:00Z"})

print("drift guards for Workflow 6\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    sys.exit(1)

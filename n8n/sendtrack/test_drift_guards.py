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
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")
COMPOSE = os.path.join(REPO, "docker-compose.yml")

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
    env["SENDTRACK_OUT"] = os.path.join(tmp, "out")
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


def setup(mutate_doc=None, mutate_compose=None, mutate_file=None):
    tmp = tempfile.mkdtemp(prefix="novascout-sendtrack-drift-")
    shutil.copytree(HERE, os.path.join(tmp, "sendtrack"),
                    ignore=shutil.ignore_patterns("__pycache__"))
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


def case(label, expect_in, mutate_doc=None, mutate_compose=None, mutate_file=None, env_overrides=None):
    tmp = setup(mutate_doc, mutate_compose, mutate_file)
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


def positive(label, check, mutate_doc=None, env_overrides=None, files=None):
    tmp = setup(mutate_doc)
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
case("the follow-up template carries a different sentence -> refuses", "VERBATIM",
     mutate_file=("code_followup.js", lambda s: s.replace("I won't follow up.", "I will not follow up.", 1)))
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
case("the follow-up node grows a third template -> refuses", "templates for follow-ups",
     mutate_file=("code_followup.js", lambda s: s.replace(
         "  2: 'One last note", "  3: 'Really the last one.',\n  2: 'One last note", 1)))

# --- Section 12 and the sender clock ----------------------------------------
case("Section 12 adds a country with no business-hours clock -> refuses", "geography list",
     mutate_doc=lambda d: d.replace("Brazil, Argentina.", "Brazil, Argentina, Chile.", 1))
case("the clock table drops a Section 12 country -> refuses", "geography list",
     mutate_file=("code_decide.js", lambda s: s.replace(
         "  'Egypt':          { utc_offset_min: 120,  weekend: [5, 6] },\n", "", 1)))
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

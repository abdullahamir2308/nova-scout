"""Tests for the IMAP health checker's two Python pieces.

    python n8n/sendtrack/test_imap_health.py

1. imap_health_server.parse_result -- every way a pre-flight run can end, turned
   into the answer Assess Health judges.
2. imap_preflight.message_id against Mailbox Watch's own messageId() in
   code_classify_reply.js and code_mirror_sent.js, on the same inputs. The health
   check compares Message-IDs as strings; if the pre-flight derived a key even
   slightly differently, every message would look missed.

imap_preflight.py is a script that connects on import, so message_id is lifted
out of it with ast -- the same idea as harness.js's extractFunctions: the test
calls the real function, not a copy.

Exits non-zero on any failure.
"""
import ast
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import imap_health_server as srv  # noqa: E402  (no side effects: main() is guarded)

PASSED, FAILED = [], []


def check(label, actual, expected):
    if actual == expected:
        PASSED.append(label)
    else:
        FAILED.append((label, "expected %r\n         actual   %r" % (expected, actual)))


# --- 1. parse_result -------------------------------------------------------------

OUT_PASSED = ("credentials in .env: ...\n1. connect  ok  imappro.zoho.com:993 over TLS\n"
              "5. Sent     ok  4 messages, 4 dated, 4 with headers\n\nIMAP CHECK: PASSED\n\nSent history by day\n")
IDS = {"hours": 48, "since": "x", "inbox": [{"message_id": "<a@b>", "at": "t", "from": "f", "subject": "s"}],
       "inbox_no_message_id": 1, "sent": [], "sent_no_message_id": 0, "sent_undated": 2}

r = srv.parse_result(0, OUT_PASSED, "", IDS)
check("passed with a Message-ID list -> ok, lists and counts carried through",
      (r["ok"], r["status"], r["inbox"], r["inbox_no_message_id"], r["sent_undated"], r["note"]),
      (True, "passed", IDS["inbox"], 1, 2, None))

r = srv.parse_result(0, OUT_PASSED, "", None)
check("passed but no list written -> NOT ok (nothing to compare is not healthy)",
      (r["ok"], r["stage"]), (False, "ids"))

r = srv.parse_result(1, OUT_PASSED, "warm-up table not found in /opt/novascout/NovaScout_MasterRef.md\n", IDS)
check("IMAP passed, the warm-up report after it failed -> still ok, with a visible note",
      (r["ok"], r["exit_code"], r["note"]),
      (True, 1, "pre-flight exited 1 after the IMAP check passed: warm-up table not found in "
                "/opt/novascout/NovaScout_MasterRef.md"))

OUT_FAILED = ("credentials in .env: ...\n\nIMAP CHECK: FAILED at connect\n"
              "  imappro.zoho.com does not resolve: [Errno -2] Name does not resolve\n"
              "  hint: check NOVASCOUT_IMAP_HOST\n")
r = srv.parse_result(1, OUT_FAILED, "", None)
check("FAILED at a stage, with a hint -> failed, stage/detail/hint split out",
      (r["ok"], r["status"], r["stage"], r["detail"], r["hint"]),
      (False, "failed", "connect", "imappro.zoho.com does not resolve: [Errno -2] Name does not resolve",
       "check NOVASCOUT_IMAP_HOST"))

r = srv.parse_result(1, "1. connect  ok\n\nIMAP CHECK: FAILED at login\n  server refused x: b'AUTHENTICATIONFAILED'\n", "", None)
check("FAILED without a hint -> hint is None", (r["stage"], r["hint"]), ("login", None))

r = srv.parse_result(2, "credentials in .env: NOVASCOUT_MAILBOX_PASSWORD=MISSING\n\n"
                        "IMAP CHECK: NOT RUN -- add NOVASCOUT_MAILBOX_PASSWORD to /tmp/x.env\n", "", None)
check("NOT RUN (credentials missing in the container) -> not ok, says so",
      (r["ok"], r["status"], r["detail"].startswith("IMAP CHECK: NOT RUN -- add NOVASCOUT_MAILBOX_PASSWORD")),
      (False, "not-run", True))

r = srv.parse_result(1, "", "Traceback (most recent call last):\n  ...\nKeyError: 'x'\n", None)
check("a crash with no verdict -> not ok, the last stderr line as the reason",
      (r["ok"], r["status"], r["detail"]), (False, "error", "pre-flight exited 1 without a result: KeyError: 'x'"))

check("output_tail is bounded", len(srv.parse_result(0, "x" * 10000, "", None)["output_tail"]) <= 1503, True)

# The answer's keys are what Assess Health reads (the build also asserts this).
r = srv.parse_result(0, OUT_PASSED, "", IDS)
check("the answer carries every field Assess Health reads",
      sorted(k for k in ("ok", "stage", "detail", "hint", "hours") if k not in r), [])

# The credentials file holds only the four keys, and nothing else from the environment.
path = srv.write_env_file({"NOVASCOUT_MAILBOX_ADDRESS": "a@b.example", "NOVASCOUT_MAILBOX_PASSWORD": "pw",
                           "NOVASCOUT_IMAP_HOST": "imap.example", "NOVASCOUT_IMAP_PORT": "993",
                           "N8N_ENCRYPTION_KEY": "never", "POSTGRES_PASSWORD": "never"})
try:
    with io.open(path, encoding="utf-8") as fh:
        written = fh.read()
finally:
    os.unlink(path)
check("the private env file holds the IMAP settings and nothing else",
      sorted(re.findall(r"^(\w+)=", written, re.M)),
      ["NOVASCOUT_IMAP_HOST", "NOVASCOUT_IMAP_PORT", "NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_MAILBOX_PASSWORD"])

# --- 2. Message-ID parity with Mailbox Watch ------------------------------------------

with io.open(os.path.join(HERE, "imap_preflight.py"), encoding="utf-8") as fh:
    tree = ast.parse(fh.read())
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "message_id"]
assert fn, "message_id() not found in imap_preflight.py"
ns = {"re": re}
exec(compile(ast.Module(body=fn, type_ignores=[]), "imap_preflight.py", "exec"), ns)
py_message_id = ns["message_id"]

CASES = [
    "<1a0aaa191d5.11b51ba0142555.2153062712890912646@amitrixlabs.com>",
    "  <CA+d=rHdQmvg_ikhHOejzOUFd=_X3v8jCmmE7sXN04vjKV2wQYA@mail.gmail.com>  ",
    "<17022299783852949353@google.com>",
    "\r\n <folded@example.com>",
    "<a@b> <c@d>",
    "bare@example.com",
    "bare@example.com trailing words",
    "<bracketed@example.com",
    "",
    "   ",
    "<with space@example.com>",
]
js = r"""
const path = require('path');
const { extractFunctions } = require('./harness');
const own = (s) => s.replace('__OWN_DOMAIN__', JSON.stringify('amitrixlabs.com'));
const a = extractFunctions(path.join(__dirname, 'code_classify_reply.js'), '// Node body', ['messageId'], own).messageId;
const b = extractFunctions(path.join(__dirname, 'code_mirror_sent.js'), '// Node body', ['messageId'], own).messageId;
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((c) => [a(c), b(c)])));
"""
p = subprocess.run(["node", "-e", js], cwd=HERE, input=json.dumps(CASES), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, encoding="utf-8")
if p.returncode != 0:
    FAILED.append(("run the JS messageId()", p.stderr[-500:]))
else:
    for case, (classify_id, mirror_id) in zip(CASES, json.loads(p.stdout)):
        check("message_id(%r): pre-flight == Inbox (classify) == Sent (mirror)" % case,
              (py_message_id(case), py_message_id(case)), (classify_id, mirror_id))

print("IMAP health checker\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)

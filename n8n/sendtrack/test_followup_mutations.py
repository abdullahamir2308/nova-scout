"""Proves every rule Assemble Follow-Up adds fails a test when it is broken.

    python n8n/sendtrack/test_followup_mutations.py

Same method as ../drafting/test_rule_mutations.py, for the follow-up node body
(code_followup_assemble.js, drafting skill v3 section 8). For each tag it can
push, the push is removed in a temp copy and test_followup.js must FAIL; then a
few behaviours that are not tags are broken by targeted edits. The claim rules
it inherits from drafting's code_assemble.js are proved by that stage's own
mutation suite; this one covers what the follow-up adds and the frame it
appends.

Exits non-zero if any mutation survives.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DRAFTING = os.path.join(HERE, "..", "drafting")
TARGET = "code_followup_assemble.js"

# (label, file, old, new) -- each `old` must occur exactly once.
STRUCTURAL = [
    ("the opt-out is appended, verbatim, before the signature", TARGET,
     "  OPT_OUT,\n  '',\n  SIGNATURE,", "  SIGNATURE,"),
    ("the first email is quoted below the signature", TARGET,
     "  'On ' + when + ', ' + SENDER_NAME + ' wrote:',\n  quoted,\n", ""),
    ("an HTTP error writes nothing -- no fallback, the lead is due again", TARGET,
     "if (resp.error) {", "if (false) {"),
    ("a cut-off response writes nothing", TARGET,
     "if (resp.stop_reason !== 'end_turn') {", "if (false) {"),
    ("a response missing a schema field writes nothing", TARGET,
     "FU_FIELDS.some(function (f) { return typeof parsed[f] !== 'string'; }) ||\n    ", ""),
    ("an uncoded ask is attributed from its text", TARGET,
     "if (j >= 0.5 && (!best || j > best.j)) best = { code: l.code, j: j };", ""),
    ("the subject stays in the first email's thread", TARGET,
     "subject: 'Re: ' + str(src.first_subject).replace(/^(?:re:\\s*)+/i, ''),", "subject: str(src.first_subject),"),
    ("Build Follow-Up offers no line repeating a capability the first email made", "code_followup.js",
     "      !l.capabilities.some(function (c) { return firstCaps.indexOf(c) !== -1; });", "      true;"),
    ("Build Follow-Up holds a #1 with nothing new to add", "code_followup.js",
     "  if (n === 1 && !pools.angle.length && !pools.benefit.length) return null;", ""),
    ("#2 is offered no angle or benefit", "code_followup.js",
     "    angle: n === 1 ? lines(bySlot('angle').filter(fresh)) : [],", "    angle: lines(bySlot('angle')),"),
    ("an email from an older claims list is judged from its text", "code_followup.js",
     "  const firstNote = resolved.retired.length || !resolved.firstCodes.length", "  const firstNote = !resolved.firstCodes.length"),
]


def run_suite(workdir):
    p = subprocess.run(["node", os.path.join(workdir, "sendtrack", "test_followup.js")], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    fails = [ln.strip()[5:] for ln in p.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    if p.returncode != 0 and not fails:
        err = [ln for ln in p.stdout.splitlines() if re.match(r"\w*Error:", ln.strip())]
        fails = ["suite crashed: " + (err[0].strip() if err else "non-zero exit")]
    return p.returncode, fails, p.stdout


def workdir():
    tmp = tempfile.mkdtemp(prefix="novascout-fumut-")
    shutil.copytree(HERE, os.path.join(tmp, "sendtrack"), ignore=shutil.ignore_patterns("__pycache__", "dryrun"))
    os.makedirs(os.path.join(tmp, "drafting"))
    shutil.copy(os.path.join(DRAFTING, "code_assemble.js"), os.path.join(tmp, "drafting", "code_assemble.js"))
    # test_followup.js runs an assembled follow-up through the shared Approval
    # Gate (migration 014).
    shutil.copy(os.path.join(DRAFTING, "code_approval.js"), os.path.join(tmp, "drafting", "code_approval.js"))
    return tmp


def mutated(file, old, new):
    tmp = workdir()
    path = os.path.join(tmp, "sendtrack", file)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    n = src.count(old)
    if n != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit("mutation anchor occurs %d times in %s: %r" % (n, file, old))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src.replace(old, new, 1))
    rc, fails, _ = run_suite(tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return rc, fails


base = workdir()
rc, fails, out = run_suite(base)
shutil.rmtree(base, ignore_errors=True)
if rc != 0:
    sys.exit("BASELINE FAILS: test_followup.js\n" + out[-1500:])
print("baseline: test_followup.js passes unmutated\n")

with open(os.path.join(HERE, TARGET), encoding="utf-8") as fh:
    target_src = fh.read()
tags = []
for t in re.findall(r"flags\.push\('([a-z-]+)'\)", target_src):
    if t not in tags:
        tags.append(t)

survived = []
print("every tag Assemble Follow-Up adds -- its push removed, test_followup.js must fail:")
for tag in tags:
    rc, fails = mutated(TARGET, "flags.push('%s')" % tag, "void 0") if target_src.count("flags.push('%s')" % tag) == 1 \
        else (None, None)
    if rc is None:
        # pushed from several places: remove every push of it (one rule)
        tmp = workdir()
        with open(os.path.join(tmp, "sendtrack", TARGET), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(target_src.replace("flags.push('%s')" % tag, "void 0"))
        rc, fails, _ = run_suite(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
    if rc == 0:
        survived.append(tag)
        print("  SURVIVED %-18s no test failed" % tag)
    else:
        print("  killed   %-18s %d test(s) fail, e.g. \"%s\"" % (tag, len(fails), fails[0][:70] if fails else "?"))

print("\nbehaviours that are not tags -- broken by a targeted edit:")
for label, file, old, new in STRUCTURAL:
    rc, fails = mutated(file, old, new)
    if rc == 0:
        survived.append(label)
        print("  SURVIVED %s" % label)
    else:
        print("  killed   %s  -- %d test(s) fail, e.g. \"%s\"" % (label, len(fails), fails[0][:60] if fails else "?"))

print("\n%d tags + %d behaviours mutated; %d survived" % (len(tags), len(STRUCTURAL), len(survived)))
if survived:
    sys.exit(1)

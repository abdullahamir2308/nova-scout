"""Proves every rule Build Follow-Up depends on fails a test when it is broken.

    python n8n/sendtrack/test_followup_mutations.py

Same method as ../drafting/test_template_mutations.py, for the follow-up node
(code_followup.js, Section 9 Workflow 6 since 2026-10-19, plus the template
functions it embeds from drafting's code_template.js). Each mutation is made in
a temp copy and test_followup.js must FAIL. The template wording and formatting
rules are proved by the drafting stage's own mutation suite; this one covers
what the follow-up node decides and the frame it writes.

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
NODE = os.path.join("sendtrack", "code_followup.js")
TEMPLATE = os.path.join("drafting", "code_template.js")

# (label, file, old, new) -- each `old` must occur exactly once.
MUTATIONS = [
    ("a mark-lost row carries no draft", NODE,
     "  if (r.action === 'mark-lost') {", "  if (false) {"),
    ("a third follow-up is refused", NODE,
     "  if (!(n >= 1 && n <= MAX_FOLLOW_UPS)) {", "  if (false) {"),
    ("a site or SMO is never followed up", NODE,
     "  if (str(r.company_type) !== 'CRO') return null;\n", ""),
    ("the empty-queue placeholder is not a lead", NODE,
     "  if (!it.json || it.json.lead_id === undefined || it.json.lead_id === null) return;\n", ""),
    ("the gate reads whether the address is verified", NODE,
     "    verified: r.verified === true,", "    verified: true,"),
    ("the gate reads the recipient domain's blocklist flag", NODE,
     "    recipient_domain_blocked: r.recipient_domain_blocked === true,\n", ""),
    ("the gate reads settings.auto_approve_email", NODE,
     "    auto_approve: r.auto_approve_email === true,", "    auto_approve: true,"),
    ("the gate reads the country (its Send clock)", NODE,
     "    country: r.country,\n    domain: r.domain,", "    country: 'Turkey',\n    domain: r.domain,"),
    ("a hold is tagged on the variant", NODE,
     "          variant: withTags(fu.variant, decision.tags),", "          variant: fu.variant,"),
    ("the gate's decision is what is written", NODE,
     "          status: decision.status,", "          status: 'approved',"),
    ("the follow-up stays in the first email's thread", TEMPLATE,
     "    subject: 'Re: ' + str(f.first_subject).replace(/^(?:re:\\s*)+/i, ''),",
     "    subject: str(f.first_subject),"),
    ("the first email is quoted below the sign-off", TEMPLATE,
     "    body: lines.concat(SIGN_OFF, ['', 'On ' + when + ', ' + SIGN_OFF[1] + ' wrote:', quoted]).join('\\n'),",
     "    body: lines.concat(SIGN_OFF).join('\\n'),"),
    ("#1 closes with the lead's own arm's close", TEMPLATE,
     "  if (n === 1) lines.push(closeFor(arm, company), '');", "  if (n === 1) lines.push(closeFor('A', company), '');"),
    ("#2 has no close", TEMPLATE,
     "  if (n === 1) lines.push(closeFor(arm, company), '');", "  lines.push(closeFor(arm, company), '');"),
    ("the sign-off carries the identity Send checks", TEMPLATE,
     'const SIGN_OFF = ["Cheers!", "Abdullah Amir", "Founder, Amitrix Labs"];',
     'const SIGN_OFF = ["Cheers!", "Abdullah Amir"];'),
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
    shutil.copy(os.path.join(DRAFTING, "code_template.js"), os.path.join(tmp, "drafting", "code_template.js"))
    return tmp


def main():
    base = workdir()
    rc, fails, out = run_suite(base)
    if rc != 0:
        print(out)
        raise SystemExit("baseline: test_followup.js does not pass unmutated -- fix that first")
    shutil.rmtree(base, ignore_errors=True)
    print("baseline: test_followup.js passes unmutated\n")

    survived = []
    for label, rel, old, new in MUTATIONS:
        work = workdir()
        p = os.path.join(work, rel)
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        n = src.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its anchor occurs %d times in %s (want exactly 1)" % (label, n, rel))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src.replace(old, new))
        rc, fails, _ = run_suite(work)
        shutil.rmtree(work, ignore_errors=True)
        if rc == 0:
            survived.append(label)
            print("  SURVIVED  %s" % label)
        else:
            print("  killed    %s  (%d failing: %s)" % (label, len(fails), fails[0] if fails else "?"))

    print("\n%d mutations, %d killed, %d survived" % (len(MUTATIONS), len(MUTATIONS) - len(survived), len(survived)))
    if survived:
        sys.exit(1)


if __name__ == "__main__":
    main()

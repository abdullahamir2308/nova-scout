"""Proves every rule Workflow 4 enforces in code fails a test when it is broken.

    python n8n/drafting/test_rule_mutations.py

A rule with no failing test behind it is a rule nobody would notice losing. For
each one, this copies the drafting directory to a temp dir, breaks exactly that
rule in the shipped source, runs the unit suite against the broken copy, and
requires the suite to FAIL. A mutation the suite survives is reported as a gap.

Two kinds of mutation:

  * every tag Assemble Drafts can put on a draft -- found by scanning
    code_assemble.js for `flags.push('<tag>')`, so a new rule is covered the
    moment it is written -- has its push removed, one at a time;
  * the behaviours that are not tags (the opt-out appended verbatim, a failed
    or refused call writing nothing, the library link appended only after
    warm-up, the trial fact never calling the prospect a sponsor, no sampling
    parameter in the request) are each broken by a targeted edit.

Rules skill v3 added are marked [v3] in the output.

Exits non-zero if any mutation survives.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# The skill v3 rules the 2026-10-02 brief listed. Everything else is carried
# over from v1/v2 and is proved here too.
V3_TAGS = {
    "long", "ask-none", "ask-multiple", "ask-scheduling", "product-name-repeat", "product-name-unbracketed",
    "subject-product", "ai-repeat", "subject-ai", "ai-powered", "prospect-sponsor", "claim-guarantee",
    "claim-number", "claim-visitor-id", "claim-named-tool", "claim-language", "claim-chatbot",
    "proof-missing", "proof-geo", "claim-code", "ungrounded-area",
    # Added 2026-10-02 with migration 013: the no-repeat rule and the two settled claims.
    "claim-repeat", "claim-books", "claim-sop",
}

TESTS = {"code_assemble.js": "test_assemble.js", "code_assess.js": "test_grounding.js"}

# (label, file, old, new) -- each `old` must occur exactly once.
STRUCTURAL = [
    ("[v1] the opt-out is appended verbatim", "code_assemble.js",
     "const emailMessage = [src.email_greeting, '', emailCore, '', OPT_OUT].join('\\n');",
     "const emailMessage = [src.email_greeting, '', emailCore].join('\\n');"),
    ("[v3] a refusal or cut-off response writes nothing (skill section 6)", "code_assemble.js",
     "if (resp.stop_reason !== 'end_turn') {",
     "if (false) {"),
    ("[v3] an HTTP error writes nothing -- no fallback, the lead stays queued", "code_assemble.js",
     "if (resp.error) {",
     "if (false) {"),
    ("[v3] a response missing a schema field writes nothing", "code_assemble.js",
     "FIELDS.some(function (f) { return typeof parsed[f] !== 'string'; }) ||\n    ",
     ""),
    ("[v3] a response without both claim lists writes nothing", "code_assemble.js",
     " ||\n    CLAIM_LISTS.some(function (f) { return !Array.isArray(parsed[f]); })",
     ""),
    ("[v3] an ask the model did not list is attributed from its text", "code_assemble.js",
     "if (!hasSlot('ask')) { const a = inferAsk(askText); if (a) inferred.push(a); }",
     ""),
    ("[v3] a proof the model did not list is attributed from its text", "code_assemble.js",
     "if (!hasSlot('proof')) { const p = inferProof(core); if (p) inferred.push(p); }",
     ""),
    ("[v3] ask attribution needs at least half the words in common", "code_assemble.js",
     "if (j >= 0.5 && (!best || j > best.j))",
     "if (j >= 0 && (!best || j > best.j))"),
    ("[v3] each message's variant records only its own claims", "code_assemble.js",
     "const linkedinCodes = linkedinClaims.used.join('.');",
     "const linkedinCodes = emailClaims.used.join('.');"),
    # The Sonnet 5.5 migration guide: "A response can begin with thinking blocks,
    # so code that reads content[0].text breaks."
    ("[v3] content blocks are read by type, not as content[0].text", "code_assemble.js",
     "const text = (Array.isArray(resp.content) ? resp.content : [])\n"
     "  .filter(function (b) { return b && b.type === 'text'; })\n"
     "  .map(function (b) { return b.text; })\n"
     "  .join('');",
     "const text = resp.content[0].text;"),
    ("[link policy] the library link is appended only after warm-up", "code_assemble.js",
     "const linkLines = week > LINK_FREE_WEEKS ? (src.library_link || []) : [];",
     "const linkLines = src.library_link || [];"),
    ("[v3] the fact sheet's numbering does not license number words", "code_assemble.js",
     "factCorpus.replace(/^\\d+\\.\\s/gm, '')",
     "factCorpus"),
    ("[v3] the trial fact never calls the prospect a sponsor", "code_assess.js",
     "' registered under your company. '",
     "' with you as the sponsor. '"),
    ("[v3] the trial hook instruction never teaches \"sponsoring\"", "code_assess.js",
     "'running ...\" -- about them, not about ClinicalTrials.gov, and never \"sponsoring\".',",
     "'sponsoring ...\" -- about them, not about ClinicalTrials.gov.',"),
    ("[v3] the request carries no sampling parameter", "code_assess.js",
     "const request = Object.assign({}, CLAUDE_REQUEST, {",
     "const request = Object.assign({ temperature: 0.45 }, CLAUDE_REQUEST, {"),
    ("[v3] only the lead's own proof line is offered", "code_assess.js",
     "proof: lines(forCountry(bySlot('proof'), lead.country)),",
     "proof: lines(bySlot('proof')),"),
    ("[v3] the prompt names every claim pair that repeats a capability", "code_assess.js",
     "const pairs = repeatPairs(pools.description.concat(pools.benefit));",
     "const pairs = [];"),
    ("[v3] a description or benefit line shows the model its capabilities", "code_assess.js",
     "? '  (about: ' + l.capabilities.join(', ') + ')' : '';",
     "? '' : '';"),
    ("[v3] a lead missing a v3 slot is held back before any API call", "code_assess.js",
     "const libraryHolds = groundable && libraryGap !== null;",
     "const libraryHolds = false;"),
]


def run_suite(workdir, test_file):
    p = subprocess.run(["node", os.path.join(workdir, test_file)], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    fails = [ln.strip()[5:] for ln in p.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    if p.returncode != 0 and not fails:
        # A crash is a failure too; say which error stopped the suite.
        err = [ln for ln in p.stdout.splitlines() if re.match(r"\w*Error:", ln.strip())]
        fails = ["suite crashed: " + (err[0].strip() if err else "non-zero exit")]
    return p.returncode, fails, p.stdout


def mutate(file, old, new):
    tmp = tempfile.mkdtemp(prefix="novascout-mut-")
    work = os.path.join(tmp, "drafting")
    shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("__pycache__"))
    path = os.path.join(work, file)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    n = src.count(old)
    if n != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit("mutation anchor occurs %d times in %s: %r" % (n, file, old))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src.replace(old, new, 1))
    rc, fails, out = run_suite(work, TESTS[file])
    shutil.rmtree(tmp, ignore_errors=True)
    return rc, fails, out


# Baseline: the unmutated suites must pass, or every "killed" below is noise.
for test in sorted(set(TESTS.values())):
    rc, fails, out = run_suite(HERE, test)
    if rc != 0:
        sys.exit("BASELINE FAILS: %s\n%s" % (test, out[-1500:]))
print("baseline: test_assemble.js and test_grounding.js pass unmutated\n")

with open(os.path.join(HERE, "code_assemble.js"), encoding="utf-8") as fh:
    assemble_src = fh.read()
tags = []
for t in re.findall(r"flags\.push\('([a-z-]+)'\)", assemble_src):
    if t not in tags:
        tags.append(t)

survived = []
print("every tag Assemble Drafts can emit -- its push removed, test_assemble.js must fail:")
for tag in tags:
    # Remove every push of this tag (a tag pushed from two places is one rule).
    tmp_old = "flags.push('%s')" % tag
    count = assemble_src.count(tmp_old)
    tmp = tempfile.mkdtemp(prefix="novascout-mut-")
    work = os.path.join(tmp, "drafting")
    shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(work, "code_assemble.js"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(assemble_src.replace(tmp_old, "void 0"))
    rc, fails, _ = run_suite(work, "test_assemble.js")
    shutil.rmtree(tmp, ignore_errors=True)
    mark = "[v3] " if tag in V3_TAGS else "     "
    if rc == 0:
        survived.append(tag)
        print("  SURVIVED %s%-26s  no test failed" % (mark, tag))
    else:
        print("  killed   %s%-26s  %d test(s) fail, e.g. \"%s\"" % (mark, tag, len(fails), fails[0][:70] if fails else "?"))

print("\nbehaviours that are not tags -- broken by a targeted edit:")
for label, file, old, new in STRUCTURAL:
    rc, fails, _ = mutate(file, old, new)
    if rc == 0:
        survived.append(label)
        print("  SURVIVED %s" % label)
    else:
        print("  killed   %s  -- %d test(s) in %s fail, e.g. \"%s\"" % (label, len(fails), TESTS[file], fails[0][:60] if fails else "?"))

missing_v3 = sorted(V3_TAGS - set(tags))
print("\n%d tags + %d behaviours mutated; %d survived" % (len(tags), len(STRUCTURAL), len(survived)))
if missing_v3:
    print("v3 tags expected but not found in code_assemble.js: %r" % missing_v3)
if survived or missing_v3:
    sys.exit(1)

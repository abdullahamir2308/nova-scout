"""Proves every template rule in code_template.js fails a test when it is broken.

    python n8n/drafting/test_template_mutations.py

Each mutation below breaks one rule -- the wording, the arm split, the SMYKM
order, a formatting rule, a hold of the gate, a node-body guard -- in a temp
copy of code_template.js, and test_template.js must FAIL. A rule that can be
broken without a failing test is a rule nobody is checking.

Exits non-zero if any mutation survives.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "code_template.js"

# (label, old, new) -- each `old` must occur exactly once in code_template.js.
MUTATIONS = [
    # the approved wording
    ("the introduction, word for word",
     "but I'm Abdullah Amir and I'm the Founder", "but I am Abdullah Amir and the Founder"),
    ("the pitch, word for word",
     "so you win more studies.\";", "so you win more trials.\";"),
    ("arm A's close, word for word",
     "and I'll send a calendar invite along accordingly.", "and I'll send a calendar invite."),
    ("arm B's close, word for word",
     'const CLOSE_B = "Worth a look for {company}?";', 'const CLOSE_B = "Worth a quick look for {company}?";'),
    ("the subject, word for word",
     'const SUBJECT_WITH_FACT = "{company} + {fact} + sponsor inquiries";',
     'const SUBJECT_WITH_FACT = "{company}: {fact} + sponsor inquiries";'),
    ("the trials sentence (A), word for word",
     "sponsors are clearly paying attention to {company}", "sponsors are paying attention to {company}"),
    ("the areas sentence (B), word for word",
     "will usually check {company}'s website before they ever email you", "will check {company}'s website first"),
    ("follow-up #1, word for word",
     "in case it got buried.", "in case you missed it."),
    ("follow-up #2, word for word",
     "and won't follow up again after this.", "and won't write again."),
    ("the role-inbox greeting",
     'const GREETING_UNNAMED = "Hi there,";', 'const GREETING_UNNAMED = "Hello,";'),
    # the A/B split
    ("even lead_id is arm A", "const ARM_BY_PARITY = { even: 'A', odd: 'B' };", "const ARM_BY_PARITY = { even: 'B', odd: 'A' };"),
    ("arm B uses sentence B (trials)", "fill(arm === 'A' ? SMYKM_TRIALS_A : SMYKM_TRIALS_B,", "fill(SMYKM_TRIALS_A,"),
    ("arm B uses sentence B (areas)", "fill(arm === 'A' ? SMYKM_AREAS_A : SMYKM_AREAS_B, {", "fill(SMYKM_AREAS_A, {"),
    # the SMYKM choice
    ("trials come before areas", "const SMYKM_ORDER = ['trials', 'areas', 'city', 'company'];",
     "const SMYKM_ORDER = ['areas', 'trials', 'city', 'company'];"),
    ("city comes before the company line", "const SMYKM_ORDER = ['trials', 'areas', 'city', 'company'];",
     "const SMYKM_ORDER = ['trials', 'areas', 'company', 'city'];"),
    ("a lead with no other fact gets the company line", "    if (kind === 'company') return 'company';\n", ""),
    ("the company line is written", "  if (kind === 'company') return fill(SMYKM_COMPANY, { company: company });\n", ""),
    ("the company line, word for word", "will usually start on your website,", "will usually begin on your website,"),
    ("zero trials is no trials fact", "Number.isInteger(facts.trials) && facts.trials > 0", "Number.isInteger(facts.trials)"),
    # formatting
    ("singular trial", "return n === 1 ? one : many;", "return many;"),
    ("at most two areas", "const MAX_AREAS = 2;", "const MAX_AREAS = 3;"),
    ("oncology first, then taxonomy order",
     "    return THERAPEUTIC_AREAS.indexOf(a) - THERAPEUTIC_AREAS.indexOf(b);",
     "    return THERAPEUTIC_AREAS.indexOf(b) - THERAPEUTIC_AREAS.indexOf(a);"),
    ("areas in lowercase", ".slice(0, MAX_AREAS).map(function (a) { return a.toLowerCase(); });", ".slice(0, MAX_AREAS);"),
    ("'Other' is never an area", "const UNSPECIFIC_AREAS = ['Other'];", "const UNSPECIFIC_AREAS = [];"),
    ("a phase range only when every phase is listed",
     "if (nums[nums.length - 1] - nums[0] + 1 === nums.length) {", "if (true) {"),
    ("no phase drops 'from {phases}'",
     "if (s.indexOf(\" from {phases}\") !== -1 && !v.phases) s = s.split(' from {phases}').join('');", ""),
    ("a single phase reads 'in', not 'from'",
     "if (s.indexOf(\" from {phases}\") !== -1 && !/ to /.test(v.phases)) s = s.split(' from {phases}').join(' in {phases}');",
     ""),
    ("a range keeps 'from'", "&& !/ to /.test(v.phases))", "&& v.phases)"),
    ("'an' before a vowel", "return /^[aeiou]/i.test(fold(word)) ? 'an' : 'a';", "return 'a';"),
    ("the possessive of a name ending in s", "return /s$/i.test(name) ? name + \"'\" : name + \"'s\";", "return name + \"'s\";"),
    ("legal suffixes are stripped", "    s = s.replace(LEGAL_FORM_RE, '');\n", ""),
    ("a trailing parenthetical is stripped", "    s = s.replace(/\\s*\\([^()]*\\)\\s*$/, '');", "    s = s;"),
    ("the Romanian leading S.C. is stripped", "  s = s.replace(LEGAL_PREFIX_RE, '');\n", ""),
    ("an ALL-CAPS name is fixed", "  s = fixCaps(s);\n  return s || str(fallback);", "  return s || str(fallback);"),
    ("an acronym is kept", "  if (letters.length <= 3) return true;\n", ""),
    ("honorifics are skipped",
     "  while (parts.length > 1 && HONORIFICS.indexOf(parts[0].toLowerCase().replace(/\\.+$/, '')) !== -1) parts.shift();\n", ""),
    ("a bare initial is no first name", "  if (first.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, '').length < 2) return '';\n", ""),
    ("a role inbox is not a person", "  if (isRoleInbox(e)) return 'role-inbox';\n", ""),
    ("a name is used only when the address is that person's",
     "  if (str(contactName) && addressIsPersons(e, contactName) && firstName(contactName)) return 'named';",
     "  if (str(contactName) && firstName(contactName)) return 'named';"),
    ("an unfilled placeholder throws", "  if (left) throw new Error(", "  if (false) throw new Error("),
    # the gate
    ("no address is held", "  if (!EMAIL_RE.test(str(ctx.email))) tags.push('no-address');\n  else ",
     "  "),
    ("an unverified address is held", "  else if (ctx.verified !== true) tags.push('unverified-address');", ""),
    ("a country with no clock is held",
     "  if ((clockKeys || []).indexOf(str(ctx.country).toLowerCase()) === -1) tags.push('no-send-clock');", ""),
    ("the clock is looked up case-insensitively",
     "(clockKeys || []).indexOf(str(ctx.country).toLowerCase())", "(clockKeys || []).indexOf(str(ctx.country))"),
    ("a blocklisted domain is held",
     "  if (ctx.lead_domain_blocked === true || ctx.recipient_domain_blocked === true) tags.push('blocklisted');", ""),
    ("auto-approval off holds", "  if (!tags.length && ctx.auto_approve !== true) reasons.push(AUTO_APPROVE_OFF);", ""),
    ("only an approval is approved_by auto", "    approved_by: approved ? 'auto' : null,", "    approved_by: 'auto',"),
    # the recruiting-trial count is the company's own
    ("a study counts only when the company sponsors or collaborates on it",
     "    return names.some(function (n) { return isSponsor(companyName, n); });", "    return true;"),
    ("another company containing the name is not the company",
     "  if (s.indexOf(c + ' ') !== 0) return false;\n", "  if (s.indexOf(c) === -1) return false;\n"),
    ("only corporate filler may follow the name",
     "  return s.slice(c.length + 1).split(' ').every(function (w) { return SPONSOR_FILLER.indexOf(w) !== -1; });",
     "  return true;"),
    ("a lookup that returned fewer studies than it matched is unverifiable",
     "  if (!(typeof ct.totalCount === 'number') || ct.totalCount > studies.length) return 0;", ""),
    ("the template prints the verified count, never totalCount",
     "const facts = leadFacts(lead, verifiedTrials(lead.company_name, ct));", "const facts = leadFacts(lead, ct.totalCount);"),
    # the node body
    ("a ClinicalTrials.gov failure writes nothing",
     "if (!ct || typeof ct !== 'object' || ct.error || typeof ct.totalCount !== 'number') {", "if (false) {"),
    ("a site or SMO is not drafted", "if (str(lead.company_type) !== 'CRO') {", "if (false) {"),
    ("the gate reads the verified flag", "  verified: lead.verified === true,", "  verified: true,"),
    ("the hold tags reach the variant", "        variant: withTags(draft.variant, decision.tags),", "        variant: draft.variant,"),
    ("only an email is written", "        channel: 'email',\n        variant: withTags",
     "        channel: 'linkedin',\n        variant: withTags"),
]


def run_suite(workdir):
    p = subprocess.run(["node", os.path.join(workdir, "test_template.js")], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    fails = [ln.strip()[5:] for ln in p.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    if p.returncode != 0 and not fails:
        err = [ln for ln in p.stdout.splitlines() if re.match(r"\w*Error:", ln.strip())]
        fails = ["suite crashed: " + (err[0].strip() if err else "non-zero exit")]
    return p.returncode, fails, p.stdout


def workdir():
    tmp = tempfile.mkdtemp(prefix="novascout-tplmut-")
    for name in (TARGET, "test_template.js", "harness.js"):
        shutil.copy(os.path.join(HERE, name), os.path.join(tmp, name))
    return tmp


def main():
    base = workdir()
    rc, fails, out = run_suite(base)
    shutil.rmtree(base, ignore_errors=True)
    if rc != 0:
        print(out)
        raise SystemExit("baseline: test_template.js does not pass unmutated -- fix that first")
    print("baseline: test_template.js passes unmutated\n")

    with open(os.path.join(HERE, TARGET), encoding="utf-8") as fh:
        src = fh.read()
    survived = []
    for label, old, new in MUTATIONS:
        n = src.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its anchor occurs %d times in %s (want exactly 1)" % (label, n, TARGET))
        work = workdir()
        with open(os.path.join(work, TARGET), "w", encoding="utf-8", newline="\n") as fh:
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

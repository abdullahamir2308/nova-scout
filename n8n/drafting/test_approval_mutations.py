"""Proves every auto-approval rule fails a test when it is broken (migration 014).

    python n8n/drafting/test_approval_mutations.py

Same method as test_rule_mutations.py: copy this directory to a temp dir, break
exactly one rule in code_approval.js or code_approval_apply.js, run
test_approval.js against the copy, and require it to FAIL. A mutation the suite
survives is a rule nobody would notice losing -- here, a way for a draft to be
sent with no human having read it.

Exits non-zero if any mutation survives.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_approval.js"

# (label, file, old, new) -- each `old` must occur exactly once.
MUTATIONS = [
    ("rule 1: the flag off holds every draft", "code_approval.js",
     "if (ctx.auto_approve !== true) reasons.push(", "if (false) reasons.push("),
    ("rule 1: only an explicit true counts as on", "code_approval.js",
     "if (ctx.auto_approve !== true)", "if (ctx.auto_approve === false)"),
    ("rule 2: a rule tag holds it", "code_approval.js",
     "if (tags.length) reasons.push(", "if (false) reasons.push("),
    ("rule 3: a low-context note is held", "code_approval.js",
     "if (isLowContext(variant)) {", "if (false) {"),
    ("rule 4: an unconfirmed claim code holds it", "code_approval.js",
     "if (bad.length) reasons.push(", "if (false) reasons.push("),
    ("rule 4: no claim codes holds it", "code_approval.js",
     "reasons.push('no-claims: the draft records no claim codes');", "void 0;"),
    ("rule 4: only confirmed lines count", "code_approval.js",
     "filter(function (c) { return c && c.confirmed === true; })\n    .map(", "filter(function (c) { return c; })\n    .map("),
    ("rule 4: an unconfirmed line is never shown to the checker", "code_approval.js",
     "const claims = (ctx.claims || []).filter(function (c) { return c && c.confirmed === true; });\n  const followUp",
     "const claims = ctx.claims || [];\n  const followUp"),
    ("repair: an unconfirmed line is never offered to the repair either", "code_approval.js",
     "function repairPrompt(ctx, flagged) {\n  const claims = (ctx.claims || []).filter(function (c) { return c && c.confirmed === true; });",
     "function repairPrompt(ctx, flagged) {\n  const claims = ctx.claims || [];"),
    ("Section 6: LinkedIn is never auto-approved", "code_approval.js",
     "if (!draft || draft.channel !== 'email') {", "if (!draft) {"),
    ("a draft without its context is held", "code_approval.js",
     "if (!ctx || typeof ctx !== 'object') {", "if (!ctx && false) {"),
    ("one email draft per item reaches the check", "code_approval.js",
     "if (candidates.length > 1) {", "if (false) {"),
    ("the gate never approves -- only the check can", "code_approval.js",
     "  d.status = 'pending';\n", "  d.status = reasons.length ? 'pending' : 'approved';\n"),
    ("the gate copies the payload instead of mutating its input", "code_approval.js",
     "const payload = JSON.parse(JSON.stringify(item.payload || {}));", "const payload = item.payload || {};"),
    ("the request carries no sampling parameter", "code_approval.js",
     "function checkRequest(ctx) {\n  return {\n    model: CHECK_MODEL,\n",
     "function checkRequest(ctx) {\n  return {\n    model: CHECK_MODEL,\n    temperature: 0,\n"),
    ("repair: the repair request carries no sampling parameter", "code_approval.js",
     "function repairRequest(ctx, flagged) {\n  return {\n    model: CHECK_MODEL,\n",
     "function repairRequest(ctx, flagged) {\n  return {\n    model: CHECK_MODEL,\n    temperature: 0,\n"),
    ("the check is Sonnet 5.5, not a cheaper model", "code_approval.js",
     "const CHECK_MODEL = 'claude-sonnet-5-5';", "const CHECK_MODEL = 'claude-haiku-4-5';"),
    ("draft 99's sentence stays out of the check's prompt (held-out)", "code_approval.js",
     "  'request answered -- that the approved claim does not make.',\n",
     "  'request answered -- that the approved claim does not make. Likewise \"so you know exactly what came in\".',\n"),
    ("rule 5: a widened or unsupported claim holds it", "code_approval.js",
     "      if (s.verdict !== 'supported') {\n        problems.push(s.verdict + ' ' + what + src);",
     "      if (false) {\n        problems.push(s.verdict + ' ' + what + src);"),
    ("rule 5: a 'supported' claim must name a confirmed code", "code_approval.js",
     "} else if (!codes.length || codes.some(function (c) { return ok.indexOf(c) === -1; })) {",
     "} else if (false) {"),
    ("rule 5: an unsupported or joined fact holds it", "code_approval.js",
     "if (s.verdict !== 'supported') problems.push(s.verdict + ' fact ' + what + src);",
     "void 0;"),
    ("rule 5: an inconsistent 'none' statement holds it", "code_approval.js",
     "} else if (s.verdict !== 'none') {", "} else if (false) {"),
    ("rule 5: a sentence the check skipped holds it", "code_approval.js",
     "if (cov.left.length) {", "if (false) {"),
    ("rule 5: a quote that is not in the email holds it", "code_approval.js",
     "if (cov.foreign.length) {", "if (false) {"),
    ("rule 5: a first email with no claim found is held", "code_approval.js",
     "if (ctx.kind === 'first-touch' && !list.some(", "if (false && !list.some("),
    ("rule 5: an HTTP error holds it", "code_approval.js",
     "  if (resp.error) {\n    const e = resp.error;\n    return fail(",
     "  if (false) {\n    const e = resp.error;\n    return fail("),
    ("rule 5: a refusal or cut-off holds it", "code_approval.js",
     "if (resp.stop_reason !== 'end_turn') {", "if (false) {"),
    ("rule 5: an answer that is not the schema holds it", "code_approval.js",
     "if (!wellFormed) return fail(", "if (false) return fail("),
    ("rule 5: coverage folds case, accents and quotes", "code_approval.js",
     "return apFold(v).replace(/[^a-z0-9]+/g, ' ').trim();", "return apStr(v).replace(/\\s+/g, ' ').trim();"),
    ("rule 5: a quoted subject's 'Subject:' label is not held against it (dry run 1)", "code_approval.js",
     "const w = apWords(s && s.sentence).replace(/^subject /, '');", "const w = apWords(s && s.sentence);"),
    ("the checker is told the bracketed product name is not a claim (dry run 1)", "code_approval.js",
     "  'The product\\'s name in brackets, \"(we call it Nova)\", only names what the sentence describes:',\n"
     "  'it is \"none\", not a claim. Judge the rest of that sentence as usual.',\n", ""),
    ("rule 5: pass only when there is no reason at all", "code_approval.js",
     "rec.result = reasons.length ? 'hold' : 'pass';", "rec.result = problems.length ? 'hold' : 'pass';"),
    ("apply: a held draft stays pending", "code_approval_apply.js",
     "  target.status = 'pending';\n  target.approved_by = null;",
     "  target.status = 'approved';\n  target.approved_by = 'auto';"),
    ("apply: the verdicts are kept on the draft", "code_approval_apply.js",
     "target.claim_check = Object.assign({}, verdict, {", "target.claim_check = Object.assign({}, { result: verdict.result }, {"),
    # --- the repair loop (2026-10-03) ---
    ("repair: at most MAX_REPAIRS repairs", "code_approval_apply.js",
     "prior.length < MAX_REPAIRS", "prior.length < MAX_REPAIRS + 1"),
    # (Not mutated: Apply Claim Check's `verdict.result === 'hold'` before
    # flaggedSentences. A passing verdict cannot hold a widened, unsupported or
    # joined statement -- any one makes it a hold -- so removing it changes
    # nothing; it is an equivalent mutant, kept in the code as belt and braces.)
    ("repair: only statements that say more than their source are repairable", "code_approval.js",
     "const REPAIRABLE = ['widened', 'unsupported', 'joined'];", "const REPAIRABLE = ['widened', 'unsupported', 'joined', 'supported'];"),
    ("repair: nothing is repaired without the composition to edit", "code_approval_apply.js",
     "  !!gate.composition && typeof gate.composition === 'object';", "  true;"),
    ("repair: a final hold lists every attempt's reasons", "code_approval_apply.js",
     "  target.hold_reason = historyText(history);", "  target.hold_reason = verdict.reasons.join('; ');"),
    ("repair: every attempt is kept on the draft", "code_approval_apply.js",
     "  attempts: history,\n  compose_usage: composeUsage,", "  attempts: [attempt],\n  compose_usage: composeUsage,"),
    ("repair: the checker's exact reason reaches the composer", "code_approval.js",
     "      out.push('   checker: ' + r.verdict + ' -- \"' + r.statement + '\"' + (r.source ? ' (compared with ' + r.source + ')' : '') +\n        ': ' + r.why);",
     "      out.push('   checker: ' + r.verdict);"),
    ("repair: a rewrite of an unflagged sentence is ignored", "code_approval.js",
     "    if (!f) {\n      ignored.push({ original: original, why: 'not a flagged sentence' });\n      return;\n    }",
     "    if (!f) {\n      out.email_body = (out.email_body || '') + ' ' + replacement;\n      return;\n    }"),
    ("repair: a repair that changes nothing ends the loop", "code_approval_repair.js",
     "if (!res.changed) {", "if (false) {"),
    ("repair: a failed repair call ends the loop", "code_approval_repair.js",
     "if (resp.error) {\n  const e = resp.error;\n  return hold(", "if (false) {\n  const e = resp.error;\n  return hold("),
    ("repair: a refused or cut-off repair ends the loop", "code_approval_repair.js",
     "if (resp.stop_reason !== 'end_turn') {", "if (false) {"),
    ("repair: an answer that is not the repair schema ends the loop", "code_approval_repair.js",
     "if (!wellFormed) return hold(", "if (false) return hold("),
    ("repair: the attempts so far ride on to the next round", "code_approval_repair.js",
     "      history: st.history || [],\n      compose_usage:", "      history: [],\n      compose_usage:"),
    ("repair: a held draft after a failed repair carries every reason", "code_approval_repair.js",
     "    d.hold_reason = historyText(history);", "    d.hold_reason = reason;"),
    ("repair: the deletion keeps clean spacing", "code_approval.js",
     "        out[field] = b + (b && a && !/\\n$/.test(b) && !/^\\n/.test(a) ? ' ' : '') + a;",
     "        out[field] = before + after;"),
    ("repair: the gate copy keeps the attempts on a draft a rewrite broke", "code_approval.js",
     "  if (reasons.length && prior.length && repair.target === i) {", "  if (false) {"),
    ("repair: the attempts stay on the repaired draft, not its LinkedIn DM", "code_approval.js",
     "  if (reasons.length && prior.length && repair.target === i) {", "  if (reasons.length && prior.length) {"),
    ("repair: the repair request uses the drafting model", "code_approval.js",
     "    output_config: { effort: CHECK_EFFORT, format: { type: 'json_schema', schema: REPAIR_SCHEMA } },",
     "    output_config: { effort: 'low', format: { type: 'json_schema', schema: REPAIR_SCHEMA } },"),
]


def run_suite(workdir):
    p = subprocess.run(["node", os.path.join(workdir, TEST)], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    fails = [ln.strip()[5:] for ln in p.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    if p.returncode != 0 and not fails:
        err = [ln for ln in p.stdout.splitlines() if re.match(r"\w*Error:", ln.strip())]
        fails = ["suite crashed: " + (err[0].strip() if err else "non-zero exit")]
    return p.returncode, fails, p.stdout


rc, fails, out = run_suite(HERE)
if rc != 0:
    sys.exit("BASELINE FAILS: %s\n%s" % (TEST, out[-1500:]))
print("baseline: %s passes unmutated\n" % TEST)

survived = []
for label, file, old, new in MUTATIONS:
    tmp = tempfile.mkdtemp(prefix="novascout-amut-")
    work = os.path.join(tmp, "drafting")
    shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("__pycache__"))
    path = os.path.join(work, file)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    n = src.count(old)
    if n != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit("mutation anchor occurs %d times in %s: %r" % (n, file, old))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src.replace(old, new, 1))
    rc, fails, _ = run_suite(work)
    shutil.rmtree(tmp, ignore_errors=True)
    if rc == 0:
        survived.append(label)
        print("  SURVIVED %s" % label)
    else:
        print("  killed   %-58s %d fail, e.g. \"%s\"" % (label, len(fails), fails[0][:60] if fails else "?"))

print("\n%d mutations; %d survived" % (len(MUTATIONS), len(survived)))
sys.exit(1 if survived else 0)

"""Proves every rule of the Send safety brake fails a test when it is broken.

    python n8n/sendtrack/test_send_brake_mutations.py

Section 9, Workflow 6 (operator decision 2026-10-19), with the warm-up ceiling
raised to 50 the same day. The brake lives in four places, and each mutation
breaks one rule in one of them; a test must then fail:

  * the SQL -- migration 021's send_brake_stats() and the shipped Check Brake,
    Load Send State and Record Brake Alert -> test_send_brake_sql.py, on one
    scratch database (a mutation that makes a statement error counts as caught:
    the workflow would fail the tick, and the test says so);
  * Decide Send (code_decide.js) -> test_decide.js;
  * the classifier (code_classify_reply.js) -> test_mailbox.js.

Claim Send's own refusal is a build guard (test_drift_guards.py makes it fire).
Exits non-zero if any mutation survives, or if a suite fails unmutated.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import test_send_brake_sql as T

HERE = os.path.dirname(os.path.abspath(__file__))

# SQL: (label, where, old, new) -- where is 'migration' or a statement key of T.shipped().
SQL_MUTATIONS = [
    ("over the share, not at it", "check",
     "     AND v.bounced * 100.0 > $3::numeric * v.sends", "     AND v.bounced * 100.0 >= $3::numeric * v.sends"),
    ("the window is the last N days", "migration",
     "greatest(as_of - make_interval(days => window_days),", "greatest('-infinity'::timestamptz,"),
    ("a claim is not a send", "migration",
     "       AND o.message_id IS NOT NULL          -- a confirmed send, not a claim\n", ""),
    ("clearing a bounce-rate pause restarts its count", "migration",
     "coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'bounce-rate'),",
     "coalesce(NULL::timestamptz,"),
    ("clearing a complaint pause restarts its count", "migration",
     "coalesce((SELECT max(p.cleared_at) FROM send_pauses p WHERE p.reason = 'spam-complaint'),",
     "coalesce(NULL::timestamptz,"),
    ("only a flagged message is a complaint", "migration", "     WHERE i.spam_complaint\n", "     WHERE true\n"),
    ("a tripped brake latches once (the bounce insert)", "check",
     "     AND v.bounced * 100.0 > $3::numeric * v.sends\n  ON CONFLICT (reason) WHERE cleared_at IS NULL DO NOTHING",
     "     AND v.bounced * 100.0 > $3::numeric * v.sends"),
    ("any complaint trips it", "check",
     "   WHERE jsonb_array_length(v.complaints) > 0\n", "   WHERE jsonb_array_length(v.complaints) > 1\n"),
    ("Load Send State shows only open pauses", "load",
     "FROM send_pauses sp WHERE sp.cleared_at IS NULL) AS pauses", "FROM send_pauses sp) AS pauses"),
    ("an alert counts only once SMTP accepted it", "record", "   AND p.r->>'error' IS NULL\n", ""),
    ("an alert stamps only the pauses it told the operator about", "record",
     " WHERE sp.id IN (SELECT (jsonb_array_elements_text(p.a->'pause_ids'))::bigint)\n", " WHERE true\n"),
]

# JS: (label, file, old, new, the suite that must fail)
JS_MUTATIONS = [
    ("an open pause stops the tick", "code_decide.js", "  if (pauses.length) {", "  if (false) {", "test_decide.js"),
    ("the brake is checked before the Sent mirror", "code_decide.js",
     "  const pauses = Array.isArray(state.pauses) ? state.pauses : [];",
     "  const pauses = state.sent_mirror_synced_at && Array.isArray(state.pauses) ? state.pauses : [];",
     "test_decide.js"),
    ("a pause is alerted once", "code_decide.js", "!str(p.alerted_at)", "true", "test_decide.js"),
    ("no operator address, no alert", "code_decide.js", "  if (!fresh.length || !EMAIL_RE.test(to)) return alert;",
     "  if (!fresh.length) return alert;", "test_decide.js"),
    ("only the new pauses are stamped", "code_decide.js",
     "  alert.pause_ids = fresh.map(function (p) { return Number(p.id); });",
     "  alert.pause_ids = pauses.map(function (p) { return Number(p.id); });", "test_decide.js"),
    ("the alert tells the operator how to resume", "code_decide.js",
     "  lines.push(\"  UPDATE send_pauses SET cleared_at = now(), cleared_note = 'why' WHERE cleared_at IS NULL;\");\n",
     "", "test_decide.js"),
    ("week 5+ allows 50 a day", "code_decide.js", "  [5, 50],", "  [5, 20],", "test_decide.js"),
    ("a feedback-loop report is a complaint", "code_classify_reply.js",
     "  const isReport = isComplaintReport(meta, raw);", "  const isReport = false;", "test_mailbox.js"),
    ("a person's complaint is read", "code_classify_reply.js",
     "  const phrase = person ? complaintPhrase(fresh) : null;", "  const phrase = null;", "test_mailbox.js"),
    ("a complaint is an opt-out", "code_classify_reply.js",
     "    : (kw || phrase) ? 'opt-out' : 'reply';", "    : kw ? 'opt-out' : 'reply';", "test_mailbox.js"),
    ("the complaint flag is set", "code_classify_reply.js",
     "    spam_complaint: isReport || phrase !== null,", "    spam_complaint: false,", "test_mailbox.js"),
    ("a report's Message-ID comes from its body", "code_classify_reply.js",
     "  const fromBody = isReport || isBounce;", "  const fromBody = isBounce;", "test_mailbox.js"),
    ("'it went to my spam folder' is not a complaint", "code_classify_reply.js",
     "  /\\b(?:this|it|that|your (?:e-?mail|message|mail))(?:['’]s|\\s+is|\\s+was|\\s+looks like)\\s+(?:pure\\s+|just\\s+|total\\s+|obvious\\s+|blatant\\s+)?(?:spam|junk)\\b/i,",
     "  /\\bspam\\b/i,", "test_mailbox.js"),
    ("only what they wrote is read for a complaint", "code_classify_reply.js",
     "  const t = String(fresh || '');\n  for (let i = 0; i < COMPLAINT_PHRASES.length; i++) {",
     "  const t = String(j_raw_for_mutation || fresh || '');\n  for (let i = 0; i < COMPLAINT_PHRASES.length; i++) {",
     "test_mailbox.js"),
]


def sql_variants():
    base = T.shipped()
    mig = T.migration()
    out = []
    for label, where, old, new in SQL_MUTATIONS:
        text = mig if where == "migration" else base[where]
        n = text.count(old)
        if n != 1:
            raise SystemExit("SQL mutation %r: its text occurs %d times in %s, expected once" % (label, n, where))
        if where == "migration":
            out.append((label, dict(base), text.replace(old, new)))
        else:
            out.append((label, dict(base, **{where: text.replace(old, new)}), mig))
    return base, mig, out


def run_sql():
    base, mig, variants = sql_variants()
    T.create_scratch(mig)
    survived = 0
    try:
        T.psql(T.SCRATCH, mig)
        failed = [r for r in T.checks(base) if not r[1]]
        if failed:
            print("baseline: test_send_brake_sql.py does not pass unmutated -- fix that first")
            for label, _, detail in failed:
                print("  FAIL", label, "--", detail[:300])
            sys.exit(1)
        print("baseline: test_send_brake_sql.py passes unmutated")
        for label, stmts, m in variants:
            try:
                T.psql(T.SCRATCH, m)
                caught = [r[0] for r in T.checks(stmts) if not r[1]]
                why = caught[0] if caught else None
            except SystemExit as e:
                why = "the statement errored: " + str(e).splitlines()[-1][:120]
            except Exception as e:     # a check that cannot even run on the result is a failing check
                why = "the checks could not run: %s: %s" % (type(e).__name__, str(e)[:100])
            finally:
                T.psql(T.SCRATCH, mig)     # restore the shipped function for the next mutation
            if why:
                print("  killed   %s\n           (%s)" % (label, why))
            else:
                survived += 1
                print("  SURVIVED %s" % label)
    finally:
        T.psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % T.SCRATCH, check=False)
    return survived


def node(suite, cwd):
    p = subprocess.run(["node", suite], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr)


def run_js():
    for suite in sorted({m[4] for m in JS_MUTATIONS}):
        rc, out = node(suite, HERE)
        if rc != 0:
            print("baseline: %s does not pass unmutated -- fix that first\n%s" % (suite, out[-800:]))
            sys.exit(1)
    print("baseline: %s pass unmutated" % ", ".join(sorted({m[4] for m in JS_MUTATIONS})))
    survived = 0
    for label, fname, old, new, suite in JS_MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="novascout-brake-mut-")
        try:
            for f in os.listdir(HERE):
                if f.endswith(".js"):
                    shutil.copy(os.path.join(HERE, f), tmp)
            p = os.path.join(tmp, fname)
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            n = src.count(old)
            if n != 1:
                raise SystemExit("JS mutation %r: its text occurs %d times in %s, expected once" % (label, n, fname))
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src.replace(old, new))
            rc, out = node(suite, tmp)
            if rc != 0:
                first = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAIL")),
                             out.strip().splitlines()[-1][:120] if out.strip() else "non-zero exit")
                print("  killed   %s\n           (%s: %s)" % (label, suite, first[:140]))
            else:
                survived += 1
                print("  SURVIVED %s (%s still passes)" % (label, suite))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return survived


if __name__ == "__main__":
    s_sql = run_sql()
    print()
    s_js = run_js()
    total = len(SQL_MUTATIONS) + len(JS_MUTATIONS)
    survived = s_sql + s_js
    print("\n%d mutations, %d killed, %d survived" % (total, total - survived, survived))
    sys.exit(1 if survived else 0)

"""Proves every rule of the Send safety brake fails a test when it is broken.

    python n8n/sendtrack/test_send_brake_mutations.py

Section 9, Workflow 6 (operator decision 2026-10-19), with the warm-up ceiling
raised to 50 the same day; only permanent bounces count since 2026-10-24
(migration 022). The brake lives in four places, and each mutation breaks one
rule in one of them; a test must then fail:

  * the SQL -- send_brake_stats() (made by migration 021, last replaced by 022,
    which is the file mutated here) and the shipped Check Brake, Load Send
    State and Record Brake Alert -> test_send_brake_sql.py, on one scratch
    database (a mutation that makes a statement error counts as caught: the
    workflow would fail the tick, and the test says so);
  * Decide Send (code_decide.js) -> test_decide.js;
  * the classifier (code_classify_reply.js: complaints, permanent or
    transient, and DMARC reports -- never a bounce the brake counts, an opt-out
    or a reply) and the notification (code_notify.js) -> test_mailbox.js.

Record Inbound and migration 022's contact rule have their own:
test_hard_bounce_mutations.py.

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
    # Migration 022 (operator decision 2026-10-24): only a permanent bounce counts.
    ("a delay warning is not a bounce for the brake", "migration",
     "    SELECT * FROM sends WHERE outcome = 'bounced' AND bounce_kind IS DISTINCT FROM 'transient'\n",
     "    SELECT * FROM sends WHERE outcome = 'bounced'\n"),
    ("an unlabelled bounce (an older Mailbox Watch) still counts", "migration",
     "AND bounce_kind IS DISTINCT FROM 'transient'\n", "AND bounce_kind = 'permanent'\n"),
    ("Load Send State sees the delays counted apart", "migration",
     "    'delayed',     (SELECT count(*) FROM sends WHERE outcome = 'bounced' AND bounce_kind = 'transient'),",
     "    'delayed',     0,"),
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
    # Permanent or transient (migration 022, operator decision 2026-10-24).
    ("a report that says it is final is permanent", "code_classify_reply.js",
     "  if (BOUNCE_PERMANENT.some(function (re) { return re.test(t); })) return 'permanent';\n", "", "test_mailbox.js"),
    ("a report that says it is a delay is transient", "code_classify_reply.js",
     "  if (BOUNCE_TRANSIENT.some(function (re) { return re.test(t); })) return 'transient';\n", "", "test_mailbox.js"),
    ("the verdict is read before the delay wording (lead 44's 421 'permanent error')", "code_classify_reply.js",
     "  if (BOUNCE_PERMANENT.some(function (re) { return re.test(t); })) return 'permanent';\n"
     "  if (BOUNCE_TRANSIENT.some(function (re) { return re.test(t); })) return 'transient';\n",
     "  if (BOUNCE_TRANSIENT.some(function (re) { return re.test(t); })) return 'transient';\n"
     "  if (BOUNCE_PERMANENT.some(function (re) { return re.test(t); })) return 'permanent';\n", "test_mailbox.js"),
    ("any 5.x.x code is permanent, before a 4.x.x", "code_classify_reply.js",
     "  if (classes.indexOf('5') !== -1) return 'permanent';\n  if (classes.indexOf('4') !== -1) return 'transient';\n",
     "  if (classes.indexOf('4') !== -1) return 'transient';\n  if (classes.indexOf('5') !== -1) return 'permanent';\n",
     "test_mailbox.js"),
    ("a report that says neither is a failure", "code_classify_reply.js",
     "  return 'permanent';\n}\n\nfunction classify(", "  return 'transient';\n}\n\nfunction classify(", "test_mailbox.js"),
    ("'fatal errors' decides nothing (Zoho's delay warning lists them)", "code_classify_reply.js",
     "const BOUNCE_PERMANENT = [\n", "const BOUNCE_PERMANENT = [\n  /\\bfatal\\s+errors?\\b/i,\n", "test_mailbox.js"),
    ("a future 'can't be delivered' is a delay's promise, not a verdict", "code_classify_reply.js",
     r"(?:couldn['’]t|could\s+not)\s+be\s+delivered", r"(?:couldn['’]t|could\s+not|can['’]t|cannot)\s+be\s+delivered",
     "test_mailbox.js"),
    ("reports wrap their text anywhere", "code_classify_reply.js",
     r"(?:couldn['’]t|could\s+not)\s+be\s+delivered", r"(?:couldn['’]t|could not) be delivered", "test_mailbox.js"),
    ("an IP address is not a status code", "code_classify_reply.js",
     r"const STATUS_CODE = /(?:^|[^\d.])([245])\.\d{1,3}\.\d{1,3}(?![\d.])/g;",
     r"const STATUS_CODE = /([245])\.\d{1,3}\.\d{1,3}/g;", "test_mailbox.js"),
    ("only a bounce carries a kind", "code_classify_reply.js",
     "    bounce_kind: isBounce ? bounceKind(subject, raw) : null,", "    bounce_kind: bounceKind(subject, raw),",
     "test_mailbox.js"),
    # DMARC reports (2026-10-24): machine mail, never an opt-out, a reply or a
    # bounce the brake counts.
    ("a DMARC report is recognised at all", "code_classify_reply.js",
     "  const isDmarc = !isReport && isDmarcReport(subject, raw);", "  const isDmarc = false;", "test_mailbox.js"),
    ("a DMARC report is decided before a bounce (postmaster@)", "code_classify_reply.js",
     "  const isBounce = !isReport && !isDmarc && (", "  const isBounce = !isReport && (", "test_mailbox.js"),
    ("a DMARC report is machine mail, not a person", "code_classify_reply.js",
     "  const isAuto = !isReport && !isBounce && (isDmarc || (", "  const isAuto = !isReport && !isBounce && ((",
     "test_mailbox.js"),
    ("Microsoft's '[Preview]' subject prefix is allowed", "code_classify_reply.js",
     r"/^\s*(?:\[[^\]]*\]\s*)?report\s+domain:", r"/^\s*report\s+domain:", "test_mailbox.js"),
    ("'DMARC aggregate report' subjects are recognised", "code_classify_reply.js",
     r"submitter:|\bdmarc\s+(?:aggregate|failure|forensic)\s+report\b/i;", r"submitter:/i;", "test_mailbox.js"),
    ("a failure report (auth-failure) is recognised", "code_classify_reply.js",
     r" || /^\s*feedback-type:\s*auth-failure\b/im.test(String(raw || ''));", ";", "test_mailbox.js"),
    ("the payload says it was a DMARC report", "code_classify_reply.js",
     "    dmarc_report: isDmarc,", "    dmarc_report: false,", "test_mailbox.js"),
    ("a delay is labelled DELIVERY DELAY for the operator", "code_notify.js",
     "r.classification === 'bounce' && r.bounce_kind === 'transient' ? 'DELIVERY DELAY'", "false ? 'DELIVERY DELAY'",
     "test_mailbox.js"),
    ("a delay is not reported as a permanent bounce", "code_notify.js",
     "  } else if (r.classification === 'bounce' && r.bounce_kind === 'transient') {",
     "  } else if (false) {", "test_mailbox.js"),
    ("the operator is told whether the contact was unverified", "code_notify.js",
     "    lines.push(r.contact_unverified\n", "    lines.push(true\n", "test_mailbox.js"),
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

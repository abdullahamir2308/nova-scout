"""Proves every safety rule of the Sent reconcile fails test_sent_reconcile_sql.py when it is broken.

    python n8n/sendtrack/test_sent_reconcile_mutations.py

Same method as test_health_sql_mutations.py: each mutation is applied to the
SHIPPED Reconcile Sent statement or Plan Sent Reconcile code
(../workflows/mailbox-watch.json), and test_sent_reconcile_sql.py's checks must
fail. One scratch database serves every mutation; it is dropped afterwards.

Exits non-zero if any mutation survives, or if the unmutated pair fails.
"""
import sys

import test_sent_reconcile_sql as T

# (label, "sql" | "js", old, new) -- each `old` must occur exactly once.
MUTATIONS = [
    ("the statement writes only when the listing is complete (listed gate)",
     "sql", "   WHERE listed.ok\n),", "),"),
    ("the reconcile stamp is written only on a complete read",
     "sql", "    FROM p, listed\n   WHERE listed.ok\n  ON CONFLICT (folder)", "    FROM p, listed\n  ON CONFLICT (folder)"),
    ("a known message only gets seen = true (no overwrite of Send's own row)",
     "sql", "ON CONFLICT (message_id) DO UPDATE SET seen_in_sent_folder = true",
     "ON CONFLICT (message_id) DO UPDATE SET seen_in_sent_folder = true, source = 'sent-folder', "
     "sent_at = EXCLUDED.sent_at"),
    ("never double-record: the upsert is keyed on Message-ID",
     "sql", "ON CONFLICT (message_id) DO UPDATE SET seen_in_sent_folder = true",
     "ON CONFLICT DO NOTHING"),
    ("the read is stamped Sent-reconcile, not the trigger's Sent",
     "sql", "SELECT 'Sent-reconcile', now()", "SELECT 'Sent', now()"),
    ("healed counts what was unseen BEFORE this statement",
     "sql", "FROM was WHERE NOT was.seen_in_sent_folder)::int", "FROM was WHERE was.seen_in_sent_folder)::int"),
    ("the plan needs the checker's ok", "js", "if (c.ok !== true || !Array.isArray(c.sent)) {",
     "if (!Array.isArray(c.sent)) {"),
    ("the plan needs a list", "js", "if (c.ok !== true || !Array.isArray(c.sent)) {",
     "if (c.ok !== true) {"),
    ("an entry needs a well-formed Message-ID", "js",
     "if (!RECONCILE_ID_RE.test(id) || messageId(id) !== id || !str(e.date)) {", "if (!str(e.date)) {"),
    ("an entry needs a Date", "js",
     "if (!RECONCILE_ID_RE.test(id) || messageId(id) !== id || !str(e.date)) {",
     "if (!RECONCILE_ID_RE.test(id) || messageId(id) !== id) {"),
    ("every recipient header counts (Cc)", "js", "cc: str(e.cc), subject: ''", "cc: '', subject: ''"),
    ("the operator-only rule is the mirror's (operator address passed through)", "js",
     "const r = sentRows(l.items, operatorEmail);", "const r = sentRows(l.items, '');"),
]


def main():
    lib, sql = T.shipped()
    cases = []
    for label, where, old, new in MUTATIONS:
        src = sql if where == "sql" else lib
        n = src.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its text occurs %d times in the shipped %s, expected once" % (label, n, where))
        cases.append((sql.replace(old, new), lib) if where == "sql" else (sql, lib.replace(old, new)))
    results = T.run_all([(sql, lib)] + cases)
    base = [r for r in results[0] if not r[1]]
    if base:
        print("baseline: test_sent_reconcile_sql.py does not pass unmutated -- fix that first")
        for label, _, detail in base:
            print("  FAIL", label, "--", detail)
        sys.exit(1)
    print("baseline: test_sent_reconcile_sql.py passes unmutated\n")
    survived = 0
    for (label, _, _, _), res in zip(MUTATIONS, results[1:]):
        caught = [r[0] for r in res if not r[1]]
        if caught:
            print("  killed   %s\n           (failed: %s)" % (label, caught[0]))
        else:
            survived += 1
            print("  SURVIVED %s" % label)
    print("\n%d mutations, %d killed, %d survived" % (len(MUTATIONS), len(MUTATIONS) - survived, survived))
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()

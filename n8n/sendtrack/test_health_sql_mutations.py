"""Proves every rule in Load Health State fails test_health_sql.py when it is broken.

    python n8n/sendtrack/test_health_sql_mutations.py

Same method as test_followup_mutations.py, for the IMAP Health query: each
mutation is applied to the SHIPPED SQL (../workflows/imap-health.json) and
test_health_sql.py's checks must fail. The first mutation is the 2026-10-18
query exactly -- INBOX mail counted as recorded only in inbound_messages -- so
the false alarm on the operator's JSS Medical Research command is proved caught.
One scratch database serves every mutation; it is dropped afterwards.

Exits non-zero if any mutation survives, or if the unmutated query fails.
"""
import sys

import test_health_sql as T

OPCMD = "     AND NOT EXISTS (SELECT 1 FROM operator_commands c WHERE c.message_id = i.message_id)\n"
INBOUND = "     AND NOT EXISTS (SELECT 1 FROM inbound_messages m WHERE m.message_id = i.message_id)\n"

# (label, old, new) -- each `old` must occur exactly once in the shipped SQL.
MUTATIONS = [
    ("an operator command is recorded (the 2026-10-18 query: inbound_messages only)", OPCMD, ""),
    ("a prospect's mail is recorded (operator_commands only)", INBOUND, ""),
    ("recorded in EITHER table, not in both", OPCMD,
     "     OR NOT EXISTS (SELECT 1 FROM operator_commands c WHERE c.message_id = i.message_id)\n"),
    ("an operator command is matched on its Message-ID",
     "operator_commands c WHERE c.message_id = i.message_id", "operator_commands c WHERE c.code = i.message_id"),
    ("an INBOX message is missed only after the grace period",
     "   WHERE i.at <= clk.now - clk.grace\n", "   WHERE i.at <= clk.now\n"),
    ("an INBOX message from before Mailbox Watch first ran is not its to record",
     "     AND i.at >= watch.started_at\n", ""),
    ("a Sent message is recorded when the mirror has it",
     "     AND NOT EXISTS (SELECT 1 FROM mailbox_sent m WHERE m.message_id = s.message_id AND m.seen_in_sent_folder)\n",
     ""),
    # 2026-10-28 -- the first is the query as it was that morning, exactly: a row
    # Send wrote for itself counted as mirrored, and IMAP Health stayed green.
    ("a Sent message is mirrored only once the mirror has SEEN it (the 2026-10-28 query: any row will do)",
     "m.message_id = s.message_id AND m.seen_in_sent_folder)", "m.message_id = s.message_id)"),
    ("send-blocked waits for Send's grace plus block_alert_min",
     "make_interval(mins => (p.p->>'block_min')::int)", "make_interval(mins => 0)"),
    ("send-blocked counts only sends the mirror has not seen",
     "   WHERE m.source = 'workflow' AND NOT m.seen_in_sent_folder\n", "   WHERE m.source = 'workflow'\n"),
    ("a mismatch needs a complete listing", "   WHERE coalesce((p.p->>'listed')::boolean, false)\n",
     "   WHERE true\n"),
    ("a mismatch is only a row the mirror marks seen", "     AND m.seen_in_sent_folder\n", ""),
    ("a mismatch keeps an hour inside the listing's window",
     "(p.p->>'since')::timestamptz + interval '1 hour'", "(p.p->>'since')::timestamptz"),
    ("a mismatch is only older than the grace", "     AND m.sent_at <= clk.now - clk.grace\n", ""),
    ("a mismatch never counts the mirror's own fallback ids",
     "     AND m.message_id NOT LIKE '<no-message-id.%'\n", ""),
    ("a mismatch is matched on Message-ID",
     "NOT EXISTS (SELECT 1 FROM sent s WHERE s.message_id = m.message_id)", "true"),
    ("a Sent message is missed only after the grace period",
     "   WHERE s.at <= clk.now - clk.grace\n", "   WHERE s.at <= clk.now - clk.grace - interval '1 day'\n"),
    ("the previous state is this check's own row",
     "  SELECT * FROM mailbox_health WHERE check_name = 'imap'", "  SELECT * FROM mailbox_health WHERE false"),
]


def main():
    sql = T.shipped_sql()
    mutated = []
    for label, old, new in MUTATIONS:
        n = sql.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its text occurs %d times in the shipped SQL, expected once" % (label, n))
        mutated.append(sql.replace(old, new))
    results = T.run_all([sql] + mutated)
    base = [r for r in results[0] if not r[1]]
    if base:
        print("baseline: test_health_sql.py does not pass unmutated -- fix that first")
        for label, _, detail in base:
            print("  FAIL", label, "--", detail)
        sys.exit(1)
    print("baseline: test_health_sql.py passes unmutated\n")
    survived = 0
    for (label, _, _), res in zip(MUTATIONS, results[1:]):
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

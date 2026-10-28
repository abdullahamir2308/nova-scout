"""Load Health State, run for real -- which INBOX and Sent messages IMAP Health calls MISSED.

    python n8n/sendtrack/test_health_sql.py

test_health.js proves what Assess Health does with a row; this proves the row.
The SQL is the SHIPPED query (read out of ../workflows/imap-health.json, so it
cannot drift from what the workflow runs), executed against a scratch database
created from the live schema (pg_dump --schema-only of novascout: every table,
constraint and trigger, no rows), filled with fixtures, read-only. Its row then
goes through the shipped code_health.js assess(), so "alerts" means what the
workflow would email.

The case that made this test (2026-10-18 16:13:53 UTC): the operator's
"Re: [Nova Scout] still waiting: JSS Medical Research -- NS-VNS3A33B83" was
handled -- recorded in operator_commands, an accepted APPROVE -- but Load Health
State looked only in inbound_messages, so IMAP Health reported it missed for the
checker's 48 hours. Recorded now means either table; a message in neither still
alerts.

The real novascout database is only read (the schema dump); the scratch
database is dropped afterwards. test_health_sql_mutations.py breaks each rule in
the query in turn and requires a check here to fail.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "workflows", "imap-health.json")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_healthsql_test_%d" % os.getpid()

WATCH_START = "2026-09-15 14:14:26+00"   # the real Mailbox Watch start
JSS = {"message_id": "<31A34258-31DB-45C3-9E61-52E0B2039E26@gmail.com>", "at": "2026-10-18T16:13:53+00:00",
       "from": "Abdullah Amir <operator@example.invalid>",
       "subject": "Re: [Nova Scout] still waiting: JSS Medical Research -- NS-VNS3A33B83"}
REFUSED = "<refused-command@example.invalid>"
REPLY = "<prospect-reply@cro.example>"
LOST = "<lost-reply@cro.example>"
YOUNG = "<just-arrived@cro.example>"
OLD = "<before-watch@cro.example>"
SENT_OK = "<sent-recorded@amitrixlabs.example>"
SENT_LOST = "<sent-lost@amitrixlabs.example>"
SENT_CONFIRMED = "<sent-by-workflow-confirmed@amitrixlabs.example>"   # a normal send: Send's row, seen in the folder
# 2026-10-28: the Sent-mirror gap and its mirror image.
GAP = "<gap-send@amitrixlabs.example>"
GAP_YOUNG = "<gap-send-young@amitrixlabs.example>"
PHANTOM = "<phantom@amitrixlabs.example>"
PHANTOM_EDGE = "<phantom-window-edge@amitrixlabs.example>"
PHANTOM_YOUNG = "<phantom-young@amitrixlabs.example>"
FALLBACK = "<no-message-id.1a2b3c@mirror.invalid>"
UNSEEN_UNLISTED = "<unseen-unlisted@amitrixlabs.example>"


def psql(db, sql, check=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def shipped_sql():
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = json.load(fh)
    return [n for n in wf["nodes"] if n["name"] == "Load Health State"][0]["parameters"]["query"]


def shipped_cfg():
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = json.load(fh)
    cfg = [n for n in wf["nodes"] if n["name"] == "Config"][0]["parameters"]["assignments"]["assignments"]
    return {a["name"]: a["value"] for a in cfg}


def create_scratch():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner", "novascout"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if schema.returncode:
        raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
    psql(SCRATCH, schema.stdout)
    # Fixed history, plus rows relative to the database's clock (grace is 15 min).
    psql(SCRATCH, """
INSERT INTO settings (key, value) VALUES ('operator_email', 'operator@example.invalid')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder, recorded_at) VALUES
  ('<watch-start@amitrixlabs.example>', '%(ws)s', '{}', false, 'sent-folder', true, '%(ws)s'),
  ('%(sent_ok)s', now() - interval '2 hours', '{}', true, 'sent-folder', true, now() - interval '2 hours'),
  ('%(confirmed)s', now() - interval '2 hours', '{}', true, 'workflow', true, now() - interval '2 hours');
INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, classification, processed_at) VALUES
  ('%(reply)s', now() - interval '2 hours', 'Lead <lead@cro.example>', 'Re: sponsor inquiries', 'reply',
   now() - interval '2 hours');
INSERT INTO operator_commands (message_id, received_at, from_addr, subject, command, code, spf_pass, dkim_pass,
                               sender_ok, accepted, refused_reason, processed_at) VALUES
  ('%(jss)s', '2026-10-18 16:13:36+00', 'operator@example.invalid', '%(jss_subject)s', 'APPROVE', 'NS-VNS3A33B83',
   true, true, true, true, NULL, '2026-10-18 16:13:59+00'),
  ('%(refused)s', now() - interval '3 hours', 'someone@example.invalid', 'Re: [Nova Scout] APPROVE', NULL, NULL,
   false, false, false, false, 'not the operator', now() - interval '3 hours');
""" % {"ws": WATCH_START, "sent_ok": SENT_OK, "confirmed": SENT_CONFIRMED, "reply": REPLY, "jss": JSS["message_id"],
       "jss_subject": JSS["subject"].replace("'", "''"), "refused": REFUSED})


def set_prev_health(alerted):
    """The check before this one: failing on inbox-missed -- already alerted (the live state
    on 2026-10-19), or a first failure nobody has been told about yet."""
    psql(SCRATCH, """
DELETE FROM mailbox_health;
INSERT INTO mailbox_health (check_name, healthy, problems, detail, consecutive_failures, failing_since,
                            last_checked_at, last_ok_at, last_alerted_at, alerted_problems)
VALUES ('imap', false, '{inbox-missed}', 'inbox-missed: 1 message(s)', %(n)d, now() - interval '%(since)s',
        now() - interval '20 minutes', now() - interval '%(since)s', %(alerted_at)s, %(alerted)s);
""" % {"n": 46 if alerted else 1, "since": "15 hours" if alerted else "20 minutes",
       "alerted_at": "now() - interval '1 hour'" if alerted else "NULL",
       "alerted": "'{inbox-missed}'" if alerted else "NULL"})


def db_times():
    """ISO timestamps relative to the database clock: 2 h, 3 h and 5 min ago."""
    out = psql(SCRATCH, "SELECT to_char((now() - interval '2 hours') AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'), "
                        "to_char((now() - interval '3 hours') AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'), "
                        "to_char((now() - interval '5 minutes') AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00');")
    return out.strip().split("|")


def db_iso(expr):
    """An ISO timestamp for a SQL interval expression relative to the database clock."""
    return psql(SCRATCH, "SELECT to_char((%s) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00');" % expr).strip()


def run_sql(sql, inbox, sent, grace, listed=True, block_min=None):
    sql = re.sub(r"--[^\n]*", "", sql)
    assert sql.count("$1") == 1, "Load Health State is expected to take exactly one parameter, $1"
    payload = json.dumps({"inbox": inbox, "sent": sent, "grace_min": grace,
                          "block_min": block_min if block_min is not None else shipped_cfg()["block_min"],
                          # `since` even when not listed: a half answer may carry one, and the listed flag alone
                          # must keep it from counting as an empty folder.
                          "listed": listed, "since": db_iso("now() - interval '48 hours'")})
    assert "$j$" not in payload
    body = sql.strip().rstrip(";").replace("$1", "$j$" + payload + "$j$")
    out = psql(SCRATCH, "SET default_transaction_read_only = on;\nSELECT row_to_json(t) FROM (\n" + body + "\n) t;")
    return json.loads(out.strip())


def assess(check, row, cfg):
    js = ("const { extractFunctions } = require(%s);\n"
          "const { assess } = extractFunctions(%s, '// Node body', ['assess']);\n"
          "const a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
          "process.stdout.write(JSON.stringify(assess(a.check, a.row, a.cfg)));\n"
          % (json.dumps(os.path.join(HERE, "harness.js")), json.dumps(os.path.join(HERE, "code_health.js"))))
    p = subprocess.run(["node", "-e", js], input=json.dumps({"check": check, "row": row, "cfg": cfg}),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    if p.returncode:
        raise SystemExit("assess() failed:\n" + p.stderr[-1500:])
    return json.loads(p.stdout)


def ids(rows):
    return sorted(r["message_id"] for r in (rows or []))


def checks(sql):
    """(label, ok, detail) for every expectation, against `sql` as Load Health State."""
    cfg = shipped_cfg()
    two_h, three_h, five_min = db_times()
    msg = lambda mid, at: {"message_id": mid, "at": at, "from": "Someone <x@cro.example>", "subject": "s"}
    handled = [JSS, msg(REFUSED, three_h), msg(REPLY, two_h), msg(YOUNG, five_min), msg(OLD, "2026-09-01T00:00:00+00:00")]
    ok_check = {"ok": True, "status": "passed", "hours": 48, "inbox": [], "sent": []}
    out = []

    # 1. The real case: everything in INBOX was handled -> nothing missed, and one all-clear.
    set_prev_health(alerted=True)
    row = run_sql(sql, handled, [{"message_id": SENT_OK, "at": two_h, "to": "x@cro.example"}, {"message_id": SENT_CONFIRMED, "at": two_h, "to": "z@cro.example"}], cfg["grace_min"])
    out.append(("the 2026-10-18 16:13:53Z JSS Medical Research command (in operator_commands) is not missed",
                JSS["message_id"] not in ids(row["missing_inbox"]), str(ids(row["missing_inbox"]))))
    out.append(("a refused command (recorded in operator_commands too) is not missed",
                REFUSED not in ids(row["missing_inbox"]), str(ids(row["missing_inbox"]))))
    out.append(("a prospect reply in inbound_messages is not missed", REPLY not in ids(row["missing_inbox"]),
                str(ids(row["missing_inbox"]))))
    out.append(("a message younger than the grace period is not missed yet", YOUNG not in ids(row["missing_inbox"]),
                str(ids(row["missing_inbox"]))))
    out.append(("a message from before Mailbox Watch first ran is not its to record", OLD not in ids(row["missing_inbox"]),
                str(ids(row["missing_inbox"]))))
    out.append(("a recorded Sent message is not missed", ids(row["missing_sent"]) == [], str(ids(row["missing_sent"]))))
    a = assess(ok_check, row, cfg)
    out.append(("... so the check is healthy and the alert that was running ends with one all-clear",
                [a["healthy"], a["problems"], a["alert_kind"]] == [True, [], "recovered"],
                str([a["healthy"], a["problems"], a["alert_kind"]])))

    # 2. A real miss: a message in neither table, a second failing check -> the alert fires.
    set_prev_health(alerted=False)
    lost = {"message_id": LOST, "at": two_h, "from": "Lost Lead <lost@cro.example>", "subject": "Re: is this still open?"}
    row = run_sql(sql, handled + [lost], [{"message_id": SENT_OK, "at": two_h, "to": "x@cro.example"}, {"message_id": SENT_CONFIRMED, "at": two_h, "to": "z@cro.example"},
                                         {"message_id": SENT_LOST, "at": two_h, "to": "y@cro.example"}],
                  cfg["grace_min"])
    out.append(("a message in neither table is missed -- and only that one", ids(row["missing_inbox"]) == [LOST],
                str(ids(row["missing_inbox"]))))
    out.append(("a Sent message the mirror never recorded is missed -- and only that one",
                ids(row["missing_sent"]) == [SENT_LOST], str(ids(row["missing_sent"]))))
    a = assess(ok_check, row, cfg)
    out.append(("... and on the second failing check the alert fires, naming the lost message",
                a["problems"] == ["inbox-missed", "sent-missed"] and a["confirmed"] and a["alert_kind"] == "problem"
                and a["notify"] is True and '"Re: is this still open?"' in a["text"]
                and JSS["subject"] not in a["text"],
                str([a["problems"], a["confirmed"], a["alert_kind"], a["notify"]])))

    # 3. The 2026-10-28 gap: Send wrote its own rows, the folder holds the messages, the
    #    mirror never saw them. A row alone is not "mirrored" any more, and a send unseen
    #    for longer than Send's grace + block_alert_min is send-blocked -- alerted at once.
    set_prev_healthy()
    psql(SCRATCH, """
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) VALUES
  ('%s', now() - interval '50 minutes', '{a@cro.example}', true, 'workflow', false),
  ('%s', now() - interval '30 minutes', '{b@cro.example}', true, 'workflow', false);""" % (GAP, GAP_YOUNG))
    sent = [{"message_id": SENT_OK, "at": two_h, "to": "x@cro.example"}, {"message_id": SENT_CONFIRMED, "at": two_h, "to": "z@cro.example"},
            {"message_id": GAP, "at": db_iso("now() - interval '49 minutes'"), "to": "a@cro.example"},
            {"message_id": GAP_YOUNG, "at": db_iso("now() - interval '29 minutes'"), "to": "b@cro.example"}]
    row = run_sql(sql, handled, sent, cfg["grace_min"])
    out.append(("a send in the folder whose row the mirror never marked seen is missed (the 07:40 send)",
                ids(row["missing_sent"]) == sorted([GAP, GAP_YOUNG]), str(ids(row["missing_sent"]))))
    out.append(("Send is blocked once its own send has been unseen longer than its grace + block_alert_min -- "
                "only the 50-minute one, not the 30-minute one", ids(row["blocked_sends"]) == [GAP],
                str(ids(row["blocked_sends"]))))
    a = assess(ok_check, row, cfg)
    out.append(("... send-blocked alerts on the FIRST failing check, saying how to repair",
                a["problems"] == ["sent-missed", "send-blocked"] and a["confirmed"] and a["alert_kind"] == "problem"
                and "reconcile_sent_mirror.py" in a["text"] and GAP in a["text"],
                str([a["problems"], a["confirmed"], a["alert_kind"]])))
    row = run_sql(sql, handled, sent, cfg["grace_min"], listed=False)
    out.append(("send-blocked does not need the checker: with no listing it still fires",
                ids(row["blocked_sends"]) == [GAP], str(ids(row["blocked_sends"]))))
    psql(SCRATCH, "DELETE FROM mailbox_sent WHERE message_id IN ('%s', '%s');" % (GAP, GAP_YOUNG))

    # 4. The other direction: the mirror says SEEN, the folder does not hold it.
    psql(SCRATCH, """
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) VALUES
  ('%(ph)s', now() - interval '3 hours', '{c@cro.example}', true, 'sent-folder', true),
  ('%(old)s', now() - interval '47 hours 30 minutes', '{d@cro.example}', true, 'sent-folder', true),
  ('%(fb)s', now() - interval '3 hours', '{e@cro.example}', true, 'sent-folder', true),
  ('%(young)s', now() - interval '5 minutes', '{f@cro.example}', true, 'sent-folder', true),
  ('%(unl)s', now() - interval '20 minutes', '{g@cro.example}', true, 'workflow', false);""" % {
        "ph": PHANTOM, "old": PHANTOM_EDGE, "fb": FALLBACK, "young": PHANTOM_YOUNG, "unl": UNSEEN_UNLISTED})
    sent = [{"message_id": SENT_OK, "at": two_h, "to": "x@cro.example"}, {"message_id": SENT_CONFIRMED, "at": two_h, "to": "z@cro.example"}]
    row = run_sql(sql, handled, sent, cfg["grace_min"])
    out.append(("a row the mirror marks seen that the folder does not hold is a mismatch -- and only that one "
                "(not one at the window's edge, not a fallback id, not one younger than the grace, not an "
                "unseen send)", ids(row["mismatched_sent"]) == [PHANTOM], str(ids(row["mismatched_sent"]))))
    a = assess(ok_check, row, cfg)
    out.append(("... sent-mismatch needs a second failing check before it alerts",
                a["problems"] == ["sent-mismatch"] and not a["confirmed"] and a["alert_kind"] is None,
                str([a["problems"], a["confirmed"], a["alert_kind"]])))
    row = run_sql(sql, handled, [], cfg["grace_min"], listed=False)
    out.append(("with no complete listing there is no mismatch (an empty list is not an empty folder)",
                ids(row["mismatched_sent"]) == [], str(ids(row["mismatched_sent"]))))
    psql(SCRATCH, "DELETE FROM mailbox_sent WHERE message_id IN ('%s', '%s', '%s', '%s', '%s');" % (
        PHANTOM, PHANTOM_EDGE, FALLBACK, PHANTOM_YOUNG, UNSEEN_UNLISTED))
    return out


def set_prev_healthy():
    psql(SCRATCH, """
DELETE FROM mailbox_health;
INSERT INTO mailbox_health (check_name, healthy, problems, consecutive_failures, last_checked_at, last_ok_at)
VALUES ('imap', true, '{}', 0, now() - interval '20 minutes', now() - interval '20 minutes');""")


def run_all(sqls):
    """Create the scratch database once and run checks() for each SQL; returns a list of results."""
    create_scratch()
    try:
        return [checks(s) for s in sqls]
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check=False)


if __name__ == "__main__":
    results = run_all([shipped_sql()])[0]
    for label, ok, detail in results:
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "  -- " + detail))
    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)

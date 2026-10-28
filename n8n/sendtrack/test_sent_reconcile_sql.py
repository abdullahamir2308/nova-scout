"""The Sent reconcile, run for real (2026-10-28) -- what Reconcile Sent may and may not write.

    python n8n/sendtrack/test_sent_reconcile_sql.py

The pieces are the SHIPPED ones: Plan Sent Reconcile's code and Reconcile Sent's
statement, read out of ../workflows/mailbox-watch.json -- against a scratch
database created from the live schema (pg_dump --schema-only of novascout).
Fixtures only, every address on .example; the real database is only read (the
schema dump), and the scratch database is dropped afterwards.

The rules, each a check below: a send the folder holds and the mirror has unseen
is healed; a message the mirror never had is inserted exactly as the trigger
path would; a send the folder does NOT hold is never marked seen; a malformed
or undated entry is skipped, never keyed on a fallback; a second run writes
nothing (never double-record); an incomplete listing writes nothing at all; and
the read is stamped 'Sent-reconcile', never the trigger's 'Sent'.
test_sent_reconcile_mutations.py breaks each rule in turn and requires a check
here to fail.
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "workflows", "mailbox-watch.json")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_reconcilesql_test_%d" % os.getpid()
OPERATOR = "operator@ops.example"

GAP = "<gap-0740@amitrixlabs.example>"          # the 07:40 send: Send's row, never seen
SEEN = "<seen@amitrixlabs.example>"             # a normal send, already seen
MANUAL = "<manual-to-prospect@amitrixlabs.example>"  # a manual send the mirror never had
NOTE = "<note-to-operator@amitrixlabs.example>"     # to the operator only: not a warm-up send
NOT_IN_FOLDER = "<never-filed@amitrixlabs.example>"  # Send's row, not in the folder
LATE = "<late@amitrixlabs.example>"             # appears after the checker failed


def psql(db, sql, check=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def rows(sql):
    return json.loads(psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM (%s) t;" % sql))


def shipped():
    with open(WORKFLOW, encoding="utf-8") as fh:
        nodes = {n["name"]: n for n in json.load(fh)["nodes"]}
    code = nodes["Plan Sent Reconcile"]["parameters"]["jsCode"]
    return code[:code.index("// Node body")], nodes["Reconcile Sent"]["parameters"]["query"]


def plan(lib, check):
    src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(reconcilePlan(a.check, a.operator)));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
        tf.write(src)
    try:
        p = subprocess.run(["node", tf.name], input=json.dumps({"check": check, "operator": OPERATOR}),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    finally:
        os.unlink(tf.name)
    if p.returncode:
        raise PlanFailed("Plan Sent Reconcile threw: " + p.stderr.strip().splitlines()[0][:300])
    return json.loads(p.stdout)


class PlanFailed(Exception):
    pass


def reconcile(sql, payload):
    arg = json.dumps(payload)
    assert "$q$" not in arg
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", SCRATCH, "-X", "-q", "--csv",
                        "-v", "ON_ERROR_STOP=1"], input=sql.replace("$1", "$q$" + arg + "$q$"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("Reconcile Sent failed (%d):\n%s" % (p.returncode, p.stderr[-1500:]))
    return next(csv.DictReader(io.StringIO(p.stdout)))


def entry(mid, date, to, cc="", bcc=""):
    return {"message_id": mid, "at": "2026-10-28T07:40:40+00:00", "date": date, "to": to, "cc": cc, "bcc": bcc}


def setup():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner", "novascout"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if schema.returncode:
        raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
    psql(SCRATCH, schema.stdout)


def seed():
    psql(SCRATCH, """
TRUNCATE mailbox_sent, mailbox_sync;
DELETE FROM contacts; DELETE FROM leads;
INSERT INTO settings (key, value) VALUES ('operator_email', '%(op)s') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO leads (id, domain, company_name, country, source, status) OVERRIDING SYSTEM VALUE VALUES
  (1, 'prospect-cro.example', 'Prospect CRO', 'India', 'ichgcp', 'sent');
INSERT INTO mailbox_sync (folder, last_synced_at, messages_seen, undated) VALUES ('Sent', '2026-10-28 07:20:47+00', 9, 0);
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source, seen_in_sent_folder) VALUES
  ('%(gap)s',  '2026-10-28 07:40:36+00', '{bd@prospect-cro.example}', true, 1, 'workflow', false),
  ('%(seen)s', '2026-10-28 07:20:36+00', '{bd@prospect-cro.example}', true, 1, 'workflow', true),
  ('%(nif)s',  '2026-10-28 07:50:36+00', '{bd@prospect-cro.example}', true, 1, 'workflow', false);
""" % {"op": OPERATOR, "gap": GAP, "seen": SEEN, "nif": NOT_IN_FOLDER})


def checks(sql, lib):
    try:
        return _checks(sql, lib)
    except PlanFailed as e:
        return [("the shipped plan code runs on every listing shape", False, str(e))]


def _checks(sql, lib):
    out = []

    def check(label, got, want):
        out.append((label, got == want, "got %r, want %r" % (got, want)))

    seed()
    listing = {"ok": True, "status": "passed", "hours": 48, "since": "2026-10-26T08:00:00+00:00", "sent": [
        entry(GAP, "Wed, 28 Oct 2026 07:40:36 +0000", "bd@prospect-cro.example"),
        entry(SEEN, "Wed, 28 Oct 2026 07:20:36 +0000", "bd@prospect-cro.example"),
        entry(MANUAL, "Wed, 28 Oct 2026 07:45:00 +0000", "Jane <jane@prospect-cro.example>", cc="me@amitrixlabs.com"),
        entry(NOTE, "Wed, 28 Oct 2026 07:46:00 +0000", OPERATOR),
        entry("no-angle-brackets@amitrixlabs.example", "Wed, 28 Oct 2026 07:47:00 +0000", "x@prospect-cro.example"),
        entry("<undated@amitrixlabs.example>", "", "x@prospect-cro.example"),
    ]}
    p = plan(lib, listing)
    check("a complete listing is listed; the malformed and the undated entries are skipped, not keyed",
          [p["listed"], p["payload"]["skipped"], sorted(r["message_id"] for r in p["payload"]["rows"])],
          [True, 2, sorted([GAP, SEEN, MANUAL, NOTE])])
    r = reconcile(sql, p["payload"])
    check("the 07:40 send is healed, the manual send inserted, the seen one left alone",
          [r["listed"], r["healed"], json.loads(r["healed_ids"]), r["new_rows"], r["already_seen"]],
          ["t", "1", [GAP], "2", "1"])
    check("the healed row is now seen, and still Send's own row (source workflow)",
          rows("SELECT source, seen_in_sent_folder FROM mailbox_sent WHERE message_id = '%s'" % GAP),
          [{"source": "workflow", "seen_in_sent_folder": True}])
    check("a send the folder does not hold is NEVER marked seen",
          rows("SELECT seen_in_sent_folder FROM mailbox_sent WHERE message_id = '%s'" % NOT_IN_FOLDER),
          [{"seen_in_sent_folder": False}])
    check("the manual send is inserted as the trigger would: sent-folder, seen, external, linked to its lead",
          rows("SELECT source, seen_in_sent_folder, external, lead_id, recipients, sent_at FROM mailbox_sent "
               "WHERE message_id = '%s'" % MANUAL),
          [{"source": "sent-folder", "seen_in_sent_folder": True, "external": True, "lead_id": 1,
            "recipients": ["jane@prospect-cro.example", "me@amitrixlabs.com"],
            "sent_at": "2026-10-28T07:45:00+00:00"}])
    check("a note only to the operator is inserted as not a warm-up send",
          rows("SELECT external FROM mailbox_sent WHERE message_id = '%s'" % NOTE), [{"external": False}])
    check("nothing keyed on a fallback or the malformed id",
          rows("SELECT count(*) AS n FROM mailbox_sent WHERE message_id LIKE '<no-message-id.%' "
               "OR message_id LIKE '%no-angle-brackets%' OR message_id = '<undated@amitrixlabs.example>'"), [{"n": 0}])
    check("the read is stamped Sent-reconcile; the trigger's Sent stamp is untouched",
          rows("SELECT folder, messages_seen, last_synced_at > '2026-10-28 08:00+00' AS fresh FROM mailbox_sync "
               "ORDER BY folder"),
          [{"folder": "Sent", "messages_seen": 9, "fresh": False},
           {"folder": "Sent-reconcile", "messages_seen": 4, "fresh": True}])
    before = rows("SELECT message_id, seen_in_sent_folder, recorded_at FROM mailbox_sent ORDER BY message_id")

    r = reconcile(sql, plan(lib, listing)["payload"])
    check("the same listing again writes nothing: never double-record",
          [r["healed"], r["new_rows"], r["already_seen"]], ["0", "0", "4"])
    check("... the mirror is row for row what it was",
          rows("SELECT message_id, seen_in_sent_folder, recorded_at FROM mailbox_sent ORDER BY message_id"), before)

    psql(SCRATCH, "INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source, "
                  "seen_in_sent_folder) VALUES ('%s', now(), '{bd@prospect-cro.example}', true, 1, 'workflow', false);"
                  % LATE)
    stamp = rows("SELECT last_synced_at FROM mailbox_sync WHERE folder = 'Sent-reconcile'")
    for label, bad in [("the checker unreachable", {"error": {"message": "connect ECONNREFUSED"}}),
                       ("the IMAP check failed", {"ok": False, "status": "failed", "stage": "login",
                                                  "sent": [entry(LATE, "Wed, 28 Oct 2026 08:00:00 +0000", "a@b.example")]}),
                       ("a half answer (no list)", {"ok": True, "status": "passed"})]:
        p = plan(lib, bad)
        r = reconcile(sql, p["payload"])
        check("%s: not listed, nothing written" % label, [p["listed"], r["listed"], r["listed_rows"], r["new_rows"],
                                                          r["healed"]], [False, "f", "0", "0", "0"])
    forged = plan(lib, listing)["payload"]
    forged["listed"] = False
    forged["rows"].append({"message_id": LATE, "sent_at": "2026-10-28T08:00:00Z", "recipients": [], "external": True})
    r = reconcile(sql, forged)
    check("rows handed in with listed=false are ignored by the statement itself",
          [r["listed_rows"], r["healed"], r["new_rows"]], ["0", "0", "0"])
    check("... the late send stays unseen, and the reconcile stamp is not moved by a failed read",
          [rows("SELECT seen_in_sent_folder FROM mailbox_sent WHERE message_id = '%s'" % LATE),
           rows("SELECT last_synced_at FROM mailbox_sync WHERE folder = 'Sent-reconcile'")],
          [[{"seen_in_sent_folder": False}], stamp])
    return out


def run_all(cases):
    """cases: [(sql, js_lib)] -> a list of check results per case; one scratch database for all."""
    setup()
    try:
        return [checks(sql, lib) for sql, lib in cases]
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check=False)


if __name__ == "__main__":
    lib, sql = shipped()
    results = run_all([(sql, lib)])[0]
    for label, ok, detail in results:
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "  -- " + detail))
    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)

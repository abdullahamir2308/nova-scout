"""The Send safety brake's SQL, run for real -- when it trips, when it does not, and when it stays tripped.

    python n8n/sendtrack/test_send_brake_sql.py

Section 9, Workflow 6 (operator decision 2026-10-19): "if bounces over the
last 3 days exceed 10% of sends, or any spam complaint arrives, Send pauses and
the operator is alerted." (10% since
2026-10-24, operator decision; it was 3%.) test_decide.js proves what Decide Send does with an
open pause; this proves the pauses. The statements are the SHIPPED ones (Check
Brake, Load Send State and Record Brake Alert, read out of
../workflows/send.json, with Config's own brake values), against a scratch
database created from the live schema (pg_dump --schema-only of novascout)
with migration 022 applied -- so send_brake_stats() is the migration's too:
021 made it, 022 (operator decision 2026-10-24) stopped it counting a delay
warning. Fixtures only; the real novascout database is only read (the schema
dump), and the scratch database is dropped afterwards.

test_send_brake_mutations.py breaks each rule in turn and requires a check here
(or in test_decide.js / test_mailbox.js) to fail.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKFLOW = os.path.join(HERE, "..", "workflows", "send.json")
# The latest definition of send_brake_stats(): 021 created it, 022 replaced it.
MIGRATION = os.path.join(REPO, "postgres", "migrations", "022_hard_bounces.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_brakesql_test_%d" % os.getpid()


def psql(db, sql, check=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def shipped():
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = json.load(fh)
    nodes = {n["name"]: n for n in wf["nodes"]}
    cfg = {a["name"]: a["value"] for a in nodes["Config"]["parameters"]["assignments"]["assignments"]}
    return {"check": nodes["Check Brake"]["parameters"]["query"],
            "load": nodes["Load Send State"]["parameters"]["query"],
            "record": nodes["Record Brake Alert"]["parameters"]["query"], "cfg": cfg}


def migration():
    with open(MIGRATION, encoding="utf-8") as fh:
        return fh.read()


def bind(sql, params):
    """$1..$n -> dollar-quoted literals, in one pass, after the comments are gone."""
    sql = re.sub(r"--[^\n]*", "", sql).strip().rstrip(";")

    def lit(m):
        v = params[int(m.group(1)) - 1]
        s = v if isinstance(v, str) else json.dumps(v)
        assert "$q$" not in s
        return "$q$" + s + "$q$"
    return re.sub(r"\$(\d+)\b", lit, sql)


def create_scratch(mig):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner", "novascout"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if schema.returncode:
        raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
    psql(SCRATCH, schema.stdout)
    psql(SCRATCH, mig)


def reset():
    psql(SCRATCH, "TRUNCATE send_pauses, inbound_messages, outreach_log, leads RESTART IDENTITY CASCADE;\n"
                  "INSERT INTO settings (key, value) VALUES ('operator_email', 'operator@example.invalid') "
                  "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;")


def sends(n, bounced, ago="1 hour", confirmed=True, first_lead=1, kind=None):
    """n emails sent `ago`, the first `bounced` of them bounced -- with bounce_kind `kind` (migration 022; None is a
    bounce no classifier has labelled, as an older Mailbox Watch wrote it); one lead each."""
    psql(SCRATCH, """
INSERT INTO leads (id, domain, status) OVERRIDING SYSTEM VALUE
SELECT g, 'cro' || g || '.example', 'sent' FROM generate_series(%(a)d, %(b)d) g ON CONFLICT (id) DO NOTHING;
INSERT INTO outreach_log (lead_id, channel, sent_at, message_id, outcome, bounce_kind)
SELECT g, 'email', now() - interval '%(ago)s', %(mid)s, CASE WHEN g < %(a)d + %(k)d THEN 'bounced' END,
       CASE WHEN g < %(a)d + %(k)d THEN %(kind)s END
  FROM generate_series(%(a)d, %(b)d) g;
""" % {"a": first_lead, "b": first_lead + n - 1, "ago": ago, "k": bounced,
       "mid": "'<m' || g || '@amitrixlabs.example>'" if confirmed else "NULL",
       "kind": "'%s'" % kind if kind else "NULL"})


def complaint(mid, ago="5 minutes", flagged=True):
    psql(SCRATCH, """
INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, classification, processed_at, spam_complaint)
VALUES ('%s', now() - interval '%s', 'angry@cro.example', 'Re: Klixar + sponsor inquiries', 'opt-out',
        now() - interval '%s', %s);""" % (mid, ago, ago, "true" if flagged else "false"))


def run_check(sql, cfg):
    """Check Brake as the workflow runs it -- a top-level statement (its INSERTs are data-modifying CTEs) --
    with the one row it returns read back by column name."""
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", SCRATCH, "-X", "-q", "--csv",
                        "-v", "ON_ERROR_STOP=1"],
                       input=bind(sql, ["", cfg["brake_days"], cfg["brake_bounce_pct"]]) + ";",
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("Check Brake failed (%d):\n%s" % (p.returncode, p.stderr[-1500:]))
    head, row = p.stdout.strip().splitlines()[:2]
    return {k: int(v) for k, v in zip(head.split(","), row.split(","))}


def pauses():
    out = psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(p) ORDER BY p.id), '[]') FROM "
                        "(SELECT id, reason, detail, evidence, alerted_at, cleared_at FROM send_pauses) p;")
    return json.loads(out)


def open_reasons():
    return sorted(p["reason"] for p in pauses() if p["cleared_at"] is None)


def checks(s):
    """(label, ok, detail) for every expectation, against the statements in `s`."""
    cfg = s["cfg"]
    out = []

    def expect(label, ok, detail=""):
        out.append((label, bool(ok), str(detail)))

    expect("the shipped Config carries Section 9's brake: 3 days, 10%",
           [cfg["brake_days"], cfg["brake_bounce_pct"]] == [3, 10], [cfg["brake_days"], cfg["brake_bounce_pct"]])

    # 1. At the line, not over it.
    reset()
    sends(100, 10)
    r = run_check(s["check"], cfg)
    expect("10 bounces in 100 sends (10%, not over) -> no pause", open_reasons() == [] and r["tripped_bounce_rate"] == 0,
           [r, open_reasons()])
    expect("... and Check Brake reports what it counted", [r["sends"], r["bounced"]] == [100, 10], r)

    # 2. Over the line.
    reset()
    sends(100, 11)
    r = run_check(s["check"], cfg)
    p = pauses()
    expect("11 bounces in 100 sends (11%) -> a bounce-rate pause", open_reasons() == ["bounce-rate"], p)
    expect("... whose detail is what the operator is told",
           p and "11 of the 100 emails sent since" in p[0]["detail"] and "(11.0%), over the 10% brake" in p[0]["detail"]
           and "cro1.example" in p[0]["detail"], p and p[0]["detail"])
    r2 = run_check(s["check"], cfg)
    expect("a second tick latches nothing new: still one open pause, detail unchanged",
           len(pauses()) == 1 and pauses()[0]["detail"] == p[0]["detail"] and r2["tripped_bounce_rate"] == 0, pauses())

    # 2b. Only a permanent bounce counts (migration 022, operator decision 2026-10-24). Case 2's bounces carry no
    #     kind at all -- what a Mailbox Watch older than the migration writes -- and they trip it: an unlabelled
    #     bounce is no proof of a delay.
    reset()
    sends(100, 11, kind="transient")
    r = run_check(s["check"], cfg)
    expect("11 delay warnings in 100 sends -> no pause: a delay is not a bounce for the brake",
           open_reasons() == [] and [r["sends"], r["bounced"], r["tripped_bounce_rate"]] == [100, 0, 0], [r, open_reasons()])
    reset()
    sends(100, 11, kind="permanent")
    r = run_check(s["check"], cfg)
    expect("11 permanent bounces in 100 sends -> a pause", open_reasons() == ["bounce-rate"] and r["bounced"] == 11,
           [r, open_reasons()])
    reset()
    sends(100, 10, kind="permanent")
    sends(5, 5, kind="transient", first_lead=101)
    r = run_check(s["check"], cfg)
    expect("10 permanent + 5 delays in 105 sends -> no pause (10 of 105; the delays are sends, not bounces)",
           open_reasons() == [] and [r["sends"], r["bounced"]] == [105, 10], [r, open_reasons()])
    row = json.loads(psql(SCRATCH, "SELECT row_to_json(t) FROM (\n" + bind(s["load"], ["", cfg["min_gap_min"],
                                                                                        cfg["send_probability"],
                                                                                        cfg["brake_days"]]) + "\n) t;"))
    expect("... and Load Send State shows the delays apart from the bounces",
           [row["brake"]["bounced"], row["brake"]["delayed"]] == [10, 5]
           and sorted(row["brake"]["bounced_domains"]) == sorted("cro%d.example" % i for i in range(1, 11)), row["brake"])
    sends(2, 2, kind="permanent", first_lead=201)
    r = run_check(s["check"], cfg)
    expect("... two more permanent bounces (12 of 107, 11.2%) -> a pause", open_reasons() == ["bounce-rate"] and r["bounced"] == 12,
           [r, open_reasons()])

    # 3. The window is the last N days.
    reset()
    sends(10, 0)
    sends(5, 5, ago="%d days 1 hour" % cfg["brake_days"], first_lead=101)
    run_check(s["check"], cfg)
    expect("bounced sends older than the window do not count -> no pause", open_reasons() == [], pauses())

    # 4. A claim is not a send.
    reset()
    sends(100, 11)
    sends(100, 0, confirmed=False, first_lead=201)
    run_check(s["check"], cfg)
    expect("unconfirmed claims are not sends (11 of 100 confirmed, 100 claims beside) -> still a pause",
           open_reasons() == ["bounce-rate"], pauses())

    # 5. Only the operator clears it -- and clearing restarts the count.
    reset()
    sends(100, 11, ago="2 hours")
    run_check(s["check"], cfg)
    # An hour ago: tripped 90 minutes ago, reviewed and cleared 30 minutes later.
    psql(SCRATCH, "UPDATE send_pauses SET tripped_at = now() - interval '90 minutes', "
                  "cleared_at = now() - interval '1 hour', cleared_note = 'reviewed' WHERE cleared_at IS NULL;")
    run_check(s["check"], cfg)
    expect("once cleared, the bounces just reviewed (sent before the clear) do not trip it again",
           open_reasons() == [], pauses())
    row = json.loads(psql(SCRATCH, "SELECT row_to_json(t) FROM (\n" + bind(s["load"], ["", cfg["min_gap_min"],
                                                                                        cfg["send_probability"],
                                                                                        cfg["brake_days"]]) + "\n) t;"))
    expect("... and Load Send State shows Decide no pause: a cleared one is history, not a brake",
           row["pauses"] == [], row["pauses"])
    sends(5, 1, ago="10 minutes", first_lead=301)
    run_check(s["check"], cfg)
    expect("... and the count restarts at the clear: 1 bounce in the 5 sent since (20%) trips a new pause",
           open_reasons() == ["bounce-rate"] and len(pauses()) == 2, pauses())

    # 6. Any spam complaint.
    reset()
    complaint("<c1@cro.example>")
    r = run_check(s["check"], cfg)
    p = pauses()
    expect("one spam complaint, no sends at all -> a spam-complaint pause",
           open_reasons() == ["spam-complaint"] and r["tripped_spam_complaint"] == 1, p)
    expect("... naming who complained and about what",
           p and "1 spam complaint(s)" in p[0]["detail"] and "angry@cro.example" in p[0]["detail"]
           and "Re: Klixar + sponsor inquiries" in p[0]["detail"], p and p[0]["detail"])
    reset()
    complaint("<c2@cro.example>", flagged=False)
    run_check(s["check"], cfg)
    expect("an opt-out that is not a complaint -> no pause", open_reasons() == [], pauses())
    reset()
    complaint("<c3@cro.example>", ago="2 hours")
    run_check(s["check"], cfg)
    psql(SCRATCH, "UPDATE send_pauses SET tripped_at = now() - interval '90 minutes', "
                  "cleared_at = now() - interval '1 hour' WHERE cleared_at IS NULL;")
    run_check(s["check"], cfg)
    expect("a complaint from before the last clear does not trip it again", open_reasons() == [], pauses())
    complaint("<c4@cro.example>", ago="1 minute")
    run_check(s["check"], cfg)
    expect("... a new one does", open_reasons() == ["spam-complaint"], pauses())

    # 7. Both at once: two pauses, one per reason.
    reset()
    sends(5, 1)
    complaint("<c5@cro.example>")
    run_check(s["check"], cfg)
    expect("bounces over the line AND a complaint -> one pause per reason", open_reasons() == ["bounce-rate", "spam-complaint"],
           pauses())

    # 8. Load Send State shows Decide the open pauses and the numbers.
    row = json.loads(psql(SCRATCH, "SELECT row_to_json(t) FROM (\n" + bind(s["load"], ["", cfg["min_gap_min"],
                                                                                        cfg["send_probability"],
                                                                                        cfg["brake_days"]]) + "\n) t;"))
    expect("Load Send State returns both open pauses, unalerted, with the operator's address",
           sorted(x["reason"] for x in row["pauses"]) == ["bounce-rate", "spam-complaint"]
           and all(x["alerted_at"] is None and x["detail"] for x in row["pauses"])
           and row["operator_email"] == "operator@example.invalid", row["pauses"])
    expect("... and the brake numbers it counted", [row["brake"]["sends"], row["brake"]["bounced"],
                                                    len(row["brake"]["complaints"])] == [5, 1, 1], row["brake"])

    # 9. Record Brake Alert stamps only on an accepted alert.
    ids = [x["id"] for x in row["pauses"]]
    if len(ids) != 2:
        expect("Record Brake Alert can be tested (two open pauses to stamp)", False, ids)
        return out
    alert = {"notify": True, "to": "operator@example.invalid", "subject": "s", "text": "t", "pause_ids": ids}
    psql(SCRATCH, bind(s["record"], [json.dumps(alert), json.dumps({"error": "connect ECONNREFUSED"})]) + ";")
    expect("an alert SMTP refused stamps nothing -- it is sent again next tick",
           all(p["alerted_at"] is None for p in pauses()), pauses())
    psql(SCRATCH, bind(s["record"], [json.dumps(alert), json.dumps({"error": "timeout after DATA",
                                                                     "accepted": ["operator@example.invalid"]})]) + ";")
    expect("an output carrying an error is not an accepted alert, whatever else it lists",
           all(p["alerted_at"] is None for p in pauses()), pauses())
    psql(SCRATCH, bind(s["record"], [json.dumps(dict(alert, pause_ids=ids[:1])),
                                     json.dumps({"accepted": ["operator@example.invalid"], "rejected": []})]) + ";")
    p = {x["id"]: x for x in pauses()}
    expect("an accepted alert stamps exactly the pauses it told the operator about",
           p[ids[0]]["alerted_at"] is not None and p[ids[1]]["alerted_at"] is None, pauses())
    return out


def run_all(variants):
    """variants: [(statements, migration_sql)]. One scratch database; the migration is re-applied for each
    variant (its function is CREATE OR REPLACE), so a mutated migration is tested as it would ship."""
    create_scratch(variants[0][1])
    try:
        results = []
        for stmts, mig in variants:
            psql(SCRATCH, mig)
            results.append(checks(stmts))
        return results
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check=False)


if __name__ == "__main__":
    results = run_all([(shipped(), migration())])[0]
    for label, ok, detail in results:
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "  -- " + detail[:400]))
    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)

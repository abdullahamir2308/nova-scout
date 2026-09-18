"""IMAP Health dry run -- the real workflow, the real checker, nothing able to reach a real inbox.

    python n8n/sendtrack/dryrun/health_dryrun.py [--keep]

REAL: the IMAP Health workflow (built by build_workflow.py from the same node
list as the shipped one; the build proves only credentials and the schedule
trigger differ), n8n itself, every Postgres statement, the imap-health checker
and imap_preflight.py -- including a real read-only IMAP session to the
mailbox -- and nodemailer's SMTP conversation.

NOT REAL: the database is novascout_dryrun, a copy of novascout with every
contact address rewritten and the operator's address replaced by
operator@dryrun.invalid; SMTP goes to smtp_sink.js inside the n8n container,
which writes to disk and delivers nothing. The IMAP-failure scenario uses a
second, throwaway checker container pointed at a host that cannot resolve. The
checker-unreachable scenario points the workflow at a closed port. Those two
variants differ from the dry-run workflow only in Config.checker_url, asserted.

The real novascout database is only READ (pg_dump); a fingerprint of it is
compared before and after. Afterwards the scratch databases, the throwaway
container and the dry-run workflows are removed; --keep leaves them.

Every expectation is asserted. Exit 1 if any did not hold.
"""
import copy
import email
import email.policy
import io
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SENDTRACK = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
REAL, BASE, DRY = "novascout", "novascout_dryrun_base", "novascout_dryrun"
SINK_DIR, SINK_JS = "/tmp/novascout_sink", "/tmp/novascout_smtp_sink.js"
BAD_CHECKER = "nova-scout-imaphealth-dryrun-badhost"
OPERATOR = "operator@dryrun.invalid"
SCRATCH = tempfile.mkdtemp(prefix="novascout-health-dryrun-")
KEEP = "--keep" in sys.argv
RESULTS = []


def expect(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail else ""))


def sh(args, stdin=None, check=True):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("FAILED (%d): %s\n%s\n%s" % (p.returncode, " ".join(args[:6]), p.stdout[-1500:], p.stderr[-1500:]))
    return p.stdout


def psql(db, sql):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At"],
              stdin=sql)


def psql_json(db, sql):
    return json.loads(psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql + ") t;").strip())


def fingerprint(db):
    return json.loads(psql(db, """SELECT json_build_object(
      'inbound_messages', (SELECT count(*) FROM inbound_messages),
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent),
      'mailbox_sync', (SELECT json_agg(last_synced_at) FROM mailbox_sync),
      'mailbox_health', (SELECT json_agg(t) FROM mailbox_health t),
      'outreach_log', (SELECT count(*) FROM outreach_log),
      'blocklist', (SELECT count(*) FROM blocklist),
      'settings_md5', (SELECT json_object_agg(key, md5(value) ORDER BY key) FROM settings));""").strip())


def make_base():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                     "CREATE DATABASE %s;" % (DRY, BASE, BASE))
    sh(["docker", "exec", PG, "sh", "-c",
        "pg_dump -U %s -d %s --no-owner | psql -U %s -d %s -q -X -v ON_ERROR_STOP=1 >/dev/null" % (PGUSER, REAL, PGUSER, BASE)])
    psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
               "WHERE email IS NOT NULL AND email <> '';\n"
               "DELETE FROM settings;\nINSERT INTO settings (key, value) VALUES ('operator_email', '%s');\n"
               "DELETE FROM mailbox_health;" % OPERATOR)


def fresh(seed_sql=""):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s TEMPLATE %s;" % (DRY, DRY, BASE))
    if seed_sql:
        psql(DRY, seed_sql)


def build():
    env = dict(os.environ, SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"),
               SENDTRACK_DRYRUN_NOW="2026-09-16T12:00:00.000Z", PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("build failed:\n" + p.stderr[-2000:])
    with io.open(os.path.join(SCRATCH, "variants", "imaphealth-dryrun.json"), encoding="utf-8") as fh:
        return json.load(fh)


def with_checker(wf, wid, name, url):
    """The dry-run workflow with ONLY Config.checker_url changed -- proven here."""
    v = copy.deepcopy(wf)
    v["id"], v["name"] = wid, name
    cfg = [n for n in v["nodes"] if n["name"] == "Config"][0]
    [a for a in cfg["parameters"]["assignments"]["assignments"] if a["name"] == "checker_url"][0]["value"] = url

    def blank(w):
        w = copy.deepcopy(w)
        for n in w["nodes"]:
            if n["name"] == "Config":
                for a in n["parameters"]["assignments"]["assignments"]:
                    if a["name"] == "checker_url":
                        a["value"] = None
        return (w["nodes"], w["connections"])
    assert blank(v) == blank(wf), "%s differs from the dry-run workflow in more than checker_url" % wid
    return v


def import_wf(wf):
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_wf.json"], stdin=json.dumps(wf))
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_wf.json"])
    sh(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_wf.json"])


def execute(wid):
    out = sh(["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "-e", "N8N_RUNNERS_ENABLED=false", N8N,
              "n8n", "execute", "--id=" + wid, "--rawOutput"], check=False)
    start = out.find("{\n")
    if start == -1:
        raise SystemExit("no execution JSON from n8n execute --id=%s:\n%s" % (wid, out[-2000:]))
    run = json.loads(out[start:])
    err = run.get("data", {}).get("resultData", {}).get("error")
    if err:
        raise SystemExit("execution %s errored: %s" % (wid, json.dumps(err)[:1500]))
    return run


def out1(run, node):
    rd = run["data"]["resultData"]["runData"]
    if node not in rd:
        return None
    main = rd[node][0]["data"]["main"]
    items = [i["json"] for i in (main[0] if main and main[0] else [])]
    return items[0] if items else None


def start_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; rm -rf %s; exit 0" % SINK_DIR])
    with io.open(os.path.join(HERE, "smtp_sink.js"), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % SINK_JS], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "node", SINK_JS, SINK_DIR, "2525"])
    for _ in range(50):
        if sh(["docker", "exec", N8N, "sh", "-c", "test -d %s && echo up; exit 0" % SINK_DIR]).strip() == "up":
            return
        time.sleep(0.2)
    raise SystemExit("the SMTP sink did not start")


def stop_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; exit 0"])
    time.sleep(0.5)


def sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def sink_message(entry):
    raw = subprocess.run(["docker", "exec", N8N, "cat", entry["file"]], stdout=subprocess.PIPE).stdout
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return msg["Subject"], msg.get_body(preferencelist=("plain",)).get_content()


def health_row():
    rows = psql_json(DRY, "SELECT healthy, problems, consecutive_failures, failing_since IS NOT NULL AS failing, "
                          "last_alerted_at IS NOT NULL AS alerted, alerted_problems FROM mailbox_health")
    return rows[0] if rows else None


# ---------------------------------------------------------------------------------

print("IMAP Health dry run -- scratch dir %s\n" % SCRATCH)
real_before = fingerprint(REAL)
print("  real novascout BEFORE: %s" % json.dumps(real_before, sort_keys=True))

dry = build()
network = sh(["docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}", N8N]).strip()
bad = with_checker(dry, "imaphealth0001bad", "DRY RUN - IMAP Health (checker on an unresolvable IMAP host)",
                   "http://%s:8765/check" % BAD_CHECKER)
gone = with_checker(dry, "imaphealth0001gone", "DRY RUN - IMAP Health (checker port closed)",
                    "http://imap-health:9/check")
for wf in (dry, bad, gone):
    import_wf(wf)
make_base()
start_sink()

# --- A. real mailbox, real records: healthy ------------------------------------------
print("\n-- A. a real IMAP session against a copy of the real records --")
fresh()
run = execute("imaphealth0001dry")
chk, db, a = out1(run, "Check IMAP"), out1(run, "Load Health State"), out1(run, "Assess Health")
expect("the checker answered: fresh IMAP session ok",
       chk and chk.get("ok") is True and chk.get("status") == "passed", json.dumps({k: chk.get(k) for k in ("status", "stage", "detail")}))
expect("... with a non-empty comparison (the check is not vacuous)",
       len(chk.get("inbox", [])) > 0 and len(chk.get("sent", [])) > 0,
       "%d INBOX, %d Sent Message-IDs" % (len(chk.get("inbox", [])), len(chk.get("sent", []))))
expect("every one of them is in Mailbox Watch's records -- nothing missed",
       db["missing_inbox"] == [] and db["missing_sent"] == [], json.dumps([db["missing_inbox"], db["missing_sent"]])[:300])
expect("Assess Health: healthy, nothing to say", a["healthy"] and a["alert_kind"] is None and not a["notify"])
expect("mailbox_health: one healthy row", health_row() == {"healthy": True, "problems": [], "consecutive_failures": 0,
                                                           "failing": False, "alerted": False, "alerted_problems": None},
       json.dumps(health_row()))
expect("no email", sink_log() == [])

# --- B. Mailbox Watch misses an INBOX message --------------------------------------------
print("\n-- B. an INBOX message Mailbox Watch did not record (its row removed from the copy) --")
fresh()
held = psql_json(DRY, "SELECT message_id, subject FROM inbound_messages ORDER BY processed_at DESC LIMIT 1")[0]
psql(DRY, "CREATE TABLE held AS SELECT * FROM inbound_messages WHERE message_id = '%s';\n"
          "DELETE FROM inbound_messages WHERE message_id = '%s';" % ((held["message_id"].replace("'", "''"),) * 2))
run = execute("imaphealth0001dry")
db, a = out1(run, "Load Health State"), out1(run, "Assess Health")
expect("B1: Load Health State finds exactly that message missed",
       [m["message_id"] for m in db["missing_inbox"]] == [held["message_id"]], json.dumps(db["missing_inbox"])[:300])
expect("B1: first failing check -> not confirmed, no alert (a wake from sleep looks like this)",
       a["problems"] == ["inbox-missed"] and not a["confirmed"] and a["alert_kind"] is None and sink_log() == [])

stop_sink()
run = execute("imaphealth0001dry")
a, sent = out1(run, "Assess Health"), out1(run, "Send Alert")
expect("B2: second failing check -> confirmed, alert attempted ... while SMTP is DOWN",
       a["alert_kind"] == "problem" and a["notify"] and sent is not None and "error" in sent, json.dumps(sent)[:200])
row = health_row()
expect("B2: the failed alert is NOT recorded as sent (so the next check tries again)",
       row["consecutive_failures"] == 2 and not row["alerted"], json.dumps(row))

start_sink()
run = execute("imaphealth0001dry")
a, rec = out1(run, "Assess Health"), out1(run, "Record Health")
log = sink_log()
expect("B3: SMTP back -> the alert is sent, to the operator address from settings only",
       a["alert_kind"] == "problem" and len(log) == 1 and log[0]["rcpt_to"] == ["<%s>" % OPERATOR],
       json.dumps([l.get("rcpt_to") for l in log]))
subject, body = sink_message(log[0]) if log else ("", "")
expect("B3: the email names the problem and lists the message for a by-hand opt-out check",
       subject == "[Nova Scout] IMAP health: Mailbox Watch has missed INBOX mail" and held["subject"] in body
       and "blocklist any opt-out" in body, subject)
expect("B3: mailbox_health records the alert", rec["alert_sent"] is True and health_row()["alerted"]
       and health_row()["alerted_problems"] == ["inbox-missed"], json.dumps(health_row()))
print("       ---- the alert as delivered ----\n       Subject: %s\n       %s" % (subject, body.strip().replace("\n", "\n       ")))

run = execute("imaphealth0001dry")
expect("B4: still missing on the next check -> no second email", out1(run, "Assess Health")["alert_kind"] is None
       and len(sink_log()) == 1)

psql(DRY, "INSERT INTO inbound_messages SELECT * FROM held;")
run = execute("imaphealth0001dry")
a = out1(run, "Assess Health")
log = sink_log()
subject, body = sink_message(log[-1]) if len(log) == 2 else ("", "")
expect("B5: recorded again -> one all-clear email", a["alert_kind"] == "recovered" and len(log) == 2
       and subject == "[Nova Scout] IMAP health: OK again", subject)
expect("B5: mailbox_health closes the episode", health_row() == {
    "healthy": True, "problems": [], "consecutive_failures": 0, "failing": False, "alerted": False,
    "alerted_problems": None}, json.dumps(health_row()))

# --- C. the Sent mirror misses a message ---------------------------------------------------
print("\n-- C. a Sent message missing from the mirror --")
fresh("DELETE FROM mailbox_sent WHERE message_id = (SELECT message_id FROM mailbox_sent ORDER BY sent_at DESC LIMIT 1);")
before = len(sink_log())
execute("imaphealth0001dry")
run = execute("imaphealth0001dry")
a = out1(run, "Assess Health")
log = sink_log()
subject, body = sink_message(log[-1]) if len(log) == before + 1 else ("", "")
expect("C: two checks -> sent-missed alert, saying the warm-up ceiling does not count it",
       a["problems"] == ["sent-missed"] and subject == "[Nova Scout] IMAP health: Mailbox Watch has missed Sent mail"
       and "warm-up ceiling does not count them" in body, subject)

# --- D. IMAP itself fails -- the real pre-flight, pointed at a host that cannot resolve --------
print("\n-- D. IMAP unreachable: a throwaway checker whose IMAP host cannot resolve --")
sh(["docker", "rm", "-f", BAD_CHECKER], check=False)
sh(["docker", "run", "-d", "--name", BAD_CHECKER, "--network", network, "--user", "65534:65534", "--read-only",
    "--tmpfs", "/tmp", "-e", "NOVASCOUT_MAILBOX_ADDRESS=probe@dryrun.invalid",
    "-e", "NOVASCOUT_MAILBOX_PASSWORD=not-a-password", "-e", "NOVASCOUT_IMAP_HOST=imap.nonexistent.invalid",
    "-e", "NOVASCOUT_IMAP_PORT=993", "-e", "NOVASCOUT_MASTER_REF=/opt/novascout/NovaScout_MasterRef.md",
    "-e", "PYTHONDONTWRITEBYTECODE=1",
    "--mount", "type=bind,source=%s,target=/opt/novascout/sendtrack,readonly" % SENDTRACK,
    "--mount", "type=bind,source=%s,target=/opt/novascout/NovaScout_MasterRef.md,readonly"
    % os.path.join(REPO, "NovaScout_MasterRef.md"),
    "python:3.12-alpine", "python", "-u", "/opt/novascout/sendtrack/imap_health_server.py"])
for _ in range(50):
    if "listening" in sh(["docker", "logs", BAD_CHECKER], check=False):
        break
    time.sleep(0.2)
fresh()
before = len(sink_log())
run = execute("imaphealth0001bad")
chk, a = out1(run, "Check IMAP"), out1(run, "Assess Health")
expect("D1: the real pre-flight fails at connect", chk.get("status") == "failed" and chk.get("stage") == "connect",
       "%s: %s" % (chk.get("stage"), chk.get("detail")))
expect("D1: one failure -> no alert", a["problems"] == ["imap-failed"] and a["alert_kind"] is None)
run = execute("imaphealth0001bad")
log = sink_log()
subject, body = sink_message(log[-1]) if len(log) == before + 1 else ("", "")
expect("D2: second failure -> alert with the stage and the pre-flight's own reason",
       subject == "[Nova Scout] IMAP health: the mailbox is not reachable over IMAP"
       and "failed at connect" in body and "does not resolve" in body, subject)

# --- E. the checker itself is gone ------------------------------------------------------------
print("\n-- E. nothing answers at checker_url --")
fresh()
before = len(sink_log())
execute("imaphealth0001gone")
run = execute("imaphealth0001gone")
a = out1(run, "Assess Health")
log = sink_log()
subject, body = sink_message(log[-1]) if len(log) == before + 1 else ("", "")
expect("E: HTTP error item -> checker-unreachable, and after two checks an alert that says so",
       a["problems"] == ["checker-unreachable"] and subject == "[Nova Scout] IMAP health: the health checker is not answering"
       and "docker compose logs imap-health" in body, subject)

# --- the real database, untouched ------------------------------------------------------------
stop_sink()
real_after = fingerprint(REAL)
print("\n  real novascout AFTER:  %s" % json.dumps(real_after, sort_keys=True))
expect("the real novascout database is unchanged", real_after == real_before)
if not KEEP:
    sh(["docker", "rm", "-f", BAD_CHECKER], check=False)
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);" % (DRY, BASE))
    gone_ids = psql("n8n", "DELETE FROM workflow_entity WHERE id IN ('imaphealth0001dry', 'imaphealth0001bad', "
                           "'imaphealth0001gone') AND name ~ '^DRY RUN - ' RETURNING id;").split()
    print("  removed: throwaway checker, scratch databases, dry-run workflows (%s)" % ", ".join(gone_ids))

failed = [r for r in RESULTS if not r[1]]
print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
for label, _, detail in failed:
    print("  FAILED: " + label + ("  -- " + detail if detail else ""))
sys.exit(1 if failed else 0)

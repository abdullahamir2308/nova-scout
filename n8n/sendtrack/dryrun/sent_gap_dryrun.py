"""The Sent-mirror gap, end to end through n8n, on a copy of the database (2026-10-28).

    python n8n/sendtrack/dryrun/sent_gap_dryrun.py [--keep]

The scenario of 2026-10-28 07:40 UTC, replayed: Send sent a message, the Sent
trigger never saw it, Send failed closed sent-mirror-behind, IMAP Health stayed
green. Here the real workflows meet that state and must (A) alert at once,
(B) heal it by the reconcile, (C) write nothing on a second reconcile, (D) let
Send go again and report the recovery, (E) never mark a send the folder does
not hold, (F) write nothing when the checker fails, and (G) catch the mirror
claiming a message the folder lost -- on the second check.

REAL: the IMAP Health workflow and Mailbox Watch's Sent reconcile, as dry-run
variants built by build_workflow.py from the shipped node lists (the build
proves they differ only in credentials, the schedule/IMAP triggers removed and
a Manual Trigger added); n8n itself; every Postgres statement; migration 024;
the Sent listing -- the REAL Sent folder of the last 48 hours, read once,
read-only (imap_preflight.py --ids-json) and shaped by imap_health_server.py's
own parse_result(); Send's decision, by the shipped Load Send State and Decide
Send (status_now.py's method).

NOT REAL: the database is novascout_dryrun, a copy of novascout (contacts
rewritten to .dryrun.invalid, the operator's address to operator@dryrun.invalid)
with migration 024 applied to the COPY ONLY; the checker is checker_stub.js in
the n8n container, serving the real listing plus the simulated messages -- the
one Config value changed, asserted; SMTP goes to smtp_sink.js, which delivers
nothing. The real novascout database is only read; its fingerprint is compared
before and after. Afterwards the scratch database, the stub, the sink and the
dry-run workflows are removed; --keep leaves them.

Every expectation is asserted. Exit 1 if any did not hold.
"""
import copy
import email.policy
import email.utils
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SENDTRACK = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
sys.path.insert(0, SENDTRACK)
import imap_health_server  # noqa: E402 -- parse_result(), the checker's own shaping

N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
REAL, DRY = "novascout", "novascout_dryrun"
MIGRATION = os.path.join(REPO, "postgres", "migrations", "024_sent_mirror_health.sql")
SINK_DIR, SINK_JS = "/tmp/novascout_gap_sink", "/tmp/novascout_gap_smtp_sink.js"
STUB_JS, ANSWER = "/tmp/novascout_checker_stub.js", "/tmp/novascout_checker_answer.json"
STUB_URL = "http://127.0.0.1:8799/check"
OPERATOR = "operator@dryrun.invalid"
HEALTH_ID, REC_ID = "imaphealth0001gap", "mailwatch0001gap"
GAP = "<dryrun-gap-0740@amitrixlabs.com>"
MANUAL = "<dryrun-manual-send@amitrixlabs.com>"
UNFILED = "<dryrun-never-filed@amitrixlabs.com>"
SCRATCH = tempfile.mkdtemp(prefix="novascout-gap-dryrun-")
KEEP = "--keep" in sys.argv
RESULTS = []


def expect(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail else ""))


def sh(args, stdin=None, check=True):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                       errors="replace")
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
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent),
      'mailbox_sent_unseen', (SELECT count(*) FROM mailbox_sent WHERE NOT seen_in_sent_folder),
      'mailbox_sync', (SELECT json_object_agg(folder, last_synced_at) FROM mailbox_sync),
      'mailbox_health', (SELECT json_agg(t) FROM mailbox_health t),
      'outreach_log', (SELECT count(*) FROM outreach_log),
      'health_problems_check', (SELECT pg_get_constraintdef(oid) FROM pg_constraint
                                 WHERE conname = 'mailbox_health_problems_check'));""").strip())


def make_dry():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (DRY, DRY))
    sh(["docker", "exec", PG, "sh", "-c",
        "pg_dump -U %s -d %s --no-owner | psql -U %s -d %s -q -X -v ON_ERROR_STOP=1 >/dev/null" % (PGUSER, REAL, PGUSER, DRY)])
    psql(DRY, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
              "WHERE email IS NOT NULL AND email <> '';\n"
              "DELETE FROM settings WHERE key = 'operator_email';\n"
              "INSERT INTO settings (key, value) VALUES ('operator_email', '%s');\n"
              "DELETE FROM mailbox_health;" % OPERATOR)
    with io.open(MIGRATION, encoding="utf-8") as fh:
        psql(DRY, fh.read())


def build():
    env = dict(os.environ, SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"),
               SENDTRACK_DRYRUN_NOW="2026-10-28T10:00:00.000Z", PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("build failed:\n" + p.stderr[-2000:])
    load = lambda f: json.load(io.open(os.path.join(SCRATCH, "variants", f), encoding="utf-8"))
    return load("imaphealth-dryrun.json"), load("mailwatch-reconcile-dryrun.json"), \
        json.load(io.open(os.path.join(SCRATCH, "shipped", "send.json"), encoding="utf-8"))


def with_stub(wf, wid, name):
    """The variant with ONLY Config.checker_url changed (to the stub) -- proven here."""
    v = copy.deepcopy(wf)
    v["id"], v["name"] = wid, name
    for n in v["nodes"]:
        if n["name"] == "Config":
            for a in n["parameters"]["assignments"]["assignments"]:
                if a["name"] == "checker_url":
                    a["value"] = STUB_URL

    def blank(w):
        w = copy.deepcopy(w)
        for n in w["nodes"]:
            if n["name"] == "Config":
                for a in n["parameters"]["assignments"]["assignments"]:
                    if a["name"] == "checker_url":
                        a["value"] = None
        return (w["nodes"], w["connections"])
    assert blank(v) == blank(wf), "%s differs from its dry-run variant in more than checker_url" % wid
    return v


def import_wf(wf):
    tmp = "/tmp/ns_gap_%s.json" % wf["id"]
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % tmp], stdin=json.dumps(wf))
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=" + tmp])
    sh(["docker", "exec", N8N, "rm", "-f", tmp])


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


def start_helpers():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_gap_smtp_sink'; pkill -f '[n]ovascout_checker_stub'; "
                                           "exit 0"])
    sh(["docker", "exec", N8N, "rm", "-rf", SINK_DIR, ANSWER + ".up"])
    for local, remote in (("smtp_sink.js", SINK_JS), ("checker_stub.js", STUB_JS)):
        with io.open(os.path.join(HERE, local), encoding="utf-8") as fh:
            sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % remote], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "node", SINK_JS, SINK_DIR, "2525"])
    sh(["docker", "exec", "-d", N8N, "node", STUB_JS, ANSWER, "8799"])
    for _ in range(50):
        up = sh(["docker", "exec", N8N, "sh", "-c", "test -d %s && test -f %s.up && echo up; exit 0" % (SINK_DIR, ANSWER)])
        if up.strip() == "up":
            return
        subprocess.run(["docker", "exec", N8N, "sleep", "0.2"])
    raise SystemExit("the SMTP sink or the checker stub did not start")


def stop_helpers():
    # pkill in a shell of its own: a shell whose command line also names the
    # files matches the pattern and kills itself (exit 143, seen 2026-10-28).
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_gap_smtp_sink'; pkill -f '[n]ovascout_checker_stub'; "
                                           "exit 0"])
    sh(["docker", "exec", N8N, "rm", "-rf", SINK_DIR, ANSWER, ANSWER + ".up", SINK_JS, STUB_JS])


def sink_subjects():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    subjects = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        raw = subprocess.run(["docker", "exec", N8N, "cat", json.loads(ln)["file"]], stdout=subprocess.PIPE).stdout
        # A long Subject is folded over several lines; the parser unfolds it.
        subjects.append(str(email.message_from_bytes(raw, policy=email.policy.default)["Subject"] or ""))
    return subjects


def answer(obj):
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % ANSWER], stdin=json.dumps(obj))


def real_listing():
    """The real Sent folder (and INBOX) of the last 48 hours, read-only, shaped as the checker answers."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "imap_preflight.py"), "--ids-json", path,
                        "--ids-hours", "48"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                       errors="replace")
    ids = json.load(io.open(path, encoding="utf-8")) if os.path.isfile(path) else None
    if os.path.isfile(path):
        os.unlink(path)
    res = imap_health_server.parse_result(p.returncode, p.stdout, p.stderr, ids)
    if not res["ok"]:
        raise SystemExit("the real read-only listing failed: %s %s" % (res["stage"], res["detail"]))
    res.update(checked_at=datetime.now(timezone.utc).isoformat(), cached=False, duration_ms=0)
    return res


def send_decision():
    """Send's decision on the scratch database: the shipped Load Send State + Decide Send."""
    nodes = {n["name"]: n for n in SEND["nodes"]}
    cfg = {a["name"]: a["value"] for a in nodes["Config"]["parameters"]["assignments"]["assignments"]}
    sql = (nodes["Load Send State"]["parameters"]["query"].replace("$1", "''").replace("$2", str(int(cfg["min_gap_min"])))
           .replace("$3", repr(float(cfg["send_probability"]))).replace("$4", str(int(cfg["brake_days"]))))
    state = json.loads(psql(DRY, "SET default_transaction_read_only = on;\nSELECT row_to_json(t) FROM (" +
                            sql.rstrip().rstrip(";") + ") t;").strip())
    code = nodes["Decide Send"]["parameters"]["jsCode"]
    js = code[:code.index("// Node body")] + "\nconst state = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(decide(state, () => 0)));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8", dir=SCRATCH) as tf:
        tf.write(js)
    try:
        d = json.loads(sh(["node", tf.name], stdin=json.dumps(state)))
    finally:
        os.unlink(tf.name)
    return d["reason"]


def at(minutes_ago):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t, t.isoformat(), email.utils.format_datetime(t)


def health():
    run = execute(HEALTH_ID)
    a = out1(run, "Assess Health")
    return a, psql_json(DRY, "SELECT healthy, problems, consecutive_failures FROM mailbox_health")[0]


def reconcile():
    run = execute(REC_ID)
    return out1(run, "Plan Sent Reconcile"), out1(run, "Reconcile Sent")


def seen(mid):
    rows = psql_json(DRY, "SELECT source, seen_in_sent_folder FROM mailbox_sent WHERE message_id = '%s'" % mid)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------------

print("Sent-mirror gap dry run -- scratch dir %s\n" % SCRATCH)
real_before = fingerprint(REAL)
print("  real novascout BEFORE: %s" % json.dumps(real_before, sort_keys=True))
dry_health, dry_rec, SEND = build()
gap_health = with_stub(dry_health, HEALTH_ID, "DRY RUN - IMAP Health (checker stub, scratch DB, SMTP sink)")
gap_rec = with_stub(dry_rec, REC_ID, "DRY RUN - Mailbox Watch Sent reconcile (checker stub, scratch DB)")
try:
    make_dry()
    start_helpers()
    for wf in (gap_health, gap_rec):
        import_wf(wf)
    base = real_listing()
    print("  real Sent folder, last 48 h (read-only): %d message(s)" % len(base["sent"]))

    _, gap_iso, gap_hdr = at(40)
    _, man_iso, man_hdr = at(30)
    psql(DRY, "INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) "
              "VALUES ('%s', '%s', '{bd@prospect.dryrun.invalid}', true, 'workflow', false);" % (GAP, gap_iso))
    folder = copy.deepcopy(base)
    folder["sent"] += [
        {"message_id": GAP, "at": at(39)[1], "date": gap_hdr, "to": "bd@prospect.dryrun.invalid", "cc": "", "bcc": ""},
        {"message_id": MANUAL, "at": at(29)[1], "date": man_hdr, "to": "Ana <ana@prospect.dryrun.invalid>", "cc": "",
         "bcc": ""}]
    answer(folder)

    print("\n-- A. the gap: Send's own send is in the folder, the mirror never saw it --")
    expect("A: Send refuses everything: sent-mirror-behind", send_decision() == "sent-mirror-behind")
    a, h = health()
    expect("A: IMAP Health is NOT green: sent-missed + send-blocked", a["problems"] == ["sent-missed", "send-blocked"],
           str(a["problems"]))
    expect("A: send-blocked alerts on the FIRST check", a["confirmed"] and a["alert_kind"] == "problem")
    subj = sink_subjects()
    expect("A: the operator got one alert saying Send is blocked",
           len(subj) == 1 and "Send is blocked" in subj[0], str(subj))

    print("\n-- B. the reconcile heals it --")
    plan, rec = reconcile()
    expect("B: the listing is complete", plan["listed"] is True)
    expect("B: healed exactly the gap send, inserted the manual send",
           rec and rec["healed"] == 1 and rec["healed_ids"] == [GAP] and rec["new_rows"] == 1, json.dumps(rec)[:300])
    expect("B: the gap send is seen, still Send's own row", seen(GAP) == {"source": "workflow", "seen_in_sent_folder": True},
           str(seen(GAP)))
    expect("B: the manual send is in the mirror as the trigger would write it",
           seen(MANUAL) == {"source": "sent-folder", "seen_in_sent_folder": True}, str(seen(MANUAL)))

    print("\n-- C. a second reconcile writes nothing --")
    before = psql_json(DRY, "SELECT message_id, seen_in_sent_folder, recorded_at FROM mailbox_sent ORDER BY message_id")
    _, rec = reconcile()
    after = psql_json(DRY, "SELECT message_id, seen_in_sent_folder, recorded_at FROM mailbox_sent ORDER BY message_id")
    expect("C: healed 0, new 0", rec["healed"] == 0 and rec["new_rows"] == 0, json.dumps(rec)[:200])
    expect("C: the mirror is row for row unchanged (never double-record)", before == after)

    print("\n-- D. Send goes again; IMAP Health reports the recovery --")
    reason = send_decision()
    expect("D: Send is no longer blocked by the mirror", reason != "sent-mirror-behind", reason)
    a, h = health()
    expect("D: IMAP Health healthy, one all-clear", h["healthy"] is True and a["alert_kind"] == "recovered", str(a["problems"]))
    expect("D: the all-clear reached the operator", sink_subjects()[-1:] == ["[Nova Scout] IMAP health: OK again"],
           str(sink_subjects()))

    print("\n-- E. a send the folder does not hold is never marked seen --")
    psql(DRY, "INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) "
              "VALUES ('%s', '%s', '{x@prospect.dryrun.invalid}', true, 'workflow', false);" % (UNFILED, gap_iso))
    _, rec = reconcile()
    expect("E: the reconcile leaves it unseen", seen(UNFILED)["seen_in_sent_folder"] is False and rec["healed"] == 0)
    expect("E: so Send stays stopped, as it must", send_decision() == "sent-mirror-behind")
    a, h = health()
    expect("E: and IMAP Health alerts send-blocked at once", a["problems"] == ["send-blocked"] and a["alert_kind"] == "problem",
           str([a["problems"], a["alert_kind"]]))
    psql(DRY, "DELETE FROM mailbox_sent WHERE message_id = '%s';" % UNFILED)

    print("\n-- F. the checker fails: nothing is reconciled --")
    psql(DRY, "UPDATE mailbox_sent SET seen_in_sent_folder = false WHERE message_id = '%s';" % GAP)
    stamp = psql(DRY, "SELECT last_synced_at FROM mailbox_sync WHERE folder = 'Sent-reconcile';").strip()
    answer({"ok": False, "status": "failed", "stage": "login", "detail": "server refused", "hint": None, "note": None,
            "exit_code": 1, "inbox": [], "sent": folder["sent"], "hours": 48, "since": None,
            "inbox_no_message_id": 0, "sent_no_message_id": 0, "sent_undated": 0})
    plan, rec = reconcile()
    expect("F: not listed, Reconcile Sent never ran", plan["listed"] is False and rec is None, str(rec))
    expect("F: the gap send stays unseen and the reconcile stamp does not move",
           seen(GAP)["seen_in_sent_folder"] is False and
           psql(DRY, "SELECT last_synced_at FROM mailbox_sync WHERE folder = 'Sent-reconcile';").strip() == stamp)
    answer(folder)
    reconcile()

    print("\n-- G. the mirror claims a message the folder no longer holds --")
    a, h = health()
    expect("G: back to healthy first", h["healthy"] is True, str(a["problems"]))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
    lost = [s for s in folder["sent"] if s["message_id"] not in (GAP, MANUAL)
            and datetime.fromisoformat(s["at"]) < cutoff
            and datetime.fromisoformat(s["at"]) > datetime.now(timezone.utc) - timedelta(hours=40)]
    if not lost:
        expect("G: a real message from the last 40 hours to drop from the stub folder", False, "none found")
    else:
        mid = lost[-1]["message_id"]
        lessened = copy.deepcopy(folder)
        lessened["sent"] = [s for s in lessened["sent"] if s["message_id"] != mid]
        answer(lessened)
        a1, _ = health()
        a2, _ = health()
        expect("G: first check: sent-mismatch, not yet alerted", a1["problems"] == ["sent-mismatch"] and a1["alert_kind"] is None,
               str([a1["problems"], a1["alert_kind"]]))
        expect("G: second check: alerted, naming the message", a2["alert_kind"] == "problem" and mid in a2["text"],
               str(a2["alert_kind"]))
finally:
    if KEEP:
        print("\n  --keep: left the scratch database %s, the stub and sink, and workflows %s, %s" % (DRY, HEALTH_ID, REC_ID))
    else:
        stop_helpers()
        psql("n8n", "DELETE FROM workflow_entity WHERE id IN ('%s', '%s');" % (HEALTH_ID, REC_ID))
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % DRY)
        print("\n  removed: scratch database, checker stub, SMTP sink, dry-run workflows")

real_after = fingerprint(REAL)
expect("the real novascout database is unchanged", real_after == real_before,
       "" if real_after == real_before else json.dumps(real_after, sort_keys=True))
failed = [r for r in RESULTS if not r[1]]
print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)

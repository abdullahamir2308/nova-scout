"""Workflow 6 dry run -- the real send path, end to end, with nothing able to reach a real inbox.

    python n8n/sendtrack/dryrun/dryrun.py [--keep]

REAL: the workflows (built by build_workflow.py from the same node lists as the
shipped ones; the build proves only credentials, Config values and the schedule
trigger differ), n8n itself, every Postgres statement, nodemailer's SMTP
conversation.

NOT REAL: the database is novascout_dryrun, a copy of novascout taken at the
start with every contact address rewritten to <local>.at.<domain>@dryrun.invalid
(the .invalid TLD cannot resolve); and SMTP goes to smtp_sink.js on
127.0.0.1:2525 INSIDE the n8n container, which writes to disk and has no route
anywhere. The real novascout database is only READ (pg_dump); a fingerprint of
it is printed before and after.

Every expectation is asserted. Exit 1 if any guard did not hold.
"""
import csv
import email
import email.policy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SENDTRACK = os.path.dirname(HERE)
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
REAL, BASE, DRY = "novascout", "novascout_dryrun_base", "novascout_dryrun"
SINK_DIR, SINK_JS = "/tmp/novascout_sink", "/tmp/novascout_smtp_sink.js"
SCRATCH = tempfile.mkdtemp(prefix="novascout-dryrun-")
KEEP = "--keep" in sys.argv

# Monday 2026-09-14 10:00 UTC = 15:00 PKT, sender-day 2026-09-14.
# Recipients: Argentina 07:00 (closed), India 15:30, Poland 11:00, Turkey 13:00 (open).
CLOCK = "2026-09-14T10:00:00.000Z"
DAY_START, DAY_END = "2026-09-13T19:00:00.000Z", "2026-09-14T19:00:00.000Z"
OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up."

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
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1",
               "-At"], stdin=sql)


def psql_json(db, sql):
    out = psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql + ") t;").strip()
    return json.loads(out)


def psql_csv(db, sql):
    out = sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1",
              "--csv"], stdin=sql)
    return list(csv.DictReader(io.StringIO(out)))


def fingerprint(db):
    return json.loads(psql(db, """SELECT json_build_object(
      'drafts', (SELECT json_object_agg(k, n ORDER BY k) FROM (SELECT channel || ':' || status AS k, count(*) AS n FROM drafts GROUP BY 1) s),
      'leads', (SELECT json_object_agg(status, n ORDER BY status) FROM (SELECT status, count(*) AS n FROM leads GROUP BY 1) s),
      'outreach_log', (SELECT count(*) FROM outreach_log),
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent),
      'inbound_messages', (SELECT count(*) FROM inbound_messages),
      'blocklist', (SELECT count(*) FROM blocklist),
      'max_draft_id', (SELECT max(id) FROM drafts));""").strip())


# --- scratch database ---------------------------------------------------------

def make_base():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                     "CREATE DATABASE %s;" % (DRY, BASE, BASE))
    sh(["docker", "exec", PG, "sh", "-c",
        "pg_dump -U %s -d %s --no-owner | psql -U %s -d %s -q -X -v ON_ERROR_STOP=1 >/dev/null" % (PGUSER, REAL, PGUSER, BASE)])
    psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
               "WHERE email IS NOT NULL AND email <> '';")
    left = int(psql(BASE, "SELECT count(*) FROM contacts WHERE email IS NOT NULL AND email <> '' "
                          "AND email NOT LIKE '%@dryrun.invalid';").strip())
    return left


def fresh(seed_sql=""):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s TEMPLATE %s;" % (DRY, DRY, BASE))
    if seed_sql:
        psql(DRY, seed_sql)


# --- n8n ------------------------------------------------------------------------

def build(fixtures=None, operator=None):
    env = dict(os.environ, SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"), SENDTRACK_DRYRUN_NOW=CLOCK,
               PYTHONIOENCODING="utf-8")
    if fixtures:
        path = os.path.join(SCRATCH, "fixtures.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(fixtures, fh, ensure_ascii=False)
        env["SENDTRACK_FIXTURES"] = path
    if operator:
        env["NOVASCOUT_OPERATOR_EMAIL"] = operator
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("build failed:\n" + p.stderr[-2000:])


def import_wf(name):
    with io.open(os.path.join(SCRATCH, "variants", name), encoding="utf-8") as fh:
        blob = fh.read()
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_wf.json"], stdin=blob)
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_wf.json"])
    sh(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_wf.json"])
    return json.loads(blob)


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


def out_items(run, node, output=0):
    rd = run["data"]["resultData"]["runData"]
    if node not in rd:
        return None
    main = rd[node][0]["data"]["main"]
    return [i["json"] for i in (main[output] if len(main) > output and main[output] else [])]


# --- the sink -------------------------------------------------------------------

def start_sink():
    # "[n]ovascout..." matches the sink's command line but not this shell's own.
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; rm -rf %s; exit 0" % SINK_DIR])
    with io.open(os.path.join(HERE, "smtp_sink.js"), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % SINK_JS], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "node", SINK_JS, SINK_DIR, "2525"])
    for _ in range(50):
        if sh(["docker", "exec", N8N, "sh", "-c", "test -d %s && echo up; exit 0" % SINK_DIR]).strip() == "up":
            return
        time.sleep(0.2)
    raise SystemExit("the SMTP sink did not start")


def sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def sink_message(file):
    raw = subprocess.run(["docker", "exec", N8N, "cat", file], stdout=subprocess.PIPE).stdout
    return raw, email.message_from_bytes(raw, policy=email.policy.default)


def stop_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; exit 0"])


def norm(text):
    """Decide Send's normaliseBody, for comparing what was checked with what arrived."""
    return re.sub(r"[ \t]+\n", "\n", str(text).replace("\r\n", "\n").replace("\r", "\n")).strip()


# --- helpers --------------------------------------------------------------------

def claim_sql(send_wf):
    q = [n for n in send_wf["nodes"] if n["name"] == "Claim Send"][0]["parameters"]["query"]
    assert q.count("$1") == 1, "Claim Send SQL should take exactly one parameter"
    return q


def direct_claim(sql, draft_id):
    """Run the shipped Claim Send statement straight at the scratch DB, with a
    payload that would be valid if the draft were sendable -- the proof that
    the database statement itself refuses, whatever Decide Send said."""
    payload = psql(DRY, """SELECT json_build_object('draft_id', d.id, 'lead_id', d.lead_id, 'to_addr', c.email,
        'subject', coalesce(d.subject, ''), 'body', coalesce(d.edited_body, d.body),
        'raw_body', coalesce(d.edited_body, d.body), 'clock', '%s', 'day_start', '%s', 'day_end', '%s',
        'ceiling', 5) FROM drafts d JOIN contacts c ON c.lead_id = d.lead_id WHERE d.id = %d;"""
                   % (CLOCK, DAY_START, DAY_END, draft_id)).strip()
    rows = psql_csv(DRY, sql.replace("$1::jsonb", "$ns$" + payload + "$ns$::jsonb"))
    return rows[0]


def drafts(ids):
    return {r["id"]: (r["channel"], r["status"], r["variant"]) for r in psql_json(
        DRY, "SELECT id, channel, status, variant FROM drafts WHERE id IN (%s) ORDER BY id" % ",".join(map(str, ids)))}


def show(title, obj):
    print("  %s: %s" % (title, json.dumps(obj, ensure_ascii=False)))


def decision_summary(d):
    return {"send": d["send"], "reason": d["reason"], "detail": d["detail"],
            "warmup": {k: d["warmup"][k] for k in ("history", "first_send_day", "week", "ceiling", "sent_today", "remaining")},
            "eligible": [(e["draft_id"], e["country"], e["local_time"], e["in_window"]) for e in d["eligible"]],
            "excluded": [(x["draft_id"], x["reason"]) for x in d["excluded"]]}


MIRROR_ALIVE = "INSERT INTO mailbox_sync (folder, last_synced_at, messages_seen) VALUES ('Sent', '2026-09-14T09:55:00Z', 6);\n"


def manual_sends(hours):
    return "".join(
        "INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) "
        "VALUES ('<manual-%d@amitrixlabs.com>', '2026-09-14T%02d:00:00Z', ARRAY['warmup-friend%d@example.org'], "
        "true, 'sent-folder', true);\n" % (i, h, i) for i, h in enumerate(hours, 1))


OWN_DOMAIN_NOTES = (
    "INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, source, seen_in_sent_folder) VALUES "
    "('<self-1@amitrixlabs.com>', '2026-09-14T08:00:00Z', ARRAY['abdullah@amitrixlabs.com'], false, 'sent-folder', true), "
    "('<self-2@amitrixlabs.com>', '2026-09-14T08:30:00Z', ARRAY['ops@amitrixlabs.com'], false, 'sent-folder', true);\n")
APPROVE_LINKEDIN = "UPDATE drafts SET status = 'approved' WHERE channel = 'linkedin' AND variant NOT LIKE 'low-context%';\n"


# =============================================================================

print("Workflow 6 dry run -- scratch DB %s, SMTP sink on 127.0.0.1:2525 inside %s" % (DRY, N8N))
print("scenario clock: %s (Mon 15:00 PKT; Argentina 07:00, India 15:30, Poland 11:00, Turkey 13:00)\n" % CLOCK)

real_before = fingerprint(REAL)
show("real novascout BEFORE", real_before)
left = make_base()
expect("every contact address in the scratch copy is rewritten to @dryrun.invalid", left == 0, "%d left" % left)
show("scratch contacts (sample)", psql_json(BASE, "SELECT lead_id, email FROM contacts WHERE lead_id IN (7, 50, 91, 104) ORDER BY lead_id"))
creds = psql("n8n", "SELECT string_agg(id || '=' || type, ', ' ORDER BY id) FROM credentials_entity "
                    "WHERE id IN ('novascoutPgDry01', 'novascoutSmtpDry01');").strip()
if "novascoutPgDry01" not in creds or "novascoutSmtpDry01" not in creds:
    raise SystemExit("dry-run credentials missing (%r) -- run provision_credentials.py --dry-run-only" % creds)

build()
send_wf = import_wf("send-dryrun.json")
fu_wf = import_wf("followups-dryrun.json")
smtp_nodes = [n for n in send_wf["nodes"] if n["type"] == "n8n-nodes-base.emailSend"]
expect("the dry-run Send workflow's only SMTP node uses the SINK credential",
       [n["credentials"]["smtp"]["id"] for n in smtp_nodes] == ["novascoutSmtpDry01"])
expect("... and every Postgres node the scratch DB",
       {n["credentials"]["postgres"]["id"] for n in send_wf["nodes"] if n["type"] == "n8n-nodes-base.postgres"} == {"novascoutPgDry01"})
CLAIM = claim_sql(send_wf)
start_sink()
print()

# --- A. the full send path, and the warm-up ceiling -------------------------------
print("=== A. Week-1 day, 4 external sends already today: the 5th goes out, the 6th does not ===")
fresh(MIRROR_ALIVE + manual_sends([4, 5, 6, 7]) + OWN_DOMAIN_NOTES + APPROVE_LINKEDIN)
print("  seeded: Sent mirror alive; 4 manual external sends today 09:00-12:00 PKT (first ever = today);")
print("          2 same-domain notes today (not external); LinkedIn drafts 32/34/36/38 APPROVED")
run = execute("send0001dry")
state = out_items(run, "Load Send State")[0]
show("Load Send State", {k: state[k] for k in ("clock", "sent_mirror_synced_at", "first_external_send_at")} |
     {"recent_sends": len(state["recent_sends"]), "candidates": [c["draft_id"] for c in state["candidates"]],
      "drafts_by_status": state["drafts_by_status"]})
d = out_items(run, "Decide Send")[0]
show("Decide Send", decision_summary(d))
claim = out_items(run, "Claim Send")[0]
info = out_items(run, "Send Email")[0]
conf = out_items(run, "Confirm Send")[0]
show("Claim Send", claim | {"body": "(%d chars)" % len(claim["body"])})
show("Send Email -> nodemailer info", {k: info.get(k) for k in ("accepted", "rejected", "messageId", "response")})
show("Confirm Send", conf)
log = sink_log()
expect("warm-up derived from the mirrored Sent history: week 1, ceiling 5, 4 sent today, 1 remaining",
       (d["warmup"]["week"], d["warmup"]["ceiling"], d["warmup"]["sent_today"], d["warmup"]["remaining"]) == (1, 5, 4, 1))
expect("same-domain notes did not count toward the ceiling", d["warmup"]["sent_today"] == 4)
expect("the send path never selected a LinkedIn draft, though 4 were approved",
       state["drafts_by_status"].get("linkedin:approved") == 4 and
       all(c["channel"] == "email" for c in state["candidates"]))
expect("the 5th send was allowed and went through claim -> SMTP -> confirm",
       d["send"] and claim["claimed"] is True and conf["confirmed"] == 1, "draft %s" % d["payload"]["draft_id"])
expect("exactly one message reached the sink", len(log) == 1)
raw, msg = sink_message(log[0]["file"])
body = norm(msg.get_body(preferencelist=("plain",)).get_content())
row = psql_json(DRY, "SELECT o.id, o.draft_id, o.message_id, o.sent_at, l.status AS lead_status, d.status AS draft_status, "
                     "coalesce(d.edited_body, d.body) AS sent_text, c.email FROM outreach_log o JOIN drafts d ON d.id = o.draft_id "
                     "JOIN leads l ON l.id = o.lead_id JOIN contacts c ON c.lead_id = o.lead_id")[0]
print("  captured message #%d -- MAIL FROM %s  RCPT TO %s  AUTH %s" % (log[0]["seq"], log[0]["mail_from"], log[0]["rcpt_to"], log[0]["auth"]))
for h in ("From", "To", "Subject", "Message-ID", "MIME-Version", "Content-Type", "Content-Transfer-Encoding"):
    print("    %-26s %s" % (h + ":", msg.get(h)))
print("    --- body (decoded) ---")
for ln in body.rstrip("\n").split("\n"):
    print("    | " + ln)
expect("From is the configured sender", msg.get("From") == '"Abdullah Amir" <abdullah@amitrixlabs.com>' or
       msg.get("From") == "Abdullah Amir <abdullah@amitrixlabs.com>", msg.get("From"))
expect("recipient is the scratch (.invalid) address, never a real one", msg.get("To").endswith("@dryrun.invalid"), msg.get("To"))
expect("plain text only: one text/plain part, no HTML, no multipart",
       msg.get_content_type() == "text/plain" and not msg.is_multipart() and b"text/html" not in raw)
expect("no n8n attribution and no tracking anywhere in the raw message",
       b"n8n" not in raw.lower() and b"<img" not in raw.lower() and b"http" not in raw.lower())
expect("the body sent is exactly the approved (edited) draft text", body == norm(row["sent_text"]))
expect("the opt-out line and signature are in what was sent", OPT_OUT + "\n\nAbdullah Amir\nFounder, Amitrix Labs" in body)
expect("outreach_log carries the same Message-ID the sink received",
       row["message_id"] == msg.get("Message-ID"), row["message_id"])
expect("draft -> sent, lead -> sent", (row["draft_status"], row["lead_status"]) == ("sent", "sent"))
ms = psql_json(DRY, "SELECT message_id, source, external, seen_in_sent_folder, lead_id FROM mailbox_sent WHERE source = 'workflow'")
show("mailbox_sent (workflow row)", ms)
expect("the send counts at once: a workflow row in mailbox_sent, not yet seen by the mirror",
       len(ms) == 1 and ms[0]["external"] and not ms[0]["seen_in_sent_folder"])

print("  -- second tick, same day, same clock --")
run = execute("send0001dry")
d2 = out_items(run, "Decide Send")[0]
show("Decide Send", decision_summary(d2))
expect("the 6th send is REFUSED by the ceiling (5/5), with recipients still eligible and at work",
       not d2["send"] and d2["reason"] == "ceiling-reached" and d2["warmup"]["sent_today"] == 5
       and any(e["in_window"] for e in d2["eligible"]), d2["detail"])
expect("Claim Send never ran on the refused tick", out_items(run, "Claim Send") is None)
expect("still exactly one message in the sink", len(sink_log()) == 1)
c = direct_claim(CLAIM, 35)
show("direct Claim Send on draft 35 at 5/5", c)
expect("the Claim statement itself refuses at the ceiling, even bypassing Decide",
       c["claimed"] == "f" and c["sent_today_before"] == "5")
show("drafts after A", drafts([32, 33, 34, 35, 36, 37, 38]))
expect("LinkedIn drafts untouched: still approved, never logged",
       all(v[1] == "approved" for k, v in drafts([32, 34, 36, 38]).items()) and
       psql(DRY, "SELECT count(*) FROM outreach_log WHERE channel <> 'email';").strip() == "0")
print()

# --- B. LinkedIn -------------------------------------------------------------------
print("=== B. Only LinkedIn drafts approved: nothing is selected, nothing is sent ===")
fresh(MIRROR_ALIVE + APPROVE_LINKEDIN + "UPDATE drafts SET status = 'pending' WHERE channel = 'email' AND status = 'approved';\n")
print("  seeded: Sent mirror alive, no sends today; every email draft PENDING; LinkedIn 32/34/36/38 APPROVED")
before = len(sink_log())
run = execute("send0001dry")
state = out_items(run, "Load Send State")[0]
d = out_items(run, "Decide Send")[0]
show("Load Send State", {"candidates": state["candidates"], "drafts_by_status": state["drafts_by_status"]})
show("Decide Send", decision_summary(d))
expect("4 approved LinkedIn drafts exist, and the send query returned zero candidates",
       state["drafts_by_status"].get("linkedin:approved") == 4 and state["candidates"] == [])
expect("no send", not d["send"] and d["reason"] == "no-eligible-draft" and len(sink_log()) == before)
c = direct_claim(CLAIM, 34)
show("direct Claim Send on LinkedIn draft 34 (approved, verified contact, in window, under the ceiling)", c)
expect("the Claim statement refuses a LinkedIn draft even when handed one directly", c["claimed"] == "f")
expect("LinkedIn drafts still approved, zero outreach rows", all(v[1] == "approved" for v in drafts([32, 34, 36, 38]).values())
       and psql(DRY, "SELECT count(*) FROM outreach_log;").strip() == "0")
print()

# --- C. blocklist ------------------------------------------------------------------
print("=== C. Blocklisted domain: skipped, and the next eligible draft goes instead ===")
fresh(MIRROR_ALIVE + "INSERT INTO blocklist (domain, reason) VALUES ('innovate-research.com', 'dry run: blocklist test');\n")
print("  seeded: Sent mirror alive, no sends; innovate-research.com (lead 50, draft 33 -- the oldest in-window draft) BLOCKLISTED")
before = len(sink_log())
run = execute("send0001dry")
d = out_items(run, "Decide Send")[0]
show("Decide Send", decision_summary(d))
log = sink_log()
expect("draft 33 excluded as blocklisted-domain", (33, "blocklisted-domain") in [(x["draft_id"], x["reason"]) for x in d["excluded"]])
expect("the tick sent the next eligible draft instead (35, bitrial.hu)", d["send"] and d["payload"]["draft_id"] == 35)
expect("the one captured message is to bitrial.hu's scratch address, not innovate-research's",
       len(log) == before + 1 and "bitrial.hu" in log[-1]["rcpt_to"][0] and "innovate" not in log[-1]["rcpt_to"][0],
       str(log[-1]["rcpt_to"]))
c = direct_claim(CLAIM, 33)
show("direct Claim Send on blocklisted draft 33", c)
expect("the Claim statement refuses the blocklisted domain on its own", c["claimed"] == "f")
expect("draft 33 still approved -- skipped, not consumed", drafts([33])[33][1] == "approved")
print()

# --- D. unapproved -----------------------------------------------------------------
print("=== D. Unapproved drafts are never eligible -- even older, in-window, with a verified contact ===")
fresh(MIRROR_ALIVE +
      "UPDATE drafts SET status = 'pending' WHERE id = 33;\n"
      "UPDATE drafts SET status = 'rejected', reject_reason = 'bad fit' WHERE id = 35;\n"
      "UPDATE drafts SET status = 'approved' WHERE id = 39;\n")
print("  seeded: 33 PENDING (India, open), 35 REJECTED (Poland, open), 37 approved (Turkey, open),")
print("          31 approved (Argentina, closed), 39 = the low-context note, APPROVED by mistake (India, open)")
before = len(sink_log())
run = execute("send0001dry")
state = out_items(run, "Load Send State")[0]
d = out_items(run, "Decide Send")[0]
show("Load Send State candidates", [c["draft_id"] for c in state["candidates"]])
show("Decide Send", decision_summary(d))
ids = [c["draft_id"] for c in state["candidates"]]
expect("the pending (33) and rejected (35) drafts are not even selected", 33 not in ids and 35 not in ids, str(ids))
expect("the approved low-context note (39) is refused as low-context",
       (39, "low-context") in [(x["draft_id"], x["reason"]) for x in d["excluded"]])
expect("the only draft sent is the approved one whose recipient is at work (37)",
       d["send"] and d["payload"]["draft_id"] == 37 and len(sink_log()) == before + 1)
c = direct_claim(CLAIM, 33)
show("direct Claim Send on pending draft 33", c)
expect("the Claim statement refuses an unapproved draft on its own", c["claimed"] == "f")
show("drafts after D", drafts([31, 33, 35, 37, 39]))
expect("33 still pending, 35 still rejected", (drafts([33])[33][1], drafts([35])[35][1]) == ("pending", "rejected"))
print()

# --- E. SMTP refusal -----------------------------------------------------------------
print("=== E. The server refuses the address: the claim is reverted, nothing is left 'sent' ===")
fresh(MIRROR_ALIVE + "UPDATE contacts SET email = 'reject.' || email WHERE lead_id = 50;\n")
print("  seeded: lead 50's scratch address starts with 'reject' -- the sink answers RCPT with 550 5.1.1")
before = len(sink_log())
run = execute("send0001dry")
chk = out_items(run, "Check SMTP Result")[0]
rev = out_items(run, "Revert Claim")[0]
show("Send Email -> item", out_items(run, "Send Email")[0])
show("Check SMTP Result", {k: chk[k] for k in ("ok", "failure")} | {"error": chk["payload"]["error"]})
show("Revert Claim", rev)
expect("classified as a recipient failure, reverted", chk["ok"] is False and chk["failure"] == "recipient"
       and rev["claim_removed"] == 1)
expect("draft 33 back to pending for a human, tagged +smtp-rejected; no outreach row; nothing captured",
       drafts([33])[33][1:] == ("pending", "unnamed+smtp-rejected")
       and psql(DRY, "SELECT count(*) FROM outreach_log;").strip() == "0" and len(sink_log()) == before,
       str(drafts([33])[33]))
print()

# --- F. follow-ups -------------------------------------------------------------------
print("=== F. Follow-ups: due after 6 days, drafted as pending, idempotent; lost after two ===")
fresh("""
UPDATE drafts SET status = 'sent' WHERE id IN (31, 35, 37);
UPDATE leads SET status = 'sent' WHERE id IN (7, 91, 104);
INSERT INTO outreach_log (lead_id, draft_id, channel, sent_at, message_body, message_id) VALUES
  (91, 35, 'email', '2026-09-07T10:00:00Z', (SELECT coalesce(edited_body, body) FROM drafts WHERE id = 35), '<seed-35@amitrixlabs.com>'),
  (7, 31, 'email', '2026-09-06T10:00:00Z', (SELECT coalesce(edited_body, body) FROM drafts WHERE id = 31), '<seed-31@amitrixlabs.com>'),
  (104, 37, 'email', '2026-08-25T10:00:00Z', (SELECT coalesce(edited_body, body) FROM drafts WHERE id = 37), '<seed-37@amitrixlabs.com>');
UPDATE outreach_log SET replied = true, replied_at = '2026-09-08T10:00:00Z', outcome = 'replied' WHERE lead_id = 7;
INSERT INTO drafts (lead_id, channel, variant, subject, body, status, created_at) VALUES
  (104, 'email', 'follow-up-1', 'Re: x', 'fu1', 'sent', '2026-08-31T10:00:00Z'),
  (104, 'email', 'follow-up-2', 'Re: x', 'fu2', 'sent', '2026-09-06T10:00:00Z');
INSERT INTO outreach_log (lead_id, channel, sent_at, message_body, message_id) VALUES
  (104, 'email', '2026-08-31T12:00:00Z', 'fu1', '<seed-fu1@amitrixlabs.com>'),
  (104, 'email', '2026-09-07T09:00:00Z', 'fu2', '<seed-fu2@amitrixlabs.com>');
""")
print("  seeded: lead 91 first touch 7 days ago, no reply; lead 7 sent 8 days ago but REPLIED;")
print("          lead 104: first touch + both follow-ups sent, last 7 days ago")
run = execute("followup0001dry")
show("Find Due Follow-Ups", [{k: r[k] for k in ("lead_id", "action", "next_follow_up")} for r in out_items(run, "Find Due Follow-Ups")])
show("Write Follow-Up", out_items(run, "Write Follow-Up"))
fu = psql_json(DRY, "SELECT lead_id, variant, status, subject, body FROM drafts WHERE variant LIKE 'follow-up-%' AND status = 'pending'")
expect("lead 91 got follow-up-1 as a PENDING draft (the review queue)", [(r["lead_id"], r["variant"]) for r in fu] == [(91, "follow-up-1")])
expect("lead 7 (replied) got nothing", not any(r["lead_id"] == 7 for r in fu))
expect("lead 104 (two follow-ups used) marked lost", psql(DRY, "SELECT status FROM leads WHERE id = 104;").strip() == "lost")
if fu:
    print("    follow-up subject: %s" % fu[0]["subject"])
    for ln in fu[0]["body"].split("\n")[:9]:
        print("    | " + ln)
    print("    | ...")
run = execute("followup0001dry")
show("second run: Find Due Follow-Ups", out_items(run, "Find Due Follow-Ups"))
expect("a second run writes nothing (idempotent): Write Follow-Up never runs, still one pending follow-up",
       out_items(run, "Write Follow-Up") is None and
       psql(DRY, "SELECT count(*) FROM drafts WHERE variant LIKE 'follow-up-%' AND status = 'pending';").strip() == "1")
print()

# --- G. mailbox watch ----------------------------------------------------------------
print("=== G. Mailbox Watch: Sent mirror, reply, opt-out, out-of-office, newsletter, duplicate ===")
fresh("""
UPDATE drafts SET status = 'sent' WHERE id IN (31, 35);
UPDATE leads SET status = 'sent' WHERE id IN (7, 91);
INSERT INTO outreach_log (lead_id, draft_id, channel, sent_at, message_body, message_id) VALUES
  (7, 31, 'email', '2026-09-13T15:00:00Z', 'first touch', '<seed-31@amitrixlabs.com>'),
  (91, 35, 'email', '2026-09-13T15:30:00Z', 'first touch', '<seed-35@amitrixlabs.com>');
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source) VALUES
  ('<seed-31@amitrixlabs.com>', '2026-09-13T15:00:00Z', ARRAY['x'], true, 7, 'workflow');
""")
addr = {r["lead_id"]: r["email"] for r in psql_json(DRY, "SELECT lead_id, email FROM contacts WHERE lead_id IN (7, 91, 104)")}
FROM = "Abdullah Amir <abdullah@amitrixlabs.com>"
QUOTE = ("\n\nOn Sun, 13 Sep 2026 at 20:00, Abdullah Amir <abdullah@amitrixlabs.com> wrote:\n"
         "> Hello,\n>\n> Nova is an assistant trained on your own SOPs.\n>\n> " + OPT_OUT + "\n>\n> Abdullah Amir")
fixtures = {
    "sent": [
        {"date": "Sun, 13 Sep 2026 20:00:00 +0500", "from": FROM, "to": addr[7], "subject": "question",
         "textPlain": "first touch", "textHtml": "", "metadata": {"message-id": "<seed-31@amitrixlabs.com>"}, "attributes": {"uid": 1}},
        {"date": "Mon, 14 Sep 2026 09:10:00 +0500", "from": FROM, "to": "Someone <" + addr[104] + ">", "subject": "hello",
         "textPlain": "manual note", "textHtml": "", "metadata": {"message-id": "<manual-104@amitrixlabs.com>"}, "attributes": {"uid": 2}},
        {"date": "Mon, 14 Sep 2026 09:20:00 +0500", "from": FROM, "to": "abdullah@amitrixlabs.com", "subject": "note to self",
         "textPlain": "x", "textHtml": "", "metadata": {"message-id": "<self-9@amitrixlabs.com>"}, "attributes": {"uid": 3}},
        {"date": "", "from": FROM, "to": "friend@example.org", "subject": "undated",
         "textPlain": "x", "textHtml": "", "metadata": {"message-id": "<undated@amitrixlabs.com>"}, "attributes": {"uid": 4}},
    ],
    "inbox": [
        {"date": "Mon, 14 Sep 2026 09:00:00 -0300", "from": "Enrique <" + addr[7] + ">", "to": "abdullah@amitrixlabs.com",
         "subject": "Re: question", "textPlain": "Sounds useful, please send the recording." + QUOTE, "textHtml": "",
         "metadata": {"message-id": "<reply-7@klixar.example>", "in-reply-to": "<seed-31@amitrixlabs.com>"}, "attributes": {"uid": 11}},
        # From the company's own domain, not the scratch contact address: it
        # matches by thread (In-Reply-To), and the domain blocklisted is the
        # real one a reply would come from.
        {"date": "Mon, 14 Sep 2026 11:00:00 +0200", "from": "Office <office@bitrial.hu>", "to": "abdullah@amitrixlabs.com",
         "subject": "Re: question", "textPlain": "Stop emailing us." + QUOTE, "textHtml": "",
         "metadata": {"message-id": "<optout-91@bitrial.example>", "in-reply-to": "<seed-35@amitrixlabs.com>"}, "attributes": {"uid": 12}},
        {"date": "Mon, 14 Sep 2026 09:05:00 -0300", "from": "Enrique <" + addr[7] + ">", "to": "abdullah@amitrixlabs.com",
         "subject": "Automatic reply: question", "textPlain": "I am out of office with no access to email.", "textHtml": "",
         "metadata": {"message-id": "<ooo-7@klixar.example>", "auto-submitted": "auto-replied"}, "attributes": {"uid": 13}},
        {"date": "Mon, 14 Sep 2026 08:00:00 +0000", "from": "SaaS News <news@somesaas.example>", "to": "abdullah@amitrixlabs.com",
         "subject": "This week in SaaS", "textPlain": "Big news.\n\nTo unsubscribe click here.", "textHtml": "",
         "metadata": {"message-id": "<news-1@somesaas.example>"}, "attributes": {"uid": 14}},
        {"date": "Mon, 14 Sep 2026 09:00:00 -0300", "from": "Enrique <" + addr[7] + ">", "to": "abdullah@amitrixlabs.com",
         "subject": "Re: question", "textPlain": "Sounds useful, please send the recording." + QUOTE, "textHtml": "",
         "metadata": {"message-id": "<reply-7@klixar.example>", "in-reply-to": "<seed-31@amitrixlabs.com>"}, "attributes": {"uid": 15}},
    ],
}
build(fixtures=fixtures, operator="operator@dryrun.invalid")
import_wf("mailwatch-test.json")
before = len(sink_log())
run = execute("mailwatch0001t")
show("Mirror Sent", out_items(run, "Mirror Sent"))
show("Record Inbound", [{k: r[k] for k in ("classification", "notify", "matched_by", "lead_id", "blocklisted")}
                        for r in out_items(run, "Record Inbound")])
ms = {r["message_id"]: r for r in psql_json(DRY, "SELECT message_id, external, source, seen_in_sent_folder, lead_id FROM mailbox_sent")}
expect("the workflow's own send is now seen in the Sent folder (the mirror's proof of life)",
       ms["<seed-31@amitrixlabs.com>"]["seen_in_sent_folder"] is True and ms["<seed-31@amitrixlabs.com>"]["source"] == "workflow")
expect("a manual send to a prospect is mirrored, external, and linked to lead 104",
       ms["<manual-104@amitrixlabs.com>"]["external"] is True and ms["<manual-104@amitrixlabs.com>"]["lead_id"] == 104)
expect("a note to self is mirrored as NOT external", ms["<self-9@amitrixlabs.com>"]["external"] is False)
expect("an undated message is counted as undated, not guessed",
       "<undated@amitrixlabs.com>" not in ms and psql(DRY, "SELECT undated FROM mailbox_sync WHERE folder = 'Sent';").strip() == "1")
im = {r["message_id"]: r for r in psql_json(DRY, "SELECT message_id, classification, opt_out_keyword, lead_id, matched_by FROM inbound_messages")}
show("inbound_messages", list(im.values()))
expect("the positive reply (quoting our 'reply no' line) is a REPLY, matched by thread",
       im["<reply-7@klixar.example>"]["classification"] == "reply" and im["<reply-7@klixar.example>"]["matched_by"] == "thread")
expect("'Stop emailing us.' is an OPT-OUT on 'stop'",
       (im["<optout-91@bitrial.example>"]["classification"], im["<optout-91@bitrial.example>"]["opt_out_keyword"]) == ("opt-out", "stop"))
expect("the out-of-office is an auto-reply, not a reply", im["<ooo-7@klixar.example>"]["classification"] == "auto-reply")
expect("the newsletter (with 'unsubscribe' in it) is unmatched and blocklists nothing",
       im["<news-1@somesaas.example>"]["classification"] == "unmatched")
bl = [r["domain"] for r in psql_json(DRY, "SELECT domain FROM blocklist ORDER BY domain")]
expect("the opt-out blocklisted bitrial.hu -- and only that", bl == ["bitrial.hu"], str(bl))
leads = {r["id"]: r["status"] for r in psql_json(DRY, "SELECT id, status FROM leads WHERE id IN (7, 91)")}
expect("both leads that answered are 'replied' (kills their follow-ups)", leads == {7: "replied", 91: "replied"}, str(leads))
ol = {r["lead_id"]: r for r in psql_json(DRY, "SELECT lead_id, replied, outcome, left(reply_body, 45) AS reply FROM outreach_log")}
show("outreach_log", list(ol.values()))
expect("outreach_log records the reply text they wrote -- not our quoted email",
       ol[7]["replied"] and ol[7]["reply"].startswith("Sounds useful") and ol[91]["outcome"] == "opt-out:stop")
notes = sink_log()[before:]
expect("two notifications (reply + opt-out) to the operator -- the duplicate delivery did not notify again",
       len(notes) == 2 and all(n["rcpt_to"] == ["<operator@dryrun.invalid>"] for n in notes), str([n["rcpt_to"] for n in notes]))
for n in notes:
    _, m = sink_message(n["file"])
    print("    notification: %s" % m.get("Subject"))
print()

# --- the real database, untouched -------------------------------------------------
stop_sink()
real_after = fingerprint(REAL)
show("real novascout AFTER ", real_after)
expect("the real novascout database is byte-for-byte the same shape as before the run", real_after == real_before)
if not KEEP:
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);" % (DRY, BASE))
    print("  scratch databases dropped (pass --keep to inspect them)")

failed = [r for r in RESULTS if not r[1]]
print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
for label, _, detail in failed:
    print("  FAILED: " + label + ("  -- " + detail if detail else ""))
sys.exit(1 if failed else 0)

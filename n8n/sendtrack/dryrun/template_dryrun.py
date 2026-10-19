"""Template outreach dry run (2026-10-19) -- the real Drafting, Follow-Ups and Send
workflows, end to end, against a scratch copy of the database.

    python n8n/sendtrack/dryrun/template_dryrun.py [--keep]

What it proves, through n8n:

  1. the 30 first touches retired on 2026-10-19 are re-queued by
     redraft_with_template.py's own SQL, and Drafting writes one template email
     for each -- no model call, no LinkedIn draft, their old LinkedIn rows
     untouched;
  2. a template draft with a verified address, a Send clock and no blocklisted
     domain approves itself (approved_by = 'auto', accepted by migration 020's
     trigger);
  3. a lead with no email address is held (no-address), and a lead whose
     country has no Send clock is held (no-send-clock);
  4. the trigger refuses, on the table, a model-written draft's auto-approval,
     a no-address template draft forced to auto, and a template draft for a site;
  5. a template follow-up is written by Follow-Ups and approves itself, and an
     AI follow-up retired with the 'retired/' prefix uses no slot: the lead gets
     template #1, not #2;
  6. Send finds every auto-approved template draft eligible, and sends a first
     touch and then the follow-up into the SMTP sink -- the three-line
     sign-off, no opt-out sentence, no phone, Re: threading on the follow-up;
  7. the safety brake (Section 9, operator decision 2026-10-19; migration 021):
     a bounce rate over 3% pauses Send, the operator gets exactly one alert in
     the sink, nothing goes to a prospect, Claim Send refuses on the table too;
     clearing the pause resumes Send; then a spam complaint -- a real reply run
     through the shipped classifier and Record Inbound -- pauses it again.

Since the switch itself ran live on 2026-10-19 (08:02 UTC), the copy first sets
that morning's 30 template first touches aside, so step 1 replays the switch
from where --apply found it.

REAL: the workflows (built from the same node lists as the shipped ones -- the
differences are listed and asserted), n8n, every Postgres statement and
trigger (migration 020 applied to the scratch copy), the ClinicalTrials.gov
lookup, and nodemailer's SMTP conversation with the sink.

NOT REAL: the recipients. The database is novascout_dryrun, a copy of
novascout with every contact address rewritten to ...@dryrun.invalid and the
operator address replaced by operator@dryrun.invalid. SMTP goes to the sink on
127.0.0.1:2525 inside the n8n container; the dry-run credential's host is read
back and checked before anything runs. The real novascout is only read
(pg_dump); its fingerprint is printed before and after. In the copy, the
follow-ups the live Follow-Ups wrote are set aside (rejected, and lead 1748's
deleted, leaving it only its retired AI #1) so Send's oldest-first pick reaches
the drafts this run writes.

Afterwards the scratch databases are dropped, the sink stopped and the test
workflows deleted from n8n; --keep leaves them. Exit 1 if any expectation did
not hold.
"""
import copy
import csv
import datetime
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
DRAFTING = os.path.join(SENDTRACK, "..", "drafting")
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
MIGRATIONS = [os.path.join(REPO, "postgres", "migrations", m)
              for m in ("020_template_outreach.sql", "021_send_brake.sql")]
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
REAL, BASE, DRY = "novascout", "novascout_dryrun_base", "novascout_dryrun"
SINK_DIR, SINK_JS = "/tmp/novascout_tpl_sink", "/tmp/novascout_tpl_smtp_sink.js"
SCRATCH = tempfile.mkdtemp(prefix="novascout-template-dryrun-")
KEEP = "--keep" in sys.argv
OPERATOR = "operator@dryrun.invalid"
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
TEST_IDS = ["drafting0001t", "followup0001dry", "send0001dry"]
# A Tuesday, 15:00 UTC: 10:00 in the USA, 12:00 in Argentina, 16:00 in Europe.
CLOCK = "2026-10-20T15:00:00.000Z"
NO_CLOCK_LEAD, NO_CLOCK_COUNTRY = 1917, "Atlantis"
FOLLOW_UP_LEAD = 1748      # emailed 2026-10-09; AI #1 retired live; its template #1 is removed in the copy only
SITE_LEAD = 3132           # a Trialsites site at contact_found
OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up."
SIGN_OFF = "Cheers!\nAbdullah Amir\nFounder, Amitrix Labs"

sys.path.insert(0, DRAFTING)
import redraft_with_template as redraft  # noqa: E402  (its REQUEUE_SQL is what --apply runs)

RESULTS = []
SINK_PIDS = []


def expect(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))


def sh(args, stdin=None, check=True):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("FAILED (%d): %s\n%s\n%s" % (p.returncode, " ".join(args[:6]), p.stdout[-1500:], p.stderr[-1500:]))
    return p.stdout


def psql(db, sql):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1",
               "-At"], stdin=sql)


def rows(db, sql):
    return json.loads(psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql.strip().rstrip(";") + ") t;").strip())


def fingerprint(db):
    return json.loads(psql(db, """SELECT json_build_object(
      'drafts', (SELECT json_object_agg(k, n ORDER BY k) FROM (SELECT channel || ':' || status AS k, count(*) AS n FROM drafts GROUP BY 1) s),
      'leads', (SELECT json_object_agg(status, n ORDER BY status) FROM (SELECT status, count(*) AS n FROM leads GROUP BY 1) s),
      'outreach_log', (SELECT count(*) FROM outreach_log),
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent),
      'max_draft_id', (SELECT max(id) FROM drafts),
      'send_pauses', (SELECT count(*) FROM send_pauses),
      'spam_complaints', (SELECT count(*) FROM inbound_messages WHERE spam_complaint),
      'trigger_is_020', (SELECT position('not a template draft' IN prosrc) > 0 FROM pg_proc WHERE proname = 'drafts_approval_rules'));""").strip())


# --- the safety checks, before anything runs ------------------------------------------

def dry_smtp_is_the_sink():
    """Read back the dry-run SMTP credential (its user and password are the
    literal 'dryrun' -- provision_credentials.py) and refuse unless it is the
    loopback sink. Prints host and port only."""
    path = "/tmp/ns_tpl_cred_check.json"
    sh(["docker", "exec", N8N, "n8n", "export:credentials", "--id=novascoutSmtpDry01", "--decrypted",
        "--output=" + path])
    smtp = json.loads(sh(["docker", "exec", N8N, "cat", path]))
    sh(["docker", "exec", N8N, "rm", "-f", path])
    s = (smtp[0] if isinstance(smtp, list) else smtp)["data"]
    print("  dry SMTP credential: host=%s port=%s" % (s.get("host"), s.get("port")))
    if s.get("host") not in ("127.0.0.1", "localhost") or int(s.get("port")) != 2525:
        raise SystemExit("REFUSING: the dry-run SMTP credential does not point at the loopback sink")


def dry_postgres_is_the_scratch_db():
    """Ask the dry-run Postgres credential which database it reaches, through
    n8n itself -- the credential holds the real database password, so it is
    never exported; a one-node probe workflow answers the question instead."""
    probe = {"id": "pgdryprobe01", "name": "TEST - which database does the dry-run credential reach",
             "active": False, "settings": {"executionOrder": "v1"}, "pinData": {},
             "nodes": [{"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger",
                        "typeVersion": 1, "position": [0, 0]},
                       {"parameters": {"operation": "executeQuery", "query": "SELECT current_database() AS db;",
                                       "options": {}},
                        "name": "Which DB", "type": "n8n-nodes-base.postgres", "typeVersion": 2.7, "position": [220, 0],
                        "credentials": PG_DRY}],
             "connections": {"Manual Trigger": {"main": [[{"node": "Which DB", "type": "main", "index": 0}]]}}}
    import_wf(probe)
    try:
        got = out_items(execute("pgdryprobe01"), "Which DB")
    finally:
        psql("n8n", "DELETE FROM workflow_entity WHERE id = 'pgdryprobe01';")
    print("  dry Postgres credential reaches: %s" % (got[0]["db"] if got else "?"))
    if not got or got[0]["db"] != DRY:
        raise SystemExit("REFUSING: the dry-run Postgres credential does not reach %s: %r" % (DRY, got))


# --- scratch database ---------------------------------------------------------------------

def make_base():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                     "CREATE DATABASE %s;" % (DRY, BASE, BASE))
    sh(["docker", "exec", PG, "sh", "-c",
        "pg_dump -U %s -d %s --no-owner | psql -U %s -d %s -q -X -v ON_ERROR_STOP=1 >/dev/null"
        % (PGUSER, REAL, PGUSER, BASE)])
    for m in MIGRATIONS:
        with io.open(m, encoding="utf-8") as fh:
            psql(BASE, fh.read())
    psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
               "WHERE email IS NOT NULL AND email <> '';\n"
               "DELETE FROM settings;\n"
               "INSERT INTO settings (key, value) VALUES ('operator_email', '%s'), ('auto_approve_email', 'true');\n"
               "DELETE FROM digest_log;\n" % OPERATOR)
    left = int(psql(BASE, "SELECT count(*) FROM contacts WHERE coalesce(email, '') <> '' "
                          "AND email NOT LIKE '%@dryrun.invalid';").strip())
    if left:
        raise SystemExit("REFUSING: %d contact address(es) in the scratch copy are not @dryrun.invalid" % left)


def fresh():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s TEMPLATE %s;" % (DRY, DRY, BASE))


# --- n8n ---------------------------------------------------------------------------------

def build_all():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DRAFTING_OUT=os.path.join(SCRATCH, "shipped", "drafting.json"))
    p = subprocess.run([sys.executable, os.path.join(DRAFTING, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("drafting build failed:\n" + p.stderr[-2000:])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"), SENDTRACK_DRYRUN_NOW=CLOCK)
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("sendtrack build failed:\n" + p.stderr[-2000:])


def load(*parts):
    with io.open(os.path.join(SCRATCH, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def node_diff(a, b):
    an = {n["name"]: n for n in a["nodes"]}
    bn = {n["name"]: n for n in b["nodes"]}
    out = [(k, "removed") for k in sorted(set(an) - set(bn))] + [(k, "added") for k in sorted(set(bn) - set(an))]
    for k in sorted(set(an) & set(bn)):
        x, y = copy.deepcopy(an[k]), copy.deepcopy(bn[k])
        if x.get("credentials") != y.get("credentials"):
            out.append((k, "credentials"))
        x.pop("credentials", None)
        y.pop("credentials", None)
        if x != y:
            out.append((k, "OTHER"))
    return sorted(out)


def drafting_variant():
    shipped = load("shipped", "drafting.json")
    v = copy.deepcopy(shipped)
    v["id"], v["name"] = "drafting0001t", "TEST - Drafting (scratch DB, template)"
    v["nodes"] = [n for n in v["nodes"] if n["name"] != "Every 30 Minutes"]
    v["connections"] = {k: x for k, x in v["connections"].items() if k != "Every 30 Minutes"}
    for n in v["nodes"]:
        if "postgres" in n.get("credentials", {}):
            n["credentials"] = copy.deepcopy(PG_DRY)
    want = sorted([("Every 30 Minutes", "removed"), ("Get Draft Batch", "credentials"),
                   ("Write Drafts & Advance", "credentials")])
    got = node_diff(shipped, v)
    if got != want:
        raise SystemExit("the Drafting test variant differs from the shipped workflow in more than allowed: %r" % got)
    return v


def import_wf(wf):
    blob = json.dumps(wf, ensure_ascii=False)
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_tpl_wf.json"], stdin=blob)
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_tpl_wf.json"])
    sh(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_tpl_wf.json"])
    got = rows("n8n", "SELECT id, active FROM workflow_entity WHERE id = '%s'" % wf["id"])
    if not got or got[0]["active"]:
        raise SystemExit("test workflow %s did not import inactive: %r" % (wf["id"], got))


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
        return []
    items = []
    for r in rd[node]:
        main = r["data"]["main"]
        items += [i["json"] for i in (main[output] if len(main) > output and main[output] else [])]
    return items


# --- the sink (stopped by its own PID, never by name) ---------------------------------------

def start_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "rm -rf %s %s.pid %s.ready" % (SINK_DIR, SINK_JS, SINK_JS)])
    with io.open(os.path.join(HERE, "smtp_sink.js"), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % SINK_JS], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "sh", "-c",
        "node %s %s 2525 > %s.ready 2>&1 & echo $! > %s.pid" % (SINK_JS, SINK_DIR, SINK_JS, SINK_JS)])
    for _ in range(50):
        out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s.ready 2>/dev/null; exit 0" % SINK_JS])
        if "sink on" in out:
            break
        if "Error" in out:
            raise SystemExit("the SMTP sink failed to start:\n" + out)
        time.sleep(0.2)
    else:
        raise SystemExit("the SMTP sink did not start")
    pid = sh(["docker", "exec", N8N, "cat", SINK_JS + ".pid"]).strip()
    if pid:
        SINK_PIDS.append(pid)


def sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def sink_message(file):
    raw = subprocess.run(["docker", "exec", N8N, "cat", file], stdout=subprocess.PIPE).stdout
    return email.message_from_bytes(raw, policy=email.policy.default)


def stop_sink():
    for pid in SINK_PIDS:
        sh(["docker", "exec", N8N, "kill", pid], check=False)
    SINK_PIDS[:] = []


# =============================================================================================

print("Template outreach dry run -- scratch DB %s, SMTP sink on 127.0.0.1:2525 inside %s, clock %s\n"
      % (DRY, N8N, CLOCK))
before = fingerprint(REAL)
print("  live novascout before: %s" % json.dumps(before))
dry_smtp_is_the_sink()

try:
    make_base()
    fresh()
    dry_postgres_is_the_scratch_db()
    build_all()
    import_wf(drafting_variant())
    import_wf(load("variants", "followups-dryrun.json"))
    import_wf(load("variants", "send-dryrun.json"))
    for wf_file, name in (("followups-dryrun.json", "Find Due Follow-Ups"), ("send-dryrun.json", "Send Email")):
        n = [x for x in load("variants", wf_file)["nodes"] if x["name"] == name][0]
        if json.dumps(n.get("credentials", {})).find("Dry01") == -1:
            raise SystemExit("REFUSING: %s in %s does not carry a dry-run credential" % (name, wf_file))
    start_sink()

    # --- 1. the re-queue, exactly as redraft_with_template.py --apply does it ------------------
    print("\n1. Re-queue the retired leads and draft them")
    # In the copy only: the switch ran live at 08:02 UTC on 2026-10-19 and the
    # template first touches were written 08:30-09:30. Those not yet sent are set
    # aside, so the copy is back where --apply found it; a lead live Send has
    # already emailed is no longer --apply's to re-queue, and is left out.
    undone = psql(DRY, "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' "
                       "WHERE variant LIKE 'template/%%' AND created_at >= '2026-10-19 08:00+00' "
                       "AND status IN ('approved', 'pending') RETURNING id;").split()
    print("     (copy only: %d of this morning's template first touches set aside to replay the switch)" % len(undone))
    retired = rows(DRY, "SELECT DISTINCT d.lead_id FROM drafts d WHERE d.status = 'rejected' "
                        "AND position('%s' IN coalesce(d.hold_reason, '')) > 0 ORDER BY 1" % redraft.RETIRED_MARK)
    emailed = {r["lead_id"] for r in rows(DRY, "SELECT DISTINCT lead_id FROM outreach_log WHERE channel = 'email'")}
    expect("the copy carries the 30 leads retired on 2026-10-19", len(retired) == 30, str(retired))
    retired_ids = [r["lead_id"] for r in retired if r["lead_id"] not in emailed]
    print("     %d of them not yet emailed live -- the ones --apply re-queues" % len(retired_ids))
    if NO_CLOCK_LEAD not in retired_ids:
        NO_CLOCK_LEAD = [i for i in retired_ids if i not in (14, 55, 538)][0]
    linkedin_before = rows(DRY, "SELECT id, status FROM drafts WHERE channel = 'linkedin' AND lead_id IN (%s) ORDER BY id"
                           % ",".join(map(str, retired_ids)))
    psql(DRY, "UPDATE leads SET country = '%s' WHERE id = %d;" % (NO_CLOCK_COUNTRY, NO_CLOCK_LEAD))
    requeued = psql(DRY, "BEGIN;\n" + redraft.REQUEUE_SQL + "\nCOMMIT;\n").split()
    expect("redraft_with_template.py's re-queue puts exactly those back to contact_found",
           sorted(int(x) for x in requeued) == retired_ids, str(requeued))
    max_before = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())

    runs = 0
    while runs < 5:
        waiting = int(psql(DRY, "SELECT count(*) FROM leads l JOIN enrichments e ON e.lead_id = l.id "
                                "WHERE l.status = 'contact_found' AND lead_company_type(e.raw_extraction, l.source) = 'CRO';").strip())
        if not waiting:
            break
        run = execute("drafting0001t")
        runs += 1
        built = out_items(run, "Build First Touch")
        print("     run %d: %d lead(s) built, %d written" % (runs, len(built), len(out_items(run, "Write Drafts & Advance"))))
    new = rows(DRY, "SELECT d.id, d.lead_id, d.channel, d.status, d.approved_by, d.variant, d.subject, d.body, d.hold_reason "
                    "FROM drafts d WHERE d.id > %d ORDER BY d.id" % max_before)
    by_lead = {}
    for d in new:
        by_lead.setdefault(d["lead_id"], []).append(d)
    expect("every re-queued lead got exactly one new draft, an email", sorted(by_lead) == retired_ids
           and all(len(v) == 1 and v[0]["channel"] == "email" for v in by_lead.values()),
           "%d leads, %d drafts" % (len(by_lead), len(new)))
    expect("no LinkedIn draft was written", not [d for d in new if d["channel"] == "linkedin"])
    linkedin_after = rows(DRY, "SELECT id, status FROM drafts WHERE channel = 'linkedin' AND lead_id IN (%s) ORDER BY id"
                          % ",".join(map(str, retired_ids)))
    expect("their existing LinkedIn drafts are untouched (%d rows)" % len(linkedin_before), linkedin_before == linkedin_after)
    expect("every new draft is a template variant", all(d["variant"].startswith("template/") for d in new))
    expect("no new draft carries the opt-out sentence or a phone number",
           all(OPT_OUT not in d["body"] and "+923" not in d["body"] for d in new))
    expect("every new draft ends with the three-line sign-off", all(d["body"].endswith(SIGN_OFF) for d in new))
    site = rows(DRY, "SELECT status FROM leads WHERE id = %d" % SITE_LEAD)
    expect("the site lead is not drafted -- it waits at contact_found",
           site and site[0]["status"] == "contact_found" and SITE_LEAD not in by_lead)

    # --- 2. and 3. who approved itself and who was held -----------------------------------------
    print("\n2-3. Auto-approval and holds")
    facts = rows(DRY, "SELECT l.id AS lead_id, l.country, c.email, coalesce(c.verified, false) AS verified FROM leads l "
                      "JOIN contacts c ON c.lead_id = l.id WHERE l.id IN (%s)" % ",".join(map(str, retired_ids)))
    facts = {f["lead_id"]: f for f in facts}
    approved = [d for d in new if d["status"] == "approved"]
    expect("every approved draft is approved_by 'auto' (the trigger accepted it)",
           approved and all(d["approved_by"] == "auto" for d in approved), str(len(approved)))
    expect("every lead with a verified address and a clock was approved",
           all(by_lead[i][0]["status"] == "approved" for i, f in facts.items()
               if f["email"] and f["verified"] and i != NO_CLOCK_LEAD))
    no_addr = [i for i, f in facts.items() if not f["email"]]
    expect("a lead with no email address is held no-address (%s)" % no_addr,
           no_addr and all(by_lead[i][0]["status"] == "pending" and by_lead[i][0]["variant"].endswith("+no-address")
                           and by_lead[i][0]["hold_reason"].startswith("no-address:") for i in no_addr))
    nc = by_lead[NO_CLOCK_LEAD][0]
    expect("a lead whose country has no Send clock is held no-send-clock (lead %d, %s)" % (NO_CLOCK_LEAD, NO_CLOCK_COUNTRY),
           nc["status"] == "pending" and nc["variant"].endswith("+no-send-clock")
           and nc["hold_reason"] == "no-send-clock: Atlantis has no business-hours clock, so this email can never be "
                                    "sent (Section 12)", "%s / %s" % (nc["variant"], nc["hold_reason"]))
    counts = {}
    for d in new:
        k = d["variant"].split("/")[1] + "/" + d["variant"].split("/")[2].split("+")[0]
        counts[k] = counts.get(k, 0) + 1
    print("     arm/fact split: %s" % json.dumps(counts, sort_keys=True))
    expect("both arms were written", any(k.startswith("A/") for k in counts) and any(k.startswith("B/") for k in counts))

    # --- 4. the table refuses what the workflow would never write -----------------------------------
    print("\n4. The trigger (migration 020) on the table")
    lead_ok = [i for i, f in facts.items() if f["email"] and f["verified"] and i != NO_CLOCK_LEAD][0]
    held = psql(DRY, """INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by, claim_check)
                       VALUES (%d, 'email', 'role-inbox/D1.ANG-HOURS.BEN-247.PR-BOTH.A1', 's', 'b', 'approved', 'auto',
                               '{"result": "pass"}'::jsonb)
                       RETURNING status || ' | ' || coalesce(hold_reason, '');""" % lead_ok).strip()
    expect("a model-written draft can no longer approve itself, even with a passing claim check",
           held.startswith("pending | db-guard:") and "not a template draft" in held, held)
    forced = psql(DRY, "UPDATE drafts SET status = 'approved', approved_by = 'auto' WHERE id = %d "
                       "RETURNING status || ' | ' || coalesce(hold_reason, '');" % by_lead[no_addr[0]][0]["id"]).strip()
    expect("a no-address template draft forced to auto is turned back into a hold",
           forced.startswith("pending | db-guard:") and "no verified deliverable address" in forced, forced)
    site_try = psql(DRY, """INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by)
                           VALUES (%d, 'email', 'template/A/company/role-inbox', 's', 'b', 'approved', 'auto')
                           RETURNING status || ' | ' || coalesce(hold_reason, '');""" % SITE_LEAD).strip()
    expect("a template draft for a site cannot approve itself", "not a CRO" in site_try, site_try)
    psql(DRY, "DELETE FROM drafts WHERE id > (SELECT max(id) FROM drafts) - 2 AND variant IN "
              "('role-inbox/D1.ANG-HOURS.BEN-247.PR-BOTH.A1', 'template/A/company/role-inbox') AND subject = 's';")

    # --- 5. a template follow-up ------------------------------------------------------------------------
    print("\n5. Follow-Ups writes a template follow-up; a retired AI follow-up uses no slot")
    # Live, 2026-10-19: the 41 unsent AI follow-ups were retired -- rejected, the
    # variant prefixed 'retired/' -- and Follow-Ups wrote each lead its template
    # follow-up. In the copy only: lead 1748's template #1 is removed, so all it
    # has is its retired AI #1, and every other open follow-up is set aside.
    old = rows(DRY, "SELECT variant, status FROM drafts WHERE lead_id = %d AND variant LIKE 'retired/follow-up-%%'"
                    % FOLLOW_UP_LEAD)
    expect("lead %d's AI follow-up #1 is retired: rejected, variant prefixed retired/" % FOLLOW_UP_LEAD,
           len(old) == 1 and old[0]["status"] == "rejected" and old[0]["variant"].startswith("retired/follow-up-1/"),
           str(old))
    psql(DRY, "DELETE FROM drafts WHERE lead_id = %d AND variant LIKE 'follow-up-%%' AND status IN ('approved', 'pending');\n"
              "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' "
              "WHERE channel = 'email' AND status IN ('approved', 'pending') AND variant LIKE 'follow-up-%%';"
              % FOLLOW_UP_LEAD)
    fu_max = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())
    run = execute("followup0001dry")
    fus = rows(DRY, "SELECT id, lead_id, status, approved_by, variant, subject, body FROM drafts WHERE id > %d "
                    "AND variant LIKE 'follow-up-%%' ORDER BY id" % fu_max)
    mine = [f for f in fus if f["lead_id"] == FOLLOW_UP_LEAD]
    expect("lead %d got template follow-up #1 -- not #2: the retired AI #1 used no slot -- approved by auto"
           % FOLLOW_UP_LEAD,
           len(mine) == 1 and mine[0]["variant"] == "follow-up-1/template/A" and mine[0]["status"] == "approved"
           and mine[0]["approved_by"] == "auto", str(mine[:1]))
    expect("every follow-up written is a template follow-up (%d written)" % len(fus),
           all("/template/" in f["variant"] for f in fus))
    fu = mine[0] if mine else None

    # --- 6. Send ------------------------------------------------------------------------------------------
    print("\n6. Send")
    # In the copy only: its Sent mirror is a snapshot of the live mailbox, which
    # this run never reads, so a live send the live mirror has not seen yet
    # (Mailbox Watch down) would make Send refuse sent-mirror-behind here.
    unseen = psql(DRY, "UPDATE mailbox_sent SET seen_in_sent_folder = true WHERE source = 'workflow' "
                       "AND NOT seen_in_sent_folder RETURNING message_id;").split()
    if unseen:
        print("     (copy only: %d live send(s) the live mirror has not seen marked seen)" % len(unseen))
    run = execute("send0001dry")
    decision = out_items(run, "Decide Send")[0]
    template_ids = {d["id"] for d in approved} | ({fu["id"]} if fu else set())
    excluded = {x["draft_id"]: x["reason"] for x in decision["excluded"]}
    eligible = {x["draft_id"] for x in decision["eligible"]}
    expect("Send finds every auto-approved template draft eligible (%d), none excluded" % len(template_ids),
           template_ids <= eligible and not (template_ids & set(excluded)),
           "excluded: %r" % {k: v for k, v in excluded.items() if k in template_ids})
    expect("... and sends one: %s" % decision.get("detail"), decision["send"] is True, decision.get("reason"))
    sent_id = decision["payload"]["draft_id"] if decision.get("payload") else None
    log = sink_log()
    expect("the sink captured exactly one message", len(log) == 1, str(log))
    if log:
        msg = sink_message(log[0]["file"])
        body = msg.get_content().replace("\r\n", "\n").strip()
        d = [x for x in new if x["id"] == sent_id][0]
        expect("it went to a @dryrun.invalid address", all(r.strip("<>").endswith("@dryrun.invalid") for r in log[0]["rcpt_to"]),
               str(log[0]["rcpt_to"]))
        expect("what went out is the draft, byte for byte", body == d["body"].strip(), body[:200])
        expect("From is the sender", msg["From"] == "Abdullah Amir <abdullah@amitrixlabs.com>", msg["From"])
        expect("no opt-out sentence, no phone, the three-line sign-off",
               OPT_OUT not in body and "+923" not in body and body.endswith(SIGN_OFF))
        print("\n     --- sent to the sink (draft %d, lead %d) ---\n     Subject: %s\n%s\n     ---"
              % (sent_id, d["lead_id"], msg["Subject"], "\n".join("     " + ln for ln in body.split("\n"))))
    st = rows(DRY, "SELECT d.status, l.status AS lead_status, o.message_id IS NOT NULL AS confirmed FROM drafts d "
                   "JOIN leads l ON l.id = d.lead_id JOIN outreach_log o ON o.draft_id = d.id WHERE d.id = %s"
              % sent_id) if sent_id is not None else []
    expect("the draft is sent, the lead is sent, the claim carries the sink's Message-ID",
           st and st[0] == {"status": "sent", "lead_status": "sent", "confirmed": True}, str(st))

    if fu:
        # Only the follow-up left approved, so the second tick picks it.
        psql(DRY, "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' "
                  "WHERE status = 'approved' AND channel = 'email' AND id <> %d;" % fu["id"])
        run = execute("send0001dry")
        decision = out_items(run, "Decide Send")[0]
        expect("the template follow-up is sent on the next tick: %s" % decision.get("detail"),
               decision["send"] is True and decision["payload"]["draft_id"] == fu["id"], decision.get("reason"))
        log = sink_log()
        if len(log) == 2:
            msg = sink_message(log[1]["file"])
            body = msg.get_content().replace("\r\n", "\n").strip()
            first = rows(DRY, "SELECT d.subject FROM outreach_log o JOIN drafts d ON d.id = o.draft_id "
                              "WHERE o.lead_id = %d AND d.variant NOT LIKE 'follow-up-%%' ORDER BY o.sent_at LIMIT 1"
                         % FOLLOW_UP_LEAD)[0]["subject"]
            expect("the follow-up is threaded: Re: the first subject", msg["Subject"] == "Re: " + first, msg["Subject"])
            expect("the follow-up signs with the three-line sign-off and quotes the first email below it",
                   (SIGN_OFF + "\n\nOn ") in body and "\n> " in body)
            print("\n     --- sent to the sink (follow-up, draft %d, lead %d) ---\n     Subject: %s\n%s\n     ---"
                  % (fu["id"], FOLLOW_UP_LEAD, msg["Subject"],
                     "\n".join("     " + ln for ln in body.split("\n")[:12])))
        else:
            expect("the sink captured the follow-up", False, str(log))

    # --- 7. The safety brake (Section 9, operator decision 2026-10-19; migration 021) ------------------
    print("\n7. The safety brake: a bounce rate over the line, then a spam complaint")

    def bind(sql, params):
        # $n -> dollar-quoted literals. Comments are left alone: the statements carry
        # ' -- ' inside string literals, and a JSON literal never spans a line.
        return re.sub(r"\$(\d+)\b", lambda m: "$q$" + params[int(m.group(1)) - 1] + "$q$", sql)

    def csv_row(db, sql):
        out = sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "--csv",
                  "-v", "ON_ERROR_STOP=1"], stdin=sql)
        got = list(csv.DictReader(io.StringIO(out)))
        return got[-1] if got else {}

    def classify_js(msg):
        js = ("const { extractFunctions } = require(%s);\n"
              "const { classify } = extractFunctions(%s, '// Node body', ['classify'], "
              "(s) => s.replace('__OWN_DOMAIN__', JSON.stringify('amitrixlabs.com')));\n"
              "process.stdout.write(JSON.stringify(classify(JSON.parse(require('fs').readFileSync(0, 'utf8')))));\n"
              % (json.dumps(os.path.join(SENDTRACK, "harness.js")),
                 json.dumps(os.path.join(SENDTRACK, "code_classify_reply.js"))))
        return json.loads(sh(["node", "-e", js], stdin=json.dumps(msg)))

    def operator_mail(log):
        return [x for x in log if any(r.strip("<>") == OPERATOR for r in x["rcpt_to"])]

    def prospect_mail(log):
        return [x for x in log if not any(r.strip("<>") == OPERATOR for r in x["rcpt_to"])]

    send_nodes = {n["name"]: n for n in load("variants", "send-dryrun.json")["nodes"]}
    # Every approved first touch this run wrote, back in the queue (step 6 set them aside).
    spare = [d["id"] for d in approved if d["id"] != sent_id]
    psql(DRY, "UPDATE drafts SET status = 'approved', approved_by = 'auto', reject_reason = NULL "
              "WHERE id IN (%s) AND status = 'rejected';" % ",".join(map(str, spare)))
    # One bounce, on a send already in the window before this run -- so clearing the
    # pause (at the real clock, which is before the dry-run clock) leaves it behind.
    bounced = rows(DRY, "SELECT o.id, o.lead_id FROM outreach_log o WHERE o.channel = 'email' AND o.message_id IS NOT NULL "
                        "AND o.sent_at >= timestamptz '%s' - interval '3 days' AND o.sent_at < now() "
                        "ORDER BY o.sent_at DESC LIMIT 1" % CLOCK)
    psql(DRY, "UPDATE outreach_log SET outcome = 'bounced' WHERE id = %d;" % bounced[0]["id"])
    stats = json.loads(psql(DRY, "SELECT send_brake_stats(timestamptz '%s', 3);" % CLOCK).strip())
    print("     window as of the dry-run clock: %d sends, %d bounced (%.1f%%)"
          % (stats["sends"], stats["bounced"], 100.0 * stats["bounced"] / max(1, stats["sends"])))
    expect("the copy is over the line: 1 bounce in %d sends is more than 3%%" % stats["sends"],
           stats["bounced"] == 1 and stats["bounced"] * 100.0 > 3 * stats["sends"], json.dumps(stats))
    log0 = sink_log()

    run = execute("send0001dry")
    decision = out_items(run, "Decide Send")[0]
    pauses = rows(DRY, "SELECT id, reason, detail, alerted_at, cleared_at FROM send_pauses ORDER BY id")
    log1 = sink_log()
    expect("Check Brake latched one bounce-rate pause",
           [p["reason"] for p in pauses] == ["bounce-rate"] and "1 of the %d emails sent since" % stats["sends"] in pauses[0]["detail"],
           json.dumps(pauses))
    expect("Send paused: Decide refused before anything else, with eligible in-hours drafts waiting (%d)"
           % len([e for e in decision["eligible"] if e["in_window"]]),
           decision["send"] is False and decision["reason"] == "send-paused"
           and any(e["in_window"] for e in decision["eligible"]), decision.get("reason"))
    expect("nothing went to a prospect", len(prospect_mail(log1)) == len(prospect_mail(log0)), str(log1[len(log0):]))
    new_ops = operator_mail(log1)[len(operator_mail(log0)):]
    expect("exactly one alert reached the operator (%s)" % OPERATOR, len(new_ops) == 1, str(new_ops))
    if new_ops:
        msg = sink_message(new_ops[0]["file"])
        body = msg.get_content().replace("\r\n", "\n")
        expect("... subject and text say what tripped and how to resume",
               msg["Subject"] == "[Nova Scout] Send paused: bounce rate over the brake"
               and pauses[0]["detail"] in body and "UPDATE send_pauses SET cleared_at = now()" in body, msg["Subject"])
        print("\n     --- the alert (to %s) ---\n     Subject: %s\n%s\n     ---"
              % (OPERATOR, msg["Subject"], "\n".join("     " + ln for ln in body.strip().split("\n"))))
    expect("... and the pause is stamped alerted once SMTP accepted it",
           rows(DRY, "SELECT alerted_at IS NOT NULL AS a FROM send_pauses WHERE reason = 'bounce-rate'")[0]["a"] is True)

    run = execute("send0001dry")
    decision = out_items(run, "Decide Send")[0]
    log2 = sink_log()
    expect("the next tick: still paused, and no second alert",
           decision["reason"] == "send-paused" and decision["alert"]["notify"] is False and len(log2) == len(log1),
           "%s / %d -> %d" % (decision["reason"], len(log1), len(log2)))

    # Claim Send refuses on the table too: the last line of defence, called directly
    # with exactly the payload Decide would have built for the oldest in-hours draft.
    pick = [e for e in decision["eligible"] if e["in_window"]][0]
    dr = rows(DRY, "SELECT d.id, d.lead_id, d.subject, coalesce(d.edited_body, d.body) AS body, c.email FROM drafts d "
                   "JOIN contacts c ON c.lead_id = d.lead_id WHERE d.id = %d" % pick["draft_id"])[0]
    payload = {"draft_id": dr["id"], "lead_id": dr["lead_id"], "to_addr": dr["email"], "subject": dr["subject"],
               "body": dr["body"].strip(), "raw_body": dr["body"], "clock": CLOCK,
               "day_start": "2026-10-19T19:00:00.000Z", "day_end": "2026-10-20T19:00:00.000Z", "ceiling": 50}
    claim = csv_row(DRY, bind(send_nodes["Claim Send"]["parameters"]["query"], [json.dumps(payload)]))
    still = rows(DRY, "SELECT status FROM drafts WHERE id = %d" % dr["id"])[0]["status"]
    expect("Claim Send refuses the draft while the pause is open (claimed=%s), and it stays approved" % claim.get("claimed"),
           claim.get("claimed") == "f" and still == "approved", json.dumps(claim))

    # The operator clears it. Send resumes on the next tick.
    psql(DRY, "UPDATE send_pauses SET cleared_at = now(), cleared_note = 'dry run: reviewed' WHERE cleared_at IS NULL;")
    run = execute("send0001dry")
    decision = out_items(run, "Decide Send")[0]
    log3 = sink_log()
    resumed = decision.get("payload") or {}
    expect("cleared -> the next tick sends again: %s" % decision.get("detail"),
           decision["send"] is True and len(prospect_mail(log3)) == len(prospect_mail(log2)) + 1, decision.get("reason"))
    expect("... the bounce just reviewed did not trip it again",
           [p["reason"] for p in rows(DRY, "SELECT reason FROM send_pauses WHERE cleared_at IS NULL")] == [])

    # A spam complaint: the reply to that email, through the shipped classifier and Record Inbound.
    mid = rows(DRY, "SELECT message_id FROM outreach_log WHERE draft_id = %d" % resumed.get("draft_id", -1))
    reply = {"from": "Prospect <%s>" % resumed.get("to_addr"), "to": "abdullah@amitrixlabs.com",
             "subject": "Re: " + str(resumed.get("subject")), "date": "Tue, 20 Oct 2026 15:20:00 +0000",
             "textPlain": "This is spam. Stop emailing us.\n\nOn Tue, 20 Oct 2026, Abdullah Amir wrote:\n> Hi there,",
             "textHtml": "",
             "metadata": {"message-id": "<complaint-dryrun-1@cro.example>",
                          "in-reply-to": mid[0]["message_id"] if mid else "", "references": mid[0]["message_id"] if mid else ""}}
    payload = classify_js(reply)
    expect("the classifier reads the reply as an opt-out and a spam complaint",
           [payload["classification"], payload["opt_out_keyword"], payload["spam_complaint"]] == ["opt-out", "stop", True],
           json.dumps({k: payload[k] for k in ("classification", "opt_out_keyword", "spam_complaint")}))
    mw = load("shipped", "mailbox-watch.json")
    record = [n for n in mw["nodes"] if n["name"] == "Record Inbound"][0]["parameters"]["query"]
    psql(DRY, bind(record, [json.dumps(payload)]))
    got = rows(DRY, "SELECT lead_id, classification, spam_complaint, matched_by FROM inbound_messages "
                    "WHERE message_id = '<complaint-dryrun-1@cro.example>'")
    expect("Record Inbound stores it matched to the lead, spam_complaint true",
           got and got[0]["lead_id"] == resumed.get("lead_id") and got[0]["spam_complaint"] is True
           and got[0]["classification"] == "opt-out", json.dumps(got))
    run = execute("send0001dry")
    decision = out_items(run, "Decide Send")[0]
    log4 = sink_log()
    new_ops = operator_mail(log4)[len(operator_mail(log3)):]
    expect("the complaint pauses Send on the next tick", decision["reason"] == "send-paused"
           and [p["reason"] for p in rows(DRY, "SELECT reason FROM send_pauses WHERE cleared_at IS NULL")] == ["spam-complaint"],
           decision.get("reason"))
    expect("... nothing went to a prospect, and one alert reached the operator",
           len(prospect_mail(log4)) == len(prospect_mail(log3)) and len(new_ops) == 1, str(log4[len(log3):]))
    if new_ops:
        msg = sink_message(new_ops[0]["file"])
        expect("... subject: spam complaint", msg["Subject"] == "[Nova Scout] Send paused: spam complaint", msg["Subject"])
finally:
    stop_sink()
    if not KEEP:
        psql("n8n", "DELETE FROM workflow_entity WHERE id IN (%s);" % ",".join("'%s'" % i for i in TEST_IDS))
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);" % (DRY, BASE))
        print("\n(scratch databases dropped, test workflows deleted from n8n, sink stopped)")

after = fingerprint(REAL)
print("  live novascout after:  %s" % json.dumps(after))
expect("the live database is unchanged by the dry run", before == after)
failed = [r for r in RESULTS if not r[1]]
print("\n%d passed, %d failed" % (len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)

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
  5. a template follow-up is written by Follow-Ups and approves itself;
  6. Send finds every auto-approved template draft eligible, and sends a first
     touch and then the follow-up into the SMTP sink -- the three-line
     sign-off, no opt-out sentence, no phone, Re: threading on the follow-up.

REAL: the workflows (built from the same node lists as the shipped ones -- the
differences are listed and asserted), n8n, every Postgres statement and
trigger (migration 020 applied to the scratch copy), the ClinicalTrials.gov
lookup, and nodemailer's SMTP conversation with the sink.

NOT REAL: the recipients. The database is novascout_dryrun, a copy of
novascout with every contact address rewritten to ...@dryrun.invalid and the
operator address replaced by operator@dryrun.invalid. SMTP goes to the sink on
127.0.0.1:2525 inside the n8n container; the dry-run credential's host is read
back and checked before anything runs. The real novascout is only read
(pg_dump); its fingerprint is printed before and after. In the copy, the 40
AI-composed follow-ups approved before the switch are set aside (rejected, and
one deleted) so Send's oldest-first pick reaches the template drafts.

Afterwards the scratch databases are dropped, the sink stopped and the test
workflows deleted from n8n; --keep leaves them. Exit 1 if any expectation did
not hold.
"""
import copy
import datetime
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
DRAFTING = os.path.join(SENDTRACK, "..", "drafting")
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
MIGRATION = os.path.join(REPO, "postgres", "migrations", "020_template_outreach.sql")
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
FOLLOW_UP_LEAD = 1748      # emailed 2026-10-09; its AI follow-up is removed in the copy only
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
    with io.open(MIGRATION, encoding="utf-8") as fh:
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
    retired = rows(DRY, "SELECT DISTINCT d.lead_id FROM drafts d WHERE d.status = 'rejected' "
                        "AND position('%s' IN coalesce(d.hold_reason, '')) > 0 ORDER BY 1" % redraft.RETIRED_MARK)
    retired_ids = [r["lead_id"] for r in retired]
    expect("the copy carries the 30 leads retired on 2026-10-19", len(retired_ids) == 30, str(retired_ids))
    linkedin_before = rows(DRY, "SELECT id, status FROM drafts WHERE channel = 'linkedin' AND lead_id IN (%s) ORDER BY id"
                           % ",".join(map(str, retired_ids)))
    psql(DRY, "UPDATE leads SET country = '%s' WHERE id = %d;" % (NO_CLOCK_COUNTRY, NO_CLOCK_LEAD))
    requeued = psql(DRY, "BEGIN;\n" + redraft.REQUEUE_SQL + "\nCOMMIT;\n").split()
    expect("redraft_with_template.py's re-queue puts exactly those 30 back to contact_found",
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
                           VALUES (%d, 'email', 'template/A/none/role-inbox', 's', 'b', 'approved', 'auto')
                           RETURNING status || ' | ' || coalesce(hold_reason, '');""" % SITE_LEAD).strip()
    expect("a template draft for a site cannot approve itself", "not a CRO" in site_try, site_try)
    psql(DRY, "DELETE FROM drafts WHERE id > (SELECT max(id) FROM drafts) - 2 AND variant IN "
              "('role-inbox/D1.ANG-HOURS.BEN-247.PR-BOTH.A1', 'template/A/none/role-inbox') AND subject = 's';")

    # --- 5. a template follow-up ------------------------------------------------------------------------
    print("\n5. Follow-Ups writes a template follow-up")
    # In the copy only: the 40 AI follow-ups approved before the switch are set
    # aside, and lead %d's is removed so the lead is due for its first one.
    psql(DRY, "DELETE FROM drafts WHERE lead_id = %d AND variant LIKE 'follow-up-%%' AND status IN ('approved', 'pending');\n"
              "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' "
              "WHERE channel = 'email' AND status IN ('approved', 'pending') AND variant LIKE 'follow-up-%%' "
              "AND variant NOT LIKE 'follow-up-%%/template/%%';" % FOLLOW_UP_LEAD)
    fu_max = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())
    run = execute("followup0001dry")
    fus = rows(DRY, "SELECT id, lead_id, status, approved_by, variant, subject, body FROM drafts WHERE id > %d "
                    "AND variant LIKE 'follow-up-%%' ORDER BY id" % fu_max)
    mine = [f for f in fus if f["lead_id"] == FOLLOW_UP_LEAD]
    expect("lead %d got template follow-up #1, approved by auto" % FOLLOW_UP_LEAD,
           len(mine) == 1 and mine[0]["variant"] == "follow-up-1/template/A" and mine[0]["status"] == "approved"
           and mine[0]["approved_by"] == "auto", str(mine[:1]))
    expect("every follow-up written is a template follow-up (%d written)" % len(fus),
           all("/template/" in f["variant"] for f in fus))
    fu = mine[0] if mine else None

    # --- 6. Send ------------------------------------------------------------------------------------------
    print("\n6. Send")
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
                   "JOIN leads l ON l.id = d.lead_id JOIN outreach_log o ON o.draft_id = d.id WHERE d.id = %s" % sent_id)
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

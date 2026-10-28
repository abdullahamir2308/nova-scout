"""Research-site niche dry run (migration 025, 2026-10-28) -- the whole new niche, end to end,
through the real workflows in n8n, against a scratch copy of the database.

    python n8n/sendtrack/dryrun/site_dryrun.py VERIFIED.json [--keep] [--leads N]

  0. A copy of the live database with migration 025 applied, every contact
     address rewritten to ...@dryrun.invalid, the operator operator@dryrun.invalid.
  1. N verified research sites (verify.js's output) go in by ingest.py's own
     statement -- an existing lead is never touched.
  2. The real Enrichment (Ollama), Scoring and Contact Lookup workflows run on
     them; every address Contact Lookup finds is rewritten to @dryrun.invalid
     before anything is drafted.
  3. CONTACT, the shipped Decide Command + Apply Operator Command, gives one
     scored site an address; its acknowledgement names no "not drafted" caveat.
  4. Drafting - Research Sites writes the site wording; a lead with a verified
     address auto-approves (accepted by migration 025's trigger); a Trialsites
     site is not drafted; the niche switch holds.
  5. The trigger on the table: a site draft signed as the CRO, a draft for a
     lead of no niche, a niche switched off -> holds; a CRO template draft still
     approves itself.
  6. Send (dry variant: SMTP into the loopback sink) finds the site drafts
     eligible and sends one, byte for byte, signed "Software Engineer".
  7. Follow-Ups - Research Sites writes follow-up #1 a week later, auto-approved,
     and Send sends it threaded (Re:, the first email quoted).
  8. The Daily Digest's shipped query and code count per niche and per arm.
  9. Every sent CRO template email still re-renders byte for byte.
The live database is only read (pg_dump); its fingerprint is compared before and after.
"""
import argparse
import copy
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
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
WF = os.path.join(REPO, "n8n", "workflows")
N8N, PG, PGUSER = "nova-scout-n8n-1", "nova-scout-postgres-1", "novascout"
REAL, BASE, DRY = "novascout", "novascout_dryrun_base", "novascout_dryrun"
SINK_DIR, SINK_JS = "/tmp/novascout_site_sink", "/tmp/novascout_site_smtp_sink.js"
SCRATCH = tempfile.mkdtemp(prefix="novascout-site-dryrun-")
OPERATOR = "operator@dryrun.invalid"
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
# Wednesday 17:00 UTC: 11:00 in Mexico, 12:00 in the USA, 14:00 in Brazil and Argentina.
CLOCK = "2026-10-28T17:00:00.000Z"
SITE_SIGN = "Cheers!\nAbdullah Amir\nSoftware Engineer, Amitrix Labs"
TRIALSITES_SITE = 3132
IDS = {"enrichment": "enrichment01dry", "scoring": "scoring0001dry", "contacts": "contacts0001dry",
       "drafting": "draftingSites01dry", "followups": "followupSites01dry", "send": "send0001dry"}
RESULTS, SINK_PIDS = [], []


def expect(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + str(detail)[:600]) if detail and not ok else ""))


def sh(args, stdin=None, check=True):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("FAILED (%d): %s\n%s\n%s" % (p.returncode, " ".join(args[:6]), p.stdout[-1500:], p.stderr[-1500:]))
    return p.stdout


def psql(db, sql):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At"], stdin=sql)


def rows(db, sql):
    return json.loads(psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql.strip().rstrip(";") + ") t;").strip())


def fingerprint(db):
    return json.loads(psql(db, """SELECT json_build_object(
      'drafts', (SELECT json_object_agg(k, n ORDER BY k) FROM (SELECT channel || ':' || status AS k, count(*) AS n FROM drafts GROUP BY 1) s),
      'leads', (SELECT json_object_agg(status, n ORDER BY status) FROM (SELECT status, count(*) AS n FROM leads GROUP BY 1) s),
      'contacts', (SELECT count(*) FROM contacts), 'outreach_log', (SELECT count(*) FROM outreach_log),
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent), 'max_draft_id', (SELECT max(id) FROM drafts),
      'operator_commands', (SELECT count(*) FROM operator_commands));""").strip())


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dry_variant(wf, new_id):
    v = copy.deepcopy(wf)
    v["id"], v["name"] = new_id, "DRY RUN - " + wf["name"] + " (scratch DB)"
    sched = {n["name"] for n in v["nodes"] if n["type"] == "n8n-nodes-base.scheduleTrigger"}
    v["nodes"] = [n for n in v["nodes"] if n["name"] not in sched]
    v["connections"] = {k: x for k, x in v["connections"].items() if k not in sched}
    v["settings"] = {"executionOrder": "v1"}
    for n in v["nodes"]:
        if "postgres" in n.get("credentials", {}):
            n["credentials"]["postgres"] = copy.deepcopy(PG_DRY["postgres"])
    # Only the schedule and the Postgres credential may differ from what ships.
    a = {n["name"]: n for n in wf["nodes"] if n["name"] not in sched}
    b = {n["name"]: n for n in v["nodes"]}
    for k in a:
        x, y = copy.deepcopy(a[k]), copy.deepcopy(b[k])
        x.get("credentials", {}).pop("postgres", None)
        y.get("credentials", {}).pop("postgres", None)
        assert x == y, "dry variant of %s differs from what ships in node %s" % (wf["name"], k)
    return v


def import_wf(wf):
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_site_wf.json"], stdin=json.dumps(wf, ensure_ascii=False))
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_site_wf.json"])
    sh(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_site_wf.json"])
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
    items = []
    for r in rd.get(node, []):
        main = r["data"]["main"]
        items += [i["json"] for i in (main[output] if len(main) > output and main[output] else [])]
    return items


def start_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "rm -rf %s %s.pid %s.ready" % (SINK_DIR, SINK_JS, SINK_JS)])
    with io.open(os.path.join(HERE, "smtp_sink.js"), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % SINK_JS], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "sh", "-c", "node %s %s 2525 > %s.ready 2>&1 & echo $! > %s.pid" % (SINK_JS, SINK_DIR, SINK_JS, SINK_JS)])
    for _ in range(50):
        if "sink on" in sh(["docker", "exec", N8N, "sh", "-c", "cat %s.ready 2>/dev/null; exit 0" % SINK_JS]):
            break
        time.sleep(0.2)
    else:
        raise SystemExit("the SMTP sink did not start")
    SINK_PIDS.append(sh(["docker", "exec", N8N, "cat", SINK_JS + ".pid"]).strip())


def sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def sink_message(file):
    raw = subprocess.run(["docker", "exec", N8N, "cat", file], stdout=subprocess.PIPE).stdout
    return email.message_from_bytes(raw, policy=email.policy.default)


def dry_smtp_is_the_sink():
    path = "/tmp/ns_site_cred_check.json"
    sh(["docker", "exec", N8N, "n8n", "export:credentials", "--id=novascoutSmtpDry01", "--decrypted", "--output=" + path])
    smtp = json.loads(sh(["docker", "exec", N8N, "cat", path]))
    sh(["docker", "exec", N8N, "rm", "-f", path])
    s = (smtp[0] if isinstance(smtp, list) else smtp)["data"]
    if s.get("host") not in ("127.0.0.1", "localhost") or int(s.get("port")) != 2525:
        raise SystemExit("REFUSING: the dry-run SMTP credential does not point at the loopback sink")


def run_until(wid, sql_waiting, limit, label):
    for i in range(limit):
        if not int(psql(DRY, sql_waiting).strip()):
            return i
        run = execute(wid)
        print("     %s run %d done" % (label, i + 1))
    return limit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("verified")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--leads", type=int, default=30)
    a = ap.parse_args()

    print("Research-site niche dry run -- scratch DB %s, sink on 127.0.0.1:2525, Send clock %s\n" % (DRY, CLOCK))
    before = fingerprint(REAL)
    before_site_leads = rows(REAL, "SELECT count(*) AS n FROM leads WHERE source IN ('sites-ctgov', 'sites-overture')")[0]["n"]
    dry_smtp_is_the_sink()
    try:
        # --- 0. the copy ----------------------------------------------------------------------
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                         "CREATE DATABASE %s;" % (DRY, BASE, BASE))
        dump = os.path.join(SCRATCH, "live.sql")
        with open(dump, "w", encoding="utf-8") as fh:
            fh.write(sh(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "-d", REAL, "--no-owner"]))
        with open(dump, encoding="utf-8") as fh:
            sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", BASE, "-q", "-X", "-v", "ON_ERROR_STOP=1"], stdin=fh.read())
        with io.open(os.path.join(REPO, "postgres", "migrations", "025_niches.sql"), encoding="utf-8") as fh:
            psql(BASE, fh.read())
        psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
                   "WHERE email IS NOT NULL AND email <> '';\nDELETE FROM settings;\n"
                   "INSERT INTO settings (key, value) VALUES ('operator_email', '%s'), ('auto_approve_email', 'true');\n"
                   "DELETE FROM digest_log;\n" % OPERATOR)
        psql("postgres", "CREATE DATABASE %s TEMPLATE %s;" % (DRY, BASE))
        expect("the copy carries migration 025: two niches, both active",
               rows(DRY, "SELECT code FROM niches WHERE active ORDER BY code") == [{"code": "cro"}, {"code": "research_site"}])

        # Workflows: the dry Send comes from the sendtrack build's own variant generator.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
                   SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"), SENDTRACK_DRYRUN_NOW=CLOCK)
        p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        if p.returncode:
            raise SystemExit("sendtrack build failed:\n" + p.stderr[-2000:])
        import_wf(dry_variant(load(os.path.join(WF, "enrichment.json")), IDS["enrichment"]))
        import_wf(dry_variant(load(os.path.join(WF, "scoring.json")), IDS["scoring"]))
        import_wf(dry_variant(load(os.path.join(WF, "apollo-contacts.json")), IDS["contacts"]))
        import_wf(dry_variant(load(os.path.join(WF, "drafting-sites.json")), IDS["drafting"]))
        import_wf(dry_variant(load(os.path.join(WF, "follow-ups-sites.json")), IDS["followups"]))
        import_wf(load(os.path.join(SCRATCH, "variants", "send-dryrun.json")))
        start_sink()

        # --- 1. ingest -----------------------------------------------------------------------
        print("\n1. Ingest %d verified research sites" % a.leads)
        verified = load(a.verified)
        good = [v for v in verified.values() if v.get("outcome") == "resolved" and v.get("robots_allowed")]
        americas = ("Mexico", "USA", "Brazil", "Argentina", "Colombia", "Chile", "Peru")
        good.sort(key=lambda v: (v["candidate"]["country"] not in americas, -v["candidate"]["ctgov_recruiting"], v["host"]))
        pick = {v["host"]: v for v in good[:a.leads]}
        sample = os.path.join(SCRATCH, "sample.json")
        with open(sample, "w", encoding="utf-8") as fh:
            json.dump(pick, fh)
        max_lead = int(psql(DRY, "SELECT max(id) FROM leads;").strip())
        out = sh([sys.executable, os.path.join(REPO, "n8n", "sites", "open_sources", "ingest.py"), sample, "--db", DRY, "--apply"])
        print("     " + out.strip().replace("\n", "\n     "))
        new = rows(DRY, "SELECT id, source, status FROM leads WHERE id > %d" % max_lead)
        expect("ingest wrote new leads at 'ingested', each with its source row", new and all(r["status"] == "ingested" for r in new)
               and int(psql(DRY, "SELECT count(*) FROM site_lead_sources WHERE lead_id > %d;" % max_lead).strip()) == len(new), len(new))
        again = sh([sys.executable, os.path.join(REPO, "n8n", "sites", "open_sources", "ingest.py"), sample, "--db", DRY, "--apply"])
        expect("ingest is idempotent: a second run writes nothing", '"new_leads" : 0' in again, again)
        ids = ",".join(str(r["id"]) for r in new)

        # --- 2. Enrichment, Scoring, Contact Lookup --------------------------------------------
        print("\n2. Enrichment (Ollama), Scoring, Contact Lookup -- the real workflows")
        run_until(IDS["enrichment"], "SELECT count(*) FROM leads WHERE status = 'ingested' AND id IN (%s);" % ids, 6, "enrichment")
        run_until(IDS["scoring"], "SELECT count(*) FROM leads WHERE status = 'enriched' AND id IN (%s);" % ids, 6, "scoring")
        st = rows(DRY, "SELECT l.id, l.status, l.company_name, s.fit_score, s.disqualified, left(s.disqualify_reason, 90) AS why, "
                       "lead_niche(e.raw_extraction, l.source) AS niche, e.raw_extraction->>'company_type' AS ct "
                       "FROM leads l JOIN scores s ON s.lead_id = l.id JOIN enrichments e ON e.lead_id = l.id WHERE l.id IN (%s) ORDER BY l.id" % ids)
        for r in st:
            print("     lead %(id)s %(status)s fit %(fit_score)s %(ct)s %(niche)s %(company_name)s %(why)s" % r)
        expect("every ingested site was enriched and scored", len(st) == len(new))
        expect("no site-source lead is in the CRO niche", all(r["niche"] != "cro" for r in st))
        rec = {r["lead_id"]: r["n"] for r in rows(DRY, "SELECT lead_id, ctgov_recruiting AS n FROM site_lead_sources WHERE lead_id IN (%s)" % ids)}
        expect("enrichment's 'CRO' on a site source: disqualified, unless the facility recruits on ClinicalTrials.gov "
               "(then a site -- never the CRO lane)",
               all(r["disqualified"] or (rec.get(r["id"], 0) > 0 and r["niche"] == "research_site") for r in st if r["ct"] == "CRO"))
        psql(DRY, "SELECT 1;")
        run_until(IDS["contacts"], "SELECT count(*) FROM leads l JOIN scores s ON s.lead_id = l.id WHERE l.status = 'scored' "
                                   "AND s.fit_score >= 50 AND l.id IN (%s) AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.lead_id = l.id) "
                                   "AND NOT EXISTS (SELECT 1 FROM contact_attempts a WHERE a.lead_id = l.id);" % ids, 6, "contacts")
        psql(DRY, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
                  "WHERE email IS NOT NULL AND email <> '' AND email NOT LIKE '%@dryrun.invalid';")
        left = int(psql(DRY, "SELECT count(*) FROM contacts WHERE coalesce(email, '') <> '' AND email NOT LIKE '%@dryrun.invalid';").strip())
        expect("every address in the copy is @dryrun.invalid before anything is drafted", left == 0, left)
        found = rows(DRY, "SELECT l.id, c.email, c.verified FROM leads l JOIN contacts c ON c.lead_id = l.id "
                          "WHERE l.id IN (%s) AND l.status = 'contact_found'" % ids)
        print("     %d site(s) at contact_found with an address from their own website" % len(found))

        # --- 3. CONTACT ----------------------------------------------------------------------
        print("\n3. CONTACT gives a scored site its address")
        target = rows(DRY, "SELECT l.id, l.domain FROM leads l JOIN scores s ON s.lead_id = l.id JOIN enrichments e ON e.lead_id = l.id "
                           "WHERE l.id IN (%s) AND l.status = 'scored' AND NOT s.disqualified "
                           "AND lead_niche(e.raw_extraction, l.source) = 'research_site' ORDER BY s.fit_score DESC LIMIT 1" % ids)
        contacted = None
        if target:
            contacted = target[0]
            mw = load(os.path.join(WF, "mailbox-watch.json"))
            nodes = {n["name"]: n for n in mw["nodes"]}
            js, apply_sql = nodes["Decide Command"]["parameters"]["jsCode"], nodes["Apply Operator Command"]["parameters"]["query"]
            psql(DRY, "UPDATE settings SET value = '%s' WHERE key = 'operator_email';" % OPERATOR)
            lib = js[:js.index("// Node body")]
            src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
                "const ctx = Object.assign({ command: operatorCommand({ metadata: { 'authentication-results': a.auth } }, a.fresh) }, a.ctx);\n" \
                "process.stdout.write(JSON.stringify(Object.assign({}, commandDecision(ctx), { payload: ctx.payload })));\n"
            ctx = {"payload": {"message_id": "<contact-site-dryrun@dryrun.invalid>", "received_at": CLOCK, "from_addr": OPERATOR,
                               "subject": "contacts"}, "operator_email": OPERATOR, "now": CLOCK, "code_found": False,
                   "code_used_at": None, "code_expires_at": None, "code_kind": None, "code_outcome": None, "draft_id": None,
                   "draft_status": None, "draft_body": None, "code_lead_domain": None}
            auth = "mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com: domain of dryrun.invalid designates 1.2.3.4)"
            fresh = "CONTACT %s maria.lopez@dryrun.invalid Maria Lopez" % contacted["domain"]
            js_file = os.path.join(SCRATCH, "decide_command.js")
            with open(js_file, "w", encoding="utf-8") as fh:
                fh.write(src)
            verdict = json.loads(subprocess.run(["node", js_file], input=json.dumps({"fresh": fresh, "ctx": ctx, "auth": auth}),
                                                stdout=subprocess.PIPE, encoding="utf-8").stdout)
            res = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", DRY, "-X", "-q", "--csv", "-v", "ON_ERROR_STOP=1"],
                                 input=apply_sql.replace("$1", "$q$" + json.dumps(verdict) + "$q$"), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, encoding="utf-8")
            applied = next(csv.DictReader(io.StringIO(res.stdout)), {}) if not res.returncode else {}
            got = rows(DRY, "SELECT l.status, c.email, c.verified, c.name FROM leads l JOIN contacts c ON c.lead_id = l.id WHERE l.id = %d" % contacted["id"])
            expect("CONTACT sets the site's contact (verified) and moves it to contact_found",
                   got == [{"status": "contact_found", "email": "maria.lopez@dryrun.invalid", "verified": True, "name": "Maria Lopez"}], got)
            results = json.loads(applied.get("contact_results") or "[]") if applied else []
            ack_src = io.open(os.path.join(SENDTRACK, "code_command_ack.js"), encoding="utf-8").read()
            ack_lib = ack_src[:ack_src.index("// Node body")]
            ack = subprocess.run(["node", "-e", ack_lib + "\nprocess.stdout.write(contactLines({contact_results: JSON.parse(require('fs').readFileSync(0,'utf8'))}).join('\\n'));"],
                                 input=json.dumps(results), stdout=subprocess.PIPE, encoding="utf-8").stdout
            print("     ack: " + ack.strip())
            expect("the acknowledgement says it is queued, with no 'not drafted' caveat", "queued for drafting" in ack
                   and "never drafts" not in ack and "no niche drafts" not in ack, ack)
        else:
            expect("a scored research-site lead exists for CONTACT", False, "none scored >= its floor without a contact")

        # --- 4. Drafting - Research Sites ------------------------------------------------------
        print("\n4. Drafting - Research Sites")
        max_draft = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())
        run_until(IDS["drafting"], "SELECT count(*) FROM leads l JOIN enrichments e ON e.lead_id = l.id JOIN scores s ON s.lead_id = l.id "
                                   "WHERE l.status = 'contact_found' AND lead_niche(e.raw_extraction, l.source) = 'research_site' "
                                   "AND s.fit_score >= 50;", 5, "drafting")
        drafts = rows(DRY, "SELECT d.id, d.lead_id, d.status, d.approved_by, d.variant, d.subject, d.body, d.hold_reason, c.verified, "
                           "l.country FROM drafts d JOIN leads l ON l.id = d.lead_id LEFT JOIN contacts c ON c.lead_id = d.lead_id "
                           "WHERE d.id > %d ORDER BY d.id" % max_draft)
        for d in drafts:
            print("     #%(id)s lead %(lead_id)s %(country)s %(status)s/%(approved_by)s %(variant)s | %(subject)s" % d)
        expect("site drafts were written", len(drafts) >= 1, len(drafts))
        expect("every one carries the site wording and the site sign-off, never the CRO's",
               all("a software engineer who builds AI assistants for clinical research websites" in d["body"]
                   and d["body"].endswith(SITE_SIGN) and "CRO websites" not in d["body"] and "Founder" not in d["body"] for d in drafts))
        expect("every draft with a verified address and a clock approved itself (approved_by auto)",
               all(d["status"] == "approved" and d["approved_by"] == "auto" for d in drafts if d["verified"] and "+" not in d["variant"]))
        if contacted:
            mine = [d for d in drafts if d["lead_id"] == contacted["id"]]
            expect("the CONTACT lead was drafted, greeted by name, auto-approved",
                   mine and mine[0]["body"].startswith("Hi Maria,") and mine[0]["status"] == "approved", mine[:1])
        ts = rows(DRY, "SELECT status FROM leads WHERE id = %d" % TRIALSITES_SITE)
        expect("the Trialsites site (lead %d) is not drafted -- no niche until its licence is settled" % TRIALSITES_SITE,
               ts == [{"status": "contact_found"}] and not [d for d in drafts if d["lead_id"] == TRIALSITES_SITE], ts)

        # --- 5. the trigger -------------------------------------------------------------------
        print("\n5. The trigger (migration 025) on the table")
        probe_floor = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())
        ok_d = [d for d in drafts if d["status"] == "approved"]
        if ok_d:
            d0 = ok_d[0]
            cro_signed = d0["body"].replace("Software Engineer, Amitrix Labs", "Founder, Amitrix Labs").replace("'", "''")
            r = psql(DRY, "INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by) VALUES "
                          "(%d, 'email', 'template/A/company/unnamed', 's', '%s', 'approved', 'auto') "
                          "RETURNING status || ' | ' || coalesce(hold_reason, '');" % (d0["lead_id"], cro_signed)).strip()
            expect("a site draft signed as the CRO cannot approve itself", "not signed as niche research_site signs" in r, r)
            psql(DRY, "UPDATE niches SET auto_approve = false WHERE code = 'research_site';")
            r = psql(DRY, "INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by) VALUES "
                          "(%d, 'email', 'template/A/company/unnamed', 's', '%s', 'approved', 'auto') "
                          "RETURNING status || ' | ' || coalesce(hold_reason, '');" % (d0["lead_id"], d0["body"].replace("'", "''"))).strip()
            expect("with the niche's switch off, its draft cannot approve itself", "does not auto-approve" in r, r)
            psql(DRY, "UPDATE niches SET auto_approve = true WHERE code = 'research_site';")
        r = psql(DRY, "INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by) VALUES "
                      "(%d, 'email', 'template/A/company/unnamed', 's', E'Hi there,\\n\\n%s\\n\\n%s', 'approved', 'auto') "
                      "RETURNING status || ' | ' || coalesce(hold_reason, '');" % (TRIALSITES_SITE, "x " * 30, SITE_SIGN.replace("\n", "\\n"))).strip()
        expect("a draft for a lead of no niche (Trialsites) cannot approve itself", "no niche drafts this lead" in r, r)
        cro = rows(DRY, "SELECT d.lead_id, d.variant, d.subject, d.body FROM drafts d JOIN outreach_log o ON o.draft_id = d.id "
                        "WHERE d.variant LIKE 'template/%%' ORDER BY d.id DESC LIMIT 1")
        if cro:
            c = cro[0]
            r = psql(DRY, "INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by) VALUES "
                          "(%d, 'email', '%s', '%s', '%s', 'approved', 'auto') RETURNING status || ' | ' || coalesce(approved_by, '') "
                          "|| ' | ' || coalesce(hold_reason, '');" % (c["lead_id"], c["variant"], c["subject"].replace("'", "''"),
                                                                      c["body"].replace("'", "''"))).strip()
            expect("a CRO template draft still approves itself exactly as before (only the address or blocklist could hold it)",
                   r.startswith("approved | auto") or "no verified deliverable address" in r, r)
        # Every probe row above goes again, so none of them can be sent below.
        psql(DRY, "DELETE FROM drafts WHERE id > %d;" % probe_floor)

        # --- 6. Send --------------------------------------------------------------------------
        print("\n6. Send (dry variant, SMTP into the sink)")
        psql(DRY, "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' WHERE channel = 'email' AND status = 'approved' "
                  "AND id <= %d;" % max_draft)
        psql(DRY, "UPDATE mailbox_sent SET seen_in_sent_folder = true WHERE source = 'workflow' AND NOT seen_in_sent_folder;")
        run = execute(IDS["send"])
        dec = out_items(run, "Decide Send")[0]
        site_ids = {d["id"] for d in drafts if d["status"] == "approved"}
        excluded = {x["draft_id"]: x["reason"] for x in dec["excluded"]}
        print("     decision: %s / %s; excluded: %s" % (dec.get("reason"), dec.get("detail"), {k: v for k, v in excluded.items() if k in site_ids}))
        expect("no site draft is refused for its signature or shape",
               not [v for k, v in excluded.items() if k in site_ids and (v.startswith("stale-signature") or v.startswith("not-an-email"))],
               excluded)
        expect("Send sends one: %s" % dec.get("detail"), dec["send"] is True, dec.get("reason"))
        sent_id = dec["payload"]["draft_id"] if dec.get("payload") else None
        log = sink_log()
        first = None
        if log and sent_id:
            msg = sink_message(log[0]["file"])
            body = msg.get_content().replace("\r\n", "\n").strip()
            first = [x for x in drafts if x["id"] == sent_id][0]
            expect("what went out is the site draft, byte for byte, to a @dryrun.invalid address",
                   body == first["body"].strip() and all(r.strip("<>").endswith("@dryrun.invalid") for r in log[0]["rcpt_to"]),
                   body[:300])
            expect("From is the sender; the sign-off is the niche's", msg["From"] == "Abdullah Amir <abdullah@amitrixlabs.com>"
                   and body.endswith(SITE_SIGN), msg["From"])
            print("\n     --- sent to the sink (draft %d, lead %d) ---\n     Subject: %s\n%s\n     ---"
                  % (sent_id, first["lead_id"], msg["Subject"], "\n".join("     " + ln for ln in body.split("\n"))))

        # --- 7. Follow-Ups - Research Sites -------------------------------------------------------
        print("\n7. Follow-Ups - Research Sites, a week later")
        if first:
            psql(DRY, "UPDATE outreach_log SET sent_at = sent_at - interval '7 days' WHERE draft_id = %d;\n"
                      "UPDATE drafts SET created_at = created_at - interval '7 days' WHERE id = %d;" % (sent_id, sent_id))
            fu_max = int(psql(DRY, "SELECT max(id) FROM drafts;").strip())
            execute(IDS["followups"])
            fus = rows(DRY, "SELECT id, lead_id, status, approved_by, variant, subject, body FROM drafts WHERE id > %d "
                            "AND variant LIKE 'follow-up-%%' ORDER BY id" % fu_max)
            mine = [f for f in fus if f["lead_id"] == first["lead_id"]]
            arm = "A" if first["lead_id"] % 2 == 0 else "B"
            expect("follow-up #1 for the sent site, the lead's own arm, auto-approved",
                   len(mine) == 1 and mine[0]["variant"] == "follow-up-1/template/" + arm and mine[0]["status"] == "approved"
                   and mine[0]["approved_by"] == "auto", mine)
            if mine:
                expect("its wording is the site follow-up's",
                       "and I help make sure those visits turn into conversations." in mine[0]["body"]
                       and (SITE_SIGN + "\n\nOn ") in mine[0]["body"], mine[0]["body"][:400])
                psql(DRY, "UPDATE drafts SET status = 'rejected', reject_reason = 'bad draft' WHERE status = 'approved' "
                          "AND channel = 'email' AND id <> %d;" % mine[0]["id"])
                dec = out_items(execute(IDS["send"]), "Decide Send")[0]
                expect("Send sends the follow-up: %s" % dec.get("detail"), dec["send"] is True and dec["payload"]["draft_id"] == mine[0]["id"],
                       dec.get("reason"))
                log = sink_log()
                if len(log) == 2:
                    msg = sink_message(log[1]["file"])
                    expect("threaded: Re: the first subject", msg["Subject"] == "Re: " + first["subject"], msg["Subject"])

        # --- 8. the Digest --------------------------------------------------------------------
        print("\n8. The Daily Digest, per niche and arm")
        dg = load(os.path.join(WF, "daily-digest.json"))
        nodes = {n["name"]: n for n in dg["nodes"]}
        q = nodes["Load Digest"]["parameters"]["query"].replace("$1", "''").replace("$2::int", "0")
        res = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", DRY, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"],
                             input="SELECT row_to_json(t) FROM (" + q.strip().rstrip(";") + ") t;", stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding="utf-8")
        row = json.loads(res.stdout[res.stdout.index("{"):]) if not res.returncode else {}
        bjs = nodes["Build Digest"]["parameters"]["jsCode"]
        src = bjs[:bjs.index("// Node body")] + "\nconst r = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
            "process.stdout.write(buildDigest(r, { digest_hour: 0 }).text);\n"
        f = os.path.join(SCRATCH, "digest.js")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(src)
        text = subprocess.run(["node", f], input=json.dumps(row), stdout=subprocess.PIPE, encoding="utf-8").stdout
        block = text[text.find("BY NICHE AND A/B ARM"):text.find("-- Nova Scout")]
        print("\n".join("     " + ln for ln in block.strip().splitlines()))
        expect("the digest counts the research-site niche per arm", "research_site / arm " in block, block[:400])
        expect("... and the CRO niche per arm", "cro / arm A:" in block and "cro / arm B:" in block, block[:400])

        # --- 9. CRO byte-identical ---------------------------------------------------------------
        print("\n9. Every sent CRO template email re-renders byte for byte")
        # Against the LIVE database, read-only: the copy's addresses are rewritten
        # to @dryrun.invalid, so its inputs are not the ones the emails were written from.
        out = subprocess.run([sys.executable, os.path.join(REPO, "n8n", "drafting", "rerender_sent_cro.py"), "--db", REAL],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        print("     " + out.stdout.strip().replace("\n", "\n     "))
        expect("0 different", out.returncode == 0 and "different: 0" in out.stdout, out.stdout[-500:])
    finally:
        for pid in SINK_PIDS:
            sh(["docker", "exec", N8N, "kill", pid], check=False)
        if not a.keep:
            psql("n8n", "DELETE FROM workflow_entity WHERE id IN (%s);" % ",".join("'%s'" % i for i in IDS.values()))
            psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);" % (DRY, BASE))
            print("\n(scratch databases dropped, test workflows deleted from n8n, sink stopped)")
    # The live pipeline keeps working during a run (Send, Drafting), so its counts
    # move on their own; what must hold is that nothing of the dry run is in it.
    after = fingerprint(REAL)
    print("  live before: %s\n  live after:  %s" % (json.dumps(before), json.dumps(after)))
    leaked = rows(REAL, "SELECT (SELECT count(*) FROM leads WHERE source IN ('sites-ctgov', 'sites-overture')) AS site_leads, "
                        "(SELECT count(*) FROM contacts WHERE email LIKE '%@dryrun.invalid') AS dry_contacts, "
                        "(SELECT count(*) FROM operator_commands WHERE message_id LIKE '%dryrun%') AS dry_commands")[0]
    left = rows("n8n", "SELECT count(*) AS n FROM workflow_entity WHERE id IN (%s)" % ",".join("'%s'" % i for i in IDS.values()))[0]["n"]
    expect("nothing of the dry run reached the live database, and its test workflows are gone",
           leaked == {"site_leads": before_site_leads, "dry_contacts": 0, "dry_commands": 0} and (a.keep or left == 0),
           "%s, %d test workflows left" % (leaked, left))
    failed = [r for r in RESULTS if not r[1]]
    print("\n%d passed, %d failed" % (len(RESULTS) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

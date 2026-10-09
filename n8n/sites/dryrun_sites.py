#!/usr/bin/env python3
"""Dry run of the sites-and-SMOs lead source, end to end, against a scratch database.

    python n8n/sites/dryrun_sites.py                 # 20 candidates, then clean up
    python n8n/sites/dryrun_sites.py --candidates 20 --keep

What runs, all through n8n, all the SHIPPED workflows (only the Postgres
credential, the id and the disabled schedule trigger differ):

  1. Trialsites (Workflow 1b): the real weekly harvest against the live AIMR API,
     then the website lookup on the first N candidates -- real Claude Haiku 5.5
     calls with web search, real fetches of the proposed sites.
  2. Enrichment, Scoring, Contact Lookup, Drafting on every lead that produced --
     local qwen3.5:9b, real website fetches, real Sonnet 5.5 composing calls.

Against `novascout_dryrun`, a clone of the live database in which every contact
address is rewritten to @dryrun.invalid, with migration 019 applied to the clone
only (twice: the second run must change nothing) and its rules proven there. The
site claims are switched ACTIVE in the clone -- the operator's first step after
publishing -- and stay UNCONFIRMED, so every site draft must come out held.

What never runs: Send, Follow-Ups, Mailbox Watch, the digest, the reply path --
nothing that can send mail. The harness refuses to start if any variant holds a
mail node, and blanks every SMTP / mailbox variable in its own environment by
plain assignment first (the standing rule; `setdefault` would do nothing here).

Costs real money: ~$0.02 per lookup and ~$0.03 per drafted lead. It prints both.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

# --- The standing safety rule, before anything else is imported or run --------
# Plain assignment, never setdefault: this shell exports the live .env.
for _k in list(os.environ):
    if re.match(r"NOVASCOUT_(SMTP|MAILBOX|IMAP)", _k):
        os.environ[_k] = ""
_left = [k for k in os.environ if re.match(r"NOVASCOUT_(SMTP|MAILBOX|IMAP)", k) and os.environ[k]]
if _left:
    raise SystemExit("refusing to run: mail variables still set: %s" % _left)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "n8n", "drafting"))
import cache_report  # noqa: E402  (reads Sonnet usage out of n8n's execution data)

PG = os.environ.get("NOVASCOUT_PG_CONTAINER", "nova-scout-postgres-1")
N8N = os.environ.get("NOVASCOUT_N8N_CONTAINER", "nova-scout-n8n-1")
PGUSER = os.environ.get("POSTGRES_USER", "novascout")
REAL = "novascout"
DRY = "novascout_dryrun"            # what the novascoutPgDry01 credential points at
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
MIGRATION = os.path.join(ROOT, "postgres", "migrations", "019_sites_and_smos.sql")
SCRATCH = tempfile.mkdtemp(prefix="novascout-sites-dryrun-")
STAMP = time.strftime("%H%M%S")

# stage -> (generator dir, env var for its output path, dry-run workflow id)
STAGES = [
    ("trialsites", "sites", "SITES_OUT", "drysites0001"),
    ("enrichment", "enrichment", "ENRICHMENT_OUT", "dryenrich0001"),
    ("scoring", "scoring", "SCORING_OUT", "dryscore0001"),
    ("contacts", "contacts", "CONTACTS_OUT", "drycontact0001"),
    ("drafting", "drafting", "DRAFTING_OUT", "drydraft0001"),
]
MAIL_NODE_TYPES = ("n8n-nodes-base.emailSend", "n8n-nodes-base.emailReadImap", "n8n-nodes-base.imap")


def sh(cmd, stdin=None, check=True, timeout=None):
    p = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if check and p.returncode:
        raise SystemExit("command failed: %s\n%s" % (" ".join(cmd), (p.stderr or p.stdout)[-3000:]))
    return p


def psql(db, sql, check=True):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-v", "ON_ERROR_STOP=1",
               "-X", "-q", "-t", "-A", "-f", "-"], stdin=sql, check=check)


def q(db, sql):
    return psql(db, sql).stdout.strip()


def qjson(db, sql):
    out = q(db, sql)
    return json.loads(out) if out else None


# ---------------------------------------------------------------------------
# What the live database must still say afterwards
# ---------------------------------------------------------------------------

def live_marks():
    """The things this dry run could change in the live database if anything were
    mis-pointed. Not a whole-database fingerprint: the live pipeline is running
    (Send, Follow-Ups, a Friday ingestion shard) and changes other rows on its own."""
    return qjson(REAL, """SELECT json_build_object(
      'site_candidates_table', to_regclass('site_candidates')::text,
      'trialsites_leads', (SELECT count(*) FROM leads WHERE source = 'trialsites'),
      'claims_md5', (SELECT md5(string_agg(code || active::text || confirmed::text || coalesce(body, ''), '|' ORDER BY code))
                       FROM claims_library),
      'settings_keys', (SELECT string_agg(key, ',' ORDER BY key) FROM settings),
      'max_lead_id', (SELECT max(id) FROM leads),
      'enrichments', (SELECT count(*) FROM enrichments),
      'scores', (SELECT count(*) FROM scores),
      'contacts', (SELECT count(*) FROM contacts));""")


# ---------------------------------------------------------------------------
# The scratch database
# ---------------------------------------------------------------------------

def clone():
    print("  cloning %s -> %s ..." % (REAL, DRY))
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE); CREATE DATABASE %s;" % (DRY, DRY))
    dump = sh(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "-d", REAL, "--no-owner"]).stdout
    psql(DRY, dump)
    psql(DRY, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
              "WHERE coalesce(email, '') <> '';")
    leaked = q(DRY, "SELECT count(*) FROM contacts WHERE coalesce(email,'') <> '' "
                    "AND email NOT LIKE '%@dryrun.invalid';")
    assert leaked == "0", "%s contacts in the clone still hold a real address" % leaked
    print("  clone ready; every contact address is @dryrun.invalid")


CHECKS = []


def expect(label, ok, detail=""):
    CHECKS.append((label, bool(ok)))
    print("    [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))


def refused(sql, needle):
    """True when the statement fails and the error names `needle`."""
    p = psql(DRY, "BEGIN;\n" + sql + "\nROLLBACK;", check=False)
    return p.returncode != 0 and needle in (p.stderr or "")


def migrate_and_prove():
    print("\n  migration 019 on the clone")
    with io.open(MIGRATION, encoding="utf-8") as fh:
        mig = fh.read()
    psql(DRY, mig)
    snap = ("SELECT json_build_object('claims', (SELECT count(*) FROM claims_library), "
            "'site_claims', (SELECT count(*) FROM claims_library WHERE company_types && ARRAY['site','SMO']), "
            "'cro_claims', (SELECT count(*) FROM claims_library WHERE company_types = ARRAY['CRO']), "
            "'settings', (SELECT json_object_agg(key, value ORDER BY key) FROM settings), "
            "'claims_md5', (SELECT md5(string_agg(code || active::text || confirmed::text || "
            "array_to_string(company_types, ','), '|' ORDER BY code)) FROM claims_library));")
    first = qjson(DRY, snap)
    psql(DRY, mig)
    second = qjson(DRY, snap)
    expect("re-running 019 changes nothing", first == second, "%r vs %r" % (first, second))
    expect("18 site/SMO lines seeded, every one inactive and unconfirmed",
           q(DRY, "SELECT count(*) FILTER (WHERE NOT active AND NOT confirmed) || '/' || count(*) "
                  "FROM claims_library WHERE company_types && ARRAY['site','SMO'];") == "18/18")
    expect("every pre-existing line is a CRO line", first["cro_claims"] == first["claims"] - 18)
    expect("the monthly cap is seeded at $20 and all of it is left",
           q(DRY, "SELECT site_lookup_budget_left();") == "20")
    expect("a site line saying \"two CROs\" is refused",
           refused("UPDATE claims_library SET body = 'It''s live at two CROs, in Türkiye and Mexico.' "
                   "WHERE code = 'PR-SITE';", "claims_site_lines_never_cro_proof"))
    expect("a site line calling Vertex a CRO is refused",
           refused("UPDATE claims_library SET body = 'It''s live at Vertex Clinical Research, a CRO in Mexico.' "
                   "WHERE code = 'PR-SITE';", "claims_site_lines_never_cro_proof"))
    expect("a site line saying \"CRO websites\" is refused",
           refused("UPDATE claims_library SET body = 'Most CRO websites lose inquiries at night.' "
                   "WHERE code = 'ANG-SITE-SILENT';", "claims_site_lines_never_cro_proof"))
    expect("a company type outside CRO/site/SMO is refused",
           refused("UPDATE claims_library SET company_types = ARRAY['hospital'] WHERE code = 'D1';",
                   "claims_company_types_vocabulary"))
    expect("a confirmed line widened to sites is un-confirmed",
           q(DRY, "BEGIN; UPDATE claims_library SET company_types = ARRAY['CRO','site'] WHERE code = 'D1' "
                  "RETURNING confirmed; ROLLBACK;").splitlines()[0] == "f")
    expect("a malformed cap is refused",
           refused("UPDATE settings SET value = 'lots' WHERE key = 'site_lookup_monthly_cap_usd';",
                   "settings_site_lookup_cap_shape"))
    expect("no cap configured means $0 to spend, never unlimited",
           q(DRY, "BEGIN; DELETE FROM settings WHERE key = 'site_lookup_monthly_cap_usd'; "
                  "SELECT site_lookup_budget_left(); ROLLBACK;").splitlines()[0] == "0")
    expect("spend this month comes off the cap",
           q(DRY, "BEGIN; INSERT INTO site_candidates (location_id, canonical_name, country, site_tier) "
                  "VALUES (-1, 'x', 'Mexico', 'A'); INSERT INTO site_lookup_log (location_id, model, cost_usd, "
                  "outcome) VALUES (-1, 'm', 5.5, 'no-match'); SELECT site_lookup_budget_left(); ROLLBACK;")
           .splitlines()[0] in ("14.5", "14.500000"))
    expect("a candidate with a domain but no confirmed website is refused",
           refused("INSERT INTO site_candidates (location_id, canonical_name, country, site_tier, domain) "
                   "VALUES (-2, 'x', 'Mexico', 'A', 'guessed.example');", "site_candidates_domain_only_when_confirmed"))
    expect("a 'resolved' candidate with no domain is refused",
           refused("INSERT INTO site_candidates (location_id, canonical_name, country, site_tier, lookup_status) "
                   "VALUES (-3, 'x', 'Mexico', 'A', 'resolved');", "site_candidates_domain_only_when_confirmed"))
    expect("tier C is refused",
           refused("INSERT INTO site_candidates (location_id, canonical_name, country, site_tier) "
                   "VALUES (-4, 'x', 'Mexico', 'C');", "site_tier"))
    expect("lead_company_type: enrichment wins, then the source, then CRO",
           q(DRY, "SELECT lead_company_type('{\"company_type\":\"SMO\"}', 'ichgcp') || ',' || "
                  "lead_company_type('{\"company_type\":\"unclear\"}', 'trialsites') || ',' || "
                  "lead_company_type('{\"company_type\":\"other\"}', NULL) || ',' || "
                  "lead_company_type(NULL, NULL);") == "SMO,site,CRO,CRO")

    # The operator's first step after publishing Drafting and Follow-Ups: switch
    # the site lines on. They stay unconfirmed, so every site draft is held.
    n = q(DRY, "WITH u AS (UPDATE claims_library SET active = true WHERE company_types && ARRAY['site','SMO'] "
               "RETURNING 1) SELECT count(*) FROM u;")
    print("  site lines switched ACTIVE in the clone (still unconfirmed): %s" % n)


# ---------------------------------------------------------------------------
# The workflow variants
# ---------------------------------------------------------------------------

def build_variants():
    ids = []
    for label, gen, env_var, wid in STAGES:
        out = os.path.join(SCRATCH, label + ".json")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env[env_var] = out
        p = subprocess.run([sys.executable, os.path.join(ROOT, "n8n", gen, "build_workflow.py")], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        if p.returncode:
            raise SystemExit("%s build failed:\n%s" % (label, p.stderr[-3000:]))
        with io.open(out, encoding="utf-8") as fh:
            wf = json.load(fh)
        repointed = 0
        for n in wf["nodes"]:
            if n["type"] in MAIL_NODE_TYPES:
                raise SystemExit("refusing to run: %s holds a mail node (%s)" % (label, n["name"]))
            url = json.dumps(n.get("parameters", {}).get("url", ""))
            if "smtp-send" in url or "imap-health" in url:
                raise SystemExit("refusing to run: %s calls a mail sidecar (%s)" % (label, n["name"]))
            if n.get("credentials", {}).get("postgres"):
                n["credentials"] = PG_DRY
                repointed += 1
            if n["type"] == "n8n-nodes-base.scheduleTrigger":
                n["disabled"] = True
        assert repointed, "%s has no Postgres node to repoint" % label
        stray = [n["name"] for n in wf["nodes"] if "novascoutPg01" in json.dumps(n.get("credentials", {}))]
        assert not stray, "%s still points at the live database: %s" % (label, stray)
        wf["id"] = wid
        wf["name"] = "DRYRUN - %s (scratch DB)" % label
        wf["active"] = False
        # Keep this run's execution data, so Sonnet usage can be read back.
        wf.setdefault("settings", {})["saveDataSuccessExecution"] = "all"
        path = "/tmp/ns_sites_dry_%s_%s.json" % (label, STAMP)
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > " + path], stdin=json.dumps([wf], ensure_ascii=False))
        sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=" + path])
        ids.append(wid)
        print("  imported %-11s as %-15s (%d nodes, %d Postgres node(s) on the scratch DB, no mail node)"
              % (label, wid, len(wf["nodes"]), repointed))
    return ids


def execute(wid, timeout=2400):
    t0 = time.time()
    log = "/tmp/ns_sites_dry_exec_%s.log" % wid
    p = sh(["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "-e", "N8N_RUNNERS_ENABLED=false", N8N,
            "sh", "-c", "n8n execute --id=%s > %s 2>&1; echo rc=$?; tail -c 600 %s" % (wid, log, log)],
           check=False, timeout=timeout)
    rc = re.search(r"rc=(\d+)", p.stdout or "")
    ok = bool(rc) and rc.group(1) == "0"
    if not ok:
        print("    execution of %s did not succeed:\n%s" % (wid, (p.stdout or p.stderr)[-800:]))
    return ok, time.time() - t0


def last_execution_id():
    got = cache_report.psql("n8n", "SELECT coalesce(max(id), 0) FROM execution_entity;")
    return int(got[0]) if got else 0


def sonnet_usage(wid, after_id):
    rows = cache_report.psql("n8n", """
        SELECT e.id, w.name, e."startedAt", d.data
          FROM execution_entity e JOIN execution_data d ON d."executionId" = e.id
          JOIN workflow_entity w ON w.id = e."workflowId"
         WHERE w.id = '%s' AND e.id > %d ORDER BY e.id""" % (wid, after_id))
    out = []
    for line in rows:
        parts = line.split("\x1f")
        if len(parts) >= 4:
            out.extend(cache_report.usage_blocks("\x1f".join(parts[3:])))
    return out


def count(sql):
    return int(q(DRY, sql) or 0)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def harvest_only():
    """The weekly harvest alone, with this month's lookup budget at $0: proves
    the one-request-at-a-time harvest, and that a spent cap stops every lookup.
    Costs nothing -- no Claude call can be made."""
    print("\n  harvest only, with the lookup cap set to $0 in the clone")
    psql(DRY, "UPDATE settings SET value = '0' WHERE key = 'site_lookup_monthly_cap_usd';")
    expect("with the cap at $0 nothing is left to spend", q(DRY, "SELECT site_lookup_budget_left();") == "0")
    ok, secs = execute("drysites0001")
    h = qjson(DRY, "SELECT row_to_json(x) FROM (SELECT responses, rows_seen, eligible_new, chosen, "
                   "(stats->>'failed_responses')::int AS failed, stats->'rejected' AS rejected, "
                   "stats->'unmapped_countries' AS unmapped FROM site_harvest_log ORDER BY id DESC LIMIT 1) x;") or {}
    print("     execution: %s in %.0fs" % ("ok" if ok else "FAILED", secs))
    print("     harvest: %s" % json.dumps(h))
    rate = h.get("responses", 0) / (secs / 60.0) if secs else 0
    print("     pages read: %d in %.1f min = %.1f a minute" % (h.get("responses", 0), secs / 60.0, rate))
    expect("the harvest ran and logged exactly one row", count("SELECT count(*) FROM site_harvest_log;") == 1)
    expect("no Trialsites request failed", h.get("failed") == 0, "failed responses: %r" % h.get("failed"))
    expect("well under Trialsites' 60 requests a minute", rate < 60, "%.1f a minute" % rate)
    expect("at most 200 new candidates chosen", 0 < h.get("chosen", 0) <= 200, "chosen %r" % h.get("chosen"))
    expect("with the cap spent, no lookup was made and every candidate is still pending",
           count("SELECT count(*) FROM site_lookup_log;") == 0
           and count("SELECT count(*) FROM site_candidates WHERE lookup_status <> 'pending';") == 0)


def harvest_ran(ok, harvest):
    """Did the harvest actually happen?

    A harvest that never ran makes every check in report() pass on no rows at
    all: 26/26 on an empty database proves nothing. Hit 2026-10-10, when a stale
    `n8n execute` held the task broker's port 5690, the harvest failed, and the
    run still reported success. A separate function so the decision is testable
    without a 13-minute harvest -- n8n/sites/test_drift_guards.py exercises it.
    """
    return bool(ok) and bool(harvest) and bool(harvest.get("chosen"))


def run(n_candidates):
    lookups = -(-n_candidates // 10)          # Load Lookup Batch takes 10 a tick
    print("\n  1. Trialsites: the weekly harvest, then %d lookup batch(es)" % lookups)
    for k in range(lookups):
        ok, secs = execute("drysites0001")
        h = qjson(DRY, "SELECT row_to_json(x) FROM (SELECT responses, rows_seen, eligible_new, chosen, "
                       "stats->'rejected' AS rejected, stats->'failed_responses' AS failed, "
                       "stats->'unmapped_countries' AS unmapped FROM site_harvest_log ORDER BY id DESC LIMIT 1) x;")
        looked = count("SELECT count(*) FROM site_candidates WHERE lookup_status <> 'pending';")
        print("     execution %d: %s in %.0fs; candidates looked up so far: %d" % (k + 1, "ok" if ok else "FAILED", secs, looked))
        if k == 0:
            print("     harvest: %s" % json.dumps(h))
            if not harvest_ran(ok, h):
                raise SystemExit(
                    "refusing to go on: the harvest did not run (execution %s, chosen=%s).\n"
                    "Every check after this would pass on an empty database.\n"
                    "If the n8n log says the task broker's port is in use, a previous\n"
                    "`n8n execute` is still alive in the container -- kill that PID and re-run."
                    % ("ok" if ok else "FAILED", (h or {}).get("chosen")))
    queues = [
        ("2. Enrichment", "dryenrich0001", "SELECT count(*) FROM leads WHERE source='trialsites' AND status='ingested';", 4),
        ("3. Scoring", "dryscore0001", "SELECT count(*) FROM leads WHERE source='trialsites' AND status='enriched';", 3),
        ("4. Contact Lookup", "drycontact0001",
         "SELECT count(*) FROM leads l JOIN scores s ON s.lead_id = l.id WHERE l.source='trialsites' "
         "AND l.status='scored' AND s.fit_score >= 50 AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.lead_id=l.id) "
         "AND NOT EXISTS (SELECT 1 FROM contact_attempts a WHERE a.lead_id=l.id);", 3),
        ("5. Drafting", "drydraft0001", "SELECT count(*) FROM leads WHERE status='contact_found';", 4),
    ]
    spend = {"sonnet": 0.0, "calls": 0}
    for label, wid, waiting_sql, max_runs in queues:
        waiting = count(waiting_sql)
        print("\n  %s: %d waiting" % (label, waiting))
        runs = 0
        while waiting and runs < max_runs:
            before = last_execution_id()
            ok, secs = execute(wid)
            runs += 1
            if wid == "drydraft0001":
                us = sonnet_usage(wid, before)
                c = sum(cache_report.cost(u) for u in us)
                spend["sonnet"] += c
                spend["calls"] += len(us)
                print("     run %d: %s in %.0fs, %d Sonnet call(s), $%.4f" % (runs, "ok" if ok else "FAILED", secs, len(us), c))
            else:
                print("     run %d: %s in %.0fs" % (runs, "ok" if ok else "FAILED", secs))
            left = count(waiting_sql)
            if left == waiting and not ok:
                break
            waiting = left
        if wid == "drycontact0001":
            # Step 0's rule, proven on what Contact Lookup actually wrote: a site
            # lead's address is one its own website published, on its own domain.
            expect("every address found for a Trialsites lead is on that lead's own domain",
                   count("SELECT count(*) FROM contacts c JOIN leads l ON l.id = c.lead_id "
                         "WHERE l.source = 'trialsites' AND coalesce(c.email, '') <> '' "
                         "AND NOT (split_part(lower(c.email), '@', 2) = l.domain "
                         "OR split_part(lower(c.email), '@', 2) LIKE '%.' || l.domain);") == 0)
            print("     addresses found on the leads' own websites: %d"
                  % count("SELECT count(*) FROM contacts c JOIN leads l ON l.id = c.lead_id "
                          "WHERE l.source = 'trialsites' AND coalesce(c.email, '') <> '';"))
            # Then made undeliverable, like every other address in the clone.
            psql(DRY, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
                      "WHERE coalesce(email, '') <> '' AND email NOT LIKE '%@dryrun.invalid';")
    return spend


def report(spend, n_candidates):
    print("\n  FUNNEL (scratch database)")
    for line in q(DRY, "SELECT lookup_status || ': ' || count(*) FROM site_candidates "
                       "WHERE lookup_status <> 'pending' GROUP BY lookup_status ORDER BY count(*) DESC;").splitlines():
        print("    candidates " + line)
    print("    candidates still pending (next weeks' lookups): %d"
          % count("SELECT count(*) FROM site_candidates WHERE lookup_status = 'pending';"))
    lk = qjson(DRY, "SELECT json_build_object('n', count(*), 'usd', coalesce(sum(cost_usd), 0), "
                    "'searches', coalesce(sum(web_search_requests), 0), "
                    "'resolved', (SELECT count(*) FROM site_candidates WHERE lookup_status IN ('resolved','duplicate'))) "
                    "FROM site_lookup_log;")
    per_resolved = float(lk["usd"]) / lk["resolved"] if lk["resolved"] else 0
    print("    website lookups: %d, $%.4f, %d web searches; $%.4f per lookup, $%.4f per resolved site (%d resolved)"
          % (lk["n"], float(lk["usd"]), lk["searches"], float(lk["usd"]) / max(1, lk["n"]), per_resolved, lk["resolved"]))
    print("    budget left this month in the clone: $%s" % q(DRY, "SELECT site_lookup_budget_left();"))
    for line in q(DRY, "SELECT status || ' / ' || kind || ': ' || n FROM (SELECT l.status, "
                       "coalesce(e.raw_extraction->>'company_type', '-') AS kind, count(*) AS n "
                       "FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id WHERE l.source = 'trialsites' "
                       "GROUP BY 1, 2) x ORDER BY 1;").splitlines():
        print("    leads " + line)
    print("\n  every Trialsites lead:")
    for line in q(DRY, """SELECT l.id || ' ' || rpad(left(l.company_name, 38), 38) || ' ' || rpad(l.country, 12) || ' '
                            || rpad(l.domain, 28) || ' ' || l.status || ' type=' || coalesce(e.raw_extraction->>'company_type','-')
                            || ' fit=' || coalesce(s.fit_score::text, '-')
                            || coalesce(' dq=' || s.disqualify_reason, '')
                            || ' contact=' || CASE WHEN c.email IS NOT NULL THEN 'email' WHEN c.linkedin_url IS NOT NULL
                                                   THEN 'linkedin' WHEN a.lead_id IS NOT NULL THEN 'none(' || a.last_outcome || ')'
                                                   ELSE '-' END
                          FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id LEFT JOIN scores s ON s.lead_id = l.id
                          LEFT JOIN contacts c ON c.lead_id = l.id LEFT JOIN contact_attempts a ON a.lead_id = l.id
                         WHERE l.source = 'trialsites' ORDER BY l.id;""").splitlines():
        print("    " + line[:260])
    print("\n  drafts written for Trialsites leads:")
    rows = qjson(DRY, """SELECT coalesce(json_agg(x ORDER BY x.id), '[]') FROM (
        SELECT d.id, d.lead_id, d.channel, d.status, d.approved_by, d.variant, d.hold_reason, d.subject, d.body,
               lead_company_type(e.raw_extraction, l.source) AS company_type, l.company_name, l.country
          FROM drafts d JOIN leads l ON l.id = d.lead_id LEFT JOIN enrichments e ON e.lead_id = l.id
         WHERE l.source = 'trialsites') x;""") or []
    for r in rows:
        print("    draft %s lead %s %-8s %-8s %-5s approved_by=%s variant=%s\n      hold: %s"
              % (r["id"], r["lead_id"], r["channel"], r["status"], r["company_type"], r["approved_by"],
                 r["variant"], (r["hold_reason"] or "")[:240]))
    emails = [r for r in rows if r["channel"] == "email" and not str(r["variant"]).startswith(("low-context", "no-send-clock"))]
    shown = []
    for kind in ("site", "SMO"):
        pick = next((r for r in emails if r["company_type"] == kind), None)
        if pick:
            shown.append(pick)
    for r in shown:
        print("\n  SAMPLE %s DRAFT -- draft %s, lead %s (%s, %s), %s, hold: %s\n  Subject: %s\n%s"
              % (r["company_type"].upper(), r["id"], r["lead_id"], r["company_name"], r["country"], r["status"],
                 r["hold_reason"], r["subject"], "\n".join("    " + ln for ln in (r["body"] or "").splitlines())))

    print("\n  CHECKS ON THE RESULT")
    # Positive first: every check below this one is satisfiable by an empty
    # database, so the run has to prove it did something before they mean
    # anything (2026-10-10 -- see the harvest guard in run()).
    _harvested = count("SELECT count(*) FROM site_candidates;")
    _looked = count("SELECT count(*) FROM site_candidates WHERE lookup_status <> 'pending';")
    expect("the harvest actually wrote candidates, so the checks below are not vacuous",
           _harvested > 0, "%d candidates" % _harvested)
    expect("the website lookup actually ran", _looked > 0, "%d looked up" % _looked)
    expect("at most %d candidates were looked up" % n_candidates, _looked <= n_candidates)
    expect("every resolved candidate has its own lead, written source='trialsites'",
           count("SELECT count(*) FROM site_candidates c LEFT JOIN leads l ON l.id = c.lead_id "
                 "WHERE c.lookup_status = 'resolved' AND (l.id IS NULL OR l.source <> 'trialsites');") == 0)
    expect("no candidate holds a domain it did not confirm",
           count("SELECT count(*) FROM site_candidates WHERE domain IS NOT NULL "
                 "AND lookup_status NOT IN ('resolved','duplicate');") == 0)
    expect("no site or SMO draft was approved (every site line is unconfirmed)",
           not [r for r in rows if r["status"] == "approved"])
    expect("every site/SMO email draft that reached the model is held as unconfirmed-claim",
           all("unconfirmed-claim" in (r["hold_reason"] or "") for r in emails) if emails else True)
    expect("no site/SMO draft says \"CRO websites\", \"two CROs\" or calls Vertex a CRO",
           not [r for r in rows if re.search(r"\bCROs?['’]?s? websites?\b|\btwo CROs\b|Vertex[^.]{0,60}\bCRO\b",
                                             (r["subject"] or "") + " " + (r["body"] or ""), re.I)])
    expect("no site/SMO draft uses a CRO-only claim code",
           not [r for r in rows if re.search(r"(^|[/.])(D1|D2|ANG-HOURS|ANG-SILENT|ANG-SPEED|ANG-STAKES|ANG-TIME|BEN-247|"
                                             r"BEN-BOOK|BEN-BRIEF|BEN-CAPTURE|BEN-DECK|BEN-FIT|BEN-ROUTE|BEN-SEE|PR-BOTH|"
                                             r"PR-MX|PR-TR|A1|A2)([.+]|$)", r["variant"] or "")])
    expect("no investigator data reached the scratch database",
           count("SELECT count(*) FROM information_schema.columns WHERE table_name = 'site_candidates' "
                 "AND column_name ~ 'investigator';") == 0)
    print("\n  real spend: website lookups $%.4f + Sonnet composing $%.4f (%d calls) = $%.4f"
          % (float(lk["usd"]), spend["sonnet"], spend["calls"], float(lk["usd"]) + spend["sonnet"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="leave the scratch database and workflows behind")
    ap.add_argument("--harvest-only", action="store_true",
                    help="run only the weekly harvest, with the lookup cap at $0 (no API spend)")
    args = ap.parse_args()
    print("\nSites & SMOs dry run -- scratch database, real APIs, no mail path")
    before = live_marks()
    print("  live marks before: %s" % json.dumps(before))
    wids = []
    error = None
    try:
        clone()
        migrate_and_prove()
        wids = build_variants()
        if args.harvest_only:
            harvest_only()
        else:
            spend = run(args.candidates)
            report(spend, args.candidates)
    except BaseException as e:      # recorded so the summary below never masks it
        error = e
        raise
    finally:
        after = live_marks()
        print("\n  live marks after:  %s" % json.dumps(after))
        same = {k: before[k] == after[k] for k in ("site_candidates_table", "trialsites_leads", "claims_md5", "settings_keys")}
        expect("the live database gained no site table, no Trialsites lead, no claim or setting change", all(same.values()),
               json.dumps(same))
        if args.keep:
            print("  kept: database %s, workflows %s" % (DRY, ", ".join(wids)))
        else:
            psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % DRY, check=False)
            for wid in [s[3] for s in STAGES]:
                psql("n8n", "DELETE FROM workflow_entity WHERE id = '%s';" % wid, check=False)
            print("  cleaned up: scratch database dropped, dry-run workflows deleted")
        failed = [c for c in CHECKS if not c[1]]
        print("\n  %d/%d checks passed%s\n" % (len(CHECKS) - len(failed), len(CHECKS),
                                             "" if not failed else ": FAILED " + "; ".join(c[0] for c in failed)))
        if error is not None:
            print("  the run stopped on an error (traceback below); the checks above are partial\n")
        elif failed:
            sys.exit(1)


if __name__ == "__main__":
    main()

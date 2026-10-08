#!/usr/bin/env python3
"""Measure prompt caching on REAL Sonnet 5.5 calls, against a scratch database.

Section 3 / Section 4 want the cache writes and reads from real responses, and
the cost per draft before and after. This runs the SHIPPED Drafting workflow --
the real composing call, the real claim check, the real repair loop -- through
n8n, twice, against a throwaway clone of the live database.

WHY A SCRATCH DATABASE, when the calls are real anyway. A drafting run can
auto-approve an email, and Send is published and ticks every 10 minutes: a
measurement run against the live database could put a real email in front of a
real prospect. So the clone gets every contact address rewritten to
`@dryrun.invalid`, and the live database is never written to at all. The cache
is a property of the API, not of the database, so the measurement is unaffected.

WHAT THE TWO RUNS SHOW. Run 1 starts cold: a batch's calls are concurrent (n8n's
HTTP node starts them all before awaiting any), so each writes the cache and
none reads a sibling's write. Run 2 starts inside the 5-minute window with the
same leads re-queued, so every call reads the entry run 1 left. That pair is the
before/after: the same prompts, the same leads, one paying base input and one
paying the cache-read rate.

    python n8n/drafting/cache_measure.py --leads 658,700,642
    python n8n/drafting/cache_measure.py --leads 658,700,642 --keep

Costs real money -- about $0.10-$0.15 per lead per run. It prints the total.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import cache_report  # noqa: E402  (the usage reader; one source of truth for prices)

PG = os.environ.get("NOVASCOUT_PG_CONTAINER", "nova-scout-postgres-1")
N8N = os.environ.get("NOVASCOUT_N8N_CONTAINER", "nova-scout-n8n-1")
PGUSER = os.environ.get("POSTGRES_USER", "novascout")
REAL = "novascout"
# The scratch database the `novascoutPgDry01` credential already points at --
# the same one n8n/sendtrack/dryrun/ uses, so no new credential is needed.
DRY = "novascout_dryrun"
WID = "cachemeasure1"
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
SCRATCH = tempfile.mkdtemp(prefix="novascout-cache-measure-")


def sh(cmd, stdin=None, check=True):
    p = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("command failed: %s\n%s" % (" ".join(cmd), (p.stderr or p.stdout)[-3000:]))
    return p.stdout


def psql(db, sql, check=True):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-v", "ON_ERROR_STOP=1",
               "-t", "-A", "-f", "-"], stdin=sql, check=check)


def clone():
    """A throwaway clone of the live database, with every contact address made
    undeliverable. The live database is only ever READ here (pg_dump)."""
    print("  cloning %s -> %s ..." % (REAL, DRY))
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE); CREATE DATABASE %s;" % (DRY, DRY))
    dump = os.path.join(SCRATCH, "live.sql")
    with io.open(dump, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sh(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "-d", REAL, "--no-owner"]))
    with io.open(dump, encoding="utf-8") as fh:
        psql(DRY, fh.read())
    psql(DRY, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
              "WHERE coalesce(email, '') <> '';")
    leaked = psql(DRY, "SELECT count(*) FROM contacts WHERE coalesce(email,'') <> '' "
                       "AND email NOT LIKE '%@dryrun.invalid';").strip()
    assert leaked == "0", "%s contacts in the clone still hold a real address" % leaked
    print("  clone ready; every contact address is @dryrun.invalid")


def build_variant():
    """The shipped Drafting workflow, with only its Postgres credential and id
    changed, so what is measured is the workflow that ships."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               DRAFTING_OUT=os.path.join(SCRATCH, "drafting.json"))
    p = subprocess.run([sys.executable, os.path.join(HERE, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("drafting build failed:\n" + p.stderr[-3000:])
    with io.open(os.path.join(SCRATCH, "drafting.json"), encoding="utf-8") as fh:
        wf = json.load(fh)

    changed = []
    for n in wf["nodes"]:
        if n.get("credentials", {}).get("postgres"):
            n["credentials"] = PG_DRY
            changed.append(n["name"])
        # The schedule trigger must not register; this runs by CLI only.
        if n["type"] == "n8n-nodes-base.scheduleTrigger":
            n["disabled"] = True
    assert changed, "no Postgres node found to repoint at the scratch database"
    wf["id"] = WID
    wf["name"] = "MEASURE - Drafting prompt cache (scratch DB)"
    wf["active"] = False
    blob = json.dumps([wf], ensure_ascii=False)
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_cache_measure.json"], stdin=blob)
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_cache_measure.json"])
    print("  imported %s (%d nodes); Postgres repointed on: %s"
          % (WID, len(wf["nodes"]), ", ".join(changed)))


def queue(leads):
    """Put the chosen leads back at `contact_found` so Drafting picks them up.
    Scratch database only."""
    ids = ",".join(str(i) for i in leads)
    psql(DRY, "UPDATE leads SET status = 'contact_found' WHERE id IN (%s);" % ids)
    n = psql(DRY, "SELECT count(*) FROM leads WHERE status='contact_found';").strip()
    print("  queued %s lead(s) at contact_found" % n)


def execute():
    t0 = time.time()
    sh(["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "-e", "N8N_RUNNERS_ENABLED=false",
        N8N, "n8n", "execute", "--id=" + WID], check=False)
    return time.time() - t0


def usage_for_execution(after_id):
    rows = cache_report.psql("n8n", """
        SELECT e.id, w.name, e."startedAt", d.data
          FROM execution_entity e
          JOIN execution_data d ON d."executionId" = e.id
          JOIN workflow_entity w ON w.id = e."workflowId"
         WHERE w.id = '%s' AND e.id > %d
         ORDER BY e.id
    """ % (WID, after_id))
    out = []
    for line in rows:
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        for u in cache_report.usage_blocks("\x1f".join(parts[3:])):
            u["_execution"] = parts[0]
            out.append(u)
    return out


def last_execution_id():
    got = cache_report.psql("n8n", "SELECT coalesce(max(id), 0) FROM execution_entity;")
    return int(got[0]) if got else 0


def report(label, us):
    if not us:
        print("  %s: no Sonnet calls (nothing was queued, or every lead took a no-model branch)" % label)
        return None
    by = {}
    for u in us:
        by.setdefault(u["_site"], []).append(u)
    tot = {"cost": 0.0, "unc": 0.0, "w": 0, "r": 0, "n": 0}
    print("\n  %s" % label)
    print("    %-13s %5s %8s %8s %8s %8s %10s %10s %7s"
          % ("call site", "calls", "in", "write", "read", "out", "cost", "uncached", "saved"))
    for site in sorted(by):
        s = by[site]
        c = sum(cache_report.cost(u) for u in s)
        unc = sum(cache_report.uncached_cost(u) for u in s)
        print("    %-13s %5d %8d %8d %8d %8d %10.4f %10.4f %6.1f%%"
              % (site, len(s), sum(u["input_tokens"] for u in s),
                 sum(u["cache_creation_input_tokens"] for u in s),
                 sum(u["cache_read_input_tokens"] for u in s),
                 sum(u["output_tokens"] for u in s), c, unc,
                 (1 - c / unc) * 100 if unc else 0))
        tot["cost"] += c; tot["unc"] += unc; tot["n"] += len(s)
        tot["w"] += sum(u["cache_creation_input_tokens"] for u in s)
        tot["r"] += sum(u["cache_read_input_tokens"] for u in s)
    print("    %-13s %5d %8s %8d %8d %8s %10.4f %10.4f %6.1f%%"
          % ("TOTAL", tot["n"], "", tot["w"], tot["r"], "", tot["cost"], tot["unc"],
             (1 - tot["cost"] / tot["unc"]) * 100 if tot["unc"] else 0))
    return tot


def drafts_written():
    return psql(DRY, "SELECT d.lead_id || ' ' || d.channel || ' ' || d.status || ' ' || "
                     "coalesce(d.approved_by,'-') || ' ' || d.variant FROM drafts d "
                     "WHERE d.created_at > now() - interval '30 minutes' ORDER BY d.id;").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leads", required=True, help="comma-separated lead ids to draft")
    ap.add_argument("--runs", type=int, default=2, help="how many runs (default 2: cold then warm)")
    ap.add_argument("--keep", action="store_true", help="leave the scratch database and workflow behind")
    args = ap.parse_args()
    leads = [int(x) for x in args.leads.split(",") if x.strip()]

    print("\nPrompt-cache measurement -- real Sonnet 5.5 calls, scratch database")
    print("  leads: %r" % leads)
    try:
        clone()
        build_variant()
        totals = []
        for k in range(1, args.runs + 1):
            queue(leads)
            before = last_execution_id()
            print("  run %d: executing ..." % k)
            secs = execute()
            us = usage_for_execution(before)
            print("  run %d finished in %.0fs, %d Sonnet call(s)" % (k, secs, len(us)))
            totals.append(report("RUN %d (%s)" % (k, "cold -- expect writes" if k == 1
                                                  else "warm -- expect reads"), us))
        print("\n  drafts written in the scratch database:")
        for ln in (drafts_written() or "(none)").splitlines():
            print("    " + ln)
        spent = sum(t["cost"] for t in totals if t)
        print("\n  real spend on this measurement: $%.4f" % spent)
        a, b = (totals + [None, None])[:2]
        if a and b and a["n"] and b["n"]:
            print("  cost per call: cold $%.5f -> warm $%.5f (%.1f%% lower)"
                  % (a["cost"] / a["n"], b["cost"] / b["n"],
                     (1 - (b["cost"] / b["n"]) / (a["cost"] / a["n"])) * 100))
    finally:
        if args.keep:
            print("\n  kept: database %s, workflow %s" % (DRY, WID))
        else:
            psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % DRY, check=False)
            psql("n8n", "DELETE FROM workflow_entity WHERE id = '%s';" % WID, check=False)
            print("\n  cleaned up: scratch database dropped, measurement workflow deleted")
        print("  the live database was never written to.\n")


if __name__ == "__main__":
    main()

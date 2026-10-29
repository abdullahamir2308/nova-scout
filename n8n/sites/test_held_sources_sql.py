"""Migration 026 run for real -- a niche that holds a source never auto-approves its drafts.

    python n8n/sites/test_held_sources_sql.py

Operator brief 2026-10-30: hold Overture-sourced drafts until the precision
audit (docs/research_site_niche.md section 7.5) measures that source at 90 % or
more. A scratch database is created from the live schema (pg_dump
--schema-only of novascout) and the live niches rows, fixtures are written as
the database stood before the migration, then 026 is applied -- twice, because
a migration is re-runnable -- and the trigger is driven the way Drafting,
CONTACT and the lift recipe drive it. The real novascout database is only
read; the scratch database is dropped afterwards.
"""
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MIGRATION = os.path.join(REPO, "postgres", "migrations", "026_held_sources.sql")
MIG025 = os.path.join(REPO, "postgres", "migrations", "025_niches.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_heldsrc_test_%d" % os.getpid()
HELD = "db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)"


def psql(db, sql, check=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout.strip()


def dump(*args):
    p = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--no-owner"] + list(args) + ["novascout"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("pg_dump failed:\n" + p.stderr[-1500:])
    return p.stdout


def body(title):
    return ("Hi there,\n\nWe have yet to be properly introduced, but I'm Abdullah Amir, a software engineer who builds "
            "AI assistants for clinical research websites.\n\nA sentence about the site that is long enough.\n\n"
            "Cheers!\nAbdullah Amir\n%s" % title)


SITE = body("Software Engineer, Amitrix Labs").replace("'", "''")
CRO = body("Founder, Amitrix Labs").replace("'", "''")

FIXTURES = """
DELETE FROM settings WHERE key = 'auto_approve_email';
INSERT INTO leads (id, domain, company_name, country, source, status) OVERRIDING SYSTEM VALUE VALUES
  (1, 'ov-approved.example', 'Ov Approved Research', 'USA', 'sites-overture', 'drafted'),
  (2, 'ct-approved.example', 'Ct Approved Research', 'USA', 'sites-ctgov',    'drafted'),
  (3, 'ov-sent.example',     'Ov Sent Research',     'USA', 'sites-overture', 'sent'),
  (4, 'ov-new.example',      'Ov New Research',      'USA', 'sites-overture', 'contact_found'),
  (5, 'ct-new.example',      'Ct New Research',      'USA', 'sites-ctgov',    'contact_found'),
  (6, 'cro-new.example',     'Cro New',              'USA', 'ichgcp',         'contact_found'),
  (7, 'ov-noaddr.example',   'Ov Noaddr Research',   'USA', 'sites-overture', 'contact_found'),
  (8, 'ov-human.example',    'Ov Human Research',    'USA', 'sites-overture', 'contact_found');
INSERT INTO enrichments (lead_id, raw_extraction) VALUES
  (1, '{"company_type": "site"}'), (2, '{"company_type": "site"}'), (3, '{"company_type": "site"}'),
  (4, '{"company_type": "site"}'), (5, '{"company_type": "site"}'), (6, '{"company_type": "CRO"}'),
  (7, '{"company_type": "site"}'), (8, '{"company_type": "site"}');
INSERT INTO contacts (lead_id, email, verified) VALUES
  (1, 'info@ov-approved.example', true), (2, 'info@ct-approved.example', true), (3, 'info@ov-sent.example', true),
  (4, 'info@ov-new.example', true), (5, 'info@ct-new.example', true), (6, 'info@cro-new.example', true),
  (7, NULL, false), (8, 'info@ov-human.example', true);
-- As the database stood before 026: three auto-approved site drafts, one of them sent.
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, approved_by) OVERRIDING SYSTEM VALUE VALUES
  (11, 1, 'email', 'template/A/areas/role-inbox', 's', '%(site)s', 'approved', 'auto'),
  (12, 2, 'email', 'template/A/trials/role-inbox', 's', '%(site)s', 'approved', 'auto'),
  (13, 3, 'email', 'template/B/areas/role-inbox', 's', '%(site)s', 'approved', 'auto');
UPDATE drafts SET status = 'sent' WHERE id = 13;
""" % {"site": SITE}

AFTER = """
-- Drafting writes approved/auto; the trigger decides.
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, approved_by) OVERRIDING SYSTEM VALUE VALUES
  (21, 4, 'email', 'template/A/areas/role-inbox', 's', '%(site)s', 'approved', 'auto'),
  (22, 5, 'email', 'template/B/trials/role-inbox', 's', '%(site)s', 'approved', 'auto'),
  (23, 6, 'email', 'template/A/areas/role-inbox', 's', '%(cro)s', 'approved', 'auto'),
  (24, 7, 'email', 'template/B/areas/role-inbox', 's', '%(site)s', 'approved', 'auto'),
  (25, 8, 'email', 'template/A/areas/role-inbox', 's', '%(site)s', 'pending', NULL);
-- A person approves one held Overture draft by hand (APPROVE <code>): that is the operator's call.
UPDATE drafts SET status = 'approved', approved_by = NULL WHERE id = 25;
""" % {"site": SITE, "cro": CRO}

LIFT = """
BEGIN;
UPDATE niches SET held_sources = '{}' WHERE code = 'research_site';
UPDATE drafts SET status = 'approved', approved_by = 'auto'
 WHERE status = 'pending' AND hold_reason LIKE 'db-guard: source sites-overture is held%%';
COMMIT;
"""

ROWS = "SELECT id, status, coalesce(approved_by, '-'), coalesce(hold_reason, '-') FROM drafts ORDER BY id;"


def rows(db):
    out = {}
    for line in psql(db, ROWS).splitlines():
        i, st, by, hr = line.split("|", 3)
        out[int(i)] = (st, by, hr)
    return out


def main():
    passed = failed = 0

    def check(name, ok, got=None):
        nonlocal passed, failed
        if ok:
            passed += 1
            print("  [PASS] " + name)
        else:
            failed += 1
            print("  [FAIL] " + name + ("" if got is None else "  -- got %r" % (got,)))

    with open(MIGRATION, encoding="utf-8") as fh:
        mig = fh.read()
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    try:
        schema = dump("--schema-only")
        psql(SCRATCH, schema)
        niches = dump("--data-only", "-t", "niches")
        psql(SCRATCH, re.sub(r"(?m)^SELECT pg_catalog\.set_config.*$", "", niches))
        # The live schema carries 026 once it is applied; start from 025's state either way: no column, and
        # 025's own trigger function.
        with open(MIG025, encoding="utf-8") as fh:
            fn025 = re.search(r"CREATE OR REPLACE FUNCTION drafts_approval_rules\(\).*?\n\$fn\$;", fh.read(), re.S).group(0)
        psql(SCRATCH, "ALTER TABLE niches DROP COLUMN IF EXISTS held_sources;\n" + fn025)
        psql(SCRATCH, FIXTURES)

        psql(SCRATCH, mig)
        r = rows(SCRATCH)
        check("026 holds the approved, unsent Overture draft", r[11] == ("pending", "-", HELD), r[11])
        check("026 leaves the ClinicalTrials.gov draft approved", r[12][:2] == ("approved", "auto"), r[12])
        check("026 never touches a sent draft", r[13][0] == "sent", r[13])
        check("research_site holds sites-overture",
              psql(SCRATCH, "SELECT held_sources FROM niches WHERE code = 'research_site';") == "{sites-overture}")
        check("cro holds nothing", psql(SCRATCH, "SELECT held_sources FROM niches WHERE code = 'cro';") == "{}")

        psql(SCRATCH, mig)
        check("026 runs twice and changes nothing more", rows(SCRATCH) == r)

        psql(SCRATCH, AFTER)
        r = rows(SCRATCH)
        check("a new Overture draft is held by the trigger, with the lift recipe's reason", r[21] == ("pending", "-", HELD), r[21])
        check("a new ClinicalTrials.gov draft still auto-approves", r[22] == ("approved", "auto", "-"), r[22])
        check("a CRO draft still auto-approves", r[23] == ("approved", "auto", "-"), r[23])
        check("a held source with no address names both problems",
              r[24][0] == "pending" and r[24][2].startswith(HELD[:-1]) and "no verified deliverable address" in r[24][2],
              r[24])
        check("a person can still approve a held Overture draft", r[25][:2] == ("approved", "human"), r[25])

        psql(SCRATCH, LIFT)
        r = rows(SCRATCH)
        check("lifting the hold re-approves the held drafts through the guards",
              r[11][:2] == ("approved", "auto") and r[21][:2] == ("approved", "auto"), (r[11], r[21]))
        check("... but not one that another guard holds", r[24][0] == "pending" and
              r[24][2] == "db-guard: no verified deliverable address", r[24])
        check("... and nothing else moved", r[12][:2] == ("approved", "auto") and r[13][0] == "sent"
              and r[22][:2] == ("approved", "auto") and r[23][:2] == ("approved", "auto"))
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check=False)
    print("%d passed, %d failed" % (passed, failed))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

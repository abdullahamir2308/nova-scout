"""Prove the dashboard's role can read the metrics views and nothing else, and write nothing.

    python dashboard/check_reader_role.py

Logs in as nova_dashboard exactly as Grafana does -- over the compose network, password from .env, SCRAM --
from a throwaway container of the pinned postgres image (the one docker-compose names), and tries every way
out of the views: every table and view in `public`, the private schema and its functions, the functions
in `public` (the writing ones included), server files and programs, the other databases, and every kind of
write -- first in the role's default read-only transaction, then again after switching that default off, so
it is privileges that stop each write, not only the read-only flag. Each probe has an expected outcome;
the script exits 0 only if every one matches. Reads NOVASCOUT_DASHBOARD_DB_PASSWORD from .env (passed to
the container by name, never on a command line). Changes nothing: every probe either fails or reads.
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(ROOT, ".env"))
NETWORK = "nova-scout_default"
PG = "nova-scout-postgres-1"
ROLE = "nova_dashboard"


def load_env(key):
    with io.open(ENV_FILE, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln.startswith(key + "="):
                v = ln.split("=", 1)[1].strip()
                return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v
    return ""


def postgres_image():
    with io.open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        m = re.search(r"^  postgres:\n\s+image: (\S+)", fh.read(), re.M)
    return m.group(1)


ENV = dict(os.environ, PGPASSWORD=load_env("NOVASCOUT_DASHBOARD_DB_PASSWORD"))
IMAGE = postgres_image()


def as_reader(script, db="novascout"):
    """Run a psql script as nova_dashboard over the network; stdout and stderr together."""
    r = subprocess.run(["docker", "run", "--rm", "-i", "--network", NETWORK, "-e", "PGPASSWORD", IMAGE,
                        "sh", "-c", "psql -h postgres -U %s -d %s -X -v ON_ERROR_STOP=0 -At -f - 2>&1" % (ROLE, db)],
                       input=script, capture_output=True, text=True, env=ENV, encoding="utf-8")
    return r.stdout + r.stderr


def superuser(sql):
    r = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", "novascout", "-At", "-f", "-"],
                       input=sql, capture_output=True, text=True, encoding="utf-8")
    return [ln for ln in r.stdout.splitlines() if ln]


# ---------------------------------------------------------------------------------------------------------
# The probes: (name, statement, expected) -- expected is a regex the output must match
# ---------------------------------------------------------------------------------------------------------
DENIED_SCHEMA_PUBLIC = r"permission denied for schema public"
DENIED_SCHEMA_PRIVATE = r"permission denied for schema metrics_private"
READ_ONLY = r"cannot execute \w+.* in a read-only transaction"

public_tables = superuser("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1;")
metric_views = superuser("SELECT table_name FROM information_schema.views WHERE table_schema = 'metrics' ORDER BY 1;")
public_functions = superuser("SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' FROM pg_proc p "
                             "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
                             "AND p.prokind = 'f' AND p.prorettype <> 'trigger'::regtype ORDER BY 1;")
private_relations = superuser("SELECT table_name FROM information_schema.tables WHERE table_schema = 'metrics_private';")
private_functions = superuser("SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                              "WHERE n.nspname = 'metrics_private' ORDER BY 1;")


def call_args(sig):
    """A call with typed NULL arguments for a signature like f(a text, b bigint)."""
    name, args = sig.split("(", 1)
    args = args.rstrip(")")
    if not args:
        return "public.%s()" % name
    types = [a.strip().split(" ", 1)[1] if " " in a.strip() else a.strip() for a in args.split(",")]
    return "public.%s(%s)" % (name, ", ".join("NULL::%s" % t for t in types))


PROBES = []
# Reads that must work: every metrics view
for v in metric_views:
    PROBES.append(("read metrics.%s" % v, "SELECT count(*) >= 0 FROM metrics.%s;" % v, r"^t$"))
# Reads that must not: every table and view in public, the private schema, the public functions
for t in public_tables:
    PROBES.append(("read public.%s" % t, "SELECT 1 FROM public.%s LIMIT 1;" % t, DENIED_SCHEMA_PUBLIC))
for t in private_relations:
    PROBES.append(("read metrics_private.%s" % t, "SELECT * FROM metrics_private.%s;" % t, DENIED_SCHEMA_PRIVATE))
for f in private_functions:
    PROBES.append(("call metrics_private.%s()" % f, "SELECT metrics_private.%s();" % f, DENIED_SCHEMA_PRIVATE))
for f in public_functions:
    PROBES.append(("call %s" % f, "SELECT %s;" % call_args(f), DENIED_SCHEMA_PUBLIC))
PROBES += [
    ("a table by search_path", "SET search_path = public; SELECT 1 FROM leads LIMIT 1;",
     r'relation "leads" does not exist|permission denied'),
    ("pg_authid (password hashes)", "SELECT rolpassword FROM pg_authid LIMIT 1;", r"permission denied for table pg_authid"),
    ("pg_statistic (column samples)", "SELECT 1 FROM pg_statistic LIMIT 1;", r"permission denied for table pg_statistic"),
    ("pg_stats rows of public tables", "SELECT count(*) FROM pg_stats WHERE schemaname = 'public';", r"^0$"),
    ("server file read", "SELECT pg_read_file('/etc/passwd');", r"permission denied for function pg_read_file"),
    ("server directory list", "SELECT pg_ls_dir('.');", r"permission denied for function pg_ls_dir"),
    ("COPY to a program", "COPY (SELECT 1) TO PROGRAM 'id';", r"permission denied to COPY to or from an external program|must be superuser"),
    ("COPY to a server file", "COPY (SELECT 1) TO '/tmp/nova_probe';", r"permission denied to COPY to a file|must be superuser"),
    ("become the superuser", "SET ROLE novascout;", r'permission denied to set role "novascout"'),
    ("others' queries in pg_stat_activity",
     "SELECT count(*) FROM pg_stat_activity WHERE usename = 'novascout' AND query <> '<insufficient privilege>';",
     r"^0$"),
]
# Writes, in the role's default (read-only) transaction
WRITES = [
    ("insert into a view", "INSERT INTO metrics.niche_list VALUES ('x', 'x', true);"),
    ("update a view", "UPDATE metrics.niche_list SET label = 'x';"),
    ("delete from a view", "DELETE FROM metrics.niche_list;"),
    ("create a table in metrics", "CREATE TABLE metrics.nova_probe (i int);"),
    ("create a table in public", "CREATE TABLE public.nova_probe (i int);"),
    ("create a temp table", "CREATE TEMP TABLE nova_probe (i int);"),
    ("create a schema", "CREATE SCHEMA nova_probe;"),
    ("create a large object", "SELECT lo_create(0);"),
    ("grant on a view", "GRANT SELECT ON metrics.funnel TO PUBLIC;"),
    ("make itself superuser", "ALTER ROLE nova_dashboard SUPERUSER;"),
]
for name, sql in WRITES:
    PROBES.append(("read-only txn: " + name, sql,
                   READ_ONLY + "|permission denied|must be superuser|cannot (?:insert into|update|delete from) view"))
# The same writes with the read-only default switched off: privileges alone must stop each one. A write to
# a view is refused either for privilege or because none of these views is updatable -- Postgres checks the
# second first; either way nothing is written.
VIEW_REFUSED = r'permission denied for view niche_list|cannot (?:insert into|update|delete from) view "niche_list"'
PRIV = {
    "insert into a view": VIEW_REFUSED,
    "update a view": VIEW_REFUSED,
    "delete from a view": VIEW_REFUSED,
    "create a table in metrics": r"permission denied for schema metrics",
    "create a table in public": r"permission denied for schema public",
    "create a temp table": r'permission denied to create temporary tables in database "novascout"',
    "create a schema": r'permission denied for database novascout',
    "create a large object": r"permission denied for function lo_create",
    # not the owner: Postgres grants nothing and says so (a warning, no error) -- checked below by the ACL
    "grant on a view": r"no privileges were granted for \"funnel\"",
    "make itself superuser": r"permission denied to alter role|must be superuser",
}
for name, sql in WRITES:
    PROBES.append(("read-write txn: " + name, "SET default_transaction_read_only = off; " + sql, PRIV[name]))


def run_probes():
    script = []
    for i, (name, sql, _) in enumerate(PROBES):
        # each probe in its own session-state reset; markers delimit the output
        script.append("\\echo ==P%d==" % i)
        script.append("RESET ALL;")
        script.append(sql)
    out = as_reader("\n".join(script) + "\n")
    parts = re.split(r"^==P(\d+)==$", out, flags=re.M)
    got = {}
    for k in range(1, len(parts), 2):
        got[int(parts[k])] = parts[k + 1].strip()
    if "password authentication failed" in out or not got:
        sys.exit("could not log in as %s: %s" % (ROLE, out[:400]))
    return got


def main():
    got = run_probes()
    ok = bad = 0
    for i, (name, sql, expect) in enumerate(PROBES):
        text = got.get(i, "<no output>")
        # psql prints `psql:<stdin>:N: ERROR:  ...`; keep the message
        msg = " | ".join(ln.split("ERROR:", 1)[-1].strip() for ln in text.splitlines() if ln.strip()
                         and not ln.startswith("RESET") and not ln.startswith("SET")) or "<empty>"
        if re.search(expect, msg, re.M):
            ok += 1
            print("ok    %-52s %s" % (name, msg[:110]))
        else:
            bad += 1
            print("FAIL  %-52s expected /%s/\n      got: %s" % (name, expect, msg[:300]))
    # nothing the probes tried left a trace
    after = superuser("SELECT (SELECT count(*) FROM pg_largeobject_metadata WHERE lomowner = '%s'::regrole) || ',' || "
                      "has_table_privilege('public', 'metrics.funnel', 'SELECT') || ',' || "
                      "(SELECT rolsuper FROM pg_roles WHERE rolname = '%s') || ',' || "
                      "(SELECT count(*) FROM pg_class WHERE relname = 'nova_probe');" % (ROLE, ROLE))
    if after == ["0,false,false,0"]:
        ok += 1
        print("ok    %-52s %s" % ("afterwards: no large object, grant, superuser, table", after[0]))
    else:
        bad += 1
        print("FAIL  %-52s %s" % ("afterwards: no large object, grant, superuser, table", after))
    # the other databases: connecting at all must be refused
    for db in superuser("SELECT datname FROM pg_database WHERE datname <> 'novascout' AND NOT datistemplate ORDER BY 1;") + ["template1"]:
        out = as_reader("SELECT 1;\n", db=db)
        if re.search(r'permission denied for database "%s"|not currently accepting connections|User does not have CONNECT privilege' % db, out):
            ok += 1
            print("ok    %-52s %s" % ("connect to database %s" % db, out.strip().splitlines()[-1][:110]))
        else:
            bad += 1
            print("FAIL  %-52s got: %s" % ("connect to database %s" % db, out.strip()[:300]))
    print("\n%d probes as expected, %d not" % (ok, bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

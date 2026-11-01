"""Give the dashboard's database role its password, from the repo's .env.

    python dashboard/provision_reader.py

Migration 033 creates the role `nova_dashboard` with LOGIN and no password -- a NULL password never
authenticates, so the role is inert until this runs. This reads NOVASCOUT_DASHBOARD_DB_PASSWORD from .env
(the same value docker-compose hands the grafana service, which interpolates it into its data source) and
sets it as a SCRAM-SHA-256 verifier computed here, so the plain password is never sent to Postgres, never
appears on a command line and never lands in a log. Re-run it after changing the value in .env, then
`docker compose restart grafana`.

Only NOVASCOUT_DASHBOARD_DB_PASSWORD is read from .env. Nothing else changes: the role's grants are the
migration's.
"""
import base64
import hashlib
import hmac
import io
import os
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", ".env"))
PG = "nova-scout-postgres-1"
DB = os.environ.get("NOVASCOUT_DB", "novascout")
ROLE = "nova_dashboard"
KEY = "NOVASCOUT_DASHBOARD_DB_PASSWORD"
MIN_LENGTH = 24
# Postgres' own default for a SCRAM verifier (scram_iterations, PG 16).
ITERATIONS = 4096


def load_env(path, key):
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k.strip() != key:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            return v
    return ""


def scram_verifier(password):
    """RFC 5802 / RFC 7677, in the form pg_authid.rolpassword stores: what `\\password` sends."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return "SCRAM-SHA-256$%d:%s$%s:%s" % (ITERATIONS, b64(salt), b64(stored_key), b64(server_key))


def psql(sql):
    return subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", DB, "-v", "ON_ERROR_STOP=1",
                           "-qAt"], input=sql, capture_output=True, text=True)


password = load_env(ENV_FILE, KEY)
if len(password) < MIN_LENGTH:
    sys.exit("not provisioning -- %s in %s is missing or shorter than %d characters"
             % (KEY, os.path.abspath(ENV_FILE), MIN_LENGTH))
if any(ch in password for ch in "$'\"\\ "):
    # docker-compose and Grafana's provisioning both interpolate `$`; keep the value to safe characters.
    sys.exit("not provisioning -- %s may not contain $, quotes, backslashes or spaces" % KEY)

r = psql("SELECT 1 FROM pg_roles WHERE rolname = '%s';" % ROLE)
if r.returncode != 0 or r.stdout.strip() != "1":
    sys.exit("role %s does not exist -- apply postgres/migrations/033_dashboard_metrics.sql first. %s"
             % (ROLE, r.stderr.strip()))
r = psql("ALTER ROLE %s PASSWORD '%s';" % (ROLE, scram_verifier(password)))
if r.returncode != 0:
    sys.exit("ALTER ROLE failed: %s" % r.stderr.strip())
r = psql("SELECT rolpassword LIKE 'SCRAM-SHA-256$%%' FROM pg_authid WHERE rolname = '%s';" % ROLE)
print("%s: password set from %s (SCRAM-SHA-256: %s)" % (ROLE, KEY, r.stdout.strip() == "t"))

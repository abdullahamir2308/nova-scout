"""Create the n8n credentials Workflow 6 needs, from the repo's .env.

    python provision_credentials.py                  # all four
    python provision_credentials.py --dry-run-only   # only the two that cannot send

    novascoutSmtp01     SMTP     the outreach mailbox, NOVASCOUT_SMTP_HOST:PORT (STARTTLS)
    novascoutImap01     IMAP     the outreach mailbox, NOVASCOUT_IMAP_HOST (TLS)
    novascoutPgDry01    Postgres novascout_dryrun -- the scratch copy dryrun.py builds
    novascoutSmtpDry01  SMTP     127.0.0.1:2525 inside the n8n container -- dryrun.py's
                                 sink, which captures and never delivers

Secrets never reach stdout or the repo. The import file is written to a private
temp dir, copied into the container, imported, and deleted from both places;
`n8n import:credentials` encrypts `data` with N8N_ENCRYPTION_KEY on the way in.
Re-running replaces the credentials in place (same ids).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
DRY_ONLY = "--dry-run-only" in sys.argv

KEYS = ("NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_MAILBOX_PASSWORD", "NOVASCOUT_IMAP_HOST",
        "NOVASCOUT_IMAP_PORT", "NOVASCOUT_SMTP_HOST", "NOVASCOUT_SMTP_PORT", "POSTGRES_USER")


def load_env(path, keys):
    vals = {}
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in keys:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            vals[k] = v
    return vals


def run(args, capture=True, stdin=None):
    p = subprocess.run(args, stdout=subprocess.PIPE if capture else None, input=stdin,
                       stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("command failed (%d): %s\n%s" % (p.returncode, " ".join(args[:4]), p.stderr[-800:]))
    return p.stdout


env = load_env(ENV_FILE, KEYS)
need = ["POSTGRES_USER"] + ([] if DRY_ONLY else
                            ["NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_MAILBOX_PASSWORD",
                             "NOVASCOUT_IMAP_HOST", "NOVASCOUT_SMTP_HOST", "NOVASCOUT_SMTP_PORT"])
print(".env: " + ", ".join("%s=%s" % (k, "set" if env.get(k) else "MISSING") for k in need))
missing = [k for k in need if not env.get(k)]
if missing:
    raise SystemExit("not provisioning -- add %s to %s" % (", ".join(missing), ENV_FILE))

tmp = tempfile.mkdtemp(prefix="novascout-creds-")
try:
    creds = []

    # The scratch-database credential is the live one with only the database
    # (and id/name) changed, so it cannot drift from how novascoutPg01 connects.
    run(["docker", "exec", N8N, "n8n", "export:credentials", "--id=novascoutPg01", "--decrypted",
         "--output=/tmp/ns_pg_export.json"])
    try:
        exported = json.loads(run(["docker", "exec", N8N, "cat", "/tmp/ns_pg_export.json"]))
    finally:
        run(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_pg_export.json"])
    pg = exported[0] if isinstance(exported, list) else exported
    pg_data = dict(pg["data"])
    pg_data["database"] = "novascout_dryrun"
    creds.append({"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)",
                  "type": "postgres", "data": pg_data})

    creds.append({"id": "novascoutSmtpDry01", "name": "SMTP - dry-run sink (never delivers)", "type": "smtp",
                  "data": {"user": "dryrun", "password": "dryrun", "host": "127.0.0.1", "port": 2525,
                           "secure": False, "disableStartTls": True, "hostName": ""}})

    if not DRY_ONLY:
        # Port 587 is STARTTLS: secure=false and STARTTLS left on. (The user's
        # setting, 2026-09-14; the SMTP pre-flight saw TLSv1.3 negotiated.)
        creds.append({"id": "novascoutSmtp01", "name": "SMTP - outreach mailbox", "type": "smtp",
                      "data": {"user": env["NOVASCOUT_MAILBOX_ADDRESS"],
                               "password": env["NOVASCOUT_MAILBOX_PASSWORD"],
                               "host": env["NOVASCOUT_SMTP_HOST"], "port": int(env["NOVASCOUT_SMTP_PORT"]),
                               "secure": int(env["NOVASCOUT_SMTP_PORT"]) == 465,
                               "disableStartTls": False, "hostName": ""}})
        creds.append({"id": "novascoutImap01", "name": "IMAP - outreach mailbox", "type": "imap",
                      "data": {"user": env["NOVASCOUT_MAILBOX_ADDRESS"],
                               "password": env["NOVASCOUT_MAILBOX_PASSWORD"],
                               "host": env["NOVASCOUT_IMAP_HOST"],
                               "port": int(env.get("NOVASCOUT_IMAP_PORT") or 993),
                               "secure": True, "allowUnauthorizedCerts": False}})

    # Streamed in over stdin and written by the container's own user with umask
    # 077 -- NOT `docker cp`, which writes the file as root: the n8n process
    # (user `node`) can then read it but not delete it, and the secrets outlive
    # the import in /tmp. (That happened on the first run of this script.)
    run(["docker", "exec", "-i", N8N, "sh", "-c", "umask 077 && cat > /tmp/ns_creds_import.json"],
        stdin=json.dumps(creds))
    try:
        out = run(["docker", "exec", N8N, "n8n", "import:credentials", "--input=/tmp/ns_creds_import.json"])
    finally:
        run(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_creds_import.json"])
    print(out.strip().splitlines()[-1] if out.strip() else "(import printed nothing)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

ids = ",".join("'%s'" % c["id"] for c in creds)
print(run(["docker", "exec", PG, "psql", "-U", env["POSTGRES_USER"], "-d", "n8n", "-c",
           "SELECT id, name, type, left(data, 10) AS data_starts, length(data) AS data_len "
           "FROM credentials_entity WHERE id IN (%s) ORDER BY id;" % ids]))

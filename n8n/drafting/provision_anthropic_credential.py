"""Create the n8n credential Workflow 4's Claude Draft node uses, from the repo's .env.

    python provision_anthropic_credential.py

    novascoutAnthropic01   anthropicApi   ANTHROPIC_API_KEY from .env

Skill v3 section 6: drafting runs on the Anthropic API, and the key belongs in
an n8n credential, never in the workflow JSON. Same mechanics as
../sendtrack/provision_credentials.py: the secret never reaches stdout or the
repo; the import file is streamed into the container over stdin, written by the
container's own user with umask 077, imported (n8n encrypts `data` with
N8N_ENCRYPTION_KEY on the way in), and deleted. Re-running replaces the
credential in place (same id) -- the way to rotate the key.

The skill also asks for a key dedicated to Nova Scout with a monthly spend limit
set in the Claude Console. Neither can be set from here.
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
CRED_ID = "novascoutAnthropic01"
CRED_NAME = "Anthropic - Nova Scout drafting"


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


def run(args, stdin=None):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=stdin,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("command failed (%d): %s\n%s" % (p.returncode, " ".join(args[:4]), p.stderr[-800:]))
    return p.stdout


env = load_env(ENV_FILE, ("ANTHROPIC_API_KEY", "POSTGRES_USER"))
key = env.get("ANTHROPIC_API_KEY", "")
print(".env: ANTHROPIC_API_KEY=%s, POSTGRES_USER=%s" % ("set" if key else "MISSING",
                                                       "set" if env.get("POSTGRES_USER") else "MISSING"))
if not key or not env.get("POSTGRES_USER"):
    sys.exit("not provisioning -- add the missing key(s) to %s" % ENV_FILE)
if not key.startswith("sk-ant-"):
    sys.exit("ANTHROPIC_API_KEY in %s does not look like an Anthropic API key (sk-ant-...)" % ENV_FILE)

cred = [{"id": CRED_ID, "name": CRED_NAME, "type": "anthropicApi",
         "data": {"apiKey": key, "url": "https://api.anthropic.com", "header": False}}]
run(["docker", "exec", "-i", N8N, "sh", "-c", "umask 077 && cat > /tmp/ns_anthropic_import.json"],
    stdin=json.dumps(cred))
try:
    out = run(["docker", "exec", N8N, "n8n", "import:credentials", "--input=/tmp/ns_anthropic_import.json"])
finally:
    run(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_anthropic_import.json"])
print(out.strip().splitlines()[-1] if out.strip() else "(import printed nothing)")

print(run(["docker", "exec", PG, "psql", "-U", env["POSTGRES_USER"], "-d", "n8n", "-c",
           "SELECT id, name, type, left(data, 10) AS data_starts, length(data) AS data_len "
           "FROM credentials_entity WHERE id = '%s';" % CRED_ID]))

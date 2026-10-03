"""Write Workflow 6's runtime settings from the repo's .env into Postgres.

    python n8n/sendtrack/sync_settings.py

Mailbox Watch reads the operator's notification address at RUNTIME, from the
novascout `settings` table (migration 008): for the reply notification's To,
and to keep those notifications out of the Section 5 warm-up count. It is not a
build constant, so it never appears in the committed workflow JSON, and not an
n8n environment variable, which n8n 2.x only allows by exposing the whole
container environment to every Code node (Section 9).

Run this after changing NOVASCOUT_OPERATOR_EMAIL in .env. Nothing needs
rebuilding, re-importing or re-publishing: the next message Mailbox Watch
handles reads the new value. Empty or missing removes the row -- replies are
still recorded, but nobody is notified.

Also NOVASCOUT_AUTO_APPROVE_EMAIL (migration 014): `true` or `false` writes
settings.auto_approve_email; unset leaves the row as it is (the migration seeds
it `true` -- on by default). Workflow 4 and Follow-Ups read it on every run, and
the drafts table re-checks it on every auto-approval, so `false` stops
auto-approval from the next draft on, with nothing rebuilt or re-published.

Only NOVASCOUT_OPERATOR_EMAIL, NOVASCOUT_MAILBOX_ADDRESS and
NOVASCOUT_AUTO_APPROVE_EMAIL are read from .env.
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
PG = "nova-scout-postgres-1"
DB = os.environ.get("NOVASCOUT_DB", "novascout")
KEYS = ("NOVASCOUT_OPERATOR_EMAIL", "NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_AUTO_APPROVE_EMAIL")
# The build's rule for an address; migration 008's CHECK enforces the same.
EMAIL_RE = re.compile(r"^[^\s@<>(),;:\"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def load_env(path, keys):
    vals = {}
    if not os.path.isfile(path):
        return vals
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


env = load_env(ENV_FILE, KEYS)
operator = env.get("NOVASCOUT_OPERATOR_EMAIL", "").strip().lower()
mailbox = env.get("NOVASCOUT_MAILBOX_ADDRESS", "").strip().lower()
auto = env.get("NOVASCOUT_AUTO_APPROVE_EMAIL", "").strip().lower()
if auto not in ("", "true", "false"):
    sys.exit("not syncing -- NOVASCOUT_AUTO_APPROVE_EMAIL in %s must be true or false, not %r"
             % (os.path.abspath(ENV_FILE), auto))

if operator:
    if not EMAIL_RE.match(operator):
        sys.exit("not syncing -- NOVASCOUT_OPERATOR_EMAIL in %s is not one email address"
                 % os.path.abspath(ENV_FILE))
    if operator == mailbox:
        sys.exit("not syncing -- NOVASCOUT_OPERATOR_EMAIL is the outreach mailbox itself. Section 9: the reply "
                 "notification goes to 'the operator's own inbox (not the outreach mailbox)'.")
    sql = ("INSERT INTO settings (key, value) VALUES ('operator_email', :'op')\n"
           "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()\n"
           " WHERE settings.value IS DISTINCT FROM EXCLUDED.value;\n")
else:
    sql = "DELETE FROM settings WHERE key = 'operator_email';\n"
if auto:
    sql += ("INSERT INTO settings (key, value) VALUES ('auto_approve_email', :'auto')\n"
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()\n"
            " WHERE settings.value IS DISTINCT FROM EXCLUDED.value;\n")
sql += ("SELECT key || ' = ' || value || '   (updated ' || "
        "to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC)' FROM settings ORDER BY key;\n")

# psql's :'op' quotes the value as a literal; it is passed as an argument, never
# spliced into the SQL text.
p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", DB, "-X", "-q", "-At",
                    "-v", "ON_ERROR_STOP=1", "-v", "op=" + operator, "-v", "auto=" + auto],
                   input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
if p.returncode != 0:
    sys.exit("psql failed:\n" + p.stderr)
rows = [ln for ln in p.stdout.splitlines() if ln.strip()]
print("NOVASCOUT_OPERATOR_EMAIL in .env: %s" % ("set" if operator else "empty -- notifications off"))
print("NOVASCOUT_AUTO_APPROVE_EMAIL in .env: %s" % (auto or "unset -- settings.auto_approve_email left as it is"))
print("settings in %s:" % DB)
for r in rows:
    print("  " + r)
if not rows:
    print("  (none -- Mailbox Watch still records replies, but notifies nobody)")

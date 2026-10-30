"""What the operator would be emailed about the CURRENT held set -- the shipped code, the live database, nothing kept.

    python n8n/sendtrack/dryrun/hold_notice_preview.py [--at 2026-10-31T03:00:00Z] [--quiet]

Migration 028 (2026-10-30). Three questions, answered by the workflows' own SQL and JS read out of
n8n/workflows/*.json, against the live novascout database inside ONE transaction that is rolled back:

  1. Reply Assistant, reminder lane: how many "still waiting" emails would its next tick send?
     (Find Due Reminders, as shipped; every row is one email.)
  2. Daily Digest at --at (default: the next 08:00 operator time -- the tick that sends and mints codes):
     how many one-time codes would it mint, and how many per-draft "APPROVE <code>" lines would it carry?
     (Load Digest + Build Digest, as shipped. Codes are minted by reply_approval_issue() inside the
     transaction and rolled back with it.)
  3. The digest itself: printed in full, as the preview.

Exit 0 when 1 and 2 are both zero for every hold that needs no person, i.e. the operator gets one digest
and nothing per draft. Read-only in effect: the transaction always rolls back.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, "..", "..", "workflows")
PG = "nova-scout-postgres-1"


def node(workflow, name):
    with open(os.path.join(WF, workflow), encoding="utf-8") as fh:
        for n in json.load(fh)["nodes"]:
            if n["name"] == name:
                return n
    raise SystemExit("%s has no node %r" % (workflow, name))


def config(workflow):
    out = {}
    for a in node(workflow, "Config")["parameters"]["assignments"]["assignments"]:
        out[a["name"]] = a["value"]
    return out


def literal(v):
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def bind(sql, params):
    """The Postgres node's positional parameters, as SQL literals (highest number first, so $1 never eats $10)."""
    for i in sorted(range(len(params)), reverse=True):
        sql = re.sub(r"\$%d(?!\d)" % (i + 1), literal(params[i]), sql)
    return sql.strip().rstrip(";")


def next_digest_tick(hour, offset_min=300):
    now = datetime.datetime.now(datetime.timezone.utc)
    local = now + datetime.timedelta(minutes=offset_min)
    day = local.date() if local.hour < hour else local.date() + datetime.timedelta(days=1)
    t = datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=datetime.timezone.utc)
    return (t - datetime.timedelta(minutes=offset_min)).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=None, help="the digest tick to simulate (UTC ISO); default the next 08:00 PKT")
    ap.add_argument("--quiet", action="store_true", help="do not print the digest text")
    a = ap.parse_args()

    rem_cfg = config("reply-assistant.json")
    dig_cfg = config("daily-digest.json")
    at = a.at or next_digest_tick(int(dig_cfg["digest_hour"]))
    reminders = bind(node("reply-assistant.json", "Find Due Reminders")["parameters"]["query"],
                     ["", rem_cfg["remind_hours"]])
    load = bind(node("daily-digest.json", "Load Digest")["parameters"]["query"],
                [at, dig_cfg["digest_hour"], dig_cfg["held_cap"]])

    sql = """\\set ON_ERROR_STOP 1
BEGIN;
\\echo REMINDERS
SELECT count(*) FROM (%s) t;
\\echo DIGEST
SELECT row_to_json(t) FROM (%s) t;
\\echo CODES
SELECT count(*) FROM reply_approvals WHERE kind = 'email-hold' AND used_at IS NULL AND expires_at > now();
ROLLBACK;
""" % (reminders, load)
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", "novascout", "-X", "-q", "-At"],
                       input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("psql failed:\n" + p.stderr[-2000:])
    out = p.stdout.split("\n")
    reminders_due = int(out[out.index("REMINDERS") + 1])
    # json_agg separates its elements with a newline, so the row spans lines up to the next marker.
    row = json.loads(" ".join(out[out.index("DIGEST") + 1:out.index("CODES")]))
    codes_open = int(out[out.index("CODES") + 1])

    js = node("daily-digest.json", "Build Digest")["parameters"]["jsCode"]
    lib = js[:js.index("// Node body")]
    src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
                "process.stdout.write(JSON.stringify(buildDigest(a.row, a.cfg)));\n"
    q = subprocess.run(["node", "-e", src], input=json.dumps({"row": row, "cfg": dig_cfg}),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    if q.returncode:
        raise SystemExit("the shipped Build Digest failed:\n" + q.stderr[-1500:])
    d = json.loads(q.stdout)

    held = row.get("held") or []
    needs = [h for h in held if h.get("needs_person")]
    approve_lines = len(re.findall(r"APPROVE NS-", d["text"]))
    print("simulated digest tick: %s (Config digest_hour %s, held_cap %s)" % (at, dig_cfg["digest_hour"],
                                                                             dig_cfg["held_cap"]))
    print("held drafts: %d  (email %d, needing a person %d)" % (
        len(held), sum(1 for h in held if h.get("channel") == "email"), len(needs)))
    print("1. reminder emails the Reply Assistant's next tick would send: %d" % reminders_due)
    print("2. digest due: %s (%s); codes minted: %d; codes open in the transaction: %d; per-draft APPROVE lines: %d"
          % (d["send"], d["reason"], len(d["record"]["coded"]), codes_open, approve_lines))
    print("   codes withdrawn by the tick: %s" % row.get("codes_withdrawn"))
    print("   operator emails for the held set: %d digest + %d per-draft" % (1 if d["send"] else 0,
                                                                         reminders_due + 0))
    print("   (transaction rolled back: nothing minted, withdrawn or recorded)")
    if not a.quiet:
        print("\n--- Subject: %s\n%s\n---" % (d["subject"], d["text"]))
    ok = reminders_due == 0 and approve_lines == len(d["record"]["coded"]) <= len(needs)
    if not needs:
        ok = ok and approve_lines == 0 and codes_open == 0
    print("RESULT: %s" % ("PASS -- one digest, nothing per draft that needs no person" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

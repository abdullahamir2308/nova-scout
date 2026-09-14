"""What would Workflow 6's send path do right now? Read-only.

    python n8n/sendtrack/status_now.py

Runs the SHIPPED "Load Send State" query (read out of n8n/workflows/send.json,
so it cannot drift from what the workflow runs) against the live novascout
database -- a single SELECT, it writes nothing -- and feeds the row to the
shipped "Decide Send" code. Nothing is claimed, nothing is sent, no n8n
execution happens.

The per-tick coin flip is reported, not rolled: the decision is shown as if the
coin landed heads, which is the most the tick could do.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SEND_JSON = os.path.join(HERE, "..", "workflows", "send.json")
PG = "nova-scout-postgres-1"

with io.open(SEND_JSON, encoding="utf-8") as fh:
    wf = json.load(fh)
nodes = {n["name"]: n for n in wf["nodes"]}
cfg = {a["name"]: a["value"] for a in nodes["Config"]["parameters"]["assignments"]["assignments"]}
sql = nodes["Load Send State"]["parameters"]["query"]
code_only = re.sub(r"--[^\n]*", "", sql).upper()
assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE)\b", code_only), \
    "Load Send State is expected to be a pure SELECT"
sql = (sql.replace("$1", "''").replace("$2", str(int(cfg["min_gap_min"])))
       .replace("$3", repr(float(cfg["send_probability"]))))
# Belt and braces: the session is read-only, so even a changed query could not write.
p = subprocess.run(
    ["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", "novascout", "-X", "-q", "-At",
     "-v", "ON_ERROR_STOP=1"],
    input="SET default_transaction_read_only = on;\nSELECT row_to_json(t) FROM (" + sql.rstrip().rstrip(";") + ") t;",
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
if p.returncode != 0:
    sys.exit(p.stderr)
state = json.loads(p.stdout.strip())

code = nodes["Decide Send"]["parameters"]["jsCode"]
body_at = code.index("// Node body")
harness = code[:body_at] + "\nconst state = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
    "process.stdout.write(JSON.stringify(decide(state, () => 0)));\n"
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
    tf.write(harness)
try:
    out = subprocess.run(["node", tf.name], input=json.dumps(state), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, encoding="utf-8")
finally:
    os.unlink(tf.name)
if out.returncode != 0:
    sys.exit(out.stderr)
d = json.loads(out.stdout)

w = d["warmup"]
print("clock (live DB now):      %s" % d["clock"])
print("Sent mirror last synced:  %s" % (state["sent_mirror_synced_at"] or "NEVER"))
print("first external send:      %s" % (state["first_external_send_at"] or "none in the mirror"))
print("warm-up:                  history=%s, sender-day %s, day %d, week %d -> ceiling %d/day"
      % (w["history"], w["sender_day"], w["day_index"] + 1, w["week"], w["ceiling"]))
print("sent today / remaining:   %d / %d" % (w["sent_today"], w["remaining"]))
print("drafts by status:         %s" % json.dumps(state["drafts_by_status"], sort_keys=True))
print("approved email drafts:    %d" % len(state["candidates"]))
print("  eligible (all checks):  %d" % len(d["eligible"]))
for e in d["eligible"]:
    print("    draft %-4s %-24s %-10s local %s  %s" % (e["draft_id"], e["domain"], e["country"], e["local_time"],
                                                     "IN business hours" if e["in_window"] else "outside business hours"))
print("  excluded:               %d" % len(d["excluded"]))
for x in d["excluded"]:
    print("    draft %-4s %-24s %s" % (x["draft_id"], x["domain"], x["reason"]))
print("decision this tick:       send=%s  reason=%s" % (d["send"], d["reason"]))
print("                          %s" % d["detail"])

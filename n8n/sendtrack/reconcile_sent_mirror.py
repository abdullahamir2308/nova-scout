"""Reconcile the Sent mirror (mailbox_sent) with the real Sent folder -- the repair tool.

    python n8n/sendtrack/reconcile_sent_mirror.py              # dry run: what it would do
    python n8n/sendtrack/reconcile_sent_mirror.py --apply      # do it
    python n8n/sendtrack/reconcile_sent_mirror.py --hours 72   # only messages that landed in the last 72 hours

Replaces the hand UPDATE of 2026-10-28 (Master Ref Section 9, Workflow 6 / Section
13). Mailbox Watch reconciles the last 48 hours by itself every 10 minutes; this
is for when that is not enough -- an older gap, the reconcile not running, or a
check after an incident.

What runs is what ships:
  * the listing: imap_preflight.py --ids-json, a fresh READ-ONLY IMAP session
    (EXAMINE, BODY.PEEK) to the real mailbox from the repo's .env -- the whole
    Sent folder by default;
  * the plan: the Plan Sent Reconcile node's code, read out of
    ../workflows/mailbox-watch.json (code_mirror_sent.js's library above
    code_reconcile_sent.js);
  * the write: the Reconcile Sent node's statement, read out of the same file.

The dry run executes that statement inside a transaction and rolls it back, so
the counts it prints are the statement's own. --apply commits it. Either way it
can only mark seen, or insert, a message whose Message-ID the folder listing
holds -- it never marks seen a send the folder does not have; those are listed
as UNREPAIRABLE so a person can look (was it really sent? did Zoho file it?).
Safe to run while Mailbox Watch runs: everything is an upsert on Message-ID.

Exit 0 when the mirror and the folder agree afterwards (or would, for a dry run);
1 when a workflow send remains unseen; 2 when the folder could not be listed.
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "workflows", "mailbox-watch.json")
PREFLIGHT = os.path.join(HERE, "imap_preflight.py")
PG = os.environ.get("NOVASCOUT_PG_CONTAINER", "nova-scout-postgres-1")
DB = os.environ.get("NOVASCOUT_RECONCILE_DB", "novascout")


def args():
    a = sys.argv[1:]
    apply, hours = False, 0
    while a:
        x = a.pop(0)
        if x == "--apply":
            apply = True
        elif x == "--hours" and a:
            hours = int(a.pop(0))
        else:
            raise SystemExit(__doc__)
    return apply, hours


def psql(sql, csv_out=False):
    cmd = ["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", DB, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    cmd += ["--csv"] if csv_out else ["-At"]
    p = subprocess.run(cmd, input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                       errors="replace")
    if p.returncode:
        raise SystemExit("psql failed (%d):\n%s" % (p.returncode, p.stderr[-1500:]))
    return p.stdout


def shipped():
    with io.open(WORKFLOW, encoding="utf-8") as fh:
        nodes = {n["name"]: n for n in json.load(fh)["nodes"]}
    code = nodes["Plan Sent Reconcile"]["parameters"]["jsCode"]
    return code[:code.index("// Node body")], nodes["Reconcile Sent"]["parameters"]["query"]


def listing(hours):
    """The real Sent folder, read-only, as the imap-health checker would answer."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    try:
        p = subprocess.run([sys.executable, PREFLIGHT, "--ids-json", path, "--ids-hours", str(hours)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        if "IMAP CHECK: PASSED" not in p.stdout or not os.path.isfile(path):
            tail = "\n".join((p.stdout + "\n" + p.stderr).strip().splitlines()[-6:])
            print("could not list the Sent folder -- nothing was changed:\n" + tail)
            sys.exit(2)
        with io.open(path, encoding="utf-8") as fh:
            ids = json.load(fh)
    finally:
        if os.path.isfile(path):
            os.unlink(path)
    return {"ok": True, "status": "passed", "sent": ids["sent"], "hours": ids["hours"], "since": ids["since"],
            "sent_no_message_id": ids["sent_no_message_id"], "sent_undated": ids["sent_undated"]}


def plan(lib, check, operator):
    """The shipped Plan Sent Reconcile, on this listing (a file, not `node -e`: the code is long)."""
    src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(reconcilePlan(a.check, a.operator)));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
        tf.write(src)
    try:
        p = subprocess.run(["node", tf.name], input=json.dumps({"check": check, "operator": operator}),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    finally:
        os.unlink(tf.name)
    if p.returncode:
        raise SystemExit("the shipped Plan Sent Reconcile failed:\n" + p.stderr[-1500:])
    return json.loads(p.stdout)


def main():
    apply, hours = args()
    lib, sql = shipped()
    check = listing(hours)
    operator = psql("SELECT coalesce((SELECT value FROM settings WHERE key = 'operator_email'), '');").strip()
    pl = plan(lib, check, operator)
    if not pl["listed"]:
        print("the listing was not complete (%s) -- nothing was changed" % pl["why"])
        sys.exit(2)
    arg = json.dumps(pl["payload"])
    assert "$q$" not in arg
    stmt = sql.replace("$1", "$q$" + arg + "$q$").rstrip().rstrip(";") + ";"
    out = psql("BEGIN;\n" + stmt + "\n" + ("COMMIT;" if apply else "ROLLBACK;"), csv_out=True)
    row = next(csv.DictReader(io.StringIO(out)))
    listed_ids = [r["message_id"] for r in pl["payload"]["rows"]]
    unseen = json.loads(psql(
        "SELECT coalesce(json_agg(json_build_object('message_id', message_id, 'sent_at', sent_at) ORDER BY sent_at), "
        "'[]') FROM mailbox_sent WHERE source = 'workflow' AND NOT seen_in_sent_folder;").strip())
    left = [u for u in unseen if u["message_id"] not in listed_ids]

    print("Sent folder: %d message(s) listed%s (read-only IMAP); %d without a Message-ID or Date, skipped by the "
          "listing" % (len(listed_ids), " from the last %d hours" % hours if hours > 0 else ", whole folder",
                       check["sent_no_message_id"] + check["sent_undated"]))
    print("%s:" % ("APPLIED" if apply else "DRY RUN (rolled back -- run with --apply to write)"))
    print("  healed (in the folder, mirror had it unseen -> seen): %s" % row["healed"])
    for mid in json.loads(row["healed_ids"]):
        print("    " + mid)
    print("  new rows (in the folder, not in the mirror at all):   %s" % row["new_rows"])
    print("  already seen:                                         %s" % row["already_seen"])
    if left:
        print("UNREPAIRABLE: %d workflow send(s) unseen and NOT in the Sent folder%s -- not marked:" % (
            len(left), " listing" if hours <= 0 else " listing of the last %d hours" % hours))
        for u in left:
            print("    %s  %s" % (u["sent_at"], u["message_id"]))
        sys.exit(1)
    if not apply and int(row["healed"]):
        print("the mirror is behind the folder until this is applied (Send refuses once a send of its own has been "
              "unseen for 15 minutes).")
        sys.exit(1)
    print("mirror and folder agree.")
    sys.exit(0)


if __name__ == "__main__":
    main()

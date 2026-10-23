"""Classify the bounces recorded before migration 022 and apply its rules to them.

    python n8n/sendtrack/backfill_bounce_kind.py            # dry run: what would change
    python n8n/sendtrack/backfill_bounce_kind.py --apply    # change it, in one transaction

Migration 022 (operator decision 2026-10-24): only a permanent bounce counts
toward the Send safety brake, and a permanent bounce marks the contact it
bounced from unverified. Mailbox Watch decides the kind for every bounce it
records from now on; this does the same for the ones it recorded before.

Every bounce in inbound_messages with no bounce_kind is classified by the
SHIPPED classifier -- bounceKind() in the Classify Inbound node, read out of
../workflows/mailbox-watch.json -- from what was stored of it: its subject and
its body excerpt. --apply then, in one transaction, in the order the bounces
arrived:

  * sets inbound_messages.bounce_kind -- a permanent one fires migration 022's
    trigger, which unverifies the contact the bounce names (and only that one);
  * sets the kind on the lead's bounced send(s) in outreach_log, a permanent one
    sticking: lead 44 had a delay warning on 2026-10-06 and the final failure on
    2026-10-10, so its send ends up permanent.

and prints every contact whose `verified` changed. Re-running it finds nothing
to do. --db names another database (the tests run it on a scratch copy).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "workflows", "mailbox-watch.json")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"


def psql(db, sql):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def lit(s):
    s = "" if s is None else str(s)
    assert "$q$" not in s
    return "$q$" + s + "$q$"


def pending(db):
    out = psql(db, """
SELECT coalesce(json_agg(row_to_json(t) ORDER BY t.at, t.message_id), '[]')
  FROM (SELECT i.message_id, i.lead_id, i.subject, i.body_excerpt, coalesce(i.received_at, i.processed_at) AS at,
               c.email AS contact_email, c.verified AS contact_verified
          FROM inbound_messages i
          LEFT JOIN contacts c ON c.lead_id = i.lead_id
         WHERE i.classification = 'bounce' AND i.bounce_kind IS NULL) t;""")
    return json.loads(out)


def shipped_kinds(rows):
    """bounceKind(subject, text) for each row, by the classifier the workflow ships."""
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = json.load(fh)
    code = {n["name"]: n for n in wf["nodes"]}["Classify Inbound"]["parameters"]["jsCode"]
    lib = code[:code.index("// Node body")]
    js = lib + "\nconst rows = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(rows.map(r => bounceKind(r.subject, r.body_excerpt))));\n"
    p = subprocess.run(["node", "-e", js], input=json.dumps(rows), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8")
    if p.returncode:
        raise SystemExit("the shipped classifier failed:\n" + p.stderr[-1500:])
    kinds = json.loads(p.stdout)
    assert len(kinds) == len(rows) and all(k in ("permanent", "transient") for k in kinds), kinds
    return kinds


def apply_sql(rows, kinds):
    """One transaction: each bounce in arrival order, then the contacts that changed."""
    out = ["BEGIN;",
           "CREATE TEMP TABLE backfill_before ON COMMIT DROP AS SELECT id, lead_id, email, verified FROM contacts;"]
    for r, k in zip(rows, kinds):
        out.append("UPDATE inbound_messages SET bounce_kind = %s WHERE message_id = %s AND classification = 'bounce' "
                   "AND bounce_kind IS NULL;" % (lit(k), lit(r["message_id"])))
        if r["lead_id"] is not None:
            out.append("UPDATE outreach_log SET bounce_kind = CASE WHEN bounce_kind = 'permanent' THEN 'permanent' "
                       "ELSE %s END WHERE lead_id = %d AND outcome = 'bounced' AND message_id IS NOT NULL;"
                       % (lit(k), int(r["lead_id"])))
    out.append("""SELECT coalesce(json_agg(json_build_object('contact_id', c.id, 'lead_id', c.lead_id, 'email', c.email,
                                       'verified_before', b.verified, 'verified_after', c.verified) ORDER BY c.lead_id), '[]')
  FROM contacts c JOIN backfill_before b ON b.id = c.id
 WHERE c.verified IS DISTINCT FROM b.verified;""")
    out.append("COMMIT;")
    return "\n".join(out)


def main(argv):
    db = "novascout"
    if "--db" in argv:
        db = argv[argv.index("--db") + 1]
    rows = pending(db)
    if not rows:
        print("nothing to do: every bounce in %s already has a bounce_kind" % db)
        return 0
    kinds = shipped_kinds(rows)
    print("%d bounce(s) in %s with no bounce_kind, classified by the shipped classifier:" % (len(rows), db))
    for r, k in zip(rows, kinds):
        print("  %-9s lead %-5s %s  %-48s contact %s (verified=%s)"
              % (k, r["lead_id"], str(r["at"])[:16], (r["subject"] or "")[:48], r["contact_email"],
                 r["contact_verified"]))
    if "--apply" not in argv:
        print("\ndry run -- nothing changed. Re-run with --apply.")
        return 0
    changed = json.loads(psql(db, apply_sql(rows, kinds)).strip().splitlines()[-1])
    print("\napplied. Contacts whose verified changed (migration 022's trigger): %d" % len(changed))
    for c in changed:
        print("  lead %-5s contact %-4s %-32s verified %s -> %s"
              % (c["lead_id"], c["contact_id"], c["email"], c["verified_before"], c["verified_after"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

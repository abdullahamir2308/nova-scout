"""Apply a data-only migration on `drafts` -- snapshot, run, compare, and commit only if the delta is the expected one.

    python postgres/apply_drafts_migration.py postgres/migrations/029_area_list_holds.sql \
        --expect 466,526,768,771,773 [--expect-file ids.txt] [--apply]

027 was applied by hand and compared row by row in the session that applied it. This does the same in one
transaction against the live database: every `drafts` and `leads` row is snapshotted (to_jsonb), the
migration's statements run (its own BEGIN/COMMIT lines are dropped -- the runner owns the transaction), every
row is compared again, and a DO block aborts the transaction unless

  * exactly the --expect drafts changed (no more, no fewer -- pass --expect '' for "nothing may change"),
  * only in the columns status / hold_reason / approved_by / approved_at,
  * no lead changed at all, and no draft appeared or vanished.

Without --apply it always rolls back (a dry run that prints the same delta). A second run of a re-runnable
migration should be run with --expect '' and must pass: that is the proof it is re-runnable.

The delta is printed draft by draft -- the before and after of every changed column -- as the proof.
"""
import argparse
import json
import os
import re
import subprocess
import sys

PG = "nova-scout-postgres-1"
PGUSER = "novascout"
ALLOWED = ["approved_at", "approved_by", "hold_reason", "status"]


def body_of(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    # The runner owns the transaction: drop the migration's own BEGIN; / COMMIT; lines.
    stripped = re.sub(r"(?im)^\s*(BEGIN|COMMIT)\s*;\s*$", "", src)
    if re.search(r"(?im)^\s*(ROLLBACK|BEGIN|COMMIT)\b", stripped):
        raise SystemExit("%s holds transaction control the runner cannot strip safely" % path)
    return stripped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("migration")
    ap.add_argument("--expect", default=None, help="comma-separated draft ids that must change ('' = none)")
    ap.add_argument("--expect-file", default=None, help="a file of draft ids, comma or whitespace separated")
    ap.add_argument("--apply", action="store_true", help="commit if the delta is the expected one")
    ap.add_argument("--db", default="novascout")
    a = ap.parse_args()
    raw = a.expect if a.expect is not None else ""
    if a.expect_file:
        with open(a.expect_file, encoding="utf-8") as fh:
            raw += "," + fh.read()
    if a.expect is None and not a.expect_file:
        raise SystemExit("--expect (or --expect-file) is required: say which drafts may change")
    expect = sorted({int(x) for x in re.split(r"[\s,]+", raw) if x.strip()})
    expect_sql = "ARRAY[%s]::bigint[]" % ",".join(str(i) for i in expect) if expect else "ARRAY[]::bigint[]"
    allowed_sql = "ARRAY[%s]::text[]" % ",".join("'%s'" % c for c in ALLOWED)

    sql = r"""
\set ON_ERROR_STOP 1
BEGIN;
CREATE TEMP TABLE _drafts_before ON COMMIT DROP AS SELECT d.id, to_jsonb(d) AS j FROM drafts d;
CREATE TEMP TABLE _leads_before  ON COMMIT DROP AS SELECT l.id, to_jsonb(l) AS j FROM leads l;
%(body)s
CREATE TEMP TABLE _delta ON COMMIT DROP AS
SELECT coalesce(b.id, n.id) AS id,
       (SELECT coalesce(array_agg(k ORDER BY k), '{}')
          FROM jsonb_object_keys(coalesce(n.j, b.j)) k
         WHERE (n.j -> k) IS DISTINCT FROM (b.j -> k)) AS cols,
       b.j AS before, n.j AS after
  FROM _drafts_before b
  FULL JOIN (SELECT d.id, to_jsonb(d) AS j FROM drafts d) n ON n.id = b.id
 WHERE n.j IS DISTINCT FROM b.j;
\echo DELTA
SELECT coalesce(json_agg(json_build_object(
         'id', id, 'cols', cols,
         'before', (SELECT jsonb_object_agg(k, before -> k) FROM unnest(cols) k),
         'after',  (SELECT jsonb_object_agg(k, after -> k) FROM unnest(cols) k)) ORDER BY id), '[]')
  FROM _delta;
\echo END
DO $chk$
DECLARE
  got   bigint[] := (SELECT coalesce(array_agg(id ORDER BY id), '{}') FROM _delta);
  bad   text;
  leads int := (SELECT count(*) FROM _leads_before b
                  FULL JOIN (SELECT l.id, to_jsonb(l) AS j FROM leads l) n ON n.id = b.id
                 WHERE n.j IS DISTINCT FROM b.j);
BEGIN
  IF got IS DISTINCT FROM %(expect)s THEN
    RAISE EXCEPTION 'unexpected delta: drafts %% changed, %% expected', got, %(expect)s;
  END IF;
  SELECT string_agg(id || ':' || array_to_string(cols, '/'), ', ') INTO bad
    FROM _delta WHERE before IS NULL OR after IS NULL OR NOT (cols <@ %(allowed)s);
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected columns (or a draft appeared/vanished): %%', bad;
  END IF;
  IF leads <> 0 THEN
    RAISE EXCEPTION '%% lead row(s) changed; this runner allows none', leads;
  END IF;
  RAISE NOTICE 'delta is exactly the expected %% draft(s), only in %%', cardinality(got), %(allowed)s;
END
$chk$;
%(end)s;
""" % {"body": body_of(a.migration), "expect": expect_sql, "allowed": allowed_sql,
       "end": "COMMIT" if a.apply else "ROLLBACK"}
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", a.db, "-X", "-q", "-At"],
                       input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    out = p.stdout
    delta = []
    if "DELTA\n" in out and "\nEND" in out:
        delta = json.loads(out.split("DELTA\n", 1)[1].split("\nEND", 1)[0])
    for d in delta:
        print("  draft %s: %s" % (d["id"], ", ".join(
            "%s %s -> %s" % (c, json.dumps((d["before"] or {}).get(c)), json.dumps((d["after"] or {}).get(c)))
            for c in d["cols"])))
    print("changed drafts: %d (expected %d)" % (len(delta), len(expect)))
    notices = [l for l in p.stderr.splitlines() if "NOTICE" in l or "ERROR" in l]
    for l in notices:
        print("  " + l)
    if p.returncode or any("ERROR" in l for l in notices):
        print("NOT APPLIED -- the transaction was rolled back.")
        return 1
    print("APPLIED (committed)." if a.apply else "dry run: rolled back. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

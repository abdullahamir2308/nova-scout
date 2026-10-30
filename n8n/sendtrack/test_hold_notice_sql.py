"""Held-email notifications, run for real (migration 028, 2026-10-30).

    python n8n/sendtrack/test_hold_notice_sql.py

The pieces are the SHIPPED ones -- Load Digest, Build Digest and Record Digest out of
../workflows/daily-digest.json; Find Due Reminders out of reply-assistant.json; Load Command
Context, Decide Command and Apply Operator Command out of mailbox-watch.json -- and migration 028
itself, against a scratch database created from the live schema (pg_dump --schema-only). Fixtures
only, every address on .example; the real database is only read (the schema dump), and the
scratch database is dropped afterwards.

What it proves: on the digest's sending tick only an allowlisted hold (auto-approve-off) gets a
code; an Overture source hold, a no-address draft, a hold class never seen before and a LinkedIn
DM get none; the open code the flood left on the Overture draft is withdrawn; the digest carries
one APPROVE line and flags the new class, and Record Digest records the class as seen and NOT
allowlisted; the reminder lane ignores a held cold email but still reminds about a prospect reply;
reply_approval_issue() itself refuses an email-hold code for a source hold; and APPROVE <code> from
the operator still approves the allowlisted draft (approved_by human, code spent).
"""
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WF = os.path.join(HERE, "..", "workflows")
MIGRATION = os.path.join(REPO, "postgres", "migrations", "028_hold_notifications.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_holdnotice_test_%d" % os.getpid()
OPERATOR = "operator@ops.example"
AUTH = "mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com: domain of ops.example designates 1.2.3.4)"
FLOOD_CODE = "NS-2345678ABC"
REPLY_CODE = "NS-3456789BCD"
TICK = "2026-10-31T03:00:00Z"   # 08:00 PKT: the digest's sending tick

FAILS = []


def check(name, got, want):
    ok = got == want
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "\n       got  %r\n       want %r" % (got, want)))
    if not ok:
        FAILS.append(name)


def psql(db, sql, csv_out=False):
    args = ["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    args += ["--csv"] if csv_out else ["-At"]
    p = subprocess.run(args, input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                       errors="replace")
    if p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def rows(sql):
    return json.loads(psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM (%s) t;" % sql))


def one(sql):
    """A top-level statement (data-modifying CTEs allowed), its one row back through --csv."""
    return next(csv.DictReader(io.StringIO(psql(SCRATCH, sql, csv_out=True))))


def nodes(workflow):
    with open(os.path.join(WF, workflow), encoding="utf-8") as fh:
        return {n["name"]: n for n in json.load(fh)["nodes"]}


def bind(sql, params):
    for i in sorted(range(len(params)), reverse=True):
        v = params[i]
        lit = str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'"
        sql = re.sub(r"\$%d(?!\d)" % (i + 1), lambda m: lit, sql)
    return sql.strip().rstrip(";")


def run_js(js, call, arg):
    lib = js[:js.index("// Node body")]
    src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" + \
        "process.stdout.write(JSON.stringify(" + call + "));\n"
    p = subprocess.run(["node", "-e", src], input=json.dumps(arg), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8")
    if p.returncode:
        raise SystemExit("shipped JS failed:\n" + p.stderr[-1500:])
    return json.loads(p.stdout)


FIXTURES = """
INSERT INTO settings (key, value) VALUES ('operator_email', '%(op)s')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO leads (id, domain, company_name, country, source, status) OVERRIDING SYSTEM VALUE VALUES
  (1, 'off.example',   'Switch Off CRO', 'India', 'ichgcp',         'drafted'),
  (2, 'over.example',  'Overture Site',  'USA',   'sites-overture', 'drafted'),
  (3, 'noaddr.example','No Address CRO', 'India', 'ichgcp',         'drafted'),
  (4, 'new.example',   'New Hold CRO',   'India', 'ichgcp',         'drafted'),
  (5, 'li.example',    'LinkedIn CRO',   'India', 'ichgcp',         'drafted'),
  (6, 'replied.example','Replied CRO',   'India', 'ichgcp',         'replied');
INSERT INTO enrichments (lead_id, raw_extraction) VALUES
  (1, '{"company_type": "CRO"}'), (2, '{"company_type": "site"}'), (3, '{"company_type": "CRO"}'),
  (4, '{"company_type": "CRO"}'), (5, '{"company_type": "CRO"}'), (6, '{"company_type": "CRO"}');
INSERT INTO contacts (lead_id, email, verified) VALUES
  (1, 'info@off.example', true), (2, 'info@over.example', true), (4, 'info@new.example', true),
  (6, 'info@replied.example', true);
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, hold_reason) OVERRIDING SYSTEM VALUE VALUES
  (10, 1, 'email', 'template/A/city/role-inbox', 's', 'b', 'pending',
   'auto-approve-off: settings.auto_approve_email is off, so every email waits for a person'),
  (11, 2, 'email', 'template/A/areas/role-inbox', 's', 'b', 'pending',
   'db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)'),
  (12, 3, 'email', 'template/B/city/unaddressed+no-address', 's', 'b', 'pending',
   'no-address: no deliverable email address on file, so Send could never deliver it'),
  (13, 4, 'email', 'template/B/city/role-inbox', 's', 'b', 'pending', 'db-guard: a problem nobody has seen yet'),
  (14, 5, 'linkedin', 'no-profile/x', NULL, 'b', 'pending', 'linkedin: always reviewed and sent by hand'),
  (15, 6, 'email', 'reply/answer', 'Re: s', 'b', 'pending', NULL);
-- What the flood left: an open email-hold code on the Overture draft, told 5 hours ago.
INSERT INTO reply_approvals (code, kind, draft_id, lead_id, issued_at, expires_at, reminded_at)
  VALUES ('%(flood)s', 'email-hold', 11, 2, now() - interval '6 hours', now() + interval '24 hours',
          now() - interval '5 hours');
-- A prospect reply with its own code, told 5 hours ago: still reminded about.
INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by, classification)
  VALUES ('<p1@replied.example>', now() - interval '7 hours', 'info@replied.example', 'Re: s', 6, 'thread', 'reply');
INSERT INTO reply_approvals (code, kind, draft_id, lead_id, inbound_message_id, issued_at, expires_at, reminded_at)
  VALUES ('%(reply)s', 'reply', 15, 6, '<p1@replied.example>', now() - interval '6 hours', now() + interval '24 hours',
          now() - interval '5 hours');
""" % {"op": OPERATOR, "flood": FLOOD_CODE, "reply": REPLY_CODE}


def main():
    digest = nodes("daily-digest.json")
    reply = nodes("reply-assistant.json")
    mail = nodes("mailbox-watch.json")
    with open(MIGRATION, encoding="utf-8") as fh:
        mig = fh.read()
    cfg = {a["name"]: a["value"] for a in digest["Config"]["parameters"]["assignments"]["assignments"]}
    rcfg = {a["name"]: a["value"] for a in reply["Config"]["parameters"]["assignments"]["assignments"]}
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    try:
        schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner",
                                 "novascout"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                                errors="replace")
        if schema.returncode:
            raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
        psql(SCRATCH, schema.stdout)
        psql(SCRATCH, mig)
        psql(SCRATCH, mig)  # re-runnable
        psql(SCRATCH, FIXTURES)  # after the migration, so draft 13's class is one 028 never saw

        print("\nthe allowlist (migration 028)")
        check("classes: allowlisted, source hold, no-address, the new one, LinkedIn",
              rows("SELECT hold_reason_class(hold_reason) AS c, hold_needs_person(hold_reason) AS np FROM drafts "
                   "WHERE id BETWEEN 10 AND 14 ORDER BY id"),
              [{"c": "auto-approve-off", "np": True},
               {"c": "db-guard: source sites-overture is held by niche research_site", "np": False},
               {"c": "no-address", "np": False}, {"c": "db-guard: a problem nobody has seen yet", "np": False},
               {"c": "linkedin", "np": False}])
        check("reply_approval_issue refuses an email-hold code for a source hold and for no-address",
              rows("SELECT reply_approval_issue('email-hold', 11, 2) AS a, reply_approval_issue('email-hold', 12, 3) AS b"),
              [{"a": None, "b": None}])
        check("... and returns the flood's open code for nobody: it is still there until the tick withdraws it",
              rows("SELECT code FROM reply_approvals WHERE kind = 'email-hold'"), [{"code": FLOOD_CODE}])

        print("\nthe reminder lane (shipped Find Due Reminders)")
        due = rows(bind(reply["Find Due Reminders"]["parameters"]["query"], ["", rcfg["remind_hours"]]))
        check("only the prospect reply is due; the held cold email is not",
              [(r["code"], r["kind"]) for r in due], [(REPLY_CODE, "reply")])

        print("\nthe digest's sending tick (shipped Load Digest + Build Digest)")
        load = bind(digest["Load Digest"]["parameters"]["query"], [TICK, cfg["digest_hour"], cfg["held_cap"]])
        row = json.loads(psql(SCRATCH, "SELECT row_to_json(t) FROM (%s) t;" % load).replace("\n", " "))
        coded = {h["draft_id"]: h["code"] for h in row["held"]}
        check("a code for the allowlisted draft only", sorted(k for k, v in coded.items() if v), [10])
        check("the flood's open code withdrawn by the tick",
              [row["codes_withdrawn"], rows("SELECT code FROM reply_approvals WHERE kind = 'email-hold' AND draft_id = 11")],
              [1, []])
        # 028 seeds the classes the live queue held when it ran, plus the gate's fixed ones. A fresh
        # database never held the Overture source hold, so that class is new here too -- as a hold on
        # any new source would be: flagged once, never coded.
        check("the never-seen classes are reported unknown (draft 13's, and the source hold on a fresh database)",
              {h["draft_id"]: h["class_known"] for h in row["held"]}, {10: True, 11: False, 12: True, 13: False,
                                                                      14: True, 15: True})
        d = run_js(digest["Build Digest"]["parameters"]["jsCode"], "buildDigest(a.row, a.cfg)", {"row": row, "cfg": cfg})
        code10 = coded[10]
        check("the digest: due, one APPROVE line (draft 10's), the new class flagged",
              [d["send"], re.findall(r"APPROVE (NS-\w+)", d["text"]), "NEW HOLD REASON" in d["text"],
               "db-guard: a problem nobody has seen yet  [NEW]" in d["text"]],
              [True, [code10], True, True])
        rec = one(bind(digest["Record Digest"]["parameters"]["query"],
                       [json.dumps(d["record"]), json.dumps({"accepted": [OPERATOR], "response": "250 ok"})]))
        check("Record Digest: recorded, the code stamped as told, both new classes seen",
              [rec["recorded"], rec["codes_told"], rec["classes_seen"]], ["1", "1", "2"])
        check("... and a newly seen class is never allowlisted",
              rows("SELECT class, needs_person FROM hold_reason_classes "
                   "WHERE class = 'db-guard: a problem nobody has seen yet'"),
              [{"class": "db-guard: a problem nobody has seen yet", "needs_person": False}])

        print("\nAPPROVE <code> on the allowlisted draft (shipped Load Command Context -> Decide -> Apply)")
        js = mail["Decide Command"]["parameters"]["jsCode"]
        fresh = "APPROVE %s" % code10
        cmd = run_js(js, "operatorCommand({ metadata: { 'authentication-results': a.auth } }, a.fresh)",
                     {"auth": AUTH, "fresh": fresh})
        payload = {"message_id": "<approve-1@ops.example>", "received_at": "2026-10-31T03:10:00Z",
                   "from_addr": OPERATOR, "subject": "Re: [Nova Scout] Digest"}
        arg = json.dumps({"msg": payload, "cmd": cmd})
        ctx = rows(mail["Load Command Context"]["parameters"]["query"].strip().rstrip(";")
                   .replace("$1::jsonb", "$q$" + arg + "$q$::jsonb"))[0]
        verdict = run_js(js, "Object.assign({}, commandDecision(a.ctx), { payload: a.ctx.payload })", {"ctx": ctx})
        r = one(mail["Apply Operator Command"]["parameters"]["query"].replace(
            "$1", "$q$" + json.dumps(verdict) + "$q$"))
        check("recorded, accepted, handled", [r["recorded"], r["accepted"], r["handled"]], ["t", "t", "t"])
        check("draft 10 approved by a human; the code spent as approved",
              [rows("SELECT status, approved_by FROM drafts WHERE id = 10"),
               rows("SELECT outcome FROM reply_approvals WHERE code = '%s'" % code10)],
              [[{"status": "approved", "approved_by": "human"}], [{"outcome": "approved"}]])
        check("every other held draft untouched",
              rows("SELECT id, status FROM drafts WHERE id BETWEEN 11 AND 15 ORDER BY id"),
              [{"id": i, "status": "pending"} for i in range(11, 16)])
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH)

    print("\n%d failed" % len(FAILS) if FAILS else "\nall passed, 0 failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

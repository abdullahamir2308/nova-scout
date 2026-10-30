"""A held source is approvable by nobody and sent by nothing -- and lifting the hold still works (migration 031).

    python n8n/sendtrack/test_held_source_sql.py

The pieces are the SHIPPED ones: migration 031's trigger and functions as the live schema holds them (pg_dump
--schema-only of novascout, with 031 applied again on top), the niches rows copied from the live database, and
Load Send State / Claim Send (send.json), Load Command Context / Decide Command / Apply Operator Command
(mailbox-watch.json) and Load Digest (daily-digest.json) read out of the workflows. Fixtures only, every address
on .example; the real database is only read, and the scratch database is dropped afterwards.

Every route that could approve or send a draft from a held source, each refused:
  1. the NocoDB grid / psql: a human UPDATE to approved                -> an error saying why; nothing changes
  2. a human INSERT of an approved draft                               -> the same error
  3. EDIT of a draft auto-approved before its source was held          -> the same error
  4. APPROVE <code> by email (and EDIT)                                -> recorded, refused with a plain reason,
                                                                          the code unspent; REJECT still works
  5. a code for it at all (reply_approval_issue) / "needs you" in the digest -> none
  6. Send: Load Send State never lists it; Claim Send (the last check) refuses it, saying why
  7. an automatic approval                                             -> still 026's hold reason, word for word
and then 026's lift recipe: clear held_sources, re-approve as 'auto' -> approved; Claim Send now claims it;
a human approval works again.
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
MIGRATION = os.path.join(REPO, "postgres", "migrations", "031_held_source_never_approvable.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_heldsource_test_%d" % os.getpid()
OPERATOR = "operator@ops.example"
AUTH = "mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com: domain of ops.example designates 1.2.3.4)"
CODE = "NS-4567892CDE"
SRC_REASON = "db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)"
LIFT = """BEGIN;
UPDATE niches SET held_sources = '{}' WHERE code = 'research_site';
UPDATE drafts SET status = 'approved', approved_by = 'auto'
 WHERE status = 'pending' AND hold_reason LIKE 'db-guard: source sites-overture is held%';
COMMIT;"""

FAILS = []


def check(name, got, want):
    ok = got == want
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "\n       got  %r\n       want %r" % (got, want)))
    if not ok:
        FAILS.append(name)


def run(db, sql, csv_out=False):
    args = ["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    args += ["--csv"] if csv_out else ["-At"]
    return subprocess.run(args, input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                          errors="replace")


def psql(db, sql, csv_out=False):
    p = run(db, sql, csv_out)
    if p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def refused(sql):
    """Run a statement that must fail; return its ERROR + HINT lines (or None if it did not fail)."""
    p = run(SCRATCH, sql)
    if p.returncode == 0:
        return None
    return " ".join(l.strip() for l in p.stderr.splitlines() if l.startswith(("ERROR", "HINT")))


def rows(sql):
    return json.loads(psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM (%s) t;" % sql))


def one(sql):
    return next(csv.DictReader(io.StringIO(psql(SCRATCH, sql, csv_out=True))))


def nodes(workflow):
    with open(os.path.join(WF, workflow), encoding="utf-8") as fh:
        return {n["name"]: n for n in json.load(fh)["nodes"]}


def config(n):
    return {a["name"]: a["value"] for a in n["Config"]["parameters"]["assignments"]["assignments"]}


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


SITE_BODY = ("Hi there,\n\nWe have yet to be properly introduced, but I'm Abdullah Amir, a software engineer who builds "
             "AI assistants for clinical research websites.\n\nOver Site works in dermatology, so sponsors and CROs "
             "visiting your website arrive with very different questions, and each one is a lead worth capturing."
             "\n\nCheers!\nAbdullah Amir\nSoftware Engineer, Amitrix Labs")


def lit(s):
    # An E'' literal with \n escapes: a raw newline sent through psql's stdin from Windows arrives as CRLF, and
    # the body would then never equal the LF body Claim Send is handed.
    return "E'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


FIXTURES = """
INSERT INTO settings (key, value) VALUES ('operator_email', '%(op)s')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO leads (id, domain, company_name, country, source, status) OVERRIDING SYSTEM VALUE VALUES
  (1, 'over.example', 'Over Site', 'USA', 'sites-overture', 'drafted'),
  (2, 'ctg.example',  'Ctg Site',  'USA', 'sites-ctgov',    'drafted');
INSERT INTO enrichments (lead_id, raw_extraction) VALUES (1, '{"company_type": "site"}'), (2, '{"company_type": "site"}');
INSERT INTO contacts (lead_id, email, verified) VALUES (1, 'info@over.example', true), (2, 'info@ctg.example', true);
-- 12: approved BEFORE the source was held (as 812/867/933 were by APPROVE); 13 for lead 2, never held.
UPDATE niches SET held_sources = '{}' WHERE code = 'research_site';
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, approved_by) OVERRIDING SYSTEM VALUE VALUES
  (12, 1, 'email', 'template/A/areas/role-inbox', 'Over Site + dermatology', %(body)s, 'approved', 'auto'),
  (13, 2, 'email', 'template/A/areas/role-inbox', 'Ctg Site + dermatology', %(body2)s, 'approved', 'auto');
UPDATE niches SET held_sources = '{sites-overture}' WHERE code = 'research_site';
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, hold_reason) OVERRIDING SYSTEM VALUE VALUES
  (10, 1, 'email', 'template/A/areas/role-inbox', 'Over Site + dermatology', %(body)s, 'pending', %(src)s),
  (11, 1, 'email', 'template/A/areas/role-inbox', 'Over Site + dermatology', %(body)s, 'pending',
   'auto-approve-off: settings.auto_approve_email is off, so every email waits for a person');
-- An allowlisted hold on a held-source draft with an open code (minted before 031 would have refused it).
INSERT INTO reply_approvals (code, kind, draft_id, lead_id, issued_at, expires_at)
  VALUES ('%(code)s', 'email-hold', 11, 1, now(), now() + interval '24 hours');
""" % {"op": OPERATOR, "body": lit(SITE_BODY), "body2": lit(SITE_BODY.replace("Over Site", "Ctg Site")),
       "src": lit(SRC_REASON), "code": CODE}


def command(mail, fresh, msg_id):
    js = mail["Decide Command"]["parameters"]["jsCode"]
    cmd = run_js(js, "operatorCommand({ metadata: { 'authentication-results': a.auth } }, a.fresh)",
                 {"auth": AUTH, "fresh": fresh})
    payload = {"message_id": msg_id, "received_at": "2026-10-31T03:10:00Z", "from_addr": OPERATOR,
               "subject": "Re: [Nova Scout] Digest"}
    arg = json.dumps({"msg": payload, "cmd": cmd})
    ctx = rows(mail["Load Command Context"]["parameters"]["query"].strip().rstrip(";")
               .replace("$1::jsonb", "$q$" + arg + "$q$::jsonb"))[0]
    verdict = run_js(js, "Object.assign({}, commandDecision(a.ctx), { payload: a.ctx.payload })", {"ctx": ctx})
    return one(mail["Apply Operator Command"]["parameters"]["query"].replace("$1", "$q$" + json.dumps(verdict) + "$q$"))


def claim(send, draft_id, lead_id, body, to_addr):
    p = {"draft_id": draft_id, "lead_id": lead_id, "raw_body": body, "body": body, "to_addr": to_addr,
         "subject": "s", "clock": "2026-10-30T15:00:00Z", "day_start": "2026-10-29T19:00:00Z",
         "day_end": "2026-10-30T19:00:00Z", "ceiling": 50}
    return one(send["Claim Send"]["parameters"]["query"].replace("$1", "$q$" + json.dumps(p) + "$q$"))


def main():
    send, mail, digest = nodes("send.json"), nodes("mailbox-watch.json"), nodes("daily-digest.json")
    scfg, dcfg = config(send), config(digest)
    with open(MIGRATION, encoding="utf-8") as fh:
        mig = fh.read()
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    try:
        dump = lambda *a: subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--no-owner"] + list(a) +
                                         ["novascout"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         encoding="utf-8", errors="replace").stdout
        psql(SCRATCH, dump("--schema-only"))
        psql(SCRATCH, dump("--data-only", "-t", "niches", "-t", "hold_reason_classes"))
        psql(SCRATCH, mig)  # re-runnable on top of the live schema that already holds it
        psql(SCRATCH, FIXTURES)

        print("\n1-3. the grid, psql, an INSERT, an EDIT: refused, saying why")
        e = refused("UPDATE drafts SET status = 'approved' WHERE id = 10;")
        check("a human UPDATE to approved fails with the reason and the way out",
              [bool(e) and "Draft 10 cannot be approved: its source sites-overture is held by niche research_site "
                           "(niches.held_sources). Nobody can approve a draft from a held source." in e,
               bool(e) and "lifted for the whole source with migration 026's recipe" in e], [True, True])
        e = refused("UPDATE drafts SET status = 'approved', approved_by = 'human' WHERE id = 10;")
        check("... with approved_by = 'human' spelled out too", bool(e) and "Draft 10 cannot be approved" in e, True)
        check("... and draft 10 is exactly as it was", rows("SELECT status, approved_by, hold_reason FROM drafts WHERE id = 10"),
              [{"status": "pending", "approved_by": None, "hold_reason": SRC_REASON}])
        e = refused("INSERT INTO drafts (lead_id, channel, variant, subject, body, status) "
                    "VALUES (1, 'email', 'template/A/city/x', 's', 'b', 'approved');")
        check("a human INSERT of an approved draft fails the same way", bool(e) and "cannot be approved" in e, True)
        e = refused("UPDATE drafts SET edited_body = 'Hi there, new words' WHERE id = 12;")
        check("an EDIT of a draft auto-approved before the hold fails, saying why",
              bool(e) and "Draft 12 cannot be edited into an approval: its source sites-overture is held" in e, True)

        print("\n7. an automatic approval: 026's hold, unchanged")
        psql(SCRATCH, "UPDATE drafts SET status = 'approved', approved_by = 'auto' WHERE id = 10;")
        check("auto -> pending with 026's exact reason", rows("SELECT status, hold_reason FROM drafts WHERE id = 10"),
              [{"status": "pending", "hold_reason": "db-guard: source sites-overture is held by niche research_site "
                                                    "(precision audit, 2026-10-30)"}])

        print("\n4-5. email: APPROVE / EDIT refused with a plain reason, REJECT still works; no code, not 'needs you'")
        r = command(mail, "APPROVE %s" % CODE, "<approve-1@ops.example>")
        check("APPROVE: recorded, refused, handled", [r["recorded"], r["accepted"], r["handled"]], ["t", "f", "t"])
        check("... the reason the operator reads", r["refused_reason"],
              "APPROVE refused: draft #11 cannot be approved -- its source sites-overture is held by niche "
              "research_site (niches.held_sources). Nobody can approve a draft from a held source; the hold is lifted "
              "for the whole source (migration 026's recipe). The draft stays pending, and this code still works for "
              "REJECT.")
        check("... draft 11 pending, the code unspent",
              [rows("SELECT status FROM drafts WHERE id = 11"), rows("SELECT used_at FROM reply_approvals WHERE code = '%s'" % CODE)],
              [[{"status": "pending"}], [{"used_at": None}]])
        r = command(mail, "EDIT %s Hi there,\n\nA longer message of more than twenty words that is a whole email, so "
                    "only the held source can refuse it here, nothing else at all.\n\nCheers!" % CODE,
                    "<edit-1@ops.example>")
        check("EDIT: refused the same way, code unspent",
              [r["accepted"], (r["refused_reason"] or "").startswith("EDIT refused: draft #11 cannot be approved"),
               rows("SELECT used_at FROM reply_approvals WHERE code = '%s'" % CODE)], ["f", True, [{"used_at": None}]])
        r = command(mail, "REJECT %s" % CODE, "<reject-1@ops.example>")
        check("REJECT still works on a held source", [r["accepted"], rows("SELECT status FROM drafts WHERE id = 11")],
              ["t", [{"status": "rejected"}]])
        check("reply_approval_issue mints no code for a held-source draft, even with an allowlisted hold",
              rows("SELECT reply_approval_issue('email-hold', 10, 1) AS a"), [{"a": None}])
        psql(SCRATCH, "INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, hold_reason) "
                      "OVERRIDING SYSTEM VALUE VALUES (15, 1, 'email', 'template/A/city/x', 's', 'b', 'pending', "
                      "'auto-approve-off: settings.auto_approve_email is off');"
                      "INSERT INTO reply_approvals (code, kind, draft_id, lead_id, issued_at, expires_at) "
                      "VALUES ('NS-5678923DEF', 'email-hold', 15, 1, now(), now() + interval '24 hours');")
        load = bind(digest["Load Digest"]["parameters"]["query"], ["2026-10-31T03:00:00Z", dcfg["digest_hour"],
                                                                    dcfg["held_cap"]])
        held = json.loads(psql(SCRATCH, "SELECT row_to_json(t) FROM (%s) t;" % load).replace("\n", " "))["held"]
        check("the digest: draft 15 (allowlisted hold, held source) not 'needs you', no code; its open code withdrawn",
              [[(h["needs_person"], h["code"]) for h in held if h["draft_id"] == 15],
               rows("SELECT code FROM reply_approvals WHERE draft_id = 15")], [[(False, None)], []])

        print("\n6. Send: never a candidate; Claim Send refuses it")
        state = rows(bind(send["Load Send State"]["parameters"]["query"],
                          ["", scfg["min_gap_min"], scfg["send_probability"], scfg["brake_days"]]))[0]
        check("Load Send State lists the unheld draft 13, not the held 12",
              sorted(c["draft_id"] for c in state["candidates"]), [13])
        c = claim(send, 12, 1, SITE_BODY, "info@over.example")
        check("Claim Send refuses 12 and says why; it stays approved, nothing logged",
              [c["claimed"], c["held_source"], rows("SELECT status FROM drafts WHERE id = 12"),
               rows("SELECT count(*) AS n FROM outreach_log WHERE draft_id = 12")],
              ["f", "its source sites-overture is held by niche research_site (niches.held_sources)",
               [{"status": "approved"}], [{"n": 0}]])

        print("\nlifting the hold: 026's recipe")
        psql(SCRATCH, LIFT)
        check("draft 10 re-approved as auto by the recipe", rows("SELECT status, approved_by, hold_reason FROM drafts WHERE id = 10"),
              [{"status": "approved", "approved_by": "auto", "hold_reason": None}])
        c = claim(send, 12, 1, SITE_BODY, "info@over.example")
        check("Claim Send now claims 12", [c["claimed"], c["held_source"]], ["t", ""])
        psql(SCRATCH, "INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, hold_reason) "
                      "OVERRIDING SYSTEM VALUE VALUES (14, 1, 'email', 'template/A/city/x', 's', 'b', 'pending', 'x');"
                      "UPDATE drafts SET status = 'approved' WHERE id = 14;")
        check("a human approval works again", rows("SELECT status, approved_by FROM drafts WHERE id = 14"),
              [{"status": "approved", "approved_by": "human"}])
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH)

    print("\n%d failed" % len(FAILS) if FAILS else "\nall passed, 0 failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

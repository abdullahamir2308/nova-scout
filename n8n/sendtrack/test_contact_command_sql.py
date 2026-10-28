"""CONTACT, and EDIT on a held cold email, run for real (migration 023, 2026-10-28).

    python n8n/sendtrack/test_contact_command_sql.py

The pieces are the SHIPPED ones: the Decide Command code and the Apply Operator
Command statement read out of ../workflows/mailbox-watch.json, and migration 023
itself -- against a scratch database created from the live schema (pg_dump
--schema-only of novascout) with the migration applied. Fixtures only, every
address on .example; the real novascout database is only read (the schema
dump), and the scratch database is dropped afterwards.

What it proves: a CONTACT line sets the contact (verified), clears the lead's
contact_attempts row and moves it to contact_found, and every line that must
not apply does not -- a lead already emailed, a disqualified one, a blocklisted
domain, an unknown domain, a line a later line supersedes; a forged sender
changes nothing; a re-delivered message changes nothing; and the 2026-10-24
EDIT on draft 350 is refused with its code left unused and the draft pending.
"""
import csv
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKFLOW = os.path.join(HERE, "..", "workflows", "mailbox-watch.json")
MIGRATION = os.path.join(REPO, "postgres", "migrations", "023_contact_command.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_contactsql_test_%d" % os.getpid()
OPERATOR = "operator@ops.example"
AUTH = "mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com: domain of ops.example designates 1.2.3.4)"
CODE = "NS-23456789AB"

FAILS = []


def check(name, got, want):
    ok = got == want
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "\n       got  %r\n       want %r" % (got, want)))
    if not ok:
        FAILS.append(name)


def psql(db, sql, check_rc=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check_rc and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout


def rows(sql):
    return json.loads(psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM (%s) t;" % sql))


def shipped():
    with open(WORKFLOW, encoding="utf-8") as fh:
        nodes = {n["name"]: n for n in json.load(fh)["nodes"]}
    return nodes["Decide Command"]["parameters"]["jsCode"], nodes["Apply Operator Command"]["parameters"]["query"]


def decide(js, fresh, ctx):
    """The shipped Decide Command, on a message whose fresh text is `fresh`."""
    lib = js[:js.index("// Node body")]
    src = lib + "\nconst a = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "const ctx = Object.assign({ command: operatorCommand({ metadata: { 'authentication-results': a.auth } }, a.fresh) }, a.ctx);\n" \
        "process.stdout.write(JSON.stringify(Object.assign({}, commandDecision(ctx), { payload: ctx.payload })));\n"
    p = subprocess.run(["node", "-e", src], input=json.dumps({"fresh": fresh, "ctx": ctx, "auth": ctx.pop("_auth", AUTH)}),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    if p.returncode:
        raise SystemExit("the shipped Decide Command failed:\n" + p.stderr[-1500:])
    return json.loads(p.stdout)


def apply(sql, verdict):
    """Apply Operator Command as the workflow runs it: one top-level statement, $1 the verdict."""
    arg = json.dumps(verdict)
    assert "$q$" not in arg
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", SCRATCH, "-X", "-q", "--csv",
                        "-v", "ON_ERROR_STOP=1"], input=sql.replace("$1", "$q$" + arg + "$q$"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("Apply Operator Command failed (%d):\n%s" % (p.returncode, p.stderr[-1500:]))
    return next(csv.DictReader(io.StringIO(p.stdout)))


def ctx(msg_id, frm=OPERATOR, **over):
    base = {"payload": {"message_id": msg_id, "received_at": "2026-10-28T10:00:00Z", "from_addr": frm,
                        "subject": "contacts"},
            "operator_email": OPERATOR, "now": "2026-10-28T10:00:00.000Z", "code_found": False,
            "code_used_at": None, "code_expires_at": None, "code_kind": None, "code_outcome": None,
            "draft_id": None, "draft_status": None, "draft_body": None, "code_lead_domain": None}
    base.update(over)
    return base


FIXTURES = """
INSERT INTO settings (key, value) VALUES ('operator_email', '%(op)s')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
INSERT INTO leads (id, domain, company_name, country, source, status) OVERRIDING SYSTEM VALUE VALUES
  (1, 'alpha.example', 'Alpha CRO', 'India', 'ichgcp', 'scored'),
  (2, 'beta.example',  'Beta CRO',  'India', 'ichgcp', 'drafted'),
  (3, 'gamma.example', 'Gamma CRO', 'India', 'ichgcp', 'sent'),
  (4, 'delta.example', 'Delta CRO', 'India', 'ichgcp', 'disqualified'),
  (5, 'eps.example',   'Eps CRO',   'India', 'ichgcp', 'scored'),
  (6, 'zeta.example',  'Zeta CRO',  'India', 'ichgcp', 'scored');
INSERT INTO enrichments (lead_id, raw_extraction) VALUES
  (1, '{"company_type": "CRO"}'), (2, '{"company_type": "CRO"}'), (3, '{"company_type": "CRO"}'),
  (4, '{"company_type": "CRO"}'), (5, '{"company_type": "CRO"}'), (6, '{"company_type": "CRO"}');
-- Alpha: looked up, nothing found -- a tombstone with the founder's name, retired as exhausted.
INSERT INTO contacts (lead_id, name, title, email, linkedin_url, verified) VALUES
  (1, 'Dr. Anil Rao', 'Founder', NULL, 'https://www.linkedin.com/in/anilrao', false),
  (2, NULL, NULL, NULL, NULL, false),
  (3, NULL, NULL, 'info@gamma.example', NULL, true);
INSERT INTO contact_attempts (lead_id, attempts, last_outcome, detail) VALUES
  (1, 1, 'exhausted', 'no source'), (6, 1, 'exhausted', 'no source');
INSERT INTO blocklist (domain, reason) VALUES ('eps.example', 'opt-out');
-- Beta: a held first touch with an open email-hold code, as draft 350 was.
INSERT INTO drafts (id, lead_id, channel, variant, subject, body, status, hold_reason) OVERRIDING SYSTEM VALUE VALUES
  (10, 2, 'email', 'template/A/areas/unaddressed+no-address', 'Beta CRO + oncology + sponsor inquiries',
   E'Hi there,\\n\\nWe have yet to be properly introduced, but I''m Abdullah Amir and I''m the Founder of Amitrix Labs.'
   || E'\\n\\nBeta CRO covers oncology, so sponsors visiting your website arrive with very different questions, and each '
   || E'one is a lead worth capturing.\\n\\nWorth a look for Beta CRO?\\n\\nCheers!\\nAbdullah Amir\\nFounder, Amitrix Labs',
   'pending', 'no-address: no deliverable email address on file');
INSERT INTO reply_approvals (code, kind, draft_id, lead_id, issued_at, expires_at)
  VALUES ('%(code)s', 'email-hold', 10, 2, now(), now() + interval '24 hours');
INSERT INTO outreach_log (lead_id, channel, sent_at, message_id) VALUES (3, 'email', now() - interval '3 days', '<s3@x.example>');
""" % {"op": OPERATOR, "code": CODE}


def main():
    decide_js, apply_sql = shipped()
    with open(MIGRATION, encoding="utf-8") as fh:
        mig = fh.read()
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    try:
        schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner", "novascout"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
        if schema.returncode:
            raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
        psql(SCRATCH, schema.stdout)
        psql(SCRATCH, mig)
        psql(SCRATCH, mig)  # idempotent
        psql(SCRATCH, FIXTURES)

        print("\nEDIT on a held cold email: the 2026-10-24 instruction is refused, the code stays good")
        held = rows("SELECT body FROM drafts WHERE id = 10")[0]["body"]
        v = decide(decide_js, "EDIT %s Send to this email mailto:bd@beta.example" % CODE,
                   ctx("<edit-1@ops.example>", code_found=True, code_kind="email-hold", draft_id=10,
                       draft_status="pending", draft_body=held, code_lead_domain="beta.example",
                       code_expires_at="2026-10-29T10:00:00.000Z"))
        r = apply(apply_sql, v)
        check("recorded, refused, handled (operator mail)", [r["recorded"], r["accepted"], r["handled"]], ["t", "f", "t"])
        check("the reason names CONTACT for this lead",
              "CONTACT beta.example <email address>" in (r["refused_reason"] or ""), True)
        check("draft 10 still pending, its text untouched",
              rows("SELECT status, edited_body FROM drafts WHERE id = 10"), [{"status": "pending", "edited_body": None}])
        check("the code is not spent", rows("SELECT used_at FROM reply_approvals WHERE code = '%s'" % CODE),
              [{"used_at": None}])

        print("\nCONTACT from the operator: each line judged, the good ones applied")
        msg = "\n".join([
            "CONTACT alpha.example bd@alpha.example",
            "CONTACT beta.example <http://beta.example> office@beta.example <mailto:office@beta.example> Maria Lopez",
            "CONTACT gamma.example new@gamma.example",
            "CONTACT delta.example x@delta.example",
            "CONTACT eps.example x@eps.example",
            "CONTACT nosuch.example x@nosuch.example",
            "CONTACT zeta.example first@zeta.example",
            "CONTACT zeta.example second@zeta.example",
            "", "Thanks"])
        v = decide(decide_js, msg, ctx("<contact-1@ops.example>"))
        r = apply(apply_sql, v)
        check("recorded, accepted, handled, acknowledged",
              [r["recorded"], r["accepted"], r["handled"], r["notify"]], ["t", "t", "t", "t"])
        res = {c["line"]: c["refused"] for c in json.loads(r["contact_results"])}
        check("per line: applied, applied, emailed, disqualified, blocklisted, unknown, superseded, applied", res, {
            1: None, 2: None, 3: "this lead has already been emailed", 4: "this lead is disqualified",
            5: "the lead's domain is on the blocklist", 6: "no lead has the domain nosuch.example",
            7: "a later line names the same lead", 8: None})
        check("alpha: the address, verified, the founder's name and title kept (no name on the line)",
              rows("SELECT name, title, email, verified, linkedin_url FROM contacts WHERE lead_id = 1"),
              [{"name": "Dr. Anil Rao", "title": "Founder", "email": "bd@alpha.example", "verified": True,
                "linkedin_url": "https://www.linkedin.com/in/anilrao"}])
        check("beta: autolinks unwrapped, the given name stored",
              rows("SELECT name, title, email, verified FROM contacts WHERE lead_id = 2"),
              [{"name": "Maria Lopez", "title": None, "email": "office@beta.example", "verified": True}])
        check("zeta: no contacts row before -- inserted from the later line",
              rows("SELECT email, verified FROM contacts WHERE lead_id = 6"),
              [{"email": "second@zeta.example", "verified": True}])
        check("gamma (already emailed): its contact untouched",
              rows("SELECT email FROM contacts WHERE lead_id = 3"), [{"email": "info@gamma.example"}])
        check("statuses: alpha, beta, zeta -> contact_found; the rest unchanged",
              rows("SELECT id, status FROM leads ORDER BY id"),
              [{"id": 1, "status": "contact_found"}, {"id": 2, "status": "contact_found"}, {"id": 3, "status": "sent"},
               {"id": 4, "status": "disqualified"}, {"id": 5, "status": "scored"}, {"id": 6, "status": "contact_found"}])
        check("contact_attempts cleared for the leads that were set",
              rows("SELECT lead_id FROM contact_attempts ORDER BY lead_id"), [])
        rec = rows("SELECT command, code, accepted, refused_reason, jsonb_array_length(contact_lines) AS n "
                   "FROM operator_commands WHERE message_id = '<contact-1@ops.example>'")
        check("operator_commands keeps the message and all 8 lines", rec,
              [{"command": "CONTACT", "code": None, "accepted": True, "refused_reason": None, "n": 8}])
        check("nothing was sent and no draft was written", rows("SELECT count(*) AS n FROM drafts"), [{"n": 1}])

        print("\nRe-delivery, forgery, nothing readable")
        psql(SCRATCH, "UPDATE contacts SET email = 'changed@alpha.example' WHERE lead_id = 1;")
        r = apply(apply_sql, v)
        check("the same Message-ID again: not recorded, not acknowledged",
              [r["recorded"], r["notify"]], ["f", "f"])
        check("... and nothing re-applied", rows("SELECT email FROM contacts WHERE lead_id = 1"),
              [{"email": "changed@alpha.example"}])
        psql(SCRATCH, "UPDATE leads SET status = 'scored' WHERE id = 1;")
        v = decide(decide_js, "CONTACT alpha.example evil@attacker.example",
                   ctx("<forged@attacker.example>", frm="ceo@attacker.example"))
        r = apply(apply_sql, v)
        check("a sender other than the operator: recorded refused, not handled, no ack",
              [r["recorded"], r["accepted"], r["handled"], r["notify"]], ["t", "f", "f", "f"])
        check("... alpha's contact and status untouched",
              rows("SELECT c.email, l.status FROM contacts c JOIN leads l ON l.id = c.lead_id WHERE l.id = 1"),
              [{"email": "changed@alpha.example", "status": "scored"}])
        v = decide(decide_js, "CONTACT alpha.example bd@alpha.example",
                   ctx("<nodkim@ops.example>", _auth="mx.zohomail.com; dkim=fail; spf=pass"))
        r = apply(apply_sql, v)
        check("from the operator's address but DKIM failing: refused, not handled",
              [r["accepted"], r["handled"]], ["f", "f"])
        v = decide(decide_js, "CONTACT alpha.example", ctx("<empty@ops.example>"))
        r = apply(apply_sql, v)
        check("a CONTACT with no readable line: refused with the syntax, acknowledged",
              [r["accepted"], r["notify"], "CONTACT <lead domain> <email address> [name]" in r["refused_reason"]],
              ["f", "t", True])
        v = decide(decide_js, "CONTACT gamma.example a@gamma.example", ctx("<allbad@ops.example>"))
        r = apply(apply_sql, v)
        check("a CONTACT whose every line is refused by the database: refused, the line says why",
              [r["accepted"], r["refused_reason"], json.loads(r["contact_results"])[0]["refused"]],
              ["f", "no CONTACT line could be applied (each line says why)", "this lead has already been emailed"])
        check("migration 023's CHECKs held for every row written",
              rows("SELECT count(*) AS n FROM operator_commands"), [{"n": 6}])
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check_rc=False)

    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

"""Migration 022's bounce rules, run for real -- permanent or transient, and whose contact goes unverified.

    python n8n/sendtrack/test_hard_bounce_sql.py

Operator decision 2026-10-24 (Section 9, Workflow 6): only a permanent bounce
counts toward the Send safety brake, and a permanent bounce marks the contact it
bounced from unverified. The pieces are the SHIPPED ones: the Classify Inbound
code and the Record Inbound statement read out of ../workflows/mailbox-watch.json,
migration 022 itself, and backfill_bounce_kind.py for the bounces recorded
before it -- against a scratch database created from the live schema (pg_dump
--schema-only of novascout) with the migration applied. Fixtures only, every
address on .example; the real novascout database is only read (the schema
dump), and the scratch database is dropped afterwards.

test_send_brake_sql.py proves what the brake does with the counts;
test_hard_bounce_mutations.py breaks each rule here in turn and requires a
check (here or in test_mailbox.js) to fail.
"""
import csv
import io
import json
import os
import subprocess
import sys

import backfill_bounce_kind as backfill

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKFLOW = os.path.join(HERE, "..", "workflows", "mailbox-watch.json")
MIGRATION = os.path.join(REPO, "postgres", "migrations", "022_hard_bounces.sql")
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
SCRATCH = "novascout_bouncesql_test_%d" % os.getpid()
ZOHO = "Mail Delivery Subsystem <mailer-daemon@mail.zoho.com>"


def psql(db, sql, check=True):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise SystemExit("psql failed on %s (%d):\n%s" % (db, p.returncode, p.stderr[-1500:]))
    return p.stdout if check else (p.returncode, p.stdout, p.stderr)


def shipped():
    with open(WORKFLOW, encoding="utf-8") as fh:
        nodes = {n["name"]: n for n in json.load(fh)["nodes"]}
    return {"record": nodes["Record Inbound"]["parameters"]["query"],
            "classify": nodes["Classify Inbound"]["parameters"]["jsCode"]}


def migration():
    with open(MIGRATION, encoding="utf-8") as fh:
        return fh.read()


def create_scratch(mig):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s;" % (SCRATCH, SCRATCH))
    schema = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "--schema-only", "--no-owner", "novascout"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if schema.returncode:
        raise SystemExit("pg_dump --schema-only failed:\n" + schema.stderr[-1500:])
    psql(SCRATCH, schema.stdout)
    psql(SCRATCH, mig)


def classify(js, items):
    """The shipped Classify Inbound's classify(), on IMAP-trigger-shaped items."""
    lib = js[:js.index("// Node body")]
    src = lib + "\nconst items = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
        "process.stdout.write(JSON.stringify(items.map(classify)));\n"
    p = subprocess.run(["node", "-e", src], input=json.dumps(items), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8")
    if p.returncode:
        raise SystemExit("the shipped classifier failed:\n" + p.stderr[-1500:])
    return json.loads(p.stdout)


def record(sql, payload):
    """Record Inbound as the workflow runs it: one top-level statement (data-modifying CTEs), $1 the payload."""
    arg = json.dumps(payload)
    assert "$q$" not in arg
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", SCRATCH, "-X", "-q", "--csv",
                        "-v", "ON_ERROR_STOP=1"], input=sql.replace("$1", "$q$" + arg + "$q$"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("Record Inbound failed (%d):\n%s" % (p.returncode, p.stderr[-1500:]))
    return next(csv.DictReader(io.StringIO(p.stdout)))


def rows(sql):
    return json.loads(psql(SCRATCH, "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM (%s) t;" % sql))


def item(text, subject, msg_id, frm=ZOHO):
    return {"from": frm, "to": "abdullah@amitrixlabs.com", "subject": subject, "date": "Tue, 20 Oct 2026 10:00:00 +0000",
            "textPlain": text, "textHtml": "", "metadata": {"message-id": msg_id, "in-reply-to": "", "references": ""}}


# Report bodies: the wording of the real ones in inbound_messages, on .example addresses.
def zoho_delay(addr, sent_id):
    return ("This message was created automatically by mail delivery system.\n\nTHIS IS A WARNING MESSAGE ONLY.\n\n"
            "YOU DO NOT NEED TO RESEND YOUR MESSAGE.\n\n    ----- The following addresses had fatal errors -----    \n"
            "   [%s]\n\nMessage will be retried for 4 more day(s)\n\nMessage-ID: %s" % (addr, sent_id))


def zoho_final(line, sent_id=None):
    return ("This message was created automatically by mail delivery software.\n A message that you sent could not "
            "be delivered to one or more of its recipients. This is a permanent error. \n\n" + line +
            ("\n\nMessage-ID: %s" % sent_id if sent_id else ""))


def reset():
    psql(SCRATCH, """
TRUNCATE inbound_messages, outreach_log, contacts, mailbox_sent, send_pauses, leads RESTART IDENTITY CASCADE;
INSERT INTO settings (key, value) VALUES ('operator_email', 'operator@example.invalid')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;""")


def lead(n, contact, sent_to=None, verified=True):
    """Lead n, its contact, and one confirmed send <sN@amitrixlabs.com> (to `sent_to`, default the contact)."""
    psql(SCRATCH, """
INSERT INTO leads (id, domain, status) OVERRIDING SYSTEM VALUE VALUES (%(n)d, 'cro%(n)d.example', 'sent');
INSERT INTO contacts (lead_id, email, verified) VALUES (%(n)d, '%(c)s', %(v)s);
INSERT INTO outreach_log (lead_id, channel, sent_at, message_id) VALUES (%(n)d, 'email', now() - interval '1 day', '<s%(n)d@amitrixlabs.com>');
INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source, seen_in_sent_folder)
VALUES ('<s%(n)d@amitrixlabs.com>', now() - interval '1 day', ARRAY['%(to)s'], true, %(n)d, 'workflow', true);
""" % {"n": n, "c": contact, "v": "true" if verified else "false", "to": (sent_to or contact).lower()})


def state(n):
    r = rows("SELECT (SELECT verified FROM contacts WHERE lead_id = %d) AS verified, "
             "(SELECT outcome FROM outreach_log WHERE lead_id = %d) AS outcome, "
             "(SELECT bounce_kind FROM outreach_log WHERE lead_id = %d) AS send_kind" % (n, n, n))[0]
    return [r["verified"], r["outcome"], r["send_kind"]]


def classifier_is_current(js):
    """The shipped Classify Inbound carries code_classify_reply.js as it is now -- the build was re-run after the
    last edit. (2026-10-23: an edit made after the build was tested here against the stale JSON, and imported.)"""
    with open(os.path.join(HERE, "code_classify_reply.js"), encoding="utf-8") as fh:
        src = fh.read()
    lib = src[:src.index("// Node body")].replace("__OWN_DOMAIN__", json.dumps("amitrixlabs.com"))
    return lib.strip() in js


def checks(s):
    """(label, ok, detail) for every expectation, against the statements in `s` (the migration is already applied)."""
    out = []

    def expect(label, ok, detail=""):
        out.append((label, bool(ok), str(detail)))

    expect("the shipped Classify Inbound is the current code_classify_reply.js (run build_workflow.py after an edit)",
           classifier_is_current(s["classify"]))

    reset()
    # Mixed case on purpose: the Sent mirror stores recipients lower-cased, a contact keeps the case it was
    # found in (lead 1884's is Contact@IntrinsicImaging.com).
    lead(1, "Info@Cro1.example")
    lead(2, "ops@cro2.example")
    lead(3, "info@cro3.example")
    lead(4, "bd@cro4.example")
    lead(5, "new@cro5.example", sent_to="old@cro5.example")
    lead(6, "Contact@CRO6.example")
    p = classify(s["classify"], [
        # 0: lead 1, threaded by the NDR body, the address nowhere in its text
        item("Your message couldn't be delivered. The recipient's mailbox is unavailable.\n\nOriginal message:\n"
             "Message-ID: <s1@amitrixlabs.com>\nFrom: abdullah@amitrixlabs.com", "Undeliverable: Klixar + sponsor inquiries",
             "<b1@mx.cro1.example>", "postmaster@cro1.example"),
        # 1: lead 2, a delay warning
        item(zoho_delay("ops@cro2.example", "<s2@amitrixlabs.com>"), "Mail Delivery Status Notification (Delay)", "<b2@zoho>"),
        # 2, 3: lead 3 -- lead 44's two reports, a delay then the final one with an SMTP 421
        item(zoho_delay("info@cro3.example", "<s3@amitrixlabs.com>"), "Mail Delivery Status Notification (Delay)", "<b3a@zoho>"),
        item(zoho_final("info@cro3.example, ERROR CODE :421 - Host not reachable.", "<s3@amitrixlabs.com>"),
             "Undelivered Mail Returned to Sender", "<b3b@zoho>"),
        # 4, 5: lead 4 -- the final report, and only then a stray delay warning
        item(zoho_final("bd@cro4.example, ERROR CODE :550 - 5.1.1 User unknown", "<s4@amitrixlabs.com>"),
             "Undelivered Mail Returned to Sender", "<b4a@zoho>"),
        item(zoho_delay("bd@cro4.example", "<s4@amitrixlabs.com>"), "Mail Delivery Status Notification (Delay)", "<b4b@zoho>"),
        # 6: lead 5 -- the send went to old@, the contact has since become new@
        item(zoho_final("old@cro5.example, ERROR CODE :550 - 5.1.1 User unknown", "<s5@amitrixlabs.com>"),
             "Undelivered Mail Returned to Sender", "<b5@zoho>"),
        # 7: lead 6 -- no Message-ID anywhere (matched by the address it names, in another case)
        item(zoho_final("contact@cro6.example, ERROR CODE :550 - 5.4.1 Recipient address rejected: Access denied."),
             "Undelivered Mail Returned to Sender", "<b6@zoho>"),
        # 8: a bounce for an address nobody here has
        item(zoho_final("nobody@elsewhere.example, ERROR CODE :550 - 5.1.1 User unknown"),
             "Undelivered Mail Returned to Sender", "<b7@zoho>"),
    ])
    expect("the shipped classifier: kinds as the reports say",
           [x["bounce_kind"] for x in p] == ["permanent", "transient", "transient", "permanent", "permanent", "transient",
                                              "permanent", "permanent", "permanent"], [x["bounce_kind"] for x in p])

    r = record(s["record"], p[0])
    expect("a permanent bounce threaded to a send: recorded permanent, matched by thread",
           [r["classification"], r["bounce_kind"], r["matched_by"]] == ["bounce", "permanent", "thread"], r)
    expect("... the send is bounced, permanent, and the contact it went to is unverified",
           state(1) == [False, "bounced", "permanent"], state(1))
    expect("... found through the Sent mirror's recipients (in another case) -- the report never names the address",
           "info@cro1.example" not in p[0]["body_excerpt"].lower(), p[0]["body_excerpt"])
    expect("... and Record Inbound tells the notification so", r["contact_unverified"] == "t", r)
    r2 = record(s["record"], p[0])
    expect("the same report delivered twice: recorded once, no second notification",
           r2["classification"] == "already-recorded" and r2["notify"] == "f"
           and rows("SELECT 1 FROM inbound_messages WHERE message_id = '<b1@mx.cro1.example>'") == [{"?column?": 1}], r2)

    r = record(s["record"], p[1])
    expect("a delay warning: recorded transient, the send bounced-transient, the contact still verified",
           r["bounce_kind"] == "transient" and state(2) == [True, "bounced", "transient"] and r["contact_unverified"] == "f",
           [r, state(2)])

    record(s["record"], p[2])
    expect("lead 44's shape, first the delay: contact verified, send transient", state(3) == [True, "bounced", "transient"],
           state(3))
    r = record(s["record"], p[3])
    expect("... then the final report (an SMTP 421 it calls permanent): the send becomes permanent, the contact unverified",
           state(3) == [False, "bounced", "permanent"] and r["contact_unverified"] == "t", [r, state(3)])

    record(s["record"], p[4])
    record(s["record"], p[5])
    expect("a permanent bounce sticks: a later delay warning cannot downgrade the send",
           state(4) == [False, "bounced", "permanent"], state(4))

    r = record(s["record"], p[6])
    expect("a bounce about an address the contact no longer has: the send is bounced, the new address untouched",
           state(5) == [True, "bounced", "permanent"] and r["contact_unverified"] == "f", [r, state(5)])

    r = record(s["record"], p[7])
    expect("no thread ids at all: matched by the address it names, which unverifies that contact (case-insensitive)",
           [r["matched_by"], r["bounce_kind"]] == ["bounced-address", "permanent"] and state(6)[0] is False, [r, state(6)])

    r = record(s["record"], p[8])
    expect("a bounce matched to no lead: recorded unmatched, with no kind, and nothing else changes",
           [r["classification"], r["bounce_kind"]] == ["unmatched", ""]
           and rows("SELECT bounce_kind FROM inbound_messages WHERE message_id = '<b7@zoho>'") == [{"bounce_kind": None}], r)

    stats = json.loads(psql(SCRATCH, "SELECT send_brake_stats(now(), 3);"))
    expect("the brake counts the five permanent bounces and not the one lead whose only bounce was a delay",
           [stats["sends"], stats["bounced"], stats["delayed"]] == [6, 5, 1]
           and "cro2.example" not in stats["bounced_domains"], stats)

    # The rule on the table holds for every writer, not only Record Inbound.
    psql(SCRATCH, "UPDATE contacts SET verified = true WHERE lead_id = 2;")
    rc, _, err = psql(SCRATCH, "INSERT INTO inbound_messages (message_id, lead_id, classification, bounce_kind, body_excerpt) "
                               "VALUES ('<x1@zoho>', 2, 'reply', 'permanent', 'ops@cro2.example');", check=False)
    expect("a kind on anything but a bounce is refused by the table", rc != 0 and "inbound_bounce_kind_valid" in err, err)
    psql(SCRATCH, "INSERT INTO inbound_messages (message_id, lead_id, classification, bounce_kind, body_excerpt) "
                  "VALUES ('<x2@zoho>', 2, 'bounce', 'transient', 'ops@cro2.example');")
    expect("a transient bounce written by hand leaves the contact alone", state(2)[0] is True, state(2))
    psql(SCRATCH, "INSERT INTO inbound_messages (message_id, lead_id, classification, bounce_kind, body_excerpt) "
                  "VALUES ('<x3@zoho>', 2, 'bounce', 'permanent', 'ops@cro2.example, ERROR CODE :550');")
    expect("a permanent one written by hand unverifies it", state(2)[0] is False, state(2))

    # bounce_names_address: the whole address, never a piece of a longer one.
    names = rows("""SELECT bounce_names_address('{}', t, a) AS hit FROM (VALUES
      ('info@advinus.example.au, ERROR CODE', 'info@advinus.example'),
      ('could not be delivered to info@advinus.example.', 'info@advinus.example'),
      ('xinfo@advinus.example, ERROR CODE', 'info@advinus.example'),
      ('CONTACT@IntrinsicImaging.example, ERROR CODE', 'contact@intrinsicimaging.example'),
      ('to a+b@cro.example failed', 'a+b@cro.example'),
      ('aab@cro.example failed', 'a+b@cro.example'),
      ('to [info@cro.example]', 'info@cro.example'),
      ('anything', ''),
      ('anything', NULL)) v(t, a)""")
    expect("the address as a whole token: .example.au, xinfo@ and a regex-shaped a+b@ never match; case and brackets do",
           [x["hit"] for x in names] == [False, True, False, True, True, False, True, False, False], names)

    # The bounces recorded before migration 022: the backfill, by the shipped classifier.
    reset()
    for n, c in [(16, "office@cro16.example"), (44, "info@cro44.example"), (1378, "kudo@cro1378.example"),
                 (1884, "Contact@CRO1884.example"), (7, "bd@cro7.example"), (8, "ops@cro8.example")]:
        lead(n, c)
    legacy = [
        ("<l44a@zoho>", 44, "Mail Delivery Status Notification (Delay)", zoho_delay("info@cro44.example", "<s44@amitrixlabs.com>"), "2026-10-06 11:45"),
        ("<l16@o365>", 16, "Undeliverable: Oncology and metabolic disorders",
         "[https://products.office.com/en-us/CMSImages/Office365Logo_Orange.png]\nYour message to office@cro16.example "
         "couldn't be delivered.\noffice wasn't found at cro16.example.", "2026-10-06 12:10"),
        ("<l1378@zoho>", 1378, "Undelivered Mail Returned to Sender",
         zoho_final("kudo@cro1378.example, ERROR CODE :550 - 5.4.1 Recipient address rejected: Access denied."), "2026-10-09 00:30"),
        ("<l1884@zoho>", 1884, "Undelivered Mail Returned to Sender",
         zoho_final("contact@cro1884.example, ERROR CODE :550 - 5.4.1 Recipient address rejected: Access denied."), "2026-10-09 21:40"),
        ("<l44b@zoho>", 44, "Undelivered Mail Returned to Sender",
         zoho_final("info@cro44.example, ERROR CODE :421 - Host not reachable."), "2026-10-10 10:46"),
        ("<l7a@zoho>", 7, "Undelivered Mail Returned to Sender", zoho_final("bd@cro7.example, ERROR CODE :550 - 5.1.1"), "2026-10-11 09:00"),
        ("<l7b@zoho>", 7, "Mail Delivery Status Notification (Delay)", zoho_delay("bd@cro7.example", "<s7@amitrixlabs.com>"), "2026-10-12 09:00"),
        ("<l8@zoho>", 8, "Mail Delivery Status Notification (Delay)", zoho_delay("ops@cro8.example", "<s8@amitrixlabs.com>"), "2026-10-12 10:00"),
    ]
    psql(SCRATCH, "\n".join(
        "INSERT INTO inbound_messages (message_id, received_at, lead_id, matched_by, classification, subject, body_excerpt) "
        "VALUES ('%s', '%s+00', %d, 'thread', 'bounce', $q$%s$q$, $q$%s$q$);\n"
        "UPDATE outreach_log SET outcome = 'bounced' WHERE lead_id = %d;" % (m, at, n, subj, body, n)
        for m, n, subj, body, at in legacy))
    before = json.loads(psql(SCRATCH, "SELECT send_brake_stats(now(), 30);"))
    expect("before the backfill every one of the six bounced sends counts (no kind is no proof of a delay)",
           [before["sends"], before["bounced"]] == [6, 6], before)
    pend = backfill.pending(SCRATCH)
    kinds = backfill.shipped_kinds(pend)
    expect("the backfill classifies the stored reports in arrival order",
           [(r["lead_id"], k) for r, k in zip(pend, kinds)] == [
               (44, "transient"), (16, "permanent"), (1378, "permanent"), (1884, "permanent"), (44, "permanent"),
               (7, "permanent"), (7, "transient"), (8, "transient")], list(zip([r["lead_id"] for r in pend], kinds)))
    snap = psql(SCRATCH, "SELECT json_agg(row_to_json(c) ORDER BY lead_id) FROM contacts c;")
    backfill.main(["--db", SCRATCH])
    expect("a dry run changes nothing", psql(SCRATCH, "SELECT json_agg(row_to_json(c) ORDER BY lead_id) FROM contacts c;") == snap
           and len(backfill.pending(SCRATCH)) == 8)
    changed = json.loads(psql(SCRATCH, backfill.apply_sql(pend, kinds)).strip().splitlines()[-1])
    expect("--apply unverifies exactly the contacts of the permanent bounces (16, 44, 1378, 1884, 7), each named in its report",
           sorted(c["lead_id"] for c in changed) == [7, 16, 44, 1378, 1884]
           and all(c["verified_after"] is False for c in changed), changed)
    expect("... lead 44's send ends up permanent (a delay, then the final report)", state(44)[2] == "permanent", state(44))
    expect("... lead 7's stays permanent though a delay warning came after its final report", state(7)[2] == "permanent",
           state(7))
    expect("... lead 8, a delay only, keeps its contact and a transient send", state(8) == [True, "bounced", "transient"],
           state(8))
    after = json.loads(psql(SCRATCH, "SELECT send_brake_stats(now(), 30);"))
    expect("... and the brake now counts 5 of the 6 bounced sends, the delay-only one apart",
           [after["bounced"], after["delayed"]] == [5, 1], after)
    expect("re-running the backfill finds nothing to do", backfill.pending(SCRATCH) == [])
    return out


def run_all(variants):
    """variants: [(statements, migration_sql)] on one scratch database, the migration re-applied for each
    (every object in it is CREATE OR REPLACE / IF NOT EXISTS), so a mutated migration is tested as it would ship."""
    create_scratch(variants[0][1])
    try:
        results = []
        for stmts, mig in variants:
            psql(SCRATCH, mig)
            results.append(checks(stmts))
        return results
    finally:
        psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % SCRATCH, check=False)


if __name__ == "__main__":
    results = run_all([(shipped(), migration())])[0]
    for label, ok, detail in results:
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "  -- " + detail[:400]))
    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)

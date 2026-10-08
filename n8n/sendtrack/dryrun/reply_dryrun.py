"""Workflow 7 dry run -- the operator command fork and the reply send lane, end
to end, with nothing able to reach a real inbox or a real mailbox.

    python n8n/sendtrack/dryrun/reply_dryrun.py [--keep]

REAL: the workflows (mailwatch-test.json and sendreply-dryrun.json, built by
build_workflow.py from the SAME node lists that ship -- the build proves only
credentials, Config values, the send_url sink override and the schedule
trigger differ), n8n itself, every Postgres statement, including migration
018's own functions and triggers.

NOT REAL: the database is novascout_dryrun (the same scratch name dryrun.py
uses -- do not run both at once), built from a private template copy
(migration 018 applied to the TEMPLATE only -- see below) with every contact
address rewritten to .dryrun.invalid and the operator's notification address
replaced by operator@dryrun.invalid; the "operator" messages below are
fixtures fed to Mailbox Watch's IMAP trigger, not real mail; and Send Reply's
Config points at a loopback HTTP sink (http_sink.js) instead of the real
smtp-send sidecar, so no SMTP conversation of any kind happens anywhere in
this file. The real novascout database is only READ (pg_dump).

WHY MIGRATION 018 IS APPLIED HERE, TO THE SCRATCH COPY ONLY: this harness's
job is to prove the reply assistant before anything in it touches the live
database or a live mailbox (see prompts/_preamble.md). The live `novascout` at
the time this runs has migration 017 but not 018 (confirmed: reply_approvals
and operator_commands exist; reply_approval_issue() and
retire_linkedin_on_reply() do not) -- so proving 018's own functions and the
drafts trigger it changes requires them to exist somewhere, and "somewhere"
being a throwaway clone, never the live database, is the whole point. Applying
018 to the live database is a separate, later step once this proof exists.

Afterwards the scratch databases are dropped, the sinks are stopped, and the
two dry-run workflow entries are deleted from n8n again; --keep leaves all of
it for inspection.

Every expectation is asserted. Exit 1 if any guard did not hold.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SENDTRACK = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SENDTRACK))
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
# DRY must be exactly "novascout_dryrun" -- the pre-provisioned n8n credential
# "Postgres - novascout_dryrun (scratch)" (novascoutPgDry01, PG_DRY in
# build_workflow.py) is pointed at that literal database name and cannot be
# parameterised from here. The same name dryrun.py uses -- do not run both
# harnesses against the same n8n instance at once.
REAL, BASE, DRY = "novascout", "novascout_replydry_base", "novascout_dryrun"
SMTP_SINK_DIR, SMTP_SINK_JS = "/tmp/novascout_reply_smtp_sink", "/tmp/novascout_reply_smtp_sink.js"
HTTP_SINK_DIR, HTTP_SINK_JS = "/tmp/novascout_reply_http_sink", "/tmp/novascout_reply_http_sink.js"
HTTP_SINK_PORT = 18766
SCRATCH = tempfile.mkdtemp(prefix="novascout-replydryrun-")
KEEP = "--keep" in sys.argv

OPERATOR = "operator@dryrun.invalid"
ATTACKER = "attacker@evil.dryrun.invalid"
# Off-hours by construction (02:00 UTC), and the day the 20 synthetic cold
# sends below are dated -- Decide Reply Send's own query has no clock table
# and no warm-up count to consult (code_reply_decide.js says so), so this is
# the clearest way to show it sent anyway rather than merely citing the code.
CLOCK = "2026-10-09T02:00:00.000Z"
WRONG_CODE = "NS-7Q9X4K8M3P"  # shape-valid (migration 017's alphabet), never issued

RESULTS = []


def expect(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail else ""))


def sh(args, stdin=None, check=True):
    p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("FAILED (%d): %s\n%s\n%s" % (p.returncode, " ".join(args[:6]), p.stdout[-1500:], p.stderr[-1500:]))
    return p.stdout


def psql(db, sql):
    return sh(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1",
               "-At"], stdin=sql)


def psql_json(db, sql):
    out = psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql + ") t;").strip()
    return json.loads(out)


# --- scratch database ---------------------------------------------------------

def make_base():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                     "CREATE DATABASE %s;" % (DRY, BASE, BASE))
    sh(["docker", "exec", PG, "sh", "-c",
        "pg_dump -U %s -d %s --no-owner | psql -U %s -d %s -q -X -v ON_ERROR_STOP=1 >/dev/null" % (PGUSER, REAL, PGUSER, BASE)])
    psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
               "WHERE email IS NOT NULL AND email <> '';")
    psql(BASE, "DELETE FROM settings;\n"
               "INSERT INTO settings (key, value) VALUES ('operator_email', '%s');" % OPERATOR)
    # Migration 018, on the scratch copy only -- see the module docstring.
    has_018 = psql(BASE, "SELECT to_regprocedure('reply_approval_issue(text,bigint,bigint,text,numeric)') "
                        "IS NOT NULL;").strip()
    if has_018 != "t":
        with io.open(os.path.join(REPO, "postgres", "migrations", "018_reply_assistant.sql"),
                    encoding="utf-8") as fh:
            migration_018 = fh.read()
        psql(BASE, migration_018)
    confirm = psql(BASE, "SELECT to_regprocedure('reply_approval_issue(text,bigint,bigint,text,numeric)') "
                        "IS NOT NULL AND to_regprocedure('retire_linkedin_on_reply()') IS NOT NULL;").strip()
    if confirm != "t":
        raise SystemExit("migration 018 did not apply cleanly to the scratch base")


def fresh(seed_sql=""):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s TEMPLATE %s;" % (DRY, DRY, BASE))
    if seed_sql:
        psql(DRY, seed_sql)


# --- n8n ------------------------------------------------------------------------

def build(fixtures=None):
    env = dict(os.environ, SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"), SENDTRACK_DRYRUN_NOW=CLOCK,
               SENDTRACK_SENDREPLY_SINK_URL="http://127.0.0.1:%d/send" % HTTP_SINK_PORT,
               PYTHONIOENCODING="utf-8")
    if fixtures:
        path = os.path.join(SCRATCH, "fixtures.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(fixtures, fh, ensure_ascii=False)
        env["SENDTRACK_FIXTURES"] = path
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("build failed:\n" + p.stderr[-2000:])


def import_wf(name):
    with io.open(os.path.join(SCRATCH, "variants", name), encoding="utf-8") as fh:
        blob = fh.read()
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_reply_wf.json"], stdin=blob)
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_reply_wf.json"])
    sh(["docker", "exec", N8N, "rm", "-f", "/tmp/ns_reply_wf.json"])
    return json.loads(blob)


def execute(wid):
    out = sh(["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "-e", "N8N_RUNNERS_ENABLED=false", N8N,
              "n8n", "execute", "--id=" + wid, "--rawOutput"], check=False)
    start = out.find("{\n")
    if start == -1:
        raise SystemExit("no execution JSON from n8n execute --id=%s:\n%s" % (wid, out[-2000:]))
    run = json.loads(out[start:])
    err = run.get("data", {}).get("resultData", {}).get("error")
    if err:
        raise SystemExit("execution %s errored: %s" % (wid, json.dumps(err)[:1500]))
    return run


def out_items(run, node, output=0):
    rd = run["data"]["resultData"]["runData"]
    if node not in rd:
        return None
    main = rd[node][0]["data"]["main"]
    return [i["json"] for i in (main[output] if len(main) > output and main[output] else [])]


# --- the sinks ------------------------------------------------------------------
#
# No `pkill` anywhere here (by name match): each sink's own PID is captured
# when it starts and stopped by that exact PID -- a plain `kill <pid>` against
# a process this script itself just launched inside the n8n container, nothing
# pattern-matched by name.
SINK_PIDS = []


def _start_sink(js_name, dest_js, out_dir, port):
    sh(["docker", "exec", N8N, "sh", "-c", "rm -rf %s" % out_dir])
    with io.open(os.path.join(HERE, js_name), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % dest_js], stdin=fh.read())
    pidfile, readylog = dest_js + ".pid", dest_js + ".ready"
    sh(["docker", "exec", N8N, "sh", "-c", "rm -f %s %s" % (pidfile, readylog)])
    # stdout (node's own "listening on" line) is the actual readiness signal --
    # the out_dir existing only proves fs.mkdirSync ran, not that .listen()
    # succeeded (a second process on an already-bound port would mkdir the
    # same dir and then crash on EADDRINUSE, which "test -d" would miss).
    sh(["docker", "exec", "-d", N8N, "sh", "-c",
        "node %s %s %s > %s 2>&1 & echo $! > %s" % (dest_js, out_dir, port, readylog, pidfile)])
    for _ in range(50):
        out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s 2>/dev/null; exit 0" % readylog])
        if "sink on" in out:
            break
        if "Error" in out or "EADDRINUSE" in out:
            raise SystemExit("the sink at %s failed to start:\n%s" % (dest_js, out))
        time.sleep(0.2)
    else:
        raise SystemExit("the sink at %s did not start (no readiness line within 10s)" % dest_js)
    pid = sh(["docker", "exec", N8N, "cat", pidfile]).strip()
    if pid:
        SINK_PIDS.append(pid)


def start_smtp_sink():
    _start_sink("smtp_sink.js", SMTP_SINK_JS, SMTP_SINK_DIR, "2525")


def smtp_sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SMTP_SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def start_http_sink():
    _start_sink("http_sink.js", HTTP_SINK_JS, HTTP_SINK_DIR, str(HTTP_SINK_PORT))


def http_sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % HTTP_SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def stop_sinks():
    for pid in SINK_PIDS:
        sh(["docker", "exec", N8N, "kill", pid], check=False)
    SINK_PIDS[:] = []


def show(title, obj):
    print("  %s: %s" % (title, json.dumps(obj, ensure_ascii=False)))


def auth_header(spf, dkim):
    """An Authentication-Results header in the shape Zoho writes (measured on
    the real Pharmahungary reply, migration 017's header comment)."""
    return ("mx.zohomail.com;\tdkim=%s;\tspf=%s (zohomail.com: domain designates 1.2.3.4 "
           "as permitted sender)" % ("pass" if dkim else "fail", "pass" if spf else "fail"))


def op_item(uid, message_id, text, spf=True, dkim=True, frm=None, date="Thu, 08 Oct 2026 22:00:00 +0000"):
    return {"date": date, "from": frm or ("Abdullah Amir <%s>" % OPERATOR), "to": "abdullah@amitrixlabs.com",
           "subject": "Re: your note", "textPlain": text, "textHtml": "",
           "metadata": {"message-id": message_id, "authentication-results": auth_header(spf, dkim)},
           "attributes": {"uid": uid}}


DRAFT_BODY = ("Hello,\n\nThanks for your question -- happy to help.\n\n"
             "Abdullah Amir\nFounder, Amitrix Labs\n+923178485713")

# E/B/C/F: one lead, one inbound reply and one pending reply/ draft each, built
# through reply_approval_issue() (migration 018) -- the real function, not a
# hand-rolled row -- exactly as Write Reply Draft & Issue Code would. D is a
# prospect who replies and opts out; it gets no draft or code at all.
SEED_SQL = """
WITH new_leads AS (
  INSERT INTO leads (domain, company_name, country, source, status) VALUES
    ('spoof-e.dryrun.invalid',     'Spoof E Corp',    'Poland', 'test', 'sent'),
    ('expired-b.dryrun.invalid',   'Expired B Corp',  'Poland', 'test', 'sent'),
    ('reused-c.dryrun.invalid',    'Reused C Corp',   'Poland', 'test', 'sent'),
    ('prospectno-d.dryrun.invalid','Prospect D Corp', 'Poland', 'test', 'sent'),
    ('replyf.dryrun.invalid',      'Reply F Corp',    'Poland', 'test', 'replied')
  RETURNING id, domain
),
new_inbound AS (
  INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by,
                                classification, body_excerpt)
  SELECT v.message_id, v.received_at::timestamptz, v.from_addr, v.subject, nl.id, 'thread', 'reply', v.body
    FROM (VALUES
      ('<rsvp-e@spoof-e.dryrun.invalid>',   '2026-10-08T09:00:00Z', 'pat@spoof-e.dryrun.invalid',
       'Re: your note', 'spoof-e.dryrun.invalid',   'Thanks, a question for you.'),
      ('<rsvp-b@expired-b.dryrun.invalid>', '2026-10-08T09:00:00Z', 'kim@expired-b.dryrun.invalid',
       'Re: your note', 'expired-b.dryrun.invalid', 'Thanks, a question for you.'),
      ('<rsvp-c@reused-c.dryrun.invalid>',  '2026-10-08T09:00:00Z', 'sam@reused-c.dryrun.invalid',
       'Re: your note', 'reused-c.dryrun.invalid',  'Thanks, a question for you.'),
      ('<rsvp-f@replyf.dryrun.invalid>',    '2026-10-08T09:00:00Z', 'jane@replyf.dryrun.invalid',
       'Re: your note', 'replyf.dryrun.invalid',    'Thanks, a question for you.')
    ) AS v(message_id, received_at, from_addr, subject, domain, body)
    JOIN new_leads nl ON nl.domain = v.domain
  RETURNING message_id, lead_id
),
new_drafts AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, status, body)
  SELECT nl.id, 'email', 'reply/D2.ANG-HOURS', 'Re: your note', 'pending', '{body}'
    FROM new_leads nl
   WHERE nl.domain IN ('spoof-e.dryrun.invalid', 'expired-b.dryrun.invalid', 'reused-c.dryrun.invalid',
                       'replyf.dryrun.invalid')
  RETURNING id, lead_id
),
issued AS (
  SELECT d.id AS draft_id, d.lead_id, im.message_id AS inbound_message_id,
         reply_approval_issue('reply', d.id, d.lead_id, im.message_id, 48) AS code
    FROM new_drafts d
    JOIN new_inbound im ON im.lead_id = d.lead_id
),
contact_d AS (
  INSERT INTO contacts (lead_id, name, email, verified)
  SELECT id, 'Dana Prospect', 'dana@prospectno-d.dryrun.invalid', true
    FROM new_leads WHERE domain = 'prospectno-d.dryrun.invalid'
  RETURNING lead_id
),
outreach_d AS (
  INSERT INTO outreach_log (lead_id, channel, sent_at, message_id)
  SELECT id, 'email', now() - interval '2 days', '<seed-d@amitrixlabs.com>'
    FROM new_leads WHERE domain = 'prospectno-d.dryrun.invalid'
  RETURNING lead_id
),
-- 20 cold sends already logged for the day Send Reply's own clock will use --
-- a maxed week-4 warm-up ceiling -- so "the reply still went out" is shown
-- against a concrete number, not only against code that never reads one.
ceiling AS (
  INSERT INTO outreach_log (lead_id, channel, sent_at, message_id)
  SELECT nl.id, 'email', '{ceiling_day}T01:00:00Z'::timestamptz, '<ceiling-' || g || '@amitrixlabs.com>'
    FROM new_leads nl, generate_series(1, 20) g
   WHERE nl.domain = 'prospectno-d.dryrun.invalid'
  RETURNING 1
)
SELECT nl.domain || '|' || nl.id || '|' || coalesce(iss.draft_id::text, '') || '|' ||
       coalesce(iss.code, '')
  FROM new_leads nl
  LEFT JOIN issued iss ON iss.lead_id = nl.id;
""".format(body=DRAFT_BODY.replace("'", "''"), ceiling_day=CLOCK[:10])


def seed():
    """Fresh DRY from BASE, with leads/drafts/codes E, B, C, D, F. Returns
    {domain: {lead_id, draft_id, code}}."""
    fresh()
    rows = {}
    for line in psql(DRY, SEED_SQL).splitlines():
        if not line.strip():
            continue
        domain, lead_id, draft_id, code = line.split("|")
        rows[domain] = {"lead_id": int(lead_id), "draft_id": int(draft_id) if draft_id else None,
                       "code": code or None}
    # Backdate B's code: issued long enough ago that 48h from issuance has
    # already passed, and never used. The window CHECK (migration 017) still
    # holds: expires_at (now-2h) <= issued_at (now-50h) + 48h (= now-2h).
    psql(DRY, "UPDATE reply_approvals SET issued_at = now() - interval '50 hours', "
              "expires_at = now() - interval '2 hours' WHERE draft_id = %d;"
              % rows["expired-b.dryrun.invalid"]["draft_id"])
    return rows


def op_commands():
    return psql_json(DRY, "SELECT message_id, command, code, accepted, refused_reason, draft_id "
                          "FROM operator_commands ORDER BY processed_at")


def draft_status(draft_id):
    return psql(DRY, "SELECT status FROM drafts WHERE id = %d;" % draft_id).strip()


def code_state(code):
    rows = psql_json(DRY, "SELECT used_at IS NOT NULL AS used, outcome FROM reply_approvals "
                         "WHERE code = '%s'" % code)
    return rows[0] if rows else None


# =============================================================================
print("Workflow 7 dry run -- scratch DB %s, SMTP sink on 127.0.0.1:2525 and HTTP sink on "
     "127.0.0.1:%d, both inside %s\n" % (DRY, HTTP_SINK_PORT, N8N))

make_base()
leads = seed()
show("seeded", leads)

WRONG_CODE_FULL = WRONG_CODE
CODE_E = leads["spoof-e.dryrun.invalid"]["code"]
CODE_B = leads["expired-b.dryrun.invalid"]["code"]
CODE_C = leads["reused-c.dryrun.invalid"]["code"]
CODE_F = leads["replyf.dryrun.invalid"]["code"]

# --- Section 1: the operator command fork (Mailbox Watch) -------------------
print("\n-- Section 1: the operator command fork --\n")

items = [
    op_item(101, "<spoof-1@evil.dryrun.invalid>", "APPROVE " + CODE_E, frm="Attacker <%s>" % ATTACKER),
    op_item(102, "<wrong-2@amitrixlabs.com>", "APPROVE " + WRONG_CODE_FULL),
    op_item(103, "<expired-3@amitrixlabs.com>", "APPROVE " + CODE_B),
    op_item(104, "<reuse-4a@amitrixlabs.com>", "APPROVE " + CODE_C),
    op_item(105, "<reuse-4b@amitrixlabs.com>", "APPROVE " + CODE_C),
    op_item(106, "<plainno-5@amitrixlabs.com>", "No, thanks."),
    {"date": "Thu, 08 Oct 2026 22:06:00 +0000", "from": "Dana Prospect <dana@prospectno-d.dryrun.invalid>",
     "to": "abdullah@amitrixlabs.com", "subject": "Re: your note", "textPlain": "No, thanks.", "textHtml": "",
     "metadata": {"message-id": "<prospectno-6@prospectno-d.dryrun.invalid>"}, "attributes": {"uid": 107}},
    op_item(108, "<approvef-8@amitrixlabs.com>", "APPROVE " + CODE_F),
]
build(fixtures={"sent": [], "inbox": items})
import_wf("mailwatch-test.json")
start_smtp_sink()
execute("mailwatch0001t")

cmds = {c["message_id"]: c for c in op_commands()}
show("operator_commands", cmds)

spoof = cmds["<spoof-1@evil.dryrun.invalid>"]
expect("a spoofed sender (real SPF/DKIM for its OWN domain, wrong From) is refused",
      spoof["accepted"] is False and spoof["refused_reason"] == "not sent from the operator address",
      str(spoof))
expect("... and the code it quoted is still unused and still open",
      code_state(CODE_E) == {"used": False, "outcome": None}, str(code_state(CODE_E)))
expect("... and the draft it named is still pending",
      draft_status(leads["spoof-e.dryrun.invalid"]["draft_id"]) == "pending", "")

wrong = cmds["<wrong-2@amitrixlabs.com>"]
expect("a wrong (nonexistent, but shape-valid) code is refused",
      wrong["accepted"] is False and wrong["refused_reason"] == "no such one-time code", str(wrong))

expired = cmds["<expired-3@amitrixlabs.com>"]
expect("an expired, never-used code is refused",
      expired["accepted"] is False and expired["refused_reason"].startswith("that one-time code expired at"),
      str(expired))
expect("... and the draft it named is still pending",
      draft_status(leads["expired-b.dryrun.invalid"]["draft_id"]) == "pending", "")

reuse_a, reuse_b = cmds["<reuse-4a@amitrixlabs.com>"], cmds["<reuse-4b@amitrixlabs.com>"]
expect("the first APPROVE of a fresh code is accepted",
      reuse_a["accepted"] is True, str(reuse_a))
# Both messages are read (Load Command Context) before either is written
# (Apply Operator Command) -- n8n runs one node across every item before the
# next node starts on any of them. So Decide Command's OWN wording for a
# known-used code ("that one-time code was already used at...") never fires
# here; what catches the reuse is Apply Operator Command re-deriving
# reply_approval_usable() at WRITE time, inside its own transaction, which DOES
# see the first message's write -- and refuses with its generic fallback
# instead. Confirmed below against the real table, not just this wording.
expect("a REUSED code -- the same code quoted again -- is refused, even though both messages were "
      "authenticated, arrived in the same run, and were read before either was applied",
      reuse_b["accepted"] is False
      and reuse_b["refused_reason"] == "the code or the draft changed between the read and the write",
      str(reuse_b))
expect("... the draft it approved moved exactly once",
      draft_status(leads["reused-c.dryrun.invalid"]["draft_id"]) == "approved", "")
used_by = psql_json(DRY, "SELECT used_by_message_id FROM reply_approvals WHERE code = '%s'" % CODE_C)[0]
expect("... and the code's one use is recorded against the FIRST message, not the second",
      used_by["used_by_message_id"] == "<reuse-4a@amitrixlabs.com>", str(used_by))

expect("a plain \"no\" from the authenticated operator is never recorded as a command at all",
      "<plainno-5@amitrixlabs.com>" not in cmds, str(sorted(cmds)))
plain_no = psql_json(DRY, "SELECT lead_id, classification FROM inbound_messages "
                         "WHERE message_id = '<plainno-5@amitrixlabs.com>'")[0]
expect("... it is recorded but matched to NO lead, so it cannot blocklist anyone",
      plain_no["lead_id"] is None and plain_no["classification"] == "unmatched", str(plain_no))
expect("... and the operator's own domain never lands on the blocklist because of it",
      psql(DRY, "SELECT count(*) FROM blocklist WHERE domain = 'dryrun.invalid';").strip() == "0", "")

prospect_no = psql_json(DRY, "SELECT lead_id, classification FROM inbound_messages "
                            "WHERE message_id = '<prospectno-6@prospectno-d.dryrun.invalid>'")[0]
expect("a REAL prospect's plain \"no\" -- not the operator's -- still matches their lead and opts them out",
      prospect_no["lead_id"] == leads["prospectno-d.dryrun.invalid"]["lead_id"]
      and prospect_no["classification"] == "opt-out", str(prospect_no))
blocked = psql_json(DRY, "SELECT domain, reason FROM blocklist WHERE domain = 'prospectno-d.dryrun.invalid'")
expect("... and their domain is blocklisted, permanently, by the same statement that classified it",
      len(blocked) == 1 and blocked[0]["reason"].startswith("opt-out: replied \"no\""), str(blocked))

approve_f = cmds["<approvef-8@amitrixlabs.com>"]
expect("a genuine, authenticated APPROVE of draft F is accepted, setting up the send-lane proof below",
      approve_f["accepted"] is True, str(approve_f))
expect("... draft F is now approved",
      draft_status(leads["replyf.dryrun.invalid"]["draft_id"]) == "approved", "")

# --- Section 2: the reply send lane skips the ceiling and business hours, ---
# --- but never the blocklist (Send Reply) ------------------------------------
print("\n-- Section 2: the reply send lane --\n")

cold_today = int(psql(DRY, "SELECT count(*) FROM outreach_log WHERE sent_at::date = '%s' AND channel = 'email';"
                     % CLOCK[:10]).strip())
show("cold sends already logged for the send lane's own clock day (the maxed warm-up ceiling)", cold_today)
expect("that is already past the week-4 ceiling of 20/day -- a cold send here would be refused",
      cold_today >= 20, str(cold_today))

build()
sendreply_wf = import_wf("sendreply-dryrun.json")
state_query = [n for n in sendreply_wf["nodes"] if n["name"] == "Load Reply Send State"][0]["parameters"]["query"]
start_http_sink()
before = len(http_sink_log())
run = execute("sendreply0001dry")
decision = out_items(run, "Decide Reply Send")[0]
show("Decide Reply Send", decision)
after = http_sink_log()

expect("the approved reply sends despite the maxed cold-send day and a clock (%s) outside anyone's "
      "business hours" % CLOCK,
      decision["send"] is True and decision["payload"]["draft_id"] == leads["replyf.dryrun.invalid"]["draft_id"],
      str(decision["reason"]))
state_query_code = "\n".join(l for l in state_query.splitlines() if not l.strip().startswith("--"))
with io.open(os.path.join(SENDTRACK, "code_decide.js"), encoding="utf-8") as fh:
    cold_decide_code = "\n".join(l for l in fh.read().splitlines() if not l.strip().startswith("//"))
with io.open(os.path.join(SENDTRACK, "code_reply_decide.js"), encoding="utf-8") as fh:
    reply_decide_code = "\n".join(l for l in fh.read().splitlines() if not l.strip().startswith("//"))
expect("... structurally, not just by observation: Load Reply Send State's own query, comments stripped, "
      "has no warm-up count (sent_today_before, the cold Load Send State's column) -- a draft's `country` "
      "it does select is display-only, never gated on",
      "sent_today" not in state_query_code.lower(), "query had: sent_today")
expect("... and the cold path's clock gate -- COUNTRY_CLOCKS, business hours -- is a real identifier in "
      "code_decide.js and genuinely absent (not just uncommented-out) from code_reply_decide.js",
      "COUNTRY_CLOCKS" in cold_decide_code and "COUNTRY_CLOCKS" not in reply_decide_code
      and "businessHours" not in reply_decide_code,
      "cold has it: %s, reply has it: %s" % ("COUNTRY_CLOCKS" in cold_decide_code,
                                             "COUNTRY_CLOCKS" in reply_decide_code))
expect("... and the HTTP sink really received it, threaded to the message it answers",
      len(after) == before + 1 and after[-1]["in_reply_to"] == "<rsvp-f@replyf.dryrun.invalid>", str(after[-1:]))
expect("... the draft is now sent, and the lead stays 'replied' (this statement only advances a "
      "draft/approved lead, Section 9)",
      draft_status(leads["replyf.dryrun.invalid"]["draft_id"]) == "sent"
      and psql(DRY, "SELECT status FROM leads WHERE id = %d;" % leads["replyf.dryrun.invalid"]["lead_id"]).strip()
          == "replied",
      "")

# A second lead/draft/code, approved the same way, so the blocklist proof does
# not depend on anything Section 2 above already consumed.
seed2 = psql(DRY, """
WITH nl AS (
  INSERT INTO leads (domain, company_name, country, source, status)
  VALUES ('replyg.dryrun.invalid', 'Reply G Corp', 'Poland', 'test', 'replied') RETURNING id
),
im AS (
  INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by,
                                classification, body_excerpt)
  SELECT '<rsvp-g@replyg.dryrun.invalid>', '2026-10-08T09:00:00Z'::timestamptz, 'gail@replyg.dryrun.invalid',
         'Re: your note', nl.id, 'thread', 'reply', 'Another question.'
    FROM nl
  RETURNING message_id, lead_id
),
d AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, status, body)
  SELECT lead_id, 'email', 'reply/D2.ANG-HOURS', 'Re: your note', 'pending', '%s' FROM im
  RETURNING id, lead_id
)
SELECT d.id || '|' || reply_approval_issue('reply', d.id, d.lead_id, im.message_id, 48)
  FROM d, im;""" % DRAFT_BODY.replace("'", "''")).strip()
draft_g_id, code_g = seed2.split("|")
draft_g_id = int(draft_g_id)

build(fixtures={"sent": [], "inbox": [op_item(201, "<approveg-1@amitrixlabs.com>", "APPROVE " + code_g)]})
import_wf("mailwatch-test.json")
execute("mailwatch0001t")
expect("draft G is approved the same way draft F was",
      draft_status(draft_g_id) == "approved", "")

psql(DRY, "INSERT INTO blocklist (domain, reason) VALUES ('replyg.dryrun.invalid', "
         "'test: proving the reply lane still honours the blocklist');")

build()
import_wf("sendreply-dryrun.json")
before2 = len(http_sink_log())
run2 = execute("sendreply0001dry")
decision2 = out_items(run2, "Decide Reply Send")[0]
show("Decide Reply Send (recipient domain now blocklisted)", decision2)
after2 = http_sink_log()

expect("an approved, otherwise-eligible reply is refused once its recipient's domain is blocklisted",
      decision2["send"] is False
      and any(x["draft_id"] == draft_g_id and x["reason"] == "blocklisted-domain" for x in decision2["excluded"]),
      str(decision2))
expect("... no HTTP call was made for it",
      len(after2) == before2, str(after2[before2:]))
expect("... the draft is left exactly where it was: approved, not sent",
      draft_status(draft_g_id) == "approved", "")

# =============================================================================
stop_sinks()
if not KEEP:
    psql("n8n", "DELETE FROM workflow_entity WHERE id IN ('mailwatch0001t', 'sendreply0001dry');")
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);"
         % (DRY, BASE))
    print("\n(scratch databases dropped, dry-run workflow entries removed, sinks stopped)")
else:
    print("\n--keep: scratch databases, sinks and the two dry-run workflow entries left in place")

failed = [r for r in RESULTS if not r[1]]
print("\n%d passed, %d failed" % (len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)

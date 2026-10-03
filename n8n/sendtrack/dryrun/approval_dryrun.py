"""Auto-approval dry run (migration 014) -- the real Drafting, Follow-Ups, Send and
Daily Digest workflows, end to end, against a scratch copy of the database.

    python n8n/sendtrack/dryrun/approval_dryrun.py [--keep]

What it proves, through n8n:

  1. a clean draft auto-approves -- a first touch and a follow-up
  2. a tagged draft is held, before any claim check is paid for
  3. a claim-check failure is held -- draft 99's own follow-up text, whose
     "..., so you know exactly what came in." widens BEN-SEE
  4. a low-context draft is held
  5. a LinkedIn draft is held
  6. with settings.auto_approve_email off, nothing auto-approves, and no claim
     check runs at all
  plus: an auto-approved email is an ordinary Send candidate (Send is unchanged),
  and the Daily Digest reaches the operator once per day, listing what was
  approved and what was held and why.

REAL: the workflows (built from the same node lists as the shipped ones -- the
differences are listed and asserted below), n8n, every Postgres statement and
trigger, the ClinicalTrials.gov lookup, nodemailer's SMTP conversation, and the
Claude Claim Check: a real Sonnet 5.5 call through the real credential. That
call is the thing under test, and it leaves this machine (it reaches no inbox).

NOT REAL: the composing model. Claude Draft and Claude Follow-Up are replaced by
fixture nodes of the same name that return fixed compositions, so each
scenario's draft text is known in advance -- real text where it exists (draft
85's tagged first touch, draft 99's follow-up), written for the case otherwise.
The database is novascout_dryrun: a copy of novascout with migration 014
applied and every active claim confirmed (step 5 of the brief), every contact
address rewritten to ...@dryrun.invalid and the operator address replaced by
operator@dryrun.invalid. SMTP goes to the sink inside the n8n container. The
real novascout is only read (pg_dump); its fingerprint is printed before and
after.

Afterwards the scratch databases are dropped and the test workflows deleted
from n8n; --keep leaves both. Exit 1 if any expectation did not hold.
"""
import copy
import datetime
import email
import email.policy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SENDTRACK = os.path.dirname(HERE)
DRAFTING = os.path.join(SENDTRACK, "..", "drafting")
REPO = os.path.abspath(os.path.join(SENDTRACK, "..", ".."))
MIGRATION = os.path.join(REPO, "postgres", "migrations", "014_auto_approval.sql")
N8N = "nova-scout-n8n-1"
PG = "nova-scout-postgres-1"
PGUSER = "novascout"
REAL, BASE, DRY = "novascout", "novascout_dryrun_base", "novascout_dryrun"
SINK_DIR, SINK_JS = "/tmp/novascout_sink", "/tmp/novascout_smtp_sink.js"
SCRATCH = tempfile.mkdtemp(prefix="novascout-approval-dryrun-")
KEEP = "--keep" in sys.argv
OPERATOR = "operator@dryrun.invalid"
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
TEST_IDS = ["drafting0001t", "followup0001t", "send0001dry", "digest0001dry"]
# Real now, a minute ahead: the drafts this run writes are stamped now(), and
# the follow-ups the scratch copy holds (first touches of 2026-09-22) are due.
CLOCK = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

RESULTS = []
COST = {"input": 0, "output": 0, "calls": 0}


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
    return json.loads(psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql + ") t;").strip())


def show(title, obj):
    print("  %s: %s" % (title, json.dumps(obj, ensure_ascii=False)))


def fingerprint(db):
    return json.loads(psql(db, """SELECT json_build_object(
      'drafts', (SELECT json_object_agg(k, n ORDER BY k) FROM (SELECT channel || ':' || status AS k, count(*) AS n FROM drafts GROUP BY 1) s),
      'leads', (SELECT json_object_agg(status, n ORDER BY status) FROM (SELECT status, count(*) AS n FROM leads GROUP BY 1) s),
      'outreach_log', (SELECT count(*) FROM outreach_log),
      'mailbox_sent', (SELECT count(*) FROM mailbox_sent),
      'max_draft_id', (SELECT max(id) FROM drafts),
      'claims_confirmed', (SELECT count(*) FROM claims_library WHERE confirmed),
      'settings_md5', (SELECT json_object_agg(key, md5(value) ORDER BY key) FROM settings));""").strip())


# --- scratch database ------------------------------------------------------------

def make_base():
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);\n"
                     "CREATE DATABASE %s;" % (DRY, BASE, BASE))
    dump = os.path.join(SCRATCH, "live.sql")
    with io.open(dump, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sh(["docker", "exec", PG, "pg_dump", "-U", PGUSER, "-d", REAL, "--no-owner"]))
    with io.open(dump, encoding="utf-8") as fh:
        psql(BASE, fh.read())
    with io.open(MIGRATION, encoding="utf-8") as fh:
        psql(BASE, fh.read())          # idempotent: a no-op if the live copy already has it
    psql(BASE, "UPDATE contacts SET email = replace(lower(email), '@', '.at.') || '@dryrun.invalid' "
               "WHERE email IS NOT NULL AND email <> '';\n"
               "DELETE FROM settings;\n"
               "INSERT INTO settings (key, value) VALUES ('operator_email', '%s'), ('auto_approve_email', 'true');\n"
               "UPDATE claims_library SET confirmed = true WHERE active AND NOT confirmed;\n" % OPERATOR)
    return int(psql(BASE, "SELECT count(*) FROM contacts WHERE coalesce(email, '') <> '' "
                          "AND email NOT LIKE '%@dryrun.invalid';").strip())


def fresh(seed_sql=""):
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nCREATE DATABASE %s TEMPLATE %s;" % (DRY, DRY, BASE))
    if seed_sql:
        psql(DRY, seed_sql)


# --- n8n --------------------------------------------------------------------------

def import_wf(wf):
    blob = json.dumps(wf, ensure_ascii=False)
    sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > /tmp/ns_approval_wf.json"], stdin=blob)
    sh(["docker", "exec", N8N, "n8n", "import:workflow", "--input=/tmp/ns_approval_wf.json"])


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
    items = []
    for r in rd[node]:            # a node fed by two branches runs once per branch
        main = r["data"]["main"]
        items += [i["json"] for i in (main[output] if len(main) > output and main[output] else [])]
    return items


def ran(run, node):
    return node in run["data"]["resultData"]["runData"]


# --- the sink -----------------------------------------------------------------------

def start_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; exit 0"])
    with io.open(os.path.join(HERE, "smtp_sink.js"), encoding="utf-8") as fh:
        sh(["docker", "exec", "-i", N8N, "sh", "-c", "cat > %s" % SINK_JS], stdin=fh.read())
    sh(["docker", "exec", "-d", N8N, "node", SINK_JS, SINK_DIR, "2525"])
    for _ in range(50):
        if sh(["docker", "exec", N8N, "sh", "-c", "test -d %s && echo up; exit 0" % SINK_DIR]).strip() == "up":
            return
        time.sleep(0.2)
    raise SystemExit("the SMTP sink did not start")


def sink_log():
    out = sh(["docker", "exec", N8N, "sh", "-c", "cat %s/log.jsonl 2>/dev/null; exit 0" % SINK_DIR])
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def sink_message(file):
    raw = subprocess.run(["docker", "exec", N8N, "cat", file], stdout=subprocess.PIPE).stdout
    return email.message_from_bytes(raw, policy=email.policy.default)


def stop_sink():
    sh(["docker", "exec", N8N, "sh", "-c", "pkill -f '[n]ovascout_smtp_sink'; exit 0"])


# --- building the test variants -------------------------------------------------------

def build_all():
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               DRAFTING_OUT=os.path.join(SCRATCH, "shipped", "drafting.json"))
    p = subprocess.run([sys.executable, os.path.join(DRAFTING, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("drafting build failed:\n" + p.stderr[-2000:])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", SENDTRACK_OUT=os.path.join(SCRATCH, "shipped"),
               SENDTRACK_VARIANTS_OUT=os.path.join(SCRATCH, "variants"), SENDTRACK_DRYRUN_NOW=CLOCK)
    p = subprocess.run([sys.executable, os.path.join(SENDTRACK, "build_workflow.py")], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("sendtrack build failed:\n" + p.stderr[-2000:])


def load(*parts):
    with io.open(os.path.join(SCRATCH, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def fixture_node(name, mode, fixtures, pos):
    """A Code node standing in for a composing HTTP node: the same name and
    position, so the shipped connections and every $('...') read are unchanged."""
    body = {str(k): v for k, v in fixtures.items()}
    src = ("// TEST FIXTURE -- stands in for the composing model (%s) in the dry run only.\n"
           "// A fixed Anthropic Messages API response per lead, so each scenario's draft text is\n"
           "// known in advance. The Claude Claim Check node downstream is REAL.\n"
           "const FIXTURES = %s;\n"
           "const lead = String($input.item.json.lead_id);\n"
           "const gen = FIXTURES[lead];\n"
           "if (!gen) return { json: { error: { message: 'dry run: no fixture for lead ' + lead } } };\n"
           "return { json: { id: 'fixture-' + lead, type: 'message', role: 'assistant', model: 'fixture',\n"
           "  stop_reason: 'end_turn', stop_details: null,\n"
           "  content: [{ type: 'text', text: JSON.stringify(gen) }],\n"
           "  usage: { input_tokens: 0, output_tokens: 0 } } };\n") % (name, json.dumps(body, ensure_ascii=False, indent=2))
    return {"parameters": {"mode": mode, "jsCode": src}, "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": pos, "notes": "Test fixture. Never in a shipped workflow."}


def node_diff(a, b):
    an = {n["name"]: n for n in a["nodes"]}
    bn = {n["name"]: n for n in b["nodes"]}
    out = [(k, "removed") for k in sorted(set(an) - set(bn))] + [(k, "added") for k in sorted(set(bn) - set(an))]
    for k in sorted(set(an) & set(bn)):
        x, y = copy.deepcopy(an[k]), copy.deepcopy(bn[k])
        if x.get("credentials") != y.get("credentials"):
            out.append((k, "credentials"))
        x.pop("credentials", None)
        y.pop("credentials", None)
        if k == "Config":
            xv = {v["name"]: v["value"] for v in x["parameters"]["assignments"]["assignments"]}
            yv = {v["name"]: v["value"] for v in y["parameters"]["assignments"]["assignments"]}
            out += [(k, "config:" + c) for c in sorted(set(xv) | set(yv)) if xv.get(c) != yv.get(c)]
            for v in x["parameters"]["assignments"]["assignments"] + y["parameters"]["assignments"]["assignments"]:
                v["value"] = None
        if x != y:
            out.append((k, "replaced by a fixture" if y["type"] == "n8n-nodes-base.code" and x["type"] != y["type"]
                        else "OTHER"))
    return sorted(out)


def with_fixture(wf, wid, name, node, mode, fixtures):
    v = copy.deepcopy(wf)
    v["id"], v["name"] = wid, name
    old = [n for n in v["nodes"] if n["name"] == node][0]
    v["nodes"] = [n if n["name"] != node else fixture_node(node, mode, fixtures, old["position"]) for n in v["nodes"]]
    return v


def drafting_variant(fixtures):
    shipped = load("shipped", "drafting.json")
    v = with_fixture(shipped, "drafting0001t", "TEST - Drafting (scratch DB, fixture composer, real claim check)",
                     "Claude Draft", "runOnceForEachItem", fixtures)
    v["nodes"] = [n for n in v["nodes"] if n["name"] != "Every 30 Minutes"]
    v["connections"] = {k: x for k, x in v["connections"].items() if k != "Every 30 Minutes"}
    for n in v["nodes"]:
        if "postgres" in n.get("credentials", {}):
            n["credentials"] = copy.deepcopy(PG_DRY)
    # The fixture is a Code node, so it also carries no Anthropic credential.
    want = sorted([("Every 30 Minutes", "removed"), ("Claude Draft", "replaced by a fixture"), ("Claude Draft", "credentials"),
                   ("Get Draft Batch", "credentials"), ("Write Drafts & Advance", "credentials")])
    return v, shipped, want


def followups_variant(fixtures):
    dry = load("variants", "followups-dryrun.json")
    v = with_fixture(dry, "followup0001t", "TEST - Follow-Ups (scratch DB, fixture composer, real claim check)",
                     "Claude Follow-Up", "runOnceForEachItem", fixtures)
    return v, load("shipped", "follow-ups.json")


# --- fixtures: compositions in the drafting schema -------------------------------------

ASK_A1 = "Would a 48-hour demo built on your own material be worth a look? One word back is enough."
ASK_A2 = "Worth a 48-hour demo on your own material? Reply yes and I'll set it up."
TRIAL_50 = "the Registry of Minimally Invasive Cancer Treatment Using Spectral Angio-CT Image Guidance"

# Lead 50, Innovate Research (India): draft 102 and 103 as written on 2026-10-02 --
# composed under v3 after migration 013, tagged only unconfirmed-claim then.
CLEAN_FIRST_TOUCH = {
    "email_subject": "Your Minimally Invasive trial and sponsor inquiries",
    "email_body": ("You're running a recruiting trial, %s. Sponsors often research CROs outside your working hours, "
                   "so an inquiry sent at 11pm waits until morning, and they may have moved on to the next CRO.\n\n"
                   "We built an AI intake assistant for your website that answers sponsors, qualifies them, and sends "
                   "them your booking link (we call it Nova). You can see every sponsor lead it captured, and any "
                   "question it passed to your team. It's live at two CROs, in Türkiye and Mexico." % TRIAL_50),
    "email_ask": ASK_A1,
    "linkedin_body": ("You're running a recruiting trial, %s. Sponsors often research CROs outside your working hours, "
                      "so an inquiry sent at 11pm waits until morning, and they may have moved on. We built an AI "
                      "assistant for your website that turns sponsor inquiries into qualified leads (we call it Nova), "
                      "and it answers sponsors from your own website at any hour. It's live at two CROs, in Türkiye "
                      "and Mexico." % TRIAL_50),
    "linkedin_ask": ASK_A2,
    "email_claims": ["D2", "ANG-HOURS", "BEN-SEE", "PR-BOTH", "A1"],
    "linkedin_claims": ["D1", "ANG-HOURS", "BEN-247", "PR-BOTH", "A2"],
}
# Lead 104, Atlant Clinical (Turkey): draft 85 as written before migration 013 --
# "books the call", "SOPs", D2 with BEN-247. Real text the rules now tag.
TAGGED_FIRST_TOUCH = {
    "email_subject": "Sponsor inquiries in oncology and cardiovascular",
    "email_body": ("Your site lists oncology and cardiovascular. Sponsors often research CROs outside your working "
                   "hours, so an inquiry sent at 11pm waits until morning, and by then they may have moved on to the "
                   "next CRO.\n\nWe built an AI intake assistant for your website that answers sponsors, qualifies "
                   "them, and books the call (we call it Nova). It answers from your own SOPs and service pages at any "
                   "hour, and qualified sponsors book a call straight into your calendar. It's live at NoblePath, an "
                   "oncology CRO in Türkiye."),
    "email_ask": ASK_A1,
    "linkedin_body": ("Your site lists oncology and cardiovascular. A single sponsor inquiry can be a multi-million-"
                      "dollar study. We built an AI assistant for your website that turns sponsor inquiries into "
                      "qualified leads (we call it Nova). It collects the therapeutic area and study phase before your "
                      "first call. It's live at NoblePath, an oncology CRO in Türkiye."),
    "linkedin_ask": ASK_A2,
    "email_claims": ["D2", "ANG-HOURS", "BEN-247", "BEN-BOOK", "PR-TR", "A1"],
    "linkedin_claims": ["D1", "ANG-STAKES", "BEN-BRIEF", "PR-TR", "A2"],
}
# Lead 91, BiTrial (Poland): a clean v3 first touch, with draft 99's widening put
# into its BEN-SEE sentence. Used in the stability scenario.
WIDENED_FIRST_TOUCH = {
    "email_subject": "Oncology and immunology sponsor inquiries",
    "email_body": ("Your site lists oncology and immunology. Sponsors often research CROs outside your working hours, "
                   "so an inquiry sent at 11pm waits until morning, and by then they may have moved on to the next "
                   "CRO.\n\nWe built an AI intake assistant for your website that answers sponsors, qualifies them, "
                   "and sends them your booking link (we call it Nova). You can see every sponsor lead it captured, "
                   "and any question it passed to your team, so you know exactly what came in. It's live at "
                   "NoblePath, an oncology CRO in Türkiye."),
    "email_ask": ASK_A1,
    "linkedin_body": ("Your site lists oncology and immunology. A single sponsor inquiry can be a multi-million-dollar "
                      "study. We built an AI assistant for your website that turns sponsor inquiries into qualified "
                      "leads (we call it Nova). It's live at NoblePath, an oncology CRO in Türkiye."),
    "linkedin_ask": ASK_A2,
    "email_claims": ["D2", "ANG-HOURS", "BEN-SEE", "PR-TR", "A1"],
    "linkedin_claims": ["D1", "ANG-STAKES", "PR-TR", "A2"],
}

# Follow-ups, in the follow-up schema.
DRAFT_99 = {   # lead 7: the brief's example of a widened claim, exactly as drafted 2026-10-02
    "body": ("Following up on my note about after-hours sponsor inquiries. One more thing about the assistant: you can "
             "see every sponsor lead it captured, and any question it passed to your team, so you know exactly what "
             "came in."),
    "ask": ASK_A1, "added_claim": "BEN-SEE", "claims": ["BEN-SEE", "A1"], "first_email_covers": [],
}
DRAFT_100 = {  # lead 91, exactly as drafted 2026-10-02
    "body": ("Following up on my note about after-hours sponsor inquiries. One thing I didn't cover: you can see every "
             "sponsor lead the assistant captured, along with any question it passed to your team. That way your team "
             "can review what came in and pick up whatever needs a person."),
    "ask": ASK_A1, "added_claim": "BEN-SEE", "claims": ["BEN-SEE", "A1"], "first_email_covers": [],
}
DRAFT_101 = {  # lead 104, exactly as drafted 2026-10-02
    "body": ("Following up on my note about after-hours sponsor inquiries. One thing I didn't mention: the assistant "
             "sends your capabilities deck the moment a sponsor asks for it, rather than leaving the request waiting "
             "for someone on your team to reply."),
    "ask": ASK_A2, "added_claim": "BEN-DECK", "claims": ["BEN-DECK", "A2"], "first_email_covers": [],
}
CLEAN_FOLLOW_UP = {
    "body": ("Following up on my note about after-hours sponsor inquiries. One more thing the assistant does: it sends "
             "your capabilities deck the moment a sponsor asks for it."),
    "ask": ASK_A1, "added_claim": "BEN-DECK", "claims": ["BEN-DECK", "A1"], "first_email_covers": [],
}


# --- what a run wrote ---------------------------------------------------------------------

def written(since_id):
    return {(r["lead_id"], r["channel"]): r for r in psql_json(DRY, """
        SELECT d.id, d.lead_id, d.channel, d.status, d.approved_by, d.approved_at IS NOT NULL AS has_approved_at,
               d.variant, d.hold_reason, d.claim_check->>'result' AS check_result,
               d.claim_check->'statements' AS statements, d.claim_check->'usage' AS usage
          FROM drafts d WHERE d.id > %d ORDER BY d.id""" % since_id)}


def max_id():
    return int(psql(DRY, "SELECT coalesce(max(id), 0) FROM drafts;").strip())


def tally(rows):
    for r in rows:
        u = r.get("usage") or {}
        if u:
            COST["input"] += int(u.get("input_tokens") or 0)
            COST["output"] += int(u.get("output_tokens") or 0)
            COST["calls"] += 1


def verdicts(row):
    return [(s["kind"], s["verdict"], s.get("source", ""), s["statement"][:70])
            for s in (row.get("statements") or []) if s["kind"] != "none"]


def print_row(label, r):
    print("    %-34s draft %-4s %-8s %-8s approved_by=%-5s %s" % (label, r["id"], r["channel"], r["status"],
                                                                  r["approved_by"], r["variant"]))
    if r["hold_reason"]:
        print("      hold_reason: %s" % r["hold_reason"])
    for v in verdicts(r):
        print("      check: %-8s %-11s %-10s %s" % v)


# =============================================================================

print("Auto-approval dry run -- scratch DB %s, SMTP sink on 127.0.0.1:2525 inside %s" % (DRY, N8N))
print("clock (follow-ups, send, digest): %s\n" % CLOCK)

real_before = fingerprint(REAL)
show("real novascout BEFORE", real_before)
left = make_base()
expect("every contact address in the scratch copy is rewritten to @dryrun.invalid", left == 0, "%d left" % left)
show("scratch settings", psql_json(BASE, "SELECT key, value FROM settings ORDER BY key"))
expect("the scratch copy has migration 014 and every active claim confirmed",
       psql(BASE, "SELECT count(*) FILTER (WHERE active AND NOT confirmed) || '/' || count(*) FILTER (WHERE active) "
                  "FROM claims_library;").strip().startswith("0/")
       and psql(BASE, "SELECT to_regclass('digest_log') IS NOT NULL;").strip() == "t")

build_all()

# Lead 50 clean, 78 low-context (no model call at all), 104 tagged -- for drafting;
# leads 7 and 91 (emailed 2026-09-22) are due their first follow-up once the
# scratch copy forgets the follow-ups drafted for them on 2026-10-02.
SEED = """
UPDATE leads SET status = 'contact_found' WHERE id IN (50, 78, 104);
DELETE FROM drafts WHERE variant LIKE 'follow-up-%';
"""

draft_wf, draft_shipped, want = drafting_variant({50: CLEAN_FIRST_TOUCH, 104: TAGGED_FIRST_TOUCH})
got = node_diff(draft_shipped, draft_wf)
expect("the Drafting test variant differs from the shipped one only in: schedule trigger removed, the composer a "
       "fixture, Postgres pointed at the scratch DB", got == want, str(got))
fu_wf, fu_shipped = followups_variant({7: DRAFT_99, 91: CLEAN_FOLLOW_UP})
got = node_diff(fu_shipped, fu_wf)
want = sorted([("Every 30 Minutes", "removed"), ("Claude Follow-Up", "replaced by a fixture"),
               ("Claude Follow-Up", "credentials"), ("Config", "config:now_override"), ("Find Due Follow-Ups", "credentials"), ("Write Follow-Up", "credentials")])
expect("the Follow-Ups test variant differs from the shipped one only in: schedule trigger removed, the composer a "
       "fixture, the clock, Postgres pointed at the scratch DB", got == want, str(got))
check_nodes = [[n for n in w["nodes"] if n["name"] == "Claude Claim Check"][0] for w in (draft_wf, fu_wf)]
expect("both test variants carry the REAL Claude Claim Check node, unchanged: real model, real credential",
       check_nodes[0] == [n for n in draft_shipped["nodes"] if n["name"] == "Claude Claim Check"][0]
       and check_nodes[1] == [n for n in fu_shipped["nodes"] if n["name"] == "Claude Claim Check"][0]
       and check_nodes[0]["credentials"]["anthropicApi"]["id"] == "novascoutAnthropic01")
digest_wf = load("variants", "digest-dryrun.json")
for a in [n for n in digest_wf["nodes"] if n["name"] == "Config"][0]["parameters"]["assignments"]["assignments"]:
    if a["name"] == "digest_hour":
        a["value"] = 0   # whatever hour this runs at, today's digest is due
send_wf = load("variants", "send-dryrun.json")
for wf in (draft_wf, fu_wf, digest_wf, send_wf):
    import_wf(wf)
start_sink()
print()

# --- 1. flag ON ----------------------------------------------------------------------------
print("=== 1. settings.auto_approve_email = true ===")
fresh(SEED)
since = max_id()
print("  seeded: leads 50, 78, 104 at contact_found; leads 7 and 91 due follow-up #1")
run = execute("drafting0001t")
gate = out_items(run, "Approval Gate")
show("Approval Gate", [{"lead": g["lead_id"], "needs_check": g["needs_check"],
                        "held": [d.get("hold_reason") for d in g["payload"]["drafts"]]} for g in gate])
show("Write Drafts & Advance", [{k: w[k] for k in ("lead_id", "decisions", "held")} for w in out_items(run, "Write Drafts & Advance")])
checked_ft = out_items(run, "Claude Claim Check") or []
run2 = execute("followup0001t")
show("Write Follow-Up", [{k: w[k] for k in ("lead_id", "status", "approved_by", "hold_reason")}
                         for w in out_items(run2, "Write Follow-Up")])
checked_fu = out_items(run2, "Claude Claim Check") or []
w = written(since)
tally(w.values())
print("  what was written:")
for key, label in (((50, "email"), "lead 50 clean first touch"), ((50, "linkedin"), "lead 50 LinkedIn"),
                   ((104, "email"), "lead 104 tagged first touch"), ((104, "linkedin"), "lead 104 LinkedIn"),
                   ((78, "email"), "lead 78 low-context"), ((78, "linkedin"), "lead 78 low-context LinkedIn"),
                   ((91, "email"), "lead 91 clean follow-up"), ((7, "email"), "lead 7 follow-up = draft 99")):
    if key in w:
        print_row(label, w[key])
    else:
        print("    %-34s NOT WRITTEN" % label)

r = w.get((50, "email"), {})
expect("1. a clean first touch AUTO-APPROVES: approved, approved_by auto, approved_at set, claim check passed",
       (r.get("status"), r.get("approved_by"), r.get("has_approved_at"), r.get("check_result")) == ("approved", "auto", True, "pass"),
       str({k: r.get(k) for k in ("status", "approved_by", "check_result", "hold_reason", "variant")}))
r = w.get((91, "email"), {})
expect("1. a clean follow-up AUTO-APPROVES", (r.get("status"), r.get("approved_by"), r.get("check_result")) == ("approved", "auto", "pass"),
       str({k: r.get(k) for k in ("status", "approved_by", "check_result", "hold_reason", "variant")}))
r = w.get((104, "email"), {})
expect("2. a tagged draft is HELD with its tags, and no claim check was paid for it",
       r.get("status") == "pending" and r.get("approved_by") is None and (r.get("hold_reason") or "").startswith("rule-tags: ")
       and r.get("check_result") is None and not any(g["lead_id"] == 104 and g["needs_check"] for g in gate),
       str({k: r.get(k) for k in ("status", "hold_reason", "variant")}))
r = w.get((7, "email"), {})
widened = [s for s in (r.get("statements") or []) if s["kind"] == "claim" and s["verdict"] == "widened"]
expect("3. draft 99's follow-up text is HELD by the claim check, which names the widened statement",
       r.get("status") == "pending" and r.get("approved_by") is None and r.get("check_result") == "hold"
       and (r.get("hold_reason") or "").startswith("claim-check: ") and "exactly what came in" in (r.get("hold_reason") or "")
       and widened, str({k: r.get(k) for k in ("status", "hold_reason")}))
r = w.get((78, "email"), {})
expect("4. a low-context draft is HELD, without any model call",
       r.get("status") == "pending" and (r.get("hold_reason") or "").startswith("low-context") and r.get("check_result") is None,
       str({k: r.get(k) for k in ("status", "hold_reason", "variant")}))
li = [w.get((lead, "linkedin"), {}) for lead in (50, 78, 104)]
expect("5. every LinkedIn draft is HELD (Section 6) -- including the clean lead's",
       all(x.get("status") == "pending" and (x.get("hold_reason") or "").startswith("linkedin") and x.get("approved_by") is None
           for x in li) and len(li) == 3, str([(x.get("id"), x.get("status"), x.get("hold_reason")) for x in li]))
expect("exactly the email drafts that passed rules 1-4 reached the claim check (50 and the two follow-ups)",
       (len(checked_ft), len(checked_fu)) == (1, 2), "first touch %d, follow-ups %d" % (len(checked_ft), len(checked_fu)))
expect("nothing else came out approved",
       sorted(k for k, x in w.items() if x["status"] == "approved") == [(50, "email"), (91, "email")],
       str(sorted(k for k, x in w.items() if x["status"] == "approved")))

print("  -- Send, unchanged: is an auto-approved draft an ordinary candidate? --")
run = execute("send0001dry")
dec = out_items(run, "Decide Send")[0]
elig = {e["draft_id"]: e for e in dec["eligible"]}
excl = {x["draft_id"]: x["reason"] for x in dec["excluded"]}
show("Decide Send", {"send": dec["send"], "reason": dec["reason"],
                     "eligible": [(e["draft_id"], e["country"], e["local_time"], e["in_window"]) for e in dec["eligible"]],
                     "excluded": sorted(excl.items())})
ids = [w.get(k, {}).get("id", -1) for k in ((50, "email"), (91, "email"))]
expect("both auto-approved drafts pass every Send guard (eligible; business hours and pacing decide the rest)",
       all(i in elig for i in ids), "eligible %s, excluded %s" % (sorted(elig), excl))
held_ids = [w.get(k, {}).get("id", -1) for k in ((7, "email"), (104, "email"), (78, "email"))]
expect("no held draft is even a candidate", not any(i in elig or i in excl for i in held_ids))

print("  -- the Daily Digest --")
before = len(sink_log())
run = execute("digest0001dry")
dg = out_items(run, "Build Digest")[0]
show("Build Digest", {k: dg[k] for k in ("send", "reason", "notify_to", "subject")})
rec = out_items(run, "Record Digest")
show("Record Digest", rec)
notes = sink_log()[before:]
expect("one digest, to the operator address only (no warm-up slot: not external)",
       len(notes) == 1 and notes[0]["rcpt_to"] == ["<%s>" % OPERATOR], str([n["rcpt_to"] for n in notes]))
text = ""
if notes:
    m = sink_message(notes[0]["file"])
    text = m.get_body(preferencelist=("plain",)).get_content()
    print("    Subject: %s" % m.get("Subject"))
    for ln in text.rstrip("\n").split("\n"):
        print("    | " + ln)
expect("the digest lists both auto-approvals under AUTO-APPROVED",
       all(("#%d  " % i) in text.split("SENT (")[0] for i in ids) and "AUTO-APPROVED (2)" in text, "")
expect("... and the held drafts with their reasons: the widened claim, the tags, low-context, LinkedIn",
       "exactly what came in" in text and "rule-tags: " in text and "low-context: " in text and "linkedin: " in text)
expect("recorded once SMTP accepted it", rec and rec[0]["recorded"] == 1)
before = len(sink_log())
run = execute("digest0001dry")
dg2 = out_items(run, "Build Digest")[0]
expect("a second tick the same day sends nothing", not dg2["send"] and dg2["reason"] == "already-sent-today"
       and len(sink_log()) == before and not ran(run, "Send Digest"), dg2["reason"])
print()

# --- 2. flag OFF ----------------------------------------------------------------------------
print("=== 2. settings.auto_approve_email = false -- same leads, same fixtures ===")
fresh(SEED + "UPDATE settings SET value = 'false' WHERE key = 'auto_approve_email';\n")
since = max_id()
run = execute("drafting0001t")
run2 = execute("followup0001t")
w = written(since)
for key in sorted(w):
    print_row("lead %d" % key[0], w[key])
expect("6. nothing auto-approves: every new draft is pending, approved_by empty",
       w and all(x["status"] == "pending" and x["approved_by"] is None for x in w.values()),
       str(sorted((k, x["status"]) for k, x in w.items())))
expect("6. the clean first touch and the clean follow-up are held for the flag",
       all((w.get(k, {}).get("hold_reason") or "").startswith("auto-approve-off") for k in ((50, "email"), (91, "email"))))
expect("6. and no claim check ran in either workflow -- nothing was paid for",
       not ran(run, "Claude Claim Check") and not ran(run2, "Claude Claim Check"))
print()

# --- 3. the database's own guard --------------------------------------------------------------
print("=== 3. the table re-checks: an approval the workflow should never send is turned into a hold ===")
psql(DRY, """
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by, claim_check) VALUES
    (50, 'email', 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1', 's', 'db-guard-1', 'approved', 'auto', '{"result": "pass"}'),
    (50, 'email', 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1+long', 's', 'db-guard-2', 'approved', 'auto', '{"result": "pass"}');""")
row = psql_json(DRY, "SELECT id, status, approved_by, hold_reason FROM drafts WHERE body IN ('db-guard-1', 'db-guard-2') ORDER BY body")
show("direct inserts with the flag off", row)
expect("with the flag off, even a well-formed auto-approval is held by the table itself",
       row[0]["status"] == "pending" and "auto-approve is off" in row[0]["hold_reason"])
expect("and a tagged one says why", "rule tags long" in row[1]["hold_reason"])
print()

# --- 4. stability and the approved drafts ------------------------------------------------------
print("=== 4. the check again: draft 99 a second time, the widening inside a first touch, drafts 100 and 101 ===")
fresh("UPDATE leads SET status = 'contact_found' WHERE id = 91;\nDELETE FROM drafts WHERE variant LIKE 'follow-up-%';\n")
since = max_id()
import_wf(drafting_variant({91: WIDENED_FIRST_TOUCH})[0])
import_wf(followups_variant({7: DRAFT_99, 104: DRAFT_101})[0])
execute("drafting0001t")
execute("followup0001t")
w = written(since)
fresh("DELETE FROM drafts WHERE variant LIKE 'follow-up-%';\nUPDATE leads SET status = 'drafted' WHERE id IN (7, 104);\n")
since2 = max_id()
import_wf(followups_variant({91: DRAFT_100})[0])
execute("followup0001t")
w2 = written(since2)
tally(list(w.values()) + list(w2.values()))
for key, label, src in (((7, "email"), "draft 99's text (2nd run)", w), ((91, "email"), "lead 91 first touch + widening", w),
                        ((104, "email"), "draft 101's text", w), ((91, "email"), "draft 100's text", w2)):
    if key in src:
        print_row(label, src[key])
expect("draft 99's text is held again on a second, independent run",
       w.get((7, "email"), {}).get("status") == "pending" and "exactly what came in" in (w.get((7, "email"), {}).get("hold_reason") or ""))
expect("the same widening inside a first touch is held too (the first-touch path)",
       w.get((91, "email"), {}).get("status") == "pending" and (w.get((91, "email"), {}).get("hold_reason") or "").startswith("claim-check"))
print()

# --- the real database, untouched --------------------------------------------------------------
stop_sink()
real_after = fingerprint(REAL)
show("real novascout AFTER ", real_after)
expect("the real novascout database is the same shape as before the run", real_after == real_before)
cost = COST["input"] * 2 / 1e6 + COST["output"] * 10 / 1e6
print("  claim checks paid for: %d calls, %d input + %d output tokens = $%.4f (Sonnet 5.5, $2/$10 per MTok)"
      % (COST["calls"], COST["input"], COST["output"], cost))
if not KEEP:
    psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);\nDROP DATABASE IF EXISTS %s WITH (FORCE);" % (DRY, BASE))
    print("  scratch databases dropped (pass --keep to inspect them)")
    gone = psql("n8n", "DELETE FROM workflow_entity WHERE id IN (%s) AND name ~ '^(DRY RUN|TEST) - ' RETURNING id;"
                % ", ".join("'%s'" % i for i in TEST_IDS)).split()
    print("  test workflows deleted from n8n: %s" % (", ".join(gone) or "none"))

failed = [x for x in RESULTS if not x[1]]
print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
for label, _, detail in failed:
    print("  FAILED: " + label + ("  -- " + detail if detail else ""))
sys.exit(1 if failed else 0)

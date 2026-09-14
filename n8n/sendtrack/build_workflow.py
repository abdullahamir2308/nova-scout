"""Assemble the Workflow 6 (Send & Track) workflows from the tested Code-node sources.

    n8n/workflows/send.json           send0001       cron -> guard -> claim -> SMTP -> log
    n8n/workflows/mailbox-watch.json  mailwatch0001  IMAP Sent mirror + IMAP reply/opt-out watch
    n8n/workflows/follow-ups.json     followup0001   cron -> follow-up drafts / mark lost

Same contract as the enrichment, scoring, contacts and drafting generators: the
.js files are read verbatim and embedded, so the JS that was tested standalone
is byte-identical to the JS that ships.

Workflow 6 is the first stage that acts outside this machine -- its failure mode
is an email in a stranger's inbox that cannot be recalled -- so the spec is
parsed out of the Master Ref and asserted here rather than retyped:

    Section 5   warm-up table                 -> WARMUP in code_decide.js
    Section 5   opt-out sentence (VERBATIM)   -> code_decide / code_classify_reply / code_followup
    Section 5   URL cap                       -> code_decide.js
    Section 5+9 opt-out keywords              -> code_classify_reply.js (both statements must agree)
    Section 9   follow-up delay and maximum   -> follow-up SQL + code_decide.js + code_followup.js
    Section 12  geographies                   -> COUNTRY_CLOCKS keys (a country with no clock cannot ship)
    Section 6   LinkedIn is manual            -> every send query filters channel='email'
    Section 8   outreach_log / drafts columns -> what the INSERTs may touch
    Section 8   lead status values (LOCKED)   -> every status the SQL writes
    compose     GENERIC_TIMEZONE              -> SENDER_UTC_OFFSET_MIN

Plus wiring guards, for the class of bug Workflow 4 found the hard way: a
Postgres node replaces its input items, and n8n resolves a field nobody emitted
to an empty string without erroring. Every field a node reads from its upstream
is checked, at build time, against what that upstream actually produces.

When a guard fires, fix the code to match the doc -- never the other way round.

Dry-run variants (only when SENDTRACK_VARIANTS_OUT is set) are generated from
the SAME node list with exactly three kinds of change -- credentials pointed at
a scratch database and an SMTP sink, the Config clock/pacing values, and the
schedule trigger removed -- and the build proves that is all that differs.
"""
import copy
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("SENDTRACK_OUT", os.path.join(HERE, "..", "workflows"))
MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md")
)
COMPOSE = os.environ.get("NOVASCOUT_COMPOSE", os.path.join(HERE, "..", "..", "docker-compose.yml"))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
VARIANTS_OUT = os.environ.get("SENDTRACK_VARIANTS_OUT", "")
DRYRUN_NOW = os.environ.get("SENDTRACK_DRYRUN_NOW", "")
FIXTURES = os.environ.get("SENDTRACK_FIXTURES", "")


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}
SMTP_CRED = {"smtp": {"id": "novascoutSmtp01", "name": "SMTP - outreach mailbox"}}
IMAP_CRED = {"imap": {"id": "novascoutImap01", "name": "IMAP - outreach mailbox"}}
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
SMTP_DRY = {"smtp": {"id": "novascoutSmtpDry01", "name": "SMTP - dry-run sink (never delivers)"}}


# ---------------------------------------------------------------------------
# Settings. Environment first, then the repo's .env -- only these keys are read
# from it; the same file holds the mailbox password, which this build has no
# business touching. The n8n SMTP/IMAP credentials hold that, not the workflow.
# ---------------------------------------------------------------------------

_BUILD_SETTINGS = (
    "NOVASCOUT_SENDER_NAME",
    "NOVASCOUT_SENDER_TITLE",
    "NOVASCOUT_SENDER_PHONE",
    "NOVASCOUT_MAILBOX_ADDRESS",
    "NOVASCOUT_OPERATOR_EMAIL",
)


def _env_file_values(path, keys):
    vals = {}
    if not os.path.isfile(path):
        return vals
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in keys:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            vals[k] = v
    return vals


_FILE_SETTINGS = _env_file_values(ENV_FILE, _BUILD_SETTINGS)


def _setting(key):
    v = os.environ.get(key, "").strip()
    if v:
        return v
    return _FILE_SETTINGS.get(key, "").strip()


EMAIL_RE = re.compile(r"^[^\s@<>(),;:\"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

SENDER_NAME = _setting("NOVASCOUT_SENDER_NAME")
SENDER_TITLE = _setting("NOVASCOUT_SENDER_TITLE")
SENDER_PHONE = _setting("NOVASCOUT_SENDER_PHONE")
MAILBOX = _setting("NOVASCOUT_MAILBOX_ADDRESS")
OPERATOR_EMAIL = _setting("NOVASCOUT_OPERATOR_EMAIL")

if not SENDER_NAME:
    raise AssertionError(
        "NOVASCOUT_SENDER_NAME is not set -- not in the environment and not in %s.\n"
        "It is the From name on every send and part of the signature the send path\n"
        "checks each body against. There is deliberately no default." % os.path.abspath(ENV_FILE)
    )
if not EMAIL_RE.match(MAILBOX):
    raise AssertionError(
        "NOVASCOUT_MAILBOX_ADDRESS is %r -- it must be the outreach mailbox address "
        "(Section 5). It is the From address, and its domain decides what counts as an "
        "external send for the warm-up ceiling." % MAILBOX
    )
OWN_DOMAIN = MAILBOX.rsplit("@", 1)[1].lower()
if OPERATOR_EMAIL:
    if not EMAIL_RE.match(OPERATOR_EMAIL):
        raise AssertionError("NOVASCOUT_OPERATOR_EMAIL %r is not an email address" % OPERATOR_EMAIL)
    if OPERATOR_EMAIL.lower() == MAILBOX.lower():
        raise AssertionError(
            "NOVASCOUT_OPERATOR_EMAIL is the outreach mailbox itself. Section 9: the reply "
            "notification goes to 'the operator's own inbox (not the outreach mailbox)'."
        )

# Section 5: "Signature: name, one line of title, phone." Composed exactly as the
# drafting build composes it -- the send path refuses any body not carrying it.
SIGNATURE = "\n".join([p for p in [SENDER_NAME, SENDER_TITLE, SENDER_PHONE] if p])
FROM_HEADER = '"%s" <%s>' % (SENDER_NAME.replace('"', ""), MAILBOX)


# ---------------------------------------------------------------------------
# Spec parsers
# ---------------------------------------------------------------------------

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _number(word, what):
    w = word.lower()
    if w in NUMBER_WORDS:
        return NUMBER_WORDS[w]
    if w.isdigit():
        return int(w)
    raise AssertionError("%s %r in %s is not a number" % (what, word, MASTER_REF))


def load_warmup(doc):
    """Section 5's warm-up table: [(week, max/day)], the last row open-ended."""
    rows = re.findall(r"^\| Week (\d+)(\+?) \| (\d+)", doc, re.M)
    if not rows:
        raise AssertionError("warm-up table not found in %s -- expected '| Week N | M |' rows" % MASTER_REF)
    weeks = [int(w) for w, _, _ in rows]
    if weeks != list(range(1, len(rows) + 1)):
        raise AssertionError("warm-up table weeks are not 1..N in order: %r" % weeks)
    if rows[-1][1] != "+" or any(p for _, p, _ in rows[:-1]):
        raise AssertionError("warm-up table: only the last row may be open-ended ('Week N+'): %r" % rows)
    return [[int(w), int(n)] for w, _, n in rows]


def load_opt_out(doc):
    m = re.search(r"Use a plain sentence:\s*\*\"(.+?)\"\*", doc)
    if not m:
        raise AssertionError(
            "opt-out sentence not found in %s -- expected 'Use a plain sentence: *\"...\"*'" % MASTER_REF)
    return m.group(1)


def load_url_cap(doc):
    m = re.search(r"Maximum (\w+) plain URL", doc)
    if not m:
        raise AssertionError("URL cap not found in Section 5 of %s" % MASTER_REF)
    return _number(m.group(1), "URL cap")


def load_opt_out_keywords(doc):
    """Stated twice -- Section 5's compliance note and Section 9's opt-out line.
    Both must agree, or the spec itself is ambiguous and the build stops."""
    m5 = re.search(r"must detect ((?:\"[a-z]+\",?\s*)+)", doc)
    if not m5:
        raise AssertionError(
            "Section 5 opt-out keywords not found in %s -- expected 'must detect \"no\", ...'" % MASTER_REF)
    m9 = re.search(r"reply matching ([a-z]+(?:/[a-z]+)+)", doc)
    if not m9:
        raise AssertionError(
            "Section 9 opt-out keywords not found in %s -- expected 'reply matching a/b/c'" % MASTER_REF)
    k5 = re.findall(r'"([a-z]+)"', m5.group(1))
    k9 = m9.group(1).split("/")
    if k5 != k9:
        raise AssertionError(
            "the Master Ref states two different opt-out keyword lists -- resolve the doc first:\n"
            "  Section 5: %r\n  Section 9: %r" % (k5, k9))
    return k5


def load_follow_up(doc):
    m = re.search(r"if no reply after (\d+) days", doc)
    if not m:
        raise AssertionError("follow-up delay not found in %s -- expected 'if no reply after N days'" % MASTER_REF)
    n = re.search(r"Maximum (\w+) follow-ups", doc)
    if not n:
        raise AssertionError("follow-up maximum not found in %s -- expected 'Maximum N follow-ups'" % MASTER_REF)
    return int(m.group(1)), _number(n.group(1), "follow-up maximum")


def load_geographies(doc):
    m = re.search(r"\*\*Geographies:\*\*\s*([^\n]+)", doc)
    if not m:
        raise AssertionError("Section 12 geographies not found in %s" % MASTER_REF)
    return [g.strip().rstrip(".") for g in m.group(1).split(",") if g.strip()]


def load_lead_statuses(doc):
    m = re.search(r"\*\*Lead status values \(LOCKED\):\*\*\s*\n`([^`]+)`", doc)
    if not m:
        raise AssertionError("Section 8 lead status values not found in %s" % MASTER_REF)
    return re.findall(r"[a-z_]+", m.group(1))


def load_columns(doc, table):
    m = re.search(r"\n%s\n(.*?)\n\n" % re.escape(table), doc, re.S)
    if not m:
        raise AssertionError("%s table not found in Section 8 of %s" % (table, MASTER_REF))
    cols = []
    for chunk in re.sub(r"\([^)]*\)", "", m.group(1)).replace("\n", " ").split(","):
        name = chunk.strip().split(" ")[0].strip()
        if name:
            cols.append(name)
    if "lead_id" not in cols:
        raise AssertionError("%s column parse looks wrong in %s: %r" % (table, MASTER_REF, cols))
    return cols


# docker-compose's GENERIC_TIMEZONE is the operator's clock -- the IMAP
# pre-flight counted sender-days on it. Only zones with no DST are accepted: a
# fixed offset is exact for those and wrong twice a year for anything else.
_FIXED_ZONES = {"Asia/Karachi": 300, "Asia/Kolkata": 330, "Asia/Dubai": 240, "UTC": 0, "Etc/UTC": 0}


def load_sender_offset(compose_text):
    m = re.search(r"GENERIC_TIMEZONE:\s*([\w/]+)", compose_text)
    if not m:
        raise AssertionError("GENERIC_TIMEZONE not found in %s" % COMPOSE)
    zone = m.group(1)
    if zone not in _FIXED_ZONES:
        raise AssertionError(
            "GENERIC_TIMEZONE is %s, which this build has no fixed offset for. The warm-up "
            "ceiling counts the sender's day on it -- add the zone (only if it has no DST)." % zone)
    return zone, _FIXED_ZONES[zone]


DOC = _read(MASTER_REF)
WARMUP = load_warmup(DOC)
OPT_OUT = load_opt_out(DOC)
MAX_URLS = load_url_cap(DOC)
OPT_OUT_KEYWORDS = load_opt_out_keywords(DOC)
FOLLOW_UP_DAYS, MAX_FOLLOW_UPS = load_follow_up(DOC)
GEOGRAPHIES = load_geographies(DOC)
LEAD_STATUSES = load_lead_statuses(DOC)
OUTREACH_COLUMNS = load_columns(DOC, "outreach_log")
DRAFTS_COLUMNS = load_columns(DOC, "drafts")
SENDER_ZONE, SENDER_OFFSET = load_sender_offset(_read(COMPOSE))


# ---------------------------------------------------------------------------
# Drift guards against the Code-node sources
# ---------------------------------------------------------------------------

def _js_string(src, name, where):
    m = re.search(r'const %s = "(.*?)";' % re.escape(name), src)
    assert m, "%s not found in %s" % (name, where)
    return m.group(1)


def _js_int(src, name, where):
    m = re.search(r"const %s = (-?\d+);" % re.escape(name), src)
    assert m, "%s not found in %s" % (name, where)
    return int(m.group(1))


def _js_block(src, name, where):
    m = re.search(r"const %s = [\[{](.*?)\n[\]}];" % re.escape(name), src, re.S)
    assert m, "%s block not found in %s" % (name, where)
    return m.group(1)


def _assert_decide_matches_spec():
    src = js("code_decide.js")

    found = [[int(a), int(b)] for a, b in re.findall(r"\[(\d+),\s*(\d+)\]", _js_block(src, "WARMUP", "code_decide.js"))]
    assert found == WARMUP, (
        "warm-up schedule drifted between Section 5 and code_decide.js:\n"
        "  doc: %r\n  js:  %r" % (WARMUP, found))

    assert _js_string(src, "OPT_OUT", "code_decide.js") == OPT_OUT, (
        "the opt-out sentence in code_decide.js is not Section 5's, VERBATIM. The send path "
        "refuses any body without it, so a mismatch refuses every draft -- or passes a "
        "paraphrase:\n  doc: %r\n  js:  %r" % (OPT_OUT, _js_string(src, "OPT_OUT", "code_decide.js")))

    found = _js_int(src, "MAX_URLS", "code_decide.js")
    assert found == MAX_URLS, (
        "URL cap drifted between Section 5 and code_decide.js:\n  doc: %d\n  js:  %d" % (MAX_URLS, found))

    found = _js_int(src, "MAX_FOLLOW_UPS", "code_decide.js")
    assert found == MAX_FOLLOW_UPS, (
        "follow-up maximum drifted between Section 9 and code_decide.js:\n  doc: %d\n  js:  %d"
        % (MAX_FOLLOW_UPS, found))

    found = _js_int(src, "SENDER_UTC_OFFSET_MIN", "code_decide.js")
    assert found == SENDER_OFFSET, (
        "the sender clock drifted: docker-compose says %s (%+d min), code_decide.js says %+d min. "
        "The warm-up ceiling counts the sender's day." % (SENDER_ZONE, SENDER_OFFSET, found))

    countries = re.findall(r"^\s*'([^']+)':\s*\{\s*utc_offset_min", _js_block(src, "COUNTRY_CLOCKS", "code_decide.js"), re.M)
    assert sorted(countries) == sorted(GEOGRAPHIES), (
        "COUNTRY_CLOCKS is not Section 12's geography list. A lead in a country with no clock "
        "can never be placed in business hours:\n  doc: %r\n  js:  %r" % (sorted(GEOGRAPHIES), sorted(countries)))


def _assert_classify_matches_spec():
    src = js("code_classify_reply.js")
    m = re.search(r"const OPT_OUT_KEYWORDS = \[(.*?)\];", src)
    assert m, "OPT_OUT_KEYWORDS not found in code_classify_reply.js"
    found = re.findall(r"'([^']+)'", m.group(1))
    assert found == OPT_OUT_KEYWORDS, (
        "opt-out keywords drifted between the Master Ref and code_classify_reply.js. Section 5 "
        "makes detecting them the GDPR/KVKK opt-out mechanism:\n  doc: %r\n  js:  %r"
        % (OPT_OUT_KEYWORDS, found))
    assert _js_string(src, "OPT_OUT", "code_classify_reply.js") == OPT_OUT, (
        "the opt-out sentence code_classify_reply.js strips out of replies is not Section 5's. "
        "Every reply quotes it -- if the strip misses, every reply reads as an opt-out.")


def _assert_followup_matches_spec():
    src = js("code_followup.js")
    assert _js_string(src, "OPT_OUT", "code_followup.js") == OPT_OUT, (
        "the opt-out sentence in code_followup.js is not Section 5's, VERBATIM.")
    found = _js_int(src, "MAX_FOLLOW_UPS", "code_followup.js")
    assert found == MAX_FOLLOW_UPS, (
        "follow-up maximum drifted between Section 9 and code_followup.js:\n  doc: %d\n  js:  %d"
        % (MAX_FOLLOW_UPS, found))
    lines = re.findall(r"^\s*(\d+): '", _js_block(src, "LINES", "code_followup.js"), re.M)
    assert [int(n) for n in lines] == list(range(1, MAX_FOLLOW_UPS + 1)), (
        "code_followup.js has templates for follow-ups %r; Section 9 allows exactly 1..%d"
        % (lines, MAX_FOLLOW_UPS))


_assert_decide_matches_spec()
_assert_classify_matches_spec()
_assert_followup_matches_spec()


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def iso(expr):
    """A timestamptz as an ISO-8601 UTC string, so every clock value crosses into
    the Code nodes in one unambiguous format."""
    return "to_char((%s) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')" % expr


BLOCKED_DOMAIN = ("EXISTS (SELECT 1 FROM blocklist b WHERE {d} = b.domain OR {d} LIKE '%.' || b.domain)")

# The candidate row Decide Send sees for every approved email draft. Kept as a
# list so the wiring guard knows exactly which fields exist.
CANDIDATE_COLUMNS = [
    ("d.id", "draft_id"),
    ("d.lead_id", "lead_id"),
    ("d.channel", "channel"),
    ("d.status", "status"),
    ("d.variant", "variant"),
    ("d.subject", "subject"),
    ("coalesce(d.edited_body, d.body)", "body"),
    ("l.domain", "domain"),
    ("l.country", "country"),
    ("l.status", "lead_status"),
    ("c.email", "to_addr"),
    ("coalesce(c.verified, false)", "verified"),
    (BLOCKED_DOMAIN.format(d="lower(l.domain)"), "lead_domain_blocked"),
    (BLOCKED_DOMAIN.format(d="lower(split_part(c.email, '@', 2))"), "recipient_domain_blocked"),
    ("(SELECT count(*) FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.channel = 'email')::int", "prior_sends"),
    ("(SELECT count(*) FROM mailbox_sent m WHERE m.lead_id = d.lead_id AND m.source = 'sent-folder')::int",
     "prior_manual_sends"),
    ("(SELECT count(*) FROM drafts f WHERE f.lead_id = d.lead_id AND f.channel = 'email' "
     "AND f.status = 'sent' AND f.variant LIKE 'follow-up-%')::int", "follow_ups_sent"),
    ("EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.replied)", "replied"),
    ("EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.outcome = 'bounced')", "bounced"),
]

LOAD_STATE_SQL = """-- Load Send State: everything Decide Send needs, in ONE statement so it is one
-- consistent snapshot.
--
-- $1 now_override -- '' in the shipped workflow (checked at build time); set only
--    in the dry-run variant, so a scenario can be replayed at a fixed instant.
-- $2 min_gap_min, $3 send_probability -- echoed back so Decide reads nothing but
--    its own input. A Postgres node replaces the items; whatever Config set is
--    gone by the time Decide runs (Workflow 4's silent-prompt bug).
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
-- Every outbound message that counts toward the mailbox's daily ceiling: the
-- Sent-folder mirror (the operator's manual sends and the workflow's own), plus
-- claims whose SMTP outcome is not known yet.
counted AS (
  SELECT m.sent_at AS at, m.source AS kind
    FROM mailbox_sent m
   WHERE m.external
  UNION ALL
  SELECT o.sent_at, 'claim'
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NULL
),
cand AS (
  SELECT __CANDIDATE_COLUMNS__
    FROM drafts d
    JOIN leads l ON l.id = d.lead_id
    LEFT JOIN contacts c ON c.lead_id = d.lead_id
   WHERE d.channel = 'email'     -- Section 6: LinkedIn is sent by a human. Always.
     AND d.status = 'approved'   -- Workflow 5: nothing goes out that a human did not approve.
   ORDER BY d.id
   LIMIT 200
)
SELECT __ISO_CLOCK__ AS clock,
       $2::int AS min_gap_min,
       $3::float8 AS send_probability,
       (SELECT __ISO_SYNC__ FROM mailbox_sync WHERE folder = 'Sent') AS sent_mirror_synced_at,
       (SELECT __ISO_MIN__ FROM counted) AS first_external_send_at,
       (SELECT __ISO_MAX__ FROM counted, clk WHERE counted.at <= clk.now) AS last_external_send_at,
       (SELECT coalesce(json_agg(json_build_object('at', __ISO_AT__, 'kind', counted.kind) ORDER BY counted.at), '[]'::json)
          FROM counted, clk
         WHERE counted.at >= clk.now - interval '2 days' AND counted.at < clk.now + interval '2 days') AS recent_sends,
       (SELECT coalesce(json_agg(json_build_object('message_id', m.message_id, 'sent_at', __ISO_M__) ORDER BY m.sent_at), '[]'::json)
          FROM mailbox_sent m
         WHERE m.source = 'workflow' AND NOT m.seen_in_sent_folder) AS unconfirmed_workflow_sends,
       (SELECT coalesce(json_agg(row_to_json(cand)), '[]'::json) FROM cand) AS candidates,
       (SELECT coalesce(json_object_agg(k, n), '{}'::json)
          FROM (SELECT coalesce(channel, '?') || ':' || coalesce(status, '?') AS k, count(*) AS n
                  FROM drafts GROUP BY 1) s) AS drafts_by_status;"""

LOAD_STATE_SQL = (
    LOAD_STATE_SQL
    .replace("__CANDIDATE_COLUMNS__", ",\n         ".join("%s AS %s" % c for c in CANDIDATE_COLUMNS))
    .replace("__ISO_CLOCK__", iso("SELECT now FROM clk"))
    .replace("__ISO_SYNC__", iso("last_synced_at"))
    .replace("__ISO_MIN__", iso("min(counted.at)"))
    .replace("__ISO_MAX__", iso("max(counted.at)"))
    .replace("__ISO_AT__", iso("counted.at"))
    .replace("__ISO_M__", iso("m.sent_at"))
)

CLAIM_SQL = """-- Claim Send: the last line of defence, in one statement under one lock.
--
-- Decide Send chose this draft moments ago. Everything that makes a send
-- permissible is re-checked HERE, atomically, because this is where the draft
-- actually flips -- a bug in Decide can make the workflow send less, never more:
--   Section 6   channel = 'email'
--   Workflow 5  status = 'approved', and the body unchanged since Decide read it
--   Section 9   contact verified, domain and recipient domain not blocklisted,
--               no reply or bounce on this lead
--   Section 5   today's external sends, re-counted, below the ceiling
--
-- The advisory lock is its own statement on purpose. The Postgres node runs
-- this text as one multi-statement query, i.e. one implicit transaction; under
-- READ COMMITTED each statement takes a fresh snapshot, so the claim below is
-- counted AFTER any concurrent claim that held the lock has committed. (Inside
-- a single statement the snapshot would predate the lock, and two overlapping
-- ticks could both see 4 of 5 and both send.)
--
-- The draft flips to 'sent' and the outreach_log claim row is written BEFORE
-- the SMTP call: a crash between sending and logging then leaves a claim, not
-- an 'approved' draft that goes out again next tick. At-most-once, on purpose.
DO $$ BEGIN PERFORM pg_advisory_xact_lock(hashtext('novascout.workflow6.send')); END $$;
WITH p AS (
  SELECT $1::jsonb AS p
),
counted AS (
  SELECT m.message_id AS k
    FROM mailbox_sent m, p
   WHERE m.external
     AND m.sent_at >= (p.p->>'day_start')::timestamptz
     AND m.sent_at <  (p.p->>'day_end')::timestamptz
  UNION ALL
  SELECT 'claim:' || o.id
    FROM outreach_log o, p
   WHERE o.channel = 'email' AND o.message_id IS NULL
     AND o.sent_at >= (p.p->>'day_start')::timestamptz
     AND o.sent_at <  (p.p->>'day_end')::timestamptz
),
d AS (
  UPDATE drafts d
     SET status = 'sent'
    FROM p, leads l, contacts c
   WHERE d.id = (p.p->>'draft_id')::bigint
     AND d.channel = 'email'
     AND d.status = 'approved'
     AND coalesce(d.edited_body, d.body) = p.p->>'raw_body'
     AND l.id = d.lead_id
     AND c.lead_id = d.lead_id
     AND c.verified
     AND lower(c.email) = lower(p.p->>'to_addr')
     AND l.status IN ('drafted', 'approved', 'sent')
     AND NOT __LEAD_BLOCKED__
     AND NOT __RCPT_BLOCKED__
     AND NOT EXISTS (SELECT 1 FROM outreach_log o
                      WHERE o.lead_id = d.lead_id AND (o.replied OR o.outcome = 'bounced'))
     AND (d.variant LIKE 'follow-up-%'
          OR NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.channel = 'email'))
     AND (SELECT count(*) FROM counted) < (p.p->>'ceiling')::int
  RETURNING d.id, d.lead_id
),
o AS (
  INSERT INTO outreach_log (lead_id, draft_id, channel, sent_at, message_body)
  SELECT d.lead_id, d.id, 'email', (p.p->>'clock')::timestamptz, p.p->>'body'
    FROM d, p
  RETURNING id
)
SELECT (SELECT count(*) FROM d) = 1       AS claimed,
       (SELECT id FROM o)                  AS outreach_id,
       (p.p->>'draft_id')::bigint          AS draft_id,
       (p.p->>'lead_id')::bigint           AS lead_id,
       p.p->>'to_addr'                     AS to_addr,
       p.p->>'subject'                     AS subject,
       p.p->>'body'                        AS body,
       p.p->>'clock'                       AS clock,
       (SELECT count(*) FROM counted)::int AS sent_today_before,
       (p.p->>'ceiling')::int              AS ceiling
  FROM p;""".replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
).replace(
    "__RCPT_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(split_part(c.email, '@', 2))")
)

CONFIRM_SQL = """-- Confirm Send: SMTP accepted the message. Record its Message-ID on the claim
-- (replies thread on it), advance the lead, and put the send in mailbox_sent
-- NOW -- so it counts toward the ceiling before the Sent-folder mirror notices
-- it. When the mirror does, it only sets seen_in_sent_folder on this row.
WITH p AS (
  SELECT $1::jsonb AS p
),
o AS (
  UPDATE outreach_log o
     SET message_id = p.p->>'message_id'
    FROM p
   WHERE o.id = (p.p->>'outreach_id')::bigint
     AND o.message_id IS NULL
  RETURNING o.id, o.lead_id, o.sent_at
),
l AS (
  UPDATE leads l
     SET status = 'sent', updated_at = now()
    FROM o
   WHERE l.id = o.lead_id
     AND l.status IN ('drafted', 'approved')
  RETURNING l.id
),
m AS (
  INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source)
  SELECT p.p->>'message_id', o.sent_at, ARRAY[p.p->>'to_addr'], (p.p->>'external')::boolean, o.lead_id, 'workflow'
    FROM o, p
  ON CONFLICT (message_id) DO UPDATE SET lead_id = EXCLUDED.lead_id, source = 'workflow'
  RETURNING message_id
)
SELECT (SELECT count(*) FROM o)::int AS confirmed,
       (SELECT count(*) FROM l)::int AS lead_advanced,
       (SELECT message_id FROM m)    AS message_id,
       (p.p->>'draft_id')::bigint    AS draft_id
  FROM p;"""

REVERT_SQL = """-- Revert Claim: SMTP did not accept the message, so nothing went out. Remove
-- the claim (it would otherwise count toward the ceiling forever) and hand the
-- draft back:
--   account failure   -> 'approved'; the next tick retries
--   recipient failure -> 'pending' + '+smtp-rejected', back to a human; retrying
--                        a refused address would stall the whole queue behind it
WITH p AS (
  SELECT $1::jsonb AS p
),
o AS (
  DELETE FROM outreach_log o
   USING p
   WHERE o.id = (p.p->>'outreach_id')::bigint
     AND o.message_id IS NULL
  RETURNING o.id
),
d AS (
  UPDATE drafts d
     SET status  = CASE WHEN p.p->>'failure' = 'recipient' THEN 'pending' ELSE 'approved' END,
         variant = CASE WHEN p.p->>'failure' = 'recipient' AND coalesce(d.variant, '') NOT LIKE '%smtp-rejected%'
                        THEN coalesce(d.variant, '') || '+smtp-rejected'
                        ELSE d.variant END
    FROM p
   WHERE d.id = (p.p->>'draft_id')::bigint
     AND d.status = 'sent'
  RETURNING d.status, d.variant
)
SELECT (SELECT count(*) FROM o)::int AS claim_removed,
       (SELECT status FROM d)         AS draft_status,
       (SELECT variant FROM d)        AS draft_variant,
       p.p->>'failure'                AS failure,
       p.p->>'error'                  AS error
  FROM p;"""

MIRROR_SQL = """-- Mirror Sent: one trigger batch of the Sent folder into mailbox_sent, and the
-- time of this read into mailbox_sync. Keyed on Message-ID, so the full re-read
-- on every activation is a no-op for anything already known; a message the
-- workflow sent itself only gets seen_in_sent_folder = true -- the mirror's
-- proof of life the send path checks.
WITH p AS (
  SELECT $1::jsonb AS p
),
r AS (
  SELECT x->>'message_id'                                  AS message_id,
         (x->>'sent_at')::timestamptz                      AS sent_at,
         ARRAY(SELECT jsonb_array_elements_text(x->'recipients')) AS recipients,
         (x->>'external')::boolean                         AS external
    FROM p, jsonb_array_elements(p.p->'rows') AS x
),
up AS (
  INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source, seen_in_sent_folder)
  SELECT r.message_id, r.sent_at, r.recipients, r.external,
         -- A manual send to a prospect links to the lead, so the send path will
         -- not follow it with a duplicate first touch.
         coalesce(
           (SELECT c.lead_id FROM contacts c WHERE lower(c.email) = ANY (r.recipients) ORDER BY c.lead_id LIMIT 1),
           (SELECT l.id FROM leads l
             WHERE lower(l.domain) = ANY (SELECT split_part(x, '@', 2) FROM unnest(r.recipients) AS x)
             ORDER BY l.id LIMIT 1)),
         'sent-folder', true
    FROM r
  ON CONFLICT (message_id) DO UPDATE SET seen_in_sent_folder = true
  RETURNING (xmax = 0) AS inserted
),
s AS (
  INSERT INTO mailbox_sync (folder, last_synced_at, messages_seen, undated)
  SELECT 'Sent', now(), (p.p->>'seen')::int, (p.p->>'undated')::int
    FROM p
  ON CONFLICT (folder) DO UPDATE
     SET last_synced_at = EXCLUDED.last_synced_at,
         messages_seen  = EXCLUDED.messages_seen,
         undated        = EXCLUDED.undated
  RETURNING last_synced_at
)
SELECT (SELECT count(*) FROM up WHERE inserted)::int     AS new_rows,
       (SELECT count(*) FROM up WHERE NOT inserted)::int AS already_known,
       (p.p->>'undated')::int                            AS undated,
       (SELECT __ISO_S__ FROM s)                         AS synced_at
  FROM p;""".replace("__ISO_S__", iso("s.last_synced_at"))

RECORD_INBOUND_SQL = """-- Record Inbound: match one INBOX message to a lead, record it once, and act on
-- it in the same statement.
--
-- Matching, strongest first: our Message-ID in its In-Reply-To/References (or,
-- for a bounce, anywhere in the NDR); the contact's own address; the address a
-- bounce names; the lead's domain -- never a freemail or our own domain. Only
-- leads this workflow has actually emailed can match.
--
-- inbound_messages is keyed on Message-ID and every action below hangs off its
-- INSERT ... RETURNING, so a message the IMAP trigger delivers twice changes
-- nothing and notifies nobody the second time.
WITH p AS (
  SELECT $1::jsonb AS p
),
hit AS (
  SELECT h.lead_id, h.how
    FROM (
      SELECT o.lead_id, 'thread' AS how, 1 AS rank
        FROM outreach_log o, p
       WHERE o.message_id IN (SELECT jsonb_array_elements_text(p.p->'thread_ids'))
      UNION ALL
      SELECT c.lead_id, 'contact-email', 2
        FROM contacts c, p
       WHERE lower(c.email) = lower(p.p->>'from_addr')
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = c.lead_id AND o.channel = 'email')
      UNION ALL
      SELECT c.lead_id, 'bounced-address', 3
        FROM contacts c, p
       WHERE p.p->>'classification' = 'bounce'
         AND lower(c.email) IN (SELECT jsonb_array_elements_text(p.p->'mentioned_addrs'))
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = c.lead_id AND o.channel = 'email')
      UNION ALL
      SELECT l.id, 'lead-domain', 4
        FROM leads l, p
       WHERE NOT (p.p->>'freemail')::boolean
         AND NOT (p.p->>'own_domain')::boolean
         AND lower(l.domain) = p.p->>'from_domain'
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND o.channel = 'email')
    ) h
   ORDER BY h.rank, h.lead_id
   LIMIT 1
),
ins AS (
  INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by,
                                classification, opt_out_keyword, body_excerpt)
  SELECT p.p->>'message_id', (p.p->>'received_at')::timestamptz, p.p->>'from_addr', p.p->>'subject',
         (SELECT lead_id FROM hit), (SELECT how FROM hit),
         CASE WHEN (SELECT lead_id FROM hit) IS NULL THEN 'unmatched' ELSE p.p->>'classification' END,
         p.p->>'opt_out_keyword', p.p->>'body_excerpt'
    FROM p
  ON CONFLICT (message_id) DO NOTHING
  RETURNING lead_id, classification, received_at
),
last_send AS (
  SELECT o.id
    FROM outreach_log o
   WHERE o.lead_id = (SELECT lead_id FROM ins)
     AND o.channel = 'email' AND o.message_id IS NOT NULL
   ORDER BY o.sent_at DESC
   LIMIT 1
),
logged AS (
  UPDATE outreach_log o
     SET replied    = CASE WHEN i.classification IN ('reply', 'opt-out') THEN true ELSE o.replied END,
         replied_at = CASE WHEN i.classification IN ('reply', 'opt-out')
                           THEN coalesce(o.replied_at, i.received_at, now()) ELSE o.replied_at END,
         reply_body = CASE WHEN i.classification IN ('reply', 'opt-out')
                           THEN coalesce(o.reply_body, p.p->>'reply_text') ELSE o.reply_body END,
         outcome    = CASE i.classification
                        WHEN 'opt-out' THEN 'opt-out:' || (p.p->>'opt_out_keyword')
                        WHEN 'reply'   THEN coalesce(o.outcome, 'replied')
                        WHEN 'bounce'  THEN 'bounced'
                        ELSE o.outcome END
    FROM ins i, p
   WHERE o.id = (SELECT id FROM last_send)
     AND i.classification IN ('reply', 'opt-out', 'bounce')
  RETURNING o.id
),
advanced AS (
  -- Section 9: "Any reply -> kill follow-up sequence, set status='replied'".
  -- A bounce or an out-of-office is not a reply and does not do this.
  UPDATE leads l
     SET status = 'replied', updated_at = now()
    FROM ins i
   WHERE l.id = i.lead_id
     AND i.classification IN ('reply', 'opt-out')
     AND l.status IN ('sent', 'lost')
  RETURNING l.id
),
blocked AS (
  -- Section 5 / Section 9: an opt-out blocklists the domain, immediately and
  -- permanently -- the lead's domain, and the sender's own if it is a company
  -- domain that differs from it.
  INSERT INTO blocklist (domain, reason)
  SELECT DISTINCT v.dom, 'opt-out: replied "' || (p.p->>'opt_out_keyword') || '" -- ' || (p.p->>'message_id')
    FROM ins i
    JOIN leads l ON l.id = i.lead_id
    CROSS JOIN p
    CROSS JOIN LATERAL (VALUES
      (lower(l.domain)),
      (CASE WHEN NOT (p.p->>'freemail')::boolean AND NOT (p.p->>'own_domain')::boolean
            THEN p.p->>'from_domain' END)) AS v(dom)
   WHERE i.classification = 'opt-out'
     AND v.dom IS NOT NULL AND v.dom <> ''
  ON CONFLICT (domain) DO NOTHING
  RETURNING domain
)
SELECT ((SELECT count(*) FROM ins) = 1
        AND (SELECT lead_id FROM ins) IS NOT NULL
        AND (SELECT classification FROM ins) IN ('reply', 'opt-out', 'bounce')) AS notify,
       coalesce((SELECT classification FROM ins), 'already-recorded')           AS classification,
       p.p->>'opt_out_keyword'                                                  AS opt_out_keyword,
       l.company_name                                                           AS company_name,
       l.domain                                                                 AS domain,
       l.country                                                                AS country,
       s.fit_score                                                              AS fit_score,
       c.email                                                                  AS contact_email,
       p.p->>'from_addr'                                                        AS from_addr,
       p.p->>'subject'                                                          AS subject,
       p.p->>'received_at'                                                      AS received_at,
       (SELECT how FROM hit)                                                    AS matched_by,
       p.p->>'body_excerpt'                                                     AS body_excerpt,
       coalesce((SELECT array_agg(domain ORDER BY domain) FROM blocked), '{}')  AS blocklisted,
       (SELECT lead_id FROM ins)                                                AS lead_id,
       (SELECT count(*) FROM logged)::int                                       AS outreach_updated,
       (SELECT count(*) FROM advanced)::int                                     AS lead_updated
  FROM p
  LEFT JOIN leads l    ON l.id = (SELECT lead_id FROM hit)
  LEFT JOIN scores s   ON s.lead_id = l.id
  LEFT JOIN contacts c ON c.lead_id = l.id;"""

FOLLOWUP_DUE_SQL = """-- Find Due Follow-Ups -- Section 9: "if no reply after N days, generate
-- follow-up draft back into the review queue. Maximum M follow-ups, then mark
-- lost." Queue-driven like every stage (Section 7): whatever is due, whenever
-- this runs.
--
-- $1 now_override ('' in the shipped workflow), $2 days, $3 maximum, $4 batch.
--
-- The clock restarts at the later of the last send and the last follow-up
-- drafted, so a follow-up the reviewer rejected does not spawn the next one the
-- same afternoon. A rejected follow-up still uses its slot: the reviewer
-- decided against it, and regenerating the same template would ask again.
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
sends AS (
  SELECT o.lead_id, o.sent_at, o.message_body, o.draft_id
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL
),
first_touch AS (
  SELECT DISTINCT ON (s.lead_id) s.lead_id, s.sent_at, s.message_body, d.subject
    FROM sends s
    JOIN drafts d ON d.id = s.draft_id
   WHERE coalesce(d.variant, '') NOT LIKE 'follow-up-%'
   ORDER BY s.lead_id, s.sent_at
),
last_send AS (
  SELECT lead_id, max(sent_at) AS sent_at FROM sends GROUP BY lead_id
),
fu AS (
  SELECT d.lead_id,
         count(*)::int                                                  AS created,
         count(*) FILTER (WHERE d.status IN ('pending', 'approved'))::int AS open,
         max(d.created_at)                                              AS last_created
    FROM drafts d
   WHERE d.channel = 'email' AND d.variant LIKE 'follow-up-%'
   GROUP BY d.lead_id
)
SELECT l.id                                                             AS lead_id,
       CASE WHEN coalesce(fu.created, 0) >= $3::int THEN 'mark-lost' ELSE 'draft-follow-up' END AS action,
       coalesce(fu.created, 0) + 1                                      AS next_follow_up,
       ft.subject                                                       AS first_subject,
       ft.message_body                                                  AS first_body,
       __ISO_FIRST__                                                    AS first_sent_at,
       __ISO_LAST__                                                     AS last_sent_at
  FROM leads l
  JOIN first_touch ft ON ft.lead_id = l.id
  JOIN last_send ls   ON ls.lead_id = l.id
  LEFT JOIN fu        ON fu.lead_id = l.id
  CROSS JOIN clk
 WHERE l.status = 'sent'
   AND greatest(ls.sent_at, coalesce(fu.last_created, ls.sent_at)) <= clk.now - make_interval(days => $2::int)
   AND coalesce(fu.open, 0) = 0
   AND NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND (o.replied OR o.outcome = 'bounced'))
   AND NOT EXISTS (SELECT 1 FROM inbound_messages i
                    WHERE i.lead_id = l.id AND i.classification IN ('reply', 'opt-out', 'bounce'))
   AND NOT __LEAD_BLOCKED__
 ORDER BY l.id
 LIMIT $4::int;""".replace(
    "__ISO_FIRST__", iso("ft.sent_at")
).replace(
    "__ISO_LAST__", iso("ls.sent_at")
).replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
)

FOLLOWUP_WRITE_SQL = """-- Write Follow-Up: insert the follow-up draft as 'pending' (the reviewer's
-- queue), or mark the lead lost. Both guarded so a repeated run is a no-op: a
-- lead gets each follow-up variant at most once, and only a lead still at
-- 'sent' moves.
WITH p AS (
  SELECT $1::jsonb AS p
),
ins AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status)
  SELECT (p.p->>'lead_id')::bigint, 'email', p.p->'draft'->>'variant', p.p->'draft'->>'subject',
         p.p->'draft'->>'body', 'pending'
    FROM p
   WHERE p.p->>'action' = 'draft-follow-up'
     AND p.p->'draft'->>'channel' = 'email'
     AND NOT EXISTS (SELECT 1 FROM drafts d
                      WHERE d.lead_id = (p.p->>'lead_id')::bigint
                        AND d.variant = p.p->'draft'->>'variant')
     AND EXISTS (SELECT 1 FROM leads l WHERE l.id = (p.p->>'lead_id')::bigint AND l.status = 'sent')
  RETURNING id, variant
),
lost AS (
  UPDATE leads l
     SET status = 'lost', updated_at = now()
    FROM p
   WHERE p.p->>'action' = 'mark-lost'
     AND l.id = (p.p->>'lead_id')::bigint
     AND l.status = 'sent'
  RETURNING l.id
)
SELECT (p.p->>'lead_id')::bigint   AS lead_id,
       p.p->>'action'              AS action,
       (SELECT id FROM ins)        AS draft_id,
       (SELECT variant FROM ins)   AS variant,
       (SELECT count(*) FROM lost)::int AS marked_lost
  FROM p;"""


# ---------------------------------------------------------------------------
# SQL guards
# ---------------------------------------------------------------------------

def final_select_aliases(sql):
    """Output column names of the statement's final SELECT (depth-0 `AS name`)."""
    start = sql.rfind("\nSELECT ")
    assert start != -1, "no final SELECT found"
    body = sql[start:]
    names, depth, i = [], 0, 0
    while i < len(body):
        ch = body[i]
        if ch == "'":
            j = body.index("'", i + 1)
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            m = re.match(r"\bAS\s+(\w+)", body[i:])
            if m and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_")):
                names.append(m.group(1))
                i += m.end()
                continue
        i += 1
    return names


# Section 6, the one rule that does not change regardless of tooling.
for _name, _sql, _needle in [
    ("Load Send State", LOAD_STATE_SQL, "d.channel = 'email'"),
    ("Claim Send", CLAIM_SQL, "d.channel = 'email'"),
]:
    assert _needle in _sql, (
        "%s no longer filters on %s. Section 6: 'LinkedIn sending is manual. Always. No "
        "exceptions.' -- the send path must never be able to see a LinkedIn draft." % (_name, _needle))
assert "d.status = 'approved'" in LOAD_STATE_SQL and "d.status = 'approved'" in CLAIM_SQL, (
    "the send path no longer requires status='approved'. Workflow 5's human gate is the "
    "whole point of the review queue.")
assert "< (p.p->>'ceiling')::int" in CLAIM_SQL, (
    "Claim Send no longer re-counts the Section 5 ceiling under its lock.")
assert CLAIM_SQL.index("pg_advisory_xact_lock") < CLAIM_SQL.index("WITH p AS"), (
    "the advisory lock must be a separate statement BEFORE the claim, or the claim's "
    "snapshot predates the lock and two ticks can both count 4 of 5.")

_insert_cols = [c.strip() for c in re.search(r"INSERT INTO outreach_log \(([^)]+)\)", CLAIM_SQL).group(1).split(",")]
_unknown = [c for c in _insert_cols if c not in OUTREACH_COLUMNS]
assert not _unknown, (
    "the outreach_log INSERT writes %r, which Section 8 of %s does not define. Update the "
    "doc first." % (_unknown, MASTER_REF))
_updated = re.findall(r"UPDATE outreach_log o\s+SET (.*?)\n\s+(?:FROM|WHERE)", CONFIRM_SQL + RECORD_INBOUND_SQL, re.S)
for _block in _updated:
    for _col in re.findall(r"(?:^|,)\s*(\w+)\s*=", _block, re.M):
        assert _col in OUTREACH_COLUMNS, (
            "an UPDATE sets outreach_log.%s, which Section 8 does not define." % _col)
_insert_cols = [c.strip() for c in re.search(r"INSERT INTO drafts \(([^)]+)\)", FOLLOWUP_WRITE_SQL).group(1).split(",")]
_unknown = [c for c in _insert_cols if c not in DRAFTS_COLUMNS]
assert not _unknown, (
    "the follow-up INSERT writes drafts.%r, which Section 8 does not define." % _unknown)

_ALL_SQL = [LOAD_STATE_SQL, CLAIM_SQL, CONFIRM_SQL, REVERT_SQL, MIRROR_SQL, RECORD_INBOUND_SQL,
            FOLLOWUP_DUE_SQL, FOLLOWUP_WRITE_SQL]
for _sql in _ALL_SQL:
    for _status in re.findall(r"UPDATE leads l\s+SET status = '(\w+)'", _sql):
        assert _status in LEAD_STATUSES, (
            "Workflow 6 writes leads.status = %r, which is not in Section 8's LOCKED status "
            "list %r." % (_status, LEAD_STATUSES))


# ---------------------------------------------------------------------------
# Wiring guards -- every field a node reads must be produced upstream
# ---------------------------------------------------------------------------

def _reads(src, var):
    return set(re.findall(r"\b%s\.(\w+)" % re.escape(var), src))


def _sql_payload_reads(sql, prefix="p.p"):
    fields = set(re.findall(re.escape(prefix) + r"->>?'(\w+)'", sql))
    fields.discard("draft")
    return fields


def _js_emits(src, field):
    return re.search(r"\b%s:" % re.escape(field), src) is not None


def _assert_wired(consumer, fields, produced, producer):
    missing = sorted(f for f in fields if f not in produced)
    assert not missing, (
        "%s reads %r, but %s does not produce %s. n8n resolves a missing field to an empty "
        "value without erroring -- this is Workflow 4's silent-prompt bug again."
        % (consumer, sorted(fields), producer, missing))


def _assert_js_emits(consumer, fields, js_src, producer):
    missing = sorted(f for f in fields if not _js_emits(js_src, f))
    assert not missing, (
        "%s reads %r, but %s never emits %s -- it would arrive empty, silently."
        % (consumer, sorted(fields), producer, missing))


_decide = js("code_decide.js")
_assert_wired("Decide Send (state.*)", _reads(_decide, "state"), final_select_aliases(LOAD_STATE_SQL), "Load Send State")
_assert_wired("Decide Send (candidate c.*)", _reads(_decide, "c"), [a for _, a in CANDIDATE_COLUMNS], "the candidate query")
_assert_js_emits("Claim Send", _sql_payload_reads(CLAIM_SQL), _decide, "code_decide.js's payload")
SEND_EMAIL_READS = {"to_addr", "subject", "body"}
_assert_wired("Send Email", SEND_EMAIL_READS, final_select_aliases(CLAIM_SQL), "Claim Send")
_check = js("code_check_smtp.js")
_assert_wired("Check SMTP Result (claim.*)", _reads(_check, "claim"), final_select_aliases(CLAIM_SQL), "Claim Send")
_assert_js_emits("Confirm Send", _sql_payload_reads(CONFIRM_SQL), _check, "code_check_smtp.js")
_assert_js_emits("Revert Claim", _sql_payload_reads(REVERT_SQL), _check, "code_check_smtp.js")
_mirror = js("code_mirror_sent.js")
_assert_js_emits("Mirror Sent", _sql_payload_reads(MIRROR_SQL) | set(re.findall(r"x->>?'(\w+)'", MIRROR_SQL)),
                 _mirror, "code_mirror_sent.js")
_classify = js("code_classify_reply.js")
_assert_js_emits("Record Inbound", _sql_payload_reads(RECORD_INBOUND_SQL), _classify, "code_classify_reply.js")
_notify = js("code_notify.js")
_assert_wired("Build Notification (r.*)", _reads(_notify, "r"), final_select_aliases(RECORD_INBOUND_SQL), "Record Inbound")
NOTIFY_EMAIL_READS = {"notify_to", "subject", "text"}
_assert_js_emits("Notify Operator", NOTIFY_EMAIL_READS, _notify, "code_notify.js")
_followup = js("code_followup.js")
_assert_wired("Build Follow-Up (r.*)", _reads(_followup, "r"), final_select_aliases(FOLLOWUP_DUE_SQL), "Find Due Follow-Ups")
_assert_js_emits("Write Follow-Up", _sql_payload_reads(FOLLOWUP_WRITE_SQL) |
                 set(re.findall(r"p\.p->'draft'->>'(\w+)'", FOLLOWUP_WRITE_SQL)), _followup, "code_followup.js")


# ---------------------------------------------------------------------------
# Code nodes with their build constants baked in
# ---------------------------------------------------------------------------

def bake(name, **subs):
    src = js(name)
    for key, val in subs.items():
        token = "__%s__" % key
        assert token in src, "%s has no %s placeholder" % (name, token)
        src = src.replace(token, json.dumps(val, ensure_ascii=False))
    left = re.findall(r"__[A-Z_]+__", src)
    assert not left, "%s still has unsubstituted placeholders %r" % (name, left)
    return src


DECIDE_JS = bake("code_decide.js", SIGNATURE=SIGNATURE)
CHECK_JS = bake("code_check_smtp.js", OWN_DOMAIN=OWN_DOMAIN)
MIRROR_JS = bake("code_mirror_sent.js", OWN_DOMAIN=OWN_DOMAIN)
CLASSIFY_JS = bake("code_classify_reply.js", OWN_DOMAIN=OWN_DOMAIN)
NOTIFY_JS = bake("code_notify.js", OPERATOR_EMAIL=OPERATOR_EMAIL)
FOLLOWUP_JS = bake("code_followup.js", SIGNATURE=SIGNATURE, SENDER_NAME=SENDER_NAME)


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def boolean_condition(cid, expr):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{
            "id": cid,
            "leftValue": expr,
            "rightValue": True,
            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
        }],
        "combinator": "and",
    }


def if_node(name, cid, expr, pos, notes):
    return {"parameters": {"conditions": boolean_condition(cid, expr), "options": {}},
            "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos, "notes": notes}


def code_node(name, src, mode, pos, notes):
    return {"parameters": {"mode": mode, "jsCode": src}, "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": pos, "notes": notes}


def pg_node(name, sql, replacement, pos, notes):
    return {"parameters": {"operation": "executeQuery", "query": sql,
                           "options": {"queryReplacement": replacement}},
            "name": name, "type": "n8n-nodes-base.postgres", "typeVersion": 2.7, "position": pos,
            "credentials": PG_CRED, "notes": notes}


def email_node(name, to_expr, subject_expr, text_expr, pos, notes):
    # Section 5: "Plain text only ... No open tracking. No link tracking. No
    # pixels." emailFormat 'text' sends no HTML part at all. appendAttribution
    # MUST be explicitly false: at typeVersion 2.1 the node appends "This email
    # was sent automatically with n8n" plus a link when the option is absent --
    # verified in the installed node's send.operation.js.
    return {
        "parameters": {
            "fromEmail": FROM_HEADER,
            "toEmail": to_expr,
            "subject": subject_expr,
            "emailFormat": "text",
            "text": text_expr,
            "options": {"appendAttribution": False},
        },
        "name": name, "type": "n8n-nodes-base.emailSend", "typeVersion": 2.1, "position": pos,
        "credentials": SMTP_CRED, "onError": "continueRegularOutput", "notes": notes,
    }


def imap_trigger(name, mailbox, track_last, pos, notes):
    return {
        "parameters": {
            "mailbox": mailbox,
            # Never "Mark as Read": the operator reads this mailbox too, and the
            # workflow must not change what they see.
            "postProcessAction": "nothing",
            "format": "simple",
            "downloadAttachments": False,
            "options": {"customEmailConfig": '["ALL"]', "forceReconnect": 30, "trackLastMessageId": track_last},
        },
        "name": name, "type": "n8n-nodes-base.emailReadImap", "typeVersion": 2.2, "position": pos,
        "credentials": IMAP_CRED, "notes": notes,
    }


def edge(*targets):
    return {"main": [[{"node": t, "type": "main", "index": 0} for t in targets]]}


def branch(true_target, false_target=None):
    out = [[{"node": true_target, "type": "main", "index": 0}]]
    out.append([{"node": false_target, "type": "main", "index": 0}] if false_target else [])
    return {"main": out}


# ---------------------------------------------------------------------------
# Workflow: Send
# ---------------------------------------------------------------------------

SEND_CONFIG = {"now_override": "", "min_gap_min": 20, "send_probability": 0.4}

send_nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}},
        "name": "Every 10 Minutes", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
        "position": [-1100, 40],
        "notes": (
            "Section 9: cron, business hours only, randomised intervals. It fires around the clock on "
            "purpose: business hours are the RECIPIENT's (UTC-6 to UTC+5:30 across Section 12), so the "
            "hour filter lives in Decide Send, per lead. At most one message per tick. Activation is a UI "
            "Publish action, never the CLI (Section 3)."),
    },
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": SEND_CONFIG["now_override"], "type": "string"},
            {"id": "mingap", "name": "min_gap_min", "value": SEND_CONFIG["min_gap_min"], "type": "number"},
            {"id": "prob", "name": "send_probability", "value": SEND_CONFIG["send_probability"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 130],
        "notes": (
            "Pacing, not policy: a 20-minute floor since the mailbox's last external send, then a 40% "
            "coin flip per 10-minute tick -- irregular gaps of roughly 20-60 minutes (Section 5: 'never a "
            "synchronised burst'). The warm-up ceiling is NOT here; it is parsed from Section 5 into "
            "Decide Send and re-checked in Claim Send.\n\nnow_override must stay empty. It exists so the "
            "dry-run variant can replay a scenario at a fixed instant; the build refuses a shipped "
            "workflow with it set."),
    },
    pg_node("Load Send State", LOAD_STATE_SQL,
            "={{ [$json.now_override, $json.min_gap_min, $json.send_probability] }}", [-660, 130],
            "One row, one snapshot: the Sent-folder mirror, today's sends and claims, every approved EMAIL "
            "draft with the facts each guard needs. LinkedIn drafts are not selected -- not filtered later, "
            "never read (Section 6)."),
    code_node("Decide Send", DECIDE_JS, "runOnceForAllItems", [-440, 130],
              "Every decision, from one row and one clock: Sent mirror trustworthy -> warm-up week and ceiling "
              "(Section 5, parsed from the doc) -> per-draft checks -> recipient business hours -> pacing. "
              "Emits exactly one item: send true with one payload, or send false with the reason."),
    if_node("Send Now?", "send", "={{ $json.send }}", [-220, 130],
            "False ends the tick. The reason is in Decide Send's output for anyone reading the execution."),
    pg_node("Claim Send", CLAIM_SQL, "={{ [JSON.stringify($json.payload)] }}", [0, 40],
            "The last line of defence: channel, approval, unchanged body, verified contact, blocklist, reply "
            "and the Section 5 ceiling, all re-checked in one statement under an advisory lock, and the draft "
            "flipped to 'sent' with a claim row BEFORE the SMTP call (at-most-once)."),
    if_node("Claimed?", "claimed", "={{ $json.claimed }}", [220, 40],
            "False means the draft changed between Decide and Claim, or the recount hit the ceiling. Nothing "
            "is sent."),
    email_node("Send Email", "={{ $json.to_addr }}", "={{ $json.subject }}", "={{ $json.body }}", [440, -40],
               "SMTP -> the outreach mailbox (Section 2). Plain text, no attribution, one recipient. Continue-on-"
               "error so a failure reaches Check SMTP Result instead of stranding the claim. No retry: a timeout "
               "after the server accepted would send twice."),
    code_node("Check SMTP Result", CHECK_JS, "runOnceForEachItem", [660, -40],
              "ok -> confirm. account failure (auth, TLS, DNS, 4xx) -> back to approved, retried next tick. "
              "recipient failure (5xx on the address) -> back to pending, tagged +smtp-rejected, for a human."),
    if_node("Sent OK?", "ok", "={{ $json.ok }}", [880, -40], "Accepted by the server, with a Message-ID."),
    pg_node("Confirm Send", CONFIRM_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, -120],
            "Message-ID onto the claim, lead -> 'sent', and the send into mailbox_sent immediately so it counts "
            "before the Sent mirror notices it."),
    pg_node("Revert Claim", REVERT_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, 40],
            "Nothing went out: the claim is removed and the draft handed back."),
]

send_connections = {
    "Every 10 Minutes": edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Load Send State"),
    "Load Send State": edge("Decide Send"),
    "Decide Send": edge("Send Now?"),
    "Send Now?": branch("Claim Send"),
    "Claim Send": edge("Claimed?"),
    "Claimed?": branch("Send Email"),
    "Send Email": edge("Check SMTP Result"),
    "Check SMTP Result": edge("Sent OK?"),
    "Sent OK?": branch("Confirm Send", "Revert Claim"),
}

# ---------------------------------------------------------------------------
# Workflow: Mailbox Watch
# ---------------------------------------------------------------------------

mailwatch_nodes = [
    imap_trigger("Sent Folder", "Sent", False, [-880, -100],
                 "The warm-up ceiling's input. Reads the WHOLE Sent folder on every activation (Fetch Only New "
                 "Emails off), so manual sends made while n8n was down are counted; then each new message as it "
                 "is filed. Keyed on Message-ID, so re-reads are no-ops."),
    code_node("Normalise Sent", MIRROR_JS, "runOnceForAllItems", [-660, -100],
              "The IMAP pre-flight's reading: when each message went, and whether it went outside "
              + OWN_DOMAIN + ". Only external sends warm the domain, so only they count."),
    pg_node("Mirror Sent", MIRROR_SQL, "={{ [JSON.stringify($json.payload)] }}", [-440, -100],
            "Upsert into mailbox_sent; stamp mailbox_sync. The send path refuses to send until this has run "
            "at least once, and stops if its own sends stop appearing here."),
    imap_trigger("Inbox", "INBOX", True, [-880, 160],
                 "Replies, opt-outs, bounces, out-of-offices. Only new messages after the first activation; "
                 "the last UID survives restarts, so anything that arrived while n8n was down is caught up."),
    code_node("Classify Inbound", CLASSIFY_JS, "runOnceForEachItem", [-660, 160],
              "Reads only what they wrote above the quoted thread -- our own email says \"reply 'no'\" and "
              "every reply quotes it. bounce > auto-reply > opt-out > reply."),
    pg_node("Record Inbound", RECORD_INBOUND_SQL, "={{ [JSON.stringify($json.payload)] }}", [-440, 160],
            "Match to a lead, record once (keyed on Message-ID), and act: reply -> outreach_log + lead "
            "'replied' (kills follow-ups); opt-out -> that plus blocklist, permanent (Section 5); bounce -> "
            "outreach_log 'bounced'."),
    code_node("Build Notification", NOTIFY_JS, "runOnceForEachItem", [-220, 160],
              "HubSpot is deferred (Section 9): the operator gets a plain-text email instead, once per message."),
    if_node("Notify Operator?", "notify", "={{ $json.notify }}", [0, 160],
            "Only the first time a matched reply, opt-out or bounce is recorded -- and only if "
            "NOVASCOUT_OPERATOR_EMAIL was set at build time."),
    email_node("Notify Operator", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}",
               [220, 160],
               "To the operator's own inbox, never the outreach mailbox (Section 9). A failure here does not "
               "undo the recording above."),
]

mailwatch_connections = {
    "Sent Folder": edge("Normalise Sent"),
    "Normalise Sent": edge("Mirror Sent"),
    "Inbox": edge("Classify Inbound"),
    "Classify Inbound": edge("Record Inbound"),
    "Record Inbound": edge("Build Notification"),
    "Build Notification": edge("Notify Operator?"),
    "Notify Operator?": branch("Notify Operator"),
}

# ---------------------------------------------------------------------------
# Workflow: Follow-Ups
# ---------------------------------------------------------------------------

FOLLOWUP_CONFIG = {"now_override": "", "follow_up_days": FOLLOW_UP_DAYS, "max_follow_ups": MAX_FOLLOW_UPS,
                   "batch_size": 25}

followup_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 6}]}},
     "name": "Every 6 Hours", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [-880, 40],
     "notes": "Queue-driven: a missed run just means the next one finds more due (Section 7)."},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-880, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": FOLLOWUP_CONFIG["now_override"], "type": "string"},
            {"id": "days", "name": "follow_up_days", "value": FOLLOW_UP_DAYS, "type": "number"},
            {"id": "max", "name": "max_follow_ups", "value": MAX_FOLLOW_UPS, "type": "number"},
            {"id": "batch", "name": "batch_size", "value": FOLLOWUP_CONFIG["batch_size"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-660, 130],
        "notes": ("follow_up_days=%d and max_follow_ups=%d are parsed out of Section 9 at build time. "
                  "now_override must stay empty (dry-run only)." % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)),
    },
    pg_node("Find Due Follow-Ups", FOLLOWUP_DUE_SQL,
            "={{ [$json.now_override, $json.follow_up_days, $json.max_follow_ups, $json.batch_size] }}",
            [-440, 130],
            "Leads at 'sent', no reply, no bounce, not blocklisted, quiet for %d days: the next follow-up, or "
            "'lost' once %d have been used." % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)),
    code_node("Build Follow-Up", FOLLOWUP_JS, "runOnceForAllItems", [-220, 130],
              "A template, not a model call: a follow-up adds no fact about the lead. It carries the Section 5 "
              "footer, so the send path accepts it once a human approves it."),
    pg_node("Write Follow-Up", FOLLOWUP_WRITE_SQL, "={{ [JSON.stringify($json.payload)] }}", [0, 130],
            "Into drafts as 'pending' -- the review queue. Nothing follows up without a human."),
]

followup_connections = {
    "Every 6 Hours": edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Find Due Follow-Ups"),
    "Find Due Follow-Ups": edge("Build Follow-Up"),
    "Build Follow-Up": edge("Write Follow-Up"),
}


# ---------------------------------------------------------------------------
# Structural guards on the shipped workflows
# ---------------------------------------------------------------------------

def _config_values(nodes):
    cfg = [n for n in nodes if n["name"] == "Config"][0]
    return {a["name"]: a["value"] for a in cfg["parameters"]["assignments"]["assignments"]}


for _nodes in (send_nodes, mailwatch_nodes, followup_nodes):
    for _n in _nodes:
        if _n["type"] == "n8n-nodes-base.emailSend":
            p = _n["parameters"]
            assert p["options"].get("appendAttribution") is False, (
                "%s would append n8n's attribution line and link to every email." % _n["name"])
            assert p["emailFormat"] == "text" and "html" not in p, (
                "%s is not plain text. Section 5: 'Plain text only. No HTML'." % _n["name"])
            assert not _n.get("retryOnFail"), (
                "%s retries on failure. An SMTP timeout after the server accepted would send twice." % _n["name"])
        if _n["type"] == "n8n-nodes-base.emailReadImap":
            assert _n["parameters"]["postProcessAction"] == "nothing", (
                "%s would change the mailbox the operator reads." % _n["name"])

_cfg = _config_values(send_nodes)
assert _cfg["now_override"] == "", "the shipped Send workflow has a clock override set"
assert _cfg["min_gap_min"] > 0 and 0 < _cfg["send_probability"] < 1, (
    "the shipped Send workflow has pacing switched off -- Section 5 wants irregular intervals")
_cfg = _config_values(followup_nodes)
assert _cfg["now_override"] == "", "the shipped Follow-Ups workflow has a clock override set"
assert (_cfg["follow_up_days"], _cfg["max_follow_ups"]) == (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)


def workflow(wid, name, nodes, connections):
    return {"id": wid, "name": name, "nodes": nodes, "connections": connections, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {}}


SHIPPED = [
    ("send.json", workflow("send0001", "Send & Track - Send", send_nodes, send_connections)),
    ("mailbox-watch.json", workflow("mailwatch0001", "Send & Track - Mailbox Watch", mailwatch_nodes, mailwatch_connections)),
    ("follow-ups.json", workflow("followup0001", "Send & Track - Follow-Ups", followup_nodes, followup_connections)),
]


def _write(path, wf):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


for _file, _wf in SHIPPED:
    _write(os.path.join(OUT_DIR, _file), _wf)


# ---------------------------------------------------------------------------
# Dry-run / test variants
# ---------------------------------------------------------------------------

_DRY_CREDS = {"postgres": PG_DRY, "smtp": SMTP_DRY}


def dry_variant(wf, wid, name, config_overrides, drop_nodes):
    """The shipped workflow with only: credentials -> scratch DB + SMTP sink,
    Config values, and schedule triggers removed. Proven below."""
    v = copy.deepcopy(wf)
    v["id"], v["name"] = wid, name
    v["nodes"] = [n for n in v["nodes"] if n["name"] not in drop_nodes]
    for n in v["nodes"]:
        for kind in list(n.get("credentials", {})):
            if kind == "imap":
                raise AssertionError("a dry-run variant must never carry the real IMAP credential")
            n["credentials"] = dict(_DRY_CREDS[kind])
        if n["name"] == "Config":
            for a in n["parameters"]["assignments"]["assignments"]:
                if a["name"] in config_overrides:
                    a["value"] = config_overrides[a["name"]]
    v["connections"] = {k: val for k, val in v["connections"].items() if k not in drop_nodes}
    return v


def _node_diff(prod, dry):
    """Every difference between two workflows, as (node, what) pairs."""
    diffs = []
    pn = {n["name"]: n for n in prod["nodes"]}
    dn = {n["name"]: n for n in dry["nodes"]}
    for name in sorted(set(pn) - set(dn)):
        diffs.append((name, "removed"))
    for name in sorted(set(dn) - set(pn)):
        diffs.append((name, "added"))
    for name in sorted(set(pn) & set(dn)):
        a, b = copy.deepcopy(pn[name]), copy.deepcopy(dn[name])
        if a.get("credentials") != b.get("credentials"):
            diffs.append((name, "credentials"))
        a.pop("credentials", None)
        b.pop("credentials", None)
        if name == "Config":
            av = {x["name"]: x["value"] for x in a["parameters"]["assignments"]["assignments"]}
            bv = {x["name"]: x["value"] for x in b["parameters"]["assignments"]["assignments"]}
            for k in sorted(set(av) | set(bv)):
                if av.get(k) != bv.get(k):
                    diffs.append((name, "config:" + k))
            for x in a["parameters"]["assignments"]["assignments"] + b["parameters"]["assignments"]["assignments"]:
                x["value"] = None
        if a != b:
            diffs.append((name, "OTHER"))
    for src in sorted(set(prod["connections"]) | set(dry["connections"])):
        if src in dn and prod["connections"].get(src) != dry["connections"].get(src):
            diffs.append((src, "connections"))
    return diffs


def _fixture_node(name, items, pos):
    src = ("// TEST FIXTURE -- stands in for the IMAP trigger in the test variant only.\n"
           "// Items shaped exactly like the Email Trigger (IMAP) node's 'simple' format.\n"
           "return " + json.dumps([{"json": i} for i in items], ensure_ascii=False, indent=2) + ";\n")
    return code_node(name, src, "runOnceForAllItems", pos, "Test fixture. Never in a shipped workflow.")


VARIANTS = []
if VARIANTS_OUT:
    if not DRYRUN_NOW:
        raise AssertionError("SENDTRACK_VARIANTS_OUT is set but SENDTRACK_DRYRUN_NOW is not -- the dry run "
                             "replays scenarios at a fixed instant.")
    send_wf = SHIPPED[0][1]
    dry_send = dry_variant(send_wf, "send0001dry", "DRY RUN - Send (scratch DB, SMTP sink)",
                           {"now_override": DRYRUN_NOW, "min_gap_min": 0, "send_probability": 1},
                           {"Every 10 Minutes"})
    got = _node_diff(send_wf, dry_send)
    want = sorted([("Every 10 Minutes", "removed"), ("Config", "config:now_override"),
                   ("Config", "config:min_gap_min"), ("Config", "config:send_probability"),
                   ("Load Send State", "credentials"), ("Claim Send", "credentials"),
                   ("Send Email", "credentials"), ("Confirm Send", "credentials"),
                   ("Revert Claim", "credentials")])
    assert sorted(got) == want, (
        "the dry-run Send variant differs from the shipped workflow in more than credentials, Config "
        "and the schedule trigger -- the dry run would not be testing what ships:\n  %r" % sorted(got))
    VARIANTS.append(("send-dryrun.json", dry_send))

    fu_wf = SHIPPED[2][1]
    dry_fu = dry_variant(fu_wf, "followup0001dry", "DRY RUN - Follow-Ups (scratch DB)",
                         {"now_override": DRYRUN_NOW}, {"Every 6 Hours"})
    got = _node_diff(fu_wf, dry_fu)
    want = sorted([("Every 6 Hours", "removed"), ("Config", "config:now_override"),
                   ("Find Due Follow-Ups", "credentials"), ("Write Follow-Up", "credentials")])
    assert sorted(got) == want, "the dry-run Follow-Ups variant drifted: %r" % sorted(got)
    VARIANTS.append(("followups-dryrun.json", dry_fu))

    if FIXTURES:
        fx = json.loads(_read(FIXTURES))
        mw = copy.deepcopy(SHIPPED[1][1])
        test_nodes = [n for n in mw["nodes"] if n["type"] != "n8n-nodes-base.emailReadImap"]
        test_nodes += [
            {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger",
             "typeVersion": 1, "position": [-1100, 30]},
            _fixture_node("Fixture: Sent Folder", fx["sent"], [-880, -100]),
            _fixture_node("Fixture: Inbox", fx["inbox"], [-880, 160]),
        ]
        conns = {k: v for k, v in mw["connections"].items() if k not in ("Sent Folder", "Inbox")}
        conns["Manual Trigger"] = edge("Fixture: Sent Folder", "Fixture: Inbox")
        conns["Fixture: Sent Folder"] = edge("Normalise Sent")
        conns["Fixture: Inbox"] = edge("Classify Inbound")
        mw_test = dry_variant(dict(mw, nodes=test_nodes, connections=conns), "mailwatch0001t",
                              "TEST - Mailbox Watch (fixtures, scratch DB, SMTP sink)", {}, set())
        shipped_mw = {n["name"]: n for n in SHIPPED[1][1]["nodes"]}
        for n in mw_test["nodes"]:
            if n["name"] in shipped_mw:
                a, b = copy.deepcopy(shipped_mw[n["name"]]), copy.deepcopy(n)
                a.pop("credentials", None)
                b.pop("credentials", None)
                assert a == b, "the Mailbox Watch test variant changed node %r" % n["name"]
        VARIANTS.append(("mailwatch-test.json", mw_test))

    for _file, _wf in VARIANTS:
        _write(os.path.join(VARIANTS_OUT, _file), _wf)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

for _file, _wf in SHIPPED:
    print("wrote", os.path.abspath(os.path.join(OUT_DIR, _file)), "(%s)" % _wf["id"])
for _file, _wf in VARIANTS:
    print("wrote", os.path.abspath(os.path.join(VARIANTS_OUT, _file)), "(%s)" % _wf["id"])
print("  warm-up (Section 5): %s" % ", ".join("week %d%s=%d/day" % (w, "+" if i == len(WARMUP) - 1 else "", n)
                                            for i, (w, n) in enumerate(WARMUP)))
print("  sender clock: %s (%+d min) -- the day the ceiling counts" % (SENDER_ZONE, SENDER_OFFSET))
print("  geographies with a business-hours clock: %d (Section 12)" % len(GEOGRAPHIES))
print("  opt-out keywords (Sections 5+9): %r" % OPT_OUT_KEYWORDS)
print("  follow-ups (Section 9): after %d days, maximum %d" % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS))
print("  From: %s    own domain: %s" % (FROM_HEADER, OWN_DOMAIN))
print("  signature checked on every body: %r" % SIGNATURE)
if not OPERATOR_EMAIL:
    print(
        "\n  WARNING: NOVASCOUT_OPERATOR_EMAIL is not set. Section 9's reply notification has\n"
        "  nowhere to go, so none is sent. Replies are still recorded, the lead still\n"
        "  leaves the send queue and opt-outs are still blocklisted -- check the\n"
        "  replied_queue view. Set it in .env (not the outreach mailbox) and rebuild.")

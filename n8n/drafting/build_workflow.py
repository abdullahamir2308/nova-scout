"""Assemble n8n/workflows/drafting.json from the tested Code-node sources.

Same contract as the enrichment, scoring and contacts generators: the .js files
are read verbatim and embedded, so the JS that was tested standalone is
byte-identical to the JS that ships inside the workflow.

Since 2026-10-19 (operator brief) Workflow 4 writes a first touch from a fixed
template -- the operator's approved wording, filled by code with verified facts.
No model call happens anywhere in this workflow. Its failure mode is still a
sentence sent to a real person under the sender's own name, so the spec is
parsed out of NovaScout_MasterRef.md and asserted here rather than retyped:

    Section 9  the first-touch template      -> code_template.js, byte for byte
    Section 9  the SMYKM sentences + subject -> code_template.js, byte for byte
    Section 9  the follow-up templates       -> code_template.js, byte for byte
    Section 9  arms, SMYKM order, area cap   -> ARM_BY_PARITY, SMYKM_ORDER, MAX_AREAS
    Section 9  therapeutic-area enum         -> what counts as an area
    Section 12 business-hours clocks         -> the gate's no-send-clock hold
    Section 8  drafts table columns          -> what the INSERT is allowed to touch
    Section 5  sender name and title         -> the template's sign-off (via .env)

When one fires, the fix is to update the JS to match the doc -- never the other
way round.
"""
import io
import json
import os
import re

# ClinicalTrials.gov (operator brief 2026-10-30): one request dispatched every 6 s -- n8n's HTTP batching
# dispatches on the interval without waiting for the answer (HttpRequestV3), so this is the ceiling:
# 10 a minute per workflow, 30 if Scoring, Drafting and Drafting - Research Sites all ran at once.
# Measured before (2026-10-28/29, 250 ms): a 25-request burst in 6 s, 58 requests in the busiest minute.
CTGOV_INTERVAL_MS = 6000
CTGOV_USER_AGENT = "NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)"
assert CTGOV_INTERVAL_MS >= 6000, "ClinicalTrials.gov: keep requests well under 50 a minute"

import template_spec  # this directory; shared with n8n/sendtrack/build_workflow.py

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "workflows", "drafting.json")
OUT = os.environ.get("DRAFTING_OUT", OUT)

MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md")
)


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _doc():
    with io.open(MASTER_REF, encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}


# ---------------------------------------------------------------------------
# Sender identity (Section 5). Read from the environment, falling back to the
# repo's .env -- the file docker-compose already reads. A real environment
# variable wins over .env, the usual dotenv precedence.
#
# The template carries the sender's name and title as approved text, and Send
# refuses any body that does not carry NOVASCOUT_SENDER_NAME followed by
# NOVASCOUT_SENDER_TITLE. So the build refuses a template whose sign-off is not
# exactly those two: there is deliberately no default name (a default once
# signed every draft as the wrong person).
# ---------------------------------------------------------------------------

ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
_BUILD_SETTINGS = ("NOVASCOUT_SENDER_NAME", "NOVASCOUT_SENDER_TITLE")


def _env_file_values(path, keys):
    """Just `keys` from a dotenv file. The same file holds credentials this
    build has no business reading, so nothing else is kept."""
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
    """(value, where it came from). An empty environment variable does not
    shadow a real value in .env."""
    v = os.environ.get(key, "").strip()
    if v:
        return v, "environment"
    v = _FILE_SETTINGS.get(key, "").strip()
    if v:
        return v, os.path.abspath(ENV_FILE)
    return "", None


SENDER_NAME, SENDER_SOURCE = _setting("NOVASCOUT_SENDER_NAME")
SENDER_TITLE, _ = _setting("NOVASCOUT_SENDER_TITLE")

if not SENDER_NAME:
    raise AssertionError(
        "NOVASCOUT_SENDER_NAME is not set -- not in the environment and not in %s.\n"
        "The template is signed with it and Send checks every body for it. There is\n"
        "deliberately no default: a default name is how drafts came to be signed by\n"
        "the wrong person. Add it to .env and rebuild." % os.path.abspath(ENV_FILE)
    )


# ---------------------------------------------------------------------------
# Spec parsers
# ---------------------------------------------------------------------------

def load_therapeutic_areas(doc=None):
    """Parse the locked taxonomy enum out of Section 9's taxonomy section.

    Identical parser to n8n/scoring/build_workflow.py, deliberately duplicated
    rather than imported: the two generators must be able to fail independently.
    """
    doc = doc if doc is not None else _doc()
    anchor = re.search(r"\*\*Therapeutic area taxonomy[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError(
            "taxonomy section not found in %s -- expected a line containing "
            "'**Therapeutic area taxonomy ...**'" % MASTER_REF
        )
    block = re.search(r"\n```\n(.*?)\n```", doc[anchor.end():], re.S)
    if not block:
        raise AssertionError("no fenced list follows the taxonomy heading in %s" % MASTER_REF)
    areas = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
    if len(areas) < 2:
        raise AssertionError("taxonomy in %s looks empty: %r" % (MASTER_REF, areas))
    return areas


def load_drafts_columns(doc=None):
    """Parse the `drafts` column list out of Section 8's schema block."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\ndrafts\n(.*?)\n\n", doc, re.S)
    if not m:
        raise AssertionError("drafts table not found in Section 8 of %s" % MASTER_REF)
    cols = []
    for chunk in m.group(1).replace("\n", " ").split(","):
        name = chunk.strip().split(" ")[0].strip()
        if name:
            cols.append(name)
    if "lead_id" not in cols:
        raise AssertionError("drafts column parse looks wrong in %s: %r" % (MASTER_REF, cols))
    return cols


def load_clock_countries(doc=None):
    """Section 12's "Business-hours clocks" table, as country names only.

    Duplicated from n8n/sendtrack/build_workflow.py on purpose, like the
    taxonomy loader above: the stages must be able to fail independently. The
    sendtrack build is the one that checks the table's numbers against
    code_decide.js; drafting only needs to know which countries are IN it,
    because a lead in a country that is not can never be sent to (Section 12),
    and the gate holds its email."""
    doc = doc if doc is not None else _doc()
    anchor = re.search(r"\*\*Business-hours clocks[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError(
            "Section 12 'Business-hours clocks' heading not found in %s" % MASTER_REF)
    table = re.search(
        r"\n\| Country \| Standard offset \| Weekend \| Tier \|\r?\n\|[-|]+\|\r?\n(.*?)(?:\r?\n\r?\n|\Z)",
        doc[anchor.end():], re.S)
    if not table:
        raise AssertionError(
            "no clock table follows the 'Business-hours clocks' heading in %s" % MASTER_REF)
    names = []
    for line in table.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError("clock row %r does not have 4 cells in %s" % (line, MASTER_REF))
        if not re.match(r"^UTC[+-]\d{1,2}:\d{2}$", cells[1]):
            raise AssertionError("clock row %r has an unreadable offset %r" % (cells[0], cells[1]))
        if cells[0] in names:
            raise AssertionError("clock row %r is listed twice in %s" % (cells[0], MASTER_REF))
        names.append(cells[0])
    if not names:
        raise AssertionError("Section 12's clock table is empty in %s" % MASTER_REF)
    return names


def load_template_only(doc=None):
    """Section 9, Workflow 4: the sentence that says no model writes a first
    touch. It is the whole design of this build -- if the doc ever brings a
    composing model back, the build stops until someone decides how."""
    doc = doc if doc is not None else _doc()
    if not re.search(r"\*\*No model call\*\* in Drafting", doc):
        raise AssertionError("Section 9 of %s no longer says '**No model call** in Drafting' -- this build ships "
                             "templates only; decide the composing design before changing it" % MASTER_REF)
    return True


DOC = _doc()
THERAPEUTIC_AREAS = load_therapeutic_areas(DOC)
DRAFTS_COLUMNS = load_drafts_columns(DOC)
CLOCK_COUNTRIES = load_clock_countries(DOC)
TEMPLATE_ONLY = load_template_only(DOC)


# ---------------------------------------------------------------------------
# Drift guards against code_template.js
# ---------------------------------------------------------------------------

TEMPLATE_SRC = js("code_template.js")
TEMPLATE_CONSTANTS = template_spec.constants(TEMPLATE_SRC, "code_template.js")


def _js_string_array(src, name, where):
    m = re.search(r"const %s = \[(.*?)\];" % re.escape(name), src, re.S)
    assert m, "%s array not found in %s" % (name, where)
    return re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))


def _assert_template_matches_spec():
    c = TEMPLATE_CONSTANTS
    for heading, rendered in ((template_spec.FIRST_TOUCH_HEADING, template_spec.first_touch_block(c)),
                              (template_spec.SMYKM_HEADING, template_spec.smykm_block(c)),
                              (template_spec.FOLLOW_UP_HEADING, template_spec.follow_up_block(c))):
        template_spec.assert_block(DOC, heading, rendered, MASTER_REF, "code_template.js")
    template_spec.assert_rules(DOC, TEMPLATE_SRC, MASTER_REF, "code_template.js")
    template_spec.assert_sign_off(c, SENDER_NAME, SENDER_TITLE, "code_template.js")
    assert '"%s"' % c["GREETING_UNNAMED"] in DOC, (
        "Section 9 no longer says a role inbox or an unnamed address is greeted %r" % c["GREETING_UNNAMED"])

    found = _js_string_array(TEMPLATE_SRC, "THERAPEUTIC_AREAS", "code_template.js")
    assert found == THERAPEUTIC_AREAS, (
        "therapeutic-area taxonomy drifted between the Master Ref and code_template.js:\n"
        "  doc: %r\n  js:  %r" % (THERAPEUTIC_AREAS, found))

    # The rules section is shared with Follow-Ups and must stay pure.
    at = TEMPLATE_SRC.find("// Node body")
    assert at != -1, "code_template.js has no '// Node body' marker -- Follow-Ups embeds everything above it"
    rules = TEMPLATE_SRC[:at]
    assert "$(" not in rules and "$input" not in rules and "__" not in re.sub(r"//.*", "", rules), (
        "the rules section of code_template.js reads n8n data or holds a build placeholder -- node-body code "
        "has moved above the 'Node body' marker")

    # No model, anywhere: a template draft has nothing to call one for. (A
    # comment is "//" not preceded by ":", so a URL is not mistaken for one.)
    code = re.sub(r"(^|[^:])//.*", r"\1", TEMPLATE_SRC, flags=re.M)
    assert not re.search(r"api\.anthropic\.com|claude-|messages:|httpRequest", code), (
        "code_template.js builds a model request. Since 2026-10-19 Section 9 says no model writes a first touch.")


_assert_template_matches_spec()


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

BLOCKED_DOMAIN = "EXISTS (SELECT 1 FROM blocklist b WHERE {d} = b.domain OR {d} LIKE '%.' || b.domain)"

BATCH_SQL = """-- The queue for this stage: Section 9 Workflow 4's "batch where
-- status='contact_found'". scores.lead_id, contacts.lead_id and
-- enrichments.lead_id are each UNIQUE (migrations 003, 004, 002), so all three
-- joins are 1:1 and this cannot fan out into duplicate drafts.
--
-- Every join is an INNER join on purpose. A lead at 'contact_found' that is
-- missing any of the three rows is not draftable and is not a row this workflow
-- should be inventing defaults for -- it is a bug upstream, and it should stay
-- visible in the queue rather than being silently drafted from nulls.
--
-- city lives in enrichments.raw_extraction rather than in a column of its own,
-- so it is pulled out here rather than in the Code node.
--
-- CROs only (2026-10-19): the template speaks to a CRO ("AI agents for CRO
-- websites"). lead_company_type() (migration 019) is the one definition --
-- enrichment's company_type when it is CRO, site or SMO, otherwise the source.
-- A site or an SMO is skipped HERE, so it waits at contact_found without taking
-- a place in the batch; ten of them would otherwise starve every CRO behind
-- them.
--
-- The blocklist flags are Send's own expressions (lead domain, recipient
-- domain), so the gate holds exactly what Send would refuse. auto_approve_email
-- is settings.auto_approve_email (migration 014), read per run: off, every
-- draft waits for a person, and the table re-checks it on the write.
SELECT l.id            AS lead_id,
       l.domain,
       l.company_name,
       l.country,
       s.fit_score,
       c.name          AS contact_name,
       c.email,
       coalesce(c.verified, false) AS verified,
       e.therapeutic_areas,
       e.phases,
       e.raw_extraction->>'city' AS city,
       ct.company_type,
       __LEAD_BLOCKED__ AS lead_domain_blocked,
       __RCPT_BLOCKED__ AS recipient_domain_blocked,
       auto_approve_email_enabled() AS auto_approve_email
  FROM leads l
  JOIN scores      s ON s.lead_id = l.id
  JOIN contacts    c ON c.lead_id = l.id
  JOIN enrichments e ON e.lead_id = l.id
  CROSS JOIN LATERAL (SELECT lead_company_type(e.raw_extraction, l.source) AS company_type) ct
 WHERE l.status = 'contact_found'
   AND ct.company_type = 'CRO'
 ORDER BY l.id
 LIMIT $1;""".replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
).replace(
    "__RCPT_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(split_part(c.email, '@', 2))")
)

WRITE_SQL = """-- Write the draft and advance the lead in ONE statement, so a crash between
-- them cannot leave a lead advanced with no draft (invisible forever) or
-- drafted but still queued (drafted again on the next tick).
--
-- The UPDATE runs first and is guarded on status='contact_found'. The INSERT
-- selects FROM that update's RETURNING, so if the guard matched nothing -- a
-- repeated run, a concurrent execution -- there is no row to join against and
-- zero drafts are inserted. That is the idempotency: re-running this statement
-- with the same payload is a no-op, not a duplicate.
--
-- A REDRAFT RETIRES WHAT IT REPLACES. A lead only reaches this statement with
-- earlier drafts if it was sent back to 'contact_found' to be redrafted. Its
-- earlier first-touch EMAIL drafts that are still pending or approved become
-- 'rejected' / 'bad draft' here, in the same statement. Left alone, an old
-- APPROVED draft would go out first: the lead returns to 'drafted', which is
-- what Workflow 6 sends from, and it sends the oldest approved draft. LinkedIn
-- drafts are left alone (no new ones are written since 2026-10-19; the old ones
-- stay in the queue for a person), and so are 'sent' drafts, follow-ups and
-- replies. A new draft is 'pending', or 'approved' by this workflow when the
-- gate passed it -- and the table's own trigger (migration 020) re-checks the
-- flag, the template variant, the tags, the address and the blocklist, turning
-- a bad approval into a hold. All CTEs share one snapshot, so `retired` cannot
-- see, and never retires, the row `ins` adds.
WITH payload AS (
  SELECT $1::jsonb AS p
), adv AS (
  UPDATE leads
     SET status = 'drafted',
         updated_at = now()
   WHERE id = ((SELECT p FROM payload)->>'lead_id')::bigint
     AND status = 'contact_found'
  RETURNING id
), retired AS (
  UPDATE drafts d
     SET status = 'rejected',
         reject_reason = 'bad draft'
    FROM adv a
   WHERE d.lead_id = a.id
     AND d.channel = 'email'
     AND d.status IN ('pending', 'approved')
     AND coalesce(d.variant, '') NOT LIKE 'follow-up-%'
     AND coalesce(d.variant, '') NOT LIKE 'reply/%'
  RETURNING d.id, d.status
), ins AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by, hold_reason)
  SELECT a.id,
         d->>'channel',
         d->>'variant',
         d->>'subject',
         d->>'body',
         CASE WHEN d->>'channel' = 'email' AND d->>'status' = 'approved' AND d->>'approved_by' = 'auto'
              THEN 'approved' ELSE 'pending' END,
         CASE WHEN d->>'channel' = 'email' AND d->>'status' = 'approved' AND d->>'approved_by' = 'auto'
              THEN 'auto' END,
         nullif(d->>'hold_reason', '')
    FROM adv a
    CROSS JOIN LATERAL jsonb_array_elements((SELECT p->'drafts' FROM payload)) AS d
   WHERE d->>'channel' = 'email'
  RETURNING id, lead_id, channel, variant, status, approved_by, hold_reason
)
SELECT ((SELECT p FROM payload)->>'lead_id')::bigint AS lead_id,
       (SELECT count(*) FROM adv) AS advanced,
       (SELECT count(*) FROM ins) AS drafts_written,
       (SELECT string_agg(channel || ':' || variant, ' | ' ORDER BY channel) FROM ins) AS wrote,
       (SELECT string_agg(id::text || ':' || channel || ':' || status || coalesce(':' || approved_by, ''), ' | '
                          ORDER BY id) FROM ins) AS decisions,
       (SELECT string_agg(id::text || ': ' || hold_reason, ' | ' ORDER BY id) FROM ins
         WHERE hold_reason IS NOT NULL) AS held,
       (SELECT string_agg(id::text, ',' ORDER BY id) FROM retired) AS retired_draft_ids;"""


# Section 8 owns the drafts schema. An INSERT that reaches for a column the doc
# does not define means the schema was changed without the doc being updated.
_insert_cols = re.search(r"INSERT INTO drafts \(([^)]+)\)", WRITE_SQL).group(1)
_insert_cols = [c.strip() for c in _insert_cols.split(",")]
_unknown = [c for c in _insert_cols if c not in DRAFTS_COLUMNS]
assert not _unknown, (
    "the drafts INSERT writes %r, which Section 8 of %s does not define. Update "
    "the doc first." % (_unknown, MASTER_REF)
)

assert "l.status = 'contact_found'" in BATCH_SQL, (
    "the batch query no longer filters on status='contact_found' -- Section 9 "
    "Workflow 4: 'Batch where status=contact_found'."
)
assert "ct.company_type = 'CRO'" in BATCH_SQL, (
    "the batch query no longer skips sites and SMOs -- Section 9 Workflow 4: they are not drafted by the "
    "template and wait at contact_found")
assert "claims_library" not in BATCH_SQL + WRITE_SQL, (
    "Drafting reads the claims library again. Since 2026-10-19 a first touch says only the approved template "
    "(Section 9, Workflow 4).")
# Section 6 / the 2026-10-19 brief: no new LinkedIn DMs. The write inserts only
# email rows and retires only email rows, whatever the payload says.
assert "WHERE d->>'channel' = 'email'" in WRITE_SQL and "AND d.channel = 'email'" in WRITE_SQL, (
    "Write Drafts & Advance no longer limits itself to email drafts -- no LinkedIn DM is drafted since "
    "2026-10-19, and the old ones are left alone")


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def boolean_condition(cid, expr, expect_true):
    return {
        "options": {
            "caseSensitive": True,
            "leftValue": "",
            "typeValidation": "strict",
            "version": 2,
        },
        "conditions": [
            {
                "id": cid,
                "leftValue": expr,
                "rightValue": expect_true,
                "operator": {
                    "type": "boolean",
                    "operation": "true" if expect_true else "false",
                    "singleValue": True,
                },
            }
        ],
        "combinator": "and",
    }


# Section 12's clock list is baked into the node body (below the 'Node body'
# marker), so the shared rules section above it stays placeholder-free.
assert TEMPLATE_SRC.count("__SEND_CLOCK_COUNTRIES__") == 1, (
    "code_template.js must carry __SEND_CLOCK_COUNTRIES__ exactly once -- a substituted constant that is not "
    "there is silently never substituted")
TEMPLATE_JS = TEMPLATE_SRC.replace("__SEND_CLOCK_COUNTRIES__", json.dumps(sorted(CLOCK_COUNTRIES), ensure_ascii=False))
assert not re.findall(r"__[A-Z_]+__", TEMPLATE_JS), "code_template.js still has an unsubstituted placeholder"


def _assert_emitted_upstream(js_src, fields, node_name, consumer):
    # An object key at the start of a line -- `    payload: {` -- never the same
    # word followed by a colon in prose.
    missing = [f for f in sorted(fields)
               if not re.search(r"^[ \t]*%s:" % re.escape(f), js_src, re.M)]
    assert not missing, (
        "%s reads $json.%s, but %s does not emit %r. A field the upstream node does not set resolves to an "
        "empty string at runtime with no error." % (consumer, "/$json.".join(sorted(fields)), node_name, missing))


FILTER_EXPR = "={{ $json.ok }}"
WRITE_PARAM = "={{ [JSON.stringify($json.payload)] }}"
_assert_emitted_upstream(TEMPLATE_JS, set(re.findall(r"\$json\.(\w+)", FILTER_EXPR)), "Build First Touch",
                         "Drop Failed Lookups")
_assert_emitted_upstream(TEMPLATE_JS, set(re.findall(r"\$json\.(\w+)", WRITE_PARAM)), "Build First Touch",
                         "Write Drafts & Advance")
# Write Drafts & Advance reads these off each payload draft; Build First Touch
# must set every one.
for _f in sorted(set(re.findall(r"d->>?'(\w+)'", WRITE_SQL))):
    assert re.search(r"^\s*%s:" % re.escape(_f), TEMPLATE_JS, re.M), (
        "Write Drafts & Advance reads d->>'%s', which Build First Touch does not set on a draft" % _f)

# Every `lead.X` Build First Touch reads off the batch row must be a column Get
# Draft Batch returns. A missing one is `undefined` in the Code node, with no
# error -- a missing `verified` would hold every draft, a missing
# `lead_domain_blocked` would approve a blocklisted one.
_lead_reads = set(re.findall(r"\blead\.(\w+)", TEMPLATE_JS.split("// Node body", 1)[1]))
_batch_cols = (set(re.findall(r"\bAS\s+(\w+)", BATCH_SQL, re.I))
               | set(re.findall(r"\b[a-z]\.(\w+),?\s*$", BATCH_SQL, re.M)))
_unread = sorted(_lead_reads - _batch_cols)
assert not _unread, (
    "code_template.js reads lead.%s, but Get Draft Batch returns no such column. It would be "
    "undefined at runtime with no error." % ", lead.".join(_unread)
)

nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-880, 40],
        "notes": "Section 9 Workflow 4: cron, batch where status='contact_found'. Queue-driven, so a missed run just means the next one catches up (build rule 4). Activation is a UI Publish action, never the CLI (Section 3).",
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-880, 220],
    },
    {
        "parameters": {
            "assignments": {
                "assignments": [
                    {"id": "batchsize", "name": "batch_size", "value": 10, "type": "number"},
                ]
            },
            "options": {},
        },
        "name": "Config",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [-660, 130],
        "notes": (
            "Bounded batch (build rule 5). A template draft costs nothing, but every lead in a batch is one "
            "ClinicalTrials.gov request, and Send's warm-up ceiling (Section 5) is 20 a day at most anyway."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": BATCH_SQL,
            "options": {"queryReplacement": "={{ [$json.batch_size] }}"},
        },
        "name": "Get Draft Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-440, 130],
        "credentials": PG_CRED,
        "notes": (
            "Oldest-first queue read (Section 7 idempotency rule), CROs only: a site or an SMO waits at "
            "contact_found, because the template speaks to a CRO (Section 9, Workflow 4).\n\n"
            "Also carries, on every row, what the gate needs -- verified, the blocklist flags Send computes, "
            "settings.auto_approve_email -- because a Postgres node replaces the items, so nothing set upstream "
            "survives to Build First Touch.\n\n"
            "Query parameters are passed as an array so a value containing a comma is never split."
        ),
        "alwaysOutputData": False,
    },
    {
        "parameters": {
            "method": "GET",
            "url": "https://clinicaltrials.gov/api/v2/studies",
            "sendQuery": True,
            "queryParameters": {
                "parameters": [
                    {"name": "query.spons", "value": "={{ $json.company_name }}"},
                    {"name": "filter.overallStatus", "value": "RECRUITING"},
                    {"name": "countTotal", "value": "true"},
                    {"name": "pageSize", "value": "1000"},
                    {"name": "fields", "value": "NCTId,LeadSponsorName,CollaboratorName"},
                ]
            },
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "User-Agent", "value": CTGOV_USER_AGENT}]},
            "options": {
                "timeout": 30000,
                "batching": {"batch": {"batchSize": 1, "batchInterval": CTGOV_INTERVAL_MS}},
            },
        },
        "name": "ClinicalTrials.gov Lookup",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-220, 130],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "The recruiting trials behind the first SMYKM fact ('{n} trial(s) recruiting right now'). Nothing in "
            "the pipeline stores them: Workflow 3 keeps only a yes/no in scores.rationale.\n\n"
            "query.spons with the company name VERBATIM -- the only ClinicalTrials.gov query this project "
            "trusts (Section 9, Workflow 3: query.locn, query.term and a cleaned sponsor name were each measured "
            "and rejected on false positives). It is still a full-text match, not an identity check -- on "
            "2026-10-19 'Therapeutics' matched 1,175 trials -- so this asks for each study's lead sponsor and "
            "collaborators (up to 1,000 studies, the API's page maximum) and Build First Touch counts only the "
            "studies whose sponsor or collaborator IS the company. totalCount alone is never printed.\n\n"
            "Free, no API key (Section 4 cost model)."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": TEMPLATE_JS},
        "name": "Build First Touch",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [0, 130],
        "notes": (
            "The fixed first-touch template (Section 9, Workflow 4, LOCKED wording, approved 2026-10-19), filled "
            "by code: the arm by lead_id (even A, odd B), the SMYKM line from the first fact the lead has "
            "(recruiting trials, therapeutic areas, city, or else the company line), and every formatting rule -- "
            "plural, at most two areas in lowercase, 'from Phase I to III' / 'in Phase II', legal suffixes stripped, ALL-CAPS fixed, honorifics "
            "skipped, a role inbox greeted 'Hi there,'. No model call.\n\n"
            "Then the gate (Section 9, Workflow 5): the email approves itself unless it has no deliverable "
            "verified address, its country has no Send clock, or a domain is blocklisted -- or the operator has "
            "turned auto-approval off. The arm and the SMYKM choice are recorded in drafts.variant "
            "('template/A/trials/role-inbox'), a hold as a tag after '+'.\n\n"
            "If ClinicalTrials.gov did not answer, nothing is written and the lead stays queued."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("ok", FILTER_EXPR, True),
            "options": {},
        },
        "name": "Drop Failed Lookups",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [220, 130],
        "notes": (
            "If ClinicalTrials.gov did not answer, the lead is dropped and stays 'contact_found' so the next run "
            "retries it: written without the count, it would open on a weaker fact than it has, for good."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": WRITE_PARAM},
        },
        "name": "Write Drafts & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [440, 130],
        "credentials": PG_CRED,
        "notes": (
            "The draft and the status advance in one statement. The whole payload goes in as a single jsonb "
            "parameter, so unicode and embedded commas are handled by Postgres rather than by string "
            "splicing.\n\n"
            "The INSERT selects FROM the guarded UPDATE's RETURNING, so a repeated run inserts zero rows instead "
            "of duplicating drafts.\n\n"
            "A redraft retires what it replaces, in the same statement: a lead's earlier pending or approved "
            "first-touch EMAIL drafts become rejected / 'bad draft'. LinkedIn drafts are never written or "
            "touched here. The table's trigger (migration 020) re-checks every auto-approval."
        ),
    },
]

# --- Guard: the schedule cannot depend on the host being up at one moment ----
#
# Section 7's idempotency rule: "the host machine will be off some of the time.
# No workflow may assume its schedule fired." Two ways to break it:
#
#   a fixed slot        `days`/1 + triggerAtHour compiles to one cron a day at
#                       that hour and nothing else.
#   a clock-value gate  n8n 2.35.7's recurrenceCheck gates an hours, days or
#                       weeks interval above 1 on the CLOCK VALUE of the last
#                       run, so a host that sleeps skips a due tick.
#
# Minutes are counted on absolute elapsed minutes in that function, so a
# minutes interval is safe.
for _n in nodes:
    if _n["type"] != "n8n-nodes-base.scheduleTrigger":
        continue
    for _iv in _n["parameters"]["rule"]["interval"]:
        for _pin in ("triggerAtHour", "triggerAtDay", "triggerAtDayOfMonth", "triggerAtMinute"):
            assert _pin not in _iv, (
                "%s: schedule %r pins %s, so it has one moment to fire in. Section 7: 'No "
                "workflow may assume its schedule fired.'" % (_n["name"], _iv, _pin))
        _field = _iv.get("field")
        assert not (_field in ("hours", "days", "weeks") and int(_iv.get(_field + "Interval", 1)) > 1), (
            "%s: schedule %r enters n8n's recurrenceCheck on a clock value (hour of day, day of "
            "year), not elapsed time -- on a host that sleeps, a due tick is silently skipped "
            "(Follow-Ups, 2026-10-02). Use a minutes interval, or an interval of 1."
            % (_n["name"], _iv))

# The trial count a template prints is the company's own (2026-10-19): the
# lookup must return each study's sponsors, a full page of them, and Build
# First Touch must count those -- totalCount alone is a full-text match.
_ct = [n for n in nodes if n["name"] == "ClinicalTrials.gov Lookup"][0]
_ct_q = {p["name"]: p["value"] for p in _ct["parameters"]["queryParameters"]["parameters"]}
assert "LeadSponsorName" in _ct_q.get("fields", "") and "CollaboratorName" in _ct_q.get("fields", "") \
    and _ct_q.get("pageSize") == "1000", (
    "the ClinicalTrials.gov lookup no longer returns each study's sponsors on a full page (fields %r, pageSize %r) -- "
    "the template could only print query.spons' totalCount, a full-text match ('Therapeutics' matched 1,175)"
    % (_ct_q.get("fields"), _ct_q.get("pageSize")))
assert "leadFacts(lead, verifiedTrials(lead.company_name, ct))" in TEMPLATE_JS, (
    "Build First Touch no longer counts only the company's own trials -- it would print query.spons' totalCount")

# No model, and no credential that could call one.
for _n in nodes:
    assert "anthropicApi" not in json.dumps(_n.get("credentials", {})), (
        "%s carries the Anthropic credential. Since 2026-10-19 no node in Drafting calls a model." % _n["name"])
    assert "api.anthropic.com" not in json.dumps(_n.get("parameters", {})), (
        "%s calls the Anthropic API. Since 2026-10-19 no node in Drafting calls a model." % _n["name"])


connections = {
    "Every 30 Minutes": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Get Draft Batch", "type": "main", "index": 0}]]},
    "Get Draft Batch": {"main": [[{"node": "ClinicalTrials.gov Lookup", "type": "main", "index": 0}]]},
    "ClinicalTrials.gov Lookup": {"main": [[{"node": "Build First Touch", "type": "main", "index": 0}]]},
    "Build First Touch": {"main": [[{"node": "Drop Failed Lookups", "type": "main", "index": 0}]]},
    "Drop Failed Lookups": {"main": [[{"node": "Write Drafts & Advance", "type": "main", "index": 0}]]},
}

# Nothing reaches the write without Build First Touch's gate.
_into_write = sorted(src for src, outs in connections.items()
                     for branch in outs["main"] for e in branch if e["node"] == "Write Drafts & Advance")
assert _into_write == ["Drop Failed Lookups"], (
    "something reaches Write Drafts & Advance other than through Build First Touch: %r" % _into_write)

# Every $('Node') a Code node reads must exist in this workflow -- n8n stops the
# execution there.
_names = {n["name"] for n in nodes}
for _n in nodes:
    if _n["type"] == "n8n-nodes-base.code":
        for _ref in re.findall(r"\$\('([^']+)'\)", _n["parameters"]["jsCode"]):
            assert _ref in _names, "%s reads $('%s'), but there is no node named %r" % (_n["name"], _ref, _ref)

workflow = {
    "id": "drafting0001",
    "name": "Drafting",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(workflow, fh, ensure_ascii=False, indent=2)
    fh.write("\n")

print("wrote", os.path.abspath(OUT))
print("  template: first touch + SMYKM + follow-ups match Section 9 byte for byte; no model call")
print("  arms: even %s, odd %s; SMYKM order %r; at most %d areas"
      % tuple([template_spec.js_rules(TEMPLATE_SRC)[k] for k in ("even", "odd", "order", "max_areas")]))
print("  sign-off: %r (sender from %s)" % (TEMPLATE_CONSTANTS["SIGN_OFF"], SENDER_SOURCE))
print("  clocks: %d countries; CROs only; email only" % len(CLOCK_COUNTRIES))

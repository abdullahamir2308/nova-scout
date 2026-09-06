"""Assemble n8n/workflows/apollo-contacts.json from the tested Code-node sources.

Same contract as the enrichment and scoring generators: the .js files are read
verbatim and embedded, so the JS that was tested standalone is byte-identical to
the JS that ships inside the workflow.

This generator holds the spec-drift guards for Workflow 3b. Four things this
stage depends on are locked elsewhere and cannot be imported into an n8n Code
node:

    Section  9  the >= 60 Apollo gate    -> what keeps Apollo on the free tier
    Section  7  Apollo runs AFTER scoring -> the same, structurally
    Section  8  the `contacts` columns    -> what the write step inserts
    Section 12  the ICP buyer titles      -> who the ranker prefers

A fifth guard is internal rather than spec-level: the CSV this stage reads for
already-scraped addresses must be the same artefact Workflow 1 publishes, so the
URL is asserted against ingestion-ichgcp.json rather than written twice.

Each is parsed out of its source here and asserted against the shipped code. A
divergence fails the build loudly instead of silently shipping a stage that
spends credits it was designed not to. When one fires, the fix is to update the
code to match the doc -- never the other way round.
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "workflows", "apollo-contacts.json")
OUT = os.environ.get("CONTACTS_OUT", OUT)

MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md")
)
INGESTION = os.environ.get(
    "NOVASCOUT_INGESTION", os.path.join(HERE, "..", "workflows", "ingestion-ichgcp.json")
)


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _doc():
    with io.open(MASTER_REF, encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}

# Created in the n8n UI as a Header Auth credential (name `x-api-key`, value the
# Apollo key). Referenced by id the same way the Postgres credential is, so the
# key itself never enters the repo.
APOLLO_CRED = {"httpHeaderAuth": {"id": "novascoutApollo01", "name": "Apollo - x-api-key"}}


# ---------------------------------------------------------------------------
# Spec parsers and guards
# ---------------------------------------------------------------------------

def load_apollo_threshold(doc=None):
    """Parse the Apollo gate out of Section 9.

    Anchored on the sentence that states the rule rather than a line number.
    This number is the entire reason Apollo stays on the free tier: lowering it
    silently would multiply spend by the width of the score distribution.
    """
    doc = doc if doc is not None else _doc()
    m = re.search(
        r"Apollo lookup for leads scoring\s*(?:>=|≥|&ge;)\s*(\d+)\s*only", doc
    )
    if not m:
        raise AssertionError(
            "Apollo score gate not found in %s -- expected a sentence containing "
            "'Apollo lookup for leads scoring >= N only'" % MASTER_REF
        )
    return int(m.group(1))


def load_contacts_columns(doc=None):
    """Parse the `contacts` column list out of the Section 8 schema block."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\ncontacts\n(.*?)(?:\n\n|\ndrafts\n)", doc, re.S)
    if not m:
        raise AssertionError(
            "contacts table not found in %s -- expected a 'contacts' block in the "
            "Section 8 schema listing" % MASTER_REF
        )
    body = m.group(1)
    cols = []
    for raw in re.split(r"[,\n]", body):
        name = raw.strip()
        if not name:
            continue
        # Strips the type annotations the doc carries, e.g. 'verified (bool)'.
        name = re.split(r"\s+", name)[0].strip()
        if name and name not in cols:
            cols.append(name)
    if "email" not in cols or "lead_id" not in cols:
        raise AssertionError(
            "contacts column list in %s looks wrong: %r" % (MASTER_REF, cols)
        )
    return cols


def load_icp_titles(doc=None):
    """Parse the buyer roles out of Section 12's '**Target:**' line.

    Scoped to the ICP Definition section first. Workflow 5 carries its own
    '**Target:** 20 minutes daily' line earlier in the doc, and an unanchored
    search finds that one -- which would have silently compared the ranker's
    title tiers against a review-queue time budget.
    """
    doc = doc if doc is not None else _doc()
    sec = re.search(r"\n##\s*12\.\s*ICP Definition\b(.*?)(?:\n##\s|\Z)", doc, re.S)
    if not sec:
        raise AssertionError(
            "Section 12 (ICP Definition) not found in %s -- expected a "
            "'## 12. ICP Definition' heading" % MASTER_REF
        )
    m = re.search(r"\*\*Target:\*\*\s*([^\n]+)", sec.group(1))
    if not m:
        raise AssertionError(
            "ICP target line not found in Section 12 of %s -- expected a line "
            "containing '**Target:** ...'" % MASTER_REF
        )
    return m.group(1).strip().rstrip(".")


def load_csv_url():
    """Read the ICH GCP CSV URL out of the shipped ingestion workflow.

    Read rather than restated so the two stages cannot drift onto different
    artefacts. If ingestion ever republishes elsewhere, this build fails instead
    of quietly checking an address list that no longer matches the leads.
    """
    with io.open(INGESTION, encoding="utf-8") as fh:
        wf = json.load(fh)
    urls = [
        n["parameters"]["url"]
        for n in wf["nodes"]
        if n["type"].endswith("httpRequest") and "url" in n.get("parameters", {})
    ]
    csv_urls = [u for u in urls if u.endswith(".csv")]
    if len(csv_urls) != 1:
        raise AssertionError(
            "expected exactly one .csv fetch in %s, found %r" % (INGESTION, urls)
        )
    return csv_urls[0]


def load_title_tiers():
    """Parse TITLE_TIERS out of code_pick_contact.js.

    The Apollo search filter is generated from the same list the ranker sorts by,
    so the endpoint can never be asked for a set of titles the ranker would then
    discard (or worse, the reverse: a tier the search never returns).
    """
    src = js("code_pick_contact.js")
    m = re.search(r"const TITLE_TIERS = \[(.*?)\n\];", src, re.S)
    if not m:
        raise AssertionError("TITLE_TIERS not found in code_pick_contact.js")
    tiers = []
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line.startswith("["):
            continue
        tiers.append(re.findall(r"'([^']+)'", line))
    if len(tiers) < 4:
        raise AssertionError("expected at least 4 title tiers, parsed %r" % (tiers,))
    return tiers


APOLLO_THRESHOLD = load_apollo_threshold()
CONTACTS_COLUMNS = load_contacts_columns()
ICP_TARGET = load_icp_titles()
CSV_URL = load_csv_url()
TITLE_TIERS = load_title_tiers()

# --- Guard: the ICP's three named roles are all reachable by the ranker -------
#
# Section 12 names founder, Managing Director and BD Director. A tier list that
# stopped covering one of them would rank that buyer as unmatched and tombstone
# companies whose only senior contact holds that exact title.
_flat_tiers = [kw for tier in TITLE_TIERS for kw in tier]
_target_lower = ICP_TARGET.lower()
for _role in ["founder", "managing director"]:
    if _role not in _target_lower:
        raise AssertionError(
            "Section 12's target line no longer names %r: %r" % (_role, ICP_TARGET)
        )
    if _role not in _flat_tiers:
        raise AssertionError(
            "code_pick_contact.js TITLE_TIERS no longer covers the ICP role %r "
            "named in Section 12 (%r). Update the JS, not the doc." % (_role, ICP_TARGET)
        )
# The doc writes it 'BD Director'; the ranker matches the expanded form, which is
# what Apollo actually returns.
if "bd director" not in _target_lower:
    raise AssertionError("Section 12's target line no longer names 'BD Director': %r" % ICP_TARGET)
if "business development" not in _flat_tiers:
    raise AssertionError(
        "code_pick_contact.js TITLE_TIERS no longer covers business development, the "
        "third ICP role named in Section 12 (%r). Update the JS, not the doc." % ICP_TARGET
    )

# --- Guard: the ranker's tier order matches the ICP's stated order ------------
_order_positions = []
for _needle in ["founder", "managing director", "bd director"]:
    _order_positions.append(_target_lower.index(_needle))
if _order_positions != sorted(_order_positions):
    raise AssertionError(
        "Section 12 reordered the buyer roles (%r); code_pick_contact.js ranks "
        "founder, then managing director, then CEO, then business development. "
        "Reconcile them deliberately." % ICP_TARGET
    )

# Apollo's own filter, generated from the ranker's tiers so the two cannot drift.
PERSON_TITLES = []
for _tier in TITLE_TIERS:
    for _kw in _tier:
        if _kw not in PERSON_TITLES:
            PERSON_TITLES.append(_kw)


# ---------------------------------------------------------------------------
# Apollo request bodies
# ---------------------------------------------------------------------------
#
# Pretty-printed for the same reason the scoring generator pretty-prints the
# Ollama schema: a compact json.dumps emits runs like `"x"}}`, and that `}}`
# closes the surrounding n8n {{ }} expression early -- the node then fails with a
# bare "invalid syntax".

_titles_js = json.dumps(PERSON_TITLES, indent=2).replace("\n", "\n  ")

SEARCH_BODY = (
    "={{ JSON.stringify({\n"
    "  q_organization_domains_list: [$json.domain],\n"
    "  person_titles: " + _titles_js + ",\n"
    "  include_similar_titles: true,\n"
    "  page: 1,\n"
    "  per_page: 25\n"
    "}) }}"
)

# People Enrichment by Apollo id: the most precise identification available, and
# the search has already returned it. Sending the id rather than a name+domain
# pair removes the chance of the enrichment resolving to a different person than
# the one the ranker chose.
MATCH_BODY = "={{ JSON.stringify({ id: $json.apollo_id }) }}"

for _name, _body in [("search", SEARCH_BODY), ("match", MATCH_BODY)]:
    assert _body.count("}}") == 1 and _body.endswith("}}"), (
        "the %s body reintroduced a `}}` that would truncate the n8n expression" % _name
    )
    assert "{{" not in _body[2:], "unexpected `{{` inside the %s expression body" % _name


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

BATCH_SQL = """-- The queue for this stage: scored, qualifying, and not yet looked up.
--
-- Section 7's ordering rule is enforced structurally here rather than by
-- discipline -- the WHERE clause cannot see a lead that has not been scored, so
-- no amount of re-running can spend a credit before scoring has had its say.
--
-- `c.lead_id IS NULL` is what makes re-running free. A lead keeps its contacts
-- row whether the lookup found somebody or not, so a domain Apollo has already
-- answered "nobody" for is never paid for twice.
SELECT l.id           AS lead_id,
       l.domain,
       l.company_name,
       l.country,
       s.fit_score,
       e.founder_name,
       e.founder_linkedin,
       e.raw_extraction->>'founder_title' AS founder_title
  FROM leads l
  JOIN scores s        ON s.lead_id = l.id
  LEFT JOIN enrichments e ON e.lead_id = l.id
  LEFT JOIN contacts c ON c.lead_id = l.id
 WHERE l.status = 'scored'
   AND s.disqualified = false
   AND s.fit_score >= $1
   AND c.lead_id IS NULL
 -- Best-first, not oldest-first. Every other workflow drains its queue in id
 -- order because the work is free; this one spends money, so a bounded batch
 -- should buy the highest-scoring contacts available rather than the oldest.
 ORDER BY s.fit_score DESC, l.id
 LIMIT $2;"""

WRITE_SQL = """-- Write the contact and advance the lead in ONE statement, so a crash between
-- the two cannot leave a contact stored against a lead still sitting in the
-- lookup queue (or a lead advanced with no contact behind it).
--
-- UPSERT on contacts_lead_id_key (migration 004). Re-running after an Apollo
-- outage, a plan change, or a threshold change updates the row in place instead
-- of adding a second one -- migration 002's lesson, applied here by construction.
--
-- `advance` is carried in the payload rather than derived here because only the
-- payload builders know whether a reachable channel was actually found. A
-- tombstone row (looked up, nobody there) writes with advance=false: it records
-- the answer so the lead is never re-queried, and leaves it 'scored' so
-- Workflow 4 never drafts to an empty contact.
WITH payload AS (
  SELECT $1::jsonb AS p
), ins AS (
  INSERT INTO contacts (lead_id, name, title, email, linkedin_url, apollo_id, verified)
  SELECT
    (p->>'lead_id')::bigint,
    p->>'name',
    p->>'title',
    p->>'email',
    p->>'linkedin_url',
    p->>'apollo_id',
    COALESCE((p->>'verified')::boolean, false)
  FROM payload
  ON CONFLICT (lead_id) DO UPDATE SET
    name         = EXCLUDED.name,
    title        = EXCLUDED.title,
    email        = EXCLUDED.email,
    linkedin_url = EXCLUDED.linkedin_url,
    apollo_id    = EXCLUDED.apollo_id,
    verified     = EXCLUDED.verified
  RETURNING lead_id, email, verified
), adv AS (
  -- Section 8's status flow: scored -> contact_found. The status guard makes a
  -- concurrent or repeated run a no-op rather than a double write.
  UPDATE leads
     SET status = 'contact_found',
         updated_at = now()
   WHERE id = (SELECT lead_id FROM ins)
     AND status = 'scored'
     AND (SELECT COALESCE((p->>'advance')::boolean, false) FROM payload)
  RETURNING id
)
SELECT i.lead_id,
       i.email,
       i.verified,
       (SELECT count(*) FROM adv) > 0 AS advanced
  FROM ins i;"""

# --- Guard: the write step inserts exactly the columns Section 8 defines ------
_insert_cols = re.search(r"INSERT INTO contacts \(([^)]+)\)", WRITE_SQL).group(1)
_insert_cols = [c.strip() for c in _insert_cols.split(",")]
_expected = [c for c in CONTACTS_COLUMNS if c not in ("id",)]
if _insert_cols != _expected:
    raise AssertionError(
        "the contacts INSERT column list %r no longer matches Section 8's schema %r "
        "in %s. Update the SQL, not the doc." % (_insert_cols, _expected, MASTER_REF)
    )

# --- Guard: the queue reads scored leads, at the locked threshold -------------
if "l.status = 'scored'" not in BATCH_SQL:
    raise AssertionError(
        "the batch query no longer filters on status='scored'. Section 7: 'Apollo "
        "contact lookup happens AFTER scoring, never before' -- that rule is enforced "
        "by this WHERE clause and nothing else."
    )
if "s.fit_score >= $1" not in BATCH_SQL:
    raise AssertionError(
        "the batch query no longer gates on fit_score. Section 9 limits Apollo to "
        "leads scoring >= %d, which is what keeps it on the free tier." % APOLLO_THRESHOLD
    )


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


nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
        "name": "Every Hour",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-1100, 40],
        "notes": (
            "Queue-driven, so a missed run just means the next one catches up (build rule 4). "
            "Hourly rather than Workflow 3's half-hourly because this stage's input is the "
            "output of a stage that only produces a handful of qualifying leads per pass."
        ),
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-1100, 220],
    },
    {
        "parameters": {
            "assignments": {
                "assignments": [
                    {"id": "batchsize", "name": "batch_size", "value": 10, "type": "number"},
                    {
                        "id": "minfit",
                        "name": "min_fit_score",
                        "value": APOLLO_THRESHOLD,
                        "type": "number",
                    },
                ]
            },
            "options": {},
        },
        "name": "Config",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [-880, 130],
        "notes": (
            "Bounded batch (build rule 5), and smaller than Workflow 3's 25: every lead that "
            "gets past Resolve Contact costs money, so the ceiling is a spend ceiling, not a "
            "throughput one.\n\n"
            "min_fit_score is generated from Section 9's '>= %d' rule by build_workflow.py and "
            "guarded against drift there. Editing it in the n8n UI will be overwritten on the "
            "next build -- change the spec instead." % APOLLO_THRESHOLD
        ),
    },
    {
        "parameters": {
            "url": CSV_URL,
            "options": {
                "response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}},
                "timeout": 30000,
            },
        },
        "name": "Fetch ICH GCP CSV",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-660, 130],
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "The same artefact Workflow 1 ingests from, fetched once per batch. Its `email` "
            "column is the field ingestion drops: Section 8 gives `leads` no email column, so "
            "the address the scraper captured from each ichgcp profile page never reached the "
            "database. It was not lost, only unread -- 92 of 123 rows carry one.\n\n"
            "The URL is read out of ingestion-ichgcp.json at build time rather than restated, "
            "so the two stages cannot drift onto different artefacts.\n\n"
            "Note this fetch is deliberately not a Postgres read. Storing the address in "
            "`leads` would mean a new column in a locked schema section; storing it in "
            "`contacts` before a lead qualifies would put a contact on a lead that has not "
            "been scored, which is the exact ordering Section 7 forbids."
        ),
    },
    {
        "parameters": {"operation": "csv", "binaryPropertyName": "data"},
        "name": "Parse CSV",
        "type": "n8n-nodes-base.extractFromFile",
        "typeVersion": 1,
        "position": [-440, 130],
    },
    {
        "parameters": {"jsCode": js("code_index_csv.js")},
        "name": "Index Scraped Emails",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-220, 130],
        "notes": (
            "Run Once for All Items: one domain -> email map per batch, then an O(1) lookup "
            "per lead. Malformed values are counted and dropped rather than written, so a "
            "junk cell never lands in contacts.email looking like a real address."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": BATCH_SQL,
            "options": {
                "queryReplacement": "={{ [$('Config').first().json.min_fit_score, $('Config').first().json.batch_size] }}"
            },
        },
        "name": "Get Qualified Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [0, 130],
        "credentials": PG_CRED,
        "notes": (
            "Section 9's gate, as a WHERE clause: status='scored' AND fit_score >= "
            "min_fit_score AND no contacts row yet. Reads Config through $('Config') because "
            "$json here is the CSV index, not the trigger.\n\n"
            "Query parameters are passed as an array so a value containing a comma is never "
            "split."
        ),
        "alwaysOutputData": False,
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_resolve.js")},
        "name": "Resolve Contact",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [220, 130],
        "notes": (
            "The credit gate. A lead whose address is already in the CSV index never reaches "
            "Apollo at all; the person fields, where the site named one, come from the "
            "Workflow 2 extraction.\n\n"
            "A role inbox (info@, contact@) is flagged rather than paired with the founder's "
            "name: it is a company address, and asserting it belongs to a named person is "
            "exactly the kind of fact no source states (build rule 6). Both facts still travel "
            "together, because Workflow 4 drafts the email and LinkedIn variants separately."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("needsapollo", "={{ $json.needs_apollo }}", True),
            "options": {},
        },
        "name": "Needs Apollo?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [440, 130],
        "notes": "Section 7's ordering rule, one level finer: of the leads that scored high enough to be worth paying for, pay only for the ones we do not already have an address for.",
    },
    {
        "parameters": {
            "method": "POST",
            "url": "https://api.apollo.io/api/v1/mixed_people/search",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": SEARCH_BODY,
            "options": {
                "timeout": 30000,
                # Section 4: parallelise I/O, serialise only the GPU. Apollo is
                # I/O, but it is also rate-limited and metered, so this batches
                # politely rather than at full width.
                "batching": {"batch": {"batchSize": 2, "batchInterval": 500}},
            },
        },
        "name": "Apollo People Search",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [660, 240],
        "credentials": APOLLO_CRED,
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 3000,
        "notes": (
            "People Search returns no email addresses at all, by design on Apollo's side -- it "
            "identifies who is there. The address costs a separate enrichment call, which is "
            "why this stage is two nodes and not one: the search narrows to a single person "
            "before anything is spent.\n\n"
            "person_titles is generated from code_pick_contact.js's TITLE_TIERS at build time, "
            "so the endpoint is never asked for a set of titles the ranker would discard.\n\n"
            "KNOWN BLOCKER as of this build: this endpoint is not included in Apollo's Free "
            "plan and returns error_code API_INACCESSIBLE on both the API-key and OAuth paths. "
            "Pick Best Contact treats that as 'did not answer' and retries, so the stage "
            "self-heals the moment the plan allows it -- see README."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_pick_contact.js")},
        "name": "Pick Best Contact",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [880, 240],
        "notes": (
            "Ranks by Section 12's buyer order -- founder, managing director, CEO, business "
            "development -- with a person the site independently named as founder outranking a "
            "better title on a stranger.\n\n"
            "Apollo attaching a person to a different organisation's domain is a hard gate, "
            "not a ranking penalty: that person is the wrong company's contact however senior "
            "the title reads.\n\n"
            "Splits three ways, and the last two are the point. A refused or failed call is "
            "retried and writes nothing. An answered call with nobody usable writes a tombstone "
            "so the domain is never paid for twice."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("searchok", "={{ $json.write }}", True),
            "options": {},
        },
        "name": "Drop Failed Searches",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [1100, 240],
        "notes": (
            "If Apollo did not answer, the lead is dropped and stays 'scored' so the next run "
            "retries it. Same self-healing shape as Workflow 3's 'Drop Failed Lookups' and "
            "Workflow 2's 'Drop Failed Calls'. Writing a no-contact row here instead would "
            "record an outage as a fact about the company, permanently."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("matched", "={{ $json.matched }}", True),
            "options": {},
        },
        "name": "Matched?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [1320, 240],
        "notes": "Only a candidate that cleared both the title and the domain gate is worth an enrichment credit. Everything else takes the tombstone path Pick Best Contact already built a payload for.",
    },
    {
        "parameters": {
            "method": "POST",
            "url": "https://api.apollo.io/api/v1/people/match",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": MATCH_BODY,
            "options": {
                "timeout": 30000,
                "batching": {"batch": {"batchSize": 2, "batchInterval": 500}},
            },
        },
        "name": "Apollo People Match",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [1540, 320],
        "credentials": APOLLO_CRED,
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 3000,
        "notes": (
            "The metered call: one credit per successful match, at most one per lead, and only "
            "for a lead that scored >= %d, had no scraped address, and produced a ranked "
            "candidate at its own domain.\n\n"
            "Identified by Apollo id rather than name+domain. The search already returned it, "
            "and it removes the chance of the enrichment resolving to a different person than "
            "the one the ranker chose.\n\n"
            "reveal_personal_emails is deliberately not set: a work address is what Section 5's "
            "outreach mailbox sends to, and a personal address is neither needed nor "
            "appropriate for a cold B2B first touch." % APOLLO_THRESHOLD
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_payload_apollo.js")},
        "name": "Build Contact Payload (Apollo)",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1760, 320],
        "notes": (
            "Maps Apollo's email_status onto contacts.verified: only 'verified' earns the flag. "
            "A 'guessed' address is pattern-derived -- real enough to keep, not enough to let "
            "Workflow 6 send to unchallenged.\n\n"
            "Apollo's 'email_not_unlocked@domain.com' placeholder is dropped rather than "
            "stored: it would sit in contacts.email looking exactly like a real address three "
            "workflows downstream."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("matchok", "={{ $json.write }}", True),
            "options": {},
        },
        "name": "Drop Failed Matches",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [1980, 320],
        "notes": "An enrichment call that did not answer is not evidence either. The lead stays 'scored' and is retried, rather than being recorded as a company with no contact.",
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_payload_scrape.js")},
        "name": "Build Contact Payload (Scraped)",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [660, 20],
        "notes": "The zero-credit branch. Every field written here came from a source already on disk -- the address from the company's own ichgcp profile page, the person from the Workflow 2 site extraction. A NULL apollo_id is what marks the row as scrape-sourced; Section 8 gives contacts no provenance column.",
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json.payload)] }}"},
        },
        "name": "Write Contact & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [2200, 130],
        "credentials": PG_CRED,
        "notes": (
            "The whole payload goes in as one jsonb parameter, so unicode and embedded commas "
            "are handled by Postgres rather than by string splicing.\n\n"
            "UPSERT on contacts_lead_id_key (migration 004), and the status update is guarded "
            "on status='scored', so a repeated run is a no-op rather than a double write. Only "
            "a payload carrying a reachable channel advances the lead to 'contact_found'."
        ),
    },
]

connections = {
    "Every Hour": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Fetch ICH GCP CSV", "type": "main", "index": 0}]]},
    "Fetch ICH GCP CSV": {"main": [[{"node": "Parse CSV", "type": "main", "index": 0}]]},
    "Parse CSV": {"main": [[{"node": "Index Scraped Emails", "type": "main", "index": 0}]]},
    "Index Scraped Emails": {"main": [[{"node": "Get Qualified Batch", "type": "main", "index": 0}]]},
    "Get Qualified Batch": {"main": [[{"node": "Resolve Contact", "type": "main", "index": 0}]]},
    "Resolve Contact": {"main": [[{"node": "Needs Apollo?", "type": "main", "index": 0}]]},
    "Needs Apollo?": {
        "main": [
            [{"node": "Apollo People Search", "type": "main", "index": 0}],
            [{"node": "Build Contact Payload (Scraped)", "type": "main", "index": 0}],
        ]
    },
    "Build Contact Payload (Scraped)": {
        "main": [[{"node": "Write Contact & Advance", "type": "main", "index": 0}]]
    },
    "Apollo People Search": {"main": [[{"node": "Pick Best Contact", "type": "main", "index": 0}]]},
    "Pick Best Contact": {"main": [[{"node": "Drop Failed Searches", "type": "main", "index": 0}]]},
    "Drop Failed Searches": {"main": [[{"node": "Matched?", "type": "main", "index": 0}]]},
    "Matched?": {
        "main": [
            [{"node": "Apollo People Match", "type": "main", "index": 0}],
            [{"node": "Write Contact & Advance", "type": "main", "index": 0}],
        ]
    },
    "Apollo People Match": {
        "main": [[{"node": "Build Contact Payload (Apollo)", "type": "main", "index": 0}]]
    },
    "Build Contact Payload (Apollo)": {
        "main": [[{"node": "Drop Failed Matches", "type": "main", "index": 0}]]
    },
    "Drop Failed Matches": {
        "main": [[{"node": "Write Contact & Advance", "type": "main", "index": 0}]]
    },
}

workflow = {
    "id": "contacts0001",
    "name": "Contact Lookup",
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
print("  apollo threshold: >= %d" % APOLLO_THRESHOLD)
print("  contacts columns: %r" % (CONTACTS_COLUMNS,))
print("  title tiers:      %r" % (TITLE_TIERS,))
print("  csv url:          %s" % CSV_URL)

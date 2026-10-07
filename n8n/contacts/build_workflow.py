"""Assemble n8n/workflows/apollo-contacts.json from the tested Code-node sources.

Same contract as the enrichment and scoring generators: the .js files are read
verbatim and embedded, so the JS that was tested standalone is byte-identical to
the JS that ships inside the workflow.

This generator holds the spec-drift guards for Workflow 3b. Four things this
stage depends on are locked elsewhere and cannot be imported into an n8n Code
node:

    Section  9  the Apollo score gate    -> what keeps Apollo on the free tier
                (>= 60 until 2026-10-06, >= 50 since; read the doc, not this line)
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
   -- Added 2026-10-07 (migration 015). THE STARVATION FIX. Without this, a
   -- lead with no scraped address, no address on its own site and no founder
   -- LinkedIn goes to Apollo, is refused by the Free plan, writes nothing, and
   -- comes back at the TOP of the next hour's batch -- `fit_score DESC` puts
   -- the same stuck leads first every time. Nine were waiting on 2026-10-07;
   -- at ten, the batch of 10 is full of them and no new lead is ever looked up
   -- again. `contacts` could not record this: a tombstone there means "asked,
   -- nobody there", which is a different thing from "asked, Apollo would not
   -- answer", and Section 8 locks that table's columns.
   --
   -- Two ways out of the queue, and the second is the one that cannot be
   -- defeated: 'exhausted' retires a lead whose every source has genuinely
   -- answered, and the attempt count retires one whose refusal nobody
   -- recognised. Delete the contact_attempts row to put a lead back.
   AND NOT EXISTS (
         SELECT 1 FROM contact_attempts a
          WHERE a.lead_id = l.id
            AND (a.last_outcome = 'exhausted' OR a.attempts >= $3))
 -- Best-first, not oldest-first. Every other workflow drains its queue in id
 -- order because the work is free; this one spends money, so a bounded batch
 -- should buy the highest-scoring contacts available rather than the oldest.
 -- Safe to keep now that a lead nobody can reach leaves the queue instead of
 -- sitting at the top of it.
 ORDER BY s.fit_score DESC, l.id
 LIMIT $2;"""

# How many pages of a lead's own site Harvest Site Emails may read (the
# homepage plus contact/imprint/about/team links discovered on it), and how many
# lookups a lead gets before it leaves the queue for needs_manual_contact.
#
# 3, not 1: a lead whose every source has answered is retired at once by
# `last_outcome = 'exhausted'`, so this number only bounds the case where a
# refusal was NOT recognised as permanent -- a shape nobody has seen yet. Three
# hourly tries is enough tolerance for a blip and short enough that ten stuck
# leads cannot sit in the batch for half a day. A lead retired by the count
# alone shows `last_outcome = 'refused'` in needs_manual_contact, which is the
# signal that the recogniser needs a new case rather than the lead needing a
# human.
SITE_MAX_PAGES = 5
MAX_ATTEMPTS = 3

ATTEMPT_SQL = """-- Record a lookup that produced no contact (migration 015).
--
-- $1 code_pick_contact.js's `attempt_row`: {lead_id, outcome, detail}
--
-- This is the queue's memory. Without it the batch query cannot tell "not
-- looked up yet" from "looked up, nobody would answer", and a lead Apollo
-- refuses comes back at the top of every hour's batch for ever.
--
-- `attempts` counts up across runs; `first_at` keeps the first time anybody
-- asked, so needs_manual_contact can show how long a lead has been waiting.
-- The lead is deliberately NOT advanced: Section 8's status flow has no state
-- for "asked and found nothing", and 'contact_found' would put a contactless
-- lead in front of Workflow 4's grounding guard.
INSERT INTO contact_attempts (lead_id, attempts, last_outcome, detail)
SELECT (p->>'lead_id')::bigint, 1, p->>'outcome', p->>'detail'
  FROM (SELECT $1::jsonb AS p) s
ON CONFLICT (lead_id) DO UPDATE SET
  attempts     = contact_attempts.attempts + 1,
  -- Once exhausted, always exhausted: a later transient refusal must not
  -- demote a lead back into the queue it has already left.
  last_outcome = CASE WHEN contact_attempts.last_outcome = 'exhausted'
                      THEN 'exhausted' ELSE EXCLUDED.last_outcome END,
  detail       = EXCLUDED.detail,
  last_at      = now()
RETURNING lead_id, attempts, last_outcome;"""

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
), att AS (
  -- The attempt record, in the same statement as the contact (migration 015).
  --
  -- A tombstone (Apollo answered, nobody there) records 'exhausted', so the
  -- lead leaves the queue and turns up in needs_manual_contact with the reason.
  -- A real contact deletes any record there is: the lead has a channel now, and
  -- a stale row would show it as still waiting for a human.
  --
  -- Here rather than in a node of its own because a crash between the two would
  -- leave a contact stored against a lead the queue still thinks is untried, or
  -- a lead retired with a contact nobody can see -- the same reason the contact
  -- and the status move together.
  INSERT INTO contact_attempts (lead_id, attempts, last_outcome, detail)
  SELECT (p->>'lead_id')::bigint, 1, p->'attempt'->>'outcome', p->'attempt'->>'detail'
    FROM payload
   WHERE p->'attempt' IS NOT NULL AND p->'attempt' <> 'null'::jsonb
  ON CONFLICT (lead_id) DO UPDATE SET
    attempts     = contact_attempts.attempts + 1,
    last_outcome = CASE WHEN contact_attempts.last_outcome = 'exhausted'
                        THEN 'exhausted' ELSE EXCLUDED.last_outcome END,
    detail       = EXCLUDED.detail,
    last_at      = now()
  RETURNING lead_id
), cleared AS (
  DELETE FROM contact_attempts
   WHERE lead_id = (SELECT lead_id FROM ins)
     AND (SELECT p->'attempt' IS NULL OR p->'attempt' = 'null'::jsonb FROM payload)
  RETURNING lead_id
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
       (SELECT count(*) FROM adv) > 0     AS advanced,
       (SELECT count(*) FROM att) > 0     AS attempt_recorded,
       (SELECT count(*) FROM cleared) > 0 AS attempt_cleared
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


# --- Guard: a lead that cannot be reached leaves the queue (migration 015) ----
#
# This is the starvation fix, and it is one WHERE clause: without it a lead
# Apollo will never answer for comes back at the top of every batch of 10 for
# ever, because the queue is ordered by fit_score and has no memory of a lookup
# that found nothing. Nine leads were already in that state on 2026-10-07.
if "FROM contact_attempts a" not in BATCH_SQL:
    raise AssertionError(
        "the batch query no longer excludes leads with a contact_attempts record. A lead "
        "with no scraped address, no address on its own site and no founder LinkedIn is "
        "refused by Apollo every run; without this clause it occupies a slot in every batch "
        "for ever, and at batch_size such leads the queue never reaches a new lead again."
    )
for _needed in ("a.last_outcome = 'exhausted'", "a.attempts >= $3"):
    if _needed not in BATCH_SQL:
        raise AssertionError(
            "the batch query lost %r. Both halves matter: 'exhausted' retires a lead whose "
            "every source has answered, and the attempt count retires one whose refusal "
            "nobody recognised -- the second is the half that cannot be defeated by an "
            "unfamiliar error shape." % _needed
        )
if "$3" not in BATCH_SQL:
    raise AssertionError("the batch query does not take max_attempts as a parameter")

# --- Guard: every outcome of a lookup is recorded -----------------------------
#
# Three places write the queue's memory, and all three have to keep doing it:
# the refusal branch through its own node, and the tombstone through the write
# statement. A match clears the record instead, so a lead that has since been
# reached leaves needs_manual_contact.
_pick = js("code_pick_contact.js")
for _needed, _why in (
    ("attempt_row", "the refusal branch must emit attempt_row for Record Lookup Attempt"),
    ("'exhausted'", "a permanent refusal must be recorded as exhausted, not retried for ever"),
    ("'refused'", "a transient refusal must be recorded as refused, so even it is counted"),
    ("isPlanGate", "nothing tells a permanent refusal from a transient one any more"),
):
    if _needed not in _pick:
        raise AssertionError("code_pick_contact.js lost %r -- %s" % (_needed, _why))
for _f in ("code_payload_apollo.js", "code_payload_scrape.js"):
    if "attempt: null" not in js(_f):
        raise AssertionError(
            "%s no longer sets payload.attempt = null. A lead that has just been reached "
            "would keep its earlier contact_attempts row and go on showing in "
            "needs_manual_contact as waiting for a human." % _f
        )
for _needed in ("p->'attempt'->>'outcome'", "DELETE FROM contact_attempts"):
    if _needed not in WRITE_SQL:
        raise AssertionError(
            "the write statement lost %r. The contact, the status move and the attempt record "
            "belong in one statement: a crash between them would leave a contact stored "
            "against a lead the queue still thinks is untried." % _needed
        )
if "last_outcome = 'exhausted'" not in ATTEMPT_SQL or "ELSE EXCLUDED.last_outcome" not in ATTEMPT_SQL:
    raise AssertionError(
        "ATTEMPT_SQL no longer keeps 'exhausted' sticky. A later transient refusal would "
        "demote a retired lead back into the queue it has already left."
    )

# --- Guard: the new free source is wired, and nothing is guessed --------------
#
# The whole value of this source is that it is free and literal. A derived
# address (first.last@, or info@<domain> because most companies have one) would
# be written `verified = true` on the strength of a pattern, which is exactly
# the line Section 9 draws between a published address and Apollo's `guessed`.
_site = js("code_site_emails.js")
if "helpers.httpRequest" not in _site:
    raise AssertionError("code_site_emails.js no longer fetches anything")
for _needed, _why in (
    ("onOwnDomain", "the same-domain rule is the only thing keeping another company's address out"),
    ("NEVER", "without the never-a-contact list a cold email can go to careers@ or ir@"),
    ("MAILTO_RE", "a mailto: href is half of what 'literally on the page' means"),
):
    if _needed not in _site:
        raise AssertionError("code_site_emails.js lost %r -- %s" % (_needed, _why))
_resolve = js("code_resolve.js")
if "site_email" not in _resolve:
    raise AssertionError(
        "code_resolve.js does not read site_email, so Harvest Site Emails fetches every "
        "lead's website and nothing uses the answer"
    )
if "verified: true" not in _resolve.split("source: 'site_published'")[1].split("evidence:")[0]:
    raise AssertionError(
        "the site-published branch no longer writes verified = true. Section 9's definition: "
        "a first-party address the company published about itself IS confirmed."
    )

# --- Guard: the schedule cannot depend on the host being up at one moment -----
#
# Section 7's idempotency rule. n8n 2.35.7 gates an "every N hours" rule
# (N > 1) on the CLOCK HOUR of the last run and an "every N days" rule on the
# day of the year, so a tick at the same clock value reads as no time elapsed --
# the bug that cost three follow-ups on 2026-10-02 (Section 9, Workflow 6). A
# `days` rule with triggerAtHour is worse still: it is a single fixed slot, and
# the host is off at night.
def _assert_schedule_catches_up(node):
    rule = node["parameters"]["rule"]["interval"]
    for iv in rule:
        field = iv.get("field")
        if field == "minutes":
            size = int(iv.get("minutesInterval", 5))
            assert 1 <= size <= 59, "a minutes interval must be 1-59, got %d" % size
            continue
        if field == "hours" and int(iv.get("hoursInterval", 1)) == 1 and "triggerAtMinute" not in iv:
            # Verified against the installed n8n 2.35.7: intervalToRecurrence
            # activates no recurrence check for hoursInterval == 1, so this
            # fires every hour at a stable minute with no clock-value gate.
            continue
        raise AssertionError(
            "the %s trigger is %r. A schedule that must catch one moment misses it on a host "
            "that is off at night (Section 7's idempotency rule): use a minutes interval, or "
            "hours with interval 1 and no fixed minute." % (node["name"], iv)
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
                    {"id": "sitepages", "name": "site_max_pages", "value": SITE_MAX_PAGES,
                     "type": "number"},
                    {"id": "maxattempts", "name": "max_attempts", "value": MAX_ATTEMPTS,
                     "type": "number"},
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
                "queryReplacement": "={{ [$('Config').first().json.min_fit_score, $('Config').first().json.batch_size, $('Config').first().json.max_attempts] }}"
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
        "parameters": {"mode": "runOnceForAllItems", "jsCode": js("code_site_emails.js")},
        "name": "Harvest Site Emails",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [220, 130],
        "notes": (
            "The third free source (2026-10-07): an address the company publishes on its own "
            "website. Only for a lead the CSV has no address for -- it reads the index itself "
            "and skips the rest, so no prospect's server is asked for data already on disk.\n\n"
            "Literal addresses only (a mailto: link or text on the page), the lead's own domain "
            "only, and never a careers@/ir@/privacy@-class mailbox. Nothing is pattern-derived: "
            "that is the line contacts.verified draws.\n\n"
            "Run Once for All Items because the fetches are concurrent. It passes each lead row "
            "through unchanged with site_email and the evidence added, so Resolve Contact still "
            "reads one item per lead and keeps every decision in one place."
        ),
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
        "name": "Apollo Answered?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [1100, 240],
        "notes": (
            "Was 'Drop Failed Searches', a filter that discarded a refusal. It no longer "
            "discards: the lead still gets no `contacts` row -- writing one would record an "
            "outage as a fact about the company, permanently -- but the attempt is recorded "
            "(migration 015), which is what stops a lead Apollo will never answer for from "
            "filling every batch of 10 for ever.\n\n"
            "true: Apollo answered, go on to the ranking. false: it did not, record the "
            "attempt. code_pick_contact.js decides whether that attempt is 'refused' (retried) "
            "or 'exhausted' (the plan gate, which answers the same way every hour)."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": ATTEMPT_SQL,
            "options": {"queryReplacement": "={{ JSON.stringify($json.attempt_row) }}"},
        },
        "name": "Record Lookup Attempt",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [1320, 360],
        "credentials": PG_CRED,
        "notes": (
            "The lead keeps no `contacts` row -- Apollo never answered about it -- but the "
            "queue now remembers that it was asked. `attempts` is what bounds a refusal "
            "nobody recognised; `last_outcome = 'exhausted'` retires one that has genuinely "
            "run out of sources. Either way the lead stays 'scored' and surfaces in "
            "needs_manual_contact with the detail as its `why`."
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

# Every schedule in this workflow has to survive a host that is off at night.
for _n in nodes:
    if _n["type"] == "n8n-nodes-base.scheduleTrigger":
        _assert_schedule_catches_up(_n)

connections = {
    "Every Hour": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Fetch ICH GCP CSV", "type": "main", "index": 0}]]},
    "Fetch ICH GCP CSV": {"main": [[{"node": "Parse CSV", "type": "main", "index": 0}]]},
    "Parse CSV": {"main": [[{"node": "Index Scraped Emails", "type": "main", "index": 0}]]},
    "Index Scraped Emails": {"main": [[{"node": "Get Qualified Batch", "type": "main", "index": 0}]]},
    "Get Qualified Batch": {"main": [[{"node": "Harvest Site Emails", "type": "main", "index": 0}]]},
    "Harvest Site Emails": {"main": [[{"node": "Resolve Contact", "type": "main", "index": 0}]]},
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
    "Pick Best Contact": {"main": [[{"node": "Apollo Answered?", "type": "main", "index": 0}]]},
    "Apollo Answered?": {
        "main": [
            [{"node": "Matched?", "type": "main", "index": 0}],
            [{"node": "Record Lookup Attempt", "type": "main", "index": 0}],
        ]
    },
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
print("  free sources:     ichgcp CSV, then up to %d pages of the lead's own site, then a founder LinkedIn profile" % SITE_MAX_PAGES)
print("  queue retirement: %d attempts, or one 'exhausted' answer (migration 015; delete the contact_attempts row to re-queue)" % MAX_ATTEMPTS)

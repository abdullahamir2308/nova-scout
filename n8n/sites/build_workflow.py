"""Generates n8n/workflows/trialsites.json -- Workflow 1b, sites and SMOs from Trialsites.

    python n8n/sites/build_workflow.py

Same contract as every other stage: the workflow is generated, never hand-edited,
so the JS that was tested standalone is byte-identical to the JS that ships.

WHAT IT DOES (Section 9, Workflow 1b). Every 30 minutes, two independent lanes:

  harvest  -- once a week (site_harvest_log, not a clock: a host that was off
              on the day simply harvests at its next tick), read the AIMR
              Global Clinical Research Site Database ("Trialsites", free API, no
              key, 60 requests a minute) for every included country, tiers A and
              B (one request in flight at a time, 1.1 s apart -- the API allows
              60 a minute), choose at most MAX_NEW new independent sites / SMOs by
              deterministic rules (code_select_candidates.js), write them to
              site_candidates.
  lookup   -- a bounded batch of candidates the website lookup has not tried:
              Claude Haiku 5.5 with web search proposes the official website,
              code fetches it and accepts it only when the candidate's name and
              city are both on it (code_verify_site.js). A confirmed site becomes
              a lead (`source='trialsites'`, `status='ingested'`), which
              Enrichment picks up like any other.

Guards here, rather than in a comment:

    Section 12  included / core countries   -> substituted from the clock table
    Section 12  excluded country-code TLDs  -> read from scraper/geography.py
    Section 4   lookup model, effort, tool,
                prices                      -> substituted from Section 4's table
    Section 7   idempotency                 -> minutes trigger only, no fixed slot
    Section 8   leads columns, never status
                on conflict                 -> what the lead INSERT may touch
    standing    no investigator data        -> the detail endpoint is never called
    wiring      every $json.X a node reads is emitted by the node above it
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.environ.get("NOVASCOUT_MASTER_REF", os.path.join(REPO, "NovaScout_MasterRef.md"))
OUT = os.environ.get("SITES_OUT", os.path.join(REPO, "n8n", "workflows", "trialsites.json"))

WORKFLOW_ID = "trialsites0001"
WORKFLOW_NAME = "Ingestion - Trialsites (sites and SMOs)"
PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}
ANTHROPIC_CRED = {"anthropicApi": {"id": "novascoutAnthropic01", "name": "Anthropic - Nova Scout drafting"}}

API = "https://api.aimronline.org/api/v1"
USER_AGENT = "NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)"
TICK_MINUTES = 30
HARVEST_EVERY_DAYS = 7          # "at most 200 new candidates per weekly run"
MAX_NEW = 200
PAGE = 500                      # Trialsites' own maximum page size
MAX_PAGES = 4                   # 2,000 rows per country and tier, ranked by recent trials
REQUEST_SPACING_MS = 1100       # 60 requests a minute is the documented limit
LOOKUP_BATCH = 10
LOOKUP_MAX_ATTEMPTS = 3         # for 'call-failed' only; every other outcome is final
LOOKUP_WORST_CASE_USD = 0.04    # three searches + ~50K input tokens on Haiku 5.5


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def js(name):
    return _read(os.path.join(HERE, name))


DOC = _read(MASTER_REF)

# ---------------------------------------------------------------------------
# Section 12: which countries, and which of them are core
# ---------------------------------------------------------------------------


def load_countries(doc):
    anchor = re.search(r"\*\*Business-hours clocks[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError("Section 12 'Business-hours clocks' heading not found in %s" % MASTER_REF)
    table = re.search(
        r"\n\| Country \| Standard offset \| Weekend \| Tier \|\r?\n\|[-|]+\|\r?\n(.*?)(?:\r?\n\r?\n|\Z)",
        doc[anchor.end():], re.S)
    if not table:
        raise AssertionError("no clock table follows the 'Business-hours clocks' heading")
    included, core = [], []
    for line in table.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError("clock row %r does not have 4 cells" % line)
        included.append(cells[0])
        if cells[3] == "core":
            core.append(cells[0])
    geo = re.search(r"\*\*Geographies:\*\*\s*([^\n]+)", doc)
    if not geo:
        raise AssertionError("Section 12's **Geographies:** line not found")
    listed = [c.strip().rstrip(".") for c in geo.group(1).split(",")]
    if sorted(listed) != sorted(core):
        raise AssertionError(
            "the clock table's core rows %r differ from Section 12's Geographies line %r"
            % (sorted(core), sorted(listed)))
    return included, core


INCLUDED, CORE = load_countries(DOC)
if len(INCLUDED) < 100 or len(CORE) != 13:
    raise AssertionError("expected 13 core and 100+ included countries, got %d / %d" % (len(CORE), len(INCLUDED)))

sys.path.insert(0, os.path.join(REPO, "scraper"))
import geography  # noqa: E402  (the scraper's own exclusion policy)

EXCLUDED_TLDS = sorted(geography.EXCLUDED_TLDS)
_excluded_names = set(geography.EXCLUDED)  # keyed on the canonical country name
for _c in INCLUDED:
    if _c in _excluded_names:
        raise AssertionError("%s is in the clock table and in the scraper's EXCLUDED list" % _c)

# ---------------------------------------------------------------------------
# Section 4: the lookup model, its settings and its prices
# ---------------------------------------------------------------------------


def load_lookup_settings(doc):
    t = re.search(r"\n\| Website lookup setting \| Value \|\r?\n\|[-|]+\|\r?\n(.*?)(?:\r?\n\r?\n|\Z)", doc, re.S)
    if not t:
        raise AssertionError("Section 4's 'Website lookup setting' table not found in %s" % MASTER_REF)
    rows = {}
    for line in t.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0]] = cells[1]

    def money(text, n):
        found = re.findall(r"\$([0-9]+(?:\.[0-9]+)?)", text)
        if len(found) != n:
            raise AssertionError("expected %d prices in %r" % (n, text))
        return [float(x) for x in found]

    model = re.search(r"`([a-z0-9-]+)`", rows["Model"]).group(1)
    effort = re.search(r"`([a-z]+)`", rows["Effort"]).group(1)
    tool = re.search(r"`(web_search_\d{8})`", rows["Web search tool"]).group(1)
    max_uses = int(re.search(r"`max_uses`\s*(\d+)", rows["Web search tool"]).group(1))
    inp, outp = money(rows["Input / output"], 2)
    cread, cwrite = money(rows["Cache read / write"], 2)
    (per_search,) = money(rows["Web search"], 1)
    cap = money(rows["Monthly cap"], 1)[0]
    return {
        "model": model, "effort": effort, "tool": tool, "max_uses": max_uses, "cap": cap,
        "prices": {"model": model, "input_per_mtok": inp, "output_per_mtok": outp,
                   "cache_read_per_mtok": cread, "cache_write_per_mtok": cwrite, "per_search": per_search},
    }


LOOKUP = load_lookup_settings(DOC)
if LOOKUP["model"] != "claude-haiku-5-5":
    raise AssertionError(
        "Section 4 names %r as the lookup model; this build was measured and written for "
        "claude-haiku-5-5 (Section 4's comparison). Re-measure before changing it." % LOOKUP["model"])
if not 1 <= LOOKUP["max_uses"] <= 5:
    raise AssertionError("max_uses %d is outside 1-5; it is the cost lever" % LOOKUP["max_uses"])
_worst = LOOKUP["max_uses"] * LOOKUP["prices"]["per_search"] + 50000 * LOOKUP["prices"]["input_per_mtok"] / 1e6 \
    + 4000 * LOOKUP["prices"]["output_per_mtok"] / 1e6
if _worst > LOOKUP_WORST_CASE_USD + 1e-9:
    raise AssertionError(
        "a lookup can cost $%.4f, above LOOKUP_WORST_CASE_USD $%.2f -- the batch query would "
        "size batches past the monthly cap" % (_worst, LOOKUP_WORST_CASE_USD))
_mig = _read(os.path.join(REPO, "postgres", "migrations", "019_sites_and_smos.sql"))
_seed = re.search(r"VALUES \('site_lookup_monthly_cap_usd', '([0-9.]+)'\)", _mig)
if not _seed or float(_seed.group(1)) != LOOKUP["cap"]:
    raise AssertionError("Section 4's monthly cap and migration 019's seed disagree")

LOOKUP_SYSTEM_PROMPT = "\n".join([
    "You find the official website of one clinical research organisation -- an independent",
    "research site, a site management organisation (SMO) or a similar company -- given its name",
    "and location exactly as a public trial-site registry lists them.",
    "",
    "Rules:",
    "- Search the web. Only return a URL that appeared in your search results. Never construct,",
    "  complete or guess a domain from the name.",
    "- The website must be this organisation's own: not a directory, registry, map, social network,",
    "  news article, or a page about it on someone else's site. If it belongs to a network of sites,",
    "  the network's own website is acceptable when it has a page for this location.",
    "- url: the homepage of that website. evidence_url: the page on the same website that shows the",
    "  organisation's name together with this city (often a contact or location page), or null if",
    "  the homepage shows both.",
    "- If the organisation is a hospital, a university or a government body, return url null.",
    "- If you are not confident the website is this exact organisation in this city, return url",
    "  null. A wrong website is worse than none.",
    "- confidence: high only when a page on that website names the organisation and this city.",
])

LOOKUP_REQUEST = {
    "model": LOOKUP["model"],
    "max_tokens": 4000,
    "output_config": {
        "effort": LOOKUP["effort"],
        "format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {
                "url": {"type": ["string", "null"]},
                "evidence_url": {"type": ["string", "null"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
            },
            "required": ["url", "evidence_url", "confidence"],
            "additionalProperties": False,
        }},
    },
    "tools": [{"type": LOOKUP["tool"], "name": "web_search", "max_uses": LOOKUP["max_uses"]}],
}

# ---------------------------------------------------------------------------
# Code-node sources
# ---------------------------------------------------------------------------

COUNTRIES_JS = (js("code_countries.js")
                .replace("__INCLUDED_COUNTRIES__", json.dumps(INCLUDED, ensure_ascii=False))
                .replace("__CORE_COUNTRIES__", json.dumps(CORE, ensure_ascii=False)))
if "$(" in COUNTRIES_JS or "$input" in COUNTRIES_JS:
    raise AssertionError("code_countries.js is prepended to two nodes and must not read $( or $input")

# The alias table may only point at included countries -- never a sanctioned one.
_alias_block = re.search(r"const TRIALSITES_ALIASES = \{(.*?)^\};", COUNTRIES_JS, re.S | re.M)
if not _alias_block:
    raise AssertionError("TRIALSITES_ALIASES could not be read")
_aliases = re.findall(r"^\s*'([^']+)':\s*'([^']+)',\s*$", _alias_block.group(1), re.M)
if not _aliases:
    raise AssertionError("TRIALSITES_ALIASES could not be read")
for _src, _dst in _aliases:
    if _dst not in INCLUDED:
        raise AssertionError("alias %r -> %r targets a country that is not included" % (_src, _dst))

HARVEST_JS = COUNTRIES_JS + "\n" + js("code_harvest_requests.js")
SELECT_JS = COUNTRIES_JS + "\n" + js("code_select_candidates.js").replace("__MAX_NEW__", str(MAX_NEW))
BUILD_JS = (js("code_build_lookup.js")
            .replace("__LOOKUP_REQUEST__", json.dumps(LOOKUP_REQUEST, indent=2))
            .replace("__LOOKUP_SYSTEM_PROMPT__", json.dumps(LOOKUP_SYSTEM_PROMPT)))
VERIFY_JS = (js("code_verify_site.js")
             .replace("__LOOKUP_PRICES__", json.dumps(LOOKUP["prices"]))
             .replace("__EXCLUDED_TLDS__", json.dumps(EXCLUDED_TLDS)))
for _name, _src in (("Build Harvest Requests", HARVEST_JS), ("Select Candidates", SELECT_JS),
                    ("Build Lookup Request", BUILD_JS), ("Verify Website", VERIFY_JS)):
    _left = re.findall(r"__[A-Z_]+__", _src)
    if _left:
        raise AssertionError("%s still has placeholders %r" % (_name, _left))

# --- Guard: investigators are never read -------------------------------------
# Comments are stripped first (they say, rightly, that investigators are never
# read); `investigator_count` is a per-site aggregate number, not a person.
for _name, _src in (("Select Candidates", SELECT_JS), ("Build Lookup Request", BUILD_JS),
                    ("Verify Website", VERIFY_JS), ("Build Harvest Requests", HARVEST_JS)):
    _code = re.sub(r"//[^\n]*", "", _src)
    if re.search(r"\binvestigators\b|\binvestigator_(?!count\b)\w+|/sites/['\"]?\s*\+|/sites/\$\{", _code):
        raise AssertionError(
            "%s reads investigator data or calls the site-detail endpoint. Standing rule: never use "
            "personal investigator emails from registry data -- Workflow 1b never calls /sites/{id}." % _name)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

STATE_SQL = """-- One row, always: is a harvest due, which locations are already known, and
-- what the monthly cap still allows (migration 019).
--
-- "Due" is a question about site_harvest_log, not about a clock: a host that is
-- off on harvest day harvests at its next tick (Section 7).
SELECT (SELECT max(harvested_at) FROM site_harvest_log)                         AS last_harvest_at,
       coalesce((SELECT max(harvested_at) FROM site_harvest_log)
                  < now() - make_interval(days => $1::int), true)               AS harvest_due,
       (SELECT coalesce(jsonb_agg(location_id), '[]'::jsonb) FROM site_candidates) AS known_ids,
       site_lookup_budget_left()                                                AS budget_left;"""

WRITE_SQL = """-- Write this harvest's candidates and its log row in ONE statement, so a
-- crash cannot leave candidates written with no harvest recorded (re-harvested
-- next tick, harmless) or a harvest recorded with no candidates (the week lost).
--
-- $1 Select Candidates' {candidates, stats}
--
-- ON CONFLICT DO NOTHING: a location already chosen keeps its row and its
-- lookup outcome; Select Candidates already left known ids out.
WITH p AS (SELECT $1::jsonb AS j),
ins AS (
  INSERT INTO site_candidates (location_id, canonical_name, city, state, country, trialsites_country,
                               site_tier, trial_count, recent_trials_3yr, active_recruiting,
                               quality_score, facility_type, network_type, primary_ta,
                               therapeutic_areas, priority)
  SELECT (c->>'location_id')::bigint, c->>'canonical_name', c->>'city', c->>'state', c->>'country',
         c->>'trialsites_country', c->>'site_tier', (c->>'trial_count')::int,
         (c->>'recent_trials_3yr')::int, (c->>'active_recruiting')::int,
         (c->>'quality_score')::numeric, c->>'facility_type', c->>'network_type', c->>'primary_ta',
         c->>'therapeutic_areas', (c->>'priority')::bigint
    FROM p, jsonb_array_elements(p.j->'candidates') AS c
  ON CONFLICT (location_id) DO NOTHING
  RETURNING location_id
)
INSERT INTO site_harvest_log (responses, rows_seen, eligible_new, chosen, stats)
SELECT (j->'stats'->>'responses')::int, (j->'stats'->>'rows_seen')::int,
       (j->'stats'->>'eligible_new')::int, (SELECT count(*) FROM ins)::int, j->'stats'
  FROM p
RETURNING id, chosen, eligible_new, rows_seen, responses;"""

BATCH_SQL = """-- The lookup queue: candidates never tried, and failed calls under the attempt
-- limit -- core countries first (priority is Select Candidates' ordering).
--
-- $1 batch size   $2 max attempts for a failed call   $3 worst-case cost of one lookup
--
-- The cap (migration 019): at most budget_left / worst-case lookups, so a batch
-- is never sized past what this calendar month still allows. At $0 left the
-- batch is empty and nothing downstream runs.
SELECT c.location_id, c.canonical_name, c.city, c.state, c.country, c.lookup_attempts
  FROM site_candidates c
 WHERE c.lookup_status = 'pending'
    OR (c.lookup_status = 'call-failed' AND c.lookup_attempts < $2::int)
 ORDER BY c.priority, c.location_id
 LIMIT greatest(0, least($1::int, floor(site_lookup_budget_left() / $3::numeric)::int));"""

RECORD_SQL = """-- Record one lookup: its outcome, its cost, and -- if the website was
-- confirmed -- the lead, all in ONE statement, so a crash cannot write a lead
-- whose candidate still says 'pending' (looked up and paid for again).
--
-- $1 Verify Website's verdict for one candidate
--
-- A confirmed domain that is already a lead is 'duplicate', not a second lead:
-- leads.domain is UNIQUE and the INSERT is ON CONFLICT DO NOTHING, so an
-- existing row -- an ICH GCP CRO, or another location of the same network -- is
-- never touched, its status least of all. The candidate is linked to it only if
-- that lead came from Trialsites too; a CRO keeps its CRO scoring.
WITH p AS (SELECT $1::jsonb AS j),
cand AS (
  SELECT c.* FROM site_candidates c, p WHERE c.location_id = (p.j->>'location_id')::bigint
),
new_lead AS (
  INSERT INTO leads (domain, company_name, country, source, status)
  SELECT p.j->>'domain', cand.canonical_name, cand.country, 'trialsites', 'ingested'
    FROM p, cand
   WHERE p.j->>'outcome' = 'resolved' AND coalesce(p.j->>'domain', '') <> ''
  ON CONFLICT (domain) DO NOTHING
  RETURNING id
),
old_lead AS (
  SELECT l.id, l.source FROM leads l, p
   WHERE p.j->>'outcome' = 'resolved' AND l.domain = p.j->>'domain'
),
upd AS (
  UPDATE site_candidates c SET
    lookup_status   = CASE WHEN p.j->>'outcome' <> 'resolved' THEN p.j->>'outcome'
                           WHEN EXISTS (SELECT 1 FROM new_lead) THEN 'resolved'
                           ELSE 'duplicate' END,
    lookup_attempts = c.lookup_attempts + 1,
    lookup_detail   = left(CASE WHEN p.j->>'outcome' = 'resolved' AND NOT EXISTS (SELECT 1 FROM new_lead)
                                THEN 'domain already a lead (id ' || coalesce((SELECT id FROM old_lead)::text, '?')
                                     || ', source ' || coalesce((SELECT source FROM old_lead), '?') || '); '
                                ELSE '' END || coalesce(p.j->>'detail', ''), 1000),
    looked_up_at    = now(),
    proposed_url    = p.j->>'proposed_url',
    website_url     = CASE WHEN p.j->>'outcome' = 'resolved' THEN p.j->>'website_url' END,
    domain          = CASE WHEN p.j->>'outcome' = 'resolved' THEN p.j->>'domain' END,
    lead_id         = CASE WHEN p.j->>'outcome' <> 'resolved' THEN c.lead_id
                           WHEN EXISTS (SELECT 1 FROM new_lead) THEN (SELECT id FROM new_lead)
                           WHEN (SELECT source FROM old_lead) = 'trialsites' THEN (SELECT id FROM old_lead)
                           ELSE NULL END
    FROM p
   WHERE c.location_id = (p.j->>'location_id')::bigint
  RETURNING c.location_id, c.lookup_status, c.lead_id, c.domain
),
logged AS (
  INSERT INTO site_lookup_log (location_id, model, input_tokens, output_tokens, cache_read_tokens,
                               cache_write_tokens, web_search_requests, cost_usd, outcome)
  SELECT (j->>'location_id')::bigint, j->>'model', coalesce((j->>'input_tokens')::int, 0),
         coalesce((j->>'output_tokens')::int, 0), coalesce((j->>'cache_read_tokens')::int, 0),
         coalesce((j->>'cache_write_tokens')::int, 0), coalesce((j->>'web_search_requests')::int, 0),
         coalesce((j->>'cost_usd')::numeric, 0), j->>'outcome'
    FROM p
   WHERE EXISTS (SELECT 1 FROM cand)
  RETURNING cost_usd
)
SELECT u.location_id, u.lookup_status, u.lead_id, u.domain, (SELECT cost_usd FROM logged) AS cost_usd
  FROM upd u;"""

# --- Guard: the lead INSERT writes only Section 8's columns -------------------
_leads_cols = re.search(r"\nleads\r?\n(.*?)\r?\n\r?\n", DOC, re.S).group(1)
_leads_cols = set(re.findall(r"[a-z_]+", _leads_cols.split("**")[0]))
_insert = [c.strip() for c in re.search(r"INSERT INTO leads \(([^)]+)\)", RECORD_SQL).group(1).split(",")]
if set(_insert) - _leads_cols:
    raise AssertionError("the lead INSERT writes %r, which Section 8 does not define" % (set(_insert) - _leads_cols))
if "DO NOTHING" not in RECORD_SQL[RECORD_SQL.index("INSERT INTO leads"):RECORD_SQL.index("RETURNING id")]:
    raise AssertionError(
        "the lead INSERT must be ON CONFLICT (domain) DO NOTHING: an existing lead -- a CRO already "
        "drafted or sent -- must never be touched, its status least of all")

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def boolean_condition(cid, expr):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{"id": cid, "leftValue": expr, "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        "combinator": "and",
    }


TRIALSITES_HEADERS = {"parameters": [{"name": "User-Agent", "value": USER_AGENT}]}

nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": TICK_MINUTES}]}},
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-1200, 200],
        "notes": (
            "Two lanes every tick. Harvest: weekly, decided by site_harvest_log, so a host that was "
            "off on harvest day harvests at its next tick (Section 7). Lookup: a bounded batch of "
            "candidates, sized by the monthly cap.\n\n"
            "Minutes, not hours or days: n8n 2.35.7 gates an hours rule (N > 1) on the clock hour of "
            "the last run -- the bug that cost three follow-ups on 2026-10-02."
        ),
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-1200, 400],
    },
    # ------------------------------------------------------------ harvest lane
    {
        "parameters": {
            "operation": "executeQuery",
            "query": STATE_SQL,
            "options": {"queryReplacement": "={{ [%d] }}" % HARVEST_EVERY_DAYS},
        },
        "name": "Load Site State",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-980, 100],
        "credentials": PG_CRED,
        "notes": "One row whatever the answer, so Harvest Due? always has something to read.",
    },
    {
        "parameters": {"conditions": boolean_condition("due", "={{ $json.harvest_due }}"), "options": {}},
        "name": "Harvest Due?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [-760, 100],
        "notes": "true: no harvest in the last %d days. false: the normal outcome of most ticks." % HARVEST_EVERY_DAYS,
    },
    {
        "parameters": {
            "url": API + "/countries",
            "sendHeaders": True,
            "headerParameters": TRIALSITES_HEADERS,
            "options": {"timeout": 60000},
        },
        "name": "Fetch Trialsites Countries",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-540, 0],
        "retryOnFail": True,
        "maxTries": 3,
        "waitBetweenTries": 5000,
        "notes": (
            "Read live, because Trialsites files some countries under two spellings (\"Czech Republic\" "
            "and \"Czechia\"), and a request for one misses the other's rows."
        ),
    },
    {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": HARVEST_JS},
        "name": "Build Harvest Requests",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-320, 0],
        "notes": "One item per included country spelling, per tier (A, B). Core countries first.",
    },
    {
        "parameters": {"batchSize": 1, "options": {}},
        "name": "Loop Over Requests",
        "type": "n8n-nodes-base.splitInBatches",
        "typeVersion": 3,
        "position": [-210, 0],
        "notes": (
            "One country/tier at a time, so one request is in flight at a time. Measured 2026-10-09: the "
            "HTTP node's own batching only sleeps between dispatches and never awaits a response, so slow "
            "answers overlapped, pages came on top, and 8 requests timed out at 60 s in the first four "
            "minutes of a harvest. 'done' carries every page back to Select Candidates."
        ),
    },
    {
        "parameters": {
            "url": API + "/sites",
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "country", "value": "={{ $json.trialsites_country }}"},
                {"name": "tier", "value": "={{ $json.tier }}"},
                {"name": "identified_only", "value": "1"},
                {"name": "sort", "value": "-recent_trials_3yr"},
                {"name": "limit", "value": str(PAGE)},
            ]},
            "sendHeaders": True,
            "headerParameters": TRIALSITES_HEADERS,
            "options": {
                "timeout": 60000,
                "pagination": {"pagination": {
                    "paginationMode": "updateAParameterInEachRequest",
                    "parameters": {"parameters": [
                        {"type": "qs", "name": "offset", "value": "={{ $pageCount * %d }}" % PAGE},
                    ]},
                    "paginationCompleteWhen": "other",
                    "completeExpression": "={{ !$response.body.results || $response.body.results.length < %d }}" % PAGE,
                    "limitPagesFetched": True,
                    "maxRequests": MAX_PAGES,
                    "requestInterval": REQUEST_SPACING_MS,
                }},
            },
        },
        "name": "Fetch Trialsites Sites",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-100, 0],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 3,
        "waitBetweenTries": 10000,
        "notes": (
            "Free, no key, 60 requests a minute. ONE request in flight at a time: Loop Over Requests "
            "hands this node a single country/tier, its pages are fetched one after another %d ms apart, "
            "and Pace Requests waits before the next. Up to %d pages of %d per country and tier, most "
            "recent trial activity first.\n\n"
            "Only the search endpoint is ever called. The site-detail endpoint (/sites/{id}) can carry "
            "investigator records and this workflow never requests it -- the standing rule: never use "
            "personal investigator emails from registry data.\n\n"
            "onError continue: a country that fails is counted in the harvest's stats as a failed "
            "response rather than failing the whole week." % (REQUEST_SPACING_MS, MAX_PAGES, PAGE)
        ),
    },
    {
        "parameters": {
            "amount": REQUEST_SPACING_MS / 1000.0,
            "unit": "seconds",
        },
        "name": "Pace Requests",
        "type": "n8n-nodes-base.wait",
        "typeVersion": 1.1,
        "position": [120, -120],
        "notes": (
            "%.1f s after each country/tier's last page, before the next request. Under 65 s n8n waits "
            "in memory, so the execution never parks." % (REQUEST_SPACING_MS / 1000.0)
        ),
    },
    {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": SELECT_JS},
        "name": "Select Candidates",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [120, 0],
        "notes": (
            "Deterministic only (build rule 3): included country, tier A/B, active, an independent site "
            "or SMO by type and by name, never a hospital, university or government body; at most %d "
            "new, core countries first." % MAX_NEW
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json)] }}"},
        },
        "name": "Write Candidates",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [340, 0],
        "credentials": PG_CRED,
        "notes": "Candidates and the harvest log row in one statement.",
    },
    # ------------------------------------------------------------- lookup lane
    {
        "parameters": {
            "operation": "executeQuery",
            "query": BATCH_SQL,
            "options": {"queryReplacement": "={{ [%d, %d, %s] }}" % (
                LOOKUP_BATCH, LOOKUP_MAX_ATTEMPTS, repr(LOOKUP_WORST_CASE_USD))},
        },
        "name": "Load Lookup Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-980, 400],
        "credentials": PG_CRED,
        "notes": (
            "At most %d candidates, and never more than the monthly cap's remainder divided by the "
            "worst-case cost of one lookup ($%.2f). An empty batch ends the lane." % (LOOKUP_BATCH, LOOKUP_WORST_CASE_USD)
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": BUILD_JS},
        "name": "Build Lookup Request",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-760, 400],
        "notes": "Name, city and country -- what Trialsites published about the organisation, nothing else.",
    },
    {
        "parameters": {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "anthropicApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.request) }}",
            "options": {"timeout": 180000, "response": {"response": {"neverError": True}}},
        },
        "name": "Claude Website Lookup",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-540, 400],
        "credentials": ANTHROPIC_CRED,
        "onError": "continueRegularOutput",
        "notes": (
            "%s, effort %s, %s (max_uses %d) -- Section 4 has the measurement that chose it.\n\n"
            "neverError + continue: a failed call is an item too, so Verify Website can pair every answer "
            "with its candidate by position and record the failure ('call-failed', retried up to %d "
            "attempts). No retry here: a retried call is a second paid lookup."
            % (LOOKUP["model"], LOOKUP["effort"], LOOKUP["tool"], LOOKUP["max_uses"], LOOKUP_MAX_ATTEMPTS)
        ),
    },
    {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": VERIFY_JS},
        "name": "Verify Website",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-320, 400],
        "notes": (
            "The model proposes, code decides. Accepted only if the site is the organisation's own (not a "
            "directory, social network, institution or sanctioned ccTLD) and its pages carry both the "
            "candidate's name and its city. No confident match is a skip, never a guess."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": RECORD_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json)] }}"},
        },
        "name": "Record Lookup",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-100, 400],
        "credentials": PG_CRED,
        "notes": (
            "Outcome, cost and (if confirmed) the lead in one statement. The lead is "
            "source='trialsites', status='ingested'; an existing domain is never touched."
        ),
    },
]

connections = {
    "Every 30 Minutes": {"main": [[{"node": "Load Site State", "type": "main", "index": 0},
                                   {"node": "Load Lookup Batch", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Load Site State", "type": "main", "index": 0},
                                 {"node": "Load Lookup Batch", "type": "main", "index": 0}]]},
    "Load Site State": {"main": [[{"node": "Harvest Due?", "type": "main", "index": 0}]]},
    "Harvest Due?": {"main": [[{"node": "Fetch Trialsites Countries", "type": "main", "index": 0}], []]},
    "Fetch Trialsites Countries": {"main": [[{"node": "Build Harvest Requests", "type": "main", "index": 0}]]},
    "Build Harvest Requests": {"main": [[{"node": "Loop Over Requests", "type": "main", "index": 0}]]},
    # splitInBatches v3: output 0 is 'done', output 1 is 'loop'.
    "Loop Over Requests": {"main": [[{"node": "Select Candidates", "type": "main", "index": 0}],
                                    [{"node": "Fetch Trialsites Sites", "type": "main", "index": 0}]]},
    "Fetch Trialsites Sites": {"main": [[{"node": "Pace Requests", "type": "main", "index": 0}]]},
    "Pace Requests": {"main": [[{"node": "Loop Over Requests", "type": "main", "index": 0}]]},
    "Select Candidates": {"main": [[{"node": "Write Candidates", "type": "main", "index": 0}]]},
    "Load Lookup Batch": {"main": [[{"node": "Build Lookup Request", "type": "main", "index": 0}]]},
    "Build Lookup Request": {"main": [[{"node": "Claude Website Lookup", "type": "main", "index": 0}]]},
    "Claude Website Lookup": {"main": [[{"node": "Verify Website", "type": "main", "index": 0}]]},
    "Verify Website": {"main": [[{"node": "Record Lookup", "type": "main", "index": 0}]]},
}

# --- Guard: the trigger cannot depend on the host being up at one moment -----
for _n in nodes:
    if _n["type"] != "n8n-nodes-base.scheduleTrigger":
        continue
    for _iv in _n["parameters"]["rule"]["interval"]:
        if _iv.get("field") != "minutes":
            raise AssertionError("the %s trigger is %r; Section 7 -- use minutes" % (_n["name"], _iv))
        for _fixed in ("triggerAtHour", "triggerAtMinute", "triggerAtDay", "triggerAtDayOfMonth"):
            if _fixed in _iv:
                raise AssertionError("the %s trigger pins %s" % (_n["name"], _fixed))

# --- Guard: Trialsites is read one request at a time --------------------------
_fetch = [n for n in nodes if n["name"] == "Fetch Trialsites Sites"][0]
if "batching" in _fetch["parameters"].get("options", {}):
    raise AssertionError("Fetch Trialsites Sites must not use the HTTP node's batching: it never awaits a "
                         "response, so requests overlap and can exceed Trialsites' 60 a minute")
if connections["Loop Over Requests"]["main"][1][0]["node"] != "Fetch Trialsites Sites" or \
        connections["Pace Requests"]["main"][0][0]["node"] != "Loop Over Requests":
    raise AssertionError("Fetch Trialsites Sites must run inside Loop Over Requests -> Pace Requests")

# --- Guard: the Claude node is never retried ----------------------------------
for _n in nodes:
    if _n["name"] == "Claude Website Lookup" and _n.get("retryOnFail"):
        raise AssertionError("Claude Website Lookup must not retry: a retry is a second paid lookup "
                             "the cap's batch sizing never accounted for")

# --- Guard: every $json.X a node reads is emitted by the node above it -------
_EMITS = {
    "Load Site State": set(re.findall(r"AS (\w+)", STATE_SQL)),
    "Build Harvest Requests": {"trialsites_country", "country", "tier", "core"},
    "Loop Over Requests": {"trialsites_country", "country", "tier", "core"},
    "Select Candidates": {"candidates", "stats"},
    "Load Lookup Batch": {"location_id", "canonical_name", "city", "state", "country", "lookup_attempts"},
    "Build Lookup Request": {"location_id", "canonical_name", "city", "state", "country", "lookup_attempts", "request"},
}
_BY_NAME = {n["name"]: n for n in nodes}
for _src, _dsts in connections.items():
    for _branch in _dsts["main"]:
        for _edge in _branch:
            _dst = _BY_NAME[_edge["node"]]
            # Code nodes are checked below by name; this is for expressions in
            # HTTP / Postgres / IF parameters.
            if _dst["type"] == "n8n-nodes-base.code" or _src not in _EMITS:
                continue
            _reads = set(re.findall(r"\$json\.(\w+)", json.dumps(_dst["parameters"])))
            _missing = sorted(_reads - _EMITS[_src])
            if _missing:
                raise AssertionError("%s reads $json.%s, which %s does not emit"
                                     % (_dst["name"], ", $json.".join(_missing), _src))
# Code nodes read their input by name; check those reads point at real nodes.
for _n in nodes:
    for _node_name in re.findall(r"\$\('([^']+)'\)", json.dumps(_n["parameters"])):
        if _node_name not in _BY_NAME:
            raise AssertionError("%s reads $('%s'), which is not a node here" % (_n["name"], _node_name))
_bl = re.search(r"const c = \$input\.item\.json;(.*)", BUILD_JS, re.S).group(1)
for _f in set(re.findall(r"\bc\.(\w+)", _bl)):
    if _f not in _EMITS["Load Lookup Batch"]:
        raise AssertionError("Build Lookup Request reads c.%s, which Load Lookup Batch does not emit" % _f)

workflow = {
    "id": WORKFLOW_ID,
    "name": WORKFLOW_NAME,
    "nodes": nodes,
    "connections": connections,
    "active": False,
    # A harvest carries ~30-40K Trialsites rows through the workflow; saved as
    # n8n execution data that is tens of MB every week in the n8n database for
    # nothing -- every outcome and every cent is already in site_candidates,
    # site_lookup_log and site_harvest_log. Failed executions are still saved.
    "settings": {"executionOrder": "v1", "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all"},
    "pinData": {},
}

out_dir = os.path.dirname(OUT)
if out_dir and not os.path.isdir(out_dir):
    os.makedirs(out_dir)
with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(workflow, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

print("wrote %s (%s)" % (OUT, WORKFLOW_ID))
print("  countries:  %d included (%d core); excluded ccTLDs %s" % (len(INCLUDED), len(CORE), ",".join(EXCLUDED_TLDS)))
print("  harvest:    every %d days, at most %d new candidates, %d pages x %d per country/tier"
      % (HARVEST_EVERY_DAYS, MAX_NEW, MAX_PAGES, PAGE))
print("  lookup:     %s effort %s, %s max_uses %d; batch %d; cap $%s/month"
      % (LOOKUP["model"], LOOKUP["effort"], LOOKUP["tool"], LOOKUP["max_uses"], LOOKUP_BATCH, LOOKUP["cap"]))

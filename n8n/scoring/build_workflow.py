"""Assemble n8n/workflows/scoring.json from the tested Code-node sources.

Same contract as the enrichment generator: the .js files are read verbatim and
embedded, so the JS that was tested standalone is byte-identical to the JS that
ships inside the workflow.

This generator additionally holds the spec-drift guards for Workflow 3. Three
locked lists live in NovaScout_MasterRef.md and are needed inside an n8n Code
node, which cannot import a shared module:

    Section 12  target geographies      -> 25 points, the largest single weight
    Section  9  fit-score weights       -> the formula itself
    Section  9  therapeutic-area enum   -> how 'Oncology' is recognised

Each is parsed out of the doc here and asserted against code_evaluate.js. A
divergence fails the build loudly instead of silently shipping a formula that
no longer matches the spec. When one fires, the fix is to update the JS to match
the doc -- never the other way round.
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "workflows", "scoring.json")
OUT = os.environ.get("SCORING_OUT", OUT)

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
# Spec parsers
# ---------------------------------------------------------------------------

def load_target_geographies(doc=None):
    """Parse the ICP geography list out of Section 12.

    Anchored on the '**Geographies:**' label rather than on a line number, so
    the section can move. A formatting change that breaks this parse fails the
    build, which beats silently scoring 25 points against a stale list.
    """
    doc = doc if doc is not None else _doc()
    m = re.search(r"\*\*Geographies:\*\*\s*([^\n]+)", doc)
    if not m:
        raise AssertionError(
            "geography list not found in %s -- expected a line containing "
            "'**Geographies:** ...'" % MASTER_REF
        )
    raw = m.group(1).strip().rstrip(".")
    geos = [g.strip() for g in raw.split(",") if g.strip()]
    if len(geos) < 2:
        raise AssertionError("geography list in %s looks empty: %r" % (MASTER_REF, geos))
    if len(set(geos)) != len(geos):
        raise AssertionError("geography list in %s contains duplicates: %r" % (MASTER_REF, geos))
    return geos


# Maps a weights-table row label to the factor key used in the JS. Matching on a
# distinctive substring rather than the full label so a wording tweak in the doc
# does not fail the build, while a row that disappears entirely still does.
WEIGHT_ROW_KEYS = [
    ("geography", r"geograph"),
    ("trials", r"clinicaltrials\.gov"),
    ("founder", r"founder"),
    ("oncology", r"oncology"),
    ("employees", r"employee count"),
    ("site", r"site quality"),
]


def load_weights(doc=None):
    """Parse the weighted fit-score table out of Section 9, Workflow 3."""
    doc = doc if doc is not None else _doc()
    anchor = re.search(r"\*\*Weighted fit score[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError(
            "fit-score table not found in %s -- expected a line containing "
            "'**Weighted fit score ...**'" % MASTER_REF
        )
    tail = doc[anchor.end():]

    # Bound the parse to the ONE contiguous table immediately after the heading.
    # Scanning the whole remainder of the doc would pull in unrelated tables
    # (Section 13's open-items table, for one), and stopping at the first N
    # matched rows would let a factor appended to the end of this table slip
    # through unread -- which is exactly the drift this guard exists to catch.
    block = re.search(r"\n((?:\|[^\n]*\n)+)", tail)
    if not block:
        raise AssertionError("no weight table follows the heading in %s" % MASTER_REF)
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*$", block.group(1), re.M)
    if not rows:
        raise AssertionError("no weight table rows follow the heading in %s" % MASTER_REF)

    weights = {}
    for label, value in rows:
        low = label.lower()
        if low in ("factor", "---"):
            continue
        for key, pattern in WEIGHT_ROW_KEYS:
            if re.search(pattern, low):
                if key in weights:
                    raise AssertionError(
                        "weight table in %s matched %r twice (row %r)" % (MASTER_REF, key, label)
                    )
                weights[key] = int(value)
                break
        else:
            # An unrecognised row means the spec grew a factor the code does not
            # implement. That must fail, not be skipped.
            raise AssertionError(
                "weight table row %r in %s matches no known factor -- the spec has a "
                "factor code_evaluate.js does not implement" % (label, MASTER_REF)
            )

    missing = [k for k, _ in WEIGHT_ROW_KEYS if k not in weights]
    if missing:
        raise AssertionError("weight table in %s is missing rows for %r" % (MASTER_REF, missing))
    total = sum(weights.values())
    if total != 100:
        raise AssertionError(
            "fit-score weights in %s sum to %d, not 100: %r" % (MASTER_REF, total, weights)
        )
    return weights


def load_therapeutic_areas(doc=None):
    """Parse the locked taxonomy enum out of Section 9's taxonomy section."""
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


TARGET_GEOGRAPHIES = load_target_geographies()
WEIGHTS = load_weights()
THERAPEUTIC_AREAS = load_therapeutic_areas()


# ---------------------------------------------------------------------------
# Drift guards against code_evaluate.js
# ---------------------------------------------------------------------------

def _js_string_array(src, name):
    m = re.search(r"const %s = \[(.*?)\];" % re.escape(name), src, re.S)
    assert m, "%s array not found in code_evaluate.js" % name
    return re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))


def _assert_evaluate_matches_spec():
    src = js("code_evaluate.js")

    found_geo = _js_string_array(src, "TARGET_GEOGRAPHIES")
    assert found_geo == TARGET_GEOGRAPHIES, (
        "target geographies drifted between the Master Ref and code_evaluate.js:\n"
        "  doc (%s): %r\n  js:  %r"
        % (os.path.basename(MASTER_REF), TARGET_GEOGRAPHIES, found_geo)
    )

    found_areas = _js_string_array(src, "THERAPEUTIC_AREAS")
    assert found_areas == THERAPEUTIC_AREAS, (
        "therapeutic-area taxonomy drifted between the Master Ref and code_evaluate.js:\n"
        "  doc (%s): %r\n  js:  %r"
        % (os.path.basename(MASTER_REF), THERAPEUTIC_AREAS, found_areas)
    )

    m = re.search(r"const WEIGHTS = \{(.*?)\};", src, re.S)
    assert m, "WEIGHTS object not found in code_evaluate.js"
    found_weights = {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", m.group(1))}
    assert found_weights == WEIGHTS, (
        "fit-score weights drifted between the Master Ref and code_evaluate.js:\n"
        "  doc (%s): %r\n  js:  %r" % (os.path.basename(MASTER_REF), WEIGHTS, found_weights)
    )

    # The trials weight lives in code_score.js, because that is the node the
    # ClinicalTrials.gov result arrives at. Guard it there too, or the one
    # factor the model never sees could drift unnoticed.
    m = re.search(r"const WEIGHT_TRIALS = (\d+);", js("code_score.js"))
    assert m, "WEIGHT_TRIALS not found in code_score.js"
    assert int(m.group(1)) == WEIGHTS["trials"], (
        "trials weight drifted: doc says %d, code_score.js says %s"
        % (WEIGHTS["trials"], m.group(1))
    )

    # Section 12 locks the employee band and Section 9 the enterprise cutoff.
    m = re.search(r"const EMPLOYEE_BAND = \{ min: (\d+), max: (\d+) \};", src)
    assert m, "EMPLOYEE_BAND not found in code_evaluate.js"
    band = (int(m.group(1)), int(m.group(2)))
    doc = _doc()
    assert re.search(r"\*\*Company:\*\*\s*%d[-–]%d employees" % band, doc), (
        "employee band %r in code_evaluate.js does not match Section 12's "
        "'**Company:** N-M employees'" % (band,)
    )
    m = re.search(r"const ENTERPRISE_OVER = (\d+);", src)
    assert m, "ENTERPRISE_OVER not found in code_evaluate.js"
    assert re.search(r"Employee estimate > %s \(enterprise CRO\)" % m.group(1), doc), (
        "enterprise cutoff %s in code_evaluate.js does not match Section 9's "
        "'Employee estimate > N (enterprise CRO)'" % m.group(1)
    )


_assert_evaluate_matches_spec()


# ---------------------------------------------------------------------------
# Ollama rationale call
# ---------------------------------------------------------------------------

RATIONALE_SCHEMA = {
    "type": "object",
    "properties": {"rationale": {"type": "string"}},
    "required": ["rationale"],
}

# Pretty-printed on purpose: a compact json.dumps emits runs like `"string"}}`,
# and that `}}` closes the surrounding n8n {{ }} expression early -- the node
# then fails with a bare "invalid syntax". Indenting puts every closing brace on
# its own line so `}}` never occurs.
_schema_js = json.dumps(RATIONALE_SCHEMA, indent=2).replace("\n", "\n  ")

OLLAMA_BODY = (
    "={{ JSON.stringify({\n"
    "  model: 'qwen3.5:9b',\n"
    "  system: $json.system_prompt,\n"
    "  prompt: $json.prompt,\n"
    "  stream: false,\n"
    "  think: false,\n"
    "  format: " + _schema_js + ",\n"
    "  options: { temperature: 0.2, presence_penalty: 0, num_ctx: 16384, num_predict: 400 }\n"
    "}) }}"
)

assert OLLAMA_BODY.count("}}") == 1 and OLLAMA_BODY.endswith("}}"), \
    "schema JSON reintroduced a `}}` that would truncate the n8n expression"
assert "{{" not in OLLAMA_BODY[2:], "unexpected `{{` inside the expression body"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

BATCH_SQL = """-- One query, one row per lead. enrichments.lead_id is UNIQUE (migration 002),
-- so this join cannot fan out; blocklist membership is resolved here rather
-- than by a second node, because Postgres already has the index for it.
SELECT l.id AS lead_id,
       l.domain,
       l.company_name,
       l.country,
       e.therapeutic_areas,
       e.phases,
       e.founder_name,
       e.founder_linkedin,
       e.employee_estimate,
       e.has_chatbot,
       e.chatbot_vendor,
       e.site_quality_notes,
       e.raw_extraction,
       (b.domain IS NOT NULL) AS blocklisted,
       b.reason              AS blocklist_reason
  FROM leads l
  JOIN enrichments e ON e.lead_id = l.id
  LEFT JOIN blocklist b ON b.domain = l.domain
 WHERE l.status = 'enriched'
 ORDER BY l.id
 LIMIT $1;"""

WRITE_SQL = """-- Write the score and advance the lead in ONE statement, so a crash between
-- the two cannot leave a lead scored-but-still-queued (or advanced with no
-- score row). Re-running the batch is then safe.
--
-- The write is an UPSERT keyed on scores_lead_id_key (migration 003). Re-scoring
-- a lead -- after a reweighted formula, a re-queue, or a ClinicalTrials.gov
-- outage -- updates its row in place instead of adding a second one. That is the
-- lesson migration 002 learned on enrichments, applied here before it could bite.
WITH payload AS (
  SELECT $1::jsonb AS p
), ins AS (
  INSERT INTO scores (lead_id, fit_score, disqualified, disqualify_reason, rationale)
  SELECT
    (p->>'lead_id')::bigint,
    (p->>'fit_score')::int,
    COALESCE((p->>'disqualified')::boolean, false),
    p->>'disqualify_reason',
    p->>'rationale'
  FROM payload
  ON CONFLICT (lead_id) DO UPDATE SET
    fit_score         = EXCLUDED.fit_score,
    disqualified      = EXCLUDED.disqualified,
    disqualify_reason = EXCLUDED.disqualify_reason,
    rationale         = EXCLUDED.rationale,
    -- Refreshed so scored_at always dates the score actually stored.
    scored_at         = now()
  RETURNING lead_id, disqualified
)
-- Section 8's status flow: enriched -> scored | disqualified.
UPDATE leads
   SET status = CASE WHEN (SELECT disqualified FROM ins) THEN 'disqualified' ELSE 'scored' END,
       updated_at = now()
 WHERE id = (SELECT lead_id FROM ins)
   AND status = 'enriched'
RETURNING id AS lead_id, domain, status;"""


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
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-760, 40],
        "notes": "Section 9 Workflow 3: cron, batch where status='enriched'. Queue-driven, so a missed run just means the next one catches up (build rule 4).",
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-760, 220],
    },
    {
        "parameters": {
            "assignments": {
                "assignments": [
                    {"id": "batchsize", "name": "batch_size", "value": 25, "type": "number"}
                ]
            },
            "options": {},
        },
        "name": "Config",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [-540, 130],
        "notes": "Bounded batch (build rule 5). Larger than Workflow 2's 10 because the expensive call here is a fast ClinicalTrials.gov lookup, and only leads that survive the disqualifiers reach the GPU at all.",
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": BATCH_SQL,
            "options": {"queryReplacement": "={{ [$json.batch_size] }}"},
        },
        "name": "Get Enriched Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-320, 130],
        "credentials": PG_CRED,
        "notes": "Oldest-first queue read (Section 7 idempotency rule). Query parameters are passed as an array so a value containing a comma is never split.",
        "alwaysOutputData": False,
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_evaluate.js")},
        "name": "Evaluate Lead",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-100, 130],
        "notes": (
            "Hard disqualifiers and every weighted factor except the trials lookup, all "
            "deterministic (build rule 3). Null is never treated as evidence: the >500-employee "
            "and not-a-CRO rules fire only on a confirmed value, and null employee_estimate / "
            "founder fields take a half-weight neutral rather than a zero.\n\n"
            "The 'no functioning website' disqualifier fires only when NO rung got an HTTP "
            "response. A 403 proves the site exists, so a WAF-blocked lead is scored low and "
            "flagged needs_recheck instead of being killed permanently (Section 9)."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("dq", "={{ $json.disqualified }}", True),
            "options": {},
        },
        "name": "Disqualified?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [120, 130],
        "notes": "Section 9: hard disqualifiers run FIRST. A disqualified lead never reaches the ClinicalTrials.gov API or the GPU.",
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_disqualified.js")},
        "name": "Build Disqualified Payload",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [340, 20],
        "notes": "Writes the disqualify reason as human-readable prose for the review queue (Section 9's 'visibility over spot-checking'), deterministically -- the reason a rule fired is a fact, not something worth a token.",
    },
    {
        "parameters": {
            "method": "GET",
            "url": "https://clinicaltrials.gov/api/v2/studies",
            "sendQuery": True,
            "queryParameters": {
                "parameters": [
                    {"name": "query.spons", "value": "={{ $json.ctgov_sponsor }}"},
                    {"name": "filter.overallStatus", "value": "RECRUITING"},
                    {"name": "countTotal", "value": "true"},
                    {"name": "pageSize", "value": "1"},
                    {"name": "fields", "value": "NCTId"},
                ]
            },
            "options": {
                "timeout": 30000,
                "batching": {"batch": {"batchSize": 1, "batchInterval": 250}},
            },
        },
        "name": "ClinicalTrials.gov Lookup",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [340, 240],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "Section 9: per-candidate lookup on a company name we already have, NOT a bulk "
            "discovery source. No API key. countTotal=true is what makes totalCount appear at "
            "all; pageSize=1 keeps the response small because only the count is scored.\n\n"
            "company_name is sent verbatim. Stripping legal suffixes was tried and measured "
            "against 30 real leads: it changed one result, and that one was a false positive "
            "('A-Pharma s.r.o.' -> 'A-Pharma' matched 20 unrelated trials). See README.\n\n"
            "The query.locn fallback the spec mentions is NOT implemented -- measured, it "
            "returns 254-2470 recruiting trials for every target country, which would award "
            "the full 20 points to every lead. See README."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_score.js")},
        "name": "Compute Fit Score",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [560, 240],
        "notes": "Sums the weighted factors and builds the rationale prompt. The score is final before the model is called, so a bad rationale can never move a lead in the ranking.",
    },
    {
        "parameters": {
            "conditions": boolean_condition("writable", "={{ $json.write }}", True),
            "options": {},
        },
        "name": "Drop Failed Lookups",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [780, 240],
        "notes": (
            "If ClinicalTrials.gov did not answer, the lead is dropped and stays 'enriched' so "
            "the next run retries it. Without this, an API outage would write a whole batch of "
            "scores that are silently 20 points low and look perfectly valid afterwards. Same "
            "self-healing shape as Workflow 2's 'Drop Failed Calls'."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("groundable", "={{ $json.groundable }}", True),
            "options": {},
        },
        "name": "Groundable?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [1000, 240],
        "notes": (
            "Section 9 Workflow 4's grounding guard, applied here: a small model fabricates when "
            "under-informed. A lead whose site never yielded readable text has no facts to write "
            "a rationale from, so it keeps the deterministic one rather than being handed to the "
            "model to invent something plausible."
        ),
    },
    {
        "parameters": {
            "method": "POST",
            "url": "http://host.docker.internal:11434/api/generate",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": OLLAMA_BODY,
            "options": {
                "timeout": 300000,
                "batching": {"batch": {"batchSize": 1, "batchInterval": 0}},
            },
        },
        "name": "Ollama Rationale",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [1220, 160],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "/api/generate, NOT /api/chat -- on /api/chat, think:false silently disables `format` "
            "grammar enforcement and the model answers in prose (measured on Ollama 0.30.10 + "
            "qwen3.5:9b during Workflow 2).\n\n"
            "presence_penalty 0 (the model ships with 1.5, which drops copied values), num_ctx "
            "16384, think:false. temperature 0.2 rather than Workflow 2's 0.1: this is the one "
            "node writing prose for a human, and 0.1 made every paragraph open the same way.\n\n"
            "batchSize 1 keeps calls serial to match OLLAMA_NUM_PARALLEL=1."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_rationale.js")},
        "name": "Attach Rationale",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1440, 160],
        "notes": "Swaps the model's paragraph in and keeps the score breakdown. A failed or too-short generation falls back to the deterministic rationale -- an Ollama outage costs prose quality here, not a scoring run.",
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json.payload)] }}"},
        },
        "name": "Write Score & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [1660, 130],
        "credentials": PG_CRED,
        "notes": "The whole payload goes in as one jsonb parameter, so unicode and embedded commas are handled by Postgres rather than by string splicing. The AND status='enriched' guard makes a concurrent or repeated run a no-op instead of a double write.",
    },
]

connections = {
    "Every 30 Minutes": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Get Enriched Batch", "type": "main", "index": 0}]]},
    "Get Enriched Batch": {"main": [[{"node": "Evaluate Lead", "type": "main", "index": 0}]]},
    "Evaluate Lead": {"main": [[{"node": "Disqualified?", "type": "main", "index": 0}]]},
    "Disqualified?": {
        "main": [
            [{"node": "Build Disqualified Payload", "type": "main", "index": 0}],
            [{"node": "ClinicalTrials.gov Lookup", "type": "main", "index": 0}],
        ]
    },
    "Build Disqualified Payload": {
        "main": [[{"node": "Write Score & Advance", "type": "main", "index": 0}]]
    },
    "ClinicalTrials.gov Lookup": {
        "main": [[{"node": "Compute Fit Score", "type": "main", "index": 0}]]
    },
    "Compute Fit Score": {"main": [[{"node": "Drop Failed Lookups", "type": "main", "index": 0}]]},
    "Drop Failed Lookups": {"main": [[{"node": "Groundable?", "type": "main", "index": 0}]]},
    "Groundable?": {
        "main": [
            [{"node": "Ollama Rationale", "type": "main", "index": 0}],
            [{"node": "Write Score & Advance", "type": "main", "index": 0}],
        ]
    },
    "Ollama Rationale": {"main": [[{"node": "Attach Rationale", "type": "main", "index": 0}]]},
    "Attach Rationale": {"main": [[{"node": "Write Score & Advance", "type": "main", "index": 0}]]},
}

workflow = {
    "id": "scoring0001",
    "name": "Scoring",
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
print("  geographies: %d, weights: %r (sum %d)" % (
    len(TARGET_GEOGRAPHIES), WEIGHTS, sum(WEIGHTS.values())))

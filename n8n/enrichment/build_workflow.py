"""Assemble n8n/workflows/enrichment.json from the tested Code-node sources.

Generating the workflow rather than hand-editing it keeps the JS that was tested
standalone byte-identical to the JS that ships inside the workflow.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "workflows", "enrichment.json")
OUT = os.environ.get("ENRICHMENT_OUT", OUT)


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}

# The extraction schema. Passed to Ollama as `format`, which constrains
# generation with a grammar -- this is what makes the output parseable, not
# prompt discipline. Section 3: "always use Ollama's format: json with an
# explicit schema for extraction and scoring nodes. Never parse free text."
SCHEMA = {
    "type": "object",
    "properties": {
        "is_cro": {"type": "boolean"},
        "company_type": {"type": "string"},
        "therapeutic_areas": {"type": "array", "items": {"type": "string"}},
        "phases": {"type": "array", "items": {"type": "string"}},
        "founder_name": {"type": ["string", "null"]},
        "founder_title": {"type": ["string", "null"]},
        "employee_estimate": {"type": ["integer", "null"]},
        "city": {"type": ["string", "null"]},
        "site_quality_notes": {"type": "string"},
    },
    "required": [
        "is_cro", "company_type", "therapeutic_areas", "phases",
        "founder_name", "founder_title", "employee_estimate", "city",
        "site_quality_notes",
    ],
}

# The schema is pretty-printed on purpose. A compact json.dumps emits runs like
# `{"type": "string"}}`, and the `}}` inside closes the surrounding n8n {{ }}
# expression early -- the node then fails with a bare "invalid syntax".
# Indenting puts every closing brace on its own line so `}}` never occurs.
_schema_js = json.dumps(SCHEMA, indent=2).replace("\n", "\n  ")

OLLAMA_BODY = (
    "={{ JSON.stringify({\n"
    "  model: 'qwen3.5:9b',\n"
    "  system: $json.system_prompt,\n"
    "  prompt: $json.prompt,\n"
    "  stream: false,\n"
    "  think: false,\n"
    "  format: " + _schema_js + ",\n"
    "  options: { temperature: 0.1, presence_penalty: 0, num_ctx: 16384, num_predict: 1024 }\n"
    "}) }}"
)

# Guard the above: the only `}}` allowed is the expression terminator itself.
assert OLLAMA_BODY.count("}}") == 1 and OLLAMA_BODY.endswith("}}"), \
    "schema JSON reintroduced a `}}` that would truncate the n8n expression"
assert "{{" not in OLLAMA_BODY[2:], "unexpected `{{` inside the expression body"

WRITE_SQL = """-- Insert the enrichment and advance the lead in ONE statement, so a crash
-- between the two cannot leave a lead enriched-but-still-queued (or worse,
-- advanced with no enrichment row). Re-running the batch is then safe.
--
-- The write is an UPSERT keyed on the enrichments_lead_id_key unique
-- constraint (migration 002). A lead put back on the queue is re-extracted
-- over its previous row instead of gaining a second one -- re-processing is
-- safe by construction, not by remembering to clean up first.
WITH payload AS (
  SELECT $1::jsonb AS p
), ins AS (
  INSERT INTO enrichments (
    lead_id, therapeutic_areas, phases, founder_name, founder_linkedin,
    employee_estimate, has_chatbot, chatbot_vendor, site_quality_notes, raw_extraction
  )
  SELECT
    (p->>'lead_id')::bigint,
    ARRAY(SELECT jsonb_array_elements_text(p->'therapeutic_areas')),
    ARRAY(SELECT jsonb_array_elements_text(p->'phases')),
    p->>'founder_name',
    p->>'founder_linkedin',
    (p->>'employee_estimate')::int,
    COALESCE((p->>'has_chatbot')::boolean, false),
    p->>'chatbot_vendor',
    p->>'site_quality_notes',
    p->'raw_extraction'
  FROM payload
  ON CONFLICT (lead_id) DO UPDATE SET
    therapeutic_areas  = EXCLUDED.therapeutic_areas,
    phases             = EXCLUDED.phases,
    founder_name       = EXCLUDED.founder_name,
    founder_linkedin   = EXCLUDED.founder_linkedin,
    employee_estimate  = EXCLUDED.employee_estimate,
    has_chatbot        = EXCLUDED.has_chatbot,
    chatbot_vendor     = EXCLUDED.chatbot_vendor,
    site_quality_notes = EXCLUDED.site_quality_notes,
    raw_extraction     = EXCLUDED.raw_extraction,
    -- Refreshed so enriched_at always dates the extraction actually stored.
    enriched_at        = now()
  RETURNING lead_id
)
UPDATE leads
   SET status = 'enriched', updated_at = now()
 WHERE id = (SELECT lead_id FROM ins)
   AND status = 'ingested'
RETURNING id AS lead_id, domain, status;"""

nodes = [
    {
        "parameters": {
            "rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}
        },
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-620, 40],
        "notes": "Section 9 Workflow 2: cron every 30 min. Queue-driven, so a missed run just means the next one catches up (build rule 4).",
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-620, 220],
    },
    {
        "parameters": {
            "assignments": {
                "assignments": [
                    {
                        "id": "batchsize",
                        "name": "batch_size",
                        "value": 10,
                        "type": "number",
                    }
                ]
            },
            "options": {},
        },
        "name": "Config",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [-400, 130],
        "notes": "Batch of 10 per Section 9. Bounded batch (build rule 5) -- the GPU is serial, and OLLAMA_NUM_PARALLEL=1. Lower this to test against a few leads first.",
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": "SELECT id, domain, company_name, country\n  FROM leads\n WHERE status = 'ingested'\n ORDER BY id\n LIMIT $1;",
            "options": {"queryReplacement": "={{ [$json.batch_size] }}"},
        },
        "name": "Get Ingested Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-180, 130],
        "credentials": PG_CRED,
        "notes": "Oldest-first queue read, exactly as the idempotency rule in Section 7 requires. Query parameters are passed as an array so a value containing a comma is never split.",
        "alwaysOutputData": False,
    },
    {
        "parameters": {"jsCode": js("code_fetch.js")},
        "name": "Fetch Site Pages",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [40, 130],
        "notes": "Fetches the homepage first and only then subpages, so a dead domain costs 1 request instead of 4 (measured: a dead host burned 4x the timeout before this change). Chatbot detection and LinkedIn harvesting are regex here, never an LLM call (build rule 3).",
    },
    {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "strict",
                    "version": 2,
                },
                "conditions": [
                    {
                        "id": "fetchok",
                        "leftValue": "={{ $json.fetch_ok }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    },
                    {
                        "id": "hastext",
                        "leftValue": "={{ $json.text_too_thin }}",
                        "rightValue": False,
                        "operator": {"type": "boolean", "operation": "false", "singleValue": True},
                    },
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "name": "Has Usable Content?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [260, 130],
        "notes": "Never send an unreachable or JS-shell site to the model. Section 9 Workflow 4 records why: small models fabricate when under-informed, so an empty page must produce an explicit failure row, not an invented one.",
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
        "name": "Ollama Extract",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [520, 20],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "Uses /api/generate, NOT /api/chat. Measured on Ollama 0.30.10 + qwen3.5:9b: "
            "on /api/chat, `think:false` silently disables `format` grammar enforcement and the "
            "model answers in prose; keeping thinking on to get valid JSON costs 30-50s/lead. "
            "/api/generate honours `think:false` AND the schema together -- same output in ~5s.\n\n"
            "Section 3 gotchas, all three applied here: presence_penalty 0 and temperature 0.1 "
            "(the model ships with presence_penalty 1.5, which silently drops copied values such as "
            "LinkedIn URLs); num_ctx 16384 (never the 262K native window); think:false, verified "
            "against this build rather than assumed.\n\n"
            "batchSize 1 keeps calls serial to match OLLAMA_NUM_PARALLEL=1."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_normalise.js")},
        "name": "Normalise Extraction",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [740, 20],
        "notes": "Coerces types, canonicalises phase labels, and resolves founder_linkedin by matching the founder name against the regex-harvested candidate URLs -- deterministic, so it does not vary between runs the way the model's own URL copying did.",
    },
    {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "strict",
                    "version": 2,
                },
                "conditions": [
                    {
                        "id": "writable",
                        "leftValue": "={{ $json.write }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "name": "Drop Failed Calls",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [870, 20],
        "notes": (
            "If Ollama itself did not answer (down, timeout, 5xx) the lead is dropped here and "
            "stays 'ingested', so the next cron run simply retries it. Without this, an Ollama "
            "outage would advance the entire queue to 'enriched' carrying failure markers and "
            "need a manual SQL reset -- which is exactly what happened during this build when a "
            "malformed expression made every call fail. A response that arrived but would not "
            "parse is NOT dropped: it advances with a marker so one bad lead cannot block the queue."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_nocontent.js")},
        "name": "Record No Content",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [520, 260],
        "notes": "Keeps unreachable / empty sites moving out of the queue with an explicit marker for Workflow 3, instead of leaving them to be retried every 30 minutes forever.",
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json.payload)] }}"},
        },
        "name": "Write Enrichment & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [1120, 130],
        "credentials": PG_CRED,
        "notes": "The whole payload goes in as one jsonb parameter, so text[] columns, unicode and embedded commas are all handled by Postgres rather than by string splicing. The AND status='ingested' guard makes a concurrent or repeated run a no-op instead of a double insert.",
    },
]

connections = {
    "Every 30 Minutes": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Get Ingested Batch", "type": "main", "index": 0}]]},
    "Get Ingested Batch": {"main": [[{"node": "Fetch Site Pages", "type": "main", "index": 0}]]},
    "Fetch Site Pages": {"main": [[{"node": "Has Usable Content?", "type": "main", "index": 0}]]},
    "Has Usable Content?": {
        "main": [
            [{"node": "Ollama Extract", "type": "main", "index": 0}],
            [{"node": "Record No Content", "type": "main", "index": 0}],
        ]
    },
    "Ollama Extract": {"main": [[{"node": "Normalise Extraction", "type": "main", "index": 0}]]},
    "Normalise Extraction": {"main": [[{"node": "Drop Failed Calls", "type": "main", "index": 0}]]},
    "Drop Failed Calls": {"main": [[{"node": "Write Enrichment & Advance", "type": "main", "index": 0}]]},
    "Record No Content": {"main": [[{"node": "Write Enrichment & Advance", "type": "main", "index": 0}]]},
}

workflow = {
    "id": "enrichment01",
    "name": "Enrichment",
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

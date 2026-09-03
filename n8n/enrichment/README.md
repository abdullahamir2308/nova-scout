# Workflow 2 — Enrichment

Sources for the three Code nodes in `../workflows/enrichment.json`, the tests
that cover them, and the generator that assembles the workflow JSON.

## Why the workflow is generated, not hand-edited

`enrichment.json` embeds each Code node's JavaScript as a single escaped JSON
string. Editing that by hand means editing escaped source, and it means the JS
that was tested standalone is not provably the JS that ships. `build_workflow.py`
reads the `.js` files verbatim and emits the workflow, so the two cannot drift.

The same applies to the tests: each one slices the function under test out of the
`.js` source rather than holding its own copy.

Regenerate after any change to a `.js` file:

```powershell
python n8n/enrichment/build_workflow.py     # writes ../workflows/enrichment.json
```

Then re-import into n8n. The repo is not mounted into the container, so the file
has to be copied in first:

```powershell
docker cp n8n\workflows\enrichment.json nova-scout-n8n-1:/tmp/enrichment.json
docker exec nova-scout-n8n-1 n8n import:workflow --input=/tmp/enrichment.json
```

The CLI writes straight to n8n's own database. A running n8n does not notice —
reopen the workflow in the UI (or restart the container) before relying on the
new version. `import:workflow` also **deactivates** the workflow, and activation
is a UI action — re-activate it in the editor after importing.

## The therapeutic-area taxonomy comes from the spec

`NovaScout_MasterRef.md` (Section 9, "Therapeutic area taxonomy") is the single
source of truth. `build_workflow.py` **parses the fenced list out of that
document** at build time and ships it as a JSON-schema `enum` — that enum is the
real constraint, since Ollama's grammar cannot generate a value outside it.

So expanding the taxonomy is a one-place edit: add the category to the Master
Ref. There is no list to update in `build_workflow.py`.

Two things still need a manual follow-up when the doc changes:

1. `code_normalise.js` holds its own copy of the list, because an n8n Code node
   cannot import a shared module and the fenced-block JSON rescue path bypasses
   the grammar. `build_workflow.py` asserts it matches the doc and refuses to
   generate the workflow if it has drifted — when that fires, update the JS to
   match the doc, never the other way round.
2. The `therapeutic_areas` rules in `code_fetch.js`'s system prompt name the
   categories and the mapping hints. The grammar stops off-enum output on its
   own, but the prompt is what makes the model pick the *right* value, so a new
   category with no prompt guidance will be under-used.

The parse is anchored on the section heading, not on "the first fenced block".
Renaming that heading or removing the fence fails the build loudly rather than
silently shipping a stale enum.

## Files

| File | Role |
|---|---|
| `code_fetch.js` | **Fetch Site Pages** — homepage + up to 4 discovered subpages, regex chatbot detection, LinkedIn harvesting, prompt assembly |
| `code_normalise.js` | **Normalise Extraction** — parses the Ollama response, coerces types, resolves `founder_linkedin` deterministically |
| `code_nocontent.js` | **Record No Content** — writes an explicit failure marker for unreachable / JS-shell sites so they leave the queue |
| `build_workflow.py` | Generator — assembles all of the above into `../workflows/enrichment.json` |
| `harness.js` | Runs a Code-node body standalone against a mocked n8n context |
| `fixtures/test_leads.json` | 10 real leads used by the harness smoke run |

## Tests

Offline unit tests, no network and no Ollama. All three exit non-zero on failure:

```powershell
node n8n/enrichment/test_url_resolver.js       # 14 cases
node n8n/enrichment/test_founder_match.js      #  9 cases
node n8n/enrichment/test_chatbot_detect.js     # 13 cases
node n8n/enrichment/test_therapeutic_areas.js  # 19 cases
```

- **`test_url_resolver.js`** checks the hand-rolled URL resolver against Node's
  real WHATWG `URL`. The resolver exists because the n8n Code sandbox does not
  expose `URL` — using `new URL()` there fails inside a `try/catch` and turns
  subpage discovery into a silent no-op.
- **`test_founder_match.js`** checks that the founder→LinkedIn matcher refuses
  to guess: an opaque slug, an ambiguous tie, or a one-part name match all
  return `null` rather than a plausible wrong person.
- **`test_chatbot_detect.js`** checks each vendor's real embed snippet is
  detected and that ordinary analytics/marketing markup is not. False positives
  matter: this feeds Workflow 3's "already has a chatbot" hard disqualifier.
- **`test_therapeutic_areas.js`** checks the locked-taxonomy canonicaliser:
  casing is normalised, off-enum values (`cardiology`, `medtech`, plurals) are
  dropped and recorded rather than guessed into a neighbour, and the list in
  `code_normalise.js` still matches the Master Ref — which it reads from the doc
  rather than pinning a third copy of.

A live smoke run against the fixture leads (network, no Ollama) — writes
`fetch_out.json` next to the script, which is gitignored:

```powershell
node n8n/enrichment/harness.js
```

The harness deliberately shadows `URL`, `URLSearchParams`, `fetch` and
`AbortController` as `undefined`, because the n8n Code sandbox does not provide
them. Code that relies on them must fail here too rather than pass locally and
no-op in production.

## Draining the queue by hand

The workflow is queue-driven, so running it repeatedly is safe and idempotent.
Each execution processes one bounded batch (`Config.batch_size`, default 10).

Putting an already-enriched lead back on the queue (`status='ingested'`) is also
safe on its own: the write step upserts on the `enrichments_lead_id_key` unique
constraint (migration `002`), so a re-processed lead is re-extracted over its
existing row instead of gaining a second one. No manual cleanup first.

```powershell
docker exec -e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false `
  nova-scout-n8n-1 n8n execute --id enrichment01
```

Repeat until `SELECT count(*) FROM leads WHERE status='ingested'` reaches zero.
The two `N8N_RUNNERS_*` overrides keep the one-off CLI process from colliding
with the task-runner broker the long-running container already has bound.

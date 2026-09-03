# Workflow 3 — Scoring

Sources for the four Code nodes in `../workflows/scoring.json`, the tests that
cover them, and the generator that assembles the workflow JSON.

Same contract as `../enrichment/`: the workflow is **generated, not hand-edited**,
so the JS that was tested standalone is byte-identical to the JS that ships
inside the escaped JSON string. Regenerate after any change to a `.js` file:

```powershell
python n8n/scoring/build_workflow.py     # writes ../workflows/scoring.json
```

Then re-import. The repo is not mounted into the container, so copy it in first:

```powershell
docker cp n8n\workflows\scoring.json nova-scout-n8n-1:/tmp/scoring.json
docker exec nova-scout-n8n-1 n8n import:workflow --input=/tmp/scoring.json
```

`import:workflow` **deactivates** the workflow, and activation is a UI action —
re-activate it in the editor after importing.

## The pipeline

```
Cron / Manual → Config → Get Enriched Batch → Evaluate Lead → Disqualified?
                                                                  │
                        ┌─────────────────────────────────────────┴──── yes
                        │  Build Disqualified Payload ─────────────────────┐
                        no                                                 │
                        ↓                                                  │
   ClinicalTrials.gov Lookup → Compute Fit Score → Drop Failed Lookups     │
                                                          ↓                │
                                                    Groundable? ── no ─────┤
                                                          │ yes            │
                                       Ollama Rationale → Attach Rationale ┤
                                                                           ↓
                                                          Write Score & Advance
```

Three branch points, each with a reason:

- **Disqualified?** — Section 9 puts the hard disqualifiers first, so a
  disqualified lead costs neither a ClinicalTrials.gov call nor a GPU second.
- **Drop Failed Lookups** — if ClinicalTrials.gov did not answer, the lead is
  dropped and stays `enriched` for the next run. Scoring it anyway would write
  a batch of scores silently 20 points low that look perfectly valid afterwards.
  Same self-healing shape as Workflow 2's `Drop Failed Calls`.
- **Groundable?** — a lead whose site never yielded readable text has no facts
  to write a rationale from. It keeps the deterministic one rather than being
  handed to a small model to invent something plausible (Section 9 Workflow 4's
  grounding guard).

An Ollama outage is deliberately *not* a drop: the score is final before the
model is ever called, so a failed rationale call costs prose quality, not a
scoring run.

## What the spec locks, and how drift is caught

Three locked lists live in `NovaScout_MasterRef.md` and are needed inside an n8n
Code node, which cannot import a shared module. `build_workflow.py` parses each
one out of the doc and asserts `code_evaluate.js` matches, refusing to generate
the workflow on any divergence:

| Locked in | What | Guarded against |
|---|---|---|
| Section 12 | 13 target geographies | 25 points, the largest single weight |
| Section 9 | the six fit-score weights | the formula itself; also asserted to sum to 100 |
| Section 9 | the therapeutic-area enum | how `Oncology` is recognised |
| Section 12 | employee band 5–100 | the `employees` factor |
| Section 9 | enterprise cutoff > 500 | the `enterprise_scale` disqualifier |

When a guard fires, the fix is to update the JS to match the doc — never the
other way round. `test_drift_guards.py` proves each guard actually fires by
mutating a scratch copy of the doc (or the JS) and asserting the build refuses;
a guard that has never been seen to fail is not a guard.

## Scoring rules worth knowing

**Null is never evidence.** `employee_estimate` is null on 85% of leads because
extraction correctly refuses to guess. The `> 500` disqualifier fires only on a
confirmed number and `not a CRO` only on an explicit `false`.

**Three levels, not two, for the two sparse factors.** Section 9: "a confirmed
miss should score lower than an honest unknown." So:

| | founder (20) | employees (10) |
|---|---|---|
| confirmed hit | 20 | 10 |
| honest unknown | 10 | 5 |
| confirmed miss | 6 | 0 |

For `founder`, "confirmed miss" means a founder was named *and* LinkedIn URLs
were harvested from the site *and* none matched them. A named founder on a site
carrying no LinkedIn URLs at all is an unknown, not a miss — the matcher never
had anything to match against.

**Therapeutic areas are matched case-insensitively**, against the locked enum,
with no alias table (the same rule `code_normalise.js` applies). The live store
holds pre-enum rows in lowercase free text; a case-sensitive check would miss
every one of them, which is currently *all* the oncology leads.

**Site quality is measured, not judged.** It scores off what the fetch recorded
— content volume, whether the standard about/services/team subpages exist, and
whether a working certificate served it — not off the model's prose notes. Those
notes go into the rationale prompt as colour but do not move the number.

**`fit_score = 0` means disqualified, never "scored zero".** No lead can reach 0
on merit: the two null-neutral factors alone floor a scored lead at 15. That
keeps the review queue sortable on one column with no NULL special case.

**The score breakdown is appended to `rationale`**, after a blank line, rather
than given a column. Section 8 locks the `scores` columns, and a breakdown is
review copy rather than new state.

## The ClinicalTrials.gov lookup

`GET /api/v2/studies` with `query.spons=<company_name>`,
`filter.overallStatus=RECRUITING`, `countTotal=true`, `pageSize=1`. No API key.
`countTotal=true` is what makes `totalCount` appear at all; only the count is
scored, so the page stays at 1.

Two measured decisions, both recorded here so they are not silently re-litigated:

- **`company_name` is sent verbatim.** Stripping legal suffixes and trailing
  parentheticals ("A-Pharma s.r.o." → "A-Pharma") was implemented and measured
  against 30 real leads. It changed exactly one result, and that one was a false
  positive: the shortened name matched 20 unrelated trials. Cleaning was dropped.
- **The `query.locn` fallback the spec mentions is NOT implemented.** Measured
  against the live API, a bare country search returns 604 recruiting trials for
  India, 2,470 for Turkey, 567 for Argentina and 254 for South Africa. Using it
  as a fallback would award the full 20 points to every lead in the corpus and
  leave the factor carrying no signal at all. **Open question for the spec** —
  see the note in Section 9. A `query.term` fallback would be the useful version
  (CROs are usually a trial's collaborator, not its registered sponsor, which is
  why the sponsor hit rate is only ~8%), but that is a spec change, not an
  implementation detail.

## Files

| File | Role |
|---|---|
| `code_evaluate.js` | **Evaluate Lead** — hard disqualifiers and every weighted factor except trials |
| `code_disqualified.js` | **Build Disqualified Payload** — human-readable reason text for the review queue |
| `code_score.js` | **Compute Fit Score** — folds in the trials lookup, sums the score, builds the rationale prompt |
| `code_rationale.js` | **Attach Rationale** — swaps the model's paragraph in, keeps the breakdown |
| `build_workflow.py` | Generator, and the home of every spec-drift guard |
| `harness.js` | Runs a Code-node body standalone against a mocked n8n context |

## Tests

Offline, no network and no Ollama. All exit non-zero on failure:

```powershell
node   n8n/scoring/test_disqualifiers.js   # 31 cases
node   n8n/scoring/test_fit_score.js       # 48 cases
node   n8n/scoring/test_score_node.js      # 33 cases
python n8n/scoring/test_drift_guards.py    # 14 cases
```

- **`test_disqualifiers.js`** covers the two places Section 9 warns the naive
  implementation gets it wrong: null read as evidence, and "unreachable" treated
  as permanent. The WAF cases use real `fetch.errors` arrays from the live corpus.
- **`test_fit_score.js`** asserts the null-handling rule as an *ordering*
  constraint (miss < unknown < confirmed) rather than as magic numbers, so a
  reweighting that breaks the principle fails even if the arithmetic still works.
- **`test_score_node.js`** runs the real node bodies and covers the deliberately
  different failure handling of the two network calls.
- **`test_drift_guards.py`** mutates a scratch copy of the Master Ref and proves
  each build-time guard refuses.

## Draining the queue by hand

Queue-driven, so running it repeatedly is safe and idempotent. Each execution
processes one bounded batch (`Config.batch_size`, default 25).

```powershell
docker exec -e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false `
  nova-scout-n8n-1 n8n execute --id scoring0001
```

Repeat until `SELECT count(*) FROM leads WHERE status='enriched'` reaches zero.
The two `N8N_RUNNERS_*` overrides keep the one-off CLI process from colliding
with the task-runner broker the long-running container already has bound.

Putting an already-scored lead back on the queue (`status='enriched'`) is safe:
the write step upserts on the `scores_lead_id_key` unique constraint (migration
`003`), so a re-scored lead updates its row in place instead of gaining a second
one. That is migration `002`'s lesson on `enrichments`, applied here before it
could bite — worth having, because re-scoring is expected every time the formula
is reweighted.

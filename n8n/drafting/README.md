# Workflow 4 — Drafting

Generates the email and LinkedIn variants for every lead at `status='contact_found'`,
or records why it refused to. Workflow id `drafting0001`.

Same generated-not-hand-edited pattern as `../enrichment`, `../scoring` and
`../contacts`: the `.js` files are the tested sources, `build_workflow.py` embeds
them verbatim into `../workflows/drafting.json`, and the spec-drift guards parse
`NovaScout_MasterRef.md` and refuse to build when the code and the doc disagree.

```
python build_workflow.py        # regenerate the workflow JSON
node   test_grounding.js        # 75 cases -- the grounding guard
node   test_assemble.js         # 46 cases -- constraint enforcement
python test_drift_guards.py     # 27 cases -- each guard is made to fire
```

## The grounding guard

Section 9's rule is locked: at least two specific facts from
`(therapeutic area, named trial, city, founder name)`, or the draft is flagged
`low-context` and skipped rather than invented.

Two categories are deliberately **not** counted, and both are tempting:

| Excluded | Why |
|---|---|
| `country` | Every lead has one and ingestion only touches the 13 target geographies. Section 9's own reweight found geography measures "did our scraper touch this lead", not fit. A fact every lead shares grounds nothing. |
| `site_quality_notes` | Model-generated prose about a website. It reads like a fact and is the highest fabrication-risk field in the record. Counting it would let one model's invention license another's. |

A therapeutic area only counts if it canonicalises against the locked Section 9
enum. The live store is mixed — pre-enum rows are lowercase free text, and some
of that text is not a therapeutic area at all (`pharma`, `medtech`, `fmcg`,
`consumer health products`). Letting one through would put "your work in FMCG" in
front of a CRO founder, grounded in nothing but an extraction artefact.

## The ClinicalTrials.gov lookup is a deliberate addition

It is not in the Section 9 Workflow 4 spec. `named trial` is one of the four
grounding categories and **nothing in the pipeline stores one**: Workflow 3 asked
for `NCTId` with `pageSize=1` and kept only the count, in prose, inside
`scores.rationale`. Without this call that category is permanently dead and the
guard runs on three of its four categories.

Same call shape as Workflow 3, so the same evidence applies: `query.spons`
verbatim is the only ClinicalTrials.gov query this project trusts — `query.locn`
and `query.term` were both measured against real leads and rejected on false
positives (see the Master Ref). A `query.spons` hit really is this company's
trial, so its `briefTitle` is safe to hand a drafting model. Free, no API key.

On this lead set it changes one verdict: Innovate Research has a city and two
recruiting trials, which is two real facts, and is drafted instead of skipped.

## Joining, not fabrication, is the failure mode

Measured across three real leads at temperature 0.7, 0.45 and 0.35. The model
rarely invents from nothing. What it does is **join** — build one sentence from
two facts, where every word traces to the record and the sentence is still untrue:

> "one recruiting **oncology** trial **in Istanbul**"

Neither the trial's therapeutic area nor its location is known. Temperature did
not fix this, and neither did a prompt rule stated once at the top.

What fixed it was changing the shape of the data. Each fact is handed to the
model with its own boundary sentence attached:

```
3. The company is based in Istanbul. This says nothing about where any trial
   runs or where any staff sit.
```

plus a one-sentence-one-fact rule. Three further fixes followed the same
principle — fix the data, not the prompt:

- **At most two therapeutic areas reach the prompt.** "Name at most two" was in
  the instructions and the model listed five anyway. It cannot list a fifth area
  it was never given. Oncology goes first when present (Section 12's strongest
  signal).
- **The trial fact is phrased in the second person** ("with you as the sponsor").
  The model copies this line almost verbatim, so a third-person phrasing came
  back as "this company" and "their own material" in a message addressed to that
  company.
- **`founder_name` and `city` never open a message.** Founder because you cannot
  tell someone their own name — opening from it produced *"Enrique Gaubeca is
  founder or MD on their site"* as the first line of a DM addressed to Enrique
  Gaubeca. City because it is thin, and given nothing worth saying the model
  reliably upgraded "based in Istanbul" into "you run trials in Istanbul".

Which fact opens each channel rotates on `lead_id`: deterministic, so a redraft
after a re-queue is comparable to what it replaced, but not identical across a
batch — every email opening on the same kind of sentence is a merge-tag tell
produced without a merge tag.

## What the model is not allowed to write

The opt-out sentence, the signature and the greeting are appended in
`code_assemble.js`, not generated. Section 5 locks the opt-out **verbatim** and
makes it the basis of the GDPR/KVKK legitimate-interest position; asking a 9B
model to reproduce a compliance string exactly, every time, is a bet with no
upside (build rule 3). `test_drift_guards.py` refuses the build on even a
punctuation change to it.

Constraint violations tag the draft's `variant` rather than discarding it —
`role-inbox+long`, `named+adjective,merge-tag`. The reviewer sees the tag next to
the text; a silently dropped draft teaches nobody anything.

## A role inbox is not a person

Section 9 Workflow 3b, locked. The email greeting is gated on the **address**,
not on whether a name is known. KLIXAR is the live case: Enrique Gaubeca is a
confirmed founder with a confirmed LinkedIn profile, and `hello@klixar.com` is
still not his mailbox. He is greeted by name on LinkedIn and not by name over
email. `Devesh.kumar@innovate-research.com` is the mirror image — a
personal-shaped address whose owner no source names, so it gets no name either.

## Known gaps

- **The signature is incomplete.** Section 5 wants name / one line of title /
  phone; Section 13's mailbox item is still open, so only the name is set and the
  build prints a warning. Nothing was invented to fill the gap. Set
  `NOVASCOUT_SENDER_TITLE` / `NOVASCOUT_SENDER_PHONE` and rebuild.
- **There is no demo URL.** Section 13's 90-second recording does not exist, so
  drafts reference it in words and carry no link. Set `NOVASCOUT_DEMO_URL` and
  rebuild; the prompt rule flips from "no links" to "exactly this one URL".
- **The `no-demo` and subject checks are email-only.** The LinkedIn DM is sent by
  a human from their own account (Section 6), so it is held to Section 5's
  message rules only where they make sense.

## The bug worth remembering

The first live run produced four drafts that mentioned neither Nova nor
NoblePath. The system prompt was assigned on the **Config** node, and a Postgres
node replaces the items wholesale — so `$json.system_prompt` resolved to an empty
string by the time it reached Ollama. n8n does not error on a missing expression
field. The model got no instructions, summarised the fact sheet back, and the
execution reported `"status": "success"`.

Unit tests could not catch it (they test the Code node, not the wiring) and
runtime could not catch it (nothing errored). It is now a **build-time** guard:
every `$json.X` the Ollama body reads must be a field `code_assess.js` actually
emits, with two drift-guard cases proving the guard fires.

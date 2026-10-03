# Workflow 4 — Drafting

Generates the email and LinkedIn variants for every lead at `status='contact_found'`,
or records why it refused to. Workflow id `drafting0001`.

Same generated-not-hand-edited pattern as `../enrichment`, `../scoring` and
`../contacts`: the `.js` files are the tested sources, `build_workflow.py` embeds
them verbatim into `../workflows/drafting.json`, and the spec-drift guards parse
`NovaScout_MasterRef.md` and `NovaScout_DraftingSkill.md` and refuse to build when
the code and the docs disagree.

```
python build_workflow.py        # regenerate the workflow JSON
node   test_grounding.js        # 140 cases -- the grounding guard (v1's 75) + the v3 library, prompt and request
node   test_assemble.js         # 142 cases -- response handling, assembly, every skill v3 section 3 rule
node   test_approval.js         # 55 cases -- auto-approval: the Approval Gate and Apply Claim Check (migration 014)
python test_drift_guards.py     # 66 cases -- each guard is made to fire
python test_rule_mutations.py   # breaks every rule in turn; the unit suites must fail each time
python test_approval_mutations.py   # breaks every auto-approval rule in turn; test_approval.js must fail each time
python audit_drafts_vs_onepager.py   # read-only: the live queue and library vs nova-one-pager.docx
python provision_anthropic_credential.py   # ANTHROPIC_API_KEY in .env -> n8n credential novascoutAnthropic01
python write_drafting_review.py [exec_id]  # read-only: DRAFTING_REVIEW.md from what n8n has deployed
```

## Drafting skill v3 — Claude composes, code enforces

Since 2026-10-02 a first touch is built to `NovaScout_DraftingSkill.md` v3. The
drafting node is **Claude Sonnet 5.5** (`claude-sonnet-5-5`) on the Anthropic API,
through the n8n credential `novascoutAnthropic01`; enrichment and scoring stay on
local `qwen3.5:9b`. The request parameters come from the live Sonnet 5.5 docs
and are recorded in Master Ref Section 3: no sampling parameters (a 400 on this
model), adaptive thinking by default, effort `high`, structured output through
`output_config.format`. **If a call fails, nothing is written and the lead stays
`contact_found`. There is no fallback to the local model.**

v2 had the model write a subject and two hooks and pasted claims-library lines
after them, verbatim. v3 is composition: the model writes the whole email and the
LinkedIn DM from the fact it is told to open on, plus this lead's approved claims
(`claims_library`, migration 012) -- every description, pain angle, benefit and
ask, and only the proof line matched to the lead's country. It may rephrase a
claim, never widen it. Its schema is subject, body and **ask as a separate field**
for each channel, plus the claim codes each message used, so "exactly one ask"
is checkable.

| Part | Where it comes from |
|---|---|
| Subject, body, ask | the model, from the fact sheet and the approved claims |
| Which fact opens each channel, which proof line is offered | Assess Grounding, deterministically |
| Greeting, opt-out, signature | appended here, never generated |
| Link | none in warm-up weeks 1-2; after that only the library's `link` line, appended here |

**Every rule skill section 3 states is enforced in `code_assemble.js` and tagged
on `variant`** -- length (70-110 target, `long` above 125), one ask, the link
policy, the product name at most once and in brackets, "AI" at most once, no
"You're sponsoring", the forbidden claims (guarantees, numbers the library does
not hold, visitor identification, named CRMs and tools, languages, "chatbot"),
the geography-matched proof, the subject rules, and v2's grounding checks.
Since 2026-10-02 (migration 013) also: never that Nova books a call (`claim-books`
-- it sends the booking link, the sponsor books), never SOPs or client documents
as its source (`claim-sop` -- it answers from the website), and **no repeats**:
each description and benefit line names its `capabilities`, Assess Grounding
tells the model which pairs share one (D2 with BEN-247 or BEN-BOOK), and a
message whose claim codes repeat a capability is tagged `claim-repeat`.

**The rules section is shared.** Workflow 6's follow-ups (`../sendtrack/`,
skill section 8) embed everything in `code_assemble.js` above its `// Node body`
marker, verbatim, so they run these same rule functions. Keep that section pure
-- no `$(...)`, no `$input`, no build placeholder -- or the sendtrack build
refuses.
`DRAFTING_REVIEW.md` (repo root, generated from the deployed workflow) lists
every tag and its pattern. `test_rule_mutations.py` breaks each rule in the
source in turn and proves the unit suite fails.

`variant` records each message's own claim codes in slot order, e.g.
`role-inbox/D2.ANG-HOURS.BEN-247.PR-TR.A1+unconfirmed-claim`. When the model
leaves out the ask or proof code -- it did on three of eight messages in the
first run -- code attributes it from the text rather than trusting the model to
copy an identifier.

**Prompt caching is off on purpose.** The HTTP node dispatches every item's
request before awaiting any, so a run's calls are concurrent and never read each
other's cache (measured: four writes, zero reads), and cron runs are 30 minutes
apart, past the 5-minute cache lifetime.

**Terminology, reversed in v3.** "Sponsor" is the CRO's client again, in the
claims, the prompt, the drafts and the one-pager. The prospect is never the one
sponsoring: the trial fact says "registered under your company", and any draft
that says "You're sponsoring" is tagged `prospect-sponsor`.

**A redraft retires what it replaces.** Send a lead back to `contact_found` and
the write statement sets its earlier pending or approved first-touch drafts to
`rejected` / `bad draft` in the same statement as the insert. New drafts are
always `pending`. A lead Workflow 6 has already emailed must be put back to
`sent` after its redraft (2026-10-02 did this for leads 7, 91 and 104); its new
email draft can never send, because Workflow 6 refuses `already-contacted`.

## Auto-approval -- the review queue is the exceptions queue

Since 2026-10-03 (migration 014) an email draft is approved by the workflow
that wrote it when it passes every check below, and held for a person otherwise.
Two Code nodes do it, and Follow-Ups (`../sendtrack/`) ships the same two
verbatim, so a first touch and a follow-up are judged by the same functions:

```
Assemble Drafts / Build Low-Context Drafts -> Approval Gate -> Needs Claim Check? -> Claude Claim Check -> Apply Claim Check -> Write Drafts & Advance
                                                                                \-> (held) ------------------------------------------> Write Drafts & Advance
```

| Rule | Where | Held as |
|---|---|---|
| `settings.auto_approve_email` is on (unset = on) | Approval Gate, from the batch query | `auto-approve-off` |
| an email -- LinkedIn never auto-approves (Section 6) | Approval Gate | `linkedin` |
| not a low-context note | Approval Gate | `low-context` |
| no rule tag: nothing after `+` in `variant` | Approval Gate | `rule-tags: ...` |
| every claim code in `variant` is active and confirmed | Approval Gate | `unconfirmed-claim: ...` / `no-claims` |
| a second Sonnet 5.5 call finds every claim supported by a confirmed line and every prospect fact in the record -- a widened claim fails | Claude Claim Check -> Apply Claim Check | `claim-check: widened "..." (BEN-SEE)` and the like |

`code_approval.js` is the Approval Gate (rules above its `// Node body` marker);
Apply Claim Check is that rules section followed by `code_approval_apply.js`.
The deterministic rules run first, so a draft they hold costs no API call. The
check gets exactly what the drafter had: the lead's fact sheet (the
ClinicalTrials.gov trial included -- it is looked up per run and stored nowhere)
and the confirmed lines; for a follow-up, the enrichment record and the first
email as context.

**The model judges statements; code decides.** The check's schema has no
overall verdict to trust. It returns every statement, sentence by sentence,
with its kind (claim / prospect / none), the claim code or fact it compared it
with, and a verdict. Apply Claim Check approves only if every claim is
`supported` by a code that is really a confirmed line, every prospect fact is
`supported`, and the sentences it quoted account for the whole email -- a
sentence it skipped is a sentence nobody judged. Any failed call (HTTP error,
refusal, cut-off, wrong schema) holds the draft. There is no fallback model: the
check is Sonnet 5.5's or a person's.

**The system prompt's widening example is not draft 99's sentence.** Draft 99's
"..., so you know exactly what came in." is the held-out case the dry run proves
the check catches (`../sendtrack/dryrun/approval_dryrun.py`); a check shown its
own test case proves nothing. `test_approval_mutations.py` fails if it is ever
added.

**The table re-checks.** Migration 014's `drafts_approval_rules` trigger turns
any `approved_by = 'auto'` approval that breaks the flag, the email-only rule,
low-context, the tags, a claim code's confirmation, or lacks a passing
`claim_check`, into a hold (`db-guard: ...`) -- so a bug here can make the
workflow approve less, never more. It also sets `approved_by = 'human'` for
every other approval, clears it when a draft leaves approved/sent, and sends an
auto-approved draft back to a person if its text is rewritten underneath it.
A confirmed claim whose text is edited is un-confirmed by a trigger on
`claims_library`, until someone confirms the new text.

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
makes it the basis of the GDPR/KVKK legitimate-interest position.
`test_drift_guards.py` refuses the build on even a punctuation change to it, in
the JS or in the skill's section 4.

Nothing about the product, the problem or the proof may come from anywhere but
the approved claims the prompt lists; what comes back is checked against them
(numbers, deployments, claim codes).

Constraint violations tag the draft's `variant` rather than discarding it. The
reviewer sees the tag next to the text; a silently dropped draft teaches nobody
anything.

## A role inbox is not a person

Section 9 Workflow 3b, locked. The email greeting is gated on the **address**,
not on whether a name is known. KLIXAR is the live case: Enrique Gaubeca is a
confirmed founder with a confirmed LinkedIn profile, and `hello@klixar.com` is
still not his mailbox. He is greeted by name on LinkedIn and not by name over
email. `Devesh.kumar@innovate-research.com` is the mirror image — a
personal-shaped address whose owner no source names, so it gets no name either.

## Sender identity

The signature (Section 5: name / one line of title / phone) and the sender name
the system prompt gives the model come from `NOVASCOUT_SENDER_NAME`,
`NOVASCOUT_SENDER_TITLE` and `NOVASCOUT_SENDER_PHONE` — the environment if set,
otherwise the repo's `.env`. Both are baked into the Code nodes at build time,
so a change means rebuild and re-import.

There is **no default name**. The build used to fall back to "Fatima", and
because the signature is baked in, a rebuild from any shell without the variable
set silently signed every future draft as the wrong person. A missing name now
refuses the build (`test_drift_guards.py` proves it, and proves the `.env`
fallback lands in the shipped node). A missing title or phone only warns;
nothing is invented to fill the gap.

## Known gaps

- **Confirmation is per text.** The operator confirmed every active line on
  2026-10-03 after reviewing them in `DRAFTING_REVIEW.md`. Editing a confirmed
  line's text un-confirms it (migration 014), and a draft using it is held
  until it is confirmed again -- in a second save, after reading it.
- **A follow-up that names the prospect's trial is held.** Follow-Ups has no
  ClinicalTrials.gov lookup, so the record its check gets says no trial is in
  it; a person approves those.
- **A pattern check is a pattern check, and the claim check is a model.** The
  dry run measures it on real text (draft 99 held twice, a clean draft passed),
  not on every possible widening. Every auto-approval lands in the Daily Digest
  before its recipient's business hours open.
- **Pending LinkedIn draft 90 still carries pre-013 wording** (D2 with
  "books the call"). It is held for a person, like every LinkedIn draft, and is
  sent by hand -- do not send it as it stands.
- **A pattern check is a pattern check.** The claim rules catch the phrasings
  their patterns name (`DRAFTING_REVIEW.md` lists them). A forbidden claim worded
  some other way gets past them, and the human review is the backstop.
- **The subject and link checks are email-only.** The LinkedIn DM is sent by a
  human from their own account (Section 6). Any URL in a DM is tagged.

## The one-pager is not built here

`nova-one-pager.docx` (repo root) is the asset Follow-Up #1 offers
(`../sendtrack/code_followup.js`). **No script in this repo builds it.** Its
`docProps/core.xml` (creator `Un-named`, revision 1, created and modified 9 ms
apart) is programmatic output, consistent with the JS `docx` library, and the
versions created on 2026-09-18 and 2026-09-21 both carried an
`[Abdullah: ... Do not estimate one.]` note paragraph (the first also had
`[DATE]` placeholders). The repo's copy is that output **edited in place**
(`word/document.xml`) to drop the note and, later the same day, to say "pharma
team" instead of "sponsor". `core.xml` was left alone, so it still shows the
generator's timestamp.

If whatever generated it runs again and its output replaces this file, the
source needs the same fixes, or they come back with it. The note already came
back once: the 2026-09-21 regeneration dropped the `[DATE]`s and re-emitted the
note.

1. **No internal note and no placeholders:** no `[Abdullah: ...]` paragraph, no
   `[DATE]`, nothing in square brackets.
2. **`sponsor` for the CRO's client, never `pharma team`, and nothing calling
   the reader a sponsor** (skill v3, 2026-10-02 -- this reverses the 2026-09-21
   rule). The claims and the drafts say "sponsor" for the client again.
3. **No speed claim beyond "in real time".** The one-pager says Nova answers
   "in real time"; nothing supports a seconds figure, and no active claim
   says more (the audit's section B fails on a seconds claim).
4. **The five capability bullets under "What Nova does"** (added 2026-10-02):
   qualification fields, routing of investigators and trainees, the
   capabilities deck, the booking link, the dashboard. Each is worded to what
   the Nova Agent Kit code does, and several active claims cite them as
   evidence (`audit_drafts_vs_onepager.py`, section C).
5. **The website is the only knowledge source** (settled 2026-10-02, migration
   013): "Nova is trained on your own website", no SOPs, protocols or "service
   documentation"; the 48-hour demo step is "You share your site URL" and "We
   configure Nova specifically on your site content and service pages". The
   Nova Agent Kit has no code path for documents a client provides.
6. **No "new dashboard" line.** "Nova doesn't force a new dashboard into that"
   was dropped with BEN-ROUTE's clause: Nova has its own leads dashboard.

After regenerating, run `python audit_drafts_vs_onepager.py` before the file is
served. It fails on a placeholder, on leftover "pharma team" wording, on anything calling
the reader a sponsor, and on any claim an active library line makes that the
one-pager does not. It also reads the live
queue, so run it after a library edit or a redraft too.

## The bug worth remembering

The first live run produced four drafts that mentioned neither Nova nor
NoblePath. The system prompt was assigned on the **Config** node, and a Postgres
node replaces the items wholesale — so `$json.system_prompt` resolved to an empty
string by the time it reached Ollama. n8n does not error on a missing expression
field. The model got no instructions, summarised the fact sheet back, and the
execution reported `"status": "success"`.

Unit tests could not catch it (they test the Code node, not the wiring) and
runtime could not catch it (nothing errored). It is now a **build-time** guard:
every `$json.X` the model node's body reads (today the Claude Draft node's
`$json.request`) must be a field `code_assess.js` actually emits, with two drift-guard cases proving the guard fires.

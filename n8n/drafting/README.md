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
node   test_grounding.js        # 121 cases -- the grounding guard (v1's 75, untouched) + v2
node   test_assemble.js         # 112 cases -- assembly and constraint enforcement
python test_drift_guards.py     # 45 cases -- each guard is made to fire
python audit_drafts_vs_onepager.py   # read-only: the live queue and library vs nova-one-pager.docx
```

## Drafting skill v2 — the model writes three sentences

Since 2026-09-19 a first touch is built to `NovaScout_DraftingSkill.md` v2: hook,
problem, outcome, proof, ask. **Only the hook and the subject are generated.** The
model returns `email_subject`, `email_hook` and `linkedin_hook`, and nothing else.
The schema has no other field, and the build refuses one that disagrees with the
skill's "only the hook and subject are freely generated". The problem, outcome,
proof and exactly one ask are lines from the **claims library**, the
`claims_library` table (migration 010), which the operator owns and edits in
NocoDB or psql. The batch query reads the active rows on every run, so an edit
reaches the next draft with no rebuild. `code_assemble.js` inserts them verbatim.
The model never sees them, so it cannot paraphrase a claim or join a prospect
fact onto one.

| Part | Where it comes from |
|---|---|
| Subject, hook | the model, from the fact sheet, under the guard below |
| Problem, outcome, ask | library lines, rotated on `lead_id`, chosen to fit the 80-word body |
| Proof | the library line whose `countries` names the lead's country; the line with no countries otherwise; an active `measured` line wins |
| Link | none in warm-up weeks 1–2; after that only the library's `link` line |
| Opt-out, signature, greeting | appended here, as in v1 |

`variant` now records the lines used, e.g. `role-inbox/P2.O2.PR-MX.A2+unconfirmed-claim`,
because that is what the learning loop needs to see which lines earn replies.
New tags: `subject-*` (length, product name, Re:/Fwd:, banned word, shouting,
ungrounded), `hook-*` (question, ask, pitch, ungrounded, opener, source, long),
`link-in-warmup` / `unapproved-link` / `linkedin-link` / `pdf-link`, and
`unconfirmed-claim` (the library is seeded unconfirmed, as skill §4 is a DRAFT).

**The worked examples in the system prompt carry other companies' facts**
(INM004, a 40-person team, oncology), and a 9B model copies examples. Any digit
token in a hook or subject that is not in the lead's facts, or any therapeutic
area the lead does not have, is tagged `-ungrounded`. Measured: before
`code_assess.js` started picking the trial's short name (its code, otherwise the
first two content words of the title), the model gave a trial with no code the
subject "INM004 trial — a quick question".

**Two hook failures measured on real leads, and what fixed them.** When the model
was told to vary the LinkedIn hook's wording, it wrote "Your site lists work on
the Efficacy of INM004…" — the trial is on ClinicalTrials.gov, not their site.
That instruction was removed (truth beats variety), and the hook is tagged
`hook-source`. And the trial fact line is phrased from its source, which the
model copies ("ClinicalTrials.gov lists…"). A per-lead instruction naming the
opening words, "You're sponsoring", fixed that.

**A redraft retires what it replaces.** Send a lead back to `contact_found` and
the write statement sets its earlier pending or approved first-touch drafts to
`rejected` / `bad draft` in the same statement as the insert. Left approved, the
old draft would be the first thing Workflow 6 sends, because it sends the oldest
approved draft of a `drafted` lead. New drafts are always `pending`.

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

Since v2 the same goes for every sentence that is not about the prospect: the
problem, outcome, proof and ask are claims-library lines inserted verbatim (see
above).

Constraint violations tag the draft's `variant` rather than discarding it —
`role-inbox/P1.O1.PR-TR.A1+long`, `named/P2.O3.PR-MX.A3+adjective,merge-tag`. The
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

- **`NOVASCOUT_DEMO_URL` is gone.** Skill v2 moves the recording, the PDF and the
  LinkedIn profile to the reply payload (skill §8). The one URL a first touch may
  carry after warm-up is the library's `link` line (seeded inactive, `L-NP`).
- **Ask line A3 offers the 90-second recording,** which Section 13 still lists as
  not made. A "yes" to it needs the recording to exist. Deactivate A3 in the
  library until it does, if that matters.
- **The subject and link checks are email-only.** The LinkedIn DM is sent by a
  human from their own account (Section 6), so it is held to Section 5's message
  rules only where they make sense. Its hook is checked like the email's.
- **A long trial title makes a long hook.** Atlant Clinical's LinkedIn DM opens
  on its real trial title (a 28-word hook) and is tagged `long`, rather than
  having the title shortened into something the registry does not say.

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
2. **`pharma team` (noun) / `pharma-team` (modifier) for the pharma-side party,
   never `sponsor`.** A first-touch hook opens "You're sponsoring ..." (the CRO
   as trial sponsor), and the claims library has said "pharma team" since
   2026-09-21, so a one-pager that says "sponsor" puts one word on two parties
   between the email and the asset it leads to. "Sponsor" is right only for the
   CRO's own registered-sponsor status on ClinicalTrials.gov, which the
   one-pager does not mention.
3. **No speed claim beyond "in real time".** The one-pager says Nova answers
   "in real time"; nothing supports a seconds figure, and `claims_library` O2
   says the same.

After regenerating, run `python audit_drafts_vs_onepager.py` before the file is
served. It fails on a placeholder, on the word "sponsor", and on any claim an
active library line makes that the one-pager does not. It also reads the live
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
every `$json.X` the Ollama body reads must be a field `code_assess.js` actually
emits, with two drift-guard cases proving the guard fires.

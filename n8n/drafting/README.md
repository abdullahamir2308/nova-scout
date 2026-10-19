# Workflow 4 — Drafting

Writes one first-touch email for every CRO lead at `status='contact_found'`,
from the operator's fixed template. Workflow id `drafting0001`.

Same generated-not-hand-edited pattern as `../enrichment`, `../scoring` and
`../contacts`: the `.js` files are the tested sources, `build_workflow.py` embeds
them verbatim into `../workflows/drafting.json`, and the spec-drift guards parse
`NovaScout_MasterRef.md` and refuse to build when the code and the doc disagree.

```
python build_workflow.py              # regenerate the workflow JSON
node   test_template.js               # 181 cases -- the wording, the SMYKM line, every formatting rule, the A/B split, the gate
python test_template_mutations.py     # 57 mutations -- every template rule broken in turn; test_template.js must fail
python test_drift_guards.py           # 41 cases -- each guard is made to fire
python redraft_with_template.py       # the 2026-10-19 switch: retire AI first touches, re-queue their leads (see below)
node   test_assemble.js               # 159 cases -- the Reply Assistant's claim rules (code_assemble.js, see below)
python test_rule_mutations.py         # breaks each of those rules in turn; test_assemble.js must fail
python audit_drafts_vs_onepager.py    # read-only: the live queue and library vs nova-one-pager.docx
python provision_anthropic_credential.py   # ANTHROPIC_API_KEY in .env -> n8n credential novascoutAnthropic01 (Reply Assistant)
```

## Fixed templates since 2026-10-19 — no model call

The operator replaced AI-composed outreach with fixed templates. A first touch is
the approved wording in Master Ref Section 9, Workflow 4, filled by code with
facts the pipeline already holds. Sonnet composition, the claim check, the repair
loop and the claims-library reads are gone from this workflow (and from
Follow-Ups); `NovaScout_DraftingSkill.md` and `DRAFTING_REVIEW.md` are
superseded.

```
Get Draft Batch -> ClinicalTrials.gov Lookup -> Build First Touch -> Drop Failed Lookups -> Write Drafts & Advance
```

`code_template.js` is the whole of it. Everything above its `// Node body`
marker is pure and shared — Follow-Ups (`../sendtrack/code_followup.js`) embeds
it verbatim — so a first touch and a follow-up are written and gated by the same
functions:

| What | Rule |
|---|---|
| Wording | Section 9's blocks, byte for byte. `template_spec.py` renders the JS constants back into the doc's block format and both builds refuse on any difference |
| Arm | lead_id even = A, odd = B (`ARM_BY_PARITY`); the close and the SMYKM sentence follow it |
| SMYKM line | the first fact the lead has: recruiting trials, therapeutic areas, city, else the company line, which needs only the name (`SMYKM_ORDER`; the fourth line added by the operator 2026-10-19) |
| Trials | only the studies whose lead sponsor or a collaborator **is** the company (below); never `query.spons`' raw `totalCount` |
| Areas | the locked taxonomy, case-insensitively, `Other` and free text dropped; at most two, lowercase, joined with "and", taxonomy order (oncology first) |
| Phases | "from Phase I to III" for a full run; "in Phase II" alone and "in Phase I and III" for a gap ("from" only before a range, operator 2026-10-19); Bioequivalence is not a phase; none drops "from {phases}" |
| Grammar | "1 trial" / "3 trials"; "an" before a vowel in "a {area1}"; an apostrophe alone after a name ending in s |
| Company | legal forms stripped from the end (S.A., Ltd, s.r.o., GmbH, Sp. z o.o., Pvt Ltd, S.A. de C.V., Inc., LLC, …), a trailing parenthetical, Romania's leading S.C.; an ALL-CAPS name fixed unless a word is an acronym (three letters or fewer, or at most one vowel) |
| Greeting | "Hi {first_name}," only when the address is that person's (a token overlap with the contact's name); a role inbox, an unattributed address or no name is "Hi there,"; honorifics, credentials after a comma and bare initials skipped |
| Gate | approved by auto unless no deliverable verified address (`no-address`, `unverified-address`), no Send clock (`no-send-clock`) or a blocklisted domain (`blocklisted`) — or `settings.auto_approve_email` off |
| Variant | `template/<arm>/<trials\|areas\|city\|company>/<named\|role-inbox\|unnamed\|unaddressed>`, hold tags after `+` |

**Sites and SMOs are not drafted.** Get Draft Batch selects only
`lead_company_type() = 'CRO'`, so a site or SMO waits at `contact_found`
without taking a place in the batch; Build First Touch writes nothing for a
non-CRO row; migration 020's trigger refuses to auto-approve a template draft for
one. **No LinkedIn DM is drafted**: Write Drafts & Advance inserts and retires
email rows only, so the DMs drafted before 2026-10-19 stay in the queue
untouched.

## The recruiting-trial count is the company's own

`query.spons` is the only ClinicalTrials.gov query this project trusts, but it is
a full-text match on the sponsor and collaborator fields, not an identity check.
Measured on the 30 leads redrafted 2026-10-19: "Therapeutics" matched 1,175
recruiting trials of every "… Therapeutics", "A+ Science" six trials of Chinese
hospitals, "JSS Medical Research" an unrelated JSS college, "CRO Group" a
Tigermed company. Printed in the template, each would have been a false sentence
under the sender's name.

So the lookup asks for each study's lead sponsor and collaborators (up to 1,000
studies, the API's page maximum), and `verifiedTrials()` counts a study only when
one of those names, folded and stripped of legal forms and punctuation, is the
company's — or the company's followed only by corporate filler ("Innovaderm
Research Inc." is Innovaderm, "MCRA (an IQVIA business)" is MCRA). A lookup that
matched more studies than it returned cannot be verified and counts as none. The
query itself keeps the company name verbatim, as Workflow 3's does. A lookup that
fails writes nothing and the lead stays queued.

## The table re-checks

Migration 020's `drafts_approval_rules` turns any `approved_by = 'auto'` approval
into a hold (`db-guard: …`) unless the flag is on, it is an email, its variant is
a template's with no `+` tag, its text was not edited, its lead is a CRO, its
contact has a verified deliverable address and neither domain is blocklisted. A
workflow bug can make approval less likely, never more, and a draft written any
other way — the retired composer's, a hand-written one — can no longer approve
itself.

## A redraft retires what it replaces

Send a lead back to `contact_found` and the write statement sets its earlier
pending or approved first-touch email drafts to `rejected` / `bad draft` in the
same statement as the insert, so Send (which sends the oldest approved draft)
cannot send the old one first. Sent drafts, follow-ups, replies and LinkedIn
drafts are never touched.

**The switch, 2026-10-19 (`redraft_with_template.py`).** The 30 approved or
pending AI-composed first touches that had not been sent were retired with
`--retire-only` (each marked in `hold_reason`); their leads stayed at `drafted`,
so neither Send nor the old composing Drafting — still running from n8n's memory
until the new one is published — could act on them. `--apply` re-queues them to
`contact_found` (and retires any AI first touch written since), and refuses until
the published Drafting version is the template build. With no flag it changes
nothing and says what it would do.

It ran once the first template build was published (2026-10-18, 22:00–22:29
UTC). The operator then amended the template — the `company` SMYKM line,
"in Phase II" for a single phase — before that build had drafted any of the 30,
so they were put back to `drafted` at 22:29:28 UTC. `--apply` now also refuses
until the published Drafting carries the amendment (`FIX_MARKER`).

## A role inbox is not a person

Section 9 Workflow 3b, locked. The greeting is gated on the **address**, not on
whether a name is known: KLIXAR's founder is a confirmed name with a confirmed
LinkedIn profile, and KLIXAR's hello@ inbox is still not his mailbox, so it is
greeted "Hi there,". A personal-shaped address whose owner no source names gets
no name either.

## Sender identity

The template carries "Abdullah Amir" and "Founder, Amitrix Labs" as approved
text. Send checks every cold body for `NOVASCOUT_SENDER_NAME` followed by
`NOVASCOUT_SENDER_TITLE` (the environment if set, otherwise the repo's `.env`),
and the Reply Assistant signs with them, so the build refuses a template whose
sign-off is not exactly `Cheers!` / those two lines. There is no default name:
a default once signed every draft as the wrong person.

## code_assemble.js is the Reply Assistant's now

`code_assemble.js` was the composer's Assemble Drafts node. It is no longer
shipped in this workflow, but everything above its `// Node body` marker is
embedded verbatim in the Reply Assistant's Assemble Reply
(`../sendtrack/build_workflow.py`): the claim rules a drafted reply is checked
by. It was left byte-identical on 2026-10-19 so `reply-assistant.json` did not
change. `test_assemble.js` and `test_rule_mutations.py` still prove those rules.
Keep its rules section free of n8n reads.

## The one-pager is not built here

`nova-one-pager.docx` (repo root) is the asset Mailbox Watch's reply
notification links. **No script in this repo builds it.** Its
`docProps/core.xml` (creator `Un-named`, revision 1, created and modified 9 ms
apart) is programmatic output, consistent with the JS `docx` library, and the
versions created on 2026-09-18 and 2026-09-21 both carried an
`[Abdullah: ... Do not estimate one.]` note paragraph (the first also had
`[DATE]` placeholders). The repo's copy is that output **edited in place**
(`word/document.xml`).

If whatever generated it runs again and its output replaces this file, the
source needs the same fixes, or they come back with it:

1. **No internal note and no placeholders:** no `[Abdullah: ...]` paragraph, no
   `[DATE]`, nothing in square brackets.
2. **`sponsor` for the CRO's client, never `pharma team`, and nothing calling
   the reader a sponsor.**
3. **No speed claim beyond "in real time".**
4. **The five capability bullets under "What Nova does"** (added 2026-10-02):
   qualification fields, routing of investigators and trainees, the
   capabilities deck, the booking link, the dashboard.
5. **The website is the only knowledge source**: "Nova is trained on your own
   website", no SOPs, protocols or "service documentation"; the 48-hour demo
   step is "You share your site URL".
6. **No "new dashboard" line.**

After regenerating, run `python audit_drafts_vs_onepager.py` before the file is
served, then `python build_onepager_sites.py` (it stops if any phrase is not
found exactly once).

## The bug worth remembering

The first live run, in September, produced four drafts that mentioned neither
Nova nor NoblePath: the system prompt was assigned on the Config node, and a
Postgres node replaces the items wholesale, so the model got an empty prompt and
the execution reported success. n8n does not error on a missing field. The
template build keeps the guard that came out of it: every field a node reads off
the item (`$json.ok`, `$json.payload`, each `d->>'…'` of the write, each `lead.…`
of Build First Touch) must be something the node above actually emits, with
drift-guard cases proving each guard fires.

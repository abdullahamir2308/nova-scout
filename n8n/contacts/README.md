# Workflow 3b — Contact Lookup

Sources for the five Code nodes in `../workflows/apollo-contacts.json`, the tests
that cover them, and the generator that assembles the workflow JSON.

Same contract as `../enrichment/` and `../scoring/`: the workflow is **generated,
not hand-edited**, so the JS that was tested standalone is byte-identical to the
JS that ships inside the escaped JSON string. Regenerate after any change to a
`.js` file:

```powershell
python n8n/contacts/build_workflow.py    # writes ../workflows/apollo-contacts.json
```

Then re-import. The repo is not mounted into the container, so copy it in first:

```powershell
docker cp n8n\workflows\apollo-contacts.json nova-scout-n8n-1:/tmp/apollo-contacts.json
docker exec nova-scout-n8n-1 n8n import:workflow --input=/tmp/apollo-contacts.json
```

`import:workflow` **deactivates** the workflow, and activation is a UI action —
re-activate it in the editor after importing.

## Why this is 3b and not part of Workflow 3

Section 9 puts the Apollo lookup inside Workflow 3 ("**Then** Apollo lookup for
leads scoring ≥ 50 only" -- ≥ 60 until 2026-10-06, see Section 9's "Decision
2026-10-06"). It ships as a separate workflow anyway, for one
reason: build rule 4 requires every workflow to be queue-driven and idempotent,
and an inline Apollo stage is neither.

Inline, a lead's only chance at a contact is the same execution that scored it.
Nine leads were already sitting at `status='scored'` when this stage was built —
scored before it existed. Reaching them would have meant re-queueing them to
`enriched` and paying for a full re-score (a GPU rationale call and a
ClinicalTrials.gov lookup each) to get at a step that costs neither. Every future
outage, plan change, or threshold change would have the same shape.

As its own queue keyed on `status='scored' AND fit_score >= 50 AND no contacts
row`, it drains whatever is waiting, whenever it runs.

## Four ways a lead gets a contact (2026-10-07)

`Harvest Site Emails` and `Resolve Contact` take the first that applies; only the
fourth spends anything.

1. **A scraped address** in `data/ichgcp_leads.csv` (via `Index Scraped Emails`):
   the email, verified, plus the founder's name, title and LinkedIn if the site
   gave them. Nothing is fetched and Apollo is not called.
2. **An address the company publishes on its own website** (added 2026-10-07,
   `Harvest Site Emails`): literally on the page, same domain only, never a
   careers@/ir@/privacy@-class mailbox. Written `verified = true` for the same
   reason the ichgcp address is — it is first-party. Rules and evidence below.
3. **No address, but a founder LinkedIn profile** that enrichment harvested from
   the company's own site (`linkedin.com/in/...`; a company page does not count):
   a **LinkedIn-only contact** — `linkedin_url` plus the name and title the site
   gave, `email` NULL, `verified` false, `apollo_id` NULL. Apollo is not called,
   the lead advances to `contact_found`, and Workflow 4 drafts the DM (its email
   is tagged `no-address` and held).
4. **None of those:** Apollo. On the Free plan it answers 403, which is a
   property of the account and not a blip — so the attempt is recorded as
   `exhausted`, the lead **leaves the queue**, and it surfaces in
   `needs_manual_contact` with what each source actually tried. That is the
   two-minute manual look-up Section 7 prices.

**Such a lead is never queued again** because it now has a `contacts` row (2, 3)
or a `contact_attempts` row (4). After an Apollo plan upgrade, put them all back
with two deletes:

```sql
DELETE FROM contacts WHERE apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL;
DELETE FROM contact_attempts;
```

## The queue used to starve, and why it does not now (migration 015)

**The bug.** The queue is `status='scored' AND fit_score >= 50 AND no contacts
row`, ordered `fit_score DESC` and capped at `Config.batch_size` (10). A lead
with no scraped address, no first-party address on its own site and no founder
LinkedIn went to Apollo, was refused, wrote nothing, stayed `scored` — and came
back at the **top** of the next hour's batch, because it scores well. Nine leads
were in exactly that state on 2026-10-07 (35, 53, 92, 94, 212, 498, 500, 658,
733). At ten, the batch of 10 would have held nothing but stuck leads and no new
lead would ever have been looked up again.

The queue had no memory of a lookup that found nothing, and `contacts` had
nowhere to keep one: a tombstone row there means *"asked, nobody there"*, which
is a different thing from *"asked, Apollo would not answer"*, and Master Ref
Section 8 locks that table's columns.

**The fix** is `contact_attempts` (migration 015) and one more clause in the
batch query. Two ways out of the queue, and the second is the one that cannot be
defeated:

| `last_outcome` | Means | Retried? |
|---|---|---|
| `exhausted` | every source answered and there is no contact — including a plan-gated 403, which will answer identically every hour until somebody upgrades | no, ever |
| `refused` | a 429, a 5xx, a timeout, a response that was not the expected shape | yes, up to `Config.max_attempts` (3) |

`attempts` is what bounds the second row: a refusal whose shape nobody
recognised still stops after three tries instead of starving the queue. A lead
retired by the count alone shows `last_outcome = 'refused'` in
`needs_manual_contact`, which is the signal that `isPlanGate` in
`code_pick_contact.js` needs a new case rather than the lead needing a human.

The lead stays `scored` throughout: Section 8's status flow has no state for
"asked and found nothing", and `contact_found` would put a contactless lead in
front of Workflow 4's grounding guard. A lookup that *does* find a contact
deletes the record in the same statement that writes the contact, so a lead
since reached leaves the manual queue.

**Measured, the run that shipped this** (2026-10-07, one execution): of the nine
stuck leads, **five were unblocked by source 2 alone** — 53 `info@ivrs.org.in`,
94 `contact_us@rotrial.com`, 498 `ilse.eder@ee-cro.com`, 658
`info@delphiniumcro.com`, 733 `bd@hvivo.com` — all five `verified`, all five now
`contact_found`. The remaining four (35, 92, 212, 500) were recorded `exhausted`
and left the queue, which went from 9 waiting to 0. No Apollo credit was spent;
none could be.

## Source 2: an address published on the lead's own website

`code_site_emails.js`. It runs for a lead the CSV has no address for, reads the
homepage and up to `Config.site_max_pages - 1` contact/imprint/about/team pages
discovered on it (plus `/contact`, `/contact-us`, `/kontakt`, `/impressum`,
`/about` as fallbacks), and harvests addresses from `mailto:` hrefs and from the
page text.

**Why the fetch is here and not in enrichment.** Workflow 2 already fetches these
very pages, but keeps none of their text: it harvests LinkedIn URLs from the blob
by regex and the rest goes to the model and is discarded. Adding the harvest
there would only help leads enriched from then on — the 274 already in the table
would each need a re-enrichment (a GPU call) and a re-score (a rationale call and
a ClinicalTrials.gov lookup) to reach something that costs neither. So the stage
that needs an address goes and looks for one, which also means it reads the site
as it is today rather than as it was when the lead was enriched.

**Three rules, each one earning its place against real pages:**

| Rule | What it stops, measured |
|---|---|
| literally on the page — a `mailto:` or text, never pattern-derived | `first.last@`, or `info@<domain>` because most companies have one. A guessed address is not a published one, and `verified` draws exactly that line |
| the lead's own domain, or a subdomain of it | `mtz-clinical.pl` publishes only `@pratia.com` (it was acquired), `cebisinternational.com` only `@cebis-int.com`, `archerresearch.eu` only Wix's `@sentry.wixpress.com` telemetry — the wrong company or no company, all three rejected by this one rule |
| never a non-contact mailbox (`NEVER`) | `hvivo.com` publishes `bd@`, `careers@` and `ir@`. A cold email to careers@ is worse than none: wrong desk, and it reads as a mass send |

**Which address, when there are several.** A deny list decides what is usable; a
short preference ladder decides between what is left, and a mailbox carrying the
founder's own name wins outright because two independent sources then agree on
the person (the same corroboration `code_pick_contact.js` ranks an Apollo
candidate by). Measured on the real pages: `ee-cro.com` → `ilse.eder@` over
`office@` and `michael.freigassner@`; `hvivo.com` → `bd@` over `careers@`/`ir@`;
`delphiniumcro.com` → `info@` over `client@`. Ties break by length then
alphabetically, so two runs of one batch write the same address.

**Politeness.** At most `site_max_pages` requests per lead, only for leads with
no address on disk, only on the lead's own host, three leads at a time, and a
404 on a path nobody linked is not recorded as an error. A batch of 10 is at
most ~50 requests spread over as many hosts.

## The pipeline

```
Cron / Manual → Config → Fetch ICH GCP CSV → Parse CSV → Index Scraped Emails
                                                                │
                                            Get Qualified Batch ┘
                                                    ↓
                                           Harvest Site Emails
                                                    ↓
                                            Resolve Contact → Needs Apollo?
                                                                   │
                          ┌────────────────────────────────── no ──┤
                          │  Build Contact Payload (Scraped)       │ yes
                          │                                        ↓
                          │              Apollo People Search → Pick Best Contact
                          │                                        ↓
                          │                               Apollo Answered? ── no ──┐
                          │                                        │ yes           ↓
                          │                                    Matched? ── no ──┐  Record
                          │                                        │ yes        │  Lookup
                          │                     Apollo People Match ↓           │  Attempt
                          │                Build Contact Payload (Apollo)       │
                          │                                        ↓            │
                          │                            Drop Failed Matches      │
                          ↓                                        ↓            ↓
                          └──────────────→ Write Contact & Advance ←────────────┘
```

Five branch points, each with a reason:

- **Needs Apollo?** — Section 7's ordering rule, one level finer. Of the leads
  that scored high enough to be worth paying for, only the ones with no address
  on disk and none published on their own site reach a paid endpoint.
- **Apollo Answered?** — was `Drop Failed Searches`, a filter that discarded a
  refusal. It no longer discards. The lead still gets no `contacts` row — writing
  one would record an outage as a permanent fact about the company — but the
  attempt is recorded, which is what stops a lead Apollo will never answer for
  from filling every batch of 10 for ever.
- **Matched?** — only a candidate that cleared both the title gate and the domain
  gate is worth an enrichment credit.
- **Drop Failed Matches** — a call that did not answer is not evidence. Same
  self-healing shape as Workflow 3's `Drop Failed Lookups` and Workflow 2's
  `Drop Failed Calls`.

## The credit-conservation finding

**Every ICH GCP company profile carried an email address, and ingestion dropped
it.** Section 8 gives `leads` no email column, so the scraper's `email` field —
present on 92 of 123 rows in `data/ichgcp_leads.csv` — was written to the CSV,
committed to the repo, fetched by the ingestion workflow, and then discarded at
the `INSERT INTO leads` boundary. Nothing was lost; it was only unread.

Of the nine leads qualifying at the time this stage was built, **five already had
an address in that CSV** — 56% of the batch, needing no Apollo call at all.

This is why the stage re-fetches the CSV rather than reading the address out of
Postgres. The alternatives were both worse:

- A new `leads.email` column changes a schema section Section 8 locks.
- Writing the address into `contacts` at ingestion time would attach a contact to
  a lead that has not been scored — exactly the ordering Section 7 forbids, and
  the thing that keeps Apollo on the free tier.

One fetch per batch, an in-memory index, an O(1) lookup per lead. The URL is read
out of `ingestion-ichgcp.json` at build time rather than restated, so the two
stages cannot drift onto different artefacts.

## What `verified` means

One flag, two sources, one definition: **the address is confirmed, not inferred.**

| Source | `verified` | Why |
|---|---|---|
| ichgcp profile page | `true` | A first-party address the company published about itself |
| the company's own website | `true` | The same ground: published by them, about them, literally on the page |
| Apollo, `email_status: verified` | `true` | Apollo confirmed deliverability |
| Apollo, `email_status: guessed` | `false` | Pattern-derived. Real enough to keep, not to send to unchallenged |
| Apollo, masked or absent | `false` | No address at all |

Workflow 6 is the consumer: this is the flag a send should gate on.

## What is never inferred

A role inbox (`info@`, `contact@`, `hello@`, `connect@`) is a company address,
not a person's. Where enrichment also named a founder, both facts are written —
but the name is **not** presented as the owner of that inbox, because no source
says it is. That distinction matters downstream: Workflow 4 drafts an email
variant and a LinkedIn variant separately, so the address and the person feed
different channels anyway.

Of the five scrape-sourced contacts, four are role inboxes and one
(`Devesh.kumar@…`) is a named mailbox. Two of the five carry a founder name,
title and LinkedIn URL from the Workflow 2 site extraction.

## The three-way split, and why it is three and not two

*(Still true of what reaches `contacts`. Since migration 015 every one of these
outcomes also writes or clears a `contact_attempts` row — see "The queue used to
starve" above — which is what decides whether the lead stays in the queue.)*

The distinction this stage turns on:

| Outcome | Row written | Lead advances | Retried |
|---|---|---|---|
| Apollo refused transiently (429, 5xx, timeout) | no | no | **yes**, up to `max_attempts` |
| Apollo refused permanently (the plan gate) | no (an `exhausted` attempt instead) | no | no |
| Apollo answered, nobody usable | tombstone | no | no |
| Apollo answered, contact found | yes | yes | no |

"Refused" and "nobody there" look nearly identical at the node boundary and mean
opposite things. Collapsing them either burns a credit per cron tick forever on
domains Apollo has already answered for, or writes a permanent "no contact"
against a company that has one, on the strength of a five-minute outage.

**And a third pair, found 2026-10-07: "refused" is itself two things.** A 429 or a
timeout could answer differently next hour. A plan-gated 403 cannot — it is a
property of the account, measured on every endpoint, and will say the same thing
every hour until somebody upgrades. Treating the second like the first is what
was filling the batch of 10 with the same nine leads. `isPlanGate` in
`code_pick_contact.js` recognises only the plan gate, positively; anything it
does not recognise stays a retry, so the old rule holds for every refusal that
could still pass.

The **tombstone** is a `contacts` row with every field null and `verified=false`.
It exists because the batch query has no other way to tell "not looked up yet"
from "looked up, nothing there" — without it, every run re-pays for the same dead
domains. It deliberately does not advance the lead: Section 8's status flow has
no state for "asked and found nothing", and `contact_found` would put a
contactless lead in front of Workflow 4, which is what the grounding guard exists
to prevent. The lead stays `scored`.

## Ranking

Section 12 names the buyer: "CRO founder, Managing Director, or BD Director".
That is encoded as an ordered preference, not a filter:

| Tier | Matches |
|---|---|
| 0 | founder, co-founder, owner, proprietor |
| 1 | managing director, general manager, director general, managing partner |
| 2 | CEO, chief executive |
| 3 | business development, BD director, BD manager, commercial director |

Above all four sits **corroboration**: a person the company's own site named as
founder in Workflow 2 outranks a better-sounding title on a stranger. Two
independent sources agreeing on the person beats one source's title string.

Within a tier, seniority breaks the tie (a Business Development *Director* over a
Business Development *Representative*) — a tiebreak, not a fifth preference level
the spec does not have.

**Apollo attaching a person to a different domain is a hard gate, not a ranking
penalty.** Apollo will return a former employee, a parent-company executive, or a
same-name business in another country. That person is the wrong company's contact
however senior the title reads, and paying to enrich them buys nothing.

The Apollo search's `person_titles` filter is generated from this same tier list
at build time, so the endpoint can never be asked for a set of titles the ranker
would then discard.

## BLOCKED: Apollo's Free plan does not include these endpoints

Measured 2026-09-06, on both the API-key path and the OAuth path, on an account
showing 125 unused lead credits:

```
POST /api/v1/mixed_people/search   -> 403 API_INACCESSIBLE  "not included in your Free plan"
POST /api/v1/people/match          -> 403 API_INACCESSIBLE  "not included in your Free plan"
POST /api/v1/mixed_companies/search-> 403 API_INACCESSIBLE  "not included in your Free plan"
GET  /api/v1/auth/health           -> 200 {"healthy":true,"is_logged_in":true}
```

The key is valid and the account is live — the endpoints are gated by plan, not
by key. The 125 lead credits are usable inside app.apollo.io, not through the
API.

This contradicts Section 4's "Apollo | $0 (free tier, protected by pipeline
order)". Pipeline order was never the constraint that mattered; API access is.

`/api/v1/organizations/enrich` returns a *different* error — a key-scope message
rather than a plan message — so it may be reachable if the key's scope is widened
in Apollo's settings. It was not pursued: org enrichment returns company
firmographics, not people, so it cannot produce a contact.

**The stage is built, tested and correct against this.** A plan-gated 403 is
handled by the same path as a timeout: nothing is written, the lead stays
`scored`, and the next run retries. Nothing needs changing when the plan allows
it — the four leads currently waiting will be picked up by the next execution.

## Files

| File | Role |
|---|---|
| `code_index_csv.js` | **Index Scraped Emails** — domain → email map from the committed CSV |
| `code_site_emails.js` | **Harvest Site Emails** — an address published on the lead's own site |
| `code_resolve.js` | **Resolve Contact** — the credit gate; a published address short-circuits Apollo |
| `code_pick_contact.js` | **Pick Best Contact** — ranks the search, splits refusal / no-match / match |
| `code_payload_apollo.js` | **Build Contact Payload (Apollo)** — email_status → `verified`, drops masked addresses |
| `code_payload_scrape.js` | **Build Contact Payload (Scraped)** — the zero-credit branch |
| `build_workflow.py` | Generator, and the home of every spec-drift guard |
| `harness.js` | Runs a Code-node body standalone against a mocked n8n context |

## Tests

Offline, no network and no Apollo. All exit non-zero on failure:

```powershell
node   n8n/contacts/test_resolve.js        # 67 cases
node   n8n/contacts/test_site_emails.js    # 32 cases
node   n8n/contacts/test_pick_contact.js   # 68 cases
python n8n/contacts/test_drift_guards.py   # 31 cases
```

- **`test_resolve.js`** covers the half that spends nothing, where the failure
  mode is silent: a normalisation slip that misses a domain does not error, it
  just quietly buys an address Apollo would sell us that we already had.
- **`test_pick_contact.js`** covers the three-way split, the ranking order, the
  domain gate, and Apollo's `email_not_unlocked@domain.com` placeholder. The
  object-shaped-error case is a regression test: the first live run rendered n8n's
  wrapped HTTP failure as `[object Object]`, throwing away the only field an
  operator needs.
- **`test_site_emails.js`** mocks the HTTP layer and replays the real pages of
  the nine leads that were stuck on 2026-10-07. Its job is the silent failure
  mode of this source: a rule a shade too loose writes another company's address,
  `verified = true`, and the pipeline emails it with nobody the wiser. So there is
  a case for each of the three rules against the page that actually breaks it.
- **`test_drift_guards.py`** mutates a scratch copy of the Master Ref (or the JS,
  or the ingestion workflow) and proves each build-time guard refuses. Two cases
  assert the opposite: that moving the score gate in the doc, or the CSV URL in
  ingestion, *changes the generated workflow* rather than failing — those are read
  from their source, not restated.

## The Apollo credential

A Header Auth credential named `Apollo - x-api-key` (header name `x-api-key`,
value `APOLLO_KEY` from `.env`), referenced by the fixed id `novascoutApollo01`
so the key never enters the repo. Create it in the n8n UI, or import it:

```powershell
# a throwaway file, deleted immediately after — do not commit it
docker cp apollo-cred.json nova-scout-n8n-1:/tmp/apollo-cred.json
docker exec nova-scout-n8n-1 n8n import:credentials --input=/tmp/apollo-cred.json
docker exec -u 0 nova-scout-n8n-1 rm -f /tmp/apollo-cred.json
```

## Draining the queue by hand

Queue-driven, so running it repeatedly is safe and idempotent. Each execution
processes one bounded batch (`Config.batch_size`, default 10 — a spend ceiling,
not a throughput one).

```powershell
docker exec -e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false `
  nova-scout-n8n-1 n8n execute --id contacts0001
```

Repeat until this reaches zero:

```sql
SELECT count(*) FROM leads l
  JOIN scores s ON s.lead_id = l.id
  LEFT JOIN contacts c ON c.lead_id = l.id
 WHERE l.status = 'scored' AND s.disqualified = false
   AND s.fit_score >= 50 AND c.lead_id IS NULL
   AND NOT EXISTS (SELECT 1 FROM contact_attempts a
                    WHERE a.lead_id = l.id
                      AND (a.last_outcome = 'exhausted' OR a.attempts >= 3));
```

Since migration 015 this **does** reach zero: a lead nobody can reach is
retired rather than retried, and `needs_manual_contact` is where it goes.

The two `N8N_RUNNERS_*` overrides keep the one-off CLI process from colliding
with the task-runner broker the long-running container already has bound.

Re-running is free where it should be: a lead keeps its `contacts` row whether
the lookup found somebody or not, so the batch query never returns it twice. The
write upserts on `contacts_lead_id_key` (migration 004), so a lead re-queried
after a plan upgrade updates its row in place rather than gaining a second one.

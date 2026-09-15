# Nova Scout — Master Reference Document v1
## AI-Automated BD Pipeline for Nova Agent Kit

> Paste this document into the Claude Project tab before every build session.
> Single source of truth for architecture, stack, and build decisions.
> Do not deviate from anything marked **LOCKED**. Ask before changing anything marked **CONFIRMED**.

---

## 1. Project Overview

**What we are building:** An agentic outbound pipeline that finds small-to-medium CROs worldwide, enriches and scores them against our ICP, drafts personalised outreach, and delivers a ranked review queue every morning. Human approves; system sends and tracks.

**Why:** Nova Agent Kit is built and live at two CROs. The remaining problem is purely distribution. Manual prospecting does not scale; this replaces ~8 hours/day of manual work with a ~20 minute morning review.

**Secondary goals (equal weight):**
- Learn n8n, self-hosted agent orchestration, and local LLM inference
- Produce a portfolio-grade project
- Run at near-zero cost

**Endgame:** Nova Scout retargeted (CRO → pharma sponsor) becomes a second sellable product to existing Nova clients. Not before it has produced our own first 10 clients.

---

## 2. Tech Stack — LOCKED

| Layer | Choice | Notes |
|---|---|---|
| Orchestration | n8n, self-hosted, Docker | Community edition, free |
| Database | Postgres 16, Docker | Same container stack as n8n |
| Inference | Ollama, native Windows install | NOT in Docker — needs direct GPU access |
| Review UI | NocoDB, Docker | Airtable-style layer over Postgres |
| Email send | SMTP node → Zoho Mail mailbox | Not Resend, not SendGrid |
| Email receive | n8n IMAP trigger → same mailbox | No webhooks, no public URL needed |
| CRM | HubSpot (native n8n node) | Existing account |
| Contact data | Apollo.io API — health-check only, contact endpoints gated | Free plan does NOT include people/contact search regardless of credit balance, see §4 |
| Version control | Git — workflows exported as JSON | Workflows are code |

**DO NOT USE:** Make.com (per-operation pricing), Resend/SendGrid/Postmark for cold outreach (ToS prohibits it), any paid LLM API as the default path, LinkedIn automation tools of any kind, open/click tracking pixels.

**Host:** Windows, Docker Desktop with WSL2 backend. n8n reaches Ollama at `http://host.docker.internal:11434`.

---

## 3. Hardware & Model Config — LOCKED

**Machine:** RTX 4060 Ti 16 GB VRAM · Ryzen 5 3600 · DDR4-3000

**Binding constraint:** memory bandwidth (~288 GB/s), not compute. Model must fit entirely in 16 GB VRAM — CPU offload onto DDR4-3000 is unacceptably slow.

**Model rule — ONE MODEL FOR THE ENTIRE PIPELINE.**
Running different models per node causes Ollama to evict and reload weights on every switch. Load time exceeds generation time. One model, always resident.

**LOCKED CHOICE: `qwen3.5:9b` at Q4_K_M (6.6 GB).** Leaves ~9 GB headroom for KV cache.

**What does NOT fit on 16 GB — do not attempt:**

| Model | Q4 footprint | Verdict |
|---|---|---|
| Qwen3.5/3.6/3.8 **27B** | ~15–17 GB | Exceeds VRAM before KV cache. Partial CPU offload on DDR4-3000 → unusable batch speed. |
| Qwen3.5/3.6 **35B-A3B** (MoE) | ~19 GB | All 35B must load even though only 3B activate. Does not fit. |
| Any 27B at Q3 | ~13 GB | Fits, but quantization damage makes it worse than a comfortable 9B. |

**No 14B exists in the current generation.** Qwen3.5+ sizes are 0.8B / 2B / 4B / 9B / 27B / 35B-A3B / 122B-A10B / 397B-A17B. A 9B from the 3.5 generation outperforms a 14B from the older Qwen3 generation — architecture gains beat parameter count at this scale.

**Quality ladder — climb only on evidence, in this order:**
1. `qwen3.5:9b` (Q4_K_M, 6.6 GB) — start here
2. `qwen3.5:9b-q8_0` (11 GB, near-lossless) — if drafting quality disappoints. Still fits with ~5 GB spare.
3. **Claude Sonnet 5 for the drafting node only** — `claude-sonnet-5`, $2/$10 per MTok

**Note on the API fallback:** Sonnet 5 costs 2× Haiku 4.5 ($1/$5), not less. It is chosen anyway because drafting volume is 10–20/day, which prices out at roughly $3/month versus $1.65 on Haiku. At that volume, capability wins and price is noise. The $2/$10 rate is now permanent — Anthropic cancelled the September 2026 increase to $3/$15.

**Rule for model selection across this project:** pick by capability where volume is low, pick by price only where volume is high. Never route the high-volume enrichment or scoring nodes to a paid API — that is what the local 9B exists for.

Never step up to a larger local parameter count on this card. Higher-quality quant of a smaller model beats a starved larger one.

**Before Sprint 0:** check `ollama.com/library` for whether a 9B has shipped in the Qwen3.6 or 3.8 generation. If it has, use the newest 9B instead. Sizes and footprints in this table stay valid.

**Required Ollama environment variables (set as Windows system env vars):**
```
OLLAMA_KEEP_ALIVE=-1        # never unload the model
OLLAMA_NUM_PARALLEL=1       # serial processing, avoid VRAM contention
OLLAMA_HOST=0.0.0.0:11434   # reachable from Docker containers
```

**Structured output:** always use Ollama's `format: json` with an explicit schema for extraction and scoring nodes. Never parse free text.

**Inference tuning — three gotchas that will bite otherwise:**

1. **Override the default `presence_penalty`.** Qwen3.5 ships with `presence_penalty: 1.5`. **Tested correction:** the failure mode is NOT malformed JSON as originally guessed here — Ollama's grammar-constrained `format: json` mode makes syntactically invalid output impossible, which masks the problem. The real failure is silent, field-level data loss on copy-from-source fields: at 1.5, `founder_linkedin` and `therapeutic_areas` entries came back null/truncated even when clearly present in the source text, because the penalty discourages "repeating" tokens the model is meant to be copying, not generating. Output is valid-looking JSON with quietly missing data — more dangerous than a visible parse error, since it can pass silently into scoring. At `presence_penalty: 0`, all fields extracted correctly. For extraction and scoring nodes set `presence_penalty: 0`, `temperature: 0.1`. **For drafting, `temperature: 0.45`, `presence_penalty: 0.3`** — corrected from an original guess of 0.7 after Sprint 4 testing: at 0.7 the model fabricated ("our own staff data"), joined unrelated facts into an untrue claim, and on one lead inverted the entire pitch — telling the prospect they already owned the product being sold. 0.45 fixed it; verified against real failure examples, not reverted on guesswork.

2. **Cap the context window at 16K–32K.** These models advertise 262K native context. Do not use it — KV cache at full context adds 4–8 GB and will push you off the GPU. Scraped pages are 5–20K tokens; 32K is generous.

3. **Thinking mode — tested WRONG in the original version of this doc.** For this build, thinking is ON by default, not off. Two compounding traps: omitting `think` entirely burns the whole token budget on reasoning traces and returns empty content. Worse — on `/api/chat`, setting `think:false` silently disables `format` schema enforcement, so the model answers in free prose instead of structured JSON, with no error. Keeping thinking on to preserve JSON enforcement works but costs 30–50s/lead. **Use `/api/generate`, not `/api/chat`, for structured extraction** — it honours `think:false` together with the schema correctly, same valid output in ~5s. This is load-bearing for every future Ollama structured-output node (scoring rationale, drafting) — verify it still holds on that model/endpoint before assuming it, don't just copy the setting forward blind.

**Parallelization principle, confirmed by real testing:** only the GPU call needs to be serial (`OLLAMA_NUM_PARALLEL=1` still holds for inference). Fetching/I/O steps do not — running them serially when they don't need to be caused a real failure (10 dead-domain fetches serially blew a 300s task-runner timeout; fetching 5-at-a-time dropped that batch from 305s to 46s). Apply this shape to Workflow 3 and 4 too: parallelize lookups/fetches, serialize only the model call.

**n8n activation — there is no "Active" toggle, use Publish.** n8n 2.0+ replaced the classic active/inactive flag with a draft/published model, confirmed in official docs — this applies to Community Edition, not just enterprise plans. The UI element is a **Publish** button (near the workflow name, alongside Save), not a switch. Running `publish:workflow` from the CLI leaves a misleading half-state: `workflow_entity.active` flips true and the UI can show it as published, but the actual trigger-registration tables stay empty — no cron is registered with the running process, and it silently never fires. **Always publish via the n8n UI**, never the CLI, for anything that needs to run on a schedule. `active` in the DB does NOT gate a one-off `n8n execute --id=...` run — that works regardless of published state.

**Publishing locks to a specific version.** If the workflow is regenerated and re-imported after being published (which happens routinely here — build_workflow.py has rebuilt this workflow multiple times for fixes), the publish goes stale and must be redone against the new version. Re-publish after every workflow rebuild, not just once.

**Verification — the publication tables are a dead end on this instance, don't check them.** `workflow_publication_trigger_status`, `workflow_published_version`, and `workflow_publication_outbox` only get written when `useWorkflowPublicationService` is true. It defaults to false and `N8N_USE_WORKFLOW_PUBLICATION_SERVICE` isn't set here, so those tables stay empty regardless of whether activation actually worked — checking them proves nothing on this instance. **The real signals**, confirmed by reading the running source directly: `workflow_entity.active='t'`, `activeVersionId` non-null, `triggerCount=1`, a new row in `workflow_publish_history`, and — the only fully conclusive proof — an execution with `mode='trigger'` (the execution column is `mode`, not `trigger_mode`; the value for a fired schedule is `'trigger'`, not `'cron'`). `triggerCount=0` on its own is not evidence of a broken trigger node — it's set only after successful activation, so it reads zero on any never-activated workflow no matter how correctly built.

**n8n execute against an already-running container needs runner env overrides.** A one-off `docker exec ... n8n execute --id=...` against the live container conflicts with the main process's own task-runner broker unless you pass `-e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false`. Without these it can hang or fail confusingly.

**Verify before building anything on top:** run `ollama ps` after loading and confirm the model shows 100% GPU, not a CPU/GPU split.

**Escape hatch (CONFIRMED, not yet active):** if local drafts consistently require heavy editing after two weeks of real use, swap ONLY the drafting node to Claude Sonnet 5. Volume is 10–20 drafts/day, so cost is ~$3/month. Decide on evidence, not upfront. Enrichment and scoring stay local permanently — those are the high-volume nodes.

---

## 4. Cost Model — LOCKED

| Item | Cost |
|---|---|
| Outreach domain | ~$10/year |
| Zoho Mail Lite mailbox | ~$12/year |
| n8n, Postgres, NocoDB, Ollama | $0 |
| ClinicalTrials.gov API | $0 |
| Apollo | $0 — but see correction below |
| **Total** | **~$22/year + electricity** |

Any proposed change that introduces recurring cost must be justified against this baseline.

---

## 5. Email Infrastructure — LOCKED

**Domain:** dedicated outreach domain, purchased separately. Never `noblepathcro.com` (client's reputation), never a personal Gmail (ToS violation + account risk).

**Mailbox:** a named human address, not `hello@` or `info@` or `sales@`. Currently `abdullah@amitrixlabs.com` — **Amitrix Labs**, not Amitrex (this doc had the wrong spelling until corrected here; check for it elsewhere if this file is ever searched). Sender identity for drafting: Abdullah Amir, Founder, Amitrix Labs, +923178485713 — set via `NOVASCOUT_SENDER_NAME`/`_TITLE`/`_PHONE`, not hardcoded, so it can change without touching the drafting workflow again.

**DNS (all three required before first send):**
- SPF record authorising Zoho
- DKIM signing enabled
- DMARC policy record

**Warm-up schedule — enforce in the workflow, not by memory:**

| Period | Max sends/day |
|---|---|
| Week 1 | 5 |
| Week 2 | 10 |
| Week 3 | 15 |
| Week 4+ | 20 (hard ceiling) |

Start warm-up manually the same day the domain is purchased — send real emails by hand to real contacts while the rest of the system is built. The domain ages in parallel with development.

**Message rules — LOCKED:**
- Plain text only. No HTML, no images, no logo, no banner.
- **No open tracking. No link tracking. No pixels.** Reply rate is the only metric.
- Maximum one plain URL. No buttons.
- Signature: name, one line of title, phone. Nothing else.
- No marketing unsubscribe footer. Use a plain sentence: *"If this isn't relevant, reply 'no' and I won't follow up."*
- Irregular send intervals within the recipient's business hours. Never a synchronised burst.
- Under 80 words for first touch.

**Compliance:** GDPR and KVKK both apply across target geographies. Basis is B2B legitimate interest, defensible only if opt-out is trivial and honoured instantly. IMAP workflow must detect "no", "unsubscribe", "remove", "stop" and blocklist the domain immediately and permanently.

---

## 6. LinkedIn — LOCKED

**LinkedIn sending is manual. Always. No exceptions.**

LinkedIn actively bans automation. Fatima's account is a business asset that cannot be replaced. The system drafts the DM and provides a copy button plus a deep link to the profile. A human reads it, edits if needed, and sends it by hand.

This rule does not change regardless of tooling.

---

## 7. Pipeline Architecture

Six workflows, each independent, each queue-driven.

```
Sources → 1 Ingest → 2 Enrich → 3 Score → [Apollo] → 4 Draft → 5 Review (HUMAN) → 6 Send → CRM
                                                                         ↑                    │
                                                                         └──── learn ─────────┘
```

**Correction — the original assumption here was wrong.** "Conserve Apollo credits by scoring first" assumed the constraint was volume/rate. Measured directly: Apollo's Free plan returns 403 API_INACCESSIBLE on `mixed_people/search`, `people/match`, and `mixed_companies/search` regardless of credit balance — the account showed 125 unused credits throughout and consumed zero. Credits shown in Apollo's dashboard are a UI allowance, not an API entitlement; the gate is the plan tier, not usage. Pipeline order still controls volume, but volume was never the actual constraint.

**Decision: don't upgrade Apollo.** 5 of the first 9 qualifying leads were satisfiable for free from data already scraped (ICH GCP profile pages carry an email field the original `leads` table schema had no column for — recovered and used directly, no API call). The remaining gap is genuinely small — a few leads at a time with no scraped email — and fits the existing human-review design better than a paid integration would: when a qualifying lead has no contact, that's a two-minute manual look-up in the review queue, not something worth paying to automate at this volume. Revisit only if that gap grows large enough to actually be a burden.

**Critical ordering rule (still correct, for a different reason):** Apollo contact lookup happens AFTER scoring, never before — this controls volume and avoids wasted lookups on leads that get disqualified, even though it turned out not to be what "kept" Apollo free.

**Idempotency rule — LOCKED:** the host machine will be off some of the time. No workflow may assume its schedule fired. Every workflow queries Postgres for "oldest lead in my input status" and processes a bounded batch. A machine off all weekend simply catches up on Monday. No time-critical webhooks anywhere in the system.

---

## 8. Database Schema

**Location:** the single Postgres container hosts two separate databases — `n8n` (n8n's own internal state, owned exclusively by n8n) and `novascout` (everything below). All application tables live in `novascout`. Never point application workflows at the `n8n` database.

```sql
leads
  id, domain (UNIQUE), company_name, country, source,
  status, created_at, updated_at

enrichments
  id, lead_id FK, therapeutic_areas[], phases[],
  founder_name, founder_linkedin, employee_estimate,
  has_chatbot (bool), chatbot_vendor, site_quality_notes,
  raw_extraction jsonb, enriched_at

  **lead_id must be UNIQUE.** Originally missing — the write step did
  a plain INSERT with no conflict handling, so any re-processing of a
  lead (which the bounded-retry disqualifier design above requires)
  created a second row instead of updating in place. Fixed to a
  unique constraint + upsert; if this schema is ever rebuilt from
  scratch, don't lose this constraint.

scores
  id, lead_id FK, fit_score (0-100), disqualified (bool),
  disqualify_reason, rationale, scored_at

contacts
  id, lead_id FK, name, title, email, linkedin_url,
  apollo_id, verified (bool)

drafts
  id, lead_id FK, channel (email|linkedin), variant,
  subject, body, status (pending|approved|rejected|sent),
  reject_reason, edited_body, created_at

outreach_log
  id, lead_id FK, draft_id FK, channel, sent_at, message_body,
  message_id (UNIQUE), replied (bool), replied_at, reply_body, outcome

blocklist
  domain (UNIQUE), reason, added_at

mailbox_sent
  message_id (PK), sent_at, recipients[], external (bool), lead_id FK,
  source (workflow|sent-folder), seen_in_sent_folder (bool), recorded_at

mailbox_sync
  folder (PK), last_synced_at, messages_seen, undated

inbound_messages
  message_id (PK), received_at, from_addr, subject, lead_id FK, matched_by,
  classification (reply|opt-out|auto-reply|bounce|unmatched),
  opt_out_keyword, body_excerpt, processed_at
```

**Added for Workflow 6 (migration `007`).** `outreach_log.message_id` is the Message-ID the SMTP server accepted — NULL means a *claim*, written before the SMTP call so a crash can never cause a second send — and `draft_id` records which approved draft went out, so the learning loop can tell which variant earned a reply. `mailbox_sent` mirrors the mailbox's Sent folder, because Section 5's warm-up ceiling belongs to the mailbox, not the workflow: the operator's manual sends count against it too. `inbound_messages` records every INBOX message once, keyed on Message-ID, which is what makes reply handling idempotent. `replied_queue` is the read-only NocoDB view Section 9 asks for.

**Lead status values (LOCKED):**
`ingested → enriched → scored → disqualified | contact_found → drafted → approved → sent → replied → won | lost`

---

## 9. Workflow Specifications

### Workflow 1 — Ingestion
**Trigger:** Cron, weekly
**Sources:**
- ICH GCP directory (`ichgcp.net/cro-list`) — per-country pages, server-rendered, no JS needed.
- Manual CSV import path for referrals and conference lists — via psql `COPY`, no workflow needed yet.

**ICH GCP page structure — VERIFIED, two-stage scrape required:**

Country pages (`/cro-list/country/{slug}`) list companies with name, truncated description, and a link to an ichgcp company profile page — but **no external website URL**. The CRO's actual domain appears only on the company profile page (`/cro-list/country/{slug}/company/{company_slug}`) as a `Web:` field. Exception: the two paid "Featured CROs" slots at the top of each country page do carry a direct website link.

So: stage 1 collects profile URLs from the country page, stage 2 fetches each profile to extract the domain. Budget ~15–40 profile fetches per country. Rate-limit politely.

**ICP pre-filter — free, use it:** each country page splits listings under two headings, "Local, small- and mid-size Contract Research Organizations in {country}" and "Global Contract Research Organizations in {country}". The global section is IQVIA, ICON, Parexel, PPD, Syneos, SGS, Fortrea et al — all of which the Section 9 disqualifiers reject on employee count anyway. **Scrape only the local/mid-size section.** Halves fetch volume and pre-qualifies leads before any enrichment spend.

**Access — confirmed IP-level block, not a UA block.** ichgcp.net returns 403 from the dev machine even with a full browser User-Agent spoofed — the block is IP/GeoIP-based, not a `curl` signature match. n8n running locally cannot reach this domain at all, now or on a weekly schedule.

**Shipped architecture:** the scraper runs as a scheduled GitHub Action (`.github/workflows/scrape-ichgcp.yml`, weekly + manual dispatch) from GitHub's runners, and commits `data/ichgcp_leads.csv` to the repo only when it changes. The local n8n workflow (`n8n/workflows/ingestion-ichgcp.json`) never touches ichgcp.net — it fetches the committed CSV from `raw.githubusercontent.com` and upserts into `leads`. This cleanly separates the blocked scrape from the pipeline.

**Known risk, unresolved as of first build:** GitHub Actions runner IPs are datacenter-class and commonly penalized by the same reputation-based WAF systems that block scraping traffic generally — there is a real chance the Action itself also gets 403'd on first run, independent of UA string. If so, the next lever is a small always-on VPS with a residential-leaning IP, or a scraping proxy service — not a scraper redesign, the parsing logic itself is already fixture-tested and sound.

Scraper UA: self-identifying (`NovaScoutBot/1.0` + repo link), not a spoofed browser string — deliberate choice, overridable via `ICHGCP_USER_AGENT` env var if needed.

**Sprint 1 outcome, validated end-to-end and closed:** no 403s across two full Action runs — the IP-reputation WAF risk did not materialize. Only 429 (rate limit) and 404 seen. Retry-with-backoff on 429 confirmed working: every rate-limit hit cleared on the first retry after a 15s wait, none escalated. 13/13 countries succeeded, 123 unique leads in `data/ichgcp_leads.csv`. Remaining profile-fetch failures (4) are genuine dead links on ichgcp.net's side — correctly left non-retryable.

**Correction:** an earlier version of this doc stated 123 leads had landed in `novascout.leads` — that was wrong, conflated with the CSV count. Scrape success and DB ingestion are two separate steps; ingestion must be re-triggered after any manual/out-of-band scrape run, not assumed to follow automatically. As of the first enrichment pass, `leads` held 107 rows — the CSV's 16 additional domains (mostly Poland, some India — the countries that hit 429s in the original scrape) were not yet re-ingested.

**Known, accepted limitation — corrected mechanism.** The shipped upsert is `ON CONFLICT (domain) DO UPDATE`, not `DO NOTHING` as originally assumed here. For a multi-country CRO (same domain on several country pages), this means **the last-processed country in a given ingestion run wins `leads.country`, and it can flip on every re-run** — observed directly: `bitrial.hu` flipped Romania→Poland when the CSV was re-ingested with Poland's leads included. `status` is excluded from the update, so this never regresses pipeline progress (verified in Sprint 1) — only `country` (and other metadata) is unstable.

**Why this doesn't currently hurt scoring:** the scraper only ever touches the 13 target-geography countries, so a flip can only ever land on another already-target country — Section 9's 25-point geography weight is scored identically either way. This would stop being true if the source-country list and target-geography list ever diverge. Not urgent; the fix, if it's ever needed, is the same `lead_countries` join table already noted for the undercounting issue below — one fix resolves both.

**Known, accepted limitation, undercounting:** the same domain-collision dedup means some countries' raw per-scrape totals don't match what lands in the DB (observed: Hungary 8→6, UAE 3→2). Correct behavior for avoiding duplicate lead rows; accepted as-is at current scale (~3% of leads affected).

**ClinicalTrials.gov is NOT an ingestion source.** Its API returns sponsor/collaborator names, not company websites, so it can't populate a domain-keyed `leads` row without a separate name→domain resolution step. It belongs in Workflow 3 (Scoring) instead, as a per-candidate lookup once a domain already exists — see below.

**Output:** deduped rows in `leads` with `status='ingested'`. Dedupe key is normalised domain.

### Workflow 2 — Enrichment
**Trigger:** Cron, every 30 min. Batch of 10 where `status='ingested'`.
**Steps:**
1. Fetch homepage, /about, /services, /team via HTTP node
2. **Chatbot detection is a Code node with regex — NOT an LLM call.** Match against known widget scripts: Intercom, Drift, Tidio, Tawk, Crisp, HubSpot chat, LiveChat, Zendesk. Never spend tokens on what a string match answers.
3. Ollama node with `format: json` and explicit schema → therapeutic areas, phases, founder, employee estimate, site notes
4. Write to `enrichments`, advance status

### Workflow 3 — Scoring
**Trigger:** Cron. Batch where `status='enriched'`.

**Hard disqualifiers (deterministic Code node, runs first):**
- Already has a chatbot
- Employee estimate > 500 (enterprise CRO) — **only on a confirmed number.** Real Sprint 2 data: `employee_estimate` is null on 85% of leads (sites rarely state headcount, and extraction correctly refuses to guess). Null must never trigger this disqualifier — treat it as unknown, not as evidence of scale. Same null-safe handling applies to `founder_name`/`founder_linkedin`, sparse for the same honest-refusal-to-guess reason.
- No functioning website — **"unreachable" over-counts; investigation closed, accepted at current scale.** Of 26 originally flagged, 7 were alive. 2 rescued by relaxing TLS cert validation (broken/mismatched certs, not a block — one, `gvkbio.com`, is a 3,412-employee CRDMO that will correctly hard-disqualify on employee count once Sprint 3 runs). The remaining 5 end in HTTP 403 on every retry rung regardless of technique — confirmed WAF blocks, same class as ichgcp.net, genuinely up but hostile to non-browser clients. Not worth extending the GitHub-Actions-IP workaround here — that was justified for ichgcp.net as the sole ingestion source; for ~5% of individual enriched leads it isn't. Accepted as a permanent, expected loss rate.

  **Design implication for this disqualifier specifically:** don't treat "unreachable" as instantly and permanently disqualifying. Give a lead a bounded number of enrichment attempts (e.g. 2) before trusting the marker, and treat a confirmed WAF-block (got an HTTP 4xx response) as weaker evidence of "no functioning website" than a true connection failure — a 403 proves the site exists. Not yet implemented; decide the exact mechanism when this workflow is actually built.
- Not actually a CRO (agency, consultancy, vendor)
- Domain on blocklist

**Weighted fit score (0–100):**
| Factor | Weight |
|---|---|
| Target geography | 10 |
| Active trials on ClinicalTrials.gov | 20 |
| Founder/MD identified with LinkedIn | 20 |
| Oncology focus (strongest case study) | 15 |
| Employee count 5–100 | 25 |
| Site quality suggests budget | 10 |

**Rebalanced from the original 25/20/20/15/10/10, based on real Sprint 3 measurement, not upfront guessing.** Geography scored 25/25 for all 79 scored leads with zero variance — the only ingestion source (ICH GCP) is already filtered to the 13 target countries, so this factor measures "did our scraper touch this lead," not fit. Kept nonzero rather than dropped to zero, as cheap insurance against a future non-pre-filtered source. The freed 15 points went to employee count: real data showed a company far outside the 5–100 band (~300 employees) ranking #1 overall despite the scoring rationale itself flagging the mismatch as disqualifying in substance — the old 10-point weight didn't cost enough to matter against strong scores elsewhere. This does not change the separate >500 hard disqualifier, which stays as-is.

**ClinicalTrials.gov — measured, not assumed.** The originally-specified `query.locn` country-only fallback was never implemented: measured directly, a bare country search returns 600+ recruiting trials for India alone, which would have awarded the full 20 points to nearly every lead — the same non-discrimination problem geography had, just undetected before shipping. Only the primary `query.spons` sponsor-name lookup shipped (real ~8% hit rate — CROs are usually a trial's collaborator, not its registered sponsor).

**Concrete evidence the `query.term` fallback is worth building now, not deferring further:** all 18 leads in the 50–59 fit_score band are held back by this single factor — zero have a sponsor match, while every other factor (geography, site quality, oncology for 15/18) is confirmed strong. Flip trials alone and all 18 cross 60, landing 71–78. **Do not fix this by lowering the ≥60 threshold** — that declares the factor doesn't matter without checking whether a fairer query would have credited these leads honestly. Test `query.term` against exactly this 18-lead set first, with the same false-positive scrutiny that killed the sponsor-name-cleaning idea, before shipping or discarding it.

**Measured 2026-09-06 — tested against exactly those 18 leads, and rejected.** Same call shape as the sponsor lookup (`filter.overallStatus=RECRUITING`, `countTotal=true`), `query.term=<company_name>` in place of `query.spons`, verbatim company name. 12 of 18 return `totalCount=0` — no different from the sponsor search, no gain. The other 6 return non-trivial counts, but every sampled hit is a false positive — `leadSponsor`, `collaborators`, and `locationFacility` were pulled for each and checked against the company name; none named the company:

| Company | totalCount | What actually matched (sampled) |
|---|---|---|
| Metrics Research | 694 | Generic trial vocabulary — no sponsor/collaborator/facility named "Metrics" |
| MTZ Clinical Research | 82 | Same — nothing named "MTZ" |
| Monitor Medical Research and Consulting | 42 | Same — nothing named "Monitor" |
| LAT Research | 39 | Same — the one substring hit was "lat" inside an unrelated Portuguese-language facility name |
| A-Pharma s.r.o. | 14 | All sponsored by unrelated companies whose names merely contain "Pharma" (Sumitomo Pharma, etc.) — the identical substring collision that already killed sponsor-name-cleaning |
| FARMOVS | 1 | Sponsored by Merck Sharp & Dohme — no reference to FARMOVS anywhere in the record |

`query.term` is a full-text search across the whole study record, not a company-identity match — a CRO's own descriptive name (built from ordinary industry words) is exactly the kind of string that collides with unrelated trials at this vocabulary. Same failure shape as `query.locn` (600+ trials per country, above) and sponsor-name-cleaning (README: stripping "s.r.o." matched 20 unrelated trials). **Not wired in.** The 18 leads are not re-scored and the ≥60 threshold is unchanged — this measurement confirms that decision, it doesn't reopen it.

**Null-handling for weighted factors:** where `employee_estimate` or `founder_name`/`founder_linkedin` is null, that factor contributes a neutral partial score, not zero — a confirmed miss (e.g. a named founder found and clearly not on LinkedIn) should score lower than an honest unknown. Don't let extraction's correct refusal to guess become a scoring penalty.

**`is_cro` disqualifications:** Sprint 2 found 23/80 successfully-enriched leads judged `is_cro: false`, despite all being sourced from ICH GCP's own "local/mid-size CRO" section. Plausible and not alarming — directory listings drift (rebrands, vendors miscategorized, defunct domains) and this is enrichment correctly catching what the source didn't (spot-checked `endpointclinical.com` directly: it's an RTSM/IRT technology vendor, not a CRO — correct call). Decided against a one-time manual audit of the full set — see the visibility decision below instead.

**Therapeutic area taxonomy — LOCKED, expanded.** `therapeutic_areas` extraction is constrained to this fixed enum in the Ollama JSON schema (array of strings from this list, not free text), enforced at the schema-grammar level and verified with adversarial testing — this fixes both the drift (`cardiology`/`cardiovascular`, singular/plural) and industry-term leakage (`medtech`, `pharma` simply can't be output once excluded from the enum):

```
Oncology
Cardiovascular
Central Nervous System
Immunology
Infectious Disease
Endocrinology
Metabolic Disorders
Respiratory
Rare Diseases
Internal Medicine
Anesthesiology
Dermatology
Rheumatology
Ophthalmology
Gastroenterology
Nephrology
Hematology
Other
```

Broader than NoblePath's own 6-category list by design — chosen to preserve granularity already showing up organically in real extraction rather than forcing everything into 6 buckets. The last six (Dermatology through Hematology) were added after the initial 12-category build showed `Other` immediately absorbing real volume (dermatology 12, rheumatology 7, ophthalmology 6, gastroenterology 6, nephrology 5, hematology 3, against oncology's 27) — the "recognizable pattern → add a category" signal fired on first contact with real data, not gradually over time. `Other` still exists as a catch-all for genuine long-tail cases.

**Implemented and verified** in the enrichment workflow — grammar-constrained, drift-checked against `code_normalise.js`, adversarial-tested (zero off-enum output). The current 123 leads hold a mixed store: pre-fix rows in lowercase free text, post-fix rows in enum Title Case. **Sprint 3 must match case-insensitively** against this field — a case-sensitive check against old rows will silently miss them. Re-enriching the old rows for taxonomy cleanliness alone is not required (oncology, the only category actually scored, already extracted consistently even before this fix).

**Known, accepted, unrelated:** non-English source text doesn't reliably map into the `phases` field — e.g. Turkish `biyoeşdeğerlik` didn't resolve to `Bioequivalence`. Real gap, low priority, not touched.

**`is_cro` disqualifications — visibility over spot-checking.** Instead of a one-time manual audit, Sprint 3's review queue must surface `scores.disqualify_reason` for disqualified leads, not just hide them — ongoing visibility catches misclassification as it happens, across every batch, not just one.


**ClinicalTrials.gov lookup mechanism (this is where that source lives):** for each candidate that has cleared hard disqualifiers, call `GET https://clinicaltrials.gov/api/v2/studies` with `query.spons=<company_name>` (and `query.locn=<country>` as a fallback if the sponsor-name search misses) and `filter.overallStatus=RECRUITING`. No API key required. A non-zero `totalCount` earns the 20-point weight. This is a per-candidate lookup keyed on a company name/domain we already have — not a bulk discovery source, since the API has no company-website field to key a new lead on.

**Then** Ollama generates a one-paragraph "why this lead" rationale. This rationale is what makes the morning review fast — it must be specific, not generic.

**Then** Apollo lookup for leads scoring ≥ 60 only.

**Shipped as Workflow 3b, a separate queue — deliberate deviation from "inside Workflow 3".** Build rule 4 requires every workflow to be queue-driven and idempotent, and an inline Apollo step is neither: a lead's only chance at a contact would be the same execution that scored it. Nine leads were already sitting at `status='scored'` when this stage was built, scored before it existed; reaching them inline would have meant re-queueing to `enriched` and re-paying for a full re-score (a GPU rationale call and a ClinicalTrials.gov lookup each) to get at a step that costs neither. Every future outage, plan change, or threshold change has that same shape. As its own queue — `status='scored' AND fit_score >= 60 AND no contacts row` — it drains whatever is waiting, whenever it runs. `n8n/contacts/`, workflow id `contacts0001`.

**Endpoint-level detail behind the §7 correction** — measured 2026-09-06, on both the API-key path and the OAuth/MCP path, on an account showing 125 unused lead credits:

| Endpoint | Result |
|---|---|
| `POST /api/v1/mixed_people/search` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `POST /api/v1/people/match` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `POST /api/v1/mixed_companies/search` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `GET /api/v1/auth/health` | 200 `{"healthy":true,"is_logged_in":true}` |

The key is valid and the account is live; the gate is the plan, not the key or its scope. `organizations/enrich` returns a key-scope error rather than a plan error, so it may be reachable if the key's scope is widened — not pursued, since it returns firmographics, not people. Nothing in the stage needs to change if the plan ever allows it: a plan-gated 403 takes the same path as a timeout — nothing written, lead stays `scored`, next run retries.

**The scraped email nobody read — 56% of the first qualifying batch needed no Apollo call at all.** Every ICH GCP company profile carries an `E-mail:` field, and `scrape_ichgcp.py` has always captured it into `data/ichgcp_leads.csv` (92 of 123 rows). Section 8 gives `leads` no email column, so ingestion discarded it at the `INSERT INTO leads` boundary — written, committed, fetched, then dropped. It was never lost, only unread. Five of the nine leads qualifying at ≥ 60 already had an address there — this is the free-data gap the §7 decision leans on.

Workflow 3b therefore re-fetches the CSV (the same `raw.githubusercontent.com` artefact ingestion reads, with the URL parsed out of `ingestion-ichgcp.json` at build time so the two cannot drift) and checks it before any paid call. The two alternatives were both worse: a `leads.email` column changes a schema section this doc locks, and writing the address into `contacts` at ingestion time would attach a contact to an unscored lead — the exact ordering Section 7 forbids.

**A role inbox is not a person.** Four of those five addresses are `info@`/`contact@`-class. Where enrichment also named a founder the name, title and LinkedIn URL are written alongside — but the name is never presented as the owner of that inbox, because no source says it is. Workflow 4 drafts the email and LinkedIn variants separately, so the address and the person feed different channels regardless.

**`contacts.verified` means one thing: the address is confirmed, not inferred.** True for a first-party address the company published about itself, and for Apollo `email_status: verified`. False for Apollo's `guessed` (pattern-derived — real enough to keep, not to send to unchallenged), for a masked `email_not_unlocked@…` placeholder, and for a tombstone. Workflow 6 is the consumer; this is the flag a send gates on.

**Three outcomes, not two, and the third is why re-running is free.** "Apollo refused the call" and "Apollo answered, nobody there" look nearly identical at the node boundary and mean opposite things. A refusal is retried and writes nothing. An answered-but-empty lookup writes a **tombstone** — a `contacts` row with every field null and `verified=false` — because the batch query has no other way to tell "not looked up yet" from "looked up, nothing there", and without it every cron tick re-pays for the same dead domains. The tombstone does not advance the lead: there is no status for "asked and found nothing", and `contact_found` would put a contactless lead in front of Workflow 4's grounding guard. It stays `scored`.

**`contacts.lead_id` is now UNIQUE** (migration `004`), for the third time this lesson has been paid for after `enrichments` (002) and `scores` (003). This locks in one contact per lead rather than a roster per company — which matches `drafts` being per-lead and Section 12's single named buyer, but is a real constraint, not just a de-dupe.

### Workflow 4 — Drafting
**Trigger:** Cron. Batch where `status='contact_found'`.

**Grounding guard — LOCKED.** The drafting prompt may only use facts present in the enrichment record. If the record lacks at least two specific facts (therapeutic area, named trial, city, founder name), the draft is flagged `low-context` and skipped rather than invented.

> This rule exists because of the Nova field-fabrication bug: under forced tool use, Haiku invented a specialty from an email domain. Small models fabricate when under-informed. Design for it.

**Refinement, found in Sprint 4 testing: the failure mode isn't only fabrication, it's joining.** A sentence can use zero invented words and still assert an untrue relationship — "one recruiting oncology trial in Istanbul" traces to two real, separate facts that were never actually linked. Word-level grounding passes this; claim-level grounding doesn't. Lowering temperature (0.7→0.45) reduced fabrication but did not fix joining — that needed prompt structure, not sampling: attach an explicit boundary to each fact rather than listing them freely, cap therapeutic areas mentioned at two (five read as generic, not specific), use second-person trial phrasing (third-person produced "their own material" — an odd distancing artifact), and bar `founder_name`/`city` from the opening line specifically — allowing it produced "Enrique Gaubeca is founder or MD on their site" as the first line of a LinkedIn DM addressed to Enrique Gaubeca.

**A silent wiring failure, not a logic bug — check for this class of thing whenever a node chain is edited.** The first live run reported `"status": "success"` on all four drafts, and every one of them mentioned neither Nova nor NoblePath. The system prompt was set correctly on the Config node, but a later Postgres node in the same chain replaces the item outright rather than merging into it, so `$json.system_prompt` was an empty string by the time the Ollama call read it — the model drafted with no instructions at all, silently, no error anywhere. Unit tests validate logic given correct inputs; they cannot see wiring, and runtime doesn't error on a field that's simply absent. Fixed with a build-time guard: every `$json.X` field the Ollama body reads must be traceable to something `code_assess.js` actually emits, checked at build time, not assumed from the node graph looking right.

**Prompt constraints:** under 80 words, minimum two specific facts, mention the live NoblePath demo, no adjectives like "revolutionary" or "cutting-edge", no merge-tag phrasing tells.

**Output:** email variant + LinkedIn variant per lead into `drafts`.

### Workflow 5 — Review Queue (HUMAN)
**Interface:** NocoDB grid view over `drafts` joined to `leads`, `scores`, `contacts`.
**Sort:** fit_score descending.
**Actions:** Approve · Edit · Reject.
**Rejection requires a reason** (bad fit / bad draft / already contacted / wrong contact) — this is the training data for the learning loop.
**Target:** 20 minutes daily.

**The most serious bug found in this project, do not repeat it.** A Postgres view has no primary key and cannot be given one (error 42809). Without a PK, NocoDB cannot scope a write to one row — a single approve click on one draft went out as an unqualified `UPDATE review_queue SET status='approved'`, and an INSTEAD OF trigger applied it to every row in the view. It then returned an unrelated error, making the mass-approval look like a failed no-op. **This is the dangerous shape: silent full mutation plus a misleading failure signal**, in a workflow whose entire purpose is a human gate before sending. Fix: the view is read-only, so any write attempt fails loud and immediate (error 55000) instead of silently succeeding somewhere. NocoDB writes directly to `drafts` (which has a real PK), with approve/edit/reject validation enforced on `drafts` itself so it holds for every writer, not just NocoDB's UI. Any future change to this view's schema must re-verify this — confirm a real write behaves correctly against live data, never assume a clean join means a clean write path.

### Workflow 6 — Send & Track
**Send trigger:** Cron, business hours only, randomised intervals.
**Guard:** query today's send count; abort if at the warm-up ceiling. Ceiling is enforced in the workflow, not by discipline.
**Send:** SMTP node → mailbox. Log to `outreach_log`.
**Reply detection:** IMAP trigger polls the same mailbox. Any reply → kill follow-up sequence, set `status='replied'`.

**No HubSpot account exists yet — deferred, not dropped.** Original spec created a HubSpot deal on reply; skipped for now rather than routing into the wrong account (this pipeline's sender is Abdullah/Amitrix Labs, not the Fatima/NoblePath HubSpot the doc originally assumed). Substitute, using infrastructure this workflow already builds: replied leads get their own filtered view in NocoDB so they're never buried in the main queue, and a short internal notification email fires to the operator's own inbox (not the outreach mailbox) the moment a reply lands — reusing the same SMTP capability this workflow needs anyway, no new infrastructure. Re-adding a real CRM later is a small addition on top of this, not a rebuild.
**Opt-out detection:** reply matching no/unsubscribe/remove/stop → add domain to `blocklist`, permanent.
**Follow-up:** if no reply after 6 days, generate follow-up draft back into the review queue. Maximum two follow-ups, then mark lost.

**Shipped in Sprint 5 (2026-09-14) as three workflows** — `send0001` (cron → guard → claim → SMTP → log), `mailwatch0001` (IMAP on Sent and on INBOX), `followup0001` (cron → follow-up drafts / lost) — generated by `n8n/sendtrack/build_workflow.py` like every other stage, with migration `007` (see Section 8). Split because IMAP triggers and cron ticks start differently, and so each path can be executed headlessly on its own. **Built and dry-run only: nothing has been sent live, no workflow is published.**

**The warm-up ceiling belongs to the mailbox, not the workflow — derived from the real Sent folder, never assumed.** The operator's manual warm-up sends land in the same Sent folder and count against the same daily limit, so the ceiling is computed exactly as the IMAP pre-flight did it: warm-up starts on the sender-day of the first send with an external recipient, week = ⌊days since ÷ 7⌋ + 1, and only messages leaving `amitrixlabs.com` count. An empty history is "not started": the first send is day 1 of week 1. The day is the sender's (Asia/Karachi, docker-compose's `GENERIC_TIMEZONE`), and each tick uses one clock value throughout, so a count cannot straddle midnight. Mailbox Watch mirrors the Sent folder into `mailbox_sent` (whole folder on every activation, so manual sends made while n8n was down are counted); the send path writes its own sends there the moment SMTP accepts them.

**It fails closed, and that has a consequence worth knowing.** The send path refuses to send if the Sent mirror has never read the folder, or if one of its own sends has not appeared there after 15 minutes — a dead mirror would undercount the manual sends with no error anywhere. The IMAP trigger only fires once a folder holds a message (node-imap emits `mail` on EXISTS growth only), so **nothing sends until at least one message is in Sent and Mailbox Watch is published.** Measured the day this shipped: the IMAP Sent folder held **0 messages** — the manual warm-up sends reported as started that day were not in it when checked. The workflow therefore computes "warm-up not started, week 1, ceiling 5" and currently refuses with `sent-mirror-never-ran`. **Re-measured 2026-09-15:** Sent held 2 messages, both to external domains, both on sender-day 2026-09-15 — so warm-up day 1 is **2026-09-15** (week 1, ceiling 5). The send path still refuses with `sent-mirror-never-ran` until Mailbox Watch is published; now that the folder is non-empty it syncs on activation. Zoho filing SMTP-submitted mail into Sent is assumed, not yet observed; if it does not, the first live send will stop the send path (`sent-mirror-behind`) rather than over-send.

**Claim, then send — at most once.** The draft flips to `sent` and an `outreach_log` row with no Message-ID (a *claim*) is written before the SMTP call, so a crash between sending and logging can never cause a second send; a duplicate cold email is worse than a missed one. The claim re-checks channel, approval, unchanged body, verified contact, blocklist, reply/bounce and **re-counts the ceiling in SQL under an advisory lock taken in a separate, earlier statement** — the Postgres node runs its text as one implicit transaction, and under READ COMMITTED only a later statement's snapshot sees a concurrent claim; in a single statement two overlapping ticks could both count 4 of 5. SMTP refusals are split: account-level (auth, TLS, 4xx) → back to `approved`, retried; recipient-level 5xx → back to `pending` tagged `+smtp-rejected`, for a human. The Send Email node has no retry (a timeout after acceptance would send twice), and `appendAttribution` is forced off — at typeVersion 2.1 n8n appends "This email was sent automatically with n8n" plus a link by default.

**Per-draft checks.** `channel='email'` and `status='approved'` in SQL (Section 6 is enforced three times: the query never selects LinkedIn, Decide Send throws if one arrives, the claim refuses one); `contacts.verified` (the Workflow 3b flag this section said a send gates on); domain and recipient domain not blocklisted; no reply/bounce; first touch only to a lead never emailed, including by hand; not a low-context note even if approved by mistake; the Section 5 opt-out sentence **verbatim, immediately followed by the current signature** — drafts signed "Fatima" existed from before the sender changed, and a stale signature is refused rather than sent under the wrong name; at most one URL. **Business hours are the recipient's:** 09:00–17:00 local, Mon–Fri (Egypt Sun–Thu), fixed standard-time offsets per Section 12 country, **no DST by operator decision** — Poland, Czech Republic, Hungary, Romania and Egypt run an hour later than the table in summer. Pacing (Config, not locked): 10-minute tick, one message at most, 20-minute floor since the mailbox's last external send, then a 40% coin flip.

**Reply classification reads only what they wrote above the quoted thread.** Every reply quotes our email and our email says "reply 'no'" — classify the whole body and every reply is an opt-out. `no` counts only as the first word ("No, thanks", not "we have no chatbot"), also in Portuguese, Polish, Turkish, Hungarian and Romanian; Czech "ne" is left out because it opens ordinary Turkish questions. **Deliberate narrowing of "any reply → replied":** an out-of-office or a bounce is recorded but does not set `replied` — an out-of-office must not kill the follow-up sequence. `inbound_messages` is keyed on Message-ID, so a re-delivered message changes nothing and notifies nobody twice. The operator notification goes to `NOVASCOUT_OPERATOR_EMAIL` (set in `.env` 2026-09-15; baked into Mailbox Watch at build time, so changing it means rebuild, re-import and re-publish); replies also land in the `replied_queue` view. The notification is sent from the outreach mailbox, so it lands in Sent and counts as an external send against that day's warm-up ceiling and the 20-minute pacing floor — consistent with the ceiling belonging to the mailbox, at the cost of one prospect slot per notification.

**Follow-ups are a template, not a model call** — a follow-up adds no fact about the lead, so there is nothing to ground (build rule 6 by construction). They quote the first touch, carry the Section 5 footer so the send path accepts them, and land as `pending`: nothing follows up without a human. A rejected follow-up uses its slot; the clock restarts at the later of the last send and the last follow-up drafted.

**Verified by dry run, not by live send** (`n8n/sendtrack/dryrun/dryrun.py`): the real workflows through n8n against a copy of the database with every contact rewritten to `@dryrun.invalid`, SMTP into a loopback sink inside the n8n container. The build proves the dry-run variants differ from the shipped ones only in credentials, Config clock/pacing and the removed schedule trigger. Proven there: on a week-1 day with 4 external sends the 5th goes through claim → SMTP → confirm and the 6th is refused by the ceiling with eligible recipients at work; approved LinkedIn drafts are never selected; a blocklisted domain is skipped and the next draft goes instead; pending/rejected drafts are never selected and an approved low-context note is refused — and for each, the claim statement run directly also refuses. The live database's fingerprint was identical before and after.

---

## 10. Build Order

Each sprint is one or two Claude Code sessions. New session per sprint. Commit manually.

| Sprint | Deliverable | Exit criteria |
|---|---|---|
| **0** | Docker compose (n8n + Postgres + NocoDB), Ollama installed, model pulled, env vars set, hello-world workflow calling Ollama. **Buy the domain today.** | Ollama responds through n8n |
| **1** | Schema migration + ingestion workflows | 200+ deduped leads in Postgres |
| **2** | Enrichment workflow + regex chatbot detection | 50 leads enriched, hand-verified |
| **3** | Scoring rules + rationale + gated Apollo lookup | Ranked queue with contacts |
| **4** | Drafting workflow + NocoDB review UI. **DNS records live, manual warm-up running.** | Drafts you would actually send |
| **5** | SMTP send + warm-up cap + IMAP reply trigger + follow-ups + HubSpot sync | First real approved batch sent |
| **6** | Metrics as Postgres views in NocoDB | Reply rate, approval rate, source quality visible |

**Timeline:** ~2 weeks of evenings to first sent email. Domain warms in parallel so it is never the blocker.

---

## 11. Build Rules

1. **Domain first.** Buy it and start manual warm-up on day one. It is the only thing with an unavoidable calendar delay.
2. **Export every workflow to git as JSON** after each session. Workflows are code.
3. **Never spend tokens on deterministic work.** Chatbot detection, disqualifiers, dedupe, date math — all Code nodes.
4. **Every workflow is queue-driven and idempotent.** No assumptions about uptime.
5. **Bounded batches.** Never process an unbounded set; the GPU is serial.
6. **Ground every generation.** No fact in a draft that is not in the enrichment record.
7. **Human sends LinkedIn. Always.**
8. **No tracking pixels, ever.**
9. Ask before assuming. Confirm terminal type before giving shell commands (Windows / PowerShell / Git Bash).

---

## 12. ICP Definition

**Target:** CRO founder, Managing Director, or BD Director.
**Company:** 5–100 employees, running active trials, has a website, no existing chatbot.
**Geographies:** Turkey, Mexico, India, Pakistan, Egypt, Poland, Romania, Hungary, Czech Republic, UAE, South Africa, Brazil, Argentina.
**Strongest signal:** oncology focus (matches the NoblePath case study).

**Second case study — not yet reflected in Workflow 4's prompt.** Nova is also live at Vertex Clinical Research (Mexico), not just NoblePath (Turkey). Workflow 4's current prompt constraint ("mention the live NoblePath demo") references only NoblePath regardless of lead geography — a Mexico/Latin America lead would likely find Vertex more credible as a same-region reference than Turkey. Worth making the case-study reference geography-aware once send infrastructure is confirmed working: Vertex for Mexico/LatAm leads, NoblePath for Turkey/nearby, either for everywhere else. Not yet implemented.

**Commercial offer:** $500–1,000 build + $300/month. Monthly, cancel anytime. 48-hour custom demo on their own knowledge base, no commitment.

---

## 13. Open Items

| Item | Owner | Blocks |
|---|---|---|
| Purchase outreach domain | Fatima | Everything downstream of Sprint 4 |
| Zoho Mail mailbox + DNS | Fatima | First send |
| Check ollama.com for a 9B in the Qwen3.6/3.8 generation | Fatima | Sprint 0 (defaults to `qwen3.5:9b` if none) |
| 90-second Nova demo screen recording | Fatima | Referenced in every draft |
| Decide Sonnet 5 escape hatch for drafting | Both | After 2 weeks of real drafts |
| Publish Mailbox Watch in the UI (Sent has held the manual warm-up sends since 2026-09-15, so it syncs on activation), then Send and Follow-Ups | Abdullah | Every Workflow 6 send and every reply notification — the send path refuses until the Sent mirror has read the folder |

---

## 14. Related: Nova Chatbot Model Migration (separate project)

Not part of Nova Scout, tracked here to keep the decision record in one place.

**Proposal:** migrate Nova Agent Kit from Haiku 4.5 to Sonnet 5.

**Cost reality:** Sonnet 5 is $2/$10 vs Haiku 4.5 at $1/$5 — double, not cheaper. At Nova's volume with a ~17K-token context-stuffed system prompt on every turn, this is a real increase, not noise. Still comfortably absorbed by the $300/month per-tenant fee.

**Do this first, regardless of model choice:** enable prompt caching on the static system prompt. Cache hits bill at 10% of base input. On a 17K-token system prompt sent every turn, this cuts the input bill by roughly 90% — a far larger lever than model selection, and it makes the Sonnet upgrade close to cost-neutral.

**Migration risks — test before deploying:**
- The reliability scaffolding (forced `tool_choice`, keyword triggers, `request_missing_information` guard) was engineered against Haiku 4.5's specific failure modes. Sonnet 5 fails differently. It may allow removing some scaffolding — verify, don't assume.
- Re-test the field-fabrication guard specifically. That bug drove real design decisions.
- Check whether extended thinking is on by default. Thinking tokens hurt time-to-first-token, and the widget UX depends on the response feeling instant.

**Rollout order:** Vertex staging tenant → all six flows end to end → NoblePath production. Never NoblePath first.

---

*Document version 1. Update when any architectural decision changes. Do not let sessions drift from this spec.*
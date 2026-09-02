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
| Contact data | Apollo.io API | Free tier, credits conserved by pipeline order |
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

1. **Override the default `presence_penalty`.** Qwen3.5 ships with `presence_penalty: 1.5`. That penalises repeated tokens — and JSON is nothing but repeated structural tokens (braces, quotes, recurring field names). High presence penalty produces malformed JSON. For extraction and scoring nodes set `presence_penalty: 0`, `temperature: 0.1`. For drafting, `temperature: 0.7`, `presence_penalty: 0.3`.

2. **Cap the context window at 16K–32K.** These models advertise 262K native context. Do not use it — KV cache at full context adds 4–8 GB and will push you off the GPU. Scraped pages are 5–20K tokens; 32K is generous.

3. **Confirm thinking mode is off.** Qwen3.5 small models disable thinking by default, but verify. Reasoning traces on a batch extraction job multiply token cost and wall-clock time for zero benefit on structured extraction.

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
| Apollo | $0 (free tier, protected by pipeline order) |
| **Total** | **~$22/year + electricity** |

Any proposed change that introduces recurring cost must be justified against this baseline.

---

## 5. Email Infrastructure — LOCKED

**Domain:** dedicated outreach domain, purchased separately. Never `noblepathcro.com` (client's reputation), never a personal Gmail (ToS violation + account risk).

**Mailbox:** a named human address — `fatima@<domain>`, not `hello@` or `info@` or `sales@`.

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

**Critical ordering rule:** Apollo contact lookup happens AFTER scoring, never before. Scrape and score for free with local models first; spend Apollo credits only on leads above threshold. This is what keeps Apollo on the free tier.

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
  id, lead_id FK, channel, sent_at, message_body,
  replied (bool), replied_at, reply_body, outcome

blocklist
  domain (UNIQUE), reason, added_at
```

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

**Sprint 1 outcome, validated end-to-end:** first real Action run — no 403s, the IP-reputation WAF risk did not materialize. Failures were 429 (rate limit) and 404 only. 12 of 13 countries succeeded; Poland's country page got 429'd, producing zero Poland leads (retry-with-backoff fix pending/applied — check scraper for current state). 107 unique leads landed in `novascout.leads`, verified against the live DB.

**Known, accepted limitation:** multi-country CROs (same domain listed on several country pages) collide on `ON CONFLICT (domain) DO NOTHING` — whichever country the scraper processes first for a given domain wins the `leads.country` value; later countries lose that lead to the collision. This undercounts some countries' per-scrape totals versus what lands in the DB (observed: Hungary 8→6, UAE 3→2). Correct behavior for avoiding duplicate lead rows; accepted as-is at current scale (~3% of leads affected). If country-level accuracy becomes material later (e.g. scoring's geography weight, or per-country reporting), the fix is a `lead_countries` join table rather than a single `leads.country` column — not implemented, revisit only if it matters in practice.

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
- Employee estimate > 500 (enterprise CRO)
- No functioning website
- Not actually a CRO (agency, consultancy, vendor)
- Domain on blocklist

**Weighted fit score (0–100):**
| Factor | Weight |
|---|---|
| Target geography | 25 |
| Active trials on ClinicalTrials.gov | 20 |
| Founder/MD identified with LinkedIn | 20 |
| Oncology focus (strongest case study) | 15 |
| Employee count 5–100 | 10 |
| Site quality suggests budget | 10 |

**ClinicalTrials.gov lookup mechanism (this is where that source lives):** for each candidate that has cleared hard disqualifiers, call `GET https://clinicaltrials.gov/api/v2/studies` with `query.spons=<company_name>` (and `query.locn=<country>` as a fallback if the sponsor-name search misses) and `filter.overallStatus=RECRUITING`. No API key required. A non-zero `totalCount` earns the 20-point weight. This is a per-candidate lookup keyed on a company name/domain we already have — not a bulk discovery source, since the API has no company-website field to key a new lead on.

**Then** Ollama generates a one-paragraph "why this lead" rationale. This rationale is what makes the morning review fast — it must be specific, not generic.

**Then** Apollo lookup for leads scoring ≥ 60 only.

### Workflow 4 — Drafting
**Trigger:** Cron. Batch where `status='contact_found'`.

**Grounding guard — LOCKED.** The drafting prompt may only use facts present in the enrichment record. If the record lacks at least two specific facts (therapeutic area, named trial, city, founder name), the draft is flagged `low-context` and skipped rather than invented.

> This rule exists because of the Nova field-fabrication bug: under forced tool use, Haiku invented a specialty from an email domain. Small models fabricate when under-informed. Design for it.

**Prompt constraints:** under 80 words, minimum two specific facts, mention the live NoblePath demo, no adjectives like "revolutionary" or "cutting-edge", no merge-tag phrasing tells.

**Output:** email variant + LinkedIn variant per lead into `drafts`.

### Workflow 5 — Review Queue (HUMAN)
**Interface:** NocoDB grid view over `drafts` joined to `leads`, `scores`, `contacts`.
**Sort:** fit_score descending.
**Actions:** Approve · Edit · Reject.
**Rejection requires a reason** (bad fit / bad draft / already contacted / wrong contact) — this is the training data for the learning loop.
**Target:** 20 minutes daily.

### Workflow 6 — Send & Track
**Send trigger:** Cron, business hours only, randomised intervals.
**Guard:** query today's send count; abort if at the warm-up ceiling. Ceiling is enforced in the workflow, not by discipline.
**Send:** SMTP node → mailbox. Log to `outreach_log`.
**Reply detection:** IMAP trigger polls the same mailbox. Any reply → kill follow-up sequence, create HubSpot deal, notify.
**Opt-out detection:** reply matching no/unsubscribe/remove/stop → add domain to `blocklist`, permanent.
**Follow-up:** if no reply after 6 days, generate follow-up draft back into the review queue. Maximum two follow-ups, then mark lost.

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
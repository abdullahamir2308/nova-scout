# Nova Scout — Master Reference Document v2
## AI-Automated BD Pipeline for Nova Agent Kit

> Single source of truth for architecture, stack, and build decisions. Paste it into the Claude Project tab before every build session.
> Do not deviate from anything marked **LOCKED**. Ask before changing anything marked **CONFIRMED**.
> **v2 — condensed 2026-10-18** from v1 (1,230 lines). Incident write-ups, per-run logs, dry-run transcripts, superseded decisions and finished open items were removed. Every rule, decision and number that still governs the build was kept. Facts are as last recorded on 2026-10-10; check live state with the §3 publish signals and the database, not with this file.
> **The build scripts parse this file.** Do not reword these blocks without changing the generator and its drift-guard test together: the §3 model and caching tables; the §4 website-lookup table and cap; the §5 warm-up table, first-touch word limit and opt-out sentence; in §9, Workflow 3's company-type disqualifier sentence, weights table, Trialsites bands table, taxonomy list and the "Apollo lookup for leads scoring ≥ 50 only" sentence; and in §12, the geography lines, the excluded list and the clock table. After any edit run the drift-guard suites (`n8n/*/test_drift_guards.py`, `scraper/test_geography.py`, `scraper/test_shards.py`).

---

## 1. Project Overview

**What we are building:** An agentic outbound pipeline that finds small-to-medium CROs worldwide — and, since 2026-10-09, independent clinical research sites and SMOs (§9 Workflow 1b; §12) — enriches and scores them against our ICP, drafts personalised outreach, and sends and tracks it. Since 2026-10-03 an email draft that passes every check approves itself; a human reviews the exceptions queue (what the checks held, plus every LinkedIn DM) and reads a daily digest of what went out (§9 Workflow 5).

**Why:** Nova Agent Kit is built and live at NoblePath CRO (Türkiye) and Vertex Clinical Research (Mexico, a research center, not a CRO). The remaining problem is purely distribution. Manual prospecting does not scale; this replaces ~8 hours/day of manual work with a short morning pass over the exceptions queue and the digest.

**Secondary goals (equal weight):**
- Learn n8n, self-hosted agent orchestration, and local LLM inference
- Produce a portfolio-grade project
- Run at near-zero cost

**Endgame:** Nova Scout retargeted (CRO → pharma sponsor) becomes a second sellable product to existing Nova clients. Not before it has produced our own first 10 clients.

**Identity:** sender is Abdullah Amir, Founder, **Amitrix Labs** (not "Amitrex"), `abdullah@amitrixlabs.com`.

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

**Machine:** RTX 4060 Ti 16 GB VRAM · Ryzen 5 3600 · DDR4-3000. **Binding constraint:** memory bandwidth (~288 GB/s), not compute. A local model must fit entirely in 16 GB VRAM; CPU offload onto DDR4-3000 is unacceptably slow.

**Model rule — ONE LOCAL MODEL FOR THE ENTIRE GPU PIPELINE.** Different models per node make Ollama evict and reload weights on every switch; load time exceeds generation time.

**LOCKED CHOICE: `qwen3.5:9b` at Q4_K_M (6.6 GB)**, ~9 GB headroom for KV cache. Used for enrichment and scoring only, permanently — those are the high-volume nodes. Never step up to a larger local parameter count on this card; a better quant of a smaller model beats a starved larger one.

**What does NOT fit on 16 GB — do not attempt:**


| Model | Q4 footprint | Verdict |
|---|---|---|
| Qwen3.5/3.6/3.8 **27B** | ~15–17 GB | Exceeds VRAM before KV cache. Partial CPU offload on DDR4-3000 → unusable batch speed. |
| Qwen3.5/3.6 **35B-A3B** (MoE) | ~19 GB | All 35B must load even though only 3B activate. Does not fit. |
| Any 27B at Q3 | ~13 GB | Fits, but quantization damage makes it worse than a comfortable 9B. |

There is no 14B in the current Qwen generation (sizes: 0.8B / 2B / 4B / 9B / 27B / 35B-A3B / 122B-A10B / 397B-A17B); a 3.5-generation 9B beats an older-generation 14B.

**Where each model is used:**
1. `qwen3.5:9b` — enrichment and scoring (local, free).
2. `qwen3.5:9b-q8_0` — skipped (2026-10-02): drafting skill v3 went straight to an API model, because the 9B needed verbatim line-picking to stay safe and a better quant would not change that.
3. **Claude Sonnet 5.5** — the drafting node (since 2026-10-02), follow-ups (2026-10-02), the **claim check** that gates auto-approval and its repair loop (2026-10-03), and reply drafts (2026-10-09). The claim check always uses the same model and parameters as drafting; the build refuses a cheaper check model or effort.
4. **Claude Haiku 5.5 + web search** — website lookup for Trialsites candidates only (§4, Workflow 1b).

**Rule for model selection:** pick by capability where volume is low, by price only where volume is high. Never route the high-volume enrichment or scoring nodes to a paid API.

**Drafting model: `claude-sonnet-5-5`**, effort `high`, through the Anthropic Messages API, $2/$10 per MTok (cache reads **$0.10**, not $0.20 — corrected 2026-10-08 from the live pricing page: Sonnet 5.5 and Opus 5.5 are documented exceptions at 0.05x base input, where every other model is 0.1x). Drafting node and the follow-up composer only (Section 9, Workflow 6); enrichment and scoring stay on local `qwen3.5:9b`, and the one-model rule above still governs everything that runs on the GPU. Request parameters were taken from the live Sonnet 5.5 docs on 2026-10-02 (released 2026-09-28; confirmed present with `GET /v1/models/claude-sonnet-5-5`), not carried over from older settings: no `temperature`/`top_p`/`top_k` (a non-default value is a 400 on this model, so the old drafting `temperature: 0.45` cannot be sent); `thinking` omitted, which runs adaptive thinking; effort set explicitly to `high`, the documented starting point for work that is neither agentic nor latency-sensitive; JSON through `output_config.format` with a strict schema; `max_tokens` 16000, because it covers thinking as well as text; and **prompt caching on since 2026-10-08** (below). The key is the n8n credential `novascoutAnthropic01`, made from `ANTHROPIC_API_KEY` in `.env` by `n8n/drafting/provision_anthropic_credential.py` — a Nova-Scout-only key since 2026-10-02 (re-provisioned that day; `GET /v1/models/claude-sonnet-5-5` answered 200 with it). **If a call fails, the lead stays queued for the next run. There is no fallback to the local model** (skill v3 §6), so the quality of what reaches review does not depend on which model answered.

**Sonnet 5.5 costs 2× Haiku 4.5** ($1/$5), not less; it is chosen because drafting volume is 10–20/day and capability wins at that volume. Measured cost is in §4.

**Prompt caching — ON since 2026-10-08, one breakpoint per call.** Settings were read from the live docs, not carried forward:

| What | Value | How it was established |
|---|---|---|
| Minimum cacheable prefix | **512 tokens** for `claude-sonnet-5-5` | Live prompt-caching docs. **Not monotonic across generations** — 512 here, 1,024 on Sonnet 5, 2,048 on Opus 4.7, 4,096 on Opus 4.6 and Haiku 4.5 — so it must be re-read per model, never assumed to fall over time. A prefix under the minimum caches nothing **and raises no error**. |
| Lifetime | **5 minutes** default; `"ttl": "1h"` for an hour | Live docs. Measured from the **start** of the request that writes or reads, so generation time counts against it. A read refreshes the timer free. |
| Request format | `cache_control: {"type": "ephemeral"}` on a content block; at most 4 breakpoints; render order `tools` → `system` → `messages` | Live docs |
| Write price | 1.25× base for 5 min ($2.50/MTok), 2× for 1 h ($4/MTok) | Live pricing page |
| Read price | **0.05× base = $0.10/MTok** | Live pricing page. This doc said $0.20 until today; that is Sonnet 5's rate. |
| Verification fields | `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, and `usage.cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` | Live docs; read out of n8n's own execution records by `n8n/drafting/cache_report.py` |

**One breakpoint, on the last (only) system block, at every call site.** The system prompt is the only text byte-identical across leads and runs; everything per-lead is in the user message after it. The cached prefix is bigger than the prompt, because `output_config.format`'s schema renders into it.

| Call site | System prompt | Cached? |
|---|---:|---|
| Drafting (`code_assess.js`) | 1,825 tok | yes — 2,224 with the schema |
| Claim check (`code_approval.js`) | 1,080 tok | yes — 1,522 with the schema |
| Follow-up composer (`code_followup.js`) | 1,169 tok | yes |
| **Repair** (`code_approval.js`) | **448 tok** | **no — under the 512 minimum.** It would cache nothing, raise no error, and still bill the write premium on every repair. Padding a prompt to win a few cents is the wrong trade. |

- **The 5-minute TTL is a measured choice.** Calls arrive in bursts of two runs 0.1–2 minutes apart, and the bursts are ~1 hour or more apart; a 5-minute entry catches every read that happens at a 1.25× write, a 1-hour entry costs 2× and would only earn more on a coin flip. Both generators pin the value.
- **Pre-warming is impossible:** the documented `max_tokens: 0` warm-up is rejected together with structured outputs (`400 output_config.format cannot be set when max_tokens is 0`), and `format` is part of the cached prefix.
- **Caching cannot change what a draft says:** `cache_control` is a billing instruction about a prefix. The generators assert exactly one breakpoint, on the system block, never in `messages`, with the 448-token repair prompt left bare; seven mutations of those rules are each proven to refuse the build.
- The batch-split optimisation that would add within-batch reads was built and rejected (§9 Workflow 4).

**Ollama environment variables (Windows system env vars):**
```
OLLAMA_KEEP_ALIVE=-1        # never unload the model
OLLAMA_NUM_PARALLEL=1       # serial processing, avoid VRAM contention
OLLAMA_HOST=0.0.0.0:11434   # reachable from Docker containers
```

**Structured output:** always use Ollama's `format: json` with an explicit schema for extraction and scoring nodes. Never parse free text. Run `ollama ps` after loading and confirm 100% GPU, not a CPU/GPU split.

**Inference tuning — three gotchas (tested):**
1. **Override `presence_penalty`.** Qwen3.5 ships at 1.5; the failure is silent, field-level data loss on copy-from-source fields (`founder_linkedin`, `therapeutic_areas` came back null even when present), hidden because grammar-constrained JSON is always syntactically valid. For extraction and scoring set `presence_penalty: 0`, `temperature: 0.1`.
2. **Cap the context window at 16K–32K.** The models advertise 262K; full context adds 4–8 GB of KV cache and pushes you off the GPU. Pages are 5–20K tokens.
3. **Use `/api/generate` with `think:false`, not `/api/chat`.** On `/api/chat`, `think:false` silently disables `format` schema enforcement; omitting `think` burns the budget on reasoning and returns empty content. `/api/generate` honours both together (~5 s). Re-verify on any new model/endpoint before copying the setting forward.

**Parallelization:** only the GPU call needs to be serial. Fetch/I/O steps should run in parallel (5 at a time cut a 305 s batch to 46 s). Parallelize lookups, serialize the model call.

**n8n operations (2.35.x) — hard-won:**
- **There is no "Active" toggle — use Publish, in the UI.** `publish:workflow` from the CLI leaves a half-state (`active` flips, but no trigger is registered and the cron never fires). `n8n execute --id=…` ignores published state.
- **Publishing locks to a version, and importing unpublishes.** Re-publish after every rebuild and re-import. An import sets `active=false` in the database but does **not** stop the old trigger in the running process: the old in-memory version keeps firing until the new one is published or n8n restarts (if n8n restarts first, the workflow stays off until published).
- **Real activation signals** (the publication-service tables stay empty on this instance and prove nothing): `workflow_entity.active='t'`, `activeVersionId` non-null, `triggerCount` equal to the workflow's trigger-node count, a new `workflow_publish_history` row, and — the only conclusive proof — an execution with `mode='trigger'`. `triggerCount` is a count, not a flag (Mailbox Watch has two IMAP triggers and reads 2); 0 is not evidence of a broken node on a never-activated workflow.
- **Those signals prove a publish happened, not that triggers are connected now.** When a trigger fails (an IMAP socket closed by the server), n8n drops all of that workflow's triggers from memory, writes nothing to the database, and retries with exponential backoff capped at 24 hours (`WORKFLOW_REACTIVATE_MAX_TIMEOUT`). Only a later `mode='trigger'` execution, or the log line `Activation of workflow "…" was successful!`, shows recovery.
- **One-off `docker exec … n8n execute` against the running container** needs `-e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false`.
- **Schedules:** see §7 (never `hours`/`days`/`weeks` above 1, never a pinned slot).
- **Code-node sandbox:** `require('crypto')` is refused; BigInt, Buffer and TextEncoder exist; `$env` is blocked on purpose (config goes in the `settings` table).
- **Postgres node replaces the item** rather than merging into it, so a field set earlier is gone unless re-emitted. The HTTP node fires a batch's requests concurrently; its `batchSize`/`batchInterval` only sleeps between dispatches and does not wait for responses.
- **`pairedItem` lineage collapses** when a lane is fed from a one-item node; `$('Node').item` then resolves the wrong lead silently (§9 Workflow 4).
- On this host avoid `pkill` (the sandbox intercepts it); an `rm` exit 0 inside the n8n container's `/tmp` once did not remove the file.
- **Testing never sends.** Any test or probe that imports `smtp_send_server.py` or `imap_preflight.py` must blank every SMTP/mailbox variable by plain assignment (never `os.environ.setdefault`, a no-op on an exported credential) and verify no reachable host remains before importing. A 2026-10-08 test sent one live message to a real prospect (lead 26) this way. The rule is standing in `prompts/_preamble.md`.

---

## 4. Cost Model — LOCKED

| Item | Cost |
|---|---|
| Outreach domain | ~$10/year |
| Zoho Mail Lite mailbox | ~$12/year |
| n8n, Postgres, NocoDB, Ollama | $0 |
| ClinicalTrials.gov API | $0 |
| Trialsites (AIMR site database) API | $0 — no key, 60 requests/min. Its API page says "Free for non-commercial use"; using it for outreach is an open operator decision (Section 13) |
| Apollo | $0 — but see correction below |
| Claude API — drafting node (Sonnet 5.5, $2/$10 per MTok, since 2026-10-02) | **$0.028 per drafted lead, measured** (4 leads, 13,605 input + 8,613 output tokens = $0.1133; thinking ≈ 77% of output). A low-context lead costs $0 — it never reaches the model. At 10–20 drafted leads/day: **~$8.50–$17/month**; effort is the lever. **Follow-ups:** $0.0176 each, at most two per emailed lead. **Claim check** (one call per email draft that passes the deterministic rules; none for a LinkedIn DM, a low-context note, a tagged draft, or with auto-approval off): **$0.0170 per check**, ≈ +60% on a first touch. **Repair** (only for a widened/unsupported hold, at most 2): ~$0.005 each plus the repeated check. A repaired draft costs $0.030 (follow-up) to $0.059 (first touch) in checks and repairs, against $0.010–0.024 for one that passes first time. |
| Claude API — website lookup for Trialsites candidates (Haiku 5.5 + web search, since 2026-10-09, Workflow 1b) | **$0.0179 per lookup, $0.0469 per resolved site, measured**; ~$3.60/week at the full 200 candidates, **hard cap $20 a calendar month** (`settings.site_lookup_monthly_cap_usd`) |
| **Total** | **~$22/year + electricity + the drafting API line above (~$100–$200/year at full volume) + the website lookup (~$185/year at full volume, never more than $240)** |

**Prompt caching, measured 2026-10-08 on real runs:** output tokens are 74% of the Sonnet bill and caching cannot touch them, so ~15% was always the ceiling. Per call: $0.02369 uncached (206 production calls) → $0.01928 fully warm (**−18.6%**; drafting −14.2%, claim check −18.2%, repair 0%). A first touch moves from ~$0.028 to ~$0.023 warm; ~$7–$14/month instead of $8.50–$17. Effort, not caching, is the real lever.

**Website lookup for Trialsites candidates (Workflow 1b) — the one Claude call that is not Sonnet 5.5.** A Trialsites site has no website field, so each candidate costs one Claude call with server-side web search; code then fetches the proposed site and accepts it only if the candidate's name and city are on it. It is the highest-volume paid call (up to 200 a week), so it was measured on both models before choosing:

| Website lookup setting | Value |
|---|---|
| Model | `claude-haiku-5-5` |
| Effort | `low` |
| Web search tool | `web_search_20250305`, `max_uses` 3 |
| Input / output | $0.10 / $0.50 per MTok |
| Cache read / write | $0.01 / $0.125 per MTok |
| Web search | $0.01 per search |
| Monthly cap | `settings.site_lookup_monthly_cap_usd`, seeded $20 |

Prices read from the live pricing page on 2026-10-09. `web_search_20250305` because the dynamic-filtering `_20260209` variant is Opus/Sonnet only. Measured on 21 real candidates, each answer then fetched and checked by `code_verify_site.js`: Haiku 5.5 resolved 8, Sonnet 5.5 resolved 8 (they agree on 7), both refused the made-up control name; Haiku cost $0.0179 per lookup, Sonnet $0.0569 — 3.2× for the same yield. About 90% of a Haiku lookup is the search fee, so the lever is `max_uses`, not the model. The cap stops spend at $20 in any calendar month (UTC): `site_lookup_budget_left()` is the one definition of what is left, and the batch query asks for at most `budget_left ÷ $0.04` lookups (the worst case), so a batch is never sized past the cap.

Any proposed change that introduces recurring cost must be justified against this baseline.

---

## 5. Email Infrastructure — LOCKED

**Domain:** dedicated outreach domain `amitrixlabs.com`. Never `noblepathcro.com` (client's reputation), never a personal Gmail (ToS violation + account risk).

**Mailbox:** a named human address, not `hello@`/`info@`/`sales@`. Currently `abdullah@amitrixlabs.com` (Zoho). Sender identity for drafting: Abdullah Amir, Founder, Amitrix Labs, +923178485713 — set via `NOVASCOUT_SENDER_NAME`/`_TITLE`/`_PHONE`, not hardcoded.

**DNS (all three required before first send):** SPF authorising Zoho, DKIM signing enabled, DMARC policy record.

**Warm-up schedule — enforce in the workflow, not by memory:**

| Period | Max sends/day |
|---|---|
| Week 1 | 5 |
| Week 2 | 10 |
| Week 3 | 15 |
| Week 4+ | 20 (hard ceiling) |

**Message rules — LOCKED:**
- Plain text only. No HTML, no images, no logo, no banner.
- **No open tracking. No link tracking. No pixels.** Reply rate is the only metric.
- Maximum one plain URL. No buttons.
- Signature: name, one line of title, phone. Nothing else.
- No marketing unsubscribe footer. Use a plain sentence: *"If this isn't relevant, reply 'no' and I won't follow up."*
- Irregular send intervals within the recipient's business hours. Never a synchronised burst.
- First touch: body 70–110 words, never more than 125 (drafting skill v3; was under 80 words through v2). The body is everything between the greeting and the opt-out line.

**Compliance:** GDPR and KVKK both apply across target geographies. Basis is B2B legitimate interest, defensible only if opt-out is trivial and honoured instantly. The IMAP workflow must detect "no", "unsubscribe", "remove", "stop" and blocklist the domain immediately and permanently.

**Operator commands by email** (§9 Workflow 7) are accepted only when SPF and DKIM pass and the sender equals `settings.operator_email`; this is a CHECK on `operator_commands`, not only a code path.

---

## 6. LinkedIn — LOCKED

**LinkedIn sending is manual. Always. No exceptions.** LinkedIn bans automation, and the account is a business asset that cannot be replaced. The system drafts the DM and provides a copy button plus a deep link to the profile; a human reads it, edits if needed, and sends it by hand. A LinkedIn draft never auto-approves.

---

## 7. Pipeline Architecture

Six workflows, each independent, each queue-driven.

```
Sources → 1 Ingest → 2 Enrich → 3 Score → [Apollo] → 4 Draft → 5 Approve → 6 Send → CRM
                                                                         ↑   (auto, or HUMAN  │
                                                                         │   for exceptions)  │
                                                                         └──── learn ─────────┘
```

**Step 5 is no longer a human gate on every draft (2026-10-03, migration `014`).** An email draft that passes the deterministic checks and the Sonnet 5.5 claim check approves itself inside Workflow 4 (first touch) or Workflow 6's Follow-Ups; everything else, and every LinkedIn DM, waits for a human (§9 Workflow 5).

**Apollo is not usable.** The Free plan returns 403 `API_INACCESSIBLE` on every people/company endpoint regardless of credit balance (§9 Workflow 3b); credits in the dashboard are a UI allowance, not an API entitlement. **Decision: don't upgrade Apollo.** Contacts come from free sources (scraped ichgcp email, an address published on the lead's own site, founder LinkedIn); a lead with none is a two-minute manual look-up in `needs_manual_contact`.

**Ordering rule:** contact lookup happens AFTER scoring, never before, to avoid lookups on disqualified leads.

**Idempotency rule — LOCKED:** the host will be off some of the time. No workflow may assume its schedule fired. Every workflow queries Postgres for "oldest lead in my input status" and processes a bounded batch; a machine off all weekend catches up on Monday. No time-critical webhooks anywhere.

**Schedule audit (against the installed n8n 2.35.7, not the docs):**

| Workflow | Trigger | Catches up? |
|---|---|---|
| Ingestion | was `days`/1 at 23:00 → **now `minutes`/30 + the CSV-fingerprint gate** | **fixed** — it was the one real violation |
| Enrichment | `minutes`/30 | yes — queue-driven, `status='ingested'` |
| Scoring | `minutes`/30 | yes — queue-driven, `status='enriched'` |
| Contact Lookup | `hours`/1 | yes — see the correction below |
| Drafting | `minutes`/30 | yes — queue-driven, `status='contact_found'` |
| Send | `minutes`/10 | yes — queue-driven; the ceiling and the recipient's business hours are re-counted every tick |
| Follow-Ups | `minutes`/30 | yes — since 2026-10-02, when the 6-hour rule was found skipping ticks |
| Daily Digest | `minutes`/30 | yes — sends at the first tick **at or after** 08:00 operator time, once per operator day, covering from where the last digest stopped |
| IMAP Health | `minutes`/20 | yes — stateful in `mailbox_health`, and two consecutive failures are needed to alert |
| Mailbox Watch | IMAP triggers, no schedule | n/a — but it cannot report its own absence; that gap is Section 9, Workflow 6 |

- **`hours`/`days`/`weeks` above 1 are unsafe.** n8n gates an "every N hours" rule on the clock *hour* of the last run, not elapsed time, so a tick on the same clock hour days later reads as zero hours elapsed and is skipped (this cost three follow-ups). `hoursInterval` / `daysInterval` / `weeksInterval` of exactly 1 add no recurrence check and are safe; `minutes` is counted on an absolute minute count and is safe.
- **A fixed slot is also unsafe.** `triggerAtHour`, `triggerAtDay`, `triggerAtDayOfMonth` or `triggerAtMinute` pin the cron to one moment a day, which a host that is off at that moment never meets. Every generator refuses, at build time, any schedule that is `hours`/`days`/`weeks` above 1 or that pins one of those fields.
- **A workflow whose input is a file, not a queue** (Ingestion) ticks every 30 minutes and does its work when the committed CSV's fingerprint has changed (`ingest_log`). The Daily Digest is built the same way: the hour is a floor, not a slot.
- **What none of this fixes:** when the host or Docker is down nothing runs, including the health check. Docker Desktop's `AutoStart` is `false` (§13).

---

## 8. Database Schema

**Location:** the single Postgres container hosts two databases — `n8n` (n8n's own state, owned by n8n; never point application workflows at it) and `novascout` (everything below). Migrations `001`–`019` are applied by hand. **Rules live on the tables, not only in the workflows** (migration `006`/`014` principle): a rule on `drafts` or `claims_library` holds for every writer, including the NocoDB grid and psql.

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
  reject_reason, edited_body, created_at,
  approved_by (auto|human), approved_at, hold_reason, claim_check jsonb

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

mailbox_health
  check_name (PK: imap), healthy (bool),
  problems[] (checker-unreachable|imap-failed|inbox-missed|sent-missed),
  detail, consecutive_failures, failing_since, last_checked_at, last_ok_at,
  last_alerted_at, alerted_problems[]

settings
  key (PK: operator_email|auto_approve_email|site_lookup_monthly_cap_usd), value, updated_at

digest_log
  digest_day (PK), covers_from, covers_to, sent_at, summary jsonb

claims_library
  code (PK), slot (description|angle|benefit|proof|ask|link), body, countries[],
  capabilities[], company_types[] (CRO|site|SMO), measured (bool), active (bool), confirmed (bool),
  note, updated_at

contact_attempts
  lead_id (PK), attempts, last_outcome (exhausted|refused), detail,
  first_at, last_at

ingest_log
  csv_fingerprint (PK), ingested_at, rows_in_csv, leads_before, leads_after

site_candidates
  location_id (PK, Trialsites), canonical_name, city, state, country, trialsites_country,
  site_tier (A|B), trial_count, recent_trials_3yr, active_recruiting, quality_score,
  facility_type, network_type, primary_ta, therapeutic_areas, priority, harvested_at,
  lookup_status (pending|resolved|duplicate|no-match|not-confirmed|excluded|call-failed),
  lookup_attempts, lookup_detail, looked_up_at, proposed_url, website_url, domain, lead_id FK

site_lookup_log
  id, location_id FK, called_at, model, input_tokens, output_tokens, cache_read_tokens,
  cache_write_tokens, web_search_requests, cost_usd, outcome

site_harvest_log
  id, harvested_at, responses, rows_seen, eligible_new, chosen, stats jsonb
```

**Constraints worth keeping:** `enrichments.lead_id`, `scores.lead_id` and `contacts.lead_id` are UNIQUE (one row per lead, enforced with upserts — migrations `002`–`004`; if the schema is rebuilt, don't lose them). `leads.updated_at` is **not** a status-change timestamp (Ingestion's upsert touches every row); use `inbound_messages.processed_at`, `outreach_log.replied_at`, `drafts.approved_at` or `digest_log`.

**Migration notes (what each added and why):**
- **`007` — Send & Track.** `outreach_log.message_id` NULL means a *claim*, written before the SMTP call so a crash can never cause a second send; `draft_id` records which draft went out. `mailbox_sent` mirrors the Sent folder because the warm-up ceiling belongs to the mailbox (manual sends count). `inbound_messages` records every INBOX message once, keyed on Message-ID, which makes reply handling idempotent. `replied_queue` is a read-only NocoDB view.
- **`008` — `settings`.** Operator-owned runtime values (`operator_email`, later `auto_approve_email`, `site_lookup_monthly_cap_usd`); written from `.env` by `n8n/sendtrack/sync_settings.py`, read at runtime, never baked into workflow JSON.
- **`009` — `mailbox_health`.** The IMAP health check's memory (one row, `check_name='imap'`); a CHECK keeps `problems` to four codes.
- **`010` → `012` → `013` — `claims_library`.** The approved claims the drafting model may use; the table is the library, `NovaScout_DraftingSkill.md` §4 is only the seed. `012` (skill v3) changed the slots to `description`, `angle`, `benefit`, `proof`, `ask` (+ optional `link`), with CHECKs for what any claim may say: no line names the product (the model adds "(we call it Nova)" once), "AI" only in a description and at most once, never "AI-powered"/"chatbot", no guarantee, nothing calling the prospect a sponsor, an ask is exactly one question. `013` settled the lines the code reading questioned: Nova **sends a booking link** (it does not book); the knowledge base is **the scraped website only** (no SOPs or client documents); no "not in another dashboard". `capabilities[]` (closed vocabulary: answers, qualifies, captures-lead, study-details, booking-link, deck, routes-leads, dashboard, configured) is required on every description and benefit line and drives the no-repeat rule. Geography CHECK, link rules (one plain URL, never LinkedIn/PDF) and the measured-proof rule are unchanged. On a proof line `countries` is the geography match (§12).
- **`014` — auto-approval.** `settings.auto_approve_email` (on by default; `auto_approve_email_enabled()` is the one reader); `drafts.approved_by` (`auto`|`human`), `approved_at`, `hold_reason`, `claim_check`. The trigger `drafts_approval_rules` turns any `auto` approval that breaks a rule into a hold (`db-guard: …`), stamps every other approval `human`, and un-approves an auto draft whose text is rewritten. On `claims_library` a trigger un-confirms a confirmed line whose text, slot, countries, capabilities or company types change. `digest_log` is the Daily Digest's memory.
- **`015` — `contact_attempts`, `ingest_log`.** Each a workflow's memory of what it has done (§9 Workflow 3b, Workflow 1). Read-only view `needs_manual_contact`.
- **`016` —** the `no-send-clock/` variant is held with that word (§9 Workflow 4).
- **`017` — reply-assistant foundations.** Thread headers on `inbound_messages`, plus:

```sql
inbound_messages  (+ 3 columns)
  in_reply_to, references_raw, thread_ids text[]

reply_approvals
  code (PK: NS-XXXXXXXXXX), kind (reply|email-hold), draft_id FK, lead_id FK,
  inbound_message_id FK, issued_at, expires_at, reminded_at,
  used_at, used_by_message_id, outcome (approved|rejected|edited)

operator_commands
  message_id (PK), received_at, from_addr, subject, command (APPROVE|REJECT|EDIT),
  code, spf_pass, dkim_pass, sender_ok, accepted, refused_reason,
  replacement, draft_id FK, processed_at
```

  `reply_approvals` is a bearer token in a mailbox, so its rules are CHECKs: `code` is the primary key; `expires_at` ≤ 48 hours after `issued_at`; at most one unused code per draft (partial unique index); `reply_approval_usable(code)` is the one definition of "may be acted on now". `operator_commands` records every APPROVE/REJECT/EDIT received, accepted or refused; `accepted` is only permitted when `spf_pass AND dkim_pass AND sender_ok` and a command and code are present, so a workflow bug can refuse a real approval but never accept a forged one.
- **`018` — reply workflow rules.** `reply_approval_issue()` is the only way a code is minted, reused or reaped; `drafts_approval_rules()` lets a `reply/` draft become `approved` only while a one-time code for it is open or already used to approve/edit it, and refuses `approved_by='auto'` on a reply; `retire_linkedin_on_reply()` (trigger on `inbound_messages`) retires a replied lead's pending/approved LinkedIn draft.
- **`019` — sites and SMOs.** Tables `site_candidates`, `site_lookup_log`, `site_harvest_log`; `leads.source` gains `trialsites`; "never guess a domain" as a CHECK (a candidate holds a domain only when its website was confirmed); `settings.site_lookup_monthly_cap_usd` (seeded $20) and `site_lookup_budget_left()` (a missing setting reads as $0, never unlimited); `claims_library.company_types` (default `{CRO}`; a CHECK that no site/SMO line says "two CROs" or "CRO website(s)" or calls Vertex a CRO); `lead_company_type(raw_extraction, source)`, the one definition of CRO / site / SMO that Drafting, Follow-Ups, the Reply Assistant and Mailbox Watch read. 18 site/SMO claim lines are seeded `active=false, confirmed=false`.

**Lead status values (LOCKED):**
`ingested → enriched → scored → disqualified | contact_found → drafted → approved → sent → replied → won | lost`

**NocoDB:** the review grid writes directly to `drafts` (real PK), never through a view. **Never write through a view:** a Postgres view cannot have a primary key, so NocoDB cannot scope a write to one row — one approve click once went out as an unqualified `UPDATE` and an INSTEAD OF trigger applied it to every row, then returned an unrelated error that made the mass-approval look like a failed no-op. Views are read-only now (a write fails loud, error 55000); any change to a view's schema must re-verify this against live data. After a migration, run a NocoDB metadata sync on the `novascout` source to see new tables and columns.

---

## 9. Workflow Specifications

| Workflow | Name in n8n | id |
|---|---|---|
| 1 | Ingestion - ICH GCP | `ingestionIchgcp01` |
| 1b | Trialsites (sites and SMOs) | `trialsites0001` |
| 2 | Enrichment | `enrichment01` |
| 3 | Scoring | `scoring0001` |
| 3b | Contact Lookup | `contacts0001` |
| 4 | Drafting | `drafting0001` |
| 6 | Send & Track - Send / Follow-Ups / Mailbox Watch / IMAP Health / Daily Digest | `send0001` / `followup0001` / `mailwatch0001` / `imaphealth0001` / `digest0001` |
| 7 | Reply Assistant / Send Reply | `reply0001` / `sendreply0001` |

Each is generated by `n8n/<stage>/build_workflow.py` (Code-node JS in `code_*.js`), with a README and drift-guard tests that fail the build when the workflow, the migrations and this document disagree.

### Workflow 1 — Ingestion
**Trigger:** every 30 minutes, doing its work only when the committed CSV has changed since the last ingest (`ingest_log`, migration `015`). A GitHub Action feeds it, one region per weekday.

**Source:** the ICH GCP directory (`ichgcp.net/cro-list`), per-country pages, server-rendered. Referral and conference CSVs go in by psql `COPY`.

**Two-stage scrape (verified).** A country page (`/cro-list/country/{slug}`) lists companies with name, description and a profile link but **no website** (except the two paid "Featured CROs" slots). The domain is the `Web:` field on the profile page (`…/company/{company_slug}`), which also carries `E-mail:`, phone and address. Scrape **only the "Local, small- and mid-size Contract Research Organizations in {country}" section**; the "Global" section (IQVIA, ICON, Parexel, …) is all enterprise CROs the disqualifiers reject anyway. Rate-limit politely (2 s between requests; self-identifying UA `NovaScoutBot/1.0`, overridable via `ICHGCP_USER_AGENT`). Skip profiles with no `Web:`.

**Access:** ichgcp.net returns 403 to the dev machine's IP (an IP-level block, not a UA block), so n8n can never reach it. **Shipped architecture:** a scheduled GitHub Action (`.github/workflows/scrape-ichgcp.yml`) scrapes from GitHub's runners and commits `data/ichgcp_leads.csv` only when it changes; the local workflow fetches that file from `raw.githubusercontent.com` and upserts into `leads`. No 403s from the runners; 429s are retried with `Retry-After`-aware backoff. Scraping success and DB ingestion are separate steps.

**Dedupe and country.** Dedupe key is the normalised domain; the upsert is `ON CONFLICT (domain) DO UPDATE` and never touches `status` (a 30-minute tick would otherwise drag every lead back to `ingested`). `leads.country` comes from the CSV on every ingest, so the scraper's merge keeps **one country per domain by a fixed rule**: core beats extended; among equals the country the CSV already holds wins. (An alphabetical tie-break once would have moved a CRO listed in Hungary, Poland and Romania.) Raw per-country totals undercount slightly because of this dedup (accepted).

**ClinicalTrials.gov is NOT an ingestion source** (sponsor names, no websites); it is a Scoring lookup.

**Coverage since 2026-10-06:** every country on `ichgcp.net/cro-list` except the sanctioned jurisdictions of §12 (138 country links read live, 133 included). Core 13 earn 10 geography points, extended countries 5, excluded 0 (§12, Workflow 3).
- **Discovery is live** from the shape of `/cro-list/country/{slug}` hrefs; an index with fewer than 15 links is treated as a changed page (exit 2, nothing written). `leads.country` is the locked core spelling for the 13 and a name derived from the slug for the rest (five slugs have fixed display names: `usa`, `dr_congo`, `cote_d_ivoire`, `bosnia_herzegovina`, `trinidad_tobago`).
- **Exclusions** (`scraper/geography.py`) apply to the index before anything is requested, and per row: a profile on an excluded country-code domain, or whose address *ends* in an excluded country's name, is dropped even under a retained country; occupied-territory addresses under Ukraine likewise. Rows of a newly excluded country are purged from the CSV on the next scrape, **not from `leads`** (delete those by hand).
- **Shards: one region per weekday** — Mon `europe`, Tue `asia-pacific`, Wed `americas`, Thu `middle-east` (incl. North Africa, Turkey, Egypt, UAE), Fri `africa` (sub-Saharan plus any unmapped country). The Action picks the shard from the cron expression that fired, not the date, because scheduled runs start 5–7 hours late. `workflow_dispatch` takes `shard` and `plan_only`. Core countries go first within a shard.
- **Each shard is merged into the CSV, not written over it:** a country that finished cleanly is refreshed; one the run did not finish keeps every row; a row whose profile failed to load is kept; other shards' rows are untouched. A run that scraped nothing exits 1 and writes nothing.
- **Politeness:** circuit breaker (8 consecutive 403/429 on profile pages or 32 in a run abort the run; completed countries are still merged), a 4.5 h runtime budget (`ICHGCP_MAX_RUNTIME_SECONDS`) under a 345-minute job timeout, and a `pull --rebase` + `push` retry (four attempts) because five shards a week race routine commits. The Action runs the offline tests first (`test_parse_fixture.py`, `test_geography.py`, `test_shards.py`) and scrapes nothing if exclusions, core list or shard table have drifted.
- **Plan run:** `gh workflow run scrape-ichgcp.yml -f shard=all -f plan_only=true` reads the index, classifies every country, counts local listings with one request each, and prints per-shard workload; nothing is fetched or committed.
- **Result (2026-10-06/07):** all five shards ran with 0 blocked answers; the CSV went 123 → 504 rows and `leads` 125 → 506 (381 new unique leads, all extended-country; the core 13 added none). The whole directory is ~42 minutes of scraping.

**Ingestion runs when the file has changed, not at a time (migration `015`).** The old "daily 23:00" trigger was a fixed slot on a host that is off at night. Now: a fingerprint of the three columns the upsert writes — domain (normalised), company name, country — canonicalised, sorted by domain, hashed with FNV-1a/64 in BigInt, row count carried alongside (`fnv1a64:<16 hex>:<rows>`). Not cryptographic on purpose (the sandbox refuses `crypto`). It is **recorded after the upsert, never before**; a CSV that parses to zero rows is refused; a rewritten description or phone number is no change to `leads` and does not trigger an ingest. The build refuses if the hashed and upserted columns ever differ, if the trigger is a fixed slot, if the upsert sets `status` or an unknown column, or if a node reads a `$json.X` the node above does not emit. To force a re-ingest, delete the file's `ingest_log` row.

**Output:** deduped rows in `leads` with `status='ingested'`.

### Workflow 1b — Ingestion: research sites and SMOs from Trialsites (2026-10-09)

**Why:** on 2026-10-09 the CRO pipeline had run dry (see §13). Independent clinical research sites and site management organisations (SMOs) hear from sponsors, and from CROs choosing sites, so they are the next market for the same product. `trialsites0001` (`n8n/sites/build_workflow.py`; migration `019`).

**Source:** Trialsites, the AIMR Global Clinical Research Site Database (`trialsites.aimronline.org`; API `https://api.aimronline.org/api/v1`): read-only JSON, no authentication, 60 requests per 60 seconds per IP, 142,889 sites in 237 countries from 14 registries, tiers A–D. **Its API page says "Free for non-commercial use"; Nova Scout is commercial outreach, so publishing this workflow is an operator decision (§13).** The site object has **no website, domain, e-mail or phone field** (hence the lookup step). The detail endpoint (`/sites/{id}`) returns investigator records; **this workflow never calls it** and the build refuses a Code node that reads investigator fields — never use personal investigator emails from registry data.

**Lane 1 — weekly harvest.** Each tick asks `site_harvest_log` whether a harvest ran in the last 7 days (a question about a table, not a clock). When due: read `/countries` live, map each spelling through an explicit alias table to §12's clock table (only countries the ICH GCP scraper includes; the build refuses an alias to anything else, so a sanctioned jurisdiction cannot be reached), and for every spelling and tier A and B request `/sites?identified_only=1&sort=-recent_trials_3yr&limit=500`, up to 4 pages, **strictly one request in flight** (Loop Over Items batch 1 + a 1.1 s pace; the HTTP node's own batching caused 60 s timeouts). Deterministic selection, first failing rule wins: included country → tier A/B → `identified` → **active** (≥ 1 trial in the last three years or ≥ 1 recruiting) → not an institution by type or network → not an institution by name (hospital, university, ministry, NHS, … in the included languages, matched as whole words and stems) → a **positive signal** (research word in the name, company legal form, or `Private Practice` / `CRO / Phase 1 Unit`) → not already a candidate → one per name + city → ordered core country, tier A, recent then lifetime trials → **at most 200 new candidates a week**.

**Lane 2 — website lookup.** A batch of at most 10 candidates per tick, never more than the month's remaining cap ÷ $0.04. **The model proposes, code decides.** Haiku 5.5 with web search (§4) gets the name, city and country and answers `{url, evidence_url, confidence}`. Code refuses a directory, social network, registry, institutional domain (`.edu`, `.gov`, `.ac.xx`, `.nhs.uk`) or sanctioned ccTLD, fetches the homepage, the evidence page and up to three contact/location pages on the same host, and **accepts the site only if the candidate's name and its city both appear**. No confident match is a skip, never a guess. Outcomes: `resolved` (a lead is written), `duplicate` (domain already a lead; never touched), `no-match`, `not-confirmed`, `excluded`, `call-failed` (retried up to 3 attempts; the others are final). Outcome, lead and cost are recorded in one statement. A resolved site becomes `leads (source='trialsites', status='ingested')` and is then an ordinary lead.

**Contacts for a site lead come only from its own website** (Workflow 3b's order, minus the CSV): an address published on the lead's own site, then a founder LinkedIn, then Apollo (plan-gated, so in practice `needs_manual_contact`).

**Execution data:** a harvest carries ~30–40K rows, so `trialsites0001` saves no successful execution data; every outcome and cent is in `site_candidates`, `site_lookup_log`, `site_harvest_log`.

**Dry run (2026-10-09/10, scratch database, all addresses `@dryrun.invalid`):** 30,034 rows → 2,658 eligible new candidates, 200 chosen; first 20 lookups → 10 resolved, 2 duplicates, 4 not confirmed, 4 no match, $0.0188 per lookup; the 10 leads scored 55–74 and 4 had own-site addresses; every site email was held `unconfirmed-claim`. `n8n/sites/test_drift_guards.py` (33 cases) proves the build's refusals. The dry run caught overlapping requests (fixed by the sequential loop), a Polish university hospital selected because only two word forms were listed (stems added), and a site draft opening "Your site lists …" (site prompts say "your website").

### Workflow 2 — Enrichment
**Trigger:** every 30 min. Batch of 10 where `status='ingested'`.
**Steps:**
1. Fetch homepage, /about, /services, /team via HTTP node
2. **Chatbot detection is a Code node with regex — NOT an LLM call.** Match against known widget scripts: Intercom, Drift, Tidio, Tawk, Crisp, HubSpot chat, LiveChat, Zendesk. Never spend tokens on what a string match answers.
3. Ollama node (`/api/generate`, `think:false`, `format: json` with explicit schema, §3) → therapeutic areas, phases, founder, employee estimate, site notes
4. Write to `enrichments` (upsert), advance status

**`company_type` replaced `is_cro` — 2026-10-09.** The schema asks for `company_type` as a closed enum — `CRO`, `site`, `SMO`, `other`, `unclear` — with a one-line definition of each in the prompt (a CRO that also runs its own phase I unit is still `CRO`; a private practice that runs trials is `site`; a hospital, university, government body, agency, consultancy, vendor, supplier or pharma company is `other`), and `code_normalise.js` maps anything else to null. `build_workflow.py` parses the list out of Workflow 3's disqualifier sentence and refuses to build if the schema, the normaliser or this document disagree. Rows enriched before that day keep `is_cro` and a free-text label and are not re-enriched. Drafting and the reply path read the type through `lead_company_type()`: enrichment's answer when it is `CRO`, `site` or `SMO`, otherwise the source (ICH GCP → CRO, Trialsites → site).

### Workflow 3 — Scoring
**Trigger:** every 30 min. Batch where `status='enriched'`.

**Hard disqualifiers (deterministic Code node, runs first):**
- Already has a chatbot
- Employee estimate > 500 (enterprise CRO) — **only on a confirmed number.** `employee_estimate` is null on ~85% of leads (extraction correctly refuses to guess); null is unknown, never evidence of scale. The same null-safe handling applies to `founder_name`/`founder_linkedin`.
- No functioning website — "unreachable" over-counts (of 26 first flagged, 7 were alive: 2 rescued by relaxing TLS cert validation, 5 are WAF 403s on every rung). Accepted as a permanent ~5% loss. Design implication: give a lead a bounded number of enrichment attempts before trusting the marker, and treat an HTTP 4xx (a 403 proves the site exists) as weaker evidence than a true connection failure.
- Not a CRO, site or SMO (agency, consultancy, vendor, hospital, university, government body) — **changed 2026-10-09** from "not actually a CRO", when research sites and SMOs became a second lead source (Workflow 1b). Enrichment now classifies `company_type` as exactly one of `CRO`, `site`, `SMO`, `other` or `unclear` (a closed enum in the Ollama schema, so the grammar enforces it); only `other` disqualifies, and `unclear` or a missing value never does (null is not evidence). A lead enriched before that day carries the old boolean `is_cro` and a free-text label instead; for it the rule fires on `is_cro = false` unless the label itself says site or SMO.
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

`fit_score = 0` means *disqualified*. **Geography is three-tier (§12):** the 10 is the ceiling — a core-13 country earns all 10, any other included country earns 5, no country or an excluded one earns 0. An excluded country is deliberately not a hard disqualifier here, because the scraper never lets one into the CSV and Send holds any country without a clock. Weights were rebalanced from 25/20/20/15/10/10 after Sprint 3 showed geography scoring 25/25 for all 79 scored leads (zero variance), and a ~300-employee company outranking everything else.

**Active trials.** For a CRO this 20-point factor is a ClinicalTrials.gov sponsor-name match — about an 8% hit rate, because a CRO is a trial's collaborator, not its sponsor. Every alternative was measured and rejected: `query.locn` (600+ recruiting trials for India alone, awarding 20 points to almost everyone), sponsor-name cleaning (stripping "s.r.o." matched 20 unrelated trials), and `query.term` (12 of 18 test leads returned zero, the other 6 were false positives from generic vocabulary). **There is no fairer query**, so the factor stays and the contact-lookup gate was lowered instead (3b). Mechanism: `GET https://clinicaltrials.gov/api/v2/studies` with `query.spons=<company_name>` and `filter.overallStatus=RECRUITING`, no key; a non-zero `totalCount` earns the 20 points.

**Active trials for a Trialsites lead.** A lead with a linked `site_candidates` row takes this factor from Trialsites (its counts are trials actually run at that location) instead of ClinicalTrials.gov — not even its failure is used, so a CT.gov outage does not hold a site lead back. The three components sum to the factor's full weight; each takes the highest band the lead reaches:

| Trialsites component | At least | Points |
|---|---:|---:|
| recent_trials_3yr | 10 | 10 |
| recent_trials_3yr | 5 | 7 |
| recent_trials_3yr | 2 | 4 |
| recent_trials_3yr | 1 | 2 |
| trial_count | 50 | 5 |
| trial_count | 20 | 4 |
| trial_count | 10 | 3 |
| trial_count | 5 | 2 |
| trial_count | 1 | 1 |
| site_tier | A | 5 |
| site_tier | B | 3 |

Recency carries half the factor (a site that started ten or more trials in three years is fielding inquiries now), lifetime volume a quarter, AIMR tier a quarter (worth only 2 points between A and B). The floor for a candidate is 4; a busy tier-A site with ten recent and fifty lifetime trials earns all 20. If a domain has several Trialsites locations, the strongest counts — never a sum. `n8n/scoring/build_workflow.py` parses this table, refuses a build where the top bands do not add up to 20, and checks `code_evaluate.js` carries the same bands.

**Null-handling for weighted factors:** where `employee_estimate` or `founder_name`/`founder_linkedin` is null the factor contributes a neutral partial score, not zero — a confirmed miss scores lower than an honest unknown.

**Therapeutic area taxonomy — LOCKED.** `therapeutic_areas` extraction is constrained to this fixed enum in the Ollama JSON schema (grammar-enforced, drift-checked against `code_normalise.js` by `test_therapeutic_areas.js`, adversarially tested), which fixes drift (`cardiology`/`cardiovascular`) and industry-term leakage:

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

The last six (Dermatology → Hematology) were added when the first 12-category build showed `Other` absorbing real volume. **Scoring must match case-insensitively**: older rows hold lowercase free text. Non-English text does not reliably map into `phases` (accepted).

**Visibility over spot-checking:** the exceptions queue and NocoDB surface `scores.disqualify_reason`, so misclassification is caught as it happens (23 of 80 early enrichments were judged not-a-CRO, e.g. an RTSM vendor).

**Then** Ollama generates a one-paragraph "why this lead" rationale — specific, not generic.

**Then** Apollo lookup for leads scoring ≥ 50 only. *(Contact lookup, not only Apollo: the gate on Workflow 3b's queue. Lowered from ≥ 60 on 2026-10-06 — "Decision 2026-10-06" below. `n8n/contacts/build_workflow.py` parses the number out of this sentence, so it is the one place to change it.)*

**Workflow 3b — Contact Lookup (`contacts0001`)**
A separate queue, deliberately: `status='scored' AND fit_score >= 50 AND no contacts row AND not retired in contact_attempts`, ordered `fit_score DESC`, batch of 10, hourly (`hours`/1 is safe, §7). It is its own queue (build rule 4) so that a lead scored before this stage existed, or an outage, a plan change or a threshold change, is simply drained when the stage runs. `build_workflow.py` parses the ≥ 50 number out of the sentence above into the Config node.

**The gate is ≥ 50, not ≥ 60 (decision 2026-10-06).** The pipeline had run out of prospects: the leads at 50–59 are each held back by the one factor that cannot discriminate for a CRO (active trials, above), not by a weak profile — every other factor was strong for them. Nothing else changed.

**Sources, in order — nothing is ever pattern-derived or guessed:**
1. **The ichgcp profile e-mail** already in `data/ichgcp_leads.csv` (re-fetched by this workflow; `leads` has no email column and the schema section is locked). About 56% of the first qualifying batch needed no other source. A scraped address wins over a site address because the CSV is a committed file.
2. **An address the lead publishes on its own website** (`Harvest Site Emails`, `code_site_emails.js`): the homepage plus up to four contact/imprint/about/team pages (and `/contact`, `/contact-us`, `/kontakt`, `/impressum`, `/about` as fallbacks), literally present as a `mailto:` or on-page text — no `first.last@`, no `info@` guess, no un-obfuscating "[at]". It must be on the **lead's own domain or a subdomain** (rejects the acquirer's, a parent's or Wix telemetry addresses) and never a non-business mailbox (`careers@`, `ir@`, `press@`, `privacy@`, `noreply@`, `billing@`, …). Among several: business development, then a general inbox, then an operational one; a mailbox carrying the founder's name wins outright. At most five requests per lead, on its own host, three leads at a time. Written `verified = true`. The fetch lives here, not in enrichment, because re-enriching 274 leads would cost a GPU call and a re-score each.
3. **A founder's personal LinkedIn profile** (`linkedin.com/in/…`, harvested from the site's own links by regex): written as a **LinkedIn-only contact** — name, title, `linkedin_url`, no email, `verified=false`, `apollo_id` NULL — and the lead advances to `contact_found` so the DM can be drafted. The email draft for it is tagged `no-address` and held (Workflow 4).
4. **Apollo** — refused by the Free plan (table below), so in practice a lead with none of the above lands in `needs_manual_contact`.

| Endpoint | Result |
|---|---|
| `POST /api/v1/mixed_people/search` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `POST /api/v1/people/match` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `POST /api/v1/mixed_companies/search` | 403 `API_INACCESSIBLE` — "not included in your Free plan" |
| `GET /api/v1/auth/health` | 200 `{"healthy":true,"is_logged_in":true}` |

The key is valid; the gate is the plan. A plan-gated 403 is classified (`isPlanGate` in `code_pick_contact.js`) as `exhausted`; a 429, 5xx, timeout or unparseable response is `refused` and retried.

**`contacts.verified` means one thing:** the address is confirmed, not inferred — true for a first-party address the company published about itself and for Apollo `email_status: verified`; false for Apollo `guessed`, a masked placeholder, a tombstone, or a LinkedIn-only contact. Send gates on it. **A role inbox is not a person:** where enrichment also named a founder the name is written alongside but never presented as the owner of that inbox.

**Three outcomes, and why re-running is free.** A *refusal* is retried and writes nothing. An *answered-but-empty* lookup writes a **tombstone** (a `contacts` row, every field null, `verified=false`) so the batch query can tell "not looked up" from "looked up, nothing there"; the lead stays `scored` (`contact_found` would put a contactless lead in front of the grounding guard). And `contact_attempts` (migration `015`) retires a lead from the queue: `last_outcome='exhausted'` (every source genuinely answered, including the plan-gated 403) or `attempts >= 3` (an unrecognised refusal). Without it, nine stuck leads re-filled the top of every hourly batch and a tenth would have starved the queue. A lookup that finds a contact deletes the attempt row in the same statement. **`needs_manual_contact`** is the read-only NocoDB view (lead, score, country, founder, LinkedIn, and a `why` naming what each source tried); to action one, insert the contact into `contacts` and set the lead `contact_found`. **Re-queue recipes:** `DELETE FROM contact_attempts` re-queues every retired lead; `DELETE FROM contacts WHERE apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL` re-queues the LinkedIn-only ones — the two moves to make after an Apollo plan upgrade.

### Workflow 4 — Drafting
**Trigger:** every 30 min. Batch where `status='contact_found'`. Skill: `NovaScout_DraftingSkill.md` v3 (the table `claims_library` wins over its §4 seed).

**Grounding guard — LOCKED.** The drafting prompt may only use facts present in the enrichment record. If the record lacks at least two specific facts (therapeutic area, named trial, city, founder name), the draft is flagged `low-context` and skipped rather than invented (no model call). This rule exists because of the Nova field-fabrication bug: under forced tool use, Haiku invented a specialty from an email domain.

**The failure mode is also *joining*:** a sentence can use zero invented words and still assert an untrue relationship ("one recruiting oncology trial in Istanbul" from two unrelated facts). Lower temperature reduced fabrication but not joining; prompt structure fixed it — an explicit boundary attached to each fact, at most two therapeutic areas mentioned, second-person trial phrasing, and `founder_name`/city barred from the opening line.

**A silent wiring failure — check for this class whenever a node chain is edited.** A later Postgres node replaced the item, so `$json.system_prompt` was empty and the model drafted with no instructions; the run reported `success` on four drafts that mentioned neither Nova nor NoblePath. Build-time guard: every `$json.X` a node reads must be traceable to something the node above emits.

**Prompt constraints (skill v3):** body 70–110 words, never more than 125; minimum two specific facts; one geography-matched live customer; no adjectives like "revolutionary" or "cutting-edge".

**Skill v3 (2026-10-02):**
- **Model.** `claude-sonnet-5-5` on the Anthropic API via `novascoutAnthropic01` (§3). A failed call (HTTP error, refusal, cut off at `max_tokens`, or text that is not the schema) writes nothing; the lead stays `contact_found`. No fallback to the local model.
- **Composition, not line-picking.** The model writes the whole email and DM from the fact it is told to open on plus the lead's approved claims (description, angle, benefit, ask, and only the proof line matched to the lead's country). It may rephrase a claim, never widen it. Its JSON schema is subject, body and **ask as a separate field** for each channel, plus the claim codes used, so "exactly one ask" can be checked.
- **Rules in code (`code_assemble.js`), as tags after `+` in `variant`** — a tag marks the draft, it never drops it: `long`/`short`; `ask-none`/`ask-multiple`/`ask-scheduling`; link policy (`link-in-warmup`, `unapproved-link`, `linkedin-link`, `pdf-link`, `urls`); `product-name-repeat`/`product-name-unbracketed`/`subject-product`; `ai-repeat`/`ai-powered`/`subject-ai`; `prospect-sponsor`; forbidden claims (`claim-guarantee`, `claim-number`, `claim-visitor-id`, `claim-named-tool`, `claim-language`, `claim-chatbot`); `proof-missing`/`proof-geo`; `ungrounded-area`, `hook-opener`, `hook-source`; subject rules (30–55 characters, `subject-reply`, `subject-caps`, `subject-ungrounded`); `claim-code`; `claim-books`, `claim-sop`, `claim-repeat`; `no-address`; `unconfirmed-claim`; and for sites/SMOs `vertex-cro`, `cro-website`, `proof-two-cros`, `prospect-cro`. `n8n/drafting/test_rule_mutations.py` breaks every rule in turn and proves the suite fails each time (0 survived).
- **Attribution.** `variant` records each message's claim codes in slot order (`role-inbox/D2.ANG-HOURS.BEN-247.PR-TR.A1`). When the model omits the ask or proof code, code attributes it from the text (ask → the library ask sharing at least half its words; proof → the line whose deployment it names). The model is never trusted to copy an identifier.
- **No repeats.** The description and benefits must not repeat the same capability (`capabilities[]`); Assess Grounding names every offered pair that shares one, the prompt carries the rule, Assemble tags `claim-repeat`.
- **Links:** none in warm-up weeks 1–2; after that only the library's `link` line; a LinkedIn URL or PDF is tagged in any week.
- **Subject:** 30–55 characters, never "Nova", never "Re:"/"Fwd:". Leaks from the prompt's worked examples are caught deterministically (a digit or therapeutic area the lead lacks tags `-ungrounded`); a hook that attributes a ClinicalTrials.gov fact to "your site" is tagged `hook-source`.
- **Terminology (decided 2026-10-02, final).** "Sponsor" is the CRO's client (biotech, pharma, device, academic) in claims, prompt, drafts and the one-pager. The prospect is never described as sponsoring: hooks use "running", "recruiting", "registered under your company". (A brief 2026-09-21 "pharma team" experiment was reversed.) `n8n/drafting/audit_drafts_vs_onepager.py` fails on text that calls the prospect a sponsor or leaves any "pharma team", in the library, live drafts or one-pager.
- **A redraft retires what it replaces:** a lead sent back to `contact_found` has its earlier pending/approved first-touch drafts set to `rejected` / `bad draft` in the same statement that writes the new ones (otherwise Send would send the old approved draft first, since it sends the oldest). Sent drafts and follow-ups are never touched. New drafts are always `pending` until the gate approves them.
- **Proof is geography-aware** (§12): resolved from each proof line's `countries`. A groundable lead the library cannot complete waits at `contact_found` rather than go out with a hole in it.
- **Worked-example narrowing:** the skill's §5 example is shown to the model as written except one narrowing (BEN-BRIEF → "therapeutic area and study phase", because the code captures only those); `EXAMPLE_NARROWINGS` in `build_workflow.py` refuses to build once the skill's example changes.

**Sites and SMOs — claims picked by company type (migration `019`).** Every `claims_library` line carries `company_types`. The batch query resolves each lead's kind through `lead_company_type()`, Assess Grounding offers only that kind's lines, and the user message (never the cached system prompt) opens with what the prospect is ("a site or SMO is NOT a CRO; its inquiries come from sponsors and from CROs choosing sites"). A kind with no active description line is skipped by the batch query itself, so unswitched site leads wait at `contact_found` without taking a place in front of CRO leads. Once lines are active but unconfirmed, a site draft is written and held `unconfirmed-claim`. Follow-Ups and the Reply Assistant filter by the same kind; Mailbox Watch's reply notification links `nova-one-pager-sites.docx` for a site or SMO and `nova-one-pager.docx` otherwise (both from `main`). The site one-pager is derived by `n8n/drafting/build_onepager_sites.py` (phrase-for-phrase; each replaced exactly once or the build stops).

**A contact with no email address (tag `no-address`).** A LinkedIn-only contact still gets both channels drafted, but the email has nowhere to go, so Assemble tags it and the gate holds it with no claim check paid for. A held `no-address` email can simply be rejected.

**A country with no business-hours clock is stopped before any model call (tag `no-send-clock`).** Assess Grounding checks the clock **first** (before the CT.gov answer is read), case-insensitively as Send does, and routes the lead down the no-API branch: a `no-send-clock/<addressing>` note naming the country and what to add. The note advances the lead to `drafted`, like a low-context note, because a lead that writes no row is re-picked every tick and starves the batch. With all 133 included countries now holding a clock this only fires for a country the directory starts listing before anyone adds its clock (§13).

**Auto-approval and the repair loop** sit between Assemble Drafts and Write Drafts & Advance; rules, check and dry runs are in Workflow 5. The repair loop unrolls at most **2 repairs** as nodes (`Claude Repair 1`, `Apply Repair 1`, `Assemble Drafts R1`, `Approval Gate R1`, …) generated by `n8n/drafting/approval_chain.py`, because an n8n loop would break each Code node's read of the item it follows.

**Prompt caching and the batch split that was rejected.** n8n's HTTP node starts every item's request before awaiting any, so a batch's calls are concurrent and a cache entry is readable only once the response writing it has begun. The structural fix (first lead alone, the rest after) was built and four live probes killed it: a lane fed from a one-item node collapses `pairedItem` lineage so `$('Assess Grounding').item` silently resolved the first lead's facts for every later lead (wrong facts in the wrong email, no error); the lineage-safe form (tag + IF + a `chooseBranch` Merge) stopped the workflow dead on a one-lead batch, the commonest size. **So caching ships without the split** and reads come from the next run within the 5-minute window. What would make the split safe: have `code_assemble.js` and `code_approval_apply.js` resolve their upstream item **by `lead_id`** out of `$('<node>').all()` instead of by `pairedItem` — a change to the two most safety-critical Code nodes, needing its own mutation tests and dry run (optional, ~4%; §13).

**Output:** an email variant and a LinkedIn variant per lead into `drafts`.

### Workflow 5 — Exceptions Queue (HUMAN) — was the Review Queue
**Changed 2026-10-03 (migration `014`): the review queue is an exceptions queue.** An email draft — first touch or follow-up — no longer waits for a human by default. The workflow that wrote it approves it (`approved_by = 'auto'`) when, and only when, all of these hold; anything else is written `pending` with the reason in `hold_reason`, and *that* is the queue:

| Rule | Held as |
|---|---|
| `settings.auto_approve_email` is on (on by default; off, nothing auto-approves and no claim check is paid for) | `auto-approve-off` |
| it is an email — **a LinkedIn draft never auto-approves** (Section 6) | `linkedin` |
| it is not a low-context note, nor a `no-send-clock` note | `low-context` / `no-send-clock` |
| its lead's country has a Send business-hours clock (Section 12) — held at once, never repaired: no rewrite can give a country a clock, and Send would refuse the email anyway | `no-send-clock: <country> has no business-hours clock, so this email can never be sent (Section 12)` |
| zero rule tags: nothing after `+` in `variant` (every Workflow 4 / skill v3 rule tag, `unconfirmed-claim` included) | `rule-tags: ...` |
| every claim code it records is active and `confirmed` in `claims_library` | `unconfirmed-claim: ...` / `no-claims` |
| a second Sonnet 5.5 call (Section 3's model and parameters) passes it: each product claim compared with the confirmed claims, each prospect fact with the enrichment record — a widened claim is a failure. **A widened or unsupported statement is repaired first** (up to 2 repairs, below); it is held only if it still fails | `claim-check: widened "..." (BEN-SEE)`, `claim-check-incomplete: ...`, `claim-check-failed: ...`; after repairs, every attempt's reasons: `first draft: … \| after repair 1: … \| after repair 2: …` |

**How it is built.** Two Code nodes, `code_approval.js` (Approval Gate) and `code_approval_apply.js` (Apply Claim Check), sit between Assemble and Write in Workflow 4 and in Follow-Ups, embedded verbatim in both, so a first touch and a follow-up are judged by the same functions. The clock list is substituted at build time from §12's table. Deterministic rules run first, so a held draft costs no API call. The checker gets exactly what the drafter had (fact sheet, including the ClinicalTrials.gov trial, looked up per run and stored nowhere) and, for a follow-up, the enrichment record plus the first email as context. **The model judges statements; code decides.** Its schema has no overall verdict: it returns every statement sentence by sentence with a kind (claim / prospect / none), the claim code or record fact compared, and a verdict; Apply Claim Check approves only if every claim is `supported` by a code that really is a confirmed line, every prospect fact is `supported`, and the quoted sentences account for the whole email (a skipped sentence is one nobody judged). A failed call holds the draft; no fallback model. The prompt's example of a widening is deliberately not draft 99's sentence, the held-out case the dry run proves the check catches. The `drafts_approval_rules` trigger re-checks every deterministic rule on the table, so a workflow bug can make approval *less* likely, never more.

**The repair loop.** When the check holds an email for a `widened` or `unsupported` claim or an `unsupported` or `joined` prospect fact, the drafting model (same model and parameters) gets the email, the confirmed lines, the record and **each flagged sentence with the checker's exact reason**, and rewrites only those sentences in the approved claim's own words. `code_repair.js` applies the answer **by code**, only to flagged sentences and only inside the fields the text came from. The repaired composition then runs through **every rule again** (a second Assemble copy), the Approval Gate and the claim check. **At most 2 repairs.** A hold the composer cannot fix (failed call, quotes that don't cover the email, a "supported" claim citing no confirmed line) is not repaired; rules 1–4 hold at once with no repair. `hold_reason` lists each attempt (`first draft: … | after repair 1: … | after repair 2: …`) and `drafts.claim_check.attempts` keeps verdicts, rewrites and costs, so a draft's cost includes its repairs. Follow-up #1 is told to use its added line almost word for word.

**Proven by dry run 2026-10-03** (`n8n/sendtrack/dryrun/approval_dryrun.py`, 33/33 checks against a scratch database; composer a fixture, checker real): a clean first touch and follow-up approve with no repair; a widened sentence is repaired once and approved; repairs that keep widening are held after exactly 2; tagged, low-context and LinkedIn drafts are held with no check; with the flag off nothing approves; the table turns a direct bad auto-approval into a hold; the digest reaches the operator once. The check is **stricter than the human approvals were** (it held 3 of the 4 drafts a person had approved). The first run found two faults unit tests could not (a "Subject:" label in a quote; the required "(we call it Nova)" judged a claim), both fixed and pinned.

**Send is unchanged:** an auto-approved email is an approved email and goes out in its recipient's business hours like any other.

**What the human does now.** The NocoDB grid on `drafts` is still the interface and Approve · Edit · Reject still apply, with **rejection requiring a reason** (bad fit / bad draft / already contacted / wrong contact — the learning loop's data). What needs a person is every `pending` draft: each LinkedIn DM, and every email the checks held, with `hold_reason` saying why. A human approval is recorded `approved_by = 'human'` and the hold reason is kept (evidence for tuning the checks); editing an auto-approved email's text makes it the human's. To stop an auto-approved email before it goes, reject it. The Daily Digest (Workflow 6) lists what was auto-approved, sent and held.

### Workflow 6 — Send & Track
Built as separate workflows (split because IMAP triggers and cron ticks start differently): `send0001` (cron → guard → claim → SMTP → log), `mailwatch0001` (IMAP on Sent and INBOX), `followup0001`, `imaphealth0001`, `digest0001`. Migration `007`.

**Send:** every 10 minutes, recipient's business hours only, randomised pacing. SMTP node → mailbox → `outreach_log`. **Reply detection:** IMAP triggers on the same mailbox. Opt-out (no/unsubscribe/remove/stop) → domain to `blocklist`, permanent. **Follow-up:** if no reply after 6 days, a follow-up draft; at most two, then mark lost. **No HubSpot yet** (the account is Fatima/NoblePath's, not Amitrix's): replied leads get a filtered NocoDB view (`replied_queue`) and an operator notification email instead.

**The warm-up ceiling belongs to the mailbox, derived from the real Sent folder.** Operator manual sends count too. Warm-up starts on the sender-day (Asia/Karachi) of the first send with an external recipient; week = ⌊days since ÷ 7⌋ + 1; only messages with a recipient outside `amitrixlabs.com` and other than the operator's notification address count (notifications use no warm-up slot). Mailbox Watch mirrors Sent into `mailbox_sent` (whole folder on every activation); the send path writes its own sends there as SMTP accepts them. **It fails closed:** Send refuses if the mirror has never read the folder (`sent-mirror-never-ran`) or one of its own sends has not appeared there after 15 minutes (`sent-mirror-behind`). The IMAP trigger only fires once a folder holds a message, so nothing sends until Sent has a message and Mailbox Watch is published.

**Claim, then send — at most once.** The draft flips to `sent` and an `outreach_log` row with no Message-ID (a *claim*) is written before the SMTP call. The claim re-checks channel, approval, unchanged body, verified contact, blocklist, reply/bounce and **re-counts the ceiling in SQL under an advisory lock taken in an earlier, separate statement** (the Postgres node runs its text as one implicit transaction, and under READ COMMITTED only a later statement sees a concurrent claim). SMTP refusals split: account-level (auth, TLS, 4xx) → back to `approved`, retried; recipient-level 5xx → `pending` tagged `+smtp-rejected`. The Send Email node has no retry (a timeout after acceptance would send twice) and `appendAttribution` is forced off (Send Email typeVersion 2.1 otherwise appends "sent automatically with n8n").

**Per-draft checks.** `channel='email'` and `status='approved'` in SQL (§6 enforced three times: the query never selects LinkedIn, Decide Send throws on one, the claim refuses one); `contacts.verified`; domain and recipient not blocklisted; no reply/bounce; first touch only to a lead never emailed (including by hand); not a low-context note; the §5 opt-out sentence **verbatim, immediately followed by the current signature** (a stale signature is refused); at most one URL. **Business hours are the recipient's:** 09:00–17:00 local with each country's own statutory weekend, from §12's table — all 133 included countries. Fixed standard-time offsets, **no DST by operator decision** (so a country on summer time runs an hour later; it never sends before 09:00 local or on a weekend). Looked up case-insensitively, the same as Drafting's guard. A country outside the table is refused `unknown-country`. Pacing (Config, not locked): 10-minute tick, one message at most, 20-minute floor since the mailbox's last external send, then a 40% coin flip.

**Reply classification reads only what they wrote above the quoted thread** (every reply quotes our "reply 'no'"). `no` counts only as the first word, also in Portuguese, Polish, Turkish, Hungarian and Romanian (Czech "ne" omitted). **An out-of-office or bounce is recorded but does not set `replied`.** `inbound_messages` is keyed on Message-ID. Matching, strongest first: `thread` (our Message-ID in their References), `contact-email`, … `lead-domain`; **the reply goes back to the address that wrote**, not necessarily `contacts.email`. A positive/neutral signal (`positiveSignal()`) is computed at notification time and stored nowhere. The operator notification goes to `settings.operator_email`. The first real prospect reply (lead 26, Hungary, 2026-10-07) was checked end to end and bookkept correctly; thread headers are now stored (migration `017`).

**Mailbox Watch is proven live only when it fires.** The IMAP trigger cannot act as a heartbeat; sockets drop on host sleep, DNS blips and server closes, and n8n's backoff can leave a trigger down for hours after the mailbox is reachable. Inbox is the only path that catches an opt-out. **IMAP Health** (`imaphealth0001`) runs every 20 minutes through the `imap-health` sidecar container (a sidecar, not Execute Command, by operator decision: no shell access for workflows; read-only filesystem, runs as `nobody`, no published port). It asks (1) does a fresh read-only IMAP session work, and (2) did Mailbox Watch miss any INBOX/Sent message from the last 48 hours older than 15 minutes (the proof that the triggers are still listening). Two consecutive failing checks are needed to alert; then one email to the operator, a reminder every 6 hours, and one all-clear. Problems: `checker-unreachable`, `imap-failed`, `inbox-missed`, `sent-missed`. It cannot report its own absence when n8n or the host is down.

**Follow-ups are composed, not templated.** Sonnet 5.5 writes each from the first email as sent plus the approved claims; nothing else about the lead reaches it (build rule 6 holds by construction). #1: 40–70 words, refers back, adds exactly one angle or benefit the first email did not use; #2: a short final note of at most 40 words with no new claim. Both pass Workflow 4's rule functions (embedded verbatim) plus `short`, `long`, `fu-no-new-claim`, `fu-repeat`, `fu-new-claim`, and go through Workflow 5's Approval Gate and claim check (auto-approved or `pending`). Only the note is checked; the quoted first email already went out. Find Due Follow-Ups counts follow-up *numbers*, not rows (a regenerated #1 is still one slot), and a rejected follow-up uses its slot. The follow-up runs every 30 minutes, not every 6 hours: the hours rule skipped the one tick that found three leads due, and the stack had also been down 9 days (Docker Desktop does not autostart).

**Daily Digest (`digest0001`, `digest_log`).** One plain-text email a day to `settings.operator_email`, at the first 30-minute tick at or after 08:00 operator time (Asia/Karachi; before India, the earliest recipient, opens at 08:30), covering from where the last digest stopped: **AUTO-APPROVED**, **SENT** (and who approved it), **HELD FOR A PERSON** (with `hold_reason`; for a held email it carries a one-time code, §9 Workflow 7). Recorded only once SMTP accepted it. No address, no digest. It uses no warm-up slot.

**Verified by dry run, not live send** (`n8n/sendtrack/dryrun/dryrun.py`): the real workflows through n8n against a database copy with every contact rewritten to `@dryrun.invalid` and SMTP into a loopback sink. Proven: ceiling enforced (5th send of a 5/day week goes, 6th refused), LinkedIn never selected, blocklist skipped, pending/rejected/low-context never selected.

### Workflow 7 — Reply Assistant

**A prospect reply gets a drafted answer, held for a one-time code, never sent without it.** Three workflows built on migrations `017`/`018`.

**`reply0001` — the draft and the code.** Every 30 minutes it reads each `inbound_messages` row classified `reply`, matched to a lead, with no `reply_approvals` row for its Message-ID — one drafted reply per prospect *message* for ever (delete the `reply_approvals` row to ask for another; a prospect who writes again gets a fresh answer). The blocklist is checked too. `claude-sonnet-5-5` at effort `high` drafts from their message, the thread as sent, the enrichment boundary facts and the confirmed claims — the only model call in the path (the operator reads every one, so no claim check). Five topics nothing confirmed can settle (pricing, integrations, languages, security, timelines) are detected in *their* text and the model is told to defer them. `Assemble Reply` enforces Workflow 4's claim rules plus 40–120 words, one next step, the one-URL cap, and **never reuses the "two CROs" proof line or calls Vertex a CRO** (`proof-refused`). The draft is written `pending` — **a reply is never auto-approved** (refused on the table) — and its one-time code is minted by `reply_approval_issue()`. `Build Review Email` sends the operator (their own inbox, no warm-up slot) what the prospect wrote, the exact bytes that would be sent, what the reply does not answer, the rule tags, and `APPROVE <code>`, `REJECT <code>`, `EDIT <code> <replacement>`. A second lane reminds about any open code the operator has not been told about in 4 hours.

**The operator fork in Mailbox Watch — the hazard this exists to remove.** The operator answers the review email *inside the prospect's thread*, so `Record Inbound`'s `thread` matcher would tie the operator's one-word "no" to the prospect's lead and blocklist them permanently. So `Classify Inbound` parses the operator-command shape **first**; `Command Shaped? → Apply Operator Command → Operator Mail?` run before `Record Inbound` sees a command-shaped message, and `Record Inbound` independently refuses to match the operator's own address to any lead. Three separate questions (`code_operator_command.js`): **record** (command-shaped at all → every one gets an `operator_commands` row, accepted or refused), **handled** (SPF, DKIM and sender all authenticated → it is operator mail, never a prospect reply), **accepted** (handled and nothing refused it → only then does a draft move). `Apply Operator Command` re-derives all three from the database at write time. A plain "no" from the operator is not command-shaped and is matched to no lead.

**`sendreply0001` — a separate lane from cold `send0001`.** An answer to a message a prospect sent *us* is not cold outreach: it skips the warm-up ceiling, business hours and pacing floor (structurally — its query carries no clock table). **It never skips:** the blocklist (lead domain and the address answered), the one-time code re-checked at claim time (outcome `approved` or `edited`, belonging to *this* draft), the §5 signature and one-URL cap, and at-most-once. `d.variant LIKE 'reply/%'` is the whole boundary between the lanes. It threads through the `smtp-send` sidecar (`smtp_send_server.py`, `POST /send`), because n8n's Send Email node has no `headers` option and so cannot set `In-Reply-To`/`References`; the sidecar holds only the five mailbox/SMTP variables, a read-only filesystem, `user: "65534:65534"` and **no published port** (it has no authentication; the build refuses a `ports:` entry). It refuses to send as any From but the configured mailbox.

**Proven 2026-10-09 (`n8n/sendtrack/dryrun/reply_dryrun.py`, 27/27 checks, scratch database, HTTP and SMTP sinks):** a spoofed sender, an unknown code, an expired code and a reused code (even when both messages are processed in one run) are each refused with the draft left pending; a plain operator "no" blocklists nothing while a real prospect's "no" in the same run still blocklists their domain; an approved reply sends despite a 02:00 UTC clock and a maxed ceiling, and is refused once its recipient's domain is blocklisted. The dry run also caught a build-macro bug (`with_iso()` shipped every `ISO(…)` unsubstituted) before anything went live.

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
**Extended geographies (5 points):** every other country listed on `ichgcp.net/cro-list`, except the excluded jurisdictions below. Each one has a business-hours clock as of 2026-10-07 — the table below. The 13 above are the **core geographies** and keep the full 10 points of the geography factor (Section 9, Workflow 3); an extended country earns 5 of those 10. Added 2026-10-06 — until then the pipeline ingested only the core 13. Section 9, Workflow 1 holds the mechanics.
**Excluded jurisdictions (checked 2026-10-06):** Afghanistan, Belarus, Cuba, Iran, Myanmar, Nicaragua, North Korea, Russia, Syria, Venezuela.
**Strongest signal:** oncology focus (matches the NoblePath case study).

**Second ICP — independent research sites and SMOs (2026-10-09, §9 Workflow 1b).**
- **Site target:** whoever answers study inquiries — the site's director, owner, or business-development / study start-up lead. The address is only ever one the site publishes on its own website (Workflow 3b), never an investigator's from registry data.
- **Site company:** an independent clinical research site (a private practice or clinic that runs trials counts) or a site management organisation; **active** — Trialsites tier A or B with at least one trial in the last three years or one recruiting now; has a website; no existing chatbot. **Never a hospital, a university or a government body** — screened by type and name before any money is spent, and by enrichment (`company_type = other` disqualifies) afterwards. The 5–100 employee band and the `> 500` disqualifier apply unchanged.
- **Site geographies:** exactly the CRO list — same exclusions, geography factor and business-hours clocks.
- **What they hear from:** sponsors, **and CROs choosing sites for a study**. Their claims are their own lines (`claims_library.company_types` site/SMO), never a CRO line reworded at draft time; an SMO additionally gets the investigator-network line.
- **Proof:** "It's live at Vertex Clinical Research, a research center in Mexico." (`PR-SITE`) for every site and SMO lead, whatever the country. **Vertex is a clinical research center, never a CRO; a site never gets the "two CROs" line (`PR-BOTH`) or NoblePath's CRO line, and no message to a site says "CRO websites"** — refused on the table, in the drafting rules and in the site one-pager.
- **Scoring:** the same six weights; the active-trials factor comes from Trialsites (Workflow 3).

**Second case study — geography-matched proof (2026-09-19).** Nova is live at NoblePath (Türkiye) and Vertex Clinical Research (Mexico). A CRO's proof line is resolved from `claims_library` by country: Vertex for Mexico/Brazil/Argentina; NoblePath for Turkey, Egypt, UAE, Romania, Hungary, Poland and Czech Republic ("nearby" is a judgement and one edit); the catch-all `PR-BOTH` ("It's live at two CROs, in Türkiye and Mexico") everywhere else — **that wording is wrong for Vertex and is on the fix list (§13)**. Each proof line's `countries` is the mapping, so the operator owns it.

**How the exclusions were decided — and where to change them.** Read on 2026-10-06 from OFAC's programme list, the EU sanctions map (all 55 regimes) and the gov.uk list of UK regimes. **The rule:** a country is excluded when OFAC, the EU or the UK maintains against it *as such* (a) a comprehensive or territory-wide embargo, or (b) measures aimed at its government, ruling authority, state-owned entities or whole economic sectors — beyond asset freezes and travel bans on named individuals, and beyond arms embargoes.

| Excluded | Why |
|---|---|
| Cuba, North Korea | Comprehensive embargoes (OFAC; North Korea also EU, UK and UN) |
| Afghanistan | The Taliban de facto government is the target: OFAC Afghanistan-Related programme, the EU Taliban regime, the UK Afghanistan regime; Afghan central-bank assets frozen |
| Nicaragua | OFAC's programme targets the regime's institutions, its state mining enterprise and the gold sector; EU and UK regimes |
| Syria | **Being unwound, not lifted.** The US programme was terminated in 2025 and the Caesar Act repealed in December 2025, and the UK and EU have lifted most economic measures, but the EU regime as published still carries measures aimed at the Government of Syria (financial, insurance, bonds) and the UK regime remains. Excluded until the residual state-level measures are confirmed gone — the first one to revisit |
| Iran, Russia, Belarus, Venezuela, Myanmar | Per the operator's list. Venezuela's OFAC measures were eased by general licences in 2026 (Government of Venezuela and PDVSA still blocked); Myanmar's rest on OFAC's designation of the military, state banks and state enterprises plus EU/UK regimes |

**Retained, and flagged.** A country-named programme at one authority is not enough (most are lists of named people or an arms embargo; applying it would drop Ukraine, Turkey and China). These stay in scope: Bosnia and Herzegovina, Burundi, Central African Republic, China and Hong Kong, DR Congo, Ethiopia, Guatemala, Guinea and Guinea-Bissau, Haiti, Iraq, Lebanon, Libya, Mali, Moldova, Niger, Somalia, South Sudan, Sudan, Tunisia, Turkey, Ukraine, Yemen, Zimbabwe. The scraper's plan run prints which of them the directory actually contains. **Occupied territory is excluded as territory, not as Ukraine:** a Ukraine profile whose address names Crimea, Sevastopol, Donetsk, Luhansk, Kherson or Zaporizhzhia is dropped. **Two row-level screens back the country filter:** a profile on an excluded country-code domain (`.ru`, `.ir`, `.by`, `.su`, `.cu`, `.kp`, `.mm`, `.af`, `.ni`, `.ve`, `.sy`) and one whose address *ends* in an excluded country's name are dropped even under a retained country (the end only: Buenos Aires and Mexico City have streets named Cuba, Venezuela and Nicaragua).

**To change the list:** edit `EXCLUDED` in `scraper/geography.py` and the "Excluded jurisdictions" line above together — `scraper/test_geography.py` and `n8n/scoring/build_workflow.py` both refuse to proceed if they disagree. Rows for a newly excluded country are purged from the CSV on the next scrape, **not** from `leads`: delete those by hand.

**Business-hours clocks — LOCKED, one per included country.** Workflow 6 sends inside the recipient's business hours, 09:00–17:00 local, and `COUNTRY_CLOCKS` (`n8n/sendtrack/code_decide.js`) is the only table that says when that is. The table below holds all 133 included countries and `n8n/sendtrack/build_workflow.py` refuses to build if it and the code disagree (offset and weekend, not just keys). The name is `leads.country` as the scraper writes it. `scraper/index_slugs.json` is the `/cro-list` index as the 2026-10-06 plan run read it (138 links, 133 included), committed so `n8n/sendtrack/test_drift_guards.py` can prove offline that every country the pipeline can ingest has a clock.

**Offsets are standard time, no DST — operator decision 2026-09-14.** A country on summer time (Europe, North America, Egypt, Israel, Lebanon, Chile, New Zealand, south-east Australia) runs an hour later than the table for part of the year, so the last hour of its window can land just after close; Morocco drifts the other way during Ramadan (harmless). Paraguay, Jordan, Turkey, Mexico and Brazil abolished summer time and sit on one offset, which is what the table carries. It never sends before 09:00 local or on a weekend. A real tz database is not available in the Code-node sandbox; revisit only if a reply says the timing was wrong.

**The weekend column is each country's statutory weekend:** Friday–Saturday across the Gulf and most of the Arab world plus Israel, Bangladesh, Algeria, Djibouti and Mauritania; Saturday alone in Nepal (UTC+5:45, carried as 345 minutes); Saturday–Sunday everywhere else, UAE included (since 2022), and in Lebanon, Morocco and Tunisia.

**A lead whose country has no clock is stopped before any money is spent,** in this order: Assess Grounding (no-API `no-send-clock` note), the Approval Gate (holds any email for a lead with no clock, no check paid for), then Send (`unknown-country`). To rescue one: add its clock here and to `code_decide.js`, rebuild, re-import, and set the lead back to `contact_found`.

| Country | Standard offset | Weekend | Tier |
|---|---|---|---|
| Argentina | UTC-3:00 | Sat-Sun | core |
| Brazil | UTC-3:00 | Sat-Sun | core |
| Czech Republic | UTC+1:00 | Sat-Sun | core |
| Egypt | UTC+2:00 | Fri-Sat | core |
| Hungary | UTC+1:00 | Sat-Sun | core |
| India | UTC+5:30 | Sat-Sun | core |
| Mexico | UTC-6:00 | Sat-Sun | core |
| Pakistan | UTC+5:00 | Sat-Sun | core |
| Poland | UTC+1:00 | Sat-Sun | core |
| Romania | UTC+2:00 | Sat-Sun | core |
| South Africa | UTC+2:00 | Sat-Sun | core |
| Turkey | UTC+3:00 | Sat-Sun | core |
| UAE | UTC+4:00 | Sat-Sun | core |
| Albania | UTC+1:00 | Sat-Sun | |
| Algeria | UTC+1:00 | Fri-Sat | |
| Angola | UTC+1:00 | Sat-Sun | |
| Armenia | UTC+4:00 | Sat-Sun | |
| Australia | UTC+10:00 | Sat-Sun | |
| Austria | UTC+1:00 | Sat-Sun | |
| Azerbaijan | UTC+4:00 | Sat-Sun | |
| Bahamas | UTC-5:00 | Sat-Sun | |
| Bahrain | UTC+3:00 | Fri-Sat | |
| Bangladesh | UTC+6:00 | Fri-Sat | |
| Belgium | UTC+1:00 | Sat-Sun | |
| Benin | UTC+1:00 | Sat-Sun | |
| Bosnia and Herzegovina | UTC+1:00 | Sat-Sun | |
| Bulgaria | UTC+2:00 | Sat-Sun | |
| Burkina Faso | UTC+0:00 | Sat-Sun | |
| Burundi | UTC+2:00 | Sat-Sun | |
| Cambodia | UTC+7:00 | Sat-Sun | |
| Cameroon | UTC+1:00 | Sat-Sun | |
| Canada | UTC-5:00 | Sat-Sun | |
| Chile | UTC-4:00 | Sat-Sun | |
| China | UTC+8:00 | Sat-Sun | |
| Colombia | UTC-5:00 | Sat-Sun | |
| Congo | UTC+1:00 | Sat-Sun | |
| Costa Rica | UTC-6:00 | Sat-Sun | |
| Cote d'Ivoire | UTC+0:00 | Sat-Sun | |
| Croatia | UTC+1:00 | Sat-Sun | |
| Cyprus | UTC+2:00 | Sat-Sun | |
| DR Congo | UTC+1:00 | Sat-Sun | |
| Denmark | UTC+1:00 | Sat-Sun | |
| Djibouti | UTC+3:00 | Fri-Sat | |
| Dominican Republic | UTC-4:00 | Sat-Sun | |
| Ecuador | UTC-5:00 | Sat-Sun | |
| El Salvador | UTC-6:00 | Sat-Sun | |
| Equatorial Guinea | UTC+1:00 | Sat-Sun | |
| Estonia | UTC+2:00 | Sat-Sun | |
| Ethiopia | UTC+3:00 | Sat-Sun | |
| Finland | UTC+2:00 | Sat-Sun | |
| France | UTC+1:00 | Sat-Sun | |
| Gabon | UTC+1:00 | Sat-Sun | |
| Gambia | UTC+0:00 | Sat-Sun | |
| Georgia | UTC+4:00 | Sat-Sun | |
| Germany | UTC+1:00 | Sat-Sun | |
| Ghana | UTC+0:00 | Sat-Sun | |
| Greece | UTC+2:00 | Sat-Sun | |
| Guatemala | UTC-6:00 | Sat-Sun | |
| Guinea | UTC+0:00 | Sat-Sun | |
| Hong Kong | UTC+8:00 | Sat-Sun | |
| Indonesia | UTC+7:00 | Sat-Sun | |
| Ireland | UTC+0:00 | Sat-Sun | |
| Israel | UTC+2:00 | Fri-Sat | |
| Italy | UTC+1:00 | Sat-Sun | |
| Jamaica | UTC-5:00 | Sat-Sun | |
| Japan | UTC+9:00 | Sat-Sun | |
| Jordan | UTC+3:00 | Fri-Sat | |
| Kazakhstan | UTC+5:00 | Sat-Sun | |
| Kenya | UTC+3:00 | Sat-Sun | |
| Kosovo | UTC+1:00 | Sat-Sun | |
| Kuwait | UTC+3:00 | Fri-Sat | |
| Kyrgyzstan | UTC+6:00 | Sat-Sun | |
| Latvia | UTC+2:00 | Sat-Sun | |
| Lebanon | UTC+2:00 | Sat-Sun | |
| Liberia | UTC+0:00 | Sat-Sun | |
| Lithuania | UTC+2:00 | Sat-Sun | |
| Luxembourg | UTC+1:00 | Sat-Sun | |
| Macedonia | UTC+1:00 | Sat-Sun | |
| Madagascar | UTC+3:00 | Sat-Sun | |
| Malawi | UTC+2:00 | Sat-Sun | |
| Malaysia | UTC+8:00 | Sat-Sun | |
| Mali | UTC+0:00 | Sat-Sun | |
| Malta | UTC+1:00 | Sat-Sun | |
| Mauritania | UTC+0:00 | Fri-Sat | |
| Mauritius | UTC+4:00 | Sat-Sun | |
| Moldova | UTC+2:00 | Sat-Sun | |
| Mongolia | UTC+8:00 | Sat-Sun | |
| Montenegro | UTC+1:00 | Sat-Sun | |
| Morocco | UTC+1:00 | Sat-Sun | |
| Mozambique | UTC+2:00 | Sat-Sun | |
| Nepal | UTC+5:45 | Sat | |
| Netherlands | UTC+1:00 | Sat-Sun | |
| New Zealand | UTC+12:00 | Sat-Sun | |
| Nigeria | UTC+1:00 | Sat-Sun | |
| Norway | UTC+1:00 | Sat-Sun | |
| Oman | UTC+4:00 | Fri-Sat | |
| Panama | UTC-5:00 | Sat-Sun | |
| Papua New Guinea | UTC+10:00 | Sat-Sun | |
| Paraguay | UTC-3:00 | Sat-Sun | |
| Peru | UTC-5:00 | Sat-Sun | |
| Philippines | UTC+8:00 | Sat-Sun | |
| Portugal | UTC+0:00 | Sat-Sun | |
| Qatar | UTC+3:00 | Fri-Sat | |
| Rwanda | UTC+2:00 | Sat-Sun | |
| Saint Lucia | UTC-4:00 | Sat-Sun | |
| Saudi Arabia | UTC+3:00 | Fri-Sat | |
| Senegal | UTC+0:00 | Sat-Sun | |
| Serbia | UTC+1:00 | Sat-Sun | |
| Singapore | UTC+8:00 | Sat-Sun | |
| Slovakia | UTC+1:00 | Sat-Sun | |
| Slovenia | UTC+1:00 | Sat-Sun | |
| South Korea | UTC+9:00 | Sat-Sun | |
| Spain | UTC+1:00 | Sat-Sun | |
| Sri Lanka | UTC+5:30 | Sat-Sun | |
| Suriname | UTC-3:00 | Sat-Sun | |
| Sweden | UTC+1:00 | Sat-Sun | |
| Switzerland | UTC+1:00 | Sat-Sun | |
| Taiwan | UTC+8:00 | Sat-Sun | |
| Tanzania | UTC+3:00 | Sat-Sun | |
| Thailand | UTC+7:00 | Sat-Sun | |
| Togo | UTC+0:00 | Sat-Sun | |
| Trinidad and Tobago | UTC-4:00 | Sat-Sun | |
| Tunisia | UTC+1:00 | Sat-Sun | |
| Turkmenistan | UTC+5:00 | Sat-Sun | |
| USA | UTC-5:00 | Sat-Sun | |
| Uganda | UTC+3:00 | Sat-Sun | |
| Ukraine | UTC+2:00 | Sat-Sun | |
| United Kingdom | UTC+0:00 | Sat-Sun | |
| Uruguay | UTC-3:00 | Sat-Sun | |
| Uzbekistan | UTC+5:00 | Sat-Sun | |
| Vietnam | UTC+7:00 | Sat-Sun | |
| Zambia | UTC+2:00 | Sat-Sun | |
| Zimbabwe | UTC+2:00 | Sat-Sun | |


**Commercial offer:** $500–1,000 build + $300/month. Monthly, cancel anytime. 48-hour custom demo on their own knowledge base, no commitment.

---

## 13. Open Items

Status as last recorded 2026-10-10; verify live state with the §3 signals. Finished items were removed in v2.

| Item | Owner | Blocks |
|---|---|---|
| **Drafting simplification (operator decision 2026-10-18).** The current claims-library and review machinery is judged too heavy and is to be simplified and strengthened for positive replies. The operator is researching and will return with three outreach versions (pain-first, observation-first, give-first) to A/B test (about a third of new leads each, ~2 weeks, judged by positive replies). Version C needs a quick demo on a prospect's own website — whether Nova can build that fast is unanswered. `NovaScout_DraftingSkill.md` v3 and `DRAFTING_REVIEW.md` are expected to be replaced | Abdullah | Reply rate |
| **Fix the "two CROs" proof line (`PR-BOTH`)** — Vertex Clinical Research is a research center, not a CRO (§12). Deferred to the end of the drafting rework. Also confirm the NoblePath proof claim | Abdullah | Honest CRO-side proof |
| **Publish the workflows imported 2026-10-10 for sites and SMOs, in this order:** Mailbox Watch (`mailwatch0001`, first — it is the opt-out path), Reply Assistant (`reply0001`), Follow-Ups, Drafting, Scoring, Enrichment — all were published and firing before, so their old in-memory versions keep running until each is published (§3) — then, last and only after the licence row below, Trialsites (`trialsites0001`, never published). Migration `019` is applied live | Abdullah | Sites and SMOs entering the pipeline |
| **Switch the site and SMO claims on, then confirm them.** The 18 lines (`company_types` site/SMO) are seeded `active = false, confirmed = false`. **1.** After Drafting and Follow-Ups are published, tick `active` on all 18 in NocoDB (site drafts are then written and held `unconfirmed-claim`, so nothing reaches a site unread). **2.** Read each line against its `note` (two say "operator to confirm": whether the tenant prompt qualifies a CRO choosing sites the way it does a sponsor, and for `BEN-SMO-NETWORK` that `capture_investigator_registration`'s description still names NoblePath's network in code) and tick `confirmed`. Do not tick `active` before step 1's publish: the old in-memory Drafting would offer site lines to CRO leads | Abdullah | Any site or SMO email being sent |
| **Trialsites says "Free for non-commercial use"** on its API page (no other licence text). Nova Scout is commercial outreach. Ask AIMR (`info@aimronline.org`) for permission, accept the risk, or don't publish `trialsites0001` | Abdullah | Publishing Trialsites |
| **Push `main` to GitHub** so `nova-one-pager-sites.docx` exists at `raw.githubusercontent.com/abdullahamir2308/nova-scout/main/` (the URL a site/SMO reply notification links; the CRO one-pager is already there) | Abdullah | A working site one-pager link |
| **The CRO pipeline is out of supply** (measured 2026-10-10): 506 leads — 271 `scored`, 157 `disqualified`, 44 `sent`, 33 `drafted`, 1 `replied`; nothing at `ingested`, `enriched` or `contact_found`; the contact queue empty (11 leads at fit ≥ 50 retired to `needs_manual_contact`); 15 approved unsent first touches ≈ 0.75 days at the 20/day ceiling. Sites and SMOs (Workflow 1b) are the next supply; the 11 manual leads are the only CRO leads a human can still add | Abdullah | Sending beyond the backlog |
| **Start Docker Desktop with Windows.** `AutoStart` is `false`: after every reboot the whole stack (Send, Mailbox Watch, IMAP Health, Follow-Ups) stays down until someone starts it by hand (9 days once). Turn on "Start Docker Desktop when you sign in" | Abdullah | Every scheduled workflow, opt-out handling included |
| **The ichgcp scraped address is taken as-is, including a `media@` or `marketing@` inbox.** The site source refuses non-business mailboxes; the scraped source has no such list, because adding one would change which contact 100+ processed leads hold. Two of 44 contacts are affected (`media@avancecro.com`, lead 1341; `marketing@clinixir.com`, lead 1387). Decide: apply `NEVER` from `code_site_emails.js` to scraped addresses and re-queue those leads, or reject those drafts by hand | Abdullah | Those two leads |
| **`nova-one-pager.docx` has no generator in this repo.** It was edited in place (sponsor terminology, five code-confirmed capabilities, website as the only knowledge source, demo step "You share your site URL"). A regeneration must carry the same changes, then pass `audit_drafts_vs_onepager.py`, **then rerun `n8n/drafting/build_onepager_sites.py`** (it stops if any phrase is not found exactly once) | Whoever regenerates it | One-pager consistency |
| **90-second Nova demo recording** — not made. Do not reference it in any draft until it exists | Fatima | — |
| **Review the exclusions and the flagged list.** Syria is excluded as "being unwound" (§12) and is the first to revisit; look at which retained-and-flagged countries the directory contains before their shard runs. A hand-imported lead from an excluded country would still score on its other factors | Abdullah | A sanctioned jurisdiction reaching `leads` |
| **A country the directory starts listing after 2026-10-06 has no clock yet.** The build checks only the committed snapshot. It fails safe (a `no-send-clock` note, nothing spent). Refresh the snapshot from a plan run's artifact, add the country to §12's table and to `code_decide.js`, rebuild, and re-queue the lead to `contact_found` | Abdullah | A new directory country reaching a draft |
| **Sync NocoDB's metadata** so the grid shows the new `drafts` columns (`approved_by`, `approved_at`, `hold_reason`, `claim_check`) and tables; filter the review grid to `status = pending` to make it the exceptions queue | Abdullah | Seeing hold reasons in the grid (the digest shows them meanwhile) |
| **Make the prompt-cache batch split safe** (optional, ~4%): resolve upstream items by `lead_id` instead of `pairedItem` in `code_assemble.js` and `code_approval_apply.js`, with mutation tests and a dry run (§9 Workflow 4) | Abdullah | Nothing — an optimisation |
| **Re-queue recipes** (after an Apollo plan upgrade or a manual `leads` edit): `DELETE FROM contact_attempts`; `DELETE FROM contacts WHERE apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL`; delete an `ingest_log` row to force a re-ingest of that CSV version | Abdullah | Re-queueing |

---

*Document version 2. Update when any architectural decision changes. Do not let sessions drift from this spec.*
# Nova Scout — Master Reference Document v1
## AI-Automated BD Pipeline for Nova Agent Kit

> Paste this document into the Claude Project tab before every build session.
> Single source of truth for architecture, stack, and build decisions.
> Do not deviate from anything marked **LOCKED**. Ask before changing anything marked **CONFIRMED**.

---

## 1. Project Overview

**What we are building:** An agentic outbound pipeline that finds small-to-medium CROs worldwide, enriches and scores them against our ICP, drafts personalised outreach, and sends and tracks it. Since 2026-10-03 an email draft that passes every check approves itself; a human reviews the exceptions queue — what the checks held, and every LinkedIn DM — and reads a daily digest of what went out (Section 9, Workflow 5).

**Why:** Nova Agent Kit is built and live at two CROs. The remaining problem is purely distribution. Manual prospecting does not scale; this replaces ~8 hours/day of manual work with a short morning pass over the exceptions queue and the digest.

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
1. `qwen3.5:9b` (Q4_K_M, 6.6 GB) — start here. **Still the model for enrichment and scoring.** It drafted v1 and v2.
2. ~~`qwen3.5:9b-q8_0` (11 GB, near-lossless) — if drafting quality disappoints.~~ **Skipped, 2026-10-02.** Drafting skill v3 went straight to step 3: the 9B needed verbatim line-picking to stay safe, the copy came out stiff, and a better quant of the same model would not change that design.
3. **Claude Sonnet 5.5 for the drafting node only — in use since 2026-10-02 (drafting skill v3).** The follow-ups (Workflow 6) are composed by the same model, with the same request parameters, since the same day (skill v3 §8). Since 2026-10-03 the same model, same parameters, also runs the **claim check** that gates auto-approval (Section 9, Workflow 5) — a second call per email draft, never a cheaper model: the build refuses a check model or effort that is not this section's.

**Drafting model: `claude-sonnet-5-5`**, effort `high`, through the Anthropic Messages API, $2/$10 per MTok (cache reads $0.20). Drafting node and the follow-up composer only (Section 9, Workflow 6); enrichment and scoring stay on local `qwen3.5:9b`, and the one-model rule above still governs everything that runs on the GPU. Request parameters were taken from the live Sonnet 5.5 docs on 2026-10-02 (released 2026-09-28; confirmed present with `GET /v1/models/claude-sonnet-5-5`), not carried over from older settings: no `temperature`/`top_p`/`top_k` (a non-default value is a 400 on this model, so the old drafting `temperature: 0.45` cannot be sent); `thinking` omitted, which runs adaptive thinking; effort set explicitly to `high`, the documented starting point for work that is neither agentic nor latency-sensitive; JSON through `output_config.format` with a strict schema; `max_tokens` 16000, because it covers thinking as well as text; no prompt caching, because a run's calls are concurrent and never read each other's cache (measured, see Section 9 Workflow 4). The key is the n8n credential `novascoutAnthropic01`, made from `ANTHROPIC_API_KEY` in `.env` by `n8n/drafting/provision_anthropic_credential.py` — a Nova-Scout-only key since 2026-10-02 (re-provisioned that day; `GET /v1/models/claude-sonnet-5-5` answered 200 with it). **If a call fails, the lead stays queued for the next run. There is no fallback to the local model** (skill v3 §6), so the quality of what reaches review does not depend on which model answered.

**Note on the price:** Sonnet 5.5 costs 2× Haiku 4.5 ($1/$5), not less. It is chosen anyway because drafting volume is 10–20/day. At that volume, capability wins and price is noise. Measured cost is in Section 4.

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

1. **Override the default `presence_penalty`.** Qwen3.5 ships with `presence_penalty: 1.5`. **Tested correction:** the failure mode is NOT malformed JSON as originally guessed here — Ollama's grammar-constrained `format: json` mode makes syntactically invalid output impossible, which masks the problem. The real failure is silent, field-level data loss on copy-from-source fields: at 1.5, `founder_linkedin` and `therapeutic_areas` entries came back null/truncated even when clearly present in the source text, because the penalty discourages "repeating" tokens the model is meant to be copying, not generating. Output is valid-looking JSON with quietly missing data — more dangerous than a visible parse error, since it can pass silently into scoring. At `presence_penalty: 0`, all fields extracted correctly. For extraction and scoring nodes set `presence_penalty: 0`, `temperature: 0.1`. For local drafting (v1–v2, retired 2026-10-02 — drafting is on the API now, see the ladder above), `temperature: 0.45`, `presence_penalty: 0.3` — corrected from an original guess of 0.7 after Sprint 4 testing: at 0.7 the model fabricated ("our own staff data"), joined unrelated facts into an untrue claim, and on one lead inverted the entire pitch — telling the prospect they already owned the product being sold. 0.45 fixed it; verified against real failure examples, not reverted on guesswork.

2. **Cap the context window at 16K–32K.** These models advertise 262K native context. Do not use it — KV cache at full context adds 4–8 GB and will push you off the GPU. Scraped pages are 5–20K tokens; 32K is generous.

3. **Thinking mode — tested WRONG in the original version of this doc.** For this build, thinking is ON by default, not off. Two compounding traps: omitting `think` entirely burns the whole token budget on reasoning traces and returns empty content. Worse — on `/api/chat`, setting `think:false` silently disables `format` schema enforcement, so the model answers in free prose instead of structured JSON, with no error. Keeping thinking on to preserve JSON enforcement works but costs 30–50s/lead. **Use `/api/generate`, not `/api/chat`, for structured extraction** — it honours `think:false` together with the schema correctly, same valid output in ~5s. This is load-bearing for every future Ollama structured-output node (scoring rationale, drafting) — verify it still holds on that model/endpoint before assuming it, don't just copy the setting forward blind.

**Parallelization principle, confirmed by real testing:** only the GPU call needs to be serial (`OLLAMA_NUM_PARALLEL=1` still holds for inference). Fetching/I/O steps do not — running them serially when they don't need to be caused a real failure (10 dead-domain fetches serially blew a 300s task-runner timeout; fetching 5-at-a-time dropped that batch from 305s to 46s). Apply this shape to Workflow 3 and 4 too: parallelize lookups/fetches, serialize only the model call.

**n8n activation — there is no "Active" toggle, use Publish.** n8n 2.0+ replaced the classic active/inactive flag with a draft/published model, confirmed in official docs — this applies to Community Edition, not just enterprise plans. The UI element is a **Publish** button (near the workflow name, alongside Save), not a switch. Running `publish:workflow` from the CLI leaves a misleading half-state: `workflow_entity.active` flips true and the UI can show it as published, but the actual trigger-registration tables stay empty — no cron is registered with the running process, and it silently never fires. **Always publish via the n8n UI**, never the CLI, for anything that needs to run on a schedule. `active` in the DB does NOT gate a one-off `n8n execute --id=...` run — that works regardless of published state.

**Publishing locks to a specific version.** If the workflow is regenerated and re-imported after being published (which happens routinely here — build_workflow.py has rebuilt this workflow multiple times for fixes), the publish goes stale and must be redone against the new version. Re-publish after every workflow rebuild, not just once.

**Verification — the publication tables are a dead end on this instance, don't check them.** `workflow_publication_trigger_status`, `workflow_published_version`, and `workflow_publication_outbox` only get written when `useWorkflowPublicationService` is true. It defaults to false and `N8N_USE_WORKFLOW_PUBLICATION_SERVICE` isn't set here, so those tables stay empty regardless of whether activation actually worked — checking them proves nothing on this instance. **The real signals**, confirmed by reading the running source directly: `workflow_entity.active='t'`, `activeVersionId` non-null, `triggerCount` equal to the workflow's number of triggers (below), a new row in `workflow_publish_history`, and — the only fully conclusive proof — an execution with `mode='trigger'` (the execution column is `mode`, not `trigger_mode`; the value for a fired schedule is `'trigger'`, not `'cron'`). `triggerCount=0` on its own is not evidence of a broken trigger node — it's set only after successful activation, so it reads zero on any never-activated workflow no matter how correctly built.

**`triggerCount` is a count, not a flag — check it against the workflow's trigger nodes, not against 1.** Read from `countTriggers()` in n8n 2.35.7's `active-workflow-manager`: every trigger node except Manual, plus every polling node, plus every distinct webhook node. A single-schedule workflow reads 1. **Mailbox Watch reads 2, and that is correct** — it has two IMAP triggers, Sent Folder and Inbox (verified 2026-09-16).

**These signals prove a publish happened, not that the triggers are connected now.** When a running trigger fails — an IMAP socket closed by the server, say — n8n drops *all* of that workflow's triggers from memory, records an activation error, queues a reactivation, and writes nothing to the database: `active`, `activeVersionId`, `triggerCount` and the publish-history row stay exactly as they were while the workflow listens to nothing. The reactivation retries forever, but the wait starts at 1 s and doubles after each failed attempt up to a **24-hour cap** (`WORKFLOW_REACTIVATE_MAX_TIMEOUT`), so after an outage of a few hours the next attempt can be roughly as far away again — the trigger stays down after the far end is back. Only a `mode='trigger'` execution after the failure, or the log line `Activation of workflow "…" was successful!`, shows it recovered. What this has done in practice is recorded under Workflow 6.

**n8n execute against an already-running container needs runner env overrides.** A one-off `docker exec ... n8n execute --id=...` against the live container conflicts with the main process's own task-runner broker unless you pass `-e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false`. Without these it can hang or fail confusingly.

**Verify before building anything on top:** run `ollama ps` after loading and confirm the model shows 100% GPU, not a CPU/GPU split.

**Escape hatch — TAKEN 2026-10-02 (drafting skill v3).** The plan was to swap only the drafting node to the API if local drafts needed heavy editing after two weeks of real use. It was taken on a design reason instead: composition needs a frontier model, and line-picking was what kept the 9B safe. Only the drafting node moved, to Sonnet 5.5 rather than Sonnet 5; the follow-up composer joined it the same day (it is low-volume too: at most two per emailed lead). Enrichment and scoring stay local permanently — those are the high-volume nodes.

---

## 4. Cost Model — LOCKED

| Item | Cost |
|---|---|
| Outreach domain | ~$10/year |
| Zoho Mail Lite mailbox | ~$12/year |
| n8n, Postgres, NocoDB, Ollama | $0 |
| ClinicalTrials.gov API | $0 |
| Apollo | $0 — but see correction below |
| Claude API — drafting node only (Sonnet 5.5, $2/$10 per MTok, since 2026-10-02) | **$0.028 per drafted lead, measured** (2026-10-02 run: 4 leads, 13,605 input + 8,613 output tokens = $0.1133; 6,634 of the output tokens were thinking). A low-context lead costs $0 — it never reaches the model. At Section 3's 10–20 drafted leads/day: **~$8.50–$17/month**. Effort is the lever if that matters: thinking is ~77% of output. **Follow-ups** (same model, since 2026-10-02): **$0.0176 per follow-up, measured** (3 follow-ups, 8,006 input + 3,688 output tokens = $0.0529), at most two per emailed lead. **Claim check** (same model, since 2026-10-03, one call per email draft that passes the deterministic rules — none for a LinkedIn DM, a low-context note, a tagged draft, or with auto-approval off): **$0.0170 per check, measured** in the 2026-10-03 dry run (7 checks, 19,606 input + 7,974 output tokens = $0.1190) — roughly +60% on a first touch and +100% on a follow-up. |
| **Total** | **~$22/year + electricity + the drafting API line above (~$100–$200/year at full volume)** |

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
- First touch: body 70–110 words, never more than 125 (drafting skill v3; was under 80 words through v2). The body is everything between the greeting and the opt-out line.

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
Sources → 1 Ingest → 2 Enrich → 3 Score → [Apollo] → 4 Draft → 5 Approve → 6 Send → CRM
                                                                         ↑   (auto, or HUMAN  │
                                                                         │   for exceptions)  │
                                                                         └──── learn ─────────┘
```

**Changed 2026-10-03 (migration `014`):** step 5 is no longer a human gate on every draft. An email draft that passes the deterministic checks and the Sonnet 5.5 claim check approves itself inside Workflow 4 (first touch) or Workflow 6's Follow-Ups; everything else — and every LinkedIn DM — waits for a human. Section 9, Workflow 5.

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
  key (PK: operator_email|auto_approve_email), value, updated_at

digest_log
  digest_day (PK), covers_from, covers_to, sent_at, summary jsonb

claims_library
  code (PK), slot (description|angle|benefit|proof|ask|link), body, countries[],
  capabilities[], measured (bool), active (bool), confirmed (bool), note, updated_at
```

**Added for Workflow 6 (migration `007`).** `outreach_log.message_id` is the Message-ID the SMTP server accepted — NULL means a *claim*, written before the SMTP call so a crash can never cause a second send — and `draft_id` records which approved draft went out, so the learning loop can tell which variant earned a reply. `mailbox_sent` mirrors the mailbox's Sent folder, because Section 5's warm-up ceiling belongs to the mailbox, not the workflow: the operator's manual sends count against it too. `inbound_messages` records every INBOX message once, keyed on Message-ID, which is what makes reply handling idempotent. `replied_queue` is the read-only NocoDB view Section 9 asks for.

**Added 2026-09-15 (migration `008`).** `settings` holds runtime values that belong to the operator rather than the code — today only `operator_email`, where reply notifications go — so they never land in committed workflow JSON. Written from `.env` by `n8n/sendtrack/sync_settings.py`, read by Mailbox Watch at runtime; a CHECK keeps it to one lower-cased bare address.

**Added 2026-09-16 (migration `009`).** `mailbox_health` is the IMAP health check's memory (Section 9, Workflow 6) — one row, `check_name='imap'` — so the operator gets one alert per problem plus reminders, not an email every tick: whether the last check was healthy, which problems it found, how many checks in a row have failed and since when, and when the operator was last alerted about which problems. Written only by the IMAP Health workflow; a CHECK keeps `problems` to the four codes above.

**Added 2026-09-19 (migration `010`).** `claims_library` is the approved claims library of `NovaScout_DraftingSkill.md` v2 (§4): the only sentences in a first touch that are not prospect facts, which Workflow 4 inserts verbatim. It belongs to the operator, not the code, so — like `settings` — it is read at runtime and never baked into workflow JSON: an edit reaches the next draft with no rebuild. Seeded from skill §4 verbatim, with every line `confirmed=false` because §4 is marked "DRAFT, human confirms before first use"; a draft built from an unconfirmed line is tagged `unconfirmed-claim`. From here on **the table is the library**, and §4 records only what it started as. The skill's rules for each slot are CHECKs, so a broken line is refused when it is saved: a problem ends in "?", an outcome says "we built", there is no URL outside the `link` slot, the link is one plain URL (never LinkedIn, never a PDF), and `countries` holds only Section 12 geographies. On a proof line, `countries` is the geography match. The seed sends Mexico, Brazil and Argentina to Vertex; Turkey plus Egypt, UAE, Romania, Hungary, Poland and Czech Republic to NoblePath (reading "nearby" is a judgement, and changing it is one edit); and every other country to PR-BOTH, the line with no countries. `measured` marks PR-TR-N / PR-MX-N: empty and inactive by design, and when filled from Nova's own analytics they replace the plain line.

**Changed 2026-10-02 (migration `012`, drafting skill v3).** The library is no longer pasted in verbatim; the drafting model composes each email from one prospect fact plus these lines, and may rephrase a line but never widen it. So the slots became skill v3 §4's — `description`, `angle`, `benefit`, `proof`, `ask` (+ the optional `link`) — and v2's per-slot CHECKs (a problem ends in "?", an outcome says "we built") were replaced by skill v3 §3's rules for what any claim may say: no line names the product (the model adds "(we call it Nova)" once), "AI" only in a description line and at most once, never "AI-powered" or "chatbot", no guarantee, nothing calling the prospect a sponsor, and an ask is exactly one question. The geography CHECK, the link rules and the measured-proof rule are unchanged. The table was reseeded from skill v3 §4, every line `confirmed=false`, after the §4 `[verify]` lines were checked against the Nova Agent Kit source: BEN-CAPTURE, BEN-BRIEF and BEN-SEE were narrowed to what the code does, ANG-TIME was supported as written, and each row's `note` records the original wording and the evidence. The reseed runs only while v2-shaped rows exist, so re-running `012` never overwrites a human's v3 edit. A database built fresh runs `010` → `011` → `012` → `013` and ends on the v3 seed as `013` settles it. The v2 rows `012` replaced are kept verbatim in `postgres/backups/claims_library_v2_pre_012.json` (15 rows, as backed up just before the reseed).

**Changed 2026-10-02 (migration `013`) — three claims settled, and capabilities.** The operator settled the lines the `012` code reading questioned. **D2 / BEN-BOOK:** Nova sends the booking link (`CALENDLY_BOOKING_URL`, returned by `capture_sponsor_lead`) and the sponsor books — it does not book; D2 now ends "sends them your booking link" and BEN-BOOK reads "It sends qualified sponsors your booking link, so they can book a call with your team." **BEN-247:** a tenant's knowledge base is one text file, filled only by the website scraper (which skips PDFs) and loaded verbatim into the system prompt; there is no code path for documents a client provides, so the line says "from your own website" and nothing about SOPs. **BEN-ROUTE:** "not in another dashboard" dropped — Nova has its own dashboard. `nova-one-pager.docx` was edited to match (website only, demo step "You share your site URL", no "new dashboard" sentence). A row is reworded only while it still holds the `012` text, so a human edit is never overwritten, and two CHECKs pin the decisions (`claims_nova_does_not_book`, `claims_no_sops`). **`capabilities[]`** names what each description and benefit line asserts, from a closed vocabulary (answers, qualifies, captures-lead, study-details, booking-link, deck, routes-leads, dashboard, configured); a CHECK requires it on every description and benefit line with text and forbids it elsewhere. It serves skill v3 §3's new rule — the description and the benefits must not repeat the same capability (Section 9, Workflow 4).

**Added 2026-10-03 (migration `014`) — auto-approval.** `settings` gains `auto_approve_email` (`true`/`false`, a CHECK refuses anything else), seeded `true` — on by default; the SQL function `auto_approve_email_enabled()` is the one definition every reader uses, and a missing row reads as on. `sync_settings.py` writes it from `.env`'s `NOVASCOUT_AUTO_APPROVE_EMAIL` when that is set. `drafts` gains `approved_by` (`auto` = approved by the workflow that wrote it; `human` = anyone else), `approved_at`, `hold_reason` (why auto-approval held it, `<code>[: detail]` joined by `; `) and `claim_check` (the claim check's verdict on every statement, the model and the usage; never cleared, so an auto-approval a human later rejected stays recoverable). Every approval that existed before was a human's and was backfilled `human`; the pending LinkedIn and low-context drafts got their hold reason. A CHECK keeps `approved_by` to approved/sent drafts and `auto` to email. **The rules are on the table, not only in the workflows** (migration `006`'s principle): the trigger `drafts_approval_rules` turns any `approved_by = 'auto'` approval that breaks the flag, the email-only rule, low-context, the tags, an active-and-confirmed claim code, or lacks a passing `claim_check`, into a hold (`db-guard: ...`) — a hold, not an error, because a failed write would make Workflow 4 redraft the lead, a paid call, every 30 minutes. It also stamps every other approval `human`, clears `approved_by` when a draft leaves approved/sent, records Send's recipient refusals as `smtp-rejected`, and sends an auto-approved draft back to a person if its text is rewritten underneath it. On `claims_library`, a trigger un-confirms a confirmed line whose text, slot, countries or capabilities change — a confirmation is of the text that was read. `digest_log` is the Daily Digest's memory: one row per operator day whose digest SMTP accepted.

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

**Prompt constraints (skill v3):** body 70–110 words, never more than 125; minimum two specific facts, one geography-matched live customer (drafting skill v2; this replaced "mention the live NoblePath demo"), no adjectives like "revolutionary" or "cutting-edge", no merge-tag phrasing tells.

**Drafting skill v2 — shipped 2026-09-19 (`NovaScout_DraftingSkill.md`).** The grounding guard above is unchanged. It still counts the same four facts against the same threshold, the fact sheet and its boundary sentences are the same, and the v1 grounding tests pass untouched. What changed is what the model is asked to write. It now writes **a subject and two one-sentence hooks, and nothing else**. Its JSON schema has no other field, and the build refuses a schema that disagrees with the skill's "only the hook and subject are freely generated". The problem, outcome, proof and exactly one ask are `claims_library` lines (Section 8, migration `010`), inserted verbatim after the hook. They are rotated on `lead_id` and chosen to fit the ceiling. The model never sees them, so it cannot paraphrase a claim or join a fact onto one. A draft's `variant` records the line codes (`role-inbox/P2.O2.PR-MX.A2`), which is what the learning loop needs to tell which lines earn replies. Per skill §2 the 80-word ceiling counts the body, parts 1–5; greeting, opt-out and signature are outside it (v1 counted the opt-out).
- **Proof is geography-aware** (Section 12), resolved from each proof line's `countries`. A groundable lead the library cannot complete waits at `contact_found` rather than go out with a hole in it.
- **Links:** none in warm-up weeks 1–2, where the week is derived exactly as Workflow 6 derives it. After that the only URL allowed is the library's `link` line. A LinkedIn URL or a PDF is tagged in any week.
- **Subject:** 30–50 characters, never "Nova", never "Re:"/"Fwd:", none of the skill's banned words. It is generated with a worked-example table in the system prompt and built from the same fact the email opens with. A small confirmed headcount (Section 12's 5–100 band) is used only for the subject, and only when no openable fact exists. It never counts toward the guard.
- **Leaks from the worked examples are caught deterministically.** A digit token that is not in the lead's facts, or a therapeutic area the lead lacks, tags the hook or subject `-ungrounded`. Measured before the fix: a trial with no drug code got "INM004 trial — a quick question". Picking the trial's short name moved into code: its code, otherwise the first two content words of the title. Also measured: a hook nudged to vary its wording wrote "Your site lists work on the Efficacy of INM004…" — every word traces to the record, and the sentence is still untrue, because the trial is on ClinicalTrials.gov, not their site. That hook-vs-source failure is now tagged `hook-source`.
- **A redraft retires what it replaces.** A lead sent back to `contact_found` has its earlier pending or approved first-touch drafts set to `rejected` / `bad draft`, in the same statement that writes the new ones. Left approved, the old draft would be the first thing Workflow 6 sends, since it sends the oldest approved draft once a lead is `drafted` again. New drafts are always `pending`, bodies and `edited_body` are kept, and sent drafts and follow-ups are never touched.
- **Terminology — decided 2026-09-21: the pharma-side party is a "pharma team", and "sponsor" belongs to the CRO.** A trial hook opens "You're sponsoring …", the prospect as the registered sponsor of its own trial, so a library line that also said "sponsor" for the pharma company put one word on two parties in the same email. Measured on the queue that day: all 5 drafts that opened "You're sponsoring" then used a library line saying "sponsor" for the pharma side. What a CRO reads now says "pharma team" (noun) or "pharma-team" (modifier): `claims_library` P1–P3, O2 and O3; the subject worked example in the system prompt and in skill §3 (`Pharma-team inquiries after hours`; a lead whose subject source is `problem` is steered to it, so it decides a real subject line); and `nova-one-pager.docx`. "Sponsor" stays only for the CRO's own registered-sponsor status. O2 also lost "in seconds": the one-pager says Nova answers "in real time" and nothing supports a seconds-level figure, so no first-touch line carries one, and a measured proof line (PR-TR-N / PR-MX-N) states only what Nova's own analytics show, in the same words. The seed in skill §4 and migration `010` keeps the original wording by design (the table is the library, and each reworded row's `note` says why), so a database seeded fresh from `010` starts with the collision until those five rows are re-applied. `n8n/drafting/audit_drafts_vs_onepager.py` checks all of this against the live table, the live queue and the one-pager. It is read-only; run it after a library edit, a redraft, or a regeneration of the one-pager, which is not built in this repo (see `n8n/drafting/README.md`).

**Regenerated 2026-09-19:** the 5 drafted leads were re-queued and redrafted under v2. The 4 approved v1 email drafts and the pending v1 LinkedIn drafts were retired, and 10 v2 drafts are `pending` (Vedic Lifesciences is still low-context, one fact). All 4 v2 email drafts pass Workflow 6's shipped send checks.

**Drafting skill v3 — shipped 2026-10-02 (`NovaScout_DraftingSkill.md` v3).** The grounding guard above is unchanged: same four facts, same threshold, same fact sheet and boundary sentences, and the v1 grounding tests pass with one anchor moved (below). What changed:
- **Model.** The drafting node is `claude-sonnet-5-5` on the Anthropic API, through the n8n credential `novascoutAnthropic01` (Section 3 has the request parameters). Enrichment and scoring stay on local `qwen3.5:9b`. If a call fails — an HTTP error, a refusal, a response cut off at `max_tokens`, or text that is not the schema — nothing is written and the lead stays `contact_found` for the next run. There is no fallback to the local model.
- **Composition, not line-picking.** The model writes the whole email and the LinkedIn DM from the fact it is told to open on plus the lead's approved claims (`claims_library`, Section 8, migration `012`): every description, angle, benefit and ask, and only the proof line matched to the lead's country. It may rephrase a claim, never widen it. Its JSON schema is subject, body and **ask as a field of its own** for each channel, plus the claim codes each message used. The ask is separate so "exactly one ask" can be checked: one question there, no request in the body. The build refuses a schema without a body and an ask, and refuses an Assemble node whose required fields differ from the schema.
- **Skill §3 enforced in code (`code_assemble.js`), as tags on `variant`:** body (everything between the greeting and the opt-out) 70–110 words is the target and above 125 is `long`; `ask-none` / `ask-multiple` / `ask-scheduling`; the existing link policy (`link-in-warmup`, `unapproved-link`, `linkedin-link`, `pdf-link`, `urls`); `product-name-repeat` / `product-name-unbracketed` / `subject-product`; `ai-repeat` / `ai-powered` / `subject-ai`; `prospect-sponsor`; the forbidden claims, `claim-guarantee`, `claim-number` (a digit, number word, multiplier or % that neither the lead's facts nor its offered claims hold), `claim-visitor-id`, `claim-named-tool`, `claim-language` and `claim-chatbot`; `proof-missing` / `proof-geo`; `ungrounded-area`, `hook-opener` and `hook-source` carried from v2; the subject rules (`subject` 30–55 characters, `subject-reply`, `subject-caps`, `subject-ungrounded`); `claim-code` for a code the lead was not offered; `claim-books` (Nova booking a call itself), `claim-sop` (SOPs or client documents as its source) and `claim-repeat` (two claim codes sharing a capability), added with migration `013`; and `unconfirmed-claim`. `n8n/drafting/test_rule_mutations.py` breaks every one of them in turn and proves the unit suite fails each time (39 tags and 19 behaviours after migration `013`, none survived).
- **Attribution.** `variant` records each message's own claim codes in slot order (`role-inbox/D2.ANG-HOURS.BEN-247.PR-TR.A1`). On the first run the model listed no ask code for three of eight messages, one of which used A1 word for word. So when the model leaves out the ask or the proof, code attributes it from the text: the ask goes to the library ask sharing at least half its words, and the proof to the line whose deployment it names. The model is never trusted to copy an identifier.
- **Prompt caching is off on purpose.** n8n's HTTP node sends every item's request before awaiting any, so one run's calls are concurrent. Measured: four calls wrote the cache four times and read it never. Cron runs are 30 minutes apart, past the 5-minute cache lifetime, so caching only cost the 25% write premium.
- **The skill's worked example is shown to the model as written, except for one narrowing.** The skill's §5 example says the assistant "collects their study brief". BEN-BRIEF was narrowed to "therapeutic area and study phase" because the code captures only those, so the prompt carries the narrowed phrase. `EXAMPLE_NARROWINGS` in `build_workflow.py` refuses to build once the skill's example changes.

**No repeats — added 2026-10-02 (skill v3 §3, migration `013`).** The description and the benefits must not repeat the same capability. On 2026-10-02 the model paired D2 ("answers sponsors … books the call") with BEN-247 ("It answers sponsors …") in 3 of 4 email drafts, twice adding BEN-BOOK too. Assess Grounding now shows each description and benefit line with its capabilities and names every offered pair that shares one ("D2 with BEN-247 (both: answers)"); the system prompt carries the rule; Assemble Drafts tags `claim-repeat`. Lead 50 was redrafted alone on 2026-10-02 after `013`: drafts 102 (email, D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1, 111 words) and 103 (LinkedIn) `pending`, 91–92 retired, $0.0376.

**Terminology — reversed 2026-10-02 (skill v3).** "Sponsor" is again the CRO's client (biotech, pharma, device or academic), in the claims, the prompt, the drafts and `nova-one-pager.docx`. The prospect is never the one sponsoring: the v2 collision came from hooks that opened "You're sponsoring …", so that phrasing is banned, not the word. The trial fact now reads "registered under your company" instead of "with you as the sponsor". `audit_drafts_vs_onepager.py` was inverted to match: it fails on text that calls the prospect a sponsor, or on any leftover "pharma team", in the library, the live drafts or the one-pager. The 2026-09-21 paragraph above records the v2 decision this replaces.

**Redrafted 2026-10-02 under v3.** All 5 leads were re-queued and drafted three times while v3 settled: the first run found the shared claims list, and the second found the missing ask codes. The 4 groundable leads (7, 50, 91, 104) now have pending drafts 89–96; Vedic (78) took the low-context branch again with no API call. Every earlier pending or approved first-touch draft was retired as `rejected` / `bad draft`, including approved LinkedIn drafts 62, 64, 66 and 68. Leads 7, 91 and 104 had already received their v2 email on 2026-09-22, so each was put back to `sent` straight after its run, keeping follow-ups and reply matching intact. Their new email drafts can never send, because Workflow 6 refuses a first touch to a lead it has already emailed (`already-contacted`). The three runs cost $0.1230, $0.1176 and $0.1133.

**Auto-approval — 2026-10-03 (migration `014`).** Between Assemble Drafts and Write Drafts & Advance, the Approval Gate and the claim check decide whether the email draft is written `approved` (`approved_by = 'auto'`) or `pending` with a `hold_reason`; the LinkedIn DM and every low-context note are always held. The rules, the check and the dry run are in Workflow 5. Assemble Drafts hands the gate the lead's fact sheet and offered lines, so the check sees what the drafter saw; the batch query carries `auto_approve_email` like it carries the library.

**Output:** email variant + LinkedIn variant per lead into `drafts`.

### Workflow 5 — Exceptions Queue (HUMAN) — was the Review Queue
**Changed 2026-10-03 (migration `014`): the review queue is now an exceptions queue.** An email draft — first touch or follow-up — no longer waits for a human by default. The workflow that wrote it approves it (`approved_by = 'auto'`) when, and only when, all of these hold; anything else is written `pending` with the reason in `hold_reason`, and *that* is the queue:

| Rule | Held as |
|---|---|
| `settings.auto_approve_email` is on (on by default; off, nothing auto-approves and no claim check is paid for) | `auto-approve-off` |
| it is an email — **a LinkedIn draft never auto-approves** (Section 6) | `linkedin` |
| it is not a low-context note | `low-context` |
| zero rule tags: nothing after `+` in `variant` (every Workflow 4 / skill v3 rule tag, `unconfirmed-claim` included) | `rule-tags: ...` |
| every claim code it records is active and `confirmed` in `claims_library` | `unconfirmed-claim: ...` / `no-claims` |
| a second Sonnet 5.5 call (Section 3's model and parameters) passes it: each product claim compared with the confirmed claims, each prospect fact with the enrichment record — a widened claim is a failure | `claim-check: widened "..." (BEN-SEE)`, `claim-check-incomplete: ...`, `claim-check-failed: ...` |

**How it is built.** Two Code nodes, `n8n/drafting/code_approval.js` (Approval Gate) and `code_approval_apply.js` (Apply Claim Check), sit between Assemble and Write in Workflow 4 and in Follow-Ups, embedded verbatim in both, so a first touch and a follow-up are judged by the same functions. The deterministic rules run first, so a draft they hold costs no API call. The checker gets exactly what the drafter had — the lead's fact sheet, including the ClinicalTrials.gov trial, which is looked up per run and stored nowhere, so this could not be a separate after-the-fact workflow — and, for a follow-up, the enrichment record plus the first email as context (the record holds no trial, so a follow-up naming one is held). **The model judges statements; code decides.** Its schema has no overall verdict: it returns every statement sentence by sentence with a kind (claim / prospect / none), the claim code or record fact it compared it with, and a verdict, and Apply Claim Check approves only if every claim is `supported` by a code that really is a confirmed line, every prospect fact is `supported`, and the quoted sentences account for the whole email — a sentence it skipped is one nobody judged. A failed call (HTTP error, refusal, cut-off, wrong schema) holds the draft; there is no fallback model. The system prompt's example of a widening is deliberately *not* draft 99's sentence, which is the held-out case the dry run proves the check catches. Migration `014`'s trigger re-checks every deterministic rule on the table and turns a bad auto-approval into a hold, so a bug in the workflows can make them approve less, never more (Section 8).

**Send is unchanged** (the ceiling, business hours and every guard): an auto-approved email is an approved email, and goes out in its recipient's business hours like any other.

**What the human does now.** The NocoDB grid on `drafts` is still the interface and Approve · Edit · Reject still apply, with **rejection still requiring a reason** (bad fit / bad draft / already contacted / wrong contact — the learning loop's training data). What needs a person is every `pending` draft: each LinkedIn DM (always — Section 6), and every email the checks held, with `hold_reason` saying why. A human approval is recorded `approved_by = 'human'`, and the hold reason is kept, so a held draft a person approves anyway is the evidence for tuning the checks. To stop an auto-approved email before it goes, reject it; editing its text makes the approval the human's. **The Daily Digest** (Workflow 6) reaches the operator once a day, from 08:00 PKT — before India, the earliest recipient, opens — listing every draft auto-approved since the last digest, every email sent and who approved it, and every pending draft with its reason. NocoDB shows the four new `drafts` columns after a metadata sync.

**Proven by dry run 2026-10-03, not live** (`n8n/sendtrack/dryrun/approval_dryrun.py`, 28/28 checks): the real Drafting, Follow-Ups, Send and Daily Digest workflows through n8n against a scratch copy of the database with migration `014` and the confirmed library, the composing model replaced by fixtures (so each draft's text is known) and the claim check real. A clean first touch (lead 50, draft 102's text) and a clean follow-up auto-approved and were both eligible to Send; a tagged draft (draft 85's real "books the call"/"SOPs" text) was held with its tags and never reached the check; draft 99's follow-up text was held — `claim-check: widened "so you know exactly what came in" (BEN-SEE)` — on two independent runs, and the same widening inside a first touch was held too; the low-context note and every LinkedIn draft were held; with the flag off nothing approved and no claim check ran; the table turned a direct bad auto-approval into a hold; the digest reached the operator address once and not a second time that day. The real database was unchanged. **The first run found two faults the unit tests could not:** the checker quoted the subject with the prompt's "Subject:" label, which the coverage check did not find in the email, and it judged the required "(we call it Nova)" an unsupported claim — both held a clean first touch. Coverage now drops a leading "Subject:" from a quote, and the prompt says the bracketed name is not a claim; both are pinned by unit tests and mutations. **Measured on the drafts approved by hand before this existed:** the check passes draft 102 and holds 99, 100 ("your team can review what came in and pick up whatever needs a person", BEN-SEE) and 101 ("rather than leaving the request waiting for someone on your team to reply", BEN-DECK) as widened — it is stricter than the human approvals were.

**The most serious bug found in this project, do not repeat it.** A Postgres view has no primary key and cannot be given one (error 42809). Without a PK, NocoDB cannot scope a write to one row — a single approve click on one draft went out as an unqualified `UPDATE review_queue SET status='approved'`, and an INSTEAD OF trigger applied it to every row in the view. It then returned an unrelated error, making the mass-approval look like a failed no-op. **This is the dangerous shape: silent full mutation plus a misleading failure signal**, in a workflow whose entire purpose is a human gate before sending. Fix: the view is read-only, so any write attempt fails loud and immediate (error 55000) instead of silently succeeding somewhere. NocoDB writes directly to `drafts` (which has a real PK), with approve/edit/reject validation enforced on `drafts` itself so it holds for every writer, not just NocoDB's UI. Any future change to this view's schema must re-verify this — confirm a real write behaves correctly against live data, never assume a clean join means a clean write path.

### Workflow 6 — Send & Track
**Send trigger:** Cron, business hours only, randomised intervals.
**Guard:** query today's send count; abort if at the warm-up ceiling. Ceiling is enforced in the workflow, not by discipline.
**Send:** SMTP node → mailbox. Log to `outreach_log`.
**Reply detection:** IMAP trigger polls the same mailbox. Any reply → kill follow-up sequence, set `status='replied'`.

**No HubSpot account exists yet — deferred, not dropped.** Original spec created a HubSpot deal on reply; skipped for now rather than routing into the wrong account (this pipeline's sender is Abdullah/Amitrix Labs, not the Fatima/NoblePath HubSpot the doc originally assumed). Substitute, using infrastructure this workflow already builds: replied leads get their own filtered view in NocoDB so they're never buried in the main queue, and a short internal notification email fires to the operator's own inbox (not the outreach mailbox) the moment a reply lands — reusing the same SMTP capability this workflow needs anyway, no new infrastructure. Re-adding a real CRM later is a small addition on top of this, not a rebuild.
**Opt-out detection:** reply matching no/unsubscribe/remove/stop → add domain to `blocklist`, permanent.
**Follow-up:** if no reply after 6 days, generate follow-up draft back into the review queue. Maximum two follow-ups, then mark lost.

**Shipped in Sprint 5 (2026-09-14) as three workflows** — `send0001` (cron → guard → claim → SMTP → log), `mailwatch0001` (IMAP on Sent and on INBOX), `followup0001` (cron → follow-up drafts / lost) — generated by `n8n/sendtrack/build_workflow.py` like every other stage, with migration `007` (see Section 8). Split because IMAP triggers and cron ticks start differently, and so each path can be executed headlessly on its own.

**Status, verified 2026-09-16 against the Section 3 signals (not the UI badge).** **Mailbox Watch is published and live:** published in the UI 2026-09-15 14:14 UTC, `active=t`, `activeVersionId` set and equal to the current version (whose nodes match `n8n/workflows/mailbox-watch.json`), `triggerCount=2`, one `activated` row in `workflow_publish_history`, and 11 `mode='trigger'` executions, all successful. **Send and Follow-Ups have never been published:** `active=f`, `activeVersionId` null, `triggerCount=0`, no publish-history row, and no execution of any mode. **Nothing has been sent to a prospect:** `outreach_log` holds 0 rows, and no recipient in the mirrored Sent folder matches a `contacts.email` or a lead's domain — every message there went to the operator, the team, or the mailbox's own domain.

**The warm-up ceiling belongs to the mailbox, not the workflow — derived from the real Sent folder, never assumed.** The operator's manual warm-up sends land in the same Sent folder and count against the same daily limit, so the ceiling is computed exactly as the IMAP pre-flight (`n8n/sendtrack/imap_preflight.py`) does it: warm-up starts on the sender-day of the first send with an external recipient, week = ⌊days since ÷ 7⌋ + 1, and only messages with a recipient outside `amitrixlabs.com` **other than the operator's notification address** count. An empty history is "not started": the first send is day 1 of week 1. The day is the sender's (Asia/Karachi, docker-compose's `GENERIC_TIMEZONE`), and each tick uses one clock value throughout, so a count cannot straddle midnight. Mailbox Watch mirrors the Sent folder into `mailbox_sent` (whole folder on every activation, so manual sends made while n8n was down are counted); the send path writes its own sends there the moment SMTP accepts them.

**It fails closed, and that has a consequence worth knowing.** The send path refuses to send if the Sent mirror has never read the folder, or if one of its own sends has not appeared there after 15 minutes — a dead mirror would undercount the manual sends with no error anywhere. The IMAP trigger only fires once a folder holds a message (node-imap emits `mail` on EXISTS growth only), so **nothing sends until at least one message is in Sent and Mailbox Watch is published.** Measured the day this shipped: the IMAP Sent folder held **0 messages** — the manual warm-up sends reported as started that day were not in it when checked. The workflow therefore computes "warm-up not started, week 1, ceiling 5" and currently refuses with `sent-mirror-never-ran`. **Re-measured 2026-09-15:** Sent held 2 messages, both on sender-day 2026-09-15 — one to the operator's own notification address, which does not count (below), and one external — so warm-up day 1 is **2026-09-15** (week 1, ceiling 5). **Mirror running since 2026-09-15:** it first read the folder when Mailbox Watch was published (14:14 UTC, 2 messages) and by 2026-09-16 had synced six times — on publish, on each new message, and after each reconnect — holding 4 messages, all `seen_in_sent_folder`. `sent-mirror-never-ran` no longer applies, so **the send path is unblocked and publishing Send is the only step left before a live send**: `status_now.py` on 2026-09-16 found all 4 approved email drafts eligible and decided `send=True` for one whose recipient was in business hours. Zoho filing SMTP-submitted mail into Sent is assumed, not yet observed; if it does not, the first live send will stop the send path (`sent-mirror-behind`) rather than over-send.

**Claim, then send — at most once.** The draft flips to `sent` and an `outreach_log` row with no Message-ID (a *claim*) is written before the SMTP call, so a crash between sending and logging can never cause a second send; a duplicate cold email is worse than a missed one. The claim re-checks channel, approval, unchanged body, verified contact, blocklist, reply/bounce and **re-counts the ceiling in SQL under an advisory lock taken in a separate, earlier statement** — the Postgres node runs its text as one implicit transaction, and under READ COMMITTED only a later statement's snapshot sees a concurrent claim; in a single statement two overlapping ticks could both count 4 of 5. SMTP refusals are split: account-level (auth, TLS, 4xx) → back to `approved`, retried; recipient-level 5xx → back to `pending` tagged `+smtp-rejected`, for a human. The Send Email node has no retry (a timeout after acceptance would send twice), and `appendAttribution` is forced off — at typeVersion 2.1 n8n appends "This email was sent automatically with n8n" plus a link by default.

**Per-draft checks.** `channel='email'` and `status='approved'` in SQL (Section 6 is enforced three times: the query never selects LinkedIn, Decide Send throws if one arrives, the claim refuses one); `contacts.verified` (the Workflow 3b flag this section said a send gates on); domain and recipient domain not blocklisted; no reply/bounce; first touch only to a lead never emailed, including by hand; not a low-context note even if approved by mistake; the Section 5 opt-out sentence **verbatim, immediately followed by the current signature** — drafts signed "Fatima" existed from before the sender changed, and a stale signature is refused rather than sent under the wrong name; at most one URL. **Business hours are the recipient's:** 09:00–17:00 local, Mon–Fri (Egypt Sun–Thu), fixed standard-time offsets per Section 12 country, **no DST by operator decision** — Poland, Czech Republic, Hungary, Romania and Egypt run an hour later than the table in summer. Pacing (Config, not locked): 10-minute tick, one message at most, 20-minute floor since the mailbox's last external send, then a 40% coin flip.

**Reply classification reads only what they wrote above the quoted thread.** Every reply quotes our email and our email says "reply 'no'" — classify the whole body and every reply is an opt-out. `no` counts only as the first word ("No, thanks", not "we have no chatbot"), also in Portuguese, Polish, Turkish, Hungarian and Romanian; Czech "ne" is left out because it opens ordinary Turkish questions. **Deliberate narrowing of "any reply → replied":** an out-of-office or a bounce is recorded but does not set `replied` — an out-of-office must not kill the follow-up sequence. `inbound_messages` is keyed on Message-ID, so a re-delivered message changes nothing and notifies nobody twice. The operator notification goes to the address in the `settings` table (migration `008`), which `n8n/sendtrack/sync_settings.py` writes from `.env`'s `NOVASCOUT_OPERATOR_EMAIL`; Mailbox Watch reads it at runtime, so no address is baked into the workflow JSON and changing it needs only a re-sync. Replies also land in the `replied_queue` view. **Notifications do not count toward the warm-up ceiling** (operator decision 2026-09-15): they go to the operator's own inbox, not a cold prospect, so a message sent only to that address is treated like a note to an `amitrixlabs.com` colleague — not external, so it touches neither the daily count nor the 20-minute pacing floor. **Why a table, not an n8n environment variable:** n8n 2.x blocks `$env` in Code nodes and expressions unless `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, and that would hand every Code node the whole container environment, `N8N_ENCRYPTION_KEY` and the DB password included — decided 2026-09-15 to keep n8n's default.

**Mailbox Watch is proven live only when it fires — nothing proves its IMAP connection is up between events.** Its first ~26 hours, reconstructed from the n8n and Postgres logs, the Windows System event log, and each INBOX message's arrival time (INTERNALDATE) against `inbound_messages.processed_at` — all times UTC:
- **2026-09-15 22:35 → 2026-09-16 03:25, host asleep.** At resume the server had closed the Inbox socket (logged 03:25:33) and three reconnects failed to resolve `imappro.zoho.com` while the network came back — the "DNS blip" — before one succeeded at 03:25:56. A reply that arrived at 02:22 was recorded at 03:25:58.
- **04:29 → 14:27, host shut down.** Windows shut down at 04:29 and restarted three times by 04:32; the Docker stack stayed down until 14:27. Postgres logged `not properly shut down` — the "ungraceful restart". Two INBOX messages that arrived at 10:41 and 11:26 were recorded at 14:28, when the Inbox trigger caught up on activation.
- **14:30:49, host up:** the server closed both sockets three minutes after that start; reconnected at 14:30:59.
- **15:23:36 and 16:05:52, host up** (no sleep, resume or shutdown in the Windows log): both sockets closed and `imappro.zoho.com` would not resolve for 41 s, then for 2 min 14 s — the second time n8n's backoff had grown to 64 s before a retry succeeded at 16:08:06. No mail arrived in either window.

Nothing was lost: every message was recorded once n8n was running and reconnected. But every Section 3 database signal stayed green throughout, and none of this was visible without digging. Three things keep it from being self-evident: the IMAP trigger cannot act as a heartbeat (it emits only when a folder grows, and a forced reconnect re-emits nothing); n8n's reactivation backoff (Section 3) can leave a trigger down for hours after the mailbox is reachable again; and when n8n or the host is down, nothing inside n8n can say so — mail that lands then is recorded on the next start, not before. It matters most for **Inbox**, the only path that catches an opt-out — Section 5 requires those honoured immediately. A reply that lands while Inbox is down *with the host up* sits unrecorded, un-blocklisted and un-notified, with no error anywhere, until someone notices replies have stopped.

**IMAP health check — built 2026-09-16, not yet published** (`imaphealth0001`, `n8n/workflows/imap-health.json`). The health check runs every 20 minutes and asks two questions. **(1) Does a fresh read-only IMAP session work?** It runs `n8n/sendtrack/imap_preflight.py` — the pre-flight's connection logic, unchanged — through the `imap-health` service in docker-compose.yml. The n8n image has no Python and n8n 2.x excludes the Execute Command node by default; **operator decision 2026-09-16: a sidecar container, not Execute Command**, which would give every workflow shell access (the same kind of exposure turned down for `$env` on 2026-09-15). The service gets only the mailbox's IMAP settings, mounts its code read-only, runs as `nobody` on a read-only filesystem, and publishes no port; the build refuses a port. **(2) Has Mailbox Watch missed anything?** The pre-flight's opt-in `--ids-json` lists the Message-ID of every INBOX and Sent message that landed in the last 48 hours; one that landed more than 15 minutes ago with no row in `inbound_messages` (INBOX, counted from Mailbox Watch's first run) or `mailbox_sent` (Sent) was **missed**. That is the proof a login alone cannot give that the triggers are still listening — **operator decision 2026-09-16: prove nothing was missed, not only that the mailbox is reachable.** Checked against live data the day it was built: all 6 INBOX and 4 Sent Message-IDs of the previous 48 hours matched Mailbox Watch's records exactly, so the pre-flight derives the key exactly as the workflow's Code nodes do.

**Alerting.** Problems are `checker-unreachable`, `imap-failed`, `inbox-missed`, `sent-missed`. No alert on one failing check — a host waking from sleep can tick the check before Mailbox Watch has reconnected, and mail that arrived during sleep looks missed for those seconds — so two consecutive failing checks are needed. Then one email to the operator address in `settings`, a reminder every 6 hours while the same problem lasts, a new email if the problem changes, and one when it clears; the all-clear says whether Mailbox Watch has re-read Sent since the problem began (a re-read means it reconnected; none means it either held on or is still in n8n's backoff). State lives in `mailbox_health` (migration `009`); an alert counts as sent only once SMTP accepted it, so a failed one is retried next check. Like a reply notification, an alert goes only to the operator and uses no warm-up slot. Worst case from a message landing to an alert: 15 minutes' grace plus two 20-minute checks, about 55 minutes. **Verified by dry run, not live** (`n8n/sendtrack/dryrun/health_dryrun.py`, 22 checks): the real workflow through n8n, the real checker and a real read-only IMAP session, against a copy of the database with an SMTP sink. Proven there: the live records compare clean (6 INBOX, 4 Sent, nothing missed); a removed INBOX row is found, stays quiet on the first check, alerts on the second — and when SMTP is down that alert is not recorded as sent and goes out on the next check — then stays quiet, then sends one all-clear once the row is back; a Sent row missing from the mirror alerts; the real pre-flight against an unresolvable IMAP host alerts with its own `connect` reason; a closed checker port alerts as `checker-unreachable`. The live database was unchanged. **What it cannot catch:** when n8n or the host is down the check does not run either — nothing inside this stack can report its own absence — and mail that lands then is recorded on the next start, as it was on 2026-09-16. **The gap stays open until IMAP Health is published in the UI** (Section 13).

**Follow-ups are composed, not a template — since 2026-10-02 (drafting skill v3 §8).** The drafting model (Section 3) writes each one from the first email as sent plus the approved claims; nothing else about the lead reaches it, so build rule 6 still holds by construction. Follow-up #1 is 40–70 words, refers back to the first email and adds exactly one angle or benefit that email did not use (no line it used, and no benefit sharing a capability with one it used, is offered); #2 is a short final note of at most 40 words with no new claim. Both are checked by Workflow 4's own rule functions — Assemble Follow-Up embeds `n8n/drafting/code_assemble.js` above its `// Node body` marker verbatim — plus §8's tags (`short`, `long`, `fu-no-new-claim`, `fu-repeat`, `fu-new-claim`). The greeting, the Section 5 footer and the quoted first touch are appended, so the send path accepts them; they land as `pending`: nothing follows up without a human. A failed call writes nothing and the lead is due again next run. A rejected follow-up uses its slot; the clock restarts at the later of the last send and the last follow-up drafted. Generated 2026-10-02 for the three leads emailed 2026-09-22: drafts 99 (lead 7), 100 (lead 91), 101 (lead 104), all `pending`.

**Follow-ups auto-approve under the same rules — 2026-10-03 (migration `014`).** A composed follow-up goes through Workflow 4's own Approval Gate and claim check (the same two Code nodes, embedded verbatim) before Write Follow-Up: approved by the workflow when it passes, `pending` with `hold_reason` otherwise — so "nothing follows up without a human" above no longer holds for a follow-up that passes every check. Only the note is checked; its subject is `Re:` + one already sent and the quoted first email already went out. Find Due Follow-Ups now also carries the flag and the enrichment record; the record goes to the checker only, never to the composing model, so build rule 6 still holds by construction. It holds no trial (Workflow 4 stores none), so a follow-up that names the prospect's trial is held for a person. Workflow 5 has the rules and the dry run. **Find Due Follow-Ups counts follow-up numbers, not rows** (2026-10-03): a #1 rejected and regenerated is still one slot, as Write Follow-Up's dedupe already read it — counted as rows, the regenerated #1 for leads 7, 91 and 104 would have taken #2's slot and the lead would have been marked lost instead of followed up. "A rejected follow-up uses its slot" still holds for a #1 that is only rejected. **Regenerated 2026-10-03:** drafts 99–101 rejected by the operator; a one-off copy of Follow-Ups (only its queue limited to those three leads and its dedupe ignoring 99–101; composer, gate and check the shipped nodes) wrote follow-up #1 again as drafts 104, 105, 106, $0.0497 composing + $0.0435 checking — all three held as widened (Section 13).

**Daily Digest — 2026-10-03 (`digest0001`, `n8n/workflows/daily-digest.json`, migration `014`'s `digest_log`).** Auto-approval puts emails in front of prospects that no person read, so the operator gets one plain-text email a day at `settings.operator_email`: **AUTO-APPROVED** (every draft the workflows approved themselves since the last digest, and what became of it), **SENT** (every email SMTP accepted, and whether a person or the workflow approved it), **HELD FOR A PERSON** (every pending draft, new ones marked, each with its `hold_reason` — the exceptions queue). It runs every 30 minutes and sends at the first tick at or after 08:00 operator time (Asia/Karachi), once per operator day — 08:00, before India, the earliest recipient, opens at 08:30 PKT, so an overnight auto-approval can be rejected before it goes. It covers from where the last digest stopped, so a day the host was off folds into the next digest; it is recorded only once SMTP accepted it, so a failed one goes out on the next tick. Sent only to the operator address, it uses no warm-up slot (the same rule as a reply notification). No address, no digest. Not an "every day at 08:00" schedule on purpose: n8n's day/hour schedules gate on the clock value of the last run, the Follow-Ups failure below.

**Why Follow-Ups runs every 30 minutes — root cause of the missed follow-ups, 2026-10-02.** Leads 7, 91 and 104 were emailed 2026-09-22 and had no follow-up 10 days later. None had replied (no `inbound_messages` row from their domains; `outreach_log.replied` false). The due query was correct — run on 2026-10-02 it returned all three. Two causes, both measured. (1) **The stack was not running.** Follow-Ups last executed 2026-09-22 19:00 UTC; between 2026-09-23 02:40 and 2026-10-01 23:05 PKT n8n executed nothing at all, while Windows was up 2026-09-27 15:03 → 09-28 03:06 and 2026-09-28 23:21 → 10-01 12:00 PKT (System log 12/13/6005/6006). Docker Desktop's `AutoStart` is `false`; on 2026-10-02 it was still not running 17 minutes after boot. `restart: unless-stopped` only helps once Docker itself runs. (2) **n8n skipped the one tick that found them due.** At 2026-10-02 00:00:29 PKT n8n was up (Send fired that second) and Follow-Ups did not run: n8n 2.35.7's Schedule Trigger gates an "every N hours" rule on `recurrenceCheck`, which compares the **clock hour** of the last run, persisted in `staticData` (`recurrenceRules: [0]`, from 2026-09-23 00:00 PKT), as `(hour − last + 24) % 24 ≥ N` — so a tick on the same clock hour days later reads as zero hours elapsed. Replaying the installed function with that state: 00:00:29 SKIPPED, 06/12/18:00 FIRES. Minutes intervals are counted on elapsed time in that function and are safe, so Follow-Ups now runs every 30 minutes (a quiet run is one query; a Claude call happens only for a lead that is due), and the Workflow 6 build refuses any hours/days/weeks interval above 1. On 2026-10-02 the old template version was unpublished and n8n restarted before its 18:00 PKT tick, so the composed #1 was not pre-empted by a template one.

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

**Second case study — implemented 2026-09-19 (drafting skill v2).** Nova is also live at Vertex Clinical Research (Mexico), not just NoblePath (Turkey), and a Mexico/Latin America lead finds a same-region reference more credible. Workflow 4's proof line is now geography-matched from `claims_library` (Section 8): Vertex for Mexico/LatAm leads, NoblePath for Turkey/nearby, both for everywhere else. Each proof line's `countries` is the mapping, so the operator owns it.

**Commercial offer:** $500–1,000 build + $300/month. Monthly, cancel anytime. 48-hour custom demo on their own knowledge base, no commitment.

---

## 13. Open Items

| Item | Owner | Blocks |
|---|---|---|
| Purchase outreach domain | Fatima | Everything downstream of Sprint 4 |
| Zoho Mail mailbox + DNS | Fatima | First send |
| Check ollama.com for a 9B in the Qwen3.6/3.8 generation | Fatima | Sprint 0 (defaults to `qwen3.5:9b` if none) |
| 90-second Nova demo screen recording | Fatima | Referenced in every draft |
| ~~Decide Sonnet 5 escape hatch for drafting~~ — **done 2026-10-02**: drafting moved to Sonnet 5.5 with skill v3 (Section 3) | Both | — |
| ~~Confirm the v3 claims library~~ — **done 2026-10-03**: all 20 active rows confirmed by the operator after review in `DRAFTING_REVIEW.md` (each row checked identical to what that document showed). From migration `014` on, editing a confirmed line's text un-confirms it | Both | — |
| ~~Publish Drafting and the Daily Digest~~ — **done 2026-10-03 06:42 UTC** (publish history). **Re-publish Follow-Ups (`followup0001`) in the UI:** it was published at 06:42 too, and the 06:45 re-import of the follow-up count fix (Section 9, Workflow 6) unpublished it. Until it is published again no follow-up is drafted and no lead is marked lost | Abdullah | Every follow-up |
| ~~Decide on drafts 99, 100 and 101~~ — **done 2026-10-03**: rejected (`bad draft`; widened claims per the claim check), and follow-up #1 regenerated through the auto-approval gate for leads 7, 91 and 104 as drafts 104, 105 and 106 — **all three held** by the claim check (Section 9, Workflow 5) | Abdullah | — |
| **Held follow-ups 104, 105, 106** (leads 7, 91, 104, `pending`): each adds a consequence sentence to its benefit line that the line does not make — "so you can review what came in overnight when you start the day", "Nothing the assistant handles is hidden from your team." (both BEN-SEE), "The sponsor has what they asked for while they are still looking at your site, not once someone gets back to them." (BEN-DECK). Approve, edit or reject them in the queue. Composed follow-ups have now been held 6 times out of 6 for the same pattern (99, 100, 101 and these three); the follow-up prompt's "build the note around it" invites the elaboration — tightening it is a separate decision | Abdullah | Follow-up #1 to leads 7, 91, 104 |
| Sync NocoDB's metadata for `drafts` so the grid shows `approved_by`, `approved_at`, `hold_reason` and `claim_check` (migration `014`), and filter the review grid to `status = pending` to make it the exceptions queue | Abdullah | Seeing hold reasons in the grid (the digest shows them meanwhile) |
| ~~Use a Nova-Scout-only Anthropic API key … then rerun `provision_anthropic_credential.py`~~ — **done 2026-10-02**: `.env` holds a dedicated key and the credential was re-provisioned (the monthly spend limit lives in the Claude Console and cannot be checked from here) | Abdullah | — |
| `nova-one-pager.docx` was edited in place on 2026-10-02 ("sponsor" restored; five code-confirmed capabilities added as bullets; still one page in Word), and again after migration `013` (the website as the only knowledge source, no SOPs or "service documentation", no "new dashboard" sentence; sha256 `625be8a6…`). Its generator is not in this repo, so a regeneration must carry the same changes, then pass `audit_drafts_vs_onepager.py` | Whoever regenerates it | Keeping the one-pager consistent with the claims |
| ~~Follow-Ups produced no follow-up for the 3 leads emailed 2026-09-22~~ — **root cause found and fixed 2026-10-02** (Section 9, Workflow 6): the stack was down, and n8n's hour-based schedule check skipped the one tick it was up for. Follow-up #1 drafts 99, 100, 101 are `pending` for review | Abdullah | — |
| **Start Docker Desktop with Windows.** Its `AutoStart` setting is `false`: after every reboot the whole stack — Send, Mailbox Watch, IMAP Health, Follow-Ups — stays down until someone starts Docker Desktop by hand (2.5 days on 2026-09-28 → 10-01 with the laptop on). Turn on "Start Docker Desktop when you sign in" | Abdullah | Every scheduled workflow, opt-out handling included |
| LinkedIn draft 90 (lead 7, `pending`, written before migration `013`) still says "books the call". Drafts 89, 93, 94 and 95 are no longer in the table. LinkedIn is sent by hand — **do not send 90 as it stands** | Abdullah | A clean audit |
| ~~Publish Mailbox Watch in the UI~~ — **done 2026-09-15**, live and mirroring Sent (Section 9, Workflow 6) | Abdullah | — |
| Publish Send, then Follow-Ups, in the UI — **neither has ever been published** (verified 2026-09-16). Send is no longer blocked: it sends on its first eligible tick, and 4 approved drafts were eligible on 2026-09-16 | Abdullah | Every Workflow 6 send and follow-up |
| Publish **Send & Track - IMAP Health** in the UI — built and dry-run 2026-09-16 (Section 9, Workflow 6), imported unpublished; the `imap-health` service is running and migration `009` is applied. Until it is published nothing proves Mailbox Watch is listening between events | Abdullah | Catching a dead Inbox trigger before an opt-out goes unrecorded |

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
# Nova Scout — Master Reference Document v2
## AI-Automated BD Pipeline for Nova Agent Kit

> Single source of truth for architecture, stack, and build decisions. Paste it into the Claude Project tab before every build session.
> Do not deviate from anything marked **LOCKED**. Ask before changing anything marked **CONFIRMED**.
> **v2 — condensed 2026-10-18** from v1 (1,230 lines). Incident write-ups, per-run logs, dry-run transcripts, superseded decisions and finished open items were removed. Every rule, decision and number that still governs the build was kept. Facts are as last recorded on 2026-10-10; check live state with the §3 publish signals and the database, not with this file.
> **v2.1 — 2026-10-19:** outreach is fixed templates (operator brief): no model writes a first touch or a follow-up, the claim check is gone, the opt-out sentence and the phone are out of the cold signature. §3, §4, §5, §8 (migration `020`), §9 Workflows 4–6 and §13 changed; the template wording itself is in §9. Amended the same day (operator): a fourth SMYKM line for a lead with no other fact, "in Phase II" for a single phase, and the 41 unsent AI follow-ups retired with their slots freed (§9 Workflows 4 and 6).
> **2026-10-24 (operator):** a bounce is permanent or transient (migration `022`): only a permanent one counts toward the Send safety brake, and a permanent one marks the contact unverified. §8, §9 Workflows 3b and 6, §13. The same day's health check found n8n upgraded to 2.44.6 by a Docker reinstall (§3, §13).
> **2026-10-24 (evening, operator):** weekend sending — Saturday and Sunday are sending days, still in the recipient's 09:00–17:00 local window and under the 50/day cap; the safety brake trips over 10% permanent bounces instead of 3% (§9 Workflow 6, §12).
> **2026-10-24 (later):** every Docker image pinned to version + digest, with the upgrade procedure (§3); the chat-widget filter matches chat embeds, not vendor domains, and checks HubSpot portal loaders (§9 Workflow 2); DMARC reports are machine mail in Mailbox Watch (§9 Workflow 6); mail DNS measured and the 2048-bit DKIM / DMARC `rua` plan written (§5, §13).
> **The build scripts parse this file.** Do not reword these blocks without changing the generator and its drift-guard test together: the §3 reply-model line; the §4 website-lookup table and cap; the §5 warm-up table, URL cap, retired opt-out sentence and opt-out keywords; in §9, Workflow 3's company-type disqualifier sentence, weights table, Trialsites bands table, taxonomy list and the "Apollo lookup for leads scoring ≥ N only" sentence, Workflow 4's "**No model call** in Drafting" sentence, first-touch template, SMYKM block and its arm/order/area-cap sentences, and Workflow 6's follow-up templates, its opt-out-detection, follow-up and health-check sentences, the safety-brake sentence and the weekend-sending sentence; and in §12, the geography lines, the excluded list and the clock table. After any edit run the drift-guard suites (`n8n/*/test_drift_guards.py`, `scraper/test_geography.py`, `scraper/test_shards.py`).

---

## 1. Project Overview

**What we are building:** An agentic outbound pipeline that finds small-to-medium CROs worldwide — and, since 2026-10-09, independent clinical research sites and SMOs (§9 Workflow 1b; §12) — enriches and scores them against our ICP, drafts outreach, and sends and tracks it. Since 2026-10-19 every first touch and follow-up is a fixed template in the operator's approved wording, filled with verified facts — no model writes outreach (§9 Workflow 4). An email that has a verified address, a Send clock for its country and no blocklisted domain approves itself; a human reviews the exceptions queue (what was held, plus the LinkedIn DMs drafted before 2026-10-19) and reads a daily digest of what went out (§9 Workflow 5).

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
3. **Claude Sonnet 5.5** — reply drafts only (§9 Workflow 7, since 2026-10-09). From 2026-10-02/03 until 2026-10-19 it also composed first touches and follow-ups and ran the claim check (with its repair loop) that gated their auto-approval; fixed templates replaced all three on 2026-10-19 by operator decision (§9 Workflow 4), so no model writes outreach.
4. **Claude Haiku 5.5 + web search** — website lookup for Trialsites candidates only (§4, Workflow 1b).

**Rule for model selection:** pick by capability where volume is low, by price only where volume is high. Never route the high-volume enrichment or scoring nodes to a paid API.

**Reply model: `claude-sonnet-5-5`**, effort `high`, through the Anthropic Messages API, $2/$10 per MTok (cache reads **$0.10**, not $0.20 — corrected 2026-10-08 from the live pricing page: Sonnet 5.5 and Opus 5.5 are documented exceptions at 0.05x base input, where every other model is 0.1x). The Reply Assistant only (Section 9, Workflow 7) since 2026-10-19 — until then it was also the drafting model, the follow-up composer and the claim check; enrichment and scoring stay on local `qwen3.5:9b`, and the one-model rule above still governs everything that runs on the GPU. Request parameters were taken from the live Sonnet 5.5 docs on 2026-10-02 (released 2026-09-28; confirmed present with `GET /v1/models/claude-sonnet-5-5`), not carried over from older settings: no `temperature`/`top_p`/`top_k` (a non-default value is a 400 on this model, so the old drafting `temperature: 0.45` cannot be sent); `thinking` omitted, which runs adaptive thinking; effort set explicitly to `high`, the documented starting point for work that is neither agentic nor latency-sensitive; JSON through `output_config.format` with a strict schema; `max_tokens` 16000, because it covers thinking as well as text; and **prompt caching on since 2026-10-08** (below). The key is the n8n credential `novascoutAnthropic01`, made from `ANTHROPIC_API_KEY` in `.env` by `n8n/drafting/provision_anthropic_credential.py` — a Nova-Scout-only key since 2026-10-02 (re-provisioned that day; `GET /v1/models/claude-sonnet-5-5` answered 200 with it). **If a call fails, the message stays queued for the next run. There is no fallback to the local model**, so the quality of what reaches the operator does not depend on which model answered.

**Sonnet 5.5 costs 2× Haiku 4.5** ($1/$5), not less; it is chosen for replies because they are few and every one is read by the operator before it goes. Cost is in §4.

**Prompt caching — ON since 2026-10-08, one breakpoint per call, on the system prompt.** Measured on the drafting, claim-check and follow-up calls, which were retired on 2026-10-19; the Reply Assistant's call (`code_reply.js`) carries the same single breakpoint. Settings were read from the live docs, not carried forward:

| What | Value | How it was established |
|---|---|---|
| Minimum cacheable prefix | **512 tokens** for `claude-sonnet-5-5` | Live prompt-caching docs. **Not monotonic across generations** — 512 here, 1,024 on Sonnet 5, 2,048 on Opus 4.7, 4,096 on Opus 4.6 and Haiku 4.5 — so it must be re-read per model, never assumed to fall over time. A prefix under the minimum caches nothing **and raises no error**. |
| Lifetime | **5 minutes** default; `"ttl": "1h"` for an hour | Live docs. Measured from the **start** of the request that writes or reads, so generation time counts against it. A read refreshes the timer free. |
| Request format | `cache_control: {"type": "ephemeral"}` on a content block; at most 4 breakpoints; render order `tools` → `system` → `messages` | Live docs |
| Write price | 1.25× base for 5 min ($2.50/MTok), 2× for 1 h ($4/MTok) | Live pricing page |
| Read price | **0.05× base = $0.10/MTok** | Live pricing page. This doc said $0.20 until today; that is Sonnet 5's rate. |
| Verification fields | `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, and `usage.cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` | Live docs; read out of n8n's own execution records by `n8n/drafting/cache_report.py` |

**One breakpoint, on the last (only) system block.** The system prompt is the only text byte-identical across calls; everything per-message is in the user message after it. The cached prefix is bigger than the prompt, because `output_config.format`'s schema renders into it (measured 2026-10-08 on the then drafting call: 1,825 tokens of prompt, 2,224 with the schema). A prompt under the 512-token minimum — the old repair prompt, 448 — caches nothing and raises no error.

- **The 5-minute TTL is a measured choice** (calls in bursts minutes apart, bursts an hour or more apart); a 1-hour entry costs 2× to write and would only earn more on a coin flip.
- **Pre-warming is impossible:** the documented `max_tokens: 0` warm-up is rejected together with structured outputs (`400 output_config.format cannot be set when max_tokens is 0`), and `format` is part of the cached prefix.
- **Caching cannot change what a message says:** `cache_control` is a billing instruction about a prefix.

**Ollama environment variables (Windows system env vars):**
**Ollama upgrades — never half-applied (2026-10-24).** The app's own updater downloads to `%LOCALAPPDATA%\Ollama\updates_v2` and runs the installer `/SILENT`; when `ollama.exe` is running elevated the installer cannot close it (RestartManager: Permission Denied), asks Abort/Retry/Ignore, and Ignore leaves a new `lib\ollama\` under the old executables — every model load then fails with `invalid argument: --no-mmap`. That happened on 2026-10-24 (0.30.10 executables over a 0.40.10 `lib`), and a silent re-run with Ollama up aborted and its rollback emptied `lib\` entirely. Since then: **auto-update is off** (`settings.auto_update_enabled = 0` in `%LOCALAPPDATA%\Ollama\db.sqlite`, the app's own setting), Ollama starts from the Startup-folder shortcut, which does not run as administrator — **never start it elevated**. To upgrade: quit Ollama (tray → Quit) and check `Get-Process ollama*` is empty; download the release's `OllamaSetup.exe` and check `Get-AuthenticodeSignature` is Valid, Ollama Inc.; run it; then `ollama --version` must equal the registry `DisplayVersion` (HKCU Uninstall `{44E83376-…}_is1`) and `/api/version`, and `ollama ps` must show one `qwen3.5:9b` row, `100% GPU`, context 16384 after an Enrichment run. Model weights under `~\.ollama\models` are not touched by an install.

```
OLLAMA_KEEP_ALIVE=5m        # unload after 5 idle minutes (operator decision 2026-10-30; was -1, never)
OLLAMA_MAX_LOADED_MODELS=1  # one model copy at most (2026-10-30)
OLLAMA_NUM_PARALLEL=1       # serial processing, avoid VRAM contention
OLLAMA_HOST=0.0.0.0:11434   # reachable from Docker containers
```
These are **user** environment variables (HKCU), not system ones. Every n8n call to Ollama (Enrichment's *Ollama Extract*, Scoring's *Ollama Rationale*) also sends `keep_alive: '5m'` with the same model tag and `num_ctx` (`OLLAMA_MODEL` / `OLLAMA_NUM_CTX` / `OLLAMA_KEEP_ALIVE` in both builds; `n8n/scoring/test_ollama_calls.py` refuses a pair that differ), because a request's `keep_alive` overrides the server's and a different tag or `num_ctx` loads another copy.

**Structured output:** always use Ollama's `format: json` with an explicit schema for extraction and scoring nodes. Never parse free text. Run `ollama ps` after loading and confirm 100% GPU, not a CPU/GPU split.

**Inference tuning — three gotchas (tested):**
1. **Override `presence_penalty`.** Qwen3.5 ships at 1.5; the failure is silent, field-level data loss on copy-from-source fields (`founder_linkedin`, `therapeutic_areas` came back null even when present), hidden because grammar-constrained JSON is always syntactically valid. For extraction and scoring set `presence_penalty: 0`, `temperature: 0.1`.
2. **Cap the context window at 16K–32K.** The models advertise 262K; full context adds 4–8 GB of KV cache and pushes you off the GPU. Pages are 5–20K tokens.
3. **Use `/api/generate` with `think:false`, not `/api/chat`.** On `/api/chat`, `think:false` silently disables `format` schema enforcement; omitting `think` burns the budget on reasoning and returns empty content. `/api/generate` honours both together (~5 s). Re-verify on any new model/endpoint before copying the setting forward.

**Parallelization:** only the GPU call needs to be serial. Fetch/I/O steps should run in parallel (5 at a time cut a 305 s batch to 46 s). Parallelize lookups, serialize the model call.

**n8n operations — hard-won on 2.35.x.** The instance is **2.44.6 since 2026-10-23**: `docker-compose.yml` pins `n8nio/n8n:latest`, so reinstalling Docker pulled a new image (n8n's log: `Recorded version change: 2.35.7 -> 2.44.6`; it migrated its own database on start). Checked on 2.44.6 the same day: every published workflow ran on one version, Send sent through the full claim → SMTP → confirm path, the Sent mirror caught a send within 8 seconds, IMAP Health reported healthy. The rules below were measured on 2.35.x; re-verify one before leaning on it (§13). **One has already changed: on 2.44.6 `import:workflow` stops the running workflow's triggers** — after `mailwatch0001` was imported at 20:10 UTC on 2026-10-23, Send's 20:40:54 send was filed in Sent at 20:41:06 and Mailbox Watch ran no execution at all (on 2.35 the old version kept firing until a restart). So an import takes a trigger-driven workflow **off until it is published**: import only when the publish can follow at once.
- **There is no "Active" toggle — use Publish, in the UI or through the n8n MCP.** `publish:workflow` from the CLI leaves a half-state (`active` flips, but no trigger is registered and the cron never fires). `n8n execute --id=…` ignores published state. **Measured on 2.44.6 (2026-10-24):** the MCP's `publish_workflow` (with the full `versionId`) registers the triggers properly, but only for a workflow whose settings carry `availableInMCP: true` (the workflow-settings toggle) — and `import:workflow` drops that key, because the repo JSON does not carry it, so a re-imported workflow needs the toggle again before the MCP can publish it.
- **Publishing locks to a version, and importing unpublishes.** Re-publish after every rebuild and re-import. An import sets `active=false` in the database but does **not** stop the old trigger in the running process.
- **On 2.44.6 a publish swaps the version in place — no restart (measured 2026-10-24).** Mailbox Watch: its new Classify Inbound was saved through the MCP's `update_workflow` (a draft: a new `workflow_history` row, `activeVersionId` unchanged), then published; `workflow_publish_history` wrote `deactivated` for the old version and `activated` for the new one at the same instant, `workflow_publication_outbox` a `publish` row `completed`, and the next seven executions all ran the new version, none the old. Send: imported with the CLI (which stopped it), published through the MCP 90 s later, and its next tick ran only the new version. **An interval schedule re-anchors to the publish:** Send's tick moved from x0:54 to x0:36. Still check one `workflowVersionId` per workflow after any publish. **On 2.35 (the rule below, kept for history): after importing and publishing a workflow, restart n8n** (`docker compose restart n8n`), then check that only the new version runs. **Publishing does not stop the old version either** — measured 2026-10-18/19: after Drafting and Follow-Ups were imported and published, *both* the published template build and the old AI-composing build fired on every tick for ~11 hours (the old one ~28 s earlier, with a `workflowVersionId` that has no `workflow_history` row), until a restart cleared it. The proof is `execution_entity."workflowVersionId"`: after the restart, one version per workflow. Order matters: **import → publish → restart** — a restart before the publish leaves the imported workflow off (it is unpublished) until it is published.
- **Real activation signals** (on 2.35 the publication-service tables stayed empty and proved nothing; on 2.44.6 `workflow_publication_outbox` gets one row per publish and per startup, see below): `workflow_entity.active='t'`, `activeVersionId` non-null, `triggerCount` equal to the workflow's trigger-node count, a new `workflow_publish_history` row, and — the only conclusive proof — an execution with `mode='trigger'`. `triggerCount` is a count, not a flag (Mailbox Watch has two IMAP triggers and reads 2); 0 is not evidence of a broken node on a never-activated workflow.
- **Those signals prove a publish happened, not that triggers are connected now.** When a trigger fails (an IMAP socket closed by the server), n8n drops all of that workflow's triggers from memory, writes nothing to the database, and retries with exponential backoff capped at 24 hours (`WORKFLOW_REACTIVATE_MAX_TIMEOUT`). Only a later `mode='trigger'` execution, or the log line `Activation of workflow "…" was successful!`, shows recovery.
- **On 2.44.6 a restart leaves one more signal:** at startup n8n writes one `workflow_publication_outbox` row per published workflow (`reason='startup'`), and `status='completed'` says that workflow's triggers registered (2026-10-24: 13 rows, all completed, Mailbox Watch ~8 s after the rest — its IMAP logins). It is the only table that records a restart's re-activation; `failed`/`partial_success` with an `errorMessage` is the first place to look when a trigger is missing after a restart.
- **Image pins, and upgrading on purpose (2026-10-24).** `docker-compose.yml` pins every image to a version *and* the digest it had that day — `n8nio/n8n:2.44.6`, `postgres:16.15`, `nocodb/nocodb:2026.10.0`, `python:3.12.15-alpine3.24` (both sidecars), each `@sha256:…`; each tag was checked on the registry to point at the digest that was running. With a digest present Docker never re-resolves the tag, so a reinstall, a `pull` or an `up` runs exactly that image (the 2026-10-23 reinstall is what upgraded n8n unasked). Applied 2026-10-24 09:12 UTC with `docker compose up -d`: no pull, every container recreated on the same image ID, n8n still 2.44.6 with no `Recorded version change` line, all 13 published workflows re-registered (above), every row count of every table in `novascout` and `n8n` identical before and after (only the new outbox rows), and Send, IMAP Health and Send Reply ran on their published versions at 09:20. `health_dryrun.py` runs its throwaway checker on the image the `imap-health` service names. **To upgrade an image:** (1) read the release notes from the pinned version to the target — for n8n every minor, including its database migrations; Postgres stays inside major 16 (a major upgrade is a dump and restore, never an image swap). (2) Back up: `docker exec nova-scout-postgres-1 pg_dumpall -U novascout > <outside the repo>/backup-<date>.sql` (it holds contact data — never commit it) and record every table's row count. (3) `docker pull <image>:<version>`, then `docker image inspect <image>:<version> --format '{{index .RepoDigests 0}}'`, and write `<image>:<version>@sha256:<digest>` in the compose file — never `latest`, never a bare major. (4) Away from :00/:30 and Send's x0:54 tick, `docker compose up -d <service>`. (5) Verify: the version; n8n's log line `Recorded version change: A -> B` means it migrated its own database, which re-pinning the old image does **not** undo — roll back by restoring the backup with the old pin; every published workflow `active` with `activeVersionId = versionId` and a `completed` startup row; a `mode='trigger'` Send execution within 10 minutes; one `workflowVersionId` per workflow in `execution_entity`; row counts unchanged. (6) Re-verify any rule in this section measured on an older n8n before leaning on it — 2.44.6 already changed what an import does. (7) Commit the compose change with the reason.
- **One-off `docker exec … n8n execute` against the running container** needs `-e N8N_RUNNERS_BROKER_PORT=5690 -e N8N_RUNNERS_ENABLED=false`.
- **Schedules:** see §7 (never `hours`/`days`/`weeks` above 1, never a pinned slot).
- **Code-node sandbox:** `require('crypto')` is refused; BigInt, Buffer and TextEncoder exist; `$env` is blocked on purpose (config goes in the `settings` table).
- **Postgres node replaces the item** rather than merging into it, so a field set earlier is gone unless re-emitted. The HTTP node fires a batch's requests concurrently; its `batchSize`/`batchInterval` only sleeps between dispatches and does not wait for responses.
- **`pairedItem` lineage collapses** when a lane is fed from a one-item node; `$('Node').item` then resolves the wrong lead silently (probed live 2026-10-08 on the then drafting workflow).
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
| Drafting and follow-ups (Workflows 4 and 6) | **$0 since 2026-10-19** — fixed templates, no model call, no claim check (operator decision). Until then Sonnet 5.5 cost ~$0.023–0.028 per first touch, $0.0176 per follow-up and $0.0170 per claim check, measured; ~$100–$200/year at full volume, now gone. |
| Claude API — reply drafts (Sonnet 5.5, Workflow 7, since 2026-10-09) | one call per prospect reply, same model and effort as the retired drafting call; not separately measured. Replies are a handful a week |
| Claude API — website lookup for Trialsites candidates (Haiku 5.5 + web search, since 2026-10-09, Workflow 1b) | **$0.0179 per lookup, $0.0469 per resolved site, measured**; ~$3.60/week at the full 200 candidates, **hard cap $20 a calendar month** (`settings.site_lookup_monthly_cap_usd`) |
| **Total** | **~$22/year + electricity + the website lookup (~$185/year at full volume, never more than $240) + reply drafts** |

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

**Mailbox:** a named human address, not `hello@`/`info@`/`sales@`. Currently `abdullah@amitrixlabs.com` (Zoho). Sender identity: Abdullah Amir, Founder, Amitrix Labs — `NOVASCOUT_SENDER_NAME`/`_TITLE`; `+923178485713` (`_PHONE`) is used by reply drafts only. The cold templates (§9 Workflows 4 and 6) carry the name and title as approved text, and both builds refuse a template whose sign-off is not those two values.

**DNS (all three required before first send):** SPF authorising Zoho, DKIM signing enabled, DMARC policy record. **Measured 2026-10-24** (DNS at Hostinger, nameservers `*.dns-parking.com`): SPF `v=spf1 include:zohomail.com ~all`; DKIM selector `zmail`, a **1024-bit** RSA key; DMARC `v=DMARC1; p=none;` with no `rua`, so nobody receives reports (an earlier `rua` on this mailbox is why Google's and Microsoft's reached the INBOX in September). **Planned (§13):** a 2048-bit key on a new selector, switched to only after the published key checks out, and `rua` to Postmark's free DMARC digest (`re+…@dmarc.postmarkapp.com`; Postmark publishes the wildcard authorization record RFC 7489 §7.1 requires, checked 2026-10-24) — never an address on this domain: a report here is mail Mailbox Watch reads (§9 Workflow 6). `python n8n/sendtrack/check_mail_dns.py [--selector S] [--expect-bits 2048]` reads all three from Google's and Cloudflare's resolvers, decodes the DKIM key length, and checks each external `rua` is authorized.

**Warm-up schedule — enforce in the workflow, not by memory:**

| Period | Max sends/day |
|---|---|
| Week 1 | 5 |
| Week 2 | 10 |
| Week 3 | 15 |
| Week 4 | 20 |
| Week 5+ | 50 (hard ceiling) |

**Raised 2026-10-19 (operator decision):** from week 5 — the current week that day, warm-up having started on sender-day 2026-09-15 — every week allows 50 a day, up from a hard ceiling of 20. Business hours, pacing and every other Send guard are unchanged, and the safety brake (§9 Workflow 6) came with it. Pacing, not the ceiling, now bounds a day in practice: a 20-minute floor and a 40% coin flip per 10-minute tick average about one send every 35 minutes while a recipient is in business hours — simulated with the shipped Decide Send code on the live queue that day, a full sender-day averaged 39 sends and never reached 50 (§13).

**Message rules — LOCKED:**
- Plain text only. No HTML, no images, no logo, no banner.
- **No open tracking. No link tracking. No pixels.** Reply rate is the only metric.
- Maximum one plain URL. No buttons.
- Signature (cold outreach — first touch and follow-ups, since 2026-10-19): `Cheers!` / name / one line of title. **No phone.** A reply to a prospect (§9 Workflow 7) still signs name / title / phone. Send checks every body for the name line followed by the title line.
- No marketing unsubscribe footer. **No opt-out sentence since 2026-10-19 — removed by operator decision 2026-10-19.** Every email sent before then ended with it, so Mailbox Watch still strips it out of quoted replies. Retired opt-out sentence, still stripped from quoted replies: *"If this isn't relevant, reply 'no' and I won't follow up."*
- Irregular send intervals within the recipient's business hours. Never a synchronised burst.
- First touch and follow-ups: the fixed templates of §9 Workflows 4 and 6, word for word. The 70–110-word rule of drafting skill v3 went with the model that needed it.

**Compliance:** GDPR and KVKK both apply across target geographies. Basis is B2B legitimate interest, defensible only if opt-out is trivial and honoured instantly. The IMAP workflow must detect "no", "unsubscribe", "remove", "stop" and blocklist the domain immediately and permanently. Unchanged by the 2026-10-19 switch: a reply carrying any of those words still blocklists the domain at once — the emails no longer tell the recipient so.

**Operator commands by email** (§9 Workflow 7) are accepted only when SPF and DKIM pass and the sender equals `settings.operator_email`; this is a CHECK on `operator_commands`, not only a code path.

---

## 6. LinkedIn — LOCKED

**LinkedIn sending is manual. Always. No exceptions.** LinkedIn bans automation, and the account is a business asset that cannot be replaced. **Since 2026-10-19 the system drafts no new DMs** (operator decision, with the template switch); the DMs drafted before stay in the exceptions queue untouched, a human reads one, edits if needed, and sends it by hand. A LinkedIn draft never auto-approves.

---

## 7. Pipeline Architecture

Six workflows, each independent, each queue-driven.

```
Sources → 1 Ingest → 2 Enrich → 3 Score → [Apollo] → 4 Draft → 5 Approve → 6 Send → CRM
                                                                         ↑   (auto, or HUMAN  │
                                                                         │   for exceptions)  │
                                                                         └──── learn ─────────┘
```

**Step 5 is no longer a human gate on every draft (2026-10-03, migration `014`; simplified 2026-10-19, migration `020`).** A template email approves itself inside Workflow 4 (first touch) or Workflow 6's Follow-Ups unless it has no verified deliverable address, no Send clock or a blocklisted domain (until 2026-10-19 a Sonnet 5.5 claim check gated model-written drafts); everything else, and every LinkedIn DM, waits for a human (§9 Workflow 5).

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

**Location:** the single Postgres container hosts two databases — `n8n` (n8n's own state, owned by n8n; never point application workflows at it) and `novascout` (everything below). Migrations `001`–`023` are applied by hand. **Rules live on the tables, not only in the workflows** (migration `006`/`014` principle): a rule on `drafts` or `claims_library` holds for every writer, including the NocoDB grid and psql.

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
  message_id (UNIQUE), replied (bool), replied_at, reply_body, outcome,
  bounce_kind (permanent|transient)

blocklist
  domain (UNIQUE), reason, added_at

mailbox_sent
  message_id (PK), sent_at, recipients[], external (bool), lead_id FK,
  source (workflow|sent-folder), seen_in_sent_folder (bool), recorded_at

mailbox_sync
  folder (PK: Sent | Sent-reconcile), last_synced_at, messages_seen, undated

inbound_messages
  message_id (PK), received_at, from_addr, subject, lead_id FK, matched_by,
  classification (reply|opt-out|auto-reply|bounce|unmatched),
  opt_out_keyword, body_excerpt, processed_at, bounce_kind (permanent|transient)

mailbox_health
  check_name (PK: imap), healthy (bool),
  problems[] (checker-unreachable|imap-failed|inbox-missed|sent-missed|sent-mismatch|send-blocked),
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
- **`010` → `012` → `013` — `claims_library`.** The approved claims the drafting model could use until 2026-10-19; since then only the Reply Assistant reads them (Drafting, Follow-Ups and the approval trigger stopped, migration `020`). The table is the library, `NovaScout_DraftingSkill.md` §4 (superseded) only the seed. `012` (skill v3) changed the slots to `description`, `angle`, `benefit`, `proof`, `ask` (+ optional `link`), with CHECKs for what any claim may say: no line names the product (the model adds "(we call it Nova)" once), "AI" only in a description and at most once, never "AI-powered"/"chatbot", no guarantee, nothing calling the prospect a sponsor, an ask is exactly one question. `013` settled the lines the code reading questioned: Nova **sends a booking link** (it does not book); the knowledge base is **the scraped website only** (no SOPs or client documents); no "not in another dashboard". `capabilities[]` (closed vocabulary: answers, qualifies, captures-lead, study-details, booking-link, deck, routes-leads, dashboard, configured) is required on every description and benefit line and drives the no-repeat rule. Geography CHECK, link rules (one plain URL, never LinkedIn/PDF) and the measured-proof rule are unchanged. On a proof line `countries` is the geography match (§12).
- **`014` — auto-approval.** `settings.auto_approve_email` (on by default; `auto_approve_email_enabled()` is the one reader); `drafts.approved_by` (`auto`|`human`), `approved_at`, `hold_reason`, `claim_check`. The trigger `drafts_approval_rules` turns any `auto` approval that breaks a rule into a hold (`db-guard: …`), stamps every other approval `human`, and un-approves an auto draft whose text is rewritten. On `claims_library` a trigger un-confirms a confirmed line whose text, slot, countries, capabilities or company types change. `digest_log` is the Daily Digest's memory.
- **`015` — `contact_attempts`, `ingest_log`.** Each a workflow's memory of what it has done (§9 Workflow 3b, Workflow 1). Read-only view `needs_manual_contact`.
- **`016` —** the `no-send-clock/` variant is held with that word (superseded by `020`: there is no such note any more, a no-clock template draft is held by its `+no-send-clock` tag).
- **`017` — reply-assistant foundations.** Thread headers on `inbound_messages`, plus:

```sql
inbound_messages  (+ 3 columns)
  in_reply_to, references_raw, thread_ids text[]

reply_approvals
  code (PK: NS-XXXXXXXXXX), kind (reply|email-hold), draft_id FK, lead_id FK,
  inbound_message_id FK, issued_at, expires_at, reminded_at,
  used_at, used_by_message_id, outcome (approved|rejected|edited)

operator_commands
  message_id (PK), received_at, from_addr, subject, command (APPROVE|REJECT|EDIT|CONTACT),
  code, spf_pass, dkim_pass, sender_ok, accepted, refused_reason,
  replacement, draft_id FK, processed_at, contact_lines jsonb
```

  `reply_approvals` is a bearer token in a mailbox, so its rules are CHECKs: `code` is the primary key; `expires_at` ≤ 48 hours after `issued_at`; at most one unused code per draft (partial unique index); `reply_approval_usable(code)` is the one definition of "may be acted on now". `operator_commands` records every APPROVE/REJECT/EDIT received, accepted or refused; `accepted` is only permitted when `spf_pass AND dkim_pass AND sender_ok` and a command and code are present, so a workflow bug can refuse a real approval but never accept a forged one.
- **`018` — reply workflow rules.** `reply_approval_issue()` is the only way a code is minted, reused or reaped; `drafts_approval_rules()` lets a `reply/` draft become `approved` only while a one-time code for it is open or already used to approve/edit it, and refuses `approved_by='auto'` on a reply; `retire_linkedin_on_reply()` (trigger on `inbound_messages`) retires a replied lead's pending/approved LinkedIn draft.
- **`019` — sites and SMOs.** Tables `site_candidates`, `site_lookup_log`, `site_harvest_log`; `leads.source` gains `trialsites`; "never guess a domain" as a CHECK (a candidate holds a domain only when its website was confirmed); `settings.site_lookup_monthly_cap_usd` (seeded $20) and `site_lookup_budget_left()` (a missing setting reads as $0, never unlimited); `claims_library.company_types` (default `{CRO}`; a CHECK that no site/SMO line says "two CROs" or "CRO website(s)" or calls Vertex a CRO); `lead_company_type(raw_extraction, source)`, the one definition of CRO / site / SMO that Drafting, Follow-Ups, the Reply Assistant and Mailbox Watch read. 18 site/SMO claim lines are seeded `active=false, confirmed=false`.
- **`020` — template outreach (2026-10-19).** `drafts_approval_rules()` rewritten for the fixed templates (§9 Workflows 4–6): an `approved_by = 'auto'` approval needs the flag, an email, a template variant (`template/…` or `follow-up-N/template/…`) with no `+` tag, unedited text, a CRO lead (`lead_company_type()`), a verified deliverable address and neither domain blocklisted. The claim-code lookup and the passing-claim-check requirement are gone, so the trigger no longer reads `claims_library` (the table stays; the Reply Assistant reads it), and a draft written any other way can no longer approve itself. The reply branch (`018`) and the "text changed after auto-approval" rule are unchanged. **`drafts.variant`** since 2026-10-19: a first touch is `template/<A|B>/<trials|areas|city|company>/<named|role-inbox|unnamed|unaddressed>[+tags]`, a follow-up `follow-up-<1|2>/template/<A|B>[+tags]`; the tags are the four holds of §9 Workflow 5. A follow-up retired by the operator is prefixed `retired/` (§9 Workflow 6) and is no longer a follow-up to any query.
- **`021` — the Send safety brake (2026-10-19).** `inbound_messages.spam_complaint` (set by Mailbox Watch's classifier); `send_pauses` (the latch: an open row — `cleared_at` NULL — pauses Send; one open row per reason, `bounce-rate` or `spam-complaint`; never cleared by code); `send_brake_stats(as_of, window_days)` (the bounce and complaint counts, defined once for Check Brake and Load Send State). Additive only. §9 Workflow 6.
- **`022` — permanent and transient bounces (2026-10-24, operator).** `inbound_messages.bounce_kind` (`permanent` | `transient`, only on a `bounce` row — a CHECK) and `outreach_log.bounce_kind` (the kind on that send; Record Inbound keeps `permanent` once written, so a later delay warning cannot downgrade it). `send_brake_stats()` replaced: a bounced send counts unless its kind is `transient`; NULL still counts (a bounce an older Mailbox Watch wrote), and the JSON gains `delayed`. `bounce_names_address(thread_ids, text, address)` — is this bounce about this address: a recipient of the sent message it threads to (`mailbox_sent`), or the address written in the report as a whole token, case-insensitive. Trigger `unverify_contact_on_hard_bounce_trg` (after insert, or update of `bounce_kind`, on `inbound_messages`): a permanent bounce matched to a lead sets that lead's contact `verified = false` when the bounce names its address — a contact whose address changed since the send is left alone. Additive and idempotent. The five bounces recorded before it were classified by `n8n/sendtrack/backfill_bounce_kind.py` (§9 Workflow 6).
- **`023` — the CONTACT operator command (2026-10-28, operator).** `operator_commands.command` may be `CONTACT`; an accepted CONTACT needs no code (SPF, DKIM and the operator's own address are still required — the CHECK is relaxed for `CONTACT` only); `operator_commands.contact_lines` (jsonb array, CONTACT only) keeps every line as judged. What CONTACT does is Apply Operator Command's (§9 Workflow 7). Additive and idempotent.
- **`024` — IMAP Health learns the Sent-mirror gap (2026-10-28).** `mailbox_health.problems` may also hold `sent-mismatch` (a row the mirror marks seen whose Message-ID the Sent folder does not hold) and `send-blocked` (a workflow send unseen for longer than Send's mirror grace + 20 minutes); `sent-missed` now means in the folder but not confirmed seen. `mailbox_sync` gains the row `Sent-reconcile` (no schema change). §9 Workflow 6. Additive and idempotent.

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

**Not drafted since 2026-10-19.** The fixed first-touch template is a CRO message, so a site or SMO lead that reaches `contact_found` waits there until a site template is approved (Workflow 4 says how that is enforced).

**Execution data:** a harvest carries ~30–40K rows, so `trialsites0001` saves no successful execution data; every outcome and cent is in `site_candidates`, `site_lookup_log`, `site_harvest_log`.

**Dry run (2026-10-09/10, scratch database, all addresses `@dryrun.invalid`):** 30,034 rows → 2,658 eligible new candidates, 200 chosen; first 20 lookups → 10 resolved, 2 duplicates, 4 not confirmed, 4 no match, $0.0188 per lookup; the 10 leads scored 55–74 and 4 had own-site addresses; every site email was held `unconfirmed-claim`. `n8n/sites/test_drift_guards.py` (33 cases) proves the build's refusals. The dry run caught overlapping requests (fixed by the sequential loop), a Polish university hospital selected because only two word forms were listed (stems added), and a site draft opening "Your site lists …" (site prompts say "your website").

### Workflow 2 — Enrichment
**Trigger:** every 30 min. Batch of 10 where `status='ingested'`.
**Steps:**
1. Fetch homepage, /about, /services, /team via HTTP node
2. **Chatbot detection is a Code node with regex — NOT an LLM call.** Match against known widget scripts: Intercom, Drift, Tidio, Tawk, Crisp, HubSpot chat, LiveChat, Zendesk. Never spend tokens on what a string match answers. **Match the chat embed, never the vendor's domain (2026-10-24).** A vendor's domain also serves its analytics, forms, cookie banner and help-centre assets, and pages mention or link vendors. The "HubSpot Chat" pattern matched `js.hs-scripts.com` (HubSpot's tracking loader, on every HubSpot customer's site) and `hs-banner` (its cookie banner): 24 of the 28 leads flagged by 2026-10-24, and fetched live that day 15 of the 17 it disqualified on its own had no chat at all — their portal loaders inject analytics, banner, ads pixel and forms but not `js.usemessages.com/conversations-embed.js`. HubSpot chat is now the embed itself, or a portal loader (`//js[-region].hs-scripts.com/<portal>.js`, at most two per lead, fetched with a script Accept header — HubSpot answers the page fetch's HTML Accept with 406) that injects the embed; a loader that cannot be read is unknown and does not disqualify. Intercom (`js.intercomcdn.com`, not any `intercomcdn.com`), Tawk (`Tawk_API`, not a mention of tawk.to), Crisp (`CRISP_WEBSITE_ID`), LiveChat (`__lc.license`) and Zendesk (`static.zdassets.com/ekr/snippet.js`, not Help Centre assets) were narrowed the same way; Drift and Tidio already named only their embeds. What matched is stored in `raw_extraction.fetch.chat_evidence`. Proven by `n8n/enrichment/test_chatbot_detect.js` (19 real pages captured 2026-10-24 — six widgets, thirteen lookalikes — and five real HubSpot loaders, in `fixtures/`) and `test_chatbot_mutations.py` (every rule broken once, every mutation killed), and through n8n 2.44.6 on a scratch clone (§13). Not added: Chatbase (Evotec runs it beside HubSpot chat) and the clinic vendors in `EXPANSION_PLAN.md`.
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

**Then** Apollo lookup for leads scoring ≥ 50 only, and for a CRO from ≥ 40. *(Contact lookup, not only Apollo: the gate on Workflow 3b's queue. Lowered from ≥ 60 on 2026-10-06 — "Decision 2026-10-06" below; the CRO gate added 2026-10-28 — "Decision 2026-10-28" below. `n8n/contacts/build_workflow.py` parses both numbers out of this sentence, so it is the one place to change them.)*

**Workflow 3b — Contact Lookup (`contacts0001`)**
A separate queue, deliberately: `status='scored' AND fit_score >= 50 (>= 40 when lead_company_type() is CRO) AND no contacts row AND not retired in contact_attempts`, ordered `fit_score DESC`, batch of 10, hourly (`hours`/1 is safe, §7). It is its own queue (build rule 4) so that a lead scored before this stage existed, or an outage, a plan change or a threshold change, is simply drained when the stage runs. `build_workflow.py` parses both numbers out of the sentence above into the Config node (`min_fit_score`, `min_fit_score_cro`) and refuses a CRO gate above the general one.

**The gate is ≥ 50, not ≥ 60 (decision 2026-10-06).** The pipeline had run out of prospects: the leads at 50–59 are each held back by the one factor that cannot discriminate for a CRO (active trials, above), not by a weak profile — every other factor was strong for them. Nothing else changed.

**A CRO from ≥ 40 (decision 2026-10-28, operator).** The pipeline had run out again: every lead at ≥ 50 had been emailed, queued, or retired to `needs_manual_contact` (the 2026-10-28 survey, `docs/survey_2026-10-28.md`). The lower gate applies to CRO-shaped leads only (`lead_company_type()`, migration `019`) — the CRO template is the only one that drafts; sites and SMOs keep ≥ 50. Everything after the gate is unchanged: the same free sources in the same order, the same verification, the same Drafting gate and the same Send brake. **First run, 2026-10-28:** the gate admitted 75 leads, every one a CRO (scores 40–44: 28, 45–49: 47), counted beforehand with the shipped batch query. Contact Lookup's 08:00 UTC tick and seven headless runs of the same published workflow between ticks (08:02–08:05) looked them all up: **62** got a verified address and went to `contact_found`, **11** found nothing (`needs_manual_contact`), 1 got a LinkedIn-only contact (held `no-address`), and 1 (lead 526, an e-invoicing inbox from its ICH GCP profile) was set unverified by hand (§13). Drafting's 08:30 tick wrote the first ten first touches, all auto-approved templates; the rest follow at ten a tick.

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

**`contacts.verified` means one thing:** the address is confirmed, not inferred — true for a first-party address the company published about itself and for Apollo `email_status: verified`; false for Apollo `guessed`, a masked placeholder, a tombstone, a LinkedIn-only contact, or **an address a permanent bounce came back from** (migration `022`, since 2026-10-24: set by a trigger, §9 Workflow 6). Send, Drafting's and Follow-Ups' gate and migration `020`'s trigger all gate on it; Scoring never reads `contacts`. This workflow does not look a lead up again once it has a contacts row, so a bounced lead stays put until a person enters a working address and sets it verified. **A role inbox is not a person:** where enrichment also named a founder the name is written alongside but never presented as the owner of that inbox.

**Three outcomes, and why re-running is free.** A *refusal* is retried and writes nothing. An *answered-but-empty* lookup writes a **tombstone** (a `contacts` row, every field null, `verified=false`) so the batch query can tell "not looked up" from "looked up, nothing there"; the lead stays `scored` (`contact_found` would put a contactless lead in front of the grounding guard). And `contact_attempts` (migration `015`) retires a lead from the queue: `last_outcome='exhausted'` (every source genuinely answered, including the plan-gated 403) or `attempts >= 3` (an unrecognised refusal). Without it, nine stuck leads re-filled the top of every hourly batch and a tenth would have starved the queue. A lookup that finds a contact deletes the attempt row in the same statement. **`needs_manual_contact`** is the read-only NocoDB view (lead, score, country, founder, LinkedIn, and a `why` naming what each source tried); to action one, insert the contact into `contacts` and set the lead `contact_found`. **Re-queue recipes:** `DELETE FROM contact_attempts` re-queues every retired lead; `DELETE FROM contacts WHERE apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL` re-queues the LinkedIn-only ones — the two moves to make after an Apollo plan upgrade.

### Workflow 4 — Drafting
**Trigger:** every 30 min. Batch where `status='contact_found'` and the lead is a CRO (`lead_company_type()` = `CRO`). **Fixed templates since 2026-10-19 (operator decision):** a first touch is the approved template below, filled by code with verified facts — the enrichment record and the recruiting-trial count this workflow looks up at ClinicalTrials.gov. **No model call** in Drafting, nor in Follow-Ups (Workflow 6): the Sonnet 5.5 composer, the claim check, the repair loop and the claims-library reads were removed that day (`claims_library` itself stays; the Reply Assistant still reads it). `n8n/drafting/code_template.js` (node *Build First Touch*) holds the wording and the rules; `n8n/drafting/build_workflow.py` refuses to build unless the blocks below and the code agree byte for byte, and `n8n/sendtrack/build_workflow.py` checks the same for the follow-ups.

**First-touch template — LOCKED (wording approved by the operator 2026-10-19; do not change).** Two arms, assigned by lead_id: even = A, odd = B. The arm and the SMYKM choice are recorded in `drafts.variant` (`template/<arm>/<fact>/<addressing>`, e.g. `template/A/trials/role-inbox`).

```text
Subject: {company} + {fact} + sponsor inquiries

Hi {first_name},

We have yet to be properly introduced, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs.

{SMYKM line}

That's precisely what we do at Amitrix Labs. We build AI agents for CRO websites that answer sponsor and visitor questions, collect their details, and turn more of your visitors into qualified leads, so you win more studies.

[A] Do you have time over the next week or two to learn more? Let me know what works for you, and I'll send a calendar invite along accordingly.
[B] Worth a look for {company}?

Cheers!
Abdullah Amir
Founder, Amitrix Labs
```

**SMYKM line — the first fact the lead has, in this order: `trials`, `areas`, `city`, `company`; sentence A or B by the lead's arm.** (`company` added by the operator 2026-10-19: it needs only the company name, so every lead has a SMYKM line.) `trials` is the number of recruiting trials ClinicalTrials.gov lists **with the company itself as lead sponsor or collaborator** — the same `query.spons` call as Workflow 3, with the company name verbatim, but its `totalCount` is never printed: `query.spons` is a full-text match, and on 2026-10-19 four of the thirteen redrafted leads with a non-zero count were someone else's ("Therapeutics" matched 1,175 trials of every "… Therapeutics", "A+ Science" six of Chinese hospitals, "JSS Medical Research" an unrelated JSS college, "CRO Group" a Tigermed company). Code counts a study only when a sponsor's name, folded and stripped of legal forms, is the company's, or the company's followed only by corporate filler ("Innovaderm Research Inc."); a lookup that matched more studies than the 1,000 one page returns cannot be verified and counts as none. (Workflow 3 still awards its 20 points on the raw `totalCount`.) `areas` are the lead's therapeutic areas from the locked taxonomy (`Other` and free text never count), with its phases when known; `city` is the enrichment record's. The subject's `{fact}` is the same fact, short:

```text
trials A: With {n} trial(s) recruiting right now, sponsors are clearly paying attention to {company}, and every one who visits your website is a potential next study worth turning into a conversation.
trials B: {company} has {n} trial(s) recruiting right now. Each sponsor who checks your website after seeing that is a lead worth capturing.
trials subject: {n} recruiting trial(s)
areas A: {company} covers {areas} from {phases}, so sponsors visiting your website arrive with very different questions, and each one is a lead worth capturing.
areas B: A sponsor planning a {area1} study will usually check {company}'s website before they ever email you. That visit is a lead worth capturing.
areas subject: {areas}
city: Sponsors looking for a CRO in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.
city subject: {city}
company: Sponsors evaluating {company} will usually start on your website, and every one of those visits is a potential study worth turning into a conversation.
company subject: {company} + sponsor inquiries
```

**Formatting rules (code, all unit-tested in `n8n/drafting/test_template.js`).** Correct singular/plural ("1 trial", "3 trials"). At most two areas, lowercase, joined with "and" — oncology first when present, then taxonomy order. An areas fact only from a list of at most six specific areas (2026-10-30, `MAX_LISTED_AREAS`; `Other` not counted): a longer list is the extraction reciting the taxonomy, not reading the site, and would always come out "oncology and cardiovascular" — so the next SMYKM fact (city, then the company) is used instead. The measurement behind six is in `code_template.js`. Phases as "Phase I to III" — a range only when every phase in it is listed ("Phase I and III" otherwise, so a Phase II nobody recorded is never claimed); Bioequivalence is not a phase number; with no phase, "from {phases}" is dropped; "from" only before a range — a single phase reads "in Phase II" (operator, 2026-10-19), and so does a gap ("in Phase I and III"). "an" before a vowel in "a {area1}" ("an oncology study"). A name ending in s takes an apostrophe alone ("MedTrials' website"). Legal suffixes stripped from `{company}` (S.A., Ltd, s.r.o., GmbH, Sp. z o.o., Pvt Ltd, S.A. de C.V., Inc., LLC, Kft, a.s., AB, a trailing parenthetical such as "(AGI)", …) and an ALL-CAPS name fixed ("KLIXAR" → "Klixar"; a word of three letters or fewer, or with at most one vowel, is an acronym and kept: "KCR", "MCRA"). Honorifics skipped in first names, and an ALL-CAPS first name fixed. A role inbox, an address no source attributes to the named contact, or no person's name → "Hi there,". The greeting is gated on the address, so a founder's name is never presented as the owner of a role inbox (Workflow 3b). The ClinicalTrials.gov lookup keeps the company name verbatim; only the text is cleaned.

**What else the workflow does.**
- **Holds** (Workflow 5): an email approves itself unless it has no deliverable verified address, its country has no Send clock (§12), or its domain or the recipient's is blocklisted — or `settings.auto_approve_email` is off. A hold is a tag after `+` in `variant` (`template/B/areas/unaddressed+no-address`) and a reason in `hold_reason`.
- **Sites and SMOs are not drafted** by this template ("AI agents for CRO websites" is a CRO message). Enforced three times: the batch query selects only `lead_company_type() = 'CRO'`, so a site or SMO lead waits at `contact_found` without taking a place in the batch; Build First Touch writes nothing for a non-CRO row; and migration 020's trigger refuses to auto-approve any template draft whose lead is not a CRO. Follow-Ups filters the same way.
- **LinkedIn:** no DM is drafted since 2026-10-19. Write Drafts & Advance inserts and retires email rows only, so the DMs drafted before stay in the exceptions queue untouched.
- **A ClinicalTrials.gov failure** writes nothing; the lead stays `contact_found` for the next run.
- **A redraft retires what it replaces:** a lead sent back to `contact_found` has its earlier pending/approved first-touch *email* drafts set to `rejected` / `bad draft` in the same statement that writes the new one (otherwise Send would send the old approved draft first, since it sends the oldest). Sent drafts, follow-ups, replies and LinkedIn drafts are never touched.
- **Retired with the composer:** the grounding guard and its `low-context` notes (a lead with no other fact now opens on the `company` line), the `no-send-clock` note (now a hold on a real draft), every claim/product/AI/hook/proof/subject rule tag, `unconfirmed-claim`, the skill v3 word limits, the prompt-caching measurements, the repair loop. `NovaScout_DraftingSkill.md` and `DRAFTING_REVIEW.md` are superseded.
- **The switch, 2026-10-19:** the 30 approved or pending first touches that had not been sent (18 approved, 12 pending — every one a CRO lead never emailed) were retired `rejected` / `bad draft` by `n8n/drafting/redraft_with_template.py --retire-only`, which marks each in `hold_reason`. `--apply` re-queues those leads to `contact_found` once the template Drafting is published, and refuses before that: the old composing Drafting keeps running from n8n's memory until the UI publishes the new one (§3). It ran once the first template build was published (2026-10-18, between 22:00 and 22:29 UTC); the operator then amended the template (the `company` line, "in Phase II") before that build had drafted any of the 30, so they were put back to `drafted` at 22:29:28 UTC, 42 seconds before its next tick. `--apply` now also refuses until the published Drafting carries the amendment (`SMYKM_COMPANY` in its code).

**Output:** one email draft per lead into `drafts`.

### Workflow 5 — Exceptions Queue (HUMAN) — was the Review Queue
**Changed 2026-10-03 (migration `014`), simplified 2026-10-19 (migration `020`): the review queue is an exceptions queue.** An email draft — first touch or follow-up — is approved by the workflow that wrote it (`approved_by = 'auto'`) unless one of these fails; then it is written `pending` with the reason in `hold_reason`, and *that* is the queue:

| Rule | Held as |
|---|---|
| it has a deliverable email address (`contacts.email` present and shaped like one) | `no-address: ...` |
| that address is verified (`contacts.verified`) | `unverified-address: ...` |
| its lead's country has a Send business-hours clock (Section 12) | `no-send-clock: <country> has no business-hours clock, so this email can never be sent (Section 12)` |
| neither the lead's domain nor the recipient's is on the `blocklist` | `blocklisted: <domain> is on the blocklist` |
| `settings.auto_approve_email` is on (on by default; the operator's switch) | `auto-approve-off: ...` |

Nothing about the words is checked any more: they are the approved template. Until 2026-10-19 the gate also held every rule tag, every unconfirmed claim code, and every draft a second Sonnet 5.5 call (the claim check, with a repair loop of up to two rewrites) would not pass; all of that went with the composer.

**How it is built.** `gate()` in the shared rules section of `n8n/drafting/code_template.js`, run by Build First Touch (Workflow 4) and Build Follow-Up (Workflow 6) alike — the same function for both. The `drafts_approval_rules` trigger (migration `020`) re-checks it on the table: an `approved_by = 'auto'` approval becomes a hold (`db-guard: …`) unless the flag is on, it is an email, its variant is a template's (`template/…` or `follow-up-N/template/…`) with no `+` tag, its text was not edited, its lead is a CRO, its contact has a verified deliverable address and neither domain is blocklisted. So a workflow bug can make approval *less* likely, never more, and a draft written any other way can no longer approve itself. **A held source is approvable by nobody (migration `031`, 2026-10-30):** a draft whose lead's source is in its niche's `held_sources` (`lead_held_source()`) cannot be approved by a person either — an UPDATE or INSERT from the NocoDB grid or psql raises "Draft N cannot be approved: its source … is held by niche … Nobody can approve a draft from a held source", and APPROVE / EDIT by email is refused with the same reason (the code unspent, REJECT still works). An automatic approval still becomes migration `026`'s hold, so `026`'s lift recipe (clear `held_sources`, re-approve as `auto`) is how a source is released. A reply (`reply/…`) still needs its one-time code (migration `018`), unchanged.

**Proven by dry run 2026-10-19** (`n8n/sendtrack/dryrun/template_dryrun.py`, the real Drafting, Follow-Ups and Send workflows through n8n against a scratch copy of the database, every address `@dryrun.invalid`, SMTP into the loopback sink), 31/31 checks (32/32 after the amendment, adding the retired-follow-up check below): the 30 first touches retired that day were re-queued by `redraft_with_template.py`'s own SQL, and Drafting wrote one template email for each — no model call, no LinkedIn draft, their 35 existing LinkedIn rows untouched; arm A 16, arm B 14; SMYKM trials 9, areas 18, city 3. 26 approved themselves (`approved_by = 'auto'`, accepted by migration `020`'s trigger); the three leads with no address (14, 55, 538) were held `no-address`, and a lead given a country with no clock was held `no-send-clock`. The trigger turned a model-written variant with a passing claim check, a no-address draft forced to `auto`, and a template draft for a site into holds. Follow-Ups wrote and auto-approved a template #1 — after the amendment, for a lead whose only follow-up is its retired AI #1, proving the `retired/` prefix frees the slot. Send found all 27 template drafts eligible and sent a first touch, then the follow-up, into the sink — the three-line sign-off, no opt-out sentence, no phone, `Re:` threading and the quoted first email. The live database's fingerprint was identical before and after. The run's first pass also found that `query.spons`' count is not the lead's own (Workflow 4); the second pass is the one above.

**What the human does now.** The NocoDB grid on `drafts` is still the interface and Approve · Edit · Reject still apply, with **rejection requiring a reason** (bad fit / bad draft / already contacted / wrong contact — the learning loop's data). What needs a person is every `pending` draft: every email held for one of the reasons above, and the LinkedIn DMs drafted before 2026-10-19. A human approval is recorded `approved_by = 'human'` and the hold reason is kept. Editing an auto-approved email's text makes it the human's. To stop an auto-approved email before it goes, reject it. The Daily Digest (Workflow 6) lists what was auto-approved, sent and held.

### Workflow 6 — Send & Track
Built as separate workflows (split because IMAP triggers and cron ticks start differently): `send0001` (cron → guard → claim → SMTP → log), `mailwatch0001` (IMAP on Sent and INBOX), `followup0001`, `imaphealth0001`, `digest0001`. Migration `007`.

**Send:** every 10 minutes, every day of the week, recipient's business hours only, randomised pacing. SMTP node → mailbox → `outreach_log`. **Reply detection:** IMAP triggers on the same mailbox. **Opt-out detection:** reply matching no/unsubscribe/remove/stop → domain to `blocklist`, permanent. **Follow-up:** if no reply after 6 days, a follow-up draft from the fixed template. Maximum two follow-ups, then mark lost. **No HubSpot yet** (the account is Fatima/NoblePath's, not Amitrix's): replied leads get a filtered NocoDB view (`replied_queue`) and an operator notification email instead.

**The warm-up ceiling belongs to the mailbox, derived from the real Sent folder.** Operator manual sends count too. Warm-up starts on the sender-day (Asia/Karachi) of the first send with an external recipient; week = ⌊days since ÷ 7⌋ + 1; only messages with a recipient outside `amitrixlabs.com` and other than the operator's notification address count (notifications use no warm-up slot). Mailbox Watch mirrors Sent into `mailbox_sent` (the whole folder on the first arrival after an activation, then each new message — and, since 2026-10-28, the Sent reconcile below, every 10 minutes); the send path writes its own sends there as SMTP accepts them. **It fails closed:** Send refuses if the mirror has never read the folder (`sent-mirror-never-ran`) or one of its own sends has not appeared there after 15 minutes (`sent-mirror-behind`). The IMAP trigger only fires once a folder holds a message, so nothing sends until Sent has a message and Mailbox Watch is published.

**Claim, then send — at most once.** The draft flips to `sent` and an `outreach_log` row with no Message-ID (a *claim*) is written before the SMTP call. The claim re-checks channel, approval, unchanged body, verified contact, blocklist, reply/bounce and **re-counts the ceiling in SQL under an advisory lock taken in an earlier, separate statement** (the Postgres node runs its text as one implicit transaction, and under READ COMMITTED only a later statement sees a concurrent claim). SMTP refusals split: account-level (auth, TLS, 4xx) → back to `approved`, retried; recipient-level 5xx → `pending` tagged `+smtp-rejected`. The Send Email node has no retry (a timeout after acceptance would send twice) and `appendAttribution` is forced off (Send Email typeVersion 2.1 otherwise appends "sent automatically with n8n").

**Per-draft checks.** `channel='email'` and `status='approved'` in SQL (§6 enforced three times: the query never selects LinkedIn, Decide Send throws on one, the claim refuses one); `contacts.verified`; domain and recipient not blocklisted; no reply/bounce; first touch only to a lead never emailed (including by hand); not a low-context note; **the sender's name line immediately followed by the title line, as whole lines** (a stale signature is refused) — the template's `Cheers!` sign-off carries them, and so does the name / title / phone signature of everything written before 2026-10-19, which still sends; **since 2026-10-19 neither the opt-out sentence nor a phone number is required** (operator decision); at most one URL; **its lead's source not held by its niche** (migration `031`: Load Send State never lists such a draft, so it cannot block its lane, and Claim Send refuses one — `held_source` on the claim says why); **and, since 2026-10-28, a whole email** (`n8n/sendtrack/code_cold_email.js`, refused `not-an-email:<why>`): a greeting line ("Hi …," / "Hello …," / "Dear …,"), at least 20 words of message between it and the sender's identity, and no email address or `mailto:` in that message. Draft 350 (JSS Medical Research) is why: on 2026-10-24 the operator answered its no-address hold with `EDIT <code> Send to this email mailto:bdteam@…`, EDIT kept the greeting and signature and put the instruction in between, and the draft was approved with a five-word message — every other check passed, and only the missing address kept it from going to JSS. The shortest real cold email (follow-up #2) has about 35 words; none of the 11 approved drafts in the queue that day was refused. The same function judges an EDIT on a held email (Workflow 7). **Business hours are the recipient's:** 09:00–17:00 local, from §12's table — all 133 included countries. **Weekend sending — operator decision 2026-10-24:** Send sends on Saturday and Sunday too, in the same 09:00–17:00 local window, under the same 50/day cap and pacing (`WEEKEND_SEND_DAYS` in `code_decide.js`; the build refuses if it and this sentence disagree). A statutory weekend day that is neither — Friday in the Friday–Saturday countries of §12 — still sends nothing. Every other schedule in the pipeline is an interval trigger that already runs seven days a week (§7). Fixed standard-time offsets, **no DST by operator decision** (so a country on summer time runs an hour later; it never sends before 09:00 local, after 17:00 by the table, or on a Friday in a Friday–Saturday country). Looked up case-insensitively, the same as Drafting's gate. A country outside the table is refused `unknown-country`. Pacing (Config, not locked): 10-minute tick, one message at most, 20-minute floor since the mailbox's last external send, then a 40% coin flip. **Queue order — the lanes take turns, 1:1 (operator decision 2026-10-30).** The research-site niche's drafts are one lane and every other lead's — the CRO lane, a lead in no niche included — the other. Decide Send takes the oldest approved draft whose recipient is at work from the lane the last cold send was not from (Load Send State's `last_cold_send_niche`; a claim counts), and when that lane has none at work, the oldest at work in the other, so a lane out of hours never holds up the other. Until then it took the oldest draft of either, so the older CRO drafts went first. Load Send State lists the oldest of each lane first, so its 200-row limit always holds both. Every guard above, the pacing, the ceiling and the safety brake below are unchanged (`test_decide.js`: ten sends from a full queue go CRO, site, CRO, site …).

**Safety brake — operator decision 2026-10-19, with the ceiling raised to 50; share raised 2026-10-24 (operator):** if bounces over the last 3 days exceed 10% of sends, or any spam complaint arrives, Send pauses and the operator is alerted. Built as a latch (migration `021`): every Send tick first runs *Check Brake*, which reads `send_brake_stats()` — of the confirmed emails sent in the window, how many came back bounced; and every `inbound_messages` row with `spam_complaint` — and writes a `send_pauses` row when either trips (strictly over 10%: 10 bounces in 100 sends does not trip, 11 does; with fewer than 10 sends one bounce is over). Until 2026-10-24 the share was 3%; the spam-complaint pause is unchanged. While a row is open, Decide Send refuses (`send-paused`) before any other check, and Claim Send refuses on the table too. The alert is one email to `settings.operator_email`, sent by the Send workflow itself and stamped `alerted_at` only once SMTP accepts it (so a failed one is retried next tick); it uses no warm-up slot. **The pause never lifts on its own** — a complaint does not expire, and a bounce rate that falls only because nothing was sent is not a recovery. To resume: `UPDATE send_pauses SET cleared_at = now(), cleared_note = '…' WHERE cleared_at IS NULL;`. Clearing restarts that reason's count from the moment of clearing (the window is the later of 3 days ago and the last clear), so it does not re-trip on what was just reviewed. Replies the operator approves (`sendreply0001`) are not paused. **Only a permanent bounce counts (operator decision 2026-10-24, migration `022`)** — until then a delay warning counted too (lead 44, 2026-10-06).

**A DMARC report is machine mail (2026-10-24).** Aggregate reports (subject `Report Domain: <d> Submitter: <s> Report-ID: …`, RFC 7489 §7.2.1.1, which Microsoft prefixes `[Preview]`; or "DMARC aggregate report") and failure reports (ARF `Feedback-Type: auth-failure`) are classified `auto-reply` — recorded, never a reply, an opt-out or a bounce — and decided before the bounce check (`isDmarcReport()`, payload `dmarc_report`). Two real ones reached this mailbox (Google 2026-09-15, Microsoft 2026-09-24, from an old `rua`); Microsoft's says "Please remove your email address from the 'rua' tag" and was recorded `opt_out_keyword = 'remove'` — harmless only because it matched no lead. Sent from a contacted prospect's own domain through the shipped Record Inbound (scratch clone, lead 7, rolled back), the old classifier blocklisted the domain (Microsoft's), marked the lead replied so the Reply Assistant would answer it (Google's, empty body), or recorded a bounce the brake counts (Microsoft's via `postmaster@`); the new one stores `auto-reply` and changes nothing. Reports should not reach this mailbox at all — `rua` goes to an outside service (§5) — this is the second line. `test_mailbox.js` (both real reports), `test_send_brake_mutations.py` (seven DMARC mutations, all killed).

**Permanent or transient — what a bounce is (2026-10-24, migration `022`).** Mailbox Watch's classifier (`bounceKind()` in `code_classify_reply.js`) reads the report's subject and whole text, in this order: the report says it is final ("This is a permanent error", "failed permanently", `Action: failed`, "couldn't be delivered", "Address not found") → **permanent**; it says it is only a delay ("WARNING MESSAGE ONLY", "will be retried", `Action: delayed`, "Delivery Status Notification (Delay)", "temporary problem") → **transient**; then RFC 3463 status codes — any 5.x.x permanent, else a 4.x.x transient; a report that says neither is permanent (a delay always says it is one). **The report's own verdict beats the code inside it:** Zoho's final report for lead 44 (2026-10-10) called an SMTP "421 Host not reachable" a permanent error, and counts; its delay warning for the same send listed "fatal errors" while promising four more days of retries, so "fatal" decides nothing; Exchange's and Gmail's delay notices promise to report "if the message can't be delivered" / "if the delivery fails permanently", so future-tense phrases decide nothing either. Record Inbound stores the kind on the bounce row and on the send (permanent sticks). **A permanent bounce unverifies the contact** it bounced from (`contacts.verified = false`, migration `022`'s trigger — only when the bounce names that address, through the Sent mirror's recipients or the report's own text), so no later draft for that lead approves itself or sends until a person enters a working address. **A delay warning is still a bounce on the send** (`outcome = 'bounced'`), so it still stops that lead's follow-ups, as before — the operator's call to change (§13). The notification says which it was (`BOUNCE` or `DELIVERY DELAY`) and whether the contact was unverified; `status_now.py` shows the delays the brake did not count. The five bounces recorded before the migration were classified by `n8n/sendtrack/backfill_bounce_kind.py` (dry run by default; `--apply` in one transaction; re-run finds nothing): lead 44's 2026-10-06 delay transient, the other four permanent, and the contacts of leads 16, 44, 1378 and 1884 unverified (2026-10-23 20:00 UTC). Proven by `test_mailbox.js` (the five real reports' wording, plus Gmail, Exchange and Postfix shapes), `test_send_brake_sql.py` and `test_hard_bounce_sql.py` (the shipped Record Inbound, migration and backfill on a scratch schema copy), `test_send_brake_mutations.py` and `test_hard_bounce_mutations.py` (every rule broken once, every mutation killed), two drift guards, and end to end through n8n 2.44.6 by `template_dryrun.py` (50/50 on 2026-10-23: a delay warning and then its final report, through the shipped classifier and Record Inbound — the first not counted, the second unverifying the contact and tripping Check Brake).

**A spam complaint is an opt-out and trips the brake** (since 2026-10-19): a mailbox provider's feedback-loop report (ARF, `report-type=feedback-report` / `Feedback-Type: abuse`), or a person saying our email is spam in what they wrote — narrow English phrases ("this is spam", "stop spamming", "reported you as spam", "unsolicited email"; never "it went to my spam folder" or "is this spam?"). Either is classified `opt-out` (keyword `spam-report` / `spam-complaint`, so the domain is blocklisted and no reply is drafted) and sets `inbound_messages.spam_complaint`, matched to a lead or not. **Reply classification reads only what they wrote above the quoted thread** (every reply to an email sent before 2026-10-19 quotes our "reply 'no'", which is why the retired opt-out sentence of §5 is still stripped). `no` counts only as the first word, also in Portuguese, Polish, Turkish, Hungarian and Romanian (Czech "ne" omitted). **An out-of-office or bounce is recorded but does not set `replied`.** `inbound_messages` is keyed on Message-ID. Matching, strongest first: `thread` (our Message-ID in their References), `contact-email`, … `lead-domain`; **the reply goes back to the address that wrote**, not necessarily `contacts.email`. A positive/neutral signal (`positiveSignal()`) is computed at notification time and stored nowhere. The operator notification goes to `settings.operator_email`. The first real prospect reply (lead 26, Hungary, 2026-10-07) was checked end to end and bookkept correctly; thread headers are now stored (migration `017`).

**The Sent reconcile (2026-10-28) — the mirror does not depend on the trigger.** The Sent trigger (n8n `EmailReadImap` v2.2, search `ALL`, Fetch Only New Emails off) fires only when a message **arrives**: the first arrival after an activation reads the whole folder, later arrivals only newer UIDs, and a reconnect catches up only on what is newer than the last UID it saw. A send that lands in Sent while its connection is not listening is never delivered to it — not by a reconnect, a publish or an n8n restart — and since Send then fails closed `sent-mirror-behind`, nothing new reaches Sent to wake the trigger: a deadlock (2026-10-28, 07:40–08:22 UTC, below). So Mailbox Watch also reads the folder on its own clock: the Sent reconcile runs every 10 minutes (inside Send's 15-minute mirror grace; the build refuses an interval that is not), and at once when n8n starts or the workflow is published (an `n8n Trigger` on Instance Started / Workflow Published / Published Workflow Updated). n8n gives a workflow no event for an IMAP reconnect — the trigger's reconnect handler only writes a debug log (`EmailReadImapV2.node.js`, read in the container) — so the 10-minute tick is what covers one. **How:** *List Sent Folder* calls the imap-health checker (`imap_preflight.py --ids-json`: a fresh read-only session, EXAMINE and BODY.PEEK, every Sent message of the last 48 hours by Message-ID with its Date, To, Cc and Bcc); *Plan Sent Reconcile* (`code_reconcile_sent.js`, with `code_mirror_sent.js`'s library placed above it) turns the listing into rows with the trigger path's own `sentRows()`; *Reconcile Sent* is Mirror Sent's upsert, its `r` and `up` CTEs cut verbatim out of Mirror Sent's statement by the build. **Safety:** only a complete listing counts — the checker's IMAP check passed and `sent` is a list; anything else reconciles nothing (`listed = false`), so a row is never marked seen unless the folder holds its Message-ID; an entry without a well-formed Message-ID is skipped and counted, never keyed on the trigger's fallback hash (which hashes headers the listing does not reproduce byte for byte, so it would make a second row); rows are upserted on the Message-ID primary key, so a known message only gets `seen_in_sent_folder = true` and a repeat changes nothing. Each read is stamped on its own `mailbox_sync` row, `Sent-reconcile`; the `Sent` row stays the trigger's proof of life. Messages older than the listing's 48 hours are `reconcile_sent_mirror.py`'s (below).

**Repair by hand — `n8n/sendtrack/reconcile_sent_mirror.py` (2026-10-28; replaces the hand `UPDATE`).** `python n8n/sendtrack/reconcile_sent_mirror.py` lists the real Sent folder read-only from the host (`imap_preflight.py`, whole folder by default, `--hours N` for a window), runs the shipped *Plan Sent Reconcile* code and *Reconcile Sent* statement out of `mailbox-watch.json`, and shows what they would do — inside a transaction that it rolls back — plus every workflow send the folder does not hold (those it can never mark). `--apply` commits the same statement. It never marks a send seen that the folder does not hold, and it is safe to run while Mailbox Watch runs (the upsert is keyed on Message-ID).

**Mailbox Watch is proven live only when it fires.** The IMAP trigger cannot act as a heartbeat; sockets drop on host sleep, DNS blips and server closes, and n8n's backoff can leave a trigger down for hours after the mailbox is reachable. Inbox is the only path that catches an opt-out. **IMAP Health** (`imaphealth0001`): the health check runs every 20 minutes, through the `imap-health` sidecar container (a sidecar, not Execute Command, by operator decision: no shell access for workflows; read-only filesystem, runs as `nobody`, no published port). It asks (1) does a fresh read-only IMAP session work, and (2) did Mailbox Watch miss any INBOX/Sent message from the last 48 hours older than 15 minutes (the proof that the triggers are still listening). **An INBOX message counts as recorded when it is in `inbound_messages` or `operator_commands`** (since 2026-10-19): Mailbox Watch records the operator's command mail (APPROVE / REJECT / EDIT with a code) in the second, so reading only the first reported every command as missed for 48 hours — the 2026-10-18 16:13 UTC "still waiting: JSS Medical Research" APPROVE, handled at 16:13:59, kept IMAP Health failing `inbox-missed` until the fix. A message in neither table still alerts (`n8n/sendtrack/test_health_sql.py`, the shipped query against a scratch copy of the schema, with `test_health_sql_mutations.py`; the build refuses a query that drops either table). Two consecutive failing checks are needed to alert — except `send-blocked`, which already carries its 20 minutes and alerts on the first check that finds it (so the alert comes 20–40 minutes into a block, at the 20-minute check interval); then one email to the operator, a reminder every 6 hours, and one all-clear. Problems: `checker-unreachable`, `imap-failed`, `inbox-missed`, `sent-missed` (since 2026-10-28: in the folder but not confirmed **seen** by the mirror — Send writes its own row as SMTP accepts, so a row alone proved nothing), `sent-mismatch` (the mirror marks seen a Message-ID the folder's listing does not hold; only with a complete listing, inside its window with an hour's margin, never the mirror's fallback ids) and `send-blocked` (a workflow send unseen for longer than Send's 15-minute mirror grace plus `block_alert_min` = 20, i.e. Send has refused everything for more than 20 minutes — Load Send State's own rule, word for word; it does not need the checker). Migration `024`. It cannot report its own absence when n8n or the host is down.

**Follow-ups are templated, not composed (since 2026-10-19).** Build Follow-Up writes #1 or #2 from the blocks below with the same `code_template.js` functions as a first touch (its rules section embedded verbatim): the same greeting rules, the same cleaned `{company}`, and #1 closes with the lead's own arm's close — lead_id even A, odd B, the arm its template first touch had. Threading is as before: the subject is `Re:` the first subject, and the first email is quoted below `On <date>, Abdullah Amir wrote:`. The gate is Workflow 5's; no model call. Variant `follow-up-<n>/template/<arm>` (+ hold tags). Timing unchanged: #1 six days after the first touch, #2 six days after #1, then lost. Find Due Follow-Ups counts follow-up *numbers*, not rows (a regenerated #1 is still one slot), a rejected follow-up uses its slot, and since 2026-10-19 only CRO leads are due. **A retired follow-up does not use its slot:** its variant is prefixed `retired/`, so it matches neither the `follow-up-%` count nor Write Follow-Up's dedupe, and restarts no clock. The follow-up runs every 30 minutes, not every 6 hours: the hours rule skipped the one tick that found three leads due (§7). **The 41 unsent AI-composed follow-ups were retired on 2026-10-19 (operator):** drafts 262–303 except 299 (sent 2026-10-18) — 40 approved and the pending 293 — set `rejected` / `bad draft`, the variant prefixed `retired/`, a note in `hold_reason`, at 22:28 UTC, before Monday's business hours opened. A plain reject would have used each slot and skipped the lead to #2. All 41 were already due, so Follow-Ups wrote their template follow-ups in its next five runs (22:30–00:30 UTC, ten a run): #1 for 38 leads, #2 for leads 7, 91 and 104, whose AI #1 had been sent on 2026-10-05 and whose retired draft was a #2.

**Follow-up templates — LOCKED (wording approved by the operator 2026-10-19; do not change).** `{arm close}` is the first touch's close for the lead's arm; #2 has none.

```text
Follow-up #1:
Hi {first_name},

Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits {company}'s website is a potential study, and we help make sure those visits turn into conversations.

{arm close}

Cheers!
Abdullah Amir
Founder, Amitrix Labs

Follow-up #2:
Hi {first_name},

I'll keep this short and won't follow up again after this. If turning more of {company}'s website visitors into qualified leads is ever on your list, just reply and I'll send over the details.

Cheers!
Abdullah Amir
Founder, Amitrix Labs
```

**Daily Digest (`digest0001`, `digest_log`).** One plain-text email a day to `settings.operator_email`, at the first 30-minute tick at or after 08:00 operator time (Asia/Karachi; before India, the earliest recipient, opens at 08:30), covering from where the last digest stopped: **AUTO-APPROVED**, **SENT** (and who approved it), **HELD FOR A PERSON** (with `hold_reason`; for a held email it carries a one-time code, §9 Workflow 7). Recorded only once SMTP accepted it. No address, no digest. It uses no warm-up slot. **Since 2026-10-30 (migration `028`, operator brief) HELD is counted, not listed:** one line per hold class (`hold_reason_class()`: the reason before its first `:`, a db-guard reason with its first problem, no dates or parentheses), and one line with a code only for a held email whose class is **allowlisted** (`hold_reason_classes.needs_person`, seeded `auto-approve-off` and `claim-check`; at most `held_cap` = 15, then "+N more"). Everything else — `no-address` (operator: no address, no email to me), `unverified-address`, source and niche holds (`db-guard: …`), `no-send-clock`, `blocklisted`, `smtp-rejected`, operator holds, LinkedIn — is counted and never coded, and `reply_approval_issue()` itself returns no `email-hold` code for one. A class never seen before gets a `[NEW]` line, then is recorded with `needs_person = false`, so a new hold type is silent by default. On the tick that sends, every open `email-hold` code whose draft no longer needs a person is withdrawn (`reply_approval_withdraw_holds()`). Why: on 2026-10-30 21:00–21:02 PKT the operator got 20+ "still waiting" emails about 166 open codes, 153 of them Overture source holds; none was a decision about the words. The subject line is unchanged. `n8n/sendtrack/dryrun/hold_notice_preview.py` shows, for the live held set, what the next reminder tick and digest would send (rolled back).

**Verified by dry run, not live send** (`n8n/sendtrack/dryrun/dryrun.py`): the real workflows through n8n against a database copy with every contact rewritten to `@dryrun.invalid` and SMTP into a loopback sink. Proven: ceiling enforced (5th send of a 5/day week goes, 6th refused), LinkedIn never selected, blocklist skipped, pending/rejected/low-context never selected. The template path is proven by `template_dryrun.py` (Workflow 5).

### Workflow 7 — Reply Assistant

**A prospect reply gets a drafted answer, held for a one-time code, never sent without it.** Three workflows built on migrations `017`/`018`.

**`reply0001` — the draft and the code.** Every 30 minutes it reads each `inbound_messages` row classified `reply`, matched to a lead, with no `reply_approvals` row for its Message-ID — one drafted reply per prospect *message* for ever (delete the `reply_approvals` row to ask for another; a prospect who writes again gets a fresh answer). The blocklist is checked too. `claude-sonnet-5-5` at effort `high` drafts from their message, the thread as sent, the enrichment boundary facts and the confirmed claims — the only model call in the path (the operator reads every one, so no claim check). Five topics nothing confirmed can settle (pricing, integrations, languages, security, timelines) are detected in *their* text and the model is told to defer them. `Assemble Reply` enforces the claim rules of `n8n/drafting/code_assemble.js` (the retired composer's rule functions, kept for this path alone and unchanged on 2026-10-19) plus 40–120 words, one next step, the one-URL cap, and **never reuses the "two CROs" proof line or calls Vertex a CRO** (`proof-refused`). The draft is written `pending` — **a reply is never auto-approved** (refused on the table) — and its one-time code is minted by `reply_approval_issue()`. `Build Review Email` sends the operator (their own inbox, no warm-up slot) what the prospect wrote, the exact bytes that would be sent, what the reply does not answer, the rule tags, and `APPROVE <code>`, `REJECT <code>`, `EDIT <code> <replacement>`. A second lane reminds about any open code the operator has not been told about in 4 hours — **a `reply` code only, since 2026-10-30** (migration `028`): a held cold email is the Daily Digest's alone.

**Words a reply never uses:** revolutionary, cutting-edge, innovative, game-changing, seamless.

**The operator fork in Mailbox Watch — the hazard this exists to remove.** The operator answers the review email *inside the prospect's thread*, so `Record Inbound`'s `thread` matcher would tie the operator's one-word "no" to the prospect's lead and blocklist them permanently. So `Classify Inbound` parses the operator-command shape **first**; `Command Shaped? → Apply Operator Command → Operator Mail?` run before `Record Inbound` sees a command-shaped message, and `Record Inbound` independently refuses to match the operator's own address to any lead. Three separate questions (`code_operator_command.js`): **record** (command-shaped at all → every one gets an `operator_commands` row, accepted or refused), **handled** (SPF, DKIM and sender all authenticated → it is operator mail, never a prospect reply), **accepted** (handled and nothing refused it → only then does a draft move). `Apply Operator Command` re-derives all three from the database at write time. A plain "no" from the operator is not command-shaped and is matched to no lead.

**EDIT on a held cold email, and CONTACT (2026-10-28, migration `023`).** A held first touch or follow-up carries a one-time code too (kind `email-hold`, in the Daily Digest) — since 2026-10-30 only when its hold is allowlisted (Workflow 6, Daily Digest), and never reminded about. EDIT replaces the email's whole text under its greeting and the reply signature — it was built for replies — so on an `email-hold` code the result must pass `code_cold_email.js` (the rule Send applies, Workflow 6); otherwise it is refused, the code stays good, and the refusal names the command that was probably meant. **CONTACT gives a lead its address** — the thing EDIT cannot do. Mail the mailbox from the operator address, one line per lead, as many lines as wanted, no code:

```text
CONTACT <lead domain> <email address> [name]
```

The domain names the lead (`leads.domain`; a leading `www.` is ignored, and mail-client autolinks such as `x.com <http://x.com>` or `mailto:` are unwrapped). Authentication is the same as for every command (SPF, DKIM, the operator's own address; anything else is recorded refused and, not being operator mail, falls through to Record Inbound). Apply Operator Command judges each line against the database and refuses it when the lead is unknown, was ever emailed (`outreach_log` or a manual send in `mailbox_sent`), is not at `scored` / `contact_found` / `drafted`, or either domain is blocklisted; of two lines for one lead the later wins. Every line that passes, in the same statement: the contact is upserted with that address and `verified = true` (the operator's word); a name on the line replaces the stored name and clears the title, otherwise the stored name stays (the greeting uses a name only when the address carries it, Workflow 4); the lead's `contact_attempts` row is deleted; the lead moves to `contact_found`. Drafting then writes the first touch on its next tick through the normal gate (Workflow 5) and retires any earlier unsent first touch. CONTACT sends nothing itself. A re-delivered message changes nothing (its side effects run only while its Message-ID is not yet in `operator_commands`). The acknowledgement lists every line, set or not and why. The digest shows the `CONTACT <lead domain> <email address>` syntax on its `no-address` / `unverified-address` count line (since 2026-10-30 those drafts are counted, not listed). Proven by `test_operator_command.js` (parsing, autolinks, authentication, the refused EDIT) and `test_contact_command_sql.py` (the shipped Decide Command and Apply Operator Command against a scratch copy of the live schema with migration `023`: 23/23 — each refusal reason, the forged sender, DKIM failing, the re-delivery, the 2026-10-24 EDIT refused with its code unspent). Not yet exercised by real operator mail.

**`sendreply0001` — a separate lane from cold `send0001`.** An answer to a message a prospect sent *us* is not cold outreach: it skips the warm-up ceiling, business hours and pacing floor (structurally — its query carries no clock table). **It never skips:** the blocklist (lead domain and the address answered), the one-time code re-checked at claim time (outcome `approved` or `edited`, belonging to *this* draft), the reply signature (name / title / phone, §5) and one-URL cap, and at-most-once. `d.variant LIKE 'reply/%'` is the whole boundary between the lanes. It threads through the `smtp-send` sidecar (`smtp_send_server.py`, `POST /send`), because n8n's Send Email node has no `headers` option and so cannot set `In-Reply-To`/`References`; the sidecar holds only the five mailbox/SMTP variables, a read-only filesystem, `user: "65534:65534"` and **no published port** (it has no authentication; the build refuses a `ports:` entry). It refuses to send as any From but the configured mailbox.

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
- **Outreach (2026-10-19):** none yet. The fixed template is for CROs; site and SMO leads wait at `contact_found` (§9 Workflow 4). The site claims and proof above apply to the Reply Assistant only.

**Second case study — geography-matched proof (2026-09-19).** Nova is live at NoblePath (Türkiye) and Vertex Clinical Research (Mexico). Since 2026-10-19 no cold email carries a proof line (the template names no client); the lines below matter to the Reply Assistant only. A CRO's proof line is resolved from `claims_library` by country: Vertex for Mexico/Brazil/Argentina; NoblePath for Turkey, Egypt, UAE, Romania, Hungary, Poland and Czech Republic ("nearby" is a judgement and one edit); the catch-all `PR-BOTH` ("It's live at two CROs, in Türkiye and Mexico") everywhere else — **that wording is wrong for Vertex and is on the fix list (§13)**. Each proof line's `countries` is the mapping, so the operator owns it.

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

**Offsets are standard time, no DST — operator decision 2026-09-14.** A country on summer time (Europe, North America, Egypt, Israel, Lebanon, Chile, New Zealand, south-east Australia) runs an hour later than the table for part of the year, so the last hour of its window can land just after close; Morocco drifts the other way during Ramadan (harmless). Paraguay, Jordan, Turkey, Mexico and Brazil abolished summer time and sit on one offset, which is what the table carries. It never sends before 09:00 local, or on a statutory weekend day other than Saturday and Sunday (§9 Workflow 6, weekend sending). A real tz database is not available in the Code-node sandbox; revisit only if a reply says the timing was wrong.

**The weekend column is each country's statutory weekend** — since 2026-10-24 Send sends on its Saturday and Sunday anyway (§9 Workflow 6), so in practice the column only stops Friday in the Friday–Saturday countries: Friday–Saturday across the Gulf and most of the Arab world plus Israel, Bangladesh, Algeria, Djibouti and Mauritania; Saturday alone in Nepal (UTC+5:45, carried as 345 minutes); Saturday–Sunday everywhere else, UAE included (since 2022), and in Lebanon, Morocco and Tunisia.

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

Status as last recorded 2026-10-28 (rows not touched that day: 2026-10-24, 2026-10-19 or 2026-10-10); verify live state with the §3 signals. Finished items were removed in v2.

| Item | Owner | Blocks |
|---|---|---|
| **Uptime — the stack runs only while the PC is on (2026-10-28).** Measured Sat 10-24 → Tue 10-27 from Send's own ticks: n8n usually runs 05:40–22:40 UTC. The PC is a desktop (no battery); idle sleep and hibernate on AC are both "never"; every night it is powered off by hand from the Start menu (event 1074, 10-24 → 10-27), and on 10-27 it hibernated 12:19–13:17 UTC (Kernel-Power 42, TargetState 4 — requested, not idle). Docker Desktop starts at login (`AutoStart: true`, Run key enabled) and every container has `restart: unless-stopped`; a Docker Desktop restart on 2026-10-28 08:11 UTC brought all five containers back by themselves in 16 s. **What stopped n8n at 13:46 UTC on 10-27:** the Docker Desktop window's Stop on the `nova-scout` compose project (backend log: `POST /compose/bulk/:action` from the GUI, then `ContainerStopComposeLinux` ×5, 5 s after the dashboard was opened). A manual stop is exactly what `unless-stopped` does not undo, so nothing ran until the project was started by hand at 06:58 UTC on 10-28. **Recipients whose 09:00–17:00 falls mostly while the PC is off** (share of the window n8n actually ran): Australia 26%, Japan / South Korea 32%, China / Hong Kong / Malaysia / Singapore / Taiwan 37%, Thailand 43%, Nepal 53%, India 56% (21 leads), Pakistan / Uzbekistan 60%; Europe, the UK, Africa and the Americas 61–83%. Ollama's Startup shortcut is disabled in Windows Startup apps (StartupApproved flag 03), so Ollama does not start at login. | Abdullah | Asia-Pacific sends; enrichment after a reboot |
| ~~Mailbox Watch does not catch up on Sent after a dropped connection, and IMAP Health does not notice~~ — **fixed 2026-10-28 (later):** the Sent reconcile and the hand-repair tool (§9 Workflow 6), `send-blocked` / `sent-mismatch` and the stricter `sent-missed` in IMAP Health (migration `024`). **Root cause of the 07:28:37 UTC drop [verified]:** not the network — no Windows event at all between 07:20 and 07:36 UTC, and IMAP Health's fresh sessions worked at 07:20 and 07:40. It was the Sent trigger's own scheduled connection replacement: `forceReconnect: 30` makes n8n's `@n8n/imap` wrapper (`imap-simple.js`, `scheduleReplace`) close and reopen each trigger's IMAP connection every 30 minutes, and n8n had started at 06:58:28 — the error came 30 min 9 s later. The only other `Connection not available` since n8n came back, 10-26 12:03:13 UTC, also falls on that schedule (13 × 30 min after the 05:32 start, within a minute). After that replacement the Sent connection reported no new message: the 07:40 send never arrived, and no `Connection closed unexpectedly` was logged, so n8n did not reactivate it. Why the replacement left it deaf is **not established** [assumed: a race in the replacement — the old connection is closed without waiting, the new one opened at once, both triggers in the same second; n8n logs it only at debug level]. **Will it recur:** yes — each trigger replaces its connection every 30 minutes (~34 times a day each while n8n runs); 2 errors in about 3 days of uptime, one harmful. `forceReconnect` was left at 30: without it a silently dead IDLE would never be replaced. What changed is that a missed arrival no longer matters: the reconcile mirrors the folder within 10 minutes whatever the trigger does. Original row: Mailbox Watch does not catch up on Sent after a dropped connection (found 2026-10-28): the 07:40:36 send (draft 354) was never mirrored; Send failed closed `sent-mirror-behind` from ~07:55; reconciled by hand at 08:22 UTC. | Abdullah | — |
| **The CONTACT command — first real use (2026-10-28).** Built, deployed and proven on a scratch database (§9 Workflow 7), not yet used by real operator mail. 26 CROs wait in `needs_manual_contact` (15 from before, 11 from the ≥ 40 gate); `docs/manual_contacts_2026-10-28.md` lists each with its website, contact page and every address its site publishes. | Abdullah | 26 leads' first touches |
| **Off-domain ICH GCP addresses (2026-10-28).** The ≥ 40 gate brought in lead 526 (medfiles.eu), whose ICH GCP profile address is an e-invoicing inbox (`…@docinbound.com`); its contact was set `verified = false` by hand before Drafting reached it, so it will be held. Six more in the band use a sister or parent domain (`theradex.co.uk`, `cnrres.com`, `wuxiapptec.com`, `mcr-med.com`, `creative-biomart.com`, `clinicology.com`); the existing rule accepts a scraped profile address on any domain, and they were left to it. | Abdullah | — |
| ~~Template outreach — publish, then re-queue~~ — **done 2026-10-19:** all three published, n8n restarted at 07:45 UTC (the old AI builds had kept firing beside the published ones until then; they wrote nothing — their Claude calls failed on the API credit balance and DNS), the amended Drafting published, `--apply` run at 08:02 UTC. Original row: **Template outreach — publish, then re-queue (2026-10-19).** Migration `020` is applied live; Drafting, Follow-Ups and Send are imported unpublished, and their old versions keep running from n8n's memory until each is published (§3). **1.** Publish Drafting (`drafting0001`). **2.** Run `python n8n/drafting/redraft_with_template.py --apply`: it re-queues the 30 leads whose AI first touches were retired that day (and retires any AI first touch written since), and refuses until the published Drafting is the template build. The next Drafting tick writes their template drafts. **3.** Publish Follow-Ups (`followup0001`) — until then the old one keeps composing with Sonnet, and the table now holds whatever it writes. **4.** Publish Send (`send0001`) — until then the old Send refuses every template draft (`no-opt-out`) and keeps sending the AI follow-ups below. Then measure: the arm and the SMYKM fact are in `drafts.variant`; positive replies per arm, ~2 weeks | Abdullah | Any template email going out |
| ~~Hard bounces — publish Mailbox Watch, then restart~~ — **done 2026-10-24:** published 06:14 UTC (publish history), n8n restarted by the 09:12 image-pin recreate (one version per workflow since), `backfill_bounce_kind.py` dry run 10:05 UTC: "nothing to do: every bounce in novascout already has a bounce_kind". Original row: **Hard bounces (migration `022`) — publish Mailbox Watch, then restart (2026-10-24).** Migration `022` is applied live and the five old bounces are backfilled (contacts of leads 16, 44, 1378, 1884 unverified). The brake's new count is live already — it is the database function, and `send.json` did not change. `mailbox-watch.json` is imported **unpublished**, and on n8n 2.44.6 the import stopped the running one (§3): **Mailbox Watch has been off since ~20:10 UTC 2026-10-23** — no INBOX mail read (opt-outs included), the Sent folder not mirrored, so Send refuses `sent-mirror-behind` from 20:55 UTC (fails safe; send 77 of 20:40 UTC is the one waiting to be mirrored). **1.** Publish Send & Track - Mailbox Watch (`mailwatch0001`) in the UI — this is what turns it back on; its Sent trigger re-reads the folder and Send resumes. **2.** `docker compose restart n8n` — publish first: a restart before the publish leaves Mailbox Watch off, Send then fails closed `sent-mirror-behind` and no opt-out is read. **3.** Check `execution_entity."workflowVersionId"` shows one version of `mailwatch0001`. **4.** `python n8n/sendtrack/backfill_bounce_kind.py` (dry run) — it classifies any bounce the old version recorded meanwhile; `--apply` if it lists one | Abdullah | The contact rule on new bounces |
| **A delay warning still stops that lead's follow-ups** (2026-10-24). It no longer counts toward the brake, but it is still `outcome = 'bounced'` on the send, and Send and Follow-Ups stop on that. Conservative and unchanged by the operator's brief; if a delayed message is later delivered the lead gets no follow-up. To change: key the per-lead stop on `bounce_kind IS DISTINCT FROM 'transient'` in Load Send State, Claim Send and Find Due Follow-Ups | Abdullah | — |
| **Anthropic API credit balance is empty** (since 2026-10-18; re-checked 2026-10-23 with a 1-token call: HTTP 400 "Your credit balance is too low"). The Reply Assistant's next draft will fail and stay queued (no fallback, §3), and the Trialsites website lookup failed 360 calls on 10-18/19 (100 on the balance, the rest on a DNS outage): **120 candidates sit at `call-failed` with 3 attempts, which is final**. After topping up: `UPDATE site_candidates SET lookup_attempts = 0 WHERE lookup_status = 'call-failed';` re-queues them (the batch retries `call-failed` under 3 attempts) | Abdullah | Reply drafts; site supply |
| ~~Chat-widget fix — publish Enrichment~~ — **done 2026-10-24:** published by the operator before the 10:17 UTC restart; the first runs failed on Ollama (a half-applied upgrade, §3) and wrote nothing. After Ollama 0.40.10 was reinstalled, Enrichment runs 5898 + 5901 re-enriched all 19 (`has_chatbot` false on every one; pre-run rows backed up again, scratchpad only), Scoring 5902, Contact Lookup 5914, Drafting 5922: exactly the clone's prediction — auto-approved emails 378 (734), 379 (745), 380 (2007), 381 (2019); 707, 1852, 1946 `exhausted` at fit ≥ 50 with no address (manual look-up); 749, 1641, 1650, 1966, 2005, 2104 scored < 50; 48, 1652, 1755, 1814, 1905, 1992 disqualified as not a CRO, site or SMO. 2007 (Quorum Review → Advarra) was not dropped, so draft 380 is in the send queue; reject it to keep it out. Original row: **Chat-widget fix — publish Enrichment (2026-10-24).** `enrichment.json` (the HubSpot loader check, §9 Workflow 2) is imported **unpublished** — the import stopped the running Enrichment (no 10:00 tick), so nothing is enriched until it is published. 19 leads the old filter flagged that have no chat (live-checked 2026-10-24) are back at `ingested`: 707, 734, 745, 749, 1641, 1650, 1814, 1852, 1905, 1966, 1992, 2005, 2007, 2019, 2104 (chat was their only disqualifier) and 48, 1652, 1755, 1946 (chat plus "not a CRO" from a pre-2026-10-09 enrichment); their pre-requeue rows are backed up in that session's scratchpad only. **1.** Publish Enrichment (`enrichment01`); Scoring, Contact Lookup and Drafting take them from there. The same build run through n8n on a scratch clone: 4 auto-approved emails (734 `connect@imperialcrs.com`, 2019 `business@rhoworld.com`, 745 `Info@pharmexcel-cro.com`, 2007 `info@QuorumReview.com`), 3 scored ≥ 50 with no address found (707, 1852, 1946 → manual look-up), 6 scored < 50, 6 disqualified as not a CRO, site or SMO. **Lead 2007 is Quorum Review — acquired by Advarra; its site redirects there, its mail goes to Mimecast, and the source spells it "Quorom".** To keep it out, before publishing: `UPDATE leads SET status = 'disqualified' WHERE id = 2007 AND status = 'ingested';` (its score row still says why it was disqualified before). Several other requeued domains redirect to an acquirer too (healthdec → Premier Research, quartesian → Veranex, watermark → Advarra, acurian → Trialmed, alcherabio → Argenta); re-enrichment scores what the site says now | Abdullah | 4 more first touches |
| ~~DMARC reports — publish Mailbox Watch~~ — **done 2026-10-24:** the repo build's Classify Inbound (eb801de) was written into the draft through the n8n MCP (`update_workflow`, byte for byte with `mailbox-watch.json`; code and queries identical on all 19 nodes), which left the published version active (no mail arrived in the 53 s before the publish, so trigger continuity was not observed), and published through the MCP at 12:30:46 UTC — no import, no restart; the next executions all ran `2a125816` (§3). Original row: **DMARC reports — publish Mailbox Watch (2026-10-24).** `mailbox-watch.json` (DMARC reports are machine mail, §9 Workflow 6) is built and tested but **not imported**: on 2.44.6 an import stops Mailbox Watch until it is published (opt-outs unread, Send failing closed `sent-mirror-behind`), so import it only when the publish follows at once. **1.** Away from :00/:30: `docker exec -i nova-scout-n8n-1 sh -c "cat > /tmp/ns_mailwatch.json" < n8n/workflows/mailbox-watch.json`, then `docker exec nova-scout-n8n-1 n8n import:workflow --input=/tmp/ns_mailwatch.json` (PowerShell). **2.** Publish Send & Track - Mailbox Watch in the UI at once. **3.** `docker compose restart n8n`. **4.** One `workflowVersionId` of `mailwatch0001` in `execution_entity` | Abdullah | DMARC reports read as replies, opt-outs or bounces |
| **Mail DNS — a 2048-bit DKIM key and a DMARC `rua` (2026-10-24, plan).** §5 has the measured state. **DKIM, in this order:** (1) Zoho Mail Admin Console → Domains → amitrixlabs.com → Email Configuration → DKIM → Add selector `zmail2048`, key size 2048 (third-party guides describe the option; Zoho's own help page does not state the size — if the screen offers no 2048, stop). (2) Hostinger → DNS: one new TXT record, name `zmail2048._domainkey`, value exactly as Zoho shows it; leave `zmail._domainkey` alone. (3) `python n8n/sendtrack/check_mail_dns.py --selector zmail --selector zmail2048 --expect-bits 2048` until both resolvers show it as 2048-bit. (4) Zoho → Verify the selector. (5) Make `zmail2048` the default (and enabled). (6) Send one message from the mailbox to the operator's address (exempt from the warm-up count) and check "Show original": `DKIM: PASS`, `s=zmail2048`. (7) Keep `zmail` published at least 7 days, then delete it in Zoho, then its DNS record. Rollback until then: make `zmail` default again. **DMARC:** sign up for Postmark's DMARC digest with the operator's Gmail (not this mailbox), then **replace** the `_dmarc` TXT with `v=DMARC1; p=none; rua=mailto:<the re+… address Postmark gives>` — one record only; no `ruf`. `check_mail_dns.py` must then show one DMARC record and the rua authorized | Abdullah | DKIM strength, DMARC visibility |
| ~~n8n is 2.44.6, and unpinned~~ — **done 2026-10-24:** every image pinned to version + digest and applied (§3, "Image pins, and upgrading on purpose"). Original row: **n8n is 2.44.6, and unpinned.** The 2026-10-23 Docker reinstall pulled `n8nio/n8n:latest` (2.35.7 → 2.44.6, §3). It works as checked that day, but the next pull can change it again without anyone choosing to. Pin the image to the version in use (`n8nio/n8n:2.44.6`) — and `nocodb/nocodb:latest` likewise — so an upgrade is a decision | Abdullah | Predictable n8n behaviour |
| ~~**Send cap 50 + safety brake — publish, then restart (2026-10-19).**~~ — **done, verified 2026-10-23:** since n8n came back up every workflow runs one version, and all 13 published versions match the committed JSON node for node (Send carries Check Brake and week 5+ = 50). Original row: **Send cap 50 + safety brake — publish, then restart (2026-10-19).** Migration `021` is applied live. Send (`send0001`) and Mailbox Watch (`mailwatch0001`) are imported unpublished: publish both in the UI, then restart n8n (§3) and check `execution_entity."workflowVersionId"` shows one version of each. Until then the old in-memory Send keeps the 20/day ceiling and has no brake, and the old Mailbox Watch flags no complaint. Measured the same day (the shipped Decide Send code, 400 runs from the live queue of 64 approved emails): median finish 47 h, at most 47 sends in a sender-day — pacing, not the 50 cap, sets the pace (at 20/day: 75 h) | Abdullah | 50/day and the brake |
| **`n8n/sendtrack/dryrun/dryrun.py` no longer fits the live data** (found 2026-10-19). It stages its scenarios on September's drafts 31–39 and expects them to be the only approved emails; those drafts have since been rejected or sent and their leads emailed, and the copy now holds ~60 approved template drafts, so 10 of its checks fail and section E crashes — the same 10 at commit `71b3731`, before the brake (run side by side). The template dry run (`template_dryrun.py`, 50 checks on 2026-10-23) covers Send end to end on today's data, brake included. Its clock is frozen at 2026-10-20 15:00 UTC, which has been in the past since then: every time in its brake section is anchored to that clock, never to `now()` (fixed 2026-10-23, when a real 10-23 send fell outside its window). Re-stage `dryrun.py` on fixtures of its own, not on live draft ids | Abdullah / Claude | — |
| ~~40 AI-composed follow-ups approved before the switch still send~~ — **done 2026-10-19:** all 41 unsent (40 approved + pending 293) retired with the `retired/` prefix, and their leads given template follow-ups (§9 Workflow 6) | Abdullah | — |
| **Template copy, for the operator's eye.** Amended 2026-10-19: a lead with no other fact opens on the `company` line, and a single phase reads "in Phase II". By code's choice, not stated in the amendment: a gap also reads "in Phase I and III" ("from" only before a range). Code writes "an" before a vowel in "a {area1}" and an apostrophe alone after a name ending in s ("Therapeutics' website"). Change the wording in §9 and `code_template.js` together if wanted | Abdullah | — |
| **Workflow 3 still scores the 20 trial points on `query.spons`' raw `totalCount`**, a full-text match ("Therapeutics" matched 1,175 trials of other companies; §9 Workflow 4). The template no longer prints it; scoring was out of the 2026-10-19 brief | Abdullah | Scoring accuracy |
| **Fix the "two CROs" proof line (`PR-BOTH`)** — Vertex Clinical Research is a research center, not a CRO (§12). Since 2026-10-19 no cold email carries a proof line; only the Reply Assistant can still use it. Also confirm the NoblePath proof claim | Abdullah | Honest CRO-side proof |
| **Sites and SMOs: published, not drafted.** Every workflow imported 2026-10-10 was published by 2026-10-18 (`workflow_entity.active`, Trialsites included — see the licence row below). Since 2026-10-19 the template is for CROs, so site and SMO leads wait at `contact_found` (two on that day) | Abdullah | Site outreach (needs an approved site template) |
| **The 18 site and SMO claim lines** (`company_types` site/SMO, seeded `active = false, confirmed = false`) matter only to the Reply Assistant since 2026-10-19 — no cold email reads `claims_library`. Two notes still say "operator to confirm" (whether the tenant prompt qualifies a CRO choosing sites the way it does a sponsor; that `capture_investigator_registration` still names NoblePath's network) | Abdullah | A site reply drafted from those lines |
| **Trialsites says "Free for non-commercial use"** on its API page (no other licence text). Nova Scout is commercial outreach. Ask AIMR (`info@aimronline.org`) for permission, accept the risk, or don't publish `trialsites0001` | Abdullah | Publishing Trialsites |
| **Push `main` to GitHub** so `nova-one-pager-sites.docx` exists at `raw.githubusercontent.com/abdullahamir2308/nova-scout/main/` (the URL a site/SMO reply notification links; the CRO one-pager is already there) | Abdullah | A working site one-pager link |
| **The CRO pipeline is out of supply** (measured 2026-10-10): 506 leads — 271 `scored`, 157 `disqualified`, 44 `sent`, 33 `drafted`, 1 `replied`; nothing at `ingested`, `enriched` or `contact_found`; the contact queue empty (11 leads at fit ≥ 50 retired to `needs_manual_contact`); 15 approved unsent first touches ≈ 0.75 days at the 20/day ceiling. Sites and SMOs (Workflow 1b) are the next supply; the 11 manual leads are the only CRO leads a human can still add | Abdullah | Sending beyond the backlog |
| **Start Docker Desktop with Windows.** `AutoStart` is `false`: after every reboot the whole stack (Send, Mailbox Watch, IMAP Health, Follow-Ups) stays down until someone starts it by hand (9 days once; 4.4 days from 2026-10-19 10:03 to 2026-10-23 18:39 UTC, through three boots on 10-19, 10-20 and 10-21 — nothing was lost: no INBOX mail arrived meanwhile). Turn on "Start Docker Desktop when you sign in". **Ollama does not start with Windows either** — it was down after the 2026-10-23 boot; Enrichment and Scoring fail on the first lead that needs them | Abdullah | Every scheduled workflow, opt-out handling included |
| **The ichgcp scraped address is taken as-is, including a `media@` or `marketing@` inbox.** The site source refuses non-business mailboxes; the scraped source has no such list, because adding one would change which contact 100+ processed leads hold. Two of 44 contacts are affected (`media@avancecro.com`, lead 1341; `marketing@clinixir.com`, lead 1387). Decide: apply `NEVER` from `code_site_emails.js` to scraped addresses and re-queue those leads, or reject those drafts by hand | Abdullah | Those two leads |
| **`nova-one-pager.docx` has no generator in this repo.** It was edited in place (sponsor terminology, five code-confirmed capabilities, website as the only knowledge source, demo step "You share your site URL"). A regeneration must carry the same changes, then pass `audit_drafts_vs_onepager.py`, **then rerun `n8n/drafting/build_onepager_sites.py`** (it stops if any phrase is not found exactly once) | Whoever regenerates it | One-pager consistency |
| **90-second Nova demo recording** — not made. Do not reference it in any draft until it exists | Fatima | — |
| **Review the exclusions and the flagged list.** Syria is excluded as "being unwound" (§12) and is the first to revisit; look at which retained-and-flagged countries the directory contains before their shard runs. A hand-imported lead from an excluded country would still score on its other factors | Abdullah | A sanctioned jurisdiction reaching `leads` |
| **A country the directory starts listing after 2026-10-06 has no clock yet.** The build checks only the committed snapshot. It fails safe (the template draft is held `no-send-clock`, nothing sent). Refresh the snapshot from a plan run's artifact, add the country to §12's table and to `code_decide.js`, rebuild, and re-queue the lead to `contact_found` | Abdullah | A new directory country reaching a draft |
| **Sync NocoDB's metadata** so the grid shows the new `drafts` columns (`approved_by`, `approved_at`, `hold_reason`, `claim_check`) and tables; filter the review grid to `status = pending` to make it the exceptions queue | Abdullah | Seeing hold reasons in the grid (the digest shows them meanwhile) |
| **Re-queue recipes** (after an Apollo plan upgrade or a manual `leads` edit): `DELETE FROM contact_attempts`; `DELETE FROM contacts WHERE apollo_id IS NULL AND email IS NULL AND linkedin_url IS NOT NULL`; delete an `ingest_log` row to force a re-ingest of that CSV version | Abdullah | Re-queueing |

---

*Document version 2. Update when any architectural decision changes. Do not let sessions drift from this spec.*
# Nova Scout — Expansion plan: 1,000 emails a week, and niches beyond CROs

**Written 2026-10-24. A plan only — nothing in it is built.** Each fact is marked **[verified]** (measured in this
repo, the live database or a primary source that day) or **[assumed]** (third-party figure, industry rule of thumb,
or my judgement). Decisions that are yours are collected in §8. Appointment booking is out of scope: Nova sends a
booking link, it does not book.

---

## 0. The short version

1. **Mailboxes are the third constraint, not the first.** In order: **lead supply**, then **address quality**, then
   sending capacity.
   - *Supply* **[verified]**: the whole ICH GCP directory gave 504 leads → 89 at fit ≥ 50 → 75 with an email →
     48 emailed in five weeks. Trialsites gave 111 → 6 at fit ≥ 50 → 2 with an email. At three emails per lead
     (first touch + two follow-ups), 1,000 a week needs **~330 new emailable leads every week**, about four times
     the CRO pipeline's lifetime output. CROs cannot get there. New niches are not optional for this target.
   - *Address quality* **[verified]**: 4 permanent bounces in 58 sends over the last 30 days (**6.9%**, leads 16,
     44, 1378, 1884), all to first-party "published" addresses. At 200 a day that trips the 3% brake inside a day.
     Verification before queueing is a prerequisite for volume, not a nice-to-have.
2. **Recommended setup**: 3 secondary domains × 2 mailboxes = **6 Zoho mailboxes at ~35/day each ≈ 200/day**, the
   primary `amitrixlabs.com` kept for replies, operator mail and a low cold volume. ~$110/year **[assumed prices]**.
   Read §2.1's policy risk first: Zoho's usage policy bars "Promotional", "Bulk or Mass" and "Marketing" email
   **[verified]**.
3. **Niches**: a `niches` table plus per-niche template files, so a niche is a row and a folder rather than a fork
   of six workflows. First new niche: **dental / medical clinics from OpenStreetMap country extracts** (free,
   licence-clean when processed locally).
4. **Build order** (§7): verification → niche config → first clinic niche → multi-mailbox → ramp. ~14–19 Claude
   Code sessions to 1,000/week, with the calendar set by domain warm-up (~5–6 weeks), not by the code.

---

## 1. What 1,000 a week requires

| Stage | CRO pipeline today [verified] | Needed per week for 1,000 emails [assumed ratios] |
|---|---|---|
| Raw leads in | 504 (ICH GCP, whole directory) + 111 (Trialsites) | ~2,200 |
| Fit ≥ 50, not disqualified | 89 + 6 | ~400 |
| With an email | 75 + 2 | ~360 |
| After verification (−10%) | — | ~330 |
| Emails (×3 touches) | 61 sent to date | ~1,000 |

The ratios in the right-hand column are the CRO pipeline's own (≈15% of raw leads became emailable) — a new niche
will differ, which is why the first niche run (§7 step 3) is also the measurement that decides whether 1,000/week is
realistic. GPU load at ~2,200 enrichments a week is ~7 hours of `qwen3.5:9b` (measured: a batch of 10 in ~2 min)
**[verified rate, assumed volume]** — fine, but only while the host and Ollama are up (§13 of the Master Ref: neither
starts with Windows).

Throughput from one mailbox **[verified, simulated 2026-10-23 with the shipped Decide Send on the live queue]**:
pacing (20-minute floor, 40% coin flip per 10-minute tick) caps a day near 35–45 even with recipients spread across
every time zone; the 50/day ceiling is never the binding limit. More volume therefore means more mailboxes, each
with its own pacing — not a higher cap on one.

---

## 2. Sending from several mailboxes on secondary domains

### 2.1 How many, and the lowest-cost setup

**Recommendation: 6 mailboxes on 3 new domains (2 per domain), ~35 cold emails a day each at full warm-up.**
Common practice puts a warmed cold-email mailbox at 25–50 a day and caps each domain's share so one burned domain
costs a third of capacity, not all of it **[assumed — vendor blogs disagree: 25–30, 30–50, one outlier at 50–100]**.
Keep `amitrixlabs.com` (warm, 50/day today) for replies, the operator's mail and ≤ 20 cold a day, so the brand domain
carries the least risk.

| Option | Per mailbox | 6 mailboxes + 3 domains, per year | Notes |
|---|---|---|---|
| **Zoho Mail Lite (recommended)** | ~$1/user/month, annual **[assumed — Capterra and resellers; Zoho's own pricing page did not show the figure]** | ~$72 + ~$36 = **~$110** | Same provider, credentials, IMAP/SMTP and code paths as today. Confirm Mail Lite includes IMAP/SMTP before buying (Zoho's page lists IMAP/POP as excluded only on Mail Free) |
| Google Workspace / Microsoft 365 | several $/user/month **[not verified this session]** | several hundred $ | Better inbox reputation is often claimed **[assumed]**; new credentials, new IMAP quirks; same anti-spam terms |
| Resold "cold email infrastructure" mailboxes | a few $/mailbox **[not verified]** | — | Not recommended: shared infrastructure and warm-up networks you do not control, against §2's spirit |

**Provider policy risk — read before deciding [verified, Zoho's own pages, 2026-10-24]:**
- Zoho's usage policy: Zoho Mail "**cannot** be used for sending out emails in the following categories:
  Promotional emails, Bulk or Mass emails, Marketing emails", and "Do not send unsolicited spam emails".
- Zoho's rates page: external sending is "50–500 emails/hour", a "dynamic limit based on sender reputation,
  recipient engagement, and contact quality"; bulk or burst sending "Not permitted under business usage policy".
- What Nova Scout sends is one-to-one, plain text, to a company's published business address, in that company's
  business hours — closer to business correspondence than to a campaign — but it is unsolicited and templated. At
  200 a day the account is more visible. Google's and Microsoft's terms carry the same class of prohibition
  **[assumed, not re-read this session]**. Mitigations already in the pipeline: pacing, the brake, instant
  opt-out handling. The risk is a suspended mailbox, which is why the domains are separate from the brand.
  **This is your call (§8, D1).**

### 2.2 What changes in the code

n8n's Send Email and IMAP Trigger nodes take **one fixed credential each** **[verified: the node schema has a
credential slot, not an expression]**. Everything per-mailbox follows from that.

| Area | Today | Change | Effort |
|---|---|---|---|
| **Mailbox registry** | one mailbox in `.env` | migration: `mailboxes` (address, domain, display name, role `cold`/`primary`, `warmup_started_on`, `active`, `max_per_day` override); `outreach_log.mailbox`, `mailbox_sent.mailbox`, `drafts.mailbox` | small |
| **Sending** | Send Email node, one credential | route every cold send through the existing `smtp-send` sidecar (`smtp_send_server.py`, already used by Send Reply), extended from one mailbox to an allow-list read from its own env; `POST /send` takes the mailbox. The sidecar can also set `In-Reply-To`/`References`, which the Send Email node cannot — follow-ups would thread properly instead of only by `Re:` | medium |
| **Warm-up and caps** | §5 table derived from one Sent folder | the same derivation per mailbox: first external send per mailbox, week per mailbox, `sent_today` per mailbox; the §5 table becomes the per-mailbox ramp (§3.3) | small–medium |
| **Pacing** | 20-min floor, 40% coin per tick, one send per tick | floor and coin **per mailbox**; a tick may send one message per mailbox (6 per tick at most), never two from one mailbox | small |
| **Rotation** | — | **sticky**: a lead is assigned a mailbox at its first touch (the eligible mailbox with the most room today, ties random) and keeps it for follow-ups and replies; never two mailboxes to one company | small |
| **Brake** | global: >3% permanent bounces / 3 days, any complaint | `send_pauses` gains `scope` (`global` / `domain` / `mailbox`): bounce rate per mailbox (one bad list stops one mailbox), a complaint pauses its **domain** (reputation is per domain), and the global brake stays as the backstop | medium |
| **Replies and bounces** | Mailbox Watch: 2 IMAP triggers on one mailbox | one Mailbox Watch **per mailbox, generated** by `build_workflow.py` from the registry (same code, different credential); `inbound_messages.mailbox`; matching by thread works unchanged because Message-IDs are unique across mailboxes; blocklist stays global | medium |
| **IMAP connections** | 2 | 14 with 7 mailboxes. If the IMAP trigger's known fragility (sockets dropped on sleep/DNS blips, §9 WF6) multiplies, replace the triggers with a polling sidecar that writes to Postgres — larger change, decide after a month of data | — / large |
| **IMAP Health** | one mailbox | `imap_preflight.py` loops over the registry; one `mailbox_health` row per mailbox; alerts name the mailbox | small |
| **Sent mirror** | one Sent folder; `sent-mirror-behind` refuses all sends | per mailbox; a stale mirror stops only its own mailbox | small |
| **Signature / identity** | Send checks `Abdullah Amir\nFounder, Amitrix Labs` | unchanged if every mailbox is Abdullah on a brand-consistent domain (`abdullah@<secondary>`); the template already says "Amitrix Labs". Personas are a decision (§8, D3) | none–small |
| **Operator mail, digest, reply review** | from the one mailbox | from the primary only; no warm-up slot, as now | none |
| **Reply Assistant** | Send Reply → sidecar, one mailbox | answers **from the mailbox that received the reply** (sidecar takes the mailbox) | small |
| **Tests / dry runs** | per-mailbox none | the dry runs gain a two-mailbox scenario: rotation, per-mailbox caps, a domain brake | medium |

**Total: ~4–6 sessions.**

---

## 3. Deliverability per secondary domain

### 3.1 DNS — copy the primary's working records, and fix two things on all of them

Live today on `amitrixlabs.com` **[verified, DNS read 2026-10-24]**:

```
TXT  amitrixlabs.com          v=spf1 include:zohomail.com ~all
TXT  zmail._domainkey         v=DKIM1; k=rsa; p=MIGfMA0...   (a 1024-bit key)
TXT  _dmarc                   v=DMARC1; p=none;              (no rua -- nobody receives reports)
MX   10 mx.zoho.com, 20 mx2.zoho.com, 30 mx3.zoho.com
```

For each secondary domain:
- **MX** to Zoho (replies and bounces must reach the mailbox — a send-only domain loses opt-outs).
- **SPF** `v=spf1 include:zohomail.com ~all` (one `include`, nothing else sends for these domains).
- **DKIM** generated in Zoho's admin console, **2048-bit** if offered **[assumed: offered]**; the primary's 1024-bit
  key should be rotated too.
- **DMARC** `v=DMARC1; p=none; rua=mailto:<a reports address on the primary>; adkim=r; aspf=r` during warm-up, then
  `p=quarantine` once a month of reports shows only Zoho sending. Add the `rua` to the primary as well.
- **A web presence**: redirect each secondary domain's root to `amitrixlabs.com`, so a prospect who checks it lands
  on the real site.
- Gmail/Yahoo/Microsoft's "bulk sender" rules start above **5,000 a day to that provider** **[assumed — vendor
  summaries agree; check the providers' own pages]**; at 200 a day in total Nova Scout is far below, but the
  baseline (SPF or DKIM aligned, valid DNS) applies to everyone and the 0.3% spam-complaint ceiling is the number
  to stay well under. The brake's "any complaint pauses" is already stricter.

### 3.2 Names

Brand-consistent, not look-alike tricks: `amitrixlabs.co`, `getamitrix.com`, `amitrix-labs.com` — **[assumed,
availability not checked]**. `.com` where possible. Buy all three on day 0: domain age is part of the warm-up.

### 3.3 Warm-up per new mailbox

Enforced by the same machinery as §5, per mailbox (§2.2). Proposed ramp **[assumed — common practice is
5–10/day at start, 3–4+ weeks for a brand-new domain]**:

| Week of that mailbox | Max/day | What to send |
|---|---|---|
| 0 (domain bought) | 0 | DNS, Zoho, signature; a few real messages to people who will answer (the operator, colleagues) |
| 1 | 5 | the highest-fit, verified leads only |
| 2 | 10 | |
| 3 | 15 | |
| 4 | 25 | |
| 5+ | 35 (ceiling) | |

No warm-up networks (automated fake conversations): they are opaque and against the providers' terms
**[assumed]**. Calendar: 6 mailboxes at full speed in week 5–6; the primary keeps sending meanwhile, so volume
rises from ~45/day now to ~200/day over that period.

---

## 4. Niches beyond CROs

### 4.1 What is CRO-specific today [verified, read from the repo]

| Part | Where | CRO-specific how |
|---|---|---|
| Lead source | `scraper/` (ICH GCP), `n8n/sites/` (Trialsites) | both are clinical-research directories |
| Enrichment schema | `n8n/enrichment/` | `company_type` enum CRO/site/SMO/other/unclear; therapeutic areas (locked taxonomy); phases |
| Disqualifiers | `n8n/scoring/code_evaluate.js`, §9 WF3 | "not a CRO, site or SMO"; employees > 500; chat widget; no website; blocklist |
| Scoring weights | §9 WF3 table | active trials (ClinicalTrials.gov / Trialsites) 20, oncology 15, geography tiers, founder LinkedIn |
| Contact rules | `n8n/contacts/` | mailbox preferences (business development first), `NEVER` list — mostly generic already |
| First-touch template + SMYKM | `n8n/drafting/code_template.js`, §9 WF4 (LOCKED) | "sponsor inquiries", "AI agents for CRO websites", "win more studies"; SMYKM facts trials/areas/city/company |
| Follow-up templates | §9 WF6 (LOCKED) | "every sponsor who visits", "a potential study" |
| Approval rules | migration `020` trigger; Drafting's batch query | **auto-approval requires `lead_company_type() = 'CRO'`**; sites/SMOs wait at `contact_found` |
| Reply Assistant | `claims_library` (`company_types`), proof lines | NoblePath / Vertex proof, CRO vs site claims |
| One-pagers | `nova-one-pager*.docx`, `code_notify.js` | per company type |
| Send clocks | §12 | per country — **already niche-agnostic** |
| Brake, blocklist, warm-up, opt-out | Workflow 6 | **niche-agnostic** |

### 4.2 A config-driven design: a niche is a row plus a folder

```
niches                          -- migration; the one place a niche is defined
  code (PK: cro, site, dental, physio, ...), active, label
  definition text               -- plain language, for the operator and for the query/question generator (§5 #2)
  sources jsonb                 -- which ingesters feed it, with their parameters
  company_types text[]          -- enrichment classes that count as this niche
  disqualifiers jsonb           -- e.g. employees_max, chain/franchise, hospital
  questions jsonb               -- yes/no scoring questions with weights (§5 #3)
  template_dir text             -- n8n/drafting/niches/<code>/ : first_touch.txt, follow_up_1.txt, follow_up_2.txt, smykm.json
  one_pager_url text
leads.niche  FK -> niches.code  -- replaces lead_company_type() as "what is this lead"
```

- **Templates stay LOCKED and approved by you**, but per niche: the build parses each niche's files the way it parses
  §9 today, and refuses to build a niche whose template has no approval stamp (a line in the Master Ref per niche).
- **Auto-approval** (migration `020`) changes from "lead is a CRO" to "lead's niche is active and has approved
  templates". A niche without approved templates drafts nothing — the same safety as today's site/SMO hold.
- **Scoring** keeps the six generic factors where they make sense (geography, size, site quality, founder) and
  replaces the CRO-only ones (trials, oncology) with the niche's own yes/no questions.
- **Everything in Workflow 6 stays as it is.** It is already niche-agnostic.

Migration path: seed `cro` and `site` rows that reproduce today's behaviour byte for byte (the dry runs prove it),
then add niches. ~3–4 sessions.

### 4.3 What a new niche needs (checklist) — and where clinics come from

1. A definition and a source with **websites** (the pipeline needs a site to enrich and an address on it).
2. Enrichment classes and disqualifiers (e.g. hospital, chain/franchise, > N locations).
3. Yes/no questions + weights (§5 #3).
4. Approved first touch + two follow-ups + SMYKM facts that a website can prove (e.g. "{n} locations", services
   offered, languages).
5. Its own chat-widget vendors (§5 #1 — clinics use different ones).
6. A one-pager.

**Sources for clinics, doctors, practices** (businesses with many daily clients):

| Source | Cost | Notes |
|---|---|---|
| **OpenStreetMap country extracts** (Geofabrik `.osm.pbf`, filtered locally for `amenity=dentist/clinic/doctors`, `healthcare=*` with a `website`) | **$0** | Recommended first. The public Overpass API's policy says commercial use should self-host or pay, and it is meant for < 10,000 queries/day **[verified, OSM wiki]** — so download extracts and filter offline (a Docker `osmium` step, the same "file into Postgres" shape as Ingestion). Coverage of `website` tags varies a lot by country **[assumed — measure it in step 3]**. ODbL attribution applies |
| Google Places API (New) | free caps then ~$32–35 per 1,000 for Text Search with a website field **[assumed — third-party summaries of Google's 2025 per-SKU caps: 10k Essentials / 5k Pro / 1k Enterprise free a month]** | Best coverage; the website field puts a request in the Enterprise SKU, so the free tier is ~1,000 places a month |
| Explee (§5 #5) | see §5 | |
| National registries / association directories | $0 | Per country, scraper per site — like ICH GCP; good for one high-value country |

---

## 5. Ideas evaluated

| # | Idea | Value | Effort | Verdict |
|---|---|---|---|---|
| 1 | **Chat-widget detection as a lead filter** | high | small | **Already built** — regex in Enrichment, hard disqualifier in Scoring **[verified]**: 28 of 617 leads flagged, 17 disqualified. **Finding:** the "HubSpot Chat" pattern matches `js.hs-scripts.com` and `hs-banner` — HubSpot's general tracking loader and cookie banner — and accounts for 24 of the 28. Of six flagged sites checked on 2026-10-24, one (Evotec) has chat enabled in its HubSpot loader, three have loaders with no chat, two no longer load HubSpot **[verified, small sample]**. Fix: match the chat loader (`usemessages.com` / `conversations-embed`) and, for `hs-scripts` alone, fetch the portal loader and check it; then re-score the 17. For clinics, add their vendors (Podium, Birdeye, Weave, NexHealth, WhatsApp/Messenger widgets, Doctolib/Zocdoc booking widgets as a separate signal) **[assumed list]** |
| 2 | **Plain-language niche → search queries + scoring questions via local Ollama** | medium | medium | Do it as a **build-time assistant, not a runtime step**: `qwen3.5:9b` drafts the niche row's source filters and yes/no questions from the definition; you approve the row; the pipeline runs on the approved row (rule 3: no tokens on deterministic work; and an LLM-written filter needs a human read once, not on every run) |
| 3 | **Custom yes/no scoring questions per niche** | high | medium | Yes. Add them to Enrichment's Ollama call as a per-niche boolean block in the same `format` schema (grammar-enforced, `presence_penalty: 0`, §3); Scoring sums the weights. Each answer carries a short quote from the page, matched back to the page text by code, so an unsupported "yes" is dropped (the "model proposes, code decides" rule of Workflow 1b) |
| 4 | **Email verification before queuing** | **very high** (6.9% bounce rate today [verified]) | small | Two layers. **Free**: DNS check (MX or A record resolves) at contact time — would not have caught the four bounces (all four domains have mail servers; three were "recipient rejected") **[assumed from the DSN texts]**, but cheap insurance for dead domains. **Paid**: a verification API on each new address before it is marked `verified` — around $0.004 per address at MillionVerifier's 10k tier **[assumed — aggregator figure]**, i.e. ~$1.30/week at 330 addresses, ~$70/year. Treat "catch-all" as a separate state (allowed, but at most a few per day). Do **not** probe SMTP from this host: a residential IP's port 25 is usually blocked and probing harms reputation **[assumed]** |
| 5 | **Explee as a lead source (500 free credits)** | medium (unknown quality) | small to try | **The "500 free credits" is unverified**: Explee's own pricing page shows "$30 to try" for its Outreach Agent and a Database priced "from $5 per 1k" (research) and "from $10 per 1k" (emails), with a "Global Maps" dataset of 210M+ places **[verified, explee.com/pricing]**; the 500 credits appear only on third-party listings. Worth a one-session trial: export ~200 clinics for one country, run them through Enrichment + verification, and compare cost per **verified emailable lead** with OSM's. Their "Outreach Agent ... books demos" is irrelevant here |

---

## 6. Cost, near zero

| Item | Per year | Status |
|---|---|---|
| 3 secondary domains | ~$36 | [assumed .com price] |
| 6 Zoho Mail Lite mailboxes | ~$72 | [assumed price; confirm IMAP/SMTP included] |
| Email verification, ~17k addresses/year | ~$70 | [assumed rate] |
| OSM extracts, Ollama, n8n, Postgres | $0 | [verified stack] |
| Reply Assistant (Sonnet 5.5) at ~10–30 replies/week | small; **the account is empty today** (Master Ref §13) | [verified balance problem] |
| **Total new spend** | **~$180/year** | vs today's ~$22/year + Trialsites lookups |

Google Places, Explee's paid tiers and Google Workspace are the options that would move this by hundreds of dollars a
year; none is needed for the plan to work.

---

## 7. Recommended build order

| Step | What | Sessions | Unblocks |
|---|---|---|---|
| 0 | **Now, no code:** buy the 3 domains, set up DNS (§3.1), create 6 Zoho mailboxes, top up the Anthropic balance, turn on Docker + Ollama autostart. Fix DMARC `rua` and the 1024-bit DKIM on the primary | 0 | the warm-up clock |
| 1 | **Verification** before an address is marked verified (§5 #4); re-check the 75 existing CRO addresses | 1–2 | volume without tripping the brake |
| 2 | **HubSpot false positive** (§5 #1) and re-score the 17 | 1 | a few more CRO leads now |
| 3 | **Niche config** (§4.2): `niches` table, per-niche templates, auto-approval by niche; `cro` and `site` rows reproduce today exactly | 3–4 | any new niche |
| 4 | **First clinic niche**: OSM ingester for 1–2 countries, clinic chat vendors, yes/no questions, your approved templates; run it and **measure** raw → emailable | 3–4 | the supply number that says whether 1,000/week is real |
| 5 | **Multi-mailbox sending** (§2.2): registry, sidecar sending, per-mailbox warm-up/pacing/brake, sticky rotation, generated Mailbox Watch per mailbox, IMAP Health per mailbox | 4–6 | 200/day |
| 6 | **Ramp**: watch per-mailbox bounce/complaint numbers weekly; add a second niche or country when supply runs low | 1 per niche | 1,000/week |

**~14–19 sessions in total**. Steps 1–4 can run while the domains warm (step 0 starts the calendar), so the
code is not the long pole: the earliest realistic 1,000/week is **~6 weeks** after the domains are bought, if step 4
shows enough supply.

---

## 8. Decisions needed from you

| # | Decision | My recommendation |
|---|---|---|
| D1 | Accept Zoho's policy risk at ~200/day, or pay more for another provider | Zoho, on separate domains, with today's pacing and brake; revisit if a mailbox is restricted |
| D2 | How many mailboxes / domains | 6 on 3 |
| D3 | One persona (Abdullah on every domain) or several | One — the templates are written in his voice |
| D4 | Does the primary keep sending cold, and how much | ≤ 20/day once the secondaries are warm |
| D5 | Pay for verification (~$70/year) | Yes — the 6.9% bounce rate is the strongest number in this plan |
| D6 | First new niche and countries | Dental clinics, 1–2 countries with good OSM website coverage (measure first) |
| D7 | Templates for the new niche | You write/approve them, as with the CRO template; I can draft candidates |
| D8 | Trial Explee | One session, ~200 records, compared on cost per verified emailable lead |
| D9 | Fix the HubSpot over-detection now (re-scores up to 17 CRO leads) | Yes — small, and it adds CRO supply immediately |

---

## Sources

- Zoho Mail usage policy — https://www.zoho.com/mail/help/usage-policy.html
- Zoho Mail rates and limits — https://www.zoho.com/mail/help/adminconsole/rates-and-limits.html
- Zoho Mail pricing page — https://www.zoho.com/mail/zohomail-pricing.html (price not shown in its text)
- Zoho Mail pricing (third-party) — https://www.capterra.com/p/174694/Zoho-Mail/pricing/
- Explee pricing — https://explee.com/pricing ; third-party listings of the 500 credits — https://softwarefinder.com/sales-tools/explee , https://neura.market/directories/ai-tools/explee
- Email verification prices (aggregators) — https://leadmagic.io/comparisons/zerobounce-vs-millionverifier , https://www.g2.com/compare/millionverifier-vs-zerobounce
- Cold-email mailbox limits and warm-up (vendor blogs) — https://www.smartlead.ai/blog/how-many-cold-emails-per-day , https://leadhaste.com/blog/cold-email-sending-limits-2026 , https://emailbison.com/blogs/cold-email-warmup , https://www.namesilo.com/blog/en/email/how-to-warm-up-a-new-domain-for-cold-email-outreach
- Sender requirements (summaries) — https://easydmarc.com/blog/google-yahoo-microsoft-icloud/ , https://www.mailreach.co/blog/gmail-sender-guidelines
- Google Places pricing (summaries) — https://openplacesapi.com/blog/google-places-api-pricing , https://developers.google.com/maps/billing-and-pricing/pricing
- Overpass API usage policy — https://wiki.openstreetmap.org/wiki/Overpass_API , https://dev.overpass-api.de/overpass-doc/en/preface/commons.html

# The research-site niche — independent clinical research sites and SMOs (2026-10-28)

Built on the operator's brief of 2026-10-28. This file is the niche's reference: what was built, what was decided
and why. **[V]** = verified (measured, read from the database or a primary source), **[A]** = assumed or projected.
The Master Ref was not edited (the brief allows it only where a build or drift guard needs it, and none does); the
lines in it that this niche makes out of date are listed in §8.

## 1. A niche is a row (migration `025_niches.sql`)

| Object | What it is |
|---|---|
| `niches` | One row per niche: `entity_types` (which `lead_company_type()` values belong to it), `sources` (NULL = any) and `excluded_sources`, `min_fit_score`, `scoring` (weights and exclusions, documentation the drift guard checks), `templates` (the exact approved wording — `node n8n/drafting/niche_templates.js` prints it from the shipped code), `sender_name` / `sender_title` (the identity its emails sign with), `active`, `auto_approve`, `approval`. |
| `cro` row | Today's behaviour: entity `CRO`, any source but the two site sources, signs "Founder, Amitrix Labs", active, auto-approving. |
| `research_site` row | Entities `site` and `SMO`; sources `sites-ctgov`, `sites-overture`, `ichgcp`; **not `trialsites`** (§6); floor 50; signs "Software Engineer, Amitrix Labs"; active, auto-approving. |
| `lead_niche(raw_extraction, source)` | The one definition of a lead's niche (NULL: nothing drafts it). |
| `lead_company_type()` | A lead from a site source now falls back to `site`, never to `CRO`. ICH GCP and Trialsites unchanged. |
| `site_lead_sources` | Where a site lead came from: the ClinicalTrials.gov facility name and recruiting count at harvest, the Overture place and its licences, what the verification fetch saw (contact form, mailto, robots.txt). |
| `drafts_approval_rules()` | Migration 020's "its lead is a CRO" became "its lead's niche is active, auto-approves, and the text signs with that niche's identity". Every other hold is unchanged: the switch, email only, a template variant with no tag, unedited text, a verified deliverable address, no blocklisted domain. |

To pause the niche: `UPDATE niches SET auto_approve = false WHERE code = 'research_site';` (every site email then
waits for a person, held `auto-approve-off`) or `active = false` (nothing drafts it; the trigger refuses its
auto-approvals). To hold one source only: `niches.held_sources` (migration `026`) — its leads are still drafted and
every draft is held `db-guard: source … is held`; `sites-overture` is held since 2026-10-30 (§7.5).

## 2. Sources

See `n8n/sites/open_sources/README.md` for the pipeline. Licences and terms:

| Source | Terms | How it is respected |
|---|---|---|
| ClinicalTrials.gov API v2 | U.S. NLM public data; robots.txt `Disallow: /api/` for crawlers, `Crawl-delay: 1` | One documented-API request in flight, ≥ 6 s apart, ~15 requests per harvest — the same API Scoring and Drafting call, also one request every 6 s since 2026-10-30 (§7.6). **[A]** that robots.txt's `/api/` line is aimed at crawlers, not at the documented API (§7, decision D2). Location contacts are never requested. |
| Overture Maps places | CDLA Permissive 2.0 (most providers), Apache 2.0 (Foursquare, with NOTICE), CC0 (AllThePlaces) | Commercial use permitted by all three; licences stored per lead; attribution in the README. Its email/phone columns are never used. |
| Each site's own website | robots.txt | `verify.js` reads robots.txt before every request and never writes a lead whose homepage it disallows. |

## 3. Classification and scoring (`n8n/scoring/code_evaluate.js`)

A lead takes the site path when it comes from a site source, or when enrichment calls an ICH GCP lead a site or an
SMO. Trialsites leads stay on the CRO path, unchanged, and in no niche.

**Strict classification, first rule wins; only a positive answer is a site.** Excluded (disqualified, with the
reason): enrichment says CRO, or the name says CRO / contract research; a sponsor by name (pharma, biotech,
therapeutics, devices …); a hospital, a university or academic body, a government or public body — by name, in the
included countries' languages (the Trialsites selector's word lists, kept equal by a drift guard), or by an
institutional domain (`.edu`, `.gov`, `.ac.xx`, `.nhs.uk` …); and anything enrichment did not positively call a
site or an SMO (`unclear`, no answer). An SMO is enrichment's SMO, or a site whose name says SMO / site management /
research network. The shared disqualifiers still apply first: a chat widget (the corrected 2026-10-24 detection),
more than 500 employees, no functioning website, blocklisted.

| Factor | Weight | Bands |
|---|---:|---|
| Recruiting trials at this facility (ClinicalTrials.gov, strict facility match at harvest) | 25 | ≥ 10: 25 · ≥ 5: 20 · ≥ 2: 15 · 1: 10 · none: 0 |
| Site type | 15 | dedicated research site or SMO 15 · a practice that runs trials 8 |
| Therapeutic areas stated (locked taxonomy, not `Other`) | 10 | any 10 · none 0 |
| Inquiry path (from the verification fetch) | 10 | contact form only 10 · form + published email 6 · no form 3 · not measured 5 |
| Geography | 10 | core 10 · extended 5 (as for CROs) |
| Employees 5–100 | 10 | in band 10 · unknown 5 · out 0 |
| Site quality | 10 | the CRO factor, unchanged |
| Director / founder identified | 10 | the CRO founder factor, rescaled |

**Threshold: 50** — the same number as Contact Lookup's general gate (Master Ref §9 Workflow 3, parsed by the
contacts build), so a site that is looked up is a site that can be drafted; `niches.research_site.min_fit_score`
and a drift guard hold the two equal.

## 4. Templates (`n8n/drafting/code_site_template.js`, `code_site_followup.js`)

Fixed text filled by code; A/B by `lead_id` parity for the SMYKM sentence and the close; the variant records arm,
fact and addressing exactly as for CROs. SMYKM order: recruiting trials at this site (strict facility-level match,
looked up at draft time: a study counts only when one of its locations is RECRUITING, names this facility after a
sponsor's site suffix is stripped, and is in the lead's city — an SMO: its country), therapeutic areas, city, the
company. The SMO variant speaks of its network and its sites. Grammar rules beyond the CRO lane's: "that area" →
"those areas" with two areas; Spanish/Portuguese/Italian/Czech/Polish titles ("Dra.", "Lic.", "Mgr." …) are
stripped before the greeting.

## 5. Workflows

| Workflow | id | |
|---|---|---|
| Drafting - Research Sites | `draftingSites01` | derived from `drafting.json` by `build_sites_workflows.py`: only the niche filter, the location lookup and the words differ |
| Follow-Ups - Research Sites | `followupSites01` | derived from `follow-ups.json` the same way |
| Scoring | `scoring0001` | the site path above; every CRO lead scores exactly as before (615 of 617 leads identical, the two that differ are the ICH GCP site and SMO) |
| Send | `send0001` | checks a body for its lead's niche's identity; every other lead exactly as before |
| Daily Digest | `digest0001` | niche and arm on every line; a per-niche, per-arm block for the window and for every template email so far |
| Mailbox Watch | `mailwatch0001` | CONTACT's acknowledgement says whether a niche drafts the lead (was: "the CRO template never drafts" a site) |

## 6. Trialsites

Its API page says "Free for non-commercial use" (Master Ref §13). The brief says to respect each site's terms, so
Trialsites leads are in **no niche** (`niches.research_site.excluded_sources`) and are never drafted until the
operator settles the licence — then `UPDATE niches SET sources = sources || 'trialsites', excluded_sources = '{}'
WHERE code = 'research_site';`. The 102 Trialsites leads disqualified "no functioning website" on 10-11/18/19 were
a DNS outage (`ENOTFOUND` on every rung), not dead sites — their domains resolve today; re-enrich them if the
licence is settled. The Anthropic lookup was not rewired to Ollama: a local model cannot search the web; the free
replacement is "Overture proposes, the shipped verifier decides", measured in §7.

## 7. Measurements and decisions

Measured on the live database at 2026-10-29 18:21 PKT, after the harvest of 2026-10-28 and an overnight drain that
a Windows Update restart cut at 07:07 PKT and that was resumed at 15:12 PKT. The decisions were not numbered by the
session that made them; they are numbered here, and D2 is the one §2 cites.

### 7.1 The funnel **[V]**

| Step | Count |
|---|---:|
| ClinicalTrials.gov: RECRUITING, industry-funded studies (15 API requests) | 14,100 |
| … their locations kept after the screens (institution name 31,436, sponsor placeholder 39,052, not recruiting here 5,853, country 1,132, sponsor or CRO name 416, occupied territory 1 excluded) | 33,765 |
| Overture places kept (no research word and no ClinicalTrials.gov match 49,305, no name 4,310, website excluded / not matching 4,670, institution 2,551, other 2,603 excluded) | 57,464 |
| Candidates, one per domain | 6,544 (sites-ctgov 942, sites-overture 5,602) |
| Shipped Verify Website: the site's own pages name the candidate and its city | 2,919 resolved · 3,534 not confirmed · 91 robots.txt disallows the homepage (never a lead) |
| New leads | 2,869 (sites-ctgov 681, sites-overture 2,188); the other 50 resolved domains were already leads (28 Trialsites, 22 ICH GCP) and kept their row — `leads.domain` is UNIQUE |

| Source | ingested | enriched | scored | disqualified | drafted | drafted / processed |
|---|---:|---:|---:|---:|---:|---:|
| sites-ctgov | 0 | 0 | 269 | 66 | 346 | 51 % |
| sites-overture | 467 | 20 | 269 | 1,279 | 153 | 9 % |

The 487 Overture leads not yet through enrichment are worked by the scheduled Enrichment (every 30 minutes, 10
leads), about a day of host uptime **[A]**; the queue is idempotent, so a faster manual drain can be resumed at any
time. `scored` holds the leads under the floor and those Contact Lookup found no deliverable address for. Fit scores of
the site leads (latest score): disqualified 1,345 · < 50: 121 · 50–59: 344 · 60–69: 347 · 70+: 225. Top
disqualifications: "not a CRO, site or SMO" (enrichment read the site as something else) 1,102; enrichment called it
a CRO 154; enrichment did not classify it 38; a university / cancer or medical centre / hospital name 14; no
functioning website 13.

Drafts: 499 — 485 auto-approved, 14 held `no-address`; 479 site and 20 SMO; arm A 250, arm B 249. Every one of the
485 goes to a verified address on the site's own domain (485/485). None had been sent by 18:21 PKT (§7.3 D5, §7.4). Approved by
country: USA 357, India 38, Canada 18, Germany 12, Australia 11, Mexico 10, Poland 8, United Kingdom 7, South
Africa 6, Brazil 3.

The CRO lane is unchanged: 615 of 617 leads score identically (the two that differ are the ICH GCP site and SMO,
§5), and on a scratch copy all 84 sent CRO template emails re-render byte-identical (dry run, 30 checks passed).

### 7.2 Overture as the free replacement for the Trialsites website lookup **[V]**

Of the 111 Trialsites candidates the Anthropic lookup resolved (each confirmed by the shipped verifier), the free
Overture matcher — same country, same name key, same city — proposes the same website for **14 (13 %)**, a different
one for 6 and nothing for 91; of the 320 candidates whose lookup call failed it proposes something for 15. It is not a
replacement for the lookup on Trialsites' records; it is a source in its own right (§7.1).

### 7.3 Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Trialsites leads are in no niche until its "free for non-commercial use" licence is settled (§6). | Operator to decide |
| D2 | ClinicalTrials.gov's robots.txt `Disallow: /api/` is read as aimed at crawlers, not at the documented API v2, which is harvested at one request in flight, ≥ 6 s apart, ~15 requests (§2, §7.6). **[A]** If the operator reads it otherwise, the harvest stops; CTTI's AACT database (a public copy of ClinicalTrials.gov) is the bulk alternative. | **[A]** — operator to confirm |
| D3 | The Anthropic website lookup was not rewired to Ollama: a local model cannot search the web, and the free matcher finds the same site for 14 of 111 (§7.2). | Made |
| D4 | Fit floor 50, equal to Contact Lookup's gate (§3). | Made |
| D5 | **Send interleaves the lanes 1:1 (operator, 2026-10-30).** Until then it took the eligible approved drafts in `ORDER BY d.id`, so an older CRO draft at work went before any site draft. Now the CRO lane and the research-site lane take turns: the oldest draft at work in the lane the last cold send was not from, else the oldest at work (Master Ref §9 Workflow 6, "Queue order"). Every other guard and the 10 % brake are unchanged. | Made, live in `send0001` since 2026-10-29 20:04 UTC |
| D6 | **The Overture harvest is stopped and its drafts are held (operator, 2026-10-30)** until it measures at least 90 % precision (§7.5). `niches.research_site.held_sources = {sites-overture}` (migration `026`): its leads are still enriched, scored, looked up and drafted, and every draft waits in the exceptions queue; `ingest.py` writes no row from a held source. | Hold on; lifting it is the operator's call (§7.5) |

### 7.4 Fourteen-day send forecast **[A]**

`python n8n/sendtrack/forecast_sends.py --days 14 --runs 100` at 2026-10-29 13:27Z: 530 approved now (CRO 45,
research site 485), 77 follow-ups due in the window, no new site drafts assumed, host up 05:40–22:40 UTC. Median per
day: 16 today (CRO 12, sites 4); 29–30 a day after that (p10–p90 about 25–34); site emails take most of the days
from 2026-10-31 until the CRO follow-ups come due around 2026-11-07. **14-day median total: 409** (about 124 CRO,
315 site). A projection, not a measurement: it assumes the host stays up through the window, and it ignores replies,
bounces and opt-outs. The 2026-10-29 restart took the host down 07:10–14:46 PKT. Run before D5 and D6 changed
the queue (the interleave, and 154 Overture drafts held): re-run it for today's picture.

### 7.5 Precision audit of the two sources (2026-10-30) **[V]**

The question asked: does the lead's domain really belong to that facility? A seeded random sample (`md5(draft id ||
'precision-audit-2026-10-30')`) of 40 approved ClinicalTrials.gov drafts and 40 Overture drafts (the 154 approved
when the brief arrived, held since). Each site was fetched again — robots.txt first, `NovaScoutBot/1.0`, the homepage
and up to three about / contact / location pages — and read and judged by Claude (the 2026-10-30 session), not by the
verifier that produced the lead; no person has checked the 80 verdicts. **Identity = yes** when the domain is the facility's own site, or that of the research organisation
that runs it (a site network); **no** when it is a parent health system or practice group whose site is not about the
research facility. A second question was recorded alongside, because it turned out to matter more: **fit** — is it an
independent research site or SMO at all? Per-site verdicts: `docs/precision_audit_2026-10-30.csv`.

| Source | Identity precision (95 % Wilson CI) | Fit: research site or SMO | Partial | Not a research site |
|---|---:|---:|---:|---:|
| sites-ctgov | **39/40 = 97.5 %** (87.1–99.6 %) | 36/40 = 90 % | 2 | 2 |
| sites-overture | **38/40 = 95.0 %** (83.5–98.6 %) | 19/40 = 47.5 % (32.9–62.5 %) | 6 | 15 |

- **Identity misses:** ClinicalTrials.gov — `curalta.com` (a podiatry group's site; its trials arm is not its subject;
  `info@` is the podiatry inbox). Overture — `honorhealth.com` (a nine-hospital system; recipient `foundation@`) and
  `centerstone.org` (a mental-health system; recipient `customer.care@`).
- **Overture fit misses** (the domain IS theirs, but they are not research sites): a yoga and ayurveda centre, an
  acupressure school, two autism / child-development therapy centres, a disability rehabilitation institute, a
  sensory-integration therapy centre, an eye hospital, an ENT clinic, a cardiac clinic, a fertility clinic, a genetic
  counselling clinic, a Mumbai hospital, a government neurology institute's unit, and the two health systems above.
  All passed Enrichment's classification as `site`; the shared word is "Research Centre" in the name.
- **ClinicalTrials.gov fit misses:** `zi-mannheim.de` (a psychiatric hospital and public research institute) and
  `kgkscience.com` (a nutraceutical CRO).

**Verdict.** On the question asked, Overture passes the 90 % bar as a point estimate (95 %), though 40 sites cannot
put its lower bound above 83.5 %. On fit it fails badly: about half of its drafts would go to businesses that are
not research sites. **The hold is left on** (D6): lifting it would send those emails, and that is not reversible.
A screen measured on the same 40 sites **[A — in-sample]**: requiring the site's own pages to talk about clinical
research (English "clinical trial / research study / principal investigator / phase II …", Spanish "ensayo / estudio
clínico / investigación clínica", Polish "badania kliniczne", German "klinische Studie", Portuguese "pesquisa clínica")
keeps all 19 real sites, 5 of the 6 partials, and 3 of the 15 non-sites, and drops both identity misses — about 70 %
fit, 89 % with partials, against 47.5 % today. The word list was written after reading these 40, so it needs a fresh
sample before it is trusted. Lifting the hold is one statement (migration `026`'s header).

**Operator decisions, 2026-10-30 (later).** The Overture hold stays as it is, and the clinical-research screen
is **not** built (those sites belong to later niches). Four drafts are held by hand, migration `027`
(`operator hold 2026-10-30: …`, status `pending`): `zi-mannheim.de` (draft 758) and `kgkscience.com` (480, kept for a
later CRO review), both approved until then, and `honorhealth.com` (858) and `centerstone.org` (791), already held
by `026` — their reason no longer starts `db-guard: source sites-overture is held`, so `026`'s lift recipe leaves
them held. Applied on 2026-10-29 (UTC), before 21:58: every other draft's status, approval, hold reason and text unchanged
(compared row by row in the same session); approved queue 373 → 371. `curalta.com` (773, the ClinicalTrials.gov
identity miss) was not in the brief and is still approved.

### 7.6 ClinicalTrials.gov — rate, User-Agent, registry contacts (2026-10-30) **[V]**

- **The harvest** (`harvest_ctgov.py`; the 2026-10-28 run was its scratch twin `ctgov_facilities.py`, same fields and
  pacing): one request in flight. Measured: 15 requests from 12:37:04.5 to 12:38:07.0 UTC — **14.4 a minute**, one
  every 4.2 s, no retries. Its 1.2 s pause would have allowed 50 a minute had the API answered instantly, so it is
  now 6 s: at most 10 a minute.
- **The workflows** (Scoring, Drafting, Drafting - Research Sites): n8n's HTTP batching dispatches on the interval
  without waiting for the answer, and the interval was 250 ms. Measured from every stored execution of the last 48
  hours: 1,701 requests; **the busiest 60 seconds held 58** (2026-10-28 21:29 UTC, during the drain), and a 25-lead
  Scoring run dispatched 25 requests in 6 s (240 a minute while it lasted). Dispatch times are each run's recorded
  start plus 250 ms per request, the node's own rule — derived, not packet-timed. **Now 6 s** (`CTGOV_INTERVAL_MS`,
  the builds refuse less): at most 10 a minute per workflow, 30 if all three ran at once. Live since 2026-10-29
  20:09 UTC.
- **User-Agent:** the harvest sent `NovaScoutBot/1.0 (+abdullah@amitrixlabs.com)` (scratch) / `NovaScoutBot/1.0
  (+https://github.com/abdullahamir2308/nova-scout)` (repo). **The three workflows sent none of their own** (n8n's
  default); they now send the repo one.
- **No registry contact is used:** the harvest's `fields` names no contact, email or phone (an `assert` guards it);
  the workflows ask for `NCTId`, sponsor, collaborator and location facility / city / country / status only; every
  one of the 485 auto-approved site emails goes to a verified address on the site's own domain (§7.1).

### 7.7 Held-email notifications, the area-list holds, and the human APPROVE (2026-10-30, evening)

**Notifications (migration `028`) [V].** 21:00–21:02 PKT the operator got 20+ "[Nova Scout] still waiting" emails:
166 email-hold codes were open (153 Overture source holds, 9 no-address, 4 operator holds — none a decision about
the words), and the reminder lane sent 20 per 30-minute tick. Contained at 21:41 PKT (16:41 UTC): all 166 expired and
reaped, 167 rows deleted (one had already expired); the 17:00 UTC reminder tick found 0. Since then the Daily
Digest counts holds by class and codes only allowlisted ones, and the reminder lane is replies-only — Master Ref §9
Workflow 6 "Daily Digest" and Workflow 7.

**Area-list holds (migration `029`) [V].** Applied with `postgres/apply_drafts_migration.py` (snapshot of every
draft and lead, one transaction, committed only if exactly the expected drafts changed and only in status /
hold_reason / approved_by / approved_at; a re-run changed 0). 55 drafts: curalta.com (773) and the four approved
site drafts whose areas fact came from the full list (466 diex.ca, 526 ezytrials.com, 768 apexresearchgroup.net, 771
titanclinicalresearch.com), approved → pending; and the 50 Overture drafts held by `026` whose areas fact came from
more than six specific areas, re-reasoned so `026`'s lift recipe cannot release them (111 Overture drafts remain
under the plain source hold). Approved email drafts after it: 361 **[V]** (five fewer than before it; sends
since 2026-10-29 account for the rest of the drop from 371).

**The threshold: six specific areas** (`MAX_LISTED_AREAS`, `Other` not counted; a longer list gives no areas fact
and the SMYKM line falls to city, then the company). Measured on every enrichment on 2026-10-30 **[V]**: counts fall
smoothly from 1 to 16 areas and jump at 17 (116 leads, the whole taxonomy); the share of lists naming the taxonomy's
first four areas together is 0–3 % for 1–5 areas, 9 % at 6, 22 % at 7, 35 % at 8 and 9, then up to 100 % at 17.
Because the template names the first two in taxonomy order, a long list always reads "oncology and cardiovascular"
whatever the site does — HIDRA (16), Clinademi (9), Apex (8). Six is a judgment **[A]**: the knee is gradual, and
the cost of a dropped fact (a city sentence, always true) is much smaller than a wrong one sent under the operator's
name. It applies to both lanes (the function is shared): 15 sent CRO first touches would now render city / company
instead of areas (`rerender_sent_cro.py`; nothing sent changes).

**Not held, not decided — the operator's call [V]:** approved drafts whose areas fact comes from 7–16 areas:
CRO 415, 418, 430 (CMAX, 13), 450 (Q-Trials, 9); ClinicalTrials.gov 485, 598 (Flourish, 10), 600 (GCP Research,
12), 691 (IICS, 9). Holding them is the same shape as `029` part 1.

**Draft 812 (Apex Medical Research Amr, apexmedicalresearch.com, Overture, USA) [V].** Held by `026`; the operator
answered its email-hold code with APPROVE at 15:58:52 UTC (20:58 PKT), so it is `approved_by = 'human'`, not sent.
So are 867 (Intouch Clinical Research Center, 15:58:22) and 933 (Liv Medical and Research Center, 15:50:24) —
the only Overture drafts approved, and no Overture lead has ever been emailed. They are not "the next tick":
Send takes the oldest approved draft per lane, and 279 / 290 / 291 older approved USA site drafts are ahead of them
(weeks at today's 4–6 site sends a day **[A]**). 812's list has 8 specific areas and 933's 17 — both the pattern
`029` holds; 867's has 6. Left exactly as the operator approved them. To put the three back under the source hold:
`UPDATE drafts SET status = 'pending', hold_reason = 'db-guard: source sites-overture is held by niche research_site
(precision audit, 2026-10-30)' WHERE id IN (812, 867, 933) AND status = 'approved';`

**A human approval does bypass `held_sources` [V].** `drafts_approval_rules()` checks the held source only inside
`IF NEW.approved_by = 'auto'`; a human approval (APPROVE by email, or the NocoDB grid) takes the `ELSE` branch, and
Send never reads `held_sources`. That is a hole for a *source* hold: the hold means "nothing from this source sends
until the audit says so", and the flood turned it into 153 one-click releases. `028` closes the email path (no
email-hold code is minted for a source hold, the open ones were withdrawn). **To close it fully** (not done — the
brief said not to touch the hold): in the trigger's human branch, refuse an approval whose lead's source is in its
niche's `held_sources` (status back to `pending`, `db-guard: source … is held; a human approval does not lift a
source hold`), and have Send's claim re-check the same, so a draft approved before the hold, or by hand in psql,
cannot go either. An operator hold (`027`, `029`) stays releasable by a person on purpose.

## 8. Master Ref lines this niche makes out of date (not edited, per the brief)

- §1 and §9 Workflow 1b / 4 / 13 "Sites and SMOs are not drafted" / "site and SMO leads wait at `contact_found`".
- §8: migrations list ends at `024`; `025` and the `niches` / `site_lead_sources` tables are not in the schema block,
  nor `026` (`niches.held_sources`, the held-source auto-approval problem), nor `027` (four operator holds, data only), nor `028` (`hold_reason_classes`, `hold_reason_class()`, `hold_needs_person()`,
  `reply_approval_withdraw_holds()`) or `029` (55 operator holds, data only).
- §9 Workflow 5: the trigger's rule "its lead is a CRO" is now "its niche is active and auto-approves, and it signs
  with the niche's identity".
- §12 "Second ICP": "Outreach (2026-10-19): none yet".

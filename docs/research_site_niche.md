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
auto-approvals).

## 2. Sources

See `n8n/sites/open_sources/README.md` for the pipeline. Licences and terms:

| Source | Terms | How it is respected |
|---|---|---|
| ClinicalTrials.gov API v2 | U.S. NLM public data; robots.txt `Disallow: /api/` for crawlers, `Crawl-delay: 1` | One documented-API request in flight, ≥ 1.2 s apart, ~15 requests per harvest — the same API Scoring and Drafting already call. **[A]** that robots.txt's `/api/` line is aimed at crawlers, not at the documented API (§7, decision D2). Location contacts are never requested. |
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
| D2 | ClinicalTrials.gov's robots.txt `Disallow: /api/` is read as aimed at crawlers, not at the documented API v2, which is harvested at one request in flight, ≥ 1.2 s apart, ~15 requests (§2). **[A]** If the operator reads it otherwise, the harvest stops; CTTI's AACT database (a public copy of ClinicalTrials.gov) is the bulk alternative. | **[A]** — operator to confirm |
| D3 | The Anthropic website lookup was not rewired to Ollama: a local model cannot search the web, and the free matcher finds the same site for 14 of 111 (§7.2). | Made |
| D4 | Fit floor 50, equal to Contact Lookup's gate (§3). | Made |
| D5 | Send is unchanged and takes the eligible approved drafts in `ORDER BY d.id`, so an older CRO draft whose recipient is inside their 09:00–17:00 goes before any site draft (45 CRO drafts left at 18:21 PKT); site emails take the ticks no CRO recipient is eligible for (§7.4). Interleaving the niches would change Send. | Operator to decide |
| D6 | The Overture source is kept despite its yield (9 % of processed leads drafted, against 51 % from ClinicalTrials.gov); most of its disqualifications are places that are not research sites. | Operator to decide whether to keep harvesting it |

### 7.4 Fourteen-day send forecast **[A]**

`python n8n/sendtrack/forecast_sends.py --days 14 --runs 100` at 2026-10-29 13:27Z: 530 approved now (CRO 45,
research site 485), 77 follow-ups due in the window, no new site drafts assumed, host up 05:40–22:40 UTC. Median per
day: 16 today (CRO 12, sites 4); 29–30 a day after that (p10–p90 about 25–34); site emails take most of the days
from 2026-10-31 until the CRO follow-ups come due around 2026-11-07. **14-day median total: 409** (about 124 CRO,
315 site). A projection, not a measurement: it assumes the host stays up through the window, and it ignores replies,
bounces and opt-outs. The 2026-10-29 restart took the host down 07:10–14:46 PKT.

## 8. Master Ref lines this niche makes out of date (not edited, per the brief)

- §1 and §9 Workflow 1b / 4 / 13 "Sites and SMOs are not drafted" / "site and SMO leads wait at `contact_found`".
- §8: migrations list ends at `024`; `025` and the `niches` / `site_lead_sources` tables are not in the schema block.
- §9 Workflow 5: the trigger's rule "its lead is a CRO" is now "its niche is active and auto-approves, and it signs
  with the niche's identity".
- §12 "Second ICP": "Outreach (2026-10-19): none yet".

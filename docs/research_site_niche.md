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

Filled in from the 2026-10-28 run — see the session report.

## 8. Master Ref lines this niche makes out of date (not edited, per the brief)

- §1 and §9 Workflow 1b / 4 / 13 "Sites and SMOs are not drafted" / "site and SMO leads wait at `contact_found`".
- §8: migrations list ends at `024`; `025` and the `niches` / `site_lead_sources` tables are not in the schema block.
- §9 Workflow 5: the trigger's rule "its lead is a CRO" is now "its niche is active and auto-approves, and it signs
  with the niche's identity".
- §12 "Second ICP": "Outreach (2026-10-19): none yet".

# Open sources for the research-site niche (migration 025, 2026-10-28)

Two free, licence-clean sources feed `leads` with independent clinical research sites and SMOs. Nothing here
calls a model or a paid API, and no contact ever comes from a source: a site's address is read from its own
website later, by Contact Lookup (Workflow 3b).

| Step | Script | What it does |
|---|---|---|
| 1 | `harvest_ctgov.py OUT.jsonl` | Every RECRUITING, industry-funded study on ClinicalTrials.gov with each location's facility, city, country and the location's own recruiting status. API v2, one request in flight, ≥ 6 s apart (at most 10 a minute, operator brief 2026-10-30; its robots.txt asks `Crawl-delay: 1`). ~15 requests. Location contacts are never requested. |
| 2 | `extract_overture.py RELEASE OUT_DIR` | Overture Maps places with a website whose name or category says research site, read locally with DuckDB from the public bucket (CDLA Permissive 2.0 / Apache 2.0 / CC0 per provider; each row keeps its licences). ~16 files, resumable. |
| 3 | `overture_to_json.py OUT_DIR overture.json` | Flattens step 2 (no emails or phones carried forward). |
| 4 | `node candidates.js trialsites.json ctgov.jsonl overture.json candidates.json` | Screens both with the **shipped** Trialsites rules (included countries = Section 12's clocks, hospital / university / government words and stems, institutional and sanctioned domains) plus this niche's own (sponsor placeholders such as "Research Site", sponsor and CRO names). A ClinicalTrials.gov facility gets a website only from an Overture place of the same country, same name (legal forms off) and same city. One candidate per website host. |
| 5 | `node verify.js trialsites.json candidates.json verified.json [concurrency]` | The **shipped** Verify Website code decides: the site's own pages must name the candidate and its city. Every request first passes the site's robots.txt (`NovaScoutBot`, else `*`); a site that disallows its homepage is never a lead. Records whether the pages carry a contact form and a mailto on the site's own domain. Resumable. |
| 6 | `python ingest.py verified.json [--db novascout] [--apply]` | Dry run by default. Writes nothing from a source a niche holds (`niches.held_sources`, migration 026): **the Overture harvest is stopped since 2026-10-30** (precision audit, `docs/research_site_niche.md` §7.5), so its rows are counted `held_source` and skipped. Writes `leads` (`source = sites-ctgov` / `sites-overture`, `status = ingested`) and `site_lead_sources`. Never touches an existing lead (`ON CONFLICT (domain) DO NOTHING`) or a blocklisted domain. Idempotent. |

From `ingested` a site is an ordinary lead: Enrichment (Ollama: `company_type`), Scoring (the research-site
factors, `n8n/scoring/code_evaluate.js`), Contact Lookup (≥ 50), **Drafting - Research Sites** and **Follow-Ups -
Research Sites** (`n8n/drafting/build_sites_workflows.py`), then Send.

Tests: `node test_open_sources.js` (robots.txt rules, the form detector, the screens).
Attribution: ClinicalTrials.gov (U.S. National Library of Medicine); Overture Maps Foundation, with each
provider's licence as stored per lead in `site_lead_sources.overture_licences`.

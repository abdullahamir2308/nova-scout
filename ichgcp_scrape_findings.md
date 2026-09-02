# ICH GCP Directory — Verified Scrape Findings

Reference for the Nova Scout ingestion scraper. Everything below was
verified by direct fetch, not inferred.

---

## Access constraint

`ichgcp.net` returns **403 Forbidden** to the dev machine's IP, including
with a browser User-Agent header. This is an IP-level block, not a
user-agent block. The site serves normally from other networks.

**Consequence:** the scraper cannot run on the local machine or in the
local n8n container. It must run from an unblocked IP.

**Chosen fix:** scheduled GitHub Action → scrapes → commits CSV to repo →
local n8n fetches the raw CSV from `raw.githubusercontent.com` and upserts
into Postgres. Free, no VPS, version-controlled, and n8n never touches
the blocked domain.

---

## Page structure — two stages required

### Stage 1 — country page
`https://ichgcp.net/cro-list/country/{slug}`

Contains three sections:
1. **"Featured CROs"** — 2 paid slots. These DO include a direct website link.
2. **"Local, small- and mid-size Contract Research Organizations in {country}"** — the ICP target section. Name, description, profile URL. **No website link.**
3. **"Global Contract Research Organizations in {country}"** — IQVIA, ICON, Parexel, PPD, PSI, Fortrea, Syneos, SGS, Celero, Worldwide, ZEINCRO etc.

**Scrape section 2 only.** Section 3 is entirely enterprise CROs that the
Section 9 disqualifiers reject on employee count. Skipping it halves fetch
volume and pre-qualifies leads before any enrichment spend.

Each entry in section 2 links to a profile at:
`/cro-list/country/{country_slug}/company/{company_slug}`

### Stage 2 — company profile page
Verified example: `/cro-list/country/turkey/company/klinar_cro`

Fields present, as labelled text:
- `E-mail:` — mailto link
- `Web:` — the external company website (this is the domain key)
- `Phone:` — tel link
- `Address:` — under an "## Address:" heading
- About text — under "## About {company}" heading

**Notable:** the E-mail field means many leads arrive with a contact address
already attached. Check this before spending an Apollo credit in Workflow 3.

---

## Country slugs — verified, irregular

Do not derive these; several don't match the display name.

```
Turkey          turkey
Mexico          mexico
India           india
Pakistan        pakistan
Egypt           egypt
Poland          poland
Romania         romania
Hungary         hungary
Czech Republic  czech_republic
UAE             united_arab_emirates
South Africa    south_africa
Brazil          brazil
Argentina       argentina
```

---

## Türkiye — local/mid-size section (verified list)

Seven companies. Profile slug under `/cro-list/country/turkey/company/`:

| Company | Slug | Domain |
|---|---|---|
| Klinar CRO | `klinar_cro` | klinar-cro.com ✅ verified |
| Vital CRO Clinical Research Organization Education and Consultancy Ltd. Co. | `vital_cro_clinical_research_organization_education_and_consultancy_ltd_co` | vital-cro.com ✅ verified (featured slot) |
| Atlant Clinical | `atlant_clinical` | not yet fetched |
| Chiron Medical Contract Research Organization | `chiron_medical_contract_research_organization` | not yet fetched |
| CRM-CRO | `crm_cro` | not yet fetched |
| Monitor Medical Research and Consulting | `monitor_medical_research_and_consulting` | not yet fetched |
| Omega CRO | `omega_cro` | not yet fetched |

Also in the featured slot: **PVR** (`pvr`) — pv-r.com ✅ verified. Note PVR
operates across Georgia, Israel, Türkiye, Ukraine, UK — may not be
small/mid-size. Let scoring decide.

Full verified record for Klinar CRO, as a parser test fixture:
```
company_name: Klinar CRO
email:        info@klinar-cro.com
website:      https://klinar-cro.com/
phone:        +(90) (312)-447-0274
address:      Mustafa Kemal Mah 2127. Sk. 42 /3 Çankaya / ANKARA TURKEY
country:      Türkiye
```

---

## Notes for the scraper

- Multi-country CROs appear on every country page they operate in, each
  with a different profile URL but the same underlying company. Dedup on
  domain handles this — the same company resolves to one domain.
- Some entries have a "View locations" sublist. Ignore it for ingestion.
- Rate-limit politely: sequential requests with a delay, not parallel bursts.
- Skip any profile with no `Web:` field — a domain-keyed row can't exist
  without one. Count and report skips.

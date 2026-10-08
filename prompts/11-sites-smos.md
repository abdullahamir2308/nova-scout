Add clinical research sites and SMOs as a second lead source, 
alongside CROs.

Source: Trialsites (trialsites.aimronline.org). Free API, no auth, 
60 requests/min. Read its API docs first and report the available 
fields, especially whether a website or domain is included.

1. Pull candidates that are active, quality tier A or B, and have a 
   facility type of independent research site or SMO. Exclude 
   hospitals, universities and government institutions. Use only the 
   countries Session A included. At most 200 new candidates per 
   weekly run.

2. Website lookup (only for sites without one in Trialsites): use 
   Claude with web search to find the official website, then confirm 
   it by fetching the page and checking that the site's name and city 
   appear. If there's no confident match, skip the site; never guess 
   a domain. Use the cheapest model that does this reliably. Report 
   the cost per resolved site and set a monthly spend cap.

3. Enrichment classifies company_type as CRO, site or SMO. Replace 
   the "not a CRO" disqualifier with "not a CRO, site or SMO".

4. Scoring for sites: use Trialsites' trial volume, recency and tier 
   as the active-trials factor. Unlike for CROs, it measures 
   something real here.

5. Claims: add site-specific angles and benefits to claims_library 
   (sites receive inquiries from sponsors and from CROs choosing 
   sites). Verify each one against the Nova Agent Kit code first, 
   and seed them as confirmed=false, so drafts using them stay held 
   until I confirm. Drafting picks claims by company_type.

6. Dry-run end to end on 20 candidates. Report the funnel counts and 
   two sample drafts.

7. Update MasterRef §4 (costs), §9 and §12.

8. Import the changed workflows. I'll publish them in the UI.

Publishing workflows in the n8n UI does not block later tasks. Import them, 
list the names under human actions in your report, and still end with 
STATUS: DONE.
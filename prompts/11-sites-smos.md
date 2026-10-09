model: opus

Context: Session R (the reply assistant) was finished in a separate 
session. Read NovaScout_MasterRef.md §3, §4, §5, §7, §9, §12 and §13 
and git log before anything else. Nova Scout is live and sending up to 
20 emails a day.

Safety: this shell exports the live .env. Before running anything that 
can send mail, blank every SMTP and mailbox variable and confirm no 
reachable host remains. Dry runs use the SMTP sink only. Do not send, 
approve or schedule anything to any prospect.

Goal: add clinical research sites and SMOs as a second lead source 
alongside CROs.

0. Report the funnel: leads per status, approved-unsent drafts, and 
   days of supply at the 20/day ceiling. Check whether the "address 
   published on the lead's own website" contact source and the "needs 
   manual contact" NocoDB view exist. If not, build them first. 
   Addresses come only from the company's own site pages, same 
   domain, never guessed.

1. Source: Trialsites (trialsites.aimronline.org, free API, no auth, 
   60 requests/min). Read its API docs first and report the fields 
   returned, especially any website or domain. Candidates: active, 
   tier A or B, independent research sites or SMOs. Exclude 
   hospitals, universities and government bodies. Only countries the 
   CRO scraper already includes. At most 200 new candidates per weekly 
   run. Never use personal investigator emails from registry data.

2. Website lookup for candidates without one: Claude with web search 
   finds the official site, then confirm by fetching it and checking 
   that the name and city appear. No confident match means skip; 
   never guess a domain. Use the cheapest model that does this 
   reliably, report cost per resolved site, and add a monthly spend 
   cap.

3. Enrichment classifies company_type as CRO, site or SMO. Change the 
   "not a CRO" disqualifier to "not a CRO, site or SMO".

4. Scoring for sites: use Trialsites' trial volume, recency and tier 
   as the active-trials factor, since it measures something real 
   here. Document the weights.

5. Claims: add site-specific description, angles and benefits (sites 
   get inquiries from sponsors and from CROs choosing sites). Verify 
   each against the Nova Agent Kit code. The Vertex tenant isn't in 
   that repo, so claim only what the code supports. Seed everything 
   confirmed=false so site drafts stay held until I confirm. 
   Proof line for sites: "It's live at Vertex Clinical Research, a 
   research center in Mexico." Vertex is a clinical research center, 
   never a CRO, and never use the "two CROs" line. Drafting picks 
   claims by company_type and must not say "CRO websites" for a site.

6. Add a site version of the one-pager, hosted the same way as the 
   current one, and pick the reply link by company_type.

7. Dry-run end to end on 20 candidates. Report funnel counts and two 
   sample drafts, one site and one SMO if available.

8. Update MasterRef §4, §9, §12 and §13. Import changed workflows. 
   Don't publish.

Publishing workflows in the n8n UI and confirming the site claims do 
not block later tasks. List both under human actions in your report 
and still end with STATUS: DONE. Commit your finished work with 
specific paths. Don't push.
"""Harvest every RECRUITING, industry-funded study's sites from ClinicalTrials.gov.

    python n8n/sites/open_sources/harvest_ctgov.py OUT.jsonl

Source 1 of the research-site niche (migration 025). What it asks for and what it
does not:

  * the study id, its lead sponsor, conditions and phases, and for each location
    ONLY the facility name, city, state, country and that location's recruiting
    status. Never a location's contact (name, email, phone): those are for
    patients enquiring about a trial, and the operator's rule is that a site's
    address comes only from the site's own website (Workflow 3b).
  * the documented public API v2, one request in flight, >= 1.2 s apart --
    ClinicalTrials.gov's robots.txt asks crawlers for Crawl-delay: 1. A full
    harvest is ~15 pages of 1,000 studies (measured 2026-10-28: 14,100 studies,
    248,276 locations, 15 requests).

ClinicalTrials.gov data is a U.S. National Library of Medicine service; the same
API already feeds Scoring and Drafting (Master Ref Section 9).
"""
import json
import sys
import time
import urllib.parse
import urllib.request

API = "https://clinicaltrials.gov/api/v2/studies"
UA = "NovaScoutBot/1.0 (+https://github.com/abdullahamir2308/nova-scout)"
FIELDS = ("NCTId,LeadSponsorName,LeadSponsorClass,Condition,Phase,"
          "LocationFacility,LocationCity,LocationState,LocationCountry,LocationStatus")
# The fields above carry no contact: a guard, so nobody "just adds" one.
assert "Contact" not in FIELDS and "Email" not in FIELDS and "Phone" not in FIELDS
PACE_S = 1.2


def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=120).read())
        except Exception as e:  # transient 502s are documented (memory: CT.gov returns HTML 502 pages)
            print("retry %d: %s" % (attempt + 1, e), flush=True)
            time.sleep(5 * (attempt + 1))
    sys.exit("ClinicalTrials.gov did not answer after 5 attempts")


def main(out_path):
    params = {"filter.overallStatus": "RECRUITING", "aggFilters": "funderType:industry", "fields": FIELDS,
              "pageSize": "1000", "countTotal": "true"}
    token, pages, studies, rows = None, 0, 0, 0
    with open(out_path + ".tmp", "w", encoding="utf-8") as fh:
        while True:
            p = dict(params)
            if token:
                p["pageToken"] = token
            data = fetch(p)
            if pages == 0:
                print("studies matching:", data.get("totalCount"), flush=True)
            for s in data.get("studies", []):
                ps = s["protocolSection"]
                for loc in ps.get("contactsLocationsModule", {}).get("locations", []):
                    fh.write(json.dumps({
                        "nct": ps["identificationModule"]["nctId"],
                        "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
                        "conditions": ps.get("conditionsModule", {}).get("conditions", []),
                        "phases": ps.get("designModule", {}).get("phases", []),
                        "facility": loc.get("facility"), "city": loc.get("city"), "state": loc.get("state"),
                        "country": loc.get("country"), "status": loc.get("status"),
                    }, ensure_ascii=False) + "\n")
                    rows += 1
                studies += 1
            pages += 1
            print("page %d: %d studies, %d locations" % (pages, studies, rows), flush=True)
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(PACE_S)
    import os
    os.replace(out_path + ".tmp", out_path)
    print("done: %d requests, %d studies, %d locations -> %s" % (pages, studies, rows, out_path))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

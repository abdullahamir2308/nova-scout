"""
Offline regression test for scrape_ichgcp.py's parsing logic.

ichgcp.net returns 403 to this machine and to local n8n (see
ichgcp_scrape_findings.md) — there is no way to fetch real HTML here. This
test instead builds synthetic HTML that encodes the *documented* page
structure (labelled fields on the profile page; two named sections on the
country page) using the verified Klinar CRO fixture values, and checks the
parser recovers them. It proves the parsing logic matches the documented
structure — it does NOT prove the real site's markup matches that
description. The first real Action run against ichgcp.net is the actual
test of that.

Run: python scraper/test_parse_fixture.py
"""

from scrape_ichgcp import (
    extract_local_section_links,
    parse_profile,
    normalize_domain,
    dedupe_by_domain,
    LeadRow,
)

FIXTURE = {
    "company_name": "Klinar CRO",
    "email": "info@klinar-cro.com",
    "website": "https://klinar-cro.com/",
    "phone": "+(90) (312)-447-0274",
    "address": "Mustafa Kemal Mah 2127. Sk. 42 /3 Çankaya / ANKARA TURKEY",
    "country": "Türkiye",
}

PROFILE_HTML = f"""
<html><body>
<div class="profile">
  <h1>{FIXTURE['company_name']}</h1>
  <p>E-mail: <a href="mailto:{FIXTURE['email']}">{FIXTURE['email']}</a></p>
  <p>Web: <a href="{FIXTURE['website']}">{FIXTURE['website']}</a></p>
  <p>Phone: <a href="tel:{FIXTURE['phone']}">{FIXTURE['phone']}</a></p>
  <h3>Address:</h3>
  <p>{FIXTURE['address']}</p>
  <h3>About {FIXTURE['company_name']}</h3>
  <p>Klinar CRO is a full-service contract research organization based in
  Ankara, Turkey, supporting oncology and cardiology trials across the
  region.</p>
  <a href="https://www.facebook.com/klinarcro">Facebook</a>
  <a href="https://www.linkedin.com/company/klinarcro">LinkedIn</a>
</div>
</body></html>
"""

COUNTRY_PAGE_HTML = """
<html><body>
  <section>
    <h2>Featured CROs</h2>
    <div class="listing">
      <a href="https://pv-r.com">PVR</a>
      <a href="/cro-list/country/turkey/company/vital_cro_clinical_research_organization_education_and_consultancy_ltd_co">Vital CRO</a>
    </div>
  </section>
  <section>
    <h2>Local, small- and mid-size Contract Research Organizations in Turkey</h2>
    <ul>
      <li><a href="/cro-list/country/turkey/company/klinar_cro">Klinar CRO</a></li>
      <li><a href="/cro-list/country/turkey/company/atlant_clinical">Atlant Clinical</a></li>
      <li><a href="/cro-list/country/turkey/company/omega_cro">Omega CRO</a>
        <div class="sublocations">
          <a href="/cro-list/country/turkey/company/omega_cro#locations">View locations</a>
        </div>
      </li>
    </ul>
  </section>
  <section>
    <h2>Global Contract Research Organizations in Turkey</h2>
    <ul>
      <li><a href="/cro-list/country/turkey/company/iqvia">IQVIA</a></li>
      <li><a href="/cro-list/country/turkey/company/icon">ICON</a></li>
    </ul>
  </section>
</body></html>
"""


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def main():
    all_ok = True

    fields = parse_profile(PROFILE_HTML)
    print("parse_profile() ->", fields)
    all_ok &= check("email matches fixture", fields["email"] == FIXTURE["email"])
    all_ok &= check("website matches fixture (no social links leaked)",
                     fields["website"] == FIXTURE["website"])
    all_ok &= check("phone matches fixture", fields["phone"] == FIXTURE["phone"])
    all_ok &= check("address matches fixture", fields["address"] == FIXTURE["address"])
    all_ok &= check("description captured and non-empty", len(fields["description"]) > 20)
    all_ok &= check("description does not swallow the social links",
                     "facebook" not in fields["description"].lower())

    domain = normalize_domain(fields["website"])
    print("normalize_domain() ->", domain)
    all_ok &= check("domain normalizes to klinar-cro.com", domain == "klinar-cro.com")

    links = extract_local_section_links(COUNTRY_PAGE_HTML, "turkey")
    link_urls = [l.url for l in links]
    print("extract_local_section_links() ->", link_urls)
    all_ok &= check("finds exactly the 3 local-section companies", len(links) == 3)
    all_ok &= check("includes Klinar CRO",
                     any("klinar_cro" in u for u in link_urls))
    all_ok &= check("excludes Featured section (Vital CRO / PVR)",
                     not any("vital_cro" in u for u in link_urls))
    all_ok &= check("excludes Global section (IQVIA / ICON)",
                     not any(u.endswith("/iqvia") or u.endswith("/icon") for u in link_urls))
    all_ok &= check("'View locations' sublink not duplicated as a separate row",
                     len([u for u in link_urls if "omega_cro" in u]) == 1)

    dup_rows = [
        LeadRow(company_name="Klinar CRO", domain="klinar-cro.com", country="Turkey", email="info@klinar-cro.com"),
        LeadRow(company_name="Klinar CRO", domain="klinar-cro.com", country="Turkey", email=""),
    ]
    deduped, dup_count = dedupe_by_domain(dup_rows)
    all_ok &= check("multi-country duplicate collapses to one row", len(deduped) == 1)
    all_ok &= check("duplicate count reported correctly", dup_count == 1)

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
ICH GCP directory scraper for Nova Scout — Workflow 1 (Ingestion) source.

Runs only from GitHub Actions (ichgcp.net blocks the dev machine's IP; see
ichgcp_scrape_findings.md). Two-stage scrape per country:
  1. Country page -> profile links from the "local/mid-size" section only.
  2. Each profile page -> company website, email, phone, address, description.

Output: a deduped CSV at OUTPUT_CSV_PATH (default data/ichgcp_leads.csv).
Entries with no discoverable website are skipped (a domain-keyed leads row
can't exist without one) and counted in the report.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ichgcp.net"

# Verified, irregular — do not derive from the display name.
COUNTRY_SLUGS = {
    "Turkey": "turkey",
    "Mexico": "mexico",
    "India": "india",
    "Pakistan": "pakistan",
    "Egypt": "egypt",
    "Poland": "poland",
    "Romania": "romania",
    "Hungary": "hungary",
    "Czech Republic": "czech_republic",
    "UAE": "united_arab_emirates",
    "South Africa": "south_africa",
    "Brazil": "brazil",
    "Argentina": "argentina",
}

# Self-identifying, non-deceptive UA (Googlebot/Bingbot-style format) rather
# than spoofing a real browser. Override via ICHGCP_USER_AGENT if the WAF
# rejects it — this is the one thing that can't be verified without a real
# run from an Actions runner.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; NovaScoutBot/1.0; "
    "+https://github.com/abdullahamir2308/nova-scout)"
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3

SOCIAL_OR_MAP_HOSTS = {
    "facebook.com", "www.facebook.com",
    "linkedin.com", "www.linkedin.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "wa.me", "whatsapp.com", "www.whatsapp.com",
    "maps.google.com", "www.google.com", "goo.gl", "g.page",
    "ichgcp.net", "www.ichgcp.net",
}

LOCAL_SECTION_RE = re.compile(r"local,?\s+small-?\s*and\s+mid-?\s*size", re.I)
GLOBAL_SECTION_RE = re.compile(r"global\s+contract\s+research\s+organi[sz]ations", re.I)


@dataclass
class ProfileLink:
    name: str
    url: str


@dataclass
class LeadRow:
    company_name: str
    domain: str
    country: str
    source: str = "ichgcp"
    email: str = ""
    phone: str = ""
    address: str = ""
    description: str = ""
    profile_url: str = ""


@dataclass
class CountryStats:
    country: str
    found: int = 0
    skipped_no_website: int = 0
    fetch_failures: int = 0
    country_page_failed: bool = False


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get("ICHGCP_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def fetch(session: requests.Session, url: str) -> requests.Response | None:
    """Sequential fetch with a small retry budget for transient failures.
    A 403 is treated as terminal (retrying won't help and would be rude)."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = exc
            print(f"    [warn] fetch error on attempt {attempt} for {url}: {exc}")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if resp.status_code == 403:
            print(f"    [error] 403 Forbidden for {url} — not retrying")
            return None
        if resp.status_code >= 500 and attempt < MAX_RETRIES:
            print(f"    [warn] {resp.status_code} on attempt {attempt} for {url}, retrying")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue
        if resp.status_code >= 400:
            print(f"    [error] HTTP {resp.status_code} for {url}")
            return None
        return resp

    print(f"    [error] giving up on {url} after {MAX_RETRIES} attempts: {last_exc}")
    return None


def extract_local_section_links(html: str, slug: str) -> list[ProfileLink]:
    """Walk the country page in document order, tracking which of the two
    named sections we're in. Only a text-only tag (no <a> descendants) can
    flip the section state, so a wrapping <div> that contains both the
    heading and the company links never falsely re-triggers the match."""
    soup = BeautifulSoup(html, "html.parser")
    profile_marker = f"/cro-list/country/{slug}/company/"

    state = "before"
    seen_urls: set[str] = set()
    links: list[ProfileLink] = []

    for tag in soup.find_all(True):
        has_links = bool(tag.find("a"))
        if not has_links:
            own_text = tag.get_text(" ", strip=True)
            if own_text and LOCAL_SECTION_RE.search(own_text):
                state = "local"
                continue
            if own_text and GLOBAL_SECTION_RE.search(own_text):
                state = "global"
                continue

        if state != "local" or tag.name != "a":
            continue

        href = tag.get("href")
        if not href or profile_marker not in href:
            continue

        # Strip fragments so a "View locations" sublink (#locations) collapses
        # into the same entry as the company's primary profile link.
        absolute = urllib.parse.urljoin(BASE_URL, href).split("#", 1)[0]
        if absolute in seen_urls:
            continue
        seen_urls.add(absolute)

        name = tag.get_text(strip=True)
        links.append(ProfileLink(name=name, url=absolute))

    return links


def _is_real_website(href: str) -> bool:
    if not href:
        return False
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.netloc.lower()
    return host not in SOCIAL_OR_MAP_HOSTS


MAX_CONTINUATION_LINES = 10


def _find_labelled_value(lines: list[str], label_re: re.Pattern, stop_labels_re: re.Pattern) -> str:
    """Find a 'Label:' line and return either its inline value or the
    following non-blank lines up to the next known label. `lines` must
    already have blank/whitespace-only entries filtered out — real pages
    are full of whitespace-only text nodes between block tags, which would
    otherwise look identical to an intentional paragraph break."""
    for i, line in enumerate(lines):
        m = label_re.match(line)
        if not m:
            continue
        inline = line[m.end():].strip(" :–-")
        if inline:
            return inline
        collected = []
        for follow in lines[i + 1:i + 1 + MAX_CONTINUATION_LINES]:
            if stop_labels_re.match(follow):
                break
            collected.append(follow)
        return " ".join(collected).strip()
    return ""


KNOWN_LABELS_RE = re.compile(
    r"^\s*(E-?mail|Web|Phone|Address|About)\b", re.I
)

# Generic nav/footer/social chrome that shows up on virtually any site.
# There's no real HTML sample to scope the "About" paragraph to a specific
# container, so this is a safety net against swallowing page furniture into
# the description — stop collecting as soon as one of these appears.
FOOTER_NOISE_RE = re.compile(
    r"^(Home|About Us|Contact( Us)?|Services|Privacy Policy|"
    r"Terms( of (Use|Service))?|Cookie[s]?( Policy)?|"
    r"All rights reserved|Copyright|©|Facebook|Twitter|LinkedIn|"
    r"Instagram|YouTube|WhatsApp)\b",
    re.I,
)
STOP_RE = re.compile(f"(?:{KNOWN_LABELS_RE.pattern})|(?:{FOOTER_NOISE_RE.pattern})")


def parse_profile(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    email = ""
    mailto = soup.find("a", href=re.compile(r"^mailto:", re.I))
    if mailto:
        email = mailto["href"].split(":", 1)[1].split("?")[0].strip()

    phone = ""
    tel = soup.find("a", href=re.compile(r"^tel:", re.I))
    if tel:
        phone = tel["href"].split(":", 1)[1].strip()

    website = ""
    for a in soup.find_all("a", href=True):
        if _is_real_website(a["href"]):
            website = a["href"].strip()
            break

    # Strip known social/share links before flattening to text — they're
    # page chrome, not profile content, and otherwise bleed into whatever
    # text region happens to follow them (e.g. an About paragraph placed
    # right above a row of social icons).
    for a in soup.find_all("a", href=True):
        host = urllib.parse.urlparse(a["href"]).netloc.lower()
        if host in SOCIAL_OR_MAP_HOSTS:
            a.decompose()

    # Drop blank/whitespace-only entries: get_text("\n") joins every
    # NavigableString (including pure-indentation whitespace between block
    # tags) with the separator, so a "blank line" here is markup noise, not
    # a real paragraph break.
    text_lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]

    if not phone:
        phone = _find_labelled_value(
            text_lines, re.compile(r"^\s*Phone\s*:?", re.I), STOP_RE
        )

    address = _find_labelled_value(
        text_lines, re.compile(r"^\s*Address\s*:?", re.I), STOP_RE
    )

    description = ""
    about_re = re.compile(r"^\s*About\s+.+", re.I)
    for i, line in enumerate(text_lines):
        if about_re.match(line) and len(line) < 120:
            collected = []
            for follow in text_lines[i + 1:i + 1 + MAX_CONTINUATION_LINES]:
                if STOP_RE.match(follow):
                    break
                collected.append(follow)
            description = " ".join(collected).strip()[:1000]
            break

    return {
        "email": email,
        "phone": phone,
        "website": website,
        "address": address,
        "description": description,
    }


def normalize_domain(url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "http://" + url
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    host = host.split("@")[-1]
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    return host


def scrape_country(session: requests.Session, display_name: str, slug: str, delay: float) -> tuple[list[LeadRow], CountryStats]:
    stats = CountryStats(country=display_name)
    rows: list[LeadRow] = []

    country_url = f"{BASE_URL}/cro-list/country/{slug}"
    print(f"[{display_name}] fetching country page: {country_url}")
    resp = fetch(session, country_url)
    if resp is None:
        stats.country_page_failed = True
        print(f"[{display_name}] country page fetch failed — skipping country")
        return rows, stats

    links = extract_local_section_links(resp.text, slug)
    print(f"[{display_name}] {len(links)} local/mid-size profile link(s) found")

    for link in links:
        time.sleep(delay)
        profile_resp = fetch(session, link.url)
        if profile_resp is None:
            stats.fetch_failures += 1
            print(f"  [skip] profile fetch failed: {link.url}")
            continue

        fields = parse_profile(profile_resp.text)
        domain = normalize_domain(fields["website"])
        if not domain:
            stats.skipped_no_website += 1
            print(f"  [skip] no website: {link.name or link.url}")
            continue

        stats.found += 1
        rows.append(LeadRow(
            company_name=link.name or domain,
            domain=domain,
            country=display_name,
            email=fields["email"],
            phone=fields["phone"],
            address=fields["address"],
            description=fields["description"],
            profile_url=link.url,
        ))

    return rows, stats


def dedupe_by_domain(rows: list[LeadRow]) -> tuple[list[LeadRow], int]:
    by_domain: dict[str, LeadRow] = {}
    duplicates = 0
    for row in rows:
        existing = by_domain.get(row.domain)
        if existing is None:
            by_domain[row.domain] = row
            continue
        duplicates += 1
        # Multi-country CROs resolve to one row (Section 9 dedupe rule).
        # Keep the first country seen; backfill any blank fields from the
        # duplicate in case that profile page had more complete data.
        for attr in ("email", "phone", "address", "description"):
            if not getattr(existing, attr) and getattr(row, attr):
                setattr(existing, attr, getattr(row, attr))
    return list(by_domain.values()), duplicates


CSV_FIELDS = [
    "company_name", "domain", "country", "source",
    "email", "phone", "address", "description", "profile_url",
]


def write_csv(path: str, rows: list[LeadRow]) -> None:
    rows_sorted = sorted(rows, key=lambda r: (r.country, r.company_name.lower()))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow({field_name: getattr(row, field_name) for field_name in CSV_FIELDS})


def main() -> int:
    delay = float(os.environ.get("ICHGCP_DELAY_SECONDS", "2"))
    output_path = os.environ.get("OUTPUT_CSV_PATH", os.path.join("data", "ichgcp_leads.csv"))

    session = make_session()
    all_rows: list[LeadRow] = []
    all_stats: list[CountryStats] = []

    country_items = list(COUNTRY_SLUGS.items())
    for idx, (display_name, slug) in enumerate(country_items):
        rows, stats = scrape_country(session, display_name, slug, delay)
        all_rows.extend(rows)
        all_stats.append(stats)
        if idx < len(country_items) - 1:
            time.sleep(delay)

    deduped_rows, duplicate_count = dedupe_by_domain(all_rows)
    write_csv(output_path, deduped_rows)

    total_found = sum(s.found for s in all_stats)
    total_skipped = sum(s.skipped_no_website for s in all_stats)
    total_fetch_failures = sum(s.fetch_failures for s in all_stats)
    countries_failed = [s.country for s in all_stats if s.country_page_failed]

    print("\n=== ICH GCP Scrape Report ===")
    for s in all_stats:
        if s.country_page_failed:
            print(f"{s.country}: COUNTRY PAGE FETCH FAILED")
        else:
            print(f"{s.country}: {s.found} found, {s.skipped_no_website} skipped (no website), "
                  f"{s.fetch_failures} profile fetch failures")
    print("-" * 40)
    print(f"Total profiles with a usable website: {total_found}")
    print(f"Total skipped (no website): {total_skipped}")
    print(f"Total profile fetch failures: {total_fetch_failures}")
    print(f"Duplicate domains merged: {duplicate_count}")
    print(f"Countries with failed country-page fetch: {len(countries_failed)}"
          + (f" ({', '.join(countries_failed)})" if countries_failed else ""))
    print(f"Final unique leads written to {output_path}: {len(deduped_rows)}")
    print("=" * 40)

    if len(deduped_rows) == 0:
        print("\n[fatal] zero leads scraped across all countries — failing the run.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

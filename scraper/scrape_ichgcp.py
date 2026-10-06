#!/usr/bin/env python3
"""
ICH GCP directory scraper for Nova Scout — Workflow 1 (Ingestion) source.

Runs only from GitHub Actions (ichgcp.net blocks the dev machine's IP; see
ichgcp_scrape_findings.md). Three stages:
  0. Country index (/cro-list) -> every country the directory lists, minus the
     sanctioned jurisdictions (geography.py, MasterRef Section 12), narrowed to
     one weekday shard so a run stays small and inside the 6-hour job limit.
  1. Country page -> profile links from the "local/mid-size" section only.
  2. Each profile page -> company website, email, phone, address, description.

Output: the shard's results MERGED into the CSV at OUTPUT_CSV_PATH (default
data/ichgcp_leads.csv); rows of other shards are left as they are. Entries with
no discoverable website are skipped (a domain-keyed leads row can't exist
without one) and counted in the report.

Environment:
  ICHGCP_SHARD                 europe | asia-pacific | americas | middle-east |
                               africa | all   (default: all)
  ICHGCP_PLAN_ONLY=1           classify the index and count each country's local
                               listings (one request per country); fetch no
                               profile and write no CSV
  ICHGCP_DELAY_SECONDS         pause between requests (default 2)
  ICHGCP_MAX_RUNTIME_SECONDS   stop starting new work after this long (default
                               16200 = 4.5 h; the job limit is 6 h)
  ICHGCP_BREAKER               consecutive 403/exhausted-429 answers that abort
                               the run (default 8)
  ICHGCP_REPORT_PATH           where the JSON report goes (default
                               scrape_report.json, never committed)

Exit codes: 0 ok · 1 no rows scraped · 2 country index unusable · 3 aborted by
the circuit breaker (completed countries are still merged and written).
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

import geography

BASE_URL = "https://ichgcp.net"

# Verified, irregular — do not derive from the display name. Kept under its old
# name; the definition now lives with the rest of the country policy.
COUNTRY_SLUGS = geography.CORE_COUNTRIES

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

# 429 gets its own, longer retry budget — it's the site's rate limiter
# clearing on a cooldown timer, not a transient hiccup that a 3s/9s backoff
# fixes. Kept separate from MAX_RETRIES so exceptions/5xx behavior is
# unchanged.
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BACKOFF_SECONDS = 15
RATE_LIMIT_BACKOFF_CAP_SECONDS = 60

# Politeness at directory scale. A WAF block (403) or a rate limit that outlasts
# the whole retry budget means "stop", not "try the next of 2,000 profiles": the
# breaker aborts after this many such answers in a row, and a success resets it.
DEFAULT_BREAKER = 8
TOTAL_BLOCKED_FACTOR = 4   # ...or this many times the limit in total, successes or not
# One run never starts new work after this long. GitHub's job limit is 6 h and a
# job killed at the limit commits nothing, so the scraper stops itself first.
DEFAULT_MAX_RUNTIME_SECONDS = 16200
# A country index with fewer links than this is a changed page, not a directory.
MIN_INDEX_COUNTRIES = 15

COUNTRY_LINK_PATH_RE = re.compile(r"^/cro-list/country/([^/?#]+)/?$")

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
    # Merge bookkeeping: a country is `complete` only when every one of its
    # profiles was attempted. An incomplete country (deadline, breaker) must not
    # delete its previously scraped rows.
    complete: bool = False
    aborted: bool = False           # the circuit breaker ended this country
    listed: int = 0                 # local/mid-size links on the country page
    sanctioned_nexus: int = 0       # rows dropped by the domain/address screen
    new_unique: int = 0             # filled in after the merge
    failed_profile_urls: list = field(default_factory=list)


class AbortRun(Exception):
    """Raised by the circuit breaker; main() merges what it has and exits 3."""


class FetchHealth:
    """Counts consecutive answers that mean 'stop' (403, or a 429 that outlasted
    its retry budget). 404 and 5xx are a dead link or a hiccup, not a block."""

    def __init__(self, limit: int = DEFAULT_BREAKER):
        self.limit = limit
        self.streak = 0
        self.total_blocked = 0

    def ok(self) -> None:
        self.streak = 0

    def blocked(self, why: str) -> None:
        self.streak += 1
        self.total_blocked += 1
        if self.limit <= 0:
            return
        if self.streak >= self.limit:
            raise AbortRun(f"{self.streak} consecutive blocked answers (last: {why})")
        # Blocks interleaved with successes (a flaky or partial WAF) never make a
        # streak, so the whole run also has a ceiling.
        if self.total_blocked >= self.limit * TOTAL_BLOCKED_FACTOR:
            raise AbortRun(f"{self.total_blocked} blocked answers in one run (last: {why})")


HEALTH = FetchHealth()


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get("ICHGCP_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date form)."""
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(value)
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=datetime.timezone.utc)
        delta = (target - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        return max(delta, 0.0)
    except (TypeError, ValueError):
        return None


def fetch(session: requests.Session, url: str) -> requests.Response | None:
    """Sequential fetch with a small retry budget for transient failures.
    A 403 is treated as terminal (retrying won't help and would be rude).
    A 429 draws from its own longer, Retry-After-aware budget (see
    RATE_LIMIT_MAX_RETRIES) instead of the generic one."""
    last_exc = None
    rate_limit_attempt = 0
    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = exc
            print(f"    [warn] fetch error on attempt {attempt} for {url}: {exc}")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if resp.status_code == 403:
            print(f"    [error] 403 Forbidden for {url} — not retrying")
            HEALTH.blocked("403")
            return None
        if resp.status_code == 429:
            attempt -= 1  # doesn't draw from the generic retry budget
            rate_limit_attempt += 1
            if rate_limit_attempt > RATE_LIMIT_MAX_RETRIES:
                print(f"    [error] 429 for {url} — exhausted "
                      f"{RATE_LIMIT_MAX_RETRIES} rate-limit retries")
                HEALTH.blocked("429")
                return None
            wait = _retry_after_seconds(resp)
            if wait is None:
                wait = min(
                    RATE_LIMIT_BACKOFF_SECONDS * rate_limit_attempt,
                    RATE_LIMIT_BACKOFF_CAP_SECONDS,
                )
            print(f"    [warn] 429 for {url}, waiting {wait:.0f}s "
                  f"(rate-limit retry {rate_limit_attempt}/{RATE_LIMIT_MAX_RETRIES})")
            time.sleep(wait)
            continue
        if resp.status_code >= 500 and attempt < MAX_RETRIES:
            print(f"    [warn] {resp.status_code} on attempt {attempt} for {url}, retrying")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue
        if resp.status_code >= 400:
            print(f"    [error] HTTP {resp.status_code} for {url}")
            return None
        # Only a profile page resets the streak. A country page loading proves
        # nothing about profile pages, and with 4-9 profiles a country a
        # profile-blocking WAF would otherwise be reset before it ever tripped.
        if "/company/" in url:
            HEALTH.ok()
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


def scrape_country(session: requests.Session, display_name: str, slug: str, delay: float,
                   deadline: float | None = None) -> tuple[list[LeadRow], CountryStats]:
    """Scrape one country. Never raises AbortRun: the breaker or the deadline
    ends the country early, marks it incomplete (so the merge keeps its old
    rows) and sets `stats.aborted` for the caller."""
    stats = CountryStats(country=display_name)
    rows: list[LeadRow] = []

    country_url = f"{BASE_URL}/cro-list/country/{slug}"
    print(f"[{display_name}] fetching country page: {country_url}")
    try:
        resp = fetch(session, country_url)
    except AbortRun as exc:
        stats.country_page_failed = True
        stats.aborted = True
        print(f"[{display_name}] ABORT: {exc}")
        return rows, stats
    if resp is None:
        stats.country_page_failed = True
        print(f"[{display_name}] country page fetch failed — skipping country")
        return rows, stats

    links = extract_local_section_links(resp.text, slug)
    stats.listed = len(links)
    print(f"[{display_name}] {len(links)} local/mid-size profile link(s) found")

    for link in links:
        if deadline is not None and time.monotonic() >= deadline:
            print(f"[{display_name}] runtime budget reached — country left incomplete")
            return rows, stats
        time.sleep(delay)
        try:
            profile_resp = fetch(session, link.url)
        except AbortRun as exc:
            stats.aborted = True
            print(f"[{display_name}] ABORT: {exc}")
            return rows, stats
        if profile_resp is None:
            stats.fetch_failures += 1
            stats.failed_profile_urls.append(link.url)
            print(f"  [skip] profile fetch failed: {link.url}")
            continue

        fields = parse_profile(profile_resp.text)
        domain = normalize_domain(fields["website"])
        if not domain:
            stats.skipped_no_website += 1
            print(f"  [skip] no website: {link.name or link.url}")
            continue

        # Second line of control after the country filter: a company listed
        # under a retained country but located in (or domain-registered to) an
        # excluded jurisdiction is still excluded.
        nexus = geography.domain_nexus(domain) or geography.address_nexus(fields["address"])
        if nexus:
            stats.sanctioned_nexus += 1
            print(f"  [skip] sanctioned nexus ({nexus}): {link.name or domain}")
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

    stats.complete = True
    return rows, stats


def dedupe_by_domain(rows: list[LeadRow], prefer: dict[str, str] | None = None) -> tuple[list[LeadRow], int]:
    """One row per domain. A CRO listed in several countries keeps ONE, chosen
    so that a re-scrape never moves a lead:

      1. core before extended (a core country is worth more geography points);
      2. among equals, the country the CSV already holds for that domain
         (`prefer`) — leads.country is overwritten from this file on every
         ingestion, so a country that flips here flips there;
      3. otherwise the first one seen.
    """
    prefer = prefer or {}
    groups: dict[str, list[LeadRow]] = {}
    for row in rows:
        groups.setdefault(row.domain, []).append(row)
    out: list[LeadRow] = []
    duplicates = 0
    for domain, group in groups.items():
        duplicates += len(group) - 1
        winner = min(
            enumerate(group),
            key=lambda ir: (_tier(ir[1].country), 0 if ir[1].country == prefer.get(domain) else 1, ir[0]),
        )[1]
        for other in group:
            if other is not winner:
                _backfill(winner, other)
        out.append(winner)
    return out, duplicates


def _backfill(winner: LeadRow, other: LeadRow) -> None:
    for attr in ("email", "phone", "address", "description"):
        if not getattr(winner, attr) and getattr(other, attr):
            setattr(winner, attr, getattr(other, attr))


# ---------------------------------------------------------------------------
# Country index -> what to scrape
# ---------------------------------------------------------------------------

@dataclass
class CountryEntry:
    name: str
    slug: str
    shard: str
    mapped: bool
    core: bool
    flagged: tuple | None = None


@dataclass
class Classification:
    included: list
    excluded: list          # (slug, jurisdiction)
    missing_core: list      # core slugs the index did not list (still scraped)


def parse_country_index(html: str) -> list[str]:
    """Every /cro-list/country/{slug} link, in page order, deduplicated. Only
    the shape of the href is used — not the surrounding markup — because the
    markup of this page has never been seen from the dev machine."""
    soup = BeautifulSoup(html, "html.parser")
    slugs: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(BASE_URL, a["href"]))
        if parsed.netloc.lower() not in ("ichgcp.net", "www.ichgcp.net"):
            continue
        m = COUNTRY_LINK_PATH_RE.match(urllib.parse.unquote(parsed.path))
        if not m:
            continue
        slug = m.group(1).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", slug) or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    return slugs


def classify_index(slugs: list[str]) -> Classification:
    included: list[CountryEntry] = []
    excluded: list[tuple[str, str]] = []
    seen_core: set[str] = set()
    core_slugs = set(geography.CORE_COUNTRIES.values())

    def entry_for(slug: str) -> CountryEntry:
        name = geography.canonical_name(slug)
        shard, mapped = geography.shard_of(slug)
        return CountryEntry(name=name, slug=slug, shard=shard, mapped=mapped,
                            core=geography.is_core(name),
                            flagged=geography.flagged_as(slug))

    for slug in slugs:
        jurisdiction = geography.excluded_as(slug)
        if jurisdiction:
            excluded.append((slug, jurisdiction))
            continue
        if slug in core_slugs:
            seen_core.add(slug)
        included.append(entry_for(slug))

    # The 13 core countries are always scraped, even if the index page ever
    # stops linking one of them: discovery may add countries, never lose these.
    missing_core = sorted(core_slugs - seen_core)
    for slug in missing_core:
        included.append(entry_for(slug))

    included.sort(key=lambda e: (not e.core, e.name.lower()))
    return Classification(included=included, excluded=excluded, missing_core=missing_core)


def select_shard(entries: list[CountryEntry], shard: str) -> list[CountryEntry]:
    if shard == "all":
        return list(entries)
    return [e for e in entries if e.shard == shard]


# ---------------------------------------------------------------------------
# Merging a shard into the leads CSV
# ---------------------------------------------------------------------------

def _tier(country: str) -> int:
    """0 = core geography (10 points), 1 = extended (5 points)."""
    return 0 if geography.is_core(country) else 1


def merge_results(existing: list[LeadRow], scraped: list[LeadRow], completed: set[str],
                  failed_profile_urls: set[str]) -> tuple[list[LeadRow], dict]:
    """Fold one shard's rows into the existing CSV rows.

    * A country that was fully scraped this run is refreshed: its old rows are
      dropped and re-added from the new scrape — except a row whose profile page
      failed to load this time, which is kept (a transient failure must not
      delete a lead). A country that was not completed keeps all of its rows.
    * Rows of countries that are excluded today, and rows on an excluded
      country-code domain, are purged whatever shard this is.
    * A domain found in two countries keeps ONE country, chosen by a fixed rule
      so it cannot flip between runs: core beats extended (more geography
      points), the same country refreshes, otherwise the row already there wins.
      This is what Section 9's "last country wins" caveat said would need doing
      the moment the source list and the target list diverged.
    """
    stats = {"purged_excluded": 0, "removed_from_directory": 0}
    kept: list[LeadRow] = []
    dropped_for_refresh: list[LeadRow] = []
    for row in existing:
        if geography.excluded_as(row.country) or geography.domain_nexus(row.domain):
            stats["purged_excluded"] += 1
            continue
        if row.country in completed and row.profile_url not in failed_profile_urls:
            dropped_for_refresh.append(row)
            continue
        kept.append(row)

    by_domain: dict[str, LeadRow] = {r.domain: r for r in kept}
    for row in scraped:
        cur = by_domain.get(row.domain)
        if cur is None:
            by_domain[row.domain] = row
            continue
        if _tier(row.country) < _tier(cur.country) or row.country == cur.country:
            _backfill(row, cur)
            by_domain[row.domain] = row
        else:
            _backfill(cur, row)
    # A row dropped for refresh and put straight back is not a removal. The
    # first real run (europe, 2026-10-06) reported 20 "removed" for 20 rows that
    # were all re-added, with 0 leads actually lost.
    stats["removed_from_directory"] = sum(1 for r in dropped_for_refresh if r.domain not in by_domain)
    return list(by_domain.values()), stats


CSV_FIELDS = [
    "company_name", "domain", "country", "source",
    "email", "phone", "address", "description", "profile_url",
]


def read_csv(path: str) -> list[LeadRow]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [LeadRow(**{k: (rec.get(k) or "") for k in CSV_FIELDS}) for rec in csv.DictReader(f)]


def write_csv(path: str, rows: list[LeadRow]) -> None:
    rows_sorted = sorted(rows, key=lambda r: (r.country, r.company_name.lower()))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow({field_name: getattr(row, field_name) for field_name in CSV_FIELDS})


# ---------------------------------------------------------------------------
# Plan mode and the report
# ---------------------------------------------------------------------------

def seconds_per_request(delay: float) -> float:
    """Measured: the 13-country run (157 requests) took ~487 s at a 2 s delay."""
    return delay + 1.0


def plan_listings(session: requests.Session, entries: list[CountryEntry], delay: float,
                  deadline: float) -> dict[str, int | None]:
    """One request per country: how many local/mid-size profiles does it list?"""
    counts: dict[str, int | None] = {}
    for idx, e in enumerate(entries):
        if time.monotonic() >= deadline:
            counts[e.slug] = None
            continue
        try:
            resp = fetch(session, f"{BASE_URL}/cro-list/country/{e.slug}")
        except AbortRun as exc:
            print(f"[plan] ABORT: {exc}")
            for rest in entries[idx:]:
                counts[rest.slug] = None
            break
        counts[e.slug] = len(extract_local_section_links(resp.text, e.slug)) if resp else None
        if idx < len(entries) - 1:
            time.sleep(delay)
    return counts


def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def write_step_summary(report: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## ICH GCP scrape — shard `{report['shard']}`" + (" (plan only)" if report["plan_only"] else ""), ""]
    lines.append(f"Runtime {report['runtime_seconds']} s · exit {report['exit_code']}")
    lines.append("")
    if report["plan_only"]:
        lines.append(_md_table(
            ["Shard", "Countries", "Listings", "Est. runtime (min)"],
            [[s, d["countries"], d["listings"], d["est_minutes"]] for s, d in report["shards"].items()]))
        lines += ["", f"Excluded in index: {', '.join(f'{s} ({j})' for s, j in report['excluded_in_index']) or 'none'}",
                  "", f"Retained but flagged: {', '.join(f'{c} — {n}' for c, n in report['flagged_included']) or 'none'}",
                  "", f"Unmapped to a region (scraped on the fallback shard): {', '.join(report['unmapped']) or 'none'}"]
    else:
        lines.append(_md_table(
            ["Country", "Tier", "Listed", "Found", "No website", "Fetch failures", "Sanctioned nexus", "New unique", "Complete"],
            [[c["country"], c["tier"], c["listed"], c["found"], c["skipped_no_website"], c["fetch_failures"],
              c["sanctioned_nexus"], c["new_unique"], c["complete"]] for c in report["countries"]]))
        t = report["totals"]
        lines += ["", f"Rows before **{t['rows_before']}** → after **{t['rows_after']}** · new unique leads **{t['new_unique']}** · "
                      f"removed from directory {t['removed_from_directory']} · purged as excluded {t['purged_excluded']} · "
                      f"duplicate domains merged {t['duplicates_merged']}"]
        if report["not_attempted"]:
            lines += ["", f"Not attempted (runtime budget / abort): {', '.join(report['not_attempted'])}"]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"[warn] could not write step summary: {exc}")


def run_plan(session, cls: Classification, shard: str, delay: float, deadline: float,
             started: float, report_path: str) -> int:
    entries = cls.included
    counts = plan_listings(session, entries, delay, deadline)
    per_req = seconds_per_request(delay)
    shards: dict[str, dict] = {}
    rows = []
    for e in entries:
        n = counts.get(e.slug)
        rows.append({"country": e.name, "slug": e.slug, "shard": e.shard, "tier": "core" if e.core else "extended",
                     "listings": n})
        d = shards.setdefault(e.shard, {"countries": 0, "listings": 0, "unknown": 0})
        d["countries"] += 1
        if n is None:
            d["unknown"] += 1
        else:
            d["listings"] += n
    for d in shards.values():
        d["est_minutes"] = round((d["listings"] + d["countries"]) * per_req / 60.0, 1)

    print("\n=== ICH GCP Plan ===")
    print(f"Countries listed on the index: {len(cls.included) - len(cls.missing_core) + len(cls.excluded)}")
    print(f"Excluded as sanctioned jurisdictions ({len(cls.excluded)}): "
          + (", ".join(f"{s} -> {j}" for s, j in cls.excluded) or "none"))
    print(f"Core slugs missing from the index (still scraped): {', '.join(cls.missing_core) or 'none'}")
    print(f"Included: {len(entries)} countries")
    for shard_name in geography.SHARDS:
        d = shards.get(shard_name)
        if not d:
            continue
        print(f"  shard {shard_name}: {d['countries']} countries, {d['listings']} listings, "
              f"~{d['est_minutes']} min at {per_req:.0f} s/request")
        for r in rows:
            if r["shard"] == shard_name:
                print(f"    {r['country']:<32} {r['tier']:<9} {'?' if r['listings'] is None else r['listings']}")
    flagged = [(e.name, e.flagged[1]) for e in entries if e.flagged]
    unmapped = [e.slug for e in entries if not e.mapped]
    print("Retained but flagged (list-based or arms-embargo programmes only):")
    for name, note in flagged:
        print(f"  {name}: {note}")
    print(f"Unmapped to a region -> scraped on shard '{geography.FALLBACK_SHARD}': {', '.join(unmapped) or 'none'}")
    print("=" * 40)

    report = {
        "plan_only": True, "shard": shard, "exit_code": 0,
        "runtime_seconds": round(time.monotonic() - started),
        "index_slugs": [e.slug for e in entries if e.slug not in cls.missing_core] + [s for s, _ in cls.excluded],
        "excluded_in_index": cls.excluded, "missing_core": cls.missing_core,
        "flagged_included": flagged, "unmapped": unmapped,
        "shards": shards, "countries": rows,
    }
    _write_report(report_path, report)
    write_step_summary(report)
    return 0


def _write_report(path: str, report: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"[warn] could not write report {path}: {exc}")


def main() -> int:
    delay = float(os.environ.get("ICHGCP_DELAY_SECONDS", "2"))
    output_path = os.environ.get("OUTPUT_CSV_PATH", os.path.join("data", "ichgcp_leads.csv"))
    report_path = os.environ.get("ICHGCP_REPORT_PATH", "scrape_report.json")
    shard = (os.environ.get("ICHGCP_SHARD") or "all").strip().lower()
    plan_only = os.environ.get("ICHGCP_PLAN_ONLY", "").strip().lower() in ("1", "true", "yes")
    max_runtime = float(os.environ.get("ICHGCP_MAX_RUNTIME_SECONDS", str(DEFAULT_MAX_RUNTIME_SECONDS)))

    if shard != "all" and shard not in geography.SHARDS:
        print(f"[fatal] unknown shard {shard!r}; expected one of {geography.SHARDS + ['all']}")
        return 2

    HEALTH.limit = int(os.environ.get("ICHGCP_BREAKER", str(DEFAULT_BREAKER)))
    HEALTH.streak = 0
    started = time.monotonic()
    deadline = started + max_runtime
    session = make_session()

    # Stage 0: the country index.
    print(f"[index] fetching {BASE_URL}/cro-list")
    try:
        index_resp = fetch(session, f"{BASE_URL}/cro-list")
    except AbortRun as exc:
        print(f"[fatal] country index blocked: {exc}")
        return 2
    if index_resp is None:
        print("[fatal] country index fetch failed — nothing scraped, nothing written")
        return 2
    slugs = parse_country_index(index_resp.text)
    print(f"[index] {len(slugs)} country link(s) found")
    if len(slugs) < MIN_INDEX_COUNTRIES:
        print(f"[fatal] only {len(slugs)} country links on the index (expected at least "
              f"{MIN_INDEX_COUNTRIES}) — the page layout has changed; nothing scraped, nothing written")
        return 2
    cls = classify_index(slugs)

    if plan_only:
        return run_plan(session, cls, shard, delay, deadline, started, report_path)

    targets = select_shard(cls.included, shard)
    print(f"[shard {shard}] {len(targets)} of {len(cls.included)} included countries: "
          + ", ".join(e.name for e in targets))
    for e in targets:
        if not e.mapped:
            print(f"[shard {shard}] note: {e.slug!r} is in no region table — scraped on the "
                  f"{geography.FALLBACK_SHARD!r} shard")
        if e.flagged:
            print(f"[shard {shard}] note: {e.name} is retained but flagged — {e.flagged[1]}")

    existing = read_csv(output_path)
    existing_domains = {r.domain for r in existing}

    all_rows: list[LeadRow] = []
    all_stats: list[CountryStats] = []
    not_attempted: list[str] = []
    aborted = False
    for idx, e in enumerate(targets):
        if aborted or time.monotonic() >= deadline:
            not_attempted.append(e.name)
            continue
        rows, stats = scrape_country(session, e.name, e.slug, delay, deadline)
        all_rows.extend(rows)
        all_stats.append(stats)
        if stats.aborted:
            aborted = True
            continue
        if idx < len(targets) - 1:
            time.sleep(delay)

    deduped_rows, duplicate_count = dedupe_by_domain(all_rows, {r.domain: r.country for r in existing})
    completed = {s.country for s in all_stats if s.complete and not s.country_page_failed}
    failed_urls = {u for s in all_stats for u in s.failed_profile_urls}
    merged, merge_stats = merge_results(existing, deduped_rows, completed, failed_urls)

    # A country-page that failed or a run that scraped nothing must never wipe
    # the CSV: with no completed country the merge only purges excluded rows.
    wrote = bool(all_stats) or merge_stats["purged_excluded"] > 0
    if wrote:
        write_csv(output_path, merged)

    new_by_country: dict[str, int] = {}
    for row in merged:
        if row.domain not in existing_domains:
            new_by_country[row.country] = new_by_country.get(row.country, 0) + 1
    for s in all_stats:
        s.new_unique = new_by_country.get(s.country, 0)
    new_unique_total = sum(new_by_country.values())

    exit_code = 3 if aborted else (1 if (not deduped_rows and not aborted) else 0)
    entry_by_name = {e.name: e for e in targets}
    report = {
        "plan_only": False, "shard": shard, "exit_code": exit_code,
        "runtime_seconds": round(time.monotonic() - started),
        "countries": [{
            "country": s.country,
            "tier": "core" if geography.is_core(s.country) else "extended",
            "listed": s.listed, "found": s.found, "skipped_no_website": s.skipped_no_website,
            "fetch_failures": s.fetch_failures, "sanctioned_nexus": s.sanctioned_nexus,
            "new_unique": s.new_unique, "complete": s.complete,
            "country_page_failed": s.country_page_failed,
            "flagged": bool(entry_by_name[s.country].flagged) if s.country in entry_by_name else False,
        } for s in all_stats],
        "not_attempted": not_attempted,
        "totals": {
            "rows_before": len(existing), "rows_after": len(merged), "new_unique": new_unique_total,
            "removed_from_directory": merge_stats["removed_from_directory"],
            "purged_excluded": merge_stats["purged_excluded"],
            "duplicates_merged": duplicate_count,
            "profiles_found": sum(s.found for s in all_stats),
            "fetch_failures": sum(s.fetch_failures for s in all_stats),
            "blocked_answers": HEALTH.total_blocked,
        },
    }

    print("\n=== ICH GCP Scrape Report ===")
    print(f"Shard: {shard} · {len(all_stats)} of {len(targets)} countries attempted")
    for s in all_stats:
        tier = "core" if geography.is_core(s.country) else "extended"
        if s.country_page_failed:
            print(f"{s.country} [{tier}]: COUNTRY PAGE FETCH FAILED")
        else:
            print(f"{s.country} [{tier}]: {s.found} found, {s.skipped_no_website} skipped (no website), "
                  f"{s.fetch_failures} profile fetch failures, {s.sanctioned_nexus} sanctioned-nexus, "
                  f"{s.new_unique} new unique" + ("" if s.complete else " — INCOMPLETE"))
    print("-" * 40)
    t = report["totals"]
    print(f"Total profiles with a usable website: {t['profiles_found']}")
    print(f"Total skipped (no website): {sum(s.skipped_no_website for s in all_stats)}")
    print(f"Total profile fetch failures: {t['fetch_failures']}")
    print(f"Total dropped as sanctioned nexus: {sum(s.sanctioned_nexus for s in all_stats)}")
    print(f"Duplicate domains merged (within this shard): {duplicate_count}")
    failed_pages = [s.country for s in all_stats if s.country_page_failed]
    print(f"Countries with failed country-page fetch: {len(failed_pages)}"
          + (f" ({', '.join(failed_pages)})" if failed_pages else ""))
    print(f"Countries not attempted (runtime budget / abort): {len(not_attempted)}"
          + (f" ({', '.join(not_attempted)})" if not_attempted else ""))
    print(f"Rows in {output_path}: {t['rows_before']} before -> {t['rows_after']} after")
    print(f"New unique leads this run: {t['new_unique']}")
    print(f"Removed (no longer in the directory): {t['removed_from_directory']} · "
          f"purged as excluded: {t['purged_excluded']}")
    print(f"Runtime: {report['runtime_seconds']} s")
    print("=" * 40)

    _write_report(report_path, report)
    write_step_summary(report)

    if aborted:
        print("\n[fatal] run aborted by the circuit breaker (the site is blocking or rate-limiting). "
              "Completed countries were merged; the rest will be retried on this shard's next run.")
        return 3
    if exit_code == 1:
        print("\n[fatal] zero leads scraped in this shard — failing the run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

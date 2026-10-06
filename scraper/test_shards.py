"""
Offline end-to-end test of the sharded scraper: country index -> classification
-> shard -> scrape -> merge into the CSV, driven by a fake directory served from
memory (ichgcp.net is not reachable from the dev machine).

What this proves: the pipeline's logic and its safety properties -- sanctioned
countries are never requested, a shard merges without disturbing other shards'
rows, a transient failure cannot delete a lead, the circuit breaker stops a
blocked run without wiping the CSV, plan mode writes nothing. What it does NOT
prove: that the real site's markup matches the fake's. The first real Action
run is that test.

Run: python scraper/test_shards.py
"""

import csv
import json
import os
import sys
import tempfile
import time

import geography
import scrape_ichgcp as s

_failed = []


def check(label, condition):
    print(("[PASS] " if condition else "[FAIL] ") + label)
    if not condition:
        _failed.append(label)
    return condition


# --- a fake directory ---------------------------------------------------------
FILLER = ["france", "spain", "italy", "norway", "sweden", "finland", "denmark", "austria", "belgium",
          "portugal", "greece", "ireland", "slovakia", "croatia", "bulgaria"]  # keeps the index above the minimum

# slug -> list of (company name, profile slug)
LISTINGS = {
    "poland": [("Polcro", "polcro"), ("Shared CRO", "shared_cro")],
    "germany": [("Deutsche Studien", "deutsche_studien"), ("Shared CRO", "shared_cro"),
                ("Moskau Pharma", "moskau_pharma")],
    "ukraine": [("Kyiv Trials", "kyiv_trials"), ("Donetsk Trials", "donetsk_trials")],
    "turkey": [("Klinar", "klinar")],
    "iran": [("Tehran Trials", "tehran_trials")],
    "russia": [("Moscow Trials", "moscow_trials")],
    "north_korea": [("Pyongyang Trials", "pyongyang_trials")],
}
for f in FILLER:
    LISTINGS[f] = []

# profile slug -> (website, address). Absent website -> "no website".
PROFILES = {
    "polcro": ("https://polcro.pl", "Warsaw, Poland"),
    "shared_cro": ("https://shared-cro.com", "Berlin, Germany"),
    "deutsche_studien": ("https://deutsche-studien.de", "Munich, Germany"),
    "moskau_pharma": ("https://moskau-pharma.ru", "Berlin, Germany"),  # .ru domain: screened out
    "kyiv_trials": ("https://kyivtrials.ua", "Khreshchatyk 1, Kyiv, Ukraine"),
    "donetsk_trials": ("https://donetsktrials.ua", "Artema 10, Donetsk, Ukraine"),  # occupied: screened out
    "klinar": ("https://klinar-cro.com", "Ankara TURKEY"),
}


def index_html():
    slugs = list(LISTINGS) + ["afghanistan", "cuba", "syria", "venezuela", "myanmar", "nicaragua", "belarus"]
    links = "".join('<li><a href="/cro-list/country/%s">%s</a></li>' % (sl, sl.title()) for sl in slugs)
    links += '<li><a href="https://pv-r.com">Featured sponsor</a></li><li><a href="/cro-list">All</a></li>'
    return "<html><body><ul>%s</ul></body></html>" % links


def country_html(slug):
    items = "".join('<li><a href="/cro-list/country/%s/company/%s">%s</a></li>' % (slug, ps, nm)
                    for nm, ps in LISTINGS[slug])
    return ("<html><body><h2>Local, small- and mid-size Contract Research Organizations in X</h2><ul>%s</ul>"
            "<h2>Global Contract Research Organizations in X</h2><ul>"
            '<li><a href="/cro-list/country/%s/company/iqvia">IQVIA</a></li></ul></body></html>' % (items, slug))


def profile_html(slug):
    website, address = PROFILES.get(slug, ("", ""))
    web = '<p>Web: <a href="%s">%s</a></p>' % (website, website) if website else ""
    return ("<html><body><h1>%s</h1><p>E-mail: <a href=\"mailto:info@%s.example\">x</a></p>%s"
            "<h3>Address:</h3><p>%s</p></body></html>" % (slug, slug, web, address))


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text, self.headers = status, text, {}


class FakeSession:
    """Serves the fake directory and records every URL asked for."""

    def __init__(self, profile_status=None, index_ok=True):
        self.requested = []
        self.profile_status = profile_status or {}   # profile slug -> status code
        self.block_all_profiles = False
        self.index_ok = index_ok
        self.headers = {}

    def get(self, url, timeout=None):
        self.requested.append(url)
        path = url.replace("https://ichgcp.net", "")
        if path == "/cro-list":
            return Resp(200, index_html()) if self.index_ok else Resp(200, "<html><body>moved</body></html>")
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["cro-list", "country"]:
            slug = parts[2]
            return Resp(200, country_html(slug)) if slug in LISTINGS else Resp(404)
        if len(parts) == 5 and parts[3] == "company":
            if self.block_all_profiles:
                return Resp(403)
            code = self.profile_status.get(parts[4], 200)
            return Resp(code, profile_html(parts[4])) if code == 200 else Resp(code)
        return Resp(404)


def write_seed(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=s.CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in s.CSV_FIELDS})


def rows_of(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(session, tmp, seed, env):
    """Run main() against `session` with a seeded CSV. Returns (exit, rows, report)."""
    out = os.path.join(tmp, "leads.csv")
    rep = os.path.join(tmp, "report.json")
    if seed is not None:
        write_seed(out, seed)
    elif os.path.exists(out):
        os.remove(out)
    summary = os.path.join(tmp, "summary.md")
    if os.path.exists(summary):
        os.remove(summary)
    base = {"OUTPUT_CSV_PATH": out, "ICHGCP_REPORT_PATH": rep, "ICHGCP_DELAY_SECONDS": "0",
            "GITHUB_STEP_SUMMARY": summary}
    base.update(env)
    saved = {k: os.environ.get(k) for k in list(base) + ["ICHGCP_SHARD", "ICHGCP_PLAN_ONLY",
                                                         "ICHGCP_MAX_RUNTIME_SECONDS", "ICHGCP_BREAKER"]}
    for k in ["ICHGCP_SHARD", "ICHGCP_PLAN_ONLY", "ICHGCP_MAX_RUNTIME_SECONDS", "ICHGCP_BREAKER"]:
        os.environ.pop(k, None)
    os.environ.update(base)
    s.make_session = lambda: session
    try:
        code = s.main()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    rows = rows_of(out) if os.path.exists(out) else None
    report = json.load(open(rep, encoding="utf-8")) if os.path.exists(rep) else None
    return code, rows, report


time.sleep = lambda *_a, **_k: None  # the real delay is 0 anyway; never really sleep in tests

# Rows already in the CSV from earlier runs (other shards, plus stale Poland data).
SEED = [
    {"company_name": "Klinar", "domain": "klinar-cro.com", "country": "Turkey", "source": "ichgcp",
     "profile_url": "https://ichgcp.net/cro-list/country/turkey/company/klinar"},
    {"company_name": "Old Poland Co", "domain": "oldpoland.pl", "country": "Poland", "source": "ichgcp",
     "profile_url": "https://ichgcp.net/cro-list/country/poland/company/old_poland"},
    {"company_name": "Polcro", "domain": "polcro.pl", "country": "Poland", "source": "ichgcp",
     "email": "keep-me@polcro.pl", "profile_url": "https://ichgcp.net/cro-list/country/poland/company/polcro"},
    {"company_name": "Banned Co", "domain": "banned.example", "country": "Iran", "source": "ichgcp",
     "profile_url": "https://ichgcp.net/cro-list/country/iran/company/banned"},
    {"company_name": "Cuba Domain Co", "domain": "somecro.cu", "country": "Germany", "source": "ichgcp",
     "profile_url": "https://ichgcp.net/cro-list/country/germany/company/cu_co"},
    {"company_name": "Old Germany Co", "domain": "oldgermany.de", "country": "Germany", "source": "ichgcp",
     "profile_url": "https://ichgcp.net/cro-list/country/germany/company/old_germany"},
]

with tempfile.TemporaryDirectory() as tmp:
    # --- A. a normal europe shard -------------------------------------------
    sess = FakeSession()
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "europe"})
    by = {r["domain"]: r for r in rows}
    asked = "\n".join(sess.requested)
    check("A: exit 0", code == 0)
    check("A: no excluded country was ever requested (iran, russia, north_korea, cuba, syria, ...)",
          not any(("/country/" + x) in asked for x in
                  ["iran", "russia", "north_korea", "cuba", "syria", "venezuela", "myanmar", "nicaragua",
                   "belarus", "afghanistan"]))
    check("A: only europe-shard countries were scraped (no turkey page)", "/country/turkey" not in asked)
    check("A: other shards' rows are untouched (Turkey row kept)", "klinar-cro.com" in by)
    check("A: a stale row of a completed country is dropped (Old Poland Co)", "oldpoland.pl" not in by)
    check("A: a row that is still listed is refreshed, not duplicated", sum(1 for r in rows if r["domain"] == "polcro.pl") == 1)
    check("A: a refreshed row takes the page's current data (email re-read from the profile)",
          by["polcro.pl"]["email"] == "info@polcro.example")
    check("A: a CRO listed in Poland (core) and Germany (extended) keeps the CORE country",
          by["shared-cro.com"]["country"] == "Poland")
    check("A: an extended-country lead is written with a derived name", by["deutsche-studien.de"]["country"] == "Germany")
    check("A: a .ru domain under Germany is screened out", "moskau-pharma.ru" not in by)
    check("A: an occupied-territory address under Ukraine is screened out", "donetsktrials.ua" not in by)
    check("A: a normal Ukraine lead is kept", by["kyivtrials.ua"]["country"] == "Ukraine")
    check("A: an existing row for an excluded country is purged (Iran)", "banned.example" not in by)
    check("A: an existing row on an excluded ccTLD is purged (.cu)", "somecro.cu" not in by)
    check("A: report counts the screened rows", sum(c["sanctioned_nexus"] for c in rep["countries"]) == 2)
    germany = [c for c in rep["countries"] if c["country"] == "Germany"][0]
    check("A: report: Germany tier extended, 2 found, 1 new unique (shared-cro.com went to Poland)",
          germany["tier"] == "extended" and germany["found"] == 2 and germany["new_unique"] == 1)
    check("A: report: purged_excluded == 2", rep["totals"]["purged_excluded"] == 2)
    # Old Poland Co and Old Germany Co are gone from the directory; polcro.pl was
    # dropped for refresh and re-added, which is NOT a removal.
    check("A: report: removed_from_directory counts net removals only (2, not 3)",
          rep["totals"]["removed_from_directory"] == 2)
    md = open(os.path.join(tmp, "summary.md"), encoding="utf-8").read()
    check("A: the Action step summary was written, with the per-country table and the totals line",
          "| Country | Tier |" in md and "| Germany | extended |" in md and "new unique leads" in md)

    # --- B. rerun is stable: the country of a shared domain does not flip -----
    code2, rows2, rep2 = run(FakeSession(), tmp, [dict(r) for r in rows], {"ICHGCP_SHARD": "europe"})
    by2 = {r["domain"]: r for r in rows2}
    check("B: second run is stable: same domains", sorted(by2) == sorted(by))
    check("B: second run: shared domain still Poland", by2["shared-cro.com"]["country"] == "Poland")
    check("B: second run: 0 new unique", rep2["totals"]["new_unique"] == 0)

    # --- C. a transient profile failure must not delete a lead ---------------
    sess = FakeSession(profile_status={"polcro": 502})
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "europe"})
    by = {r["domain"]: r for r in rows}
    check("C: the lead whose profile failed to load is KEPT with its old data",
          "polcro.pl" in by and by["polcro.pl"]["email"] == "keep-me@polcro.pl")
    check("C: the failure is counted", [c for c in rep["countries"] if c["country"] == "Poland"][0]["fetch_failures"] == 1)

    # --- D. the circuit breaker ------------------------------------------------
    sess = FakeSession()
    sess.block_all_profiles = True
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "europe", "ICHGCP_BREAKER": "3"})
    check("D: a blocked run exits 3", code == 3)
    by = {r["domain"]: r for r in rows}
    check("D: the CSV is NOT wiped by an aborted run: another shard's row survives", "klinar-cro.com" in by)
    check("D: a listed lead whose profile was blocked keeps its old data", by.get("polcro.pl", {}).get("email") == "keep-me@polcro.pl")
    check("D: the country the breaker cut short (Germany) keeps its old rows", "oldgermany.de" in by)
    profile_asks = [u for u in sess.requested if "/company/" in u]
    check("D: it stopped asking at the breaker (3 blocked profile requests, though country pages "
          "in between succeeded)", len(profile_asks) == 3)
    check("D: later countries are reported as not attempted", len(rep["not_attempted"]) > 0)
    germany_d = [c for c in rep["countries"] if c["country"] == "Germany"][0]
    check("D: Germany is reported incomplete", germany_d["complete"] is False)

    # --- E. runtime budget ------------------------------------------------------
    sess = FakeSession()
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "europe", "ICHGCP_MAX_RUNTIME_SECONDS": "0"})
    check("E: with no runtime budget nothing is attempted and the run reports it", len(rep["not_attempted"]) > 0)
    check("E: existing rows survive (Old Poland Co still present)",
          any(r["domain"] == "oldpoland.pl" for r in rows))
    check("E: it did not request a single profile", not any("/company/" in u for u in sess.requested))

    # --- F. plan mode -----------------------------------------------------------
    sess = FakeSession()
    out = os.path.join(tmp, "leads.csv")
    write_seed(out, SEED)
    before = open(out, "rb").read()
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "all", "ICHGCP_PLAN_ONLY": "1"})
    check("F: plan exits 0", code == 0)
    check("F: plan fetched no profile", not any("/company/" in u for u in sess.requested))
    check("F: plan did not touch the CSV", open(out, "rb").read() == before)
    check("F: plan lists the 7 excluded jurisdictions the index contained",
          {j for _s, j in rep["excluded_in_index"]} >= {"Afghanistan", "Cuba", "Syria", "Venezuela", "Myanmar",
                                                        "Nicaragua", "Belarus", "Iran", "Russia", "North Korea"})
    check("F: plan counts Germany's three listings", [c for c in rep["countries"] if c["slug"] == "germany"][0]["listings"] == 3)
    check("F: plan retains and flags Turkey and Ukraine",
          {"Turkey", "Ukraine"} <= {n for n, _ in rep["flagged_included"]})
    check("F: plan puts every shard it uses in the estimate", set(rep["shards"]) <= set(geography.SHARDS))
    check("F: all 13 core countries are planned even though the fake index lists only some of them",
          {c["country"] for c in rep["countries"]} >= set(geography.CORE_COUNTRIES))
    check("F: the missing core slugs are reported", "mexico" in rep["missing_core"])
    md = open(os.path.join(tmp, "summary.md"), encoding="utf-8").read()
    check("F: the plan's step summary lists the shards and the excluded countries found",
          "(plan only)" in md and "| Shard | Countries |" in md and "Excluded in index:" in md and "afghanistan" in md)

    # --- G. a changed index page fails loudly, writes nothing ---------------------
    sess = FakeSession(index_ok=False)
    out = os.path.join(tmp, "leads.csv")
    write_seed(out, SEED)
    before = open(out, "rb").read()
    code, rows, rep = run(sess, tmp, SEED, {"ICHGCP_SHARD": "europe"})
    check("G: an unusable country index exits 2", code == 2)
    check("G: and leaves the CSV byte-identical", open(out, "rb").read() == before)

    # --- H. a bad shard name -------------------------------------------------------
    code, rows, rep = run(FakeSession(), tmp, SEED, {"ICHGCP_SHARD": "mars"})
    check("H: an unknown shard exits 2", code == 2)

    # --- I. a lead's country never moves on a re-scrape -----------------------------
    # leads.country is overwritten from this CSV on every ingestion, and the live
    # DB and CSV agree on every country today -- so a CRO listed in two countries
    # must stay under the one it already has. Hungary sorts before Poland and
    # Romania, so "first seen wins" would move all three of these.
    saved = (dict(LISTINGS), dict(PROFILES))
    try:
        LISTINGS["hungary"] = [("BiTrial", "bitrial"), ("Magyar Trials", "magyar")]
        LISTINGS["romania"] = [("BiTrial", "bitrial")]
        LISTINGS["poland"] = [("Polcro", "polcro"), ("BiTrial", "bitrial")]
        PROFILES["bitrial"] = ("https://bitrial.example.hu", "Budapest, Hungary")
        PROFILES["magyar"] = ("https://magyar-trials.example.hu", "Budapest, Hungary")

        def seed_for(country):
            return [{"company_name": "BiTrial", "domain": "bitrial.example.hu", "country": country,
                     "source": "ichgcp", "profile_url": "https://ichgcp.net/cro-list/country/x/company/bitrial"}]

        for held in ("Romania", "Poland", "Hungary"):
            code, rows, rep = run(FakeSession(), tmp, seed_for(held), {"ICHGCP_SHARD": "europe"})
            by = {r["domain"]: r for r in rows}
            check("I: listed in Hungary, Poland and Romania, already under %s -> stays %s" % (held, held),
                  by["bitrial.example.hu"]["country"] == held)
            check("I: ...and stays a single row", sum(1 for r in rows if r["domain"] == "bitrial.example.hu") == 1)

        # Core beats extended even when the extended row is the one on file.
        LISTINGS["germany"] = [("BiTrial", "bitrial")]
        code, rows, rep = run(FakeSession(), tmp, seed_for("Germany"), {"ICHGCP_SHARD": "europe"})
        by = {r["domain"]: r for r in rows}
        check("I: on file under Germany (extended), also listed in a core country -> moves to a core country",
              by["bitrial.example.hu"]["country"] in ("Hungary", "Poland", "Romania"))
        # Seeded fresh (no history): the answer is deterministic, core first.
        code, rows, rep = run(FakeSession(), tmp, [], {"ICHGCP_SHARD": "europe"})
        by_a = {r["domain"]: r for r in rows}
        code, rows, rep = run(FakeSession(), tmp, [], {"ICHGCP_SHARD": "europe"})
        by_b = {r["domain"]: r for r in rows}
        check("I: with no history two runs agree on every country",
              {d: r["country"] for d, r in by_a.items()} == {d: r["country"] for d, r in by_b.items()})
    finally:
        LISTINGS.clear(); LISTINGS.update(saved[0])
        PROFILES.clear(); PROFILES.update(saved[1])

print("\n" + ("ALL CHECKS PASSED" if not _failed else "FAILED: %d check(s)" % len(_failed)))
sys.exit(0 if not _failed else 1)

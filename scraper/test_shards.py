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

import contextlib
import csv
import io
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

    def __init__(self, profile_status=None, index_ok=True, index_script=None):
        self.requested = []
        self.profile_status = profile_status or {}   # profile slug -> status code
        self.block_all_profiles = False
        self.index_ok = index_ok
        self.index_script = list(index_script or [])  # status of the 1st, 2nd... /cro-list request; then normal
        self.headers = {}

    def get(self, url, timeout=None):
        self.requested.append(url)
        path = url.replace("https://ichgcp.net", "")
        if path == "/cro-list":
            if self.index_script:
                code = self.index_script.pop(0)
                if code != 200:
                    return Resp(code)
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


SLEEPS = []
time.sleep = lambda secs=0, *_a, **_k: SLEEPS.append(secs)  # never really sleep in tests; remember what was asked


def run_loud(session, tmp, seed, env):
    """run(), also returning what the scraper printed (the ::error annotation) and the step summary."""
    SLEEPS.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, rows, rep = run(session, tmp, seed, env)
    summary = os.path.join(tmp, "summary.md")
    md = open(summary, encoding="utf-8").read() if os.path.exists(summary) else ""
    return code, rows, rep, buf.getvalue(), md


def retry_sleeps():
    return [x for x in SLEEPS if x]   # the per-request delay is 0 in tests; what is left are the index waits

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

    INDEX = "https://ichgcp.net/cro-list"

    # --- J. the first request is retried, spaced; a recovery is an ordinary run --------
    # Refused twice, then answered. ICHGCP_BREAKER=2 would trip on those two refusals
    # if they counted toward the circuit breaker, which guards the profile stage only.
    sess = FakeSession(index_script=[403, 403])
    code, rows, rep, text, md = run_loud(sess, tmp, SEED, {"ICHGCP_SHARD": "europe", "ICHGCP_BREAKER": "2",
                                                          "ICHGCP_INDEX_RETRY_WAITS": "7,11,13"})
    check("J: refused twice then answered: the run completes, exit 0", code == 0)
    check("J: it asked for the index three times", sess.requested.count(INDEX) == 3)
    check("J: it waited the configured time between asks, in order, and no more", retry_sleeps() == [7, 11])
    check("J: the refusals on the index do not count toward the circuit breaker",
          rep["totals"]["blocked_answers"] == 0)
    check("J: a recovered run reports no failure and prints no error annotation",
          rep["failure"] is None and "::error" not in text)
    check("J: the shard was scraped as normal", any(r["domain"] == "polcro.pl" for r in rows))

    # --- K. a block that outlasts the retries ends the run, politely --------------------
    out = os.path.join(tmp, "leads.csv")
    write_seed(out, SEED)
    before = open(out, "rb").read()
    sess = FakeSession(index_script=[403] * 10)
    code, rows, rep, text, md = run_loud(sess, tmp, SEED, {"ICHGCP_SHARD": "europe",
                                                          "ICHGCP_INDEX_RETRY_WAITS": "7,11,13"})
    check("K: an index that stays refused exits 2", code == 2)
    check("K: exactly 4 requests (1 + 3 retries), all to the index and nothing else",
          sess.requested == [INDEX] * 4)
    check("K: the waits were the configured ones, in order, never shorter", retry_sleeps() == [7, 11, 13])
    check("K: the CSV is byte-identical", open(out, "rb").read() == before)
    check("K: the report says blocked", rep["failure"]["reason"] == "blocked" and rep["exit_code"] == 2)
    check("K: the red annotation leads with the reason (this is what the failure e-mail quotes)",
          "::error title=ICH GCP scrape failed - blocked::blocked: " in text and "403" in text)
    check("K: the step summary says blocked", "Failed: `blocked`" in md)

    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[403] * 10), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": ""})
    check("K: with retries switched off it asks once", s_.requested == [INDEX] and code == 2 and retry_sleeps() == [])
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[403] * 10), tmp, SEED, {"ICHGCP_SHARD": "europe"})
    check("K: the default is 4 attempts spaced 5, 15 and 30 minutes apart",
          s_.requested == [INDEX] * 4 and retry_sleeps() == [300, 900, 1800])
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[403] * 10), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": "7,11,13",
                                          "ICHGCP_MAX_RUNTIME_SECONDS": "10"})
    check("K: it will not start a wait that would run past the runtime budget",
          s_.requested == [INDEX, INDEX] and retry_sleeps() == [7] and code == 2)

    # --- L. a failed fetch that is not a refusal is named differently ------------------
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[503] * 10), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": ""})
    check("L: a 503 index is fetch-failed, not blocked", code == 2 and rep["failure"]["reason"] == "fetch-failed"
          and "::error title=ICH GCP scrape failed - fetch-failed::" in text)
    check("L: ...after the fetch's own three quick tries", s_.requested == [INDEX] * 3)
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[503] * 10), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": "5"})
    check("L: a 5xx index is retried after the long wait too", s_.requested == [INDEX] * 6 and 5 in SLEEPS)
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_script=[404] * 10), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": "5,5"})
    check("L: a 404 index is not retried (the page is gone, not busy)",
          s_.requested == [INDEX] and rep["failure"]["reason"] == "fetch-failed" and retry_sleeps() == [])

    # --- M. a changed layout is named, and not retried ---------------------------------
    out = os.path.join(tmp, "leads.csv")
    write_seed(out, SEED)
    before = open(out, "rb").read()
    code, rows, rep, text, md = run_loud(s_ := FakeSession(index_ok=False), tmp, SEED,
                                         {"ICHGCP_SHARD": "europe", "ICHGCP_INDEX_RETRY_WAITS": "7,11,13"})
    check("M: a 200 index with too few country links is page-changed, exit 2",
          code == 2 and rep["failure"]["reason"] == "page-changed")
    check("M: it is not retried (a different page would not fix itself)", s_.requested == [INDEX] and retry_sleeps() == [])
    check("M: annotation and step summary name it",
          "::error title=ICH GCP scrape failed - page-changed::page-changed: " in text and "Failed: `page-changed`" in md)
    check("M: the CSV is byte-identical", open(out, "rb").read() == before)

    # --- N. the circuit breaker is a block too -------------------------------------------
    sess = FakeSession()
    sess.block_all_profiles = True
    code, rows, rep, text, md = run_loud(sess, tmp, SEED, {"ICHGCP_SHARD": "europe", "ICHGCP_BREAKER": "3"})
    check("N: a breaker abort exits 3 and says blocked", code == 3 and rep["failure"]["reason"] == "blocked")
    check("N: annotation and step summary say blocked, and the country table is still there",
          "::error title=ICH GCP scrape failed - blocked::blocked: " in text and "Failed: `blocked`" in md
          and "| Country | Tier |" in md)

    # --- O. a shard that yields nothing ----------------------------------------------------
    saved = dict(LISTINGS)
    try:
        for k in ("poland", "germany", "ukraine"):
            LISTINGS[k] = []
        code, rows, rep, text, md = run_loud(FakeSession(), tmp, SEED, {"ICHGCP_SHARD": "europe"})
    finally:
        LISTINGS.clear(); LISTINGS.update(saved)
    check("O: a shard with no leads exits 1 and says no-leads", code == 1 and rep["failure"]["reason"] == "no-leads")
    check("O: annotation and step summary say no-leads",
          "::error title=ICH GCP scrape failed - no-leads::no-leads: " in text and "Failed: `no-leads`" in md)

    # --- P. a bad shard name is named, and asks for nothing --------------------------------------
    code, rows, rep, text, md = run_loud(s_ := FakeSession(), tmp, SEED, {"ICHGCP_SHARD": "mars"})
    check("P: an unknown shard is config, and made no request",
          code == 2 and rep["failure"]["reason"] == "config" and s_.requested == [])

    # --- Q. a crash names itself (Python's own exit code for it is 1, the same as no-leads) ---------
    real_main = s.main
    buf, err = io.StringIO(), io.StringIO()
    os.environ["GITHUB_STEP_SUMMARY"] = os.path.join(tmp, "crash_summary.md")
    s.main = lambda: 1 // 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = s.run()
    finally:
        s.main = real_main
        del os.environ["GITHUB_STEP_SUMMARY"]
    check("Q: an uncaught exception exits 1 and is announced as crashed",
          code == 1 and "::error title=ICH GCP scrape failed - crashed::crashed: ZeroDivisionError" in buf.getvalue())
    check("Q: the traceback still goes to the log", "ZeroDivisionError" in err.getvalue())
    check("Q: and the step summary says crashed",
          "Failed: `crashed`" in open(os.path.join(tmp, "crash_summary.md"), encoding="utf-8").read())

    # --- R. workflow-command escaping and the reason list ---------------------------------------------
    check("R: a message's %, CR and LF are escaped", s._gh_escape("a%b\r\nc") == "a%25b%0D%0Ac")
    check("R: a property also escapes ':' and ','", s._gh_escape("a: b, c", True) == "a%3A b%2C c")
    check("R: every reason the script can give is one of FAILURE_REASONS",
          set(s.FAILURE_REASONS) == {"blocked", "page-changed", "no-leads", "fetch-failed", "config", "crashed"})

print("\n" + ("ALL CHECKS PASSED" if not _failed else "FAILED: %d check(s)" % len(_failed)))
sys.exit(0 if not _failed else 1)

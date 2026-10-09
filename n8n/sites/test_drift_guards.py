"""Checks that build_workflow.py's spec-drift guards actually FIRE.

A guard that has never been seen to fail is not a guard. Workflow 1b was shipped
on 2026-10-09 with 28 build-time refusals and no suite proving any of them, while
every other stage had one; Section 9's Workflow 1b entry asserts several of these
refusals in prose ("the build refuses an alias that targets anything else", "the
build refuses batching on that node", "the build refuses a Code node that reads
investigator fields"). This is that proof.

    python n8n/sites/test_drift_guards.py

Exits non-zero on the first guard that failed to fire.

Each case copies n8n/sites/, scraper/ and postgres/migrations/ to a scratch tree,
introduces exactly ONE divergence, and asserts the generator refuses to run. Two
kinds of guard are worth the most here, because their first symptom in production
is not a wrong answer:

  * spend -- the lookup model, its `max_uses`, the worst-case price the batch
    query sizes against, and the agreement between Section 4's cap and migration
    019's seed. The cap is the only thing standing between a re-queueing bug and
    a card limit (Section 4).
  * reach -- the country alias table may only point at countries Section 12
    includes, so a sanctioned jurisdiction cannot be harvested; and no Code node
    may read investigator data or call the site-detail endpoint (the standing
    rule: never use personal investigator e-mails from registry data).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MASTER_REF = os.path.join(REPO, "NovaScout_MasterRef.md")
SCRAPER = os.path.join(REPO, "scraper")
MIGRATIONS = os.path.join(REPO, "postgres", "migrations")

PASSED = []
FAILED = []


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


def setup(mutate_doc=None, mutate_file=None, mutate_migration=None):
    """A scratch copy of everything the generator reads, with one mutation.

    The tree mirrors the repo's layout: the generator finds scraper/geography.py
    and migration 019 by walking up from its own file, so n8n/sites/ has to sit
    two levels below the scratch root, not one."""
    tmp = tempfile.mkdtemp(prefix="novascout-sites-drift-")
    shutil.copytree(HERE, os.path.join(tmp, "n8n", "sites"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SCRAPER, os.path.join(tmp, "scraper"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(MIGRATIONS, os.path.join(tmp, "postgres", "migrations"))

    doc = read(MASTER_REF)
    if mutate_doc:
        new = mutate_doc(doc)
        assert new != doc, "doc mutation did not change the doc"
        doc = new
    write(os.path.join(tmp, "MasterRef.md"), doc)

    if mutate_file:
        name, fn = mutate_file
        p = os.path.join(tmp, "n8n", "sites", name)
        src = read(p)
        new = fn(src)
        assert new != src, "mutation did not change %s" % name
        write(p, new)

    if mutate_migration:
        p = os.path.join(tmp, "postgres", "migrations", "019_sites_and_smos.sql")
        src = read(p)
        new = mutate_migration(src)
        assert new != src, "mutation did not change migration 019"
        write(p, new)

    return tmp


def run_build(tmp):
    env = dict(os.environ)
    env["NOVASCOUT_MASTER_REF"] = os.path.join(tmp, "MasterRef.md")
    env["SITES_OUT"] = os.path.join(tmp, "out", "trialsites.json")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    p = subprocess.run([sys.executable, os.path.join(tmp, "n8n", "sites", "build_workflow.py")],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    return p.returncode, p.stderr.decode("utf-8", "replace")


def case(label, expect_in, **mutations):
    tmp = setup(**mutations)
    try:
        rc, err = run_build(tmp)
        if rc == 0:
            FAILED.append((label, "build SUCCEEDED but should have refused"))
        elif expect_in and expect_in.lower() not in err.lower():
            FAILED.append((label, "refused, but message lacked %r:\n%s" % (expect_in, err[-400:])))
        else:
            PASSED.append(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- the unmodified build must succeed, or every case below is meaningless ---
def baseline():
    tmp = setup()
    try:
        rc, err = run_build(tmp)
        if rc != 0:
            FAILED.append(("BASELINE: the unmodified build succeeds", err[-600:]))
        else:
            out = json.loads(read(os.path.join(tmp, "out", "trialsites.json")))
            if out.get("id") != "trialsites0001":
                FAILED.append(("BASELINE: the unmodified build succeeds",
                               "built %r, not trialsites0001" % out.get("id")))
            else:
                PASSED.append("BASELINE: the unmodified build succeeds")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


baseline()


# --- Section 12: which countries can be reached at all -----------------------
#
# The alias table maps Trialsites' spellings onto Section 12's countries. An
# alias pointing anywhere else is how a sanctioned jurisdiction would get
# harvested, so it is the single most important refusal in this file.
case("an alias targeting a country Section 12 does not include -> refuses",
     "targets a country that is not included",
     mutate_file=("code_countries.js", lambda s: s.replace(
         "'czechia': 'Czech Republic',", "'rossiya': 'Russia',", 1)))

case("an alias targeting a plausible but unlisted country -> refuses",
     "targets a country that is not included",
     mutate_file=("code_countries.js", lambda s: s.replace(
         "'czechia': 'Czech Republic',", "'czechia': 'Czechoslovakia',", 1)))

case("the alias table renamed out of reach -> refuses rather than harvesting unaliased",
     "TRIALSITES_ALIASES could not be read",
     mutate_file=("code_countries.js", lambda s: s.replace("TRIALSITES_ALIASES", "COUNTRY_ALIASES")))

# An EXTENDED row, so the core list is untouched and this guard is the one that
# fires rather than the Geographies cross-check above it.
case("a country in both Section 12's clock table and the scraper's EXCLUDED list -> refuses",
     "in the clock table and in the scraper's EXCLUDED list",
     mutate_doc=lambda d: d.replace("| Armenia | UTC+4:00 | Sat-Sun | |",
                                    "| Russia | UTC+3:00 | Sat-Sun | |", 1))

case("Section 12's Geographies line and the clock table's core rows disagree -> refuses",
     "differ from Section 12's Geographies line",
     mutate_doc=lambda d: d.replace("**Geographies:**", "**Geographies:** Narnia,", 1))

case("the clock table's heading gone -> refuses rather than harvesting every country",
     "'Business-hours clocks' heading not found",
     mutate_doc=lambda d: d.replace("**Business-hours clocks", "**Business hours, clocks", 1))

# code_countries.js is prepended to two different nodes, so it must be a pure
# library: a read of $input in it would silently mean two different things.
case("code_countries.js reading $input -> refuses",
     "must not read $( or $input",
     mutate_file=("code_countries.js", lambda s: s + "\nconst _x = $input.all();\n"))

case("code_countries.js reading another node -> refuses",
     "must not read $( or $input",
     mutate_file=("code_countries.js", lambda s: s + "\nconst _x = $('Load Site State').first();\n"))


# --- Section 4: the money ----------------------------------------------------
#
# Section 4 chose Haiku 5.5 by measuring it against Sonnet 5.5 on the same 21
# candidates. Naming a different model in the doc must not quietly re-point the
# highest-volume paid call in the project.
case("Section 4 naming a different lookup model -> refuses",
     "re-measure before changing it",
     mutate_doc=lambda d: d.replace("| Model | `claude-haiku-5-5` |",
                                    "| Model | `claude-sonnet-5-5` |", 1))

case("max_uses raised out of range -> refuses (it is the cost lever)",
     "is outside 1-5",
     mutate_doc=lambda d: d.replace("`web_search_20250305`, `max_uses` 3",
                                    "`web_search_20250305`, `max_uses` 9", 1))

# ~90% of a lookup is the web-search fee. If a price rises far enough that one
# lookup can cost more than LOOKUP_WORST_CASE_USD, the batch query -- which sizes
# a batch as budget_left / worst case -- would start sizing past the cap.
case("a search fee that makes one lookup cost more than the batch query assumes -> refuses",
     "the batch query would size batches past the monthly cap",
     mutate_doc=lambda d: d.replace("| Web search | $0.01 per search |",
                                    "| Web search | $0.05 per search |", 1))

case("Section 4's cap and migration 019's seed disagreeing -> refuses",
     "monthly cap and migration 019's seed disagree",
     mutate_doc=lambda d: d.replace("`settings.site_lookup_monthly_cap_usd`, seeded $20",
                                    "`settings.site_lookup_monthly_cap_usd`, seeded $50", 1))

case("migration 019's seed moved instead -> still refuses",
     "monthly cap and migration 019's seed disagree",
     mutate_migration=lambda s: s.replace("VALUES ('site_lookup_monthly_cap_usd', '20')",
                                          "VALUES ('site_lookup_monthly_cap_usd', '200')", 1))

# A retry on the paid node is a second lookup the cap's batch sizing never
# counted, for a question a second call has no reason to answer differently.
case("a retry on the Claude node -> refuses",
     "must not retry",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"name": "Claude Website Lookup",', '"name": "Claude Website Lookup", "retryOnFail": True,', 1)))


# --- the standing rule: no investigator data, ever ---------------------------
case("a Code node reading an investigator field -> refuses",
     "never use personal investigator emails",
     mutate_file=("code_select_candidates.js", lambda s: s.replace(
         "const MAX_NEW = __MAX_NEW__;",
         "const MAX_NEW = __MAX_NEW__;\nconst inv = function (r) { return r.investigator_name; };", 1)))

case("a Code node reading the investigators array -> refuses",
     "never use personal investigator emails",
     mutate_file=("code_verify_site.js", lambda s: s.replace(
         "const PRICES = __LOOKUP_PRICES__;",
         "const PRICES = __LOOKUP_PRICES__;\nconst who = function (r) { return r.investigators; };", 1)))

case("a Code node calling the site-detail endpoint -> refuses",
     "never calls /sites/{id}",
     mutate_file=("code_harvest_requests.js", lambda s: s.replace(
         "const TIERS = ['A', 'B'];",
         "const TIERS = ['A', 'B'];\nconst detail = function (id) { return '/sites/' + id; };", 1)))


# --- Section 7: the schedule cannot depend on one moment ---------------------
case("an hours-based trigger -> refuses",
     "Section 7 -- use minutes",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"field": "minutes", "minutesInterval": TICK_MINUTES',
         '"field": "hours", "hoursInterval": 6', 1)))

case("a trigger pinned to one hour of the day -> refuses",
     "pins triggerAtHour",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"field": "minutes", "minutesInterval": TICK_MINUTES',
         '"field": "minutes", "minutesInterval": TICK_MINUTES, "triggerAtHour": 23', 1)))


# --- Trialsites is read one request at a time --------------------------------
#
# The 2026-10-09 dry run's first finding: the HTTP node's own batching only
# sleeps between dispatches and never awaits a response, so 8 requests timed out
# at 60s in a harvest's first four minutes.
case("the HTTP node's batching on the Trialsites fetch -> refuses",
     "never awaits a",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '''            "options": {
                "timeout": 60000,
                "pagination":''',
         '''            "options": {
                "batching": {"batch": {"batchSize": 5}},
                "timeout": 60000,
                "pagination":''', 1)))

case("the Trialsites fetch moved outside the pacing loop -> refuses",
     "must run inside Loop Over Requests",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"Pace Requests": {"main": [[{"node": "Loop Over Requests"',
         '"Pace Requests": {"main": [[{"node": "Select Candidates"', 1)))


# --- Section 8: what the lead INSERT may touch -------------------------------
case("the lead INSERT writing a column Section 8 does not define -> refuses",
     "which Section 8 does not define",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "INSERT INTO leads (domain, company_name, country, source, status)",
         "INSERT INTO leads (domain, company_name, country, source, status, site_tier)", 1)))

# An existing lead -- a CRO already drafted or sent -- must never be touched by
# a site resolving to the same domain. 'duplicate' is the outcome for that.
case("the lead INSERT upserting instead of DO NOTHING -> refuses",
     "must never be touched",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         "ON CONFLICT (domain) DO NOTHING", "ON CONFLICT (domain) DO UPDATE SET status = 'ingested'", 1)))


# --- wiring: every $json.X a node reads is emitted by the node above it ------
case("the Trialsites fetch reading a country field its upstream does not emit -> refuses",
     "does not emit",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '{"name": "country", "value": "={{ $json.trialsites_country }}"},',
         '{"name": "country", "value": "={{ $json.trialsites_country_name }}"},', 1)))

case("the Claude node reading a request field Build Lookup Request does not emit -> refuses",
     "does not emit",
     mutate_file=("build_workflow.py", lambda s: s.replace(
         '"jsonBody": "={{ JSON.stringify($json.request) }}",',
         '"jsonBody": "={{ JSON.stringify($json.lookup_request) }}",', 1)))

case("a Code node reading a node that is not in this workflow -> refuses",
     "which is not a node here",
     mutate_file=("code_verify_site.js", lambda s: s.replace(
         "const NOT_OWN_SITE = [",
         "const _b = $('Load Draft Batch').all();\nconst NOT_OWN_SITE = [", 1)))

case("Build Lookup Request reading a candidate field the batch query does not select -> refuses",
     "which Load Lookup Batch does not emit",
     mutate_file=("code_build_lookup.js", lambda s: s.replace(
         "c.canonical_name", "c.canonical_name + c.site_tier", 1)))


# --- a placeholder left unsubstituted ----------------------------------------
case("a placeholder the generator no longer substitutes -> refuses",
     "still has placeholders",
     mutate_file=("code_select_candidates.js", lambda s: s.replace("__MAX_NEW__", "__MAX_NEW_CANDIDATES__", 1)))


# --- the dry run cannot report success on an empty harvest -------------------
#
# Not a build guard, but the same kind of claim: dryrun_sites.py printed "26/26
# checks passed" on 2026-10-10 having harvested nothing, because a stale
# `n8n execute` held the task broker's port 5690 and every check after the
# harvest is satisfiable by an empty database. harvest_ran() is the decision
# that now stops it, and these are the shapes it has to reject. Proving it here
# costs nothing; proving it by running the harness costs a 13-minute harvest.
sys.path.insert(0, HERE)
import dryrun_sites  # noqa: E402  (blanks mail variables on import, by design)

for _label, _args, _want in (
        ("a harvest whose execution failed is not a harvest", (False, {"chosen": 200}), False),
        ("no site_harvest_log row at all is not a harvest", (True, None), False),
        ("a harvest that chose nothing is not a harvest", (True, {"chosen": 0}), False),
        ("a successful harvest that chose candidates is one", (True, {"chosen": 200}), True)):
    _got = dryrun_sites.harvest_ran(*_args)
    if _got is _want:
        PASSED.append(_label)
    else:
        FAILED.append((_label, "harvest_ran%r returned %r, wanted %r" % (_args, _got, _want)))


# ---------------------------------------------------------------------------

for _label in PASSED:
    print("  ok   %s" % _label)
for _label, _why in FAILED:
    print("  FAIL %s\n         %s" % (_label, _why))
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)

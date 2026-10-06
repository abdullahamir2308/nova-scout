"""
Offline tests for scraper/geography.py -- the country policy.

Covers (1) the sanctions matcher against realistic name variants and the
false-positive traps that would silently drop real leads, (2) the row-level
address/domain screen, (3) shard coverage, and (4) drift between this file and
NovaScout_MasterRef.md Section 12, which is the source of truth.

Run: python scraper/test_geography.py
"""

import os
import re
import sys

import geography as g

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "NovaScout_MasterRef.md")
)

_failed = []


def check(label, condition):
    print(("[PASS] " if condition else "[FAIL] ") + label)
    if not condition:
        _failed.append(label)
    return condition


def doc_text():
    with open(MASTER_REF, encoding="utf-8") as fh:
        return fh.read()


def doc_list(label_regex):
    m = re.search(label_regex + r"\s*([^\n]+)", doc_text())
    if not m:
        return None
    raw = m.group(m.lastindex).strip().rstrip(".")
    return [x.strip() for x in raw.split(",") if x.strip()]


# --- 1. the sanctions matcher ------------------------------------------------
MUST_EXCLUDE = {
    "Afghanistan": ["afghanistan", "Afghanistan", "islamic_emirate_of_afghanistan"],
    "Belarus": ["belarus", "Belarus", "byelorussia"],
    "Cuba": ["cuba", "Cuba"],
    "Iran": ["iran", "iran_islamic_republic_of", "islamic_republic_of_iran", "Iran (Islamic Republic of)"],
    "Myanmar": ["myanmar", "burma", "myanmar_burma", "burma_myanmar", "Burma (Myanmar)"],
    "Nicaragua": ["nicaragua"],
    "North Korea": ["north_korea", "korea_north", "dprk", "korea_democratic_people_s_republic_of",
                    "democratic_people_s_republic_of_korea", "Korea, Democratic People's Republic of"],
    "Russia": ["russia", "russian_federation", "Russian Federation"],
    "Syria": ["syria", "syrian_arab_republic", "Syrian Arab Republic"],
    "Venezuela": ["venezuela", "venezuela_bolivarian_republic_of", "Venezuela (Bolivarian Republic of)"],
}
for canonical, variants in MUST_EXCLUDE.items():
    for v in variants:
        check("excluded: %-45r -> %s" % (v, canonical), g.excluded_as(v) == canonical)

for territory in ("crimea", "sevastopol", "donetsk", "luhansk", "lugansk", "Donetsk People's Republic"):
    check("occupied territory excluded as a 'country': %r" % territory,
          g.excluded_as(territory) == "Ukraine (occupied territory)")

# Names that share a word with a sanctioned one, or that a careless matcher
# would catch. Every one of these must be INCLUDED.
MUST_INCLUDE = [
    "korea", "south_korea", "korea_south", "republic_of_korea", "guinea", "papua_new_guinea", "equatorial_guinea",
    "niger", "nigeria", "mali", "ukraine", "turkey", "turkiye", "united_arab_emirates", "czech_republic", "india",
    "iraq", "ireland", "lebanon", "sudan", "south_sudan", "libya", "yemen", "zimbabwe", "china", "hong_kong",
    "united_states", "united_kingdom", "germany", "uzbekistan", "kazakhstan", "serbia", "bosnia_and_herzegovina",
    "moldova", "tunisia", "guatemala", "haiti", "ethiopia", "somalia", "democratic_republic_of_the_congo",
]
for v in MUST_INCLUDE:
    check("not excluded: %r" % v, g.excluded_as(v) is None)

# --- core countries are never excluded, and keep their locked names ----------
check("13 core countries", len(g.CORE_COUNTRIES) == 13)
for name, slug in g.CORE_COUNTRIES.items():
    check("core %-15s not excluded" % name, g.excluded_as(slug) is None and g.excluded_as(name) is None)
    check("core %-15s slug -> locked display name" % name, g.canonical_name(slug) == name)
    check("core %-15s is in a region table" % name, g.shard_of(slug)[1] is True)
check("a non-core slug gets a stable derived name", g.canonical_name("united_states") == "United States")
check("'of'/'and' stay lower-case in derived names", g.canonical_name("bosnia_and_herzegovina") == "Bosnia and Herzegovina")
check("the 13 core names are never produced for another country",
      all(g.canonical_name(s) == n for n, s in g.CORE_COUNTRIES.items()))

# --- 2. the row-level screen --------------------------------------------------
check("address ending in Iran is flagged", g.address_nexus("12 Valiasr St, Tehran, Iran") == "Iran")
check("address ending in Russian Federation is flagged", g.address_nexus("Tverskaya 1, Moscow, Russian Federation") == "Russia")
check("address ending in Syria is flagged", g.address_nexus("Mazzeh, Damascus, Syria") == "Syria")
check("address ending in North Korea is flagged", g.address_nexus("Taedonggang District, Pyongyang, North Korea") == "North Korea")
check("occupied territory anywhere in the address is flagged", g.address_nexus("Lenina 5, Simferopol, Crimea, Ukraine") == "crimea")
check("Donetsk is flagged", g.address_nexus("Artema 10, Donetsk, Ukraine") == "donetsk")
# The trap: Buenos Aires and Mexico City have streets named after these
# countries. A match anywhere in the address would drop real leads.
check("'Calle Cuba' in Buenos Aires is NOT flagged", g.address_nexus("Cuba 2200, C1428 Buenos Aires, Argentina") is None)
check("'Av. Venezuela' in Mexico City is NOT flagged", g.address_nexus("Av. Venezuela 55, Centro, Mexico City, Mexico") is None)
check("'Nicaragua' street in Buenos Aires is NOT flagged", g.address_nexus("Nicaragua 4800, Palermo, Buenos Aires, Argentina") is None)
check("the Klinar fixture address is NOT flagged",
      g.address_nexus("Mustafa Kemal Mah 2127. Sk. 42 /3 Çankaya / ANKARA TURKEY") is None)
check("South Korea is NOT flagged", g.address_nexus("Gangnam-gu, Seoul, South Korea") is None)
check("Kyiv is NOT flagged", g.address_nexus("Khreshchatyk 1, Kyiv, Ukraine") is None)
check("empty address is NOT flagged", g.address_nexus("") is None)
check("excluded ccTLD flagged: acme.ir", g.domain_nexus("acme.ir") == "ir")
check("excluded ccTLD flagged: clinic.com.ru", g.domain_nexus("clinic.com.ru") == "ru")
check("excluded ccTLD flagged: cro.by", g.domain_nexus("cro.by") == "by")
check("ordinary domain not flagged: cro.com", g.domain_nexus("cro.com") is None)
check("ordinary domain not flagged: cro.in", g.domain_nexus("cro.in") is None)
check("ordinary domain not flagged: cro.ua", g.domain_nexus("cro.ua") is None)

# --- 3. shards ---------------------------------------------------------------
check("five weekday shards", g.SHARDS == ["europe", "asia-pacific", "americas", "middle-east", "africa"])
check("every shard has members in the table",
      all(any(v == s for v in g._REGION_BY_NAME.values()) for s in g.SHARDS))
check("the fallback shard is a real shard", g.FALLBACK_SHARD in g.SHARDS)
check("an unknown country is routed to the fallback and reported unmapped",
      g.shard_of("atlantis") == (g.FALLBACK_SHARD, False))
for slug, shard in [("poland", "europe"), ("india", "asia-pacific"), ("mexico", "americas"),
                    ("turkey", "middle-east"), ("south_africa", "africa"), ("united_states", "americas"),
                    ("united_kingdom", "europe"), ("china", "asia-pacific"), ("egypt", "middle-east")]:
    check("%s -> shard %s" % (slug, shard), g.shard_of(slug) == (shard, True))
check("no excluded jurisdiction is in a region table (they are never scraped)",
      all(g.excluded_as(n) is None for n in g._REGION_BY_NAME))

# --- 3b. the Action's weekday table matches SHARDS ---------------------------
WORKFLOW = os.path.join(HERE, "..", ".github", "workflows", "scrape-ichgcp.yml")
with open(WORKFLOW, encoding="utf-8") as fh:
    wf = fh.read()
crons = re.findall(r'- cron: "0 6 \* \* (\d)"', wf)
check("workflow: one cron per weekday Mon..Fri", crons == ["1", "2", "3", "4", "5"])
case_map = dict((int(d), sh) for d, sh in re.findall(r'"0 6 \* \* (\d)"\) shard=([a-z\-]+) ;;', wf))
check("workflow: weekday -> shard table is exactly SHARDS in order",
      [case_map.get(d) for d in range(1, 6)] == g.SHARDS)
opts = re.search(r"options: \[([^\]]+)\]", wf)
check("workflow: manual-dispatch options are SHARDS + all",
      opts is not None and [o.strip() for o in opts.group(1).split(",")] == g.SHARDS + ["all"])
check("workflow: job timeout is under GitHub's 6-hour limit", 0 < int(re.search(r"timeout-minutes: (\d+)", wf).group(1)) < 360)
budget = int(re.search(r'ICHGCP_MAX_RUNTIME_SECONDS: "(\d+)"', wf).group(1))
check("workflow: the scraper's runtime budget leaves room under the job timeout",
      budget + 20 * 60 <= int(re.search(r"timeout-minutes: (\d+)", wf).group(1)) * 60)
check("workflow: the push is retried after a rebase", "git pull --rebase" in wf and "attempt" in wf)
check("workflow: tests gate the scrape", wf.index("scraper/test_geography.py") < wf.index("python scraper/scrape_ichgcp.py"))

# --- 4. drift against the Master Ref (Section 12) ----------------------------
doc_core = doc_list(r"\*\*Geographies:\*\*")
check("doc: '**Geographies:**' line present", doc_core is not None)
check("doc: Geographies == scraper CORE_COUNTRIES (order included)",
      doc_core == list(g.CORE_COUNTRIES.keys()))

doc_excluded = doc_list(r"\*\*Excluded jurisdictions[^*\n]*\*\*")
check("doc: '**Excluded jurisdictions ...:**' line present", doc_excluded is not None)
check("doc: Excluded jurisdictions == scraper EXCLUDED", sorted(doc_excluded or []) == sorted(g.EXCLUDED))
check("doc: Excluded jurisdictions are in the same order as the scraper", (doc_excluded or []) == list(g.EXCLUDED))

m = re.search(r"\*\*Extended geographies \((\d+) points\):\*\*", doc_text())
check("doc: '**Extended geographies (N points):**' line present", m is not None)
check("doc: the check date in the Excluded line is the scraper's CHECKED_ON",
      re.search(r"\*\*Excluded jurisdictions \(checked " + re.escape(g.CHECKED_ON) + r"\):\*\*", doc_text()) is not None)

print("\n" + ("ALL CHECKS PASSED" if not _failed else "FAILED: %d check(s)" % len(_failed)))
sys.exit(0 if not _failed else 1)

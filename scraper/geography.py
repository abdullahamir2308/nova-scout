"""
Country policy for the ICH GCP scraper: which countries are scraped at all,
which weekday shard each belongs to, and which jurisdictions are excluded.

This module is the scraper's copy of three decisions that live in
NovaScout_MasterRef.md Section 12 (the doc is the source of truth;
test_geography.py fails if this file drifts from it):

  CORE_COUNTRIES        "Geographies:"                 -> 10 geography points
  EXCLUDED              "Excluded jurisdictions ...:"  -> never scraped, 0 points
  every other country   "Extended geographies (N points):" -> 5 points

Nothing here touches the network. ichgcp.net is not reachable from the dev
machine (403), so the country list itself is discovered live by the scraper;
this module only classifies what discovery finds.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Core geographies -- Section 12 "Geographies". display name -> ichgcp slug.
# Verified, irregular -- do not derive from the display name. The display name
# is what lands in leads.country and what scoring/drafting/send match on, so it
# must never change (a discovered "Türkiye" must NOT replace "Turkey").
# ---------------------------------------------------------------------------
CORE_COUNTRIES = {
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
_CORE_NAME_BY_SLUG = {slug: name for name, slug in CORE_COUNTRIES.items()}

# ---------------------------------------------------------------------------
# Excluded jurisdictions -- Section 12 "Excluded jurisdictions".
#
# The rule (written out in the doc): a country is excluded when OFAC, the EU or
# the UK maintains, against that country as such, (a) a comprehensive or
# territory-wide embargo, or (b) measures aimed at its government, ruling
# authority, state-owned entities or whole economic sectors -- beyond asset
# freezes and travel bans on named individuals, and beyond arms embargoes.
# Countries whose only measures are list-based or an arms embargo are retained
# and flagged (FLAGGED below) so a human sees them before they are scraped.
#
# CHECKED_ON is the date the three authorities' lists were read. Sources:
#   OFAC   https://ofac.treasury.gov/sanctions-programs-and-country-information
#   EU     https://www.sanctionsmap.eu/api/v1/regime  (55 regimes, read raw)
#   UK     https://www.gov.uk/government/collections/uk-sanctions-regimes-under-the-sanctions-act
# ---------------------------------------------------------------------------
CHECKED_ON = "2026-10-06"

# canonical name -> (token sets that identify it, one-line evidence)
# A name matches when ALL tokens of ANY one set appear as whole words in the
# normalised slug/name, so "iran_islamic_republic_of", "Iran (Islamic Republic
# of)" and "iran" all match, while "guinea" never matches "papua_new_guinea"
# for a rule that does not name it.
EXCLUDED: dict[str, tuple[list[set[str]], str]] = {
    "Afghanistan": ([{"afghanistan"}],
                    "Taliban de facto government: OFAC Afghanistan-Related programme, "
                    "EU Taliban regime (UN 1988), UK Afghanistan regime; Afghan central-bank assets frozen"),
    "Belarus": ([{"belarus"}, {"byelorussia"}],
                "OFAC Belarus programme; EU regime with sectoral trade and financial measures; UK Belarus regime"),
    "Cuba": ([{"cuba"}],
             "OFAC comprehensive embargo (Cuban Assets Control Regulations)"),
    "Iran": ([{"iran"}],
             "OFAC comprehensive sanctions; EU non-proliferation and military-support regimes; UK Iran regimes"),
    "Myanmar": ([{"myanmar"}, {"burma"}],
                "OFAC Burma-Related programme (military, state banks and state enterprises); "
                "EU Myanmar/Burma regime; UK Myanmar regime"),
    "Nicaragua": ([{"nicaragua"}],
                  "OFAC Nicaragua-related programme (regime institutions, state mining enterprise, gold sector); "
                  "EU and UK Nicaragua regimes"),
    "North Korea": ([{"north", "korea"}, {"dprk"}, {"democratic", "korea"}],
                    "OFAC comprehensive sanctions; EU and UK DPRK regimes; UN Security Council"),
    "Russia": ([{"russia"}, {"russian"}],
               "OFAC Ukraine-/Russia-related and Russian Harmful Foreign Activities programmes; EU sectoral "
               "regime; UK Russia regime"),
    "Syria": ([{"syria"}, {"syrian"}],
              "Being unwound, not lifted: US programme terminated 2025 and Caesar Act repealed 2025-12, but the EU "
              "regime still lists measures aimed at the Government of Syria and the UK regime remains; excluded "
              "until the residual state-level measures are confirmed gone"),
    "Venezuela": ([{"venezuela"}],
                  "OFAC Venezuela-Related programme (the Government of Venezuela is blocked; eased by general "
                  "licences in 2026, not lifted); EU and UK Venezuela regimes"),
}

# Occupied or annexed territory is excluded as territory, not as Ukraine. If the
# directory lists any of these as a "country" they are excluded outright; on a
# Ukraine page they are screened by address (see address_nexus).
EXCLUDED_TERRITORY_TOKENS = [
    {"crimea"}, {"sevastopol"}, {"donetsk"}, {"luhansk"}, {"lugansk"},
]
OCCUPIED_ADDRESS_TERMS = [
    "crimea", "sevastopol", "donetsk", "luhansk", "lugansk", "kherson", "zaporizhzhia", "zaporozhye",
]

# Country-code domains of excluded jurisdictions. A company listed under a
# retained country but with one of these domains is treated as located there.
EXCLUDED_TLDS = {
    "af", "by", "cu", "ir", "kp", "mm", "ni", "ru", "su", "sy", "ve", "xn--p1ai",
}

# Retained but flagged: a country-named programme exists at one of the three
# authorities, but it is list-based (named persons) or an arms embargo, so the
# country is not a sanctioned jurisdiction as such. Listed so the plan report
# shows a human exactly which of these the directory actually contains.
FLAGGED: list[tuple[list[set[str]], str, str]] = [
    ([{"bosnia"}], "Bosnia and Herzegovina", "EU/UK Bosnia regimes and OFAC Balkans: listed individuals only"),
    ([{"burundi"}], "Burundi", "EU regime: listed individuals only"),
    ([{"central", "african"}], "Central African Republic", "UN arms embargo; EU/UK/OFAC listings"),
    ([{"china"}, {"hong", "kong"}], "China / Hong Kong",
     "OFAC Hong Kong-Related and CMIC lists; UK/EU arms embargoes: not a jurisdiction-level measure"),
    ([{"congo"}], "Congo (DRC)", "UN arms embargo; EU/UK/OFAC listings"),
    ([{"ethiopia"}], "Ethiopia", "OFAC EO 14046: listings only"),
    ([{"guatemala"}], "Guatemala", "EU regime: listed individuals only"),
    ([{"guinea"}], "Guinea / Guinea-Bissau", "EU/UK regimes: listed individuals only"),
    ([{"haiti"}], "Haiti", "UN/EU/UK regimes: gang and facilitator listings, arms embargo"),
    ([{"iraq"}], "Iraq", "legacy UN/EU/UK regimes: arms embargo on non-government, listings"),
    ([{"lebanon"}], "Lebanon", "OFAC/EU/UK: listed individuals only"),
    ([{"libya"}], "Libya", "UN arms embargo and vessel/asset listings; EU/UK/OFAC"),
    ([{"mali"}], "Mali", "OFAC/EU/UK: listed individuals (junta officials) only"),
    ([{"moldova"}], "Moldova", "EU regime: listed individuals only"),
    ([{"niger"}], "Niger", "EU regime: listed individuals only"),
    ([{"somalia"}], "Somalia", "UN arms embargo and charcoal ban; listings"),
    ([{"south", "sudan"}], "South Sudan", "UN arms embargo; listings"),
    ([{"sudan"}], "Sudan", "UN/EU/UK/OFAC: listings and arms embargo (conflict parties)"),
    ([{"tunisia"}], "Tunisia", "EU misappropriation regime: listed individuals only"),
    ([{"turkey"}, {"turkiye"}], "Turkey",
     "EU/UK 'unauthorised drilling' regime: conduct-named, two listed individuals"),
    ([{"ukraine"}], "Ukraine",
     "EU misappropriation regime (individuals); occupied regions are excluded by address, not by country"),
    ([{"yemen"}], "Yemen", "UN arms embargo; EU/UK/OFAC listings"),
    ([{"zimbabwe"}], "Zimbabwe", "EU/UK: arms embargo and listings; OFAC programme terminated 2024"),
]

# ---------------------------------------------------------------------------
# Weekday shards -- one region per weekday, so each Action run stays small,
# polite and well inside GitHub's 6-hour job limit. The order is the weekday
# order (Mon..Fri); the workflow maps a cron weekday to a shard by index.
# ---------------------------------------------------------------------------
SHARDS = ["europe", "asia-pacific", "americas", "middle-east", "africa"]
FALLBACK_SHARD = "africa"  # anything the table below does not name

_REGION_MEMBERS = {
    "europe": """
        albania andorra armenia austria azerbaijan belgium bosnia_and_herzegovina bosnia_herzegovina bosnia
        bulgaria croatia cyprus czech_republic czechia denmark estonia finland france georgia germany greece
        hungary iceland ireland italy kosovo latvia liechtenstein lithuania luxembourg malta moldova monaco
        montenegro netherlands the_netherlands holland north_macedonia macedonia norway poland portugal romania
        serbia slovakia slovak_republic slovenia spain sweden switzerland ukraine united_kingdom uk great_britain
        england scotland wales northern_ireland san_marino
    """,
    "asia-pacific": """
        india pakistan bangladesh sri_lanka nepal bhutan maldives china hong_kong macau macao taiwan japan
        south_korea korea_south republic_of_korea korea mongolia vietnam viet_nam thailand malaysia singapore
        indonesia philippines the_philippines cambodia laos lao brunei timor_leste east_timor kazakhstan
        uzbekistan kyrgyzstan kyrgyz_republic tajikistan turkmenistan australia new_zealand fiji
        papua_new_guinea
    """,
    "americas": """
        united_states usa us united_states_of_america canada mexico guatemala belize honduras el_salvador
        costa_rica panama colombia ecuador peru bolivia chile argentina uruguay paraguay brazil dominican_republic
        jamaica trinidad_and_tobago trinidad_tobago saint_lucia puerto_rico haiti bahamas barbados guyana suriname
        bermuda cayman_islands
    """,
    "middle-east": """
        turkey turkiye israel jordan lebanon iraq saudi_arabia united_arab_emirates uae kuwait qatar bahrain oman
        yemen palestine egypt libya tunisia algeria morocco
    """,
    "africa": """
        south_africa nigeria kenya ghana ethiopia tanzania uganda zambia zimbabwe mozambique malawi rwanda senegal
        cameroon ivory_coast cote_d_ivoire botswana namibia angola mauritius madagascar sudan south_sudan somalia
        democratic_republic_of_the_congo dr_congo congo gabon benin togo burkina_faso mali niger guinea sierra_leone
        liberia gambia burundi djibouti equatorial_guinea mauritania
    """,
}
_REGION_BY_NAME = {}
for _region, _names in _REGION_MEMBERS.items():
    for _n in _names.split():
        _REGION_BY_NAME[_n] = _region


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def norm(value: str) -> str:
    """'Iran (Islamic Republic of)' / 'iran_islamic_republic_of' -> 'iran_islamic_republic_of'."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return text.strip("_")


def _tokens(value: str) -> set[str]:
    return set(norm(value).split("_")) - {""}


def _matches(tokens: set[str], rules: list[set[str]]) -> bool:
    return any(rule <= tokens for rule in rules)


def slug_to_name(slug: str) -> str:
    """ichgcp slug -> a stable display name. Used only for non-core countries."""
    words = [w for w in norm(slug).split("_") if w]
    small = {"and", "of", "the"}
    return " ".join(w if (i and w in small) else w.capitalize() for i, w in enumerate(words))


# Slugs whose derived name reads wrongly. Found on the first real plan run
# (2026-10-06, 138 index links); the rest derive cleanly. A name is written to
# leads.country and never changes afterwards, so these are fixed before the
# first shard, not after.
DISPLAY_NAMES = {
    "usa": "USA",
    "dr_congo": "DR Congo",
    "cote_d_ivoire": "Cote d'Ivoire",
    "bosnia_herzegovina": "Bosnia and Herzegovina",
    "trinidad_tobago": "Trinidad and Tobago",
}


def canonical_name(slug: str) -> str:
    """The name written to leads.country. Core countries keep their locked
    spelling; everything else is derived from the slug (never from link text,
    which can change), so a country's name is the same on every run."""
    return _CORE_NAME_BY_SLUG.get(slug) or DISPLAY_NAMES.get(slug) or slug_to_name(slug)


def is_core(display_name: str) -> bool:
    return display_name in CORE_COUNTRIES


def excluded_as(value: str) -> str | None:
    """The excluded jurisdiction `value` (a slug, a display name, an alias) is,
    or None. Territories resolve to 'Ukraine (occupied territory)'."""
    tokens = _tokens(value)
    for name, (rules, _evidence) in EXCLUDED.items():
        if _matches(tokens, rules):
            return name
    if _matches(tokens, EXCLUDED_TERRITORY_TOKENS):
        return "Ukraine (occupied territory)"
    return None


def flagged_as(value: str) -> tuple[str, str] | None:
    tokens = _tokens(value)
    # "guinea" is a whole word in Papua New Guinea and Equatorial Guinea, which
    # have no programme of their own (the first real plan flagged both).
    if "guinea" in tokens and tokens & {"papua", "equatorial"}:
        return None
    for rules, label, note in FLAGGED:
        if _matches(tokens, rules):
            return label, note
    return None


def shard_of(slug_or_name: str) -> tuple[str, bool]:
    """(shard, mapped). An unmapped country goes to FALLBACK_SHARD and the
    caller logs it, so a country the table has never heard of is still scraped."""
    key = norm(slug_or_name)
    region = _REGION_BY_NAME.get(key)
    if region:
        return region, True
    return FALLBACK_SHARD, False


# ---------------------------------------------------------------------------
# Row-level screens (second line of control, after the country filter)
# ---------------------------------------------------------------------------

def domain_nexus(domain: str) -> str | None:
    """An excluded country-code domain, e.g. 'acme.ir' -> 'ir'."""
    tld = (domain or "").strip().lower().rsplit(".", 1)[-1]
    return tld if tld in EXCLUDED_TLDS else None


def address_nexus(address: str) -> str | None:
    """Does a profile address place the company in an excluded jurisdiction?

    Two checks, deliberately different. Occupied-territory names are matched
    anywhere in the address (they are not street names). Excluded COUNTRY names
    are matched only at the END of the address, where a country goes: Buenos
    Aires and Mexico City both have streets called Cuba, Venezuela and
    Nicaragua, and a match anywhere would silently drop real Argentine and
    Mexican leads.
    """
    text = norm(address)
    if not text:
        return None
    words = text.split("_")
    for term in OCCUPIED_ADDRESS_TERMS:
        if term in words:
            return term
    tail = set(words[-3:])  # "... Tehran Iran", "... Korea Democratic Peoples Republic Of"
    for name, (rules, _evidence) in EXCLUDED.items():
        if _matches(tail, rules):
            return name
    return None

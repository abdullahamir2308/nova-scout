"""Assemble the Workflow 6 (Send & Track) workflows from the tested Code-node sources.

    n8n/workflows/send.json           send0001       cron -> guard -> claim -> SMTP -> log
    n8n/workflows/mailbox-watch.json  mailwatch0001  IMAP Sent mirror + IMAP reply/opt-out watch
    n8n/workflows/follow-ups.json     followup0001   cron -> follow-up drafts / mark lost
    n8n/workflows/imap-health.json    imaphealth0001 cron -> IMAP checker -> missed mail? -> alert operator

Same contract as the enrichment, scoring, contacts and drafting generators: the
.js files are read verbatim and embedded, so the JS that was tested standalone
is byte-identical to the JS that ships.

Workflow 6 is the first stage that acts outside this machine -- its failure mode
is an email in a stranger's inbox that cannot be recalled -- so the spec is
parsed out of the Master Ref and asserted here rather than retyped:

    Section 5   warm-up table                 -> WARMUP in code_decide.js
    Section 5   opt-out sentence (VERBATIM)   -> code_decide / code_classify_reply / code_followup
    Section 5   URL cap                       -> code_decide.js
    Section 5+9 opt-out keywords              -> code_classify_reply.js (both statements must agree)
    Section 9   follow-up delay and maximum   -> follow-up SQL + code_decide.js + code_followup.js
    Section 3   drafting model and effort     -> the follow-up composition request
    Skill §8    follow-up lengths             -> code_followup.js + code_followup_assemble.js + the prompt
    Skill §3    claim rules                   -> Assemble Follow-Up embeds drafting's code_assemble.js rules
    Section 12  business-hours clocks         -> COUNTRY_CLOCKS (a country with no clock cannot ship)
    Section 12  geographies + index snapshot  -> every country the scraper includes has a clock
    Section 6   LinkedIn is manual            -> every send query filters channel='email'
    Section 8   outreach_log / drafts columns -> what the INSERTs may touch
    Section 8   lead status values (LOCKED)   -> every status the SQL writes
    compose     GENERIC_TIMEZONE              -> SENDER_UTC_OFFSET_MIN

Plus wiring guards, for the class of bug Workflow 4 found the hard way: a
Postgres node replaces its input items, and n8n resolves a field nobody emitted
to an empty string without erroring. Every field a node reads from its upstream
is checked, at build time, against what that upstream actually produces.

When a guard fires, fix the code to match the doc -- never the other way round.

Dry-run variants (only when SENDTRACK_VARIANTS_OUT is set) are generated from
the SAME node list with exactly three kinds of change -- credentials pointed at
a scratch database and an SMTP sink, the Config clock/pacing values, and the
schedule trigger removed -- and the build proves that is all that differs.
"""
import copy
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("SENDTRACK_OUT", os.path.join(HERE, "..", "workflows"))
MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md")
)
COMPOSE = os.environ.get("NOVASCOUT_COMPOSE", os.path.join(HERE, "..", "..", "docker-compose.yml"))
# Drafting skill v3 section 8: follow-ups are composed under the first touch's
# rules. The skill's section 8 numbers are parsed from here, and the rule
# functions are drafting's own code_assemble.js, embedded verbatim.
SKILL = os.environ.get(
    "NOVASCOUT_DRAFTING_SKILL", os.path.join(HERE, "..", "..", "NovaScout_DraftingSkill.md")
)
DRAFTING_DIR = os.environ.get("NOVASCOUT_DRAFTING_DIR", os.path.join(HERE, "..", "drafting"))
ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
VARIANTS_OUT = os.environ.get("SENDTRACK_VARIANTS_OUT", "")
DRYRUN_NOW = os.environ.get("SENDTRACK_DRYRUN_NOW", "")
FIXTURES = os.environ.get("SENDTRACK_FIXTURES", "")


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}
SMTP_CRED = {"smtp": {"id": "novascoutSmtp01", "name": "SMTP - outreach mailbox"}}
IMAP_CRED = {"imap": {"id": "novascoutImap01", "name": "IMAP - outreach mailbox"}}
PG_DRY = {"postgres": {"id": "novascoutPgDry01", "name": "Postgres - novascout_dryrun (scratch)"}}
SMTP_DRY = {"smtp": {"id": "novascoutSmtpDry01", "name": "SMTP - dry-run sink (never delivers)"}}


# ---------------------------------------------------------------------------
# Settings. Environment first, then the repo's .env -- only these keys are read
# from it; the same file holds the mailbox password, which this build has no
# business touching. The n8n SMTP/IMAP credentials hold that, not the workflow.
# ---------------------------------------------------------------------------

_BUILD_SETTINGS = (
    "NOVASCOUT_SENDER_NAME",
    "NOVASCOUT_SENDER_TITLE",
    "NOVASCOUT_SENDER_PHONE",
    "NOVASCOUT_MAILBOX_ADDRESS",
)
# NOVASCOUT_OPERATOR_EMAIL is deliberately not a build setting. Mailbox Watch
# reads the operator's notification address at runtime from the settings table
# (migration 008, written by sync_settings.py), so it never lands in a committed
# workflow -- and the guard after SHIPPED refuses any literal address other than
# the sender's own From.


def _env_file_values(path, keys):
    vals = {}
    if not os.path.isfile(path):
        return vals
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in keys:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            vals[k] = v
    return vals


_FILE_SETTINGS = _env_file_values(ENV_FILE, _BUILD_SETTINGS)


def _setting(key):
    v = os.environ.get(key, "").strip()
    if v:
        return v
    return _FILE_SETTINGS.get(key, "").strip()


EMAIL_RE = re.compile(r"^[^\s@<>(),;:\"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

SENDER_NAME = _setting("NOVASCOUT_SENDER_NAME")
SENDER_TITLE = _setting("NOVASCOUT_SENDER_TITLE")
SENDER_PHONE = _setting("NOVASCOUT_SENDER_PHONE")
MAILBOX = _setting("NOVASCOUT_MAILBOX_ADDRESS")

if not SENDER_NAME:
    raise AssertionError(
        "NOVASCOUT_SENDER_NAME is not set -- not in the environment and not in %s.\n"
        "It is the From name on every send and part of the signature the send path\n"
        "checks each body against. There is deliberately no default." % os.path.abspath(ENV_FILE)
    )
if not EMAIL_RE.match(MAILBOX):
    raise AssertionError(
        "NOVASCOUT_MAILBOX_ADDRESS is %r -- it must be the outreach mailbox address "
        "(Section 5). It is the From address, and its domain decides what counts as an "
        "external send for the warm-up ceiling." % MAILBOX
    )
OWN_DOMAIN = MAILBOX.rsplit("@", 1)[1].lower()

# Section 5: "Signature: name, one line of title, phone." Composed exactly as the
# drafting build composes it -- the send path refuses any body not carrying it.
SIGNATURE = "\n".join([p for p in [SENDER_NAME, SENDER_TITLE, SENDER_PHONE] if p])
FROM_HEADER = '"%s" <%s>' % (SENDER_NAME.replace('"', ""), MAILBOX)


# ---------------------------------------------------------------------------
# Spec parsers
# ---------------------------------------------------------------------------

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _number(word, what):
    w = word.lower()
    if w in NUMBER_WORDS:
        return NUMBER_WORDS[w]
    if w.isdigit():
        return int(w)
    raise AssertionError("%s %r in %s is not a number" % (what, word, MASTER_REF))


def load_warmup(doc):
    """Section 5's warm-up table: [(week, max/day)], the last row open-ended."""
    rows = re.findall(r"^\| Week (\d+)(\+?) \| (\d+)", doc, re.M)
    if not rows:
        raise AssertionError("warm-up table not found in %s -- expected '| Week N | M |' rows" % MASTER_REF)
    weeks = [int(w) for w, _, _ in rows]
    if weeks != list(range(1, len(rows) + 1)):
        raise AssertionError("warm-up table weeks are not 1..N in order: %r" % weeks)
    if rows[-1][1] != "+" or any(p for _, p, _ in rows[:-1]):
        raise AssertionError("warm-up table: only the last row may be open-ended ('Week N+'): %r" % rows)
    return [[int(w), int(n)] for w, _, n in rows]


def load_opt_out(doc):
    m = re.search(r"Use a plain sentence:\s*\*\"(.+?)\"\*", doc)
    if not m:
        raise AssertionError(
            "opt-out sentence not found in %s -- expected 'Use a plain sentence: *\"...\"*'" % MASTER_REF)
    return m.group(1)


def load_url_cap(doc):
    m = re.search(r"Maximum (\w+) plain URL", doc)
    if not m:
        raise AssertionError("URL cap not found in Section 5 of %s" % MASTER_REF)
    return _number(m.group(1), "URL cap")


def load_opt_out_keywords(doc):
    """Stated twice -- Section 5's compliance note and Section 9's opt-out line.
    Both must agree, or the spec itself is ambiguous and the build stops."""
    m5 = re.search(r"must detect ((?:\"[a-z]+\",?\s*)+)", doc)
    if not m5:
        raise AssertionError(
            "Section 5 opt-out keywords not found in %s -- expected 'must detect \"no\", ...'" % MASTER_REF)
    m9 = re.search(r"reply matching ([a-z]+(?:/[a-z]+)+)", doc)
    if not m9:
        raise AssertionError(
            "Section 9 opt-out keywords not found in %s -- expected 'reply matching a/b/c'" % MASTER_REF)
    k5 = re.findall(r'"([a-z]+)"', m5.group(1))
    k9 = m9.group(1).split("/")
    if k5 != k9:
        raise AssertionError(
            "the Master Ref states two different opt-out keyword lists -- resolve the doc first:\n"
            "  Section 5: %r\n  Section 9: %r" % (k5, k9))
    return k5


def load_follow_up(doc):
    m = re.search(r"if no reply after (\d+) days", doc)
    if not m:
        raise AssertionError("follow-up delay not found in %s -- expected 'if no reply after N days'" % MASTER_REF)
    n = re.search(r"Maximum (\w+) follow-ups", doc)
    if not n:
        raise AssertionError("follow-up maximum not found in %s -- expected 'Maximum N follow-ups'" % MASTER_REF)
    return int(m.group(1)), _number(n.group(1), "follow-up maximum")


def load_followup_rules(skill):
    """Drafting skill v3 section 8: follow-up #1 is N-M words; #2, the short
    final note, at most K. Parsed, so the prompt and the check say the skill's
    numbers."""
    m1 = re.search(r"\*\*Follow-up #1\.\*\* (\d+)[–-](\d+) words", skill)
    if not m1:
        raise AssertionError("follow-up #1 length not found in %s -- expected '**Follow-up #1.** N-M words'" % SKILL)
    m2 = re.search(r"\*\*Follow-up #2[^*]*\*\* At most (\d+) words", skill)
    if not m2:
        raise AssertionError("follow-up #2 length not found in %s -- expected '**Follow-up #2 ...** At most N words'"
                             % SKILL)
    if not re.search(r"Add one angle or benefit from §4 that the first email did not use", skill):
        raise AssertionError("follow-up #1's rule 'Add one angle or benefit from §4 that the first email did not use' "
                             "not found in %s -- the new-claim check depends on it" % SKILL)
    return int(m1.group(1)), int(m1.group(2)), int(m2.group(1))


def load_skill_banned_adjectives(skill):
    m = re.search(r"No banned adjectives \(([^)]+)\)", skill)
    if not m:
        raise AssertionError("banned adjectives not found in %s -- expected 'No banned adjectives (a, b, ...)'" % SKILL)
    return [a.strip().lower() for a in m.group(1).split(",") if a.strip()]


def load_drafting_model(doc):
    """Section 3: the drafting model and effort. The follow-ups are composed by
    the drafting model (skill section 8), with the same request parameters."""
    m = re.search(r"\*\*Drafting model: `([a-z0-9-]+)`\*\*, effort `(low|medium|high|xhigh|max)`", doc)
    if not m:
        raise AssertionError("drafting model not found in Section 3 of %s -- expected '**Drafting model: `id`**, "
                             "effort `level`'" % MASTER_REF)
    return m.group(1), m.group(2)


def load_therapeutic_areas(doc):
    """Section 9's locked taxonomy. Duplicated from the drafting and scoring
    generators on purpose: the stages must be able to fail independently."""
    anchor = re.search(r"\*\*Therapeutic area taxonomy[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError("taxonomy section not found in %s" % MASTER_REF)
    block = re.search(r"\n```\n(.*?)\n```", doc[anchor.end():], re.S)
    if not block:
        raise AssertionError("no fenced list follows the taxonomy heading in %s" % MASTER_REF)
    return [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]


def load_geographies(doc):
    m = re.search(r"\*\*Geographies:\*\*\s*([^\n]+)", doc)
    if not m:
        raise AssertionError("Section 12 geographies not found in %s" % MASTER_REF)
    return [g.strip().rstrip(".") for g in m.group(1).split(",") if g.strip()]


# Section 12, "Business-hours clocks": one row per included country. The row is
# the clock -- offset and weekend both -- so a wrong number in either place is a
# build failure, not a send that quietly lands out of hours.
_DAY_INDEX = {"Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6}


def load_country_clocks(doc):
    anchor = re.search(r"\*\*Business-hours clocks[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError(
            "Section 12 'Business-hours clocks' heading not found in %s" % MASTER_REF)
    table = re.search(
        r"\n\| Country \| Standard offset \| Weekend \| Tier \|\r?\n\|[-|]+\|\r?\n(.*?)(?:\r?\n\r?\n|\Z)",
        doc[anchor.end():], re.S)
    if not table:
        raise AssertionError(
            "no clock table follows the 'Business-hours clocks' heading in %s" % MASTER_REF)
    clocks, core = {}, []
    for line in table.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError("clock row %r does not have 4 cells in %s" % (line, MASTER_REF))
        name, offset, weekend, tier = cells
        mo = re.match(r"^UTC([+-])(\d{1,2}):(\d{2})$", offset)
        if not mo:
            raise AssertionError("clock row %r has an unreadable offset %r" % (name, offset))
        minutes = int(mo.group(2)) * 60 + int(mo.group(3))
        if mo.group(1) == "-":
            minutes = -minutes
        days = []
        for d in weekend.split("-"):
            if d not in _DAY_INDEX:
                raise AssertionError("clock row %r has an unreadable weekend day %r" % (name, d))
            days.append(_DAY_INDEX[d])
        if not days:
            raise AssertionError("clock row %r has no weekend" % name)
        if name in clocks:
            raise AssertionError("clock row %r is listed twice in %s" % (name, MASTER_REF))
        clocks[name] = {"utc_offset_min": minutes, "weekend": days}
        if tier == "core":
            core.append(name)
    return clocks, core


def load_index_countries():
    """The /cro-list index as the scraper last read it (scraper/index_slugs.json),
    classified by the scraper's own geography module, as the display names that
    land in leads.country. Committed precisely so this question -- does every
    country the pipeline can ingest have a clock? -- is answerable offline:
    ichgcp.net 403s the dev machine, so nothing here may ask it."""
    root = os.path.abspath(os.environ.get(
        "NOVASCOUT_SCRAPER_ROOT", os.path.join(HERE, "..", "..")))
    if root not in sys.path:
        sys.path.insert(0, root)
    from scraper import geography as geo

    with io.open(os.path.join(root, "scraper", "index_slugs.json"), encoding="utf-8") as fh:
        snap = json.load(fh)
    included = [s for s in snap["slugs"] if not geo.excluded_as(s)]
    return sorted({geo.canonical_name(s) for s in included}), snap


def load_lead_statuses(doc):
    m = re.search(r"\*\*Lead status values \(LOCKED\):\*\*\s*\n`([^`]+)`", doc)
    if not m:
        raise AssertionError("Section 8 lead status values not found in %s" % MASTER_REF)
    return re.findall(r"[a-z_]+", m.group(1))


def _table_block(doc, table):
    m = re.search(r"\n%s\r?\n(.*?)\r?\n\r?\n" % re.escape(table), doc, re.S)
    if not m:
        raise AssertionError("%s table not found in Section 8 of %s" % (table, MASTER_REF))
    return m.group(1)


def load_columns(doc, table, must="lead_id"):
    cols = []
    for chunk in re.sub(r"\([^)]*\)", "", _table_block(doc, table)).replace("\n", " ").split(","):
        name = chunk.strip().split(" ")[0].strip().replace("[]", "")
        if name:
            cols.append(name)
    if must not in cols:
        raise AssertionError("%s column parse looks wrong in %s: %r" % (table, MASTER_REF, cols))
    return cols


def load_health_problems(doc):
    """Section 8: mailbox_health.problems[] (a|b|c) -- the IMAP health check's problem codes."""
    m = re.search(r"problems\[\] \(([a-z|-]+)\)", _table_block(doc, "mailbox_health"))
    if not m:
        raise AssertionError("mailbox_health problem codes not found in Section 8 of %s -- expected "
                             "'problems[] (a|b|...)'" % MASTER_REF)
    return m.group(1).split("|")


def load_health_interval(doc):
    m = re.search(r"health check runs every (\d+) minutes", doc)
    if not m:
        raise AssertionError("IMAP health check interval not found in Section 9 of %s -- expected "
                             "'health check runs every N minutes'" % MASTER_REF)
    return int(m.group(1))


# docker-compose's GENERIC_TIMEZONE is the operator's clock -- the IMAP
# pre-flight counted sender-days on it. Only zones with no DST are accepted: a
# fixed offset is exact for those and wrong twice a year for anything else.
_FIXED_ZONES = {"Asia/Karachi": 300, "Asia/Kolkata": 330, "Asia/Dubai": 240, "UTC": 0, "Etc/UTC": 0}


def load_sender_offset(compose_text):
    m = re.search(r"GENERIC_TIMEZONE:\s*([\w/]+)", compose_text)
    if not m:
        raise AssertionError("GENERIC_TIMEZONE not found in %s" % COMPOSE)
    zone = m.group(1)
    if zone not in _FIXED_ZONES:
        raise AssertionError(
            "GENERIC_TIMEZONE is %s, which this build has no fixed offset for. The warm-up "
            "ceiling counts the sender's day on it -- add the zone (only if it has no DST)." % zone)
    return zone, _FIXED_ZONES[zone]


DOC = _read(MASTER_REF)
WARMUP = load_warmup(DOC)
OPT_OUT = load_opt_out(DOC)
MAX_URLS = load_url_cap(DOC)
OPT_OUT_KEYWORDS = load_opt_out_keywords(DOC)
FOLLOW_UP_DAYS, MAX_FOLLOW_UPS = load_follow_up(DOC)
GEOGRAPHIES = load_geographies(DOC)
COUNTRY_CLOCKS, CLOCK_CORE = load_country_clocks(DOC)
INDEX_COUNTRIES, INDEX_SNAPSHOT = load_index_countries()
LEAD_STATUSES = load_lead_statuses(DOC)
OUTREACH_COLUMNS = load_columns(DOC, "outreach_log")
DRAFTS_COLUMNS = load_columns(DOC, "drafts")
HEALTH_COLUMNS = load_columns(DOC, "mailbox_health", must="check_name")
HEALTH_PROBLEMS = load_health_problems(DOC)
HEALTH_INTERVAL_MIN = load_health_interval(DOC)
COMPOSE_TEXT = _read(COMPOSE)
SENDER_ZONE, SENDER_OFFSET = load_sender_offset(COMPOSE_TEXT)
SKILL_DOC = _read(SKILL)
FU1_MIN, FU1_MAX, FU2_MAX = load_followup_rules(SKILL_DOC)
SKILL_BANNED = load_skill_banned_adjectives(SKILL_DOC)
DRAFT_MODEL, DRAFT_EFFORT = load_drafting_model(DOC)
THERAPEUTIC_AREAS = load_therapeutic_areas(DOC)


# ---------------------------------------------------------------------------
# Drift guards against the Code-node sources
# ---------------------------------------------------------------------------

def _js_string(src, name, where):
    m = re.search(r'const %s = "(.*?)";' % re.escape(name), src)
    assert m, "%s not found in %s" % (name, where)
    return m.group(1)


def _js_int(src, name, where):
    m = re.search(r"const %s = (-?\d+);" % re.escape(name), src)
    assert m, "%s not found in %s" % (name, where)
    return int(m.group(1))


def _js_block(src, name, where):
    m = re.search(r"const %s = [\[{](.*?)\n[\]}];" % re.escape(name), src, re.S)
    assert m, "%s block not found in %s" % (name, where)
    return m.group(1)


def _assert_decide_matches_spec():
    src = js("code_decide.js")

    found = [[int(a), int(b)] for a, b in re.findall(r"\[(\d+),\s*(\d+)\]", _js_block(src, "WARMUP", "code_decide.js"))]
    assert found == WARMUP, (
        "warm-up schedule drifted between Section 5 and code_decide.js:\n"
        "  doc: %r\n  js:  %r" % (WARMUP, found))

    assert _js_string(src, "OPT_OUT", "code_decide.js") == OPT_OUT, (
        "the opt-out sentence in code_decide.js is not Section 5's, VERBATIM. The send path "
        "refuses any body without it, so a mismatch refuses every draft -- or passes a "
        "paraphrase:\n  doc: %r\n  js:  %r" % (OPT_OUT, _js_string(src, "OPT_OUT", "code_decide.js")))

    found = _js_int(src, "MAX_URLS", "code_decide.js")
    assert found == MAX_URLS, (
        "URL cap drifted between Section 5 and code_decide.js:\n  doc: %d\n  js:  %d" % (MAX_URLS, found))

    found = _js_int(src, "MAX_FOLLOW_UPS", "code_decide.js")
    assert found == MAX_FOLLOW_UPS, (
        "follow-up maximum drifted between Section 9 and code_decide.js:\n  doc: %d\n  js:  %d"
        % (MAX_FOLLOW_UPS, found))

    found = _js_int(src, "SENDER_UTC_OFFSET_MIN", "code_decide.js")
    assert found == SENDER_OFFSET, (
        "the sender clock drifted: docker-compose says %s (%+d min), code_decide.js says %+d min. "
        "The warm-up ceiling counts the sender's day." % (SENDER_ZONE, SENDER_OFFSET, found))

    # --- Section 12's business-hours clocks, row for row ---------------------
    #
    # Not just the keys any more. Since 2026-10-07 the table holds every country
    # the scraper includes, and the doc's table carries each one's offset and
    # weekend, so both numbers are checked here: a country with no clock can
    # never be placed in business hours, and a country with the WRONG clock
    # sends out of hours without anything failing.
    block = _js_block(src, "COUNTRY_CLOCKS", "code_decide.js")
    js_clocks = {}
    for mo in re.finditer(
        r"""^\s*(?:'([^']+)'|"([^"]+)"):\s*\{\s*utc_offset_min:\s*(-?\d+),\s*weekend:\s*\[([0-9,\s]+)\]\s*\},?\s*$""",
        block, re.M,
    ):
        name = mo.group(1) if mo.group(1) is not None else mo.group(2)
        js_clocks[name] = {
            "utc_offset_min": int(mo.group(3)),
            "weekend": [int(d) for d in mo.group(4).replace(" ", "").split(",") if d != ""],
        }
    entries = len([ln for ln in block.splitlines() if "utc_offset_min" in ln])
    assert len(js_clocks) == entries, (
        "code_decide.js has %d COUNTRY_CLOCKS entries but only %d parsed -- a row is not in the "
        "one shape the build reads (\"Name\": { utc_offset_min: N, weekend: [..] },)" % (entries, len(js_clocks)))
    assert js_clocks == COUNTRY_CLOCKS, (
        "COUNTRY_CLOCKS is not Section 12's 'Business-hours clocks' table. A country with no clock "
        "can never be placed in business hours, and a wrong offset sends out of hours silently:\n"
        "  only in the doc: %r\n  only in the js:  %r\n  differing rows:  %r"
        % (sorted(set(COUNTRY_CLOCKS) - set(js_clocks)),
           sorted(set(js_clocks) - set(COUNTRY_CLOCKS)),
           sorted(n for n in set(js_clocks) & set(COUNTRY_CLOCKS) if js_clocks[n] != COUNTRY_CLOCKS[n])))

    # The core 13 are the same list in both of Section 12's places.
    assert sorted(CLOCK_CORE) == sorted(GEOGRAPHIES), (
        "Section 12's clock table marks a different set of countries 'core' than its "
        "\"Geographies:\" line:\n  Geographies: %r\n  tier=core:   %r"
        % (sorted(GEOGRAPHIES), sorted(CLOCK_CORE)))

    # And the whole point: every country this pipeline can ingest has a clock.
    # INDEX_COUNTRIES is scraper/index_slugs.json run through the scraper's own
    # geography module, so it is the same names the CSV and leads.country carry.
    missing = [c for c in INDEX_COUNTRIES if c not in COUNTRY_CLOCKS]
    assert not missing, (
        "%d countries the scraper includes have no business-hours clock, so their leads can be "
        "scored, drafted and approved and then never sent: %r. Add each to Section 12's clock "
        "table and to code_decide.js." % (len(missing), missing))

def _assert_classify_matches_spec():
    src = js("code_classify_reply.js")
    m = re.search(r"const OPT_OUT_KEYWORDS = \[(.*?)\];", src)
    assert m, "OPT_OUT_KEYWORDS not found in code_classify_reply.js"
    found = re.findall(r"'([^']+)'", m.group(1))
    assert found == OPT_OUT_KEYWORDS, (
        "opt-out keywords drifted between the Master Ref and code_classify_reply.js. Section 5 "
        "makes detecting them the GDPR/KVKK opt-out mechanism:\n  doc: %r\n  js:  %r"
        % (OPT_OUT_KEYWORDS, found))
    assert _js_string(src, "OPT_OUT", "code_classify_reply.js") == OPT_OUT, (
        "the opt-out sentence code_classify_reply.js strips out of replies is not Section 5's. "
        "Every reply quotes it -- if the strip misses, every reply reads as an opt-out.")


# Drafting skill v3 section 8: follow-ups are composed "under every rule above".
# Assemble Follow-Up is drafting's code_assemble.js -- everything above its
# "Node body" marker, verbatim -- followed by code_followup_assemble.js. The
# follow-up body calls these; each must be defined there, or the node fails
# at runtime on the first follow-up.
RULES_FILE = os.path.join(DRAFTING_DIR, "code_assemble.js")
RULES_USED = ["str", "fold", "words", "cleanText", "unique", "jaccard", "bannedAdjectivesIn", "askFlags",
              "productFlags", "aiFlags", "prospectSponsor", "claimFlags", "absentAreasNamed", "proofFlags",
              "linkFlags", "repeatFlags"]
RULES_CONSTS = ["OPT_OUT", "MERGE_TAG", "LINK_FREE_WEEKS", "SLOT_ORDER", "DEPLOYMENTS", "BANNED_ADJECTIVES"]


def load_rules_js():
    src = _read(RULES_FILE)
    at = src.find("// Node body")
    assert at != -1, "%s has no '// Node body' marker -- the follow-ups embed everything above it" % RULES_FILE
    rules = src[:at]
    missing = [f for f in RULES_USED if not re.search(r"^function %s\(" % f, rules, re.M)]
    missing += [c for c in RULES_CONSTS if not re.search(r"^const %s = " % c, rules, re.M)]
    assert not missing, (
        "Assemble Follow-Up calls %r, which drafting's code_assemble.js no longer defines above its "
        "'Node body' marker. The follow-ups run the first touch's claim rules; restore them there." % missing)
    assert "$(" not in rules and "$input" not in rules and "__" not in re.sub(r"//.*", "", rules), (
        "the rules section of %s reads n8n data or holds a build placeholder -- node-body code has moved "
        "above the 'Node body' marker" % RULES_FILE)
    return rules


RULES_JS = load_rules_js()


def _assert_followup_matches_spec():
    src = js("code_followup.js")
    assert _js_string(src, "OPT_OUT", "code_followup.js") == OPT_OUT, (
        "the opt-out sentence in code_followup.js is not Section 5's, VERBATIM.")
    assert _js_string(RULES_JS, "OPT_OUT", "drafting's code_assemble.js") == OPT_OUT, (
        "the opt-out sentence Assemble Follow-Up appends (drafting's code_assemble.js) is not Section 5's, "
        "VERBATIM.")
    found = _js_int(src, "MAX_FOLLOW_UPS", "code_followup.js")
    assert found == MAX_FOLLOW_UPS, (
        "follow-up maximum drifted between Section 9 and code_followup.js:\n  doc: %d\n  js:  %d"
        % (MAX_FOLLOW_UPS, found))
    for name in ("code_followup.js", "code_followup_assemble.js"):
        s = js(name)
        found = (_js_int(s, "FOLLOW_UP_1_MIN", name), _js_int(s, "FOLLOW_UP_1_MAX", name),
                 _js_int(s, "FOLLOW_UP_2_MAX", name))
        assert found == (FU1_MIN, FU1_MAX, FU2_MAX), (
            "the follow-up lengths drifted between the drafting skill (section 8) and %s:\n"
            "  skill: #1 %d-%d words, #2 at most %d\n  js:    %r" % (name, FU1_MIN, FU1_MAX, FU2_MAX, found))
    banned = [a.lower() for a in re.findall(r"'([^']+)'", _js_block(RULES_JS, "BANNED_ADJECTIVES", RULES_FILE))]
    missing = [a for a in SKILL_BANNED if a not in banned]
    assert not missing, "the drafting skill bans %r, which the embedded rules do not" % missing

    # Prompt caching (Section 3, on since 2026-10-08). The composing call's
    # system prompt is the cached block; nothing per-follow-up may be, because
    # caching is a prefix match and everything about the lead differs per call.
    assert re.search(r"system: \[\{ type: 'text', text: SYSTEM_PROMPT, cache_control: CACHE_CONTROL \}\]",
                     src), (
        "the follow-up request's cached block is no longer exactly its system prompt. That prompt "
        "is ~1,169 tokens and build-substituted, so it is identical for every follow-up and every "
        "run -- the one prefix here worth a breakpoint.")
    assert not re.search(r"messages: \[\{ role: 'user', content: prompt \}\][^}]*cache_control", src), (
        "a message block in the follow-up request is marked for caching; everything there is "
        "per-lead, so the entry would be read by nothing.")
    # The TTL is the measured judgement drafting's build records; the two copies
    # must agree, or a follow-up and a first touch would be billed differently
    # for the same decision.
    assert "const CACHE_CONTROL = { type: 'ephemeral' };" in src, (
        "code_followup.js no longer uses the 5-minute (default) cache TTL. A 1-hour entry costs "
        "2x base input to write instead of 1.25x and only pays if calls sharing the prefix land "
        "more than 5 minutes but less than an hour apart -- measured 2026-10-08, this pipeline's "
        "do not. Change drafting's CACHE_TTL_JS and Section 3 at the same time.")


_assert_decide_matches_spec()
_assert_classify_matches_spec()
_assert_followup_matches_spec()


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def iso(expr):
    """A timestamptz as an ISO-8601 UTC string, so every clock value crosses into
    the Code nodes in one unambiguous format."""
    return "to_char((%s) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')" % expr


BLOCKED_DOMAIN = ("EXISTS (SELECT 1 FROM blocklist b WHERE {d} = b.domain OR {d} LIKE '%.' || b.domain)")

# The candidate row Decide Send sees for every approved email draft. Kept as a
# list so the wiring guard knows exactly which fields exist.
CANDIDATE_COLUMNS = [
    ("d.id", "draft_id"),
    ("d.lead_id", "lead_id"),
    ("d.channel", "channel"),
    ("d.status", "status"),
    ("d.variant", "variant"),
    ("d.subject", "subject"),
    ("coalesce(d.edited_body, d.body)", "body"),
    ("l.domain", "domain"),
    ("l.country", "country"),
    ("l.status", "lead_status"),
    ("c.email", "to_addr"),
    ("coalesce(c.verified, false)", "verified"),
    (BLOCKED_DOMAIN.format(d="lower(l.domain)"), "lead_domain_blocked"),
    (BLOCKED_DOMAIN.format(d="lower(split_part(c.email, '@', 2))"), "recipient_domain_blocked"),
    ("(SELECT count(*) FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.channel = 'email')::int", "prior_sends"),
    ("(SELECT count(*) FROM mailbox_sent m WHERE m.lead_id = d.lead_id AND m.source = 'sent-folder')::int",
     "prior_manual_sends"),
    ("(SELECT count(*) FROM drafts f WHERE f.lead_id = d.lead_id AND f.channel = 'email' "
     "AND f.status = 'sent' AND f.variant LIKE 'follow-up-%')::int", "follow_ups_sent"),
    ("EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.replied)", "replied"),
    ("EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.outcome = 'bounced')", "bounced"),
]

LOAD_STATE_SQL = """-- Load Send State: everything Decide Send needs, in ONE statement so it is one
-- consistent snapshot.
--
-- $1 now_override -- '' in the shipped workflow (checked at build time); set only
--    in the dry-run variant, so a scenario can be replayed at a fixed instant.
-- $2 min_gap_min, $3 send_probability -- echoed back so Decide reads nothing but
--    its own input. A Postgres node replaces the items; whatever Config set is
--    gone by the time Decide runs (Workflow 4's silent-prompt bug).
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
-- Every outbound message that counts toward the mailbox's daily ceiling: the
-- Sent-folder mirror (the operator's manual sends and the workflow's own), plus
-- claims whose SMTP outcome is not known yet.
counted AS (
  SELECT m.sent_at AS at, m.source AS kind
    FROM mailbox_sent m
   WHERE m.external
  UNION ALL
  SELECT o.sent_at, 'claim'
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NULL
),
cand AS (
  SELECT __CANDIDATE_COLUMNS__
    FROM drafts d
    JOIN leads l ON l.id = d.lead_id
    LEFT JOIN contacts c ON c.lead_id = d.lead_id
   WHERE d.channel = 'email'     -- Section 6: LinkedIn is sent by a human. Always.
     AND d.status = 'approved'   -- Workflow 5: nothing goes out that a human did not approve.
   ORDER BY d.id
   LIMIT 200
)
SELECT __ISO_CLOCK__ AS clock,
       $2::int AS min_gap_min,
       $3::float8 AS send_probability,
       (SELECT __ISO_SYNC__ FROM mailbox_sync WHERE folder = 'Sent') AS sent_mirror_synced_at,
       (SELECT __ISO_MIN__ FROM counted) AS first_external_send_at,
       (SELECT __ISO_MAX__ FROM counted, clk WHERE counted.at <= clk.now) AS last_external_send_at,
       (SELECT coalesce(json_agg(json_build_object('at', __ISO_AT__, 'kind', counted.kind) ORDER BY counted.at), '[]'::json)
          FROM counted, clk
         WHERE counted.at >= clk.now - interval '2 days' AND counted.at < clk.now + interval '2 days') AS recent_sends,
       (SELECT coalesce(json_agg(json_build_object('message_id', m.message_id, 'sent_at', __ISO_M__) ORDER BY m.sent_at), '[]'::json)
          FROM mailbox_sent m
         WHERE m.source = 'workflow' AND NOT m.seen_in_sent_folder) AS unconfirmed_workflow_sends,
       (SELECT coalesce(json_agg(row_to_json(cand)), '[]'::json) FROM cand) AS candidates,
       (SELECT coalesce(json_object_agg(k, n), '{}'::json)
          FROM (SELECT coalesce(channel, '?') || ':' || coalesce(status, '?') AS k, count(*) AS n
                  FROM drafts GROUP BY 1) s) AS drafts_by_status;"""

LOAD_STATE_SQL = (
    LOAD_STATE_SQL
    .replace("__CANDIDATE_COLUMNS__", ",\n         ".join("%s AS %s" % c for c in CANDIDATE_COLUMNS))
    .replace("__ISO_CLOCK__", iso("SELECT now FROM clk"))
    .replace("__ISO_SYNC__", iso("last_synced_at"))
    .replace("__ISO_MIN__", iso("min(counted.at)"))
    .replace("__ISO_MAX__", iso("max(counted.at)"))
    .replace("__ISO_AT__", iso("counted.at"))
    .replace("__ISO_M__", iso("m.sent_at"))
)

CLAIM_SQL = """-- Claim Send: the last line of defence, in one statement under one lock.
--
-- Decide Send chose this draft moments ago. Everything that makes a send
-- permissible is re-checked HERE, atomically, because this is where the draft
-- actually flips -- a bug in Decide can make the workflow send less, never more:
--   Section 6   channel = 'email'
--   Workflow 5  status = 'approved', and the body unchanged since Decide read it
--   Section 9   contact verified, domain and recipient domain not blocklisted,
--               no reply or bounce on this lead
--   Section 5   today's external sends, re-counted, below the ceiling
--
-- The advisory lock is its own statement on purpose. The Postgres node runs
-- this text as one multi-statement query, i.e. one implicit transaction; under
-- READ COMMITTED each statement takes a fresh snapshot, so the claim below is
-- counted AFTER any concurrent claim that held the lock has committed. (Inside
-- a single statement the snapshot would predate the lock, and two overlapping
-- ticks could both see 4 of 5 and both send.)
--
-- The draft flips to 'sent' and the outreach_log claim row is written BEFORE
-- the SMTP call: a crash between sending and logging then leaves a claim, not
-- an 'approved' draft that goes out again next tick. At-most-once, on purpose.
DO $$ BEGIN PERFORM pg_advisory_xact_lock(hashtext('novascout.workflow6.send')); END $$;
WITH p AS (
  SELECT $1::jsonb AS p
),
counted AS (
  SELECT m.message_id AS k
    FROM mailbox_sent m, p
   WHERE m.external
     AND m.sent_at >= (p.p->>'day_start')::timestamptz
     AND m.sent_at <  (p.p->>'day_end')::timestamptz
  UNION ALL
  SELECT 'claim:' || o.id
    FROM outreach_log o, p
   WHERE o.channel = 'email' AND o.message_id IS NULL
     AND o.sent_at >= (p.p->>'day_start')::timestamptz
     AND o.sent_at <  (p.p->>'day_end')::timestamptz
),
d AS (
  UPDATE drafts d
     SET status = 'sent'
    FROM p, leads l, contacts c
   WHERE d.id = (p.p->>'draft_id')::bigint
     AND d.channel = 'email'
     AND d.status = 'approved'
     AND coalesce(d.edited_body, d.body) = p.p->>'raw_body'
     AND l.id = d.lead_id
     AND c.lead_id = d.lead_id
     AND c.verified
     AND lower(c.email) = lower(p.p->>'to_addr')
     AND l.status IN ('drafted', 'approved', 'sent')
     AND NOT __LEAD_BLOCKED__
     AND NOT __RCPT_BLOCKED__
     AND NOT EXISTS (SELECT 1 FROM outreach_log o
                      WHERE o.lead_id = d.lead_id AND (o.replied OR o.outcome = 'bounced'))
     AND (d.variant LIKE 'follow-up-%'
          OR NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = d.lead_id AND o.channel = 'email'))
     AND (SELECT count(*) FROM counted) < (p.p->>'ceiling')::int
  RETURNING d.id, d.lead_id
),
o AS (
  INSERT INTO outreach_log (lead_id, draft_id, channel, sent_at, message_body)
  SELECT d.lead_id, d.id, 'email', (p.p->>'clock')::timestamptz, p.p->>'body'
    FROM d, p
  RETURNING id
)
SELECT (SELECT count(*) FROM d) = 1       AS claimed,
       (SELECT id FROM o)                  AS outreach_id,
       (p.p->>'draft_id')::bigint          AS draft_id,
       (p.p->>'lead_id')::bigint           AS lead_id,
       p.p->>'to_addr'                     AS to_addr,
       p.p->>'subject'                     AS subject,
       p.p->>'body'                        AS body,
       p.p->>'clock'                       AS clock,
       (SELECT count(*) FROM counted)::int AS sent_today_before,
       (p.p->>'ceiling')::int              AS ceiling
  FROM p;""".replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
).replace(
    "__RCPT_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(split_part(c.email, '@', 2))")
)

CONFIRM_SQL = """-- Confirm Send: SMTP accepted the message. Record its Message-ID on the claim
-- (replies thread on it), advance the lead, and put the send in mailbox_sent
-- NOW -- so it counts toward the ceiling before the Sent-folder mirror notices
-- it. When the mirror does, it only sets seen_in_sent_folder on this row.
WITH p AS (
  SELECT $1::jsonb AS p
),
o AS (
  UPDATE outreach_log o
     SET message_id = p.p->>'message_id'
    FROM p
   WHERE o.id = (p.p->>'outreach_id')::bigint
     AND o.message_id IS NULL
  RETURNING o.id, o.lead_id, o.sent_at
),
l AS (
  UPDATE leads l
     SET status = 'sent', updated_at = now()
    FROM o
   WHERE l.id = o.lead_id
     AND l.status IN ('drafted', 'approved')
  RETURNING l.id
),
m AS (
  INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source)
  SELECT p.p->>'message_id', o.sent_at, ARRAY[p.p->>'to_addr'], (p.p->>'external')::boolean, o.lead_id, 'workflow'
    FROM o, p
  ON CONFLICT (message_id) DO UPDATE SET lead_id = EXCLUDED.lead_id, source = 'workflow'
  RETURNING message_id
)
SELECT (SELECT count(*) FROM o)::int AS confirmed,
       (SELECT count(*) FROM l)::int AS lead_advanced,
       (SELECT message_id FROM m)    AS message_id,
       (p.p->>'draft_id')::bigint    AS draft_id
  FROM p;"""

REVERT_SQL = """-- Revert Claim: SMTP did not accept the message, so nothing went out. Remove
-- the claim (it would otherwise count toward the ceiling forever) and hand the
-- draft back:
--   account failure   -> 'approved'; the next tick retries
--   recipient failure -> 'pending' + '+smtp-rejected', back to a human; retrying
--                        a refused address would stall the whole queue behind it
WITH p AS (
  SELECT $1::jsonb AS p
),
o AS (
  DELETE FROM outreach_log o
   USING p
   WHERE o.id = (p.p->>'outreach_id')::bigint
     AND o.message_id IS NULL
  RETURNING o.id
),
d AS (
  UPDATE drafts d
     SET status  = CASE WHEN p.p->>'failure' = 'recipient' THEN 'pending' ELSE 'approved' END,
         variant = CASE WHEN p.p->>'failure' = 'recipient' AND coalesce(d.variant, '') NOT LIKE '%smtp-rejected%'
                        THEN coalesce(d.variant, '') || '+smtp-rejected'
                        ELSE d.variant END
    FROM p
   WHERE d.id = (p.p->>'draft_id')::bigint
     AND d.status = 'sent'
  RETURNING d.status, d.variant
)
SELECT (SELECT count(*) FROM o)::int AS claim_removed,
       (SELECT status FROM d)         AS draft_status,
       (SELECT variant FROM d)        AS draft_variant,
       p.p->>'failure'                AS failure,
       p.p->>'error'                  AS error
  FROM p;"""

MIRROR_SQL = """-- Mirror Sent: one trigger batch of the Sent folder into mailbox_sent, and the
-- time of this read into mailbox_sync. Keyed on Message-ID, so the full re-read
-- on every activation is a no-op for anything already known; a message the
-- workflow sent itself only gets seen_in_sent_folder = true -- the mirror's
-- proof of life the send path checks.
WITH p AS (
  SELECT $1::jsonb AS p
),
r AS (
  SELECT x->>'message_id'                                  AS message_id,
         (x->>'sent_at')::timestamptz                      AS sent_at,
         ARRAY(SELECT jsonb_array_elements_text(x->'recipients')) AS recipients,
         (x->>'external')::boolean                         AS external
    FROM p, jsonb_array_elements(p.p->'rows') AS x
),
up AS (
  INSERT INTO mailbox_sent (message_id, sent_at, recipients, external, lead_id, source, seen_in_sent_folder)
  SELECT r.message_id, r.sent_at, r.recipients, r.external,
         -- A manual send to a prospect links to the lead, so the send path will
         -- not follow it with a duplicate first touch.
         coalesce(
           (SELECT c.lead_id FROM contacts c WHERE lower(c.email) = ANY (r.recipients) ORDER BY c.lead_id LIMIT 1),
           (SELECT l.id FROM leads l
             WHERE lower(l.domain) = ANY (SELECT split_part(x, '@', 2) FROM unnest(r.recipients) AS x)
             ORDER BY l.id LIMIT 1)),
         'sent-folder', true
    FROM r
  ON CONFLICT (message_id) DO UPDATE SET seen_in_sent_folder = true
  RETURNING (xmax = 0) AS inserted
),
s AS (
  INSERT INTO mailbox_sync (folder, last_synced_at, messages_seen, undated)
  SELECT 'Sent', now(), (p.p->>'seen')::int, (p.p->>'undated')::int
    FROM p
  ON CONFLICT (folder) DO UPDATE
     SET last_synced_at = EXCLUDED.last_synced_at,
         messages_seen  = EXCLUDED.messages_seen,
         undated        = EXCLUDED.undated
  RETURNING last_synced_at
)
SELECT (SELECT count(*) FROM up WHERE inserted)::int     AS new_rows,
       (SELECT count(*) FROM up WHERE NOT inserted)::int AS already_known,
       (p.p->>'undated')::int                            AS undated,
       (SELECT __ISO_S__ FROM s)                         AS synced_at
  FROM p;""".replace("__ISO_S__", iso("s.last_synced_at"))

LOAD_SETTINGS_SQL = """-- Load Settings: the operator's notification address, read at runtime from the
-- settings table (migration 008; written from .env by sync_settings.py), so it
-- is never baked into this workflow. Normalise Sent treats a message that went
-- only to it like a note to an own-domain colleague: not a warm-up send.
-- Always exactly one row; NULL when no address is configured.
SELECT (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email;"""

RECORD_INBOUND_SQL = """-- Record Inbound: match one INBOX message to a lead, record it once, and act on
-- it in the same statement.
--
-- Matching, strongest first: our Message-ID in its In-Reply-To/References (or,
-- for a bounce, anywhere in the NDR); the contact's own address; the address a
-- bounce names; the lead's domain -- never a freemail or our own domain. Only
-- leads this workflow has actually emailed can match.
--
-- inbound_messages is keyed on Message-ID and every action below hangs off its
-- INSERT ... RETURNING, so a message the IMAP trigger delivers twice changes
-- nothing and notifies nobody the second time.
--
-- The row also carries the operator's notification address, read from the
-- settings table at runtime, so Build Notification has nothing baked in.
WITH p AS (
  SELECT $1::jsonb AS p
),
hit AS (
  SELECT h.lead_id, h.how
    FROM (
      SELECT o.lead_id, 'thread' AS how, 1 AS rank
        FROM outreach_log o, p
       WHERE o.message_id IN (SELECT jsonb_array_elements_text(p.p->'thread_ids'))
      UNION ALL
      SELECT c.lead_id, 'contact-email', 2
        FROM contacts c, p
       WHERE lower(c.email) = lower(p.p->>'from_addr')
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = c.lead_id AND o.channel = 'email')
      UNION ALL
      SELECT c.lead_id, 'bounced-address', 3
        FROM contacts c, p
       WHERE p.p->>'classification' = 'bounce'
         AND lower(c.email) IN (SELECT jsonb_array_elements_text(p.p->'mentioned_addrs'))
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = c.lead_id AND o.channel = 'email')
      UNION ALL
      SELECT l.id, 'lead-domain', 4
        FROM leads l, p
       WHERE NOT (p.p->>'freemail')::boolean
         AND NOT (p.p->>'own_domain')::boolean
         AND lower(l.domain) = p.p->>'from_domain'
         AND EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND o.channel = 'email')
    ) h
   ORDER BY h.rank, h.lead_id
   LIMIT 1
),
ins AS (
  INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by,
                                classification, opt_out_keyword, body_excerpt)
  SELECT p.p->>'message_id', (p.p->>'received_at')::timestamptz, p.p->>'from_addr', p.p->>'subject',
         (SELECT lead_id FROM hit), (SELECT how FROM hit),
         CASE WHEN (SELECT lead_id FROM hit) IS NULL THEN 'unmatched' ELSE p.p->>'classification' END,
         p.p->>'opt_out_keyword', p.p->>'body_excerpt'
    FROM p
  ON CONFLICT (message_id) DO NOTHING
  RETURNING lead_id, classification, received_at
),
last_send AS (
  SELECT o.id
    FROM outreach_log o
   WHERE o.lead_id = (SELECT lead_id FROM ins)
     AND o.channel = 'email' AND o.message_id IS NOT NULL
   ORDER BY o.sent_at DESC
   LIMIT 1
),
logged AS (
  UPDATE outreach_log o
     SET replied    = CASE WHEN i.classification IN ('reply', 'opt-out') THEN true ELSE o.replied END,
         replied_at = CASE WHEN i.classification IN ('reply', 'opt-out')
                           THEN coalesce(o.replied_at, i.received_at, now()) ELSE o.replied_at END,
         reply_body = CASE WHEN i.classification IN ('reply', 'opt-out')
                           THEN coalesce(o.reply_body, p.p->>'reply_text') ELSE o.reply_body END,
         outcome    = CASE i.classification
                        WHEN 'opt-out' THEN 'opt-out:' || (p.p->>'opt_out_keyword')
                        WHEN 'reply'   THEN coalesce(o.outcome, 'replied')
                        WHEN 'bounce'  THEN 'bounced'
                        ELSE o.outcome END
    FROM ins i, p
   WHERE o.id = (SELECT id FROM last_send)
     AND i.classification IN ('reply', 'opt-out', 'bounce')
  RETURNING o.id
),
advanced AS (
  -- Section 9: "Any reply -> kill follow-up sequence, set status='replied'".
  -- A bounce or an out-of-office is not a reply and does not do this.
  UPDATE leads l
     SET status = 'replied', updated_at = now()
    FROM ins i
   WHERE l.id = i.lead_id
     AND i.classification IN ('reply', 'opt-out')
     AND l.status IN ('sent', 'lost')
  RETURNING l.id
),
blocked AS (
  -- Section 5 / Section 9: an opt-out blocklists the domain, immediately and
  -- permanently -- the lead's domain, and the sender's own if it is a company
  -- domain that differs from it.
  INSERT INTO blocklist (domain, reason)
  SELECT DISTINCT v.dom, 'opt-out: replied "' || (p.p->>'opt_out_keyword') || '" -- ' || (p.p->>'message_id')
    FROM ins i
    JOIN leads l ON l.id = i.lead_id
    CROSS JOIN p
    CROSS JOIN LATERAL (VALUES
      (lower(l.domain)),
      (CASE WHEN NOT (p.p->>'freemail')::boolean AND NOT (p.p->>'own_domain')::boolean
            THEN p.p->>'from_domain' END)) AS v(dom)
   WHERE i.classification = 'opt-out'
     AND v.dom IS NOT NULL AND v.dom <> ''
  ON CONFLICT (domain) DO NOTHING
  RETURNING domain
)
SELECT ((SELECT count(*) FROM ins) = 1
        AND (SELECT lead_id FROM ins) IS NOT NULL
        AND (SELECT classification FROM ins) IN ('reply', 'opt-out', 'bounce')) AS notify,
       coalesce((SELECT classification FROM ins), 'already-recorded')           AS classification,
       p.p->>'opt_out_keyword'                                                  AS opt_out_keyword,
       l.company_name                                                           AS company_name,
       l.domain                                                                 AS domain,
       l.country                                                                AS country,
       s.fit_score                                                              AS fit_score,
       c.email                                                                  AS contact_email,
       p.p->>'from_addr'                                                        AS from_addr,
       p.p->>'subject'                                                          AS subject,
       p.p->>'received_at'                                                      AS received_at,
       (SELECT how FROM hit)                                                    AS matched_by,
       p.p->>'body_excerpt'                                                     AS body_excerpt,
       coalesce((SELECT array_agg(domain ORDER BY domain) FROM blocked), '{}')  AS blocklisted,
       (SELECT lead_id FROM ins)                                                AS lead_id,
       (SELECT count(*) FROM logged)::int                                       AS outreach_updated,
       (SELECT count(*) FROM advanced)::int                                     AS lead_updated,
       (SELECT value FROM settings WHERE key = 'operator_email')                AS operator_email
  FROM p
  LEFT JOIN leads l    ON l.id = (SELECT lead_id FROM hit)
  LEFT JOIN scores s   ON s.lead_id = l.id
  LEFT JOIN contacts c ON c.lead_id = l.id;"""

FOLLOWUP_DUE_SQL = """-- Find Due Follow-Ups -- Section 9: "if no reply after N days, generate
-- follow-up draft back into the review queue. Maximum M follow-ups, then mark
-- lost." Queue-driven like every stage (Section 7): whatever is due, whenever
-- this runs.
--
-- $1 now_override ('' in the shipped workflow), $2 days, $3 maximum, $4 batch.
--
-- The clock restarts at the later of the last send and the last follow-up
-- drafted, so a follow-up the reviewer rejected does not spawn the next one the
-- same afternoon. A rejected follow-up still uses its slot: the reviewer
-- decided against it, and composing another would ask again.
--
-- Drafting skill v3 section 8: the follow-up is composed by the drafting model,
-- so each due row also carries what Build Follow-Up needs for the request --
-- the lead's country (its proof line), the first touch's variant (the claim
-- codes it used, which a follow-up must not repeat), the ACTIVE claims library
-- (read per run, like Workflow 4's batch query, so an operator's edit reaches
-- the next follow-up with no rebuild), and the last follow-up that went out
-- (follow-up #2 must not repeat it).
--
-- Auto-approval (migration 014) adds the flag and the enrichment record: the
-- claim check compares each prospect fact in a follow-up with it. The record
-- never reaches the composing model -- Build Follow-Up hands that model only
-- the first email, as before -- it goes to the checker alone.
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
sends AS (
  SELECT o.lead_id, o.sent_at, o.message_body, o.draft_id
    FROM outreach_log o
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL
),
first_touch AS (
  SELECT DISTINCT ON (s.lead_id) s.lead_id, s.sent_at, s.message_body, d.subject, d.variant
    FROM sends s
    JOIN drafts d ON d.id = s.draft_id
   WHERE coalesce(d.variant, '') NOT LIKE 'follow-up-%'
   ORDER BY s.lead_id, s.sent_at
),
last_follow_up AS (
  SELECT DISTINCT ON (s.lead_id) s.lead_id, s.message_body
    FROM sends s
    JOIN drafts d ON d.id = s.draft_id
   WHERE d.variant LIKE 'follow-up-%'
   ORDER BY s.lead_id, s.sent_at DESC
),
last_send AS (
  SELECT lead_id, max(sent_at) AS sent_at FROM sends GROUP BY lead_id
),
-- created counts follow-up NUMBERS, not rows: a #1 the operator rejected and
-- had regenerated (2026-10-03, leads 7, 91, 104) is still one slot, the way
-- Write Follow-Up's dedupe already reads it. Counted as rows, the second #1
-- took #2's slot and the lead was marked lost instead of followed up again.
fu AS (
  SELECT d.lead_id,
         count(DISTINCT regexp_replace(d.variant, '[/+].*$', ''))::int  AS created,
         count(*) FILTER (WHERE d.status IN ('pending', 'approved'))::int AS open,
         max(d.created_at)                                              AS last_created
    FROM drafts d
   WHERE d.channel = 'email' AND d.variant LIKE 'follow-up-%'
   GROUP BY d.lead_id
)
SELECT l.id                                                             AS lead_id,
       CASE WHEN coalesce(fu.created, 0) >= $3::int THEN 'mark-lost' ELSE 'draft-follow-up' END AS action,
       coalesce(fu.created, 0) + 1                                      AS next_follow_up,
       ft.subject                                                       AS first_subject,
       ft.message_body                                                  AS first_body,
       ft.variant                                                       AS first_variant,
       __ISO_FIRST__                                                    AS first_sent_at,
       __ISO_LAST__                                                     AS last_sent_at,
       l.country                                                        AS country,
       lf.message_body                                                  AS last_follow_up_body,
       (SELECT coalesce(jsonb_agg(jsonb_build_object(
                  'code', k.code, 'slot', k.slot, 'body', btrim(k.body),
                  'countries', to_jsonb(k.countries), 'measured', k.measured,
                  'confirmed', k.confirmed, 'capabilities', to_jsonb(k.capabilities))
                  ORDER BY k.code), '[]'::jsonb)
          FROM claims_library k
         WHERE k.active)                                                AS library,
       auto_approve_email_enabled()                                     AS auto_approve_email,
       l.company_name                                                   AS company_name,
       e.therapeutic_areas                                              AS therapeutic_areas,
       e.phases                                                         AS phases,
       e.raw_extraction->>'city'                                        AS city,
       e.founder_name                                                   AS founder_name,
       e.employee_estimate                                              AS employee_estimate
  FROM leads l
  JOIN first_touch ft ON ft.lead_id = l.id
  JOIN last_send ls   ON ls.lead_id = l.id
  LEFT JOIN enrichments e ON e.lead_id = l.id
  LEFT JOIN last_follow_up lf ON lf.lead_id = l.id
  LEFT JOIN fu        ON fu.lead_id = l.id
  CROSS JOIN clk
 WHERE l.status = 'sent'
   AND greatest(ls.sent_at, coalesce(fu.last_created, ls.sent_at)) <= clk.now - make_interval(days => $2::int)
   AND coalesce(fu.open, 0) = 0
   AND NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND (o.replied OR o.outcome = 'bounced'))
   AND NOT EXISTS (SELECT 1 FROM inbound_messages i
                    WHERE i.lead_id = l.id AND i.classification IN ('reply', 'opt-out', 'bounce'))
   AND NOT __LEAD_BLOCKED__
 ORDER BY l.id
 LIMIT $4::int;""".replace(
    "__ISO_FIRST__", iso("ft.sent_at")
).replace(
    "__ISO_LAST__", iso("ls.sent_at")
).replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
)

FOLLOWUP_WRITE_SQL = """-- Write Follow-Up: insert the follow-up draft, or mark the lead lost. Both
-- guarded so a repeated run is a no-op: a lead gets each follow-up NUMBER at
-- most once, and only a lead still at 'sent' moves. A composed follow-up's
-- variant carries its claim codes and tags ('follow-up-1/BEN-DECK.A1+...'), and
-- a template one from before 2026-10-02 is the bare 'follow-up-1', so the
-- number is compared, not the variant.
--
-- The draft is 'pending' -- the reviewer's queue, with hold_reason -- unless the
-- Approval Gate and the claim check approved it (migration 014): only a payload
-- marked approved_by 'auto' comes out approved, and the table's trigger
-- re-checks that against the live flag and library.
WITH p AS (
  SELECT $1::jsonb AS p
),
ins AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by, hold_reason, claim_check)
  SELECT (p.p->>'lead_id')::bigint, 'email', p.p->'draft'->>'variant', p.p->'draft'->>'subject',
         p.p->'draft'->>'body',
         CASE WHEN p.p->'draft'->>'status' = 'approved' AND p.p->'draft'->>'approved_by' = 'auto'
              THEN 'approved' ELSE 'pending' END,
         CASE WHEN p.p->'draft'->>'status' = 'approved' AND p.p->'draft'->>'approved_by' = 'auto'
              THEN 'auto' END,
         nullif(p.p->'draft'->>'hold_reason', ''),
         nullif(p.p->'draft'->'claim_check', 'null'::jsonb)
    FROM p
   WHERE p.p->>'action' = 'draft-follow-up'
     AND p.p->'draft'->>'channel' = 'email'
     AND NOT EXISTS (SELECT 1 FROM drafts d
                      WHERE d.lead_id = (p.p->>'lead_id')::bigint
                        AND regexp_replace(coalesce(d.variant, ''), '[/+].*$', '')
                            = regexp_replace(p.p->'draft'->>'variant', '[/+].*$', ''))
     AND EXISTS (SELECT 1 FROM leads l WHERE l.id = (p.p->>'lead_id')::bigint AND l.status = 'sent')
  RETURNING id, variant, status, approved_by, hold_reason
),
lost AS (
  UPDATE leads l
     SET status = 'lost', updated_at = now()
    FROM p
   WHERE p.p->>'action' = 'mark-lost'
     AND l.id = (p.p->>'lead_id')::bigint
     AND l.status = 'sent'
  RETURNING l.id
)
SELECT (p.p->>'lead_id')::bigint   AS lead_id,
       p.p->>'action'              AS action,
       (SELECT id FROM ins)        AS draft_id,
       (SELECT variant FROM ins)   AS variant,
       (SELECT status FROM ins)    AS status,
       (SELECT approved_by FROM ins) AS approved_by,
       (SELECT hold_reason FROM ins) AS hold_reason,
       (SELECT count(*) FROM lost)::int AS marked_lost
  FROM p;"""


def with_iso(sql):
    """ISO(x) in the IMAP Health SQL -> iso(x), so every clock value leaves in one format."""
    return re.sub(r"\bISO\(([\w.]+)\)", lambda m: iso(m.group(1)), sql)


HEALTH_LOAD_SQL = with_iso("""-- Load Health State: the IMAP checker's Message-IDs against what Mailbox Watch
-- has recorded, plus this check's previous state -- one statement, one snapshot,
-- one clock.
--
-- $1 {inbox: [{message_id, at, from, subject}], sent: [{message_id, at, to}], grace_min}
--    The lists come from Check IMAP (empty when the checker did not answer);
--    `at` is INTERNALDATE, when the message landed in its folder.
--
-- A message is MISSED once it has been in its folder longer than grace_min with
-- no row. INBOX counts only from when Mailbox Watch first ran: its Inbox trigger
-- reads new mail only, so older messages were never its to record. Sent is read
-- whole on every activation, so every Sent message is expected -- except the ones
-- the pre-flight already leaves out (no Message-ID, or no usable Date header,
-- which the mirror skips as undated).
WITH p AS (
  SELECT $1::jsonb AS p
),
clk AS (
  SELECT now() AS now, make_interval(mins => (p.p->>'grace_min')::int) AS grace
    FROM p
),
watch AS (
  SELECT least((SELECT min(recorded_at) FROM mailbox_sent WHERE source = 'sent-folder'),
               (SELECT min(processed_at) FROM inbound_messages)) AS started_at
),
inbox AS (
  SELECT DISTINCT ON (x->>'message_id')
         x->>'message_id' AS message_id, (x->>'at')::timestamptz AS at, x->>'from' AS from_addr, x->>'subject' AS subject
    FROM p, jsonb_array_elements(CASE WHEN jsonb_typeof(p.p->'inbox') = 'array' THEN p.p->'inbox' ELSE '[]'::jsonb END) AS x
   ORDER BY x->>'message_id', (x->>'at')::timestamptz
),
sent AS (
  SELECT DISTINCT ON (x->>'message_id')
         x->>'message_id' AS message_id, (x->>'at')::timestamptz AS at, x->>'to' AS to_addr
    FROM p, jsonb_array_elements(CASE WHEN jsonb_typeof(p.p->'sent') = 'array' THEN p.p->'sent' ELSE '[]'::jsonb END) AS x
   ORDER BY x->>'message_id', (x->>'at')::timestamptz
),
missed_inbox AS (
  SELECT i.* FROM inbox i, clk, watch
   WHERE i.at <= clk.now - clk.grace
     AND i.at >= watch.started_at
     AND NOT EXISTS (SELECT 1 FROM inbound_messages m WHERE m.message_id = i.message_id)
),
missed_sent AS (
  SELECT s.* FROM sent s, clk
   WHERE s.at <= clk.now - clk.grace
     AND NOT EXISTS (SELECT 1 FROM mailbox_sent m WHERE m.message_id = s.message_id)
),
h AS (
  SELECT * FROM mailbox_health WHERE check_name = 'imap'
)
SELECT ISO(clk.now) AS now,
       (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email,
       ISO(watch.started_at) AS watch_started_at,
       (SELECT ISO(last_synced_at) FROM mailbox_sync WHERE folder = 'Sent') AS sent_synced_at,
       (SELECT coalesce(jsonb_agg(jsonb_build_object('message_id', message_id, 'at', ISO(at),
                                                     'from', from_addr, 'subject', subject) ORDER BY at), '[]'::jsonb)
          FROM missed_inbox) AS missing_inbox,
       (SELECT coalesce(jsonb_agg(jsonb_build_object('message_id', message_id, 'at', ISO(at),
                                                     'to', to_addr) ORDER BY at), '[]'::jsonb)
          FROM missed_sent) AS missing_sent,
       (SELECT healthy FROM h) AS prev_healthy,
       (SELECT problems FROM h) AS prev_problems,
       (SELECT consecutive_failures FROM h) AS prev_consecutive_failures,
       (SELECT ISO(failing_since) FROM h) AS prev_failing_since,
       (SELECT ISO(last_alerted_at) FROM h) AS prev_last_alerted_at,
       (SELECT alerted_problems FROM h) AS prev_alerted_problems
  FROM clk, watch;""")

HEALTH_RECORD_SQL = with_iso("""-- Record Health: this check into mailbox_health (migration 009). It runs AFTER
-- the alert, so an alert counts as sent only when SMTP accepted it -- one that
-- failed is tried again on the next check.
--
-- $1 the state Assess Health emitted
-- $2 Send Alert's output when an alert was attempted; otherwise Assess Health's
--    own item, which has no `accepted`
WITH p AS (
  SELECT $1::jsonb AS s, $2::jsonb AS r
),
v AS (
  SELECT (p.s->>'healthy')::boolean AS healthy,
         coalesce(p.s->>'alert_kind' = 'problem'
                  AND p.r->>'error' IS NULL
                  AND jsonb_typeof(p.r->'accepted') = 'array'
                  AND jsonb_array_length(p.r->'accepted') > 0, false) AS alert_sent,
         ARRAY(SELECT jsonb_array_elements_text(
           CASE WHEN jsonb_typeof(p.s->'problems') = 'array' THEN p.s->'problems' ELSE '[]'::jsonb END)) AS problems,
         ARRAY(SELECT jsonb_array_elements_text(
           CASE WHEN jsonb_typeof(p.s->'prev_alerted_problems') = 'array' THEN p.s->'prev_alerted_problems'
                ELSE '[]'::jsonb END)) AS prev_alerted_problems
    FROM p
),
up AS (
  INSERT INTO mailbox_health AS h (check_name, healthy, problems, detail, consecutive_failures, failing_since,
                                   last_checked_at, last_ok_at, last_alerted_at, alerted_problems)
  SELECT 'imap', v.healthy, v.problems, p.s->>'detail', (p.s->>'consecutive_failures')::int,
         (p.s->>'failing_since')::timestamptz, now(),
         CASE WHEN v.healthy THEN now() END,
         -- Cleared when healthy (the episode is over); stamped only on an alert
         -- SMTP accepted; otherwise carried forward.
         CASE WHEN v.healthy THEN NULL WHEN v.alert_sent THEN now()
              ELSE (p.s->>'prev_last_alerted_at')::timestamptz END,
         CASE WHEN v.healthy THEN NULL WHEN v.alert_sent THEN v.problems ELSE v.prev_alerted_problems END
    FROM p, v
  ON CONFLICT (check_name) DO UPDATE
     SET healthy              = EXCLUDED.healthy,
         problems             = EXCLUDED.problems,
         detail               = EXCLUDED.detail,
         consecutive_failures = EXCLUDED.consecutive_failures,
         failing_since        = EXCLUDED.failing_since,
         last_checked_at      = EXCLUDED.last_checked_at,
         last_ok_at           = coalesce(EXCLUDED.last_ok_at, h.last_ok_at),
         last_alerted_at      = EXCLUDED.last_alerted_at,
         alerted_problems     = EXCLUDED.alerted_problems
  RETURNING h.healthy, h.problems, h.consecutive_failures, h.failing_since, h.last_alerted_at
)
SELECT up.healthy                AS healthy,
       up.problems               AS problems,
       up.consecutive_failures   AS consecutive_failures,
       ISO(up.failing_since)     AS failing_since,
       ISO(up.last_alerted_at)   AS last_alerted_at,
       p.s->>'alert_kind'        AS alert_kind,
       v.alert_sent              AS alert_sent
  FROM up, p, v;""")


DIGEST_LOAD_SQL = with_iso("""-- Load Digest: everything the operator's daily digest says, in one snapshot
-- (migration 014). Read-only.
--
-- $1 now_override ('' in the shipped workflow)
--
-- The window starts where the last digest that went out stopped (digest_log),
-- so a day the host was off is folded into the next digest instead of lost;
-- the first digest ever covers the last 24 hours. The day and hour are the
-- operator's (__ZONE__, docker-compose's GENERIC_TIMEZONE).
--   auto_approved  every draft the workflows approved themselves in the window,
--                  with what became of it since
--   sent           every email SMTP accepted in the window, with who approved it
--   held           EVERY pending draft -- the exceptions queue -- with its
--                  hold_reason; `new` marks the ones drafted in the window
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
win AS (
  SELECT clk.now AS to_ts,
         coalesce((SELECT max(g.covers_to) FROM digest_log g), clk.now - interval '24 hours') AS from_ts,
         (clk.now AT TIME ZONE '__ZONE__')::date AS day,
         extract(hour FROM clk.now AT TIME ZONE '__ZONE__')::int AS hour
    FROM clk
)
SELECT win.day::text                                                     AS digest_day,
       win.hour                                                          AS local_hour,
       ISO(win.from_ts)                                                  AS covers_from,
       ISO(win.to_ts)                                                    AS covers_to,
       EXISTS (SELECT 1 FROM digest_log g WHERE g.digest_day = win.day)  AS already_sent,
       (SELECT value FROM settings WHERE key = 'operator_email')         AS operator_email,
       auto_approve_email_enabled()                                      AS auto_approve_email,
       (SELECT coalesce(json_agg(x ORDER BY x.approved_at, x.draft_id), '[]'::json) FROM (
          SELECT d.id AS draft_id, d.lead_id, l.company_name, l.domain, l.country, d.channel, d.variant, d.status,
                 ISO(d.approved_at) AS approved_at
            FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE d.approved_by = 'auto' AND d.approved_at >= win.from_ts AND d.approved_at < win.to_ts) x)
                                                                         AS auto_approved,
       (SELECT coalesce(json_agg(x ORDER BY x.sent_at, x.draft_id), '[]'::json) FROM (
          SELECT o.draft_id, o.lead_id, l.company_name, l.domain, c.email AS to_addr, d.variant, d.approved_by,
                 ISO(o.sent_at) AS sent_at
            FROM outreach_log o
            JOIN leads l ON l.id = o.lead_id
            LEFT JOIN drafts d ON d.id = o.draft_id
            LEFT JOIN contacts c ON c.lead_id = o.lead_id
           WHERE o.channel = 'email' AND o.message_id IS NOT NULL
             AND o.sent_at >= win.from_ts AND o.sent_at < win.to_ts) x)
                                                                         AS sent,
       (SELECT coalesce(json_agg(x ORDER BY x.created_at, x.draft_id), '[]'::json) FROM (
          SELECT d.id AS draft_id, d.lead_id, l.company_name, l.domain, d.channel, d.variant, d.hold_reason,
                 ISO(d.created_at) AS created_at, d.created_at >= win.from_ts AS new
            FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE d.status = 'pending') x)
                                                                         AS held
  FROM win;""".replace("__ZONE__", SENDER_ZONE))

DIGEST_RECORD_SQL = """-- Record Digest: the digest is recorded only once SMTP accepted it, so one that
-- failed goes out on the next tick. One row per operator day; a repeat is a no-op.
--
-- $1 Build Digest's `record`; $2 Send Digest's output (nodemailer's info, or
--    {error} -- the node continues on error)
WITH p AS (
  SELECT $1::jsonb AS d, $2::jsonb AS r
),
ins AS (
  INSERT INTO digest_log (digest_day, covers_from, covers_to, summary)
  SELECT (p.d->>'digest_day')::date, (p.d->>'covers_from')::timestamptz, (p.d->>'covers_to')::timestamptz,
         p.d->'summary'
    FROM p
   WHERE p.r->>'error' IS NULL
     AND jsonb_typeof(p.r->'accepted') = 'array'
     AND jsonb_array_length(p.r->'accepted') > 0
  ON CONFLICT (digest_day) DO NOTHING
  RETURNING digest_day
)
SELECT p.d->>'digest_day'             AS digest_day,
       (SELECT count(*) FROM ins)::int AS recorded,
       p.r->>'error'                  AS error
  FROM p;"""


# ---------------------------------------------------------------------------
# SQL guards
# ---------------------------------------------------------------------------

def final_select_aliases(sql):
    """Output column names of the statement's final SELECT (depth-0 `AS name`)."""
    start = sql.rfind("\nSELECT ")
    assert start != -1, "no final SELECT found"
    body = sql[start:]
    names, depth, i = [], 0, 0
    while i < len(body):
        ch = body[i]
        if ch == "'":
            j = body.index("'", i + 1)
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            m = re.match(r"\bAS\s+(\w+)", body[i:])
            if m and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_")):
                names.append(m.group(1))
                i += m.end()
                continue
        i += 1
    return names


# Section 6, the one rule that does not change regardless of tooling.
for _name, _sql, _needle in [
    ("Load Send State", LOAD_STATE_SQL, "d.channel = 'email'"),
    ("Claim Send", CLAIM_SQL, "d.channel = 'email'"),
]:
    assert _needle in _sql, (
        "%s no longer filters on %s. Section 6: 'LinkedIn sending is manual. Always. No "
        "exceptions.' -- the send path must never be able to see a LinkedIn draft." % (_name, _needle))
assert "d.status = 'approved'" in LOAD_STATE_SQL and "d.status = 'approved'" in CLAIM_SQL, (
    "the send path no longer requires status='approved'. Workflow 5's human gate is the "
    "whole point of the review queue.")
assert "< (p.p->>'ceiling')::int" in CLAIM_SQL, (
    "Claim Send no longer re-counts the Section 5 ceiling under its lock.")
assert CLAIM_SQL.index("pg_advisory_xact_lock") < CLAIM_SQL.index("WITH p AS"), (
    "the advisory lock must be a separate statement BEFORE the claim, or the claim's "
    "snapshot predates the lock and two ticks can both count 4 of 5.")

_insert_cols = [c.strip() for c in re.search(r"INSERT INTO outreach_log \(([^)]+)\)", CLAIM_SQL).group(1).split(",")]
_unknown = [c for c in _insert_cols if c not in OUTREACH_COLUMNS]
assert not _unknown, (
    "the outreach_log INSERT writes %r, which Section 8 of %s does not define. Update the "
    "doc first." % (_unknown, MASTER_REF))
_updated = re.findall(r"UPDATE outreach_log o\s+SET (.*?)\n\s+(?:FROM|WHERE)", CONFIRM_SQL + RECORD_INBOUND_SQL, re.S)
for _block in _updated:
    for _col in re.findall(r"(?:^|,)\s*(\w+)\s*=", _block, re.M):
        assert _col in OUTREACH_COLUMNS, (
            "an UPDATE sets outreach_log.%s, which Section 8 does not define." % _col)
_insert_cols = [c.strip() for c in re.search(r"INSERT INTO drafts \(([^)]+)\)", FOLLOWUP_WRITE_SQL).group(1).split(",")]
_unknown = [c for c in _insert_cols if c not in DRAFTS_COLUMNS]
assert not _unknown, (
    "the follow-up INSERT writes drafts.%r, which Section 8 does not define." % _unknown)

_ALL_SQL = [LOAD_STATE_SQL, CLAIM_SQL, CONFIRM_SQL, REVERT_SQL, MIRROR_SQL, RECORD_INBOUND_SQL,
            FOLLOWUP_DUE_SQL, FOLLOWUP_WRITE_SQL]
for _sql in _ALL_SQL:
    for _status in re.findall(r"UPDATE leads l\s+SET status = '(\w+)'", _sql):
        assert _status in LEAD_STATUSES, (
            "Workflow 6 writes leads.status = %r, which is not in Section 8's LOCKED status "
            "list %r." % (_status, LEAD_STATUSES))


# ---------------------------------------------------------------------------
# Wiring guards -- every field a node reads must be produced upstream
# ---------------------------------------------------------------------------

def _reads(src, var):
    return set(re.findall(r"\b%s\.(\w+)" % re.escape(var), src))


def _sql_payload_reads(sql, prefix="p.p"):
    fields = set(re.findall(re.escape(prefix) + r"->>?'(\w+)'", sql))
    fields.discard("draft")
    return fields


def _js_emits(src, field):
    return re.search(r"\b%s:" % re.escape(field), src) is not None


def _assert_wired(consumer, fields, produced, producer):
    missing = sorted(f for f in fields if f not in produced)
    assert not missing, (
        "%s reads %r, but %s does not produce %s. n8n resolves a missing field to an empty "
        "value without erroring -- this is Workflow 4's silent-prompt bug again."
        % (consumer, sorted(fields), producer, missing))


def _assert_js_emits(consumer, fields, js_src, producer):
    missing = sorted(f for f in fields if not _js_emits(js_src, f))
    assert not missing, (
        "%s reads %r, but %s never emits %s -- it would arrive empty, silently."
        % (consumer, sorted(fields), producer, missing))


_decide = js("code_decide.js")
_assert_wired("Decide Send (state.*)", _reads(_decide, "state"), final_select_aliases(LOAD_STATE_SQL), "Load Send State")
_assert_wired("Decide Send (candidate c.*)", _reads(_decide, "c"), [a for _, a in CANDIDATE_COLUMNS], "the candidate query")
_assert_js_emits("Claim Send", _sql_payload_reads(CLAIM_SQL), _decide, "code_decide.js's payload")
SEND_EMAIL_READS = {"to_addr", "subject", "body"}
_assert_wired("Send Email", SEND_EMAIL_READS, final_select_aliases(CLAIM_SQL), "Claim Send")
_check = js("code_check_smtp.js")
_assert_wired("Check SMTP Result (claim.*)", _reads(_check, "claim"), final_select_aliases(CLAIM_SQL), "Claim Send")
_assert_js_emits("Confirm Send", _sql_payload_reads(CONFIRM_SQL), _check, "code_check_smtp.js")
_assert_js_emits("Revert Claim", _sql_payload_reads(REVERT_SQL), _check, "code_check_smtp.js")
_mirror = js("code_mirror_sent.js")
_assert_wired("Normalise Sent (settings.*)", _reads(_mirror, "settings"), final_select_aliases(LOAD_SETTINGS_SQL),
              "Load Settings")
_assert_js_emits("Mirror Sent", _sql_payload_reads(MIRROR_SQL) | set(re.findall(r"x->>?'(\w+)'", MIRROR_SQL)),
                 _mirror, "code_mirror_sent.js")
_classify = js("code_classify_reply.js")
_assert_js_emits("Record Inbound", _sql_payload_reads(RECORD_INBOUND_SQL), _classify, "code_classify_reply.js")
_detect_signal = js("code_detect_signal.js")
_assert_wired("Detect Positive Signal (r.*)", _reads(_detect_signal, "r"), final_select_aliases(RECORD_INBOUND_SQL),
              "Record Inbound")
DETECT_SIGNAL_EMITS = {"signal", "signal_keyword"}
_assert_js_emits("Build Notification", DETECT_SIGNAL_EMITS, _detect_signal, "code_detect_signal.js")
_notify = js("code_notify.js")
_assert_wired("Build Notification (r.*)", _reads(_notify, "r"),
              set(final_select_aliases(RECORD_INBOUND_SQL)) | DETECT_SIGNAL_EMITS,
              "Record Inbound -> Detect Positive Signal")
NOTIFY_EMAIL_READS = {"notify_to", "subject", "text"}
_assert_js_emits("Notify Operator", NOTIFY_EMAIL_READS, _notify, "code_notify.js")
_followup = js("code_followup.js")
_assert_wired("Build Follow-Up (r.*)", _reads(_followup, "r"), final_select_aliases(FOLLOWUP_DUE_SQL), "Find Due Follow-Ups")
# Skill v3 section 8: Build Follow-Up -> [Needs Model?] -> Claude Follow-Up ->
# Assemble Follow-Up -> [Drop Failed Generations] -> Write Follow-Up, and the
# mark-lost rows straight from Build Follow-Up to Write Follow-Up. Every
# boundary, guarded.
_fu_assemble = js("code_followup_assemble.js")
FOLLOWUP_CLAUDE_READS = {"request"}
FOLLOWUP_IF_READS = {"needs_model"}
FOLLOWUP_FILTER_READS = {"write"}
_assert_js_emits("Claude Follow-Up ($json.request) / Needs Model? ($json.needs_model)",
                 FOLLOWUP_CLAUDE_READS | FOLLOWUP_IF_READS, _followup, "code_followup.js")
_assert_js_emits("Assemble Follow-Up (src.*)", _reads(_fu_assemble, "src"), _followup, "code_followup.js")
_assert_js_emits("Drop Failed Generations ($json.write)", FOLLOWUP_FILTER_READS, _fu_assemble,
                 "code_followup_assemble.js")
_fu_write_reads = _sql_payload_reads(FOLLOWUP_WRITE_SQL) | set(re.findall(r"p\.p->'draft'->>?'(\w+)'", FOLLOWUP_WRITE_SQL))
# Auto-approval (migration 014): Assemble Follow-Up -> Approval Gate ->
# [Needs Claim Check?] -> Claude Claim Check -> Apply Claim Check -> Write. The
# decision fields are set on the draft by the gate and the apply node (shared
# with Workflow 4, n8n/drafting/code_approval*.js); everything else is
# Assemble Follow-Up's.
FOLLOWUP_DECISION_FIELDS = {"status", "approved_by", "hold_reason", "claim_check"}
_approval_all = _read(os.path.join(DRAFTING_DIR, "code_approval.js")) + _read(
    os.path.join(DRAFTING_DIR, "code_approval_apply.js"))
_approval_repair = _read(os.path.join(DRAFTING_DIR, "code_approval_repair.js"))
for _f in sorted(_fu_write_reads & FOLLOWUP_DECISION_FIELDS):
    assert re.search(r"\b(?:d|target)\.%s = " % _f, _approval_all), (
        "Write Follow-Up reads draft.%s, which neither Approval Gate nor Apply Claim Check sets" % _f)
    assert re.search(r"\bd\.%s = " % _f, _approval_repair), (
        "Write Follow-Up reads draft.%s, which Apply Repair does not set on a held draft" % _f)
# The repair loop runs a second copy of Assemble Follow-Up, which must hand the
# composition and the repair state on.
_assert_js_emits("the repair loop (composition / repair / repair_fields)", {"composition", "repair", "repair_fields"},
                 _fu_assemble, "code_followup_assemble.js")
_assert_js_emits("Write Follow-Up (a composed follow-up)", (_fu_write_reads - FOLLOWUP_DECISION_FIELDS) |
                 {"payload", "draft"}, _fu_assemble, "code_followup_assemble.js")
_assert_js_emits("Approval Gate (item.approval / item.payload)", {"approval", "payload"}, _fu_assemble,
                 "code_followup_assemble.js")
for _f in sorted(set(re.findall(r"\bctx\.(\w+)", _approval_all))):
    assert re.search(r"^\s*%s:" % re.escape(_f), _fu_assemble, re.M), (
        "the shared Approval Gate reads approval.%s, but Assemble Follow-Up's `approval` has no %s -- it would be "
        "undefined at runtime, with no error" % (_f, _f))
_assert_js_emits("Write Follow-Up (mark-lost)", _sql_payload_reads(FOLLOWUP_WRITE_SQL) | {"payload", "draft"},
                 _followup, "code_followup.js")

# IMAP Health crosses two process boundaries before it reaches n8n -- the
# pre-flight writes a JSON file, the checker serves it -- and both are guarded
# the same way as a node boundary.
_health = js("code_health.js")
_server = js("imap_health_server.py")
_preflight = js("imap_preflight.py")
HEALTH_CONFIG_FIELDS = {"checker_url", "grace_min", "confirm_after", "remind_hours"}
_assert_wired("Assess Health (db.*)", _reads(_health, "db"), final_select_aliases(HEALTH_LOAD_SQL), "Load Health State")
_assert_wired("Assess Health (cfg.*)", _reads(_health, "cfg"), HEALTH_CONFIG_FIELDS, "Config")
_assert_wired("Assess Health (check.*)", _reads(_health, "check") - {"error"},
              set(re.findall(r'"(\w+)":', _server)), "imap_health_server.py's answer")
_assert_wired("Load Health State (x->>'...')", set(re.findall(r"x->>'(\w+)'", HEALTH_LOAD_SQL)),
              set(re.findall(r'"(\w+)":', _preflight)), "imap_preflight.py --ids-json")
_assert_js_emits("Record Health", _sql_payload_reads(HEALTH_RECORD_SQL, prefix="p.s"), _health, "code_health.js")
HEALTH_EMAIL_READS = {"notify", "notify_to", "subject", "text"}
_assert_js_emits("Alert? / Send Alert", HEALTH_EMAIL_READS, _health, "code_health.js")

# Daily Digest: Load Digest -> Build Digest -> [Send Digest?] -> Send Digest ->
# Record Digest.
_digest = js("code_digest.js")
_assert_wired("Build Digest (row.*)", _reads(_digest, "row"), final_select_aliases(DIGEST_LOAD_SQL), "Load Digest")
_assert_wired("Build Digest (cfg.*)", _reads(_digest, "cfg"), {"digest_hour", "now_override"}, "Config")
DIGEST_EMAIL_READS = {"send", "notify_to", "subject", "text", "record"}
_assert_js_emits("Send Digest? / Send Digest / Record Digest", DIGEST_EMAIL_READS, _digest, "code_digest.js")
_assert_js_emits("Record Digest ($1)", set(re.findall(r"p\.d->>?'(\w+)'", DIGEST_RECORD_SQL)), _digest,
                 "code_digest.js's record")
assert "AT TIME ZONE '%s'" % SENDER_ZONE in DIGEST_LOAD_SQL, (
    "the digest's day is not the operator's (%s) -- it would go out at the wrong hour" % SENDER_ZONE)
_insert_cols = [c.strip() for c in re.search(r"INSERT INTO digest_log \(([^)]+)\)", DIGEST_RECORD_SQL).group(1).split(",")]
_unknown = [c for c in _insert_cols if c not in load_columns(DOC, "digest_log", must="digest_day")]
assert not _unknown, "Record Digest writes digest_log.%r, which Section 8 does not define." % _unknown

_found = re.findall(r"^\s*'([a-z-]+)':", _js_block(_health, "PROBLEMS", "code_health.js"), re.M)
assert _found == HEALTH_PROBLEMS, (
    "the IMAP health problem codes drifted between Section 8 (mailbox_health.problems) and code_health.js:\n"
    "  doc: %r\n  js:  %r" % (HEALTH_PROBLEMS, _found))
_insert_cols = [c.strip() for c in re.search(r"INSERT INTO mailbox_health AS h \(([^)]+)\)", HEALTH_RECORD_SQL)
                .group(1).replace("\n", " ").split(",")]
_unknown = [c for c in _insert_cols if c not in HEALTH_COLUMNS]
assert not _unknown, "Record Health writes mailbox_health.%r, which Section 8 does not define." % _unknown


# ---------------------------------------------------------------------------
# Code nodes with their build constants baked in
# ---------------------------------------------------------------------------

def bake(name, **subs):
    src = js(name)
    for key, val in subs.items():
        token = "__%s__" % key
        assert token in src, "%s has no %s placeholder" % (name, token)
        src = src.replace(token, json.dumps(val, ensure_ascii=False))
    left = re.findall(r"__[A-Z_]+__", src)
    assert not left, "%s still has unsubstituted placeholders %r" % (name, left)
    return src


# ---------------------------------------------------------------------------
# The follow-up composition call -- drafting skill v3 section 8
#
# The drafting model (Section 3), with the drafting node's request parameters:
# no temperature/top_p/top_k (a non-default value is a 400 on this model),
# adaptive thinking by default, effort from Section 3, JSON through
# output_config.format with a strict schema, max_tokens covering thinking plus
# text. No prompt caching, for Workflow 4's measured reason: the HTTP node sends
# a run's calls concurrently, so none reads another's cache.
# ---------------------------------------------------------------------------

ANTHROPIC_CRED = {"anthropicApi": {"id": "novascoutAnthropic01", "name": "Anthropic - Nova Scout drafting"}}

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "ask": {"type": "string"},
        "added_claim": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "string"}},
        "first_email_covers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["body", "ask", "added_claim", "claims", "first_email_covers"],
    "additionalProperties": False,
}
_fu_fields = (re.findall(r"'(\w+)'", re.search(r"const FU_FIELDS = \[(.*?)\];", _fu_assemble).group(1))
              + re.findall(r"'(\w+)'", re.search(r"const FU_LISTS = \[(.*?)\];", _fu_assemble).group(1)))
assert sorted(_fu_fields) == sorted(FOLLOWUP_SCHEMA["required"]), (
    "Assemble Follow-Up's FU_FIELDS/FU_LISTS do not match the follow-up schema:\n  schema: %r\n  js:     %r"
    % (FOLLOWUP_SCHEMA["required"], _fu_fields))

FOLLOWUP_REQUEST = {
    "model": DRAFT_MODEL,
    "max_tokens": 16000,
    "output_config": {"effort": DRAFT_EFFORT, "format": {"type": "json_schema", "schema": FOLLOWUP_SCHEMA}},
}

FOLLOWUP_SYSTEM_PROMPT = "\n".join([
    "You write one short follow-up email to a small contract research organisation (CRO) that has",
    "not replied to a cold first email from " + SENDER_NAME + ". A person reviews it before anything is",
    "sent.",
    "",
    "Each message gives you THE FIRST EMAIL exactly as it was sent, and APPROVED CLAIMS: the only",
    "things you may say about what we built, the problem it solves, and who uses it. You may",
    "rephrase a claim. You may never widen what it means.",
    "",
    "FOLLOW-UP #1:",
    "- Open by referring back to the first email in a few words (\"Following up on my note about",
    "  after-hours sponsor inquiries\"). Do not restate it.",
    "- Add exactly ONE angle or benefit from the approved list that the first email did not use. Never",
    "  one whose point the first email already made.",
    "- Say that line almost word for word: its own words, rephrased only for grammar (\"It sends\" may",
    "  become \"the assistant sends\"). Add nothing to it -- no consequence, no outcome, no \"so you can",
    "  ...\", no second sentence about it. Every follow-up held so far was held for a sentence added here.",
    "- Then exactly one ask.",
    "- The body plus the ask is %d-%d words." % (FU1_MIN, FU1_MAX),
    "",
    "FOLLOW-UP #2 -- a short final note:",
    "- Say this is the last note, and restate the offer as the one ask. Add no new claim.",
    "- The body plus the ask is at most %d words." % FU2_MAX,
    "",
    "THE PROSPECT: no new fact about them. The only facts are the ones the first email already",
    "states. Never describe them as sponsoring anything -- in these emails \"sponsor\" means the",
    "biotech, pharma, device or academic company that hires a CRO, their client. Never write the",
    "company's name or a person's name.",
    "",
    "THE PRODUCT: the first email introduced it; refer back to it (\"the assistant\"), do not",
    "describe it again and do not name it. Never call it a chatbot or a Q&A bot. The word \"AI\"",
    "at most once. Never \"AI-powered\".",
    "",
    "NO REPEATS: the lines you use must not repeat the same capability -- each benefit says what it",
    "is about -- and never restate in your own words a capability the first email already made.",
    "",
    "CLAIMS -- forbidden, all of them:",
    "- guarantees (\"you'll never lose a sponsor\")",
    "- any number, percentage or multiplier that is not in the approved claims or the first email",
    "- claiming to identify anonymous website visitors",
    "- naming any CRM, tool or integration",
    "- supported languages",
    "- any count of clients beyond the two named deployments",
    "- saying it books calls or fills a calendar -- it sends the sponsor your booking link, and the",
    "  sponsor books",
    "- saying it answers from SOPs or from documents -- it answers from their website",
    "A stakes line is about the industry, not about us.",
    "",
    "THE ASK: one question, from the ask lines, rephrased if you like. No links, no scheduling",
    "link, no call length, no second question. The body before it asks for nothing.",
    "",
    "EVERYWHERE: plain text. No links of any kind, no bullets, no placeholders, no merge tags. No",
    "greeting, no sign-off and no opt-out line -- all three are added afterwards. Never these",
    "words: " + ", ".join(SKILL_BANNED) + ". No invented urgency, no flattery, no exclamation marks.",
    "",
    "Return JSON: body (everything before the ask, paragraphs separated by a blank line); ask;",
    "added_claim (the code of the one angle or benefit you added, or \"\" for follow-up #2); claims",
    "(the code of every approved claim the note used, the ask included); first_email_covers (the",
    "codes of the approved angles and benefits whose point the first email already made).",
])
assert "%d-%d words" % (FU1_MIN, FU1_MAX) in FOLLOWUP_SYSTEM_PROMPT and "at most %d words" % FU2_MAX in FOLLOWUP_SYSTEM_PROMPT
# Skill section 8 (amended 2026-10-03): #1's added line is said almost word for
# word, nothing added. The prompt and the per-lead task must both say it.
assert re.search(r"Add one angle or benefit from §4 that the first email did not use, in that line's own words: "
                 r"almost word for word", SKILL_DOC), (
    "skill section 8 no longer says follow-up #1's added line is used almost word for word")
assert "almost word for word" in FOLLOWUP_SYSTEM_PROMPT and "almost word for word" in js("code_followup.js"), (
    "the follow-up prompt no longer tells the model to use the added line almost word for word (skill section 8)")
assert "build the note around it" not in FOLLOWUP_SYSTEM_PROMPT + js("code_followup.js"), (
    "'build the note around it' is back in the follow-up prompt -- it invited the added sentences every held "
    "follow-up was held for (skill section 8, 2026-10-03)")


DECIDE_JS = bake("code_decide.js", SIGNATURE=SIGNATURE)
CHECK_JS = bake("code_check_smtp.js", OWN_DOMAIN=OWN_DOMAIN)
MIRROR_JS = bake("code_mirror_sent.js", OWN_DOMAIN=OWN_DOMAIN)
CLASSIFY_JS = bake("code_classify_reply.js", OWN_DOMAIN=OWN_DOMAIN)
DETECT_SIGNAL_JS = bake("code_detect_signal.js")
NOTIFY_JS = bake("code_notify.js")
FOLLOWUP_JS = bake("code_followup.js", SENDER_NAME=SENDER_NAME, FOLLOWUP_SYSTEM_PROMPT=FOLLOWUP_SYSTEM_PROMPT,
                   CLAUDE_REQUEST=FOLLOWUP_REQUEST, THERAPEUTIC_AREAS=THERAPEUTIC_AREAS)
assert '"model": "%s"' % DRAFT_MODEL in FOLLOWUP_JS, "the shipped follow-up request does not name %s" % DRAFT_MODEL
# Drafting's rule functions, verbatim, then the follow-up node body.
FOLLOWUP_ASSEMBLE_JS = (
    "// Assemble Follow-Up -- generated by n8n/sendtrack/build_workflow.py.\n"
    "// Part 1, verbatim: n8n/drafting/code_assemble.js above its 'Node body' marker -- the\n"
    "// first touch's claim rules. Part 2: n8n/sendtrack/code_followup_assemble.js.\n\n"
    + RULES_JS + "\n" + bake("code_followup_assemble.js", SIGNATURE=SIGNATURE, SENDER_NAME=SENDER_NAME)
)
HEALTH_JS = bake("code_health.js")
DIGEST_JS = bake("code_digest.js", SENDER_ZONE=SENDER_ZONE, SENDER_OFFSET=SENDER_OFFSET)

# Auto-approval (migration 014): Workflow 4's Approval Gate and Apply Claim
# Check, verbatim -- drafting's code_approval.js, and its rules section followed
# by code_approval_apply.js -- so a follow-up is judged by the same functions as
# a first touch.
# The chain itself -- gate, claim check, and the repair rounds -- comes from
# drafting's approval_chain.py, so both workflows carry the same nodes.
sys.path.insert(0, DRAFTING_DIR)
import approval_chain  # noqa: E402
APPROVAL = approval_chain.load(DRAFTING_DIR, sorted(COUNTRY_CLOCKS))
APPROVAL_JS = APPROVAL["gate"]
APPROVAL_RULES = APPROVAL["rules"]
_check_model = re.search(r"^const CHECK_MODEL = '([a-z0-9-]+)';", APPROVAL_RULES, re.M)
_check_effort = re.search(r"^const CHECK_EFFORT = '([a-z]+)';", APPROVAL_RULES, re.M)
assert _check_model and _check_model.group(1) == DRAFT_MODEL and _check_effort and _check_effort.group(1) == DRAFT_EFFORT, (
    "the claim check is not Section 3's drafting model and effort (%s, %s)" % (DRAFT_MODEL, DRAFT_EFFORT))


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def boolean_condition(cid, expr):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
        "conditions": [{
            "id": cid,
            "leftValue": expr,
            "rightValue": True,
            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
        }],
        "combinator": "and",
    }


def if_node(name, cid, expr, pos, notes):
    return {"parameters": {"conditions": boolean_condition(cid, expr), "options": {}},
            "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos, "notes": notes}


def code_node(name, src, mode, pos, notes):
    return {"parameters": {"mode": mode, "jsCode": src}, "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": pos, "notes": notes}


def pg_node(name, sql, replacement, pos, notes):
    return {"parameters": {"operation": "executeQuery", "query": sql,
                           "options": {"queryReplacement": replacement} if replacement else {}},
            "name": name, "type": "n8n-nodes-base.postgres", "typeVersion": 2.7, "position": pos,
            "credentials": PG_CRED, "notes": notes}


def email_node(name, to_expr, subject_expr, text_expr, pos, notes):
    # Section 5: "Plain text only ... No open tracking. No link tracking. No
    # pixels." emailFormat 'text' sends no HTML part at all. appendAttribution
    # MUST be explicitly false: at typeVersion 2.1 the node appends "This email
    # was sent automatically with n8n" plus a link when the option is absent --
    # verified in the installed node's send.operation.js.
    return {
        "parameters": {
            "fromEmail": FROM_HEADER,
            "toEmail": to_expr,
            "subject": subject_expr,
            "emailFormat": "text",
            "text": text_expr,
            "options": {"appendAttribution": False},
        },
        "name": name, "type": "n8n-nodes-base.emailSend", "typeVersion": 2.1, "position": pos,
        "credentials": SMTP_CRED, "onError": "continueRegularOutput", "notes": notes,
    }


def imap_trigger(name, mailbox, track_last, pos, notes):
    return {
        "parameters": {
            "mailbox": mailbox,
            # Never "Mark as Read": the operator reads this mailbox too, and the
            # workflow must not change what they see.
            "postProcessAction": "nothing",
            "format": "simple",
            "downloadAttachments": False,
            "options": {"customEmailConfig": '["ALL"]', "forceReconnect": 30, "trackLastMessageId": track_last},
        },
        "name": name, "type": "n8n-nodes-base.emailReadImap", "typeVersion": 2.2, "position": pos,
        "credentials": IMAP_CRED, "notes": notes,
    }


def http_get_node(name, url_expr, timeout_ms, pos, notes):
    # Continue-on-error so "nothing answered" reaches Assess Health as an item
    # ({error: ...}) instead of stopping the tick; never-error so a non-2xx
    # answer is judged there too, not thrown here.
    return {
        "parameters": {"url": url_expr, "options": {
            "timeout": timeout_ms, "response": {"response": {"neverError": True, "responseFormat": "json"}}}},
        "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos,
        "onError": "continueRegularOutput", "notes": notes,
    }


def edge(*targets):
    return {"main": [[{"node": t, "type": "main", "index": 0} for t in targets]]}


def branch(true_target, false_target=None):
    out = [[{"node": true_target, "type": "main", "index": 0}]]
    out.append([{"node": false_target, "type": "main", "index": 0}] if false_target else [])
    return {"main": out}


# ---------------------------------------------------------------------------
# Workflow: Send
# ---------------------------------------------------------------------------

SEND_CONFIG = {"now_override": "", "min_gap_min": 20, "send_probability": 0.4}

send_nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}},
        "name": "Every 10 Minutes", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
        "position": [-1100, 40],
        "notes": (
            "Section 9: cron, business hours only, randomised intervals. It fires around the clock on "
            "purpose: business hours are the RECIPIENT's (UTC-6 to UTC+5:30 across Section 12), so the "
            "hour filter lives in Decide Send, per lead. At most one message per tick. Activation is a UI "
            "Publish action, never the CLI (Section 3)."),
    },
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": SEND_CONFIG["now_override"], "type": "string"},
            {"id": "mingap", "name": "min_gap_min", "value": SEND_CONFIG["min_gap_min"], "type": "number"},
            {"id": "prob", "name": "send_probability", "value": SEND_CONFIG["send_probability"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 130],
        "notes": (
            "Pacing, not policy: a 20-minute floor since the mailbox's last external send, then a 40% "
            "coin flip per 10-minute tick -- irregular gaps of roughly 20-60 minutes (Section 5: 'never a "
            "synchronised burst'). The warm-up ceiling is NOT here; it is parsed from Section 5 into "
            "Decide Send and re-checked in Claim Send.\n\nnow_override must stay empty. It exists so the "
            "dry-run variant can replay a scenario at a fixed instant; the build refuses a shipped "
            "workflow with it set."),
    },
    pg_node("Load Send State", LOAD_STATE_SQL,
            "={{ [$json.now_override, $json.min_gap_min, $json.send_probability] }}", [-660, 130],
            "One row, one snapshot: the Sent-folder mirror, today's sends and claims, every approved EMAIL "
            "draft with the facts each guard needs. LinkedIn drafts are not selected -- not filtered later, "
            "never read (Section 6)."),
    code_node("Decide Send", DECIDE_JS, "runOnceForAllItems", [-440, 130],
              "Every decision, from one row and one clock: Sent mirror trustworthy -> warm-up week and ceiling "
              "(Section 5, parsed from the doc) -> per-draft checks -> recipient business hours -> pacing. "
              "Emits exactly one item: send true with one payload, or send false with the reason."),
    if_node("Send Now?", "send", "={{ $json.send }}", [-220, 130],
            "False ends the tick. The reason is in Decide Send's output for anyone reading the execution."),
    pg_node("Claim Send", CLAIM_SQL, "={{ [JSON.stringify($json.payload)] }}", [0, 40],
            "The last line of defence: channel, approval, unchanged body, verified contact, blocklist, reply "
            "and the Section 5 ceiling, all re-checked in one statement under an advisory lock, and the draft "
            "flipped to 'sent' with a claim row BEFORE the SMTP call (at-most-once)."),
    if_node("Claimed?", "claimed", "={{ $json.claimed }}", [220, 40],
            "False means the draft changed between Decide and Claim, or the recount hit the ceiling. Nothing "
            "is sent."),
    email_node("Send Email", "={{ $json.to_addr }}", "={{ $json.subject }}", "={{ $json.body }}", [440, -40],
               "SMTP -> the outreach mailbox (Section 2). Plain text, no attribution, one recipient. Continue-on-"
               "error so a failure reaches Check SMTP Result instead of stranding the claim. No retry: a timeout "
               "after the server accepted would send twice."),
    code_node("Check SMTP Result", CHECK_JS, "runOnceForEachItem", [660, -40],
              "ok -> confirm. account failure (auth, TLS, DNS, 4xx) -> back to approved, retried next tick. "
              "recipient failure (5xx on the address) -> back to pending, tagged +smtp-rejected, for a human."),
    if_node("Sent OK?", "ok", "={{ $json.ok }}", [880, -40], "Accepted by the server, with a Message-ID."),
    pg_node("Confirm Send", CONFIRM_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, -120],
            "Message-ID onto the claim, lead -> 'sent', and the send into mailbox_sent immediately so it counts "
            "before the Sent mirror notices it."),
    pg_node("Revert Claim", REVERT_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, 40],
            "Nothing went out: the claim is removed and the draft handed back."),
]

send_connections = {
    "Every 10 Minutes": edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Load Send State"),
    "Load Send State": edge("Decide Send"),
    "Decide Send": edge("Send Now?"),
    "Send Now?": branch("Claim Send"),
    "Claim Send": edge("Claimed?"),
    "Claimed?": branch("Send Email"),
    "Send Email": edge("Check SMTP Result"),
    "Check SMTP Result": edge("Sent OK?"),
    "Sent OK?": branch("Confirm Send", "Revert Claim"),
}

# ---------------------------------------------------------------------------
# Workflow: Mailbox Watch
# ---------------------------------------------------------------------------

mailwatch_nodes = [
    imap_trigger("Sent Folder", "Sent", False, [-880, -100],
                 "The warm-up ceiling's input. Reads the WHOLE Sent folder on every activation (Fetch Only New "
                 "Emails off), so manual sends made while n8n was down are counted; then each new message as it "
                 "is filed. Keyed on Message-ID, so re-reads are no-ops."),
    dict(pg_node("Load Settings", LOAD_SETTINGS_SQL, None, [-660, -100],
                 "The operator's notification address, from the settings table at runtime (sync_settings.py "
                 "writes it from .env) -- never baked into this JSON. Once per trigger batch."),
         executeOnce=True),
    code_node("Normalise Sent", MIRROR_JS, "runOnceForAllItems", [-440, -100],
              "The IMAP pre-flight's reading: when each message went, and whether it went to anyone outside "
              + OWN_DOMAIN + " other than the operator's notification address. Only those sends warm the "
              "domain, so only they count."),
    pg_node("Mirror Sent", MIRROR_SQL, "={{ [JSON.stringify($json.payload)] }}", [-220, -100],
            "Upsert into mailbox_sent; stamp mailbox_sync. The send path refuses to send until this has run "
            "at least once, and stops if its own sends stop appearing here."),
    imap_trigger("Inbox", "INBOX", True, [-880, 160],
                 "Replies, opt-outs, bounces, out-of-offices. Only new messages after the first activation; "
                 "the last UID survives restarts, so anything that arrived while n8n was down is caught up."),
    code_node("Classify Inbound", CLASSIFY_JS, "runOnceForEachItem", [-660, 160],
              "Reads only what they wrote above the quoted thread -- our own email says \"reply 'no'\" and "
              "every reply quotes it. bounce > auto-reply > opt-out > reply."),
    pg_node("Record Inbound", RECORD_INBOUND_SQL, "={{ [JSON.stringify($json.payload)] }}", [-440, 160],
            "Match to a lead, record once (keyed on Message-ID), and act: reply -> outreach_log + lead "
            "'replied' (kills follow-ups); opt-out -> that plus blocklist, permanent (Section 5); bounce -> "
            "outreach_log 'bounced'."),
    code_node("Detect Positive Signal", DETECT_SIGNAL_JS, "runOnceForEachItem", [-330, 160],
              "Deterministic keyword check (build rule 3, no model) of a reply's own text for the operator "
              "notification only -- it does not touch Record Inbound's classification. Only classification "
              "'reply' is scored; an opt-out or bounce is always 'neutral' here."),
    code_node("Build Notification", NOTIFY_JS, "runOnceForEachItem", [-220, 160],
              "HubSpot is deferred (Section 9): the operator gets a plain-text email instead, once per message."),
    if_node("Notify Operator?", "notify", "={{ $json.notify }}", [0, 160],
            "Only the first time a matched reply, opt-out or bounce is recorded -- and only if the "
            "settings table holds an operator address (sync_settings.py)."),
    email_node("Notify Operator", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}",
               [220, 160],
               "To the operator's own inbox, never the outreach mailbox (Section 9). A failure here does not "
               "undo the recording above."),
]

mailwatch_connections = {
    "Sent Folder": edge("Load Settings"),
    "Load Settings": edge("Normalise Sent"),
    "Normalise Sent": edge("Mirror Sent"),
    "Inbox": edge("Classify Inbound"),
    "Classify Inbound": edge("Record Inbound"),
    "Record Inbound": edge("Detect Positive Signal"),
    "Detect Positive Signal": edge("Build Notification"),
    "Build Notification": edge("Notify Operator?"),
    "Notify Operator?": branch("Notify Operator"),
}

# ---------------------------------------------------------------------------
# Workflow: Follow-Ups
# ---------------------------------------------------------------------------

# Every due lead costs a paid Claude call now (skill section 8), so the batch is
# bounded like Workflow 4's, not Workflow 3's.
FOLLOWUP_CONFIG = {"now_override": "", "follow_up_days": FOLLOW_UP_DAYS, "max_follow_ups": MAX_FOLLOW_UPS,
                   "batch_size": 10}
FOLLOWUP_TRIGGER = "Every 30 Minutes"

# The auto-approval chain with its repair rounds (drafting's approval_chain.py),
# the same chain Workflow 4 carries; its Assemble copies are Assemble Follow-Up.
_fu_chain_nodes, _fu_chain_conns, _fu_write_pos = approval_chain.build(
    APPROVAL, "Assemble Follow-Up", FOLLOWUP_ASSEMBLE_JS, "Write Follow-Up", ANTHROPIC_CRED, DRAFT_MODEL, DRAFT_EFFORT,
    (880, 30))
FU_CHAIN_ROUNDS = approval_chain.names(APPROVAL["max_repairs"], "Assemble Follow-Up")

followup_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
     "name": FOLLOWUP_TRIGGER, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [-880, 40],
     "notes": ("Queue-driven: a missed run just means the next one finds more due (Section 7). Every 30 minutes, "
               "not every 6 hours: the host is often up for only a few hours, and n8n's schedule trigger skips an "
               "N-hour tick whose clock hour is less than N hours after the last run's clock hour -- it compares "
               "hours of the day, not elapsed time. On 2026-10-02 that skipped the only tick in 9 days that found "
               "follow-ups due. A 30-minute interval never enters that check (build guard below).")},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-880, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": FOLLOWUP_CONFIG["now_override"], "type": "string"},
            {"id": "days", "name": "follow_up_days", "value": FOLLOW_UP_DAYS, "type": "number"},
            {"id": "max", "name": "max_follow_ups", "value": MAX_FOLLOW_UPS, "type": "number"},
            {"id": "batch", "name": "batch_size", "value": FOLLOWUP_CONFIG["batch_size"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-660, 130],
        "notes": ("follow_up_days=%d and max_follow_ups=%d are parsed out of Section 9 at build time. "
                  "now_override must stay empty (dry-run only)." % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)),
    },
    pg_node("Find Due Follow-Ups", FOLLOWUP_DUE_SQL,
            "={{ [$json.now_override, $json.follow_up_days, $json.max_follow_ups, $json.batch_size] }}",
            [-440, 130],
            "Leads at 'sent', no reply, no bounce, not blocklisted, quiet for %d days: the next follow-up, or "
            "'lost' once %d have been used." % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)),
    code_node("Build Follow-Up", FOLLOWUP_JS, "runOnceForAllItems", [-220, 130],
              "Drafting skill v3 section 8: a follow-up is composed by the drafting model under the first touch's "
              "claim rules. This builds the request -- the first email as sent, and the approved claims it did not "
              "use (a benefit sharing a capability with one it used is not offered either). No fact about the lead "
              "reaches the model except the first email, so build rule 6 holds by construction. A mark-lost row "
              "needs no model and goes straight to Write Follow-Up."),
    if_node("Needs Model?", "needsmodel", "={{ $json.needs_model }}", [0, 130],
            "true: a follow-up to compose. false: mark-lost, no model call."),
    {
        "parameters": {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "anthropicApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.request) }}",
            "options": {"timeout": 300000},
        },
        "name": "Claude Follow-Up", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [220, 30],
        "credentials": ANTHROPIC_CRED, "onError": "continueRegularOutput", "retryOnFail": True, "maxTries": 2,
        "waitBetweenTries": 5000,
        "notes": ("%s through the Anthropic Messages API, effort %s (Section 3) -- the drafting model, with the "
                  "drafting node's request parameters. The key is the n8n credential %s, never in this JSON. If "
                  "the call fails nothing is written and the lead is due again next run: no fallback to the local "
                  "model (skill section 6)." % (DRAFT_MODEL, DRAFT_EFFORT, ANTHROPIC_CRED["anthropicApi"]["id"])),
    },
    code_node("Assemble Follow-Up", FOLLOWUP_ASSEMBLE_JS, "runOnceForEachItem", [440, 30],
              "Drafting's own claim rules (code_assemble.js above its 'Node body' marker, embedded verbatim) plus "
              "skill section 8's: #1 is %d-%d words and adds exactly one angle or benefit the first email did not "
              "use; #2 is at most %d words and adds none. Violations tag the variant. The greeting, the Section 5 "
              "opt-out, the signature and the quoted first email are appended here, never generated -- the frame "
              "the send path checks for." % (FU1_MIN, FU1_MAX, FU2_MAX)),
    {
        "parameters": {"conditions": boolean_condition("writable", "={{ $json.write }}"), "options": {}},
        "name": "Drop Failed Generations", "type": "n8n-nodes-base.filter", "typeVersion": 2.2, "position": [660, 30],
        "notes": "A failed Claude call writes nothing; the lead is due again on the next run.",
    },
    *_fu_chain_nodes,
    pg_node("Write Follow-Up", FOLLOWUP_WRITE_SQL, "={{ [JSON.stringify($json.payload)] }}", _fu_write_pos,
            "Into drafts as 'pending' with hold_reason -- the exceptions queue -- or approved by this workflow "
            "when the gate and the claim check passed it (migration 014; the table re-checks). Or the lead "
            "marked lost."),
]

followup_connections = {
    FOLLOWUP_TRIGGER: edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Find Due Follow-Ups"),
    "Find Due Follow-Ups": edge("Build Follow-Up"),
    "Build Follow-Up": edge("Needs Model?"),
    "Needs Model?": branch("Claude Follow-Up", "Write Follow-Up"),
    "Claude Follow-Up": edge("Assemble Follow-Up"),
    "Assemble Follow-Up": edge("Drop Failed Generations"),
    "Drop Failed Generations": edge("Approval Gate"),
    **_fu_chain_conns,
}

# A composed follow-up reaches the write only through the gate; the only other
# way in is a mark-lost row, which carries no draft.
_into_write = sorted(src for src, outs in followup_connections.items()
                     for br in outs["main"] for e in br if e["node"] == "Write Follow-Up")
assert _into_write == sorted(approval_chain.exits(FU_CHAIN_ROUNDS) + ["Needs Model?"]), (
    "something reaches Write Follow-Up without the Approval Gate: %r" % _into_write)
assert len(FU_CHAIN_ROUNDS) == APPROVAL["max_repairs"] + 1, "the chain does not unroll MAX_REPAIRS repair rounds"
assert followup_connections["Needs Model?"]["main"][1] == [{"node": "Write Follow-Up", "type": "main", "index": 0}]

# ---------------------------------------------------------------------------
# Workflow: IMAP Health
# ---------------------------------------------------------------------------

# The checker is the imap-health service in docker-compose.yml, on the port
# imap_health_server.py listens on; both are asserted below.
_port = re.search(r"^PORT = (\d+)$", _server, re.M)
assert _port, "PORT not found in imap_health_server.py"
HEALTH_CHECKER_SERVICE = "imap-health"
HEALTH_CONFIG = {"checker_url": "http://%s:%s/check" % (HEALTH_CHECKER_SERVICE, _port.group(1)),
                 "grace_min": 15, "confirm_after": 2, "remind_hours": 6}

health_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": HEALTH_INTERVAL_MIN}]}},
     "name": "Every %d Minutes" % HEALTH_INTERVAL_MIN, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [-1100, 40],
     "notes": ("Section 9: the health check runs every %d minutes. Activation is a UI Publish action, never the "
               "CLI (Section 3). While the host sleeps nothing runs -- this included; mail that lands then is "
               "recorded when Mailbox Watch reconnects." % HEALTH_INTERVAL_MIN)},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "checker", "name": "checker_url", "value": HEALTH_CONFIG["checker_url"], "type": "string"},
            {"id": "grace", "name": "grace_min", "value": HEALTH_CONFIG["grace_min"], "type": "number"},
            {"id": "confirm", "name": "confirm_after", "value": HEALTH_CONFIG["confirm_after"], "type": "number"},
            {"id": "remind", "name": "remind_hours", "value": HEALTH_CONFIG["remind_hours"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 130],
        "notes": ("grace_min: how long a message may sit in INBOX or Sent unrecorded before it counts as missed "
                  "(the triggers record within seconds). confirm_after: consecutive failing checks before an "
                  "alert -- a host waking can tick this before Mailbox Watch reconnects. remind_hours: repeat "
                  "while it lasts. checker_url: the imap-health service (docker-compose.yml)."),
    },
    http_get_node("Check IMAP", "={{ $json.checker_url }}", 150000, [-660, 130],
                  "imap_preflight.py -- the same connection logic, unchanged -- run by the imap-health service: "
                  "a fresh read-only IMAP session, plus the Message-IDs of everything that landed in INBOX and "
                  "Sent in the last 48 hours. The n8n image has no Python, hence the service."),
    pg_node("Load Health State", HEALTH_LOAD_SQL,
            "={{ [JSON.stringify({ inbox: $json.inbox || [], sent: $json.sent || [], "
            "grace_min: $('Config').first().json.grace_min })] }}", [-440, 130],
            "Those Message-IDs against inbound_messages and mailbox_sent: anything that landed more than "
            "grace_min ago with no row was MISSED -- the proof a login alone cannot give that Mailbox Watch is "
            "still listening. Plus the previous check's state and the operator's address (settings table)."),
    code_node("Assess Health", HEALTH_JS, "runOnceForAllItems", [-220, 130],
              "Problems: checker-unreachable, imap-failed, inbox-missed, sent-missed. Alert after confirm_after "
              "consecutive failing checks, remind every remind_hours, alert again if the problem changes, one "
              "email when it clears."),
    if_node("Alert?", "notify", "={{ $json.notify }}", [0, 130],
            "Only when there is something to say AND the settings table holds an operator address."),
    email_node("Send Alert", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}", [220, 40],
               "To the operator's own inbox, never the outreach mailbox. Sent only to that address, so it counts "
               "toward no warm-up ceiling (Section 9)."),
    pg_node("Record Health", HEALTH_RECORD_SQL,
            "={{ [JSON.stringify($('Assess Health').first().json.state), JSON.stringify($json)] }}", [440, 130],
            "Every check into mailbox_health. After the alert on purpose: an alert counts as sent only when SMTP "
            "accepted it, so a failed one is retried on the next check."),
]

health_connections = {
    "Every %d Minutes" % HEALTH_INTERVAL_MIN: edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Check IMAP"),
    "Check IMAP": edge("Load Health State"),
    "Load Health State": edge("Assess Health"),
    "Assess Health": edge("Alert?"),
    "Alert?": branch("Send Alert", "Record Health"),
    "Send Alert": edge("Record Health"),
}

# ---------------------------------------------------------------------------
# Workflow: Daily Digest (migration 014)
# ---------------------------------------------------------------------------

# The first tick at or after this hour on the operator's clock: before any
# recipient's business hours open (India, the earliest, opens 08:30 PKT), so
# the operator can reject an overnight auto-approval before it goes out.
DIGEST_CONFIG = {"now_override": "", "digest_hour": 8}
DIGEST_TRIGGER = "Every 30 Minutes"

digest_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
     "name": DIGEST_TRIGGER, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [-1100, 40],
     "notes": ("Every 30 minutes, sending at most once per operator day, at the first tick at or after digest_hour. "
               "Not one daily tick: the host is often asleep, and n8n's day/hour schedules gate on the clock value of "
               "the last run (Follow-Ups, 2026-10-02). A digest that could not go out today goes at the next tick.")},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": DIGEST_CONFIG["now_override"], "type": "string"},
            {"id": "hour", "name": "digest_hour", "value": DIGEST_CONFIG["digest_hour"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 130],
        "notes": ("digest_hour: the operator's hour (%s) from which today's digest is due. now_override must stay "
                  "empty (dry-run only)." % SENDER_ZONE),
    },
    pg_node("Load Digest", DIGEST_LOAD_SQL, "={{ [$json.now_override] }}", [-660, 130],
            "One snapshot: drafts auto-approved and emails sent since the last digest, and every pending draft "
            "with its hold_reason -- the exceptions queue. Plus the operator's address and the flag."),
    code_node("Build Digest", DIGEST_JS, "runOnceForAllItems", [-440, 130],
              "Due once per operator day from digest_hour, if the settings table holds an operator address. The "
              "plain-text digest: AUTO-APPROVED, SENT, HELD FOR A PERSON (with reasons)."),
    if_node("Send Digest?", "send", "={{ $json.send }}", [-220, 130],
            "False ends the tick; Build Digest's `reason` says why (already sent today, before the hour, no address)."),
    email_node("Send Digest", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}", [0, 40],
               "To the operator's own inbox, never the outreach mailbox. Sent only to that address, so it counts "
               "toward no warm-up ceiling (Section 9)."),
    pg_node("Record Digest", DIGEST_RECORD_SQL,
            "={{ [JSON.stringify($('Build Digest').first().json.record), JSON.stringify($json)] }}", [220, 40],
            "Recorded only if SMTP accepted the digest; a failed one is sent again on the next tick."),
]

digest_connections = {
    DIGEST_TRIGGER: edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Load Digest"),
    "Load Digest": edge("Build Digest"),
    "Build Digest": edge("Send Digest?"),
    "Send Digest?": branch("Send Digest"),
    "Send Digest": edge("Record Digest"),
}

# The Load Health State payload is built in an expression: every field its SQL
# reads must be put there.
for _f in _sql_payload_reads(HEALTH_LOAD_SQL):
    assert re.search(r"\b%s:" % _f, [n for n in health_nodes if n["name"] == "Load Health State"][0]
                     ["parameters"]["options"]["queryReplacement"]), (
        "Load Health State reads $1->'%s', but its payload expression never sets it." % _f)

# The checker must be what docker-compose.yml actually runs, reachable only on
# the compose network: it logs in to the mailbox on every request, unauthenticated.
_svc = re.search(r"^  %s:\n((?:    .*\n|[ \t]*\n)+)" % re.escape(HEALTH_CHECKER_SERVICE), COMPOSE_TEXT, re.M)
assert _svc, (
    "IMAP Health calls %s, but docker-compose.yml has no '%s' service." % (HEALTH_CONFIG["checker_url"],
                                                                          HEALTH_CHECKER_SERVICE))
assert "imap_health_server.py" in _svc.group(1), (
    "the %s service in docker-compose.yml does not run imap_health_server.py" % HEALTH_CHECKER_SERVICE)
assert not re.search(r"^    ports:", _svc.group(1), re.M), (
    "the %s service publishes a port. It logs in to the mailbox on every request, unauthenticated -- keep it "
    "on the compose network only." % HEALTH_CHECKER_SERVICE)
assert 15 <= HEALTH_INTERVAL_MIN <= 30, (
    "Section 9 says the health check runs every %d minutes; it is meant to run every 15-30 minutes -- often "
    "enough to catch a dead Inbox trigger within the hour, rarely enough not to hammer the mailbox with "
    "logins." % HEALTH_INTERVAL_MIN)


# ---------------------------------------------------------------------------
# Structural guards on the shipped workflows
# ---------------------------------------------------------------------------

def _config_values(nodes):
    cfg = [n for n in nodes if n["name"] == "Config"][0]
    return {a["name"]: a["value"] for a in cfg["parameters"]["assignments"]["assignments"]}


for _nodes in (send_nodes, mailwatch_nodes, followup_nodes, health_nodes, digest_nodes):
    for _n in _nodes:
        if _n["type"] == "n8n-nodes-base.emailSend":
            p = _n["parameters"]
            assert p["options"].get("appendAttribution") is False, (
                "%s would append n8n's attribution line and link to every email." % _n["name"])
            assert p["emailFormat"] == "text" and "html" not in p, (
                "%s is not plain text. Section 5: 'Plain text only. No HTML'." % _n["name"])
            assert not _n.get("retryOnFail"), (
                "%s retries on failure. An SMTP timeout after the server accepted would send twice." % _n["name"])
        if _n["type"] == "n8n-nodes-base.emailReadImap":
            assert _n["parameters"]["postProcessAction"] == "nothing", (
                "%s would change the mailbox the operator reads." % _n["name"])
        # n8n 2.35.7's Schedule Trigger (GenericFunctions.js recurrenceCheck) gates
        # an hours, days or weeks interval > 1 on the CLOCK VALUE of the last run
        # -- (hour - lastHour + 24) % 24 >= N -- not on elapsed time, and keeps
        # that value across restarts in staticData. On a host that is up a few
        # hours at a time, a tick that lands on the same clock hour as a run days
        # earlier reads as "0 hours since" and is skipped: Follow-Ups' "Every 6
        # Hours" stored hour 0 on 2026-09-23 and skipped its only tick in 9 days,
        # at 00:00 PKT on 2026-10-02, with leads due. (Minutes and months are
        # counted absolutely in that function, so they are safe.)
        if _n["type"] == "n8n-nodes-base.scheduleTrigger":
            for _iv in _n["parameters"]["rule"]["interval"]:
                _field = _iv.get("field")
                # Added 2026-10-07: a FIXED SLOT is the other half of this. An
                # interval of 1 passes the clock-value test above, but
                # `days`/1 + triggerAtHour compiles to one cron a day at that
                # hour and nothing else -- which is how Ingestion came to fire
                # at 23:00 PKT on a laptop that is off at night, and mostly
                # never fired at all (Section 7's idempotency rule).
                for _pin in ("triggerAtHour", "triggerAtDay", "triggerAtDayOfMonth", "triggerAtMinute"):
                    assert _pin not in _iv, (
                        "%s: schedule %r pins %s, so it has one moment a day (or a week) to fire in. "
                        "The host is off at night; Section 7: 'No workflow may assume its schedule "
                        "fired.'" % (_n["name"], _iv, _pin))
                _ok = not (_field in ("hours", "days", "weeks") and int(_iv.get(_field + "Interval", 1)) > 1)
                assert _ok, (
                    "%s: schedule %r enters n8n's recurrenceCheck on a clock value (hour of day, day of year), "
                    "not elapsed time -- on a host that sleeps, a due tick is silently skipped (Follow-Ups, "
                    "2026-10-02). Use a minutes interval, or an interval of 1." % (_n["name"], _iv))

_cfg = _config_values(send_nodes)
assert _cfg["now_override"] == "", "the shipped Send workflow has a clock override set"
assert _cfg["min_gap_min"] > 0 and 0 < _cfg["send_probability"] < 1, (
    "the shipped Send workflow has pacing switched off -- Section 5 wants irregular intervals")
_cfg = _config_values(followup_nodes)
assert _cfg["now_override"] == "", "the shipped Follow-Ups workflow has a clock override set"
assert (_cfg["follow_up_days"], _cfg["max_follow_ups"]) == (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS)
_cfg = _config_values(digest_nodes)
assert _cfg["now_override"] == "", "the shipped Daily Digest workflow has a clock override set"
assert 0 <= _cfg["digest_hour"] <= 23, "digest_hour is an hour of the operator's day"
_cfg = _config_values(health_nodes)
assert set(_cfg) == HEALTH_CONFIG_FIELDS and _cfg["checker_url"] == HEALTH_CONFIG["checker_url"], (
    "the shipped IMAP Health Config is not what the build asserts against: %r" % _cfg)
assert _cfg["confirm_after"] >= 2, (
    "IMAP Health would alert on a single failing check. A host waking from sleep can tick it before Mailbox "
    "Watch reconnects (Section 9) -- that is a false alarm on every wake.")


def workflow(wid, name, nodes, connections):
    return {"id": wid, "name": name, "nodes": nodes, "connections": connections, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {}}


SHIPPED = [
    ("send.json", workflow("send0001", "Send & Track - Send", send_nodes, send_connections)),
    ("mailbox-watch.json", workflow("mailwatch0001", "Send & Track - Mailbox Watch", mailwatch_nodes, mailwatch_connections)),
    ("follow-ups.json", workflow("followup0001", "Send & Track - Follow-Ups", followup_nodes, followup_connections)),
    ("imap-health.json", workflow("imaphealth0001", "Send & Track - IMAP Health", health_nodes, health_connections)),
    ("daily-digest.json", workflow("digest0001", "Send & Track - Daily Digest", digest_nodes, digest_connections)),
]

# No literal email address in a committed workflow except the sender's own From.
# The operator's notification address -- anyone's -- is runtime data (the
# settings table), and a committed JSON is in git history for good.
ADDRESS_LITERAL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


LITERAL_ADDRESSES = {}
for _file, _wf in SHIPPED:
    for _n in _wf["nodes"]:
        for _s in _strings(_n):
            for _a in ADDRESS_LITERAL.findall(_s):
                assert _a.lower() == MAILBOX.lower(), (
                    "%s, node %r: literal email address %r. Only the sender's own From address may be baked "
                    "into a committed workflow; the operator's notification address, and anyone else's, is "
                    "read at runtime from the settings table (sync_settings.py)." % (_file, _n["name"], _a))
                LITERAL_ADDRESSES.setdefault(_a, set()).add("%s: %s" % (_file, _n["name"]))

# Every $('Node') a Code node reads must exist in its workflow -- n8n stops the
# execution there, and a renamed trigger is easy to miss.
for _file, _wf in SHIPPED:
    _names = {n["name"] for n in _wf["nodes"]}
    for _n in _wf["nodes"]:
        if _n["type"] == "n8n-nodes-base.code":
            for _ref in re.findall(r"\$\('([^']+)'\)", _n["parameters"]["jsCode"]):
                assert _ref in _names, "%s: %s reads $('%s'), but there is no node named %r" % (
                    _file, _n["name"], _ref, _ref)


def _write(path, wf):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


for _file, _wf in SHIPPED:
    _write(os.path.join(OUT_DIR, _file), _wf)


# ---------------------------------------------------------------------------
# Dry-run / test variants
# ---------------------------------------------------------------------------

_DRY_CREDS = {"postgres": PG_DRY, "smtp": SMTP_DRY}
# The Claude call has no side effect outside this machine but its cost: it reaches
# no inbox, so a dry run keeps the real credential (a follow-up composed in a
# dry run is a real API call).
_DRY_KEEPS = {"anthropicApi"}


def dry_variant(wf, wid, name, config_overrides, drop_nodes):
    """The shipped workflow with only: credentials -> scratch DB + SMTP sink,
    Config values, and schedule triggers removed. Proven below."""
    v = copy.deepcopy(wf)
    v["id"], v["name"] = wid, name
    v["nodes"] = [n for n in v["nodes"] if n["name"] not in drop_nodes]
    for n in v["nodes"]:
        for kind in list(n.get("credentials", {})):
            if kind == "imap":
                raise AssertionError("a dry-run variant must never carry the real IMAP credential")
            if kind in _DRY_KEEPS:
                continue
            n["credentials"] = dict(_DRY_CREDS[kind])
        if n["name"] == "Config":
            for a in n["parameters"]["assignments"]["assignments"]:
                if a["name"] in config_overrides:
                    a["value"] = config_overrides[a["name"]]
    v["connections"] = {k: val for k, val in v["connections"].items() if k not in drop_nodes}
    return v


def _node_diff(prod, dry):
    """Every difference between two workflows, as (node, what) pairs."""
    diffs = []
    pn = {n["name"]: n for n in prod["nodes"]}
    dn = {n["name"]: n for n in dry["nodes"]}
    for name in sorted(set(pn) - set(dn)):
        diffs.append((name, "removed"))
    for name in sorted(set(dn) - set(pn)):
        diffs.append((name, "added"))
    for name in sorted(set(pn) & set(dn)):
        a, b = copy.deepcopy(pn[name]), copy.deepcopy(dn[name])
        if a.get("credentials") != b.get("credentials"):
            diffs.append((name, "credentials"))
        a.pop("credentials", None)
        b.pop("credentials", None)
        if name == "Config":
            av = {x["name"]: x["value"] for x in a["parameters"]["assignments"]["assignments"]}
            bv = {x["name"]: x["value"] for x in b["parameters"]["assignments"]["assignments"]}
            for k in sorted(set(av) | set(bv)):
                if av.get(k) != bv.get(k):
                    diffs.append((name, "config:" + k))
            for x in a["parameters"]["assignments"]["assignments"] + b["parameters"]["assignments"]["assignments"]:
                x["value"] = None
        if a != b:
            diffs.append((name, "OTHER"))
    for src in sorted(set(prod["connections"]) | set(dry["connections"])):
        if src in dn and prod["connections"].get(src) != dry["connections"].get(src):
            diffs.append((src, "connections"))
    return diffs


def _fixture_node(name, items, pos):
    src = ("// TEST FIXTURE -- stands in for the IMAP trigger in the test variant only.\n"
           "// Items shaped exactly like the Email Trigger (IMAP) node's 'simple' format.\n"
           "return " + json.dumps([{"json": i} for i in items], ensure_ascii=False, indent=2) + ";\n")
    return code_node(name, src, "runOnceForAllItems", pos, "Test fixture. Never in a shipped workflow.")


VARIANTS = []
if VARIANTS_OUT:
    if not DRYRUN_NOW:
        raise AssertionError("SENDTRACK_VARIANTS_OUT is set but SENDTRACK_DRYRUN_NOW is not -- the dry run "
                             "replays scenarios at a fixed instant.")
    send_wf = SHIPPED[0][1]
    dry_send = dry_variant(send_wf, "send0001dry", "DRY RUN - Send (scratch DB, SMTP sink)",
                           {"now_override": DRYRUN_NOW, "min_gap_min": 0, "send_probability": 1},
                           {"Every 10 Minutes"})
    got = _node_diff(send_wf, dry_send)
    want = sorted([("Every 10 Minutes", "removed"), ("Config", "config:now_override"),
                   ("Config", "config:min_gap_min"), ("Config", "config:send_probability"),
                   ("Load Send State", "credentials"), ("Claim Send", "credentials"),
                   ("Send Email", "credentials"), ("Confirm Send", "credentials"),
                   ("Revert Claim", "credentials")])
    assert sorted(got) == want, (
        "the dry-run Send variant differs from the shipped workflow in more than credentials, Config "
        "and the schedule trigger -- the dry run would not be testing what ships:\n  %r" % sorted(got))
    VARIANTS.append(("send-dryrun.json", dry_send))

    fu_wf = SHIPPED[2][1]
    dry_fu = dry_variant(fu_wf, "followup0001dry", "DRY RUN - Follow-Ups (scratch DB)",
                         {"now_override": DRYRUN_NOW}, {FOLLOWUP_TRIGGER})
    got = _node_diff(fu_wf, dry_fu)
    want = sorted([(FOLLOWUP_TRIGGER, "removed"), ("Config", "config:now_override"),
                   ("Find Due Follow-Ups", "credentials"), ("Write Follow-Up", "credentials")])
    assert sorted(got) == want, "the dry-run Follow-Ups variant drifted: %r" % sorted(got)
    VARIANTS.append(("followups-dryrun.json", dry_fu))

    if FIXTURES:
        fx = json.loads(_read(FIXTURES))
        mw = copy.deepcopy(SHIPPED[1][1])
        imap = {n["name"] for n in mw["nodes"] if n["type"] == "n8n-nodes-base.emailReadImap"}
        assert imap == {"Sent Folder", "Inbox"}, "Mailbox Watch's triggers changed: %r" % sorted(imap)
        # Each fixture takes its trigger's NAME and position, so the shipped
        # connections stand unchanged and Normalise Sent's read of the trigger
        # by name resolves to the fixture's items exactly as it would to the
        # trigger's.
        test_nodes = [n for n in mw["nodes"] if n["name"] not in imap]
        test_nodes += [
            {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger",
             "typeVersion": 1, "position": [-1100, 30]},
            _fixture_node("Sent Folder", fx["sent"], [-880, -100]),
            _fixture_node("Inbox", fx["inbox"], [-880, 160]),
        ]
        conns = dict(mw["connections"])
        conns["Manual Trigger"] = edge("Sent Folder", "Inbox")
        mw_test = dry_variant(dict(mw, nodes=test_nodes, connections=conns), "mailwatch0001t",
                              "TEST - Mailbox Watch (fixtures, scratch DB, SMTP sink)", {}, set())
        shipped_mw = {n["name"]: n for n in SHIPPED[1][1]["nodes"]}
        for n in mw_test["nodes"]:
            if n["name"] in shipped_mw and n["name"] not in imap:
                a, b = copy.deepcopy(shipped_mw[n["name"]]), copy.deepcopy(n)
                a.pop("credentials", None)
                b.pop("credentials", None)
                assert a == b, "the Mailbox Watch test variant changed node %r" % n["name"]
        VARIANTS.append(("mailwatch-test.json", mw_test))

    health_wf = SHIPPED[3][1]
    dry_health = dry_variant(health_wf, "imaphealth0001dry", "DRY RUN - IMAP Health (scratch DB, SMTP sink)", {},
                             {"Every %d Minutes" % HEALTH_INTERVAL_MIN})
    got = _node_diff(health_wf, dry_health)
    want = sorted([("Every %d Minutes" % HEALTH_INTERVAL_MIN, "removed"), ("Load Health State", "credentials"),
                   ("Send Alert", "credentials"), ("Record Health", "credentials")])
    assert sorted(got) == want, "the dry-run IMAP Health variant drifted: %r" % sorted(got)
    VARIANTS.append(("imaphealth-dryrun.json", dry_health))

    digest_wf = SHIPPED[4][1]
    dry_digest = dry_variant(digest_wf, "digest0001dry", "DRY RUN - Daily Digest (scratch DB, SMTP sink)",
                             {"now_override": DRYRUN_NOW}, {DIGEST_TRIGGER})
    got = _node_diff(digest_wf, dry_digest)
    want = sorted([(DIGEST_TRIGGER, "removed"), ("Config", "config:now_override"), ("Load Digest", "credentials"),
                   ("Send Digest", "credentials"), ("Record Digest", "credentials")])
    assert sorted(got) == want, "the dry-run Daily Digest variant drifted: %r" % sorted(got)
    VARIANTS.append(("digest-dryrun.json", dry_digest))

    for _file, _wf in VARIANTS:
        _write(os.path.join(VARIANTS_OUT, _file), _wf)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

for _file, _wf in SHIPPED:
    print("wrote", os.path.abspath(os.path.join(OUT_DIR, _file)), "(%s)" % _wf["id"])
for _file, _wf in VARIANTS:
    print("wrote", os.path.abspath(os.path.join(VARIANTS_OUT, _file)), "(%s)" % _wf["id"])
print("  warm-up (Section 5): %s" % ", ".join("week %d%s=%d/day" % (w, "+" if i == len(WARMUP) - 1 else "", n)
                                            for i, (w, n) in enumerate(WARMUP)))
print("  sender clock: %s (%+d min) -- the day the ceiling counts" % (SENDER_ZONE, SENDER_OFFSET))
print("  business-hours clocks (Section 12): %d countries -- %d core, %d extended; every one of the %d the index snapshot includes has one"
      % (len(COUNTRY_CLOCKS), len(CLOCK_CORE), len(COUNTRY_CLOCKS) - len(CLOCK_CORE), len(INDEX_COUNTRIES)))
print("  opt-out keywords (Sections 5+9): %r" % OPT_OUT_KEYWORDS)
print("  follow-ups (Section 9): after %d days, maximum %d; composed by %s (effort %s) under drafting's rules, "
      "#1 %d-%d words, #2 at most %d (skill section 8)" % (FOLLOW_UP_DAYS, MAX_FOLLOW_UPS, DRAFT_MODEL, DRAFT_EFFORT,
                                                          FU1_MIN, FU1_MAX, FU2_MAX))
print("  From: %s    own domain: %s" % (FROM_HEADER, OWN_DOMAIN))
print("  signature checked on every body: %r" % SIGNATURE)
print("  operator notification address: read at runtime from settings.operator_email "
      "(sync_settings.py) -- not baked in")
print("  IMAP health (Section 9): every %d min -> %s; problems %r" % (
    HEALTH_INTERVAL_MIN, HEALTH_CONFIG["checker_url"], HEALTH_PROBLEMS))
print("  literal email addresses in the shipped JSON: %s" % (
    "; ".join("%s (%s)" % (a, ", ".join(sorted(w))) for a, w in sorted(LITERAL_ADDRESSES.items())) or "none"))

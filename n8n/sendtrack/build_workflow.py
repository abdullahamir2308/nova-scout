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
# Send Reply posts to the smtp-send sidecar by hostname in production; a dry
# run cannot reach that container's real mailbox credentials, so its Config's
# send_url is overridden to a loopback sink instead (reply_dryrun.py runs one).
SENDREPLY_SINK_URL = os.environ.get("SENDTRACK_SENDREPLY_SINK_URL", "http://127.0.0.1:18766/send")


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


def with_iso(sql):
    """ISO(x) in a statement here -> iso(x), so every clock value leaves in one format.

    Fixed 2026-10-09: a stray literal backspace byte had landed inside this
    function's own regex (between `r"` and `ISO`), so it matched nothing at
    all -- every ISO(...) call in every statement below was shipping as
    literal, uppercase, unsubstituted text, and Postgres has no such function.
    The `(?:\\(\\))?` tail is new on top of that: `ISO(now())`, the one call
    site whose argument is a function call rather than a dotted column
    reference, still would not have matched the plain `[\\w.]+` class. Found
    by reply_dryrun.py's first real execution against a real Postgres --
    no earlier guard runs the generated SQL at all, only reads its text."""
    return re.sub(r"ISO\(([\w.]+(?:\(\))?)\)", lambda m: iso(m.group(1)), sql)


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
     -- A reply belongs to the reply lane (Workflow 7), which skips the ceiling
     -- and business hours and answers the address that wrote rather than
     -- contacts.email. Excluded here rather than filtered later, so the cold
     -- path never reads one: two independent guards would already have stopped
     -- it (a replied lead's status, and outreach_log.replied), but "the cold
     -- path cannot see a reply" is the statement worth being able to make.
     AND coalesce(d.variant, '') NOT LIKE 'reply/%'
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
     AND coalesce(d.variant, '') NOT LIKE 'reply/%'   -- the reply lane's, not this one's (Workflow 7)
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
--
-- A MESSAGE FROM THE OPERATOR IS NEVER MATCHED TO A LEAD -- added 2026-10-09
-- with the reply assistant (Section 9, Workflow 7), and this is the guard that
-- makes a one-word "no" from the operator harmless. The operator answers the
-- reply-review email from this same mailbox, and that answer is a reply INSIDE
-- the prospect's thread: the review email quotes it, so References carries our
-- own Message-ID and the `thread` matcher below -- the strongest one -- would
-- tie the operator's message to the prospect's lead. "No" is Section 5's
-- opt-out word, so the lead's domain would be blocklisted permanently, with no
-- error anywhere. A command-shaped message never reaches this statement at all
-- (Apply Operator Command handles it first); this catches everything else the
-- operator sends here. Such a message is still RECORDED -- `lead_id` NULL,
-- classification 'unmatched' -- so it is visible and idempotent, but nothing
-- is blocklisted, no lead advances and nobody is notified.
WITH p AS (
  SELECT $1::jsonb AS p
),
op AS (
  SELECT (SELECT value FROM settings WHERE key = 'operator_email') AS addr
),
from_operator AS (
  SELECT op.addr IS NOT NULL AND lower(op.addr) = lower(p.p->>'from_addr') AS yes
    FROM p, op
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
    ) h, from_operator f
   WHERE NOT f.yes
   ORDER BY h.rank, h.lead_id
   LIMIT 1
),
ins AS (
  INSERT INTO inbound_messages (message_id, received_at, from_addr, subject, lead_id, matched_by,
                                classification, opt_out_keyword, body_excerpt,
                                in_reply_to, references_raw, thread_ids)
  SELECT p.p->>'message_id', (p.p->>'received_at')::timestamptz, p.p->>'from_addr', p.p->>'subject',
         (SELECT lead_id FROM hit), (SELECT how FROM hit),
         CASE WHEN (SELECT lead_id FROM hit) IS NULL THEN 'unmatched' ELSE p.p->>'classification' END,
         p.p->>'opt_out_keyword', p.p->>'body_excerpt',
         -- Migration 017: stored as received, because a reply draft has to
         -- thread to the conversation it answers and these were parsed and
         -- thrown away until 2026-10-08.
         p.p->>'in_reply_to', p.p->>'references_raw',
         coalesce(ARRAY(SELECT jsonb_array_elements_text(p.p->'thread_ids')), '{}')
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

COMMAND_CONTEXT_SQL = with_iso("""-- Load Command Context: the three things Decide Command cannot read off the
-- message itself (Section 9, Workflow 7). READ-ONLY -- it changes nothing, so a
-- message that turns out not to be an operator command has cost one SELECT.
--
-- $1 { msg: Classify Inbound's payload, cmd: its operator-command parse }
--
--   * the operator's address, from `settings` at runtime (migration 008). It is
--     never baked into workflow JSON, which is exactly why the sender check
--     cannot live in code_operator_command.js.
--   * the one-time code, if the message quoted one: does it exist, has it been
--     used, has it expired, and which draft is it for (migration 017).
--   * that draft's state and its current text, which an EDIT reframes.
--
-- Nothing here decides anything. Decide Command judges, and Apply Operator
-- Command re-derives all three before it writes -- a bug in between can refuse
-- a real approval, never accept a forged one.
WITH p AS (
  SELECT $1::jsonb AS p
)
SELECT p.p->'msg'                                              AS payload,
       p.p->'cmd'                                              AS command,
       (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email,
       ISO(now())                                              AS now,
       a.code IS NOT NULL                                      AS code_found,
       ISO(a.used_at)                                          AS code_used_at,
       ISO(a.expires_at)                                       AS code_expires_at,
       a.kind                                                  AS code_kind,
       a.outcome                                               AS code_outcome,
       a.draft_id                                              AS draft_id,
       a.lead_id                                               AS code_lead_id,
       d.status                                                AS draft_status,
       d.channel                                               AS draft_channel,
       d.variant                                               AS draft_variant,
       coalesce(d.edited_body, d.body)                         AS draft_body
  FROM p
  LEFT JOIN reply_approvals a ON a.code = upper(p.p->'cmd'->>'code')
  LEFT JOIN drafts d          ON d.id = a.draft_id;""")

COMMAND_APPLY_SQL = """-- Apply Operator Command: record what arrived, and do what it is allowed to do.
--
-- $1 Decide Command's verdict (with the message payload and the parse on it)
--
-- THE RECORD COMES FIRST IN IMPORTANCE, not in the statement: every
-- command-shaped message the mailbox receives gets a row in operator_commands,
-- accepted or refused, keyed on Message-ID so a re-delivery acts once -- the
-- role inbound_messages plays for a prospect message. The refused rows are the
-- point (migration 017): a spoofed sender, a failed SPF or DKIM, an unknown,
-- expired or reused code.
--
-- EVERYTHING THAT GRANTS AUTHORITY IS RE-DERIVED HERE and nothing is taken from
-- the item, exactly as Claim Send re-checks what Decide Send decided:
--   * the operator address comes from `settings`, again;
--   * `sender_ok` is computed, not read;
--   * the code must still be usable, through reply_approval_usable() --
--     migration 017's single definition of "may be acted on now";
--   * the draft must still be the pending draft that code belongs to.
-- Migration 017's CHECK then refuses to store `accepted` at all unless SPF,
-- DKIM and the sender all passed, so a bug in the workflow can refuse a real
-- approval and cannot accept a forged one.
--
-- The code is marked used ONLY when the draft actually moved, so a refusal --
-- including an EDIT that breaks Section 5's one-URL rule -- leaves the code
-- good for another try.
--
-- `payload` is handed back unchanged: a command-shaped message that did NOT
-- come from the operator falls through to Record Inbound, because it may well
-- be a prospect writing (`handled` is false for it).
WITH p AS (
  SELECT $1::jsonb AS p
),
j AS (
  SELECT p.p->'payload'                                        AS payload,
         p.p->'payload'->>'message_id'                         AS message_id,
         (p.p->'payload'->>'received_at')::timestamptz          AS received_at,
         lower(p.p->'payload'->>'from_addr')                    AS from_addr,
         left(coalesce(p.p->'payload'->>'subject', ''), 300)    AS subject,
         nullif(p.p->>'command', '')                            AS command,
         nullif(upper(coalesce(p.p->>'code', '')), '')          AS code,
         coalesce((p.p->>'spf_pass')::boolean, false)           AS spf_pass,
         coalesce((p.p->>'dkim_pass')::boolean, false)          AS dkim_pass,
         (p.p->>'refused_reason')                               AS refused_reason,
         (p.p->>'replacement')                                  AS replacement,
         (p.p->>'edited_body')                                  AS edited_body,
         (p.p->>'draft_id')::bigint                             AS draft_id,
         (p.p->>'outcome')                                      AS outcome,
         p.p->>'code_kind'                                      AS code_kind,
         (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email
    FROM p
),
ok AS (
  SELECT j.*,
         (j.spf_pass AND j.dkim_pass AND j.operator_email IS NOT NULL
          AND lower(j.operator_email) = j.from_addr)            AS sender_ok,
         (j.refused_reason IS NULL AND j.command IS NOT NULL AND j.code IS NOT NULL
          AND j.spf_pass AND j.dkim_pass AND j.operator_email IS NOT NULL
          AND lower(j.operator_email) = j.from_addr
          AND reply_approval_usable(j.code))                    AS may_act
    FROM j
),
used AS (
  UPDATE reply_approvals a
     SET used_at            = now(),
         used_by_message_id = ok.message_id,
         outcome            = ok.outcome
    FROM ok
   WHERE a.code = ok.code
     AND ok.may_act
     AND a.used_at IS NULL
     AND a.draft_id = ok.draft_id
  RETURNING a.code, a.draft_id, a.outcome, a.kind
),
act AS (
  UPDATE drafts d
     SET status        = CASE WHEN u.outcome = 'rejected' THEN 'rejected' ELSE 'approved' END,
         reject_reason = CASE WHEN u.outcome = 'rejected' THEN 'bad draft' ELSE d.reject_reason END,
         edited_body   = CASE WHEN u.outcome = 'edited' THEN ok.edited_body ELSE d.edited_body END
    FROM used u, ok
   WHERE d.id = u.draft_id
     AND d.status = 'pending'
  RETURNING d.id, d.status, d.variant
),
rec AS (
  INSERT INTO operator_commands (message_id, received_at, from_addr, subject, command, code,
                                 spf_pass, dkim_pass, sender_ok, accepted, refused_reason,
                                 replacement, draft_id)
  SELECT ok.message_id, coalesce(ok.received_at, now()), ok.from_addr, nullif(ok.subject, ''),
         ok.command, ok.code, ok.spf_pass, ok.dkim_pass, ok.sender_ok,
         (SELECT count(*) FROM act) = 1,
         CASE WHEN (SELECT count(*) FROM act) = 1 THEN NULL
              ELSE coalesce(ok.refused_reason,
                            'the code or the draft changed between the read and the write') END,
         CASE WHEN ok.command = 'EDIT' THEN ok.replacement END,
         ok.draft_id
    FROM ok
  ON CONFLICT (message_id) DO NOTHING
  RETURNING message_id, accepted, refused_reason
)
SELECT (SELECT count(*) FROM rec) = 1                     AS recorded,
       (SELECT count(*) FROM act) = 1                     AS accepted,
       -- Re-derived, not carried over from the item: "this is the operator's
       -- own mail" is the decision that stops a prospect's reply being
       -- recorded, so it is made from `settings` here too.
       (ok.sender_ok AND ok.spf_pass AND ok.dkim_pass)     AS handled,
       -- An acknowledgement goes only to an authenticated operator, and only
       -- the first time this Message-ID is seen: a re-delivery records nothing
       -- new and must not answer twice.
       (ok.sender_ok AND ok.spf_pass AND ok.dkim_pass
        AND (SELECT count(*) FROM rec) = 1)               AS notify,
       ok.operator_email                                  AS notify_to,
       ok.message_id                                      AS message_id,
       ok.subject                                         AS subject,
       ok.command                                         AS command,
       ok.code                                            AS code,
       ok.code_kind                                       AS code_kind,
       ok.spf_pass                                        AS spf_pass,
       ok.dkim_pass                                       AS dkim_pass,
       ok.sender_ok                                       AS sender_ok,
       CASE WHEN (SELECT count(*) FROM act) = 1 THEN NULL
            ELSE coalesce(ok.refused_reason,
                          'the code or the draft changed between the read and the write') END
                                                          AS refused_reason,
       ok.draft_id                                        AS draft_id,
       (SELECT status FROM act)                           AS draft_status,
       (SELECT variant FROM act)                          AS draft_variant,
       CASE WHEN (SELECT count(*) FROM act) = 1 THEN ok.edited_body END AS edited_body,
       (SELECT outcome FROM used)                         AS outcome,
       ok.payload                                         AS payload
  FROM ok;"""

REPLY_QUEUE_SQL = with_iso("""-- Find Replies To Answer -- the Reply Assistant's queue (Section 9, Workflow 7).
-- READ-ONLY, one row per prospect message that still needs an answer.
--
-- $1 now_override ('' in the shipped workflow), $2 batch_size
--
-- THE QUEUE IS inbound_messages, NOT leads, and that is the whole of its
-- idempotency: one drafted reply per prospect MESSAGE, for ever, because a
-- reply_approvals row keyed to that Message-ID is proof one was drafted --
-- whatever became of it. So a rejected reply is never redrafted (delete its
-- reply_approvals row to ask for another), an expired code does not cause a
-- second draft, and a prospect who writes again gets a new answer because that
-- is a new Message-ID. Section 7's rule holds too: a missed run just means the
-- next one finds the same message waiting.
--
-- Only `reply` is queued. An auto-reply, a bounce and an opt-out are not a
-- person asking something (Classify Inbound's precedence), and an opt-out most
-- certainly does not get answered.
--
-- The blocklist is checked HERE as well as in the send lane, so a prospect who
-- replied and then opted out costs no model call at all.
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
q AS (
  SELECT i.message_id, i.received_at, i.from_addr, i.subject, i.in_reply_to, i.references_raw,
         i.thread_ids, i.body_excerpt, i.lead_id
    FROM inbound_messages i
    JOIN leads l ON l.id = i.lead_id
   WHERE i.classification = 'reply'
     AND i.lead_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM reply_approvals a WHERE a.inbound_message_id = i.message_id)
     AND NOT __LEAD_BLOCKED__
     AND NOT __FROM_BLOCKED__
   ORDER BY i.received_at, i.message_id
   LIMIT $2::int
)
SELECT q.message_id                                                     AS inbound_message_id,
       ISO(q.received_at)                                               AS received_at,
       q.from_addr                                                      AS from_addr,
       q.subject                                                        AS subject,
       q.in_reply_to                                                    AS in_reply_to,
       q.references_raw                                                 AS references_raw,
       q.thread_ids                                                     AS thread_ids,
       -- What they wrote, above the quoted thread. outreach_log.reply_body has
       -- it to 4,000 characters but holds the FIRST reply on that lead for
       -- ever (Record Inbound coalesces), so it is used only when its
       -- replied_at is this very message; otherwise the 600-character excerpt
       -- in inbound_messages, which is per message and therefore always about
       -- the right one.
       coalesce((SELECT o.reply_body FROM outreach_log o
                  WHERE o.lead_id = q.lead_id AND o.reply_body IS NOT NULL
                    AND o.replied_at = q.received_at
                  ORDER BY o.sent_at DESC LIMIT 1), q.body_excerpt)      AS reply_text,
       l.id                                                             AS lead_id,
       l.company_name                                                   AS company_name,
       l.domain                                                         AS domain,
       l.country                                                        AS country,
       l.source                                                         AS source,
       -- A name only when the person who wrote IS the contact on file; the
       -- display name is not stored and a first name guessed out of a local
       -- part would be an invented fact in the greeting.
       (SELECT c.name FROM contacts c
         WHERE c.lead_id = l.id AND lower(c.email) = lower(q.from_addr) AND coalesce(c.name, '') <> ''
         LIMIT 1)                                                       AS their_name,
       -- Our own side of the thread, oldest first, exactly as it was sent.
       (SELECT coalesce(jsonb_agg(jsonb_build_object(
                  'subject', d.subject, 'body', o.message_body,
                  'sent_at', ISO2(o.sent_at), 'message_id', o.message_id,
                  'variant', d.variant) ORDER BY o.sent_at), '[]'::jsonb)
          FROM outreach_log o
          LEFT JOIN drafts d ON d.id = o.draft_id
         WHERE o.lead_id = l.id AND o.channel = 'email' AND o.message_id IS NOT NULL)
                                                                        AS thread,
       (SELECT coalesce(jsonb_agg(jsonb_build_object(
                  'code', k.code, 'slot', k.slot, 'body', btrim(k.body),
                  'countries', to_jsonb(k.countries), 'measured', k.measured,
                  'confirmed', k.confirmed, 'active', k.active,
                  'capabilities', to_jsonb(k.capabilities)) ORDER BY k.code), '[]'::jsonb)
          FROM claims_library k
         WHERE k.active AND k.confirmed)                                AS library,
       e.therapeutic_areas                                              AS therapeutic_areas,
       e.phases                                                         AS phases,
       e.raw_extraction->>'city'                                        AS city,
       e.founder_name                                                   AS founder_name,
       e.employee_estimate                                              AS employee_estimate
  FROM q
  JOIN leads l ON l.id = q.lead_id
  LEFT JOIN enrichments e ON e.lead_id = l.id
 ORDER BY q.received_at, q.message_id;""").replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
).replace(
    "__FROM_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(split_part(i.from_addr, '@', 2))")
).replace("ISO2(o.sent_at)", iso("o.sent_at"))

REPLY_WRITE_SQL = with_iso("""-- Write Reply Draft & Issue Code: the drafted reply into `drafts` as PENDING,
-- and its one-time code into reply_approvals (Section 9, Workflow 7).
--
-- $1 Assemble Reply's payload, with the `review` block the email shows
--
-- A REPLY IS ALWAYS PENDING. Nothing here can approve it: no claim check runs
-- on a reply, the operator reads every one, and migration 018 puts that on the
-- table as well -- a `reply/` draft cannot become `approved` without a
-- one-time code, and `approved_by = 'auto'` on one is refused by name.
--
-- The code comes from reply_approval_issue() (migration 018), which is the only
-- definition of how a code is minted, reused and reaped. Writing one here by
-- hand would have to know that 017's partial unique index allows exactly one
-- unused code per draft, and that an expired unused one blocks its own
-- replacement until it is reaped.
--
-- The NOT EXISTS is the same idempotency the queue uses, re-checked at the
-- write: two runs overlapping on one prospect message write one draft.
--
-- `hold_reason` is set to the waiting command on purpose: the Daily Digest's
-- HELD FOR A PERSON section reads that column, so a drafted reply nobody has
-- answered shows up there with its code, next to everything else waiting.
WITH p AS (
  SELECT $1::jsonb AS p
),
ins AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status)
  SELECT (p.p->>'lead_id')::bigint, p.p->'draft'->>'channel', p.p->'draft'->>'variant',
         p.p->'draft'->>'subject', p.p->'draft'->>'body', 'pending'
    FROM p
   WHERE NOT EXISTS (SELECT 1 FROM reply_approvals a
                      WHERE a.inbound_message_id = p.p->>'inbound_message_id')
  RETURNING id, lead_id, subject, body
),
code AS (
  SELECT ins.id AS draft_id,
         reply_approval_issue('reply', ins.id, ins.lead_id, p.p->>'inbound_message_id') AS code
    FROM ins, p
),
held AS (
  UPDATE drafts d
     SET hold_reason = 'reply: waiting for your APPROVE / REJECT / EDIT ' || code.code
    FROM code
   WHERE d.id = code.draft_id
  RETURNING d.id, d.hold_reason
),
a AS (
  SELECT r.code, r.kind, r.draft_id, r.lead_id, r.issued_at, r.expires_at
    FROM reply_approvals r
   WHERE r.code = (SELECT code FROM code)
)
SELECT (SELECT count(*) FROM ins) = 1        AS written,
       (SELECT id FROM ins)                  AS draft_id,
       (p.p->>'lead_id')::bigint             AS lead_id,
       (SELECT code FROM code)               AS code,
       ISO(a.issued_at)                      AS issued_at,
       ISO(a.expires_at)                     AS expires_at,
       (SELECT subject FROM ins)             AS subject,
       (SELECT body FROM ins)                AS body,
       (SELECT hold_reason FROM held)        AS hold_reason,
       p.p->'review'                         AS review,
       p.p->>'inbound_message_id'            AS inbound_message_id,
       (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email
  FROM p
  LEFT JOIN a ON true;""")

REMINDER_DUE_SQL = with_iso("""-- Find Due Reminders -- Section 9, Workflow 7: "Remind me after 4 hours."
-- READ-ONLY. One row per open code the operator has not answered.
--
-- $1 now_override ('' in the shipped workflow), $2 remind_hours
--
-- `reminded_at` means "when the operator was last TOLD about this code, by any
-- email" -- the reply-review email stamps it through the same statement a
-- reminder does (Record Review Sent / Record Reminder), and so does the digest
-- for the codes it carried. So NULL means nobody has been told at all, which is
-- due at once: that is the self-heal for a review email or a digest SMTP
-- refused, and it is why this is not simply `coalesce(reminded_at, issued_at)`.
-- Otherwise each reminder lands remind_hours after the last -- the shape of the
-- IMAP health check's 6-hourly reminder while a problem lasts.
--
-- Nothing is due once the code has expired: at that point there is nothing the
-- operator could do with it, and the next digest or the next reply mints a
-- fresh one.
--
-- BOTH KINDS, because both are the operator holding the same kind of decision:
-- a `reply` drafted for a prospect who wrote to us, and an `email-hold` -- a
-- first touch or follow-up the claim check held after its repairs, which the
-- digest listed with a code of its own.
--
-- The draft must still be pending: one rejected or approved in the NocoDB grid
-- needs no nudge, even if its code is technically still open.
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
)
SELECT a.code                                 AS code,
       a.kind                                 AS kind,
       a.draft_id                             AS draft_id,
       a.lead_id                              AS lead_id,
       ISO(a.issued_at)                       AS issued_at,
       ISO(a.expires_at)                      AS expires_at,
       ISO(a.reminded_at)                     AS reminded_at,
       ISO(clk.now)                           AS now,
       d.channel                              AS channel,
       d.variant                              AS variant,
       d.subject                              AS subject,
       coalesce(d.edited_body, d.body)        AS body,
       d.hold_reason                          AS hold_reason,
       l.company_name                         AS company_name,
       l.domain                               AS domain,
       l.country                              AS country,
       i.body_excerpt                         AS their_text,
       (SELECT value FROM settings WHERE key = 'operator_email') AS operator_email
  FROM reply_approvals a
  JOIN drafts d ON d.id = a.draft_id
  JOIN leads l  ON l.id = a.lead_id
  LEFT JOIN inbound_messages i ON i.message_id = a.inbound_message_id
  CROSS JOIN clk
 WHERE a.used_at IS NULL
   AND a.expires_at > clk.now
   AND d.status = 'pending'
   AND (a.reminded_at IS NULL OR a.reminded_at <= clk.now - make_interval(hours => $2::int))
 ORDER BY a.issued_at, a.code
 LIMIT 20;""")

REMINDER_STAMP_SQL = """-- Record Review Sent / Record Reminder -- one statement for both, so "the
-- operator has been told about this code" can only mean one thing.
--
-- Stamps reminded_at, and only once SMTP accepted the message, so one that could
-- not go out is sent again on the next tick -- the rule the Daily Digest and the
-- IMAP health alert already follow. An unstamped code reads as "nobody has been
-- told", which Find Due Reminders treats as due at once.
--
-- $1 the sender's `record` ({code}); $2 the email node's output (nodemailer's
--    info, or {error} -- the node continues on error)
WITH p AS (
  SELECT $1::jsonb AS d, $2::jsonb AS r
),
upd AS (
  UPDATE reply_approvals a
     SET reminded_at = now()
    FROM p
   WHERE a.code = p.d->>'code'
     AND a.used_at IS NULL
     AND p.r->>'error' IS NULL
     AND jsonb_typeof(p.r->'accepted') = 'array'
     AND jsonb_array_length(p.r->'accepted') > 0
  RETURNING a.code, a.reminded_at
)
SELECT p.d->>'code'                   AS code,
       (SELECT count(*) FROM upd)::int AS recorded,
       p.r->>'error'                  AS error
  FROM p;"""

# The candidate row Decide Reply Send sees. Kept as a list, like
# CANDIDATE_COLUMNS, so the wiring guard knows exactly which fields exist.
REPLY_CANDIDATE_COLUMNS = [
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
    ("a.code", "code"),
    ("a.used_at IS NOT NULL", "approval_used"),
    ("a.outcome", "approval_outcome"),
    # The reply goes back to the address that WROTE, not to contacts.email --
    # measured on lead 26, where the person who answered was not the address we
    # wrote to (Section 9, Workflow 6).
    ("i.from_addr", "to_addr"),
    ("i.message_id", "their_message_id"),
    ("i.references_raw", "references_raw"),
    ("i.thread_ids", "thread_ids"),
    ("i.message_id IS NOT NULL", "inbound_exists"),
    ("(SELECT coalesce(array_agg(o.message_id ORDER BY o.sent_at), '{}') FROM outreach_log o "
     "WHERE o.lead_id = d.lead_id AND o.channel = 'email' AND o.message_id IS NOT NULL)", "our_message_ids"),
    (BLOCKED_DOMAIN.format(d="lower(l.domain)"), "lead_domain_blocked"),
    (BLOCKED_DOMAIN.format(d="lower(split_part(i.from_addr, '@', 2))"), "recipient_domain_blocked"),
]

REPLY_SEND_STATE_SQL = """-- Load Reply Send State: every approved REPLY draft, with what each guard needs
-- (Section 9, Workflow 7). One statement, one snapshot, one clock.
--
-- $1 now_override -- '' in the shipped workflow (checked at build time).
--
-- There is no warm-up history here and no business-hours input, because a reply
-- is not cold outreach: it skips the ceiling, the recipient's business hours and
-- the pacing floor (code_reply_decide.js says why for each). What it does not
-- skip is in this query -- the blocklist on both domains, a used approval code
-- with an approve-or-edit outcome, and the thread headers it must carry.
--
-- `d.variant LIKE 'reply/%' ` is the whole boundary between the two lanes: the
-- cold Load Send State excludes exactly that, so no draft can be seen by both
-- and the two lanes can never disagree about which rules apply to one.
WITH clk AS (
  SELECT coalesce(nullif($1, '')::timestamptz, now()) AS now
),
cand AS (
  SELECT __REPLY_CANDIDATE_COLUMNS__
    FROM drafts d
    JOIN leads l ON l.id = d.lead_id
    LEFT JOIN reply_approvals a   ON a.draft_id = d.id
    LEFT JOIN inbound_messages i  ON i.message_id = a.inbound_message_id
   WHERE d.channel = 'email'          -- Section 6: LinkedIn is sent by a human. Always.
     AND d.status = 'approved'
     AND coalesce(d.variant, '') LIKE 'reply/%'
   ORDER BY d.id
   LIMIT 50
)
SELECT __ISO_CLOCK__ AS clock,
       (SELECT coalesce(json_agg(row_to_json(cand)), '[]'::json) FROM cand) AS candidates,
       (SELECT coalesce(json_object_agg(k, n), '{}'::json)
          FROM (SELECT coalesce(status, '?') AS k, count(*) AS n
                  FROM drafts WHERE coalesce(variant, '') LIKE 'reply/%' GROUP BY 1) s) AS replies_by_status;"""

REPLY_SEND_STATE_SQL = (
    REPLY_SEND_STATE_SQL
    .replace("__REPLY_CANDIDATE_COLUMNS__", ",\n         ".join("%s AS %s" % c for c in REPLY_CANDIDATE_COLUMNS))
    .replace("__ISO_CLOCK__", iso("SELECT now FROM clk"))
)

REPLY_CLAIM_SQL = """-- Claim Reply Send: the reply lane's last line of defence, in one statement.
--
-- Decide Reply Send chose this draft moments ago. Everything that makes the send
-- permissible is re-checked HERE, because this is where the draft actually
-- flips -- a bug in Decide can make the lane send less, never more:
--   Section 6   channel = 'email', and the variant really is a reply
--   Workflow 7  the one-time code was used, with outcome approved or edited,
--               and it belongs to THIS draft (migration 017/018)
--   Workflow 5  status = 'approved', and the body unchanged since Decide read it
--   Section 5   neither the lead's domain nor the address being answered is
--               blocklisted -- the one guard a reply never skips
--               and the lead really did reply
--
-- No advisory lock and no ceiling recount, and that is the difference from Claim
-- Send: the cold path's lock exists so two overlapping ticks cannot both read
-- "4 of 5" and both send. A reply has no quota to race over. What it does share
-- is at-most-once: the draft flips to 'sent' and the outreach_log claim row is
-- written BEFORE the SMTP call, so a crash between sending and logging leaves a
-- claim rather than an approved draft that goes out again next tick.
WITH p AS (
  SELECT $1::jsonb AS p
),
d AS (
  UPDATE drafts d
     SET status = 'sent'
    FROM p, leads l, reply_approvals a, inbound_messages i
   WHERE d.id = (p.p->>'draft_id')::bigint
     AND d.channel = 'email'
     AND coalesce(d.variant, '') LIKE 'reply/%'
     AND d.status = 'approved'
     AND coalesce(d.edited_body, d.body) = p.p->>'raw_body'
     AND l.id = d.lead_id
     AND l.status = 'replied'
     AND a.draft_id = d.id
     AND a.code = p.p->>'code'
     AND a.used_at IS NOT NULL
     AND a.outcome IN ('approved', 'edited')
     AND i.message_id = a.inbound_message_id
     AND lower(i.from_addr) = lower(p.p->>'to_addr')
     AND NOT __LEAD_BLOCKED__
     AND NOT __RCPT_BLOCKED__
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
       p.p->>'in_reply_to'                 AS in_reply_to,
       p.p->'references'                   AS references,
       p.p->>'clock'                       AS clock
  FROM p;""".replace(
    "__LEAD_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(l.domain)")
).replace(
    "__RCPT_BLOCKED__", BLOCKED_DOMAIN.format(d="lower(split_part(i.from_addr, '@', 2))")
)

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
-- $1 now_override ('' in the shipped workflow), $2 digest_hour
--
-- NOT QUITE READ-ONLY SINCE 2026-10-09, and this is the one thing to know about
-- it: when a digest is actually due, every pending EMAIL draft it is about to
-- list gets a one-time approval code (`reply_approval_issue`, migration 018),
-- so the operator can approve, reject or edit a held email by replying to the
-- digest exactly as they do for a drafted reply (Section 9, Workflow 7). The
-- codes are minted here because the email has to carry them, and $2 is read
-- here so that happens ONLY on the tick that sends -- a code whose 48 hours
-- start on a tick that sent nothing would be half spent before anyone saw it.
-- Build Digest re-derives due-ness from the same two inputs, so the two cannot
-- disagree. reply_approval_issue reuses an open code, so a second digest about
-- the same held draft carries the same one, and a failed send re-issues
-- nothing.
--
-- A low-context note and a no-send-clock note get no code: neither can ever be
-- sent (Workflow 5), so approving one would mean nothing. A LinkedIn DM gets
-- none either -- Section 6, a person sends those by hand.
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
                 ISO(d.created_at) AS created_at, d.created_at >= win.from_ts AS new,
                 CASE WHEN d.channel = 'email'
                       AND NOT EXISTS (SELECT 1 FROM digest_log g WHERE g.digest_day = win.day)
                       AND win.hour >= $2::int
                       AND coalesce(d.variant, '') NOT LIKE 'low-context%'
                       AND coalesce(d.variant, '') NOT LIKE 'no-send-clock%'
                      THEN reply_approval_issue('email-hold', d.id, d.lead_id) END AS code
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
),
-- The codes this digest carried: the digest IS how the operator was told about
-- them, so they are stamped exactly as a reply-review email or a reminder
-- stamps its own (Workflow 7). Without this the reminder lane would read them
-- as never mentioned and nudge about every held draft half an hour later.
told AS (
  UPDATE reply_approvals a
     SET reminded_at = now()
    FROM p
   WHERE a.code IN (SELECT jsonb_array_elements_text(p.d->'coded'))
     AND a.used_at IS NULL
     AND p.r->>'error' IS NULL
     AND jsonb_typeof(p.r->'accepted') = 'array'
     AND jsonb_array_length(p.r->'accepted') > 0
  RETURNING a.code
)
SELECT p.d->>'digest_day'             AS digest_day,
       (SELECT count(*) FROM ins)::int AS recorded,
       (SELECT count(*) FROM told)::int AS codes_told,
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
            FOLLOWUP_DUE_SQL, FOLLOWUP_WRITE_SQL, COMMAND_CONTEXT_SQL, COMMAND_APPLY_SQL, REPLY_QUEUE_SQL,
            REPLY_WRITE_SQL, REMINDER_DUE_SQL, REMINDER_STAMP_SQL, REPLY_SEND_STATE_SQL, REPLY_CLAIM_SQL]
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

# Workflow 7, the operator-command fork inside Mailbox Watch: Classify Inbound
# -> Load Command Context -> Decide Command -> [Command Shaped?] -> Apply
# Operator Command -> Build Command Ack -> [Tell Operator?] -> Send Command Ack,
# with the not-the-operator branch falling through to Record Inbound.
_command = js("code_operator_command.js")
_command_ack = js("code_command_ack.js")
_assert_js_emits("Load Command Context ($1.msg / $1.cmd)", {"payload", "command"}, _classify,
                 "code_classify_reply.js's node body")
assert "p.p->'msg'" in COMMAND_CONTEXT_SQL and "p.p->'cmd'" in COMMAND_CONTEXT_SQL, (
    "Load Command Context no longer reads both halves of Classify Inbound's output")
_assert_wired("Decide Command (ctx.*)", _reads(_command, "ctx"), final_select_aliases(COMMAND_CONTEXT_SQL),
              "Load Command Context")
_assert_js_emits("Apply Operator Command ($1)", _sql_payload_reads(COMMAND_APPLY_SQL), _command,
                 "code_operator_command.js's decision")
_assert_js_emits("Command Shaped? ($json.record) / Operator Mail? ($json.handled)", {"record", "handled"},
                 _command, "code_operator_command.js")
_assert_wired("Build Command Ack (r.*)", _reads(_command_ack, "r"), final_select_aliases(COMMAND_APPLY_SQL),
              "Apply Operator Command")
_assert_js_emits("Tell Operator? / Send Command Ack", NOTIFY_EMAIL_READS | {"notify"}, _command_ack,
                 "code_command_ack.js")
# Record Inbound is fed by TWO upstreams -- Decide Command's not-a-command
# branch and Apply Operator Command's not-the-operator branch -- and reads
# $json.payload from both, so both must carry it.
assert "p.p->'payload'" in COMMAND_APPLY_SQL and "AS payload" in COMMAND_APPLY_SQL, (
    "Apply Operator Command no longer hands the message payload back, so a command-shaped message "
    "that did NOT come from the operator could not fall through to Record Inbound -- a prospect's "
    "reply would be silently dropped")
# The whole point of the fork: the operator's own address is runtime data, so
# the sender comparison must happen in SQL and nowhere else.
assert "settings WHERE key = 'operator_email'" in COMMAND_APPLY_SQL, (
    "Apply Operator Command no longer re-derives the operator address from `settings` -- it would be "
    "trusting the item it was handed for the one check that grants authority")
assert "reply_approval_usable" in COMMAND_APPLY_SQL, (
    "Apply Operator Command no longer re-checks the code through migration 017's "
    "reply_approval_usable() -- a reused or expired code could act")
assert "from_operator" in RECORD_INBOUND_SQL, (
    "Record Inbound no longer refuses to match a message from the operator's own address to a lead. "
    "The operator answers the review email inside the prospect's thread, so a one-word 'no' would "
    "blocklist that prospect permanently (Section 9, Workflow 7)")

# Workflow 7, the Reply Assistant: Find Replies To Answer -> Build Reply ->
# Claude Reply -> Assemble Reply -> [Drop Failed Generations] -> Write Reply
# Draft & Issue Code -> Build Review Email -> [Review Email?] -> Send Review
# Email -> Record Review Sent; and the reminder lane beside it.
_reply = js("code_reply.js")
_reply_asm = js("code_reply_assemble.js")
_reply_review = js("code_reply_review.js")
_reply_remind = js("code_reply_remind.js")
_assert_wired("Build Reply (r.*)", _reads(_reply, "r"), final_select_aliases(REPLY_QUEUE_SQL),
              "Find Replies To Answer")
_assert_js_emits("Claude Reply ($json.request)", {"request"}, _reply, "code_reply.js")
_assert_js_emits("Assemble Reply (src.*)", _reads(_reply_asm, "src"), _reply, "code_reply.js")
_assert_js_emits("Drop Failed Generations ($json.write)", {"write"}, _reply_asm, "code_reply_assemble.js")
_reply_write_reads = (_sql_payload_reads(REPLY_WRITE_SQL)
                      | set(re.findall(r"p\.p->'draft'->>?'(\w+)'", REPLY_WRITE_SQL)))
_assert_js_emits("Write Reply Draft & Issue Code ($1)", _reply_write_reads | {"payload", "draft", "review"},
                 _reply_asm, "code_reply_assemble.js")
_assert_wired("Build Review Email (r.*)", _reads(_reply_review, "r"), final_select_aliases(REPLY_WRITE_SQL),
              "Write Reply Draft & Issue Code")
_assert_js_emits("Review Email? / Send Review Email / Record Review Sent",
                 NOTIFY_EMAIL_READS | {"notify", "record"}, _reply_review, "code_reply_review.js")
_assert_wired("Build Reminder (r.*)", _reads(_reply_remind, "r"), final_select_aliases(REMINDER_DUE_SQL),
              "Find Due Reminders")
_assert_js_emits("Remind? / Send Reminder", NOTIFY_EMAIL_READS | {"notify", "record"}, _reply_remind,
                 "code_reply_remind.js")
_assert_js_emits("Record Reminder ($1)", set(re.findall(r"p\.d->>?'(\w+)'", REMINDER_STAMP_SQL)),
                 _reply_remind, "code_reply_remind.js's record")
# A reply is drafted PENDING and gets its code from migration 018's one
# definition; writing either by hand here is how the 48-hour window or the
# one-open-code index would be got wrong.
assert "'pending'" in REPLY_WRITE_SQL and "reply_approval_issue(" in REPLY_WRITE_SQL, (
    "Write Reply Draft must insert the draft as 'pending' and take its code from "
    "reply_approval_issue() (migration 018)")
assert "INSERT INTO reply_approvals" not in REPLY_WRITE_SQL + DIGEST_LOAD_SQL, (
    "a workflow is inserting into reply_approvals directly instead of through reply_approval_issue() "
    "-- that function is the only thing that knows to reuse an open code and reap a dead one "
    "(migration 018)")
assert "reply_approval_issue('email-hold'" in DIGEST_LOAD_SQL, (
    "the Daily Digest no longer issues a code for the email drafts it lists as held (Section 9, "
    "Workflow 7) -- the operator could not approve one by replying to the digest")

# Workflow 7, Send Reply: a separate lane, so the cold path is untouched. The
# two must never be able to see each other's drafts.
_reply_decide = js("code_reply_decide.js")
_assert_wired("Decide Reply Send (state.*)", _reads(_reply_decide, "state"),
              final_select_aliases(REPLY_SEND_STATE_SQL), "Load Reply Send State")
_assert_wired("Decide Reply Send (candidate c.*)", _reads(_reply_decide, "c"),
              [a for _, a in REPLY_CANDIDATE_COLUMNS], "the reply candidate query")
_assert_js_emits("Claim Reply Send", _sql_payload_reads(REPLY_CLAIM_SQL), _reply_decide,
                 "code_reply_decide.js's payload")
REPLY_SEND_READS = {"to_addr", "subject", "body", "in_reply_to", "references"}
_assert_wired("Send Reply", REPLY_SEND_READS, final_select_aliases(REPLY_CLAIM_SQL), "Claim Reply Send")
_assert_wired("Check SMTP Result (claim.*)", _reads(_check, "claim"), final_select_aliases(REPLY_CLAIM_SQL),
              "Claim Reply Send")
assert "NOT LIKE 'reply/%'" in LOAD_STATE_SQL and "NOT LIKE 'reply/%'" in CLAIM_SQL, (
    "the cold send path no longer excludes reply drafts. A reply answers the address that WROTE, not "
    "contacts.email, and it skips the ceiling and business hours -- the two lanes must not be able to "
    "see one draft (Section 9, Workflow 7)")
assert "LIKE 'reply/%'" in REPLY_SEND_STATE_SQL and "LIKE 'reply/%'" in REPLY_CLAIM_SQL, (
    "the reply lane no longer restricts itself to reply drafts")
for _sql in (REPLY_SEND_STATE_SQL, REPLY_CLAIM_SQL):
    assert "blocklist" in _sql, (
        "a reply skips the warm-up ceiling and business hours; it never skips the blocklist "
        "(Section 9, Workflow 7)")
assert "warmup" not in _reply_decide and "WARMUP" not in _reply_decide, (
    "code_reply_decide.js has grown a warm-up ceiling. Section 9, Workflow 7: a reply skips it.")

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
    return bake_src(js(name), name, **subs)


def bake_src(src, name, **subs):
    for key, val in subs.items():
        token = "__%s__" % key
        assert token in src, "%s has no %s placeholder" % (name, token)
        src = src.replace(token, json.dumps(val, ensure_ascii=False))
    left = re.findall(r"__[A-Z_]+__", src)
    assert not left, "%s still has unsubstituted placeholders %r" % (name, left)
    return src


def above_node_body(name):
    """A Code-node file's library half -- everything above its 'Node body'
    marker -- for embedding in another node, the way the follow-ups embed
    drafting's rules. The marker is what keeps the two halves honest: nothing
    that reads n8n data can sit above it."""
    src = js(name)
    at = src.find("// Node body")
    assert at != -1, "%s has no '// Node body' marker, so its library half cannot be embedded" % name
    lib = src[:at]
    assert "$(" not in lib and "$input" not in lib, (
        "%s reads n8n data above its 'Node body' marker -- node-body code has moved above it" % name)
    return lib


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


# ---------------------------------------------------------------------------
# The reply composition call -- Section 9, Workflow 7
#
# Same model, parameters and caching as the follow-up call: Section 3's drafting
# model through the Anthropic Messages API, no temperature/top_p/top_k (a
# non-default value is a 400 on this model), effort from Section 3, JSON through
# output_config.format with a strict schema. One difference, and it is the whole
# shape of this workflow: a reply is NEVER auto-approved and never claim-checked
# -- the operator reads every one and approves it with a one-time code -- so
# there is no Approval Gate, no repair loop and no second model call anywhere in
# the reply path. The deterministic rules still run (Assemble Reply embeds
# drafting's own rule functions), and migration 018 refuses an auto-approved
# reply on the table as well.
# ---------------------------------------------------------------------------

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "ask": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "string"}},
        "deferred": {"type": "array", "items": {"type": "string"}},
        "flag": {"type": "string"},
    },
    "required": ["body", "ask", "claims", "deferred", "flag"],
    "additionalProperties": False,
}
_reply_assemble = js("code_reply_assemble.js")
_reply_fields = (re.findall(r"'(\w+)'", re.search(r"const REPLY_FIELDS = \[(.*?)\];", _reply_assemble).group(1))
                 + re.findall(r"'(\w+)'", re.search(r"const REPLY_LISTS = \[(.*?)\];", _reply_assemble).group(1)))
assert sorted(_reply_fields) == sorted(REPLY_SCHEMA["required"]), (
    "Assemble Reply's REPLY_FIELDS/REPLY_LISTS do not match the reply schema:\n  schema: %r\n  js:     %r"
    % (REPLY_SCHEMA["required"], _reply_fields))

REPLY_REQUEST = {
    "model": DRAFT_MODEL,
    "max_tokens": 16000,
    "output_config": {"effort": DRAFT_EFFORT, "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
}

# The five topics a reply may not answer live in code_reply_topics.js, which is
# prepended to both Build Reply and Assemble Reply. The prompt names them too,
# so the model is told the same list the code checks.
_reply_topics = js("code_reply_topics.js")
REPLY_TOPICS = re.findall(r"^\s*topic: '([a-z]+)',", _reply_topics, re.M)
assert len(REPLY_TOPICS) == 5, "code_reply_topics.js no longer holds the operator's five open topics: %r" % REPLY_TOPICS

REPLY_SYSTEM_PROMPT = "\n".join([
    "You write one short email replying to a small contract research organisation (CRO) that has",
    "answered a cold email from " + SENDER_NAME + ". " + SENDER_NAME + " reads your draft and approves,",
    "edits or rejects it before anything is sent. You are answering a real person who took the time",
    "to write back.",
    "",
    "EVERY MESSAGE GIVES YOU FOUR SOURCES, and they are the only ones you have:",
    "1. THEIR MESSAGE -- what they wrote above the quoted thread.",
    "2. THE THREAD -- our own earlier messages, exactly as they were sent.",
    "3. THEIR RECORD -- the facts we hold about their company, each with a sentence saying what it",
    "   does not mean. Read those boundaries: two true facts joined into one sentence is the",
    "   commonest way these drafts go wrong.",
    "4. APPROVED CLAIMS -- the only things you may say about what we built, the problem it solves",
    "   and who uses it. You may rephrase a line. You may never widen what it means.",
    "",
    "ANYTHING ELSE YOU DO NOT ANSWER. Not vaguely, not approximately, not \"typically\". The",
    "message lists the questions they asked that none of the four sources settles -- about " +
    ", ".join(REPLY_TOPICS[:-1]) + " or " + REPLY_TOPICS[-1] + " -- and for each one you say, in a",
    "normal sentence, that you will confirm it and come back. Give no hint of the answer: not a",
    "range, not \"it depends\", not \"usually\". Name every one of them in `deferred` and say in",
    "`flag` what has to be confirmed. " + SENDER_NAME + " knows those answers and will put them in.",
    "A guessed price, integration, language, hosting detail or date is the one failure that cannot",
    "be taken back, because it reaches a prospect over a real person's name.",
    "",
    "ANSWER WHAT THEY ACTUALLY SAID, in their own order, and nothing they did not raise:",
    "- If they declined, accept it in one line, leave one door open, and stop. Never argue, never",
    "  re-pitch, never ask them to reconsider, never suggest they have misunderstood.",
    "- If they asked something the record or the thread answers, answer it plainly and briefly.",
    "- If they asked how we found them, the record says; say exactly that and no more.",
    "- If they asked for something we have (the one approved link), offer it once.",
    "Then exactly ONE next step, from the approved list, sized to what they wrote: a declining",
    "reply gets the lightest one, an interested reply the direct one.",
    "",
    "THE PROSPECT: only the facts in their record and in the thread. Never describe them as",
    "sponsoring anything -- in these emails \"sponsor\" means the biotech, pharma, device or",
    "academic company that hires a CRO, their client. Never invent a trial, a person, a city or a",
    "number. Do not name their company back at them.",
    "",
    "THE PRODUCT: the first email introduced it; refer back to it (\"the assistant\"). Never call it",
    "a chatbot or a Q&A bot. The word \"AI\" at most once. Never \"AI-powered\". Name it only as",
    "\"(we call it Nova)\", at most once, and only if naming it helps.",
    "",
    "NO REPEATS: the lines you use must not repeat the same capability -- each one says what it is",
    "about -- and do not restate a capability the thread already covered.",
    "",
    "CLAIMS -- forbidden, all of them:",
    "- guarantees (\"you'll never lose a sponsor\")",
    "- any number, percentage or multiplier that is not in the approved claims, their record or",
    "  the thread",
    "- claiming to identify anonymous website visitors",
    "- naming any CRM, tool or integration",
    "- supported languages",
    "- any count of clients beyond the deployments the proof line names, and never the phrase",
    "  \"two CROs\"",
    "- saying it books calls or fills a calendar -- it sends the sponsor your booking link, and the",
    "  sponsor books",
    "- saying it answers from SOPs or from documents -- it answers from their website",
    "A stakes line is about the industry, not about us.",
    "",
    "THE NEXT STEP: one question, from the list, rephrased to fit what they said. No scheduling",
    "link, no call length, no second question. The body before it asks for nothing.",
    "",
    "EVERYWHERE: plain text. No bullets, no headings, no placeholders, no merge tags. At most one",
    "link, and only the one offered. No greeting, no sign-off and no opt-out line -- all three are",
    "added afterwards. Never these words: " + ", ".join(SKILL_BANNED) + ". No invented urgency, no",
    "flattery, no exclamation marks, and never thank them twice.",
    "",
    "Return JSON: body (everything before the next step, paragraphs separated by a blank line);",
    "ask (the one next step); claims (the code of every approved line the reply used, the next step",
    "included); deferred (the topic names you did not answer); flag (one line saying what " +
    SENDER_NAME + " has to confirm, or \"\" if nothing).",
])

DECIDE_JS = bake("code_decide.js", SIGNATURE=SIGNATURE)
CHECK_JS = bake("code_check_smtp.js", OWN_DOMAIN=OWN_DOMAIN)
MIRROR_JS = bake("code_mirror_sent.js", OWN_DOMAIN=OWN_DOMAIN)

# Workflow 7's operator-command library, verbatim above its own 'Node body'
# marker, then Classify Inbound: so one message is read for a command and for a
# classification by the same two files that the Decide Command node runs, and
# the command is read FIRST (the node body says why).
OPERATOR_RULES_JS = bake_src(above_node_body("code_operator_command.js"), "code_operator_command.js",
                             SIGNATURE=SIGNATURE, MAX_URLS=MAX_URLS)
CLASSIFY_JS = (
    "// Classify Inbound -- generated by n8n/sendtrack/build_workflow.py.\n"
    "// Part 1, verbatim: n8n/sendtrack/code_operator_command.js above its 'Node body' marker --\n"
    "// the operator-command parser and its authentication gate (Section 9, Workflow 7).\n"
    "// Part 2: n8n/sendtrack/code_classify_reply.js.\n\n"
    + OPERATOR_RULES_JS + "\n" + bake("code_classify_reply.js", OWN_DOMAIN=OWN_DOMAIN)
)
COMMAND_JS = bake("code_operator_command.js", SIGNATURE=SIGNATURE, MAX_URLS=MAX_URLS)
COMMAND_ACK_JS = bake("code_command_ack.js")
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

# Workflow 7's own Code nodes. The five open topics (code_reply_topics.js) are
# prepended to both the node that detects them in the prospect's message and the
# node that checks the draft against them, so the two cannot drift apart; and
# Assemble Reply additionally carries drafting's rule functions verbatim, so a
# reply is judged by the first touch's rules and not by a copy of them.
REPLY_TOPICS_JS = js("code_reply_topics.js")
REPLY_JS = (
    "// Build Reply -- generated by n8n/sendtrack/build_workflow.py.\n"
    "// Part 1, verbatim: n8n/sendtrack/code_reply_topics.js. Part 2: n8n/sendtrack/code_reply.js.\n\n"
    + REPLY_TOPICS_JS + "\n"
    + bake("code_reply.js", REPLY_SYSTEM_PROMPT=REPLY_SYSTEM_PROMPT, CLAUDE_REQUEST=REPLY_REQUEST,
           SENDER_NAME=SENDER_NAME, THERAPEUTIC_AREAS=THERAPEUTIC_AREAS)
)
assert '"model": "%s"' % DRAFT_MODEL in REPLY_JS, "the shipped reply request does not name %s" % DRAFT_MODEL
REPLY_ASSEMBLE_JS = (
    "// Assemble Reply -- generated by n8n/sendtrack/build_workflow.py.\n"
    "// Part 1, verbatim: n8n/drafting/code_assemble.js above its 'Node body' marker -- the\n"
    "// first touch's claim rules. Part 2, verbatim: n8n/sendtrack/code_reply_topics.js.\n"
    "// Part 3: n8n/sendtrack/code_reply_assemble.js.\n\n"
    + RULES_JS + "\n" + REPLY_TOPICS_JS + "\n" + bake("code_reply_assemble.js", SIGNATURE=SIGNATURE)
)
REPLY_REVIEW_JS = bake("code_reply_review.js")
REPLY_REMIND_JS = bake("code_reply_remind.js")
REPLY_DECIDE_JS = bake("code_reply_decide.js", SIGNATURE=SIGNATURE, MAX_URLS=MAX_URLS)

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


def http_post_node(name, url_expr, body_expr, timeout_ms, pos, notes):
    """The reply lane's sender (Section 9, Workflow 7). Same error contract as
    the Send Email node it stands in for: continue-on-error AND never-error, so
    a refusal arrives at Check SMTP Result as an ordinary item instead of
    stopping the execution with a claim already written -- and NO retry, because
    a timeout after the server accepted the message would send twice."""
    return {
        "parameters": {
            "method": "POST",
            "url": url_expr,
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body_expr,
            "options": {"timeout": timeout_ms,
                        "response": {"response": {"neverError": True, "responseFormat": "json"}}},
        },
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
              "Two readings of one message. The operator-command parse FIRST (Section 9, Workflow 7): the "
              "operator answers the review email inside the prospect's thread, so a one-word \"no\" from "
              "them would otherwise thread-match the prospect and blocklist it. Then the classification, "
              "from only what they wrote above the quoted thread -- our own email says \"reply 'no'\" and "
              "every reply quotes it. bounce > auto-reply > opt-out > reply."),
    pg_node("Load Command Context", COMMAND_CONTEXT_SQL, "={{ [JSON.stringify({ msg: $json.payload, "
            "cmd: $json.command })] }}", [-550, 420],
            "Read-only, and the only place that can answer the three questions one message cannot: is the "
            "sender the operator's address (settings, migration 008 -- never baked into this JSON), does "
            "the quoted code exist, is it unused and unexpired, and what state is its draft in."),
    code_node("Decide Command", COMMAND_JS, "runOnceForEachItem", [-330, 420],
              "Three separate questions: is it command-SHAPED (record it), is it really FROM the operator "
              "(then it is never a prospect reply), and is it accepted. A refusal is logged; a message "
              "that only looks like a command falls through to Record Inbound."),
    if_node("Command Shaped?", "iscommand", "={{ $json.record }}", [-110, 420],
            "APPROVE / REJECT / EDIT on a line of its own. False goes straight to Record Inbound -- a "
            "plain \"no\" from the operator included, which Record Inbound then refuses to match to any "
            "lead."),
    pg_node("Apply Operator Command", COMMAND_APPLY_SQL, "={{ [JSON.stringify($json)] }}", [110, 420],
            "Records every command-shaped message in operator_commands, accepted or refused, keyed on "
            "Message-ID. Re-derives the operator address, re-checks the code through "
            "reply_approval_usable(), and only then moves the draft -- migration 017's CHECK makes "
            "`accepted` impossible without SPF, DKIM and the sender. The code is marked used only if the "
            "draft actually moved."),
    if_node("Operator Mail?", "handled", "={{ $json.handled }}", [330, 420],
            "True: the operator's own mail, so it is never classified as a prospect reply. False: it only "
            "looked like a command (a spoofed sender, failed SPF or DKIM) and may be a prospect writing, "
            "so it goes on to Record Inbound as well as into operator_commands."),
    code_node("Build Command Ack", COMMAND_ACK_JS, "runOnceForEachItem", [330, 620],
              "The answer, built from what the statement actually did. Only an authenticated operator gets "
              "one -- answering a forgery would turn the mailbox into an oracle for guessing codes -- and "
              "only on the first delivery of that Message-ID."),
    if_node("Tell Operator?", "notifyack", "={{ $json.notify }}", [550, 620],
            "Accepted or refused, the operator hears. Nobody else does."),
    email_node("Send Command Ack", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}",
               [770, 620],
               "To the operator's own inbox. Sent only to that address, so it counts toward no warm-up "
               "ceiling (Section 9)."),
    pg_node("Record Inbound", RECORD_INBOUND_SQL, "={{ [JSON.stringify($json.payload)] }}", [-440, 160],
            "Match to a lead, record once (keyed on Message-ID), and act: reply -> outreach_log + lead "
            "'replied' (kills follow-ups, and retires the lead's pending LinkedIn DM -- migration 018); "
            "opt-out -> that plus blocklist, permanent (Section 5); bounce -> outreach_log 'bounced'. A "
            "message from the operator's own address is recorded and matched to NOTHING."),
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
    # The operator fork comes FIRST, which is the whole point of Section 9,
    # Workflow 7's hazard: Record Inbound is only ever reached by a message that
    # is not the operator's own command.
    "Classify Inbound": edge("Load Command Context"),
    "Load Command Context": edge("Decide Command"),
    "Decide Command": edge("Command Shaped?"),
    "Command Shaped?": branch("Apply Operator Command", "Record Inbound"),
    "Apply Operator Command": edge("Operator Mail?", "Build Command Ack"),
    "Operator Mail?": branch("Tell Operator?", "Record Inbound"),
    "Build Command Ack": edge("Tell Operator?"),
    "Tell Operator?": branch("Send Command Ack"),
    "Record Inbound": edge("Detect Positive Signal"),
    "Detect Positive Signal": edge("Build Notification"),
    "Build Notification": edge("Notify Operator?"),
    "Notify Operator?": branch("Notify Operator"),
}

# Record Inbound must be reachable ONLY from the two not-an-operator-command
# branches -- never straight from Classify Inbound, which is the wiring the
# hazard in Section 13 described.
_into_record = sorted(src for src, outs in mailwatch_connections.items()
                      for br in outs["main"] for e in br if e["node"] == "Record Inbound")
assert _into_record == ["Command Shaped?", "Operator Mail?"], (
    "Record Inbound is reached from %r. It must be reached only after the operator-command fork, or the "
    "operator's own \"no\" can blocklist a prospect (Section 9, Workflow 7)." % _into_record)
assert mailwatch_connections["Command Shaped?"]["main"][1] == [{"node": "Record Inbound", "type": "main", "index": 0}]
assert mailwatch_connections["Operator Mail?"]["main"][1] == [{"node": "Record Inbound", "type": "main", "index": 0}]

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
    pg_node("Load Digest", DIGEST_LOAD_SQL, "={{ [$json.now_override, $json.digest_hour] }}", [-660, 130],
            "One snapshot: drafts auto-approved and emails sent since the last digest, and every pending draft "
            "with its hold_reason -- the exceptions queue. Plus the operator's address and the flag. On the tick "
            "that actually sends (hence digest_hour here too), each held EMAIL draft also gets a one-time "
            "approval code, so the operator can approve, reject or edit it by replying (Workflow 7)."),
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

# ---------------------------------------------------------------------------
# Workflow: Reply Assistant (Section 9, Workflow 7, migrations 017 + 018)
# ---------------------------------------------------------------------------
#
# Two lanes off one trigger. The drafting lane answers prospect messages; the
# reminder lane nudges the operator about any code still open. Both are
# queue-driven, so a missed run only means the next one finds more (Section 7).
#
# Every reply costs a paid Claude call, so the batch is bounded like Workflow
# 4's. batch_size is small on purpose: a reply is rare and the operator reads
# each one, so there is no value in drafting ten at once.
REPLY_CONFIG = {"now_override": "", "batch_size": 5, "remind_hours": 4}
REPLY_TRIGGER = "Every 30 Minutes"

reply_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
     "name": REPLY_TRIGGER, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [-1100, 40],
     "notes": ("Every 30 minutes, queue-driven both ways: an unanswered prospect message and an unanswered "
               "code are both still there on the next tick (Section 7). Not an hours interval -- n8n gates "
               "those on the clock value of the last run and skips ticks on a host that sleeps (Follow-Ups, "
               "2026-10-02; the build refuses one).")},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 320]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": REPLY_CONFIG["now_override"], "type": "string"},
            {"id": "batch", "name": "batch_size", "value": REPLY_CONFIG["batch_size"], "type": "number"},
            {"id": "remind", "name": "remind_hours", "value": REPLY_CONFIG["remind_hours"], "type": "number"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 180],
        "notes": ("batch_size: prospect messages answered per run, each one a paid call. remind_hours: how "
                  "long an unanswered one-time code waits before the operator is nudged, and between "
                  "nudges (Section 9, Workflow 7). now_override must stay empty (dry-run only)."),
    },
    pg_node("Find Replies To Answer", REPLY_QUEUE_SQL, "={{ [$json.now_override, $json.batch_size] }}",
            [-660, 40],
            "Read-only. Every inbound message classified 'reply', matched to a lead, with no reply_approvals "
            "row against its Message-ID -- so one drafted reply per prospect MESSAGE, for ever. A rejected "
            "reply is never redrafted (delete its row to ask for another); a prospect who writes again gets "
            "a new answer. Blocklisted either way is skipped before any model call."),
    code_node("Build Reply", REPLY_JS, "runOnceForAllItems", [-440, 40],
              "The request: their message, the thread as sent, their enrichment record with its boundary "
              "sentences, the confirmed claims and how we found them -- and nothing else. The five topics "
              "nothing we have confirmed can settle (pricing, integrations, languages, security, timelines) "
              "are detected in THEIR text here, so the model is told to defer them rather than tagged "
              "afterwards for having guessed."),
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
        "name": "Claude Reply", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [-220, 40],
        "credentials": ANTHROPIC_CRED, "onError": "continueRegularOutput", "retryOnFail": True, "maxTries": 2,
        "waitBetweenTries": 5000,
        "notes": ("%s through the Anthropic Messages API, effort %s (Section 3) -- the drafting model, with "
                  "the drafting node's request parameters and its prompt caching. The key is the n8n "
                  "credential %s, never in this JSON. A failed call writes nothing and the message is queued "
                  "again next run: no fallback to the local model (skill section 6). This is the ONLY model "
                  "call in the reply path -- a reply is never claim-checked, because the operator reads every "
                  "one." % (DRAFT_MODEL, DRAFT_EFFORT, ANTHROPIC_CRED["anthropicApi"]["id"])),
    },
    code_node("Assemble Reply", REPLY_ASSEMBLE_JS, "runOnceForEachItem", [0, 40],
              "Drafting's own claim rules (code_assemble.js above its 'Node body' marker, embedded verbatim) "
              "plus the reply's: 40-120 words, one next step, the one-URL cap, no \"two CROs\" line and never "
              "Vertex Clinical Research as a CRO, and a deferral sentence for every topic they raised. "
              "Violations tag the variant; the operator reads the tags in the review email. The greeting, the "
              "signature and their quoted message are appended here, never generated."),
    {
        "parameters": {"conditions": boolean_condition("writable", "={{ $json.write }}"), "options": {}},
        "name": "Drop Failed Generations", "type": "n8n-nodes-base.filter", "typeVersion": 2.2,
        "position": [220, 40],
        "notes": "A failed Claude call writes nothing; the message is queued again on the next run.",
    },
    pg_node("Write Reply Draft & Issue Code", REPLY_WRITE_SQL, "={{ [JSON.stringify($json.payload)] }}",
            [440, 40],
            "The draft into `drafts` as PENDING -- a reply is never auto-approved, and migration 018 refuses "
            "one on the table too -- and its one-time code from reply_approval_issue(), the only thing that "
            "knows to reuse an open code and reap a dead one. hold_reason names the waiting command, so the "
            "daily digest lists it like any other held draft."),
    code_node("Build Review Email", REPLY_REVIEW_JS, "runOnceForEachItem", [660, 40],
              "What they wrote, the exact bytes that would be sent, what the reply does NOT answer and why, "
              "the rule tags, and the three commands with the code. Built from what the database holds, not "
              "from what the drafter proposed."),
    if_node("Review Email?", "review", "={{ $json.notify }}", [880, 40],
            "Only with a draft written, a code issued and an operator address in `settings`. No address, no "
            "review email -- and the draft stays pending, which is the safe end of that."),
    email_node("Send Review Email", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}",
               [1100, -60],
               "To the operator's own inbox, never the outreach mailbox. Sent only to that address, so it "
               "counts toward no warm-up ceiling (Section 9)."),
    pg_node("Record Review Sent", REMINDER_STAMP_SQL,
            "={{ [JSON.stringify($('Build Review Email').item.json.record), JSON.stringify($json)] }}",
            [1320, -60],
            "Stamps reply_approvals.reminded_at -- 'the operator has been told' -- and only once SMTP "
            "accepted it. A review email that could not go out leaves it NULL, which the reminder lane reads "
            "as due at once rather than in four hours."),
    pg_node("Find Due Reminders", REMINDER_DUE_SQL, "={{ [$json.now_override, $json.remind_hours] }}",
            [-660, 420],
            "Read-only. Every unused, unexpired code whose draft is still pending and whose operator has not "
            "been told in remind_hours -- or at all, which is the self-heal for a review email or a digest "
            "SMTP refused. Both kinds: a drafted reply, and an email the claim check held after its repairs."),
    code_node("Build Reminder", REPLY_REMIND_JS, "runOnceForEachItem", [-440, 420],
              "Short: what is waiting, how long the code has left, the draft itself, and the three commands."),
    if_node("Remind?", "remind", "={{ $json.notify }}", [-220, 420],
            "Only with an operator address in `settings`."),
    email_node("Send Reminder", "={{ $json.notify_to }}", "={{ $json.subject }}", "={{ $json.text }}",
               [0, 420],
               "To the operator's own inbox. Sent only to that address, so it uses no warm-up slot."),
    pg_node("Record Reminder", REMINDER_STAMP_SQL,
            "={{ [JSON.stringify($('Build Reminder').item.json.record), JSON.stringify($json)] }}",
            [220, 420],
            "The same statement Record Review Sent runs, so 'the operator has been told about this code' can "
            "only mean one thing. Stamped only once SMTP accepted the reminder."),
]

reply_connections = {
    REPLY_TRIGGER: edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Find Replies To Answer", "Find Due Reminders"),
    "Find Replies To Answer": edge("Build Reply"),
    "Build Reply": edge("Claude Reply"),
    "Claude Reply": edge("Assemble Reply"),
    "Assemble Reply": edge("Drop Failed Generations"),
    "Drop Failed Generations": edge("Write Reply Draft & Issue Code"),
    "Write Reply Draft & Issue Code": edge("Build Review Email"),
    "Build Review Email": edge("Review Email?"),
    "Review Email?": branch("Send Review Email"),
    "Send Review Email": edge("Record Review Sent"),
    "Find Due Reminders": edge("Build Reminder"),
    "Build Reminder": edge("Remind?"),
    "Remind?": branch("Send Reminder"),
    "Send Reminder": edge("Record Reminder"),
}

# Nothing in this workflow may approve a draft: the operator's one-time code is
# the only way a reply becomes sendable (migration 018 enforces it on the table
# as well). A gate or a claim-check node here would be that rule being quietly
# dropped.
for _n in reply_nodes:
    assert _n["name"] not in ("Approval Gate", "Apply Claim Check"), (
        "the Reply Assistant has grown an approval gate. A reply is approved by the operator's code and by "
        "nothing else (Section 9, Workflow 7).")
assert "status = 'approved'" not in REPLY_WRITE_SQL and "'approved'" not in REPLY_WRITE_SQL, (
    "Write Reply Draft can approve a draft. It must write 'pending' only.")

# ---------------------------------------------------------------------------
# Workflow: Send Reply (Section 9, Workflow 7)
# ---------------------------------------------------------------------------
#
# A separate lane from Send, deliberately: the cold path keeps its ceiling, its
# business hours, its pacing and its Send Email node, and nothing here can
# change any of that. What this lane has that the cold one cannot is the two
# threading headers -- the installed Send Email node builds nodemailer's
# mailOptions from a fixed field set with no headers option (read 2026-10-09;
# smtp_send_server.py has the quotation), so a reply submits through the
# smtp-send sidecar instead.
SENDREPLY_CONFIG = {"now_override": "", "send_url": "http://%s:%s/send" % ("smtp-send", 8766)}
SENDREPLY_TRIGGER = "Every 10 Minutes"

_smtp_send_py = js("smtp_send_server.py")
_smtp_port = re.search(r"^PORT = (\d+)$", _smtp_send_py, re.M)
assert _smtp_port, "PORT not found in smtp_send_server.py"
SENDREPLY_CONFIG["send_url"] = "http://smtp-send:%s/send" % _smtp_port.group(1)

sendreply_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}},
     "name": SENDREPLY_TRIGGER, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [-1100, 40],
     "notes": ("Every 10 minutes, like Send. There is no business-hours filter and no pacing here -- a reply "
               "goes out as soon as the operator has approved it (Section 9, Workflow 7) -- so this interval "
               "is just how long an approved reply may wait.")},
    {"parameters": {}, "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
     "position": [-1100, 220]},
    {
        "parameters": {"assignments": {"assignments": [
            {"id": "nowoverride", "name": "now_override", "value": SENDREPLY_CONFIG["now_override"],
             "type": "string"},
            {"id": "sendurl", "name": "send_url", "value": SENDREPLY_CONFIG["send_url"], "type": "string"},
        ]}, "options": {}},
        "name": "Config", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [-880, 130],
        "notes": ("send_url: the smtp-send service in docker-compose.yml, which is the only sender in this "
                  "project that can set In-Reply-To and References. now_override must stay empty "
                  "(dry-run only)."),
    },
    pg_node("Load Reply Send State", REPLY_SEND_STATE_SQL, "={{ [$json.now_override] }}", [-660, 130],
            "Every approved `reply/` draft with what each guard needs: the used approval code and its "
            "outcome, the address that wrote to us, the thread headers, and both blocklist checks. No "
            "warm-up history and no clock table -- a reply skips the ceiling and business hours."),
    code_node("Decide Reply Send", REPLY_DECIDE_JS, "runOnceForAllItems", [-440, 130],
              "The per-draft checks, and the threading headers built from what the database really holds "
              "(migration 017), falling back to the Message-IDs it does have rather than inventing a chain. "
              "Emits exactly one item: send true with one payload, or send false with the reason."),
    if_node("Send Now?", "send", "={{ $json.send }}", [-220, 130],
            "False ends the tick. The reason is in Decide Reply Send's output for anyone reading the "
            "execution."),
    pg_node("Claim Send", REPLY_CLAIM_SQL, "={{ [JSON.stringify($json.payload)] }}", [0, 40],
            "The last line of defence: the variant, the approval code and its outcome, the unchanged body, "
            "the replied lead, the address that wrote and both blocklists, re-checked in one statement -- and "
            "the draft flipped to 'sent' with a claim row BEFORE the SMTP call (at-most-once). Named as Send "
            "names it, because Check SMTP Result is the same file."),
    if_node("Claimed?", "claimed", "={{ $json.claimed }}", [220, 40],
            "False means the draft, the code or the recipient changed between Decide and Claim. Nothing is "
            "sent."),
    http_post_node("Send Reply", "={{ $('Config').first().json.send_url }}",
                   "={{ JSON.stringify({ to: $json.to_addr, subject: $json.subject, text: $json.body, "
                   "in_reply_to: $json.in_reply_to, references: $json.references }) }}", 120000, [440, -60],
                   "The smtp-send sidecar: one SMTP submission carrying In-Reply-To and References, which "
                   "n8n's Send Email node cannot set. It answers in nodemailer's own shape, so Check SMTP "
                   "Result classifies a failure exactly as it does on the cold path. No retry: a timeout "
                   "after the server accepted would send twice."),
    code_node("Check SMTP Result", CHECK_JS, "runOnceForEachItem", [660, -60],
              "The same file the cold path runs. ok -> confirm. account failure (auth, TLS, DNS, 4xx) -> back "
              "to approved, retried next tick. recipient failure (5xx on the address) -> back to pending, "
              "tagged +smtp-rejected, for a human."),
    if_node("Sent OK?", "ok", "={{ $json.ok }}", [880, -60], "Accepted by the server, with a Message-ID."),
    pg_node("Confirm Send", CONFIRM_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, -140],
            "The same statement the cold path runs: Message-ID onto the claim and the send into "
            "mailbox_sent. The lead stays 'replied' -- that statement only advances a draft/approved lead -- "
            "and the reply does count in the Sent mirror afterwards, because it is external mail."),
    pg_node("Revert Claim", REVERT_SQL, "={{ [JSON.stringify($json.payload)] }}", [1100, 40],
            "Nothing went out: the claim is removed and the draft handed back. A recipient refusal lands it "
            "pending with +smtp-rejected, where the operator can see it."),
]

sendreply_connections = {
    SENDREPLY_TRIGGER: edge("Config"),
    "Manual Trigger": edge("Config"),
    "Config": edge("Load Reply Send State"),
    "Load Reply Send State": edge("Decide Reply Send"),
    "Decide Reply Send": edge("Send Now?"),
    "Send Now?": branch("Claim Send"),
    "Claim Send": edge("Claimed?"),
    "Claimed?": branch("Send Reply"),
    "Send Reply": edge("Check SMTP Result"),
    "Check SMTP Result": edge("Sent OK?"),
    "Sent OK?": branch("Confirm Send", "Revert Claim"),
}

# The sidecar must be what docker-compose.yml actually runs, reachable only on
# the compose network: it holds the mailbox password and can send mail, with no
# authentication of its own.
_svc = re.search(r"^  smtp-send:\n((?:    .*\n|[ \t]*\n)+)", COMPOSE_TEXT, re.M)
assert _svc, ("Send Reply posts to %s, but docker-compose.yml has no 'smtp-send' service."
              % SENDREPLY_CONFIG["send_url"])
assert "smtp_send_server.py" in _svc.group(1), (
    "the smtp-send service in docker-compose.yml does not run smtp_send_server.py")
assert not re.search(r"^    ports:", _svc.group(1), re.M), (
    "the smtp-send service publishes a port. It can send mail as the outreach mailbox and has no "
    "authentication -- keep it on the compose network only.")
assert "NOVASCOUT_MAILBOX_PASSWORD" in _svc.group(1) and "ANTHROPIC_API_KEY" not in _svc.group(1), (
    "the smtp-send service's environment is not the mailbox's SMTP settings alone")
# It may only ever send as the mailbox itself.
assert "is not this mailbox" in _smtp_send_py, (
    "smtp_send_server.py no longer refuses a From that is not the configured mailbox")

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


for _nodes in (send_nodes, mailwatch_nodes, followup_nodes, health_nodes, digest_nodes, reply_nodes,
               sendreply_nodes):
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
_cfg = _config_values(reply_nodes)
assert _cfg["now_override"] == "", "the shipped Reply Assistant workflow has a clock override set"
assert _cfg["batch_size"] >= 1 and _cfg["remind_hours"] >= 1, (
    "the shipped Reply Assistant has its batch or its reminder switched off: %r" % _cfg)
assert _cfg["remind_hours"] == 4, (
    "Section 9, Workflow 7 says the operator is reminded after 4 hours; Config says %r"
    % _cfg["remind_hours"])
_cfg = _config_values(sendreply_nodes)
assert _cfg["now_override"] == "", "the shipped Send Reply workflow has a clock override set"
assert _cfg["send_url"] == SENDREPLY_CONFIG["send_url"] and _cfg["send_url"].startswith("http://smtp-send:"), (
    "the shipped Send Reply workflow does not post to the smtp-send service: %r" % _cfg["send_url"])
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
    ("reply-assistant.json", workflow("reply0001", "Send & Track - Reply Assistant", reply_nodes,
                                      reply_connections)),
    ("send-reply.json", workflow("sendreply0001", "Send & Track - Send Reply", sendreply_nodes,
                                 sendreply_connections)),
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

    # Send Reply (Section 9, Workflow 7): the same three kinds of change as
    # every other variant, PLUS the Config send_url -- the "Send Reply" node
    # has no credential of its own to swap (it is an unauthenticated internal
    # HTTP POST), so the only way to keep it off the real smtp-send sidecar
    # is to point Config at a sink instead. Asserted here so that remains the
    # ONLY extra difference: a dry run that silently changed anything else
    # about this lane would not be testing what ships.
    #
    # Deliberately NOT generated: a dry-run variant of the Reply Assistant
    # itself (reply0001). _DRY_KEEPS leaves the Anthropic credential real on
    # every dry variant (drafting and follow-ups measure real cost against
    # it), so a reply0001 dry run would place a real, billed Claude call --
    # and every scenario this harness proves (operator authentication, the
    # one-time code, the reply send lane's skipped/kept guards) is provable
    # without composing a single reply. Seeding `drafts` and `reply_approvals`
    # directly, as reply_dryrun.py does, is the same principle dryrun.py's own
    # `direct_claim` helper already uses for Claim Send.
    sendreply_wf = SHIPPED[6][1]
    dry_sendreply = dry_variant(sendreply_wf, "sendreply0001dry", "DRY RUN - Send Reply (scratch DB, HTTP sink)",
                                {"now_override": DRYRUN_NOW, "send_url": SENDREPLY_SINK_URL},
                                {SENDREPLY_TRIGGER})
    got = _node_diff(sendreply_wf, dry_sendreply)
    want = sorted([(SENDREPLY_TRIGGER, "removed"), ("Config", "config:now_override"),
                   ("Config", "config:send_url"), ("Load Reply Send State", "credentials"),
                   ("Claim Send", "credentials"), ("Confirm Send", "credentials"),
                   ("Revert Claim", "credentials")])
    assert sorted(got) == want, "the dry-run Send Reply variant drifted: %r" % sorted(got)
    VARIANTS.append(("sendreply-dryrun.json", dry_sendreply))

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
print("  reply assistant (Section 9, Workflow 7): %d message(s)/run composed by %s (effort %s), %d-%d words, "
      "always PENDING -- approved only by a one-time code from the operator address with SPF+DKIM passing"
      % (REPLY_CONFIG["batch_size"], DRAFT_MODEL, DRAFT_EFFORT,
         _js_int(js("code_reply.js"), "REPLY_MIN_WORDS", "code_reply.js"),
         _js_int(js("code_reply.js"), "REPLY_MAX_WORDS", "code_reply.js")))
print("    codes: NS-XXXXXXXXXX, one use, 48 h (migration 017's CHECK), minted by "
      "reply_approval_issue() (migration 018); operator reminded every %d h while one is open"
      % REPLY_CONFIG["remind_hours"])
print("    never answered, deferred to the operator: %s" % ", ".join(REPLY_TOPICS))
print("    reply send: no warm-up ceiling, no business hours, no pacing; blocklist, approval code, "
      "signature and the one-URL cap all still apply")
print("    threaded through %s -- the installed Send Email node cannot set In-Reply-To/References"
      % SENDREPLY_CONFIG["send_url"])
print("  IMAP health (Section 9): every %d min -> %s; problems %r" % (
    HEALTH_INTERVAL_MIN, HEALTH_CONFIG["checker_url"], HEALTH_PROBLEMS))
print("  literal email addresses in the shipped JSON: %s" % (
    "; ".join("%s (%s)" % (a, ", ".join(sorted(w))) for a, w in sorted(LITERAL_ADDRESSES.items())) or "none"))

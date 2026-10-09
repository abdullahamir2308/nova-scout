"""Assemble n8n/workflows/drafting.json from the tested Code-node sources.

Same contract as the enrichment, scoring and contacts generators: the .js files
are read verbatim and embedded, so the JS that was tested standalone is
byte-identical to the JS that ships inside the workflow.

This generator holds the spec-drift guards for Workflow 4. Workflow 4 is the
node where a drift is most expensive, because its failure mode is not a wrong
number in a database -- it is a fabricated sentence sent to a real person under
the sender's own name. So more of the spec is parsed and asserted here than
anywhere else in the project:

    Section 9  grounding threshold      -> "at least two specific facts"
    Section 9  grounding fact vocabulary-> "(therapeutic area, named trial, ...)"
    Section 9  therapeutic-area enum    -> what counts as a therapeutic-area fact
    Section 9  banned adjectives        -> "revolutionary" / "cutting-edge"
    Section 9  link-free warm-up        -> "Links: none in warm-up weeks 1-2"
    Section 5  opt-out sentence         -> VERBATIM, it is the GDPR/KVKK basis
    Section 5  body length              -> must agree with the skill
    Section 3  drafting model and effort-> the Anthropic request
    Section 8  drafts table columns     -> what the INSERT is allowed to touch
    Section 12 company size band        -> what a "small" headcount is

Drafting skill v3 (NovaScout_DraftingSkill.md) is parsed the same way:

    section 1  "the model composes the whole email" -> the output schema has a
               body, an ask and a subject, and the claims it used
    section 3  body 70-110 words, never more than 125
    section 3  subject 30-55 characters, no Re:/Fwd:
    section 3  product name and "AI" at most once each
    section 3  banned adjectives
    section 4  the fixed opt-out -> must be Section 5's, verbatim
    section 5  the worked example -> embedded in the system prompt as written
    section 6  the drafting model -> must be Section 3's

Migration 012's slot CHECK must be the slots Assess Grounding resolves.

When one fires, the fix is to update the JS to match the doc -- never the other
way round.
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "workflows", "drafting.json")
OUT = os.environ.get("DRAFTING_OUT", OUT)

MASTER_REF = os.environ.get(
    "NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md")
)


SKILL = os.environ.get(
    "NOVASCOUT_DRAFTING_SKILL", os.path.join(HERE, "..", "..", "NovaScout_DraftingSkill.md")
)

# Migration 010 creates the claims library. Its countries CHECK must be exactly
# Section 12's geography list, or a proof line for a real target country could
# not be saved (or one for a non-target country could).
CLAIMS_MIGRATION = os.environ.get(
    "NOVASCOUT_CLAIMS_MIGRATION",
    os.path.join(HERE, "..", "..", "postgres", "migrations", "010_claims_library.sql"),
)

# Migration 012 gives the library its v3 slots. They must be the slots Assess
# Grounding resolves, or a slot the table allows would never reach the model
# (or one the model is promised would be impossible to fill).
CLAIMS_V3_MIGRATION = os.environ.get(
    "NOVASCOUT_CLAIMS_V3_MIGRATION",
    os.path.join(HERE, "..", "..", "postgres", "migrations", "012_claims_library_v3.sql"),
)


def js(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _doc():
    with io.open(MASTER_REF, encoding="utf-8") as fh:
        return fh.read()


def _skill():
    with io.open(SKILL, encoding="utf-8") as fh:
        return fh.read()


PG_CRED = {"postgres": {"id": "novascoutPg01", "name": "Postgres - novascout"}}

# Created by provision_anthropic_credential.py from ANTHROPIC_API_KEY in .env.
# The key lives encrypted in n8n, never in this JSON.
ANTHROPIC_CRED = {"anthropicApi": {"id": "novascoutAnthropic01", "name": "Anthropic - Nova Scout drafting"}}


# ---------------------------------------------------------------------------
# Sender identity (Section 5, "Signature: name, one line of title, phone.")
#
# Read from the environment, falling back to the repo's .env -- the file
# docker-compose already reads, gitignored, and visible to every shell and
# every future session without a restart. (A Windows user variable is not: a
# terminal opened before it was set never sees it.) A real environment
# variable wins over .env, the usual dotenv precedence.
#
# There is deliberately NO default name. There used to be one, "Fatima", and
# the signature is baked into the Code node at build time -- so a rebuild from
# any shell without the variable set silently signed every future draft as the
# wrong person, with nothing to say it had happened. A missing name now
# refuses the build.
#
# Title and phone still only warn when unset. Nothing is invented to fill the
# gap: a cold email carrying a plausible-looking phone number that belongs to
# nobody is worse than one carrying no phone number at all.
# ---------------------------------------------------------------------------

ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))

_BUILD_SETTINGS = (
    "NOVASCOUT_SENDER_NAME",
    "NOVASCOUT_SENDER_TITLE",
    "NOVASCOUT_SENDER_PHONE",
)


def _env_file_values(path, keys):
    """Just `keys` from a dotenv file. The same file holds credentials this
    build has no business reading, so nothing else is kept."""
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
    """(value, where it came from). An empty environment variable does not
    shadow a real value in .env."""
    v = os.environ.get(key, "").strip()
    if v:
        return v, "environment"
    v = _FILE_SETTINGS.get(key, "").strip()
    if v:
        return v, os.path.abspath(ENV_FILE)
    return "", None


SENDER_NAME, SENDER_SOURCE = _setting("NOVASCOUT_SENDER_NAME")
SENDER_TITLE, _ = _setting("NOVASCOUT_SENDER_TITLE")
SENDER_PHONE, _ = _setting("NOVASCOUT_SENDER_PHONE")

if not SENDER_NAME:
    raise AssertionError(
        "NOVASCOUT_SENDER_NAME is not set -- not in the environment and not in %s.\n"
        "Every email draft is signed with it and the system prompt names it. There\n"
        "is deliberately no default: a default name is how drafts came to be signed\n"
        "by the wrong person. Add it to .env and rebuild." % os.path.abspath(ENV_FILE)
    )

SIGNATURE = "\n".join([p for p in [SENDER_NAME, SENDER_TITLE, SENDER_PHONE] if p])


# ---------------------------------------------------------------------------
# Spec parsers
# ---------------------------------------------------------------------------

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _number(word, where):
    w = word.lower()
    if w in NUMBER_WORDS:
        return NUMBER_WORDS[w]
    if w.isdigit():
        return int(w)
    raise AssertionError("%r in %s is not a number" % (word, where))


def load_min_facts(doc=None):
    """Parse the grounding threshold out of Section 9, Workflow 4."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"lacks at least (\w+) specific facts", doc)
    if not m:
        raise AssertionError(
            "grounding threshold not found in %s -- expected 'lacks at least N "
            "specific facts'" % MASTER_REF
        )
    return _number(m.group(1), MASTER_REF)


def load_fact_kinds(doc=None):
    """Parse the grounding fact vocabulary out of the same sentence.

    The parenthetical after 'specific facts' IS the vocabulary. A category added
    to the doc that code_assess.js does not collect would silently make the
    guard stricter than the spec; one removed would make it looser. Both are
    build failures.
    """
    doc = doc if doc is not None else _doc()
    m = re.search(r"lacks at least \w+ specific facts \(([^)]+)\)", doc)
    if not m:
        raise AssertionError(
            "grounding fact list not found in %s -- expected a parenthetical after "
            "'specific facts'" % MASTER_REF
        )
    kinds = [k.strip().lower().replace(" ", "_") for k in m.group(1).split(",")]
    kinds = [k for k in kinds if k]
    if len(kinds) < 2:
        raise AssertionError("grounding fact list in %s looks empty: %r" % (MASTER_REF, kinds))
    return kinds


# "70-110 words and never more than 125" (skill) / "70-110 words, never more
# than 125" (Master Ref). One pattern for both phrasings.
_LENGTH_RE = r"(\d+)[–-](\d+) words,? (?:and )?never more than (\d+)"


def load_length_rule(text, where):
    """Every statement of the body-length rule in `text`. They must all agree."""
    found = set(tuple(int(n) for n in m) for m in re.findall(_LENGTH_RE, text))
    if not found:
        raise AssertionError("body length rule not found in %s -- expected 'N-M words ... never more "
                             "than C'" % where)
    if len(found) > 1:
        raise AssertionError("%s states more than one body length rule: %r -- resolve the doc first"
                             % (where, sorted(found)))
    return found.pop()


def load_opt_out(doc=None):
    """Parse the opt-out sentence VERBATIM out of Section 5.

    Section 5 makes this sentence the entire opt-out mechanism, and Section 5's
    compliance note makes a trivially-honoured opt-out the basis for the
    GDPR/KVKK legitimate-interest position. A paraphrase is a compliance defect,
    not a style change -- so it is parsed, not retyped.
    """
    doc = doc if doc is not None else _doc()
    m = re.search(r"Use a plain sentence:\s*\*\"(.+?)\"\*", doc)
    if not m:
        raise AssertionError(
            "opt-out sentence not found in %s -- expected 'Use a plain sentence: "
            '*"..."*\'' % MASTER_REF
        )
    return m.group(1)


def load_skill_opt_out(skill=None):
    """Skill section 4, Fixed: '- Opt-out: "..."'. Must be Section 5's, verbatim."""
    skill = skill if skill is not None else _skill()
    m = re.search(r'^- Opt-out: "(.+)"\s*$', skill, re.M)
    if not m:
        raise AssertionError("the fixed opt-out not found in %s -- expected '- Opt-out: \"...\"'" % SKILL)
    return m.group(1)


def load_banned_adjectives(doc=None):
    """Parse the adjectives Section 9 names explicitly."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"no adjectives like ([^,]+(?:,[^,]+)*?), no merge-tag", doc)
    if not m:
        raise AssertionError(
            "banned-adjective list not found in %s -- expected 'no adjectives like "
            '"x" or "y", no merge-tag\'' % MASTER_REF
        )
    return [a.lower() for a in re.findall(r'"([^"]+)"', m.group(1))]


def load_skill_banned_adjectives(skill=None):
    """Skill section 3, Everywhere: "No banned adjectives (a, b, c)." """
    skill = skill if skill is not None else _skill()
    m = re.search(r"No banned adjectives \(([^)]+)\)", skill)
    if not m:
        raise AssertionError("banned adjectives not found in %s -- expected 'No banned adjectives "
                             "(a, b, ...)'" % SKILL)
    return [a.strip().lower() for a in m.group(1).split(",") if a.strip()]


def load_therapeutic_areas(doc=None):
    """Parse the locked taxonomy enum out of Section 9's taxonomy section.

    Identical parser to n8n/scoring/build_workflow.py, deliberately duplicated
    rather than imported: the two generators must be able to fail independently,
    and a shared helper module is one more thing that can drift from the doc.
    """
    doc = doc if doc is not None else _doc()
    anchor = re.search(r"\*\*Therapeutic area taxonomy[^\n]*\*\*", doc)
    if not anchor:
        raise AssertionError(
            "taxonomy section not found in %s -- expected a line containing "
            "'**Therapeutic area taxonomy ...**'" % MASTER_REF
        )
    block = re.search(r"\n```\n(.*?)\n```", doc[anchor.end():], re.S)
    if not block:
        raise AssertionError("no fenced list follows the taxonomy heading in %s" % MASTER_REF)
    areas = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
    if len(areas) < 2:
        raise AssertionError("taxonomy in %s looks empty: %r" % (MASTER_REF, areas))
    return areas


def load_drafting_model(doc=None):
    """Section 3: "**Drafting model: `<id>`**, effort `<level>` ...". The skill
    names the model too (section 6); the two must agree, and the request ships
    exactly this id."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\*\*Drafting model: `([a-z0-9-]+)`\*\*, effort `(low|medium|high|xhigh|max)`", doc)
    if not m:
        raise AssertionError(
            "drafting model not found in Section 3 of %s -- expected '**Drafting model: `id`**, "
            "effort `level`'" % MASTER_REF
        )
    return m.group(1), m.group(2)


def load_skill_model(skill=None):
    skill = skill if skill is not None else _skill()
    m = re.search(r"^- Drafting node: `([a-z0-9-]+)` via the Anthropic API", skill, re.M)
    if not m:
        raise AssertionError("drafting model not found in section 6 of %s -- expected '- Drafting node: "
                             "`id` via the Anthropic API'" % SKILL)
    return m.group(1)


def load_drafts_columns(doc=None):
    """Parse the `drafts` column list out of Section 8's schema block."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\ndrafts\n(.*?)\n\n", doc, re.S)
    if not m:
        raise AssertionError("drafts table not found in Section 8 of %s" % MASTER_REF)
    cols = []
    for chunk in m.group(1).replace("\n", " ").split(","):
        name = chunk.strip().split(" ")[0].strip()
        if name:
            cols.append(name)
    if "lead_id" not in cols:
        raise AssertionError("drafts column parse looks wrong in %s: %r" % (MASTER_REF, cols))
    return cols


def load_small_team(doc=None):
    """Section 12's company-size band -- what makes a confirmed headcount
    'small' enough for a headcount subject line."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\*\*Company:\*\*\s*(\d+)[-–](\d+) employees", doc)
    if not m:
        raise AssertionError(
            "company-size band not found in Section 12 of %s -- expected "
            "'**Company:** N-M employees'" % MASTER_REF
        )
    return int(m.group(1)), int(m.group(2))


def load_geographies(doc=None):
    """Section 12's target geographies."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\*\*Geographies:\*\*\s*([^\n]+?)\.?\n", doc)
    if not m:
        raise AssertionError("geography list not found in Section 12 of %s" % MASTER_REF)
    return [g.strip() for g in m.group(1).split(",") if g.strip()]


def load_clock_countries(doc=None):
    """Section 12's "Business-hours clocks" table, as country names only.

    Duplicated from n8n/sendtrack/build_workflow.py on purpose, like the
    taxonomy loader above: the stages must be able to fail independently. The
    sendtrack build is the one that checks the table's numbers against
    code_decide.js; drafting only needs to know which countries are IN it,
    because a lead in a country that is not can never be sent to (Section 12),
    and both Assess Grounding and the Approval Gate stop one before any model
    call."""
    doc = doc if doc is not None else _doc()
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
    names, core = [], []
    for line in table.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError("clock row %r does not have 4 cells in %s" % (line, MASTER_REF))
        if not re.match(r"^UTC[+-]\d{1,2}:\d{2}$", cells[1]):
            raise AssertionError("clock row %r has an unreadable offset %r" % (cells[0], cells[1]))
        if cells[0] in names:
            raise AssertionError("clock row %r is listed twice in %s" % (cells[0], MASTER_REF))
        names.append(cells[0])
        if cells[3] == "core":
            core.append(cells[0])
    if not names:
        raise AssertionError("Section 12's clock table is empty in %s" % MASTER_REF)
    return names, core


def load_link_free_weeks(doc=None):
    """Section 9, Workflow 4: "**Links:** none in warm-up weeks 1-N". The
    existing link policy, kept by v3; the v3 skill itself only says the ask
    carries no link."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"\*\*Links:\*\* none in warm-up weeks 1[–-](\d+)", doc)
    if not m:
        raise AssertionError("link-free warm-up weeks not found in %s -- expected '**Links:** none in "
                             "warm-up weeks 1-N'" % MASTER_REF)
    return int(m.group(1))


def load_composes(skill=None):
    """Skill section 1: v3's "model composes the whole email". This sentence is
    the whole v3 design, so it decides the model's output schema: a body and an
    ask, not a hook to paste library lines after. If the skill ever goes back
    to line-picking, the schema is wrong until someone decides how, and the
    build stops rather than quietly keep composing."""
    skill = skill if skill is not None else _skill()
    if not re.search(r"model composes the whole email from the hook fact plus approved claims", skill):
        raise AssertionError(
            "the composition rule not found in %s -- expected 'model composes the whole email from the "
            "hook fact plus approved claims' (skill v3, section 1)" % SKILL
        )
    return True


def load_subject_rules(skill=None):
    """Skill section 3, Subject: the character range and the fake-reply
    prefixes, parsed rather than retyped."""
    skill = skill if skill is not None else _skill()
    m = re.search(r"\*\*Subject\.\*\* (\d+)[–-](\d+) characters\.([^\n]*)", skill)
    if not m:
        raise AssertionError("subject rules not found in %s -- expected '**Subject.** N-M characters.'" % SKILL)
    rest = m.group(3)
    prefixes = re.findall(r'no "(\w+):"', rest, re.I)
    if len(prefixes) < 2:
        raise AssertionError('subject reply prefixes not found in %s -- expected \'No "Re:", no "Fwd:"\'' % SKILL)
    if "Never the product name" not in rest or 'Never "AI"' not in rest:
        raise AssertionError('subject rules in %s no longer say \'Never the product name. Never "AI".\'' % SKILL)
    return {"min": int(m.group(1)), "max": int(m.group(2)), "prefixes": [p.lower() for p in prefixes]}


def load_at_most_once(skill=None):
    """Skill section 3, Naming the product: the name and "AI" at most N times."""
    skill = skill if skill is not None else _skill()
    name = re.search(r"The name appears at most (\w+), in brackets", skill)
    ai = re.search(r'The word "AI" appears at most (\w+)', skill)
    if not name or not ai:
        raise AssertionError("the product-name / \"AI\" limits not found in %s" % SKILL)
    once = {"once": 1, "twice": 2}
    return once.get(name.group(1), None), once.get(ai.group(1), None)


def load_worked_example(skill=None):
    """Skill section 5's v3 example: the subject and the body, exactly as the
    skill writes them. It is the one example the model is shown, so it is
    parsed rather than retyped -- an edit to the skill's example reaches the
    prompt by rebuilding."""
    skill = skill if skill is not None else _skill()
    m = re.search(r"\*\*v3 \(same facts, nothing invented\):\*\*\s*\n\s*\nSubject: `([^`]+)`\s*\n\s*\n((?:>.*\n?)+)",
                  skill)
    if not m:
        raise AssertionError("the v3 worked example not found in section 5 of %s" % SKILL)
    paras = [p.strip() for p in re.sub(r"^> ?", "", m.group(2), flags=re.M).split("\n\n")]
    return m.group(1), "\n\n".join(p for p in paras if p)


def load_claims_countries():
    """The geography list migration 010's countries CHECK allows."""
    with io.open(CLAIMS_MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    m = re.search(r"claims_countries_are_geographies CHECK \(.*?ARRAY\[(.*?)\]", sql, re.S)
    if not m:
        raise AssertionError("claims_countries_are_geographies not found in %s" % CLAIMS_MIGRATION)
    return re.findall(r"'([^']+)'", m.group(1))


def load_claims_slots():
    """The slots migration 012's claims_slot_v3 CHECK allows."""
    with io.open(CLAIMS_V3_MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    m = re.search(r"claims_slot_v3\s+CHECK \(slot IN \(([^)]*)\)\)", sql)
    if not m:
        raise AssertionError("claims_slot_v3 not found in %s" % CLAIMS_V3_MIGRATION)
    return re.findall(r"'([^']+)'", m.group(1))


DOC = _doc()
SKILL_DOC = _skill()
MIN_FACTS = load_min_facts(DOC)
FACT_KINDS = load_fact_kinds(DOC)
LENGTH = load_length_rule(SKILL_DOC, SKILL)
OPT_OUT = load_opt_out(DOC)
BANNED_NAMED = load_banned_adjectives(DOC)
SKILL_BANNED = load_skill_banned_adjectives(SKILL_DOC)
THERAPEUTIC_AREAS = load_therapeutic_areas(DOC)
DRAFT_MODEL, DRAFT_EFFORT = load_drafting_model(DOC)
DRAFTS_COLUMNS = load_drafts_columns(DOC)
SMALL_TEAM = load_small_team(DOC)
GEOGRAPHIES = load_geographies(DOC)
CLOCK_COUNTRIES, CLOCK_CORE = load_clock_countries(DOC)
LINK_FREE_WEEKS = load_link_free_weeks(DOC)
COMPOSES = load_composes(SKILL_DOC)
SUBJECT_RULES = load_subject_rules(SKILL_DOC)
NAME_MAX, AI_MAX = load_at_most_once(SKILL_DOC)
EXAMPLE_SUBJECT, EXAMPLE_BODY = load_worked_example(SKILL_DOC)

# Claims the Nova Agent Kit source does not support (checked 2026-10-02; see the
# `note` on each claims_library row) are narrowed in the table. The skill's
# worked example predates that check and still uses the wider wording, and the
# model copies its one example closely -- so the same narrowing is applied to
# the example before it reaches the prompt. Each pair must still match: when
# the skill's example is edited, this list is refused until it is revisited.
EXAMPLE_NARROWINGS = [
    # BEN-BRIEF: the RFP intake records therapeutic area and study phase, not a brief.
    ("collects their study brief", "collects their therapeutic area and study phase"),
]
for _wide, _narrow in EXAMPLE_NARROWINGS:
    assert _wide in EXAMPLE_BODY, (
        "the skill's worked example no longer says %r -- revisit EXAMPLE_NARROWINGS in build_workflow.py" % _wide
    )
    EXAMPLE_BODY = EXAMPLE_BODY.replace(_wide, _narrow)

_doc_length = load_length_rule(DOC, MASTER_REF)
assert _doc_length == LENGTH, (
    "the drafting skill and the Master Ref disagree on the body length:\n"
    "  %s: %d-%d words, never more than %d\n  %s: %d-%d words, never more than %d"
    % ((SKILL,) + LENGTH + (MASTER_REF,) + _doc_length)
)

_skill_opt_out = load_skill_opt_out(SKILL_DOC)
assert _skill_opt_out == OPT_OUT, (
    "the drafting skill's fixed opt-out is not Section 5's, verbatim:\n  skill: %r\n  doc:   %r"
    % (_skill_opt_out, OPT_OUT)
)

_skill_model = load_skill_model(SKILL_DOC)
assert _skill_model == DRAFT_MODEL, (
    "the drafting skill and the Master Ref name different drafting models:\n  skill: %s\n  doc:   %s"
    % (_skill_model, DRAFT_MODEL)
)

_claims_countries = load_claims_countries()
assert sorted(_claims_countries) == sorted(GEOGRAPHIES), (
    "migration 010's claims_countries_are_geographies is not Section 12's geography list:\n"
    "  doc: %r\n  sql: %r" % (GEOGRAPHIES, _claims_countries)
)


# ---------------------------------------------------------------------------
# Drift guards against the Code-node sources
# ---------------------------------------------------------------------------

def _js_string_array(src, name, where):
    m = re.search(r"const %s = \[(.*?)\];" % re.escape(name), src, re.S)
    assert m, "%s array not found in %s" % (name, where)
    return re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))


def _js_int(src, name, where):
    m = re.search(r"const %s = (\d+);" % re.escape(name), src)
    assert m, "%s not found in %s" % (name, where)
    return int(m.group(1))


def _assert_assess_matches_spec():
    src = js("code_assess.js")

    found = _js_int(src, "MIN_FACTS", "code_assess.js")
    assert found == MIN_FACTS, (
        "grounding threshold drifted between the Master Ref and code_assess.js:\n"
        "  doc: at least %d specific facts\n  js:  MIN_FACTS = %d" % (MIN_FACTS, found)
    )

    found_areas = _js_string_array(src, "THERAPEUTIC_AREAS", "code_assess.js")
    assert found_areas == THERAPEUTIC_AREAS, (
        "therapeutic-area taxonomy drifted between the Master Ref and code_assess.js:\n"
        "  doc: %r\n  js:  %r" % (THERAPEUTIC_AREAS, found_areas)
    )

    # Every fact category the doc names must be a `kind` the node can emit, and
    # the node must not invent categories the doc does not license. This is the
    # guard that stops the fact vocabulary quietly widening until the threshold
    # is trivial to clear -- which would leave the grounding guard passing every
    # lead while still looking correct.
    emitted = set(re.findall(r"kind: '([a-z_]+)'", src))
    assert emitted == set(FACT_KINDS), (
        "grounding fact vocabulary drifted between the Master Ref and code_assess.js:\n"
        "  doc: %r\n  js:  %r" % (sorted(FACT_KINDS), sorted(emitted))
    )
    m = re.search(r"const missing = \[(.*?)\]", src, re.S)
    assert m, "missing-fact list not found in code_assess.js"
    listed = re.findall(r"'([a-z_]+)'", m.group(1))
    assert listed == FACT_KINDS, (
        "the missing-fact report in code_assess.js is not the doc's fact list:\n"
        "  doc: %r\n  js:  %r" % (FACT_KINDS, listed)
    )

    m = re.search(r"const SMALL_TEAM = \{ min: (\d+), max: (\d+) \};", src)
    assert m, "SMALL_TEAM not found in code_assess.js"
    found_band = (int(m.group(1)), int(m.group(2)))
    assert found_band == SMALL_TEAM, (
        "the small-team band drifted between Section 12 and code_assess.js:\n"
        "  doc: %d-%d employees\n  js:  SMALL_TEAM = %r" % (SMALL_TEAM + (found_band,))
    )

    # Migration 012's slots are the table; LIBRARY_SLOTS (+ the optional link)
    # are what Assess Grounding resolves and offers the model.
    slots = _js_string_array(src, "LIBRARY_SLOTS", "code_assess.js")
    table = load_claims_slots()
    assert sorted(slots + ["link"]) == sorted(table), (
        "the claims-library slots drifted between migration 012 and code_assess.js:\n"
        "  sql: %r\n  js:  LIBRARY_SLOTS = %r (+ link)" % (table, slots)
    )


def _assert_assemble_matches_spec():
    src = js("code_assemble.js")

    m = re.search(r'const OPT_OUT = "(.*?)";', src)
    assert m, "OPT_OUT not found in code_assemble.js"
    assert m.group(1) == OPT_OUT, (
        "the opt-out sentence drifted from Section 5. It is the GDPR/KVKK opt-out "
        "mechanism and must match VERBATIM:\n  doc: %r\n  js:  %r" % (OPT_OUT, m.group(1))
    )

    found = (_js_int(src, "BODY_TARGET_MIN", "code_assemble.js"),
             _js_int(src, "BODY_TARGET_MAX", "code_assemble.js"),
             _js_int(src, "BODY_CEILING", "code_assemble.js"))
    assert found == LENGTH, (
        "the body length drifted between the drafting skill and code_assemble.js:\n"
        "  skill: %d-%d words, never more than %d\n  js:    %r" % (LENGTH + (found,))
    )

    banned = [a.lower() for a in _js_string_array(src, "BANNED_ADJECTIVES", "code_assemble.js")]
    missing = [a for a in BANNED_NAMED + SKILL_BANNED if a not in banned]
    assert not missing, (
        "the Master Ref or the drafting skill names adjectives that code_assemble.js does not ban: %r\n"
        "  js bans: %r" % (missing, banned)
    )

    # Section 5 caps URLs. Parsed rather than hardcoded so a change to "no URLs"
    # or "two URLs" cannot pass silently.
    m = re.search(r"Maximum (\w+) plain URL", DOC)
    assert m, "URL cap not found in Section 5 of %s" % MASTER_REF
    doc_urls = NUMBER_WORDS.get(m.group(1).lower(), None)
    if doc_urls is None and m.group(1).isdigit():
        doc_urls = int(m.group(1))
    assert doc_urls is not None, "URL cap %r in %s is not a number" % (m.group(1), MASTER_REF)
    found_urls = _js_int(src, "MAX_URLS", "code_assemble.js")
    assert found_urls == doc_urls, (
        "URL cap drifted between the Master Ref and code_assemble.js:\n"
        "  doc: maximum %d\n  js:  MAX_URLS = %d" % (doc_urls, found_urls)
    )

    found = _js_int(src, "LINK_FREE_WEEKS", "code_assemble.js")
    assert found == LINK_FREE_WEEKS, (
        "the link-free warm-up drifted between the Master Ref and code_assemble.js:\n"
        "  doc: weeks 1-%d no links\n  js:  LINK_FREE_WEEKS = %d" % (LINK_FREE_WEEKS, found)
    )
    found = (_js_int(src, "SUBJECT_MIN_CHARS", "code_assemble.js"),
             _js_int(src, "SUBJECT_MAX_CHARS", "code_assemble.js"))
    assert found == (SUBJECT_RULES["min"], SUBJECT_RULES["max"]), (
        "the subject length drifted between the drafting skill and code_assemble.js:\n"
        "  skill: %d-%d characters\n  js:    %r" % (SUBJECT_RULES["min"], SUBJECT_RULES["max"], found)
    )
    found = _js_string_array(src, "SUBJECT_REPLY_PREFIXES", "code_assemble.js")
    assert sorted(found) == sorted(SUBJECT_RULES["prefixes"]), (
        "the fake-reply subject prefixes drifted between the drafting skill and code_assemble.js:\n"
        "  skill: %r\n  js:    %r" % (SUBJECT_RULES["prefixes"], found)
    )
    found = (_js_int(src, "PRODUCT_NAME_MAX", "code_assemble.js"), _js_int(src, "AI_MAX", "code_assemble.js"))
    assert found == (NAME_MAX, AI_MAX), (
        "the product-name / \"AI\" limits drifted between the drafting skill and code_assemble.js:\n"
        "  skill: name at most %r, AI at most %r\n  js:    PRODUCT_NAME_MAX, AI_MAX = %r" % (NAME_MAX, AI_MAX, found)
    )


_assert_assess_matches_spec()
_assert_assemble_matches_spec()


# ---------------------------------------------------------------------------
# The Claude drafting call
#
# Skill v3: the model composes the whole email. Its JSON schema is the email's
# parts -- subject, body, the one ask -- for both channels, plus the claim codes
# it used, so Assemble Drafts can check each against what this lead was offered
# and record them for the learning loop. The ask is a field of its own so
# "exactly one ask" is checkable: one question there, no request in the body.
#
# Request parameters, from the live docs on 2026-10-02 (Sonnet 5.5 overview and
# migration guide), not carried from older settings: no temperature/top_p/top_k
# (a non-default value is a 400 on this model); thinking omitted, which runs
# adaptive thinking; effort set explicitly (default `high`, and the guide says
# to start at `high` for work that is neither agentic nor latency-sensitive);
# structured output through output_config.format; max_tokens covers thinking
# plus text, so it is generous. Section 3 holds the model and effort.
# ---------------------------------------------------------------------------

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "email_subject": {"type": "string"},
        "email_body": {"type": "string"},
        "email_ask": {"type": "string"},
        "linkedin_body": {"type": "string"},
        "linkedin_ask": {"type": "string"},
        "email_claims": {"type": "array", "items": {"type": "string"}},
        "linkedin_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["email_subject", "email_body", "email_ask", "linkedin_body", "linkedin_ask",
                 "email_claims", "linkedin_claims"],
    "additionalProperties": False,
}

# Composition needs a body; "exactly one ask" needs the ask apart from it.
assert COMPOSES and {"email_body", "email_ask"} <= set(DRAFT_SCHEMA["properties"]), (
    "the model's output schema does not match the drafting skill: skill v3 has the model compose "
    "the whole email, so the schema needs email_body and email_ask"
)
# What Assemble Drafts requires must be exactly what the schema promises.
_assemble_fields = (_js_string_array(js("code_assemble.js"), "FIELDS", "code_assemble.js")
                    + _js_string_array(js("code_assemble.js"), "CLAIM_LISTS", "code_assemble.js"))
assert sorted(_assemble_fields) == sorted(DRAFT_SCHEMA["required"]), (
    "Assemble Drafts' FIELDS do not match the model's output schema:\n  schema: %r\n  js:     %r"
    % (DRAFT_SCHEMA["required"], _assemble_fields)
)

CLAUDE_REQUEST = {
    "model": DRAFT_MODEL,
    "max_tokens": 16000,
    "output_config": {
        "effort": DRAFT_EFFORT,
        "format": {"type": "json_schema", "schema": DRAFT_SCHEMA},
    },
}

# The fields the HTTP node's body reads from the item.
CLAUDE_BODY = "={{ JSON.stringify($json.request) }}"
_claude_reads = set(re.findall(r"\$json\.(\w+)", CLAUDE_BODY))


def _assert_emitted_upstream(js_src, fields, node_name):
    # An object key at the start of a line -- `    prompt: prompt,` -- never the
    # same word followed by a colon in prose. A comment reading "the v2 prompt:
    # ..." once satisfied a looser pattern and let this guard pass with the field
    # deleted; test_drift_guards.py caught it.
    missing = [f for f in sorted(fields)
               if not re.search(r"^[ \t]*%s:" % re.escape(f), js_src, re.M)]
    assert not missing, (
        "the Claude node reads $json.%s, but %s does not emit %r. A field the "
        "upstream node does not set resolves to an empty string at runtime with "
        "no error." % ("/$json.".join(sorted(fields)), node_name, missing)
    )


# ---------------------------------------------------------------------------
# System prompt -- drafting skill v3
#
# The model now writes the whole message, so it is told what Section 3 of the
# skill says, in the skill's own numbers (parsed above). What we sell is NOT in
# here: every product sentence must come from the per-lead APPROVED CLAIMS list
# Assess Grounding builds from the live claims_library, so an operator's edit to
# the table reaches the next draft with no rebuild. The grounding rules that
# fixed v1's joining failures are kept, because they are about the prospect
# facts, which have not changed.
#
# The one worked example is the skill's own section 5 email, parsed. It carries
# another lead's facts (oncology and immunology); Assemble Drafts tags a draft
# that names an area its lead does not have.
# ---------------------------------------------------------------------------

_adjectives = ", ".join(SKILL_BANNED)

SYSTEM_PROMPT = "\n".join([
    "You write one cold first-touch email and one LinkedIn DM to a small clinical research",
    "business -- a contract research organisation (CRO), an independent research site or a",
    "site management organisation (SMO); each message says which -- sent by " + SENDER_NAME + ".",
    "A person reviews both before anything is sent.",
    "",
    "Each message gives you two lists. VERIFIED FACTS are the only facts about the",
    "prospect that exist. APPROVED CLAIMS are the only things you may say about what we",
    "built, the problem it solves, and who uses it. You may rephrase a claim. You may",
    "never widen what it means.",
    "",
    "THE PROSPECT -- one sentence, one fact.",
    "- Open each message from the fact you are told to open it with: exactly one",
    "  prospect fact, stated, about them (\"Your site lists ...\", \"Your recruiting",
    "  trial ...\").",
    "- Never build a sentence out of two facts. If fact 2 gives you a trial and fact 3",
    "  a city, \"your trial in that city\" is a claim neither fact makes. Each fact ends",
    "  with a sentence saying what it does not establish. Obey it literally.",
    "- Everything not in the facts does not exist: no headcount, no clients, no growth,",
    "  no plans, no praise of their website, no trial you were not given.",
    "- Never describe them as sponsoring anything. In these emails \"sponsor\" means the",
    "  biotech, pharma, device or academic company that runs a trial and hires a CRO or a",
    "  research site -- their client.",
    "  Say \"running\", \"recruiting\" or \"your trial\".",
    "- A trial is on ClinicalTrials.gov, not on their site: never write \"your site",
    "  lists\" about a trial. Name one trial at most. Their site is where the",
    "  therapeutic areas come from, and a list of areas is what they work in, not a",
    "  list of trials.",
    "- Never open on a person's name or a city. Never write the company's name.",
    "",
    "THE EMAIL, in about this order:",
    "  1. Hook -- the one prospect fact.",
    "  2. Pain and stakes -- ONE angle from the approved list. Do not stack them.",
    "  3. What it does -- a description from the list the first time you mention it,",
    "     then one or two benefits. Not more.",
    "     NO REPEATS: the description and the benefits must not repeat the same",
    "     capability. Each line says what it is about; never use two lines that share",
    "     one, and never restate in your own words what the description already said.",
    "  4. Proof -- the proof line you were given, matched to their country.",
    "  5. Ask -- exactly one, answerable with one word. It goes in the ask field; the",
    "     body before it asks for nothing.",
    "",
    "LENGTH: the body plus the ask is %d-%d words and never more than %d. Shorter is"
    % LENGTH,
    "fine if nothing useful is lost. Say one thing well.",
    "",
    "SUBJECT: %d-%d characters, sentence case, no capitals except a name the facts"
    % (SUBJECT_RULES["min"], SUBJECT_RULES["max"]),
    "spell that way. Build it from the fact the email opens with, or from the pain",
    "when you are told to. Never the product name. Never \"AI\". No \"Re:\", no \"Fwd:\".",
    "",
    "NAMING THE PRODUCT:",
    "- Describe it first, using a description line. The name appears at most once, in",
    "  brackets: \"(we call it Nova)\". Never \"Nova\" anywhere else.",
    "- Never call it a chatbot or a Q&A bot. It qualifies the inquiry, captures the",
    "  details, and passes the lead on.",
    "- The word \"AI\" appears at most once, inside the description. Never \"AI-powered\".",
    "",
    "CLAIMS -- forbidden, all of them:",
    "- guarantees (\"you'll never lose a sponsor\")",
    "- any number, percentage or multiplier that is not in the approved claims or the",
    "  facts",
    "- claiming to identify anonymous website visitors",
    "- naming any CRM, tool or integration",
    "- supported languages",
    "- any count of clients beyond the deployments the proof line names",
    "- calling Vertex Clinical Research a CRO -- it is a clinical research center",
    "- saying it books calls or fills a calendar -- it sends the sponsor your booking",
    "  link, and the sponsor books",
    "- saying it answers from SOPs or from documents -- it answers from their website",
    "The stakes line is about the industry, not about us: \"a single sponsor inquiry",
    "can be a multi-million-dollar study\" is allowed; \"we will win you millions\" is not.",
    "",
    "THE ASK: one question. No links, no scheduling link, no call length, no second",
    "question.",
    "",
    "EVERYWHERE: plain text. No links of any kind, no bullets, no placeholders, no",
    "merge tags. No greeting and no sign-off -- both are added afterwards. Never these",
    "words: " + _adjectives + ". No invented urgency, no flattery, no",
    "exclamation marks.",
    "",
    "THE LINKEDIN DM follows the same rules. It has no subject and may be shorter.",
    "",
    "One example, for another company. Copy the shape and the restraint, never its",
    "facts -- your facts are only the ones in your own numbered list.",
    "  Subject: " + EXAMPLE_SUBJECT,
] + ["  " + ln if ln else "" for ln in EXAMPLE_BODY.split("\n")] + [
    "",
    "Return JSON: email_subject; email_body (everything before the ask, paragraphs",
    "separated by a blank line); email_ask; linkedin_body; linkedin_ask; email_claims and",
    "linkedin_claims (the code of every approved claim each message used).",
])

# The skill's example is shown to the model as written, not paraphrased.
assert EXAMPLE_SUBJECT in SYSTEM_PROMPT and EXAMPLE_BODY.split("\n\n")[0] in SYSTEM_PROMPT, \
    "the skill's worked example did not reach the system prompt"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

BATCH_SQL = """-- The queue for this stage: Section 9 Workflow 4's "batch where
-- status='contact_found'". scores.lead_id, contacts.lead_id and
-- enrichments.lead_id are each UNIQUE (migrations 003, 004, 002), so all three
-- joins are 1:1 and this cannot fan out into duplicate drafts.
--
-- Every join is an INNER join on purpose. A lead at 'contact_found' that is
-- missing any of the three rows is not draftable and is not a row this workflow
-- should be inventing defaults for -- it is a bug upstream, and it should stay
-- visible in the queue rather than being silently drafted from nulls.
--
-- city and founder_title live in enrichments.raw_extraction rather than in a
-- column of their own; city is one of Section 9's four grounding facts, so it
-- is pulled out here rather than in the Code node.
--
-- library: the ACTIVE rows of the claims library (migrations 010, 012), on every row.
-- Read here, per run, so an operator's edit reaches the next draft without a
-- rebuild -- and read in this query rather than a node of its own, because a
-- Postgres node replaces the items, and a field set upstream of this one would
-- be gone by Assess Grounding (Workflow 4's silent-prompt bug).
--
-- warmup_week: the same derivation as Workflow 6's Load Send State -- week 1
-- starts on the sender-day (Asia/Karachi) of the mailbox's first external send,
-- counting the Sent-folder mirror and unconfirmed claims. NULL = none yet, i.e.
-- the first send will be week 1. It decides the link rule (Section 9, Links).
--
-- auto_approve_email: settings.auto_approve_email (migration 014), read per run
-- for the same reason as the library. Off, the Approval Gate holds every draft
-- and no claim check is paid for; the database re-checks it on the write.
WITH counted AS (
  SELECT m.sent_at AS at FROM mailbox_sent m WHERE m.external
  UNION ALL
  SELECT o.sent_at FROM outreach_log o WHERE o.channel = 'email' AND o.message_id IS NULL
)
SELECT l.id            AS lead_id,
       l.domain,
       l.company_name,
       l.country,
       s.fit_score,
       c.name          AS contact_name,
       c.title         AS contact_title,
       c.email,
       c.linkedin_url,
       c.verified,
       e.therapeutic_areas,
       e.phases,
       e.founder_name,
       e.founder_linkedin,
       e.employee_estimate,
       e.site_quality_notes,
       e.raw_extraction->>'city'          AS city,
       e.raw_extraction->>'founder_title' AS founder_title,
       ct.company_type,
       (SELECT coalesce(jsonb_agg(jsonb_build_object(
                  'code', k.code, 'slot', k.slot, 'body', btrim(k.body),
                  'countries', to_jsonb(k.countries), 'measured', k.measured,
                  'confirmed', k.confirmed, 'capabilities', to_jsonb(k.capabilities),
                  'company_types', to_jsonb(k.company_types))
                  ORDER BY k.code), '[]'::jsonb)
          FROM claims_library k
         WHERE k.active)                  AS library,
       (SELECT (((now() AT TIME ZONE 'Asia/Karachi')::date
                 - (min(counted.at) AT TIME ZONE 'Asia/Karachi')::date) / 7) + 1
          FROM counted)                   AS warmup_week,
       auto_approve_email_enabled()       AS auto_approve_email
  FROM leads l
  JOIN scores      s ON s.lead_id = l.id
  JOIN contacts    c ON c.lead_id = l.id
  JOIN enrichments e ON e.lead_id = l.id
  -- CRO, site or SMO (2026-10-09, Workflow 1b): lead_company_type() (migration
  -- 019) is the one definition -- enrichment's company_type when it is one of
  -- the three, otherwise the source. Assess Grounding, the claims and Assemble
  -- Drafts' site rules all read it from here.
  CROSS JOIN LATERAL (SELECT lead_company_type(e.raw_extraction, l.source) AS company_type) ct
 WHERE l.status = 'contact_found'
   -- A lead whose kind has no active description line cannot be composed
   -- (the site and SMO lines start inactive, migration 019). It is skipped
   -- HERE, so it waits at contact_found without taking a place in the batch --
   -- ten such leads would otherwise starve every CRO lead behind them.
   AND EXISTS (SELECT 1 FROM claims_library k
                WHERE k.active AND k.slot = 'description' AND ct.company_type = ANY (k.company_types))
 ORDER BY l.id
 LIMIT $1;"""

WRITE_SQL = """-- Write both drafts and advance the lead in ONE statement, so a crash between
-- them cannot leave a lead advanced with no drafts (invisible forever) or
-- drafted but still queued (drafted again on the next tick).
--
-- The UPDATE runs first and is guarded on status='contact_found'. The INSERT
-- selects FROM that update's RETURNING, so if the guard matched nothing -- a
-- repeated run, a concurrent execution -- there is no row to join against and
-- zero drafts are inserted. That is the idempotency: re-running this statement
-- with the same payload is a no-op, not a duplicate.
--
-- drafts has no unique key on lead_id, and must not have one: Section 9 wants
-- two rows per lead (email + linkedin), and Workflow 6's follow-ups will add
-- more later. The status guard is what prevents duplicates here, not a
-- constraint -- which is exactly why the guard and the insert are one statement.
--
-- A REDRAFT RETIRES WHAT IT REPLACES. A lead only reaches this statement with
-- earlier drafts if an operator sent it back to 'contact_found' to be redrafted.
-- Its earlier first-touch drafts that are still pending or approved become
-- 'rejected' / 'bad draft' here, in the same statement. Left alone, an old
-- APPROVED draft would go out first: the lead returns to 'drafted', which is
-- what Workflow 6 sends from, and it sends the oldest approved draft. No
-- approval carries over to copy nobody has read: a new draft is 'pending', or
-- 'approved' by this workflow when the Approval Gate and the claim check passed
-- it (migration 014). Only an email draft the payload marks approved_by 'auto'
-- can come out approved here, and the table's own trigger re-checks the flag,
-- the tags, the low-context branch and every claim code against the live
-- library, turning a bad approval into a hold. 'sent' drafts and follow-ups are
-- never touched, and the body and any human edit (edited_body) of a retired
-- draft are kept. All CTEs share one snapshot, so `retired` cannot see, and
-- never retires, the rows `ins` adds.
WITH payload AS (
  SELECT $1::jsonb AS p
), adv AS (
  UPDATE leads
     SET status = 'drafted',
         updated_at = now()
   WHERE id = ((SELECT p FROM payload)->>'lead_id')::bigint
     AND status = 'contact_found'
  RETURNING id
), retired AS (
  UPDATE drafts d
     SET status = 'rejected',
         reject_reason = 'bad draft'
    FROM adv a
   WHERE d.lead_id = a.id
     AND d.status IN ('pending', 'approved')
     AND coalesce(d.variant, '') NOT LIKE 'follow-up-%'
  RETURNING d.id, d.status
), ins AS (
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status, approved_by, hold_reason, claim_check)
  SELECT a.id,
         d->>'channel',
         d->>'variant',
         d->>'subject',
         d->>'body',
         CASE WHEN d->>'channel' = 'email' AND d->>'status' = 'approved' AND d->>'approved_by' = 'auto'
              THEN 'approved' ELSE 'pending' END,
         CASE WHEN d->>'channel' = 'email' AND d->>'status' = 'approved' AND d->>'approved_by' = 'auto'
              THEN 'auto' END,
         nullif(d->>'hold_reason', ''),
         nullif(d->'claim_check', 'null'::jsonb)
    FROM adv a
    CROSS JOIN LATERAL jsonb_array_elements((SELECT p->'drafts' FROM payload)) AS d
  RETURNING id, lead_id, channel, variant, status, approved_by, hold_reason
)
SELECT ((SELECT p FROM payload)->>'lead_id')::bigint AS lead_id,
       (SELECT count(*) FROM adv) AS advanced,
       (SELECT count(*) FROM ins) AS drafts_written,
       (SELECT string_agg(channel || ':' || variant, ' | ' ORDER BY channel) FROM ins) AS wrote,
       (SELECT string_agg(id::text || ':' || channel || ':' || status || coalesce(':' || approved_by, ''), ' | '
                          ORDER BY id) FROM ins) AS decisions,
       (SELECT string_agg(id::text || ': ' || hold_reason, ' | ' ORDER BY id) FROM ins
         WHERE hold_reason IS NOT NULL) AS held,
       (SELECT string_agg(id::text, ',' ORDER BY id) FROM retired) AS retired_draft_ids;"""


# Section 8 owns the drafts schema. An INSERT that reaches for a column the doc
# does not define means the schema was changed without the doc being updated.
_insert_cols = re.search(r"INSERT INTO drafts \(([^)]+)\)", WRITE_SQL).group(1)
_insert_cols = [c.strip() for c in _insert_cols.split(",")]
_unknown = [c for c in _insert_cols if c not in DRAFTS_COLUMNS]
assert not _unknown, (
    "the drafts INSERT writes %r, which Section 8 of %s does not define. Update "
    "the doc first." % (_unknown, MASTER_REF)
)

assert "l.status = 'contact_found'" in BATCH_SQL, (
    "the batch query no longer filters on status='contact_found' -- Section 9 "
    "Workflow 4: 'Batch where status=contact_found'."
)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def boolean_condition(cid, expr, expect_true):
    return {
        "options": {
            "caseSensitive": True,
            "leftValue": "",
            "typeValidation": "strict",
            "version": 2,
        },
        "conditions": [
            {
                "id": cid,
                "leftValue": expr,
                "rightValue": expect_true,
                "operator": {
                    "type": "boolean",
                    "operation": "true" if expect_true else "false",
                    "singleValue": True,
                },
            }
        ],
        "combinator": "and",
    }


# The signature is a build-time constant, not a per-lead value, so it is baked
# into the Code node rather than carried through every item.
_assemble_js = js("code_assemble.js").replace(
    "__SIGNATURE__", json.dumps(SIGNATURE, ensure_ascii=False)
)
assert "__SIGNATURE__" not in _assemble_js, "signature placeholder was not substituted"

# Same mechanism for the system prompt. It has to be baked into the Code node
# and emitted per item rather than set once on the Config node: the Postgres
# batch query replaces the items wholesale, so anything Config set is gone
# downstream. Caught by running the workflow for real -- the first live run
# produced four drafts that mentioned neither Nova nor NoblePath, because the
# model was receiving an empty system prompt and simply summarising the fact
# sheet back. Nothing errored; the workflow reported success.
_assess_raw = js("code_assess.js")
for _tok in ("__SYSTEM_PROMPT__", "__CLAUDE_REQUEST__", "__SEND_CLOCK_COUNTRIES__"):
    assert _assess_raw.count(_tok) == 1, (
        "code_assess.js must carry %s exactly once, found %d -- a substituted constant that is not "
        "there is silently never substituted" % (_tok, _assess_raw.count(_tok)))
_assess_js = _assess_raw.replace(
    "__SYSTEM_PROMPT__", json.dumps(SYSTEM_PROMPT, ensure_ascii=False)
).replace(
    "__CLAUDE_REQUEST__", json.dumps(CLAUDE_REQUEST, ensure_ascii=False, indent=2).replace("\n", "\n  ")
).replace(
    "__SEND_CLOCK_COUNTRIES__", json.dumps(sorted(CLOCK_COUNTRIES), ensure_ascii=False)
)
assert "__SYSTEM_PROMPT__" not in _assess_js, "system prompt placeholder was not substituted"

# ---------------------------------------------------------------------------
# Prompt caching (Section 3, on since 2026-10-08) -- the contract, at build time
#
# Caching is a prefix match and it fails SILENTLY in both directions: a prefix
# under the model's minimum caches nothing and does not error, and a breakpoint
# placed after per-lead text writes an entry nothing will ever read. Both show
# up only as a bigger bill. So the shape is pinned here rather than trusted.
# ---------------------------------------------------------------------------

# Claude Sonnet 5.5's documented minimum cacheable prefix, from the live
# prompt-caching docs read on 2026-10-08. It is NOT monotonic across generations
# (512 here, 1,024 on Sonnet 5, 4,096 on Opus 4.6), which is why it is re-read
# from the docs rather than carried forward from an older note.
MIN_CACHEABLE_TOKENS = 512

# Characters per token, from /v1/messages/count_tokens on this project's own
# prompts (2026-10-08): drafting 5,185 ch -> 1,825 tok (2.84), claim check 3,289
# -> 1,080 (3.05), follow-up 3,389 -> 1,169 (2.90), repair 1,323 -> 448 (2.95).
# The build cannot call the API, so it uses the most pessimistic of those: a
# prompt this calls cacheable really is.
CHARS_PER_TOKEN = 3.1


def _assert_cacheable(name, prompt):
    est = len(prompt) / CHARS_PER_TOKEN
    assert est >= MIN_CACHEABLE_TOKENS, (
        "%s is marked for prompt caching but is only ~%d tokens (%d chars), under Sonnet 5.5's "
        "%d-token minimum. A prefix that short does not cache and does not error -- it would bill "
        "the write premium on every call and read nothing back."
        % (name, est, len(prompt), MIN_CACHEABLE_TOKENS))


# The drafting system prompt is the cached prefix, so it has to clear the
# minimum. It is ~1,825 tokens today; this fires if it is ever cut down.
_assert_cacheable("the drafting system prompt (code_assess.js SYSTEM_PROMPT)", SYSTEM_PROMPT)

# Exactly one breakpoint, and it is on the system prompt -- not on anything
# per-lead. Checked against the shipped JS rather than against intent: the
# request is built in code_assess.js, so that is where a stray breakpoint would
# appear.
_cache_sites = re.findall(r"cache_control", _assess_js)
assert len(_cache_sites) == 2, (
    "code_assess.js mentions cache_control %d times; expected 2 (the CACHE_CONTROL constant and "
    "the one system block that uses it). A second breakpoint in this request would sit after "
    "per-lead text and write an entry nothing reads." % len(_cache_sites))
assert re.search(r"system: \[\{ type: 'text', text: SYSTEM_PROMPT, cache_control: CACHE_CONTROL \}\]",
                 _assess_js), (
    "the drafting request's cached block is no longer exactly the system prompt. Everything "
    "per-lead must stay in `messages`, after the breakpoint -- caching is a prefix match, so a "
    "breakpoint behind the fact sheet writes a distinct entry per lead.")
assert not re.search(r"messages: \[[^\]]*cache_control", _assess_js, re.S), (
    "a message block in the drafting request is marked for caching. Everything in `messages` here "
    "is per-lead, so that entry would be written once per call and read by nothing.")

# The TTL is a measured judgement, not a default to drift: the 5-minute entry
# (1.25x to write) catches every read the real call pattern produces -- bursts of
# two runs 0.1-2 minutes apart -- while `ttl: '1h'` costs 2x to write and would
# only earn more if the observed 57-60 minute gaps between bursts landed inside
# a 60-minute window measured from the first request's start. Changing it means
# changing this line too, which is the point.
# Follow-Ups carries the same constant and n8n/sendtrack/build_workflow.py
# guards its own copy -- this build may not read that directory (the drift
# harness copies only this one).
CACHE_TTL_JS = "const CACHE_CONTROL = { type: 'ephemeral' };"
for _name, _src in (("code_assess.js", _assess_js),
                    ("code_approval.js", js("code_approval.js"))):
    assert CACHE_TTL_JS in _src, (
        "%s no longer uses the 5-minute (default) cache TTL. If the schedule or the call pattern "
        "has changed so that `ttl: '1h'` now pays, change CACHE_TTL_JS here and record the new "
        "measurement in Section 3 -- a 1-hour entry costs 2x base input to write instead of "
        "1.25x, so this is a cost decision, not a detail." % _name)

assert "__SEND_CLOCK_COUNTRIES__" not in _assess_js, "the clock-country list was not substituted"
assert "__CLAUDE_REQUEST__" not in _assess_js, "Claude request placeholder was not substituted"
assert '"model": "%s"' % DRAFT_MODEL in _assess_js, "the shipped request does not name %s" % DRAFT_MODEL

_assert_emitted_upstream(_assess_js, _claude_reads, "code_assess.js")


# ---------------------------------------------------------------------------
# Auto-approval (migration 014) -- code_approval.js, shared with Follow-Ups
#
# Approval Gate ships code_approval.js verbatim; Apply Claim Check ships its
# rules section (everything above the 'Node body' marker) followed by
# code_approval_apply.js. n8n/sendtrack/build_workflow.py embeds the same two,
# so a first touch and a follow-up are judged by the same functions.
# ---------------------------------------------------------------------------

import approval_chain  # noqa: E402  (this directory; shared with n8n/sendtrack/build_workflow.py)

APPROVAL = approval_chain.load(HERE, CLOCK_COUNTRIES)
APPROVAL_JS = APPROVAL["gate"]
APPROVAL_RULES = APPROVAL["rules"]

# The claim check is a second call to the drafting model, with its parameters
# (Section 3). A cheaper or different model here would be a different check
# than the one the operator asked for.
_check_model = re.search(r"^const CHECK_MODEL = '([a-z0-9-]+)';", APPROVAL_RULES, re.M)
_check_effort = re.search(r"^const CHECK_EFFORT = '([a-z]+)';", APPROVAL_RULES, re.M)
assert _check_model and _check_model.group(1) == DRAFT_MODEL, (
    "the claim check (code_approval.js CHECK_MODEL) is not Section 3's drafting model %s" % DRAFT_MODEL)
assert _check_effort and _check_effort.group(1) == DRAFT_EFFORT, (
    "the claim check's effort (code_approval.js CHECK_EFFORT) is not Section 3's %r" % DRAFT_EFFORT)

# The claim check caches; the repair deliberately does not (448 tokens, under
# the minimum). Both halves are asserted, so dropping either is a build error
# rather than a silent cost change.
assert re.search(r"system: \[\{ type: 'text', text: CHECK_SYSTEM_PROMPT, cache_control: CACHE_CONTROL \}\]",
                 APPROVAL_RULES), (
    "the claim check's system prompt is no longer the cached block (code_approval.js). It is "
    "1,080 tokens and identical for every draft and every repair round, so it is the one prefix "
    "worth caching in that chain.")
assert re.search(r"system: REPAIR_SYSTEM_PROMPT,", APPROVAL_RULES), (
    "the repair request's system prompt has been marked for caching. Measured 2026-10-08 it is "
    "448 tokens, UNDER Sonnet 5.5's 512-token minimum: it would cache nothing, raise no error, "
    "and bill the write premium for ever. Leave it uncached, or lengthen the prompt for a reason "
    "that is not caching.")
assert not re.search(r"\b(temperature|top_p|top_k|budget_tokens)\b", re.sub(r"//.*", "", APPROVAL_RULES)), (
    "the claim check request sets a sampling or thinking-budget parameter -- a 400 on %s (Section 3)" % DRAFT_MODEL)
# Section 6: a LinkedIn draft never auto-approves. The gate's first test.
assert re.search(r"if \(!draft \|\| draft\.channel !== 'email'\) \{\s*\n\s*reasons\.push\('linkedin", APPROVAL_RULES), (
    "code_approval.js no longer holds every non-email draft first -- Section 6: LinkedIn is reviewed and sent "
    "by hand, always")
# The Write statement can only approve what the Apply node marks approved by
# 'auto', and only an email.
assert "target.approved_by = 'auto'" in js("code_approval_apply.js")



CHECK_BODY = "={{ JSON.stringify($json.check_request) }}"
_assert_emitted_upstream(APPROVAL_JS, set(re.findall(r"\$json\.(\w+)", CHECK_BODY)) | {"needs_check"},
                         "code_approval.js (Approval Gate)")
# The repair loop's boundaries: Apply Claim Check -> [Needs Repair?] -> Claude
# Repair ($json.repair_request) -> Apply Repair -> [Repaired?] -> the Assemble
# copy, which must hand the repair state and the composition on.
_assert_emitted_upstream(js("code_approval_apply.js"), {"needs_repair", "repair_request", "repair_state"},
                         "code_approval_apply.js (Apply Claim Check)")
_assert_emitted_upstream(js("code_approval_repair.js"), {"repaired", "repair", "content", "stop_reason"},
                         "code_approval_repair.js (Apply Repair)")
_assert_emitted_upstream(_assemble_js, {"composition", "repair", "repair_fields"},
                         "code_assemble.js (Assemble Drafts)")
# What the gate reads off the item must be what both upstream nodes emit.
_assert_emitted_upstream(_assemble_js, {"approval", "payload"}, "code_assemble.js (Assemble Drafts)")
_assert_emitted_upstream(js("code_lowcontext.js"), {"payload"}, "code_lowcontext.js (Build Low-Context Drafts)")
for _f in re.findall(r"\bctx\.(\w+)", APPROVAL_RULES):
    assert re.search(r"^\s*%s:" % re.escape(_f), _assemble_js, re.M), (
        "code_approval.js reads approval.%s, but Assemble Drafts' `approval` has no %s -- it would be "
        "undefined at runtime, with no error" % (_f, _f))
# Write Drafts & Advance reads these off each payload draft; the gate or the
# apply node must set every one.
for _f in re.findall(r"d->>?'(\w+)'", WRITE_SQL):
    if _f in ("channel", "variant", "subject", "body"):
        continue
    assert re.search(r"\bd\.%s = |target\.%s = " % (_f, _f), APPROVAL_JS + js("code_approval_apply.js")), (
        "Write Drafts & Advance reads d->>'%s', which neither Approval Gate nor Apply Claim Check sets" % _f)
    assert re.search(r"\bd\.%s = " % _f, js("code_approval_repair.js")), (
        "Write Drafts & Advance reads d->>'%s', which Apply Repair does not set on a held draft" % _f)

# The same class of bug one node earlier: every `lead.X` Assess Grounding reads
# off the batch row must be a column Get Draft Batch returns. A missing one is
# `undefined` in the Code node, with no error -- a missing `library` would hold
# every lead back as "no active line", a missing `warmup_week` would read as
# week 1. Both look like data problems, not like a query that lost a column.
_lead_reads = set(re.findall(r"\blead\.(\w+)", js("code_assess.js")))
_batch_cols = (set(re.findall(r"\bAS\s+(\w+)", BATCH_SQL, re.I))
               | set(re.findall(r"\b[a-z]\.(\w+),?\s*$", BATCH_SQL, re.M)))
_unread = sorted(_lead_reads - _batch_cols)
assert not _unread, (
    "code_assess.js reads lead.%s, but Get Draft Batch returns no such column. It would be "
    "undefined at runtime with no error." % ", lead.".join(_unread)
)

# Prompt caching for the drafting call (Section 3, on since 2026-10-08). What
# the node's own notes say, and why it is one node and not two.
CACHE_NOTE = (
    "PROMPT CACHING (2026-10-08): Assess Grounding marks the system prompt with a cache_control "
    "breakpoint, so the stable part of this request -- the system prompt plus the output schema, "
    "2,224 tokens measured against the live API -- is read from cache instead of re-billed at the "
    "base input rate whenever another drafting call ran in the last 5 minutes. Everything "
    "per-lead is in the user message, after the breakpoint, which is the order a prefix match "
    "needs.\n\n"
    "This node dispatches every item's request before awaiting any (read from the installed "
    "HttpRequestV3.node.js), so a RUN's own calls are concurrent and each one writes the entry "
    "rather than reading a sibling's. The reads come from the next run inside the window; the "
    "measured call pattern is bursts of paired runs seconds apart, so most calls do read. A "
    "first/rest split that would also win the within-batch reads was built and REJECTED on "
    "evidence -- it breaks n8n's paired-item lineage, so Assemble Drafts resolved the wrong "
    "lead's facts for every item after the first, and a one-lead batch stopped the workflow "
    "dead. Section 9, Workflow 4 records the probes.\n\n"
    "Verify with the usage fields on real runs: n8n/drafting/cache_report.py."
)


def claude_draft_node(name, pos):
    """The drafting call -- one node for the whole batch."""
    return {
        "parameters": {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "anthropicApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": CLAUDE_BODY,
            "options": {"timeout": 300000},
        },
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": list(pos),
        "credentials": ANTHROPIC_CRED,
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 5000,
        "notes": (
            "Drafting skill v3: %s through the Anthropic Messages API, drafting node only "
            "(Section 3). Enrichment and scoring stay on local qwen3.5:9b.\n\n"
            "The body is $json.request, built per lead by Assess Grounding: model, max_tokens, "
            "effort %s, the JSON schema (output_config.format), the system prompt and the "
            "lead's facts plus its approved claims. No temperature/top_p: a non-default value is a "
            "400 on this model. Thinking is left at the model default (adaptive).\n\n"
            "The API key is the n8n credential %s, made from ANTHROPIC_API_KEY in .env by "
            "provision_anthropic_credential.py. It is never in this JSON.\n\n"
            "Skill section 6: if the call fails the lead stays queued and the next run retries. "
            "There is NO fallback to the local model. Errors continue as items so Assemble Drafts "
            "can drop them; it also drops a refusal, a max_tokens cut-off, or text that is not the "
            "schema.\n\n%s"
        ) % (DRAFT_MODEL, DRAFT_EFFORT, ANTHROPIC_CRED["anthropicApi"]["id"], CACHE_NOTE),
    }


# The auto-approval chain with its repair rounds, generated (approval_chain.py)
# -- the same chain Follow-Ups carries.
_chain_nodes, _chain_conns, _write_pos = approval_chain.build(
    APPROVAL, "Assemble Drafts", _assemble_js, "Write Drafts & Advance", ANTHROPIC_CRED, DRAFT_MODEL, DRAFT_EFFORT,
    (1320, 130))
CHAIN_ROUNDS = approval_chain.names(APPROVAL["max_repairs"], "Assemble Drafts")

nodes = [
    {
        "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
        "name": "Every 30 Minutes",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [-880, 40],
        "notes": "Section 9 Workflow 4: cron, batch where status='contact_found'. Queue-driven, so a missed run just means the next one catches up (build rule 4). Activation is a UI Publish action, never the CLI (Section 3).",
    },
    {
        "parameters": {},
        "name": "Manual Trigger",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-880, 220],
    },
    {
        "parameters": {
            "assignments": {
                "assignments": [
                    {"id": "batchsize", "name": "batch_size", "value": 10, "type": "number"},
                ]
            },
            "options": {},
        },
        "name": "Config",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [-660, 130],
        "notes": (
            "Bounded batch (build rule 5). 10, matching Workflow 2 rather than Workflow 3's 25: "
            "every surviving lead here costs a paid Claude call (Section 4), and Section 3 puts real "
            "drafting volume at 10-20/day anyway.\n\n"
            "The system prompt is NOT set here. It was, and it silently never reached the model: "
            "a Postgres node replaces the items, so anything assigned on this node is gone "
            "downstream. The first live run produced four drafts mentioning neither Nova nor "
            "NoblePath -- the model was handed an empty system prompt and summarised the fact "
            "sheet back. Nothing errored and the execution reported success. It is baked into "
            "Assess Grounding and emitted per item instead, the same shape Workflow 3 uses."
        ),
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": BATCH_SQL,
            "options": {"queryReplacement": "={{ [$json.batch_size] }}"},
        },
        "name": "Get Draft Batch",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [-440, 130],
        "credentials": PG_CRED,
        "notes": (
            "Oldest-first queue read (Section 7 idempotency rule). Ranking by fit_score is "
            "Workflow 5's job, not this one's -- draining the queue best-first would starve "
            "lower-scoring leads that still cleared Section 9's contact-lookup gate.\n\n"
            "Also carries, on every row, the active claims library (migration 010 -- the operator's "
            "table, read per run, never baked in) and the warm-up week the skill's link rule needs.\n\n"
            "Query parameters are passed as an array so a value containing a comma is never split."
        ),
        "alwaysOutputData": False,
    },
    {
        "parameters": {
            "method": "GET",
            "url": "https://clinicaltrials.gov/api/v2/studies",
            "sendQuery": True,
            "queryParameters": {
                "parameters": [
                    {"name": "query.spons", "value": "={{ $json.company_name }}"},
                    {"name": "filter.overallStatus", "value": "RECRUITING"},
                    {"name": "countTotal", "value": "true"},
                    {"name": "pageSize", "value": "3"},
                    {"name": "fields", "value": "NCTId,BriefTitle"},
                ]
            },
            "options": {
                "timeout": 30000,
                "batching": {"batch": {"batchSize": 1, "batchInterval": 250}},
            },
        },
        "name": "ClinicalTrials.gov Lookup",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [-220, 130],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "DELIBERATE ADDITION, not in the Section 9 Workflow 4 spec. 'Named trial' is one of "
            "the four grounding facts the guard counts, and nothing in the pipeline stores one: "
            "Workflow 3 asked for NCTId with pageSize=1 and kept only the count, in prose, inside "
            "scores.rationale. Without this call that fact category is permanently dead and the "
            "guard runs on three categories instead of four.\n\n"
            "Same call shape as Workflow 3, so the same evidence applies: query.spons verbatim is "
            "the ONLY ClinicalTrials.gov query this project trusts -- query.locn and query.term "
            "were both measured against real leads and rejected on false positives. A query.spons "
            "hit really is this company's trial, so its BriefTitle is safe to hand a drafting "
            "model. pageSize=3 instead of 1 because the titles are now grounding material, not "
            "just a count.\n\n"
            "Free, no API key (Section 4 cost model)."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": _assess_js},
        "name": "Assess Grounding",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [0, 130],
        "notes": (
            "The grounding guard (Section 9, LOCKED). Deterministic, and it runs BEFORE the model "
            "is called -- a guard that ran afterwards would be judging text the model had already "
            "invented.\n\n"
            "Counts only the four fact categories the doc names. country is excluded (every lead "
            "has one and the pipeline only ingests the 13 target geographies, so it grounds "
            "nothing) and so is site_quality_notes (model-generated prose, the highest "
            "fabrication risk field in the record -- using it would let one model's invention "
            "license another's).\n\n"
            "Also decides addressing per channel. Section 9 Workflow 3b, LOCKED: a role inbox is "
            "not a person. hello@klixar.com is not greeted as Enrique Gaubeca even though the "
            "record names him -- he is greeted by name on LinkedIn, where the URL really is his.\n\n"
            "Drafting skill v3: resolves the approved claims for this lead (proof by its country), "
            "builds the per-lead prompt and the Anthropic request, and holds back a groundable lead "
            "the library cannot complete (ok=false, stays 'contact_found') -- before any API call."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("ok", "={{ $json.ok }}", True),
            "options": {},
        },
        "name": "Drop Failed Lookups",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [220, 130],
        "notes": (
            "If ClinicalTrials.gov did not answer, the lead is dropped and stays 'contact_found' "
            "so the next run retries it. Drafting one fact short would be worse than not drafting: "
            "it can flip a lead into low-context and permanently record it as unwritable because "
            "of a transient API failure. Same self-healing shape as Workflow 3's node of the same "
            "name.\n\n"
            "Same for a groundable lead the claims library cannot complete: it waits here for the "
            "operator to fix the table rather than go out with a hole where the proof should be."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("groundable", "={{ $json.groundable }}", True),
            "options": {},
        },
        "name": "Groundable?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [440, 130],
        "notes": (
            "Section 9, Workflow 4, GROUNDING GUARD -- LOCKED. Fewer than two specific facts and "
            "the lead takes the false branch: no model call at all, a low-context row instead.\n\n"
            "The rule exists because of the Nova field-fabrication bug -- under forced tool use, "
            "Haiku invented a specialty from an email domain. Small models fabricate when "
            "under-informed, so the design assumption is that they will."
        ),
    },
    claude_draft_node("Claude Draft", (660, 20)),
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": _assemble_js},
        "name": "Assemble Drafts",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [880, 20],
        "notes": (
            "Drafting skill v3: parses Claude's composed email and DM and enforces skill section 3 "
            "in code -- body length (70-110 target, tagged above 125), exactly one ask, the link rule "
            "(none in warm-up weeks 1-2, then only the library's link line), the product name at most "
            "once and in brackets, \"AI\" at most once, never \"You're sponsoring\", no guarantee, "
            "no number the claims and facts do not hold, no visitor identification, no named CRM or "
            "tool, no languages, never that it books a call or answers from SOPs, no two claim lines "
            "that repeat a capability, the geography-matched proof, subject rules, grounding, and every "
            "claim code checked against what the lead was offered.\n\n"
            "The opt-out sentence and the signature are appended HERE, not generated. Section 5 "
            "locks the opt-out verbatim and makes it the basis of the GDPR/KVKK legitimate-"
            "interest position.\n\n"
            "A violation tags the draft's `variant` instead of discarding it. The reviewer sees "
            "the tag next to the text; a silently-dropped draft teaches nobody anything."
        ),
    },
    {
        "parameters": {
            "conditions": boolean_condition("writable", "={{ $json.write }}", True),
            "options": {},
        },
        "name": "Drop Failed Generations",
        "type": "n8n-nodes-base.filter",
        "typeVersion": 2.2,
        "position": [1100, 20],
        "notes": (
            "A failed Claude call must not mark a lead low-context either. Nothing is written, the "
            "lead stays 'contact_found', the next run redrafts it (skill section 6: no fallback)."
        ),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": js("code_lowcontext.js")},
        "name": "Build Low-Context Drafts",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [660, 260],
        "notes": (
            "'Skipped' cannot mean 'write nothing'. A lead that produces no row stays at "
            "'contact_found' and is re-picked, re-looked-up and re-skipped by every cron tick "
            "forever -- the trap Workflow 3b's tombstone exists to avoid.\n\n"
            "So the lead advances and lands in the review queue carrying a row that states which "
            "facts were missing. Section 9's 'visibility over spot-checking' decision points the "
            "same way: surface the miss to the human rather than hiding it. No model call happens "
            "on this branch -- there is nothing to write from."
        ),
    },
    *_chain_nodes,
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json.payload)] }}"},
        },
        "name": "Write Drafts & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": _write_pos,
        "credentials": PG_CRED,
        "notes": (
            "Both drafts and the status advance in one statement. The whole payload goes in as a "
            "single jsonb parameter, so unicode and embedded commas are handled by Postgres "
            "rather than by string splicing.\n\n"
            "The INSERT selects FROM the guarded UPDATE's RETURNING, so a repeated run inserts "
            "zero rows instead of duplicating drafts. drafts deliberately has no unique key on "
            "lead_id -- Section 9 wants two rows per lead, and Workflow 6's follow-ups will add "
            "more.\n\n"
            "A redraft retires what it replaces, in the same statement: a lead's earlier pending "
            "or approved first-touch drafts become rejected / 'bad draft'. An old APPROVED draft "
            "left alone would be the first thing Workflow 6 sends once the lead is back at "
            "'drafted'. No approval carries over: a new draft is 'pending', or approved by this "
            "workflow when the gate and the claim check passed it (migration 014) -- and the "
            "table's trigger re-checks that approval against the live flag and library."
        ),
    },
]

# --- Guard: the schedule cannot depend on the host being up at one moment ----
#
# Section 7's idempotency rule: "the host machine will be off some of the time.
# No workflow may assume its schedule fired." Two ways to break it, both of
# which this stage is one edit away from:
#
#   a fixed slot        `days`/1 + triggerAtHour compiles to one cron a day at
#                       that hour and nothing else. Ingestion was set that way
#                       (23:00 Asia/Karachi) and on a laptop that is off at
#                       night it mostly never fired at all.
#   a clock-value gate  n8n 2.35.7's recurrenceCheck gates an hours, days or
#                       weeks interval above 1 on the CLOCK VALUE of the last
#                       run -- (hour - lastHour + 24) % 24 >= N -- kept across
#                       restarts in staticData. Follow-Ups' "Every 6 Hours"
#                       stored hour 0 and skipped its only tick in nine days,
#                       at 00:00 PKT on 2026-10-02, with leads due.
#
# Minutes are counted on absolute elapsed minutes in that function, so a
# minutes interval is safe.
for _n in nodes:
    if _n["type"] != "n8n-nodes-base.scheduleTrigger":
        continue
    for _iv in _n["parameters"]["rule"]["interval"]:
        for _pin in ("triggerAtHour", "triggerAtDay", "triggerAtDayOfMonth", "triggerAtMinute"):
            assert _pin not in _iv, (
                "%s: schedule %r pins %s, so it has one moment to fire in. Section 7: 'No "
                "workflow may assume its schedule fired.'" % (_n["name"], _iv, _pin))
        _field = _iv.get("field")
        assert not (_field in ("hours", "days", "weeks") and int(_iv.get(_field + "Interval", 1)) > 1), (
            "%s: schedule %r enters n8n's recurrenceCheck on a clock value (hour of day, day of "
            "year), not elapsed time -- on a host that sleeps, a due tick is silently skipped "
            "(Follow-Ups, 2026-10-02). Use a minutes interval, or an interval of 1."
            % (_n["name"], _iv))


connections = {
    "Every 30 Minutes": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Manual Trigger": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
    "Config": {"main": [[{"node": "Get Draft Batch", "type": "main", "index": 0}]]},
    "Get Draft Batch": {
        "main": [[{"node": "ClinicalTrials.gov Lookup", "type": "main", "index": 0}]]
    },
    "ClinicalTrials.gov Lookup": {
        "main": [[{"node": "Assess Grounding", "type": "main", "index": 0}]]
    },
    "Assess Grounding": {"main": [[{"node": "Drop Failed Lookups", "type": "main", "index": 0}]]},
    "Drop Failed Lookups": {"main": [[{"node": "Groundable?", "type": "main", "index": 0}]]},
    "Groundable?": {
        "main": [
            [{"node": "Claude Draft", "type": "main", "index": 0}],
            [{"node": "Build Low-Context Drafts", "type": "main", "index": 0}],
        ]
    },
    "Claude Draft": {"main": [[{"node": "Assemble Drafts", "type": "main", "index": 0}]]},
    "Assemble Drafts": {
        "main": [[{"node": "Drop Failed Generations", "type": "main", "index": 0}]]
    },
    "Drop Failed Generations": {
        "main": [[{"node": "Approval Gate", "type": "main", "index": 0}]]
    },
    "Build Low-Context Drafts": {
        "main": [[{"node": "Approval Gate", "type": "main", "index": 0}]]
    },
    **_chain_conns,
}

# Nothing reaches the write without passing the gate: both of the gate's exits
# end at Write Drafts & Advance, and nothing else does.
_into_write = sorted(src for src, outs in connections.items()
                     for branch in outs["main"] for e in branch if e["node"] == "Write Drafts & Advance")
assert _into_write == approval_chain.exits(CHAIN_ROUNDS), (
    "something reaches Write Drafts & Advance without the Approval Gate: %r" % _into_write)
assert len(CHAIN_ROUNDS) == APPROVAL["max_repairs"] + 1, "the chain does not unroll MAX_REPAIRS repair rounds"

# Every $('Node') a Code node reads must exist in this workflow -- n8n stops the
# execution there. The chain's copies read their round's own nodes.
_names = {n["name"] for n in nodes}
for _n in nodes:
    if _n["type"] == "n8n-nodes-base.code":
        for _ref in re.findall(r"\$\('([^']+)'\)", _n["parameters"]["jsCode"]):
            assert _ref in _names, "%s reads $('%s'), but there is no node named %r" % (_n["name"], _ref, _ref)

workflow = {
    "id": "drafting0001",
    "name": "Drafting",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(workflow, fh, ensure_ascii=False, indent=2)
    fh.write("\n")

print("wrote", os.path.abspath(OUT))
print("  min facts: %d, fact kinds: %r" % (MIN_FACTS, FACT_KINDS))
print("  body: %d-%d words, never more than %d; url cap parsed; link-free weeks 1-%d" % (LENGTH + (LINK_FREE_WEEKS,)))
print("  drafting model: %s, effort %s, credential %s" % (DRAFT_MODEL, DRAFT_EFFORT, ANTHROPIC_CRED["anthropicApi"]["id"]))
print("  opt-out: %r" % OPT_OUT)
print("  signature: %r (from %s)" % (SIGNATURE, SENDER_SOURCE))
print("  skill: subject %d-%d chars, no %r; name at most %d, AI at most %d; banned %r"
      % (SUBJECT_RULES["min"], SUBJECT_RULES["max"], SUBJECT_RULES["prefixes"], NAME_MAX, AI_MAX, SKILL_BANNED))
print("  small team: %d-%d employees; claims library countries = Section 12's %d geographies"
      % (SMALL_TEAM + (len(GEOGRAPHIES),)))

_gaps = []
if not SENDER_TITLE:
    _gaps.append("title")
if not SENDER_PHONE:
    _gaps.append("phone")
if _gaps:
    print(
        "\n  WARNING: Section 5 wants a signature of name / one line of title / phone.\n"
        "  Missing: %s. Nothing was invented to fill the gap -- drafts ship with\n"
        "  the signature %r. Set NOVASCOUT_SENDER_TITLE / NOVASCOUT_SENDER_PHONE\n"
        "  in .env and rebuild." % (", ".join(_gaps), SIGNATURE)
    )

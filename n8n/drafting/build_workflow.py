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
    Section 9  word ceiling             -> "under 80 words"
    Section 5  opt-out sentence         -> VERBATIM, it is the GDPR/KVKK basis
    Section 3  drafting inference params-> temperature / presence_penalty
    Section 8  drafts table columns     -> what the INSERT is allowed to touch
    Section 12 company size band        -> what a "small" headcount is

Drafting skill v2 (NovaScout_DraftingSkill.md) is parsed the same way:

    section 2  "Only the hook and subject are freely generated" -> the model's
               output schema; nothing else can be composed
    section 2  body word budget         -> must agree with the Master Ref
    section 3  subject 30-50 characters, no Re:/Fwd:, banned subject words
    section 3  "Warm-up weeks 1-2: zero links"

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

# There is no demo-URL setting any more. v1 let the model write one URL, the
# recording's. Skill v2 moves the recording, the PDF and the LinkedIn profile to
# the reply payload (skill section 8), and the one URL a first touch may carry
# after warm-up is a line in the claims library (slot `link`), not a build
# constant.

SIGNATURE = "\n".join([p for p in [SENDER_NAME, SENDER_TITLE, SENDER_PHONE] if p])


# ---------------------------------------------------------------------------
# Spec parsers
# ---------------------------------------------------------------------------

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def load_min_facts(doc=None):
    """Parse the grounding threshold out of Section 9, Workflow 4."""
    doc = doc if doc is not None else _doc()
    m = re.search(r"lacks at least (\w+) specific facts", doc)
    if not m:
        raise AssertionError(
            "grounding threshold not found in %s -- expected 'lacks at least N "
            "specific facts'" % MASTER_REF
        )
    word = m.group(1).lower()
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word]
    if word.isdigit():
        return int(word)
    raise AssertionError("grounding threshold %r in %s is not a number" % (word, MASTER_REF))


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


def load_max_words(doc=None):
    """Parse the word ceiling. Stated twice (Section 5 and Section 9) -- both
    must agree, or the spec itself is ambiguous and the build should stop."""
    doc = doc if doc is not None else _doc()
    found = set(int(n) for n in re.findall(r"[Uu]nder (\d+) words", doc))
    if not found:
        raise AssertionError("word ceiling not found in %s -- expected 'under N words'" % MASTER_REF)
    if len(found) > 1:
        raise AssertionError(
            "%s states more than one word ceiling: %r -- resolve the doc first"
            % (MASTER_REF, sorted(found))
        )
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


def load_drafting_inference(doc=None):
    """Parse Section 3's drafting temperature / presence_penalty.

    Section 3 marks these UNVERIFIED and says to test them rather than assume
    the extraction settings transfer. Parsing them means the workflow always
    ships whatever the doc currently says -- so when they are tested and
    changed, the change lands here by rebuilding, not by remembering.
    """
    doc = doc if doc is not None else _doc()
    m = re.search(
        r"For drafting, `temperature: ([\d.]+)`, `presence_penalty: ([\d.]+)`", doc
    )
    if not m:
        raise AssertionError(
            "drafting inference settings not found in %s -- expected 'For drafting, "
            "`temperature: X`, `presence_penalty: Y`'" % MASTER_REF
        )
    return float(m.group(1)), float(m.group(2))


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
    'small' enough for the skill's headcount subject line."""
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


def load_generated_parts(skill=None):
    """Skill section 2: "Only the hook and subject are freely generated".

    This sentence is the whole v2 design -- everything else is selected from the
    library -- so it decides the model's output schema. If the skill ever lets
    the model write another part, the schema here is wrong until someone decides
    how, and the build stops rather than quietly keep the old split.
    """
    skill = skill if skill is not None else _skill()
    m = re.search(r"\*\*Only the ([a-z ,]+?) (?:is|are) freely generated\*\*", skill)
    if not m:
        raise AssertionError(
            "the generated-parts rule not found in %s -- expected '**Only the hook and "
            "subject are freely generated**'" % SKILL
        )
    return sorted(p.strip() for p in re.split(r",| and ", m.group(1)) if p.strip())


def load_skill_word_budget(skill=None):
    """Skill section 2: "Body (1-5) stays under N words". It says it is the
    Master Ref's number; the build checks that it is."""
    skill = skill if skill is not None else _skill()
    m = re.search(r"Body \(1[–-]5\) stays \*\*under (\d+) words\*\*", skill)
    if not m:
        raise AssertionError("body word budget not found in %s -- expected 'Body (1-5) stays "
                             "**under N words**'" % SKILL)
    return int(m.group(1))


def _skill_subject_block(skill):
    m = re.search(r"\n\*\*Subject\*\*\n(.*?)\n\n", skill, re.S)
    if not m:
        raise AssertionError("the **Subject** rules block not found in section 3 of %s" % SKILL)
    return m.group(1)


def load_subject_rules(skill=None):
    """Skill section 3, Subject: the character range, the fake-reply prefixes
    and the banned words, all parsed rather than retyped."""
    skill = skill if skill is not None else _skill()
    block = _skill_subject_block(skill)
    chars = re.search(r"^- (\d+)[–-](\d+) characters\.", block, re.M)
    if not chars:
        raise AssertionError("subject length not found in %s -- expected '- N-M characters.'" % SKILL)
    prefixes = re.search(r'No "(\w+):" or "(\w+):"', block)
    if not prefixes:
        raise AssertionError('subject reply prefixes not found in %s -- expected \'No "Re:" or '
                             '"Fwd:"\'' % SKILL)
    banned = re.search(r'No ((?:"\w+[,.]?"[ ]*){2,})', block)
    if not banned:
        raise AssertionError('banned subject words not found in %s -- expected \'No "free," '
                             '"demo," ...\'' % SKILL)
    return {
        "min": int(chars.group(1)),
        "max": int(chars.group(2)),
        "prefixes": [p.lower() for p in prefixes.groups()],
        "banned": [w.lower() for w in re.findall(r'"(\w+)[,.]?"', banned.group(1))],
    }


def load_link_free_weeks(skill=None):
    """Skill section 3, Links: "Warm-up weeks 1-N: zero links." """
    skill = skill if skill is not None else _skill()
    m = re.search(r"Warm-up weeks 1[–-](\d+): zero links", skill)
    if not m:
        raise AssertionError("link-free warm-up weeks not found in %s -- expected 'Warm-up "
                             "weeks 1-N: zero links'" % SKILL)
    return int(m.group(1))


def load_claims_countries():
    """The geography list migration 010's countries CHECK allows."""
    with io.open(CLAIMS_MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    m = re.search(r"claims_countries_are_geographies CHECK \(.*?ARRAY\[(.*?)\]", sql, re.S)
    if not m:
        raise AssertionError("claims_countries_are_geographies not found in %s" % CLAIMS_MIGRATION)
    return re.findall(r"'([^']+)'", m.group(1))


DOC = _doc()
SKILL_DOC = _skill()
MIN_FACTS = load_min_facts(DOC)
FACT_KINDS = load_fact_kinds(DOC)
MAX_WORDS = load_max_words(DOC)
OPT_OUT = load_opt_out(DOC)
BANNED_NAMED = load_banned_adjectives(DOC)
THERAPEUTIC_AREAS = load_therapeutic_areas(DOC)
DRAFT_TEMPERATURE, DRAFT_PRESENCE_PENALTY = load_drafting_inference(DOC)
DRAFTS_COLUMNS = load_drafts_columns(DOC)
SMALL_TEAM = load_small_team(DOC)
GEOGRAPHIES = load_geographies(DOC)
GENERATED_PARTS = load_generated_parts(SKILL_DOC)
SUBJECT_RULES = load_subject_rules(SKILL_DOC)
LINK_FREE_WEEKS = load_link_free_weeks(SKILL_DOC)

_skill_budget = load_skill_word_budget(SKILL_DOC)
assert _skill_budget == MAX_WORDS, (
    "the drafting skill and the Master Ref disagree on the word ceiling:\n"
    "  %s: body under %d words\n  %s: under %d words" % (SKILL, _skill_budget, MASTER_REF, MAX_WORDS)
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

    found_words = _js_int(src, "MAX_WORDS", "code_assess.js")
    assert found_words == MAX_WORDS, (
        "word ceiling drifted between the Master Ref and code_assess.js:\n"
        "  doc: under %d words\n  js:  MAX_WORDS = %d" % (MAX_WORDS, found_words)
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


def _assert_assemble_matches_spec():
    src = js("code_assemble.js")

    m = re.search(r'const OPT_OUT = "(.*?)";', src)
    assert m, "OPT_OUT not found in code_assemble.js"
    assert m.group(1) == OPT_OUT, (
        "the opt-out sentence drifted from Section 5. It is the GDPR/KVKK opt-out "
        "mechanism and must match VERBATIM:\n  doc: %r\n  js:  %r" % (OPT_OUT, m.group(1))
    )

    found_words = _js_int(src, "MAX_WORDS", "code_assemble.js")
    assert found_words == MAX_WORDS, (
        "word ceiling drifted between the Master Ref and code_assemble.js:\n"
        "  doc: under %d words\n  js:  MAX_WORDS = %d" % (MAX_WORDS, found_words)
    )

    banned = [a.lower() for a in _js_string_array(src, "BANNED_ADJECTIVES", "code_assemble.js")]
    missing = [a for a in BANNED_NAMED if a not in banned]
    assert not missing, (
        "Section 9 names adjectives that code_assemble.js does not ban: %r\n"
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

    # Skill section 3. Each is parsed from the skill and compared, so a change
    # to the skill reaches the checks by rebuilding, never by remembering.
    found = _js_int(src, "LINK_FREE_WEEKS", "code_assemble.js")
    assert found == LINK_FREE_WEEKS, (
        "the link-free warm-up drifted between the drafting skill and code_assemble.js:\n"
        "  skill: weeks 1-%d zero links\n  js:    LINK_FREE_WEEKS = %d" % (LINK_FREE_WEEKS, found)
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
    found = _js_string_array(src, "SUBJECT_BANNED_WORDS", "code_assemble.js")
    missing = [w for w in SUBJECT_RULES["banned"] if w not in found]
    assert not missing, (
        "the drafting skill bans subject words that code_assemble.js does not: %r\n"
        "  js bans: %r" % (missing, found)
    )


_assert_assess_matches_spec()
_assert_assemble_matches_spec()


# ---------------------------------------------------------------------------
# Ollama drafting call
# ---------------------------------------------------------------------------

# Skill v2: the model writes a subject and two hooks. Nothing else. The problem,
# outcome, proof and ask are library lines Assemble Drafts inserts -- the model
# has no field to write them into, so it cannot compose them.
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "email_subject": {"type": "string"},
        "email_hook": {"type": "string"},
        "linkedin_hook": {"type": "string"},
    },
    "required": ["email_subject", "email_hook", "linkedin_hook"],
}

# Every schema field must be one of the parts the skill says are generated, and
# every generated part must have a field. A `body`, `problem` or `ask` field
# here would be the model composing what the library owns.
_schema_parts = sorted(set(k.split("_", 1)[1] for k in DRAFT_SCHEMA["properties"]))
assert _schema_parts == GENERATED_PARTS, (
    "the model's output schema does not match the drafting skill:\n"
    "  skill: only the %s are freely generated\n  schema fields: %r\n"
    "Everything else is selected from the claims library, never composed."
    % (" and ".join(GENERATED_PARTS), sorted(DRAFT_SCHEMA["properties"]))
)

# Pretty-printed on purpose: a compact json.dumps emits runs like `"string"}}`,
# and that `}}` closes the surrounding n8n {{ }} expression early -- the node
# then fails with a bare "invalid syntax". Indenting puts every closing brace on
# its own line so `}}` never occurs. Same trap as Workflow 3.
_schema_js = json.dumps(DRAFT_SCHEMA, indent=2).replace("\n", "\n  ")

OLLAMA_BODY = (
    "={{ JSON.stringify({\n"
    "  model: 'qwen3.5:9b',\n"
    "  system: $json.system_prompt,\n"
    "  prompt: $json.prompt,\n"
    "  stream: false,\n"
    "  think: false,\n"
    "  format: " + _schema_js + ",\n"
    "  options: { temperature: " + repr(DRAFT_TEMPERATURE) +
    ", presence_penalty: " + repr(DRAFT_PRESENCE_PENALTY) +
    ", num_ctx: 16384, num_predict: 500 }\n"
    "}) }}"
)

assert OLLAMA_BODY.count("}}") == 1 and OLLAMA_BODY.endswith("}}"), \
    "schema JSON reintroduced a `}}` that would truncate the n8n expression"
assert "{{" not in OLLAMA_BODY[2:], "unexpected `{{` inside the expression body"

# Every `$json.X` the Ollama body reads must be a field the immediately upstream
# Code node actually emits.
#
# This guard exists because of a real, silent failure. The system prompt was
# originally assigned on the Config node, and `$json.system_prompt` resolved to
# nothing by the time it reached Ollama -- a Postgres node replaces the items,
# so Config's assignments do not survive it. n8n does not error on a missing
# expression field; it sends an empty string. The model got no instructions,
# summarised the fact sheet back, and the execution reported success with four
# drafts in the database that mentioned neither the product nor the case study.
#
# A missing field here is unobservable at runtime, so it has to be caught at
# build time.
_ollama_reads = set(re.findall(r"\$json\.(\w+)", OLLAMA_BODY))
assert _ollama_reads, "the Ollama body reads no $json fields -- did the expression change shape?"


def _assert_emitted_upstream(js_src, fields, node_name):
    # An object key at the start of a line -- `    prompt: prompt,` -- never the
    # same word followed by a colon in prose. A comment reading "the v2 prompt:
    # ..." once satisfied a looser pattern and let this guard pass with the field
    # deleted; test_drift_guards.py caught it.
    missing = [f for f in sorted(fields)
               if not re.search(r"^[ \t]*%s:" % re.escape(f), js_src, re.M)]
    assert not missing, (
        "the Ollama node reads $json.%s, but %s does not emit %r. A field the "
        "upstream node does not set resolves to an empty string at runtime with "
        "no error." % ("/$json.".join(sorted(fields)), node_name, missing)
    )


# ---------------------------------------------------------------------------
# System prompt -- drafting skill v2
#
# The model writes three short things: a subject and two hooks. It is not told
# what Nova is, who the customers are, or what the offer is, because it writes
# none of the sentences that say so -- those are claims-library lines inserted
# after the call. A model cannot misstate a claim it was never given.
#
# The subject gets worked examples rather than only rules: it is generated, not
# selected, and a 9B model follows an example more reliably than a rule list.
# The examples carry other companies' facts (INM004, 40, oncology); Assemble
# Drafts tags any of them that lands on the wrong lead.
# ---------------------------------------------------------------------------

_subject_banned = ", ".join(SUBJECT_RULES["banned"][:-1]) + " or " + SUBJECT_RULES["banned"][-1]
_subject_prefixes = " or ".join('"%s:"' % p.capitalize() for p in SUBJECT_RULES["prefixes"])

SYSTEM_PROMPT = "\n".join([
    "You write three short pieces of one cold email to a contract research",
    "organisation (CRO), sent by " + SENDER_NAME + ": the subject line, the email's first",
    "sentence (the hook), and the first sentence of a LinkedIn DM (also a hook).",
    "Nothing else. Everything after the hook -- a question about lost sponsor leads,",
    "what we built, where it runs, and the one request -- is written by a person and",
    "added afterwards, word for word. Do not write any of it.",
    "",
    "THE ONE RULE THAT MATTERS MOST: one sentence, one fact.",
    "",
    "Each hook is ONE sentence built from ONE numbered fact -- the fact you are told",
    "to open that message with -- and nothing else. Never build a sentence out of",
    "two facts. If fact 2 gives you a trial and fact 3 gives you a city, \"your trial",
    "in that city\" is a claim neither fact makes and you must not write it. The same",
    "goes for a person and a trial, an area and a trial, a person and a city. Each",
    "numbered fact ends with a sentence telling you what it does not establish. Obey",
    "it literally.",
    "",
    "Everything not in the numbered facts does not exist. No headcount, no client,",
    "no growth, no plans, no praise of their website, no trial you were not given.",
    "",
    "A HOOK IS ABOUT THEM, NOT ABOUT THE SOURCE.",
    "- Write to them as \"you\": \"You're sponsoring ...\", \"Your site lists ...\".",
    "  Never begin with \"ClinicalTrials.gov\" or \"According to\" -- the sentence is",
    "  about them, not about where the fact came from.",
    "  Never write the company's name -- not once. You will spell it wrong and it",
    "  will read like a mail merge.",
    "- Name ONE trial at most. Given two or three titles, pick one: \"You're",
    "  sponsoring two recruiting trials, including <one title>.\"",
    "- A list of therapeutic areas is what their website SAYS THEY WORK IN, not a",
    "  list of trials. \"Your site lists oncology and respiratory work.\" Never \"you",
    "  run trials in oncology\".",
    "- State the fact and stop. Never add a consequence, a guess or a judgement",
    "  (\"so sponsors ...\", \"which means ...\", \"you must be busy\"). The question",
    "  that follows is added for you.",
    "- No question mark. No request, no offer, no call, no reply, no demo -- the one",
    "  request is added for you.",
    "- Never mention Nova, AI, assistants, chatbots or anything we sell.",
    "- Never start with a person's name or a city.",
    "- A trial is on ClinicalTrials.gov, not on their site: never write \"your site",
    "  lists\" about a trial. Their site is where the therapeutic areas come from.",
    "- When both hooks open from the same fact, the LinkedIn hook may say it in the",
    "  same words. Never change what a fact says to make the two hooks differ.",
    "",
    "Hook examples. Copy the shape; the facts must come from your own numbered list.",
    "  fact: a recruiting trial they sponsor, \"Efficacy of INM004 in Children With STEC-HUS\"",
    "  hook: You're sponsoring a recruiting trial — Efficacy of INM004 in Children With STEC-HUS.",
    "  fact: their site lists Dermatology and Rheumatology",
    "  hook: Your site lists dermatology and rheumatology work.",
    "",
    "THE SUBJECT LINE: one line, sentence case, %d to %d characters."
    % (SUBJECT_RULES["min"], SUBJECT_RULES["max"]),
    "- Build it from what the message below tells you to. Only facts from the",
    "  numbered list -- or the headcount, when you are given one -- may appear in it.",
    "- A trial goes in a subject by its short name: the drug or study code if the",
    "  title has one (like INM004), otherwise two or three words of the title.",
    "  Never the whole title -- it will not fit.",
    "- Never the product name: the word Nova never appears in a subject.",
    "- Never " + _subject_prefixes + ". No capital-letter shouting. Never the words",
    "  " + _subject_banned + ".",
    "",
    "Subject worked examples. The left side says what the subject was built from,",
    "the right side is the subject. Copy the shape. Use a fact from an example only",
    "if the same fact is in your own numbered list.",
    "  named trial fact (INM004)            ->  INM004 trial — a quick question",
    "  small confirmed employee count (40)  ->  A question for a 40-person team",
    "  oncology focus only                  ->  Your oncology work — one question",
    "  only geography known                 ->  Pharma-team inquiries after hours",
    "",
    "FORMAT:",
    "- Plain text. No links of any kind, no bullets, no brackets, no placeholders,",
    "  no merge tags.",
    "- Write NO greeting and NO sign-off. Do not begin with \"Hi\" or \"Hello\" or a",
    "  name. The greeting and signature are added for you afterwards.",
    '- Banned words: revolutionary, cutting-edge, innovative, world-class,',
    '  seamless, game-changing, leverage, unlock, streamline, empower, transform.',
    '  Banned openings: "I hope this finds you well", "I came across", "I noticed".',
    "- No flattery, no exclamation marks, no sales voice.",
    "",
    "Return JSON with exactly these keys: email_subject, email_hook, linkedin_hook.",
])

# The four worked examples are the user's (2026-09-19) and are asserted present
# rather than trusted: a later prompt edit that drops one removes the anchoring
# the subject line was given, with nothing else to show for it.
#
# The last one was reworded 2026-09-21 from "Sponsor leads after hours" to say
# "pharma team", the word the claims library uses for the pharma-side party
# (Master Ref Section 9, Workflow 4, Terminology). It is the example a lead whose
# subject source is `problem` is steered to, so it decides a real subject line.
# It is also 33 characters, inside the 30-50 the prompt teaches; the old one was 25.
for _example in ("INM004 trial — a quick question", "A question for a 40-person team",
                 "Your oncology work — one question", "Pharma-team inquiries after hours"):
    assert _example in SYSTEM_PROMPT, "subject worked example %r missing from the system prompt" % _example


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
-- library: the ACTIVE rows of the claims library (migration 010), on every row.
-- Read here, per run, so an operator's edit reaches the next draft without a
-- rebuild -- and read in this query rather than a node of its own, because a
-- Postgres node replaces the items, and a field set upstream of this one would
-- be gone by Assess Grounding (Workflow 4's silent-prompt bug).
--
-- warmup_week: the same derivation as Workflow 6's Load Send State -- week 1
-- starts on the sender-day (Asia/Karachi) of the mailbox's first external send,
-- counting the Sent-folder mirror and unconfirmed claims. NULL = none yet, i.e.
-- the first send will be week 1. It decides the skill's link rule.
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
       (SELECT coalesce(jsonb_agg(jsonb_build_object(
                  'code', k.code, 'slot', k.slot, 'body', btrim(k.body),
                  'countries', to_jsonb(k.countries), 'measured', k.measured,
                  'confirmed', k.confirmed) ORDER BY k.code), '[]'::jsonb)
          FROM claims_library k
         WHERE k.active)                  AS library,
       (SELECT (((now() AT TIME ZONE 'Asia/Karachi')::date
                 - (min(counted.at) AT TIME ZONE 'Asia/Karachi')::date) / 7) + 1
          FROM counted)                   AS warmup_week
  FROM leads l
  JOIN scores      s ON s.lead_id = l.id
  JOIN contacts    c ON c.lead_id = l.id
  JOIN enrichments e ON e.lead_id = l.id
 WHERE l.status = 'contact_found'
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
-- what Workflow 6 sends from, and it sends the oldest approved draft. The new
-- drafts are always 'pending' -- no approval carries over to copy nobody has
-- read. 'sent' drafts and follow-ups are never touched, and the body and any
-- human edit (edited_body) of a retired draft are kept. All CTEs share one
-- snapshot, so `retired` cannot see, and never retires, the rows `ins` adds.
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
  INSERT INTO drafts (lead_id, channel, variant, subject, body, status)
  SELECT a.id,
         d->>'channel',
         d->>'variant',
         d->>'subject',
         d->>'body',
         'pending'
    FROM adv a
    CROSS JOIN LATERAL jsonb_array_elements((SELECT p->'drafts' FROM payload)) AS d
  RETURNING id, lead_id, channel, variant
)
SELECT ((SELECT p FROM payload)->>'lead_id')::bigint AS lead_id,
       (SELECT count(*) FROM adv) AS advanced,
       (SELECT count(*) FROM ins) AS drafts_written,
       (SELECT string_agg(channel || ':' || variant, ' | ' ORDER BY channel) FROM ins) AS wrote,
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
_assess_js = js("code_assess.js").replace(
    "__SYSTEM_PROMPT__", json.dumps(SYSTEM_PROMPT, ensure_ascii=False)
)
assert "__SYSTEM_PROMPT__" not in _assess_js, "system prompt placeholder was not substituted"

_assert_emitted_upstream(_assess_js, _ollama_reads, "code_assess.js")

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
            "every surviving lead here costs a GPU call, and Section 3 puts real drafting volume "
            "at 10-20/day anyway.\n\n"
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
            "lower-scoring leads that still cleared the >=60 gate.\n\n"
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
            "Drafting skill v2: resolves the claims-library lines for this lead (proof by its "
            "country), decides what the subject is built from, and holds back a groundable lead "
            "the library cannot complete (ok=false, stays 'contact_found') -- before any GPU time."
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
    {
        "parameters": {
            "method": "POST",
            "url": "http://host.docker.internal:11434/api/generate",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": OLLAMA_BODY,
            "options": {
                "timeout": 300000,
                "batching": {"batch": {"batchSize": 1, "batchInterval": 0}},
            },
        },
        "name": "Ollama Draft",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [660, 20],
        "onError": "continueRegularOutput",
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 2000,
        "notes": (
            "/api/generate, NOT /api/chat -- on /api/chat, think:false silently disables `format` "
            "grammar enforcement and the model answers in prose (Section 3).\n\n"
            "temperature %s / presence_penalty %s come from Section 3's drafting line, parsed out "
            "of the doc at build time. Section 3 marks them UNVERIFIED and says to test them "
            "rather than assume the extraction settings transfer -- parsing them means a retest "
            "that changes the doc lands here on the next rebuild.\n\n"
            "Drafting skill v2: the model returns a subject and two hooks -- the schema has no "
            "other field, and the build refuses a schema that disagrees with the skill's 'only the "
            "hook and subject are freely generated'. One call for both channels: two would double "
            "the serial GPU time for hooks that must cite the same fact sheet.\n\n"
            "batchSize 1 keeps calls serial to match OLLAMA_NUM_PARALLEL=1."
        ) % (repr(DRAFT_TEMPERATURE), repr(DRAFT_PRESENCE_PENALTY)),
    },
    {
        "parameters": {"mode": "runOnceForEachItem", "jsCode": _assemble_js},
        "name": "Assemble Drafts",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [880, 20],
        "notes": (
            "Drafting skill v2: builds each message as hook + problem, outcome + proof, ask -- the "
            "last four VERBATIM from the claims library, rotated on lead_id and fitted to the word "
            "ceiling. Exactly one ask. Then enforces every rule a string match can: word ceiling, "
            "banned adjectives, merge-tag tells, the link rule (none in warm-up weeks 1-2, then only "
            "the library's link line; never LinkedIn or a PDF), subject length and wording, hook "
            "grounding (numbers and therapeutic areas the lead does not have -- what a copied worked "
            "example looks like), and unconfirmed library lines.\n\n"
            "The opt-out sentence and the signature are appended HERE, not generated. Section 5 "
            "locks the opt-out verbatim and makes it the basis of the GDPR/KVKK legitimate-"
            "interest position -- asking a 9B model to reproduce a compliance string exactly, "
            "every time, is a bet with no upside (build rule 3).\n\n"
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
            "An Ollama outage must not mark a lead low-context either. Nothing is written, the "
            "lead stays 'contact_found', the next run redrafts it."
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
    {
        "parameters": {
            "operation": "executeQuery",
            "query": WRITE_SQL,
            "options": {"queryReplacement": "={{ [JSON.stringify($json.payload)] }}"},
        },
        "name": "Write Drafts & Advance",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": [1320, 130],
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
            "'drafted'. New drafts are always 'pending' -- no approval carries over."
        ),
    },
]

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
            [{"node": "Ollama Draft", "type": "main", "index": 0}],
            [{"node": "Build Low-Context Drafts", "type": "main", "index": 0}],
        ]
    },
    "Ollama Draft": {"main": [[{"node": "Assemble Drafts", "type": "main", "index": 0}]]},
    "Assemble Drafts": {
        "main": [[{"node": "Drop Failed Generations", "type": "main", "index": 0}]]
    },
    "Drop Failed Generations": {
        "main": [[{"node": "Write Drafts & Advance", "type": "main", "index": 0}]]
    },
    "Build Low-Context Drafts": {
        "main": [[{"node": "Write Drafts & Advance", "type": "main", "index": 0}]]
    },
}

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
print("  max words: %d, url cap parsed, banned adjectives named in doc: %r" % (MAX_WORDS, BANNED_NAMED))
print("  drafting inference: temperature=%s presence_penalty=%s" % (DRAFT_TEMPERATURE, DRAFT_PRESENCE_PENALTY))
print("  opt-out: %r" % OPT_OUT)
print("  signature: %r (from %s)" % (SIGNATURE, SENDER_SOURCE))
print("  skill: generated parts %r, subject %d-%d chars, no %r, banned %r, link-free weeks 1-%d"
      % (GENERATED_PARTS, SUBJECT_RULES["min"], SUBJECT_RULES["max"], SUBJECT_RULES["prefixes"],
         SUBJECT_RULES["banned"], LINK_FREE_WEEKS))
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

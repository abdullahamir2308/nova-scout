"""The fixed outreach templates, as the Master Ref states them and as code_template.js carries them.

Imported by n8n/drafting/build_workflow.py (first touches) and
n8n/sendtrack/build_workflow.py (follow-ups), the way both already import this
directory's code. The wording is the operator's (approved 2026-10-19) and
lives in two places on purpose: Section 9 of the Master Ref, where a person
reads and changes it, and the Code node, where it runs. These functions render
the Code node's constants back into the doc's own block format, so a build can
refuse the moment the two disagree -- by so much as an apostrophe.

When a guard here fires, fix the JS to match the doc, never the other way round.
"""
import json
import re

# The fenced blocks, each found by the bold heading that precedes it.
FIRST_TOUCH_HEADING = "**First-touch template"
SMYKM_HEADING = "**SMYKM line"
FOLLOW_UP_HEADING = "**Follow-up templates"


def js_constants(src):
    """Every `const NAME = "...";` and `const NAME = ["...", ...];` string constant."""
    out = {}
    for m in re.finditer(r'^const ([A-Z0-9_]+) = ("(?:[^"\\]|\\.)*");$', src, re.M):
        out[m.group(1)] = json.loads(m.group(2))
    for m in re.finditer(r'^const ([A-Z0-9_]+) = (\[(?:\s*"(?:[^"\\]|\\.)*",?)+\s*\]);$', src, re.M):
        out[m.group(1)] = json.loads(m.group(2))
    return out


def js_rules(src):
    """The parsed selection rules: ARM_BY_PARITY, SMYKM_ORDER, MAX_AREAS."""
    arm = re.search(r"^const ARM_BY_PARITY = \{ even: '([AB])', odd: '([AB])' \};$", src, re.M)
    order = re.search(r"^const SMYKM_ORDER = \[([^\]]*)\];$", src, re.M)
    areas = re.search(r"^const MAX_AREAS = (\d+);$", src, re.M)
    if not (arm and order and areas):
        raise AssertionError("code_template.js no longer declares ARM_BY_PARITY, SMYKM_ORDER and MAX_AREAS in the "
                             "one shape the build reads")
    return {"even": arm.group(1), "odd": arm.group(2),
            "order": re.findall(r"'([a-z]+)'", order.group(1)), "max_areas": int(areas.group(1))}


REQUIRED = ["SUBJECT_WITH_FACT", "SUBJECT_NO_FACT", "SUBJECT_FACT_TRIALS", "SUBJECT_FACT_AREAS", "SUBJECT_FACT_CITY",
            "GREETING_NAMED", "GREETING_UNNAMED", "INTRO_LINE", "SMYKM_TRIALS_A", "SMYKM_TRIALS_B", "SMYKM_AREAS_A",
            "SMYKM_AREAS_B", "SMYKM_CITY", "PITCH_LINE", "CLOSE_A", "CLOSE_B", "SIGN_OFF", "FOLLOW_UP_1_LINE",
            "FOLLOW_UP_2_LINE"]


def constants(src, where):
    c = js_constants(src)
    missing = [k for k in REQUIRED if k not in c]
    if missing:
        raise AssertionError("%s no longer declares the template constant(s) %r as a plain double-quoted string" %
                             (where, missing))
    return c


def first_touch_block(c):
    return "\n".join([
        "Subject: " + c["SUBJECT_WITH_FACT"],
        "",
        c["GREETING_NAMED"],
        "",
        c["INTRO_LINE"],
        "",
        "{SMYKM line}",
        "",
        c["PITCH_LINE"],
        "",
        "[A] " + c["CLOSE_A"],
        "[B] " + c["CLOSE_B"],
        "",
    ] + c["SIGN_OFF"])


def smykm_block(c):
    return "\n".join([
        "trials A: " + c["SMYKM_TRIALS_A"],
        "trials B: " + c["SMYKM_TRIALS_B"],
        "trials subject: " + c["SUBJECT_FACT_TRIALS"],
        "areas A: " + c["SMYKM_AREAS_A"],
        "areas B: " + c["SMYKM_AREAS_B"],
        "areas subject: " + c["SUBJECT_FACT_AREAS"],
        "city: " + c["SMYKM_CITY"],
        "city subject: " + c["SUBJECT_FACT_CITY"],
        "none subject: " + c["SUBJECT_NO_FACT"],
    ])


def follow_up_block(c):
    return "\n".join([
        "Follow-up #1:",
        c["GREETING_NAMED"],
        "",
        c["FOLLOW_UP_1_LINE"],
        "",
        "{arm close}",
        "",
    ] + c["SIGN_OFF"] + [
        "",
        "Follow-up #2:",
        c["GREETING_NAMED"],
        "",
        c["FOLLOW_UP_2_LINE"],
        "",
    ] + c["SIGN_OFF"])


def doc_block(doc, heading, where):
    """The ```text fenced block that follows `heading` in the doc."""
    at = doc.find(heading)
    if at == -1:
        raise AssertionError("%r not found in %s -- the approved template text must stay in Section 9" % (heading, where))
    m = re.search(r"\n```text\n(.*?)\n```", doc[at:], re.S)
    if not m:
        raise AssertionError("no ```text block follows %r in %s" % (heading, where))
    return m.group(1).replace("\r\n", "\n")


def _first_difference(a, b):
    al, bl = a.split("\n"), b.split("\n")
    for i in range(max(len(al), len(bl))):
        x = al[i] if i < len(al) else "(missing)"
        y = bl[i] if i < len(bl) else "(missing)"
        if x != y:
            return "line %d\n    doc: %r\n    js:  %r" % (i + 1, x, y)
    return "(none)"


def assert_block(doc, heading, rendered, where, js_where):
    found = doc_block(doc, heading, where)
    if found != rendered:
        raise AssertionError(
            "the approved template text drifted between %s (%s) and %s -- it must match byte for byte. "
            "Fix the JS to match the doc. First difference at %s" % (where, heading.strip("*"), js_where,
                                                                   _first_difference(found, rendered)))


def doc_rules(doc, where):
    """The selection rules as Section 9 states them."""
    arm = re.search(r"Two arms, assigned by lead_id: even = ([AB]), odd = ([AB])\.", doc)
    order = re.search(r"\*\*SMYKM line[^\n]*?in this order: ((?:`[a-z]+`(?:, )?)+)", doc)
    areas = re.search(r"[Aa]t most (\w+) areas, lowercase, joined with \"and\"", doc)
    if not (arm and order and areas):
        raise AssertionError("Section 9 of %s no longer states the arm rule ('Two arms, assigned by lead_id: even = A, "
                             "odd = B.'), the SMYKM order ('in this order: `trials`, ...') and the area cap ('at most "
                             "two areas, lowercase, joined with \"and\"')" % where)
    words = {"one": 1, "two": 2, "three": 3}
    n = words.get(areas.group(1).lower(), None)
    if n is None:
        raise AssertionError("the area cap %r in %s is not a number" % (areas.group(1), where))
    return {"even": arm.group(1), "odd": arm.group(2), "order": re.findall(r"`([a-z]+)`", order.group(1)),
            "max_areas": n}


def assert_rules(doc, src, where, js_where):
    d, j = doc_rules(doc, where), js_rules(src)
    if d != j:
        raise AssertionError("the template selection rules drifted between %s and %s:\n  doc: %r\n  js:  %r"
                             % (where, js_where, d, j))


def assert_sign_off(c, sender_name, sender_title, js_where):
    """The template signs as the sender every other path signs as: Send checks
    each body for the name line followed by the title line, and the Reply
    Assistant signs with the same two. A template signed by anyone else would be
    refused at send time -- or worse, sent under the wrong name."""
    want = ["Cheers!", sender_name, sender_title]
    if c["SIGN_OFF"] != want:
        raise AssertionError("%s signs %r, but the sender is %r / %r (NOVASCOUT_SENDER_NAME / _TITLE). Send refuses a "
                             "body without the sender's name and title lines." % (js_where, c["SIGN_OFF"], sender_name,
                                                                                    sender_title))
    if sender_name not in c["INTRO_LINE"]:
        raise AssertionError("%s's introduction does not name the sender %r" % (js_where, sender_name))

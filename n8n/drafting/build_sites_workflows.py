"""Assemble the research-site niche's two workflows (migration 025):

    n8n/workflows/drafting-sites.json    Drafting - Research Sites    draftingSites01
    n8n/workflows/follow-ups-sites.json  Follow-Ups - Research Sites  followupSites01

Both are DERIVED from the CRO workflows this repo already generates
(drafting.json by n8n/drafting/build_workflow.py, follow-ups.json by
n8n/sendtrack/build_workflow.py) -- so run those builds first. What is shared is
copied byte for byte and asserted, never retyped:

  * the schedule, Config, the drop filter, Write Drafts & Advance and Write
    Follow-Up -- the same nodes, the same SQL;
  * the batch / due queries -- the CRO text with exactly these substitutions:
    the CRO filter becomes the niche filter (lead_niche() = 'research_site' and
    the niche row active), and the row gains the niche, its auto_approve switch
    and the site's ClinicalTrials.gov facility name;
  * code_template.js's rules section, which the shipped CRO "Build First Touch"
    node starts with byte for byte, so a site email is greeted, cleaned, armed
    and gated by the very functions a CRO email is.

The CRO workflows themselves are not touched by this script.
"""
import copy
import io
import json
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "..", "workflows")
DRAFTING = os.path.join(WF_DIR, "drafting.json")
FOLLOWUPS = os.path.join(WF_DIR, "follow-ups.json")
OUT_DRAFT = os.environ.get("DRAFTING_SITES_OUT", os.path.join(WF_DIR, "drafting-sites.json"))
OUT_FU = os.environ.get("FOLLOWUPS_SITES_OUT", os.path.join(WF_DIR, "follow-ups-sites.json"))

MARK = "// Node body"


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def node(wf, name):
    hits = [n for n in wf["nodes"] if n["name"] == name]
    assert len(hits) == 1, "%s: expected one node named %r" % (wf["name"], name)
    return hits[0]


def rules_section(src, what):
    i = src.find(MARK)
    assert i > 0, "%s has no %r marker" % (what, MARK)
    # Back up to the start of the comment block the marker sits in.
    j = src.rfind("// ----", 0, i)
    return src[:j if j > 0 else i]


def sub_once(text, old, new, what):
    assert text.count(old) == 1, "%s: expected exactly one %r, found %d" % (what, old, text.count(old))
    return text.replace(old, new)


def fresh_ids(nodes):
    for n in nodes:
        n["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "novascout/sites/" + n["name"]))


cro_draft = json.loads(read(DRAFTING))
cro_fu = json.loads(read(FOLLOWUPS))

TEMPLATE_SRC = read(os.path.join(HERE, "code_template.js"))
SITE_SRC = read(os.path.join(HERE, "code_site_template.js"))
SITE_FU_SRC = read(os.path.join(HERE, "code_site_followup.js"))

SHARED = rules_section(TEMPLATE_SRC, "code_template.js")
SITE_RULES = rules_section(SITE_SRC, "code_site_template.js")

# The CRO lane ships these rules: its Build First Touch starts with them, byte
# for byte, and so does its Build Follow-Up (after its own header comment).
_cro_first = node(cro_draft, "Build First Touch")["parameters"]["jsCode"]
assert _cro_first.startswith(SHARED), (
    "drafting.json's Build First Touch does not start with code_template.js's rules section -- rebuild "
    "n8n/drafting/build_workflow.py first, so both lanes share the same functions")
assert SHARED in node(cro_fu, "Build Follow-Up")["parameters"]["jsCode"], (
    "follow-ups.json's Build Follow-Up does not embed code_template.js's rules section -- rebuild "
    "n8n/sendtrack/build_workflow.py first")

# Section 12's clocks, exactly as the CRO lane substituted them.
_m = re.search(r"const SEND_CLOCK_COUNTRIES = (\[.*?\]);", _cro_first, re.S)
assert _m, "no SEND_CLOCK_COUNTRIES in drafting.json's Build First Touch"
CLOCKS_JSON = _m.group(1)
assert len(json.loads(CLOCKS_JSON)) >= 100, "the clock list looks truncated"

for src, what in ((SITE_SRC, "code_site_template.js"), (SITE_FU_SRC, "code_site_followup.js")):
    assert "__SEND_CLOCK_COUNTRIES__" in src, "%s lost its clock placeholder" % what
    rules = rules_section(src, what) if MARK in src else src
    assert "$(" not in rules_section(src, what) and "$input" not in rules_section(src, what), (
        "%s reads n8n data above its Node body -- the rules section must stay pure" % what)

FIRST_JS = SHARED + SITE_SRC.replace("__SEND_CLOCK_COUNTRIES__", CLOCKS_JSON)
FU_JS = SHARED + SITE_RULES + SITE_FU_SRC.replace("__SEND_CLOCK_COUNTRIES__", CLOCKS_JSON)
for js, what in ((FIRST_JS, "Build Site First Touch"), (FU_JS, "Build Site Follow-Up")):
    assert "__" + "SEND_CLOCK" not in js, what + ": placeholder left"

# No CRO wording can reach a site email: the site sources never name the CRO
# pitch, intro or sign-off title.
for banned in ("AI agents for CRO websites", "I'm the Founder of Amitrix Labs", "Founder, Amitrix Labs"):
    assert banned not in SITE_SRC and banned not in SITE_FU_SRC, "site source carries CRO wording %r" % banned

# ---------------------------------------------------------------------------
# Drafting - Research Sites
# ---------------------------------------------------------------------------

d = copy.deepcopy(cro_draft)
batch = node(d, "Get Draft Batch")
q = batch["parameters"]["query"]
what = "Get Draft Batch -> Get Site Draft Batch"
q = sub_once(q, "       e.raw_extraction->>'city' AS city,\n",
             "       coalesce(nullif(e.raw_extraction->>'city', ''), sls.city) AS city,\n"
             "       sls.facility_name,\n"
             "       nc.niche,\n"
             "       n.auto_approve AS niche_auto_approve,\n", what)
q = sub_once(q, "  CROSS JOIN LATERAL (SELECT lead_company_type(e.raw_extraction, l.source) AS company_type) ct\n",
             "  CROSS JOIN LATERAL (SELECT lead_company_type(e.raw_extraction, l.source) AS company_type) ct\n"
             "  CROSS JOIN LATERAL (SELECT lead_niche(e.raw_extraction, l.source) AS niche) nc\n"
             "  JOIN niches n ON n.code = nc.niche\n"
             "  LEFT JOIN site_lead_sources sls ON sls.lead_id = l.id\n", what)
q = sub_once(q, "   AND ct.company_type = 'CRO'\n",
             "   AND nc.niche = 'research_site'\n"
             "   AND n.active\n"
             "   AND s.fit_score >= n.min_fit_score\n", what)
q = "-- Get Site Draft Batch: Drafting's own batch query (drafting.json, Get Draft Batch) with the\n" \
    "-- CRO filter replaced by the research-site niche (migration 025: lead_niche(), niches.active,\n" \
    "-- niches.min_fit_score) and three columns added: the niche, its auto_approve switch, and the\n" \
    "-- site's ClinicalTrials.gov facility name for the strict facility match.\n" + q
batch["parameters"]["query"] = q
batch["name"] = "Get Site Draft Batch"
batch["notes"] = ("Drafting's batch query for the research-site niche (migration 025): leads at contact_found whose "
                  "niche is research_site, the niche active, the score at or above the niche's floor. Built from "
                  "drafting.json's Get Draft Batch by build_sites_workflows.py; only the niche filter differs.")

ct = node(d, "ClinicalTrials.gov Lookup")
params = ct["parameters"]["queryParameters"]["parameters"]
assert [p["name"] for p in params] == ["query.spons", "filter.overallStatus", "countTotal", "pageSize", "fields"]
ct["parameters"]["queryParameters"]["parameters"] = [
    # The name as an exact PHRASE: unquoted, "Northwest Clinical Research Center" matched 230 studies (4.9 MB, any
    # location with those words); quoted, the 31 that name it (235 KB) -- measured 2026-10-28. Code still decides.
    {"name": "query.locn", "value": "={{ '\"' + String($json.facility_name || $json.company_name).replace(/\"/g, '') + '\"' }}"},
    {"name": "filter.overallStatus", "value": "RECRUITING"},
    {"name": "countTotal", "value": "true"},
    {"name": "pageSize", "value": "1000"},
    {"name": "fields", "value": "NCTId,LocationFacility,LocationCity,LocationCountry,LocationStatus"},
]
ct["name"] = "ClinicalTrials.gov Location Lookup"
ct["notes"] = ("The recruiting trials AT this site for the first SMYKM fact. query.locn is a loose search over every "
               "location field, so Build Site First Touch counts a study only when one of its locations is "
               "RECRUITING, names this facility (the CRO lane's isSponsor() rule, after stripping a sponsor's site "
               "suffix) and is in the lead's city (an SMO: its country). A page that matched more than it returned "
               "counts as none. Never a contact: the fields asked for carry no location contact. Free, no key; one "
               "request in flight.")

build = node(d, "Build First Touch")
build["name"] = "Build Site First Touch"
build["parameters"]["jsCode"] = FIRST_JS
build["notes"] = ("The research-site first touch (operator wording 2026-10-28; niches.templates, migration 025): "
                  "site or SMO wording, the arm by lead_id (even A, odd B) for the SMYKM sentence and the close, the "
                  "SMYKM fact in the order recruiting trials at this site, therapeutic areas, city, company. Then "
                  "the CRO lane's own gate(): no verified address, no Send clock or a blocklisted domain holds it, "
                  "and so does either switch (settings.auto_approve_email, niches.auto_approve). No model call.")

drop = node(d, "Drop Failed Lookups")
wr = node(d, "Write Drafts & Advance")
cro_write = node(cro_draft, "Write Drafts & Advance")["parameters"]["query"]
assert wr["parameters"]["query"] == cro_write

rename = {"Get Draft Batch": "Get Site Draft Batch", "ClinicalTrials.gov Lookup": "ClinicalTrials.gov Location Lookup",
          "Build First Touch": "Build Site First Touch"}


def renamed_connections(conns):
    out = {}
    for src, outs in conns.items():
        out[rename.get(src, src)] = {"main": [[dict(e, node=rename.get(e["node"], e["node"])) for e in br]
                                              for br in outs["main"]]}
    return out


d["connections"] = renamed_connections(cro_draft["connections"])
d["id"] = "draftingSites01"
d["name"] = "Drafting - Research Sites"
d["settings"] = {"executionOrder": "v1", "availableInMCP": True}
d["active"] = False
fresh_ids(d["nodes"])
for n in d["nodes"]:
    if n["type"] == "n8n-nodes-base.code":
        for ref in re.findall(r"\$\('([^']+)'\)", n["parameters"]["jsCode"]):
            assert ref in {x["name"] for x in d["nodes"]}, "%s reads $('%s'), no such node" % (n["name"], ref)
    assert "anthropic" not in json.dumps(n).lower(), "%s mentions Anthropic -- no model in Drafting" % n["name"]

# ---------------------------------------------------------------------------
# Follow-Ups - Research Sites
# ---------------------------------------------------------------------------

f = copy.deepcopy(cro_fu)
due = node(f, "Find Due Follow-Ups")
q = due["parameters"]["query"]
what = "Find Due Follow-Ups -> Find Due Site Follow-Ups"
q = sub_once(q, "       auto_approve_email_enabled()                                     AS auto_approve_email,\n",
             "       auto_approve_email_enabled()                                     AS auto_approve_email,\n"
             "       lead_niche(e.raw_extraction, l.source)                           AS niche,\n"
             "       (SELECT n.auto_approve FROM niches n WHERE n.code = 'research_site') AS niche_auto_approve,\n",
             what)
q = sub_once(q, "   AND lead_company_type(e.raw_extraction, l.source) = 'CRO'\n",
             "   AND lead_niche(e.raw_extraction, l.source) = 'research_site'\n"
             "   AND EXISTS (SELECT 1 FROM niches n WHERE n.code = 'research_site' AND n.active)\n", what)
q = "-- Find Due Site Follow-Ups: Follow-Ups' own due query (follow-ups.json) with the CRO filter replaced\n" \
    "-- by the research-site niche (migration 025) and two columns added (the niche, its switch).\n" + q
due["parameters"]["query"] = q
due["name"] = "Find Due Site Follow-Ups"
bf = node(f, "Build Follow-Up")
bf["name"] = "Build Site Follow-Up"
bf["parameters"]["jsCode"] = FU_JS
assert node(f, "Write Follow-Up")["parameters"]["query"] == node(cro_fu, "Write Follow-Up")["parameters"]["query"]
rename = {"Find Due Follow-Ups": "Find Due Site Follow-Ups", "Build Follow-Up": "Build Site Follow-Up"}
f["connections"] = renamed_connections(cro_fu["connections"])
f["id"] = "followupSites01"
f["name"] = "Follow-Ups - Research Sites"
f["settings"] = {"executionOrder": "v1", "availableInMCP": True}
f["active"] = False
fresh_ids(f["nodes"])
for n in f["nodes"]:
    if n["type"] == "n8n-nodes-base.code":
        for ref in re.findall(r"\$\('([^']+)'\)", n["parameters"]["jsCode"]):
            assert ref in {x["name"] for x in f["nodes"]}, "%s reads $('%s'), no such node" % (n["name"], ref)

for wf, out in ((d, OUT_DRAFT), (f, OUT_FU)):
    with io.open(out + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(out + ".tmp", out)
    print("wrote", os.path.abspath(out))
print("  shared: code_template.js rules section (%d chars), Write Drafts & Advance, Write Follow-Up -- byte for byte"
      % len(SHARED))

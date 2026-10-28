"""Drift guards for the niche config (migration 025) -- the places one fact lives twice.

    python n8n/drafting/test_niche_drift_guards.py [--live]

Each guard is a function of its inputs; the BASELINE runs it on the real files
and must pass, and each MUTATION feeds it one altered input and must make it
fire -- a guard that cannot fail proves nothing. --live also compares the live
`niches` rows with the code.

  1. niches.templates in migration 025 == the wording the code sends
     (niche_templates.js, read from code_template.js / code_site_template.js).
  2. research_site.min_fit_score == Contact Lookup's general gate, parsed from
     the Master Ref sentence the contacts build reads.
  3. the identities: cro signs NOVASCOUT_SENDER_TITLE's title, research_site
     its template's own; one sender name.
  4. Scoring's site weights / sources == the niche row's; lead_company_type()'s
     and site_lead_sources' site sources == Scoring's.
  5. Scoring's institution words / stems / research words == the Trialsites
     selector's (n8n/sites/code_select_candidates.js).
  6. candidates.js's ISO table == the included countries (Section 12).
  7. drafting-sites.json / follow-ups-sites.json are what the build produces now.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MIG = os.path.join(REPO, "postgres", "migrations", "025_niches.sql")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def code_templates():
    return json.loads(subprocess.check_output(["node", os.path.join(HERE, "niche_templates.js")], encoding="utf-8"))


def seed_templates(mig):
    out = {}
    for code, body in re.findall(r"\(\s*'(cro|research_site)',.*?\$tpl\$(.*?)\$tpl\$::jsonb", mig, re.S):
        out[code] = json.loads(body)
    return out


def seed_field(mig, code, pattern):
    row = re.search(r"\(\s*'%s',(.*?)\$tpl\$.*?\$tpl\$::jsonb,(.*?)\)(?:,\n|\nON CONFLICT)" % code, mig, re.S)
    assert row, "no seed row for %s" % code
    return re.search(pattern, row.group(1) + row.group(2), re.S)


# --- the guards -------------------------------------------------------------------------

def g_templates(mig, code):
    seed = seed_templates(mig)
    for k in ("cro", "research_site"):
        assert k in seed, "migration 025 seeds no templates for %s" % k
        assert seed[k] == code[k], "niches.templates for %s in migration 025 differs from what the code sends" % k


def g_gate(mig, master_ref):
    m = re.search(r"Apollo lookup for leads scoring\s*(?:>=|≥|&ge;)\s*(\d+)\s*only", master_ref)
    assert m, "the Section 9 contact-gate sentence is gone"
    floor = int(re.search(r"ARRAY\['trialsites'\], (\d+),", mig).group(1))
    assert floor == int(m.group(1)), (
        "research_site.min_fit_score (%d) is not Contact Lookup's general gate (%s): a lead could be looked up and "
        "never drafted, or drafted below its niche's floor" % (floor, m.group(1)))


def g_identity(mig, site_src, sender_title):
    sign = json.loads(re.search(r"const SITE_SIGN_OFF = (\[[^\]]*\]);", site_src).group(1))
    cro_title = re.search(r"'Abdullah Amir', '([^']+)', true,\s*'\{\"holds\"[^']*\"2026-10-19\"", mig).group(1)
    site_title = re.search(r"'Abdullah Amir', '([^']+)', true,\s*'\{\"holds\"[^']*\"2026-10-28\"", mig).group(1)
    assert cro_title == sender_title, "niches.cro.sender_title %r is not NOVASCOUT_SENDER_TITLE %r" % (cro_title, sender_title)
    assert site_title == sign[2], "niches.research_site.sender_title %r is not the site template's %r" % (site_title, sign[2])
    assert sign[1] == "Abdullah Amir", "the site template signs as %r" % sign[1]


def g_scoring(mig, evaluate_src):
    weights = json.loads(re.search(r"\"weights\": (\{\"recruiting_trials\"[^}]*\})", mig).group(1))
    js_w = dict((k, int(v)) for k, v in re.findall(r"^\s+(\w+): (\d+),$",
                                                   re.search(r"const SITE_WEIGHTS = \{(.*?)\};", evaluate_src, re.S).group(1), re.M))
    assert js_w == weights, "Scoring's SITE_WEIGHTS %r differ from niches.research_site.scoring %r" % (js_w, weights)
    assert sum(js_w.values()) == 100, "the site weights sum to %d" % sum(js_w.values())
    js_src = re.findall(r"'([^']+)'", re.search(r"const SITE_SOURCES = \[([^\]]*)\]", evaluate_src).group(1))
    row_src = re.findall(r"'([^']+)'", re.search(r"ARRAY\['site', 'SMO'\],\s*ARRAY\[([^\]]*)\]", mig).group(1))
    assert set(js_src) == set(row_src) - {"ichgcp"}, "Scoring's SITE_SOURCES %r differ from the niche's %r" % (js_src, row_src)
    fn = re.search(r"WHEN source IN \(([^)]*)\) THEN 'site'", mig).group(1)
    assert set(re.findall(r"'([^']+)'", fn)) == set(js_src) | {"trialsites"}, "lead_company_type()'s site sources drifted"
    chk = re.search(r"source\s+text NOT NULL CHECK \(source IN \(([^)]*)\)\)", mig).group(1)
    assert set(re.findall(r"'([^']+)'", chk)) == set(js_src), "site_lead_sources' source CHECK drifted"


def _js_list(src, name):
    m = re.search(r"const %s = \[(.*?)\];" % name, src, re.S)
    assert m, "%s not found" % name
    return re.findall(r"'([^']+)'", m.group(1))


def g_lists(evaluate_src, select_src):
    inst = _js_list(select_src, "EXCLUDED_WORDS")
    mine = _js_list(evaluate_src, "SITE_HOSPITAL_WORDS") + _js_list(evaluate_src, "SITE_ACADEMIC_WORDS") + \
        _js_list(evaluate_src, "SITE_GOVERNMENT_WORDS")
    assert sorted(mine) == sorted(inst), "Scoring's institution words differ from the Trialsites selector's: %r" % (
        sorted(set(mine) ^ set(inst)))
    assert _js_list(evaluate_src, "SITE_INSTITUTION_STEMS") == _js_list(select_src, "EXCLUDED_STEMS"), "stems drifted"
    assert _js_list(evaluate_src, "SITE_RESEARCH_WORDS") == _js_list(select_src, "RESEARCH_WORDS"), "research words drifted"


def g_iso(candidates_src, included):
    iso = re.findall(r"[A-Z]{2}: (?:'([^']+)'|\"([^\"]+)\")", re.search(r"const ISO2 = \{(.*?)\};", candidates_src, re.S).group(1))
    names = [a or b for a, b in iso]
    assert len(names) == len(set(names)), "a country appears twice in candidates.js's ISO table"
    assert set(names) == set(included), "candidates.js's ISO table and Section 12 differ: %r" % sorted(set(names) ^ set(included))


def g_built():
    env = dict(os.environ)
    tmp = os.path.join(REPO, "n8n", "workflows", ".drift-")
    env["DRAFTING_SITES_OUT"], env["FOLLOWUPS_SITES_OUT"] = tmp + "d.json", tmp + "f.json"
    subprocess.check_output([sys.executable, os.path.join(HERE, "build_sites_workflows.py")], env=env)
    try:
        for t, real in ((tmp + "d.json", "drafting-sites.json"), (tmp + "f.json", "follow-ups-sites.json")):
            assert read(t) == read(os.path.join(REPO, "n8n", "workflows", real)), (
                "%s is stale -- rerun n8n/drafting/build_sites_workflows.py" % real)
    finally:
        for t in (tmp + "d.json", tmp + "f.json"):
            if os.path.exists(t):
                os.remove(t)


# --- inputs --------------------------------------------------------------------------------

def sender_title():
    v = os.environ.get("NOVASCOUT_SENDER_TITLE")
    if v:
        return v
    for line in read(os.path.join(REPO, ".env")).splitlines():
        if line.startswith("NOVASCOUT_SENDER_TITLE="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("NOVASCOUT_SENDER_TITLE not set")


def included_countries():
    wf = json.loads(read(os.path.join(REPO, "n8n", "workflows", "trialsites.json")))
    src = [n for n in wf["nodes"] if n["name"] == "Select Candidates"][0]["parameters"]["jsCode"]
    return json.loads(re.search(r"const INCLUDED_COUNTRIES = (\[.*?\]);", src, re.S).group(1))


MIG_SRC = read(MIG)
CODE = code_templates()
MASTER = read(os.path.join(REPO, "NovaScout_MasterRef.md"))
SITE = read(os.path.join(HERE, "code_site_template.js"))
EVAL = read(os.path.join(REPO, "n8n", "scoring", "code_evaluate.js"))
SELECT = read(os.path.join(REPO, "n8n", "sites", "code_select_candidates.js"))
CAND = read(os.path.join(REPO, "n8n", "sites", "open_sources", "candidates.js"))
TITLE = sender_title()
INCLUDED = included_countries()

CASES = [
    ("BASELINE templates", lambda: g_templates(MIG_SRC, CODE), True),
    ("BASELINE gate", lambda: g_gate(MIG_SRC, MASTER), True),
    ("BASELINE identity", lambda: g_identity(MIG_SRC, SITE, TITLE), True),
    ("BASELINE scoring", lambda: g_scoring(MIG_SRC, EVAL), True),
    ("BASELINE lists", lambda: g_lists(EVAL, SELECT), True),
    ("BASELINE iso", lambda: g_iso(CAND, INCLUDED), True),
    ("BASELINE built JSON is fresh", g_built, True),
    ("a site SMYKM line edited in the code only",
     lambda: g_templates(MIG_SRC, json.loads(json.dumps(CODE).replace("works in {areas}", "specialises in {areas}"))), False),
    ("a CRO line edited in the migration only",
     lambda: g_templates(MIG_SRC.replace("Worth a look for {company}?", "Worth a look?", 1), CODE), False),
    ("the Master Ref gate moves to 45",
     lambda: g_gate(MIG_SRC, MASTER.replace("Apollo lookup for leads scoring ≥ 50 only", "Apollo lookup for leads scoring ≥ 45 only")), False),
    ("the site sign-off takes the CRO title",
     lambda: g_identity(MIG_SRC, SITE.replace('"Software Engineer, Amitrix Labs"]', '"Founder, Amitrix Labs"]'), TITLE), False),
    ("a different sender title in .env", lambda: g_identity(MIG_SRC, SITE, "CEO, Amitrix Labs"), False),
    ("a site weight changed in Scoring only", lambda: g_scoring(MIG_SRC, EVAL.replace("  recruiting_trials: 25,", "  recruiting_trials: 30,")), False),
    ("a new site source in Scoring only",
     lambda: g_scoring(MIG_SRC, EVAL.replace("const SITE_SOURCES = ['sites-ctgov', 'sites-overture'];",
                                             "const SITE_SOURCES = ['sites-ctgov', 'sites-overture', 'sites-x'];")), False),
    ("a hospital word dropped from Scoring", lambda: g_lists(EVAL.replace("'ziekenhuis', ", "", 1), SELECT), False),
    ("a stem added to the Trialsites selector only", lambda: g_lists(EVAL, SELECT.replace("'universit', ", "'universit', 'klinik', ", 1)), False),
    ("a country dropped from the ISO table", lambda: g_iso(CAND.replace("MX: 'Mexico',", ""), INCLUDED), False),
    ("a sanctioned country added to the ISO table", lambda: g_iso(CAND.replace("ZW: 'Zimbabwe',", "ZW: 'Zimbabwe', RU: 'Russia',"), INCLUDED), False),
]


def live():
    out = subprocess.run(["docker", "exec", "-i", "nova-scout-postgres-1", "psql", "-U", "novascout", "-d", "novascout", "-At",
                          "-c", "SELECT json_object_agg(code, templates) FROM niches;"], capture_output=True, text=True,
                         encoding="utf-8")
    rows = json.loads(out.stdout.strip())
    for k in ("cro", "research_site"):
        assert rows.get(k) == CODE[k], "the LIVE niches.templates for %s differs from what the code sends" % k


def main():
    failed = 0
    cases = CASES + ([("LIVE niches rows == the code", live, True)] if "--live" in sys.argv else [])
    for label, fn, should_pass in cases:
        try:
            fn()
            ok = should_pass
            why = "" if ok else "guard did not fire"
        except AssertionError as e:
            ok = not should_pass
            why = "" if ok else str(e)
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else " -- " + why))
        failed += 0 if ok else 1
    print("\n%d passed, %d failed" % (len(cases) - failed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

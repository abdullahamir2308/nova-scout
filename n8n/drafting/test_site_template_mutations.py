"""Mutation tests for the research-site templates: break each rule in
code_site_template.js / code_site_followup.js once, assemble the node code the
way build_sites_workflows.py does, and prove test_site_template.js FAILS.
A mutation that survives means a rule nobody tests.

    python n8n/drafting/test_site_template_mutations.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, "..", "workflows")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


TEMPLATE = read(os.path.join(HERE, "code_template.js"))
SITE = read(os.path.join(HERE, "code_site_template.js"))
SITE_FU = read(os.path.join(HERE, "code_site_followup.js"))


def section(src):
    i = src.find("// Node body")
    j = src.rfind("// ----", 0, i)
    return src[:j]


SHARED = section(TEMPLATE)
first = json.loads(read(os.path.join(WF, "drafting-sites.json")))
CLOCKS = re.search(r"const SEND_CLOCK_COUNTRIES = (\[.*?\]);",
                   [n for n in first["nodes"] if n["name"] == "Build Site First Touch"][0]["parameters"]["jsCode"],
                   re.S).group(1)


def assemble(site_src, fu_src):
    return (SHARED + site_src.replace("__SEND_CLOCK_COUNTRIES__", CLOCKS),
            SHARED + section(site_src) + fu_src.replace("__SEND_CLOCK_COUNTRIES__", CLOCKS))


def run(site_src, fu_src):
    f_js, u_js = assemble(site_src, fu_src)
    d = tempfile.mkdtemp(prefix="site-mut-")
    fp, up = os.path.join(d, "first.js"), os.path.join(d, "fu.js")
    open(fp, "w", encoding="utf-8").write(f_js)
    open(up, "w", encoding="utf-8").write(u_js)
    env = dict(os.environ, SITE_FIRST_SRC=fp, SITE_FU_SRC=up)
    r = subprocess.run(["node", os.path.join(HERE, "test_site_template.js")], env=env, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, r.stdout


MUTATIONS = [
    # (label, file, old, new)
    ("the site intro reverts to the CRO intro", "site",
     "but I'm Abdullah Amir, a software engineer who builds AI assistants for clinical research websites.",
     "but I'm Abdullah Amir and I'm the Founder of Amitrix Labs."),
    ("the sign-off title becomes the CRO title", "site", '"Software Engineer, Amitrix Labs"]', '"Founder, Amitrix Labs"]'),
    ("the SMO pitch loses 'across your sites'", "site", "conversations about studies across your sites.", "conversations about studies."),
    ("arms swapped for trials", "site", "fill(arm === 'A' ? t.trials_A : t.trials_B", "fill(arm === 'B' ? t.trials_A : t.trials_B"),
    ("arms swapped for areas", "site", "areaAgreement(arm === 'A' ? t.areas_A : t.areas_B", "areaAgreement(arm === 'B' ? t.areas_A : t.areas_B"),
    ("two areas keep 'that area'", "site", "return areaCount > 1 ?", "return areaCount > 9 ?"),
    ("SMO wording never used", "site", "SITE_ENTITY = { site: 'site', SMO: 'smo' }", "SITE_ENTITY = { site: 'site', SMO: 'site' }"),
    ("a COMPLETED location counts as recruiting", "site", "if (str(loc && loc.status).toUpperCase() !== 'RECRUITING') return false;", ""),
    ("any city counts for a site", "site", "return smo ? sameCountry(loc.country, lead.country) : sameCity(loc.city, lead.city);", "return true;"),
    ("a loose name match counts", "site", "if (!isSponsor(facility, facilityCore(loc.facility))) return false;", ""),
    ("the site suffix is no longer stripped", "site", ".replace(/\\s*\\/\\s*ID\\s*#\\s*\\d+.*$/i, '')", ""),
    ("an unverifiable page counts", "site", "|| ct.totalCount > studies.length) return 0;", ") return 0;"),
    ("a generic placeholder facility counts", "site", "|| GENERIC_FACILITY.test(sponsorKey(facility))", ""),
    ("the niche switch is ignored on a first touch", "site",
     "auto_approve: lead.auto_approve_email === true && lead.niche_auto_approve === true,",
     "auto_approve: lead.auto_approve_email === true,"),
    ("a CRO row is drafted with site wording", "site", "if (!SITE_ENTITY[str(lead.company_type)] || str(lead.niche) !== 'research_site') {", "if (false) {"),
    ("a lookup failure still drafts", "site", "if (!ct || typeof ct !== 'object' || ct.error || typeof ct.totalCount !== 'number') {", "if (false) {"),
    ("'Dra.' is no longer stripped", "site", "'dra', ", ""),
    ("follow-up #1 says 'we'", "site", "and I help make sure those visits", "and we help make sure those visits"),
    ("follow-up #2 loses 'sponsor conversations'", "site", "visitors into sponsor conversations is ever", "visitors into qualified leads is ever"),
    ("follow-up #1 loses its close", "site", "  if (n === 1) lines.push(closeFor(arm, company), '');\n  const firstBody", "  const firstBody"),
    ("follow-ups ignore the niche switch", "fu",
     "auto_approve: r.auto_approve_email === true && r.niche_auto_approve === true,", "auto_approve: r.auto_approve_email === true,"),
    ("follow-ups write for a CRO row", "fu", "if (str(r.niche) !== 'research_site' || !SITE_ENTITY[str(r.company_type)]) return null;", ""),
]


def main():
    code, out = run(SITE, SITE_FU)
    if code != 0:
        print(out)
        sys.exit("BASELINE fails -- fix test_site_template.js first")
    print("baseline: test_site_template.js passes on the unmutated sources")
    survived = 0
    for label, which, old, new in MUTATIONS:
        src = SITE if which == "site" else SITE_FU
        if src.count(old) != 1:
            print("  BROKEN ANCHOR  %s (%d matches)" % (label, src.count(old)))
            survived += 1
            continue
        mutated = src.replace(old, new)
        code, out = run(mutated if which == "site" else SITE, mutated if which == "fu" else SITE_FU)
        killed = code != 0
        print("  %s  %s" % ("killed  " if killed else "SURVIVED", label))
        survived += 0 if killed else 1
    print("\n%d mutations, %d killed, %d survived" % (len(MUTATIONS), len(MUTATIONS) - survived, survived))
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()

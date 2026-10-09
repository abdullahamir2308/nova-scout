"""Derives nova-one-pager-sites.docx from nova-one-pager.docx (2026-10-09).

    python n8n/drafting/build_onepager_sites.py

Research sites and SMOs became a second lead source (Section 9, Workflow 1b), and
a site that replies gets this one-pager instead of the CRO one (Mailbox Watch's
notification picks by company type; both are hosted the same way, from the repo's
main branch). The CRO one-pager's own generator is not in this repo (see
n8n/drafting/README.md), so the site version is DERIVED from it: every zip member
is copied byte for byte except word/document.xml, in which each phrase below is
replaced exactly once. A phrase that is not found exactly once stops the build --
so if the CRO one-pager is regenerated with different wording, this refuses
rather than shipping a half-converted page. Rerun it after every regeneration.

What changes, and why each change is supported by the Nova Agent Kit code (the
same evidence as the site claims in migration 019):
  * the visitor is "a sponsor or a CRO choosing sites", not only a sponsor --
    capture_sponsor_lead captures any organisation's name;
  * "website", never "site", for the prospect's web pages -- for a research site
    "your site" reads as the clinic;
  * Live today lists Vertex first, as "a clinical research center": Vertex is
    never a CRO, and a site never gets the "two CROs" line;
  * nothing about the product is widened: the same five capabilities, the same
    48-hour demo, the same contact line.
"""
import hashlib
import io
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "nova-one-pager.docx")
OUT = os.environ.get("ONEPAGER_SITES_OUT", os.path.join(REPO, "nova-one-pager-sites.docx"))

# (phrase in the CRO one-pager's document.xml, its site replacement). XML text,
# so apostrophes are &apos; exactly as the source stores them.
REPLACEMENTS = [
    ("A sponsor evaluating you finds your site at 11pm, has a question, and gets a contact form. "
     "By the time your team replies the next morning, they&apos;ve often already moved to the next "
     "CRO on their list. That gap is where qualified leads are lost before your team ever sees them.",
     "A sponsor, or a CRO choosing sites for a study, finds your website at 11pm, has a question, and "
     "gets a contact form. By the time your team replies the next morning, they&apos;ve often already "
     "moved to the next site on their list. That gap is where study inquiries are lost before your "
     "team ever sees them."),
    ("It answers sponsor questions directly, in real time, using only what your site says",
     "It answers sponsor and CRO questions directly, in real time, using only what your website says"),
    ("Qualifies each sponsor by company, contact, therapeutic area and study phase",
     "Qualifies each sponsor or CRO by company, contact, therapeutic area and study phase"),
    ("Sends investigators, sites and course enquiries to their own flows, so BD time goes to sponsors.",
     "Sends investigator and course enquiries to their own flows, so your team&apos;s time goes to study inquiries."),
    ("Offers a qualified sponsor a link to book a call with your team.",
     "Offers a qualified sponsor or CRO a link to book a call with your team."),
    ("Vertex Clinical Research (Mexico).",
     "Vertex Clinical Research (Mexico) — a clinical research center."),
    ("Every CRO already has a system that works for them",
     "Every research site already has a system that works for it"),
    ("built directly on your own site content and service pages",
     "built directly on your own website content and service pages"),
    ("nothing goes live on your site without your sign-off", "nothing goes live on your website without your sign-off"),
    ("You share your site URL.", "You share your website URL."),
    ("We configure Nova specifically on your site content and service pages.",
     "We configure Nova specifically on your website content and service pages."),
]

# Live today: Vertex first for a site reader. Two whole paragraphs swapped.
SWAP = ("NoblePath CRO (Türkiye) — an oncology-focused CRO.",
        "Vertex Clinical Research (Mexico) — a clinical research center.")


def build():
    with zipfile.ZipFile(SRC) as zin:
        members = [(i, zin.read(i.filename)) for i in zin.infolist()]
    xml = dict((i.filename, d) for i, d in members)["word/document.xml"].decode("utf-8")
    for old, new in REPLACEMENTS:
        n = xml.count(old)
        if n != 1:
            raise SystemExit("phrase found %d times in %s (expected exactly 1): %r" % (n, SRC, old[:70]))
        xml = xml.replace(old, new)
    # Swap the two Live-today paragraphs so Vertex comes first.
    paras = xml.split("</w:p>")
    idx = [k for k, p in enumerate(paras) if SWAP[0] in p or SWAP[1] in p]
    if len(idx) != 2:
        raise SystemExit("the two Live-today paragraphs were not found exactly once each")
    a, b = idx
    if SWAP[0] in paras[a]:
        paras[a], paras[b] = paras[b], paras[a]
    xml = "</w:p>".join(paras)
    for banned in ("two CROs", "CRO websites", "Vertex Clinical Research (Mexico) — a CRO"):
        if banned in xml:
            raise SystemExit("the site one-pager would say %r" % banned)
    tmp = OUT + ".tmp"
    with zipfile.ZipFile(tmp, "w") as zout:
        for info, data in members:
            if info.filename == "word/document.xml":
                data = xml.encode("utf-8")
            zout.writestr(info, data)
    os.replace(tmp, OUT)
    return hashlib.sha256(open(OUT, "rb").read()).hexdigest()


if __name__ == "__main__":
    digest = build()
    print("wrote %s (sha256 %s...%s) from %s" % (OUT, digest[:8], digest[-4:], os.path.basename(SRC)))

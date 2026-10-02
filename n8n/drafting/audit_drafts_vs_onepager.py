"""Drafts-vs-one-pager consistency check. READ-ONLY.

    python n8n/drafting/audit_drafts_vs_onepager.py

Reads the live claims library and the live first-touch queue (pending and
approved drafts) from Postgres, and nova-one-pager.docx from the repo root, and
checks that they agree. It changes nothing.

Run it after a claims_library edit, after a redraft, and after the one-pager is
regenerated. It exists because a draft can be clean on its own and still
contradict the asset it leads to.

Terminology, drafting skill v3 (2026-10-02): "sponsor" means the CRO's client
-- the biotech, pharma, device or academic company that hires a CRO -- in the
claims, the drafts and the one-pager alike. The prospect is never the one
sponsoring. v2 went the other way ("pharma team" for the client, "You're
sponsoring ..." for the prospect); that collision came from the hooks, so v3
bans the hook phrasing instead. This script used to fail on any "sponsor" in a
library line or the one-pager; it now fails on the opposite: any text that
calls the PROSPECT a sponsor, and any leftover "pharma team".

What it checks
  A  the one-pager: no placeholders or internal notes; "sponsor" for the client,
     no "pharma team", nothing calling the reader a sponsor; and the three claims
     settled on 2026-10-02 (migration 013): no SOPs or client documents as the
     knowledge source, Nova never books a call itself, no "new dashboard" line
  B  the library: v3 slots only; no active line calls the prospect a sponsor,
     says "pharma team", makes a seconds-level claim, or breaks a settled claim;
     every description and benefit line names its capabilities
  C  every active line's claims against the one-pager text (CLAIMS, below)
  D  each live draft: never calls the prospect a sponsor, no "pharma team", no
     settled claim broken, the claim codes it records are live library lines,
     no two of them repeat a capability, the word ceiling
  E  each lead's live drafts against the drafts they replaced
  F  queue integrity
  G  each live follow-up draft (skill section 8): the same text checks as D, its
     codes live, no repeated capability, and its length (#1 40-70, #2 <= 40,
     read from code_followup_assemble.js)

Keeping the claim map current. CLAIMS maps each active library line to pairs of
(phrase in the line, evidence in the one-pager). It is validated against the
live table on every run: a line that was edited so that a mapped phrase is gone,
or a new active line with no entry, FAILS instead of being skipped. When that
happens, update CLAIMS -- and find the evidence by reading the one-pager, not by
writing whatever phrase makes the check pass. An angle that asserts nothing
about Nova (an industry fact, or a question) maps to an empty list.

Exit code 0 when every check passes, 1 otherwise.

Environment
  AUDIT_DOCX             audit another .docx, e.g. a regenerated candidate
                         before it replaces the repo copy
  NOVASCOUT_PG_CONTAINER / NOVASCOUT_PG_USER / NOVASCOUT_PG_DB
                         default nova-scout-postgres-1 / novascout / novascout
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
DOCX = os.environ.get('AUDIT_DOCX', os.path.join(REPO, 'nova-one-pager.docx')).replace('\\', '/')
PG_CONTAINER = os.environ.get('NOVASCOUT_PG_CONTAINER', 'nova-scout-postgres-1')
PG_USER = os.environ.get('NOVASCOUT_PG_USER', 'novascout')
PG_DB = os.environ.get('NOVASCOUT_PG_DB', 'novascout')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
FAILS = []
V3_SLOTS = {'description', 'angle', 'benefit', 'proof', 'ask', 'link'}


def assembler_ceiling():
    """The ceiling the assembler enforces, read from the shipped source rather than retyped."""
    with io.open(os.path.join(HERE, 'code_assemble.js'), encoding='utf-8') as fh:
        m = re.search(r'const BODY_CEILING = (\d+);', fh.read())
    if not m:
        raise SystemExit('const BODY_CEILING not found in code_assemble.js -- this script needs updating')
    return int(m.group(1))


CEILING = assembler_ceiling()   # code_assemble.js tags a body `long` only when it is MORE than this

# The same patterns code_assemble.js tags `prospect-sponsor` with: the prospect
# described as sponsoring something.
PROSPECT_SPONSOR = [
    re.compile(r"\byou(?:['’]re| are)\s+(?:also\s+|currently\s+|now\s+|actively\s+)?sponsor(?:ing|s)?\b", re.I),
    re.compile(r"\byou(?:['’]re| are)\s+(?:the|a|an)\s+(?:\w+\s+)?sponsor\b", re.I),
    re.compile(r"\byou\s+sponsor(?:ed|s)?\b", re.I),
    re.compile(r"\byou as (?:the |a )?sponsor\b", re.I),
    re.compile(r"\bsponsored by you\b", re.I),
    re.compile(r"\byour (?:own )?(?:sponsored|sponsorship)\b", re.I),
]
PHARMA_TEAM = re.compile(r'\bpharma[- ]teams?\b', re.I)
# The claims settled on 2026-10-02 against the Nova Agent Kit code (migration 013): Nova
# answers from the CRO's website, never from SOPs or documents a client provides; it sends
# the booking link and the sponsor books; and it has its own dashboard. The same patterns
# code_assemble.js tags `claim-sop` / `claim-books` with, plus the dropped BEN-ROUTE clause.
SETTLED = [
    ('SOPs or client documents as the source', re.compile(
        r"\bSOPs?\b|\bstandard operating procedures?\b|\b(?:your|their)\s+(?:own\s+)?(?:documents|documentation)\b", re.I)),
    ('Nova booking a call itself', re.compile(
        r"\bbooks\s+(?:the\s+|a\s+|qualified\s+|sponsor\s+)?(?:calls?|meetings?|demos?)\b|"
        r"\b(?:into|onto|straight into)\s+your\s+(?:calendar|diary)\b|\bschedules\s+(?:the\s+|a\s+)?(?:calls?|meetings?)\b", re.I)),
    ('"not in another dashboard" / "new dashboard"', re.compile(
        r"\b(?:not|n't)\s+(?:in|force)\s+(?:another|a new)\s+dashboard\b|\bno new dashboard\b", re.I)),
]


def settled_breaks(text):
    return [label for label, pat in SETTLED if pat.search(text or '')]


def followup_limits():
    """The follow-up lengths Assemble Follow-Up enforces, read from the shipped source."""
    with io.open(os.path.join(HERE, '..', 'sendtrack', 'code_followup_assemble.js'), encoding='utf-8') as fh:
        src = fh.read()
    vals = {}
    for name in ('FOLLOW_UP_1_MIN', 'FOLLOW_UP_1_MAX', 'FOLLOW_UP_2_MAX'):
        m = re.search(r'const %s = (\d+);' % name, src)
        if not m:
            raise SystemExit('const %s not found in code_followup_assemble.js -- this script needs updating' % name)
        vals[name] = int(m.group(1))
    return vals


FU_LIMITS = followup_limits()


def calls_prospect_sponsor(text):
    return [m.group(0) for r in PROSPECT_SPONSOR for m in r.finditer(text or '')]


def check(label, ok, detail=''):
    print('  %-4s %s%s' % ('PASS' if ok else 'FAIL', label, (' -- ' + detail) if detail else ''))
    if not ok:
        FAILS.append(label)
    return ok


def psql(sql):
    try:
        r = subprocess.run(['docker', 'exec', '-i', PG_CONTAINER, 'psql', '-U', PG_USER, '-d', PG_DB, '-At', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                           input='SET default_transaction_read_only = on;\n' + sql, capture_output=True, encoding='utf-8')
    except FileNotFoundError:
        raise SystemExit('docker not found on PATH -- this check reads the live database through the %s container' % PG_CONTAINER)
    if r.returncode:
        raise SystemExit('psql failed: ' + r.stderr.strip())
    return [json.loads(l) for l in r.stdout.splitlines() if l.startswith('{')]


def norm(s):
    """casefold, hyphens and dashes -> space, drop other punctuation, collapse whitespace."""
    s = unicodedata.normalize('NFC', s).casefold()
    s = re.sub(r'[‐-―\-]+', ' ', s)
    s = re.sub(r"[^\w\s+']", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def words(text):
    """code_assemble.js words(): whitespace tokens containing a letter or a digit."""
    return len([t for t in text.split() if re.search(r'[^\W_]', t, re.U)])


def codes_of(variant):
    core = variant.split('/', 1)[1].split('+', 1)[0] if '/' in (variant or '') else ''
    return [c for c in core.split('.') if c]


def tags_of(variant):
    return (variant or '').split('+', 1)[1].split(',') if '+' in (variant or '') else []


def core_paragraphs(body):
    """What the ceiling counts: everything after the greeting, before the opt-out and the signature."""
    paras = (body or '').replace('\r\n', '\n').split('\n\n')[1:]
    out = []
    for p in paras:
        if p.startswith("If this isn't relevant"):
            break
        out.append(p)
    return out


def body_words(body):
    return words(' '.join(core_paragraphs(body)))


# ---- inputs -----------------------------------------------------------------------------------------------
if not os.path.exists(DOCX):
    raise SystemExit('one-pager not found: ' + DOCX)
z = zipfile.ZipFile(DOCX)
root = ET.fromstring(z.read('word/document.xml'))
OP_PARAS = [''.join((e.text or '') for e in p.iter(W + 't')) for p in root.iter(W + 'p')]
OP_PARAS = [p for p in OP_PARAS if p.strip()]
OP = '\n'.join(OP_PARAS)
OPN = norm(OP)
lib = {r['code']: r for r in psql("SELECT row_to_json(t) FROM (SELECT code, slot, body, active, confirmed, capabilities FROM claims_library) t;")}
active = {c: r for c, r in lib.items() if r['active']}
FIRST_TOUCH = "coalesce(d.variant,'') NOT LIKE 'follow-up-%' AND coalesce(d.variant,'') NOT LIKE 'low-context%'"
LIVE_SQL = ("SELECT row_to_json(t) FROM (SELECT d.id, d.lead_id, l.country, d.channel, d.status, d.variant, d.subject, d.body, d.edited_body "
            "FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE d.status IN ('pending','approved') AND " + FIRST_TOUCH + " ORDER BY d.lead_id, d.channel, d.id) t;")
HISTORY_SQL = ("SELECT row_to_json(t) FROM (SELECT d.id, d.lead_id, d.channel, d.status, d.variant, d.body FROM drafts d "
               "WHERE " + FIRST_TOUCH + " ORDER BY d.id) t;")
live = psql(LIVE_SQL)
history = psql(HISTORY_SQL)
FOLLOWUP_SQL = ("SELECT row_to_json(t) FROM (SELECT d.id, d.lead_id, l.country, d.status, d.variant, d.subject, d.body, d.edited_body "
                "FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE d.status IN ('pending','approved') "
                "AND d.variant LIKE 'follow-up-%' ORDER BY d.lead_id, d.id) t;")
followups = psql(FOLLOWUP_SQL)
for d in live:
    d['sendable'] = d['edited_body'] or d['body']      # what Workflow 6 would send
    d['human_edited'] = bool(d['edited_body'])


# ============================================================================================================
print('=' * 92)
print('A. ONE-PAGER  (%s)' % DOCX)
print('=' * 92)
print('       sha256 %s' % hashlib.sha256(open(DOCX, 'rb').read()).hexdigest())
xml = z.read('word/document.xml').decode('utf-8')
brackets = re.findall(r'\[[^\]]*\]', OP)
check('no bracketed segments / placeholders', not brackets, 'found %d %s' % (len(brackets), brackets[:2]) if brackets else '0 of them')
for label, pat in (('"[DATE]" placeholder', r'\[DATE\]'), ('"[Abdullah: ...]" internal note', r'\bAbdullah:'),
                   ('"Do not estimate one" note text', r'\bDo not estimate\b'), ('TODO / TBD / lorem', r'\bTODO\b|\bTBD\b|lorem')):
    check('no ' + label, not re.search(pat, OP, re.I))
check('no hidden runs, tracked changes, comments', xml.count('<w:vanish') == 0 and xml.count('<w:ins ') == 0 and xml.count('<w:del ') == 0
      and not re.search(r'<w:comment\b', z.read('word/comments.xml').decode('utf-8') if 'word/comments.xml' in z.namelist() else ''))
pharma_op = PHARMA_TEAM.findall(OP)
check('no "pharma team" left in the one-pager (v3: the client is a "sponsor")', not pharma_op,
      '%d occurrence(s)' % len(pharma_op) if pharma_op else '0 occurrences')
check('the one-pager never calls the reader a sponsor', not calls_prospect_sponsor(OP), str(calls_prospect_sponsor(OP)))
for label, pat in SETTLED:
    hits = [m.group(0) for m in pat.finditer(OP)]
    check('the one-pager makes no settled-wrong claim: %s' % label, not hits, str(hits) if hits else '')
print('       "sponsor" (the client) in the one-pager: %d occurrence(s)' % len(re.findall(r'\bsponsor\w*', OP, re.I)))

# ============================================================================================================
print('\n' + '=' * 92)
print('B. LIBRARY  (live claims_library)')
print('=' * 92)
bad_slots = sorted(c for c, r in lib.items() if r['slot'] not in V3_SLOTS)
check('every row is in a v3 slot (migration 012)', not bad_slots, str(bad_slots) if bad_slots else '%d rows' % len(lib))
for code in sorted(active):
    hits = calls_prospect_sponsor(active[code]['body'])
    if hits:
        check('%s does not call the prospect a sponsor' % code, False, str(hits))
check('no ACTIVE line calls the prospect a sponsor', not any(calls_prospect_sponsor(r['body']) for r in active.values()))
check('no ACTIVE line says "pharma team"', not any(PHARMA_TEAM.search(r['body'] or '') for r in active.values()))
check('no ACTIVE line makes a seconds-level claim ("second(s)")', not any(re.search(r'\bseconds?\b', r['body'] or '', re.I) for r in active.values()))
broken = sorted((c, settled_breaks(r['body'])) for c, r in active.items() if settled_breaks(r['body']))
check('no ACTIVE line breaks a claim settled on 2026-10-02 (SOPs, Nova booking, "another dashboard")', not broken,
      str(broken) if broken else '')
nocaps = sorted(c for c, r in active.items() if r['slot'] in ('description', 'benefit') and not r.get('capabilities'))
check('every active description and benefit line names its capabilities (migration 013)', not nocaps,
      str(nocaps) if nocaps else '%d lines' % len([1 for r in active.values() if r['slot'] in ('description', 'benefit')]))
unconf = sorted(c for c, r in active.items() if not r['confirmed'])
print('       active lines not yet confirmed by a human: %d of %d%s' % (len(unconf), len(active), (' (%s)' % ', '.join(unconf)) if unconf else ''))

# ============================================================================================================
print('\n' + '=' * 92)
print('C. WHAT EACH ACTIVE LIBRARY LINE ASSERTS  vs  THE ONE-PAGER  (phrase in the line -> evidence in the one-pager)')
print('=' * 92)
# code -> [(phrase as it appears in the LIVE line, evidence phrase that must appear in the one-pager)]
CLAIMS = {
    'D1': [('ai assistant for your website', 'ai agent for your website'), ('qualified leads', 'qualified leads')],
    'D2': [('for your website', 'for your website'), ('answers sponsors', 'it answers sponsor questions'),
           ('qualifies them', 'qualifies the inquiry'), ('sends them your booking link', 'a link to book a call')],
    'ANG-HOURS': [('11pm', '11pm'), ('until morning', 'the next morning'), ('next cro', 'next cro on their list')],
    'ANG-SILENT': [],    # a question; asserts nothing about Nova
    'ANG-STAKES': [],    # an industry fact, not a Nova result (skill section 4)
    'ANG-SPEED': [],     # an industry observation
    'ANG-TIME': [('bd time', 'bd time goes to sponsors'), ('not to sorting every inquiry', 'to their own flows')],
    'BEN-247': [('from your own website', 'trained on your own website'), ('in real time', 'in real time'),
                ('at any hour', 'at any hour')],
    'BEN-CAPTURE': [('named lead', 'captured lead'),
                    ('company contact therapeutic area and study phase', 'company contact therapeutic area and study phase')],
    'BEN-BRIEF': [('therapeutic area and study phase', 'therapeutic area and study phase'),
                  ('before your first call', 'your first call starts with the basics')],
    'BEN-BOOK': [('sends qualified sponsors your booking link', 'offers a qualified sponsor a link to book a call'),
                 ('book a call with your team', 'book a call with your team')],
    'BEN-ROUTE': [('where your team already works', 'wherever your team already works')],
    'BEN-SEE': [('every sponsor lead it captured', 'every captured lead'),
                ('any question it passed to your team', 'every question it passed to your team')],
    'BEN-DECK': [('capabilities deck', 'capabilities deck'), ('the moment a sponsor asks for it', 'any visitor who asks for it')],
    'BEN-FIT': [('configured around your services', 'configured'), ('not a template', 'not a generic template')],
    'PR-TR': [('noblepath', 'noblepath'), ('oncology cro', 'oncology focused cro'), ('türkiye', 'türkiye'), ('live at', 'live today')],
    'PR-MX': [('vertex clinical research', 'vertex clinical research'), ('mexico', 'mexico'), ('live at', 'live today')],
    'PR-BOTH': [('türkiye', 'türkiye'), ('mexico', 'mexico'), ('live at', 'live today')],   # "two CROs" is checked by counting below
    'A1': [('48 hour demo', '48 hour demo'), ('built on your own material', 'built directly on your own site content and service pages')],
    'A2': [('48 hour demo', '48 hour demo'), ('on your own material', 'built directly on your own site content and service pages')],
}
# Semantic caveats the phrase table cannot settle -- each a judgement about one line, from the
# 2026-10-02 code reading. Prune an entry when its line changes.
CAVEATS = {
    'D2': '"sends them your booking link": only when the deployment sets CALENDLY_BOOKING_URL.',
    'BEN-BOOK': '"your booking link": only when the deployment sets CALENDLY_BOOKING_URL.',
    'BEN-DECK': '"the moment a sponsor asks": the deck goes out once the visitor gives an email address.',
    'A2': '"Reply yes and I\'ll set it up" skips the one-pager\'s step "You share your site URL".',
}
unmapped = sorted(c for c, r in active.items() if r['slot'] in ('description', 'angle', 'benefit', 'proof', 'ask') and c not in CLAIMS)
check('every active line has a claim-map entry (else this table is stale)', not unmapped, str(unmapped) if unmapped else '%d lines mapped' % len([c for c in CLAIMS if c in active]))
unsupported = []
stale = []
for code in sorted(c for c in CLAIMS if c in active):
    body_n = norm(active[code]['body'])
    if not CLAIMS[code]:
        print('  %-11s (asserts nothing about Nova)' % code)
    for phrase, evidence in CLAIMS[code]:
        pn, en = norm(phrase), norm(evidence)
        if pn not in body_n:
            stale.append((code, phrase))
        if en not in OPN:
            unsupported.append((code, phrase, evidence))
        print('  %-11s %-44s %s' % (code, '"%s"' % phrase, ('one-pager: "%s"' % evidence) if en in OPN else 'NOT IN THE ONE-PAGER (looked for "%s")' % evidence))
live_bullets = 0
capture = False
for p in OP_PARAS:
    if p.strip() == 'Live today':
        capture = True
        continue
    if capture:
        if re.match(r'(NoblePath|Vertex)', p):
            live_bullets += 1
        else:
            break
check('PR-BOTH "two CROs": the one-pager lists exactly two under "Live today"', live_bullets == 2 or 'PR-BOTH' not in active, '%d listed' % live_bullets)
digits = re.sub(r'\D', '', OP)
sig_ok = all(t in OPN for t in ('abdullah amir', 'founder amitrix labs')) and '923178485713' in digits
check('signature (name, title, phone digits) matches the one-pager footer', sig_ok)
check('claim map is current: every mapped phrase is present in its live line', not stale, str(stale) if stale else '')
check('phrase-level unsupported claims: none', not unsupported, str(unsupported) if unsupported else '0 of %d claims' % sum(len(v) for c, v in CLAIMS.items() if c in active))
print('  SEMANTIC caveats, not settled by the phrase table (%d):' % len([c for c in CAVEATS if c in active]))
for c, t in CAVEATS.items():
    if c in active:
        print('       %-9s %s' % (c, t))

# ============================================================================================================
print('\n' + '=' * 92)
print('D. THE LIVE FIRST-TOUCH DRAFTS  (status pending or approved, subject + body)')
print('=' * 92)
over_list = []
prospect_sponsor_drafts = []
pharma_drafts = []
dead_codes = []
settled_drafts = []
repeat_drafts = []


def repeats(codes):
    """Two claim codes that share a capability (skill v3 section 3, No repeats)."""
    seen, out = {}, []
    for c in codes:
        for cap in (lib.get(c, {}).get('capabilities') or []):
            if cap in seen and seen[cap] != c:
                out.append('%s+%s:%s' % (seen[cap], c, cap))
            seen[cap] = c
    return out
if not live:
    print('       no live first-touch drafts in the queue -- sections D and E have nothing to audit')
else:
    print('       %d live first-touch draft(s) across %d lead(s)' % (len(live), len({d['lead_id'] for d in live})))
    hdr = '  %-4s %-4s %-8s %-8s %-36s %-5s %-9s %s'
    print(hdr % ('id', 'lead', 'channel', 'status', 'claims recorded in variant', 'words', '(<=%d)' % CEILING, 'tags'))
for d in live:
    text = (d['subject'] or '') + '\n' + d['sendable']
    used = codes_of(d['variant'])
    wc = body_words(d['sendable'])
    d['words'] = wc
    if calls_prospect_sponsor(text):
        prospect_sponsor_drafts.append((d['id'], calls_prospect_sponsor(text)))
    if PHARMA_TEAM.search(text):
        pharma_drafts.append(d['id'])
    gone = [c for c in used if c not in active]
    if gone:
        dead_codes.append((d['id'], gone))
    if settled_breaks(text):
        settled_drafts.append((d['id'], settled_breaks(text)))
    if repeats(used):
        repeat_drafts.append((d['id'], repeats(used)))
    if wc > CEILING:
        over_list.append(d)
    print(hdr % (d['id'], d['lead_id'], d['channel'], d['status'], '.'.join(used), wc, 'OVER' if wc > CEILING else 'ok',
                 ','.join(tags_of(d['variant'])) or '-'))
if live:
    check('no draft calls the prospect a sponsor ("You\'re sponsoring", "with you as the sponsor", ...)', not prospect_sponsor_drafts,
          str(prospect_sponsor_drafts) if prospect_sponsor_drafts else '0 of %d drafts' % len(live))
    check('no draft says "pharma team" (v2 wording)', not pharma_drafts, str(pharma_drafts) if pharma_drafts else '')
    check('every claim code a draft records is an active library line', not dead_codes,
          str(dead_codes) if dead_codes else '')
    check('no draft mentions the recording', not any(re.search(r'recording|90-second', d['sendable'], re.I) for d in live))
    check('no draft breaks a claim settled on 2026-10-02 (SOPs, Nova booking, "another dashboard")', not settled_drafts,
          str(settled_drafts) if settled_drafts else '0 of %d drafts' % len(live))
    check('no draft\'s claim codes repeat a capability (skill v3 section 3)', not repeat_drafts,
          str(repeat_drafts) if repeat_drafts else '0 of %d drafts' % len(live))
    check('every draft is within the %d-word ceiling' % CEILING, not over_list,
          '; '.join('draft %s = %d words' % (d['id'], d['words']) for d in over_list) if over_list else '%d of %d within' % (len(live), len(live)))

# ============================================================================================================
print('\n' + '=' * 92)
print('E. EACH LIVE DRAFT AGAINST THE DRAFT IT REPLACED')
print('=' * 92)
prev_of = {}
for d in live:
    earlier = [h for h in history if h['lead_id'] == d['lead_id'] and h['channel'] == d['channel'] and h['id'] < d['id']]
    prev_of[d['id']] = earlier[-1] if earlier else None
if not live:
    print('       nothing to compare')
else:
    print('  %-5s %-8s %-4s -> %-4s  %-32s -> %-36s %s' % ('lead', 'channel', 'id', 'id', 'claims before', 'claims now', 'words'))
    for d in live:
        b = prev_of[d['id']]
        if not b:
            print('  %-5s %-8s %-4s -> %-4s  (first draft for this lead and channel)' % (d['lead_id'], d['channel'], '-', d['id']))
            continue
        print('  %-5s %-8s %-4s -> %-4s  %-32s -> %-36s %2d -> %d' % (d['lead_id'], d['channel'], b['id'], d['id'],
              '.'.join(codes_of(b['variant'])) or '-', '.'.join(codes_of(d['variant'])), body_words(b['body']), d['words']))

# ============================================================================================================
print('\n' + '=' * 92)
print('F. QUEUE INTEGRITY')
print('=' * 92)
counts = {}
for h in history:
    counts[h['status']] = counts.get(h['status'], 0) + 1
print('       first-touch drafts by status: %s' % ', '.join('%s %d' % (k, counts[k]) for k in sorted(counts)))
dupes = {}
for d in live:
    dupes.setdefault((d['lead_id'], d['channel']), []).append(d['id'])
dupes = {k: v for k, v in dupes.items() if len(v) > 1}
check('at most one live (pending or approved) first-touch draft per lead and channel', not dupes, str(dupes) if dupes else '')

# ============================================================================================================
print('\n' + '=' * 92)
print('G. THE LIVE FOLLOW-UP DRAFTS  (skill v3 section 8; the new note only -- the quoted first email was already sent)')
print('=' * 92)


def followup_note(body):
    """The new text: after the greeting, before the opt-out (the quoted first email comes after the signature)."""
    paras = (body or '').replace('\r\n', '\n').split('\n\n')[1:]
    out = []
    for p in paras:
        if p.startswith("If this isn't relevant"):
            break
        out.append(p)
    return ' '.join(out)


fu_bad = []
if not followups:
    print('       no live follow-up drafts in the queue')
else:
    print('  %-4s %-4s %-8s %-28s %-5s %s' % ('id', 'lead', 'status', 'claims recorded in variant', 'words', 'tags'))
for d in followups:
    note = followup_note(d['edited_body'] or d['body'])
    n = int(re.match(r'follow-up-(\d+)', d['variant']).group(1))
    used = [c for c in (d['variant'].split('/', 1)[1].split('+', 1)[0].split('.') if '/' in d['variant'] else []) if c]
    wc = words(note)
    lo, hi = (FU_LIMITS['FOLLOW_UP_1_MIN'], FU_LIMITS['FOLLOW_UP_1_MAX']) if n == 1 else (0, FU_LIMITS['FOLLOW_UP_2_MAX'])
    problems = []
    if calls_prospect_sponsor(note):
        problems.append('calls the prospect a sponsor')
    if PHARMA_TEAM.search(note):
        problems.append('"pharma team"')
    problems += settled_breaks(note)
    problems += ['dead code %s' % c for c in used if c not in active]
    problems += ['repeat %s' % r for r in repeats(used)]
    if not lo <= wc <= hi:
        problems.append('%d words, outside %d-%d' % (wc, lo, hi))
    if problems:
        fu_bad.append((d['id'], problems))
    print('  %-4s %-4s %-8s %-28s %-5s %s' % (d['id'], d['lead_id'], d['status'], '.'.join(used), wc,
                                             ','.join(tags_of(d['variant'])) or '-'))
if followups:
    check('every live follow-up: no prospect-sponsor, no "pharma team", no settled claim broken, live codes, '
          'no repeated capability, within its length', not fu_bad, str(fu_bad) if fu_bad else '%d of %d' % (len(followups), len(followups)))

print('\n' + '=' * 92)
print('THE THREE CONFIRMATIONS')
print('=' * 92)
print('  1. text calling the prospect a sponsor : drafts %d, active library lines %d, one-pager %d   ("pharma team" left: drafts %d, library %d, one-pager %d)' % (
    len(prospect_sponsor_drafts), sum(bool(calls_prospect_sponsor(r['body'])) for r in active.values()), len(calls_prospect_sponsor(OP)),
    len(pharma_drafts), sum(bool(PHARMA_TEAM.search(r['body'] or '')) for r in active.values()), len(pharma_op)))
print('  2. unsupported claims remaining        : %d phrase-level   (+ %d semantic caveats: %s)' % (
    len(unsupported), len([c for c in CAVEATS if c in active]), ', '.join(c for c in CAVEATS if c in active)))
print('  3. word counts within the ceiling      : %d of %d drafts%s' % (len(live) - len(over_list), len(live),
      ('; OVER: ' + ', '.join('draft %s = %d words' % (d['id'], d['words']) for d in over_list)) if over_list else ''))
print('\n' + '=' * 92)
print('RESULT: %s' % ('ALL CHECKS PASS' if not FAILS else '%d CHECK(S) FAILED: %s' % (len(FAILS), FAILS)))
print('=' * 92)
sys.exit(1 if FAILS else 0)

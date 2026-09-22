"""Drafts-vs-one-pager consistency check. READ-ONLY.

    python n8n/drafting/audit_drafts_vs_onepager.py

Reads the live claims library and the live first-touch queue (pending and
approved drafts) from Postgres, and nova-one-pager.docx from the repo root, and
checks that they agree. It changes nothing.

Run it after a claims_library edit, after a redraft, and after the one-pager is
regenerated. It exists because a draft can be clean on its own and still
contradict the asset it leads to. The 2026-09-21 audit found two ways:

  * one email used "sponsor" for both the CRO (a "You're sponsoring ..." hook)
    and the pharma company (a library line), and
  * an outcome line promised "in seconds" where the one-pager only says "in
    real time".

What it checks
  A  the one-pager: no placeholders or internal notes, and no "sponsor" at all
  B  the library: no active line says "sponsor" or makes a seconds-level claim
  C  every active line's claims against the one-pager text (CLAIMS, below)
  D  each live draft: no collision, library lines identical to the table, the
     word ceiling, the subject line
  E  which library lines each draft moved to, against the draft it replaced
  F  queue integrity

Keeping the claim map current. CLAIMS maps each active library line to pairs of
(phrase in the line, evidence in the one-pager). It is validated against the
live table on every run: a line that was edited so that a mapped phrase is gone,
or a new active line with no entry, FAILS instead of being skipped. When that
happens, update CLAIMS -- and find the evidence by reading the one-pager, not by
writing whatever phrase makes the check pass.

Exit code 0 when every check passes, 1 otherwise. A draft over the word ceiling
fails on purpose even when the assembler tagged it `long`: the tag is how the
reviewer sees it, not a reason to stop counting it.

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


def assembler_ceiling():
    """The ceiling the assembler enforces, read from the shipped source rather than retyped."""
    with io.open(os.path.join(HERE, 'code_assemble.js'), encoding='utf-8') as fh:
        m = re.search(r'const MAX_WORDS = (\d+);', fh.read())
    if not m:
        raise SystemExit('const MAX_WORDS not found in code_assemble.js -- this script needs updating')
    return int(m.group(1))


MAX_WORDS = assembler_ceiling()   # code_assemble.js flags a body only when it is MORE than this


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
    core = variant.split('/', 1)[1].split('+', 1)[0] if '/' in variant else ''
    return [c for c in core.split('.') if c]


def core_paragraphs(body):
    """The parts the ceiling counts: everything after the greeting except the opt-out and the signature."""
    paras = body.replace('\r\n', '\n').split('\n\n')[1:]
    return [p for p in paras if not p.startswith("If this isn't relevant") and not p.startswith('Abdullah Amir')]


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
lib = {r['code']: r for r in psql("SELECT row_to_json(t) FROM (SELECT code, slot, body, active, confirmed FROM claims_library) t;")}
active = {c: r for c, r in lib.items() if r['active']}
FIRST_TOUCH = "coalesce(d.variant,'') NOT LIKE 'follow-up-%' AND coalesce(d.variant,'') NOT LIKE 'low-context%'"
LIVE_SQL = ("SELECT row_to_json(t) FROM (SELECT d.id, d.lead_id, l.country, d.channel, d.status, d.variant, d.subject, d.body, d.edited_body "
            "FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE d.status IN ('pending','approved') AND " + FIRST_TOUCH + " ORDER BY d.lead_id, d.channel, d.id) t;")
HISTORY_SQL = ("SELECT row_to_json(t) FROM (SELECT d.id, d.lead_id, d.channel, d.status, d.variant, d.body FROM drafts d "
               "WHERE " + FIRST_TOUCH + " ORDER BY d.id) t;")
live = psql(LIVE_SQL)
history = psql(HISTORY_SQL)
for d in live:
    d['sendable'] = d['edited_body'] or d['body']      # what Workflow 6 would send
    d['human_edited'] = bool(d['edited_body'])


# ============================================================================================================
print('=' * 92)
print('A. ONE-PAGER HYGIENE  (%s)' % DOCX)
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
spons_op = re.findall(r'\bsponsor\w*', OP, re.I)
check('the word "sponsor" (any form) appears nowhere in the one-pager', not spons_op, '%d occurrence(s): %s' % (len(spons_op), spons_op) if spons_op else '0 occurrences')
print('       one-pager terms for the pharma-side party: "pharma team" x%d, "pharma-team" x%d' % (OP.count('pharma team'), OP.count('pharma-team')))

# ============================================================================================================
print('\n' + '=' * 92)
print('B. LIBRARY: TERMINOLOGY AND THE SECONDS CLAIM  (live claims_library, active rows)')
print('=' * 92)
for code in sorted(active):
    b = active[code]['body'] or ''
    if re.search(r'\bsponsor\w*', b, re.I):
        check('%s has no "sponsor"' % code, False, b)
check('no ACTIVE library line contains "sponsor" in any form', not any(re.search(r'\bsponsor\w*', r['body'] or '', re.I) for r in active.values()))
check('no ACTIVE library line makes a seconds-level claim ("second(s)")', not any(re.search(r'\bseconds?\b', r['body'] or '', re.I) for r in active.values()))
check('A3 (offers a recording that does not exist yet) is inactive', not lib['A3']['active'])
print('       "in real time" is used by: %s' % ', '.join(c for c, r in sorted(active.items()) if 'in real time' in (r['body'] or '')))

# ============================================================================================================
print('\n' + '=' * 92)
print('C. WHAT EACH ACTIVE LIBRARY LINE ASSERTS  vs  THE ONE-PAGER  (phrase in the line -> evidence in the one-pager)')
print('=' * 92)
# code -> [(phrase as it appears in the LIVE line, evidence phrase that must appear in the one-pager)]
CLAIMS = {
    'P1': [('pharma team', 'pharma team'), ('11pm', '11pm'), ('next cro on their list', 'next cro on their list')],
    'P2': [('pharma team', 'pharma team'), ('already moved on', 'already moved to the next cro')],
    'P3': [('pharma team', 'pharma team'), ('qualified lead', 'qualified leads'), ('contact form', 'contact form'), ('until morning', 'the next morning')],
    'O1': [('answer from your own material', 'using only your material'), ('qualify the lead', 'qualifies the inquiry'),
           ('wherever you already work', 'wherever your team already works'), ('no new dashboard', "doesn't force a new dashboard")],
    'O2': [('pharma team inquiry', 'pharma team questions'), ('11pm', '11pm'), ('answered', 'it answers'), ('in real time', 'in real time'),
           ('qualified', 'qualifies the inquiry'), ('not the next morning', 'the next morning'),
           ('lands in the tools you already use', 'wherever your team already works')],
    'O3': [('answers pharma teams', 'answers pharma team questions'), ('your own sops and service pages', 'your own sops service pages'),
           ('hands your team a qualified lead', 'hands your team a ready lead'), ('in your system not ours', 'hand qualified leads to wherever your team already works')],
    'PR-TR': [('noblepath', 'noblepath'), ('oncology cro', 'oncology focused cro'), ('türkiye', 'türkiye'), ('live at', 'live today')],
    'PR-MX': [('vertex clinical research', 'vertex clinical research'), ('mexico', 'mexico'), ('live at', 'live today')],
    'PR-BOTH': [('türkiye', 'türkiye'), ('mexico', 'mexico'), ('live at', 'live today')],   # "two CROs" is checked by counting the bullets below
    'A1': [('48 hour demo', '48 hour demo'), ('built on your own material', 'built directly on your own site content and service pages')],
    'A2': [('demo on your material', 'built directly on your own site content and service pages'), ('48 hours', 'within 48 hours')],
}
# Semantic caveats the phrase table cannot settle. Carried over from the 2026-09-21 review; each is a judgement
# about one line, so prune an entry when its line changes.
CAVEATS = {
    'P1': '"shortlists" has no counterpart in the one-pager (it says "evaluating"). A scenario in a question, not an assertion.',
    'A1': '"Reply yes and I\'ll set it up" skips the one-pager\'s step "You share your site URL and any service documentation".',
    'A2': '"48 hours of our time" reads as effort; the one-pager says turnaround ("Within 48 hours, you get a live link").',
}
unmapped = sorted(c for c, r in active.items() if r['slot'] in ('problem', 'outcome', 'proof', 'ask') and c not in CLAIMS)
check('every active problem/outcome/proof/ask line has a claim-map entry (else this table is stale)', not unmapped, str(unmapped) if unmapped else '%d lines mapped' % len(CLAIMS))
unsupported = []
stale = []
for code in sorted(CLAIMS, key=lambda c: ('POPA'.find(c[0]), c)):
    if code not in active:
        continue
    body_n = norm(active[code]['body'])
    for phrase, evidence in CLAIMS[code]:
        pn, en = norm(phrase), norm(evidence)
        in_line = pn in body_n
        in_op = en in OPN
        if not in_line:
            stale.append((code, phrase))
        if not in_op:
            unsupported.append((code, phrase, evidence))
        print('  %-8s %-40s %s' % (code, '"%s"' % phrase, ('one-pager: "%s"' % evidence) if in_op else 'NOT IN THE ONE-PAGER (looked for "%s")' % evidence))
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
print('  carried over, SEMANTIC, not settled by the phrase table (%d):' % len([c for c in CAVEATS if c in active]))
for c, t in CAVEATS.items():
    if c in active:
        print('       %-3s %s' % (c, t))

# ============================================================================================================
print('\n' + '=' * 92)
print('D. THE LIVE FIRST-TOUCH DRAFTS  (status pending or approved, subject + body)')
print('=' * 92)
SPON = re.compile(r'\bsponsor\w*', re.I)
CRO_FORM = re.compile(r"\byou(?:['’]re| are)\s+sponsoring\b", re.I)   # the hook's own-trial phrasing: about the prospect
tot_cro = tot_pharma = tot_subj = collisions = stale_lines = 0
over_list = []
at_ceiling = []
if not live:
    print('       no live first-touch drafts in the queue -- sections D and E have nothing to audit')
else:
    print('       %d live first-touch draft(s) across %d lead(s)' % (len(live), len({d['lead_id'] for d in live})))
    hdr = '  %-4s %-4s %-8s %-19s %-5s %-12s %-7s %-7s %s'
    print(hdr % ('id', 'lead', 'channel', 'lines', 'words', 'ceiling', 'cro-side', 'pharma-', 'library text vs live table'))
    print(hdr % ('', '', '', '', '', '(<=%d)' % MAX_WORDS, '"sponsor"', 'side "sp."', ''))
for d in live:
    used = codes_of(d['variant'])
    core_text = ' '.join(core_paragraphs(d['sendable']))
    wc = words(core_text)
    d['words'] = wc
    total = len(SPON.findall(core_text))
    cro = len(CRO_FORM.findall(core_text))
    pharma = total - cro
    subj = len(SPON.findall(d['subject'] or ''))
    coll = cro > 0 and (pharma > 0 or subj > 0)
    # identity against the table is only meaningful for text the assembler wrote; a human edit may differ on purpose
    missing = [] if d['human_edited'] else [c for c in used if not (c in lib and (lib[c]['body'] or '').strip() and (lib[c]['body'] or '').strip() in core_text)]
    tot_cro += cro
    tot_pharma += pharma
    tot_subj += subj
    collisions += coll
    stale_lines += len(missing)
    if wc > MAX_WORDS:
        over_list.append(d)
    if wc == MAX_WORDS:
        at_ceiling.append(d['id'])
    print(hdr % (d['id'], d['lead_id'], d['channel'], '.'.join(used), wc, ('AT %d' % MAX_WORDS if wc == MAX_WORDS else ('OVER' if wc > MAX_WORDS else 'ok')),
                 cro, pharma + subj, ('human-edited: identity not checked' if d['human_edited'] else
                                      ('all %d lines identical to the live table' % len(used) if not missing else 'STALE/MISSING: ' + ','.join(missing)))))
if live:
    check('collisions (one email using "sponsor" for both the CRO and the pharma side): 0', collisions == 0, '%d of %d drafts' % (collisions, len(live)))
    check('pharma-side "sponsor" in any draft body: 0', tot_pharma == 0, '%d' % tot_pharma)
    check('"sponsor" in any draft SUBJECT: 0', tot_subj == 0, '%d' % tot_subj)
    check('every library line in every draft is byte-identical to the live table row', stale_lines == 0,
          '' if stale_lines == 0 else '%d line(s) differ -- the table was edited after these drafts were written; redraft the lead or patch the line' % stale_lines)
    check('draft vocabulary matches the one-pager (both say "pharma team"/"pharma-team", neither says "sponsor" for that party)',
          tot_pharma == 0 and not spons_op and any('pharma' in d['sendable'] for d in live))
    print('       remaining "sponsor" uses in drafts: %d, all CRO-side, all in a hook of the form "You\'re sponsoring ..." about the prospect\'s own registered trial' % tot_cro if tot_pharma == 0
          else '       "sponsor" uses in drafts: %d CRO-side, %d NOT in the CRO-side form' % (tot_cro, tot_pharma))
    check('every draft that says "sponsor" says it only as "You\'re sponsoring" (the CRO\'s own trial)',
          all(all(m.group(0).lower() == 'sponsoring' for m in SPON.finditer(d['sendable'])) and len(SPON.findall(d['sendable'])) == len(CRO_FORM.findall(d['sendable']))
              for d in live if SPON.search(d['sendable'])))
    check('no draft mentions the recording, and none uses A3', not any(re.search(r'recording|90-second', d['sendable'], re.I) or 'A3' in codes_of(d['variant']) for d in live))
    prev_of = {}
    for d in live:
        earlier = [h for h in history if h['lead_id'] == d['lead_id'] and h['channel'] == d['channel'] and h['id'] < d['id']]
        prev_of[d['id']] = earlier[-1] if earlier else None
    print('  ceiling: code_assemble.js flags a body only when it is MORE than %d words, so exactly %d is inside it.' % (MAX_WORDS, MAX_WORDS))
    print('           drafts at exactly %d: %s' % (MAX_WORDS, at_ceiling or 'none'))
    within = len(live) - len(over_list)
    notes = []
    for d in over_list:
        prev = prev_of[d['id']]
        first = core_paragraphs(d['sendable'])[0]
        hook_w = words(first) - max((words(lib[c]['body']) for c in codes_of(d['variant']) if c in lib and lib[c]['slot'] == 'problem'), default=0)
        notes.append('draft %s (lead %s %s) is %d words%s%s%s' % (
            d['id'], d['lead_id'], d['channel'], d['words'],
            ' (%d in the draft it replaced)' % body_words(prev['body']) if prev else '',
            ', tagged `long`' if 'long' in d['variant'].split('+', 1)[-1].split(',') else ', NOT tagged `long`',
            ' -- its hook alone is %d words (drafting README, Known gaps: a long trial title)' % hook_w if hook_w >= 20 else ''))
    check('every draft is within the %d-word ceiling' % MAX_WORDS, not over_list,
          '%d of %d within; %s' % (within, len(live), '; '.join(notes)) if over_list else '%d of %d within' % (within, len(live)))

# ============================================================================================================
print('\n' + '=' * 92)
print('E. WHICH DRAFTS MOVED BETWEEN LIBRARY LINES  (the draft each one replaced -> now; slots: problem.outcome.proof.ask)')
print('=' * 92)
moved = 0
if not live:
    print('       nothing to compare')
else:
    print('  %-5s %-8s %-4s -> %-4s  %-19s -> %-19s %-7s %s' % ('lead', 'channel', 'id', 'id', 'lines before', 'lines now', 'words', 'moved'))
    for d in live:
        b = prev_of[d['id']]
        if not b:
            print('  %-5s %-8s %-4s -> %-4s  (first draft for this lead and channel)' % (d['lead_id'], d['channel'], '-', d['id']))
            continue
        nb, na = codes_of(b['variant']), codes_of(d['variant'])
        diff = [('%s->%s' % (x, y)) for x, y in zip(nb, na) if x != y] if nb and len(nb) == len(na) else (['(earlier draft predates the library)'] if not nb else ['(different shape)'])
        moved += bool(diff)
        print('  %-5s %-8s %-4s -> %-4s  %-19s -> %-19s %2d->%-3d %s' % (d['lead_id'], d['channel'], b['id'], d['id'], '.'.join(nb) or '-', '.'.join(na), body_words(b['body']), d['words'], ', '.join(diff) if diff else 'no'))
    print('  -> %d of %d drafts moved to a different library line' % (moved, len(live)))

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

print('\n' + '=' * 92)
print('THE THREE CONFIRMATIONS')
print('=' * 92)
print('  1. collisions remaining           : %d   (pharma-side "sponsor" in drafts: %d; in subjects: %d; in the one-pager: %d; in active library lines: %d)' % (
    collisions, tot_pharma, tot_subj, len(spons_op), sum(bool(re.search(r'\bsponsor\w*', r['body'] or '', re.I)) for r in active.values())))
print('  2. unsupported claims remaining   : %d phrase-level   (+ %d carried-over semantic caveats: %s)' % (
    len(unsupported), len([c for c in CAVEATS if c in active]), ', '.join(c for c in CAVEATS if c in active)))
print('  3. word counts within the ceiling : %d of %d drafts%s' % (len(live) - len(over_list), len(live),
      ('; OVER: ' + ', '.join('draft %s = %d words' % (d['id'], d['words']) for d in over_list)) if over_list else ''))
print('\n' + '=' * 92)
print('RESULT: %s' % ('ALL CHECKS PASS' if not FAILS else '%d CHECK(S) FAILED: %s' % (len(FAILS), FAILS)))
print('=' * 92)
sys.exit(1 if FAILS else 0)

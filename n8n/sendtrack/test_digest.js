// Unit tests for the Daily Digest -- code_digest.js (Build Digest), migration 014.
//
//   node test_digest.js
//
// When it is due (once per operator day, from digest_hour, only with an
// operator address), and what it says: what auto-approved, what went out, and
// every pending draft with the reason it is waiting for a person.
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const DIGEST = path.join(__dirname, 'code_digest.js');
const bake = (s) => s.replace('__SENDER_ZONE__', JSON.stringify('Asia/Karachi')).replace('__SENDER_OFFSET__', '300');
const { buildDigest, kindOf, local } = extractFunctions(DIGEST, '// Node body', ['buildDigest', 'kindOf', 'local'], bake);

const t = runner('Workflow 6 -- Daily Digest (migration 014)');

const ROW = {
  digest_day: '2026-10-05', local_hour: 8,
  covers_from: '2026-10-04T03:00:00.000Z', covers_to: '2026-10-05T03:00:00.000Z',
  already_sent: false, operator_email: 'operator@example.org', auto_approve_email: true,
  auto_approved: [
    { draft_id: 105, lead_id: 7, company_name: 'KLIXAR', domain: 'klixar.com', country: 'Argentina', channel: 'email',
      variant: 'follow-up-2/A1', status: 'approved', approved_at: '2026-10-04T20:32:00.000Z' },
  ],
  sent: [
    { draft_id: 99, lead_id: 7, company_name: 'KLIXAR', domain: 'klixar.com', to_addr: 'hello@klixar.com',
      variant: 'follow-up-1/BEN-SEE.A1', approved_by: 'human', sent_at: '2026-10-04T12:10:00.000Z' },
  ],
  held: [
    { draft_id: 103, lead_id: 50, company_name: 'Innovate Research', domain: 'innovate-research.com', channel: 'linkedin',
      variant: 'no-profile/D1.ANG-HOURS.A2', hold_reason: 'linkedin: always reviewed and sent by hand',
      hold_class: 'linkedin', class_known: true, needs_person: false,
      created_at: '2026-10-02T11:13:55.000Z', new: false },
    { draft_id: 106, lead_id: 91, company_name: 'BiTrial', domain: 'bitrial.hu', channel: 'email',
      variant: 'follow-up-2/A2', hold_reason: 'claim-check: widened "so you know exactly what came in" (BEN-SEE)',
      hold_class: 'claim-check', class_known: true, needs_person: true,
      created_at: '2026-10-04T15:00:00.000Z', new: true },
    { draft_id: 97, lead_id: 78, company_name: 'Vedic', domain: 'vediclifesciences.com', channel: 'email',
      variant: 'low-context/role-inbox', hold_reason: null, hold_class: 'none', class_known: true, needs_person: false,
      created_at: '2026-10-01T19:50:00.000Z', new: false },
  ],
};
const CFG = { digest_hour: 8 };
const with_ = (over) => Object.assign({}, ROW, over);

let d = buildDigest(ROW, CFG);
t.check('due: the operator day\'s first tick at or after digest_hour, with an address, not yet sent',
  [d.send, d.reason, d.notify_to], [true, 'due', 'operator@example.org']);
t.check('not before the hour', [buildDigest(with_({ local_hour: 7 }), CFG).send, buildDigest(with_({ local_hour: 7 }), CFG).reason],
  [false, 'before-digest-hour']);
t.check('later the same day still due if it has not gone out (a sleeping host catches up)',
  buildDigest(with_({ local_hour: 22 }), CFG).send, true);
t.check('once per operator day', [buildDigest(with_({ already_sent: true }), CFG).send,
  buildDigest(with_({ already_sent: true }), CFG).reason], [false, 'already-sent-today']);
t.check('no operator address, no digest', [buildDigest(with_({ operator_email: null }), CFG).send,
  buildDigest(with_({ operator_email: '' }), CFG).reason], [false, 'no-operator-address']);

t.check('the subject counts all three', d.subject, '[Nova Scout] Digest 2026-10-05: 1 auto-approved, 1 sent, 3 held');
t.check('times are on the operator\'s clock', local('2026-10-04T20:32:00.000Z', 'PKT'), '2026-10-05 01:32 PKT');
t.check('the window is stated', d.text.indexOf('Covers 2026-10-04 08:00 PKT to 2026-10-05 08:00 PKT.') !== -1, true);
t.check('the flag is stated', d.text.indexOf('Auto-approval of email drafts: ON') !== -1, true);
t.check('... and OFF when it is off', buildDigest(with_({ auto_approve_email: false }), CFG).text.indexOf('Auto-approval of email drafts: OFF') !== -1, true);
t.check('each auto-approval: draft, company, domain, country, kind, when, what became of it',
  d.text.indexOf('  #105  KLIXAR (klixar.com, Argentina)  follow-up 2  [none -]  approved 2026-10-05 01:32 PKT' +
    '  -- now approved') !== -1, true);
t.check('... under a heading that says no person read them and how to stop one',
  d.text.indexOf('AUTO-APPROVED (1) -- no person read these.') !== -1 && d.text.indexOf('reject it in the review queue to stop it') !== -1, true);
t.check('each send: recipient, kind, when, and who approved it',
  d.text.indexOf('  #99  KLIXAR -> hello@klixar.com  follow-up 1  [none -]  2026-10-04 17:10 PKT  approved by human') !== -1, true);
t.check('held: counted by reason class, most first, new drafts counted in the heading',
  [d.text.indexOf('HELD (3, 1 new) -- the exceptions queue, by reason.') !== -1,
   d.text.indexOf('     1  claim-check\n     1  linkedin\n     1  none\n') !== -1],
  [true, true]);
t.check('only a hold a person can decide is listed one by one, with its reason on its own line',
  [d.text.indexOf('NEEDS YOU (1) -- held for a decision about the email itself.') !== -1,
   d.text.indexOf('  #106  BiTrial  email follow-up 2  [new]  since 2026-10-04 20:00 PKT\n        claim-check: widened "so you know exactly what came in" (BEN-SEE)') !== -1,
   d.text.indexOf('#103') === -1, d.text.indexOf('#97') === -1],
  [true, true, true, true]);
// One-time codes on held email drafts, 2026-10-09 (Section 9, Workflow 7); only
// for a hold a person can decide since 2026-10-30 (migration 028). Load Digest
// issues them (and only on the tick that sends), so what is tested here is that
// the digest shows the code it was handed and says nothing when it was handed
// none.
const CODED = with_({
  held: ROW.held.map((h) => (h.draft_id === 106 ? Object.assign({}, h, { code: 'NS-2345678ABC' }) : h)),
});
const coded = buildDigest(CODED, CFG);
t.check('a held email draft carries its three commands, with the code',
  coded.text.indexOf('        reply with:  APPROVE NS-2345678ABC   |   REJECT NS-2345678ABC   |   '
    + 'EDIT NS-2345678ABC <your text>') !== -1, true);
t.check('... and the rules of the code are stated once, not per draft, and promise no reminder',
  [coded.text.split('work once each and expire 48 hours').length - 1,
    coded.text.indexOf('Those 1 code(s) work once each') !== -1,
    coded.text.indexOf('remind you') === -1,
    coded.text.indexOf('No reminder is sent') !== -1], [1, true, true, true]);
t.check('exactly one command line in the whole digest',
  [(coded.text.match(/reply with:/g) || []).length, (coded.text.match(/APPROVE NS-/g) || []).length], [1, 1]);
t.check('Record Digest is told which codes went out',
  [coded.record.coded, coded.record.summary.held_coded], [['NS-2345678ABC'], 1]);
t.check('no codes at all -> no code paragraph', d.text.indexOf('work once each') === -1, true);

// The 2026-10-30 queue: 161 Overture source holds, 9 no-address, 4 operator
// holds. Nothing a person decides -> no per-draft line, no code.
{
  const many = (n, base) => Array.from({ length: n }, (_, i) => Object.assign({}, base, { draft_id: base.draft_id + i }));
  const SRC = 'db-guard: source sites-overture is held by niche research_site (precision audit, 2026-10-30)';
  const q = buildDigest(with_({ held: [].concat(
    many(161, { draft_id: 700, lead_id: 1, company_name: 'Overture site', channel: 'email', variant: 'template/A/areas/role-inbox',
      hold_reason: SRC, hold_class: 'db-guard: source sites-overture is held by niche research_site', class_known: true,
      needs_person: false, created_at: '2026-10-29T19:00:00.000Z', new: false }),
    many(9, { draft_id: 900, lead_id: 2, company_name: 'No address', domain: 'na.example', channel: 'email',
      variant: 'template/B/city/unaddressed+no-address',
      hold_reason: 'no-address: no deliverable email address on file, so Send could never deliver it',
      hold_class: 'no-address', class_known: true, needs_person: false, created_at: '2026-10-29T19:00:00.000Z', new: false }),
    many(4, { draft_id: 950, lead_id: 3, company_name: 'Op hold', channel: 'email', variant: 'template/A/trials/role-inbox',
      hold_reason: 'operator hold 2026-10-30: parent organisation\'s domain', hold_class: 'operator hold', class_known: true,
      needs_person: false, created_at: '2026-10-29T19:00:00.000Z', new: false })) }), CFG);
  t.check('the 2026-10-30 queue: counts only, no draft line, no code, NEEDS YOU (0)',
    [q.text.indexOf('   161  db-guard: source sites-overture is held by niche research_site\n') !== -1,
     q.text.indexOf('     9  no-address   (CONTACT <lead domain> <email address> gives one an address)') !== -1,
     q.text.indexOf('     4  operator hold\n') !== -1,
     q.text.indexOf('NEEDS YOU (0)') !== -1, /#\d+  (Overture|No address|Op hold)/.test(q.text),
     (q.text.match(/APPROVE NS-/g) || []).length, q.record.coded.length, q.record.new_classes],
    [true, true, true, true, false, 0, 0, []]);
  t.check('... and the subject is unchanged in shape (a Gmail filter matches it)',
    q.subject, '[Nova Scout] Digest 2026-10-05: 1 auto-approved, 1 sent, 174 held');

  // A hold class never seen before is flagged on a line of its own and not coded.
  const n = buildDigest(with_({ held: [{ draft_id: 1001, lead_id: 9, company_name: 'X', channel: 'email', variant: 'template/A/city/x',
    hold_reason: 'db-guard: some new problem', hold_class: 'db-guard: some new problem', class_known: false, needs_person: false,
    created_at: '2026-10-29T19:00:00.000Z', new: true }] }), CFG);
  t.check('a new hold class: [NEW] on its count, a line of its own with a sample, recorded, never coded',
    [n.text.indexOf('     1  db-guard: some new problem  [NEW]') !== -1,
     n.text.indexOf('NEW HOLD REASON -- never seen before.') !== -1,
     n.text.indexOf('  db-guard: some new problem  (1)  e.g. "db-guard: some new problem"') !== -1,
     n.record.new_classes, (n.text.match(/APPROVE NS-/g) || []).length],
    [true, true, true, ['db-guard: some new problem'], 0]);

  // The cap: at most held_cap listed one by one, then "+N more".
  const p = buildDigest(with_({ held_cap: 2, held: many(5, { draft_id: 1100, lead_id: 9, company_name: 'Off', channel: 'email',
    variant: 'template/A/city/x', hold_reason: 'auto-approve-off: settings.auto_approve_email is off',
    hold_class: 'auto-approve-off', class_known: true, needs_person: true, created_at: '2026-10-29T19:00:00.000Z', new: false }) }), CFG);
  t.check('capped: held_cap drafts listed, then "+N more"',
    [(p.text.match(/  #110\d  Off/g) || []).length, p.text.indexOf('  +3 more (in the review queue; the next digest lists them)') !== -1,
     p.record.summary.held_needs_person],
    [2, true, 5]);
}

const empty = buildDigest(with_({ auto_approved: [], sent: [], held: [] }), CFG);
t.check('an empty day still sends, saying "none" in all five sections (the digest is also the proof the stack ran)',
  [empty.send, empty.text.split('  none\n').length - 1], [true, 5]);

// Per niche and A/B arm (migration 025).
{
  const nd = buildDigest(with_({
    auto_approved: [
      { draft_id: 501, lead_id: 4000, company_name: 'Axis CRC', domain: 'axis.mx', country: 'Mexico', channel: 'email',
        variant: 'template/A/trials/unnamed', status: 'approved', approved_at: '2026-10-04T20:32:00.000Z', niche: 'research_site' },
      { draft_id: 502, lead_id: 4001, company_name: 'Beta Site', domain: 'beta.mx', country: 'Mexico', channel: 'email',
        variant: 'template/B/city/unnamed', status: 'approved', approved_at: '2026-10-04T20:40:00.000Z', niche: 'research_site' },
      { draft_id: 503, lead_id: 180, company_name: 'ProRelix', domain: 'prorelix.com', country: 'India', channel: 'email',
        variant: 'follow-up-1/template/A', status: 'approved', approved_at: '2026-10-04T21:00:00.000Z', niche: 'cro' },
    ],
    sent: [
      { draft_id: 400, lead_id: 4002, company_name: 'Gamma', domain: 'gamma.mx', to_addr: 'info@gamma.mx',
        variant: 'template/A/areas/role-inbox+x', approved_by: 'auto', sent_at: '2026-10-04T12:10:00.000Z', niche: 'research_site' },
    ],
    held: [],
    niche_totals: [
      { niche: 'cro', arm: 'A', first_touches: 14, follow_ups: 17, replied_leads: 0, bounced: 1 },
      { niche: 'research_site', arm: 'B', first_touches: 3, follow_ups: 0, replied_leads: 1, bounced: 0 },
    ],
  }), CFG);
  t.check('niche and arm on each auto-approved line',
    nd.text.indexOf('  #501  Axis CRC (axis.mx, Mexico)  first touch  [research_site A]  approved') !== -1, true);
  t.check('niche and arm on each sent line (tags after the arm do not hide it)',
    nd.text.indexOf('  #400  Gamma -> info@gamma.mx  first touch  [research_site A]') !== -1, true);
  t.check('this window, by niche and arm', [
    nd.text.indexOf('  cro / arm A:  1 auto-approved, 0 sent, 0 email(s) held') !== -1,
    nd.text.indexOf('  research_site / arm A:  1 auto-approved, 1 sent, 0 email(s) held') !== -1,
    nd.text.indexOf('  research_site / arm B:  1 auto-approved, 0 sent, 0 email(s) held') !== -1], [true, true, true]);
  t.check('every template email so far, by niche and arm', [
    nd.text.indexOf('  cro / arm A:  14 first touches, 17 follow-ups, 0 lead(s) replied, 1 bounced') !== -1,
    nd.text.indexOf('  research_site / arm B:  3 first touches, 0 follow-ups, 1 lead(s) replied, 0 bounced') !== -1],
    [true, true]);
}
t.check('kinds: first touch, follow-up N, low-context note',
  [kindOf('unnamed/D2.A1'), kindOf('follow-up-1/BEN-SEE.A1+long'), kindOf('low-context/role-inbox')],
  ['first touch', 'follow-up 1', 'low-context note']);
t.check('what Record Digest stores: the day, the window, the ids, and the codes it carried',
  d.record, { digest_day: '2026-10-05', covers_from: '2026-10-04T03:00:00.000Z', covers_to: '2026-10-05T03:00:00.000Z',
    coded: [], new_classes: [],
    summary: { auto_approved: [105], sent: [99], held: 3, held_new: 1, held_coded: 0, held_needs_person: 1,
      held_by_class: { 'claim-check': 1, linkedin: 1, none: 1 }, auto_approve_email: true } });

const node = runOnceForAll(DIGEST, [{ json: ROW }], { Config: [{ json: CFG }] }, bake);
t.check('the node emits exactly one item: Build Digest\'s answer', [node.length, node[0].json.send, node[0].json.subject],
  [1, true, d.subject]);

// --- the host is off at night: the catch-up cases (audited 2026-10-07) ------
//
// The digest is the one schedule in the system with a time of day in it, so it
// is the one worth proving catches up. It does not fire at 08:00; it fires at
// the first tick AT OR AFTER 08:00 operator time that has not already sent that
// day, and it covers from where the last digest stopped. A host that comes up
// at 10:40 still gets that day's digest, and a day it was never up at all folds
// into the next one rather than vanishing.

t.check(
  'a host that comes up at 10:40 still sends that day -- the hour is a floor, not a slot',
  buildDigest(Object.assign({}, ROW, { local_hour: 10 }), { digest_hour: 8 }).send, true);
t.check(
  '... and at 23:00, the last tick of the day',
  buildDigest(Object.assign({}, ROW, { local_hour: 23 }), { digest_hour: 8 }).send, true);
t.check(
  'a tick before the hour holds, and says why',
  [buildDigest(Object.assign({}, ROW, { local_hour: 7 }), { digest_hour: 8 }).send,
   buildDigest(Object.assign({}, ROW, { local_hour: 7 }), { digest_hour: 8 }).reason],
  [false, 'before-digest-hour']);
t.check(
  'the second tick of the day does not send again',
  [buildDigest(Object.assign({}, ROW, { already_sent: true, local_hour: 9 }), { digest_hour: 8 }).send,
   buildDigest(Object.assign({}, ROW, { already_sent: true, local_hour: 9 }), { digest_hour: 8 }).reason],
  [false, 'already-sent-today']);
t.check(
  'a day the host was off folds into the next digest -- the window is three days wide and still one email',
  (function () {
    const d = buildDigest(Object.assign({}, ROW, {
      digest_day: '2026-10-07', local_hour: 11,
      covers_from: '2026-10-04T03:00:00.000Z', covers_to: '2026-10-07T06:00:00.000Z',
    }), { digest_hour: 8 });
    return [d.send, d.text.indexOf('Covers 2026-10-04 08:00 PKT to 2026-10-07 11:00 PKT') !== -1];
  })(),
  [true, true]);

t.done();

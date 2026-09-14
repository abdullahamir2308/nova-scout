// Unit tests for code_decide.js -- the send decision.
//
//   node test_decide.js
//
// Every guard is exercised against the real function sliced out of the shipped
// node, with the build-time signature substituted exactly as build_workflow.py
// substitutes it. dryrun.py then proves the same guards end-to-end through n8n,
// Postgres and an SMTP sink.
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const FILE = path.join(__dirname, 'code_decide.js');
const SIG = 'Test Sender\nFounder, Test Labs\n+10000000000';
const transform = (s) => s.replace('__SIGNATURE__', JSON.stringify(SIG));
const { decide, warmupState, ceilingForWeek, localClock, normaliseBody } = extractFunctions(
  FILE, '// Node body',
  ['decide', 'warmupState', 'ceilingForWeek', 'localClock', 'normaliseBody'],
  transform
);

const t = runner('Workflow 6 -- Decide Send');

const OPT = "If this isn't relevant, reply 'no' and I won't follow up.";
const BODY = 'Hello,\n\nYour site lists oncology work. Nova is an assistant trained on your own ' +
  'SOPs. It already runs at NoblePath. Would you like the recording?\n\n' + OPT + '\n\n' + SIG;

// Monday 2026-09-14 15:00 UTC = 20:00 PKT, sender-day 2026-09-14.
// Argentina 12:00, Mexico 09:00, Poland 16:00 -- at work.
// Egypt 17:00, Turkey 18:00, India 20:30 -- not.
const CLOCK = '2026-09-14T15:00:00.000Z';
const ALWAYS = () => 0;

function cand(over) {
  return Object.assign({
    draft_id: 31, lead_id: 7, channel: 'email', status: 'approved', variant: 'role-inbox',
    subject: 'question about your protocol docs', body: BODY, domain: 'klixar.com',
    country: 'Argentina', lead_status: 'drafted', to_addr: 'hello@klixar.com', verified: true,
    lead_domain_blocked: false, recipient_domain_blocked: false, prior_sends: 0,
    prior_manual_sends: 0, follow_ups_sent: 0, replied: false, bounced: false,
  }, over);
}

function st(over) {
  return Object.assign({
    clock: CLOCK, min_gap_min: 0, send_probability: 1,
    sent_mirror_synced_at: '2026-09-14T14:55:00.000Z',
    first_external_send_at: null, recent_sends: [], last_external_send_at: null,
    unconfirmed_workflow_sends: [], candidates: [cand()],
  }, over);
}

const at = (iso, kind) => ({ at: iso, kind: kind || 'sent-folder' });
const FOUR_TODAY = ['04', '05', '06', '07'].map((h) => at('2026-09-14T' + h + ':00:00Z'));

// --- the warm-up ceiling -----------------------------------------------------
let d = decide(st(), ALWAYS);
t.check('empty Sent history -> warm-up not started: day 1 of week 1, ceiling 5',
  [d.warmup.history, d.warmup.day_index, d.warmup.week, d.warmup.ceiling, d.warmup.sent_today],
  ['empty', 0, 1, 5, 0]);
t.check('... and the first send goes out', [d.send, d.reason], [true, 'send']);

d = decide(st({ first_external_send_at: '2026-09-14T04:00:00Z', recent_sends: FOUR_TODAY }), ALWAYS);
t.check('week-1 day, 4 already sent -> the 5th send is ALLOWED (the ceiling is 5)',
  [d.warmup.week, d.warmup.sent_today, d.warmup.remaining, d.send], [1, 4, 1, true]);

const FIVE_TODAY = FOUR_TODAY.concat([at('2026-09-14T08:00:00Z')]);
d = decide(st({ first_external_send_at: '2026-09-14T04:00:00Z', recent_sends: FIVE_TODAY }), ALWAYS);
t.check('week-1 day, 5 already sent -> the 6th send is REFUSED',
  [d.send, d.reason, d.warmup.remaining, d.payload], [false, 'ceiling-reached', 0, null]);
t.check('... the refusal names the ceiling', d.detail,
  'Week 1 ceiling is 5/day; 5 external send(s) already on sender-day 2026-09-14.');
t.check('... while a recipient was eligible and at work -- nothing else stopped it',
  d.eligible.filter((e) => e.in_window).map((e) => e.draft_id), [31]);

d = decide(st({
  first_external_send_at: '2026-09-14T04:00:00Z',
  recent_sends: FOUR_TODAY.concat([at('2026-09-14T08:00:00Z', 'claim')]),
}), ALWAYS);
t.check('an in-flight claim (SMTP outcome unknown) counts toward the ceiling', d.reason, 'ceiling-reached');

let w = warmupState(st({
  first_external_send_at: '2026-09-13T10:00:00Z',
  recent_sends: [at('2026-09-13T18:59:59Z'), at('2026-09-13T19:00:00Z')],
}), Date.parse(CLOCK));
t.check("the day is the SENDER's: 23:59:59 PKT is yesterday, 00:00:00 PKT is today",
  [w.sender_day, w.day_start, w.day_end, w.sent_today],
  ['2026-09-14', '2026-09-13T19:00:00.000Z', '2026-09-14T19:00:00.000Z', 1]);

const week = (first) => {
  const s = warmupState(st({ first_external_send_at: first }), Date.parse(CLOCK));
  return [s.day_index, s.week, s.ceiling];
};
t.check('first external send 6 sender-days ago -> still week 1', week('2026-09-08T10:00:00Z'), [6, 1, 5]);
t.check('7 sender-days ago -> week 2, 10/day', week('2026-09-07T10:00:00Z'), [7, 2, 10]);
t.check('a send at 20:00 UTC on the 7th is 01:00 PKT on the 8th -> 6 days, week 1',
  week('2026-09-07T20:00:00Z'), [6, 1, 5]);
t.check('14 days -> week 3, 15/day', week('2026-08-31T10:00:00Z'), [14, 3, 15]);
t.check('21 days -> week 4, 20/day', week('2026-08-24T10:00:00Z'), [21, 4, 20]);
t.check('months in -> still 20, the hard ceiling', week('2026-01-05T10:00:00Z')[2], 20);
t.check('ceiling by week 1..5 and 52', [1, 2, 3, 4, 5, 52].map(ceilingForWeek), [5, 10, 15, 20, 20, 20]);

// --- can the count be trusted? -----------------------------------------------
d = decide(st({ sent_mirror_synced_at: null }), ALWAYS);
t.check('the Sent mirror has never read the folder -> refuse, whatever else is true',
  [d.send, d.reason], [false, 'sent-mirror-never-ran']);
d = decide(st({ unconfirmed_workflow_sends: [{ message_id: '<a@x>', sent_at: '2026-09-14T14:40:00Z' }] }), ALWAYS);
t.check('a workflow send 20 min old that the mirror has not seen -> refuse', d.reason, 'sent-mirror-behind');
d = decide(st({ unconfirmed_workflow_sends: [{ message_id: '<a@x>', sent_at: '2026-09-14T14:50:00Z' }] }), ALWAYS);
t.check('... 10 min old is inside the grace period', d.reason, 'send');

// --- Section 6: LinkedIn is sent by a human ----------------------------------
let threw = '';
try { decide(st({ candidates: [cand({ draft_id: 32, channel: 'linkedin' })] }), ALWAYS); } catch (e) { threw = e.message; }
t.check('a LinkedIn draft reaching the node stops the whole tick', /^Section 6 violation/.test(threw), true);
threw = '';
try { decide(st({ candidates: [cand(), cand({ draft_id: 32, channel: 'linkedin' })] }), ALWAYS); } catch (e) { threw = e.message; }
t.check('... even when a sendable email draft is also present', /^Section 6 violation/.test(threw), true);

// --- per-draft eligibility ---------------------------------------------------
const reasonFor = (over) => {
  const out = decide(st({ candidates: [cand(over)] }), ALWAYS);
  return out.excluded.length ? out.excluded[0].reason : (out.send ? 'eligible' : out.reason);
};
[
  ['pending draft', { status: 'pending' }, 'not-approved'],
  ['rejected draft', { status: 'rejected' }, 'not-approved'],
  ['draft already sent', { status: 'sent' }, 'not-approved'],
  ['lead domain on the blocklist', { lead_domain_blocked: true }, 'blocklisted-domain'],
  ['recipient domain on the blocklist', { recipient_domain_blocked: true }, 'blocklisted-recipient-domain'],
  ['contact not verified (Section 9, Workflow 3b)', { verified: false }, 'contact-unverified'],
  ['no address', { to_addr: null }, 'no-valid-address'],
  ['malformed address', { to_addr: 'hello at klixar.com' }, 'no-valid-address'],
  ['low-context variant, even though a human approved it', { variant: 'low-context/role-inbox' }, 'low-context'],
  ['low-context note as the body', { variant: 'role-inbox', body: 'NOT DRAFTED -- low context.\n\nFound: a city' }, 'low-context'],
  ['empty subject', { subject: '  ' }, 'no-subject'],
  ['opt-out sentence deleted in an edit', { body: BODY.replace(OPT, '') }, 'no-opt-out'],
  ['opt-out sentence paraphrased', { body: BODY.replace("won't", 'will not') }, 'no-opt-out'],
  ['signed as a previous sender identity', { body: BODY.replace(SIG, 'Fatima') }, 'stale-signature'],
  ['signature separated from the opt-out line', { body: BODY.replace(OPT + '\n\n' + SIG, OPT + '\n\nThanks!\n\n' + SIG) }, 'stale-signature'],
  ['two URLs (Section 5 allows one)', { body: BODY.replace('Would you', 'See https://a.example and www.b.example. Would you') }, 'too-many-urls'],
  ['country outside Section 12', { country: 'Narnia' }, 'unknown-country:Narnia'],
  ['first touch to a lead the workflow already emailed', { prior_sends: 1 }, 'already-contacted'],
  ['first touch to a lead the operator emailed by hand', { prior_manual_sends: 1 }, 'already-contacted'],
  ['first touch to a lead already at sent', { lead_status: 'sent' }, 'lead-status:sent'],
  ['lead that replied', { replied: true }, 'lead-replied'],
  ['lead whose address bounced', { bounced: true }, 'lead-bounced'],
  ['follow-up with no first touch', { variant: 'follow-up-1', lead_status: 'sent' }, 'follow-up-without-first-touch'],
  ['a third follow-up', { variant: 'follow-up-3', lead_status: 'sent', prior_sends: 3, follow_ups_sent: 2 }, 'follow-ups-exhausted'],
  ['one URL is fine', { body: BODY.replace('Would you', 'See https://a.example. Would you') }, 'eligible'],
  ['follow-up 1 after a first touch', { variant: 'follow-up-1', lead_status: 'sent', prior_sends: 1 }, 'eligible'],
  ['CRLF and trailing spaces from a grid edit', { body: BODY.replace(/\n/g, '  \r\n') }, 'eligible'],
].forEach(([label, over, want]) => t.check(label + ' -> ' + want, reasonFor(over), want));

d = decide(st({ candidates: [cand({ status: 'pending' }), cand({ draft_id: 39, variant: 'low-context/role-inbox' })] }), ALWAYS);
t.check('nothing eligible -> no send, and every draft says why',
  [d.send, d.reason, d.excluded.map((x) => x.draft_id + ':' + x.reason)],
  [false, 'no-eligible-draft', ['31:not-approved', '39:low-context']]);

// --- the recipient's business hours ------------------------------------------
const lc = (country, iso) => {
  const r = localClock(country, Date.parse(iso));
  return r && [r.local, r.in_window];
};
t.check('Mon 15:00Z: Argentina 12:00 -> at work', lc('Argentina', CLOCK), ['Mon 12:00', true]);
t.check('Mon 15:00Z: Mexico 09:00 -> at work (opening minute)', lc('Mexico', CLOCK), ['Mon 09:00', true]);
t.check('Mon 15:00Z: Poland 16:00 -> at work', lc('Poland', CLOCK), ['Mon 16:00', true]);
t.check('Mon 15:00Z: Egypt 17:00 -> not (closing minute is outside)', lc('Egypt', CLOCK), ['Mon 17:00', false]);
t.check('Mon 15:00Z: Turkey 18:00 -> not', lc('Turkey', CLOCK), ['Mon 18:00', false]);
t.check('Mon 15:00Z: India 20:30 -> not (half-hour offset)', lc('India', CLOCK), ['Mon 20:30', false]);
t.check('India opens at 03:30Z', lc('India', '2026-09-14T03:30:00Z'), ['Mon 09:00', true]);
t.check('... and is shut at 03:29Z', lc('India', '2026-09-14T03:29:00Z'), ['Mon 08:59', false]);
t.check('Egypt works Sunday', lc('Egypt', '2026-09-13T08:00:00Z'), ['Sun 10:00', true]);
t.check('Egypt does not work Friday', lc('Egypt', '2026-09-18T08:00:00Z'), ['Fri 10:00', false]);
t.check('Argentina does not work Saturday', lc('Argentina', '2026-09-19T15:00:00Z'), ['Sat 12:00', false]);
t.check('Argentina does not work Sunday', lc('Argentina', '2026-09-20T15:00:00Z'), ['Sun 12:00', false]);
t.check('a country outside Section 12 has no clock', localClock('Narnia', Date.parse(CLOCK)), null);

d = decide(st({ candidates: [cand({ country: 'India' })] }), ALWAYS);
t.check("eligible but outside the recipient's hours -> wait, don't send",
  [d.send, d.reason, d.eligible.length], [false, 'no-recipient-in-business-hours', 1]);
d = decide(st({ candidates: [cand({ draft_id: 33, country: 'India' }), cand({ draft_id: 35, country: 'Poland' }), cand({ draft_id: 31, country: 'Turkey' })] }), ALWAYS);
t.check('the oldest draft whose recipient is at work, skipping older ones who are not', d.payload.draft_id, 35);

// --- one message per tick ----------------------------------------------------
d = decide(st({ candidates: [cand({ draft_id: 37 }), cand({ draft_id: 31 }), cand({ draft_id: 35 })] }), ALWAYS);
t.check('three sendable drafts -> exactly one payload, the oldest',
  [d.send, d.payload.draft_id, Array.isArray(d.payload)], [true, 31, false]);
const items = runOnceForAll(FILE, [{ json: st({ candidates: [cand({ draft_id: 37 }), cand({ draft_id: 31 })] }) }], {}, transform);
t.check('the shipped node body returns ONE item per tick, never a batch', [items.length, items[0].json.payload.draft_id], [1, 31]);

// --- pacing: irregular intervals ---------------------------------------------
d = decide(st({ min_gap_min: 20, last_external_send_at: '2026-09-14T14:50:00Z' }), ALWAYS);
t.check('10 min since the last external send, 20 min floor -> wait', d.reason, 'min-gap');
d = decide(st({ min_gap_min: 20, last_external_send_at: '2026-09-14T14:35:00Z' }), ALWAYS);
t.check('25 min since -> go', d.reason, 'send');
let calls = 0;
d = decide(st({ send_probability: 0.4 }), () => { calls++; return 0.5; });
t.check('the per-tick coin flip can skip a tick', [d.reason, calls], ['random-gate', 1]);
d = decide(st({ send_probability: 0.4 }), () => 0.1);
t.check('... and can pass it', d.reason, 'send');
calls = 0;
decide(st({ sent_mirror_synced_at: null }), () => { calls++; return 0; });
t.check('the coin is never flipped once a hard guard has refused', calls, 0);

// --- the payload handed to Claim Send ----------------------------------------
d = decide(st({ candidates: [cand({ body: BODY.replace(/\n/g, '\r\n') })] }), ALWAYS);
t.check('the body sent is the normalised text that was checked', d.payload.body, BODY);
t.check('the raw body travels too, so Claim can prove nobody edited it since', d.payload.raw_body, BODY.replace(/\n/g, '\r\n'));
t.check("Claim gets the sender-day window, the ceiling and the tick's one clock",
  [d.payload.day_start, d.payload.day_end, d.payload.ceiling, d.payload.clock],
  ['2026-09-13T19:00:00.000Z', '2026-09-14T19:00:00.000Z', 5, CLOCK]);
t.check('normaliseBody leaves interior text alone', normaliseBody('a  b\r\n\r\nc  '), 'a  b\n\nc');

t.done();

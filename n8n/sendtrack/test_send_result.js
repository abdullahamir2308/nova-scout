// Unit tests for code_check_smtp.js -- what happens after the SMTP call.
//
//   node test_send_result.js
//
// The claim is already written when this node runs, so every branch here moves
// a real draft: confirm it, hand it back to the send queue, or hand it back to
// a human. Getting the split wrong either strands a good draft in review or
// retries a dead address every ten minutes.
const path = require('path');
const { extractFunctions, runForEachItem, runner } = require('./harness');

const FILE = path.join(__dirname, 'code_check_smtp.js');
const transform = (s) => s.replace('__OWN_DOMAIN__', JSON.stringify('amitrixlabs.com'));
const { checkResult, classifyFailure } = extractFunctions(FILE, '// Node body', ['checkResult', 'classifyFailure'], transform);

const t = runner('Workflow 6 -- Check SMTP Result');

const claim = {
  claimed: 1, outreach_id: 12, draft_id: 31, lead_id: 7,
  to_addr: 'Hello@Klixar.com', clock: '2026-09-14T15:00:00.000Z',
};
// nodemailer's `info`, which the Send Email node returns as the item's json.
const OK = {
  accepted: ['hello@klixar.com'], rejected: [],
  envelope: { from: 'abdullah@amitrixlabs.com', to: ['hello@klixar.com'] },
  messageId: '<a1b2c3@amitrixlabs.com>', response: '250 Message received',
};

let r = checkResult(claim, OK);
t.check('accepted, with a Message-ID -> confirm', [r.ok, r.failure], [true, null]);
t.check('... carrying the Message-ID replies will thread on', r.payload.message_id, '<a1b2c3@amitrixlabs.com>');
t.check('... the claim row to confirm, and the tick clock', [r.payload.outreach_id, r.payload.draft_id, r.payload.clock], [12, 31, claim.clock]);
t.check('... a prospect is external (counts toward the ceiling)', [r.payload.to_addr, r.payload.external], ['hello@klixar.com', true]);
t.check('a same-domain recipient is not external', checkResult(Object.assign({}, claim, { to_addr: 'me@amitrixlabs.com' }), Object.assign({}, OK, { accepted: ['me@amitrixlabs.com'] })).payload.external, false);

r = checkResult(claim, Object.assign({}, OK, { accepted: [], rejected: ['hello@klixar.com'], response: '550 5.1.1 User unknown' }));
t.check('the server rejected this address -> recipient failure', [r.ok, r.failure, r.payload.message_id], [false, 'recipient', null]);
r = checkResult(claim, Object.assign({}, OK, { accepted: ['someone@else.com'] }));
t.check('accepted list without our recipient -> not confirmed', [r.ok, r.failure], [false, 'account']);
r = checkResult(claim, Object.assign({}, OK, { messageId: '' }));
t.check('accepted but no Message-ID -> not confirmed (replies could never thread)', [r.ok, r.failure], [false, 'account']);

[
  ['Invalid login: 535 5.7.8 Authentication failed', 'account'],
  ["Can't send mail - all recipients were rejected: 553 5.7.1 <abdullah@amitrixlabs.com>: Sender address rejected: not owned by user", 'account'],
  ['554 5.7.1 <x@y.com>: Relay access denied', 'account'],
  ['Connection timeout', 'account'],
  ['getaddrinfo ENOTFOUND smtppro.zoho.com', 'account'],
  ['421 4.7.0 Temporary System Problem. Try again later', 'account'],
  ['Greeting never received', 'account'],
  ['something nobody anticipated', 'account'],
  ["Can't send mail - all recipients were rejected: 550 5.1.1 <hello@klixar.com>: Recipient address rejected: User unknown in virtual mailbox table", 'recipient'],
  ['550 5.1.1 <hello@klixar.com>: mailbox unavailable', 'recipient'],
  ['551 User not local', 'recipient'],
].forEach(([msg, want]) => t.check('"' + msg.slice(0, 60) + '" -> ' + want, classifyFailure(msg), want));

r = checkResult(claim, { error: "Can't send mail - all recipients were rejected: 550 5.1.1 Recipient address rejected" });
t.check('the Send Email node\'s continue-on-error item -> classified, not thrown', [r.ok, r.failure, /550 5\.1\.1/.test(r.payload.error)], [false, 'recipient', true]);

const out = runForEachItem(FILE, [{ json: OK }], { 'Claim Send': [{ json: claim }] }, transform);
t.check('the shipped node body pairs the SMTP result with its claim', [out[0].json.ok, out[0].json.payload.outreach_id], [true, 12]);

t.done();

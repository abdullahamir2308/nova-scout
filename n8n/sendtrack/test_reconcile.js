// Unit tests for code_reconcile_sent.js -- Plan Sent Reconcile (2026-10-28).
//
//   node test_reconcile.js
//
// The plan runs with code_mirror_sent.js's library above it, exactly as the
// build ships it, so "the row the trigger would write" is checked against the
// trigger path's own sentRows(), not a copy. The checker's answer is shaped as
// imap_health_server.py builds it from imap_preflight.py --ids-json.
const fs = require('fs');
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const MIRROR = fs.readFileSync(path.join(__dirname, 'code_mirror_sent.js'), 'utf8').split('// Node body')[0];
const transform = (s) => (MIRROR + '\n' + s).split('__OWN_DOMAIN__').join(JSON.stringify('amitrixlabs.com'));
const FILE = path.join(__dirname, 'code_reconcile_sent.js');
const { listingItems, reconcilePlan, sentRows } = extractFunctions(FILE, '// Node body',
  ['listingItems', 'reconcilePlan', 'sentRows'], transform);
const t = runner('Workflow 6 -- Plan Sent Reconcile');

const OP = 'operator@ops.example';
const entry = (over) => Object.assign({ message_id: '<a1@amitrixlabs.com>', at: '2026-10-28T07:40:40+00:00',
  date: 'Wed, 28 Oct 2026 07:40:36 +0000', to: 'BD Team <bd@prospect.example>', cc: '', bcc: '' }, over);
const ok = (sent) => ({ ok: true, status: 'passed', hours: 48, since: '2026-10-26T08:00:00+00:00', sent: sent });

// --- only a complete listing counts ---------------------------------------------------
t.check('checker unreachable (HTTP error item) -> not listed, no rows',
  [reconcilePlan({ error: { message: 'ECONNREFUSED' } }, OP).listed,
    reconcilePlan({ error: { message: 'ECONNREFUSED' } }, OP).payload.rows], [false, []]);
let p = reconcilePlan({ ok: false, status: 'failed', stage: 'login', sent: [entry()] }, OP);
t.check('IMAP check failed, even with a list attached -> not listed, no rows, says why',
  [p.listed, p.payload.rows, /login/.test(p.why)], [false, [], true]);
t.check('ok but no list -> not listed', reconcilePlan({ ok: true, status: 'passed' }, OP).listed, false);
t.check('nothing at all -> not listed', reconcilePlan(undefined, OP).listed, false);
p = reconcilePlan(ok([]), OP);
t.check('an empty folder listing IS a listing (nothing to do, but it was read)',
  [p.listed, p.payload.rows, p.payload.seen], [true, [], 0]);

// --- Message-ID is the key, and only a well-formed one --------------------------------
p = reconcilePlan(ok([entry({ message_id: 'no-brackets@amitrixlabs.com' }), entry({ message_id: '<two> <ids>' }),
  entry({ message_id: '' }), entry({ message_id: '<ok@amitrixlabs.com>', date: '' }),
  entry({ message_id: '<good@amitrixlabs.com>' })]), OP);
t.check('malformed, doubled, missing ids and an undated entry are skipped and counted -- never a fallback key',
  [p.payload.skipped, p.payload.rows.map((r) => r.message_id)], [4, ['<good@amitrixlabs.com>']]);
t.check('... so no row is keyed on the mirror\'s fallback hash',
  p.payload.rows.some((r) => /no-message-id/.test(r.message_id)), false);

// --- the row is the trigger path's row ----------------------------------------------
const e = entry({ cc: 'Me <me@amitrixlabs.com>', bcc: 'hidden@other.example' });
const trigger = { date: e.date, to: e.to, cc: e.cc, subject: 'Prospect + oncology + sponsor inquiries',
  metadata: { 'message-id': e.message_id, bcc: e.bcc } };
t.check('a reconciled message becomes exactly the row Normalise Sent would write for the same message',
  reconcilePlan(ok([e]), OP).payload.rows, sentRows([trigger], OP).rows);
t.check('... recipients from To, Cc and Bcc; external because one is outside the own domain',
  [reconcilePlan(ok([e]), OP).payload.rows[0].recipients, reconcilePlan(ok([e]), OP).payload.rows[0].external],
  [['bd@prospect.example', 'me@amitrixlabs.com', 'hidden@other.example'], true]);
t.check('a message only to the operator is not a warm-up send',
  reconcilePlan(ok([entry({ to: OP })]), OP).payload.rows[0].external, false);
t.check('... nor one only to the own domain',
  reconcilePlan(ok([entry({ to: 'me@amitrixlabs.com' })]), OP).payload.rows[0].external, false);
t.check('sent_at is the Date header, as for the trigger',
  reconcilePlan(ok([e]), OP).payload.rows[0].sent_at, '2026-10-28T07:40:36.000Z');

// --- the node body, as n8n runs it ---------------------------------------------------
const out = runOnceForAll(FILE, [{ json: { operator_email: OP } }], { 'List Sent Folder': [{ json: ok([e]) }] },
  transform);
t.check('node body: reads List Sent Folder by name and the settings row from its input, one item out',
  [out.length, out[0].json.listed, out[0].json.payload.rows.length, out[0].json.payload.listed], [1, true, 1, true]);

t.done();

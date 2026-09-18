// Unit tests for code_health.js -- the IMAP Health workflow's judgement:
// what counts as a problem, when it alerts, when it stays quiet, and what the
// operator is told.
//
//   node test_health.js
//
// The checker's answer is shaped exactly as imap_health_server.py builds it,
// the database row exactly as Load Health State returns it.
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const FILE = path.join(__dirname, 'code_health.js');
const { assess } = extractFunctions(FILE, '// Node body', ['assess']);
const t = runner('Workflow 6 -- IMAP Health');

const CFG = { checker_url: 'http://imap-health:8765/check', grace_min: 15, confirm_after: 2, remind_hours: 6 };
const OK = { ok: true, status: 'passed', stage: null, detail: null, hint: null, hours: 48, inbox: [], sent: [] };
const FAILED = { ok: false, status: 'failed', stage: 'connect', detail: 'imappro.zoho.com does not resolve: [Errno -2]',
  hint: 'check NOVASCOUT_IMAP_HOST', hours: 48, inbox: [], sent: [] };
const NOW = '2026-09-16T15:00:00.000Z';

const row = (over) => Object.assign({
  now: NOW, operator_email: 'Operator@Example.com', watch_started_at: '2026-09-15T14:14:26.718Z',
  sent_synced_at: '2026-09-16T14:32:01.835Z', missing_inbox: [], missing_sent: [],
  prev_healthy: null, prev_problems: null, prev_consecutive_failures: null, prev_failing_since: null,
  prev_last_alerted_at: null, prev_alerted_problems: null,
}, over);
const failingRow = (n, over) => row(Object.assign({
  prev_healthy: false, prev_problems: ['imap-failed'], prev_consecutive_failures: n,
  prev_failing_since: '2026-09-16T14:20:00.000Z',
}, over));
const MISSED = [{ message_id: '<r1@mail.example>', at: '2026-09-16T14:31:53.000Z', from: 'Lead <lead@cro.example>', subject: 'Re: Nova' }];

// --- healthy ---------------------------------------------------------------------
let a = assess(OK, row(), CFG);
t.check('first ever check, all fine -> healthy, nothing to say',
  [a.healthy, a.problems, a.alert_kind, a.notify], [true, [], null, false]);
t.check('healthy state for Record Health: no failures, no episode',
  [a.state.consecutive_failures, a.state.failing_since, a.state.detail], [0, null, null]);

// --- one failing check is not enough ------------------------------------------------
a = assess(FAILED, row(), CFG);
t.check('IMAP fails once -> failing but NOT confirmed, no alert (a wake from sleep looks like this)',
  [a.healthy, a.problems, a.confirmed, a.alert_kind, a.notify], [false, ['imap-failed'], false, null, false]);
t.check('... the episode starts now, 1 failure counted',
  [a.state.consecutive_failures, a.state.failing_since], [1, NOW]);
a = assess(OK, row({ missing_inbox: MISSED }), CFG);
t.check('a missed INBOX message once -> not confirmed either (Mailbox Watch may be seconds from reconnecting)',
  [a.problems, a.confirmed, a.alert_kind], [['inbox-missed'], false, null]);

// --- confirmed -> one alert ------------------------------------------------------------
a = assess(FAILED, failingRow(1), CFG);
t.check('IMAP fails twice in a row -> confirmed, alert to the operator (address lower-cased)',
  [a.confirmed, a.alert_kind, a.notify, a.notify_to], [true, 'problem', true, 'operator@example.com']);
t.check('... subject names the problem', a.subject, '[Nova Scout] IMAP health: the mailbox is not reachable over IMAP');
t.check('... body carries the stage, the detail and the hint',
  [a.text.includes('failed at connect: imappro.zoho.com does not resolve'), a.text.includes('(hint: check NOVASCOUT_IMAP_HOST)')],
  [true, true]);
t.check('... body says how to reconnect Mailbox Watch', a.text.includes('re-publish Mailbox Watch'), true);
t.check('... the episode keeps its start time, 2 failures counted',
  [a.state.failing_since, a.state.consecutive_failures], ['2026-09-16T14:20:00.000Z', 2]);

a = assess(OK, failingRow(1, { prev_problems: ['inbox-missed'], missing_inbox: MISSED }), CFG);
t.check('a message still missed on the second check -> alert, listing it for a by-hand opt-out check',
  [a.alert_kind, a.text.includes('lead@cro.example'), a.text.includes('"Re: Nova"'), a.text.includes('blocklist any opt-out')],
  ['problem', true, true, true]);

a = assess(OK, failingRow(1, { missing_sent: [{ message_id: '<s1@amitrixlabs.com>', at: '2026-09-16T14:00:00.000Z', to: 'x@cro.example' }] }), CFG);
t.check('a Sent message missing from the mirror -> sent-missed, says the ceiling does not count it',
  [a.problems, a.alert_kind, a.text.includes('warm-up ceiling does not count them'), a.text.includes('<s1@amitrixlabs.com>')],
  [['sent-missed'], 'problem', true, true]);

a = assess(FAILED, failingRow(1, { missing_inbox: MISSED }), CFG);
t.check('two problems at once -> both in the subject, in the fixed order',
  a.subject, '[Nova Scout] IMAP health: the mailbox is not reachable over IMAP; Mailbox Watch has missed INBOX mail');

// --- the checker itself --------------------------------------------------------------
a = assess({ error: { message: 'connect ECONNREFUSED 172.18.0.5:8765', code: 'ECONNREFUSED' } }, failingRow(1, { prev_problems: ['checker-unreachable'] }), CFG);
t.check('HTTP node error item (continue-on-error) -> checker-unreachable, with the reason',
  [a.problems, a.alert_kind, a.text.includes('ECONNREFUSED'), a.text.includes('docker compose logs imap-health')],
  [['checker-unreachable'], 'problem', true, true]);
t.check('an error string, an empty body, a body with no ok flag -> all checker-unreachable',
  [assess({ error: 'timeout of 150000ms exceeded' }, row(), CFG).problems,
    assess({}, row(), CFG).problems, assess({ status: 'passed' }, row(), CFG).problems],
  [['checker-unreachable'], ['checker-unreachable'], ['checker-unreachable']]);
t.check('checker unreachable is judged on its own -- the checker never said IMAP failed',
  assess({ error: 'x' }, row(), CFG).problems.includes('imap-failed'), false);

// --- no repeat, reminder, change ------------------------------------------------------
const alerted = (over) => failingRow(4, Object.assign({
  prev_last_alerted_at: '2026-09-16T14:40:00.000Z', prev_alerted_problems: ['imap-failed'],
}, over));
a = assess(FAILED, alerted(), CFG);
t.check('same problem, alerted 20 minutes ago -> no second email', [a.alert_kind, a.notify], [null, false]);
t.check('... the alert time and problems are carried forward for Record Health',
  [a.state.prev_last_alerted_at, a.state.prev_alerted_problems], ['2026-09-16T14:40:00.000Z', ['imap-failed']]);
a = assess(FAILED, alerted({ now: '2026-09-16T20:40:00.000Z' }), CFG);
t.check('same problem, exactly remind_hours later -> reminder', a.alert_kind, 'problem');
a = assess(FAILED, alerted({ now: '2026-09-16T20:39:59.000Z' }), CFG);
t.check('... one second short of it -> still quiet', a.alert_kind, null);
a = assess(FAILED, alerted({ missing_inbox: MISSED }), CFG);
t.check('the problem changes (now INBOX mail is missed too) -> new alert at once', a.alert_kind, 'problem');

// --- all clear -----------------------------------------------------------------------
a = assess(OK, alerted({ prev_problems: ['imap-failed'] }), CFG);
t.check('healthy after an alert -> one all-clear email',
  [a.healthy, a.alert_kind, a.notify, a.subject], [true, 'recovered', true, '[Nova Scout] IMAP health: OK again']);
t.check('... names what it was and when', [a.text.includes('the mailbox is not reachable over IMAP'),
  a.text.includes('from 2026-09-16T14:20:00.000Z until ' + NOW)], [true, true]);
t.check('... Sent re-read after the problem began -> says Mailbox Watch reconnected',
  a.text.includes('so it was connected again at that point'), true);
a = assess(OK, alerted({ sent_synced_at: '2026-09-16T09:00:00.000Z' }), CFG);
t.check('... no re-read since the problem began -> says it may still be waiting in n8n\'s backoff',
  [a.text.includes('has not read the Sent folder since the problem began'), a.text.includes('connected again')], [true, false]);
a = assess(OK, alerted({ prev_problems: ['checker-unreachable'], prev_alerted_problems: ['checker-unreachable'] }), CFG);
t.check('... after only the checker was down -> no claim about Mailbox Watch either way',
  [a.alert_kind, a.text.includes('Sent folder')], ['recovered', false]);
t.check('... the episode is closed for Record Health', [a.state.consecutive_failures, a.state.failing_since], [0, null]);
a = assess(OK, failingRow(1), CFG);
t.check('healthy after a failure nobody was told about -> no all-clear either', a.alert_kind, null);

// --- nobody to tell --------------------------------------------------------------------
a = assess(FAILED, failingRow(1, { operator_email: null }), CFG);
t.check('no operator address in settings -> the alert is still judged, but nothing is sent',
  [a.alert_kind, a.notify, a.notify_to], ['problem', false, '']);
a = assess(FAILED, failingRow(1, { operator_email: 'two@a.com, three@b.com' }), CFG);
t.check('not one bare address -> nothing is sent', a.notify, false);

// --- long lists, shape ------------------------------------------------------------------
const many = Array.from({ length: 13 }, (_, i) => ({ message_id: '<m' + i + '@x>', at: NOW, from: 'f' + i + '@x.example', subject: 's' }));
a = assess(OK, failingRow(1, { missing_inbox: many }), CFG);
t.check('13 missed messages -> the first 10 listed, then a count',
  [a.text.includes('f9@x.example'), a.text.includes('f10@x.example'), a.text.includes('... and 3 more'), a.state.detail],
  [true, false, true, 'inbox-missed: 13 message(s)']);
t.check('subject stays within 200 characters', assess(FAILED, failingRow(1, { missing_inbox: MISSED,
  missing_sent: [{ message_id: '<s>', at: NOW, to: 'x' }] }), { ...CFG }).subject.length <= 200, true);

// --- the node body, as n8n runs it ---------------------------------------------------------
const out = runOnceForAll(FILE, [{ json: failingRow(1) }], {
  'Check IMAP': [{ json: FAILED }],
  'Config': [{ json: CFG }],
});
t.check('node body: reads Check IMAP and Config by name, emits exactly one item',
  [out.length, out[0].json.alert_kind, out[0].json.state.consecutive_failures], [1, 'problem', 2]);

t.done();

// Unit tests for code_followup.js -- the follow-up drafts.
//
//   node test_followup.js
//
// The test worth reading is the cross-check: a follow-up this node writes is run
// through the SEND path's own eligibility function. A follow-up the send path
// would refuse (stale signature, no opt-out) would sit approved forever, so it
// has to be sendable by construction, and this proves it against the real code.
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const SIG = 'Test Sender\nFounder, Test Labs\n+10000000000';
const OPT = "If this isn't relevant, reply 'no' and I won't follow up.";
const bake = (s) => s
  .replace('__SIGNATURE__', JSON.stringify(SIG))
  .replace('__SENDER_NAME__', JSON.stringify('Test Sender'));

const FILE = path.join(__dirname, 'code_followup.js');
const { followUpDraft, followUpAction, greetingOf } = extractFunctions(
  FILE, '// Node body', ['followUpDraft', 'followUpAction', 'greetingOf'], bake);
const { ineligibility } = extractFunctions(
  path.join(__dirname, 'code_decide.js'), '// Node body', ['ineligibility'], bake);

const t = runner('Workflow 6 -- Build Follow-Up');

const FIRST_BODY = 'Hi Enrique,\n\nYour site lists oncology work. Nova is an assistant trained on your ' +
  'own SOPs. It already runs at NoblePath.\n\n' + OPT + '\n\n' + SIG;
const due = (over) => Object.assign({
  lead_id: 7, action: 'draft-follow-up', next_follow_up: 1,
  first_subject: 'question about your protocol docs', first_body: FIRST_BODY,
  first_sent_at: '2026-09-14T15:00:00.000Z',
}, over);

const d1 = followUpDraft(due());
t.check('follow-up 1: email channel, numbered variant, threaded subject',
  [d1.channel, d1.variant, d1.subject], ['email', 'follow-up-1', 'Re: question about your protocol docs']);
t.check('... keeps the greeting the first touch used', d1.body.split('\n')[0], 'Hi Enrique,');
t.check('... carries the compliance footer: opt-out, blank line, signature', d1.body.indexOf(OPT + '\n\n' + SIG) > 0, true);
t.check('... and quotes the first touch below it',
  [d1.body.indexOf('On Mon, 14 Sep 2026, Test Sender wrote:\n> Hi Enrique,') > 0], [true]);
const newText = d1.body.slice(0, d1.body.indexOf(SIG)).trim();
t.check('... the new text, greeting through opt-out, is under 80 words (Section 5)',
  newText.split(/\s+/).length < 80, true);

const d2 = followUpDraft(due({ next_follow_up: 2, first_subject: 'Re: Re: question about your protocol docs' }));
t.check('follow-up 2: its own line, and no "Re: Re:"',
  [d2.variant, d2.subject, d2.body.indexOf('One last note') > 0], ['follow-up-2', 'Re: question about your protocol docs', true]);

// The cross-check against the send path's real eligibility function.
const asCandidate = (draft) => ({
  draft_id: 90, lead_id: 7, channel: 'email', status: 'approved', variant: draft.variant,
  subject: draft.subject, body: draft.body, domain: 'klixar.com', country: 'Argentina',
  lead_status: 'sent', to_addr: 'hello@klixar.com', verified: true, lead_domain_blocked: false,
  recipient_domain_blocked: false, prior_sends: 1, prior_manual_sends: 0, follow_ups_sent: 0,
  replied: false, bounced: false,
});
t.check('follow-up 1 passes the SEND path\'s own checks, once approved', ineligibility(asCandidate(d1)), null);
t.check('follow-up 2 too', ineligibility(Object.assign(asCandidate(d2), { prior_sends: 2, follow_ups_sent: 1 })), null);

let threw = '';
try { followUpDraft(due({ next_follow_up: 3 })); } catch (e) { threw = e.message; }
t.check('there is no third follow-up (Section 9: maximum two)', /no follow-up template for #3/.test(threw), true);

t.check('mark-lost carries no draft', followUpAction(due({ action: 'mark-lost' })).payload, { lead_id: 7, action: 'mark-lost', draft: null });
t.check('a first touch with no greeting line gets "Hello,"', greetingOf('Your site lists...'), 'Hello,');
const node = runOnceForAll(FILE, [{ json: due() }, { json: due({ lead_id: 104, action: 'mark-lost' }) }], {}, bake);
t.check('the shipped node body emits one payload per due lead',
  node.map((n) => [n.json.payload.lead_id, n.json.payload.action]), [[7, 'draft-follow-up'], [104, 'mark-lost']]);
t.check("an empty queue's { success: true } placeholder becomes zero items, not a crash",
  runOnceForAll(FILE, [{ json: { success: true } }], {}, bake), []);

t.done();

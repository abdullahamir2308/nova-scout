// Unit tests for the Reply Assistant's Code nodes (Section 9, Workflow 7):
//
//   code_reply.js           -- Build Reply: the request, and the topics it refuses to answer
//   code_reply_assemble.js  -- Assemble Reply: the rules, the frame, the variant
//   code_reply_review.js    -- Build Review Email: the operator's three commands
//   code_reply_remind.js    -- Build Reminder: the 4-hour nudge
//   code_command_ack.js     -- Build Command Ack: what the operator is told back
//
//   node test_reply.js
//
// Each node is run as build_workflow.py ships it, placeholders substituted and
// the embedded files prepended, so the tests exercise the same bytes the
// workflow carries -- Assemble Reply is drafting's own rule functions plus the
// shared topic table plus its own file, and a test on its file alone would
// prove nothing about the first two.
const fs = require('fs');
const path = require('path');
const { extractFunctions, runOnceForAll, runForEachItem, runner } = require('./harness');

const t = runner('Workflow 7 -- Reply Assistant');

const SIGNATURE = 'Abdullah Amir\nFounder, Amitrix Labs\n+923178485713';
const SENDER_NAME = 'Abdullah Amir';
const AREAS = ['Oncology', 'Cardiovascular', 'Neurology', 'Infectious disease', 'Rare disease', 'Other'];
const REQUEST = { model: 'claude-sonnet-5-5', max_tokens: 16000,
  output_config: { effort: 'high', format: { type: 'json_schema', schema: { type: 'object' } } } };

const REPLY = path.join(__dirname, 'code_reply.js');
const ASSEMBLE = path.join(__dirname, 'code_reply_assemble.js');
const REVIEW = path.join(__dirname, 'code_reply_review.js');
const REMIND = path.join(__dirname, 'code_reply_remind.js');
const ACK = path.join(__dirname, 'code_command_ack.js');

const topics = fs.readFileSync(path.join(__dirname, 'code_reply_topics.js'), 'utf8');
const rules = fs.readFileSync(path.join(__dirname, '..', 'drafting', 'code_assemble.js'), 'utf8')
  .split('// Node body')[0];

// Build Reply ships as code_reply_topics.js + code_reply.js.
const bakeReply = (s) => topics + '\n' + s
  .replace('__REPLY_SYSTEM_PROMPT__', JSON.stringify('SYSTEM PROMPT (substituted at build time)'))
  .replace('__CLAUDE_REQUEST__', JSON.stringify(REQUEST))
  .replace('__SENDER_NAME__', JSON.stringify(SENDER_NAME))
  .replace('__THERAPEUTIC_AREAS__', JSON.stringify(AREAS));

// Assemble Reply ships as drafting's rules + code_reply_topics.js + its own file.
const bakeAssemble = (s) => rules + '\n' + topics + '\n' + s
  .replace('__SIGNATURE__', JSON.stringify(SIGNATURE));

// ---------------------------------------------------------------------------
// The claims library, as Find Replies To Answer aggregates it. Only active and
// confirmed rows reach the workflow; PR-BOTH is the "two CROs" line the
// operator barred from a reply, and L-NP is the link row -- empty and
// unconfirmed in the live library today, so a reply may carry no URL at all.
// ---------------------------------------------------------------------------
const LIB = [
  { code: 'D2', slot: 'description', body: 'an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link', countries: null, measured: false, confirmed: true, active: true, capabilities: ['answers', 'qualifies', 'booking-link'] },
  { code: 'ANG-HOURS', slot: 'angle', body: 'Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.', countries: null, measured: false, confirmed: true, active: true, capabilities: [] },
  { code: 'BEN-BRIEF', slot: 'benefit', body: 'It collects the therapeutic area and study phase before your first call.', countries: null, measured: false, confirmed: true, active: true, capabilities: ['study-details'] },
  { code: 'BEN-SEE', slot: 'benefit', body: 'You can see every sponsor lead it captured, and any question it passed to your team.', countries: null, measured: false, confirmed: true, active: true, capabilities: ['dashboard'] },
  { code: 'PR-TR', slot: 'proof', body: "It's live at NoblePath, an oncology CRO in Türkiye.", countries: ['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'], measured: false, confirmed: true, active: true, capabilities: [] },
  { code: 'PR-MX', slot: 'proof', body: "It's live at Vertex Clinical Research in Mexico.", countries: ['Mexico', 'Brazil', 'Argentina'], measured: false, confirmed: true, active: true, capabilities: [] },
  { code: 'PR-BOTH', slot: 'proof', body: "It's live at two CROs, in Türkiye and Mexico.", countries: null, measured: false, confirmed: true, active: true, capabilities: [] },
  { code: 'A1', slot: 'ask', body: 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.', countries: null, measured: false, confirmed: true, active: true, capabilities: [] },
  { code: 'L-NP', slot: 'link', body: '', countries: null, measured: false, confirmed: false, active: false, capabilities: [] },
];

// Lead 26 Pharmahungary, the first real reply this pipeline received
// (2026-10-07) -- the row Find Replies To Answer returns for it.
const THEIRS = [
  'Dear Abdullah Amir,',
  '',
  'Thank you for introducing your services. We are currently not planning a project that might',
  'benefit from them. However, we are keeping them in mind.',
  '',
  'Could you please let us know where you learned from Pharmahungary Group, e.g. online search,',
  'scientific publication, etc.?',
  '',
  'Best regards,',
  'Andras',
].join('\n');

const FIRST_TOUCH = [
  'Hello,',
  '',
  'Your site lists oncology and cardiovascular. Sponsors often research CROs outside your working hours.',
  '',
  "If this isn't relevant, reply 'no' and I won't follow up.",
  '',
  SIGNATURE,
].join('\n');

const ROW = {
  inbound_message_id: '<01bd01dd562e$5fe83240$1fb896c0$@pharmahungary.com>',
  received_at: '2026-10-07T07:35:06.000Z',
  from_addr: 'andras.nogradi@pharmahungary.com',
  subject: 'RE: Oncology and cardiovascular on your site',
  in_reply_to: '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>',
  references_raw: '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>',
  thread_ids: ['<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>'],
  reply_text: THEIRS,
  lead_id: 26,
  company_name: 'Pharmahungary',
  domain: 'pharmahungary.com',
  country: 'Hungary',
  source: 'ichgcp.net CRO directory',
  their_name: null,
  thread: [{ subject: 'Oncology and cardiovascular on your site', body: FIRST_TOUCH,
    sent_at: '2026-10-06T08:20:29.000Z', message_id: '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>',
    variant: 'role-inbox/D2.ANG-HOURS.BEN-BRIEF.PR-TR.A1' }],
  library: LIB,
  therapeutic_areas: ['cardiovascular', 'metabolic', 'renal', 'inflammation', 'oncology', 'musculoskeletal'],
  phases: [],
  city: 'Budapest',
  founder_name: '',
  employee_estimate: null,
};
const row = (over) => Object.assign({}, ROW, over || {});

// ---------------------------------------------------------------------------
// Build Reply
// ---------------------------------------------------------------------------

console.log('\nBuild Reply -- the four sources, and the questions it refuses to answer');
const built = runOnceForAll(REPLY, [{ json: ROW }], {}, bakeReply);
t.check('one request per queued message', built.length, 1);
const b = built[0].json;

t.check('the prospect text being answered is theirs, above the quoted thread',
  b.prompt.indexOf('Could you please let us know where you learned from') !== -1, true);
t.check('our own first touch is in the prompt, as it was sent',
  b.prompt.indexOf('Your site lists oncology and cardiovascular') !== -1, true);
t.check('their record carries the boundary sentences, so two facts cannot be joined',
  b.prompt.indexOf('This says nothing about which area any particular trial belongs to') !== -1, true);
t.check('how we found them is a fact on the record, and the only answer to that question',
  b.prompt.indexOf('ichgcp.net CRO directory') !== -1 &&
  b.prompt.indexOf('the only answer to "where did you hear about us"') !== -1, true);
t.check('the proof offered is the one matched to Hungary',
  [b.library_pools.proof.map((l) => l.code), b.country], [['PR-TR'], 'Hungary']);

// The operator's instruction, 2026-10-09: never reuse the "two CROs" line and
// never call Vertex Clinical Research a CRO -- it is a clinical research centre.
// The filter is on the TEXT, so a reworded library row cannot escape it.
t.check('the "two CROs" line is never offered to a reply, for any country',
  runOnceForAll(REPLY, [{ json: row({ country: 'India' }) }], {}, bakeReply)[0].json.library_pools.proof, []);
t.check('... so a lead the library can only serve with it is offered no proof, rather than that line',
  runOnceForAll(REPLY, [{ json: row({ country: 'India' }) }], {}, bakeReply)[0].json.prompt
    .indexOf('PROOF -- only if you name a deployment; it is matched to their country:\n  (none)') !== -1, true);
t.check('a line naming Vertex alongside "CRO" would be filtered too, on its text alone',
  runOnceForAll(REPLY, [{ json: row({ country: 'Mexico', library: LIB.map(
    (l) => (l.code === 'PR-MX' ? Object.assign({}, l, { body: "It's live at Vertex, a CRO in Mexico." }) : l)) }) }],
  {}, bakeReply)[0].json.library_pools.proof, []);
t.check('the unchanged Vertex line IS offered to Mexico -- it never calls them a CRO',
  runOnceForAll(REPLY, [{ json: row({ country: 'Mexico' }) }], {}, bakeReply)[0].json.library_pools.proof
    .map((l) => l.code), ['PR-MX']);

t.check('no link is offered while the library holds no confirmed link line with a URL',
  b.library_pools.link, []);
t.check('... and one is, once it is confirmed and holds a URL',
  runOnceForAll(REPLY, [{ json: row({ library: LIB.map((l) => (l.code === 'L-NP'
    ? Object.assign({}, l, { body: 'https://example.org/nova-one-pager', active: true, confirmed: true })
    : l)) }) }], {}, bakeReply)[0].json.library_pools.link.map((l) => l.code), ['L-NP']);

t.check('the five topics are detected in THEIR text, not ours -- they asked none of them here',
  b.open_topics, []);
const asked = runOnceForAll(REPLY, [{ json: row({ reply_text:
  'Thanks. What does it cost per month, does it integrate with Salesforce, and how long to go live?' }) }],
{}, bakeReply)[0].json;
t.check('pricing, integrations and timelines are all detected together',
  asked.open_topics, ['pricing', 'integrations', 'timelines']);
t.check('... and the prompt tells the model to confirm rather than answer, with no hint',
  asked.prompt.indexOf('QUESTIONS YOU MAY NOT ANSWER') !== -1 &&
  asked.prompt.indexOf('no hint of what the answer might be') !== -1, true);
t.check('security and languages are detected too',
  [runOnceForAll(REPLY, [{ json: row({ reply_text: 'Where is the data hosted? Is it GDPR compliant?' }) }], {}, bakeReply)[0].json.open_topics,
    runOnceForAll(REPLY, [{ json: row({ reply_text: 'Does it speak Hungarian?' }) }], {}, bakeReply)[0].json.open_topics],
  [['security'], ['languages']]);

t.check('the subject is theirs with exactly one Re:',
  [b.subject, runOnceForAll(REPLY, [{ json: row({ subject: 'Re: RE: Fwd: hello' }) }], {}, bakeReply)[0].json.subject],
  ['Re: Oncology and cardiovascular on your site', 'Re: hello']);
t.check('the greeting invents no name -- it is used only when the writer IS the contact on file',
  [b.greeting, runOnceForAll(REPLY, [{ json: row({ their_name: 'Andras' }) }], {}, bakeReply)[0].json.greeting],
  ['Hello,', 'Hello Andras,']);
t.check('the quoted header names the address that wrote', b.quote_attribution,
  'andras.nogradi@pharmahungary.com');

t.check('prompt caching: the cached block is exactly the build-time system prompt',
  [b.request.system.length, b.request.system[0].cache_control, b.request.system[0].text],
  [1, { type: 'ephemeral' }, 'SYSTEM PROMPT (substituted at build time)']);
t.check('... and nothing per-prospect is marked for caching',
  JSON.stringify(b.request.messages).indexOf('cache_control'), -1);
t.check('the model and its parameters are the drafting node\'s',
  [b.request.model, b.request.output_config.effort], ['claude-sonnet-5-5', 'high']);

t.check('a message with no text to answer costs no call',
  runOnceForAll(REPLY, [{ json: row({ reply_text: '' }) }], {}, bakeReply).length, 0);
t.check('a library with no ask line cannot complete a reply, so none is attempted',
  runOnceForAll(REPLY, [{ json: row({ library: LIB.filter((l) => l.slot !== 'ask') }) }], {}, bakeReply).length, 0);
t.check('the Postgres empty-queue placeholder produces nothing',
  runOnceForAll(REPLY, [{ json: { success: true } }], {}, bakeReply).length, 0);

// ---------------------------------------------------------------------------
// Assemble Reply
// ---------------------------------------------------------------------------

console.log('\nAssemble Reply -- drafting\'s rules, the reply\'s own, and the frame');

const GOOD = {
  body: 'Thank you for the clear answer, and for keeping us in mind. We found you in the ichgcp.net '
    + 'CRO directory. The assistant collects the therapeutic area and study phase before your first '
    + 'call, so nothing is lost while a project is still being planned.',
  ask: 'Would a 48-hour demo built on your own material be worth a look?',
  claims: ['BEN-BRIEF', 'A1'],
  deferred: [],
  flag: '',
};

function response(over) {
  const parsed = Object.assign({}, GOOD, over || {});
  return { stop_reason: 'end_turn', model: 'claude-sonnet-5-5', usage: { input_tokens: 10, output_tokens: 20 },
    content: [{ type: 'text', text: JSON.stringify(parsed) }] };
}

function assemble(over, src) {
  return runForEachItem(ASSEMBLE, [{ json: response(over) }],
    { 'Build Reply': [{ json: Object.assign({}, b, src || {}) }] }, bakeAssemble)[0].json;
}

let a = assemble();
t.check('a clean reply is written, with no rule tags', [a.write, a.flags], [true, []]);
t.check('the variant records the claims it used, in slot order, on the payload the write reads',
  a.payload.draft.variant, 'reply/BEN-BRIEF.A1');
t.check('the frame is appended, never generated: greeting, reply, signature, their message quoted',
  a.payload.draft.body.indexOf('Hello,\n\n') === 0 &&
  a.payload.draft.body.indexOf('\n\n' + SIGNATURE + '\n\nOn Wed, 07 Oct 2026, '
    + 'andras.nogradi@pharmahungary.com wrote:\n> Dear Abdullah Amir,') !== -1, true);
t.check('a reply carries NO Section 5 opt-out line -- it answers a message they sent us',
  a.payload.draft.body.indexOf("reply 'no' and I won't follow up") === -1, true);
t.check('the subject is the threaded one', a.payload.draft.subject, 'Re: Oncology and cardiovascular on your site');
t.check('channel email, and the inbound message it answers rides along',
  [a.payload.draft.channel, a.payload.inbound_message_id], ['email', ROW.inbound_message_id]);

t.check('over 120 words is tagged long',
  assemble({ body: [GOOD.body, GOOD.body, GOOD.body].join(' ') }).flags.indexOf('long') !== -1, true);
t.check('under 40 words is tagged short', assemble({ body: 'Thanks, understood.' }).flags.indexOf('short') !== -1, true);
t.check('no question in the next step is ask-none',
  assemble({ ask: 'Let me know.' }).flags.indexOf('ask-none') !== -1, true);
t.check('a second request in the body is ask-multiple',
  assemble({ body: GOOD.body + ' Would you be open to a call?' }).flags.indexOf('ask-multiple') !== -1, true);
t.check('a claim code the reply was not offered is claim-code',
  assemble({ claims: ['BEN-NOPE'] }).flags.indexOf('claim-code') !== -1, true);
t.check('a number nothing licenses is claim-number',
  assemble({ body: 'It answers 94% of sponsor questions.' }).flags.indexOf('claim-number') !== -1, true);
t.check('a guarantee is claim-guarantee',
  assemble({ body: 'You will never miss a sponsor inquiry again.' }).flags.indexOf('claim-guarantee') !== -1, true);
t.check('calling it a chatbot is claim-chatbot',
  assemble({ body: 'Our chatbot answers them.' }).flags.indexOf('claim-chatbot') !== -1, true);
t.check('naming a tool is claim-named-tool',
  assemble({ body: 'It writes the lead into Salesforce for you.' }).flags.indexOf('claim-named-tool') !== -1, true);
t.check('a therapeutic area nothing in front of the model named is ungrounded-area',
  assemble({ body: 'Given your rare disease work, the assistant helps.' }).flags.indexOf('ungrounded-area') !== -1, true);
t.check('a deployment that is not this country\'s is proof-geo',
  assemble({ body: "It's live at Vertex Clinical Research in Mexico." }).flags.indexOf('proof-geo') !== -1, true);

// The operator's instruction, enforced on the draft as well as on what was
// offered: the model could still write either sentence from memory.
t.check('the "two CROs" wording in a reply is proof-refused',
  assemble({ body: "It's live at two CROs, in Türkiye and Mexico." }).flags.indexOf('proof-refused') !== -1, true);
t.check('calling Vertex Clinical Research a CRO is proof-refused',
  assemble({ body: 'Vertex Clinical Research, a CRO in Mexico, uses it.' }).flags.indexOf('proof-refused') !== -1, true);
t.check('... and the NoblePath line, which really is a CRO, is not',
  assemble({ body: "It's live at NoblePath, an oncology CRO in Türkiye." }).flags.indexOf('proof-refused'), -1);

t.check('a URL at all is tagged while the library offers no confirmed link',
  assemble({ body: 'The one-pager is at https://example.org/x' }).flags.indexOf('unapproved-link') !== -1, true);

// The deferral rules: a topic they raised must be named, and must be answered
// with "we will confirm" rather than with an answer.
const ASKED_SRC = { open_topics: ['pricing', 'security'] };
t.check('a topic they raised that `deferred` does not name is deferral-missing',
  assemble({ deferred: ['pricing'] }, ASKED_SRC).flags.indexOf('deferral-missing') !== -1, true);
t.check('naming both, with a deferral sentence, is clean',
  assemble({ deferred: ['pricing', 'security'],
    body: 'Thank you for the clear answer. I will confirm the pricing and the hosting detail and come '
      + 'back to you on both, rather than guess at either of them here today for you.' },
  ASKED_SRC).flags, []);
t.check('no deferral sentence anywhere is answered-open',
  assemble({ deferred: ['pricing', 'security'] }, ASKED_SRC).flags.indexOf('answered-open') !== -1, true);
t.check('a reply that talks about the topic with no deferral is answered-open too',
  assemble({ deferred: ['pricing', 'security'], body: 'It costs about 400 a month and is hosted in the EU.' },
    ASKED_SRC).flags.indexOf('answered-open') !== -1, true);

t.check('an ask the model left uncoded is attributed from its text, never from the model',
  assemble({ claims: ['BEN-BRIEF'] }).claims, ['BEN-BRIEF', 'A1']);
// Skill v3 section 3, No repeats -- drafting's own rule, running over a reply:
// D2 and BEN-247 both say "it answers", so using both says it twice.
const WITH_247 = LIB.concat([{ code: 'BEN-247', slot: 'benefit',
  body: 'It answers sponsors from your own website, in real time, at any hour.', countries: null,
  measured: false, confirmed: true, active: true, capabilities: ['answers'] }]);
const b247 = runOnceForAll(REPLY, [{ json: row({ library: WITH_247 }) }], {}, bakeReply)[0].json;
t.check('two claims sharing a capability is claim-repeat',
  assemble({ claims: ['D2', 'BEN-247', 'A1'] }, b247).flags.indexOf('claim-repeat') !== -1, true);
t.check('... and two that share none is not',
  assemble({ claims: ['BEN-BRIEF', 'BEN-SEE', 'A1'] }, b247).flags.indexOf('claim-repeat'), -1);

t.check('a failed call writes nothing', [assembleFail({ error: 'HTTP 529' }).write,
  assembleFail({ stop_reason: 'max_tokens' }).write], [false, false]);
t.check('... and says why', assembleFail({ error: 'HTTP 529' }).skip_reason.indexOf('HTTP 529') !== -1, true);
t.check('text that is not the schema writes nothing',
  assembleFail({ stop_reason: 'end_turn', content: [{ type: 'text', text: '{"body":"x"}' }] }).write, false);

function assembleFail(resp) {
  return runForEachItem(ASSEMBLE, [{ json: resp }], { 'Build Reply': [{ json: b }] }, bakeAssemble)[0].json;
}

// ---------------------------------------------------------------------------
// Build Review Email
// ---------------------------------------------------------------------------

console.log('\nBuild Review Email -- the reply, the proposal and a one-time code');

const WRITTEN = {
  written: true, draft_id: 261, lead_id: 26, code: 'NS-2345678ABC',
  issued_at: '2026-10-09T10:00:00.000Z', expires_at: '2026-10-11T10:00:00.000Z',
  subject: a.payload.draft.subject, body: a.payload.draft.body,
  hold_reason: 'reply: waiting for your APPROVE / REJECT / EDIT NS-2345678ABC',
  review: a.review, inbound_message_id: ROW.inbound_message_id,
  operator_email: 'abdullah@amitrixlabs.com',
};
const { reviewEmail } = extractFunctions(REVIEW, '// Node body', ['reviewEmail']);
let r = reviewEmail(WRITTEN);
t.check('it goes to the operator address the statement read at runtime',
  [r.notify, r.notify_to], [true, 'abdullah@amitrixlabs.com']);
t.check('the subject names the company and the code', r.subject,
  '[Nova Scout] REPLY DRAFTED for Pharmahungary -- NS-2345678ABC');
t.check('it shows what they wrote', r.text.indexOf('Could you please let us know where you learned') !== -1, true);
t.check('it shows the exact bytes that would be sent, subject and all',
  r.text.indexOf('Subject: Re: Oncology and cardiovascular on your site') !== -1 &&
  r.text.indexOf(a.payload.draft.body) !== -1, true);
t.check('it names the address the reply would go to, and says why that is not the contact on file',
  r.text.indexOf('Reply to:  andras.nogradi@pharmahungary.com   (the address that wrote, '
    + 'not the contact on file)') !== -1, true);
t.check('all three commands, each with the code', [r.text.indexOf('  APPROVE NS-2345678ABC') !== -1,
  r.text.indexOf('  REJECT NS-2345678ABC') !== -1, r.text.indexOf('  EDIT NS-2345678ABC') !== -1], [true, true, true]);
t.check('it says the code works once, when it expires, and that only this address can use it',
  r.text.indexOf('The code works once and expires 2026-10-11T10:00:00.000Z') !== -1 &&
  r.text.indexOf('only with SPF and DKIM passing') !== -1, true);
t.check('it promises the 4-hour reminder', r.text.indexOf('remind you in 4 hours') !== -1, true);
t.check('with no rule tags it says so, rather than leaving a blank section',
  r.text.indexOf('No rule tags: it passed every check Workflow 4 runs on a first touch.') !== -1, true);

const asked2 = assemble({ deferred: ['pricing', 'security'],
  body: 'Thanks. I will confirm the pricing and the hosting detail and come back to you on both.' },
ASKED_SRC);
r = reviewEmail(Object.assign({}, WRITTEN, { review: asked2.review }));
t.check('the deferred questions are put in front of the operator, in words',
  [r.text.indexOf('  - what it costs') !== -1,
    r.text.indexOf('  - security, hosting and data protection') !== -1,
    r.text.indexOf('You know these answers. EDIT is how they get in.') !== -1], [true, true, true]);
t.check('a draft with no code written is never emailed',
  [reviewEmail(Object.assign({}, WRITTEN, { written: false })).notify,
    reviewEmail(Object.assign({}, WRITTEN, { code: '' })).notify], [false, false]);
t.check('no operator address, no review email',
  [reviewEmail(Object.assign({}, WRITTEN, { operator_email: null })).notify,
    reviewEmail(Object.assign({}, WRITTEN, { operator_email: 'not an address' })).notify], [false, false]);

// ---------------------------------------------------------------------------
// Build Reminder
// ---------------------------------------------------------------------------

console.log('\nBuild Reminder -- the 4-hour nudge, both kinds of code');

const { reminder } = extractFunctions(REMIND, '// Node body', ['reminder']);
const DUE = {
  code: 'NS-2345678ABC', kind: 'reply', draft_id: 261, lead_id: 26,
  issued_at: '2026-10-09T10:00:00.000Z', expires_at: '2026-10-11T10:00:00.000Z',
  reminded_at: '2026-10-09T10:00:30.000Z', now: '2026-10-09T14:05:00.000Z',
  channel: 'email', variant: 'reply/BEN-BRIEF.A1', subject: 'Re: Oncology and cardiovascular on your site',
  body: a.payload.draft.body, hold_reason: 'reply: waiting for your APPROVE / REJECT / EDIT NS-2345678ABC',
  company_name: 'Pharmahungary', domain: 'pharmahungary.com', country: 'Hungary',
  their_text: THEIRS, operator_email: 'abdullah@amitrixlabs.com',
};
let n = reminder(DUE);
t.check('it says what is waiting and for how long',
  [n.subject, n.text.indexOf('Still waiting on you: a reply to a prospect who wrote to us.') !== -1,
    n.text.indexOf('(4 h ago)') !== -1, n.text.indexOf('(44 h left)') !== -1],
  ['[Nova Scout] still waiting: Pharmahungary -- NS-2345678ABC', true, true, true]);
t.check('a reply reminder shows what they wrote', n.text.indexOf('Could you please let us know') !== -1, true);
t.check('... and the draft, and the three commands',
  [n.text.indexOf(a.payload.draft.body) !== -1, n.text.indexOf('  APPROVE NS-2345678ABC') !== -1], [true, true]);
n = reminder(Object.assign({}, DUE, { kind: 'email-hold', their_text: null,
  body: ['Hello,', '', 'Following up on my note.', '', SIGNATURE].join('\n'),
  hold_reason: 'claim-check: widened "so you know exactly what came in" (BEN-SEE)', variant: 'follow-up-1/BEN-SEE.A1' }));
t.check('an email-hold reminder shows the hold reason instead of a prospect message',
  [n.text.indexOf('Still waiting on you: an email the claim check held for a person.') !== -1,
    n.text.indexOf('  claim-check: widened') !== -1,
    n.text.indexOf('Could you please let us know') === -1], [true, true, true]);
t.check('no operator address, no reminder',
  reminder(Object.assign({}, DUE, { operator_email: '' })).notify, false);
t.check('it says a fresh code follows if this one lapses',
  reminder(DUE).text.indexOf('the next\nreminder carries a fresh code') !== -1, true);

// ---------------------------------------------------------------------------
// Build Command Ack
// ---------------------------------------------------------------------------

console.log('\nBuild Command Ack -- what the operator is told back');

const { commandAck } = extractFunctions(ACK, '// Node body', ['commandAck']);
const APPLIED = {
  recorded: true, accepted: true, handled: true, notify: true, notify_to: 'abdullah@amitrixlabs.com',
  message_id: '<op-1@amitrixlabs.com>', subject: 'Re: REPLY DRAFTED', command: 'APPROVE',
  code: 'NS-2345678ABC', code_kind: 'reply', spf_pass: true, dkim_pass: true, sender_ok: true,
  refused_reason: null, draft_id: 261, draft_status: 'approved', edited_body: null, outcome: 'approved',
};
let k = commandAck(APPLIED);
t.check('an accepted APPROVE says so, and what happens next',
  [k.subject, k.text.indexOf('APPROVE accepted for draft 261 (reply).') !== -1,
    k.text.indexOf('goes out on the next send tick') !== -1,
    k.text.indexOf('it never skips the') !== -1],
  ['[Nova Scout] APPROVE ok -- NS-2345678ABC', true, true, true]);
k = commandAck(Object.assign({}, APPLIED, { command: 'EDIT', outcome: 'edited',
  edited_body: 'Hello,\n\nMy own words.\n\n' + SIGNATURE }));
t.check('an accepted EDIT shows exactly what will be sent',
  k.text.indexOf('WHAT WILL BE SENT\n\nHello,\n\nMy own words.') !== -1, true);
k = commandAck(Object.assign({}, APPLIED, { command: 'REJECT', outcome: 'rejected', draft_status: 'rejected' }));
t.check('an accepted REJECT says nothing will be sent, and how to ask for another draft',
  k.text.indexOf('nothing will be sent') !== -1 && k.text.indexOf('reply_approvals') !== -1, true);
k = commandAck(Object.assign({}, APPLIED, { accepted: false, draft_status: null,
  refused_reason: 'that one-time code expired at 2026-10-11T10:00:00Z' }));
t.check('a refusal names the reason and promises nothing happened',
  [k.subject, k.text.indexOf('Reason: that one-time code expired') !== -1,
    k.text.indexOf('Nothing changed and nothing will be sent') !== -1],
  ['[Nova Scout] command refused -- NS-2345678ABC', true, true]);
t.check('a refused, unauthenticated message is answered with silence, not an oracle',
  commandAck(Object.assign({}, APPLIED, { accepted: false, notify: false, handled: false, sender_ok: false,
    refused_reason: 'not sent from the operator address' })).notify, false);
t.check('a re-delivered command is not acknowledged twice (recorded false -> notify false)',
  commandAck(Object.assign({}, APPLIED, { notify: false, recorded: false })).notify, false);
t.check('no operator address, no acknowledgement',
  commandAck(Object.assign({}, APPLIED, { notify_to: null })).notify, false);

// --- a site's reply uses site claims only (2026-10-09, migration 019) -------
const SITE_LIB = LIB.concat([
  { code: 'D-SITE2', slot: 'description', body: 'an AI assistant for your website that turns study inquiries from sponsors and CROs into qualified leads', countries: null, measured: false, confirmed: true, active: true, capabilities: ['qualifies', 'captures-lead'], company_types: ['site', 'SMO'] },
  { code: 'PR-SITE', slot: 'proof', body: "It's live at Vertex Clinical Research, a research center in Mexico.", countries: null, measured: false, confirmed: true, active: true, capabilities: [], company_types: ['site', 'SMO'] },
  { code: 'A-SITE1', slot: 'ask', body: 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.', countries: null, measured: false, confirmed: true, active: true, capabilities: [], company_types: ['site', 'SMO'] },
]);
const poolCodes = (b) => Object.keys(b.library_pools).reduce((a, s) => a.concat(b.library_pools[s].map((l) => l.code)), []);
const croReply = runOnceForAll(REPLY, [{ json: row({ library: SITE_LIB }) }], {}, bakeReply)[0].json;
t.check('a CRO reply is offered no site line', poolCodes(croReply).filter((c) => /SITE/.test(c)), []);
const siteReply = runOnceForAll(REPLY, [{ json: row({ library: SITE_LIB, company_type: 'site', country: 'Mexico' }) }], {}, bakeReply)[0].json;
t.check('a site reply is offered only site lines, and PR-SITE even in Mexico', [poolCodes(siteReply).sort(), siteReply.company_type],
  [['A-SITE1', 'D-SITE2', 'PR-SITE'], 'site']);
t.check('the site reply prompt says they are a site, not a CRO', /THEY ARE an independent clinical research site, NOT a CRO/.test(siteReply.prompt), true);

t.done();

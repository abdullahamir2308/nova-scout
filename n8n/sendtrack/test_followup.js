// Unit tests for the composed follow-ups -- code_followup.js (Build Follow-Up)
// and code_followup_assemble.js (Assemble Follow-Up), drafting skill v3 section 8.
//
//   node test_followup.js
//
// Assemble Follow-Up ships as drafting's code_assemble.js above its "Node body"
// marker followed by code_followup_assemble.js; the tests run exactly that
// concatenation, with the same build substitutions, so they exercise the JS
// that ships rather than a copy of it.
//
// The test worth reading is still the cross-check: a follow-up this workflow
// writes is run through the SEND path's own eligibility function. A follow-up
// the send path would refuse (stale signature, no opt-out, a link) would sit
// approved forever, so it has to be sendable by construction.
const fs = require('fs');
const path = require('path');
const { extractFunctions, runOnceForAll, runForEachItem, runner } = require('./harness');

const SIG = 'Test Sender\nFounder, Test Labs\n+10000000000';
const OPT = "If this isn't relevant, reply 'no' and I won't follow up.";
const TAXONOMY = ['Oncology', 'Cardiovascular', 'Central Nervous System', 'Immunology', 'Infectious Disease',
  'Endocrinology', 'Metabolic Disorders', 'Respiratory', 'Rare Diseases', 'Internal Medicine', 'Anesthesiology',
  'Dermatology', 'Rheumatology', 'Ophthalmology', 'Gastroenterology', 'Nephrology', 'Hematology', 'Other'];
const REQUEST = { model: 'claude-sonnet-5-5', max_tokens: 16000,
  output_config: { effort: 'high', format: { type: 'json_schema', schema: { type: 'object' } } } };
const SYSTEM = 'TEST FOLLOW-UP SYSTEM PROMPT';

const BUILD = path.join(__dirname, 'code_followup.js');
const ASSEMBLE = path.join(__dirname, 'code_followup_assemble.js');
const RULES_SRC = fs.readFileSync(path.join(__dirname, '..', 'drafting', 'code_assemble.js'), 'utf8');
const RULES = RULES_SRC.slice(0, RULES_SRC.indexOf('// Node body'));

const bakeBuild = (s) => s
  .replace('__SENDER_NAME__', JSON.stringify('Test Sender'))
  .replace('__FOLLOWUP_SYSTEM_PROMPT__', JSON.stringify(SYSTEM))
  .replace('__CLAUDE_REQUEST__', JSON.stringify(REQUEST))
  .replace('__THERAPEUTIC_AREAS__', JSON.stringify(TAXONOMY));
const bakeAssemble = (s) => RULES + '\n' + s
  .replace('__SIGNATURE__', JSON.stringify(SIG))
  .replace('__SENDER_NAME__', JSON.stringify('Test Sender'));

const { ineligibility } = extractFunctions(
  path.join(__dirname, 'code_decide.js'), '// Node body', ['ineligibility'],
  (s) => s.replace('__SIGNATURE__', JSON.stringify(SIG)));

const t = runner('Workflow 6 -- composed follow-ups (skill v3 section 8)');

// --- fixtures ----------------------------------------------------------------------

function row(code, slot, body, caps, extra) {
  return Object.assign({ code: code, slot: slot, body: body, countries: null, measured: false, confirmed: true,
    capabilities: caps || null }, extra || {});
}
const TR = ['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'];
const LIBRARY = [
  row('D1', 'description', 'an AI assistant for your website that turns sponsor inquiries into qualified leads', ['qualifies', 'captures-lead']),
  row('D2', 'description', 'an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link', ['answers', 'qualifies', 'booking-link']),
  row('ANG-HOURS', 'angle', 'Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.'),
  row('ANG-STAKES', 'angle', 'A single sponsor inquiry can be a multi-million-dollar study.'),
  row('BEN-247', 'benefit', 'It answers sponsors from your own website, in real time, at any hour.', ['answers']),
  row('BEN-BOOK', 'benefit', 'It sends qualified sponsors your booking link, so they can book a call with your team.', ['booking-link']),
  row('BEN-CAPTURE', 'benefit', 'A sponsor who shares their details becomes a named lead: company, contact, therapeutic area and study phase.', ['captures-lead', 'study-details']),
  row('BEN-BRIEF', 'benefit', 'It collects the therapeutic area and study phase before your first call.', ['study-details']),
  row('BEN-DECK', 'benefit', 'It sends your capabilities deck the moment a sponsor asks for it.', ['deck']),
  row('PR-TR', 'proof', "It's live at NoblePath, an oncology CRO in Türkiye.", null, { countries: TR }),
  row('PR-MX', 'proof', "It's live at Vertex Clinical Research in Mexico.", null, { countries: ['Mexico', 'Brazil', 'Argentina'] }),
  row('PR-BOTH', 'proof', "It's live at two CROs, in Türkiye and Mexico."),
  row('A1', 'ask', 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.'),
  row('A2', 'ask', "Worth a 48-hour demo on your own material? Reply yes and I'll set it up."),
];

const FIRST_BODY = 'Hello,\n\nYour site lists oncology and immunology. Sponsors often write outside your working hours, ' +
  'so an 11pm inquiry waits until morning.\n\nWe built an AI intake assistant for your website that answers sponsors, ' +
  'qualifies them, and sends them your booking link (we call it Nova). It answers from your own website at any hour. ' +
  "It's live at NoblePath, an oncology CRO in Türkiye.\n\nWould a 48-hour demo built on your own material be worth a " +
  'look? One word back is enough.\n\n' + OPT + '\n\n' + SIG;

const due = (over) => Object.assign({
  lead_id: 91, action: 'draft-follow-up', next_follow_up: 1, country: 'Poland',
  first_subject: 'Oncology and immunology sponsor inquiries', first_body: FIRST_BODY,
  first_variant: 'role-inbox/D2.ANG-HOURS.BEN-247.PR-TR.A1+unconfirmed-claim',
  first_sent_at: '2026-09-22T12:50:29.045Z', last_sent_at: '2026-09-22T12:50:29.045Z',
  last_follow_up_body: null, library: LIBRARY,
}, over);

function build(rows) {
  return runOnceForAll(BUILD, rows.map((r) => ({ json: r })), {}, bakeBuild);
}
const B1 = build([due()])[0];
const codes = (list) => (list || []).map((l) => l.code);

// ===================================================================================
// Build Follow-Up
// ===================================================================================

t.check('a due lead becomes a composition request, paired with its input row',
  [B1.json.needs_model, B1.json.follow_up, B1.pairedItem], [true, 1, { item: 0 }]);
t.check('the first email\'s claim codes are read off its variant, against the live library',
  B1.json.first_codes, ['D2', 'ANG-HOURS', 'BEN-247', 'PR-TR', 'A1']);
t.check('#1 is offered only the angles the first email did not use', codes(B1.json.library_pools.angle), ['ANG-STAKES']);
t.check('and only benefits that neither it used nor repeat a capability it made (BEN-BOOK repeats D2\'s booking link)',
  codes(B1.json.library_pools.benefit), ['BEN-CAPTURE', 'BEN-BRIEF', 'BEN-DECK']);
t.check('the proof line is the one for its country, and every ask is offered',
  [codes(B1.json.library_pools.proof), codes(B1.json.library_pools.ask)], [['PR-TR'], ['A1', 'A2']]);
t.check('no description is offered: the first email already introduced it', B1.json.library_pools.description, undefined);
const P1 = B1.json.prompt;
t.check('the prompt quotes the first email\'s text, without greeting, opt-out or signature',
  [P1.indexOf('Your site lists oncology and immunology.') !== -1, P1.indexOf('Hello,'), P1.indexOf(OPT), P1.indexOf('Test Labs')],
  [true, -1, -1, -1]);
t.check('and tells the model which claims it used',
  P1.indexOf('The first email used these approved claims: D2, ANG-HOURS, BEN-247, PR-TR, A1.') !== -1, true);
t.check('a benefit is shown with what it is about', P1.indexOf('[BEN-DECK] It sends your capabilities deck the moment a sponsor asks for it.  (about: deck)') !== -1, true);
t.check('the task states the skill\'s numbers', P1.indexOf('40-70 words') !== -1, true);
t.check('nothing about the prospect reaches the model but the first email -- no library row text names them',
  P1.indexOf('Company:'), -1);
t.check('the request is the drafting request plus this system prompt and message',
  [B1.json.request.model, B1.json.request.max_tokens, B1.json.request.output_config.effort, B1.json.request.system,
   B1.json.request.messages], ['claude-sonnet-5-5', 16000, 'high', SYSTEM, [{ role: 'user', content: P1 }]]);
t.check('no sampling parameter is sent (a 400 on Sonnet 5.5)',
  ['temperature', 'top_p', 'top_k'].filter((k) => k in B1.json.request), []);
t.check('the areas the first email names are not "absent"; the others are',
  [B1.json.absent_areas.indexOf('Oncology'), B1.json.absent_areas.indexOf('Immunology'), B1.json.absent_areas.indexOf('Cardiovascular') !== -1],
  [-1, -1, true]);
t.check('the greeting is the first email\'s', B1.json.greeting, 'Hello,');

// A v2-era first email: P2/O2 are codes the v3 library does not hold, even though
// PR-TR and A2 still exist under the same names.
const V2 = build([due({ first_variant: 'role-inbox/P2.O2.PR-TR.A2+unconfirmed-claim' })])[0].json;
t.check('a v2 first email: the model is told its claims cannot be read off codes',
  [V2.prompt.indexOf('written before the current claims list') !== -1, V2.prompt.indexOf('The first email used these approved claims')],
  [true, -1]);
t.check('and its claim headings say to leave out what it already made -- not "did not use", which nobody knows',
  [V2.prompt.indexOf('BENEFITS -- leave out any whose point the first email already made:') !== -1,
   V2.prompt.indexOf('the first email did not use:')], [true, -1]);
t.check('a v3 first email\'s lists are filtered, and say so',
  P1.indexOf('BENEFITS the first email did not use:') !== -1, true);
t.check('and is offered every angle and benefit, to judge from the text (first_email_covers)',
  [codes(V2.library_pools.angle), codes(V2.library_pools.benefit)],
  [['ANG-HOURS', 'ANG-STAKES'], ['BEN-247', 'BEN-BOOK', 'BEN-CAPTURE', 'BEN-BRIEF', 'BEN-DECK']]);

const B2 = build([due({ next_follow_up: 2, last_follow_up_body: 'Hello,\n\nFollowing up on my note.\n\n' + OPT + '\n\n' + SIG })])[0].json;
t.check('#2 is offered no angle or benefit -- it adds no claim', [B2.library_pools.angle, B2.library_pools.benefit], [[], []]);
t.check('#2 is told it is the short final note, with the skill\'s ceiling',
  [B2.prompt.indexOf('the short final note') !== -1, B2.prompt.indexOf('at most 40 words') !== -1], [true, true]);
t.check('#2 sees the follow-up that already went out', B2.prompt.indexOf('Following up on my note.') !== -1, true);

const LOST = build([due({ lead_id: 104, action: 'mark-lost', next_follow_up: 3 })])[0];
t.check('mark-lost needs no model and carries no draft',
  [LOST.json.needs_model, LOST.json.payload], [false, { lead_id: 104, action: 'mark-lost', draft: null }]);
const SPENT = LIBRARY.filter((r) => ['angle', 'benefit'].indexOf(r.slot) === -1 || r.code === 'ANG-HOURS' || r.code === 'BEN-247');
t.check('a #1 with no unused angle or benefit left is held, not written with a repeat', build([due({ library: SPENT })]), []);
t.check("an empty queue's { success: true } placeholder becomes zero items, not a crash",
  runOnceForAll(BUILD, [{ json: { success: true } }], {}, bakeBuild), []);
let threw = '';
try { build([due({ next_follow_up: 3 })]); } catch (e) { threw = e.message; }
t.check('there is no third follow-up (Section 9: maximum two)', /no follow-up #3/.test(threw), true);
const MANY = build([due(), due({ lead_id: 104, action: 'mark-lost' }), due({ lead_id: 7 })]);
t.check('one output per due lead, each paired with its own input row',
  MANY.map((o) => [o.json.lead_id, o.pairedItem.item]), [[91, 0], [104, 1], [7, 2]]);

// ===================================================================================
// Assemble Follow-Up
// ===================================================================================

const CLEAN = {
  body: 'Following up on my note about after-hours sponsor inquiries. When a sponsor asks for your capabilities ' +
    'deck, the assistant sends it the moment they ask, at any hour, so the deck reaches them while they are ' +
    'still comparing CROs.',
  ask: 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.',
  added_claim: 'BEN-DECK', claims: ['BEN-DECK', 'A1'], first_email_covers: ['ANG-HOURS', 'BEN-247'],
};
function claude(gen, over) {
  return Object.assign({ model: 'claude-sonnet-5-5', stop_reason: 'end_turn', stop_details: null,
    content: [{ type: 'thinking', thinking: '', signature: 's' }, { type: 'text', text: typeof gen === 'string' ? gen : JSON.stringify(gen) }],
    usage: { input_tokens: 2000, output_tokens: 700 } }, over || {});
}
function assemble(genOver, built, respOver) {
  const src = built || B1;
  return runForEachItem(ASSEMBLE, [{ json: claude(Object.assign({}, CLEAN, genOver || {}), respOver) }],
    { 'Build Follow-Up': [{ json: src.json || src }] }, bakeAssemble)[0].json;
}
const flagsOf = (genOver, built) => assemble(genOver, built).flags;
const has = (f, x) => f.indexOf(x) !== -1;

const A = assemble();
const D = A.payload.draft;
t.check('a clean follow-up #1 is written with no tag at all', [A.write, A.flags], [true, []]);
t.check('variant = follow-up number / the claims it used, in slot order', D.variant, 'follow-up-1/BEN-DECK.A1');
t.check('the subject stays in the first email\'s thread', D.subject, 'Re: Oncology and immunology sponsor inquiries');
t.check('the body is greeting / note / ask / opt-out / signature / the quoted first email',
  D.body.indexOf('Hello,\n\n' + CLEAN.body + '\n\n' + CLEAN.ask + '\n\n' + OPT + '\n\n' + SIG + '\n\nOn Tue, 22 Sep 2026, Test Sender wrote:\n> Hello,') === 0,
  true);
t.check('its words are counted over the note (39) and the ask (18) only', A.words, 57);
const asCandidate = (draft, over) => Object.assign({
  draft_id: 90, lead_id: 91, channel: 'email', status: 'approved', variant: draft.variant,
  subject: draft.subject, body: draft.body, domain: 'bitrial.hu', country: 'Poland',
  lead_status: 'sent', to_addr: 'info@bitrial.hu', verified: true, lead_domain_blocked: false,
  recipient_domain_blocked: false, prior_sends: 1, prior_manual_sends: 0, follow_ups_sent: 0,
  replied: false, bounced: false,
}, over || {});
t.check('the composed follow-up passes the SEND path\'s own checks, once approved', ineligibility(asCandidate(D)), null);

// --- skill section 8 ---------------------------------------------------------------
t.check('more than 70 words is `long`',
  has(flagsOf({ body: CLEAN.body + ' ' + 'It sends the deck to the sponsor who asks for it, at any hour of the day or night, so nobody waits for it.'.repeat(1) }), 'long'), true);
t.check('fewer than 40 words is `short`', has(flagsOf({ body: 'Following up on my note. The assistant sends your capabilities deck the moment a sponsor asks.' }), 'short'), true);
t.check('no added claim is `fu-no-new-claim`', has(flagsOf({ added_claim: '', claims: ['A1'] }), 'fu-no-new-claim'), true);
t.check('an added claim that is not an angle or benefit is `fu-no-new-claim`',
  has(flagsOf({ added_claim: 'A1', claims: ['A1'] }), 'fu-no-new-claim'), true);
t.check('two new claims is `fu-no-new-claim` -- exactly one',
  has(flagsOf({ claims: ['ANG-STAKES', 'BEN-DECK', 'A1'] }), 'fu-no-new-claim'), true);
t.check('adding what the first email already made (its own report) is `fu-repeat`',
  has(flagsOf({ added_claim: 'BEN-DECK', first_email_covers: ['BEN-DECK'] }), 'fu-repeat'), true);
t.check('adding a code the first email used, offered or not, is `fu-repeat`',
  has(flagsOf({ added_claim: 'BEN-247', claims: ['BEN-247', 'A1'] }), 'fu-repeat'), true);
t.check('and is `claim-code` too: it was never offered', has(flagsOf({ added_claim: 'BEN-247', claims: ['BEN-247', 'A1'] }), 'claim-code'), true);
const A2 = assemble({ body: 'One last note on this, and then I will leave it with you.', ask: 'Would a 48-hour demo on your own material be worth a look?',
  added_claim: '', claims: ['A1'], first_email_covers: [] }, { json: B2 });
t.check('a clean #2 -- short, no new claim -- carries no tag', [A2.write, A2.flags, A2.payload.draft.variant], [true, [], 'follow-up-2/A1']);
t.check('#2 above 40 words is `long`',
  has(assemble({ body: 'One last note on this, and then I will leave it with you. ' + 'I know inquiries keep coming in and your team is busy with them. '.repeat(3),
    ask: 'Would a 48-hour demo on your own material be worth a look?', added_claim: '', claims: ['A1'], first_email_covers: [] }, { json: B2 }).flags, 'long'), true);
t.check('#2 that adds a claim is `fu-new-claim`',
  has(assemble({ body: 'One last note: the assistant sends your capabilities deck the moment a sponsor asks.', ask: 'Worth a look?',
    added_claim: 'BEN-DECK', claims: ['A1'], first_email_covers: [] }, { json: B2 }).flags, 'fu-new-claim'), true);
t.check('#2 passes the send path\'s checks as follow-up 2', ineligibility(asCandidate(A2.payload.draft, { prior_sends: 2, follow_ups_sent: 1 })), null);

// --- the first touch's rules, inherited verbatim -----------------------------------
const swap = (s) => ({ body: CLEAN.body.replace('so the deck reaches them while they are still comparing CROs.', s) });
t.check('Nova booking a call is `claim-books`', has(flagsOf(swap('It also books the call for you.')), 'claim-books'), true);
t.check('SOPs as its source is `claim-sop`', has(flagsOf(swap('It answers from your own SOPs.')), 'claim-sop'), true);
t.check('a guarantee is `claim-guarantee`', has(flagsOf(swap("You'll never lose a sponsor again.")), 'claim-guarantee'), true);
t.check('a number nobody approved is `claim-number`', has(flagsOf(swap('CROs see 3x more qualified leads.')), 'claim-number'), true);
t.check('the first email\'s own numbers are licensed (11pm, 48-hour)', has(flagsOf(swap('An 11pm request is answered too.')), 'claim-number'), false);
t.check('calling them a sponsor is `prospect-sponsor`', has(flagsOf(swap("You're sponsoring trials yourself.")), 'prospect-sponsor'), true);
t.check('a second question in the body is `ask-multiple`', has(flagsOf(swap('Would you like that?')), 'ask-multiple'), true);
t.check('a scheduling ask is `ask-scheduling`', has(flagsOf({ ask: 'Could we book a 30-minute call next week?' }), 'ask-scheduling'), true);
t.check('a bare product name is `product-name-unbracketed`', has(flagsOf(swap('Nova handles it.')), 'product-name-unbracketed'), true);
t.check('any link is `unapproved-link` -- none is approved for a follow-up', has(flagsOf(swap('See https://example.com.')), 'unapproved-link'), true);
t.check('a deployment outside their geography is `proof-geo`', has(flagsOf(swap("It's live at Vertex in Mexico.")), 'proof-geo'), true);
t.check('naming none is fine -- a follow-up need not prove anything again', has(A.flags, 'proof-missing'), false);
t.check('an area the first email never named is `ungrounded-area`', has(flagsOf(swap('Cardiovascular sponsors ask for it most.')), 'ungrounded-area'), true);
t.check('two benefits sharing a capability are `claim-repeat`',
  has(flagsOf({ added_claim: 'BEN-CAPTURE', claims: ['BEN-CAPTURE', 'BEN-BRIEF', 'A1'] }), 'claim-repeat'), true);
t.check('a banned adjective is `adjective`', has(flagsOf(swap('It is a seamless fit.')), 'adjective'), true);
const UNCONF = Object.assign({}, B1.json, { library_pools: Object.assign({}, B1.json.library_pools, {
  benefit: B1.json.library_pools.benefit.map((l) => Object.assign({}, l, { confirmed: l.code !== 'BEN-DECK' })) }) });
t.check('using an unconfirmed claim is `unconfirmed-claim`', has(assemble({}, { json: UNCONF }).flags, 'unconfirmed-claim'), true);
t.check('an ask the model left uncoded is attributed from its text',
  assemble({ claims: ['BEN-DECK'] }).payload.draft.variant, 'follow-up-1/BEN-DECK.A1');

// --- a failed call writes nothing --------------------------------------------------
t.check('an HTTP error writes nothing', assemble({}, null, { error: { message: 'overloaded' } }).write, false);
t.check('a cut-off response writes nothing', assemble({}, null, { stop_reason: 'max_tokens' }).write, false);
t.check('text that is not the schema writes nothing',
  runForEachItem(ASSEMBLE, [{ json: claude('not json') }], { 'Build Follow-Up': [{ json: B1.json }] }, bakeAssemble)[0].json.write, false);
const raw = (gen) => runForEachItem(ASSEMBLE, [{ json: claude(gen) }], { 'Build Follow-Up': [{ json: B1.json }] }, bakeAssemble)[0].json;
t.check('a response missing a text field (added_claim) writes nothing',
  raw({ body: 'x', ask: 'y?', claims: [], first_email_covers: [] }).write, false);
t.check('a response missing a list (first_email_covers) writes nothing',
  raw({ body: 'x', ask: 'y?', added_claim: '', claims: [] }).write, false);

// --- the rest of the frame rules -------------------------------------------------
t.check('an empty note is `empty`', has(flagsOf({ body: '' }), 'empty'), true);
t.check('a merge tag is `merge-tag`', has(flagsOf(swap('Hi [First Name], over to you.')), 'merge-tag'), true);

t.done();

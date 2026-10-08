// Unit tests for auto-approval (migration 014): code_approval.js (Approval Gate)
// and code_approval_apply.js (Apply Claim Check, shipped as code_approval.js's
// rules section + that file -- the same text build_workflow.py embeds).
//
// Every rule that holds a draft has a case that must HOLD and, where a check
// could over-reach, a near miss that must PASS. test_approval_mutations.py then
// breaks each rule in the source and proves this file fails.

const fs = require('fs');
const path = require('path');
const { runForEachItem, runner } = require('./harness');

const GATE = path.join(__dirname, 'code_approval.js');
const APPLY = path.join(__dirname, 'code_approval_apply.js');
// Section 12's business-hours clock table, as the builds bake it in. A stand-in
// holding the countries these cases use; 'Greenland' is deliberately absent, so
// rule 3b has something to refuse. The real list is checked against the doc by
// both generators, and n8n/sendtrack/test_drift_guards.py proves that guard fires.
const CLOCK_COUNTRIES = ['India', 'Poland', 'Turkey', 'Mexico', 'Germany'];
const baked = function (src) {
  return src.replace('__SEND_CLOCK_COUNTRIES__', JSON.stringify(CLOCK_COUNTRIES));
};
const RULES = (function () {
  const src = baked(fs.readFileSync(GATE, 'utf8'));
  return src.slice(0, src.indexOf('// Node body'));
})();
// As the build ships them: the rules section, then the body, with the node it
// follows baked in (approval_chain.py) -- round 0's names here.
const asShipped = function (src) { return RULES + '\n' + src.replace("$('__GATE__')", "$('Approval Gate')"); };
const REPAIR = path.join(__dirname, 'code_approval_repair.js');
const repairShipped = function (src) { return RULES + '\n' + src.replace("$('__CHECKED__')", "$('Apply Claim Check')"); };

const t = runner('Auto-approval -- Approval Gate and Apply Claim Check (migration 014)');

// --- fixtures -------------------------------------------------------------------

function claim(code, slot, body, confirmed) {
  return { code: code, slot: slot, body: body, confirmed: confirmed !== false };
}
const CLAIMS = [
  claim('D2', 'description', 'an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link'),
  claim('ANG-HOURS', 'angle', 'Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.'),
  claim('BEN-SEE', 'benefit', 'You can see every sponsor lead it captured, and any question it passed to your team.'),
  claim('BEN-DECK', 'benefit', 'It sends your capabilities deck the moment a sponsor asks for it.'),
  claim('PR-BOTH', 'proof', "It's live at two CROs, in Türkiye and Mexico."),
  claim('A1', 'ask', 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.'),
];
const RECORD = 'Company: Innovate Research (the email never names it)\nCountry: India\n' +
  '1. ClinicalTrials.gov lists 2 recruiting trials registered under your company. The ones named are: "Registry of X"; "Study Y".\n' +
  '2. The company is based in Pune. This says nothing about where any trial runs or where any staff sit.';
const SUBJECT = 'Your Registry of X trial and sponsor inquiries';
const BODY = [
  "You're running a recruiting trial, the Registry of X.",
  'Sponsors often research CROs outside your working hours, so an inquiry sent at 11pm waits until morning.',
  'We built an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link (we call it Nova).',
  "You can see every sponsor lead it captured, and any question it passed to your team. It's live at two CROs, in Türkiye and Mexico.",
  'Would a 48-hour demo built on your own material be worth a look? One word back is enough.',
].join(' ');

function ctx(over) {
  return Object.assign({
    kind: 'first-touch',
    auto_approve: true,
    country: 'India',
    record: RECORD,
    claims: CLAIMS,
    first_email: null,
    check: { subject: SUBJECT, text: BODY },
  }, over || {});
}
const CLEAN = 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1';
function firstTouch(emailVariant, approval, linkedinVariant) {
  return {
    json: {
      lead_id: 50,
      approval: approval === undefined ? ctx() : approval,
      payload: {
        lead_id: 50,
        advance: true,
        drafts: [
          { channel: 'email', variant: emailVariant, subject: SUBJECT, body: 'Hello,\n\n' + BODY },
          { channel: 'linkedin', variant: linkedinVariant || 'no-profile/D2.ANG-HOURS.A1', subject: null, body: 'Hello,\n\n...' },
        ],
      },
    },
  };
}
function gate(item) {
  return runForEachItem(GATE, [item], {}, baked)[0].json;
}

// --- Approval Gate: rules 1-4 -----------------------------------------------------

let g = gate(firstTouch(CLEAN));
t.check('a clean email draft goes on to the claim check', [g.needs_check, g.check_target], [true, 0]);
t.check('... and is still pending until the check passes it',
  [g.payload.drafts[0].status, g.payload.drafts[0].approved_by, g.payload.drafts[0].hold_reason], ['pending', null, null]);
t.check('its LinkedIn draft is held, always (Section 6)',
  [g.payload.drafts[1].status, g.payload.drafts[1].hold_reason], ['pending', 'linkedin: always reviewed and sent by hand']);
const req = g.check_request;
t.check('the check is Sonnet 5.5 at effort high with a strict JSON schema (Section 3)',
  [req.model, req.output_config.effort, req.output_config.format.type, req.max_tokens], ['claude-sonnet-5-5', 'high', 'json_schema', 16000]);
t.check('no sampling parameter and no thinking budget (a 400 on this model)',
  ['temperature', 'top_p', 'top_k', 'thinking'].filter(function (k) { return k in req; }), []);
t.check('every object in the schema closes with additionalProperties:false',
  [req.output_config.format.schema.additionalProperties, req.output_config.format.schema.properties.statements.items.additionalProperties], [false, false]);
const prompt = req.messages[0].content;
t.check('the prompt carries every confirmed claim, the record, the subject and the body',
  [prompt.indexOf('[BEN-SEE] (benefit) You can see every sponsor lead') !== -1, prompt.indexOf('based in Pune') !== -1,
   prompt.indexOf('Subject: ' + SUBJECT) !== -1, prompt.indexOf(BODY) !== -1], [true, true, true, true]);
t.check('the system prompt\'s widening example is not draft 99\'s sentence (that one is the held-out test)',
  req.system[0].text.indexOf('exactly what came in'), -1);
t.check('a first touch has no FIRST EMAIL section', prompt.indexOf('THE FIRST EMAIL'), -1);

const input = firstTouch(CLEAN);
const before = JSON.stringify(input.json.payload);
gate(input);
t.check('the gate does not mutate the item it was handed', JSON.stringify(input.json.payload), before);

g = gate(firstTouch(CLEAN, ctx({ auto_approve: false })));
t.check('flag off: held, and no check is paid for',
  [g.needs_check, g.check_request, g.payload.drafts[0].hold_reason], [false, null, 'auto-approve-off: settings.auto_approve_email is false']);
g = gate(firstTouch(CLEAN, ctx({ auto_approve: undefined })));
t.check('flag missing from the context counts as off (only an explicit true turns it on here)',
  [g.needs_check, g.payload.drafts[0].hold_reason.indexOf('auto-approve-off')], [false, 0]);

g = gate(firstTouch(CLEAN + '+long,claim-repeat'));
t.check('a rule tag holds it, naming every tag', [g.needs_check, g.payload.drafts[0].hold_reason], [false, 'rule-tags: long, claim-repeat']);
g = gate(firstTouch(CLEAN + '+unconfirmed-claim'));
t.check('... unconfirmed-claim is a rule tag like any other', [g.needs_check, g.payload.drafts[0].hold_reason], [false, 'rule-tags: unconfirmed-claim']);
g = gate(firstTouch('unaddressed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1+no-address'));
t.check('an email to a contact with no address is held by its `no-address` tag, and no check is paid for',
  [g.needs_check, g.check_request, g.payload.drafts[0].hold_reason], [false, null, 'rule-tags: no-address']);

g = gate(firstTouch('low-context/role-inbox', null, 'low-context/no-profile'));
t.check('a low-context item (no approval context) holds both drafts, no check',
  [g.needs_check, g.payload.drafts[0].hold_reason, g.payload.drafts[1].hold_reason],
  [false, 'low-context: a note to a person, not an email to send', 'linkedin: always reviewed and sent by hand']);
g = gate(firstTouch('low-context/role-inbox'));
t.check('a low-context variant is held even with a full context and the flag on',
  [g.needs_check, g.payload.drafts[0].hold_reason], [false, 'low-context: a note to a person, not an email to send']);

const UNCONF = CLAIMS.map(function (c) { return c.code === 'BEN-SEE' ? claim(c.code, c.slot, c.body, false) : c; });
g = gate(firstTouch(CLEAN, ctx({ claims: UNCONF })));
t.check('a claim code that is not confirmed holds it, naming the code', [g.needs_check, g.payload.drafts[0].hold_reason], [false, 'unconfirmed-claim: BEN-SEE']);
g = gate(firstTouch('unnamed/D2.ANG-HOURS.BEN-XYZ.PR-BOTH.A1'));
t.check('a claim code the library does not hold counts as unconfirmed', g.payload.drafts[0].hold_reason, 'unconfirmed-claim: BEN-XYZ');
g = gate(firstTouch('unnamed/'));
t.check('no claim codes at all: held', g.payload.drafts[0].hold_reason, 'no-claims: the draft records no claim codes');
g = gate(firstTouch(CLEAN, ctx({ claims: UNCONF.filter(function (c) { return c.code !== 'BEN-SEE'; }) .concat([claim('BEN-DECK2', 'benefit', 'x', false)]).concat([CLAIMS[2]]) })));
t.check('near miss: an unconfirmed line the draft did NOT use does not hold it', g.needs_check, true);
const unconfPrompt = gate(firstTouch(CLEAN, ctx({ claims: CLAIMS.concat([claim('BEN-FIT', 'benefit', 'Configured around you.', false)]) }))).check_request.messages[0].content;
t.check('... and an unconfirmed line is never shown to the checker as approved', unconfPrompt.indexOf('BEN-FIT'), -1);

g = gate(firstTouch(CLEAN + '+long', ctx({ auto_approve: false })));
t.check('every reason is recorded, flag first', g.payload.drafts[0].hold_reason, 'auto-approve-off: settings.auto_approve_email is false; rule-tags: long');

g = gate({ json: { lead_id: 50, payload: firstTouch(CLEAN).json.payload } });
t.check('an email draft that arrives without its approval context is held, not approved',
  [g.needs_check, g.payload.drafts[0].hold_reason], [false, 'no-context: the draft arrived without its approval context']);

g = gate(firstTouch(CLEAN, undefined, CLEAN));
g = gate({ json: { lead_id: 50, approval: ctx(), payload: { lead_id: 50, drafts: [
  { channel: 'email', variant: CLEAN, body: 'a' }, { channel: 'email', variant: CLEAN, body: 'b' }] } } });
t.check('two email drafts in one item: both held, nothing checked',
  [g.needs_check, g.payload.drafts[0].hold_reason, g.payload.drafts[1].hold_reason],
  [false, 'internal: more than one email draft in one item', 'internal: more than one email draft in one item']);

// Follow-Ups' shape: payload.draft, no subject to check, the first email as context.
const FU_TEXT = 'Following up on my note about after-hours sponsor inquiries. One more thing the assistant does: it sends your capabilities deck the moment a sponsor asks for it.\n\nWould a 48-hour demo built on your own material be worth a look? One word back is enough.';
function followUp(variant, over) {
  return {
    json: {
      lead_id: 7,
      approval: ctx(Object.assign({ kind: 'follow-up', first_email: 'Your recruiting trial INM004 ...', check: { subject: null, text: FU_TEXT } }, over || {})),
      payload: { lead_id: 7, action: 'draft-follow-up', draft: { channel: 'email', variant: variant, subject: 'Re: x', body: 'Hello,\n\n' + FU_TEXT } },
    },
  };
}
g = gate(followUp('follow-up-1/BEN-DECK.A1'));
t.check('a clean follow-up goes on to the check', [g.needs_check, g.check_target, g.payload.draft.status], [true, 0, 'pending']);
const fuPrompt = g.check_request.messages[0].content;
t.check('... with the first email as context only, and no subject line to check',
  [fuPrompt.indexOf('THE FIRST EMAIL, already sent to them -- context only') !== -1, fuPrompt.indexOf('Subject:'),
   fuPrompt.indexOf('a follow-up to the first email') !== -1], [true, -1, true]);
g = gate(followUp('follow-up-1/BEN-DECK.A1+fu-no-new-claim'));
t.check('a follow-up rule tag holds it', g.payload.draft.hold_reason, 'rule-tags: fu-no-new-claim');

// --- Apply Claim Check: rule 5 ----------------------------------------------------

function response(statements, over) {
  return Object.assign({
    model: 'claude-sonnet-5-5',
    stop_reason: 'end_turn',
    usage: { input_tokens: 2000, output_tokens: 900 },
    content: [{ type: 'thinking', thinking: '' }, { type: 'text', text: JSON.stringify({ statements: statements }) }],
  }, over || {});
}
function st(sentence, kind, source, verdict, statement) {
  return { sentence: sentence, statement: statement || sentence, kind: kind, source: source, why: 'because', verdict: verdict };
}
const SENTENCES = [
  st(SUBJECT, 'prospect', '1', 'supported'),
  st("You're running a recruiting trial, the Registry of X.", 'prospect', '1', 'supported'),
  st('Sponsors often research CROs outside your working hours, so an inquiry sent at 11pm waits until morning.', 'claim', 'ANG-HOURS', 'supported'),
  st('We built an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link (we call it Nova).', 'claim', 'D2', 'supported'),
  st('You can see every sponsor lead it captured, and any question it passed to your team.', 'claim', 'BEN-SEE', 'supported'),
  st("It's live at two CROs, in Türkiye and Mexico.", 'claim', 'PR-BOTH', 'supported'),
  st('Would a 48-hour demo built on your own material be worth a look?', 'claim', 'A1', 'supported'),
  st('One word back is enough.', 'none', '', 'none'),
];
function apply(gateItem, resp) {
  const g0 = gate(gateItem);
  return runForEachItem(APPLY, [{ json: resp }], { 'Approval Gate': [{ json: g0 }] }, asShipped)[0].json;
}
function emailOf(a) {
  return a.payload.drafts ? a.payload.drafts[a.check_target] : a.payload.draft;
}

let a = apply(firstTouch(CLEAN), response(SENTENCES));
let d = emailOf(a);
t.check('every statement supported and the whole email covered: APPROVED by the workflow',
  [d.status, d.approved_by, d.hold_reason, d.claim_check.result, a.check_result], ['approved', 'auto', null, 'pass', 'pass']);
t.check('... the verdicts, model and usage are kept on the draft',
  [d.claim_check.statements.length, d.claim_check.model, d.claim_check.usage.output_tokens], [8, 'claude-sonnet-5-5', 900]);
t.check('... and its LinkedIn draft is still held', [a.payload.drafts[1].status, a.payload.drafts[1].approved_by], ['pending', null]);
t.check('the apply node drops the request body from its output', a.check_request, null);

const DRAFT99 = 'You can see every sponsor lead it captured, and any question it passed to your team, so you know exactly what came in.';
const widened = SENTENCES.slice(0, 4).concat([
  st(DRAFT99, 'claim', 'BEN-SEE', 'supported', 'You can see every sponsor lead it captured, and any question it passed to your team'),
  st(DRAFT99, 'claim', 'BEN-SEE', 'widened', 'so you know exactly what came in'),
]).concat(SENTENCES.slice(5));
a = apply(firstTouch(CLEAN, ctx({ check: { subject: SUBJECT, text: BODY.replace(
  'You can see every sponsor lead it captured, and any question it passed to your team.', DRAFT99) } })), response(widened));
d = emailOf(a);
t.check('one widened statement holds the draft, naming the statement and its claim',
  [d.status, d.approved_by, d.hold_reason], ['pending', null, 'claim-check: widened "so you know exactly what came in" (BEN-SEE)']);
t.check('... two statements quoting the same sentence count once for coverage', d.hold_reason.indexOf('never reviewed'), -1);

function holdWith(statements, over, gateItem) {
  return emailOf(apply(gateItem || firstTouch(CLEAN), response(statements, over))).hold_reason;
}
function swap(i, s) {
  const out = SENTENCES.slice();
  out[i] = s;
  return out;
}
t.check('an unsupported claim holds it',
  holdWith(swap(4, st(SENTENCES[4].sentence, 'claim', '', 'unsupported'))), 'claim-check: unsupported "You can see every sponsor lead it captured, and any question it passed to your team."');
t.check('a joined prospect fact holds it',
  holdWith(swap(1, st(SENTENCES[1].sentence, 'prospect', '1, 2', 'joined'))), 'claim-check: joined fact "You\'re running a recruiting trial, the Registry of X." (1, 2)');
t.check('an unsupported prospect fact holds it',
  holdWith(swap(0, st(SUBJECT, 'prospect', '', 'unsupported'))), 'claim-check: unsupported fact "' + SUBJECT + '"');
t.check('a claim called "supported" that names no claim code is held',
  holdWith(swap(4, st(SENTENCES[4].sentence, 'claim', '', 'supported'))), 'claim-check: no confirmed claim behind "You can see every sponsor lead it captured, and any question it passed to your team."');
t.check('... or names a code that is not a confirmed claim',
  holdWith(swap(4, st(SENTENCES[4].sentence, 'claim', 'BEN-CAPTURE', 'supported'))), 'claim-check: no confirmed claim behind "You can see every sponsor lead it captured, and any question it passed to your team." (BEN-CAPTURE)');
t.check('near miss: a claim citing two confirmed codes, bracketed, is fine',
  emailOf(apply(firstTouch(CLEAN), response(swap(3, st(SENTENCES[3].sentence, 'claim', '[D2], [BEN-SEE]', 'supported'))))).status, 'approved');
t.check('a "none" statement with any other verdict is held (inconsistent answer)',
  holdWith(swap(7, st('One word back is enough.', 'none', '', 'widened'))), 'claim-check: widened "One word back is enough."');

t.check('a sentence the check skipped holds it: nobody judged it',
  holdWith(SENTENCES.filter(function (_, i) { return i !== 5; })), 'claim-check-incomplete: 10 words never reviewed ("it s live at two cros in turkiye and mexico")');
t.check('a quote that is not in the email holds it',
  holdWith(SENTENCES.concat([st('It answers every sponsor instantly.', 'claim', 'D2', 'supported')])),
  'claim-check-incomplete: quoted text that is not in the email ("It answers every sponsor instantly.")');
const typographic = SENTENCES.map(function (s) {
  return Object.assign({}, s, { sentence: s.sentence.replace(/'/g, '’').replace('Türkiye', 'Turkiye').toUpperCase() });
});
t.check('near miss: curly quotes, a dropped accent and case changes still cover the email',
  emailOf(apply(firstTouch(CLEAN), response(typographic))).status, 'approved');
t.check('near miss: the subject quoted WITH the prompt\'s "Subject:" label still covers the subject (measured, dry run 1)',
  emailOf(apply(firstTouch(CLEAN), response(swap(0, st('Subject: ' + SUBJECT, 'prospect', '1', 'supported'))))).status, 'approved');
t.check('... but "Subject:" is only dropped when the quote leads with it -- a made-up quote is still foreign',
  holdWith(SENTENCES.concat([st('We built a Subject: line generator.', 'claim', 'D2', 'supported')])).indexOf('claim-check-incomplete: quoted text that is not in the email'), 0);
t.check('the checker is told the bracketed product name is a name, not a claim (measured, dry run 1: "we call it Nova" judged unsupported)',
  gate(firstTouch(CLEAN)).check_request.system[0].text.indexOf('"(we call it Nova)", only names what the sentence describes') !== -1, true);
t.check('a first email in which the check found no product claim at all is held',
  holdWith([st(SUBJECT + ' ' + BODY, 'none', '', 'none')]), 'claim-check-incomplete: no product claim found in a first email');

t.check('an HTTP error holds it for a human',
  holdWith([], { error: { message: 'overloaded', status: 529 } }), 'claim-check-failed: {"message":"overloaded","status":529}');
t.check('a refusal holds it, with the category',
  holdWith([], { stop_reason: 'refusal', stop_details: { type: 'refusal', category: 'cyber' }, content: [] }), 'claim-check-failed: stop_reason "refusal" (cyber)');
t.check('a cut-off answer holds it', holdWith(SENTENCES, { stop_reason: 'max_tokens' }), 'claim-check-failed: stop_reason "max_tokens"');
t.check('an answer that is not the schema holds it',
  holdWith([], { content: [{ type: 'text', text: '{"statements": [{"sentence": "x", "kind": "maybe"}]}' }] }),
  'claim-check-failed: the answer was not the check schema: {"statements": [{"sentence": "x", "kind": "maybe"}]}');
t.check('... and a model summary verdict is never read: there is no such field to trust',
  holdWith([], { content: [{ type: 'text', text: '{"pass": true}' }] }), 'claim-check-failed: the answer was not the check schema: {"pass": true}');

const FU_SENTENCES = [
  st('Following up on my note about after-hours sponsor inquiries.', 'none', '', 'none'),
  st('One more thing the assistant does: it sends your capabilities deck the moment a sponsor asks for it.', 'claim', 'BEN-DECK', 'supported'),
  st('Would a 48-hour demo built on your own material be worth a look?', 'claim', 'A1', 'supported'),
  st('One word back is enough.', 'none', '', 'none'),
];
a = apply(followUp('follow-up-1/BEN-DECK.A1'), response(FU_SENTENCES));
t.check('a clean follow-up is approved by the workflow (payload.draft shape)',
  [a.payload.draft.status, a.payload.draft.approved_by, a.payload.draft.claim_check.result], ['approved', 'auto', 'pass']);
a = apply(followUp('follow-up-1/BEN-DECK.A1'), response([FU_SENTENCES[0], FU_SENTENCES[1], FU_SENTENCES[3]]));
t.check('a follow-up whose ask was never reviewed is held',
  [a.payload.draft.status, a.payload.draft.hold_reason], ['pending', 'claim-check-incomplete: 14 words never reviewed ("would a 48 hour demo built on your own material be worth a …")']);
a = apply(followUp('follow-up-1/BEN-DECK.A1'), response([FU_SENTENCES[0], FU_SENTENCES[3]].concat([
  st(FU_SENTENCES[1].sentence, 'none', '', 'none'), st(FU_SENTENCES[2].sentence, 'none', '', 'none')])));
t.check('near miss: a follow-up with no claim statement is not held for that (only a first email must have one)',
  a.payload.draft.status, 'approved');

// ===================================================================================
// The repair loop (2026-10-03)
// ===================================================================================

const ASK = 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.';
const BODY99 = BODY.replace('You can see every sponsor lead it captured, and any question it passed to your team.', DRAFT99);
const FIXED = 'You can see every sponsor lead it captured, and any question it passed to your team.';
const FIELDS = ['email_subject', 'email_body', 'email_ask'];
function composition(text) {
  return { email_subject: SUBJECT, email_body: text.replace(' ' + ASK, ''), email_ask: ASK, linkedin_body: 'li body',
    linkedin_ask: 'li ask?', email_claims: ['D2', 'ANG-HOURS', 'BEN-SEE', 'PR-BOTH', 'A1'], linkedin_claims: ['D2'] };
}
// A first touch carrying what the loop needs, as Assemble Drafts emits it.
function repairable(over, extra) {
  const it = firstTouch(CLEAN, ctx(Object.assign({ check: { subject: SUBJECT, text: BODY99 }, repair_fields: FIELDS }, over || {})));
  Object.assign(it.json, { composition: composition(BODY99), usage: { input_tokens: 3000, output_tokens: 1500 } }, extra || {});
  return it;
}

// --- Apply Claim Check: repair, or the end of the loop -----------------------------
a = apply(repairable(), response(widened));
d = emailOf(a);
t.check('a widened statement sends the draft to a repair instead of ending there',
  [a.needs_repair, d.status, a.repair_state.flagged.length, a.repair_state.flagged[0].sentence], [true, 'pending', 1, DRAFT99]);
const rq = a.repair_request;
t.check('the repair goes to the drafting model with the check\'s parameters and a strict schema',
  [rq.model, rq.output_config.effort, rq.max_tokens, rq.output_config.format.schema.properties.rewrites.items.additionalProperties,
   ['temperature', 'top_p', 'top_k', 'thinking'].filter(function (k) { return k in rq; })],
  ['claude-sonnet-5-5', 'high', 16000, false, []]);
const rqText = rq.messages[0].content;
t.check('... carrying the email, the flagged sentence and the checker\'s exact reason, and the confirmed lines',
  [rqText.indexOf('1. ' + DRAFT99) !== -1,
   rqText.indexOf('checker: widened -- "so you know exactly what came in" (compared with BEN-SEE): because') !== -1,
   rqText.indexOf('[BEN-SEE] (benefit) You can see every sponsor lead') !== -1, rqText.indexOf('Subject: ' + SUBJECT) !== -1],
  [true, true, true, true]);
t.check('... told to rewrite only the flagged sentences, in the claim\'s own words, adding nothing',
  [rq.system.indexOf('Rewrite ONLY those sentences') !== -1, rq.system.indexOf('in its own') !== -1,
   rq.system.indexOf('Add nothing') !== -1], [true, true, true]);
// Prompt caching, 2026-10-08: the claim check's prompt is the cached block; the
// repair's is 448 tokens, under Sonnet 5.5's 512-token minimum, so it is a bare
// string on purpose -- a breakpoint there would cache nothing and still bill the
// write premium.
t.check('the claim check marks its system prompt for caching, with the 5-minute default TTL',
  gate(firstTouch(CLEAN)).check_request.system.map(function (b) { return b.cache_control; }),
  [{ type: 'ephemeral' }]);
t.check('the repair request does NOT mark its system prompt (448 tokens, under the 512 minimum)',
  typeof rq.system, 'string');
t.check('an unconfirmed line is never offered to the repair as something it may say',
  apply(repairable({ claims: CLAIMS.concat([claim('BEN-FIT', 'benefit', 'Configured around you.', false)]) }), response(widened))
    .repair_request.messages[0].content.indexOf('BEN-FIT'), -1);
t.check('the attempt is recorded on the draft: 1 attempt, 0 repairs, the composing cost carried',
  [d.claim_check.attempts.length, d.claim_check.repairs, d.claim_check.compose_usage.output_tokens, a.repair_state.compose_usage.output_tokens],
  [1, 0, 1500, 1500]);
t.check('a hold the composer cannot fix -- a sentence the check skipped -- is final, not repaired',
  apply(repairable({ check: { subject: SUBJECT, text: BODY } }), response(SENTENCES.filter(function (_, i) { return i !== 5; }))).needs_repair, false);
t.check('... nor a failed call',
  apply(repairable(), response([], { error: { message: 'overloaded' } })).needs_repair, false);
t.check('... nor a "supported" claim that cites no confirmed line (the checker\'s answer, not the draft, is wrong)',
  apply(repairable({ check: { subject: SUBJECT, text: BODY } }), response(swap(4, st(SENTENCES[4].sentence, 'claim', '', 'supported')))).needs_repair, false);
t.check('a joined or unsupported prospect fact is repairable too',
  [apply(repairable({ check: { subject: SUBJECT, text: BODY } }), response(swap(1, st(SENTENCES[1].sentence, 'prospect', '1, 2', 'joined')))).needs_repair,
   apply(repairable({ check: { subject: SUBJECT, text: BODY } }), response(swap(0, st(SUBJECT, 'prospect', '', 'unsupported')))).needs_repair],
  [true, true]);
t.check('without the composition to edit, it is held rather than repaired blind',
  apply(repairable({}, { composition: null }), response(widened)).needs_repair, false);

const H1 = { attempt: 1, result: 'hold', reasons: ['claim-check: widened "so you know exactly what came in" (BEN-SEE)'], statements: [] };
const H2 = { attempt: 2, result: 'hold', reasons: ['claim-check: widened "nothing that came in is lost" (BEN-SEE)'], statements: [],
  repair_usage: { input_tokens: 900, output_tokens: 250 } };
function afterRepairs(history, text) {
  return repairable({ check: { subject: SUBJECT, text: text || BODY99 } }, {
    repair: { round: history.length, target: 0, history: history, compose_usage: { input_tokens: 3000, output_tokens: 1500 },
      last_repair_usage: { input_tokens: 800, output_tokens: 300 }, rewrites: [{ field: 'email_body', original: DRAFT99, replacement: FIXED }] },
    usage: null,
  });
}
a = apply(afterRepairs([H1, H2]), response(widened));
d = emailOf(a);
t.check('after MAX_REPAIRS (2) repairs a third hold is final: no more repairs', [a.needs_repair, d.status, d.approved_by], [false, 'pending', null]);
t.check('... and the hold lists every attempt\'s reasons, labelled',
  d.hold_reason, 'first draft: claim-check: widened "so you know exactly what came in" (BEN-SEE) | after repair 1: claim-check: widened ' +
  '"nothing that came in is lost" (BEN-SEE) | after repair 2: claim-check: widened "so you know exactly what came in" (BEN-SEE)');
t.check('... with all three attempts, the repairs and the composing cost on the draft',
  [d.claim_check.attempts.length, d.claim_check.repairs, d.claim_check.compose_usage.output_tokens, d.claim_check.attempts[2].repair_usage.output_tokens],
  [3, 2, 1500, 300]);
a = apply(afterRepairs([H1], BODY), response(SENTENCES));
d = emailOf(a);
t.check('a repaired email that passes the check is APPROVED by the workflow',
  [d.status, d.approved_by, d.hold_reason, d.claim_check.result, a.needs_repair], ['approved', 'auto', null, 'pass', false]);
t.check('... its record shows the failed first draft, the repair\'s rewrite and cost, and the pass',
  [d.claim_check.repairs, d.claim_check.attempts.length, d.claim_check.attempts[0].result, d.claim_check.attempts[1].rewrites[0].replacement,
   d.claim_check.attempts[1].repair_usage.output_tokens, d.claim_check.attempts[1].result],
  [1, 2, 'hold', FIXED, 300, 'pass']);

// --- Apply Repair: the rewrites, applied by code -----------------------------------
const PREV = apply(repairable(), response(widened));
function repairResp(rewrites, over) {
  return Object.assign({ model: 'claude-sonnet-5-5', stop_reason: 'end_turn', usage: { input_tokens: 1200, output_tokens: 400 },
    content: [{ type: 'thinking', thinking: '' }, { type: 'text', text: JSON.stringify({ rewrites: rewrites }) }] }, over || {});
}
function runRepair(prev, resp) {
  return runForEachItem(REPAIR, [{ json: resp }], { 'Apply Claim Check': [{ json: prev }] }, repairShipped)[0].json;
}
let r = runRepair(PREV, repairResp([{ original: DRAFT99, replacement: FIXED }]));
let comp = JSON.parse(r.content[0].text);
t.check('a repair rewrites the flagged sentence, by code',
  [r.repaired, comp.email_body.indexOf('exactly what came in'), comp.email_body.indexOf(FIXED) !== -1], [true, -1, true]);
t.check('... and nothing else: every other field and sentence is byte-identical',
  [comp.email_subject, comp.email_ask, comp.linkedin_body, comp.email_claims.join('.'), comp.email_body.replace(FIXED, DRAFT99)],
  [SUBJECT, ASK, 'li body', 'D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1', composition(BODY99).email_body]);
t.check('... leaving shaped like a model response for the Assemble copy, carrying the attempts so far',
  [r.stop_reason, r.repair.round, r.repair.target, r.repair.history.length, r.repair.last_repair_usage.output_tokens,
   r.repair.compose_usage.output_tokens, r.repair.rewrites.length],
  ['end_turn', 1, 0, 1, 400, 1500, 1]);
r = runRepair(PREV, repairResp([{ original: DRAFT99, replacement: FIXED },
  { original: "It's live at two CROs, in Türkiye and Mexico.", replacement: "It's live at forty CROs." }]));
comp = JSON.parse(r.content[0].text);
t.check('a rewrite of a sentence the checker did not flag is ignored -- a repair changes only what was flagged',
  [comp.email_body.indexOf("It's live at two CROs, in Türkiye and Mexico.") !== -1, comp.email_body.indexOf('forty'), r.repair.ignored[0].why],
  [true, -1, 'not a flagged sentence']);
r = runRepair(PREV, repairResp([{ original: DRAFT99.toUpperCase().replace(',', ' ,'), replacement: FIXED }]));
t.check('near miss: the flagged sentence quoted back with different case and spacing still matches', r.repaired, true);
r = runRepair(PREV, repairResp([{ original: DRAFT99, replacement: '' }]));
comp = JSON.parse(r.content[0].text);
t.check('an empty replacement deletes the sentence and leaves clean spacing',
  [comp.email_body.indexOf('so you know'), /  /.test(comp.email_body), comp.email_body.indexOf("(we call it Nova). It's live at two CROs") !== -1],
  [-1, false, true]);
r = runRepair(PREV, repairResp([{ original: DRAFT99, replacement: DRAFT99 }]));
t.check('a repair that changes nothing ends the loop: held, with every reason so far and why the repair failed',
  [r.repaired, emailOf(r).status, emailOf(r).approved_by, emailOf(r).hold_reason, emailOf(r).claim_check.repairs],
  [false, 'pending', null, 'first draft: claim-check: widened "so you know exactly what came in" (BEN-SEE); repair 1 failed: the rewrites ' +
   'changed no flagged sentence', 1]);
r = runRepair(PREV, repairResp([{ original: 'We guarantee results.', replacement: 'x' }]));
t.check('... and one that only rewrites unflagged text says so',
  emailOf(r).hold_reason.indexOf('repair 1 failed: the rewrites changed no flagged sentence (not a flagged sentence)') !== -1, true);
t.check('a failed repair call ends the loop with its error',
  emailOf(runRepair(PREV, repairResp([], { error: { message: 'overloaded' } }))).hold_reason.indexOf('repair 1 failed: {"message":"overloaded"}') !== -1, true);
t.check('... a refusal with its category',
  emailOf(runRepair(PREV, repairResp([], { stop_reason: 'refusal', stop_details: { category: 'cyber' }, content: [] }))).hold_reason
    .indexOf('repair 1 failed: stop_reason "refusal" (cyber)') !== -1, true);
t.check('... an answer that is not the repair schema',
  emailOf(runRepair(PREV, repairResp([], { content: [{ type: 'text', text: '{"rewrite": []}' }] }))).hold_reason
    .indexOf('repair 1 failed: the answer was not the repair schema') !== -1, true);
const PREV_SUBJ = apply(repairable({ check: { subject: SUBJECT, text: BODY } }),
  response(swap(0, st('Subject: ' + SUBJECT, 'prospect', '', 'unsupported'))));
r = runRepair(PREV_SUBJ, repairResp([{ original: 'Subject: ' + SUBJECT, replacement: 'Sponsor inquiries after your working hours' }]));
comp = JSON.parse(r.content[0].text);
t.check('a flagged subject is repaired in the subject field, its "Subject:" label dropped',
  [r.repaired, comp.email_subject, comp.email_body], [true, 'Sponsor inquiries after your working hours', composition(BODY99).email_body]);

// --- the gate copy after a repair -------------------------------------------------
const AFTER = firstTouch(CLEAN + '+long', ctx({ repair_fields: FIELDS }));
AFTER.json.repair = { round: 1, target: 0, history: [H1], compose_usage: { output_tokens: 1500 } };
g = gate(AFTER);
t.check('a rewrite that breaks a rule is held by the gate copy, with every attempt\'s reasons',
  [g.needs_check, g.payload.drafts[0].hold_reason, g.payload.drafts[0].claim_check.repairs, g.payload.drafts[0].claim_check.attempts.length],
  [false, 'first draft: claim-check: widened "so you know exactly what came in" (BEN-SEE) | after repair 1: rule-tags: long', 1, 2]);
t.check('... and the attempts belong to that draft only, not to its LinkedIn DM',
  [g.payload.drafts[1].hold_reason, g.payload.drafts[1].claim_check], ['linkedin: always reviewed and sent by hand', null]);

// --- a follow-up goes round the same loop -----------------------------------------
const FU_W = 'Following up on my note about after-hours sponsor inquiries. One more thing the assistant does: it sends your capabilities deck ' +
  'the moment a sponsor asks for it, so no request ever waits.\n\n' + ASK;
const FU_W_SENT = 'One more thing the assistant does: it sends your capabilities deck the moment a sponsor asks for it, so no request ever waits.';
const fuItem = followUp('follow-up-1/BEN-DECK.A1', { check: { subject: null, text: FU_W }, repair_fields: ['body', 'ask'] });
fuItem.json.composition = { body: FU_W.replace('\n\n' + ASK, ''), ask: ASK, added_claim: 'BEN-DECK', claims: ['BEN-DECK', 'A1'], first_email_covers: [] };
const FU_PREV = apply(fuItem, response([FU_SENTENCES[0], st(FU_W_SENT, 'claim', 'BEN-DECK', 'widened', 'so no request ever waits'),
  FU_SENTENCES[2], FU_SENTENCES[3]]));
t.check('a follow-up held for a widened statement goes to a repair, with the first email as context',
  [FU_PREV.needs_repair, FU_PREV.repair_request.messages[0].content.indexOf('THE FIRST EMAIL, already sent to them') !== -1], [true, true]);
r = runRepair(FU_PREV, repairResp([{ original: FU_W_SENT, replacement: 'One more thing the assistant does: it sends your capabilities deck the moment a sponsor asks for it.' }]));
comp = JSON.parse(r.content[0].text);
t.check('... and the repair edits the follow-up\'s own fields (payload.draft shape)',
  [r.repaired, comp.body.indexOf('ever waits'), comp.ask, comp.added_claim], [true, -1, ASK, 'BEN-DECK']);

// --- rule 3b: a country with no Send business-hours clock (2026-10-07) -------
//
// Assess Grounding already stops such a lead before the drafting call, so a
// draft only reaches here from a follow-up, a redraft under an older build, or
// a country later removed from Section 12's table. Held, never repaired: no
// rewrite can give a country a clock, and Send would refuse the email anyway.

g = gate(firstTouch(CLEAN, ctx({ country: 'Greenland' })));
t.check('a country with no clock holds the email, and no check is paid for',
  [g.needs_check, g.check_request, g.payload.drafts[0].status], [false, null, 'pending']);
t.check('... and the reason names the country and why', g.payload.drafts[0].hold_reason,
  'no-send-clock: Greenland has no business-hours clock, so this email can never be sent (Section 12)');
t.check('... while its LinkedIn DM is held for being LinkedIn, as always (Section 6)',
  g.payload.drafts[1].hold_reason, 'linkedin: always reviewed and sent by hand');
g = gate(firstTouch(CLEAN, ctx({ country: null })));
t.check('no country at all is the same hold, and says so', g.payload.drafts[0].hold_reason,
  'no-send-clock: (no country) has no business-hours clock, so this email can never be sent (Section 12)');
g = gate(firstTouch(CLEAN, ctx({ country: 'india' })));
t.check('a clock country in the wrong case still passes -- matched as code_decide.js matches it',
  [g.needs_check, g.payload.drafts[0].hold_reason], [true, null]);
g = gate(firstTouch(CLEAN + '+long', ctx({ country: 'Greenland', auto_approve: false })));
t.check('the clock rule is the ONLY reason given -- nothing else is worth saying about a draft that can never go out',
  g.payload.drafts[0].hold_reason,
  'no-send-clock: Greenland has no business-hours clock, so this email can never be sent (Section 12)');
g = gate(firstTouch('no-send-clock/role-inbox', null, 'no-send-clock/no-profile'));
t.check('a no-send-clock NOTE is held for what it is, and never called low context',
  [g.needs_check, g.payload.drafts[0].hold_reason],
  [false, 'no-send-clock: a note to a person, not an email to send']);

t.done();

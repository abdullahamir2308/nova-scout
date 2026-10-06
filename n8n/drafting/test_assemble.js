// Unit tests for code_assemble.js -- drafting skill v3: response handling,
// assembly, and every section 3 rule enforced in code.
//
// v3 (NovaScout_DraftingSkill.md) moved from verbatim line-picking to
// composition: Claude Sonnet 5.5 writes the whole email from one prospect fact
// plus the approved claims. That makes Assemble Drafts the only deterministic
// check between the model's prose and the review queue, so every rule the skill
// states as a rule has a case here that must FIRE, and -- where a pattern could
// over-reach -- a near miss that must NOT. test_rule_mutations.py then breaks
// each rule in the source and proves this file fails.
//
// The compliance-critical case is unchanged from v1: the opt-out sentence is
// appended verbatim, once, never generated.

const path = require('path');
const { runForEachItem, runner } = require('./harness');

const ASSEMBLE = path.join(__dirname, 'code_assemble.js');

// build_workflow.py substitutes the signature at build time. The tests run the
// same substitution so they exercise the shipped string, not a placeholder.
const SIGNATURE = 'Test Sender\nFounder, Example Labs';
const withSignature = function (src) {
  return src.replace('__SIGNATURE__', JSON.stringify(SIGNATURE));
};

const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

// Claims as Assess Grounding hands them over: migration 012's seed as migration
// 013 left it (wording and capabilities), confirmed here so a clean draft
// carries no tag at all, with the proof already resolved for a Poland lead
// (NoblePath).
function line(code, body, confirmed, capabilities) {
  return { code: code, body: body, confirmed: confirmed !== false, capabilities: capabilities || [] };
}
const D1 = line('D1', 'an AI assistant for your website that turns sponsor inquiries into qualified leads', true,
  ['qualifies', 'captures-lead']);
const D2 = line('D2', 'an AI intake assistant for your website that answers sponsors, qualifies them, and sends them your booking link',
  true, ['answers', 'qualifies', 'booking-link']);
const ANG_HOURS = line('ANG-HOURS', 'Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.');
const ANG_STAKES = line('ANG-STAKES', 'A single sponsor inquiry can be a multi-million-dollar study.');
const ANG_SILENT = line('ANG-SILENT', 'How many sponsors visit your site and leave without ever contacting you?');
const BEN_247 = line('BEN-247', 'It answers sponsors from your own website, in real time, at any hour.', true, ['answers']);
const BEN_FIT = line('BEN-FIT', "It's configured around your services and your process, not a template.", true, ['configured']);
const BEN_BOOK = line('BEN-BOOK', 'It sends qualified sponsors your booking link, so they can book a call with your team.', true,
  ['booking-link']);
const BEN_CAPTURE = line('BEN-CAPTURE', 'A sponsor who shares their details becomes a named lead: company, contact, therapeutic area and study phase.',
  true, ['captures-lead', 'study-details']);
const BEN_BRIEF = line('BEN-BRIEF', 'It collects the therapeutic area and study phase before your first call.', true, ['study-details']);
const PR_TR = line('PR-TR', "It's live at NoblePath, an oncology CRO in Türkiye.");
const PR_BOTH = line('PR-BOTH', "It's live at two CROs, in Türkiye and Mexico.");
const A1 = line('A1', 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.');
const A2 = line('A2', "Worth a 48-hour demo on your own material? Reply yes and I'll set it up.");
const LINK = line('L-NP', 'Their site: https://noblepath.example');

const POOLS = {
  description: [D1],
  angle: [ANG_HOURS, ANG_STAKES, ANG_SILENT],
  benefit: [BEN_247, BEN_FIT],
  proof: [PR_TR],
  ask: [A1, A2],
};

const TAXONOMY = ['Oncology', 'Cardiovascular', 'Central Nervous System', 'Immunology',
  'Infectious Disease', 'Endocrinology', 'Metabolic Disorders', 'Respiratory', 'Rare Diseases',
  'Internal Medicine', 'Anesthesiology', 'Dermatology', 'Rheumatology', 'Ophthalmology',
  'Gastroenterology', 'Nephrology', 'Hematology'];

function absentGiven(areas) {
  return TAXONOMY.filter(function (a) { return areas.indexOf(a) === -1; });
}

const FACTS =
  '1. Therapeutic areas listed on their own website: Oncology and Respiratory. This says ' +
  'nothing about which area any particular trial belongs to.\n' +
  '2. The company is based in Budapest. This says nothing about where any trial runs or ' +
  'where any staff sit.\n' +
  '3. Named on their own site as founder or MD: Anna Nowak. This says nothing about which ' +
  'trials or clients this person personally handles.';

function assessed(over) {
  return Object.assign(
    {
      lead_id: 1,
      domain: 'example.com',
      company_name: 'Example CRO',
      fit_score: 65,
      fact_count: 3,
      fact_kinds: ['therapeutic_area', 'city', 'founder_name'],
      fact_sheet: FACTS,
      trial_titles: [],
      areas: ['Oncology', 'Respiratory'],
      absent_areas: absentGiven(['Oncology', 'Respiratory']),
      city: 'Budapest',
      founder_name: 'Anna Nowak',
      headcount: null,
      email_addressing: 'role-inbox',
      linkedin_addressing: 'named',
      email_greeting: 'Hello,',
      linkedin_greeting: 'Hi Anna,',
      library_pools: POOLS,
      library_link: [],
      warmup_week: 1,
    },
    over || {}
  );
}

// A clean composition in the shape of the skill's section 5 example, built only
// from this lead's facts and the claims above.
const CLEAN_BODY =
  'Your site lists oncology and respiratory work. Sponsors in those areas often research CROs ' +
  'outside your working hours, and an inquiry sent at 11pm waits until morning, by which time ' +
  'they may have moved on.\n\n' +
  'We built an AI assistant for your website that turns sponsor inquiries into qualified leads ' +
  '(we call it Nova). It answers sponsors from your own service pages at any hour. ' +
  "It's live at NoblePath, an oncology CRO in Türkiye.";
const CLEAN_ASK = 'Would a 48-hour demo built on your own material be worth a look? One word back is enough.';

function generation(over) {
  return Object.assign(
    {
      email_subject: 'Oncology sponsor inquiries after hours',
      email_body: CLEAN_BODY,
      email_ask: CLEAN_ASK,
      linkedin_body: 'Your site lists oncology and respiratory work. We built an AI assistant for your ' +
        'website that turns sponsor inquiries into qualified leads (we call it Nova), and it is live at ' +
        'NoblePath, an oncology CRO in Türkiye.',
      linkedin_ask: 'Worth a 48-hour demo on your own material?',
      email_claims: ['A1', 'PR-TR', 'BEN-247', 'ANG-HOURS', 'D1'],
      linkedin_claims: ['D1', 'PR-TR', 'A2'],
    },
    over || {}
  );
}

// What the Claude Draft HTTP node hands on: the Messages API response. A
// thinking block (adaptive thinking, display omitted) comes before the text.
function claude(gen, over) {
  return Object.assign({
    model: 'claude-sonnet-5-5',
    stop_reason: 'end_turn',
    stop_details: null,
    content: [
      { type: 'thinking', thinking: '', signature: 'sig' },
      { type: 'text', text: typeof gen === 'string' ? gen : JSON.stringify(gen) },
    ],
    usage: { input_tokens: 3000, output_tokens: 900, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
  }, over || {});
}

function run(resp, over) {
  return runForEachItem(
    ASSEMBLE,
    [{ json: resp }],
    { 'Assess Grounding': [{ json: assessed(over) }] },
    withSignature
  )[0].json;
}

function assemble(genOver, over) {
  return run(claude(generation(genOver)), over);
}

function draftOf(out, channel) {
  return out.payload.drafts.filter(function (d) { return d.channel === channel; })[0];
}

function emailFlags(genOver, over) { return assemble(genOver, over).email_flags; }
function linkedinFlags(genOver, over) { return assemble(genOver, over).linkedin_flags; }
function has(flags, f) { return flags.indexOf(f) !== -1; }
function count(hay, needle) { return hay.split(needle).length - 1; }

// The clean body with one sentence swapped in after the hook.
function bodyWith(sentence) {
  return CLEAN_BODY.replace('It answers sponsors from your own service pages at any hour.', sentence);
}

const t = runner('code_assemble.js -- drafting skill v3');

// ===========================================================================
// The clean draft
// ===========================================================================

const CLEAN = assemble();
t.check('a clean composition is written', CLEAN.write, true);
t.check('and carries no email tag at all', CLEAN.email_flags, []);
t.check('nor any LinkedIn tag', CLEAN.linkedin_flags, []);
t.check(
  'variant = addressing / the claim codes used, in slot order whatever order the model gave',
  draftOf(CLEAN, 'email').variant,
  'role-inbox/D1.ANG-HOURS.BEN-247.PR-TR.A1'
);
t.check('the LinkedIn variant uses its own addressing, and only the claims the DM used',
  draftOf(CLEAN, 'linkedin').variant, 'named/D1.PR-TR.A2');
t.check('the model and usage ride along for the cost report', [CLEAN.model, CLEAN.usage.output_tokens],
  ['claude-sonnet-5-5', 900]);

// ===========================================================================
// Assembly -- the frame the model never writes
// ===========================================================================

const EMAIL = draftOf(CLEAN, 'email').body;
t.check(
  'the email is greeting / body / ask / opt-out / signature, in that order',
  EMAIL,
  'Hello,\n\n' + CLEAN_BODY + '\n\n' + CLEAN_ASK + '\n\n' + OPT_OUT + '\n\n' + SIGNATURE
);
t.check('the opt-out sentence appears exactly once, verbatim', count(EMAIL, OPT_OUT), 1);
t.check('the subject is the model\'s, whitespace tidied',
  draftOf(assemble({ email_subject: '  Oncology   sponsor inquiries after hours ' }), 'email').subject,
  'Oncology sponsor inquiries after hours');
const DM = draftOf(CLEAN, 'linkedin');
t.check('the LinkedIn DM has no opt-out and no signature (Section 6: a human sends it)',
  [DM.body.indexOf(OPT_OUT), DM.body.indexOf('Test Sender')], [-1, -1]);
t.check('and no subject', DM.subject, null);
t.check('the DM opens with its own greeting', DM.body.indexOf('Hi Anna,\n\n'), 0);
t.check(
  'a greeting the model wrote anyway is stripped, not doubled',
  draftOf(assemble({ email_body: 'Hello,\n\n' + CLEAN_BODY }), 'email').body.indexOf('Hello,\n\nHello'),
  -1
);
t.check(
  'a sign-off the model wrote anyway is stripped',
  draftOf(assemble({ email_ask: CLEAN_ASK + '\n\nBest regards,\nAbdullah' }), 'email').body.indexOf('Best regards'),
  -1
);
t.check(
  'the word count is the body plus the ask -- not the greeting, opt-out or signature',
  CLEAN.email_words,
  CLEAN_BODY.split(/\s+/).length + CLEAN_ASK.split(/\s+/).length
);

// ===========================================================================
// Response handling -- skill section 6: a failed call leaves the lead queued
// ===========================================================================

t.check('the thinking block before the text is skipped, not parsed', CLEAN.write, true);
t.check('an empty email body is tagged `empty`, not silently written as a bare ask',
  has(emailFlags({ email_body: '' }), 'empty'), true);
t.check('an empty DM body is tagged `empty`', has(linkedinFlags({ linkedin_body: '   ' }), 'empty'), true);
const ERR = run({ error: { message: '529 overloaded_error' } });
t.check('an HTTP error writes nothing -- the lead stays queued', ERR.write, false);
t.check('and says why', ERR.skip_reason.indexOf('overloaded') !== -1, true);
const REFUSAL = run(claude(generation(), { stop_reason: 'refusal', stop_details: { type: 'refusal', category: 'general_harms' } }));
t.check('a refusal writes nothing', REFUSAL.write, false);
t.check('and names the category', REFUSAL.skip_reason.indexOf('general_harms') !== -1, true);
t.check('a response cut off at max_tokens writes nothing',
  run(claude(generation(), { stop_reason: 'max_tokens' })).write, false);
t.check('text that is not JSON writes nothing', run(claude('Here is your email: ...')).write, false);
const MISSING = generation();
delete MISSING.email_ask;
t.check('JSON missing a schema field writes nothing', run(claude(MISSING)).write, false);
t.check('a claim list that is not a list writes nothing',
  run(claude(generation({ email_claims: 'D1' }))).write, false);
t.check('either claim list missing writes nothing',
  run(claude(generation({ linkedin_claims: undefined }))).write, false);

// ===========================================================================
// Length -- "70-110 words and never more than 125"
// ===========================================================================

function bodyOfWords(n) {
  // The clean ask is 18 words; the body makes up the rest, and still names the
  // proof so no other rule fires.
  const askWords = CLEAN_ASK.split(/\s+/).length;
  const proof = "It's live at NoblePath, an oncology CRO in Türkiye.";
  const filler = [];
  for (let i = 0; i < n - askWords - proof.split(/\s+/).length; i++) filler.push('word');
  return filler.join(' ') + '. ' + proof;
}
t.check('the clean draft is inside the 70-110 target', CLEAN.email_words >= 70 && CLEAN.email_words <= 110, true);
t.check('exactly 125 words is inside the ceiling', has(emailFlags({ email_body: bodyOfWords(125) }), 'long'), false);
t.check('126 words is tagged `long`', has(emailFlags({ email_body: bodyOfWords(126) }), 'long'), true);
t.check('the count it tagged is the one reported', assemble({ email_body: bodyOfWords(126) }).email_words, 126);
t.check('the LinkedIn DM is held to the same ceiling', has(linkedinFlags({ linkedin_body: bodyOfWords(140) }), 'long'), true);
t.check('a short draft is not tagged -- "shorter is fine"', has(emailFlags({ email_body: bodyOfWords(40) }), 'long'), false);

// ===========================================================================
// Exactly one ask
// ===========================================================================

t.check('an ask with no question is `ask-none`', has(emailFlags({ email_ask: 'Let me know.' }), 'ask-none'), true);
t.check('an empty ask is `ask-none`', has(emailFlags({ email_ask: '' }), 'ask-none'), true);
t.check('two questions in the ask is `ask-multiple`',
  has(emailFlags({ email_ask: 'Worth a 48-hour demo? Or should I send the one-pager?' }), 'ask-multiple'), true);
t.check('a request in the body is a second ask',
  has(emailFlags({ email_body: bodyWith('Would you be open to a quick demo?') }), 'ask-multiple'), true);
t.check('"let me know" in the body is a second ask',
  has(emailFlags({ email_body: bodyWith('Let me know if this is useful.') }), 'ask-multiple'), true);
t.check('a rhetorical pain question in the body is not an ask (ANG-SILENT)',
  has(emailFlags({ email_body: bodyWith('How many sponsors visit your site and leave without ever contacting you?') }), 'ask-multiple'),
  false);
t.check('a call length in the ask is `ask-scheduling`',
  has(emailFlags({ email_ask: 'Open to a 30-minute call next week?' }), 'ask-scheduling'), true);
t.check('so is a scheduling offer', has(emailFlags({ email_ask: 'Can I schedule a time that works for you?' }), 'ask-scheduling'), true);
t.check('the DM is held to one ask too', has(linkedinFlags({ linkedin_ask: 'Thoughts.' }), 'ask-none'), true);

// ===========================================================================
// The existing link policy
// ===========================================================================

t.check('a URL the model wrote in warm-up week 1 is `link-in-warmup`',
  has(emailFlags({ email_body: bodyWith('See https://example.com for more.') }), 'link-in-warmup'), true);
t.check('and `unapproved-link`',
  has(emailFlags({ email_body: bodyWith('See https://example.com for more.') }), 'unapproved-link'), true);
const WK3 = assemble(null, { warmup_week: 3, library_link: [LINK] });
t.check('from week 3 the library link line is appended, untagged', has(WK3.email_flags, 'unapproved-link'), false);
t.check('it sits between the body and the ask', draftOf(WK3, 'email').body.indexOf(LINK.body + '\n\n' + CLEAN_ASK) !== -1, true);
t.check('and its code is recorded', draftOf(WK3, 'email').variant.indexOf('.A1.L-NP') !== -1, true);
t.check('the DM never carries it', draftOf(WK3, 'linkedin').body.indexOf('noblepath.example'), -1);
t.check('in week 2 it is not appended', draftOf(assemble(null, { warmup_week: 2, library_link: [LINK] }), 'email').body.indexOf('noblepath.example'), -1);
t.check('in week 3 a URL the model wrote is still `unapproved-link`',
  has(emailFlags({ email_body: bodyWith('See https://example.com.') }, { warmup_week: 3, library_link: [LINK] }), 'unapproved-link'), true);
t.check('a LinkedIn URL is `linkedin-link`',
  has(emailFlags({ email_body: bodyWith('See https://linkedin.com/in/x.') }), 'linkedin-link'), true);
t.check('a PDF is `pdf-link`', has(emailFlags({ email_body: bodyWith('See https://x.example/deck.pdf.') }), 'pdf-link'), true);
t.check('two URLs is `urls`',
  has(emailFlags({ email_body: bodyWith('See https://a.example and https://b.example.') }), 'urls'), true);
t.check('any URL in the DM is `unapproved-link`',
  has(linkedinFlags({ linkedin_body: 'Your site lists oncology work. See https://a.example. Live at NoblePath.' }), 'unapproved-link'), true);

// ===========================================================================
// The product name -- at most once, in brackets, never in the subject
// ===========================================================================

t.check('"(we call it Nova)" once is clean', has(CLEAN.email_flags, 'product-name-repeat') || has(CLEAN.email_flags, 'product-name-unbracketed'), false);
t.check('the name twice is `product-name-repeat`',
  has(emailFlags({ email_body: bodyWith('Nova answers at any hour.') }), 'product-name-repeat'), true);
t.check('the name outside brackets is `product-name-unbracketed`',
  has(emailFlags({ email_body: CLEAN_BODY.replace('(we call it Nova)', 'called Nova') }), 'product-name-unbracketed'), true);
t.check('the name in the subject is `subject-product`',
  has(emailFlags({ email_subject: 'Nova for oncology sponsor inquiries' }), 'subject-product'), true);
t.check('"NoblePath" is not the product name',
  has(emailFlags(), 'product-name-repeat'), false);

// ===========================================================================
// "AI" -- at most once, never in the subject, never "AI-powered"
// ===========================================================================

t.check('"AI" twice is `ai-repeat`',
  has(emailFlags({ email_body: bodyWith('The AI answers at any hour.') }), 'ai-repeat'), true);
t.check('"AI" in the subject is `subject-ai`',
  has(emailFlags({ email_subject: 'An AI answer for oncology sponsors' }), 'subject-ai'), true);
t.check('"AI-powered" is `ai-powered`',
  has(emailFlags({ email_body: CLEAN_BODY.replace('an AI assistant', 'an AI-powered assistant') }), 'ai-powered'), true);
t.check('"said" and "maintain" are not "AI"',
  has(emailFlags({ email_body: bodyWith('Your team maintains the pages it said it would.') }), 'ai-repeat'), false);

// ===========================================================================
// The prospect is never the one sponsoring
// ===========================================================================

t.check('"You\'re sponsoring" is `prospect-sponsor`',
  has(emailFlags({ email_body: CLEAN_BODY.replace('Your site lists oncology and respiratory work.', "You're sponsoring a recruiting trial.") }), 'prospect-sponsor'), true);
t.check('"with you as the sponsor" is `prospect-sponsor`',
  has(emailFlags({ email_body: bodyWith('ClinicalTrials.gov has a trial with you as the sponsor.') }), 'prospect-sponsor'), true);
t.check('"you sponsor" is `prospect-sponsor`',
  has(emailFlags({ email_body: bodyWith('The trial you sponsor is recruiting.') }), 'prospect-sponsor'), true);
t.check('in the subject too',
  has(emailFlags({ email_subject: "You're sponsoring an oncology trial" }), 'prospect-sponsor'), true);
t.check('in the DM too',
  has(linkedinFlags({ linkedin_body: "You're sponsoring a trial. Live at NoblePath." }), 'prospect-sponsor'), true);
t.check('"sponsor" for their clients is the point, not a violation', has(CLEAN.email_flags, 'prospect-sponsor'), false);
t.check('"you\'re losing sponsor inquiries" is not calling them a sponsor',
  has(emailFlags({ email_body: bodyWith("You're losing sponsor inquiries after hours.") }), 'prospect-sponsor'), false);

// ===========================================================================
// A contact with no email address (a LinkedIn-only contact, Workflow 3b)
// ===========================================================================

t.check('an email to a contact with no address is `no-address`',
  has(emailFlags({}, { email_addressing: 'unaddressed' }), 'no-address'), true);
t.check('...so it carries the tag in its variant, where the Approval Gate reads it',
  /\+.*\bno-address\b/.test(draftOf(assemble({}, { email_addressing: 'unaddressed' }), 'email').variant), true);
t.check('the LinkedIn DM to the same contact is not tagged',
  has(linkedinFlags({}, { email_addressing: 'unaddressed' }), 'no-address'), false);
t.check('a role inbox is addressed', has(emailFlags({}, { email_addressing: 'role-inbox' }), 'no-address'), false);
t.check('a named mailbox is addressed', has(emailFlags({}, { email_addressing: 'named' }), 'no-address'), false);
t.check('a personal-shaped address with no named owner is addressed',
  has(emailFlags({}, { email_addressing: 'unnamed' }), 'no-address'), false);

// ===========================================================================
// Forbidden claims
// ===========================================================================

t.check('a guarantee is `claim-guarantee`',
  has(emailFlags({ email_body: bodyWith("You'll never lose a sponsor again.") }), 'claim-guarantee'), true);
t.check('"guaranteed" is `claim-guarantee`',
  has(emailFlags({ email_body: bodyWith('Qualified leads, guaranteed.') }), 'claim-guarantee'), true);
t.check('"no inquiry is lost" is `claim-guarantee`',
  has(emailFlags({ email_body: bodyWith('No inquiry is ever lost.') }), 'claim-guarantee'), true);
t.check('a percentage the library does not hold is `claim-number`',
  has(emailFlags({ email_body: bodyWith('It cuts response time by 80%.') }), 'claim-number'), true);
t.check('a multiplier is `claim-number`',
  has(emailFlags({ email_body: bodyWith('CROs see 3x more qualified leads.') }), 'claim-number'), true);
t.check('a spelled-out multiplier is `claim-number`',
  has(emailFlags({ email_body: bodyWith('It doubles your qualified leads.') }), 'claim-number'), true);
t.check('a client count beyond the two is `claim-number`',
  has(emailFlags({ email_body: bodyWith('Dozens of CROs rely on it.') }), 'claim-number'), true);
t.check('"two CROs" is a number the library does not hold for a Poland lead',
  has(emailFlags({ email_body: bodyWith("It's live at two CROs.") }), 'claim-number'), true);
t.check('but it is for a lead whose proof line says it',
  has(emailFlags({ email_body: CLEAN_BODY.replace("It's live at NoblePath, an oncology CRO in Türkiye.", "It's live at two CROs, in Türkiye and Mexico.") },
    { library_pools: Object.assign({}, POOLS, { proof: [PR_BOTH] }) }), 'claim-number'), false);
t.check('numbers the library holds (11pm, 48-hour) are fine', has(CLEAN.email_flags, 'claim-number'), false);
t.check('"multi-million-dollar" is fine -- ANG-STAKES holds it',
  has(emailFlags({ email_body: bodyWith('A single sponsor inquiry can be a multi-million-dollar study.') }), 'claim-number'), false);
const TRIAL_FACTS = '1. ClinicalTrials.gov lists 2 recruiting trials registered under your company. The ones named are: "Efficacy of INM004 in Children With STEC-HUS"; "A Phase 3 Study". Nothing else about them is known.';
t.check('"two recruiting trials" is fine when the facts say 2',
  has(emailFlags({ email_body: CLEAN_BODY.replace('Your site lists oncology and respiratory work.', 'You have two recruiting trials on ClinicalTrials.gov.') },
    { fact_sheet: TRIAL_FACTS, trial_titles: ['Efficacy of INM004 in Children With STEC-HUS', 'A Phase 3 Study'] }), 'claim-number'), false);
t.check('a digit inside a quoted trial title is the registry\'s, not a claim',
  has(emailFlags({ email_body: CLEAN_BODY.replace('Your site lists oncology and respiratory work.', 'Your recruiting trial, Efficacy of INM004 in Children With STEC-HUS, is on ClinicalTrials.gov.') },
    { fact_sheet: TRIAL_FACTS, trial_titles: ['Efficacy of INM004 in Children With STEC-HUS', 'A Phase 3 Study'] }), 'claim-number'), false);
t.check('identifying visitors is `claim-visitor-id`',
  has(emailFlags({ email_body: bodyWith('It identifies the companies visiting your site.') }), 'claim-visitor-id'), true);
t.check('"see who visits" is `claim-visitor-id`',
  has(emailFlags({ email_body: bodyWith('You see exactly who visits your pricing page.') }), 'claim-visitor-id'), true);
t.check('ANG-SILENT asks about visitors without claiming to identify them',
  has(emailFlags({ email_body: bodyWith('How many sponsors visit your site and leave without ever contacting you?') }), 'claim-visitor-id'), false);
t.check('a named CRM is `claim-named-tool`',
  has(emailFlags({ email_body: bodyWith('Leads land straight in HubSpot.') }), 'claim-named-tool'), true);
t.check('a named sheet tool is `claim-named-tool`',
  has(emailFlags({ email_body: bodyWith('Leads land in Google Sheets.') }), 'claim-named-tool'), true);
t.check('"your CRM" is generic and fine',
  has(emailFlags({ email_body: bodyWith('Leads land in your CRM.') }), 'claim-named-tool'), false);
t.check('supported languages are `claim-language`',
  has(emailFlags({ email_body: bodyWith('It answers in Turkish and English.') }), 'claim-language'), true);
t.check('"multilingual" is `claim-language`',
  has(emailFlags({ email_body: bodyWith('It is multilingual.') }), 'claim-language'), true);
t.check('"chatbot" is `claim-chatbot`',
  has(emailFlags({ email_body: bodyWith('It is a chatbot for your site.') }), 'claim-chatbot'), true);
// Settled 2026-10-02 (migration 013): Nova sends the booking link, the sponsor
// books; and it answers from the website, never from SOPs or client documents.
t.check('Nova booking the call is `claim-books`',
  has(emailFlags({ email_body: bodyWith('It answers sponsors at any hour and books the call.') }), 'claim-books'), true);
t.check('Nova filling a calendar is `claim-books`',
  has(emailFlags({ email_body: bodyWith('Qualified sponsors land straight into your calendar.') }), 'claim-books'), true);
t.check('BEN-BOOK as settled -- the sponsor books through the link -- is fine',
  has(emailFlags({ email_body: bodyWith('It sends qualified sponsors your booking link, so they can book a call with your team.') }),
    'claim-books'), false);
t.check('SOPs as its source is `claim-sop`',
  has(emailFlags({ email_body: bodyWith('It answers sponsors from your own SOPs and service pages at any hour.') }), 'claim-sop'), true);
t.check('client documentation as its source is `claim-sop`',
  has(emailFlags({ email_body: bodyWith('It answers sponsors from your own documentation at any hour.') }), 'claim-sop'), true);
t.check('their website as its source is fine', has(CLEAN.email_flags, 'claim-sop'), false);
t.check('banned adjectives from the skill are `adjective`',
  ['revolutionary', 'cutting-edge', 'innovative', 'game-changing', 'seamless'].map(function (a) {
    return has(emailFlags({ email_body: bodyWith('It is a ' + a + ' fit.') }), 'adjective');
  }), [true, true, true, true, true]);
t.check('a merge tag is `merge-tag`', has(emailFlags({ email_body: bodyWith('Hi [First Name].') }), 'merge-tag'), true);

// ===========================================================================
// Proof -- the geography-matched deployment, and no other
// ===========================================================================

t.check('the matched deployment named is clean', has(CLEAN.email_flags, 'proof-missing') || has(CLEAN.email_flags, 'proof-geo'), false);
t.check('no proof at all is `proof-missing`',
  has(emailFlags({ email_body: CLEAN_BODY.replace(" It's live at NoblePath, an oncology CRO in Türkiye.", '') }), 'proof-missing'), true);
t.check('the other deployment is `proof-geo`',
  has(emailFlags({ email_body: CLEAN_BODY.replace('NoblePath, an oncology CRO in Türkiye', 'Vertex Clinical Research in Mexico') }), 'proof-geo'), true);
t.check('and the matched one is then missing',
  has(emailFlags({ email_body: CLEAN_BODY.replace('NoblePath, an oncology CRO in Türkiye', 'Vertex Clinical Research in Mexico') }), 'proof-missing'), true);
t.check('PR-BOTH is satisfied by "two CROs"',
  has(emailFlags({ email_body: CLEAN_BODY.replace("It's live at NoblePath, an oncology CRO in Türkiye.", "It's live at two CROs, in Türkiye and Mexico.") },
    { library_pools: Object.assign({}, POOLS, { proof: [PR_BOTH] }) }), 'proof-missing'), false);
t.check('but naming a deployment PR-BOTH does not name is `proof-geo`',
  has(emailFlags({}, { library_pools: Object.assign({}, POOLS, { proof: [PR_BOTH] }) }), 'proof-geo'), true);
t.check('the DM is held to the proof rule too',
  has(linkedinFlags({ linkedin_body: 'Your site lists oncology and respiratory work.' }), 'proof-missing'), true);

// ===========================================================================
// Grounding -- carried from v1/v2, applied to the whole composed text
// ===========================================================================

t.check('a therapeutic area the lead lacks is `ungrounded-area` -- the example\'s immunology',
  has(emailFlags({ email_body: CLEAN_BODY.replace('oncology and respiratory', 'oncology and immunology') }), 'ungrounded-area'), true);
t.check('"oncology CRO" in the proof line is fine for an oncology lead', has(CLEAN.email_flags, 'ungrounded-area'), false);
t.check('opening on the founder is `hook-opener`',
  has(emailFlags({ email_body: 'Anna Nowak runs a CRO. ' + CLEAN_BODY }), 'hook-opener'), true);
t.check('opening on the city is `hook-opener`',
  has(emailFlags({ email_body: 'Budapest is home to your team. ' + CLEAN_BODY }), 'hook-opener'), true);
t.check('opening on the source is `hook-source`',
  has(emailFlags({ email_body: 'ClinicalTrials.gov lists a trial for you. ' + CLEAN_BODY }), 'hook-source'), true);
t.check('a trial credited to their site is `hook-source`',
  has(emailFlags({ email_body: 'Your site lists a recruiting trial. ' + CLEAN_BODY }), 'hook-source'), true);

// ===========================================================================
// Subject
// ===========================================================================

t.check('a clean subject is untagged', CLEAN.email_flags.filter(function (f) { return /^subject/.test(f); }), []);
t.check('29 characters is `subject`', has(emailFlags({ email_subject: 'Oncology inquiries after hour' }), 'subject'), true);
t.check('55 characters is inside', has(emailFlags({ email_subject: 'Oncology sponsor inquiries that arrive after your hours' }), 'subject'), false);
t.check('56 characters is `subject`', has(emailFlags({ email_subject: 'Oncology sponsor inquiries that arrive after your hours.' }), 'subject'), true);
t.check('"Re:" is `subject-reply`', has(emailFlags({ email_subject: 'Re: oncology sponsor inquiries after hours' }), 'subject-reply'), true);
t.check('Title Case is `subject-caps`', has(emailFlags({ email_subject: 'Oncology Sponsor Inquiries After Hours' }), 'subject-caps'), true);
t.check('SHOUTING is `subject-caps`', has(emailFlags({ email_subject: 'ONCOLOGY sponsor inquiries after hours' }), 'subject-caps'), true);
t.check('a trial code the facts spell in capitals is fine',
  has(emailFlags({ email_subject: 'Your INM004 trial and sponsor inquiries' },
    { fact_sheet: TRIAL_FACTS, trial_titles: ['Efficacy of INM004 in Children With STEC-HUS'] }), 'subject-caps'), false);
t.check('so is "CRO"', has(emailFlags({ email_subject: 'Sponsor inquiries a CRO answers after hours' }), 'subject-caps'), false);
t.check('a number the facts do not hold is `subject-ungrounded`',
  has(emailFlags({ email_subject: 'A question for a 40-person oncology team' }), 'subject-ungrounded'), true);
t.check('unless it is the confirmed headcount',
  has(emailFlags({ email_subject: 'A question for a 40-person oncology team' }, { headcount: 40 }), 'subject-ungrounded'), false);
t.check('an area the lead lacks in the subject is `subject-ungrounded`',
  has(emailFlags({ email_subject: 'Immunology sponsor inquiries after hours' }), 'subject-ungrounded'), true);

// ===========================================================================
// Claim codes and confirmation
// ===========================================================================

t.check('a code the lead was not offered is `claim-code`',
  has(emailFlags({ email_claims: ['D1', 'PR-MX'] }), 'claim-code'), true);
t.check('and is left out of the variant', draftOf(assemble({ email_claims: ['D1', 'PR-MX', 'PR-TR'] }), 'email').variant.indexOf('PR-MX'), -1);
t.check('reporting no codes at all is `claim-code`', has(emailFlags({ email_claims: [] }), 'claim-code'), true);
t.check('an approved ask the model did not list is attributed from its text',
  draftOf(assemble({ email_claims: ['D1', 'ANG-HOURS', 'BEN-247', 'PR-TR'] }), 'email').variant,
  'role-inbox/D1.ANG-HOURS.BEN-247.PR-TR.A1');
t.check('so is the proof it names',
  draftOf(assemble({ email_claims: ['D1', 'ANG-HOURS', 'BEN-247', 'A1'] }), 'email').variant,
  'role-inbox/D1.ANG-HOURS.BEN-247.PR-TR.A1');
t.check('and PR-BOTH from "two CROs"',
  draftOf(assemble({ email_claims: ['D1', 'A1'], email_body: CLEAN_BODY.replace("It's live at NoblePath, an oncology CRO in Türkiye.", "It's live at two CROs, in Türkiye and Mexico.") },
    { library_pools: Object.assign({}, POOLS, { proof: [PR_BOTH] }) }), 'email').variant,
  'role-inbox/D1.PR-BOTH.A1');
t.check('an ask of the model\'s own wording is not forced onto a library line',
  draftOf(assemble({ email_claims: ['D1', 'PR-TR'], email_ask: 'Should I set one up for your team?' }), 'email').variant,
  'role-inbox/D1.PR-TR');
t.check('a reported ask code is kept, not second-guessed',
  draftOf(assemble({ email_claims: ['D1', 'PR-TR', 'A2'] }), 'email').variant, 'role-inbox/D1.PR-TR.A2');
t.check('each message is judged on its own list -- a bad DM code does not tag the email',
  [has(emailFlags({ linkedin_claims: ['XX-1'] }), 'claim-code'), has(linkedinFlags({ linkedin_claims: ['XX-1'] }), 'claim-code')],
  [false, true]);
const UNCONF = Object.assign({}, POOLS, { benefit: [line('BEN-247', BEN_247.body, false, BEN_247.capabilities), BEN_FIT] });
t.check('using an unconfirmed claim is `unconfirmed-claim`', has(emailFlags({}, { library_pools: UNCONF }), 'unconfirmed-claim'), true);
// Skill v3 section 3, No repeats: the description and the benefits must not
// repeat the same capability (claims_library.capabilities, migration 013).
const BOTH_D = Object.assign({}, POOLS, { description: [D1, D2], benefit: [BEN_247, BEN_FIT, BEN_BOOK, BEN_CAPTURE, BEN_BRIEF] });
t.check('D2 with BEN-247 repeats "answers": `claim-repeat`',
  has(emailFlags({ email_claims: ['D2', 'ANG-HOURS', 'BEN-247', 'PR-TR', 'A1'] }, { library_pools: BOTH_D }), 'claim-repeat'), true);
t.check('D2 with BEN-BOOK repeats "booking-link": `claim-repeat`',
  has(emailFlags({ email_claims: ['D2', 'ANG-HOURS', 'BEN-BOOK', 'PR-TR', 'A1'] }, { library_pools: BOTH_D }), 'claim-repeat'), true);
t.check('two benefits that share a capability are a repeat too (BEN-CAPTURE, BEN-BRIEF)',
  has(emailFlags({ email_claims: ['D2', 'ANG-HOURS', 'BEN-CAPTURE', 'BEN-BRIEF', 'PR-TR', 'A1'] }, { library_pools: BOTH_D }), 'claim-repeat'), true);
t.check('D2 with BEN-FIT says two different things: no tag',
  has(emailFlags({ email_claims: ['D2', 'ANG-HOURS', 'BEN-FIT', 'PR-TR', 'A1'] }, { library_pools: BOTH_D }), 'claim-repeat'), false);
t.check('D1 with BEN-247 and BEN-BOOK: no shared capability, no tag',
  has(emailFlags({ email_claims: ['D1', 'ANG-HOURS', 'BEN-247', 'BEN-BOOK', 'PR-TR', 'A1'] }, { library_pools: BOTH_D }), 'claim-repeat'), false);
t.check('judged per message: a DM that repeats tags the DM, not the email',
  [has(emailFlags({ linkedin_claims: ['D2', 'BEN-247', 'PR-TR', 'A2'] }, { library_pools: BOTH_D }), 'claim-repeat'),
   has(linkedinFlags({ linkedin_claims: ['D2', 'BEN-247', 'PR-TR', 'A2'] }, { library_pools: BOTH_D }), 'claim-repeat')],
  [false, true]);
t.check('an unconfirmed claim the model did not use is not',
  has(emailFlags({ email_claims: ['D1', 'ANG-HOURS', 'BEN-FIT', 'PR-TR', 'A1'] }, { library_pools: UNCONF }), 'unconfirmed-claim'), false);

// --- the repair loop (2026-10-03): this same code runs again on a repaired answer --
const FIRST = assemble();
t.check('it hands on the parsed answer (what a repair edits) and the email fields it came from',
  [FIRST.composition.email_body, FIRST.composition.email_claims.length, FIRST.approval.repair_fields, FIRST.repair],
  [CLEAN_BODY, 5, ['email_subject', 'email_body', 'email_ask'], null]);
const REPAIRED = run(Object.assign(claude(generation()), { usage: null,
  repair: { round: 1, target: 0, history: [{ attempt: 1, result: 'hold', reasons: ['claim-check: widened "x" (BEN-247)'] }] } }));
t.check('a repaired answer (Apply Repair\'s output) runs every rule again and carries the attempts on to the gate',
  [REPAIRED.write, REPAIRED.repair.round, REPAIRED.repair.history.length, draftOf(REPAIRED, 'email').body === draftOf(FIRST, 'email').body,
   REPAIRED.email_flags],
  [true, 1, 1, true, FIRST.email_flags]);

t.done();

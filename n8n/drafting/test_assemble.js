// Unit tests for code_assemble.js -- constraint enforcement and draft assembly.
//
// The point of this node is that a model is never trusted to reproduce a rule.
// Every Section 5 / Section 9 message rule that a string match can enforce is
// enforced here, and the tests below are the evidence that each one actually
// fires -- including the compliance-critical one: the opt-out sentence is
// appended verbatim, never generated.
//
// Drafting skill v2 (NovaScout_DraftingSkill.md) moved everything after the
// first sentence out of the model: the model writes a subject and two hooks,
// and the problem, outcome, proof and ask are library lines inserted here. The
// first block of cases is the v1 suite, in its original order, with inputs moved
// from `email_body` to `email_hook`. The few whose rule v2 deliberately changed
// are marked [v2] and say what replaced them. The v2 cases follow.

const path = require('path');
const { runForEachItem, runner } = require('./harness');

const ASSEMBLE = path.join(__dirname, 'code_assemble.js');

// build_workflow.py substitutes the signature at build time. The tests run the
// same substitution so they exercise the shipped string, not a placeholder.
const SIGNATURE = 'Fatima';
const withSignature = function (src) {
  return src.replace('__SIGNATURE__', JSON.stringify(SIGNATURE));
};

const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

// Library lines as Assess Grounding hands them over: skill section 4's seed,
// confirmed, with the proof already resolved for the lead's country.
function line(code, body, confirmed) {
  return { code: code, body: body, confirmed: confirmed !== false };
}
const P1 = line('P1', 'When a sponsor shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?');
const P2 = line('P2', 'How many sponsor inquiries reach you only after the sponsor has already moved on?');
const O1 = line('O1', 'We built Nova to answer from your own material, qualify the lead, and route it wherever you already work — no new dashboard.');
const O3 = line('O3', 'Nova, which we built, answers sponsors from your own SOPs and service pages and hands your team a qualified lead, in your system, not ours.');
const PR_TR = line('PR-TR', "It's live at NoblePath, an oncology CRO in Türkiye.");
const A1 = line('A1', "Worth a 48-hour demo built on your own material? Reply yes and I'll set it up.");
const A3 = line('A3', 'Want the 90-second recording of it running at NoblePath? Reply yes.');
const LINK = line('L-NP', 'Their site: https://noblepath.example');

const LIBRARY = { problem: [P1, P2], outcome: [O1, O3], proof: [PR_TR], ask: [A1, A3] };

const TAXONOMY = ['Oncology', 'Cardiovascular', 'Central Nervous System', 'Immunology',
  'Infectious Disease', 'Endocrinology', 'Metabolic Disorders', 'Respiratory', 'Rare Diseases',
  'Internal Medicine', 'Anesthesiology', 'Dermatology', 'Rheumatology', 'Ophthalmology',
  'Gastroenterology', 'Nephrology', 'Hematology'];

function absentGiven(areas) {
  return TAXONOMY.filter(function (a) { return areas.indexOf(a) === -1; });
}

function assessed(over) {
  return Object.assign(
    {
      lead_id: 1,
      domain: 'example.com',
      company_name: 'Example CRO',
      fit_score: 65,
      fact_count: 3,
      fact_kinds: ['therapeutic_area', 'city', 'founder_name'],
      fact_sheet:
        '1. Therapeutic areas listed on their own website: Oncology and Respiratory. This says ' +
        'nothing about which area any particular trial belongs to.\n' +
        '2. The company is based in Budapest. This says nothing about where any trial runs or ' +
        'where any staff sit.\n' +
        '3. Named on their own site as founder or MD: Anna Nowak. This says nothing about which ' +
        'trials or clients this person personally handles.',
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
      library_pools: LIBRARY,
      library_link: [],
      warmup_week: 1,
    },
    over || {}
  );
}

// A clean generation: one grounded fact per hook, a subject from the same fact,
// no banned words, no links, no merge tags.
function generation(over) {
  return Object.assign(
    {
      email_subject: 'Your oncology work — one question',
      email_hook: 'Your site lists oncology and respiratory work.',
      linkedin_hook: 'Your site lists oncology among the areas you work in.',
    },
    over || {}
  );
}

function assemble(gen, over) {
  // A `response` or `error` key means the caller is handing us a raw HTTP-node
  // item on purpose (a refusal, or unparseable prose). Anything else is a
  // generation to be encoded the way Ollama would.
  const raw = gen === null || typeof gen === 'string' ||
    Object.prototype.hasOwnProperty.call(gen, 'response') ||
    Object.prototype.hasOwnProperty.call(gen, 'error');
  const resp = raw ? gen : { response: JSON.stringify(gen) };
  return runForEachItem(
    ASSEMBLE,
    [{ json: resp }],
    { 'Assess Grounding': [{ json: assessed(over) }] },
    withSignature
  )[0].json;
}

function draftOf(out, channel) {
  return out.payload.drafts.filter(function (d) { return d.channel === channel; })[0];
}

function has(flags, f) {
  return flags.indexOf(f) !== -1;
}

function count(hay, needle) {
  return hay.split(needle).length - 1;
}

const t = runner('code_assemble.js -- constraint enforcement');

// ---------------------------------------------------------------------------
// The model has to have answered usably
// ---------------------------------------------------------------------------

t.check('an Ollama error is not a draft', assemble({ error: 'ECONNREFUSED' }).write, false);
t.check(
  'a non-JSON response is dropped rather than shipped as prose',
  assemble({ response: 'Sure! Here is your email:' }).write,
  false
);
t.check(
  'a dropped generation names why',
  assemble({ error: 'ECONNREFUSED' }).skip_reason.indexOf('ECONNREFUSED') !== -1,
  true
);
t.check(
  'a failed generation does NOT get recorded as low-context',
  assemble({ error: 'ECONNREFUSED' }).low_context,
  undefined
);

// ---------------------------------------------------------------------------
// Shape: two rows, one per channel
// ---------------------------------------------------------------------------

const CLEAN = assemble(generation());

t.check('exactly two drafts are produced', CLEAN.payload.drafts.length, 2);
t.check(
  'one per channel',
  CLEAN.payload.drafts.map(function (d) { return d.channel; }),
  ['email', 'linkedin']
);
t.check('a clean email carries no flags', CLEAN.email_flags, []);
t.check('a clean LinkedIn DM carries no flags', CLEAN.linkedin_flags, []);
// [v2] variant used to be the addressing mode alone. It now also records which
// library lines went into the draft -- the learning loop's unit of comparison.
t.check(
  '[v2] so variant is the addressing mode plus the library lines used',
  draftOf(CLEAN, 'email').variant,
  'role-inbox/P2.O3.PR-TR.A3'
);
t.check('[v2] for both channels', draftOf(CLEAN, 'linkedin').variant, 'named/P1.O1.PR-TR.A1');
t.check('and the lead advances', CLEAN.payload.advance, true);

// ---------------------------------------------------------------------------
// Section 5 compliance: the opt-out sentence is appended, never generated
// ---------------------------------------------------------------------------

t.check(
  'the email carries the opt-out sentence verbatim',
  draftOf(CLEAN, 'email').body.indexOf(OPT_OUT) !== -1,
  true
);
t.check(
  'even when the model never wrote one',
  generation().email_hook.indexOf(OPT_OUT),
  -1
);
t.check(
  'the LinkedIn DM does NOT carry it -- Section 5 governs the mailbox',
  draftOf(CLEAN, 'linkedin').body.indexOf(OPT_OUT),
  -1
);
t.check(
  'the email ends with the signature',
  draftOf(CLEAN, 'email').body.trim().slice(-SIGNATURE.length),
  SIGNATURE
);
t.check(
  'the LinkedIn DM has no signature -- Section 6 makes it a human sending from their own profile',
  draftOf(CLEAN, 'linkedin').body.indexOf(SIGNATURE),
  -1
);
t.check(
  'the email opens with the greeting the guard chose',
  draftOf(CLEAN, 'email').body.indexOf('Hello,'),
  0
);
t.check(
  'the LinkedIn DM opens with its own, different greeting',
  draftOf(CLEAN, 'linkedin').body.indexOf('Hi Anna,'),
  0
);
t.check('the email keeps its subject', draftOf(CLEAN, 'email').subject.length > 0, true);
t.check('a LinkedIn DM has no subject', draftOf(CLEAN, 'linkedin').subject, null);

// ---------------------------------------------------------------------------
// The model ignoring instructions is repaired, not flagged
// ---------------------------------------------------------------------------

const ECHOED = assemble(
  generation({
    email_hook: 'Hello,\n\nYou run oncology trials in Budapest. NoblePath uses ours.\n\nBest regards,\nFatima',
  })
);
t.check(
  'a greeting the model added anyway is not duplicated',
  (draftOf(ECHOED, 'email').body.match(/Hello,/g) || []).length,
  1
);
t.check(
  'a sign-off the model added anyway is stripped',
  draftOf(ECHOED, 'email').body.indexOf('Best regards'),
  -1
);
t.check(
  'and its invented signature line goes with it',
  (draftOf(ECHOED, 'email').body.match(/Fatima/g) || []).length,
  1
);
t.check(
  'a "Hi <name>," the model chose itself is replaced by the guard\'s greeting',
  draftOf(assemble(generation({ email_hook: 'Hi Enrique,\n\nYou run oncology trials. NoblePath uses ours.' })), 'email')
    .body.indexOf('Enrique'),
  -1
);

// ---------------------------------------------------------------------------
// Section 9 / Section 5 constraint checks
// ---------------------------------------------------------------------------

const LONG = assemble(
  generation({
    email_hook: ('word ').repeat(90) + ' NoblePath',
  })
);
t.check('an over-length email is flagged long', LONG.email_flags.indexOf('long') !== -1, true);
t.check('and the flag lands in variant', /^role-inbox\/[^+]+\+long$/.test(draftOf(LONG, 'email').variant), true);
t.check(
  'the draft is still written -- a flagged draft is fixable, a dropped one is not',
  LONG.write,
  true
);

t.check(
  'a banned adjective the doc names is caught',
  assemble(generation({ email_hook: 'Our revolutionary assistant. NoblePath uses it.' })).email_flags
    .indexOf('adjective') !== -1,
  true
);
t.check(
  'the other one the doc names is caught too',
  assemble(generation({ email_hook: 'Cutting-edge assistant. NoblePath uses it.' })).email_flags
    .indexOf('adjective') !== -1,
  true
);
t.check(
  'a merge-tag tell in the body is caught',
  assemble(generation({ email_hook: 'We work with [Company Name]. NoblePath uses ours.' }))
    .email_flags.indexOf('merge-tag') !== -1,
  true
);
// A merge tag inside a greeting the model added anyway is REMOVED, not flagged.
// The check asks "does the draft the reviewer sees contain a tell?", and after
// the stripper it does not -- flagging text that is no longer in the draft
// would send the reviewer hunting for something that is not there.
t.check(
  'a merge tag inside a model-added greeting is stripped rather than flagged',
  draftOf(assemble(generation({ email_hook: 'Hi [First Name],\n\nNoblePath uses ours.' })), 'email')
    .body.indexOf('[First Name]'),
  -1
);
t.check(
  'and that draft is therefore clean, not tagged for a tell it no longer has',
  assemble(generation({ email_hook: 'Hi [First Name],\n\nNoblePath uses ours.' })).email_flags
    .indexOf('merge-tag'),
  -1
);
// The stripper must not eat real content that merely begins with "Hi"/"Hello".
t.check(
  'a body starting with an unpunctuated "Hi" word keeps its content',
  draftOf(
    assemble(generation({ email_hook: 'Hi-tech vendors keep pitching you. NoblePath uses ours.' })),
    'email'
  ).body.indexOf('vendors keep pitching you') !== -1,
  true
);
t.check(
  'a handlebars tell is caught',
  assemble(generation({ email_hook: 'We work with {{company}}. NoblePath uses ours.' })).email_flags
    .indexOf('merge-tag') !== -1,
  true
);
t.check(
  'more than one URL breaks Section 5',
  assemble(
    generation({
      email_hook: 'See https://a.example and https://b.example. NoblePath uses ours.',
    })
  ).email_flags.indexOf('urls') !== -1,
  true
);
// v1 let the model write the one URL. v2 lets only the library's link line in,
// and only after warm-up week 2 -- so "one URL allowed" is now that case.
t.check(
  'exactly one URL is allowed',
  assemble(generation(), { warmup_week: 3, library_link: [LINK] }).email_flags.indexOf('urls'),
  -1
);
// [v2] "an email that never mentions the demo is flagged" (`no-demo`) is
// retired: the proof is a library line inserted by construction, so there is no
// email without one to flag. What replaced it:
t.check(
  '[v2] every email carries the geography-matched proof line, by construction',
  draftOf(assemble(generation({ email_hook: 'You run oncology trials in Budapest. Worth a chat?' })), 'email')
    .body.indexOf(PR_TR.body) !== -1,
  true
);
t.check(
  'a marketing-formula subject is flagged on length',
  assemble(generation({ email_subject: 'Transform Your Clinical Operations With AI Today And Forever' }))
    .email_flags.indexOf('subject') !== -1,
  true
);
t.check(
  'an empty subject is flagged',
  assemble(generation({ email_subject: '' })).email_flags.indexOf('subject') !== -1,
  true
);
t.check(
  'an empty body is flagged empty',
  assemble(generation({ email_hook: '' })).email_flags.indexOf('empty') !== -1,
  true
);
t.check(
  'multiple violations all land in variant',
  draftOf(assemble(generation({ email_hook: 'Our revolutionary tool for [Company].' })), 'email')
    .variant,
  'role-inbox/P2.O3.PR-TR.A3+adjective,merge-tag'
);

// The LinkedIn channel is checked independently -- a violation in one must not
// tag the other.
const ONE_BAD = assemble(
  generation({ linkedin_hook: 'Our cutting-edge platform is here.' })
);
t.check('a LinkedIn violation is flagged', ONE_BAD.linkedin_flags.indexOf('adjective') !== -1, true);
t.check('and does not contaminate the email', ONE_BAD.email_flags, []);
// [v2] was "the LinkedIn DM is NOT flagged no-demo -- that check is email-only".
// no-demo is gone; the email-only rules now are the subject and link rules.
t.check(
  '[v2] the LinkedIn DM is NOT held to the email-only subject and link rules',
  assemble(generation({ email_subject: '' }), { warmup_week: 1 }).linkedin_flags
    .filter(function (f) { return /^subject|link/.test(f); }),
  []
);

// ---------------------------------------------------------------------------
// The word count counts what the recipient reads
// ---------------------------------------------------------------------------

// [v2] v1 counted greeting + body + opt-out. Skill section 2: "Body (1-5)
// stays under 80 words" -- hook, problem, outcome, proof, ask. The opt-out and
// signature are listed there as the fixed frame, outside the budget.
t.check(
  '[v2] the counted email is parts 1-5: greeting, opt-out and signature are not counted',
  CLEAN.email_words,
  7 + 14 + 25 + 9 + 11
);
t.check(
  '[v2] the LinkedIn count is parts 1-5 as well, and it carries no opt-out to count',
  CLEAN.linkedin_words,
  10 + 21 + 22 + 9 + 16
);

// ===========================================================================
// Drafting skill v2
// ===========================================================================

// --- Structure: hook, problem, outcome, proof, ask -- the skill's order ------

const EMAIL = draftOf(CLEAN, 'email').body;

t.check(
  'the email body is hook + problem, outcome + proof, ask -- skill section 5 layout',
  EMAIL,
  'Hello,\n\n' +
    'Your site lists oncology and respiratory work. ' + P2.body + '\n\n' +
    O3.body + ' ' + PR_TR.body + '\n\n' +
    A3.body + '\n\n' +
    OPT_OUT + '\n\n' + SIGNATURE
);
t.check(
  'every library line lands verbatim, not paraphrased',
  [P2, O3, PR_TR, A3].every(function (l) { return EMAIL.indexOf(l.body) !== -1; }),
  true
);
t.check(
  'exactly one ask: one line from the ask slot, never two',
  [A1, A3].map(function (l) { return count(EMAIL, l.body); }).reduce(function (a, b) { return a + b; }, 0),
  1
);
t.check(
  'exactly one problem line and one outcome line',
  [P1, P2].concat([O1, O3]).map(function (l) { return count(EMAIL, l.body); }).reduce(function (a, b) { return a + b; }, 0),
  2
);
t.check(
  'the hook adds no question: the only question marks are the problem and the ask',
  count(EMAIL, '?'),
  count(P2.body, '?') + count(A3.body, '?')
);
// The model's JSON schema has no field for these parts, and even if the model
// returned one anyway, nothing reads it. It cannot compose the pitch.
t.check(
  'model output outside subject/hooks is ignored -- the pitch cannot be composed',
  draftOf(assemble(generation({ email_body: 'Nova is a revolutionary AI that doubles your leads.' })), 'email')
    .body.indexOf('doubles your leads'),
  -1
);
t.check(
  'the proof is the line Assess Grounding resolved for the lead\'s country',
  draftOf(assemble(generation(), { library_pools: Object.assign({}, LIBRARY, {
    proof: [line('PR-MX', 'It\'s live at Vertex Clinical Research in Mexico.')],
  }) }), 'email').body.indexOf('Vertex Clinical Research in Mexico') !== -1,
  true
);

// --- Rotation: deterministic per lead, varied across a batch -----------------

t.check(
  'two leads draw different library lines',
  assemble(generation(), { lead_id: 1 }).email_lines !== assemble(generation(), { lead_id: 2 }).email_lines,
  true
);
t.check(
  'the same lead redrafted with the same hook draws the same lines',
  assemble(generation(), { lead_id: 7 }).email_lines,
  assemble(generation(), { lead_id: 7 }).email_lines
);
t.check(
  'the LinkedIn DM asks in different words from the email where the library has a choice',
  CLEAN.email_lines !== CLEAN.linkedin_lines,
  true
);

// --- Budget: pick lines that fit, rather than overshoot ----------------------

const SHORT_Q = line('PS', 'Do sponsors reach you after hours?');
const LONG_Q = line('PL', 'When a sponsor evaluates you late at night, after your team has gone home, does their inquiry ever reach a real person on your side before they move on to the next name on their list?');
const FIT = assemble(
  generation({ email_hook: 'You are sponsoring a recruiting trial on a rare condition that your site describes in some detail today.' }),
  { lead_id: 0, library_pools: Object.assign({}, LIBRARY, { problem: [LONG_Q, SHORT_Q] }) }
);
t.check(
  'a long hook moves to a shorter library line instead of going over the ceiling',
  FIT.email_lines.split('.')[0],
  'PS'
);
t.check('and the result is under the ceiling, unflagged', has(FIT.email_flags, 'long'), false);
t.check(
  'when nothing fits, the shortest combination is used -- and tagged long',
  assemble(generation({ email_hook: ('word ').repeat(60) + 'end.' }),
    { lead_id: 0, library_pools: Object.assign({}, LIBRARY, { problem: [LONG_Q, SHORT_Q] }) }).email_lines,
  'PS.O1.PR-TR.A3'
);

// --- What a word is ---------------------------------------------------------

t.check(
  "the skill's own section 5 example is under the ceiling -- a dash is not a word",
  assemble(
    { email_subject: 'INM004 trial — a quick question',
      email_hook: "You're sponsoring a recruiting trial — Efficacy of INM004 in Children With STEC-HUS.",
      linkedin_hook: "You're sponsoring a recruiting trial — Efficacy of INM004 in Children With STEC-HUS." },
    { lead_id: 0,
      trial_titles: ['Efficacy of INM004 in Children With STEC-HUS'],
      fact_sheet: '1. ClinicalTrials.gov lists 1 recruiting trial with you as the sponsor. The only one named is: "Efficacy of INM004 in Children With STEC-HUS".',
      library_pools: { problem: [P1], outcome: [O1],
        proof: [line('PR-MX', "It's live at Vertex Clinical Research in Mexico.")], ask: [A1] } }
  ).email_words,
  79
);

// --- Links: skill section 3 --------------------------------------------------

t.check(
  'warm-up week 1: the library link line is not used',
  draftOf(assemble(generation(), { warmup_week: 1, library_link: [LINK] }), 'email').body.indexOf('noblepath.example'),
  -1
);
t.check(
  'warm-up week 2 is still link-free',
  draftOf(assemble(generation(), { warmup_week: 2, library_link: [LINK] }), 'email').body.indexOf('noblepath.example'),
  -1
);
t.check(
  'week 1: a URL the model slipped into the hook is flagged link-in-warmup',
  has(assemble(generation({ email_hook: 'Your site, www.example.com, lists oncology.' }), { warmup_week: 1 }).email_flags,
    'link-in-warmup'),
  true
);
const WEEK3 = assemble(generation(), { warmup_week: 3, library_link: [LINK] });
t.check(
  'week 3: the library link line follows the proof',
  draftOf(WEEK3, 'email').body.indexOf(PR_TR.body + ' ' + LINK.body) !== -1,
  true
);
t.check('and that draft carries no link flag at all', WEEK3.email_flags, []);
t.check('and its code is recorded', WEEK3.email_lines, 'P2.O3.PR-TR.A3.L-NP');
t.check(
  'the LinkedIn DM never carries the link line',
  draftOf(WEEK3, 'linkedin').body.indexOf('noblepath.example'),
  -1
);
t.check(
  'week 3: any URL that is not the library link is unapproved',
  has(assemble(generation({ email_hook: 'Your site, https://klixar.example, lists oncology.' }),
    { warmup_week: 3, library_link: [LINK] }).email_flags, 'unapproved-link'),
  true
);
t.check(
  'week 3 with no link line in the library: a model URL is still unapproved',
  has(assemble(generation({ email_hook: 'See https://a.example for oncology.' }), { warmup_week: 3 }).email_flags,
    'unapproved-link'),
  true
);
t.check(
  'a LinkedIn URL is flagged in any week',
  has(assemble(generation({ email_hook: 'You post at https://www.linkedin.com/in/x about oncology.' }),
    { warmup_week: 5 }).email_flags, 'linkedin-link'),
  true
);
t.check(
  'a PDF is flagged in any week',
  has(assemble(generation({ email_hook: 'Your brochure https://x.example/deck.pdf lists oncology.' }),
    { warmup_week: 5 }).email_flags, 'pdf-link'),
  true
);
t.check(
  'a URL in the subject counts as a link too',
  has(assemble(generation({ email_subject: 'Your oncology work — www.example.com' })).email_flags, 'link-in-warmup'),
  true
);

// --- Subject: skill section 3 ------------------------------------------------

function subjectFlags(subject, over) {
  return assemble(generation({ email_subject: subject }), over).email_flags
    .filter(function (f) { return /^subject/.test(f); });
}

t.check('a 30-character subject passes', subjectFlags('Your oncology work — a question'), []);
t.check('29 characters is too short', subjectFlags('Your oncology work — question'), ['subject']);
t.check('50 characters passes', subjectFlags('Your oncology and respiratory work — one question.'), []);
t.check('51 characters is too long', subjectFlags('Your oncology and respiratory work — one question..'), ['subject']);
t.check('the product name is never in a subject', subjectFlags('Nova for your oncology work — one question'), ['subject-product']);
t.check('a fake reply prefix is flagged', subjectFlags('Re: your oncology work — one question'), ['subject-reply']);
t.check('so is a fake forward', subjectFlags('Fwd: your oncology work — one question'), ['subject-reply']);
['free', 'demo', 'offer', 'opportunity'].forEach(function (w) {
  t.check('"' + w + '" is banned in a subject', subjectFlags('An oncology ' + w + ' for your team today'), ['subject-word']);
});
t.check('capital-letter shouting is flagged', subjectFlags('QUICK question on your oncology work'), ['subject-caps']);
t.check(
  'a trial code from the facts is not shouting',
  subjectFlags('INM004 trial — a quick question', {
    trial_titles: ['Efficacy of INM004 in Children With STEC-HUS'],
    fact_sheet: '1. ClinicalTrials.gov lists 1 recruiting trial with you as the sponsor. The only one named is: "Efficacy of INM004 in Children With STEC-HUS".',
  }),
  []
);

// The worked examples in the system prompt carry facts -- INM004, 40,
// oncology -- that belong to other companies. A 9B model copies examples. Each
// copied onto the wrong lead must be caught.
t.check(
  'few-shot leak: INM004 on a lead without that trial is ungrounded',
  subjectFlags('INM004 trial — a quick question'),
  ['subject-ungrounded']
);
t.check(
  'few-shot leak: "40-person" on a lead with no headcount is ungrounded',
  subjectFlags('A question for a 40-person team'),
  ['subject-ungrounded']
);
t.check(
  'a real confirmed headcount of 40 grounds the same subject',
  subjectFlags('A question for a 40-person team', { headcount: 40 }),
  []
);
t.check(
  'few-shot leak: oncology on a dermatology lead is ungrounded',
  subjectFlags('Your oncology work — one question', { areas: ['Dermatology'], absent_areas: absentGiven(['Dermatology']) }),
  ['subject-ungrounded']
);
// The "only geography known" example is what a `problem`-source subject is steered
// to, so a model that copies it verbatim puts it in a real lead's subject line.
// It carries no fact, so it is not ungrounded, and at 33 characters it is inside
// the skill's 30-50. Copied verbatim it has to pass every subject check. (It used
// to read "Sponsor leads after hours" -- 25 characters, tagged `subject`, and the
// wrong word for the pharma-side party; reworded 2026-09-21.)
t.check(
  'the geography-only worked example passes every subject check verbatim',
  subjectFlags('Pharma-team inquiries after hours'),
  []
);

// --- Hook: skill section 3 ---------------------------------------------------

function hookFlags(hook, over) {
  return assemble(generation({ email_hook: hook }), over).email_flags
    .filter(function (f) { return /^hook/.test(f); });
}

t.check('a clean hook raises nothing', hookFlags('Your site lists oncology and respiratory work.'), []);
t.check('a question in the hook is flagged -- the question is the problem line', hookFlags('Do you work in oncology?'), ['hook-question']);
t.check(
  'a question mark inside a quoted trial title is not the model asking',
  hookFlags("You're sponsoring a recruiting trial — Does X Help?", {
    trial_titles: ['Does X Help?'],
    fact_sheet: '1. ClinicalTrials.gov lists 1 recruiting trial with you as the sponsor. The only one named is: "Does X Help?".',
  }),
  []
);
t.check('an ask in the hook is flagged -- exactly one ask, and it is the library\'s', hookFlags('Your site lists oncology work, so reply if interested.'), ['hook-ask']);
t.check('the product in the hook is flagged -- the outcome line carries it', hookFlags('Your oncology work could use Nova.'), ['hook-pitch']);
t.check('a number the facts do not hold is flagged', hookFlags('Your site lists oncology work across 12 sites.'), ['hook-ungrounded']);
t.check('an area the lead does not have is flagged', hookFlags('Your site lists dermatology work.'), ['hook-ungrounded']);
t.check('opening on the founder\'s name is flagged', hookFlags('Anna Nowak leads your oncology work.'), ['hook-opener']);
t.check('opening on the city is flagged', hookFlags('Budapest is home to your oncology work.'), ['hook-opener']);
t.check('three sentences is not a hook', hookFlags('You work in oncology. You work in respiratory. You are based somewhere.'), ['hook-long']);
const TWO_TRIALS = {
  trial_titles: ['Hyperbaric Oxygen Brain Injury Treatment Trial', 'Registry of Minimally Invasive Cancer Treatment'],
  fact_sheet: '1. ClinicalTrials.gov lists 2 recruiting trials with you as the sponsor. The ones named are: "Hyperbaric Oxygen Brain Injury Treatment Trial"; "Registry of Minimally Invasive Cancer Treatment".',
};
t.check(
  'a hook naming two trial titles is two facts in one sentence',
  hookFlags("You're sponsoring two recruiting trials — Hyperbaric Oxygen Brain Injury Treatment Trial; Registry of Minimally Invasive Cancer Treatment.", TWO_TRIALS),
  ['hook-long']
);
t.check(
  'naming one of them is the hook the skill asks for',
  hookFlags("You're sponsoring two recruiting trials, including Hyperbaric Oxygen Brain Injury Treatment Trial.", TWO_TRIALS),
  []
);
t.check(
  'a trial credited to their site is flagged -- it is on ClinicalTrials.gov (measured on KLIXAR)',
  hookFlags('Your site lists work on the Efficacy of INM004 in Children With STEC-HUS.', {
    trial_titles: ['Efficacy of INM004 in Children With STEC-HUS'],
    fact_sheet: '1. ClinicalTrials.gov lists 1 recruiting trial with you as the sponsor. The only one named is: "Efficacy of INM004 in Children With STEC-HUS".',
  }),
  ['hook-source']
);
t.check(
  'so is a site credited with trials in general',
  hookFlags('Your site lists two recruiting trials.'),
  ['hook-source']
);
t.check(
  'while "your site lists" for therapeutic areas is exactly right',
  hookFlags('Your site lists oncology work.'),
  []
);
t.check(
  'a hook about the source rather than about them is flagged',
  hookFlags('ClinicalTrials.gov lists two recruiting trials with you as the sponsor, including Hyperbaric Oxygen Brain Injury Treatment Trial.', TWO_TRIALS),
  ['hook-source']
);
t.check(
  'a hook broken over lines is joined into one',
  draftOf(assemble(generation({ email_hook: 'Your site lists\noncology work.' })), 'email').body.indexOf('Your site lists oncology work.') !== -1,
  true
);
t.check(
  'the LinkedIn hook is held to the same rules',
  has(assemble(generation({ linkedin_hook: 'Want a demo of Nova for your oncology work?' })).linkedin_flags, 'hook-question'),
  true
);

// --- Human confirmation: skill section 4, "human confirms before first use" --

t.check(
  'a draft built from an unconfirmed library line is tagged unconfirmed-claim',
  has(assemble(generation(), { library_pools: Object.assign({}, LIBRARY, { proof: [line('PR-TR', PR_TR.body, false)] }) }).email_flags,
    'unconfirmed-claim'),
  true
);
t.check('all lines confirmed: no tag', has(CLEAN.email_flags, 'unconfirmed-claim'), false);

// --- Workflow 6 must still accept the result ---------------------------------

t.check(
  'the email still ends opt-out, blank line, signature -- the tail the send path checks',
  EMAIL.indexOf(OPT_OUT + '\n\n' + SIGNATURE) !== -1,
  true
);
t.check('a warm-up draft carries no URL at all', (EMAIL.match(/https?:\/\/|www\./g) || []).length, 0);

t.done();

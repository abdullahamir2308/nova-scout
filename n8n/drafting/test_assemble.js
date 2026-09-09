// Unit tests for code_assemble.js -- constraint enforcement and draft assembly.
//
// The point of this node is that a model is never trusted to reproduce a rule.
// Every Section 5 / Section 9 message rule that a string match can enforce is
// enforced here, and the tests below are the evidence that each one actually
// fires -- including the compliance-critical one: the opt-out sentence is
// appended verbatim, never generated.

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

function assessed(over) {
  return Object.assign(
    {
      lead_id: 1,
      domain: 'example.com',
      company_name: 'Example CRO',
      fit_score: 65,
      fact_count: 3,
      fact_kinds: ['therapeutic_area', 'city', 'founder_name'],
      email_addressing: 'role-inbox',
      linkedin_addressing: 'named',
      email_greeting: 'Hello,',
      linkedin_greeting: 'Hi Anna,',
    },
    over || {}
  );
}

// A clean generation: two grounded facts, the demo named, short, no banned
// words, no links, no merge tags.
function generation(over) {
  return Object.assign(
    {
      email_subject: 'oncology trials and your site',
      email_body:
        'You run oncology trials out of Budapest, and your team fields the same ' +
        'protocol questions every week. We built an assistant for NoblePath that ' +
        'answers them from their own documents. Want the 90-second recording?',
      linkedin_body:
        'Saw the oncology work out of Budapest. We built NoblePath an assistant ' +
        'that answers protocol questions from their own documents. Worth a look?',
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
t.check('so variant is just the addressing mode', draftOf(CLEAN, 'email').variant, 'role-inbox');
t.check('for both channels', draftOf(CLEAN, 'linkedin').variant, 'named');
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
  generation().email_body.indexOf(OPT_OUT),
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
    email_body: 'Hello,\n\nYou run oncology trials in Budapest. NoblePath uses ours.\n\nBest regards,\nFatima',
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
  draftOf(assemble(generation({ email_body: 'Hi Enrique,\n\nYou run oncology trials. NoblePath uses ours.' })), 'email')
    .body.indexOf('Enrique'),
  -1
);

// ---------------------------------------------------------------------------
// Section 9 / Section 5 constraint checks
// ---------------------------------------------------------------------------

const LONG = assemble(
  generation({
    email_body: ('word ').repeat(90) + ' NoblePath',
  })
);
t.check('an over-length email is flagged long', LONG.email_flags.indexOf('long') !== -1, true);
t.check('and the flag lands in variant', draftOf(LONG, 'email').variant, 'role-inbox+long');
t.check(
  'the draft is still written -- a flagged draft is fixable, a dropped one is not',
  LONG.write,
  true
);

t.check(
  'a banned adjective the doc names is caught',
  assemble(generation({ email_body: 'Our revolutionary assistant. NoblePath uses it.' })).email_flags
    .indexOf('adjective') !== -1,
  true
);
t.check(
  'the other one the doc names is caught too',
  assemble(generation({ email_body: 'Cutting-edge assistant. NoblePath uses it.' })).email_flags
    .indexOf('adjective') !== -1,
  true
);
t.check(
  'a merge-tag tell in the body is caught',
  assemble(generation({ email_body: 'We work with [Company Name]. NoblePath uses ours.' }))
    .email_flags.indexOf('merge-tag') !== -1,
  true
);
// A merge tag inside a greeting the model added anyway is REMOVED, not flagged.
// The check asks "does the draft the reviewer sees contain a tell?", and after
// the stripper it does not -- flagging text that is no longer in the draft
// would send the reviewer hunting for something that is not there.
t.check(
  'a merge tag inside a model-added greeting is stripped rather than flagged',
  draftOf(assemble(generation({ email_body: 'Hi [First Name],\n\nNoblePath uses ours.' })), 'email')
    .body.indexOf('[First Name]'),
  -1
);
t.check(
  'and that draft is therefore clean, not tagged for a tell it no longer has',
  assemble(generation({ email_body: 'Hi [First Name],\n\nNoblePath uses ours.' })).email_flags
    .indexOf('merge-tag'),
  -1
);
// The stripper must not eat real content that merely begins with "Hi"/"Hello".
t.check(
  'a body starting with an unpunctuated "Hi" word keeps its content',
  draftOf(
    assemble(generation({ email_body: 'Hi-tech vendors keep pitching you. NoblePath uses ours.' })),
    'email'
  ).body.indexOf('vendors keep pitching you') !== -1,
  true
);
t.check(
  'a handlebars tell is caught',
  assemble(generation({ email_body: 'We work with {{company}}. NoblePath uses ours.' })).email_flags
    .indexOf('merge-tag') !== -1,
  true
);
t.check(
  'more than one URL breaks Section 5',
  assemble(
    generation({
      email_body: 'See https://a.example and https://b.example. NoblePath uses ours.',
    })
  ).email_flags.indexOf('urls') !== -1,
  true
);
t.check(
  'exactly one URL is allowed',
  assemble(generation({ email_body: 'See https://a.example. NoblePath uses ours.' })).email_flags
    .indexOf('urls'),
  -1
);
t.check(
  'an email that never mentions the demo is flagged',
  assemble(generation({ email_body: 'You run oncology trials in Budapest. Worth a chat?' }))
    .email_flags.indexOf('no-demo') !== -1,
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
  assemble(generation({ email_body: '' })).email_flags.indexOf('empty') !== -1,
  true
);
t.check(
  'multiple violations all land in variant',
  draftOf(assemble(generation({ email_body: 'Our revolutionary tool for [Company].' })), 'email')
    .variant,
  'role-inbox+adjective,merge-tag,no-demo'
);

// The LinkedIn channel is checked independently -- a violation in one must not
// tag the other.
const ONE_BAD = assemble(
  generation({ linkedin_body: 'Our cutting-edge platform is here.' })
);
t.check('a LinkedIn violation is flagged', ONE_BAD.linkedin_flags.indexOf('adjective') !== -1, true);
t.check('and does not contaminate the email', ONE_BAD.email_flags, []);
t.check(
  'the LinkedIn DM is NOT flagged no-demo -- that check is email-only',
  ONE_BAD.linkedin_flags.indexOf('no-demo'),
  -1
);

// ---------------------------------------------------------------------------
// The word count counts what the recipient reads
// ---------------------------------------------------------------------------

t.check(
  'the counted email includes the opt-out sentence, which the recipient reads',
  assemble(generation()).email_words >
    generation().email_body.split(/\s+/).length,
  true
);
t.check(
  'a LinkedIn count excludes the opt-out, which is not sent there',
  assemble(generation()).linkedin_words <
    generation().linkedin_body.split(/\s+/).length + 5,
  true
);

t.done();

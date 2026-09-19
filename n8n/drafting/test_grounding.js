// Unit tests for code_assess.js -- the grounding guard.
//
// This is the node whose failure mode is a fabricated sentence in a real email,
// so the cases below are weighted toward the two ways it can be wrong in a way
// nobody notices: counting a fact that is not one (letting a lead through with
// nothing real to say), and refusing to count one that is (silently killing
// good leads). The addressing rules get the same treatment, because Section 9's
// "a role inbox is not a person" is a rule about what you may CLAIM, and the
// only evidence it is holding is a test that says so.

const path = require('path');
const { runForEachItem, runner } = require('./harness');

const ASSESS = path.join(__dirname, 'code_assess.js');

// build_workflow.py bakes the system prompt into this node at build time. The
// tests run the same substitution so the file parses and so `system_prompt` is
// exercised as a real emitted field -- it silently resolved to nothing in the
// first live run, which is the whole reason it is emitted here rather than set
// on the Config node.
const SYSTEM_PROMPT = 'SYSTEM PROMPT SENTINEL';
const withSystemPrompt = function (src) {
  return src.replace('__SYSTEM_PROMPT__', JSON.stringify(SYSTEM_PROMPT));
};

// A lead row shaped like the Get Draft Batch query returns it.
function lead(over) {
  return Object.assign(
    {
      lead_id: 1,
      domain: 'example.com',
      company_name: 'Example CRO',
      country: 'Poland',
      fit_score: 65,
      contact_name: null,
      contact_title: null,
      email: null,
      linkedin_url: null,
      verified: true,
      therapeutic_areas: [],
      phases: [],
      founder_name: null,
      founder_linkedin: null,
      employee_estimate: null,
      site_quality_notes: 'A professional site with clear service listings.',
      city: null,
      founder_title: null,
    },
    over || {}
  );
}

// A ClinicalTrials.gov response shaped like the HTTP node returns it.
function ctgov(total, titles) {
  return {
    totalCount: total,
    studies: (titles || []).map(function (t, i) {
      return {
        protocolSection: {
          identificationModule: { nctId: 'NCT0000000' + i, briefTitle: t },
        },
      };
    }),
  };
}

function assess(leadRow, ctResp) {
  return runForEachItem(
    ASSESS,
    [{ json: ctResp }],
    { 'Get Draft Batch': [{ json: leadRow }] },
    withSystemPrompt
  )[0].json;
}

const t = runner('code_assess.js -- grounding guard');

// ---------------------------------------------------------------------------
// The lookup has to have answered at all
// ---------------------------------------------------------------------------

t.check(
  'a ClinicalTrials.gov error is not a fact-count of zero',
  assess(lead(), { error: 'ETIMEDOUT' }).ok,
  false
);
t.check(
  'a response with no numeric totalCount is dropped, not drafted',
  assess(lead(), { studies: [] }).ok,
  false
);
t.check(
  'the drop reason names the failure rather than [object Object]',
  assess(lead(), { error: { code: 503 } }).skip_reason.indexOf('503') !== -1,
  true
);
t.check(
  'a dropped lead carries no groundable verdict at all',
  assess(lead(), { error: 'ETIMEDOUT' }).groundable,
  undefined
);

// ---------------------------------------------------------------------------
// The threshold
// ---------------------------------------------------------------------------

t.check('zero facts is not groundable', assess(lead(), ctgov(0, [])).groundable, false);
t.check(
  'one fact is not groundable -- the doc says at least two',
  assess(lead({ city: 'Warsaw' }), ctgov(0, [])).fact_count,
  1
);
t.check(
  'one fact is not groundable (verdict)',
  assess(lead({ city: 'Warsaw' }), ctgov(0, [])).groundable,
  false
);
t.check(
  'two facts is groundable',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak' }), ctgov(0, [])).groundable,
  true
);
t.check(
  'the missing list is the doc vocabulary minus what was found',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak' }), ctgov(0, [])).missing_facts,
  ['therapeutic_area', 'named_trial']
);

// ---------------------------------------------------------------------------
// Therapeutic areas: the live store is a mixed bag and most of it is not a fact
// ---------------------------------------------------------------------------

t.check(
  'an on-enum area counts, whatever its case (pre-enum rows are lowercase)',
  assess(lead({ therapeutic_areas: ['oncology'] }), ctgov(0, [])).areas,
  ['Oncology']
);
t.check(
  'Title Case post-enum rows count identically',
  assess(lead({ therapeutic_areas: ['Oncology'] }), ctgov(0, [])).areas,
  ['Oncology']
);
t.check(
  'industry vocabulary from pre-enum free text is NOT a therapeutic area',
  assess(
    lead({ therapeutic_areas: ['pharma', 'medtech', 'fmcg', 'nutraceuticals', 'cosmetics'] }),
    ctgov(0, [])
  ).areas,
  []
);
t.check(
  'a lead whose only areas are off-enum junk has zero facts from them',
  assess(lead({ therapeutic_areas: ['pharma', 'medtech', 'fmcg'] }), ctgov(0, [])).fact_count,
  0
);
t.check(
  "'Other' is on the enum but is not a specific fact",
  assess(lead({ therapeutic_areas: ['Other'] }), ctgov(0, [])).areas,
  []
);
t.check(
  'junk is dropped while a real area alongside it is kept',
  assess(lead({ therapeutic_areas: ['fmcg', 'oncology', 'Other', 'cardiology'] }), ctgov(0, []))
    .areas,
  ['Oncology']
);
t.check(
  'duplicates across cases collapse to one',
  assess(lead({ therapeutic_areas: ['oncology', 'Oncology', 'ONCOLOGY'] }), ctgov(0, [])).areas,
  ['Oncology']
);
t.check(
  'a null inside the array does not throw',
  assess(lead({ therapeutic_areas: [null, 'oncology'] }), ctgov(0, [])).areas,
  ['Oncology']
);
t.check(
  'a non-array therapeutic_areas is treated as empty, not crashed on',
  assess(lead({ therapeutic_areas: null }), ctgov(0, [])).areas,
  []
);

// ---------------------------------------------------------------------------
// Trials: a count is not a named trial
// ---------------------------------------------------------------------------

t.check(
  'a trial with a title counts as the named_trial fact',
  assess(lead(), ctgov(1, ['A Study of X in Advanced Solid Tumors'])).fact_kinds,
  ['named_trial']
);
t.check(
  'totalCount > 0 with no titles returned is NOT a named trial',
  assess(lead(), ctgov(4, [])).fact_kinds,
  []
);
t.check(
  'zero trials is not a fact even if studies came back empty-handed',
  assess(lead(), ctgov(0, [])).fact_kinds,
  []
);
t.check(
  'at most three titles are carried into the fact sheet',
  assess(lead(), ctgov(9, ['T1', 'T2', 'T3', 'T4', 'T5'])).trial_titles.length,
  3
);
t.check(
  'the real total is preserved even when only three titles are kept',
  assess(lead(), ctgov(9, ['T1', 'T2', 'T3', 'T4', 'T5'])).trial_count,
  9
);

// ---------------------------------------------------------------------------
// Facts the guard deliberately does NOT count
// ---------------------------------------------------------------------------

t.check(
  'country is not a grounding fact -- every lead has one',
  assess(lead({ country: 'Poland' }), ctgov(0, [])).fact_count,
  0
);
t.check(
  'site_quality_notes is not a grounding fact -- it is model-generated prose',
  assess(
    lead({ site_quality_notes: 'Detailed services, evidence of significant budget.' }),
    ctgov(0, [])
  ).fact_count,
  0
);
t.check(
  'phases are not in the doc vocabulary and do not count',
  assess(lead({ phases: ['Phase I', 'Phase II', 'Phase III'] }), ctgov(0, [])).fact_count,
  0
);
t.check(
  'employee_estimate is not in the doc vocabulary and does not count',
  assess(lead({ employee_estimate: 40 }), ctgov(0, [])).fact_count,
  0
);

// ---------------------------------------------------------------------------
// Addressing -- Section 9 Workflow 3b: "a role inbox is not a person"
// ---------------------------------------------------------------------------

const KLIXAR = lead({
  company_name: 'KLIXAR',
  email: 'hello@klixar.com',
  contact_name: 'Enrique Gaubeca',
  linkedin_url: 'https://www.linkedin.com/in/enriquegaubeca',
  founder_name: 'Enrique Gaubeca',
  city: 'Buenos Aires',
});

t.check(
  'a role inbox is a role inbox even when the founder IS named and confirmed',
  assess(KLIXAR, ctgov(0, [])).email_addressing,
  'role-inbox'
);
t.check(
  'so the email greeting stays generic',
  assess(KLIXAR, ctgov(0, [])).email_greeting,
  'Hello,'
);
t.check(
  'while LinkedIn -- where the URL really is his -- greets him by name',
  assess(KLIXAR, ctgov(0, [])).linkedin_greeting,
  'Hi Enrique,'
);
t.check(
  'and LinkedIn addressing is named',
  assess(KLIXAR, ctgov(0, [])).linkedin_addressing,
  'named'
);

['info@x.com', 'contact@x.com', 'connect@x.com', 'office@x.com', 'no-reply@x.com'].forEach(
  function (e) {
    t.check(
      e + ' is a role inbox',
      assess(lead({ email: e, contact_name: 'Real Person' }), ctgov(0, [])).email_addressing,
      'role-inbox'
    );
  }
);

t.check(
  'a personal-shaped address with no name on record is unnamed, not named',
  assess(
    lead({ email: 'Devesh.kumar@innovate-research.com', contact_name: null }),
    ctgov(0, [])
  ).email_addressing,
  'unnamed'
);
t.check(
  'an unnamed address still gets a generic greeting, never an invented one',
  assess(
    lead({ email: 'Devesh.kumar@innovate-research.com', contact_name: null }),
    ctgov(0, [])
  ).email_greeting,
  'Hello,'
);
t.check(
  'a personal address that matches the name on record IS addressable',
  assess(
    lead({ email: 'anna.nowak@x.com', contact_name: 'Anna Nowak' }),
    ctgov(0, [])
  ).email_addressing,
  'named'
);
t.check(
  'and greets by first name',
  assess(lead({ email: 'anna.nowak@x.com', contact_name: 'Anna Nowak' }), ctgov(0, []))
    .email_greeting,
  'Hi Anna,'
);
t.check(
  'diacritics in the name still match the ASCII local-part',
  assess(
    lead({ email: 'janos.biro@bitrial.hu', contact_name: 'János Bíró' }),
    ctgov(0, [])
  ).email_addressing,
  'named'
);
t.check(
  'a personal address belonging to somebody ELSE is not addressable by our name',
  assess(
    lead({ email: 'peter.schmidt@x.com', contact_name: 'Anna Nowak' }),
    ctgov(0, [])
  ).email_addressing,
  'unnamed'
);
t.check(
  'no email at all is unaddressed',
  assess(lead({ email: null, contact_name: 'Anna Nowak' }), ctgov(0, [])).email_addressing,
  'unaddressed'
);
t.check(
  'a LinkedIn URL with no name on record cannot be greeted',
  assess(lead({ linkedin_url: 'https://linkedin.com/in/x', contact_name: null }), ctgov(0, []))
    .linkedin_addressing,
  'no-profile'
);
t.check(
  'a name with no LinkedIn URL has no profile to send to',
  assess(lead({ contact_name: 'Anna Nowak', linkedin_url: null }), ctgov(0, []))
    .linkedin_addressing,
  'no-profile'
);

// ---------------------------------------------------------------------------
// The fact sheet handed to the model
// ---------------------------------------------------------------------------

const RICH = assess(
  lead({
    company_name: 'BiTrial Clinical Research',
    therapeutic_areas: ['oncology', 'respiratory', 'fmcg'],
    city: 'Budapest',
    founder_name: 'Janos Biro',
    founder_title: 'CEO, Founder',
  }),
  ctgov(2, ['A Study of Something Real'])
);

t.check('all four categories can fire at once', RICH.fact_count, 4);
t.check('and nothing is reported missing', RICH.missing_facts, []);
t.check(
  'the fact sheet carries only canonical areas, never the junk',
  RICH.fact_sheet.indexOf('fmcg') === -1 &&
    RICH.fact_sheet.indexOf('Oncology and Respiratory') !== -1,
  true
);

// --- the two-area cap is applied to the DATA, not asked for in the prompt ---
//
// "Name at most two therapeutic areas" was in the instructions and the model
// listed five anyway. A model cannot list a fifth area it was never given.
const MANY = assess(
  lead({
    therapeutic_areas: ['cardiovascular', 'rare diseases', 'oncology', 'respiratory',
                        'infectious disease'],
    city: 'Istanbul',
  }),
  ctgov(0, [])
);
t.check(
  'at most two areas reach the prompt however many were extracted',
  (MANY.fact_sheet.match(/Oncology|Cardiovascular|Rare Diseases|Respiratory|Infectious Disease/g) || [])
    .length,
  2
);
t.check(
  'Oncology is one of them when present -- Section 12 makes it the strongest signal',
  MANY.fact_sheet.indexOf('Oncology and Cardiovascular') !== -1,
  true
);
t.check(
  'the areas fact still counts as exactly one fact, not two',
  MANY.fact_count,
  2
);
t.check(
  'and the full canonical list is still reported for the review queue',
  MANY.areas.length,
  5
);
t.check(
  'the cap is stable across redrafts, not dependent on extraction order',
  (function () {
    const shuffled = assess(
      lead({
        therapeutic_areas: ['respiratory', 'infectious disease', 'cardiovascular',
                            'oncology', 'rare diseases'],
        city: 'Istanbul',
      }),
      ctgov(0, [])
    );
    return shuffled.fact_sheet === MANY.fact_sheet;
  })(),
  true
);
t.check(
  'the founder line carries the title when there is one',
  RICH.fact_sheet.indexOf('Janos Biro (CEO, Founder)') !== -1,
  true
);
t.check(
  'the prompt tells the model these are the only facts that exist',
  RICH.prompt.indexOf('the only facts about this company that exist') !== -1,
  true
);
t.check(
  'site_quality_notes never reaches the prompt',
  RICH.prompt.toLowerCase().indexOf('professional') === -1,
  true
);

// --- every fact carries its own boundary -----------------------------------
//
// This is the anti-JOIN design, and it is the part measurement actually moved:
// a sheet of bare attributes reliably produced sentences fusing two facts into
// a claim neither makes. If a boundary sentence ever goes missing, that failure
// comes back silently -- so each one is asserted.
t.check(
  'the city fact says what it does not establish',
  RICH.fact_sheet.indexOf('says nothing about where any trial runs') !== -1,
  true
);
t.check(
  'the therapeutic-area fact says what it does not establish',
  RICH.fact_sheet.indexOf('nothing about which area any particular trial belongs to') !== -1,
  true
);
t.check(
  'the founder fact says what it does not establish',
  RICH.fact_sheet.indexOf('nothing about which trials or clients this person personally handles') !== -1,
  true
);
t.check(
  'the trial fact says what it does not establish',
  RICH.fact_sheet.indexOf('not their phase, their therapeutic area, nor where they run') !== -1,
  true
);
t.check(
  'facts are numbered so the prompt can name one to open from',
  RICH.fact_sheet.indexOf('1. ') === 0,
  true
);
t.check(
  'a single named trial is phrased in the singular',
  assess(lead(), ctgov(1, ['Only One'])).fact_sheet.indexOf('The only one named is') !== -1,
  true
);
t.check(
  'a total larger than the titles returned does not claim the singular',
  assess(lead(), ctgov(9, ['A', 'B'])).fact_sheet.indexOf('The ones named are') !== -1,
  true
);
t.check(
  'and still reports the true total',
  assess(lead(), ctgov(9, ['A', 'B'])).fact_sheet.indexOf('lists 9 recruiting trials with you') !== -1,
  true
);

// --- which fact opens which channel ----------------------------------------

t.check(
  'founder_name never opens a message -- you cannot tell someone their own name',
  assess(
    lead({ founder_name: 'Anna Nowak', city: 'Warsaw', therapeutic_areas: ['oncology'] }),
    ctgov(0, [])
  ).open_email_fact,
  'therapeutic_area'
);
t.check(
  'city never opens either -- measured, the model upgrades it into a trials claim',
  assess(
    lead({ city: 'Warsaw', therapeutic_areas: ['oncology'] }),
    ctgov(1, ['A Study'])
  ).open_email_fact !== 'city',
  true
);
t.check(
  'the two channels open from different facts when there is a choice',
  (function () {
    const o = assess(lead({ therapeutic_areas: ['oncology'], city: 'Warsaw' }), ctgov(1, ['A Study']));
    return o.open_email_fact !== o.open_linkedin_fact;
  })(),
  true
);
t.check(
  'with only one openable fact both channels use it rather than failing',
  (function () {
    const o = assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak' }), ctgov(1, ['A Study']));
    return o.open_email_fact === 'named_trial' && o.open_linkedin_fact === 'named_trial';
  })(),
  true
);
t.check(
  'with nothing openable at all it falls back rather than dropping the lead',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak' }), ctgov(0, [])).open_email_fact,
  'city'
);
t.check(
  'the opener is deterministic -- the same lead redrafted opens the same way',
  (function () {
    const l = lead({ lead_id: 77, therapeutic_areas: ['oncology'], city: 'Warsaw' });
    return assess(l, ctgov(1, ['A Study'])).open_email_fact ===
      assess(l, ctgov(1, ['A Study'])).open_email_fact;
  })(),
  true
);
t.check(
  'but two different leads with the same fact shape do not both open the same way',
  (function () {
    const shape = { therapeutic_areas: ['oncology'], city: 'Warsaw' };
    const a = assess(lead(Object.assign({ lead_id: 10 }, shape)), ctgov(1, ['A Study']));
    const b = assess(lead(Object.assign({ lead_id: 11 }, shape)), ctgov(1, ['A Study']));
    return a.open_email_fact !== b.open_email_fact;
  })(),
  true
);
t.check(
  'the prompt names the opening fact by its number',
  /Open the email from fact \d+\. Open the LinkedIn DM from fact \d+\./.test(RICH.prompt),
  true
);
t.check(
  'and tells the model to write no greeting',
  RICH.prompt.indexOf('Write no greeting and no sign-off') !== -1,
  true
);
// The regression test for the silent failure: this node, not the Config node,
// is what carries the system prompt to Ollama.
t.check(
  'the system prompt is emitted on every item, not left to the Config node',
  RICH.system_prompt,
  SYSTEM_PROMPT
);
t.check(
  'including on a lead that will take the low-context branch',
  assess(lead({ city: 'Warsaw' }), ctgov(0, [])).system_prompt,
  SYSTEM_PROMPT
);

// ===========================================================================
// Drafting skill v2 -- everything above is the v1 suite, unchanged
// ===========================================================================

// The active claims-library rows as Get Draft Batch aggregates them (migration
// 010's seed shape): proof lines carry the countries they serve, and the one
// with no countries serves everywhere else.
const TR = ['Turkey', 'Egypt', 'UAE', 'Romania', 'Hungary', 'Poland', 'Czech Republic'];
const LATAM = ['Mexico', 'Brazil', 'Argentina'];
function row(code, slot, body, extra) {
  return Object.assign({ code: code, slot: slot, body: body, countries: null, measured: false, confirmed: false }, extra || {});
}
const LIB = [
  row('A1', 'ask', 'Worth a 48-hour demo built on your own material? Reply yes and I\'ll set it up.'),
  row('O1', 'outcome', 'We built Nova to answer from your own material, qualify the lead, and route it wherever you already work.'),
  row('P1', 'problem', 'When a sponsor shortlists you at 11pm, does that lead reach you by morning?'),
  row('PR-BOTH', 'proof', 'It\'s live at two CROs, in Türkiye and Mexico.'),
  row('PR-MX', 'proof', 'It\'s live at Vertex Clinical Research in Mexico.', { countries: LATAM }),
  row('PR-TR', 'proof', 'It\'s live at NoblePath, an oncology CRO in Türkiye.', { countries: TR }),
];
const GROUNDED = { city: 'Warsaw', therapeutic_areas: ['oncology'] };

function withLib(over, library) {
  return lead(Object.assign({ library: library === undefined ? LIB : library }, GROUNDED, over || {}));
}
function proofOf(country, library) {
  return assess(withLib({ country: country }, library), ctgov(0, [])).library_pools.proof
    .map(function (l) { return l.code; });
}

// --- the guard itself is not touched ----------------------------------------

t.check(
  'the library never changes the grounding verdict -- two facts is still groundable without one',
  assess(lead(GROUNDED), ctgov(0, [])).groundable,
  true
);
t.check(
  'and a library never makes a one-fact lead groundable',
  assess(lead({ city: 'Warsaw', library: LIB }), ctgov(0, [])).groundable,
  false
);
t.check(
  'a small confirmed headcount is not a grounding fact either',
  assess(lead({ city: 'Warsaw', employee_estimate: 40 }), ctgov(0, [])).fact_count,
  1
);

// --- proof is geography-matched: skill section 3, Master Ref section 12 ------

t.check('Mexico gets Vertex', proofOf('Mexico'), ['PR-MX']);
t.check('so do the other Latin American geographies', [proofOf('Brazil'), proofOf('Argentina')], [['PR-MX'], ['PR-MX']]);
t.check('Turkey gets NoblePath', proofOf('Turkey'), ['PR-TR']);
t.check('a country the operator listed as nearby gets NoblePath', proofOf('Poland'), ['PR-TR']);
t.check('a country no line names gets the line with no countries -- "either elsewhere"', proofOf('India'), ['PR-BOTH']);
t.check('country matching ignores case', proofOf('mexico'), ['PR-MX']);
t.check(
  'an active measured line replaces the plain line for its countries',
  proofOf('Mexico', LIB.concat([row('PR-MX-N', 'proof', 'Nova handled 140 sponsor conversations at Vertex in 60 days.',
    { countries: LATAM, measured: true })])),
  ['PR-MX-N']
);
t.check(
  'but a measured line for other countries does not reach this one',
  proofOf('Turkey', LIB.concat([row('PR-MX-N', 'proof', 'Nova handled 140 sponsor conversations at Vertex in 60 days.',
    { countries: LATAM, measured: true })])),
  ['PR-TR']
);
t.check(
  'a row with no text is not a line, whatever its flags say',
  proofOf('Mexico', LIB.concat([row('PR-MX-N', 'proof', null, { countries: LATAM, measured: true })])),
  ['PR-MX']
);

// --- the other slots --------------------------------------------------------

const POOLS = assess(withLib({ country: 'Poland' }), ctgov(0, [])).library_pools;
t.check('one problem, outcome and ask pool each, in code order', [POOLS.problem.length, POOLS.outcome.length, POOLS.ask.length], [1, 1, 1]);
t.check('each line carries its text verbatim', POOLS.problem[0].body, LIB[2].body);
t.check('and whether a human has confirmed it', POOLS.problem[0].confirmed, false);
t.check(
  'a link line resolves by country the same way',
  assess(withLib({ country: 'Poland' }, LIB.concat([row('L-NP', 'link', 'https://noblepath.example', { countries: TR })])), ctgov(0, []))
    .library_link.map(function (l) { return l.code; }),
  ['L-NP']
);
t.check(
  'and serves nobody it does not name',
  assess(withLib({ country: 'Mexico' }, LIB.concat([row('L-NP', 'link', 'https://noblepath.example', { countries: TR })])), ctgov(0, []))
    .library_link,
  []
);

// --- a library gap holds the lead back instead of drafting around the hole ---

const NO_ASK = assess(withLib({}, LIB.filter(function (r) { return r.slot !== 'ask'; })), ctgov(0, []));
t.check('a groundable lead with no ask line is held back, not drafted', NO_ASK.ok, false);
t.check('and says which slot is empty', NO_ASK.skip_reason.indexOf('no active ask line') !== -1, true);
const NO_PROOF = assess(withLib({ country: 'India' }, LIB.filter(function (r) { return r.code !== 'PR-BOTH'; })), ctgov(0, []));
t.check('a country with no proof line and no fallback is held back', NO_PROOF.ok, false);
t.check('and names the country', NO_PROOF.skip_reason.indexOf('none serves India') !== -1, true);
t.check(
  'an empty library holds back a groundable lead',
  assess(withLib({}, []), ctgov(0, [])).ok,
  false
);
t.check(
  'but not a low-context one -- its note needs no library',
  assess(lead({ city: 'Warsaw', library: [] }), ctgov(0, [])).ok,
  true
);
t.check('a complete library lets the lead through', assess(withLib(), ctgov(0, [])).ok, true);

// --- what the subject line is built from -------------------------------------

t.check(
  'the subject is built from the fact the email opens with -- a trial',
  assess(withLib({ therapeutic_areas: [] }), ctgov(1, ['A Study'])).subject_source,
  'named_trial'
);
t.check(
  'or a therapeutic area',
  assess(withLib(), ctgov(0, [])).subject_source,
  'therapeutic_area'
);
t.check(
  'the prompt names the subject fact by number, the same as the email opener',
  /Open the email from fact (\d+)\.[^\n]*\nBuild the email subject from fact \1 too/.test(
    assess(withLib(), ctgov(1, ['A Study'])).prompt),
  true
);
const HC = assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak', employee_estimate: 40, library: LIB }), ctgov(0, []));
t.check('with no openable fact, a small confirmed headcount builds the subject', HC.subject_source, 'headcount');
t.check('and it reaches the prompt, for the subject only', HC.prompt.indexOf('a team of 40 people') !== -1 &&
  HC.prompt.indexOf('use it nowhere else') !== -1, true);
t.check(
  'a headcount the subject does not use never reaches the prompt',
  assess(withLib({ employee_estimate: 40 }), ctgov(0, [])).prompt.indexOf('40'),
  -1
);
t.check(
  'a headcount outside Section 12\'s 5-100 band is not "small" -- the problem builds the subject',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak', employee_estimate: 150 }), ctgov(0, [])).subject_source,
  'problem'
);
t.check(
  'nor is a headcount under 5',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak', employee_estimate: 4 }), ctgov(0, [])).headcount,
  null
);
t.check(
  'no headcount at all: the problem builds the subject, with no fact in it',
  assess(lead({ city: 'Warsaw', founder_name: 'Anna Nowak' }), ctgov(0, [])).prompt.indexOf('put no fact') !== -1,
  true
);

t.check(
  'a hook opening from the trial is told to begin "You\'re sponsoring" -- about them, not the source',
  assess(withLib({ therapeutic_areas: [] }), ctgov(1, ['A Study'])).prompt.indexOf('begins with the words "You\'re sponsoring"') !== -1,
  true
);
t.check(
  'and a lead with no trial is not',
  assess(withLib(), ctgov(0, [])).prompt.indexOf('You\'re sponsoring'),
  -1
);

function subjectName(title) {
  const m = assess(withLib({ therapeutic_areas: [] }), ctgov(1, [title])).prompt.match(/call the trial "([^"]*)"/);
  return m ? m[1] : null;
}
t.check('a trial with a drug code is called by the code in the subject', subjectName('Efficacy of INM004 in Children With STEC-HUS'), 'INM004');
t.check('a hyphenated code too', subjectName('Safety and Efficacy of RPH-104 Used to Prevent Recurrent Fever Attacks'), 'RPH-104');
t.check('a title with no code gives its first two non-boilerplate words', subjectName('Hyperbaric Oxygen Brain Injury Treatment Trial'), 'Hyperbaric Oxygen');
t.check('boilerplate is skipped', subjectName('Registry of Minimally Invasive Cancer Treatment Using Spectral Angio-CT'), 'Minimally Invasive');
t.check(
  'no short name is given when the subject is not built from the trial',
  assess(withLib(), ctgov(0, [])).prompt.indexOf('call the trial'),
  -1
);

// --- warm-up week, for the link rule ----------------------------------------

t.check('no external send yet is week 1', assess(withLib({ warmup_week: null }), ctgov(0, [])).warmup_week, 1);
t.check('the week comes through as counted', assess(withLib({ warmup_week: 3 }), ctgov(0, [])).warmup_week, 3);
t.check('a nonsense value falls back to week 1, the link-free side', assess(withLib({ warmup_week: 'x' }), ctgov(0, [])).warmup_week, 1);

// --- the areas the assembler must not see named ------------------------------

const AB = assess(withLib({ therapeutic_areas: ['oncology', 'dermatology'] }), ctgov(0, [])).absent_areas;
t.check('absent areas exclude the lead\'s own', AB.indexOf('Oncology') === -1 && AB.indexOf('Dermatology') === -1, true);
t.check('and never include Other, which is an ordinary word', AB.indexOf('Other'), -1);
t.check('and include every other taxonomy area', AB.length, 15);

t.done();

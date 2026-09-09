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

t.done();

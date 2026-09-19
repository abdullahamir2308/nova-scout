// Assess Grounding -- n8n Code node (Run Once for Each Item).
//
// The gate for Workflow 4. Decides, deterministically, whether this lead has
// enough real facts to write a grounded draft -- and if it does, builds the fact
// sheet and the prompts. No model call happens before this node has said yes.
//
// Section 9, Workflow 4, GROUNDING GUARD -- LOCKED:
//
//   "The drafting prompt may only use facts present in the enrichment record.
//    If the record lacks at least two specific facts (therapeutic area, named
//    trial, city, founder name), the draft is flagged `low-context` and skipped
//    rather than invented."
//
// The four categories in that sentence are the whole fact vocabulary. Nothing
// else counts toward the threshold -- deliberately, and two exclusions are worth
// naming because both are tempting:
//
//   country            -- every lead has one, and the pipeline only ingests the
//                        13 target geographies. Section 9's own reweight found
//                        geography "measures did our scraper touch this lead",
//                        not fit. A fact every lead shares grounds nothing.
//   site_quality_notes -- model-generated prose about a website. It reads like a
//                        fact and is the single highest fabrication risk field
//                        in the record. Using it to authorise a draft would let
//                        one model's invention license another's.
//
// Drafting skill v2 (NovaScout_DraftingSkill.md) leaves all of that untouched and
// narrows what the model is asked for: a subject and two one-sentence hooks,
// nothing else. The problem, outcome, proof and ask are human-written lines from
// the claims library (migration 010), resolved here for this lead -- proof by
// its country -- and inserted verbatim by Assemble Drafts. The model never sees
// them. The guard above decides WHETHER a lead is drafted; the library decides
// what everything after the first sentence says.
//
// Master Ref build rule 3: this is all string matching, so it costs no tokens.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses these out of NovaScout_MasterRef.md
// and refuses to generate the workflow if this file has drifted. An n8n Code
// node cannot import a shared module, which is the only reason these copies
// exist. When the doc changes, update these -- never the reverse.
// ---------------------------------------------------------------------------

// Section 9, Workflow 4: "If the record lacks at least two specific facts".
const MIN_FACTS = 2;

// Section 9, Workflow 4: "under 80 words".
const MAX_WORDS = 80;

// Section 9, Workflow 3, "Therapeutic area taxonomy". A therapeutic area only
// counts as a grounding fact if it canonicalises against this locked list.
//
// This matters more here than it did in scoring. The live store is mixed: rows
// written before the enum was enforced hold free text, and some of that free
// text is not a therapeutic area at all ('pharma', 'medtech', 'fmcg',
// 'consumer health products'). Those are industry vocabulary, not a specialty.
// Letting one through would put "your work in FMCG" in front of a CRO founder,
// grounded in nothing but an extraction bug. Matching against the locked enum
// makes the guard behave identically on old and new rows.
const THERAPEUTIC_AREAS = [
  'Oncology',
  'Cardiovascular',
  'Central Nervous System',
  'Immunology',
  'Infectious Disease',
  'Endocrinology',
  'Metabolic Disorders',
  'Respiratory',
  'Rare Diseases',
  'Internal Medicine',
  'Anesthesiology',
  'Dermatology',
  'Rheumatology',
  'Ophthalmology',
  'Gastroenterology',
  'Nephrology',
  'Hematology',
  'Other',
];

// 'Other' is in the taxonomy but is by definition not a specific fact -- it is
// the catch-all. Grounding a draft in "your work in Other" is the failure this
// whole node exists to prevent.
const UNSPECIFIC_AREAS = ['Other'];

const AREA_BY_KEY = {};
for (const a of THERAPEUTIC_AREAS) AREA_BY_KEY[a.toLowerCase()] = a;

// Section 12: "Company: 5-100 employees." A headcount is not one of the four
// grounding facts and never counts toward MIN_FACTS. It is used for one thing
// only: the subject line, when no openable fact is available -- the skill's
// "small confirmed employee count" row. Enrichment records a number only when
// the site states one (code_fetch.js: "Never estimate"), so a non-null value in
// this band is the "confirmed" in that row.
const SMALL_TEAM = { min: 5, max: 100 };

// The four parts of a first touch that come from the approved claims library
// (NovaScout_DraftingSkill.md v2, section 2) rather than from the model. `link`
// is optional and only ever used after warm-up.
const LIBRARY_SLOTS = ['problem', 'outcome', 'proof', 'ask'];

// A role inbox is not a person -- Section 9, Workflow 3b, LOCKED:
//
//   "Where enrichment also named a founder the name, title and LinkedIn URL are
//    written alongside -- but the name is never presented as the owner of that
//    inbox, because no source says it is."
//
// So the email greeting is gated on the ADDRESS, not on whether a name is known.
// KLIXAR is the live case: Enrique Gaubeca is a confirmed founder with a
// confirmed LinkedIn profile, and hello@klixar.com is still not his mailbox.
// He gets greeted by name on LinkedIn and not by name over email.
const ROLE_LOCALPARTS = [
  'info', 'contact', 'contacts', 'hello', 'hi', 'connect', 'sales', 'admin',
  'office', 'enquiry', 'enquiries', 'inquiry', 'inquiries', 'mail', 'email',
  'support', 'help', 'team', 'general', 'reception', 'secretariat', 'marketing',
  'bd', 'business', 'businessdevelopment', 'careers', 'jobs', 'hr', 'press',
  'media', 'noreply', 'no-reply', 'donotreply', 'welcome', 'clinical',
  'research', 'cro', 'operations', 'ops', 'quality', 'regulatory',
];

// The drafting system prompt, substituted at build time by build_workflow.py
// (which is where it lives, next to the doc-parsed rules that shape it).
//
// It is emitted on every item rather than read from the Config node, because a
// Postgres node replaces the items entirely: anything Config set is gone by the
// time the batch query has run. Workflow 3 hit this first and solved it the same
// way -- code_score.js builds its own system_prompt and carries it forward.
const SYSTEM_PROMPT = __SYSTEM_PROMPT__;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Diacritic-insensitive lowercase. 'Janos Biro' -> 'janos biro', so a local-part
// match against a name still works on the ASCII form a mail system would use.
// normalize('NFD') is one of the few Unicode helpers the n8n Code sandbox
// actually provides (verified against the live instance during Workflow 2).
function fold(v) {
  return str(v).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

function canonicalAreas(raw) {
  const kept = [];
  const list = Array.isArray(raw) ? raw : [];
  for (const v of list) {
    if (v === null || v === undefined) continue;
    const canon = AREA_BY_KEY[String(v).trim().toLowerCase()];
    if (canon && UNSPECIFIC_AREAS.indexOf(canon) === -1 && kept.indexOf(canon) === -1) {
      kept.push(canon);
    }
  }
  return kept;
}

function localPart(email) {
  const e = str(email).toLowerCase();
  const at = e.indexOf('@');
  return at > 0 ? e.slice(0, at) : '';
}

// Does this address look like it belongs to the person we have a name for?
// Requires an actual token overlap -- 'devesh.kumar@' with no known name is NOT
// addressable by name, because we do not know that it is Devesh Kumar's; we
// only know the string. No source names the owner, so no greeting names them.
function addressIsPersons(email, name) {
  const lp = fold(localPart(email)).replace(/[^a-z]+/g, ' ').trim();
  if (!lp) return false;
  const nameTokens = fold(name).split(/[^a-z]+/).filter(function (t) { return t.length >= 3; });
  if (!nameTokens.length) return false;
  const lpTokens = lp.split(/\s+/).filter(Boolean);
  for (const nt of nameTokens) {
    if (lpTokens.indexOf(nt) !== -1) return true;
  }
  return false;
}

function isRoleInbox(email) {
  const lp = localPart(email);
  if (!lp) return false;
  const bare = lp.replace(/[^a-z0-9]+/g, '');
  return ROLE_LOCALPARTS.indexOf(lp) !== -1 || ROLE_LOCALPARTS.indexOf(bare) !== -1;
}

function firstName(name) {
  const parts = str(name).split(/\s+/).filter(Boolean);
  return parts.length ? parts[0] : '';
}

// The active claims-library rows, as the batch query aggregated them. A row
// with no text is not a line, whatever its flags say.
function libraryRows(raw) {
  const list = Array.isArray(raw) ? raw : [];
  const out = [];
  for (const r of list) {
    if (!r || typeof r !== 'object' || !str(r.body)) continue;
    out.push({
      code: str(r.code),
      slot: str(r.slot),
      body: str(r.body),
      countries: Array.isArray(r.countries) && r.countries.length ? r.countries.map(fold) : null,
      measured: r.measured === true,
      confirmed: r.confirmed === true,
    });
  }
  return out;
}

// Which proof (or link) lines serve this lead. Skill section 3: "Geography-
// matched: Vertex Clinical Research for Mexico and Latin America; NoblePath for
// Turkiye and nearby; either elsewhere." The mapping itself is data -- each
// row's `countries` -- so the operator owns it. A row with no countries is the
// "elsewhere" line, used only when no row names the lead's country.
//
// Within the chosen rows a measured line wins: skill section 4, "When filled, it
// replaces the plain line above and becomes the strongest sentence in the email."
function forCountry(rows, country) {
  const key = fold(country);
  const serving = rows.filter(function (r) { return r.countries && r.countries.indexOf(key) !== -1; });
  const pick = serving.length ? serving : rows.filter(function (r) { return !r.countries; });
  const measured = pick.filter(function (r) { return r.measured; });
  return measured.length ? measured : pick;
}

function lines(rows) {
  return rows.map(function (r) { return { code: r.code, body: r.body, confirmed: r.confirmed }; });
}

// What a trial is called in a subject line. A title does not fit in 50
// characters, and left to shorten it the model pasted the whole title or -- worse
// -- borrowed "INM004" from the worked example for a trial that has no code at
// all. Both measured on Innovate Research. Picking the name is deterministic
// (build rule 3): the drug/study code if the title has one, otherwise its first
// two words that are not trial boilerplate.
const TITLE_FILLER = [
  'a', 'an', 'the', 'of', 'in', 'and', 'for', 'with', 'to', 'on', 'at', 'by', 'versus', 'vs',
  'study', 'trial', 'registry', 'efficacy', 'safety', 'evaluation', 'evaluating', 'effect',
  'effects', 'assessment', 'randomized', 'randomised', 'controlled', 'phase', 'open-label',
  'multicenter', 'multicentre', 'single', 'double', 'blind', 'double-blind', 'pilot',
  'clinical', 'prospective', 'observational', 'comparative', 'comparing', 'investigate',
];

function trialShortName(title) {
  const t = str(title);
  const code = t.match(/\b[A-Z]{1,6}-?\d{2,6}[A-Z]?\b/);
  if (code) return code[0];
  const content = t.split(/\s+/).filter(function (w) {
    return TITLE_FILLER.indexOf(w.toLowerCase().replace(/[^a-z-]/g, '')) === -1;
  });
  return content.slice(0, 2).join(' ').replace(/[,;:.]+$/, '');
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const lead = $('Get Draft Batch').item.json;
const ct = $input.item.json;

const base = {
  lead_id: lead.lead_id,
  domain: lead.domain,
  company_name: lead.company_name,
  country: lead.country,
  fit_score: lead.fit_score,
};

// --- Did ClinicalTrials.gov answer? ----------------------------------------
//
// Same self-healing shape as Workflow 3's "Drop Failed Lookups". If the lookup
// failed we do NOT quietly draft with one fewer fact -- that would let an API
// outage flip leads into low-context and permanently mark them as unwritable.
// The lead is dropped and stays 'contact_found' for the next run.
//
// JSON.stringify, not String(): n8n wraps an HTTP failure as an object and
// String() renders that '[object Object]', discarding the one field an operator
// needs. Same handling as code_score.js and code_pick_contact.js.
if (!ct || typeof ct !== 'object' || ct.error || typeof ct.totalCount !== 'number') {
  const err = ct && (ct.error || ct.message);
  const detail = err
    ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300)
    : 'no numeric totalCount in the response';
  return {
    json: Object.assign({}, base, {
      ok: false,
      skip_reason: 'ClinicalTrials.gov lookup did not answer: ' + detail,
    }),
  };
}

// --- Collect the four locked fact categories -------------------------------

const areas = canonicalAreas(lead.therapeutic_areas);
const city = str(lead.city);
const founderName = str(lead.founder_name);
const founderTitle = str(lead.founder_title);

// Section 9's sponsor lookup is the only ClinicalTrials.gov query this project
// trusts. query.locn and query.term were both measured and rejected on false
// positives; a query.spons hit really is this company's trial, so its titles
// are safe to hand a drafting model.
const studies = Array.isArray(ct.studies) ? ct.studies : [];
const trialTitles = studies
  .map(function (s) {
    const p = s && s.protocolSection;
    const idm = p && p.identificationModule;
    return idm ? str(idm.briefTitle) : '';
  })
  .filter(Boolean)
  .slice(0, 3);
const trialCount = ct.totalCount;

// Each fact carries its own boundary -- what it does NOT establish -- in the
// same bullet as the fact itself.
//
// This is not decoration. Measured against real leads during the build, a sheet
// of bare attributes ("City: Istanbul", "Therapeutic areas: Oncology, ...")
// reliably produced sentences that JOINED two facts into a claim neither one
// makes: "one recruiting oncology trial in Istanbul", where the trial's area
// and location are both unknown. That is the Section 9 fabrication failure in
// its subtlest form -- every individual word traces to the record, and the
// sentence is still not true.
//
// Lowering the temperature did not fix it (tested at 0.35 and 0.7, same joins).
// Moving the boundary from a distant prompt rule into the bullet did.
// At most two areas reach the prompt, and the cap is applied HERE rather than
// asked for in the prompt. "Name at most two therapeutic areas" was in the
// instructions and the model listed five anyway ("You work in oncology,
// cardiovascular, rare diseases, respiratory, infectious disease" -- a database
// row, not a sentence). A model cannot list a fifth area it was never given.
// Build rule 3: this is deterministic, so it does not belong in a prompt.
//
// Oncology goes first when present -- Section 12 makes it the strongest signal
// and the NoblePath case study matches it. The rest keep taxonomy order, so the
// choice is stable across redrafts rather than depending on extraction order.
const promptAreas = areas.slice().sort(function (a, b) {
  if (a === 'Oncology') return -1;
  if (b === 'Oncology') return 1;
  return THERAPEUTIC_AREAS.indexOf(a) - THERAPEUTIC_AREAS.indexOf(b);
}).slice(0, 2);

const facts = [];
if (areas.length) {
  facts.push({
    kind: 'therapeutic_area',
    line:
      'Therapeutic areas listed on their own website: ' + promptAreas.join(' and ') +
      '. This says nothing about which area any particular trial belongs to.',
  });
}
if (trialCount > 0 && trialTitles.length) {
  facts.push({
    kind: 'named_trial',
    line:
      // "with you as the sponsor", not "with this company" -- the model copies
      // this line almost verbatim into the draft, so a third-person phrasing
      // here comes back as "this company" and "their own material" in a message
      // addressed to that company. The fact sheet is written in the voice the
      // draft needs to be in.
      'ClinicalTrials.gov lists ' + trialCount + ' recruiting trial' +
      (trialCount === 1 ? '' : 's') + ' with you as the sponsor. ' +
      // Phrased off how many titles came back, not off the total: pageSize caps
      // the titles at 3, so "2 trials ... it is titled" was a real mismatch.
      (trialTitles.length === 1 ? 'The only one named is' : 'The ones named are') + ': "' +
      trialTitles.join('"; "') +
      '". Nothing else about them is known -- not their phase, their therapeutic ' +
      'area, nor where they run.',
  });
}
if (city) {
  facts.push({
    kind: 'city',
    line:
      'The company is based in ' + city +
      '. This says nothing about where any trial runs or where any staff sit.',
  });
}
if (founderName) {
  facts.push({
    kind: 'founder_name',
    line:
      'Named on their own site as founder or MD: ' + founderName +
      (founderTitle ? ' (' + founderTitle + ')' : '') +
      '. This says nothing about which trials or clients this person personally handles.',
  });
}

const factKinds = facts.map(function (f) { return f.kind; });
const missing = ['therapeutic_area', 'named_trial', 'city', 'founder_name']
  .filter(function (k) { return factKinds.indexOf(k) === -1; });
const groundable = facts.length >= MIN_FACTS;

// --- How each channel may address this lead --------------------------------

const email = str(lead.email);
const contactName = str(lead.contact_name);
const linkedinUrl = str(lead.linkedin_url);

let emailAddressing;
if (!email) {
  emailAddressing = 'unaddressed';
} else if (isRoleInbox(email)) {
  emailAddressing = 'role-inbox';
} else if (contactName && addressIsPersons(email, contactName)) {
  emailAddressing = 'named';
} else {
  // A personal-shaped address whose owner no source names. Real and reachable,
  // but not greetable -- 'Devesh.kumar@' is a string, not an attribution.
  emailAddressing = 'unnamed';
}

const linkedinAddressing = linkedinUrl && contactName ? 'named' : 'no-profile';

const emailGreeting = emailAddressing === 'named' ? 'Hi ' + firstName(contactName) + ',' : 'Hello,';
const linkedinGreeting =
  linkedinAddressing === 'named' ? 'Hi ' + firstName(contactName) + ',' : 'Hello,';

// --- Prompts ---------------------------------------------------------------

const factSheet = facts
  .map(function (f, i) { return String(i + 1) + '. ' + f.line; })
  .join('\n');

// Which fact each channel opens from.
//
// Facts are collected in a fixed order (therapeutic area, trial, city, founder),
// so always opening from the first would make every email in a batch start on
// the same kind of sentence -- a merge-tag tell of exactly the sort Section 9
// bans, produced without a merge tag. Rotating on lead_id spreads the openings
// across the batch while staying deterministic: the same lead redrafted after a
// re-queue opens the same way, so a redraft is comparable to what it replaced.
//
// The LinkedIn DM opens from the next fact along, which is the cheapest way to
// stop it restating the email's first sentence -- the failure both temperature
// settings produced when the instruction was only "do not reuse a sentence".
//
// founder_name is excluded from opening either channel. It is a grounding fact
// -- it is evidence we know who this company is -- but it is not something you
// can tell its owner. Measured: opening from it produced "Enrique Gaubeca is
// founder or MD on their site" as the first line of a DM addressed to Enrique
// Gaubeca. A fact can be true, load-bearing for the guard, and still absurd as
// a sentence.
// city is excluded for a different reason than founder_name: it is not absurd,
// it is just thin. "You are based in Istanbul" is a weak opening sentence, and
// measured across three real leads at two temperatures the model reliably
// upgraded it into "You run trials in Istanbul" -- which is precisely the join
// the city fact's own boundary sentence forbids. Given nothing worth saying, it
// invents something worth saying. Do not hand it that opening.
const OPENABLE = ['named_trial', 'therapeutic_area'];

function idxWhere(pred) {
  const out = [];
  for (let i = 0; i < facts.length; i++) {
    if (pred(facts[i])) out.push(i);
  }
  return out;
}

// Three tiers, not two. A lead the guard passed on {city, founder_name} alone
// has nothing in OPENABLE, and rotating over "whatever is left" put the founder
// fact back in the opening slot -- the one outcome the exclusion above exists to
// prevent. So the fallback is ordered, not arbitrary: prefer a good opener, then
// anything that is not the founder, and only then give up.
const pool = (function () {
  const good = idxWhere(function (f) { return OPENABLE.indexOf(f.kind) !== -1; });
  if (good.length) return good;
  const notFounder = idxWhere(function (f) { return f.kind !== 'founder_name'; });
  if (notFounder.length) return notFounder;
  return facts.map(function (_, i) { return i; });
})();
const seed = Math.abs(parseInt(lead.lead_id, 10) || 0);
const openEmail = pool.length ? pool[seed % pool.length] : 0;
const openLinkedin = pool.length > 1
  ? pool[(seed + 1) % pool.length]
  : openEmail;

// --- What the subject line is built from -----------------------------------
//
// The same fact the email opens with, whenever that fact is one a subject can
// carry (a named trial, a therapeutic area). A subject about one fact over a
// hook about another reads as two emails stapled together -- and tying the two
// keeps the lead_id rotation above working for subjects as well, so two
// companies with the same fact shape do not get the same subject line.
//
// Only when the email opens from something a subject cannot use (the city
// fallback) does the skill's ladder go further down: a small confirmed
// headcount, and failing that the problem itself, with no prospect fact at all
// -- the "only geography known" row of the worked examples.
const n = Number(lead.employee_estimate);
const headcount = lead.employee_estimate !== null && lead.employee_estimate !== undefined &&
  Number.isInteger(n) && n >= SMALL_TEAM.min && n <= SMALL_TEAM.max ? n : null;

const openEmailKind = facts.length ? facts[openEmail].kind : null;
let subjectSource;
if (openEmailKind === 'named_trial' || openEmailKind === 'therapeutic_area') {
  subjectSource = openEmailKind;
} else if (headcount !== null) {
  subjectSource = 'headcount';
} else {
  subjectSource = 'problem';
}

let subjectLines;
if (subjectSource === 'headcount') {
  subjectLines = [
    'Build the email subject from this headcount, and use it nowhere else -- not in',
    'either hook: their own site states a team of ' + headcount + ' people. This says',
    'nothing about what those people do or where they sit.',
  ];
} else if (subjectSource === 'problem') {
  subjectLines = [
    'None of these facts suits a subject line. Build the subject from the problem',
    'instead, in the shape of the "only geography known" example, and put no fact',
    'about them in it.',
  ];
} else {
  subjectLines = [
    'Build the email subject from fact ' + (openEmail + 1) + ' too -- the same fact the email opens with.',
  ];
  if (subjectSource === 'named_trial' && trialTitles.length) {
    subjectLines.push('In the subject, call the trial "' + trialShortName(trialTitles[0]) + '".');
  }
}

// The trial fact reaches the model phrased from its source ("ClinicalTrials.gov
// lists ..."), and the model copies a fact line almost verbatim. Measured on the
// v2 prompt: one hook in eight still opened "ClinicalTrials.gov lists ..." --
// about the source, which skill section 3 rules out. A per-lead instruction
// naming the opening words is the cheap, targeted fix; Assemble Drafts still
// tags a hook that ignores it.
const trialOpeners = [openEmail, openLinkedin].some(function (i) {
  return facts.length && facts[i].kind === 'named_trial';
});

const prompt = [
  'Company: ' + str(lead.company_name),
  'Country: ' + str(lead.country),
  '',
  'VERIFIED FACTS. These are the only facts about this company that exist.',
  'Each one is numbered. Read the whole bullet, including the sentence saying',
  'what it does NOT establish:',
  factSheet,
  '',
  'Write the email subject, the email hook and the LinkedIn hook.',
  'Open the email from fact ' + (openEmail + 1) + '. Open the LinkedIn DM from fact ' +
    (openLinkedin + 1) + '.',
].concat(subjectLines, trialOpeners ? [
  'A hook built from the trial fact begins with the words "You\'re sponsoring" -- it',
  'is about them, not about ClinicalTrials.gov.',
] : [], [
  'Write no greeting and no sign-off -- those are added afterwards.',
]).join('\n');

// --- The approved claims library --------------------------------------------
//
// Parts 2-5 of the email come from here, verbatim, never from the model (skill
// section 2). They are resolved now, before any GPU time is spent: a groundable
// lead the library cannot complete -- no active line for a slot, or no proof
// line for its country and no fallback -- is dropped and stays 'contact_found'
// until the operator fixes the table. Same self-healing shape as a failed
// lookup: a gap in the operator's data must not mark a lead low-context, and it
// must not produce a draft with a hole where the proof should be.
//
// A lead that is not groundable does not need the library -- it gets the
// low-context note either way -- so it is not held back by it.
const lib = libraryRows(lead.library);
const bySlot = function (slot) { return lib.filter(function (r) { return r.slot === slot; }); };
const pools = {
  problem: lines(bySlot('problem')),
  outcome: lines(bySlot('outcome')),
  proof: lines(forCountry(bySlot('proof'), lead.country)),
  ask: lines(bySlot('ask')),
};
const linkPool = lines(forCountry(bySlot('link'), lead.country));
const emptySlots = LIBRARY_SLOTS.filter(function (s) { return !pools[s].length; });
const libraryGap = emptySlots.length
  ? 'the claims library has no active ' + emptySlots.join(', ') + ' line' +
    (emptySlots.indexOf('proof') !== -1 ? ' (proof: none serves ' + str(lead.country) + ' and no fallback)' : '')
  : null;

// Warm-up week at draft time, from the same Sent-folder history the send path
// counts. NULL means no external send yet: the first send will be week 1.
const w = Number(lead.warmup_week);
const warmupWeek = Number.isInteger(w) && w >= 1 ? w : 1;

// Every taxonomy area this lead does NOT have. Assemble Drafts flags a hook or
// subject naming one -- the worked examples in the system prompt name
// oncology, and a 9B model copies examples.
const absentAreas = THERAPEUTIC_AREAS.filter(function (a) {
  return UNSPECIFIC_AREAS.indexOf(a) === -1 && areas.indexOf(a) === -1;
});

const libraryHolds = groundable && libraryGap !== null;

return {
  json: Object.assign({}, base, {
    ok: !libraryHolds,
    skip_reason: libraryHolds ? libraryGap : undefined,
    groundable: groundable,
    fact_count: facts.length,
    fact_kinds: factKinds,
    missing_facts: missing,
    facts: facts,
    fact_sheet: factSheet,
    trial_count: trialCount,
    trial_titles: trialTitles,
    areas: areas,
    city: city,
    founder_name: founderName,
    contact_name: contactName,
    email: email,
    linkedin_url: linkedinUrl,
    email_addressing: emailAddressing,
    linkedin_addressing: linkedinAddressing,
    email_greeting: emailGreeting,
    linkedin_greeting: linkedinGreeting,
    max_words: MAX_WORDS,
    open_email_fact: facts.length ? facts[openEmail].kind : null,
    open_linkedin_fact: facts.length ? facts[openLinkedin].kind : null,
    subject_source: subjectSource,
    headcount: headcount,
    absent_areas: absentAreas,
    library_pools: pools,
    library_link: linkPool,
    library_gap: libraryGap,
    warmup_week: warmupWeek,
    system_prompt: SYSTEM_PROMPT,
    prompt: prompt,
  }),
};

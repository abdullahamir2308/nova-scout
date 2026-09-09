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

const prompt = [
  'Company: ' + str(lead.company_name),
  'Country: ' + str(lead.country),
  '',
  'VERIFIED FACTS. These are the only facts about this company that exist.',
  'Each one is numbered. Read the whole bullet, including the sentence saying',
  'what it does NOT establish:',
  factSheet,
  '',
  'Write the email body and the LinkedIn DM body.',
  'Open the email from fact ' + (openEmail + 1) + '. Open the LinkedIn DM from fact ' +
    (openLinkedin + 1) + '.',
  'Write no greeting and no sign-off -- those are added afterwards.',
].join('\n');

return {
  json: Object.assign({}, base, {
    ok: true,
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
    system_prompt: SYSTEM_PROMPT,
    prompt: prompt,
  }),
};

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
// Drafting skill v3 (NovaScout_DraftingSkill.md) leaves all of that untouched.
// What changed is what the model is given and asked for. v2 had a 9B model
// write a subject and two hooks and pasted claims-library lines in after them,
// verbatim. v3 sends the whole composition to Claude Sonnet 5.5: one prospect
// fact to open on, plus the approved claims for this lead -- descriptions, pain
// angles, benefits, the proof line matched to its country, the asks -- and the
// model writes the email around them. It may rephrase a claim, never widen it
// (skill section 0, rule 2); Assemble Drafts enforces section 3 on what comes
// back. The guard above still decides WHETHER a lead is drafted, before any
// API call is made.
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

// The claims-library slots a v3 email is composed from (NovaScout_DraftingSkill.md
// v3, section 4). A lead needs at least one active line in each, or it waits.
// `link` is optional and only ever used after warm-up, appended by code.
const LIBRARY_SLOTS = ['description', 'angle', 'benefit', 'proof', 'ask'];

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

// The fixed part of the Anthropic Messages API request -- model, max_tokens,
// effort and the JSON schema -- substituted at build time by build_workflow.py,
// which parses the model id out of the skill and the Master Ref. Each item adds
// its own system prompt and user message; the HTTP node sends $json.request as
// the body. Building the body here rather than in an n8n expression keeps it
// testable, and keeps a `}}` in the schema from ending the expression early.
const CLAUDE_REQUEST = __CLAUDE_REQUEST__;

// Section 12's business-hours clocks, as a list of country names, substituted
// at build time from the doc's own table -- the same table code_decide.js's
// COUNTRY_CLOCKS is checked against, so this cannot drift from what Send can
// actually place in business hours.
//
// Why drafting cares about a send-side table: a lead whose country has no clock
// is refused by Send for ever (`unknown-country`). Before 2026-10-07 the table
// held the core 13 only, so an extended-country lead was looked up, drafted,
// claim-checked and auto-approved -- real API spend -- for an email that could
// never go out. Two had already got that far (498 Austria, 492 Armenia). The
// clock table now covers all 133 included countries, so this guard is the net
// under it: a country the directory starts listing before anyone adds its
// clock, or a lead imported by hand from an excluded jurisdiction, is stopped
// here instead of being discovered months later in the pending queue.
const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Section 12's clock table, case-insensitively -- the same way code_decide.js
// looks it up, so this guard and Send can never disagree about one lead.
const SEND_CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

function hasSendClock(country) {
  return SEND_CLOCK_KEYS.indexOf(str(country).toLowerCase()) !== -1;
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

// An honorific is not a first name: "Dr. John S. Sampalis" is greeted "Hi John,",
// not "Hi Dr.," (found 2026-10-06 on lead 55's LinkedIn draft). Never strips the
// last remaining word, so a one-word name is returned as it was.
const HONORIFICS = ['dr', 'prof', 'professor', 'mr', 'mrs', 'ms', 'miss', 'mx', 'sir', 'madam', 'eng', 'ing'];
function firstName(name) {
  const parts = str(name).split(/\s+/).filter(Boolean);
  while (parts.length > 1 && HONORIFICS.indexOf(parts[0].toLowerCase().replace(/\.+$/, '')) !== -1) parts.shift();
  return parts.length ? parts[0] : '';
}

// Which kind of prospect this is (2026-10-09, Workflow 1b). The batch query
// already resolves it; anything unexpected is treated as a CRO, the type every
// line written before sites existed is for.
const COMPANY_TYPES = ['CRO', 'site', 'SMO'];
function companyTypeOf(v) {
  return COMPANY_TYPES.indexOf(str(v)) !== -1 ? str(v) : 'CRO';
}

// What the model is told about the prospect's kind, in the user message (never
// the cached system prompt, which is the same for every lead). A site hears from
// sponsors AND from CROs choosing sites, and is never itself a CRO. Its web pages
// are "your website": the 2026-10-09 dry run's first site draft opened "Your site
// lists ...", which to a research site reads as the clinic, not the web page.
const SITE_WORDING = 'Call its web pages "your website", never "your site" -- to a research site,\n' +
  '"your site" means the clinic.';
const PROSPECT_TYPE_NOTES = {
  CRO: 'THE PROSPECT is a contract research organisation (CRO). Its inquiries come from sponsors.',
  site: 'THE PROSPECT is an independent clinical research site, NOT a CRO. Its inquiries come from sponsors\n' +
    'and from CROs choosing sites for a study. Never call it a CRO, never write "CRO websites", and\n' +
    'never use a proof that calls a deployment a CRO. ' + SITE_WORDING,
  SMO: 'THE PROSPECT is a site management organisation (SMO) running research sites, NOT a CRO. Its\n' +
    'inquiries come from sponsors and from CROs choosing sites for a study. Never call it a CRO, never\n' +
    'write "CRO websites", and never use a proof that calls a deployment a CRO. ' + SITE_WORDING,
};

// The active claims-library rows, as the batch query aggregated them, that are
// written for this kind of prospect. A row with no text is not a line, whatever
// its flags say; a row with no company_types predates them and is a CRO line.
function libraryRows(raw, companyType) {
  const list = Array.isArray(raw) ? raw : [];
  const out = [];
  for (const r of list) {
    if (!r || typeof r !== 'object' || !str(r.body)) continue;
    const types = Array.isArray(r.company_types) && r.company_types.length ? r.company_types.map(str) : ['CRO'];
    if (companyType && types.indexOf(companyType) === -1) continue;
    out.push({
      code: str(r.code),
      slot: str(r.slot),
      body: str(r.body),
      countries: Array.isArray(r.countries) && r.countries.length ? r.countries.map(fold) : null,
      measured: r.measured === true,
      confirmed: r.confirmed === true,
      capabilities: Array.isArray(r.capabilities) ? r.capabilities.map(str).filter(Boolean) : [],
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
  return rows.map(function (r) {
    return { code: r.code, body: r.body, confirmed: r.confirmed, capabilities: r.capabilities };
  });
}

// Skill v3 section 3, No repeats: the description and the benefits must not
// repeat the same capability. Every pair of offered description/benefit lines
// that share one, worked out here so the prompt can name them outright rather
// than leave the model to compare capability lists. Assemble Drafts tags a
// message that uses one anyway (`claim-repeat`).
function repeatPairs(pool) {
  const pairs = [];
  for (let i = 0; i < pool.length; i++) {
    for (let j = i + 1; j < pool.length; j++) {
      const shared = (pool[i].capabilities || []).filter(function (c) {
        return (pool[j].capabilities || []).indexOf(c) !== -1;
      });
      if (shared.length) pairs.push({ a: pool[i].code, b: pool[j].code, shared: shared });
    }
  }
  return pairs;
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
  // CRO, site or SMO -- the batch query's one definition (enrichment's
  // company_type when it is one of the three, otherwise by source). It decides
  // which claims are offered and which wording Assemble Drafts refuses.
  company_type: companyTypeOf(lead.company_type),
};

// --- Can this lead ever be sent to? ---------------------------------------
//
// First, before the ClinicalTrials.gov answer is even read, because this is the
// one skip that must not be retried: no model call, no claim check, and no
// place in the next tick's batch either. A lead with no business-hours clock
// cannot be sent to at all (Section 12), so it takes the no-API branch and
// Build Low-Context Drafts records why -- which advances it to 'drafted' and
// gets it out of the queue, exactly as a low-context note does, for exactly the
// same reason: a lead that writes no row is re-picked by every tick and starves
// the leads behind it.
if (!hasSendClock(lead.country)) {
  return {
    json: Object.assign({}, base, {
      ok: true,
      groundable: false,
      no_send_clock: true,
      // The addressing modes are still recorded -- they tell a reviewer whether
      // a hand-written email to this lead would have anywhere to go.
      email_addressing: lead.email ? 'addressed' : 'unaddressed',
      linkedin_addressing: lead.linkedin_url ? 'addressed' : 'unaddressed',
      fact_count: 0,
      fact_kinds: [],
      missing_facts: [],
    }),
  };
}

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
      // Second person -- the model copies this line closely, so a third-person
      // phrasing came back as "this company" in a message addressed to that
      // company. But never "with you as the sponsor": skill v3 bans describing
      // the prospect as sponsoring anything, because "sponsor" now means the
      // CRO's client in every claim, and v2's "You're sponsoring ..." hooks put
      // one word on two parties in the same email.
      'ClinicalTrials.gov lists ' + trialCount + ' recruiting trial' +
      (trialCount === 1 ? '' : 's') + ' registered under your company. ' +
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
// fallback) does the ladder go further down: a small confirmed headcount, and
// failing that the pain itself, with no prospect fact at all. Skill v3: the
// subject is "built from a prospect fact or the pain".
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
  subjectSource = 'pain';
}

let subjectLines;
if (subjectSource === 'headcount') {
  subjectLines = [
    'Build the email subject from this headcount, and use it nowhere else -- not in',
    'either hook: their own site states a team of ' + headcount + ' people. This says',
    'nothing about what those people do or where they sit.',
  ];
} else if (subjectSource === 'pain') {
  subjectLines = [
    'None of these facts suits a subject line. Build the subject from the pain',
    'angle you chose instead, and put no fact about them in it.',
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
// lists ..."), and a model copies a fact line closely. Measured on the v2
// prompt: one hook in eight still opened "ClinicalTrials.gov lists ..." -- about
// the source, which the skill rules out. v2 fixed it by naming the opening
// words, "You're sponsoring"; skill v3 bans exactly that phrase, so the
// instruction now names v3's words instead. Assemble Drafts tags a hook that
// ignores either.
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
  'Open the email from fact ' + (openEmail + 1) + '. Open the LinkedIn DM from fact ' +
    (openLinkedin + 1) + '.',
].concat(subjectLines, trialOpeners ? [
  'A hook built from the trial fact says "Your recruiting trial ..." or "You\'re',
  'running ..." -- about them, not about ClinicalTrials.gov, and never "sponsoring".',
] : []).join('\n');

// --- The approved claims library --------------------------------------------
//
// Everything in the email that is not a prospect fact comes from here (skill
// v3, section 0, rule 2): the model composes with these lines and may rephrase
// them, never widen them. They are resolved now, before any API money is
// spent: a groundable lead the library cannot complete -- no active line for a
// slot, or no proof line for its country and no fallback -- is dropped and
// stays 'contact_found' until the operator fixes the table. Same self-healing
// shape as a failed lookup: a gap in the operator's data must not mark a lead
// low-context, and it must not produce a draft with no proof in it.
//
// A lead that is not groundable does not need the library -- it gets the
// low-context note either way -- so it is not held back by it.
const lib = libraryRows(lead.library, base.company_type);
const bySlot = function (slot) { return lib.filter(function (r) { return r.slot === slot; }); };
const pools = {
  description: lines(bySlot('description')),
  angle: lines(bySlot('angle')),
  benefit: lines(bySlot('benefit')),
  proof: lines(forCountry(bySlot('proof'), lead.country)),
  ask: lines(bySlot('ask')),
};
const linkPool = lines(forCountry(bySlot('link'), lead.country));
const emptySlots = LIBRARY_SLOTS.filter(function (s) { return !pools[s].length; });
const libraryGap = emptySlots.length
  ? 'the claims library has no active ' + emptySlots.join(', ') + ' line' +
    (emptySlots.indexOf('proof') !== -1 ? ' (proof: none serves ' + str(lead.country) + ' and no fallback)' : '')
  : null;

// The claims, as the model sees them: each with its code, so it can report
// which each message used (Assemble Drafts checks every code against this list and
// records them in `variant` for the learning loop). Only this lead's proof line
// is offered -- the geography match is decided here, not by the model.
const CLAIM_HEADINGS = [
  ['description', 'DESCRIPTION -- introduce what we built with one of these, the first time you mention it:'],
  ['angle', 'PAIN AND STAKES ANGLES -- build the email around ONE:'],
  ['benefit', 'BENEFITS -- use one or two:'],
  ['proof', 'PROOF -- use this; it is matched to their country:'],
  ['ask', 'ASK -- end each message with ONE of these, rephrased if you like:'],
];
const claimsSheet = CLAIM_HEADINGS.map(function (h) {
  return [h[1]].concat(pools[h[0]].map(function (l) {
    const about = (h[0] === 'description' || h[0] === 'benefit') && l.capabilities.length
      ? '  (about: ' + l.capabilities.join(', ') + ')' : '';
    return '  [' + l.code + '] ' + l.body + about;
  })).join('\n');
}).join('\n\n');

const pairs = repeatPairs(pools.description.concat(pools.benefit));
const repeatSheet = pairs.length
  ? 'NO REPEATS -- the description and the benefits must not repeat the same capability. These\n' +
    'pairs say the same thing twice, so never use both lines of a pair in one message:\n' +
    pairs.map(function (p) { return '  ' + p.a + ' with ' + p.b + ' (both: ' + p.shared.join(', ') + ')'; }).join('\n') +
    '\n\n'
  : '';

const fullPrompt = PROSPECT_TYPE_NOTES[base.company_type] + '\n\n' + prompt +
  '\n\nAPPROVED CLAIMS FOR THIS EMAIL. Nothing about the product, the problem or the\n' +
  'proof may come from anywhere else. Rephrase freely; never widen what a line says.\n\n' +
  claimsSheet + '\n\n' + repeatSheet +
  'Write the email subject, the email (body and ask) and the LinkedIn DM (body and ask).\n' +
  'Write no greeting and no sign-off -- those are added afterwards. In email_claims\n' +
  'and linkedin_claims, list the code of every claim that message used.';

// PROMPT CACHING -- on since 2026-10-08, one breakpoint, on the system prompt.
//
// The system prompt is the only part of this request that is byte-identical
// across leads and across runs: build_workflow.py substitutes it at build time,
// so every item in every batch carries the same 1,818 tokens of it. Everything
// per-lead -- the company, the fact sheet, the geography-matched claims, which
// fact to open on -- is in the user message, AFTER the breakpoint, which is the
// order caching needs (a prefix match: one differing byte invalidates
// everything after it).
//
// MEASURED against the live API on 2026-10-08, not assumed:
//   * the cached prefix is 2,224 tokens, not the 1,818 of the prompt alone --
//     `output_config.format`'s schema renders into the prefix too and caches
//     with it;
//   * a DIFFERENT lead's call reads the same 2,224 (write=0, read=2224), which
//     is what makes this worth anything across a batch;
//   * Sonnet 5.5's minimum cacheable prefix is 512 tokens (live docs, same
//     day), so 2,224 clears it comfortably. Below the minimum nothing caches
//     and nothing errors -- which is why code_approval.js leaves the 448-token
//     repair prompt alone.
//
// WHY THE DEFAULT 5-MINUTE TTL AND NOT `ttl: '1h'`. The saving comes from reads,
// and reads only happen when two calls share the prefix inside the window. The
// real call pattern was measured from n8n's own execution history: calls arrive
// in bursts of two runs 0.1-2 minutes apart, and the bursts themselves are
// 57-60 minutes apart or hours apart. A 5-minute entry catches every read that
// actually happens (the paired run seconds later) at a 1.25x write; a 1-hour
// entry costs 2x to write and would only earn more if the 59.9-minute gaps
// landed inside a 60-minute window measured from the first request's START --
// a coin flip this does not need to bet on. If the schedule ever puts bursts
// well inside the hour, `ttl: '1h'` becomes the better choice.
//
// WHAT IS NOT DONE HERE, AND WHY. A run's own calls are concurrent -- this
// node starts every item's request inside its item loop and awaits them
// together at the end (read from the installed HttpRequestV3.node.js:
// `requestPromises.push(...)` in the loop, one `Promise.allSettled` after it),
// and its `batchSize`/`batchInterval` options only `sleep()` between
// dispatches, never awaiting a response. So within one run each call writes the
// entry and none reads a sibling's; the reads come from the run that follows.
// Sending the batch's first lead through its own node first WOULD win those
// reads, and it was built and then rejected on evidence: n8n carries an item's
// ancestry in `pairedItem`, a rest lane hanging off the first call collapses
// every item's ancestry onto the first one, and Assemble Drafts' own
// `$('Assess Grounding').item` then returns the WRONG lead's facts -- silently,
// with no error (probed in the live instance: items 2 and 3 both resolved item
// 1's record). The lineage-preserving variant passed that test and then stopped
// the workflow dead on a one-lead batch, which the measured history says is the
// commonest size. Section 9, Workflow 4 records both probes and the fix that
// would make it safe.
//
// CACHING MUST NOT CHANGE WHAT A DRAFT SAYS, and it cannot: the bytes the model
// reads are identical either way -- `cache_control` is a billing instruction
// about a prefix, not part of it. build_workflow.py asserts that the only cached
// block is this system prompt and that no per-lead text is ever inside one.
const CACHE_CONTROL = { type: 'ephemeral' };

const request = Object.assign({}, CLAUDE_REQUEST, {
  system: [{ type: 'text', text: SYSTEM_PROMPT, cache_control: CACHE_CONTROL }],
  messages: [{ role: 'user', content: fullPrompt }],
});

// Warm-up week at draft time, from the same Sent-folder history the send path
// counts. NULL means no external send yet: the first send will be week 1.
const w = Number(lead.warmup_week);
const warmupWeek = Number.isInteger(w) && w >= 1 ? w : 1;

// Every taxonomy area this lead does NOT have. Assemble Drafts flags a draft
// naming one -- the worked example in the system prompt names oncology and
// immunology, and a model copies examples.
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
    open_email_fact: facts.length ? facts[openEmail].kind : null,
    open_linkedin_fact: facts.length ? facts[openLinkedin].kind : null,
    subject_source: subjectSource,
    headcount: headcount,
    absent_areas: absentAreas,
    library_pools: pools,
    repeat_pairs: pairs,
    library_link: linkPool,
    library_gap: libraryGap,
    warmup_week: warmupWeek,
    // settings.auto_approve_email as this run's batch query read it (migration
    // 014). Carried per item for the same reason the library is: a Postgres
    // node replaces the items, so nothing set upstream survives otherwise.
    auto_approve: lead.auto_approve_email === true,
    system_prompt: SYSTEM_PROMPT,
    prompt: fullPrompt,
    request: request,
  }),
};

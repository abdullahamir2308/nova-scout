// Build First Touch -- n8n Code node (Run Once for Each Item).
//
// Workflow 4 since 2026-10-19 (operator brief; Master Ref Section 9, Workflow
// 4). A first touch is no longer composed by a model. It is one fixed template,
// in the operator's approved wording, filled with facts the pipeline already
// holds: the enrichment record and the ClinicalTrials.gov count Get Draft Batch
// looked up a node earlier. No model call, no claim check, no repair loop -- the
// only words a draft can contain are the ones below plus the lead's own facts,
// so there is nothing left for a second model to police.
//
// What the code still decides:
//   * which arm a lead gets (lead_id even = A, odd = B) -- the A/B test;
//   * which one fact opens it (the SMYKM line: recruiting trials, then
//     therapeutic areas, then city, then none), and its arm's sentence;
//   * how a fact is written (singular/plural, at most two areas in lowercase,
//     phases as "Phase I to III", legal suffixes stripped from the company,
//     an ALL-CAPS name fixed, honorifics skipped, a role inbox greeted
//     "Hi there,");
//   * whether the draft may approve itself (gate(): only an email that has a
//     verified address, a Send clock for its country and no blocklisted
//     domain).
//
// Everything above "Node body" is pure and shared: Follow-Ups
// (n8n/sendtrack/code_followup.js) embeds it verbatim, so a follow-up is
// written and gated by these same functions. Keep it free of n8n data reads
// (the dollar-sign accessors) and of build placeholders, or both builds refuse.
//
// Master Ref build rule 3: deterministic work costs no tokens.

// ---------------------------------------------------------------------------
// The approved wording -- LOCKED (operator, 2026-10-19). build_workflow.py
// renders these constants back into the Master Ref's Section 9 blocks and
// refuses to build unless the doc carries them byte for byte. Change the doc
// first, then these -- never the reverse.
//
// The placeholders are filled by fill() below. Four of them carry a grammar
// rule with them, and are therefore written exactly as the approved text has
// them: "{n} trial(s)" (singular or plural), "{company}'s" (an apostrophe
// alone after a name ending in s), "a {area1}" ("an" before a vowel) and
// " from {phases}" (dropped when no phase is known).
// ---------------------------------------------------------------------------

const SUBJECT_WITH_FACT = "{company} + {fact} + sponsor inquiries";
const SUBJECT_NO_FACT = "{company} + sponsor inquiries";

// The subject's {fact}: the SMYKM fact, short.
const SUBJECT_FACT_TRIALS = "{n} recruiting trial(s)";
const SUBJECT_FACT_AREAS = "{areas}";
const SUBJECT_FACT_CITY = "{city}";

const GREETING_NAMED = "Hi {first_name},";
const GREETING_UNNAMED = "Hi there,";

const INTRO_LINE = "We have yet to be properly introduced, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs.";

const SMYKM_TRIALS_A = "With {n} trial(s) recruiting right now, sponsors are clearly paying attention to {company}, and every one who visits your website is a potential next study worth turning into a conversation.";
const SMYKM_TRIALS_B = "{company} has {n} trial(s) recruiting right now. Each sponsor who checks your website after seeing that is a lead worth capturing.";
const SMYKM_AREAS_A = "{company} covers {areas} from {phases}, so sponsors visiting your website arrive with very different questions, and each one is a lead worth capturing.";
const SMYKM_AREAS_B = "A sponsor planning a {area1} study will usually check {company}'s website before they ever email you. That visit is a lead worth capturing.";
const SMYKM_CITY = "Sponsors looking for a CRO in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.";
// The fourth line (operator, 2026-10-19): needs only the company, so every lead has one.
const SMYKM_COMPANY = "Sponsors evaluating {company} will usually start on your website, and every one of those visits is a potential study worth turning into a conversation.";

const PITCH_LINE = "That's precisely what we do at Amitrix Labs. We build AI agents for CRO websites that answer sponsor and visitor questions, collect their details, and turn more of your visitors into qualified leads, so you win more studies.";

const CLOSE_A = "Do you have time over the next week or two to learn more? Let me know what works for you, and I'll send a calendar invite along accordingly.";
const CLOSE_B = "Worth a look for {company}?";

// Three lines, no phone (Section 5, changed 2026-10-19). The name and title
// lines must equal NOVASCOUT_SENDER_NAME / _TITLE -- Send checks every body
// for those two lines, and the build refuses a template signed by anyone else.
const SIGN_OFF = ["Cheers!", "Abdullah Amir", "Founder, Amitrix Labs"];

// Follow-ups (Section 9, Workflow 6). #1 closes with the lead's own arm's
// close; #2 has no close.
const FOLLOW_UP_1_LINE = "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits {company}'s website is a potential study, and we help make sure those visits turn into conversations.";
const FOLLOW_UP_2_LINE = "I'll keep this short and won't follow up again after this. If turning more of {company}'s website visitors into qualified leads is ever on your list, just reply and I'll send over the details.";

// ---------------------------------------------------------------------------
// The rules that pick and format -- parsed from Section 9 by the build too.
// ---------------------------------------------------------------------------

// "Two arms, assigned by lead_id: even = A, odd = B."
const ARM_BY_PARITY = { even: 'A', odd: 'B' };

// "SMYKM line: first fact the lead has, in this order".
const SMYKM_ORDER = ['trials', 'areas', 'city', 'company'];

// "at most two areas, lowercase, joined with 'and'".
const MAX_AREAS = 2;

// Section 9, Workflow 3, "Therapeutic area taxonomy" -- LOCKED. An area only
// counts if it canonicalises against this list, case-insensitively: rows from
// before the enum hold free text, some of it not a therapeutic area at all
// ('pharma', 'medtech', 'fmcg'), and "covers fmcg" would be an extraction bug
// sent under the operator's name. 'Other' is the catch-all, never a fact.
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
const UNSPECIFIC_AREAS = ['Other'];
const AREA_BY_KEY = {};
for (const a of THERAPEUTIC_AREAS) AREA_BY_KEY[a.toLowerCase()] = a;

// A role inbox is not a person -- Section 9, Workflow 3b, LOCKED: where
// enrichment named a founder, the name is written alongside a scraped inbox but
// never presented as its owner. So the greeting is gated on the ADDRESS:
// KLIXAR's hello@ inbox is greeted "Hi there," even though the record names
// KLIXAR's founder, and a personal-shaped local part no source attributes gets
// no name either -- it is a string, not an attribution.
const ROLE_LOCALPARTS = [
  'info', 'contact', 'contacts', 'hello', 'hi', 'connect', 'sales', 'admin',
  'office', 'enquiry', 'enquiries', 'inquiry', 'inquiries', 'mail', 'email',
  'support', 'help', 'team', 'general', 'reception', 'secretariat', 'marketing',
  'bd', 'business', 'businessdevelopment', 'careers', 'jobs', 'hr', 'press',
  'media', 'noreply', 'no-reply', 'donotreply', 'welcome', 'clinical',
  'research', 'cro', 'operations', 'ops', 'quality', 'regulatory',
];

// "Dr. John S. Sampalis" is "John", not "Dr." (lead 55, 2026-10-06).
const HONORIFICS = ['dr', 'prof', 'professor', 'mr', 'mrs', 'ms', 'miss', 'mx', 'sir', 'madam', 'eng', 'ing'];

// Legal forms stripped from the END of a company name, repeatedly ("Linical
// Co., Ltd." -> "Linical"), each only when a space or comma precedes it, so a
// word inside a name is never touched. "Company" is deliberately absent: "The
// Clinical Trial Company" is a name, not a legal form. Longest forms first.
const LEGAL_FORMS = [
  's\\.?\\s?a\\.?\\s+de\\s+c\\.?\\s?v\\.?',
  's\\.?\\s?de\\s+r\\.?\\s?l\\.?(?:\\s+de\\s+c\\.?\\s?v\\.?)?',
  'sp\\.?\\s?z\\s?o\\.?\\s?o\\.?',
  'spol\\.?\\s+s\\s+r\\.?\\s?o\\.?',
  'gmbh\\s*&\\s*co\\.?\\s*kg',
  'pvt\\.?\\s*ltd\\.?',
  'private\\s+limited',
  '\\(?pty\\)?\\.?\\s+ltd\\.?',
  'pte\\.?\\s+ltd\\.?',
  'sdn\\.?\\s+bhd\\.?',
  'ltd\\.?\\s+şti\\.?',
  'co\\.?,?\\s+ltd\\.?',
  'ltd\\.?\\s+co\\.?',
  's\\.?\\s?r\\.?\\s?l\\.?',
  's\\.?\\s?r\\.?\\s?o\\.?',
  's\\.?\\s?p\\.?\\s?a\\.?',
  's\\.?\\s?a\\.?\\s?s\\.?',
  's\\.?\\s?l\\.?\\s?u\\.?',
  's\\.?\\s?l\\.?',
  's\\.?\\s?a\\.?',
  's\\.?\\s?c\\.?',
  'a\\.?\\s?ş\\.?',
  'a\\.?\\s?s\\.?',
  'a/s', 'aps', 'sarl', 'gmbh', 'mbh', 'ag', 'kg', 'e\\.\\s?v\\.',
  'ltd\\.?', 'limited', 'l\\.?l\\.?c\\.?', 'llp', 'inc\\.?', 'incorporated', 'corp\\.?', 'corporation',
  'co\\.?', 'plc', 'b\\.?\\s?v\\.?', 'n\\.?\\s?v\\.?', 'kft\\.?', 'zrt\\.?', 'nyrt\\.?', 'bt\\.?',
  'd\\.?\\s?o\\.?\\s?o\\.?', 'ooo', 'oyj?', 'ab', 'ltda\\.?', 'eireli', 'k\\.?\\s?k\\.?', 'jsc', 'tic\\.?',
];
const LEGAL_FORM_RE = new RegExp('[\\s,]+(?:' + LEGAL_FORMS.join('|') + ')\\s*$', 'i');
// Romania writes its form first: "S.C. Centrul de Gastroenterologie ... S.R.L".
const LEGAL_PREFIX_RE = /^s\.?\s?c\.?\s+(?=\S)/i;

// A deliverable address, the same pattern Send's Decide node refuses a draft on.
const EMAIL_RE = /^[^\s@<>(),;:"']+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;

// Why a template draft is held for a person (Section 9, Workflow 5) -- the
// only three reasons there are since 2026-10-19, plus the operator's switch.
// A tag goes after '+' in `variant`; the drafts_approval_rules trigger
// (migration 020) refuses to auto-approve anything carrying one.
const HOLD_REASONS = {
  'no-address': 'no-address: no deliverable email address on file, so Send could never deliver it',
  'unverified-address': 'unverified-address: the address on file is not verified (contacts.verified), so Send would refuse it',
  'no-send-clock': 'no-send-clock: {country} has no business-hours clock, so this email can never be sent (Section 12)',
  'blocklisted': 'blocklisted: {domain} is on the blocklist',
};
const AUTO_APPROVE_OFF = 'auto-approve-off: settings.auto_approve_email is off, so every email waits for a person';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Diacritic-insensitive lowercase, so 'Janos Biro' matches janos.biro@.
// normalize('NFD') is one of the Unicode helpers the n8n Code sandbox has.
function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function armFor(leadId) {
  const n = Math.abs(parseInt(leadId, 10));
  if (!Number.isInteger(n)) throw new Error('no lead_id to assign an arm from: ' + JSON.stringify(leadId));
  return n % 2 === 0 ? ARM_BY_PARITY.even : ARM_BY_PARITY.odd;
}

// --- ALL-CAPS names ----------------------------------------------------------
//
// "KLIXAR" and "ASCEND CLINICAL" are shouted names; "KCR" and "MCRA" are
// acronyms and must stay as they are. A word is left alone when it reads as an
// acronym: three letters or fewer, or at most one vowel. Only a name with no
// lowercase letter at all is touched -- "ONCOtherapeutics" was cased that way
// on purpose.
function isAcronym(word) {
  const letters = word.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, '');
  if (letters.length <= 3) return true;
  return (letters.match(/[AEIOUaeiouÀ-ÆÈ-ÏÒ-ÖØ-Ýà-æè-ïò-öø-ý]/g) || []).length <= 1;
}

function titleWord(word) {
  return word.split(/([-&'/.])/).map(function (part) {
    if (!/[A-Za-zÀ-ÖØ-öø-ÿ]/.test(part) || isAcronym(part)) return part;
    return part.charAt(0) + part.slice(1).toLowerCase();
  }).join('');
}

function fixCaps(text) {
  const s = str(text);
  if (!/[A-ZÀ-ÖØ-Þ]/.test(s) || /[a-zß-öø-ÿ]/.test(s)) return s;
  return s.split(/(\s+)/).map(function (w) { return /\s/.test(w) ? w : titleWord(w); }).join('');
}

// A person's name or a city has no acronyms: "JOHN" is John and "SAO PAULO" is
// Sao Paulo, whatever their vowels say.
function fixCapsPlain(text) {
  const s = str(text);
  if (!/[A-ZÀ-ÖØ-Þ]/.test(s) || /[a-zß-öø-ÿ]/.test(s)) return s;
  return s.split(/([\s\-'])/).map(function (w) {
    return /^[\s\-']$/.test(w) || !w ? w : w.charAt(0) + w.slice(1).toLowerCase();
  }).join('');
}

// --- the company name ----------------------------------------------------------

function cleanCompany(raw, fallback) {
  const original = str(raw).replace(/\s+/g, ' ');
  let s = original;
  for (let guard = 0; guard < 8; guard++) {
    const before = s;
    s = s.replace(/\s*\([^()]*\)\s*$/, '');          // "(AGI)", "(CRO Ltd.)", "(Pty)"
    s = s.replace(LEGAL_FORM_RE, '');
    s = s.replace(/[\s,\-–]+$/, '');
    if (s === before) break;
  }
  s = s.replace(LEGAL_PREFIX_RE, '');
  // Never strip a name down to nothing: a company called "Holding AG" keeps it.
  if (!/[A-Za-zÀ-ÖØ-öø-ÿ0-9]/.test(s)) s = original;
  s = fixCaps(s);
  return s || str(fallback);
}

// "{company}'s" -- an apostrophe alone after a name that ends in s: "MedTrials'
// website", not "MedTrials's website".
function possessive(name) {
  return /s$/i.test(name) ? name + "'" : name + "'s";
}

// --- the facts -----------------------------------------------------------------

function canonicalAreas(raw) {
  const kept = [];
  for (const v of Array.isArray(raw) ? raw : []) {
    if (v === null || v === undefined) continue;
    const canon = AREA_BY_KEY[String(v).trim().toLowerCase()];
    if (canon && UNSPECIFIC_AREAS.indexOf(canon) === -1 && kept.indexOf(canon) === -1) kept.push(canon);
  }
  // Taxonomy order, whatever order the extraction listed them in, so a redraft
  // picks the same two areas as the draft it replaces. The locked taxonomy
  // starts with Oncology, so oncology comes first when present -- Section 12's
  // strongest signal.
  return kept.sort(function (a, b) {
    return THERAPEUTIC_AREAS.indexOf(a) - THERAPEUTIC_AREAS.indexOf(b);
  }).slice(0, MAX_AREAS).map(function (a) { return a.toLowerCase(); });
}

function joinAnd(list) {
  if (list.length <= 1) return list.join('');
  return list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
}

const ROMAN = ['', 'I', 'II', 'III', 'IV'];

// "Phase I to III". A range is only written when every phase in it is listed:
// a lead listing I and III is "Phase I and III" -- writing "I to III" would
// claim a Phase II nobody recorded. Bioequivalence is not a phase number and is
// left out.
function formatPhases(raw) {
  const nums = [];
  for (const v of Array.isArray(raw) ? raw : []) {
    const m = /^phase\s*(iv|iii|ii|i|[1-4])$/i.exec(str(v));
    if (!m) continue;
    const t = m[1].toUpperCase();
    const n = /^\d$/.test(t) ? Number(t) : ROMAN.indexOf(t);
    if (nums.indexOf(n) === -1) nums.push(n);
  }
  nums.sort(function (a, b) { return a - b; });
  if (!nums.length) return null;
  if (nums.length === 1) return 'Phase ' + ROMAN[nums[0]];
  if (nums[nums.length - 1] - nums[0] + 1 === nums.length) {
    return 'Phase ' + ROMAN[nums[0]] + ' to ' + ROMAN[nums[nums.length - 1]];
  }
  return 'Phase ' + joinAnd(nums.map(function (n) { return ROMAN[n]; }));
}

function cleanCity(raw) {
  return fixCapsPlain(str(raw).replace(/\s+/g, ' '));
}

// --- the recruiting-trial count ------------------------------------------------
//
// ClinicalTrials.gov's query.spons is a full-text match on the sponsor and
// collaborator fields, not an identity check, so its totalCount is not a fact
// about the lead. Measured 2026-10-19 on the 30 leads redrafted that day:
// "Therapeutics" matched 1,175 recruiting trials (every "... Therapeutics"),
// "A+ Science" six trials of Chinese hospitals, "JSS Medical Research" an
// unrelated JSS college, "CRO Group" a Tigermed company. Printed in the
// template, each would be a false sentence under the sender's name.
//
// So a study counts only when its lead sponsor or a collaborator IS the
// company: the same name once both are folded, stripped of legal forms and
// punctuation, or the company's name followed only by corporate filler
// ("Innovaderm Research Inc." is Innovaderm). And when the lookup returned
// fewer studies than it matched, the count cannot be verified at all and is
// not used -- no small CRO has a thousand recruiting trials of its own.
const SPONSOR_FILLER = ['research', 'clinical', 'group', 'services', 'international', 'global', 'company',
  'corporation', 'cro', 'inc', 'ltd', 'limited', 'llc', 'sa', 'srl', 'co', 'pvt', 'private', 'gmbh', 'ag', 'bv'];

function sponsorKey(name) {
  let s = str(name);
  for (let guard = 0; guard < 8; guard++) {
    const before = s;
    s = s.replace(/\s*\([^()]*\)\s*$/, '').replace(LEGAL_FORM_RE, '').replace(/[\s,\-–]+$/, '');
    if (s === before) break;
  }
  return fold(s).replace(/[^a-z0-9]+/g, ' ').trim();
}

function isSponsor(companyName, sponsorName) {
  const c = sponsorKey(companyName);
  const s = sponsorKey(sponsorName);
  if (!c || !s) return false;
  if (s === c) return true;
  if (s.indexOf(c + ' ') !== 0) return false;
  return s.slice(c.length + 1).split(' ').every(function (w) { return SPONSOR_FILLER.indexOf(w) !== -1; });
}

// The verified count, or 0 when nothing could be verified. `ct` is the
// ClinicalTrials.gov answer (studies with their sponsor module).
function verifiedTrials(companyName, ct) {
  const studies = Array.isArray(ct && ct.studies) ? ct.studies : [];
  if (!(typeof ct.totalCount === 'number') || ct.totalCount > studies.length) return 0;
  return studies.filter(function (st) {
    const m = (st && st.protocolSection && st.protocolSection.sponsorCollaboratorsModule) || {};
    const names = [(m.leadSponsor || {}).name].concat((m.collaborators || []).map(function (x) { return x && x.name; }));
    return names.some(function (n) { return isSponsor(companyName, n); });
  }).length;
}

// The SMYKM fact: the first the lead has, in SMYKM_ORDER.
function pickSmykm(facts) {
  for (const kind of SMYKM_ORDER) {
    if (kind === 'trials' && Number.isInteger(facts.trials) && facts.trials > 0) return 'trials';
    if (kind === 'areas' && facts.areas && facts.areas.length) return 'areas';
    if (kind === 'city' && str(facts.city)) return 'city';
    if (kind === 'company') return 'company';
  }
  return 'none';
}

// --- addressing -------------------------------------------------------------------

function localPart(email) {
  const e = str(email).toLowerCase();
  const at = e.indexOf('@');
  return at > 0 ? e.slice(0, at) : '';
}

function isRoleInbox(email) {
  const lp = localPart(email);
  if (!lp) return false;
  const bare = lp.replace(/[^a-z0-9]+/g, '');
  return ROLE_LOCALPARTS.indexOf(lp) !== -1 || ROLE_LOCALPARTS.indexOf(bare) !== -1;
}

// Does this address belong to the person we have a name for? An actual token
// overlap between the local part and the name.
function addressIsPersons(email, name) {
  const lp = fold(localPart(email)).replace(/[^a-z]+/g, ' ').trim();
  if (!lp) return false;
  const nameTokens = fold(name).split(/[^a-z]+/).filter(function (t) { return t.length >= 3; });
  if (!nameTokens.length) return false;
  const lpTokens = lp.split(/\s+/).filter(Boolean);
  return nameTokens.some(function (t) { return lpTokens.indexOf(t) !== -1; });
}

// The first name, or '' when there is none worth greeting: honorifics skipped,
// a bare initial ("J. Smith") is not a name, an ALL-CAPS name is fixed.
function firstName(name) {
  const parts = str(name).replace(/,.*$/, '').split(/\s+/).filter(Boolean);
  while (parts.length > 1 && HONORIFICS.indexOf(parts[0].toLowerCase().replace(/\.+$/, '')) !== -1) parts.shift();
  const first = (parts[0] || '').replace(/[.,;:]+$/, '');
  if (HONORIFICS.indexOf(first.toLowerCase()) !== -1) return '';
  if (first.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, '').length < 2) return '';
  return fixCapsPlain(first);
}

// named | role-inbox | unnamed | unaddressed -- and the greeting that follows.
function addressing(email, contactName) {
  const e = str(email);
  if (!e) return 'unaddressed';
  if (isRoleInbox(e)) return 'role-inbox';
  if (str(contactName) && addressIsPersons(e, contactName) && firstName(contactName)) return 'named';
  return 'unnamed';
}

function greeting(email, contactName) {
  return addressing(email, contactName) === 'named'
    ? GREETING_NAMED.replace('{first_name}', firstName(contactName))
    : GREETING_UNNAMED;
}

// --- filling a template --------------------------------------------------------

function article(word) {
  return /^[aeiou]/i.test(fold(word)) ? 'an' : 'a';
}

function plural(n, one, many) {
  return n === 1 ? one : many;
}

// The grammar-carrying placeholders first, then the plain ones. Throws if any
// placeholder is left: a "{company}" reaching a prospect is the merge-tag tell
// this whole template exists to avoid.
function fill(template, v) {
  let s = template;
  if (s.indexOf('{area1}') !== -1) s = s.replace('a {area1}', article(v.area1) + ' {area1}');
  if (s.indexOf('{n} trial(s)') !== -1) s = s.split('{n} trial(s)').join(v.n + ' ' + plural(v.n, 'trial', 'trials'));
  if (s.indexOf('{n} recruiting trial(s)') !== -1) {
    s = s.split('{n} recruiting trial(s)').join(v.n + ' recruiting ' + plural(v.n, 'trial', 'trials'));
  }
  // "from" only pairs with a range ("from Phase I to III"); a single phase, or
  // a gap, reads "in Phase II" (operator, 2026-10-19).
  if (s.indexOf(" from {phases}") !== -1 && !v.phases) s = s.split(' from {phases}').join('');
  if (s.indexOf(" from {phases}") !== -1 && !/ to /.test(v.phases)) s = s.split(' from {phases}').join(' in {phases}');
  s = s.split("{company}'s").join(v.company !== undefined ? possessive(v.company) : "{company}'s");
  const keys = ['company', 'areas', 'area1', 'phases', 'city', 'n', 'fact', 'first_name'];
  keys.forEach(function (k) {
    if (v[k] !== undefined && v[k] !== null) s = s.split('{' + k + '}').join(String(v[k]));
  });
  const left = s.match(/\{[a-z0-9_]+\}/i);
  if (left) throw new Error('template placeholder ' + left[0] + ' was not filled: ' + JSON.stringify(template));
  return s;
}

// --- the gate -------------------------------------------------------------------
//
// Section 9, Workflow 5 since 2026-10-19: an email approves itself unless one
// of three things is true -- nowhere to send it, no Send clock for its country,
// or a blocklisted domain -- or the operator has turned auto-approval off.
// Nothing about the WORDS is checked any more: they are the approved template.
// `clockKeys` is Section 12's country list, lowercased (the node body has it).
function holdTags(ctx, clockKeys) {
  const tags = [];
  if (!EMAIL_RE.test(str(ctx.email))) tags.push('no-address');
  else if (ctx.verified !== true) tags.push('unverified-address');
  if ((clockKeys || []).indexOf(str(ctx.country).toLowerCase()) === -1) tags.push('no-send-clock');
  if (ctx.lead_domain_blocked === true || ctx.recipient_domain_blocked === true) tags.push('blocklisted');
  return tags;
}

function gate(ctx, clockKeys) {
  const tags = holdTags(ctx, clockKeys);
  const blocked = ctx.lead_domain_blocked === true ? str(ctx.domain) : str(ctx.email).split('@')[1];
  const reasons = tags.map(function (t) {
    return HOLD_REASONS[t].replace('{country}', str(ctx.country) || '(no country)')
      .replace('{domain}', str(blocked) || 'the domain');
  });
  if (!tags.length && ctx.auto_approve !== true) reasons.push(AUTO_APPROVE_OFF);
  const approved = !reasons.length;
  return {
    tags: tags,
    status: approved ? 'approved' : 'pending',
    approved_by: approved ? 'auto' : null,
    hold_reason: approved ? null : reasons.join('; '),
  };
}

// --- the messages ---------------------------------------------------------------

// The facts a first touch may use, from the batch row and the trial count.
function leadFacts(lead, trialCount) {
  return {
    trials: Number.isInteger(trialCount) ? trialCount : 0,
    areas: canonicalAreas(lead.therapeutic_areas),
    phases: formatPhases(lead.phases),
    city: cleanCity(lead.city),
  };
}

function smykmLine(kind, arm, company, facts) {
  if (kind === 'trials') return fill(arm === 'A' ? SMYKM_TRIALS_A : SMYKM_TRIALS_B, { company: company, n: facts.trials });
  if (kind === 'areas') {
    return fill(arm === 'A' ? SMYKM_AREAS_A : SMYKM_AREAS_B, {
      company: company, areas: joinAnd(facts.areas), area1: facts.areas[0], phases: facts.phases,
    });
  }
  if (kind === 'city') return fill(SMYKM_CITY, { company: company, city: facts.city });
  if (kind === 'company') return fill(SMYKM_COMPANY, { company: company });
  return null;
}

function subjectLine(kind, company, facts) {
  if (kind === 'trials') return fill(SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_TRIALS, { n: facts.trials }) });
  if (kind === 'areas') return fill(SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_AREAS, { areas: joinAnd(facts.areas) }) });
  if (kind === 'city') return fill(SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_CITY, { city: facts.city }) });
  return fill(SUBJECT_NO_FACT, { company: company });
}

function closeFor(arm, company) {
  return fill(arm === 'A' ? CLOSE_A : CLOSE_B, { company: company });
}

// One first touch, as text. `t` is { lead_id, company_name, domain, email,
// contact_name, facts }. The variant records the arm, the SMYKM choice and the
// addressing: 'template/A/trials/role-inbox'.
function renderFirstTouch(t) {
  const arm = armFor(t.lead_id);
  const company = cleanCompany(t.company_name, t.domain);
  const kind = pickSmykm(t.facts);
  const smykm = smykmLine(kind, arm, company, t.facts);
  const lines = [greeting(t.email, t.contact_name), '', INTRO_LINE, ''];
  if (smykm) lines.push(smykm, '');
  lines.push(PITCH_LINE, '', closeFor(arm, company), '');
  return {
    arm: arm,
    smykm: kind,
    addressing: addressing(t.email, t.contact_name),
    company: company,
    subject: subjectLine(kind, company, t.facts),
    body: lines.concat(SIGN_OFF).join('\n'),
    variant: 'template/' + arm + '/' + kind + '/' + addressing(t.email, t.contact_name),
  };
}

// A follow-up, as text: the note, the sign-off, then the first email quoted
// (threading as before 2026-10-19 -- Re: the first subject, the first email
// below "On <date>, Abdullah Amir wrote:"). `f` is { lead_id, n, company_name,
// domain, email, contact_name, first_subject, first_body, first_sent_at }.
function renderFollowUp(f) {
  const n = Number(f.n);
  if (n !== 1 && n !== 2) throw new Error('no follow-up template #' + JSON.stringify(f.n));
  const arm = armFor(f.lead_id);
  const company = cleanCompany(f.company_name, f.domain);
  const lines = [greeting(f.email, f.contact_name), '', fill(n === 1 ? FOLLOW_UP_1_LINE : FOLLOW_UP_2_LINE, { company: company }), ''];
  if (n === 1) lines.push(closeFor(arm, company), '');
  const firstBody = String(f.first_body || '').replace(/\r\n?/g, '\n').trim();
  const sent = new Date(f.first_sent_at);
  const when = isNaN(sent.getTime()) ? 'earlier' : sent.toUTCString().slice(0, 16);
  const quoted = firstBody.split('\n').map(function (ln) { return ln ? '> ' + ln : '>'; }).join('\n');
  return {
    arm: arm,
    company: company,
    subject: 'Re: ' + str(f.first_subject).replace(/^(?:re:\s*)+/i, ''),
    body: lines.concat(SIGN_OFF, ['', 'On ' + when + ', ' + SIGN_OFF[1] + ' wrote:', quoted]).join('\n'),
    variant: 'follow-up-' + n + '/template/' + arm,
  };
}

function withTags(variant, tags) {
  return tags.length ? variant + '+' + tags.join(',') : variant;
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

// Section 12's business-hours clocks as country names, substituted at build
// time from the doc's own table -- the table Send's COUNTRY_CLOCKS is checked
// against, so the gate and Send can never disagree about a lead.
const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;
const CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

const lead = $('Get Draft Batch').item.json;
const ct = $input.item.json;

// Did ClinicalTrials.gov answer? If not, nothing is written and the lead stays
// 'contact_found' for the next run: drafting without the count would open on a
// weaker fact than the lead has, for good, because of a transient outage.
// JSON.stringify, not String(): n8n wraps an HTTP failure as an object.
if (!ct || typeof ct !== 'object' || ct.error || typeof ct.totalCount !== 'number') {
  const err = ct && (ct.error || ct.message);
  return {
    json: {
      lead_id: lead.lead_id,
      ok: false,
      skip_reason: 'ClinicalTrials.gov lookup did not answer: ' +
        (err ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300) : 'no numeric totalCount in the response'),
    },
  };
}

// The template is for CROs. Get Draft Batch never selects a site or an SMO --
// they wait at 'contact_found' -- so reaching this line means the query
// changed. Nothing is written for it.
if (str(lead.company_type) !== 'CRO') {
  return {
    json: {
      lead_id: lead.lead_id,
      ok: false,
      skip_reason: 'not a CRO (' + str(lead.company_type) + '): sites and SMOs are not drafted by the template',
    },
  };
}

const facts = leadFacts(lead, verifiedTrials(lead.company_name, ct));
const draft = renderFirstTouch({
  lead_id: lead.lead_id,
  company_name: lead.company_name,
  domain: lead.domain,
  email: lead.email,
  contact_name: lead.contact_name,
  facts: facts,
});
const decision = gate({
  auto_approve: lead.auto_approve_email === true,
  email: lead.email,
  verified: lead.verified === true,
  country: lead.country,
  domain: lead.domain,
  lead_domain_blocked: lead.lead_domain_blocked === true,
  recipient_domain_blocked: lead.recipient_domain_blocked === true,
}, CLOCK_KEYS);

return {
  json: {
    lead_id: lead.lead_id,
    ok: true,
    company: draft.company,
    arm: draft.arm,
    smykm: draft.smykm,
    addressing: draft.addressing,
    facts: facts,
    // What ClinicalTrials.gov matched, and how many of those are this
    // company's own -- the number the template is allowed to print.
    trials_matched: ct.totalCount,
    trials_verified: facts.trials,
    decision: decision,
    payload: {
      lead_id: lead.lead_id,
      drafts: [{
        channel: 'email',
        variant: withTags(draft.variant, decision.tags),
        subject: draft.subject,
        body: draft.body,
        status: decision.status,
        approved_by: decision.approved_by,
        hold_reason: decision.hold_reason,
      }],
    },
  },
};

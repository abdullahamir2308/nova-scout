// Unit tests for code_template.js -- the fixed first-touch and follow-up
// templates (operator brief 2026-10-19; Master Ref Section 9, Workflows 4-6).
//
// What is pinned here: the approved wording, byte for byte, in every arm and
// every SMYKM branch; which fact is chosen and in what order; the A/B split;
// every formatting rule (plural, areas, phases, the company name, ALL-CAPS,
// first names and role inboxes, a/an, the possessive); and the gate -- the only
// three holds left, plus the operator's switch. test_template_mutations.py then
// breaks each rule in the source and proves this file fails.

const path = require('path');
const { extractFunctions, runForEachItem, runner } = require('./harness');

const TEMPLATE = path.join(__dirname, 'code_template.js');
const CLOCKS = ['Turkey', 'India', 'USA', 'Poland', 'Mexico', 'Romania', 'Argentina'];
const bake = (src) => src.replace('__SEND_CLOCK_COUNTRIES__', JSON.stringify(CLOCKS));

const F = extractFunctions(TEMPLATE, '// Node body', [
  'armFor', 'cleanCompany', 'possessive', 'canonicalAreas', 'formatPhases', 'cleanCity', 'pickSmykm',
  'isRoleInbox', 'addressIsPersons', 'firstName', 'addressing', 'greeting', 'fill', 'gate', 'holdTags',
  'leadFacts', 'renderFirstTouch', 'renderFollowUp', 'withTags', 'article', 'fixCaps', 'isSponsor', 'verifiedTrials',
  'SIGN_OFF', 'INTRO_LINE', 'PITCH_LINE', 'CLOSE_A', 'CLOSE_B', 'EMAIL_RE',
]);
const CLOCK_KEYS = CLOCKS.map((c) => c.toLowerCase());

const t = runner('code_template.js -- the fixed templates, the SMYKM line, the formatting rules, the gate');

// The approved text, typed out here once more on purpose: if the source and
// this file both drift the same way, the build's Master Ref guard still fires.
const INTRO = "We have yet to be properly introduced, but I'm Abdullah Amir and I'm the Founder of Amitrix Labs.";
const PITCH = "That's precisely what we do at Amitrix Labs. We build AI agents for CRO websites that answer sponsor and " +
  'visitor questions, collect their details, and turn more of your visitors into qualified leads, so you win more studies.';
const CLOSE_A = 'Do you have time over the next week or two to learn more? Let me know what works for you, and ' +
  "I'll send a calendar invite along accordingly.";
const SIGN = 'Cheers!\nAbdullah Amir\nFounder, Amitrix Labs';
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

function lead(over) {
  return Object.assign({
    lead_id: 104, company_name: 'Atlant Clinical Ltd', domain: 'atlantclinical.com', country: 'Turkey',
    email: 'contact@atlantclinical.com', contact_name: '', verified: true,
    therapeutic_areas: ['Cardiovascular', 'Oncology', 'Hematology'], phases: ['Phase I', 'Phase II', 'Phase III'],
    city: 'Istanbul', company_type: 'CRO', auto_approve_email: true,
    lead_domain_blocked: false, recipient_domain_blocked: false,
  }, over || {});
}
function first(over, trials) {
  const l = lead(over);
  return F.renderFirstTouch({
    lead_id: l.lead_id, company_name: l.company_name, domain: l.domain, email: l.email,
    contact_name: l.contact_name, facts: F.leadFacts(l, trials === undefined ? 0 : trials),
  });
}

// --- 1. The approved wording, whole ---------------------------------------------

{
  const d = first({}, 3);   // lead 104: even -> arm A; 3 recruiting trials -> the trials line
  t.check('arm A, trials: the subject', d.subject, 'Atlant Clinical + 3 recruiting trials + sponsor inquiries');
  t.check('arm A, trials: the whole body, byte for byte', d.body, [
    'Hi there,', '', INTRO, '',
    'With 3 trials recruiting right now, sponsors are clearly paying attention to Atlant Clinical, and every one ' +
      'who visits your website is a potential next study worth turning into a conversation.', '',
    PITCH, '', CLOSE_A, '', SIGN,
  ].join('\n'));
  t.check('arm A, trials: variant records the arm, the SMYKM choice and the addressing', d.variant,
    'template/A/trials/role-inbox');
}
{
  const d = first({ lead_id: 91, company_name: 'BiTrial', email: 'anna.kowalska@bitrial.pl', contact_name: 'Anna Kowalska' }, 1);
  t.check('arm B, trials (1): the subject is singular', d.subject, 'BiTrial + 1 recruiting trial + sponsor inquiries');
  t.check('arm B, trials (1): the whole body, byte for byte', d.body, [
    'Hi Anna,', '', INTRO, '',
    'BiTrial has 1 trial recruiting right now. Each sponsor who checks your website after seeing that is a lead ' +
      'worth capturing.', '',
    PITCH, '', 'Worth a look for BiTrial?', '', SIGN,
  ].join('\n'));
  t.check('arm B, trials: variant', d.variant, 'template/B/trials/named');
}
{
  const d = first({}, 0);
  t.check('arm A, areas with phases: the SMYKM line', d.body.split('\n\n')[2],
    'Atlant Clinical covers oncology and cardiovascular from Phase I to III, so sponsors visiting your website ' +
    'arrive with very different questions, and each one is a lead worth capturing.');
  t.check('arm A, areas: the subject carries the same two areas', d.subject,
    'Atlant Clinical + oncology and cardiovascular + sponsor inquiries');
  t.check('arm A, areas: variant', d.variant, 'template/A/areas/role-inbox');
}
{
  const d = first({ phases: ['Bioequivalence'] }, 0);
  t.check('arm A, areas with no known phase: "from {phases}" is dropped', d.body.split('\n\n')[2],
    'Atlant Clinical covers oncology and cardiovascular, so sponsors visiting your website arrive with very ' +
    'different questions, and each one is a lead worth capturing.');
}
{
  const d = first({ lead_id: 105 }, 0);
  t.check('arm B, areas: the SMYKM line, "an" before oncology', d.body.split('\n\n')[2],
    "A sponsor planning an oncology study will usually check Atlant Clinical's website before they ever email " +
    'you. That visit is a lead worth capturing.');
  t.check('arm B, areas: the close names the company', d.body.split('\n\n')[4], 'Worth a look for Atlant Clinical?');
}
{
  const d = first({ lead_id: 105, therapeutic_areas: ['Dermatology'] }, 0);
  t.check('arm B, areas: "a" before a consonant', d.body.split('\n\n')[2].slice(0, 41),
    'A sponsor planning a dermatology study wi');
}
{
  const d = first({ therapeutic_areas: [], phases: [] }, 0);
  t.check('city (arm A): the SMYKM line', d.body.split('\n\n')[2],
    "Sponsors looking for a CRO in Istanbul will find Atlant Clinical's website, and every one of those visits is " +
    'a potential study worth turning into a conversation.');
  t.check('city: the subject', d.subject, 'Atlant Clinical + Istanbul + sponsor inquiries');
  const b = first({ lead_id: 105, therapeutic_areas: [], phases: [] }, 0);
  t.check('city: one sentence for both arms', b.body.split('\n\n')[2], d.body.split('\n\n')[2]);
  t.check('city: variant', b.variant, 'template/B/city/role-inbox');
}
{
  const d = first({ therapeutic_areas: ['pharma', 'Other'], phases: [], city: '' }, 0);
  t.check('no other fact: the company line, the whole body byte for byte', d.body, [
    'Hi there,', '', INTRO, '',
    'Sponsors evaluating Atlant Clinical will usually start on your website, and every one of those visits is a ' +
      'potential study worth turning into a conversation.', '',
    PITCH, '', CLOSE_A, '', SIGN,
  ].join('\n'));
  t.check('no other fact: the subject has no fact', d.subject, 'Atlant Clinical + sponsor inquiries');
  t.check('no other fact: variant', d.variant, 'template/A/company/role-inbox');
  const b = first({ lead_id: 105, therapeutic_areas: [], phases: [], city: '' }, 0);
  t.check('company line: one sentence for both arms', b.body.split('\n\n')[2], d.body.split('\n\n')[2]);
  t.check('company line: arm B keeps its own close', b.body.split('\n\n')[4], 'Worth a look for Atlant Clinical?');
  t.check('company line: variant B', b.variant, 'template/B/company/role-inbox');
}
{
  const d = first({ company_name: 'MEDTRIALS S.A.', therapeutic_areas: [], phases: [], city: '' }, 0);
  t.check('company line: the cleaned company name', d.body.split('\n\n')[2].slice(0, 41),
    'Sponsors evaluating Medtrials will usuall');
}
{
  const d = first({ phases: ['Phase II'] }, 0);
  t.check('arm A, areas with a single phase: "in Phase II", not "from"', d.body.split('\n\n')[2],
    'Atlant Clinical covers oncology and cardiovascular in Phase II, so sponsors visiting your website arrive ' +
    'with very different questions, and each one is a lead worth capturing.');
}
{
  const d = first({ phases: ['Phase I', 'Phase III'] }, 0);
  t.check('arm A, areas with a gap: "in Phase I and III" -- "from" only before a range', d.body.split('\n\n')[2],
    'Atlant Clinical covers oncology and cardiovascular in Phase I and III, so sponsors visiting your website ' +
    'arrive with very different questions, and each one is a lead worth capturing.');
}
{
  const d = first({ phases: ['Phase II', 'Phase III'] }, 0);
  t.check('arm A, areas with a two-phase range: "from Phase II to III"', d.body.split('\n\n')[2].indexOf(
    'Atlant Clinical covers oncology and cardiovascular from Phase II to III, so') === 0, true);
}

// --- 2. The A/B split -------------------------------------------------------------

t.check('even lead_id -> A', [0, 2, 104, 2050].map(F.armFor), ['A', 'A', 'A', 'A']);
t.check('odd lead_id -> B', [1, 91, 105, 2077].map(F.armFor), ['B', 'B', 'B', 'B']);
t.check('a lead_id arriving as a string (Postgres bigint) -> the same arm', [F.armFor('104'), F.armFor('91')], ['A', 'B']);
t.check('arm A closes with the calendar question', first({ lead_id: 8 }, 0).body.indexOf(CLOSE_A) !== -1, true);
t.check('arm B closes with "Worth a look for {company}?"',
  first({ lead_id: 9 }, 0).body.indexOf('Worth a look for Atlant Clinical?') !== -1 &&
  first({ lead_id: 9 }, 0).body.indexOf(CLOSE_A) === -1, true);
{
  let threw = false;
  try { F.armFor(null); } catch (e) { threw = true; }
  t.check('no lead_id -> no arm (throws rather than guess)', threw, true);
}

// --- 3. The SMYKM choice, in order -----------------------------------------------

t.check('trials beat areas and city', F.pickSmykm({ trials: 2, areas: ['oncology'], city: 'Istanbul' }), 'trials');
t.check('no trials -> areas', F.pickSmykm({ trials: 0, areas: ['oncology'], city: 'Istanbul' }), 'areas');
t.check('no trials, no areas -> city', F.pickSmykm({ trials: 0, areas: [], city: 'Istanbul' }), 'city');
t.check('nothing else -> company', F.pickSmykm({ trials: 0, areas: [], city: '' }), 'company');
t.check('a missing count is not a trial', F.pickSmykm({ trials: null, areas: [], city: 'Rome' }), 'city');

// --- 4. Formatting rules ------------------------------------------------------------

t.check('areas: at most two, lowercase, oncology first, then taxonomy order',
  F.canonicalAreas(['Rare Diseases', 'Hematology', 'Oncology', 'Cardiovascular']), ['oncology', 'cardiovascular']);
t.check('areas: case-insensitive against the locked taxonomy; free text and Other dropped',
  F.canonicalAreas(['pharma', 'medtech', 'cardiology', 'other', 'INFECTIOUS DISEASE', 'rare diseases']),
  ['infectious disease', 'rare diseases']);
t.check('areas: one area alone', F.canonicalAreas(['Dermatology']), ['dermatology']);
t.check('areas joined with "and"', F.fill('{areas}', { areas: 'oncology and hematology' }), 'oncology and hematology');
t.check('phases: a full run is a range', F.formatPhases(['Phase I', 'Phase II', 'Phase III']), 'Phase I to III');
t.check('phases: I to IV', F.formatPhases(['Phase IV', 'Phase I', 'Phase III', 'Phase II', 'Bioequivalence']), 'Phase I to IV');
t.check('phases: one phase', F.formatPhases(['Phase II']), 'Phase II');
t.check('phases: a gap is never written as a range', F.formatPhases(['Phase I', 'Phase III']), 'Phase I and III');
t.check('phases: a gap among three', F.formatPhases(['Phase I', 'Phase II', 'Phase IV']), 'Phase I, II and IV');
t.check('phases: Bioequivalence alone is no phase', F.formatPhases(['Bioequivalence']), null);
t.check('phases: lowercase and arabic numerals read too', F.formatPhases(['phase ii', 'Phase 3']), 'Phase II to III');
t.check('phases: none', F.formatPhases(null), null);
t.check('fill: a single phase -> "in"', F.fill('x from {phases}.', { phases: 'Phase II' }), 'x in Phase II.');
t.check('fill: a range keeps "from"', F.fill('x from {phases}.', { phases: 'Phase I to III' }), 'x from Phase I to III.');
t.check('fill: no phase drops "from {phases}"', F.fill('x from {phases}.', { phases: null }), 'x.');
t.check('plural: 1 trial', F.fill('{n} trial(s)', { n: 1 }), '1 trial');
t.check('plural: 2 trials', F.fill('{n} trial(s)', { n: 2 }), '2 trials');
t.check('plural in the subject: 1 recruiting trial', F.fill('{n} recruiting trial(s)', { n: 1 }), '1 recruiting trial');
t.check('possessive: apostrophe-s', F.possessive('KCR'), "KCR's");
t.check('possessive: a name ending in s takes the apostrophe alone', F.possessive('MedTrials'), "MedTrials'");
t.check('article: an before a vowel, a before a consonant',
  [F.article('oncology'), F.article('immunology'), F.article('rare diseases'), F.article('hematology')],
  ['an', 'an', 'a', 'a']);
{
  let threw = false;
  try { F.fill('Hi {first_name}, {company}', { company: 'X' }); } catch (e) { threw = true; }
  t.check('an unfilled placeholder throws -- a merge tag never reaches a prospect', threw, true);
}

// The company name: every real shape found in leads.company_name on 2026-10-19.
const COMPANIES = [
  ['A-Pharma s.r.o.', 'A-Pharma'],
  ['Axon Clinical, s.r.o', 'Axon Clinical'],
  ['Clinstile, S.A. de C.V', 'Clinstile'],
  ['Cryptex Investigación Clínica S.A. de C.V', 'Cryptex Investigación Clínica'],
  ['Centro de Investigacion Pergamino SA', 'Centro de Investigacion Pergamino'],
  ['Stat Research S.A', 'Stat Research'],
  ['Lambda Therapeutic Research Limited', 'Lambda Therapeutic Research'],
  ['Invitro Research Solutions Private Limited', 'Invitro Research Solutions'],
  ['CliniExperts Research Services Pvt Ltd.', 'CliniExperts Research Services'],
  ['NovoBliss Research Pvt.Ltd', 'NovoBliss Research'],
  ['Biointelect Pty Ltd', 'Biointelect'],
  ['Key Oncologics (Pty) Ltd', 'Key Oncologics'],
  ['Clinical Research Consulting, Inc.', 'Clinical Research Consulting'],
  ['Comparative Biosciences, Inc', 'Comparative Biosciences'],
  ['Intrinsic Imaging LLC', 'Intrinsic Imaging'],
  ['Clinical Research Organization Ltd. (CRO Ltd.)', 'Clinical Research Organization'],
  ['AlphaGenesis Incorporated (AGI)', 'AlphaGenesis'],
  ['Prospective Research Services (PRS)', 'Prospective Research Services'],
  ['Linical Co., Ltd.', 'Linical'],
  ['Vital CRO Clinical Research Organization Education and Consultancy Ltd. Co.',
    'Vital CRO Clinical Research Organization Education and Consultancy'],
  ['Winicker Norimed GmbH', 'Winicker Norimed'],
  ['Klifovet AG', 'Klifovet'],
  ['Kinesis Pharma BV', 'Kinesis Pharma'],
  ['Data by Four B.V.', 'Data by Four'],
  ['Pierrel S.p.A.', 'Pierrel'],
  ['Hippocrates Research srl', 'Hippocrates Research'],
  ['Mediab S.R.L', 'Mediab'],
  ['S.C Centrul de Gastroenterologie Dr. Goldis S.R.L', 'Centrul de Gastroenterologie Dr. Goldis'],
  ['Marketing Farmacéutico & Investigación Clínica, S.L.', 'Marketing Farmacéutico & Investigación Clínica'],
  ['Coltrials SAS', 'Coltrials'],
  ['Clinexpert Kft', 'Clinexpert'],
  ['ISCARE a.s', 'Iscare'],   // all caps once the legal form is off
  ['Medicon a.S', 'Medicon'],
  ['Theradex Oncology AB', 'Theradex Oncology'],
  ['Oncosite - Centro de Pesquisa Clinica Em Oncologia Ltda', 'Oncosite - Centro de Pesquisa Clinica Em Oncologia'],
  ['Eukarya Pharmasite S.C', 'Eukarya Pharmasite'],
  ['Info Kinetics Sdn Bhd', 'Info Kinetics'],
  ['Sp. Example Sp. z o.o.', 'Sp. Example'],
  // a name, not a legal form -- left alone
  ['The Clinical Trial Company', 'The Clinical Trial Company'],
  ['Schiff & Company', 'Schiff & Company'],
  ['Clinicology - an FGK company', 'Clinicology - an FGK company'],
  ['ONCOtherapeutics', 'ONCOtherapeutics'],
  ['Delphinium', 'Delphinium'],
  // ALL-CAPS: a shouted name is fixed, an acronym is kept
  ['KLIXAR', 'Klixar'],
  ['FARMOVS', 'Farmovs'],
  ['ASCEND CLINICAL', 'Ascend Clinical'],
  ['PASPIGIONI', 'Paspigioni'],
  ['TRIO', 'Trio'],
  ['KCR', 'KCR'],
  ['MCRA', 'MCRA'],
  ['IAE RESEARCH', 'IAE Research'],   // three letters: an acronym, whatever its vowels
  ['A-PHARMA S.R.O.', 'A-Pharma'],
  ['C&R RESEARCH INC.', 'C&R Research'],
  ['IQVIA', 'Iqvia'],
];
COMPANIES.forEach(function (c) { t.check('company: ' + JSON.stringify(c[0]), F.cleanCompany(c[0]), c[1]); });
t.check('company: never stripped to nothing', F.cleanCompany('Ltd'), 'Ltd');
t.check('company: a missing name falls back to the domain', F.cleanCompany('', 'example-cro.com'), 'example-cro.com');
t.check('city: ALL-CAPS fixed, no acronym exception', [F.cleanCity('SAO PAULO'), F.cleanCity('  Buenos   Aires '), F.cleanCity('София')],
  ['Sao Paulo', 'Buenos Aires', 'София']);

// --- 4b. The recruiting-trial count: only the company's own trials ----------------
//
// The real answers ClinicalTrials.gov gave on 2026-10-19 for leads redrafted that
// day (query.spons is full text, so its totalCount is not about the lead).
function study(lead, collaborators) {
  return { protocolSection: { sponsorCollaboratorsModule: {
    leadSponsor: { name: lead }, collaborators: (collaborators || []).map((n) => ({ name: n })) } } };
}
t.check('sponsor: the same name', F.isSponsor('Alliance for Clinical Trials in Oncology', 'Alliance for Clinical Trials in Oncology'), true);
t.check('sponsor: case and a legal form do not matter',
  [F.isSponsor('MedTrials', 'MedTrials Incorporated'), F.isSponsor('Vedic Lifesciences', 'Vedic Lifesciences Pvt. Ltd.'),
    F.isSponsor('ONCOtherapeutics', 'Oncotherapeutics'), F.isSponsor('Delphinium', 'DELPHINIUM'),
    F.isSponsor('Donawa Lifescience Consulting', 'Donawa Lifescience Consulting S.r.l.')], [true, true, true, true, true]);
t.check('sponsor: a trailing parenthetical does not matter', F.isSponsor('MCRA', 'MCRA (an IQVIA business)'), true);
t.check('sponsor: the name followed only by corporate filler', F.isSponsor('Innovaderm', 'Innovaderm Research Inc.'), true);
t.check('sponsor: another company containing the word is NOT the company',
  [F.isSponsor('Therapeutics', 'Corcept Therapeutics'), F.isSponsor('Therapeutics', 'Therapeutics Research Institute'),
    F.isSponsor('JSS Medical Research', 'JSS MEDICAL COLLEGE, JSS ACADEMY OF HIGHER EDUCATION AND RESEARCH'),
    F.isSponsor('CRO Group', 'Opera CRO, a TIGERMED Group Company'), F.isSponsor('A+ Science', 'Xuanwu Hospital, Beijing'),
    F.isSponsor('MCRA', 'Synergy Spine Solutions')], [false, false, false, false, false, false]);
t.check('sponsor: the company\'s name must OPEN the sponsor\'s name, not sit inside it',
  F.isSponsor('Group', 'Alpha Group Research'), false);
t.check('verified count: only the studies the company sponsors or collaborates on',
  F.verifiedTrials('MCRA', { totalCount: 3, studies: [study('Synergy Spine Solutions', ['MCRA']), study('MCRA'),
    study('OsteoRemedies, LLC')] }), 2);
t.check('verified count: "Therapeutics" with 1,175 matches of other companies -> 0',
  F.verifiedTrials('Therapeutics', { totalCount: 3, studies: [study('Corcept Therapeutics'), study('Kartos Therapeutics, Inc.'),
    study('GOG Foundation', ['Atisama Therapeutics'])] }), 0);
t.check('verified count: fewer studies returned than matched -> unverifiable, 0',
  F.verifiedTrials('MCRA', { totalCount: 1175, studies: [study('MCRA')] }), 0);
t.check('verified count: nothing matched', F.verifiedTrials('KCR', { totalCount: 0, studies: [] }), 0);

// --- 5. Addressing -------------------------------------------------------------------

t.check('first name: honorific skipped', F.firstName('Dr. John S. Sampalis'), 'John');
t.check('first name: Prof and Mr skipped', [F.firstName('Prof. Anna Nowak'), F.firstName('Mr Tom Hart')], ['Anna', 'Tom']);
t.check('first name: a credential after a comma is not part of it', F.firstName('James R. Berenson, MD'), 'James');
t.check('first name: ALL-CAPS fixed, even a short one', [F.firstName('JOHN SMITH'), F.firstName('AURÉLIE WEISS-GUIMET')],
  ['John', 'Aurélie']);
t.check('first name: a bare initial is no name', [F.firstName('J. Smith'), F.firstName('J Smith')], ['', '']);
t.check('first name: an honorific alone is no name', F.firstName('Dr.'), '');
t.check('role inbox -> "Hi there," even when the record names a founder',
  F.greeting('hello@klixar.com', 'Enrique Gaubeca'), 'Hi there,');
t.check('a personal-shaped address no source attributes -> "Hi there,"',
  F.greeting('devesh.kumar@innovate-research.com', ''), 'Hi there,');
t.check("someone else's name on an address -> \"Hi there,\" (Dan Piacquadio is not ddwyer@)",
  F.greeting('ddwyer@therapeuticsinc.com', 'Dan Piacquadio'), 'Hi there,');
t.check('the address is the named person\'s -> "Hi {first_name},"',
  F.greeting('andras.kovacs@pharmahungary.hu', 'Dr. András Kovács'), 'Hi András,');
t.check('a diacritic-free local part still matches the name', F.addressIsPersons('janos.biro@x.hu', 'János Bíró'), true);
t.check('addressing modes', [F.addressing('', 'X Y'), F.addressing('info@x.com', ''), F.addressing('z.q@x.com', ''),
  F.addressing('maria.lopez@x.com', 'María López')], ['unaddressed', 'role-inbox', 'unnamed', 'named']);
t.check('a LinkedIn-only contact (no email) is greeted "Hi there,"', first({ email: '', contact_name: 'Ana Silva' }, 0)
  .body.split('\n')[0], 'Hi there,');

// --- 6. What a template never carries --------------------------------------------------

{
  const all = [first({}, 3), first({ lead_id: 105 }, 0), first({ therapeutic_areas: [], phases: [], city: '' }, 0)];
  t.check('no opt-out sentence (Section 5, removed 2026-10-19)', all.some((d) => d.body.indexOf(OPT_OUT) !== -1), false);
  t.check('no phone number in the signature', all.some((d) => /\+?\d[\d\s()-]{7,}/.test(d.body)), false);
  t.check('the body ends with the three-line sign-off', all.every((d) => d.body.slice(-SIGN.length) === SIGN), true);
  t.check('no URL, no braces', all.some((d) => /https?:|www\.|[{}]/.test(d.body + d.subject)), false);
  t.check('the sign-off constant is three lines: Cheers!, the name, the title', F.SIGN_OFF,
    ['Cheers!', 'Abdullah Amir', 'Founder, Amitrix Labs']);
}

// --- 7. The gate ----------------------------------------------------------------------

function ctx(over) {
  return Object.assign({ auto_approve: true, email: 'info@x.com', verified: true, country: 'Turkey', domain: 'x.com',
    lead_domain_blocked: false, recipient_domain_blocked: false }, over || {});
}
t.check('everything in order -> approved by auto, no hold', F.gate(ctx(), CLOCK_KEYS),
  { tags: [], status: 'approved', approved_by: 'auto', hold_reason: null });
t.check('no address -> held no-address', F.gate(ctx({ email: '' }), CLOCK_KEYS).tags, ['no-address']);
t.check('an address that is not one -> held no-address', F.gate(ctx({ email: 'info at x dot com' }), CLOCK_KEYS).tags, ['no-address']);
t.check('unverified address -> held unverified-address', F.gate(ctx({ verified: false }), CLOCK_KEYS).status, 'pending');
t.check('unverified address -> its reason', F.gate(ctx({ verified: false }), CLOCK_KEYS).hold_reason,
  'unverified-address: the address on file is not verified (contacts.verified), so Send would refuse it');
t.check('no Send clock -> held, the country named', F.gate(ctx({ country: 'Atlantis' }), CLOCK_KEYS).hold_reason,
  'no-send-clock: Atlantis has no business-hours clock, so this email can never be sent (Section 12)');
t.check('the clock is looked up case-insensitively, as Send does', F.gate(ctx({ country: 'turkey' }), CLOCK_KEYS).status, 'approved');
t.check('blocklisted lead domain -> held, the domain named', F.gate(ctx({ lead_domain_blocked: true }), CLOCK_KEYS).hold_reason,
  'blocklisted: x.com is on the blocklist');
t.check('blocklisted recipient domain -> held, that domain named',
  F.gate(ctx({ email: 'a@other.org', recipient_domain_blocked: true }), CLOCK_KEYS).hold_reason,
  'blocklisted: other.org is on the blocklist');
t.check('several reasons at once -> every tag, every reason',
  F.gate(ctx({ email: '', country: 'Atlantis', lead_domain_blocked: true }), CLOCK_KEYS).tags,
  ['no-address', 'no-send-clock', 'blocklisted']);
t.check('auto-approval off -> held, no tag (it is not about the draft)', F.gate(ctx({ auto_approve: false }), CLOCK_KEYS),
  { tags: [], status: 'pending', approved_by: null,
    hold_reason: 'auto-approve-off: settings.auto_approve_email is off, so every email waits for a person' });
t.check('only a literal true approves: a missing flag holds', F.gate(ctx({ auto_approve: undefined }), CLOCK_KEYS).status, 'pending');
t.check('only a literal true verifies', F.gate(ctx({ verified: 't' }), CLOCK_KEYS).tags, ['unverified-address']);
t.check('tags go after + in the variant', F.withTags('template/A/company/unaddressed', ['no-address', 'blocklisted']),
  'template/A/company/unaddressed+no-address,blocklisted');
t.check('no tags, no +', F.withTags('template/A/company/named', []), 'template/A/company/named');

// --- 8. The node body ---------------------------------------------------------------------

function run(rows, cts) {
  return runForEachItem(TEMPLATE, cts.map((j) => ({ json: j })), { 'Get Draft Batch': rows.map((j) => ({ json: j })) }, bake)
    .map((o) => o.json);
}
{
  const [ok, held, failed, site] = run(
    [lead(), lead({ lead_id: 55, email: '', verified: false, contact_name: 'Dr. John S. Sampalis' }), lead({ lead_id: 9 }),
      lead({ lead_id: 3132, company_type: 'site' })],
    [{ totalCount: 2, studies: [study('Atlant Clinical'), study('Some Sponsor Inc.', ['Atlant Clinical Ltd.'])] },
      { totalCount: 0, studies: [] }, { error: { message: '502 Bad Gateway' } }, { totalCount: 0, studies: [] }]);
  t.check('node: an approvable lead -> one email, approved by auto', [ok.ok, ok.payload.drafts.length,
    ok.payload.drafts[0].channel, ok.payload.drafts[0].status, ok.payload.drafts[0].approved_by], [true, 1, 'email', 'approved', 'auto']);
  t.check('node: no LinkedIn draft is written any more', ok.payload.drafts.some((d) => d.channel === 'linkedin'), false);
  t.check('node: the variant and subject of the approved one', [ok.payload.drafts[0].variant, ok.payload.drafts[0].subject],
    ['template/A/trials/role-inbox', 'Atlant Clinical + 2 recruiting trials + sponsor inquiries']);
  t.check('node: a lead with no address -> written, held no-address',
    [held.ok, held.payload.drafts[0].status, held.payload.drafts[0].approved_by, held.payload.drafts[0].variant],
    [true, 'pending', null, 'template/B/areas/unaddressed+no-address']);
  t.check('node: ClinicalTrials.gov did not answer -> nothing written, the lead stays queued',
    [failed.ok, failed.payload, /did not answer: \{"message":"502 Bad Gateway"\}/.test(failed.skip_reason)], [false, undefined, true]);
  t.check('node: a site is not drafted by the template', [site.ok, site.payload, /not a CRO \(site\)/.test(site.skip_reason)],
    [false, undefined, true]);
  t.check('node: the facts it used are on the item', ok.facts,
    { trials: 2, areas: ['oncology', 'cardiovascular'], phases: 'Phase I to III', city: 'Istanbul' });
  t.check('node: what was matched and what was verified are both recorded', [ok.trials_matched, ok.trials_verified], [2, 2]);
}
{
  const [other] = run([lead({ company_name: 'Therapeutics', domain: 'therapeuticsinc.com' })],
    [{ totalCount: 2, studies: [study('Corcept Therapeutics'), study('Kartos Therapeutics, Inc.')] }]);
  t.check('node: trials that are another company\'s are not this lead\'s -- it opens on its areas instead',
    [other.trials_matched, other.trials_verified, other.smykm, other.payload.drafts[0].subject],
    [2, 0, 'areas', 'Therapeutics + oncology and cardiovascular + sponsor inquiries']);
}
{
  const [noClock] = run([lead({ country: 'Atlantis' })], [{ totalCount: 0 }]);
  t.check('node: a country with no clock -> written, held no-send-clock', [noClock.payload.drafts[0].status,
    noClock.payload.drafts[0].variant], ['pending', 'template/A/areas/role-inbox+no-send-clock']);
  const [unverified] = run([lead({ verified: false })], [{ totalCount: 0 }]);
  t.check('node: an address present but not verified -> held unverified-address',
    [unverified.payload.drafts[0].status, unverified.payload.drafts[0].variant],
    ['pending', 'template/A/areas/role-inbox+unverified-address']);
  const [blocked] = run([lead({ recipient_domain_blocked: true })], [{ totalCount: 0 }]);
  t.check('node: a blocklisted recipient -> held blocklisted', blocked.payload.drafts[0].variant,
    'template/A/areas/role-inbox+blocklisted');
  const [off] = run([lead({ auto_approve_email: false })], [{ totalCount: 0 }]);
  t.check('node: auto-approval off -> pending, untagged', [off.payload.drafts[0].status, off.payload.drafts[0].variant],
    ['pending', 'template/A/areas/role-inbox']);
}

// --- 9. Follow-ups ----------------------------------------------------------------------

const FIRST_BODY = 'Hi there,\n\n' + INTRO + '\n\n' + PITCH + '\n\n' + CLOSE_A + '\n\n' + SIGN;
function fu(n, over) {
  return F.renderFollowUp(Object.assign({ lead_id: 104, n: n, company_name: 'Atlant Clinical Ltd', domain: 'atlantclinical.com',
    email: 'contact@atlantclinical.com', contact_name: '', first_subject: 'Atlant Clinical + sponsor inquiries',
    first_body: FIRST_BODY, first_sent_at: '2026-10-05T10:00:00.000Z' }, over || {}));
}
const QUOTE = '\n\nOn Mon, 05 Oct 2026, Abdullah Amir wrote:\n' + FIRST_BODY.split('\n').map((l) => (l ? '> ' + l : '>')).join('\n');
t.check('follow-up #1, arm A: the whole body, byte for byte', fu(1).body, [
  'Hi there,', '',
  "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits Atlant Clinical's " +
    'website is a potential study, and we help make sure those visits turn into conversations.', '',
  CLOSE_A, '', SIGN,
].join('\n') + QUOTE);
t.check('follow-up #1, arm B: the same arm\'s close', fu(1, { lead_id: 91, company_name: 'BiTrial' }).body.split('\n\n')[2],
  'Worth a look for BiTrial?');
t.check('follow-up #2: the whole body, byte for byte, no close', fu(2).body, [
  'Hi there,', '',
  "I'll keep this short and won't follow up again after this. If turning more of Atlant Clinical's website visitors " +
    "into qualified leads is ever on your list, just reply and I'll send over the details.", '',
  SIGN,
].join('\n') + QUOTE);
t.check('follow-up: threaded as before -- Re: the first subject, never Re: Re:',
  [fu(1).subject, fu(2, { first_subject: 'Re: Re: X + sponsor inquiries' }).subject],
  ['Re: Atlant Clinical + sponsor inquiries', 'Re: X + sponsor inquiries']);
t.check('follow-up: variants record the number and the arm', [fu(1).variant, fu(2, { lead_id: 91 }).variant],
  ['follow-up-1/template/A', 'follow-up-2/template/B']);
t.check('follow-up: a named person is greeted by name',
  fu(1, { email: 'anna.kowalska@bitrial.pl', contact_name: 'Anna Kowalska' }).body.split('\n')[0], 'Hi Anna,');
t.check('follow-up: no opt-out sentence, no phone', /reply 'no'|\+\d{6,}/.test(fu(1).body.split('\n\nOn ')[0] + fu(2).body.split('\n\nOn ')[0]), false);
{
  let threw = false;
  try { fu(3); } catch (e) { threw = true; }
  t.check('follow-up: there is no #3', threw, true);
}

t.done();

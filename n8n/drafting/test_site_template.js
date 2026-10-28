// Unit tests for the research-site niche's templates (operator brief 2026-10-28;
// migration 025): code_site_template.js and code_site_followup.js as SHIPPED --
// the Code nodes are read out of the built drafting-sites.json /
// follow-ups-sites.json, so what is tested is what n8n runs (code_template.js's
// rules section included). Run n8n/drafting/build_sites_workflows.py first.
//
// Pinned: the approved wording byte for byte for a site and an SMO, both arms,
// every SMYKM branch; the strict facility-level trial match; the gate and both
// switches; the follow-ups; and that no CRO wording reaches a site email.
// test_site_template_mutations.py breaks each rule and proves this file fails.

const fs = require('fs');
const path = require('path');
const { runner } = require('./harness');

const WF = path.join(__dirname, '..', 'workflows');
function shipped(file, nodeName) {
  const wf = JSON.parse(fs.readFileSync(path.join(WF, file), 'utf8'));
  const n = wf.nodes.find((x) => x.name === nodeName);
  if (!n) throw new Error('no node ' + nodeName + ' in ' + file);
  return n.parameters.jsCode;
}
const FIRST_SRC = process.env.SITE_FIRST_SRC ? fs.readFileSync(process.env.SITE_FIRST_SRC, 'utf8')
  : shipped('drafting-sites.json', 'Build Site First Touch');
const FU_SRC = process.env.SITE_FU_SRC ? fs.readFileSync(process.env.SITE_FU_SRC, 'utf8')
  : shipped('follow-ups-sites.json', 'Build Site Follow-Up');

const lib = FIRST_SRC.slice(0, FIRST_SRC.lastIndexOf('// Node body'));
const F = new Function(lib + '\nreturn { renderSiteFirstTouch, renderSiteFollowUp, verifiedSiteTrials, leadFacts, ' +
  'facilityCore, gate, SITE_SIGN_OFF, SITE_INTRO_LINE, SITE_PITCH_LINE, CLOSE_A, CLOSE_B };')();

function runFirst(lead, ct) {
  const fn = new Function('$input', '$', '"use strict"; return (() => {' + FIRST_SRC + '})()');
  return fn({ item: { json: ct } }, () => ({ item: { json: lead } })).json;
}
function runFollowUps(rows) {
  const fn = new Function('$input', '"use strict"; return (() => {' + FU_SRC + '})()');
  return fn({ all: () => rows.map((r) => ({ json: r })) });
}

const t = runner('code_site_template.js / code_site_followup.js -- the research-site niche');

const INTRO = "We have yet to be properly introduced, but I'm Abdullah Amir, a software engineer who builds AI " +
  'assistants for clinical research websites.';
const PITCH = "That's precisely what I build. My assistants answer sponsor and CRO questions on your website, collect " +
  'their details, and turn more of your visitors into conversations about studies.';
const PITCH_SMO = PITCH.replace(/\.$/, ' across your sites.');
const CLOSE_A = 'Do you have time over the next week or two to learn more? Let me know what works for you, and ' +
  "I'll send a calendar invite along accordingly.";
const SIGN = 'Cheers!\nAbdullah Amir\nSoftware Engineer, Amitrix Labs';

function lead(over) {
  return Object.assign({
    lead_id: 4000, company_name: 'Axis Clinical Research Center S.A. de C.V.', domain: 'axisclinicals.com',
    country: 'Mexico', email: 'contacto@axisclinicals.com', contact_name: '', verified: true,
    therapeutic_areas: ['Endocrinology', 'Oncology'], phases: ['Phase II', 'Phase III'], city: 'Mexico City',
    company_type: 'site', niche: 'research_site', facility_name: 'Axis Clinical Research Center',
    auto_approve_email: true, niche_auto_approve: true, lead_domain_blocked: false, recipient_domain_blocked: false,
  }, over || {});
}
function first(over, trials) {
  const l = lead(over);
  return F.renderSiteFirstTouch({
    lead_id: l.lead_id, company_name: l.company_name, domain: l.domain, email: l.email,
    contact_name: l.contact_name, company_type: l.company_type, facts: F.leadFacts(l, trials || 0),
  });
}
const para = (d, k) => d.body.split('\n\n')[k];

// --- 1. The wording, whole ---------------------------------------------------------

{
  const d = first({}, 4);
  t.check('site, arm A, trials: subject', d.subject, 'Axis Clinical Research Center + 4 recruiting trials + sponsor inquiries');
  t.check('site, arm A, trials: whole body, byte for byte', d.body, [
    'Hi there,', '', INTRO, '',
    'With 4 trials recruiting at Axis Clinical Research Center right now, sponsors are clearly paying attention, ' +
      'and every one who visits your website is a potential next study worth turning into a conversation.', '',
    PITCH, '', CLOSE_A, '', SIGN,
  ].join('\n'));
  t.check('site, arm A, trials: variant', d.variant, 'template/A/trials/unnamed');
}
{
  const d = first({ lead_id: 4001, email: 'maria.lopez@axisclinicals.com', contact_name: 'Dra. Maria Lopez' }, 1);
  t.check('site, arm B, trials (1): singular subject', d.subject, 'Axis Clinical Research Center + 1 recruiting trial + sponsor inquiries');
  t.check('site, arm B, trials: whole body, byte for byte', d.body, [
    'Hi Maria,', '', INTRO, '',
    'Axis Clinical Research Center has 1 trial recruiting right now. Each sponsor or CRO who checks your website ' +
      'after seeing that is a lead worth capturing.', '',
    PITCH, '', 'Worth a look for Axis Clinical Research Center?', '', SIGN,
  ].join('\n'));
  t.check('site, arm B: variant named', d.variant, 'template/B/trials/named');
}
{
  const d = first({}, 0);
  t.check('site, arm A, areas (two)', para(d, 2),
    'Axis Clinical Research Center works in oncology and endocrinology, so sponsors and CROs visiting your website ' +
    'arrive with very different questions, and each one is a lead worth capturing.');
  t.check('site, areas: subject', d.subject, 'Axis Clinical Research Center + oncology and endocrinology + sponsor inquiries');
  const b = first({ lead_id: 4001 }, 0);
  t.check('site, arm B, areas (two): "those areas"', para(b, 2),
    'Axis Clinical Research Center works in oncology and endocrinology, so sponsors searching for a site in those ' +
    'areas will usually check your website before they ever email you. That visit is a lead worth capturing.');
  const one = first({ lead_id: 4001, therapeutic_areas: ['Dermatology'] }, 0);
  t.check('site, arm B, areas (one): the operator\'s sentence, "that area"', para(one, 2),
    'Axis Clinical Research Center works in dermatology, so sponsors searching for a site in that area will usually ' +
    'check your website before they ever email you. That visit is a lead worth capturing.');
}
{
  const d = first({ therapeutic_areas: ['Other'] }, 0);
  t.check('site, city: the operator\'s sentence', para(d, 2),
    "Sponsors and CROs looking for research sites in Mexico City will find Axis Clinical Research Center's website, " +
    'and every one of those visits is a potential study worth turning into a conversation.');
  t.check('site, city: subject', d.subject, 'Axis Clinical Research Center + Mexico City + sponsor inquiries');
  const b = first({ lead_id: 4001, therapeutic_areas: [] }, 0);
  t.check('site, city: one sentence for both arms', para(b, 2), para(d, 2));
}
{
  const d = first({ therapeutic_areas: [], city: '' }, 0);
  t.check('site, company only: whole body', d.body, [
    'Hi there,', '', INTRO, '',
    'Sponsors evaluating Axis Clinical Research Center will usually start on your website, and every one of those ' +
      'visits is a potential study worth turning into a conversation.', '',
    PITCH, '', CLOSE_A, '', SIGN,
  ].join('\n'));
  t.check('site, company only: fallback subject', d.subject, 'Axis Clinical Research Center + sponsor inquiries');
  t.check('site, company only: variant', d.variant, 'template/A/company/unnamed');
}

// SMO: a network of sites.
{
  const smo = { company_type: 'SMO', company_name: 'Arké SMO S.A. de C.V.', domain: 'arkesmo.com', email: 'info@arkesmo.com' };
  const a = first(smo, 6);
  t.check('SMO, arm A, trials: across its network', para(a, 2),
    "With 6 trials recruiting across Arké SMO's network right now, sponsors are clearly paying attention, and every " +
    'one who visits your website is a potential next study worth turning into a conversation.');
  t.check('SMO: the pitch speaks of its sites', para(a, 3), PITCH_SMO);
  const b = first(Object.assign({ lead_id: 4001 }, smo), 2);
  t.check('SMO, arm B, trials', para(b, 2),
    "Arké SMO's network has 2 trials recruiting right now. Each sponsor or CRO who checks your website after " +
    'seeing that is a lead worth capturing.');
  t.check('SMO, arm A, areas', para(first(smo, 0), 2),
    "Arké SMO's sites work in oncology and endocrinology, so sponsors and CROs visiting your website arrive with " +
    'very different questions, and each one is a lead worth capturing.');
  t.check('SMO, arm B, areas (one)', para(first(Object.assign({ lead_id: 4001, therapeutic_areas: ['Oncology'] }, smo), 0), 2),
    "Arké SMO's sites work in oncology, so sponsors searching for sites in that area will usually check your " +
    'website before they ever email you. That visit is a lead worth capturing.');
  t.check('SMO, company only', para(first(Object.assign({ therapeutic_areas: [], city: '' }, smo), 0), 2),
    "Sponsors evaluating Arké SMO's sites will usually start on your website, and every one of those visits is a " +
    'potential study worth turning into a conversation.');
  t.check('SMO: the whole body ends with the site sign-off', a.body.endsWith('\n\n' + SIGN), true);
}

// No CRO wording can reach a site or SMO email, in any branch.
{
  const bodies = [];
  [4000, 4001].forEach((id) => ['site', 'SMO'].forEach((ct) => [5, 0].forEach((n) => [[], ['Oncology']].forEach((ta) =>
    bodies.push(first({ lead_id: id, company_type: ct, therapeutic_areas: ta }, n).body)))));
  const cro = /CRO websites|Founder, Amitrix Labs|I'm the Founder|we do at Amitrix|win more studies|qualified leads|patient|recruit (patients|participants)|enrol/i;
  t.check('no CRO wording, no patient or recruitment claim, in ' + bodies.length + ' rendered bodies',
    bodies.filter((b) => cro.test(b)).length, 0);
}

// --- 2. Recruiting trials AT this site: the strict facility-level match -------------

function study(nct, locs) {
  return { protocolSection: { identificationModule: { nctId: nct }, contactsLocationsModule: { locations: locs } } };
}
function loc(facility, city, status, country) {
  return { facility: facility, city: city, status: status || 'RECRUITING', country: country || 'Mexico' };
}
{
  const ct = { totalCount: 6, studies: [
    study('N1', [loc('Axis Clinical Research Center', 'Mexico City')]),
    study('N2', [loc('Axis Clinical Research Center S.A. de C.V. /ID# 266243', 'Mexico City')]),
    study('N3', [loc('Axis Clinical Research Center ( Site 1176)', 'Mexico City')]),
    study('N4', [loc('Axis Clinical Research Center', 'Guadalajara')]),
    study('N5', [loc('Axis Clinical Research Center', 'Mexico City', 'COMPLETED')]),
    study('N6', [loc('Axis Oncology Partners', 'Mexico City')]),
  ] };
  t.check('strict match: same facility (suffixes stripped), same city, RECRUITING -- 3 of 6', F.verifiedSiteTrials(lead(), ct), 3);
  t.check('SMO: same facility anywhere in its country -- 4', F.verifiedSiteTrials(lead({ company_type: 'SMO' }), ct), 4);
  t.check('a page that matched more than it returned counts as none',
    F.verifiedSiteTrials(lead(), Object.assign({}, ct, { totalCount: 1200 })), 0);
  t.check('a generic facility name ("Research Site") never matches',
    F.verifiedSiteTrials(lead({ facility_name: 'Research Site' }), { totalCount: 1, studies: [study('N9', [loc('Research Site', 'Mexico City')])] }), 0);
  t.check('company name is used when no facility name is stored',
    F.verifiedSiteTrials(lead({ facility_name: null, company_name: 'Axis Clinical Research Center' }), ct), 3);
  t.check('facilityCore keeps the brand, drops the office', F.facilityCore('Pan American Center for Oncology Trials, LLC - Dorado Office'),
    'Pan American Center for Oncology Trials, LLC');
}

// --- 3. The whole node: lookup failure, niche guard, the gate, both switches ----------

{
  const ct = { totalCount: 1, studies: [study('N1', [loc('Axis Clinical Research Center', 'Mexico City')])] };
  const ok = runFirst(lead(), ct);
  t.check('node: auto-approved with a verified address, a clock and no blocklist', [ok.ok, ok.decision.status, ok.decision.approved_by], [true, 'approved', 'auto']);
  t.check('node: the verified trial count reached the SMYKM line', ok.smykm + ':' + ok.trials_verified, 'trials:1');
  const down = runFirst(lead(), { error: { message: 'ECONNRESET' } });
  t.check('node: ClinicalTrials.gov did not answer -> nothing written', down.ok, false);
  t.check('node: a CRO row is never drafted here', runFirst(lead({ company_type: 'CRO', niche: 'cro' }), ct).ok, false);
  t.check('node: a lead of no niche is never drafted here', runFirst(lead({ niche: null }), ct).ok, false);
  const unver = runFirst(lead({ verified: false }), ct);
  t.check('gate: unverified address is held and tagged', [unver.decision.status, unver.payload.drafts[0].variant],
    ['pending', 'template/A/trials/unnamed+unverified-address']);
  const noAddr = runFirst(lead({ email: '' }), ct);
  t.check('gate: no address is held', noAddr.payload.drafts[0].variant, 'template/A/trials/unaddressed+no-address');
  const noClock = runFirst(lead({ country: 'Atlantis' }), ct);
  t.check('gate: no Send clock is held', /\+no-send-clock$/.test(noClock.payload.drafts[0].variant), true);
  const blocked = runFirst(lead({ lead_domain_blocked: true }), ct);
  t.check('gate: a blocklisted domain is held', /\+blocklisted$/.test(blocked.payload.drafts[0].variant), true);
  const off = runFirst(lead({ auto_approve_email: false }), ct);
  t.check('switch: settings.auto_approve_email off holds it', off.decision.status, 'pending');
  const nicheOff = runFirst(lead({ niche_auto_approve: false }), ct);
  t.check('switch: niches.auto_approve off holds it, and says which switch', [nicheOff.decision.status,
    /niches\.auto_approve/.test(nicheOff.decision.hold_reason)], ['pending', true]);
}

// --- 4. Follow-ups --------------------------------------------------------------------

{
  const row = {
    lead_id: 4001, action: 'draft-follow-up', next_follow_up: 1, niche: 'research_site', company_type: 'site',
    company_name: 'Axis Clinical Research Center', domain: 'axisclinicals.com', email: 'contacto@axisclinicals.com',
    contact_name: null, verified: true, country: 'Mexico', auto_approve_email: true, niche_auto_approve: true,
    lead_domain_blocked: false, recipient_domain_blocked: false,
    first_subject: 'Axis Clinical Research Center + Mexico City + sponsor inquiries',
    first_body: 'Hi there,\n\nFirst email.\n\n' + SIGN, first_sent_at: '2026-10-29T15:00:00.000Z',
  };
  const out = runFollowUps([row, Object.assign({}, row, { lead_id: 4002, next_follow_up: 2 }),
    Object.assign({}, row, { lead_id: 4003, niche: 'cro', company_type: 'CRO' }),
    { lead_id: 4004, action: 'mark-lost' }]);
  t.check('follow-ups: #1, #2, the mark-lost row; the CRO row is skipped', out.map((o) => o.json.lead_id), [4001, 4002, 4004]);
  const one = out[0].json.payload.draft;
  t.check('follow-up #1: whole text, byte for byte', one.body, [
    'Hi there,', '',
    "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits Axis Clinical " +
      "Research Center's website is a potential study, and I help make sure those visits turn into conversations.", '',
    'Worth a look for Axis Clinical Research Center?', '', SIGN, '',
    'On Thu, 29 Oct 2026, Abdullah Amir wrote:', '> Hi there,', '>', '> First email.', '>', '> Cheers!',
    '> Abdullah Amir', '> Software Engineer, Amitrix Labs',
  ].join('\n'));
  t.check('follow-up #1: Re: the first subject, variant, auto-approved',
    [one.subject, one.variant, one.status], ['Re: Axis Clinical Research Center + Mexico City + sponsor inquiries',
      'follow-up-1/template/B', 'approved']);
  const two = out[1].json.payload.draft;
  t.check('follow-up #2: the operator\'s sentence, no close', two.body.split('\n\n').slice(0, 3).join('\n\n'), [
    'Hi there,', '',
    "I'll keep this short and won't follow up again after this. If turning more of Axis Clinical Research Center's " +
      "website visitors into sponsor conversations is ever on your list, just reply and I'll send over the details.", '',
    SIGN,
  ].join('\n'));
  t.check('follow-up #2: variant', two.variant, 'follow-up-2/template/A');
  const held = runFollowUps([Object.assign({}, row, { niche_auto_approve: false })])[0].json.payload.draft;
  t.check('follow-up: the niche switch holds it too', held.status, 'pending');
}

t.done();

// Build Site First Touch -- n8n Code node (Run Once for Each Item).
//
// The research-site niche (niches.code = 'research_site', migration 025): the
// first touch and the two follow-ups for an independent clinical research site
// or an SMO, in the operator's wording of 2026-10-28, filled by code with
// verified facts. No model call. The build (n8n/drafting/build_sites_workflow.py)
// places drafting's code_template.js rules section -- everything above its
// "Node body" -- verbatim ABOVE this file, so a site email is greeted, cleaned,
// armed and gated by exactly the functions a CRO email is: armFor, cleanCompany,
// greeting/addressing, fill, canonicalAreas, joinAnd, cleanCity, gate, withTags,
// isSponsor, CLOSE_A/CLOSE_B. Only the words differ, and they are below.
//
// Everything above "Node body" is pure and shared with Follow-Ups Sites, which
// embeds it verbatim. Keep it free of n8n data reads and build placeholders.

// ---------------------------------------------------------------------------
// The approved wording -- research_site niche (operator brief 2026-10-28).
// migration 025 seeds niches.templates from these constants and
// test_site_template.js / test_site_drift_guards.py refuse a build where the
// two disagree. Change both together.
// ---------------------------------------------------------------------------

const SITE_SUBJECT_WITH_FACT = "{company} + {fact} + sponsor inquiries";
const SITE_SUBJECT_NO_FACT = "{company} + sponsor inquiries";

const SITE_INTRO_LINE = "We have yet to be properly introduced, but I'm Abdullah Amir, a software engineer who builds AI assistants for clinical research websites.";

// The SMYKM sentence: (1) recruiting trials at that site, (2) therapeutic
// areas, (3) city, (4) the company alone -- A/B by lead_id parity for trials and
// areas, one sentence for city and company, as in the CRO template. The areas B,
// city and company lines are the operator's words; the others adapt the CRO
// lines to a site. "that area" becomes "those areas" when two areas are named.
const SITE_SMYKM = {
  site: {
    trials_A: "With {n} trial(s) recruiting at {company} right now, sponsors are clearly paying attention, and every one who visits your website is a potential next study worth turning into a conversation.",
    trials_B: "{company} has {n} trial(s) recruiting right now. Each sponsor or CRO who checks your website after seeing that is a lead worth capturing.",
    areas_A: "{company} works in {areas}, so sponsors and CROs visiting your website arrive with very different questions, and each one is a lead worth capturing.",
    areas_B: "{company} works in {areas}, so sponsors searching for a site in that area will usually check your website before they ever email you. That visit is a lead worth capturing.",
    city: "Sponsors and CROs looking for research sites in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.",
    company: "Sponsors evaluating {company} will usually start on your website, and every one of those visits is a potential study worth turning into a conversation.",
  },
  // An SMO is a network of sites: the same facts, said of its sites.
  smo: {
    trials_A: "With {n} trial(s) recruiting across {company}'s network right now, sponsors are clearly paying attention, and every one who visits your website is a potential next study worth turning into a conversation.",
    trials_B: "{company}'s network has {n} trial(s) recruiting right now. Each sponsor or CRO who checks your website after seeing that is a lead worth capturing.",
    areas_A: "{company}'s sites work in {areas}, so sponsors and CROs visiting your website arrive with very different questions, and each one is a lead worth capturing.",
    areas_B: "{company}'s sites work in {areas}, so sponsors searching for sites in that area will usually check your website before they ever email you. That visit is a lead worth capturing.",
    city: "Sponsors and CROs looking for research sites in {city} will find {company}'s website, and every one of those visits is a potential study worth turning into a conversation.",
    company: "Sponsors evaluating {company}'s sites will usually start on your website, and every one of those visits is a potential study worth turning into a conversation.",
  },
};

const SITE_PITCH_LINE = {
  site: "That's precisely what I build. My assistants answer sponsor and CRO questions on your website, collect their details, and turn more of your visitors into conversations about studies.",
  smo: "That's precisely what I build. My assistants answer sponsor and CRO questions on your website, collect their details, and turn more of your visitors into conversations about studies across your sites.",
};

// The close is the CRO template's, word for word (CLOSE_A / CLOSE_B above).

const SITE_SIGN_OFF = ["Cheers!", "Abdullah Amir", "Software Engineer, Amitrix Labs"];

const SITE_FOLLOW_UP_1_LINE = "Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits {company}'s website is a potential study, and I help make sure those visits turn into conversations.";
const SITE_FOLLOW_UP_2_LINE = "I'll keep this short and won't follow up again after this. If turning more of {company}'s website visitors into sponsor conversations is ever on your list, just reply and I'll send over the details.";

// The entity types this niche drafts, as lead_company_type() spells them.
const SITE_ENTITY = { site: 'site', SMO: 'smo' };

// ---------------------------------------------------------------------------
// Recruiting trials AT this site -- strict facility-level match.
//
// ClinicalTrials.gov's query.locn is a loose search over every location field:
// "Pan American Clinical Research" also returns "American Oncology Partners".
// So a study counts only when one of its locations
//   * is RECRUITING (the location's own status, not the study's),
//   * names this facility: the location's facility, with a sponsor's site
//     suffix removed (" /ID# 266243", "(Site 1176)", " - Dorado Office"), is
//     the lead's facility name once both are folded and stripped of legal forms
//     -- isSponsor()'s rule, so "Axis Clinical Research Center, LLC" matches and
//     "Axis Oncology" does not;
//   * is in the lead's city (a site), or anywhere in the lead's country (an
//     SMO: its sites carry its name in different cities).
// A lookup that matched more studies than the page returned cannot be verified
// and counts as none. Generic sponsor placeholders ("Research Site",
// "Investigational Site") can never be a lead's facility, and match nothing.
// ---------------------------------------------------------------------------

const GENERIC_FACILITY = /^(research|clinical research|clinical|investigational|investigative|study|trial|local)\s+(site|institution|center|centre|facility)s?(\s+\d+)?$/;

function facilityCore(name) {
  let s = str(name)
    .replace(/\s*\/\s*ID\s*#\s*\d+.*$/i, '')
    .replace(/\s*\(\s*site\s*[^)]*\)\s*$/i, '')
    .replace(/\s*-\s*site\s*\d+\s*$/i, '')
    .replace(/\s*,?\s+site\s*#?\s*\d+\s*$/i, '');
  const dash = s.split(/\s+[-–]\s+|\s+[-–]|[-–]\s+/);
  if (dash.length > 1 && sponsorKey(dash[0]).split(' ').length >= 2) s = dash[0];
  return s;
}

function sameCity(a, b) {
  const x = fold(a).replace(/[^a-z0-9]+/g, ' ').trim();
  const y = fold(b).replace(/[^a-z0-9]+/g, ' ').trim();
  return !!x && x === y;
}

function sameCountry(locCountry, leadCountry) {
  const a = fold(locCountry).replace(/[^a-z]+/g, ' ').trim();
  const b = fold(leadCountry).replace(/[^a-z]+/g, ' ').trim();
  if (!a || !b) return false;
  const alias = { 'united states': 'usa', 'czechia': 'czech republic', 'united arab emirates': 'uae',
    'korea republic of': 'south korea', 'turkiye': 'turkey', 'north macedonia': 'macedonia' };
  return (alias[a] || a) === (alias[b] || b);
}

function verifiedSiteTrials(lead, ct) {
  const studies = Array.isArray(ct && ct.studies) ? ct.studies : [];
  if (!(typeof (ct && ct.totalCount) === 'number') || ct.totalCount > studies.length) return 0;
  const facility = str(lead.facility_name) || str(lead.company_name);
  if (!sponsorKey(facility) || GENERIC_FACILITY.test(sponsorKey(facility))) return 0;
  const smo = str(lead.company_type) === 'SMO';
  return studies.filter(function (st) {
    const locs = (st && st.protocolSection && st.protocolSection.contactsLocationsModule &&
      st.protocolSection.contactsLocationsModule.locations) || [];
    return locs.some(function (loc) {
      if (str(loc && loc.status).toUpperCase() !== 'RECRUITING') return false;
      if (!isSponsor(facility, facilityCore(loc.facility))) return false;
      return smo ? sameCountry(loc.country, lead.country) : sameCity(loc.city, lead.city);
    });
  }).length;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

// Titles the shared HONORIFICS list (code_template.js, the CRO lane's, left
// untouched) does not carry, but a site's own pages put before a name -- "Dra.
// Maria Lopez" would otherwise be greeted "Hi Dra,". Stripped here, before the
// shared greeting rules see the name.
const SITE_EXTRA_HONORIFICS = ['dra', 'drs', 'drsa', 'lic', 'licda', 'licdo', 'mtro', 'mtra', 'enf', 'psic',
  'qfb', 'dott', 'dottssa', 'mudr', 'mgr', 'doc'];

function siteContactName(name) {
  const parts = str(name).split(/\s+/).filter(Boolean);
  while (parts.length > 1 && SITE_EXTRA_HONORIFICS.indexOf(parts[0].toLowerCase().replace(/\.+$/, '')) !== -1) parts.shift();
  return parts.join(' ');
}

function siteEntity(companyType) {
  const e = SITE_ENTITY[str(companyType)];
  if (!e) throw new Error('not a research-site niche entity: ' + JSON.stringify(companyType));
  return e;
}

// "a site in that area" / "sites in that area" -> "... those areas" for two.
function areaAgreement(line, areaCount) {
  return areaCount > 1 ? line.split('in that area').join('in those areas') : line;
}

function siteSmykmLine(kind, arm, entity, company, facts) {
  const t = SITE_SMYKM[entity];
  if (kind === 'trials') return fill(arm === 'A' ? t.trials_A : t.trials_B, { company: company, n: facts.trials });
  if (kind === 'areas') {
    return fill(areaAgreement(arm === 'A' ? t.areas_A : t.areas_B, facts.areas.length),
      { company: company, areas: joinAnd(facts.areas) });
  }
  if (kind === 'city') return fill(t.city, { company: company, city: facts.city });
  if (kind === 'company') return fill(t.company, { company: company });
  return null;
}

function siteSubjectLine(kind, company, facts) {
  if (kind === 'trials') return fill(SITE_SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_TRIALS, { n: facts.trials }) });
  if (kind === 'areas') return fill(SITE_SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_AREAS, { areas: joinAnd(facts.areas) }) });
  if (kind === 'city') return fill(SITE_SUBJECT_WITH_FACT, { company: company, fact: fill(SUBJECT_FACT_CITY, { city: facts.city }) });
  return fill(SITE_SUBJECT_NO_FACT, { company: company });
}

// `t` is { lead_id, company_name, domain, email, contact_name, company_type, facts }.
function renderSiteFirstTouch(t) {
  const arm = armFor(t.lead_id);
  const entity = siteEntity(t.company_type);
  const company = cleanCompany(t.company_name, t.domain);
  const kind = pickSmykm(t.facts);
  const smykm = siteSmykmLine(kind, arm, entity, company, t.facts);
  const contact = siteContactName(t.contact_name);
  const lines = [greeting(t.email, contact), '', SITE_INTRO_LINE, ''];
  if (smykm) lines.push(smykm, '');
  lines.push(SITE_PITCH_LINE[entity], '', closeFor(arm, company), '');
  return {
    arm: arm,
    entity: entity,
    smykm: kind,
    addressing: addressing(t.email, contact),
    company: company,
    subject: siteSubjectLine(kind, company, t.facts),
    body: lines.concat(SITE_SIGN_OFF).join('\n'),
    variant: 'template/' + arm + '/' + kind + '/' + addressing(t.email, contact),
  };
}

// Threading exactly as renderFollowUp(): Re: the first subject, the first email
// quoted below "On <date>, Abdullah Amir wrote:".
function renderSiteFollowUp(f) {
  const n = Number(f.n);
  if (n !== 1 && n !== 2) throw new Error('no follow-up template #' + JSON.stringify(f.n));
  const arm = armFor(f.lead_id);
  const company = cleanCompany(f.company_name, f.domain);
  const lines = [greeting(f.email, siteContactName(f.contact_name)), '', fill(n === 1 ? SITE_FOLLOW_UP_1_LINE : SITE_FOLLOW_UP_2_LINE, { company: company }), ''];
  if (n === 1) lines.push(closeFor(arm, company), '');
  const firstBody = String(f.first_body || '').replace(/\r\n?/g, '\n').trim();
  const sent = new Date(f.first_sent_at);
  const when = isNaN(sent.getTime()) ? 'earlier' : sent.toUTCString().slice(0, 16);
  const quoted = firstBody.split('\n').map(function (ln) { return ln ? '> ' + ln : '>'; }).join('\n');
  return {
    arm: arm,
    company: company,
    subject: 'Re: ' + str(f.first_subject).replace(/^(?:re:\s*)+/i, ''),
    body: lines.concat(SITE_SIGN_OFF, ['', 'On ' + when + ', ' + SITE_SIGN_OFF[1] + ' wrote:', quoted]).join('\n'),
    variant: 'follow-up-' + n + '/template/' + arm,
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;
const CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

const lead = $('Get Site Draft Batch').item.json;
const ct = $input.item.json;

// ClinicalTrials.gov did not answer: nothing is written, the lead stays
// 'contact_found' and the next run tries again (the CRO lane's rule).
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

// Get Site Draft Batch selects only this niche's leads; anything else here
// means the query changed. Nothing is written for it.
if (!SITE_ENTITY[str(lead.company_type)] || str(lead.niche) !== 'research_site') {
  return {
    json: {
      lead_id: lead.lead_id,
      ok: false,
      skip_reason: 'not a research-site niche lead (' + str(lead.niche) + '/' + str(lead.company_type) + ')',
    },
  };
}

const facts = leadFacts(lead, verifiedSiteTrials(lead, ct));
const draft = renderSiteFirstTouch({
  lead_id: lead.lead_id,
  company_name: lead.company_name,
  domain: lead.domain,
  email: lead.email,
  contact_name: lead.contact_name,
  company_type: lead.company_type,
  facts: facts,
});
const decision = gate({
  auto_approve: lead.auto_approve_email === true && lead.niche_auto_approve === true,
  email: lead.email,
  verified: lead.verified === true,
  country: lead.country,
  domain: lead.domain,
  lead_domain_blocked: lead.lead_domain_blocked === true,
  recipient_domain_blocked: lead.recipient_domain_blocked === true,
}, CLOCK_KEYS);
// The niche's own switch (niches.auto_approve) says so in its own words.
if (lead.auto_approve_email === true && lead.niche_auto_approve !== true && decision.hold_reason === AUTO_APPROVE_OFF) {
  decision.hold_reason = 'auto-approve-off: niches.auto_approve is off for research_site, so every email waits for a person';
}

return {
  json: {
    lead_id: lead.lead_id,
    ok: true,
    company: draft.company,
    entity: draft.entity,
    arm: draft.arm,
    smykm: draft.smykm,
    addressing: draft.addressing,
    facts: facts,
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

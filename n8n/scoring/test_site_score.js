// Unit tests for the research-site niche's scoring (migration 025): who takes the
// site path, the strict classification (site / SMO / CRO / sponsor / hospital /
// academic / government / unclear), the eight factors, and the whole Evaluate
// Lead + Compute Fit Score pair on a site lead and on a CRO lead.
// Sliced out of the shipped code_evaluate.js / code_score.js.
const path = require('path');
const { extractFunctions, runForEachItem, runner } = require('./harness');

const EVAL = path.join(__dirname, 'code_evaluate.js');
const SCORE = path.join(__dirname, 'code_score.js');
const F = extractFunctions(EVAL, '// Node body', ['isSiteNicheLead', 'classifySite', 'siteNicheFactors', 'SITE_WEIGHTS',
  'SITE_ORDER', 'SITE_TRIAL_BANDS', 'SITE_SOURCES']);

const t = runner('Workflow 3 -- the research-site niche (migration 025)');

function lead(over) {
  return Object.assign({
    lead_id: 5000, domain: 'axisclinicals.com', company_name: 'Axis Clinical Research Center', country: 'Mexico',
    source: 'sites-ctgov', therapeutic_areas: ['Endocrinology', 'Oncology'], phases: [], founder_name: null,
    founder_linkedin: null, employee_estimate: 25, has_chatbot: false, chatbot_vendor: null, site_quality_notes: null,
    blocklisted: false, raw_extraction: { company_type: 'site', enrichment_status: 'ok', fetch: { ok: true, pages_fetched: 4,
      reached_via: 'https /', prompt_chars: 9000 } },
    sls_facility_name: 'Axis Clinical Research Center', sls_ctgov_recruiting: 7, sls_has_contact_form: true,
    sls_has_mailto: false, sls_name_kind: 'research-word',
  }, over || {});
}
const raw = (ct) => ({ company_type: ct, enrichment_status: 'ok', fetch: { ok: true } });

// --- who takes the site path --------------------------------------------------------
t.check('a lead from either open site source takes it', [F.isSiteNicheLead(lead()), F.isSiteNicheLead(lead({ source: 'sites-overture' }))], [true, true]);
t.check('an ICH GCP lead enrichment calls a site or an SMO takes it',
  [F.isSiteNicheLead(lead({ source: 'ichgcp', raw_extraction: raw('site') })), F.isSiteNicheLead(lead({ source: 'ichgcp', raw_extraction: raw('SMO') }))], [true, true]);
t.check('an ICH GCP CRO does NOT -- the CRO path is untouched', F.isSiteNicheLead(lead({ source: 'ichgcp', raw_extraction: raw('CRO') })), false);
t.check('a Trialsites lead does NOT (no niche until its licence is settled)', F.isSiteNicheLead(lead({ source: 'trialsites' })), false);

// --- the strict classification --------------------------------------------------------
const cls = (over) => F.classifySite(lead(over));
const code = (over) => { const c = cls(over); return c.excluded ? c.excluded.code : c.entity + '/' + c.kind; };
t.check('a dedicated research site', code(), 'site/dedicated');
t.check('a practice that runs trials (no research word)', code({ company_name: 'Valley Dermatology Associates', sls_facility_name: null }), 'site/practice');
t.check('an SMO by enrichment', code({ raw_extraction: raw('SMO') }), 'SMO/smo');
t.check('an SMO by name, enrichment said site', code({ company_name: 'Arke SMO', sls_facility_name: null }), 'SMO/smo');
t.check('enrichment says CRO, an Overture-only lead -> excluded, never the CRO template',
  code({ source: 'sites-overture', raw_extraction: raw('CRO'), sls_ctgov_recruiting: 0 }), 'site_cro');
t.check('enrichment says CRO, but it is a recruiting ClinicalTrials.gov location -> a site',
  code({ raw_extraction: raw('CRO') }), 'site/dedicated');
t.check('... unless nothing recruits there', code({ raw_extraction: raw('CRO'), sls_ctgov_recruiting: 0 }), 'site_cro');
t.check('a recruiting location whose NAME says CRO stays a CRO', code({ raw_extraction: raw('CRO'), company_name: 'Andes CRO',
  sls_facility_name: null }), 'site_cro');
t.check('a cancer center or a medical center is excluded', [code({ company_name: 'MD Anderson Cancer Center', sls_facility_name: null }),
  code({ company_name: 'Valley Medical Center', sls_facility_name: null })], ['site_academic', 'site_academic']);
t.check('the name says CRO', code({ company_name: 'Andes CRO', sls_facility_name: null }), 'site_cro');
t.check('a sponsor by name', code({ company_name: 'Laboratorios Silanes Pharmaceuticals', sls_facility_name: null }), 'site_sponsor');
t.check('a hospital by name (any included language)', [code({ company_name: 'Hospital Angeles Research' }),
  code({ company_name: 'Szpital Miejski Badania', sls_facility_name: null })], ['site_hospital', 'site_hospital']);
t.check('academic by name', code({ company_name: 'Universidad de Monterrey Research Unit', sls_facility_name: null }), 'site_academic');
t.check('government by name', code({ company_name: 'National Institute of Clinical Research', sls_facility_name: null }), 'site_government');
t.check('an institutional domain', code({ domain: 'research.unam.edu.mx' }), 'site_institution');
t.check('the CT.gov facility name is screened too', code({ sls_facility_name: 'Hospital General Research Unit' }), 'site_hospital');
t.check('enrichment unsure -> excluded (only a positive answer is a site)', [code({ raw_extraction: raw('unclear') }),
  code({ raw_extraction: raw(null) })], ['site_unclear', 'site_unclear']);

// --- the factors ---------------------------------------------------------------------------
const W = F.SITE_WEIGHTS;
t.check('the weights sum to 100', Object.values(W).reduce((a, b) => a + b, 0), 100);
t.check('every factor is in the order', F.SITE_ORDER.slice().sort(), Object.keys(W).sort());
const f = F.siteNicheFactors(lead(), cls());
t.check('7 recruiting trials -> the 5+ band (20)', f.recruiting_trials.points, 20);
t.check('trial bands: 10 -> 25, 2 -> 15, 1 -> 10, 0 -> 0',
  [10, 2, 1, 0].map((n) => F.siteNicheFactors(lead({ sls_ctgov_recruiting: n }), cls()).recruiting_trials.points), [25, 15, 10, 0]);
t.check('a dedicated site or an SMO earns the full site-type weight, a practice 8',
  [f.site_type.points, F.siteNicheFactors(lead(), { kind: 'smo' }).site_type.points, F.siteNicheFactors(lead(), { kind: 'practice' }).site_type.points],
  [15, 15, 8]);
t.check('areas stated -> 10; none -> 0', [f.therapeutic_areas.points,
  F.siteNicheFactors(lead({ therapeutic_areas: ['Other'] }), cls()).therapeutic_areas.points], [10, 0]);
t.check('inquiry path: form only 10, form + mailto 6, no form 3, not measured 5',
  [[true, false], [true, true], [false, false], [null, null]].map((p) =>
    F.siteNicheFactors(lead({ sls_has_contact_form: p[0], sls_has_mailto: p[1] }), cls()).inquiry_form.points), [10, 6, 3, 5]);
t.check('geography rescaled: core 10, extended 5', [f.geography.points,
  F.siteNicheFactors(lead({ country: 'USA' }), cls()).geography.points], [10, 5]);
t.check('employees 5-100 -> 10, unknown -> 5', [f.employees.points,
  F.siteNicheFactors(lead({ employee_estimate: null }), cls()).employees.points], [10, 5]);
t.check('every factor carries max = its weight', F.SITE_ORDER.every((k) => f[k].max === W[k]), true);

// --- the two nodes, end to end ----------------------------------------------------------------
function score(l, ctTotal) {
  const ev = runForEachItem(EVAL, [{ json: l }], {});
  if (ev[0].json.disqualified) return { dq: ev[0].json.disqualify_reason };
  const sc = runForEachItem(SCORE, [{ json: ctTotal === undefined ? { error: 'down' } : { totalCount: ctTotal } }], { 'Evaluate Lead': ev });
  return { ev: ev[0].json, sc: sc[0].json };
}
{
  const r = score(lead());
  t.check('a site lead is scored even while ClinicalTrials.gov is down (its trials come from the harvest)', r.sc.write, true);
  const expect = F.SITE_ORDER.reduce((s, k) => s + r.ev.site_niche.factors[k].points, 0);
  t.check('fit = the sum of the site factors', r.sc.fit_score, expect);
  t.check('the breakdown names the site factors', /recruiting_trials \d+\/25/.test(r.sc.breakdown_line) && /inquiry_form/.test(r.sc.breakdown_line), true);
  t.check('ClinicalTrials.gov sponsor matches play no part', score(lead(), 50).sc.fit_score, r.sc.fit_score);
}
{
  const cro = lead({ source: 'ichgcp', raw_extraction: raw('CRO'), sls_ctgov_recruiting: null, sls_has_contact_form: null });
  const r = score(cro, 3);
  t.check('a CRO lead: no site block, the CRO factors as before', [r.ev.site_niche, Object.keys(r.ev.factors).sort()],
    [null, ['employees', 'founder', 'geography', 'oncology', 'site']]);
}
t.check('a site-source lead enrichment calls a CRO is disqualified with the reason',
  /research-site niche drafts sites and SMOs only/.test(score(lead({ source: 'sites-overture', raw_extraction: raw('CRO') })).dq), true);
t.check('a chat widget still disqualifies a site (the shared rule)',
  /chat widget/i.test(score(lead({ has_chatbot: true, chatbot_vendor: 'Tidio' })).dq || ''), true);

t.done();

// Unit test for the two Code-node bodies that run after the network calls:
// Compute Fit Score (code_score.js) and Attach Rationale (code_rationale.js).
//
// What matters here is how each one handles a call that did NOT succeed, and
// the two answers are deliberately different:
//   - a failed ClinicalTrials.gov lookup DROPS the lead, because scoring it
//     would silently write a score 20 points low that looks valid afterwards;
//   - a failed Ollama call does NOT, because the score is already final and the
//     deterministic rationale is a real fallback rather than a placeholder.
//
// Runs the real node bodies through the harness, not extracted helpers.
const path = require('path');
const { runForEachItem, runner } = require('./harness');

const t = runner('Workflow 3 score + rationale nodes');

const SCORE_NODE = path.join(__dirname, 'code_score.js');
const RATIONALE_NODE = path.join(__dirname, 'code_rationale.js');

// A lead as Evaluate Lead emits it: cleared the disqualifiers, real extraction.
function evaluated(over) {
  return {
    json: Object.assign(
      {
        lead_id: 42,
        domain: 'example.com',
        company_name: 'Example Clinical Research',
        country: 'India',
        disqualified: false,
        has_extraction: true,
        needs_recheck: false,
        enrichment_status: 'ok',
        site_evidence: 'http_block',
        ctgov_sponsor: 'Example Clinical Research',
        factors: {
          geography: { points: 25, max: 25, basis: 'confirmed', detail: 'India is a target geography' },
          founder: { points: 20, max: 20, basis: 'confirmed', detail: 'Asha Rao identified with a LinkedIn profile' },
          oncology: { points: 15, max: 15, basis: 'confirmed', detail: 'oncology among the stated therapeutic areas', areas: ['Oncology'] },
          employees: { points: 5, max: 10, basis: 'unknown', detail: 'headcount not stated on the site' },
          site: { points: 10, max: 10, basis: 'confirmed', detail: 'substantial content (24000 chars); 4 standard subpages' },
        },
        facts: {
          founder_name: 'Asha Rao',
          founder_title: 'Managing Director',
          founder_linkedin: 'https://www.linkedin.com/in/asha-rao',
          city: 'Bengaluru',
          company_type: 'CRO',
          phases: ['Phase II', 'Phase III'],
          areas: ['Oncology'],
          employee_estimate: null,
          site_quality_notes: 'Detailed service pages and named team bios.',
        },
      },
      over || {}
    ),
  };
}

function score(ctgovResponse, evalOver) {
  const ev = evaluated(evalOver);
  return runForEachItem(SCORE_NODE, [{ json: ctgovResponse }], { 'Compute Fit Score': [ev], 'Evaluate Lead': [ev] })[0].json;
}

// --- the weighted sum -----------------------------------------------------
const withTrials = score({ totalCount: 3 });
const noTrials = score({ totalCount: 0 });

t.check('trials found adds the full 20 points', withTrials.fit_score, 95);
t.check('no trials adds nothing', noTrials.fit_score, 75);
t.check('a scored lead is writable', withTrials.write, true);
t.check('fit_score is mirrored into the payload', withTrials.payload.fit_score, 95);
t.check('a scored lead is never marked disqualified', withTrials.payload.disqualified, false);
t.check('...and carries no disqualify reason', withTrials.payload.disqualify_reason, null);

// --- a failed ClinicalTrials.gov lookup DROPS the lead --------------------
t.check('a lookup that returned no totalCount drops the lead',
  score({ error: 'ETIMEDOUT' }).write, false);
t.check('...and says why',
  score({ error: 'ETIMEDOUT' }).skip_reason.indexOf('ClinicalTrials.gov lookup failed') === 0, true);
t.check('a non-numeric totalCount is treated as a failure, not as zero',
  score({ totalCount: null }).write, false);
t.check('an HTML error page instead of JSON drops the lead',
  score({ body: '<html>502</html>' }).write, false);
t.check('totalCount 0 is a real answer and is NOT a failure',
  noTrials.write, true);

// --- the stored rationale is auditable ------------------------------------
t.check('the deterministic rationale names the company',
  withTrials.payload.rationale.indexOf('Example Clinical Research') !== -1, true);
t.check('the breakdown is appended to the rationale',
  withTrials.payload.rationale.indexOf('Score 95/100') !== -1, true);
t.check('the breakdown shows every factor with its weight',
  ['geography 25/25', 'trials 20/20', 'founder 20/20', 'oncology 15/15', 'employees 5/10', 'site 10/10']
    .every((s) => withTrials.payload.rationale.indexOf(s) !== -1), true);
t.check('an unknown factor is labelled as scored at half weight, not as a negative',
  withTrials.payload.rationale.indexOf('half weight rather than penalised') !== -1, true);

// --- the prompt is grounded ----------------------------------------------
t.check('the prompt carries the real founder name',
  withTrials.prompt.indexOf('Asha Rao') !== -1, true);
t.check('the prompt carries the real trial count',
  withTrials.prompt.indexOf('ClinicalTrials.gov as sponsor: 3') !== -1, true);
t.check('the prompt forbids inventing facts',
  withTrials.system_prompt.indexOf('Use ONLY the facts given') !== -1, true);
t.check('a lead with real extraction is groundable', withTrials.groundable, true);
t.check('a lead whose site could not be read is NOT groundable',
  score({ totalCount: 0 }, { has_extraction: false, needs_recheck: true, enrichment_status: 'no_readable_text' }).groundable,
  false);
t.check('...and its rationale says so explicitly',
  score({ totalCount: 0 }, { has_extraction: false, needs_recheck: true, enrichment_status: 'no_readable_text' })
    .payload.rationale.indexOf('could not read this site') !== -1,
  true);

// --- Attach Rationale -----------------------------------------------------
function attach(ollamaResponse) {
  const scored = { json: withTrials };
  return runForEachItem(RATIONALE_NODE, [{ json: ollamaResponse }], { 'Compute Fit Score': [scored] })[0].json;
}

const GOOD = 'Example Clinical Research in Bengaluru runs oncology trials through Phase III and '
  + 'has a named Managing Director reachable on LinkedIn, which makes the demo offer easy to '
  + 'place. Headcount is not stated, so fit with the 5-100 band is unconfirmed.';

const good = attach({ response: JSON.stringify({ rationale: GOOD }) });
t.check('a good model rationale replaces the deterministic one',
  good.payload.rationale.indexOf(GOOD) === 0, true);
t.check('...and the breakdown survives the swap',
  good.payload.rationale.indexOf('Score 95/100') !== -1, true);
t.check('...and is marked as model-written', good.payload.rationale_source, 'model');
t.check('the score is untouched by the rationale step', good.payload.fit_score, 95);

const failed = attach({ error: 'connect ECONNREFUSED' });
t.check('an Ollama outage does NOT drop the lead -- the score is already final',
  failed.write, true);
t.check('...it falls back to the deterministic rationale',
  failed.payload.rationale_source, 'deterministic');
t.check('...and records why', failed.payload.rationale_note.indexOf('ollama call failed') === 0, true);
t.check('...and the score is still written', failed.payload.fit_score, 95);

t.check('an unparseable response falls back',
  attach({ response: 'I think this lead looks quite good!' }).payload.rationale_source, 'deterministic');
t.check('a fenced JSON block is rescued rather than lost',
  attach({ response: 'Sure:\n```json\n{"rationale":"' + GOOD + '"}\n```' }).payload.rationale_source, 'model');
t.check('a one-word answer is treated as a failed generation, not a rationale',
  attach({ response: JSON.stringify({ rationale: 'Good.' }) }).payload.rationale_source, 'deterministic');
t.check('a missing rationale field falls back',
  attach({ response: JSON.stringify({ summary: GOOD }) }).payload.rationale_source, 'deterministic');

t.done();

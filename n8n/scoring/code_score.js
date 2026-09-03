// Compute Fit Score — n8n Code node (Run Once for Each Item).
//
// Folds the ClinicalTrials.gov result into the factors Evaluate Lead already
// computed, sums the weighted score, and builds both:
//   - `payload`, ready to write, carrying a deterministic rationale; and
//   - `prompt` / `system_prompt`, for the model to write a better one.
//
// The deterministic rationale is not a placeholder — it is what ships for any
// lead the model must not be asked about (Section 9 Workflow 4's grounding
// guard: a small model fabricates when under-informed).

const ev = $('Evaluate Lead').item.json;
const resp = $input.item.json;

const WEIGHT_TRIALS = 20;

// ---------------------------------------------------------------------------
// ClinicalTrials.gov result
// ---------------------------------------------------------------------------
//
// The HTTP node continues on error, so a failed call arrives without
// `totalCount`. That is NOT scored as "no trials": it would silently mark every
// lead 20 points low for the length of an API outage, and those scores would
// look perfectly valid in the table afterwards. The lead is dropped instead and
// stays 'enriched' for the next cron run — the same self-healing shape
// Workflow 2 uses for an Ollama outage.
const totalCount =
  resp && typeof resp.totalCount === 'number' && isFinite(resp.totalCount)
    ? resp.totalCount
    : null;

if (totalCount === null) {
  return {
    json: {
      write: false,
      lead_id: ev.lead_id,
      domain: ev.domain,
      skip_reason:
        'ClinicalTrials.gov lookup failed: ' +
        JSON.stringify(resp && resp.error ? resp.error : resp).slice(0, 300),
    },
  };
}

const trials = {
  points: totalCount > 0 ? WEIGHT_TRIALS : 0,
  max: WEIGHT_TRIALS,
  basis: totalCount > 0 ? 'confirmed' : 'miss',
  detail:
    totalCount > 0
      ? totalCount + ' recruiting trial' + (totalCount === 1 ? '' : 's') +
        ' registered with them as sponsor'
      : 'no recruiting trials registered under this company as sponsor',
  total_count: totalCount,
};

const factors = Object.assign({}, ev.factors, { trials: trials });

// ---------------------------------------------------------------------------
// Weighted sum
// ---------------------------------------------------------------------------

const ORDER = ['geography', 'trials', 'founder', 'oncology', 'employees', 'site'];
let fitScore = 0;
for (const k of ORDER) fitScore += factors[k] ? factors[k].points : 0;
fitScore = Math.max(0, Math.min(100, Math.round(fitScore)));

// The score breakdown, as one compact line per factor, appended to the stored
// rationale. This is what makes a score auditable in the review queue without
// re-running anything -- and it goes in the rationale text rather than a new
// column because Section 8 locks the `scores` columns and a breakdown is
// review copy, not new state.
const breakdown = ORDER.map(function (k) {
  const f = factors[k];
  return k + ' ' + f.points + '/' + f.max + ' (' + f.basis + ': ' + f.detail + ')';
});
const breakdownLine = 'Score ' + fitScore + '/100 — ' + breakdown.join('; ');

// ---------------------------------------------------------------------------
// Deterministic rationale
// ---------------------------------------------------------------------------
//
// Ships as-is for leads with no readable extraction, and is the fallback if the
// model call fails. States only what the factors actually measured.
function deterministicRationale() {
  const f = ev.facts || {};
  const parts = [];
  parts.push(
    (ev.company_name || ev.domain) +
      (ev.country ? ' (' + ev.country + ')' : '') +
      ' scores ' + fitScore + '/100.'
  );

  const wins = ORDER.filter(function (k) { return factors[k].basis === 'confirmed' && factors[k].points > 0; });
  const gaps = ORDER.filter(function (k) { return factors[k].basis === 'miss'; });
  const unknowns = ORDER.filter(function (k) { return factors[k].basis === 'unknown'; });

  if (wins.length) {
    parts.push('In its favour: ' + wins.map(function (k) { return factors[k].detail; }).join('; ') + '.');
  }
  if (gaps.length) {
    parts.push('Against: ' + gaps.map(function (k) { return factors[k].detail; }).join('; ') + '.');
  }
  if (unknowns.length) {
    parts.push(
      'Unknown (scored at half weight rather than penalised): ' +
        unknowns.map(function (k) { return factors[k].detail; }).join('; ') + '.'
    );
  }
  if (ev.needs_recheck) {
    parts.push(
      'Note: enrichment could not read this site (' + (ev.enrichment_status || 'unknown') +
        ', ' + ev.site_evidence + '), so everything except geography and trial registration is ' +
        'unmeasured. The host did answer, so this is a re-fetch candidate, not a dead domain.'
    );
  }
  return parts.join(' ');
}

// ---------------------------------------------------------------------------
// Rationale prompt
// ---------------------------------------------------------------------------
//
// Section 9: "This rationale is what makes the morning review fast — it must be
// specific, not generic." The prompt therefore hands the model only concrete
// extracted facts and forbids it from adding any, which is the same grounding
// discipline Workflow 4 locks. Nothing here asks the model for a judgement the
// score already encodes; it is writing up a decision, not making one.
const f = ev.facts || {};
const factLines = [];
factLines.push('Company: ' + (ev.company_name || ev.domain));
factLines.push('Domain: ' + ev.domain);
factLines.push('Country: ' + (ev.country || 'unknown'));
if (f.city) factLines.push('City: ' + f.city);
if (f.company_type) factLines.push('Described on its site as: ' + f.company_type);
factLines.push('Therapeutic areas: ' + (f.areas && f.areas.length ? f.areas.join(', ') : 'none stated'));
factLines.push('Trial phases: ' + (f.phases && f.phases.length ? f.phases.join(', ') : 'none stated'));
factLines.push(
  'Founder/MD: ' +
    (f.founder_name
      ? f.founder_name + (f.founder_title ? ', ' + f.founder_title : '') +
        (f.founder_linkedin ? ' (LinkedIn profile found)' : ' (no LinkedIn profile found)')
      : 'not named on the site')
);
factLines.push(
  'Headcount: ' + (f.employee_estimate !== null && f.employee_estimate !== undefined
    ? 'about ' + f.employee_estimate
    : 'not stated')
);
factLines.push('Recruiting trials on ClinicalTrials.gov as sponsor: ' + totalCount);
if (f.site_quality_notes) factLines.push('Site notes from extraction: ' + f.site_quality_notes);
factLines.push('Fit score: ' + fitScore + '/100');
factLines.push('Score breakdown: ' + breakdown.join(' | '));

const system_prompt = [
  'You write one-paragraph briefing notes for a B2B sales review queue.',
  'The product being sold is a custom AI chatbot for clinical research organisations:',
  'a $500-1,000 build plus $300/month, with a 48-hour custom demo on the prospect\'s own',
  'knowledge base.',
  '',
  'Write ONE paragraph, 40-70 words, explaining why this specific lead is or is not worth',
  'contacting. Rules:',
  '- Use ONLY the facts given. Never add a fact that is not listed, including trial names,',
  '  client names, headcounts, or specialisms.',
  '- Name at least two specific facts from the list. A paragraph that would read the same',
  '  for any CRO is a failure.',
  '- If something is unknown, you may say so plainly. Do not guess it.',
  '- State the main reservation as well as the case in favour.',
  '- No greeting, no sign-off, no bullet points, no markdown.',
].join('\n');

const prompt =
  'Facts about this lead:\n\n' + factLines.join('\n') +
  '\n\nWrite the one-paragraph briefing note.';

return {
  json: {
    write: true,
    groundable: ev.has_extraction === true,
    lead_id: ev.lead_id,
    domain: ev.domain,
    fit_score: fitScore,
    system_prompt: system_prompt,
    prompt: prompt,
    // Carried separately so the LLM branch can swap the paragraph and keep the
    // breakdown, without either node holding a second copy of the join.
    breakdown_line: breakdownLine,
    payload: {
      lead_id: ev.lead_id,
      fit_score: fitScore,
      disqualified: false,
      disqualify_reason: null,
      rationale: deterministicRationale() + '\n\n' + breakdownLine,
      rationale_source: 'deterministic',
    },
  },
};

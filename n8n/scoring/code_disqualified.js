// Build Disqualified Payload — n8n Code node (Run Once for Each Item).
//
// Disqualified leads never reach the model. Every hard disqualifier is a
// deterministic rule, so the reason it fired is a fact, not a judgement --
// spending tokens restating it would be the exact thing build rule 3 forbids,
// and a generated paragraph would be less precise than the rule's own text.
//
// The reason string is written for a human reading a review queue, per
// Section 9's "visibility over spot-checking" decision: disqualified leads must
// be inspectable, not merely hidden, so misclassification surfaces across every
// batch instead of in a one-off audit.

const ev = $input.item.json;

// fit_score 0 is unambiguous here: no lead can reach 0 on merit (the two
// null-neutral factors alone floor a scored lead at 15), so 0 in this column
// always and only means "disqualified, never scored". That keeps the review
// queue sortable on one column without a NULL special case.
const DISQUALIFIED_SCORE = 0;

const codes = Array.isArray(ev.disqualify_codes) ? ev.disqualify_codes : [];

const rationale =
  (ev.company_name || ev.domain) +
  (ev.country ? ' (' + ev.country + ')' : '') +
  ' was disqualified before scoring, on ' +
  (codes.length === 1 ? 'one hard disqualifier' : codes.length + ' hard disqualifiers') +
  ': ' + codes.join(', ') + '. ' +
  ev.disqualify_reason +
  ' No fit score was computed and no ClinicalTrials.gov lookup was made — hard ' +
  'disqualifiers run first, so a disqualified lead costs neither an API call nor a token.';

return {
  json: {
    write: true,
    lead_id: ev.lead_id,
    domain: ev.domain,
    fit_score: DISQUALIFIED_SCORE,
    payload: {
      lead_id: ev.lead_id,
      fit_score: DISQUALIFIED_SCORE,
      disqualified: true,
      disqualify_reason: ev.disqualify_reason,
      rationale: rationale,
      rationale_source: 'deterministic',
    },
  },
};

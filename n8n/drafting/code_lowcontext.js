// Build Low-Context Drafts -- n8n Code node (Run Once for Each Item).
//
// The other side of the grounding guard. Section 9, Workflow 4:
//
//   "If the record lacks at least two specific facts ... the draft is flagged
//    `low-context` and skipped rather than invented."
//
// "Skipped" cannot mean "write nothing". A lead that produces no row stays at
// status='contact_found' and is re-picked, re-looked-up and re-skipped by every
// subsequent cron tick, forever. That is the same trap Workflow 3b's tombstone
// was built to avoid, and the answer is the same: record the outcome.
//
// So the lead does advance to 'drafted', and it does appear in the review
// queue -- carrying a row that says plainly what was missing instead of a
// paragraph a 9B model made up to fill the gap. Section 9's own "visibility
// over spot-checking" decision (taken for is_cro disqualifications) points the
// same way: surface the miss to the human, do not hide it.
//
// No model call happens on this branch. There is nothing to write from.

const src = $input.item.json;

const LABELS = {
  therapeutic_area: 'a therapeutic area from the locked taxonomy',
  named_trial: 'a recruiting trial registered with them as sponsor',
  city: 'a city',
  founder_name: 'a named founder or MD',
};

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

const have = (src.fact_kinds || []).map(function (k) { return LABELS[k] || k; });
const missing = (src.missing_facts || []).map(function (k) { return LABELS[k] || k; });

const note = [
  'NOT DRAFTED -- low context.',
  '',
  'The grounding guard (Master Ref Section 9, Workflow 4) requires at least two',
  'specific facts in the enrichment record before a draft may be written. This',
  'lead has ' + (have.length || 'none') + '.',
  '',
  'Found: ' + (have.length ? have.join('; ') : 'nothing usable'),
  'Missing: ' + (missing.length ? missing.join('; ') : 'nothing'),
  '',
  'Nothing was generated, deliberately -- a model given one fact invents the',
  'rest. To rescue this lead: re-enrich it, or write the outreach by hand from',
  'the company site. Reject it if it is not worth either.',
].join('\n');

return {
  json: Object.assign({}, {
    lead_id: src.lead_id,
    domain: src.domain,
    company_name: src.company_name,
    fit_score: src.fit_score,
  }, {
    write: true,
    low_context: true,
    fact_count: src.fact_count,
    fact_kinds: src.fact_kinds,
    missing_facts: src.missing_facts,
    payload: {
      lead_id: src.lead_id,
      advance: true,
      drafts: [
        {
          channel: 'email',
          // The addressing mode is still recorded. It is real information -- it
          // tells the reviewer whether a manual email has anywhere to go.
          variant: 'low-context/' + str(src.email_addressing),
          subject: null,
          body: note,
        },
        {
          channel: 'linkedin',
          variant: 'low-context/' + str(src.linkedin_addressing),
          subject: null,
          body: note,
        },
      ],
    },
  }),
};

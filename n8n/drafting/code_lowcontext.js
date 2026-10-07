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
//
// Since 2026-10-07 the same branch also carries the other reason a lead is not
// drafted: its country has no Send business-hours clock (Section 12), so no
// email to it could ever go out. Assess Grounding sets `no_send_clock` and the
// note says exactly that instead of naming missing facts -- the facts are not
// the problem here, and a reviewer told "low context" would go looking for the
// wrong thing. Both notes leave the queue the same way, for the same reason.

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

const noSendClock = src.no_send_clock === true;

const note = noSendClock
  ? [
    'NOT DRAFTED -- no business-hours clock for ' + (str(src.country) || "this lead's country") + '.',
    '',
    "Workflow 6 sends only inside the recipient's business hours, and Section 12's",
    'clock table has no entry for this country -- so an email to this lead could',
    'never be sent, however good the draft. Nothing was generated and no model',
    'call was paid for.',
    '',
    'To rescue this lead: add the country to Section 12\'s "Business-hours clocks"',
    'table and to COUNTRY_CLOCKS in n8n/sendtrack/code_decide.js, rebuild, re-import',
    'Send and Drafting, then set the lead back to contact_found. Reject it if it is',
    'not worth that.',
    '',
    'The LinkedIn DM is a separate matter: a human sends those by hand (Section 6),',
    'so this lead can still be reached that way with no clock at all.',
  ].join('\n')
  : [
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

// The variant prefix is what every downstream reader keys on: the Approval Gate
// holds it, the digest labels it, and the send path refuses it. A no-clock note
// gets its own prefix so none of them calls it low context.
const prefix = noSendClock ? 'no-send-clock/' : 'low-context/';

return {
  json: Object.assign({}, {
    lead_id: src.lead_id,
    domain: src.domain,
    company_name: src.company_name,
    country: src.country,
    fit_score: src.fit_score,
  }, {
    write: true,
    low_context: true,
    no_send_clock: noSendClock,
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
          variant: prefix + str(src.email_addressing),
          subject: null,
          body: note,
        },
        {
          channel: 'linkedin',
          variant: prefix + str(src.linkedin_addressing),
          subject: null,
          body: note,
        },
      ],
    },
  }),
};

// Build Follow-Up -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 6: "if no reply after 6 days, a follow-up draft from the
// fixed template. Maximum two follow-ups, then mark lost."
//
// Since 2026-10-19 (operator brief) a follow-up is a fixed template, not a
// composition: no model call, no claim check. build_workflow.py ships this
// node as drafting's code_template.js -- every line above its "Node body"
// marker, verbatim -- followed by this file, so a follow-up is written by the
// same template functions as a first touch (renderFollowUp: the same greeting
// rules, the same cleaned company name, #1 closing with the lead's own arm's
// close) and judged by the same gate(): it approves itself unless it has no
// verified deliverable address, no Send clock or a blocklisted domain.
//
// Threading is as before: "Re:" the first subject, the first email quoted below
// "On <date>, Abdullah Amir wrote:". A mark-lost row carries no draft and goes
// straight to Write Follow-Up.

// Section 9, parsed from the doc at build time.
const MAX_FOLLOW_UPS = 2;

// Section 12's clock table as country names, substituted at build time -- the
// same list Workflow 4's gate holds a no-clock draft on.
const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;
const CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

function followUpAction(r, item) {
  if (r.action === 'mark-lost') {
    return {
      json: { lead_id: r.lead_id, payload: { lead_id: r.lead_id, action: 'mark-lost', draft: null } },
      pairedItem: { item: item },
    };
  }
  const n = Number(r.next_follow_up);
  if (!(n >= 1 && n <= MAX_FOLLOW_UPS)) {
    throw new Error('no follow-up #' + r.next_follow_up + ' (max ' + MAX_FOLLOW_UPS + ')');
  }
  // The template is for CROs (Section 9, Workflow 4). Find Due Follow-Ups never
  // returns anything else, so reaching this line means the query changed:
  // nothing is written, and the lead is simply not followed up.
  if (str(r.company_type) !== 'CRO') return null;
  const fu = renderFollowUp({
    lead_id: r.lead_id,
    n: n,
    company_name: r.company_name,
    domain: r.domain,
    email: r.email,
    contact_name: r.contact_name,
    first_subject: r.first_subject,
    first_body: r.first_body,
    first_sent_at: r.first_sent_at,
  });
  const decision = gate({
    auto_approve: r.auto_approve_email === true,
    email: r.email,
    verified: r.verified === true,
    country: r.country,
    domain: r.domain,
    lead_domain_blocked: r.lead_domain_blocked === true,
    recipient_domain_blocked: r.recipient_domain_blocked === true,
  }, CLOCK_KEYS);
  return {
    json: {
      lead_id: r.lead_id,
      follow_up: n,
      arm: fu.arm,
      decision: decision,
      payload: {
        lead_id: r.lead_id,
        action: 'draft-follow-up',
        draft: {
          channel: 'email',
          variant: withTags(fu.variant, decision.tags),
          subject: fu.subject,
          body: fu.body,
          status: decision.status,
          approved_by: decision.approved_by,
          hold_reason: decision.hold_reason,
        },
      },
    },
    pairedItem: { item: item },
  };
}

// ---------------------------------------------------------------------------
// Node body (Run Once for All Items)
// ---------------------------------------------------------------------------

// An empty queue can arrive here as one { success: true } item: the Postgres
// node emits that placeholder when it does not classify a statement as a
// SELECT, and a WITH ... SELECT is exactly the case its parser has to guess at.
// Keeping only rows that carry a lead_id turns that into zero items, so nothing
// downstream runs -- instead of this node throwing on a row that was never a
// lead.
const out = [];
$input.all().forEach(function (it, i) {
  if (!it.json || it.json.lead_id === undefined || it.json.lead_id === null) return;
  const built = followUpAction(it.json, i);
  if (built) out.push(built);
});
return out;

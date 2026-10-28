// Build Site Follow-Up -- n8n Code node (Run Once for All Items).
//
// Follow-Ups for the research-site niche (migration 025): follow-up #1 and #2
// in the operator's site wording of 2026-10-28, written and gated by the same
// functions as the CRO follow-up (Follow-Ups, n8n/sendtrack/code_followup.js).
// The build places drafting's code_template.js rules section and
// code_site_template.js's rules section verbatim above this file. Due dates,
// the two-follow-up maximum, mark-lost and the write are Follow-Ups' own SQL,
// unchanged; only the niche filter and the words differ.

const MAX_FOLLOW_UPS = 2;

const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;
const CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

function siteFollowUpAction(r, item) {
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
  // Find Due Site Follow-Ups returns only this niche's leads; anything else
  // means the query changed, and nothing is written.
  if (str(r.niche) !== 'research_site' || !SITE_ENTITY[str(r.company_type)]) return null;
  const fu = renderSiteFollowUp({
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
    auto_approve: r.auto_approve_email === true && r.niche_auto_approve === true,
    email: r.email,
    verified: r.verified === true,
    country: r.country,
    domain: r.domain,
    lead_domain_blocked: r.lead_domain_blocked === true,
    recipient_domain_blocked: r.recipient_domain_blocked === true,
  }, CLOCK_KEYS);
  if (r.auto_approve_email === true && r.niche_auto_approve !== true && decision.hold_reason === AUTO_APPROVE_OFF) {
    decision.hold_reason = 'auto-approve-off: niches.auto_approve is off for research_site, so every email waits for a person';
  }
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
// Node body
// ---------------------------------------------------------------------------

const out = [];
$input.all().forEach(function (it, i) {
  if (!it.json || it.json.lead_id === undefined || it.json.lead_id === null) return;
  const built = siteFollowUpAction(it.json, i);
  if (built) out.push(built);
});
return out;

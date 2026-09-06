// Build Contact Payload (Apollo) — n8n Code node (Run Once for Each Item).
//
// Folds the People Enrichment response into the candidate Pick Best Contact
// already chose. The search that produced the candidate returns no addresses at
// all, by design on Apollo's side; this is the call that costs the credit, so it
// runs exactly once per lead and only for a candidate that already cleared the
// title and domain gates.

const pick = $('Pick Best Contact').item.json;
const resp = $input.item.json;
const cand = pick.candidate || {};

// Same rule as Pick Best Contact and as Workflow 3's ClinicalTrials.gov step: a
// call that did not answer is not evidence. The lead is dropped with no row
// written and stays 'scored' for the next run, rather than being tombstoned as
// though Apollo had looked and found nothing.
if (!resp || typeof resp !== 'object' || resp.error || resp.error_code || !resp.person) {
  // JSON.stringify, not String(): n8n wraps an HTTP failure as an object, and
  // String() renders that '[object Object]' -- which turns the one field an
  // operator needs (why did Apollo refuse?) into nothing at all. Same shape
  // code_score.js uses for the ClinicalTrials.gov failure path.
  const err = resp && (resp.error || resp.message);
  const detail = err
    ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300)
    : 'no person object in the response';
  return {
    json: {
      write: false,
      lead_id: pick.lead_id,
      domain: pick.domain,
      source: 'apollo',
      credits_spent: 0,
      skip_reason: 'Apollo people-match did not answer: ' + detail,
    },
  };
}

const p = resp.person;

// Apollo returns this literal placeholder when the account cannot see the
// address. Writing it would put an undeliverable string in contacts.email that
// looks exactly like a real one three workflows downstream.
function usableEmail(e) {
  if (typeof e !== 'string') return null;
  const s = e.trim();
  if (!s || s.indexOf('@') < 1) return null;
  if (/^email_not_unlocked@/i.test(s)) return null;
  if (/^(unavailable|not_unlocked|locked)$/i.test(s)) return null;
  return s;
}

const email = usableEmail(p.email);
const status = typeof p.email_status === 'string' ? p.email_status.toLowerCase() : null;

// The one flag Workflow 6 will gate a send on, so it means one thing: the
// address is confirmed, not inferred. Apollo's own 'guessed' status is a
// pattern-derived address -- real enough to keep, not enough to call verified.
const verified = !!email && status === 'verified';

const name = p.name || [p.first_name, p.last_name].filter(Boolean).join(' ') || cand.name || null;
const title = p.title || cand.title || null;
const linkedin = p.linkedin_url || cand.linkedin_url || null;

return {
  json: {
    write: true,
    lead_id: pick.lead_id,
    domain: pick.domain,
    source: 'apollo',
    credits_spent: 1,
    email_status: status,
    payload: {
      lead_id: pick.lead_id,
      name: name,
      title: title,
      email: email,
      linkedin_url: linkedin,
      apollo_id: p.id || pick.apollo_id || null,
      verified: verified,
      // A person with a LinkedIn URL and no email is still contactable -- Section
      // 6 sends LinkedIn by hand, and Workflow 4 drafts that variant separately.
      advance: !!(email || linkedin),
      note:
        'Apollo people-match on a ' +
        (pick.corroborated ? 'site-corroborated ' : '') +
        'tier-' + pick.match_tier + ' title; email_status=' + (status || 'none') +
        '; ' + pick.candidates_in_role + ' of ' + pick.candidates_considered +
        ' indexed people were in an ICP role at this domain',
    },
  },
};

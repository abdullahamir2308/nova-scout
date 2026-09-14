// Check SMTP Result -- n8n Code node (Run Once for Each Item).
//
// The Send Email node runs with "continue on error", so a failed send arrives
// here as an ordinary item ({ error: "..." }) instead of stopping the
// execution. That matters: Claim Send has already flipped the draft to 'sent'
// and written a claim row, and a hard stop would strand both -- the draft would
// read as sent when nothing went out.
//
// Three outcomes:
//   ok        -- the server accepted the message for this recipient. Confirm.
//   account   -- nothing went out, and not because of this draft: auth, TLS,
//                DNS, a timeout, a 4xx deferral. The draft goes back to
//                'approved' and the next tick retries.
//   recipient -- the server permanently refused THIS address. Retrying cannot
//                help and would stall every draft queued behind it, so the
//                draft goes back to 'pending' -- the reviewer's queue -- tagged
//                `+smtp-rejected`, where a human can see it and fix the contact.

const OWN_DOMAIN = __OWN_DOMAIN__;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function domainOf(addr) {
  const s = str(addr).toLowerCase();
  const i = s.lastIndexOf('@');
  return i === -1 ? '' : s.slice(i + 1).replace(/[>\s]+$/, '');
}

function lowerList(v) {
  return (Array.isArray(v) ? v : []).map(function (a) {
    return str(a && typeof a === 'object' ? a.address : a).toLowerCase();
  });
}

// Checked in this order. A sender, relay or auth problem is ours even when the
// server phrases it as "all recipients were rejected"; only after that does a
// recipient-shaped failure count as the address's fault.
const OURS = /sender address|mail from|relay|auth|login|credential|\b53[0-8]\b|\b5\.7\.\d+\b/i;
const THE_ADDRESS = /recipient address rejected|user unknown|unknown user|no such user|mailbox (?:unavailable|not found|does not exist)|does not exist|invalid recipient|all recipients were rejected|\b5\.1\.\d+\b/i;
const TRANSIENT = /tls|ssl|certificate|timeout|timed out|connect|econn|enotfound|eai_again|esocket|etimedout|greeting|\b4\d\d\b|\b4\.\d+\.\d+\b/i;

function classifyFailure(text) {
  const t = str(text);
  if (OURS.test(t)) return 'account';
  if (THE_ADDRESS.test(t)) return 'recipient';
  if (TRANSIENT.test(t)) return 'account';
  if (/\b5\d\d\b/.test(t)) return 'recipient';
  // Unknown: assume it was not the address. A retry costs one tick; wrongly
  // bouncing a good draft back to review costs a human's attention.
  return 'account';
}

function checkResult(claim, resp) {
  const to = str(claim.to_addr).toLowerCase();
  const r = resp && typeof resp === 'object' ? resp : {};
  const accepted = lowerList(r.accepted);
  const rejected = lowerList(r.rejected);
  const messageId = str(r.messageId);
  let error = str(r.error);
  if (!error) {
    if (rejected.indexOf(to) !== -1) error = 'rejected by server: ' + str(r.response);
    else if (accepted.indexOf(to) === -1) error = 'recipient not in the accepted list: ' + JSON.stringify(r.accepted || null);
    else if (!messageId) error = 'server accepted the message but no Message-ID came back';
  }
  const ok = !error;
  const failure = ok ? null : classifyFailure(error);
  return {
    ok: ok,
    failure: failure,
    payload: {
      outreach_id: claim.outreach_id,
      draft_id: claim.draft_id,
      lead_id: claim.lead_id,
      to_addr: to,
      message_id: ok ? messageId : null,
      external: domainOf(to) !== OWN_DOMAIN,
      clock: claim.clock,
      smtp_response: str(r.response).slice(0, 500) || null,
      error: error ? error.slice(0, 500) : null,
      failure: failure,
    },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const claim = $('Claim Send').item.json;
return { json: checkResult(claim, $input.item.json) };

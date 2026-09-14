// Build Operator Notification -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 6, with HubSpot deferred: "a short internal notification
// email fires to the operator's own inbox (not the outreach mailbox) the moment
// a reply lands -- reusing the same SMTP capability this workflow needs anyway".
//
// Built from the row Record Inbound returned. That row is only marked `notify`
// the FIRST time a message is recorded (inbound_messages is keyed on
// Message-ID), so an IMAP re-delivery cannot notify twice.

// NOVASCOUT_OPERATOR_EMAIL, baked at build time. Empty means nobody to tell:
// the reply is still recorded, the lead still leaves the send queue and the
// opt-out is still blocklisted -- only this email is skipped, and the build
// says so loudly.
const OPERATOR_EMAIL = __OPERATOR_EMAIL__;

const LABEL = { 'reply': 'REPLY', 'opt-out': 'OPT-OUT', 'bounce': 'BOUNCE' };

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function notification(r) {
  const what = LABEL[r.classification] || str(r.classification).toUpperCase();
  const who = str(r.company_name) || str(r.domain) || str(r.from_addr);
  const lines = [];
  if (r.classification === 'opt-out') {
    const blocked = Array.isArray(r.blocklisted) && r.blocklisted.length ? r.blocklisted.join(', ') : 'nothing new (already listed)';
    lines.push('Opt-out detected -- matched "' + str(r.opt_out_keyword) + '". Blocklisted: ' + blocked + '.');
    lines.push('Nothing more will be sent to this domain. If they did not mean it, delete the row from `blocklist` by hand.');
  } else if (r.classification === 'bounce') {
    lines.push('A send to this lead bounced. Its follow-ups are stopped; the contact address needs checking.');
  } else {
    lines.push('A lead replied. Its follow-ups are stopped and it is in the replied_queue view in NocoDB.');
  }
  lines.push('');
  lines.push('Company:   ' + (str(r.company_name) || '?'));
  lines.push('Domain:    ' + str(r.domain) + (str(r.country) ? ' (' + str(r.country) + ')' : ''));
  lines.push('Fit score: ' + (r.fit_score === null || r.fit_score === undefined ? '?' : r.fit_score));
  lines.push('Contact:   ' + (str(r.contact_email) || '?'));
  lines.push('From:      ' + str(r.from_addr));
  lines.push('Subject:   ' + str(r.subject));
  lines.push('Received:  ' + str(r.received_at));
  lines.push('Matched:   ' + str(r.matched_by));
  lines.push('');
  lines.push('What they wrote:');
  lines.push('');
  lines.push(str(r.body_excerpt) || '(no text above the quoted thread)');
  return {
    notify: Boolean(r.notify) && OPERATOR_EMAIL !== '',
    notify_to: OPERATOR_EMAIL,
    subject: ('[Nova Scout] ' + what + ' from ' + who).slice(0, 200),
    text: lines.join('\n'),
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

return { json: notification($input.item.json) };

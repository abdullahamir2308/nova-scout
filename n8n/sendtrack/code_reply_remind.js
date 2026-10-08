// Build Reminder -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 7: "Remind me after 4 hours." A one-time code sits in the
// mailbox for at most 48 hours (migration 017's CHECK) and then stops working,
// so a drafted reply nobody answered is a prospect left waiting and a code that
// quietly dies. This nudges, and keeps nudging every REMIND_HOURS while the
// code is still open -- the same shape as the IMAP health check's reminder,
// which repeats every 6 hours while a problem lasts.
//
// "Find Due Reminders" decides what is due, in SQL, from
// `coalesce(reminded_at, issued_at)` -- so the first reminder lands 4 hours
// after the code was issued and each later one 4 hours after the last. The last
// one before the window closes says how little time is left.
//
// It covers BOTH kinds of code, because both are the operator waiting on the
// same decision: a `reply` drafted for a prospect who wrote to us, and an
// `email-hold` -- a first touch or follow-up the claim check held after its
// repairs, which the daily digest listed with a code of its own.
//
// Recorded only once SMTP accepted it ("Record Reminder"), so a reminder that
// could not go out is sent on the next tick instead of being lost.

const REMIND_ADDRESS = /^[^\s@<>(),;:"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

const KIND_LABEL = {
  'reply': 'a reply to a prospect who wrote to us',
  'email-hold': 'an email the claim check held for a person',
};

function mStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function remindAddress(r) {
  const a = mStr(r.operator_email).toLowerCase();
  return REMIND_ADDRESS.test(a) ? a : '';
}

function hoursBetween(from, to) {
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  if (isNaN(a) || isNaN(b)) return null;
  return Math.round((b - a) / 3600000);
}

function reminder(r) {
  const to = remindAddress(r);
  const code = mStr(r.code);
  const who = mStr(r.company_name) || mStr(r.domain) || ('lead ' + mStr(r.lead_id));
  const open = hoursBetween(r.issued_at, r.now);
  const left = hoursBetween(r.now, r.expires_at);
  const kind = mStr(r.kind);

  const lines = [
    'Still waiting on you: ' + (KIND_LABEL[kind] || kind) + '.',
    '',
    'Company:   ' + who + ' (' + mStr(r.domain) + ', ' + mStr(r.country) + ')',
    'Draft:     ' + mStr(r.draft_id) + ' (' + mStr(r.channel) + ', ' + mStr(r.variant) + ', still pending)',
    'Code:      ' + code,
    'Issued:    ' + mStr(r.issued_at) + (open === null ? '' : '  (' + open + ' h ago)'),
    'Expires:   ' + mStr(r.expires_at) +
      (left === null ? '' : (left <= 0 ? '  (expired)' : '  (' + left + ' h left)')),
    '',
  ];

  if (kind === 'reply') {
    lines.push('WHAT THEY WROTE');
    lines.push('');
    lines.push(mStr(r.their_text) || '(no text above the quoted thread)');
    lines.push('');
  } else if (mStr(r.hold_reason)) {
    lines.push('Why it was held:');
    lines.push('  ' + mStr(r.hold_reason));
    lines.push('');
  }

  lines.push('THE DRAFT');
  lines.push('');
  if (mStr(r.subject)) lines.push('Subject: ' + mStr(r.subject));
  lines.push('');
  lines.push(mStr(r.body));
  lines.push('');
  lines.push('Reply with one of these as the first line:');
  lines.push('');
  lines.push('  APPROVE ' + code);
  lines.push('  REJECT ' + code);
  lines.push('  EDIT ' + code + '   ...then your own text');
  lines.push('');
  lines.push('When the code expires nothing is sent and the draft stays pending; the next');
  lines.push('reminder carries a fresh code.');
  lines.push('');
  lines.push('-- Nova Scout (Workflow 7). Sent only to this address; it uses no warm-up slot.');

  return {
    notify: to !== '' && code !== '',
    notify_to: to,
    subject: ('[Nova Scout] still waiting: ' + who + ' -- ' + code).slice(0, 200),
    text: lines.join('\n'),
    record: { code: code },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

return { json: reminder($input.item.json) };

// Build Review Email -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 7: "Save it as a pending draft and email me the reply, the
// proposal and a one-time code." This is that email.
//
// Built from the row "Write Reply Draft & Issue Code" returned, so everything in
// it is what the database actually holds: the draft is `pending`, the code is
// the one `reply_approvals` stores, and the expiry is the one its CHECK bounded
// to 48 hours. The operator's address comes on the same row, read at runtime
// from `settings` (migration 008), so no address is baked into this workflow.
//
// It shows four things, in this order:
//   1. WHAT THEY WROTE -- the text above the quoted thread, verbatim.
//   2. THE PROPOSED REPLY -- the exact bytes that would be sent, frame and all,
//      so there is nothing to imagine.
//   3. WHAT IT DOES NOT ANSWER -- every topic they raised that no confirmed
//      claim and no record fact can settle (pricing, integrations, languages,
//      security, timelines), plus whatever the drafter flagged. This is the half
//      of the email the operator is the only one who can act on.
//   4. THE THREE COMMANDS, each with the code, and what each one does.
//
// Like a reply notification and the daily digest it goes only to the operator's
// own inbox, so it uses no warm-up slot.

// One bare address -- the build's rule, and migration 008's CHECK.
const REVIEW_ADDRESS = /^[^\s@<>(),;:"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

function vStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function vList(v) {
  return Array.isArray(v) ? v.map(vStr).filter(Boolean) : [];
}

function reviewAddress(r) {
  const a = vStr(r.operator_email).toLowerCase();
  return REVIEW_ADDRESS.test(a) ? a : '';
}

function reviewEmail(r) {
  const to = reviewAddress(r);
  const v = (r.review && typeof r.review === 'object') ? r.review : {};
  const code = vStr(r.code);
  const who = vStr(v.company_name) || vStr(v.domain) || ('lead ' + vStr(r.lead_id));
  const flags = vList(v.flags);
  const deferred = vList(v.deferred);
  const flag = vStr(v.flag);

  const lines = [
    who + ' (' + vStr(v.domain) + ', ' + vStr(v.country) + ') replied, and this is the answer I drafted.',
    'Nothing is sent until you say so.',
    '',
    'Draft:     ' + vStr(r.draft_id) + ' (pending, channel email, variant reply)',
    'Lead:      ' + vStr(r.lead_id),
    'Reply to:  ' + vStr(v.from_addr) + '   (the address that wrote, not the contact on file)',
    'Received:  ' + vStr(v.received_at),
    'Length:    ' + vStr(v.words) + ' words' + (v.claims && v.claims.length ? ', claims ' + vList(v.claims).join('.') : ''),
    'Code:      ' + code + '   (expires ' + vStr(r.expires_at) + ', works once)',
    '',
    '================================================================',
    'WHAT THEY WROTE',
    '================================================================',
    '',
    vStr(v.their_text) || '(no text above the quoted thread)',
    '',
    '================================================================',
    'THE PROPOSED REPLY -- exactly what would be sent',
    '================================================================',
    '',
    'Subject: ' + vStr(r.subject),
    '',
    vStr(r.body),
    '',
    '================================================================',
    'WHAT IT DOES NOT ANSWER, AND WHAT TO LOOK AT',
    '================================================================',
    '',
  ];

  if (deferred.length) {
    lines.push('They asked about this, and nothing we have confirmed covers it, so the draft says');
    lines.push('we will confirm and comes to you instead:');
    deferred.forEach(function (d) { lines.push('  - ' + d); });
    lines.push('');
    lines.push('You know these answers. EDIT is how they get in.');
    lines.push('');
  } else {
    lines.push('Nothing they asked falls outside the confirmed claims and their own record.');
    lines.push('');
  }
  if (flag) {
    lines.push('Flagged by the drafter: ' + flag);
    lines.push('');
  }
  if (flags.length) {
    lines.push('Rule tags on this draft (Workflow 4\'s own rules, run over the reply):');
    flags.forEach(function (f) { lines.push('  - ' + f); });
    lines.push('');
  } else {
    lines.push('No rule tags: it passed every check Workflow 4 runs on a first touch.');
    lines.push('');
  }

  lines.push('================================================================');
  lines.push('WHAT TO SEND BACK');
  lines.push('================================================================');
  lines.push('');
  lines.push('Reply to this email with one of these as the first line:');
  lines.push('');
  lines.push('  APPROVE ' + code);
  lines.push('      Sends it as it stands, on the next send tick. A reply skips the warm-up');
  lines.push('      ceiling and business hours; it never skips the blocklist.');
  lines.push('');
  lines.push('  REJECT ' + code);
  lines.push('      Sends nothing. Their message stays on the lead.');
  lines.push('');
  lines.push('  EDIT ' + code);
  lines.push('      ...then your own text, on the rest of that line and the lines after it.');
  lines.push('      Your words replace the draft\'s, under the same greeting and signature,');
  lines.push('      and it is approved. This is the one to use for the questions above.');
  lines.push('');
  lines.push('The code works once and expires ' + vStr(r.expires_at) + '. Only this address can use');
  lines.push('it, and only with SPF and DKIM passing -- anything else is logged and ignored.');
  lines.push('I will remind you in 4 hours if I have not heard back.');
  lines.push('');
  lines.push('-- Nova Scout (Workflow 7, Reply Assistant). Sent only to this address; it uses no');
  lines.push('   warm-up slot.');

  return {
    notify: Boolean(r.written) && to !== '' && code !== '',
    notify_to: to,
    subject: ('[Nova Scout] REPLY DRAFTED for ' + who + ' -- ' + code).slice(0, 200),
    text: lines.join('\n'),
    record: {
      code: code,
      draft_id: r.draft_id,
    },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

return { json: reviewEmail($input.item.json) };

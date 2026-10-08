// Build Command Ack -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 7. The operator answered the reply-review email with
// APPROVE / REJECT / EDIT and a one-time code; "Apply Operator Command" has
// just recorded what arrived and done whatever it was allowed to do. This node
// writes the one-line answer back.
//
// Built from that statement's returned row, never from Decide Command's
// proposal: the statement re-derives the operator address from `settings`,
// re-checks the code through reply_approval_usable() and only then moves the
// draft, so the row is what actually happened. A draft that changed underneath
// between the read and the write is refused there, and this says so.
//
// WHO GETS AN ANSWER, and this is a security decision, not a courtesy:
//
//   * an authenticated operator does -- accepted or refused. A refusal they
//     never see is a code they will keep retyping.
//   * nobody else does. A message that failed SPF or DKIM, or that did not come
//     from the operator's address, is recorded in operator_commands as refused
//     and answered with silence. Answering it would turn the mailbox into an
//     oracle for guessing codes, and would send mail to whatever address a
//     forgery chose to put in From.
//   * a re-delivered message does not, because `recorded` is false the second
//     time -- operator_commands is keyed on Message-ID, so the row was inserted
//     once and this fires once (the role inbound_messages plays for a prospect
//     message).
//
// The answer goes to the address in `settings` (migration 008), read at runtime
// by the statement upstream, so no address is baked into this workflow. Like a
// reply notification it goes only to the operator, so it uses no warm-up slot.

// One bare address -- the build's rule, and migration 008's CHECK.
const ACK_ADDRESS = /^[^\s@<>(),;:"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

function aStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function ackAddress(r) {
  const a = aStr(r.notify_to).toLowerCase();
  return ACK_ADDRESS.test(a) ? a : '';
}

function commandAck(r) {
  const to = ackAddress(r);
  const verb = aStr(r.command).toUpperCase();
  const code = aStr(r.code);
  const accepted = r.accepted === true;
  const lines = [];

  if (accepted) {
    lines.push(verb + ' accepted for draft ' + aStr(r.draft_id) +
      (aStr(r.code_kind) ? ' (' + aStr(r.code_kind) + ')' : '') + '.');
    lines.push('');
    if (verb === 'APPROVE') {
      lines.push('The draft is approved and goes out on the next send tick. A reply skips the');
      lines.push('warm-up ceiling and the recipient\'s business hours; it never skips the');
      lines.push('blocklist (Section 9, Workflow 7).');
    } else if (verb === 'EDIT') {
      lines.push('Your text replaced the draft\'s, under the same greeting and the same signature,');
      lines.push('and the draft is approved. It goes out on the next send tick.');
      lines.push('');
      lines.push('WHAT WILL BE SENT');
      lines.push('');
      lines.push(aStr(r.edited_body) || '(nothing recorded -- check draft ' + aStr(r.draft_id) + ')');
    } else {
      lines.push('The draft is rejected (bad draft) and nothing will be sent. Their message stays');
      lines.push('on the lead. To have a reply drafted again, delete that draft\'s row from');
      lines.push('reply_approvals.');
    }
  } else {
    lines.push((verb || 'That command') + ' was REFUSED.');
    lines.push('');
    lines.push('Reason: ' + (aStr(r.refused_reason) || 'not recorded'));
    lines.push('');
    lines.push('Nothing changed and nothing will be sent. Every command this mailbox receives is');
    lines.push('recorded in operator_commands, accepted or refused.');
  }

  lines.push('');
  lines.push('Code:      ' + (code || '(none in the message)'));
  lines.push('Draft:     ' + (aStr(r.draft_id) || '-') +
    (aStr(r.draft_status) ? ' (' + aStr(r.draft_status) + ')' : ''));
  lines.push('Your mail: ' + aStr(r.message_id));
  lines.push('Subject:   ' + aStr(r.subject));
  lines.push('SPF/DKIM:  ' + (r.spf_pass === true ? 'pass' : 'FAIL') + ' / ' +
    (r.dkim_pass === true ? 'pass' : 'FAIL'));
  lines.push('');
  lines.push('-- Nova Scout (Workflow 7). Sent only to this address; it uses no warm-up slot.');

  return {
    notify: Boolean(r.notify) && to !== '',
    notify_to: to,
    subject: ('[Nova Scout] ' + (accepted ? verb + ' ok' : 'command refused') +
      (code ? ' -- ' + code : '')).slice(0, 200),
    text: lines.join('\n'),
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

return { json: commandAck($input.item.json) };

// Build Follow-Up -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 6: "if no reply after 6 days, generate follow-up draft
// back into the review queue. Maximum two follow-ups, then mark lost."
//
// A template, not a model call. A follow-up adds no new fact about the lead --
// its whole job is "the note below, again" -- so build rule 3 applies, and
// build rule 6 is satisfied by construction: there is nothing to ground. It
// lands in the review queue as 'pending'. A human approves it or it never goes.
//
// The body carries the same compliance footer the first touch did -- opt-out
// line, blank line, signature -- because the send path refuses any body that
// does not.

// Section 5 (verbatim) and Section 9, parsed from the doc at build time.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";
const MAX_FOLLOW_UPS = 2;

const SIGNATURE = __SIGNATURE__;
const SENDER_NAME = __SENDER_NAME__;

// One line per follow-up, and exactly MAX_FOLLOW_UPS of them (checked at build
// time). Neither states a fact about the lead; the first touch is quoted below.
const LINES = {
  1: 'I put together a short one-pager on how Nova works and adapts to your setup — want me to send it over?',
  2: 'One last note on this, then I will leave it. If a demo built on your own material in 48 hours would help, I can set it up, no commitment.',
};

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function greetingOf(firstBody) {
  const first = str(String(firstBody || '').replace(/\r\n?/g, '\n').split('\n')[0]);
  return /^(hi|hello|dear)\b[^\n]{0,40},$/i.test(first) ? first : 'Hello,';
}

function followUpDraft(r) {
  const n = Number(r.next_follow_up);
  if (!LINES[n]) throw new Error('no follow-up template for #' + r.next_follow_up + ' (max ' + MAX_FOLLOW_UPS + ')');
  const firstBody = String(r.first_body || '').replace(/\r\n?/g, '\n').trim();
  const sent = new Date(r.first_sent_at);
  const when = isNaN(sent.getTime()) ? 'earlier' : sent.toUTCString().slice(0, 16);
  const quoted = firstBody.split('\n').map(function (ln) { return ln ? '> ' + ln : '>'; }).join('\n');
  const body = [
    greetingOf(firstBody),
    '',
    LINES[n],
    '',
    OPT_OUT,
    '',
    SIGNATURE,
    '',
    'On ' + when + ', ' + SENDER_NAME + ' wrote:',
    quoted,
  ].join('\n');
  return {
    channel: 'email',
    variant: 'follow-up-' + n,
    subject: 'Re: ' + str(r.first_subject).replace(/^(?:re:\s*)+/i, ''),
    body: body,
  };
}

function followUpAction(r) {
  if (r.action === 'mark-lost') {
    return { payload: { lead_id: r.lead_id, action: 'mark-lost', draft: null } };
  }
  return { payload: { lead_id: r.lead_id, action: 'draft-follow-up', draft: followUpDraft(r) } };
}

// ---------------------------------------------------------------------------
// Node body (Run Once for All Items)
// ---------------------------------------------------------------------------

// An empty queue can arrive here as one { success: true } item: the Postgres
// node emits that placeholder when it does not classify a statement as a
// SELECT, and a WITH ... SELECT is exactly the case its parser has to guess at.
// Keeping only rows that carry a lead_id turns that into zero items, so Write
// Follow-Up simply does not run -- instead of this node throwing on a row that
// was never a lead.
return $input.all()
  .filter(function (it) { return it.json && it.json.lead_id !== undefined && it.json.lead_id !== null; })
  .map(function (it) { return { json: followUpAction(it.json) }; });

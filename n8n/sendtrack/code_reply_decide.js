// Decide Reply Send -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 7: "replies skip the cold ceiling and business hours; the
// blocklist still applies." This is the whole of that sentence, and it is a
// separate lane from Decide Send on purpose -- the cold path is untouched, and
// the two can never argue about which rules apply to which draft, because
// neither can see the other's drafts (Load Send State excludes `reply/`, Load
// Reply Send State selects only `reply/`).
//
// WHAT A REPLY SKIPS, and why each one is right:
//   * the warm-up ceiling (Section 5) -- it protects a cold domain's
//     reputation. An answer to a message somebody sent US is the best mail this
//     mailbox can send, and holding it back for a daily quota would mean not
//     answering a prospect who asked a question.
//   * the recipient's business hours -- they just wrote to us; a reply that
//     waits for 09:00 local can wait sixteen hours to say "here you go".
//   * the pacing floor and the random gate -- both exist so cold sends do not
//     arrive as a synchronised burst. One reply is not a burst.
//   * the "first touch only to a lead never emailed" rule, and `lead-replied`,
//     which are exactly the conditions a reply requires.
//
// WHAT IT DOES NOT SKIP:
//   * the blocklist, on the lead's domain AND on the address we would answer
//     (Section 5: an opt-out is honoured instantly and permanently -- and a
//     prospect who replied and then opted out is a real sequence);
//   * the one-time approval: migration 018 refuses to mark a `reply/` draft
//     approved without one, and the claim below re-checks that the code was
//     really used to approve or edit THIS draft;
//   * the signature, the one-URL cap and an unchanged body;
//   * at-most-once: the draft flips to `sent` and an outreach_log claim is
//     written BEFORE the SMTP call, exactly as on the cold path.
//
// A reply DOES still consume a warm-up slot after the fact, and that is worth
// knowing rather than hiding: it is external mail, so when Mailbox Watch
// mirrors it out of the Sent folder it counts in `mailbox_sent.external` like
// any other send, and that day's cold ceiling has one fewer left. Skipping the
// ceiling means a reply is never BLOCKED by it, not that the mailbox did not
// send a message.

const REPLY_SIGNATURE = __SIGNATURE__;
const REPLY_MAX_URLS = __MAX_URLS__;
const REPLY_URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;
const REPLY_EMAIL_RE = /^[^\s@<>(),;:"']+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;
const MESSAGE_ID_RE = /<[^<>\s@]+@[^<>\s]+>/g;

function dStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function dMs(v) {
  if (v === null || v === undefined || v === '') return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : t;
}

// What gets checked is what gets sent -- Decide Send's own normalisation.
function normaliseBody(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}

function countUrls(v) {
  const m = String(v).match(REPLY_URL_RE);
  return m ? m.length : 0;
}

function idsIn(v) {
  const found = String(v === null || v === undefined ? '' : v).match(MESSAGE_ID_RE) || [];
  return found.filter(function (x, i) { return found.indexOf(x) === i; });
}

// RFC 5322 threading, from what the database really holds (migration 017).
//
// In-Reply-To is the message we are answering -- theirs. References is their
// own References chain with their Message-ID appended, because that is what a
// client that sees this message needs in order to place it. When their
// References header was never stored (every inbound row from before migration
// 017 except lead 26's), fall back to the parsed thread_ids, and failing that
// to the Message-IDs of our own sends on this lead: correct for a two-message
// thread, and honest about a longer one rather than inventing the middle.
function threadHeaders(c) {
  const theirs = idsIn(c.their_message_id)[0] || null;
  let chain = idsIn(c.references_raw);
  if (!chain.length) chain = (Array.isArray(c.thread_ids) ? c.thread_ids : []).map(dStr).filter(Boolean);
  if (!chain.length) chain = (Array.isArray(c.our_message_ids) ? c.our_message_ids : []).map(dStr).filter(Boolean);
  const refs = chain.slice();
  if (theirs && refs.indexOf(theirs) === -1) refs.push(theirs);
  return {
    in_reply_to: theirs,
    references: refs.filter(function (x, i) { return refs.indexOf(x) === i; }),
  };
}

// Why this approved reply draft may not go out, or null if it may.
function ineligibility(c) {
  if (dStr(c.channel) !== 'email') {
    // Section 6 is enforced three times on the cold path and three times here:
    // the query never selects LinkedIn, migration 017's CHECK refuses a
    // `reply/` variant on a LinkedIn draft, and this throws. Reaching this line
    // means the SQL changed.
    throw new Error('Section 6 violation: a ' + JSON.stringify(c.channel) +
      ' draft reached the reply send path (draft ' + c.draft_id + ')');
  }
  if (!/^reply\//.test(dStr(c.variant))) {
    throw new Error('a non-reply draft reached the reply send path (draft ' + c.draft_id +
      ', variant ' + JSON.stringify(c.variant) + ')');
  }
  if (dStr(c.status) !== 'approved') return 'not-approved';
  // Migration 018 will not let a reply be approved without this; the claim
  // re-checks it; and here it is for a third time, so a draft whose code was
  // somehow cleared cannot go out on a stale approval.
  if (!c.approval_used) return 'no-used-approval-code';
  if (['approved', 'edited'].indexOf(dStr(c.approval_outcome)) === -1) {
    return 'approval-outcome:' + (dStr(c.approval_outcome) || 'none');
  }
  if (dStr(c.lead_status) !== 'replied') return 'lead-status:' + dStr(c.lead_status);
  if (!c.inbound_exists) return 'no-inbound-message';
  if (c.lead_domain_blocked) return 'blocklisted-domain';
  if (c.recipient_domain_blocked) return 'blocklisted-recipient-domain';
  if (!REPLY_EMAIL_RE.test(dStr(c.to_addr))) return 'no-valid-address';

  const body = normaliseBody(c.body);
  if (!dStr(c.subject)) return 'no-subject';
  // A reply carries no Section 5 opt-out line -- it answers a message they
  // sent us, inside their own thread (code_reply_assemble.js says why). The
  // signature is still checked, and for the same reason the cold path checks
  // it: a body that does not carry the current one was drafted, or edited,
  // under a different identity.
  if (body.indexOf(REPLY_SIGNATURE) === -1) return 'stale-signature';
  if (countUrls(body) > REPLY_MAX_URLS) return 'too-many-urls';
  if (!threadHeaders(c).in_reply_to) return 'no-thread-headers';
  return null;
}

function decide(state) {
  const clock = dMs(state.clock);
  if (clock === null) throw new Error('Load Reply Send State returned no clock');

  const eligible = [];
  const excluded = [];
  const candidates = (state.candidates || []).slice().sort(function (a, b) {
    return Number(a.draft_id) - Number(b.draft_id);
  });
  for (let i = 0; i < candidates.length; i++) {
    const c = candidates[i];
    const why = ineligibility(c);
    if (why) {
      excluded.push({ draft_id: c.draft_id, lead_id: c.lead_id, domain: c.domain, reason: why });
      continue;
    }
    eligible.push({ draft_id: c.draft_id, lead_id: c.lead_id, domain: c.domain, to_addr: dStr(c.to_addr) });
  }

  const out = {
    send: false,
    reason: null,
    detail: null,
    clock: new Date(clock).toISOString(),
    eligible: eligible,
    excluded: excluded,
    payload: null,
  };
  if (!eligible.length) {
    out.reason = 'no-eligible-reply';
    out.detail = 'No approved reply draft passes the reply send checks.';
    return out;
  }

  // Oldest approved reply first -- Section 7's queue rule. One per tick: a
  // reply is not paced, but there is no reason to open several SMTP
  // conversations in one execution either.
  const pick = eligible[0];
  const c = candidates.filter(function (x) { return x.draft_id === pick.draft_id; })[0];
  const headers = threadHeaders(c);
  out.send = true;
  out.reason = 'send';
  out.detail = 'Sending reply draft ' + c.draft_id + ' to ' + dStr(c.to_addr) + ' (' + dStr(c.domain) +
    ', ' + dStr(c.country) + '), in reply to ' + headers.in_reply_to + '.';
  out.payload = {
    draft_id: c.draft_id,
    lead_id: c.lead_id,
    to_addr: dStr(c.to_addr),
    subject: dStr(c.subject),
    body: normaliseBody(c.body),
    raw_body: c.body,
    in_reply_to: headers.in_reply_to,
    references: headers.references,
    code: dStr(c.code),
    clock: new Date(clock).toISOString(),
  };
  return out;
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const state = $input.first().json;
return [{ json: decide(state) }];

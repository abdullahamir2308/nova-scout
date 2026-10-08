// Operator command parsing -- the library half of Classify Inbound.
//
// Section 9, Workflow 7. The operator approves a drafted reply by answering the
// review email: "APPROVE <code>", "REJECT <code>", or "EDIT <code>" followed by
// the replacement text. This file decides, from one INBOX message, whether it
// LOOKS like such a command and whether its authentication passes.
//
// WHY THIS RUNS BEFORE REPLY CLASSIFICATION. code_classify_reply.js treats "no"
// as the first word of a message as an opt-out, and Record Inbound blocklists
// the matched lead's domain permanently. The operator's answers arrive in the
// same mailbox, and an operator who replies to the review email is replying
// INSIDE the prospect's thread -- the review email quotes it, so the References
// header carries our own Message-ID and Record Inbound's strongest matcher
// (`thread`) would tie that message to the prospect's lead. A one-word "no"
// from the operator would then blocklist a real prospect, permanently, with no
// error anywhere. So the command check is first and it is a hard fork: a
// message that is an operator command is never classified as a prospect reply,
// and a message that is not one never reaches the approval path.
//
// WHAT THIS FILE DOES NOT DECIDE. It never compares the sender with the
// operator's address: that address is runtime data in the `settings` table
// (migration 008) and must not be baked into workflow JSON, so the comparison
// happens in SQL, in the same statement that records the command. This file
// reports `from_addr` and the authentication result and lets that statement
// judge. It fails closed everywhere: an unparseable or absent
// Authentication-Results header is not a pass.

// Section 5 / Section 9: an approval counts only if the message's
// authentication results show SPF and DKIM pass. Measured against the real
// header Zoho writes (n8n execution 2336, the Pharmahungary reply):
//
//   authentication-results: mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com:
//   domain of pharmahungary.com designates 185.51.190.242 as permitted sender)
//   smtp.mailfrom=...; dmarc=pass(p=none dis=none) header.from=pharmahungary.com
//
// so the method results are `<method>=<result>` separated by tabs or semicolons,
// each optionally followed by a parenthesised comment. `spf=pass` inside that
// comment text is a real possibility ("... designates ... as permitted sender"
// does not contain it, but nothing stops a future comment from doing so), so
// the comments are stripped before the methods are read.
//
// ARC-Authentication-Results is deliberately NOT consulted. It records what some
// earlier hop claims to have seen, which is a different and weaker statement
// than what our own mail host verified, and this is the gate on sending mail to
// a prospect.
const AUTH_METHODS = ['spf', 'dkim'];

// APPROVE/REJECT/EDIT and the code, on the first line that carries one. Case is
// ignored on the verb (a phone mail client may capitalise or not) but the code's
// alphabet is upper case only -- migration 017 pins the shape.
const COMMAND_VERBS = ['APPROVE', 'REJECT', 'EDIT'];
const CODE_RE = /\bNS-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{10}\b/;
const COMMAND_RE = /^\s*(approve|reject|edit)\b[\s:,-]*(NS-[23456789abcdefghjkmnpqrstvwxyz]{10})?\s*(.*)$/i;

function opStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// Drop every parenthesised comment, including nested ones, so a method result is
// read from the header's own tokens and never from prose inside a comment.
function stripComments(s) {
  let out = '';
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charAt(i);
    if (c === '(') { depth++; continue; }
    if (c === ')') { if (depth > 0) depth--; continue; }
    if (depth === 0) out += c;
  }
  return out;
}

// One Authentication-Results header (or several, joined) -> { spf: bool, dkim: bool }.
// A method named more than once must pass EVERY time: two headers disagreeing
// about SPF is not a pass.
function authResults(header) {
  const text = stripComments(opStr(header)).toLowerCase();
  const seen = {};
  const re = /([a-z][a-z0-9-]*)\s*=\s*([a-z]+)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const method = m[1];
    const result = m[2];
    if (AUTH_METHODS.indexOf(method) === -1) continue;
    const ok = result === 'pass';
    seen[method] = seen[method] === undefined ? ok : (seen[method] && ok);
  }
  const out = {};
  for (let i = 0; i < AUTH_METHODS.length; i++) {
    out[AUTH_METHODS[i]] = seen[AUTH_METHODS[i]] === true;
  }
  return out;
}

// What the operator wrote, above any quoted thread. freshText() already does
// that cut for prospect replies; the command parser is handed the same text so
// an APPROVE quoted back inside a later message is not re-read as a command.
//
// EDIT's replacement is everything after the code -- the rest of that line and
// every following line of fresh text. Blank means "no replacement given", which
// the caller refuses rather than guessing.
function parseCommand(fresh) {
  const lines = opStr(fresh).replace(/\r\n?/g, '\n').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const m = COMMAND_RE.exec(lines[i]);
    if (!m) continue;
    const verb = m[1].toUpperCase();
    if (COMMAND_VERBS.indexOf(verb) === -1) continue;

    // The verb is on this line. The code is normally on it too; accept it on a
    // following line as well, because a client may wrap a long line. Whatever
    // follows the code -- the rest of its line and every later line -- is the
    // replacement text, for EDIT.
    let code = null;
    let after = '';
    if (m[2]) {
      code = m[2].toUpperCase();
      after = [opStr(m[3])].concat(lines.slice(i + 1)).join('\n');
    } else {
      const tail = lines.slice(i + 1).join('\n');
      const hit = CODE_RE.exec(tail.toUpperCase());
      if (hit) {
        code = hit[0];
        after = tail.slice(hit.index + hit[0].length);
      } else {
        after = [opStr(m[3])].concat(lines.slice(i + 1)).join('\n');
      }
    }
    after = after.trim();
    return {
      is_command: true,
      command: verb,
      code: code,
      // Only EDIT carries a replacement; migration 017 refuses one on the others.
      replacement: verb === 'EDIT' ? (after || null) : null,
    };
  }
  return { is_command: false, command: null, code: null, replacement: null };
}

// The whole decision for one message, as the SQL statement needs it. `sender_ok`
// is left to SQL -- see the header comment.
function operatorCommand(j, fresh) {
  const meta = (j && j.metadata) || {};
  const parsed = parseCommand(fresh);
  const auth = authResults(meta['authentication-results']);

  // Why it would be refused, in the order the operator would want to hear it.
  // `sender` is the one reason this file cannot rule on, so it is reported as a
  // pending check rather than a verdict.
  let refused = null;
  if (parsed.is_command && !parsed.code) refused = 'no one-time code in the message';
  else if (parsed.is_command && !auth.spf) refused = 'the message\'s authentication results do not show SPF pass';
  else if (parsed.is_command && !auth.dkim) refused = 'the message\'s authentication results do not show DKIM pass';
  else if (parsed.is_command && parsed.command === 'EDIT' && !parsed.replacement) {
    refused = 'EDIT with no replacement text after the code';
  }

  return {
    is_command: parsed.is_command,
    command: parsed.command,
    code: parsed.code,
    replacement: parsed.replacement,
    spf_pass: auth.spf,
    dkim_pass: auth.dkim,
    refused_reason: refused,
  };
}

// ---------------------------------------------------------------------------
// The decision -- Decide Command, n8n Code node (Run Once for Each Item).
//
// Everything above this point can be read off one message. The three things
// that cannot -- is the sender the operator's address, does this code exist, is
// it unused and unexpired -- are database facts, and "Load Command Context" has
// just read them in one read-only snapshot. This section turns that snapshot
// into a verdict.
//
// It is NOT the last line of defence. "Apply Operator Command" re-derives the
// operator address from `settings`, re-checks the code through
// reply_approval_usable(), and the table itself CHECKs that `accepted` is only
// possible with SPF, DKIM and the sender all passing (migration 017). A bug
// here can refuse a real approval; it cannot accept a forged one -- the same
// direction of safety as Decide Send and Claim Send.
//
// THREE SEPARATE QUESTIONS, and conflating them is how a prospect's reply would
// get lost, or a prospect's domain blocklisted by the operator's own "no":
//
//   record   -- is this message command-SHAPED? Then it goes into
//               operator_commands, accepted or refused. The refused rows are
//               the point: a spoofed sender, a failed SPF or DKIM, an unknown,
//               expired or reused code (migration 017).
//   handled  -- is it really FROM the operator (SPF + DKIM + the operator's own
//               address)? Then it is operator mail and is never classified as a
//               prospect reply. Anything else falls through to Record Inbound,
//               which is right: a message that merely looks like a command and
//               did not come from the operator may well be a prospect writing.
//   accepted -- handled, and nothing refused it. Only then does a draft move.
//
// A message from the operator that is NOT command-shaped -- a plain "no" --
// records nothing and is handled=false, so it reaches Record Inbound. That
// statement has its own guard: a message from the operator's address is never
// matched to a lead, so the "no" cannot blocklist a prospect (Workflow 6).

// Section 5, substituted at build time: the signature every outbound message
// carries, and the one-URL cap. An EDIT replaces the reply's core text and
// keeps the frame around it, so the operator's own words go out under the same
// signature and the send path's checks still find what they look for.
const CMD_SIGNATURE = __SIGNATURE__;
const CMD_MAX_URLS = __MAX_URLS__;
const CMD_URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;

const CMD_OUTCOME = { APPROVE: 'approved', REJECT: 'rejected', EDIT: 'edited' };

function cmdUrls(v) {
  return (opStr(v).match(CMD_URL_RE) || []).length;
}

function cmdMs(v) {
  if (v === null || v === undefined || v === '') return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : t;
}

// The reply as it will go out, with the operator's text in place of the
// model's. The frame is taken from the stored draft, never regenerated: the
// greeting is its first paragraph, and everything from the signature onward --
// the signature itself and the quoted message being answered -- is kept
// verbatim. So an EDIT changes exactly the words the operator rewrote.
function framedEdit(body, replacement) {
  const b = String(body === null || body === undefined ? '' : body).replace(/\r\n?/g, '\n');
  const at = b.indexOf(CMD_SIGNATURE);
  const tail = at === -1 ? '' : b.slice(at + CMD_SIGNATURE.length);
  const firstBreak = b.indexOf('\n\n');
  const greeting = firstBreak === -1 ? 'Hello,' : b.slice(0, firstBreak).trim();
  return [greeting, '', opStr(replacement), '', CMD_SIGNATURE].join('\n') + tail;
}

function commandDecision(ctx) {
  const cmd = (ctx && ctx.command) || {};
  const isCommand = cmd.is_command === true;
  const spf = cmd.spf_pass === true;
  const dkim = cmd.dkim_pass === true;
  const operator = opStr(ctx.operator_email).toLowerCase();
  const from = opStr(ctx.payload && ctx.payload.from_addr).toLowerCase();
  const senderOk = operator !== '' && from !== '' && from === operator;
  const authenticated = isCommand && spf && dkim && senderOk;
  const verb = opStr(cmd.command).toUpperCase();
  const code = opStr(cmd.code).toUpperCase() || null;
  const now = cmdMs(ctx.now);
  const expires = cmdMs(ctx.code_expires_at);

  let edited = null;
  let refused = null;
  if (!isCommand) {
    refused = 'not an operator command';
  } else if (cmd.refused_reason) {
    // No code in the message, SPF or DKIM not passing, or EDIT with no
    // replacement text: decided from the message alone, above.
    refused = cmd.refused_reason;
  } else if (!senderOk) {
    refused = operator
      ? 'not sent from the operator address'
      : 'no operator address is configured (settings.operator_email)';
  } else if (!ctx.code_found) {
    refused = 'no such one-time code';
  } else if (ctx.code_used_at) {
    refused = 'that one-time code was already used at ' + opStr(ctx.code_used_at) +
      ' (' + opStr(ctx.code_outcome) + ')';
  } else if (expires !== null && now !== null && expires <= now) {
    refused = 'that one-time code expired at ' + opStr(ctx.code_expires_at);
  } else if (opStr(ctx.draft_status) !== 'pending') {
    refused = 'draft ' + opStr(ctx.draft_id) + ' is no longer waiting (it is ' +
      (opStr(ctx.draft_status) || 'gone') + ')';
  } else if (verb === 'EDIT') {
    edited = framedEdit(ctx.draft_body, cmd.replacement);
    if (cmdUrls(edited) > CMD_MAX_URLS) {
      // Section 5 allows one plain URL. Refused rather than sent -- and the
      // code is NOT used, so the same code still works for a corrected EDIT.
      refused = 'that replacement carries ' + cmdUrls(edited) + ' links; Section 5 allows ' +
        CMD_MAX_URLS + '. The code is still good: send EDIT again with at most one link.';
      edited = null;
    }
  }

  // `accepted` here is a PROPOSAL. Apply Operator Command re-derives the
  // operator address, re-checks the code through reply_approval_usable() and
  // only then moves the draft; what actually happened is what that statement
  // returns, and Build Command Ack writes the acknowledgement from THAT, never
  // from this.
  const accepted = authenticated && refused === null;

  return {
    // operator_commands gets a row for anything command-shaped.
    record: isCommand,
    // Operator mail is never a prospect reply.
    handled: authenticated,
    accepted: accepted,
    command: verb || null,
    code: code,
    replacement: verb === 'EDIT' ? (cmd.replacement || null) : null,
    edited_body: edited,
    spf_pass: spf,
    dkim_pass: dkim,
    sender_ok: senderOk,
    refused_reason: refused,
    outcome: accepted ? (CMD_OUTCOME[verb] || null) : null,
    draft_id: ctx.draft_id === undefined ? null : ctx.draft_id,
    code_kind: ctx.code_kind === undefined ? null : ctx.code_kind,
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const ctx = $input.item.json;
const decision = commandDecision(ctx);
return { json: Object.assign({}, decision, { payload: ctx.payload, command_parse: ctx.command }) };

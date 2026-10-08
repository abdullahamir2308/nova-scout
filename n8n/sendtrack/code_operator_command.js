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

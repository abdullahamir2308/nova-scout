// Normalise Sent -- n8n Code node (Run Once for All Items).
//
// Turns a batch of Sent-folder messages from the IMAP trigger into rows for
// mailbox_sent. It is the IMAP pre-flight's reading, kept current: when each
// message went, and whether it counts toward Section 5's warm-up ceiling. A
// message counts ("external") when any recipient is someone other than:
//   - an address on the mailbox's own domain -- a note to yourself or to a
//     colleague warms nothing;
//   - the operator's own notification address -- Mailbox Watch's reply
//     notifications go there, and they are not mail to a cold prospect
//     (operator decision, 2026-09-15). It comes from the `settings` table,
//     via Load Settings, at runtime; nothing is baked in.
//
// The trigger delivers the Sent folder in batches of up to 20, and re-reads the
// whole folder on every activation (so manual sends made while n8n was down are
// picked up). Every row is keyed on Message-ID, so re-delivery is a no-op.

const OWN_DOMAIN = __OWN_DOMAIN__;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function domainOf(addr) {
  const s = str(addr).toLowerCase();
  const i = s.lastIndexOf('@');
  return i === -1 ? '' : s.slice(i + 1);
}

// Every address in a raw header value -- 'Name <first>, second' gives
// [first, second] -- lower-cased and de-duplicated.
function addresses(v) {
  const found = String(v === null || v === undefined ? '' : v).match(/[^\s<>,;:"'()\[\]]+@[^\s<>,;:"'()\[\]]+/g) || [];
  const out = [];
  for (let i = 0; i < found.length; i++) {
    const a = found[i].toLowerCase().replace(/\.+$/, '');
    if (out.indexOf(a) === -1) out.push(a);
  }
  return out;
}

// Message-IDs are stored with their angle brackets, exactly as nodemailer
// returns them and as In-Reply-To carries them, so every table compares like
// with like.
function messageId(v) {
  const m = str(v).match(/<[^<>\s]+>/);
  if (m) return m[0];
  const s = str(v).split(/\s+/)[0].replace(/[<>]/g, '');
  return s ? '<' + s + '>' : '';
}

// A message with no Message-ID still went out and still counts. Key it on what
// it is, so the next re-read maps it to the same row instead of adding one.
function fallbackId(j) {
  const s = [str(j.date), str(j.to), str(j.cc), str(j.subject)].join('|');
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return '<no-message-id.' + h.toString(16) + '@mirror.invalid>';
}

// The one rule for "does sending to this recipient warm the domain".
function countsTowardCeiling(addr, operator) {
  return domainOf(addr) !== OWN_DOMAIN && addr !== operator;
}

function sentRows(messages, operatorEmail) {
  const operator = str(operatorEmail).toLowerCase();
  const rows = [];
  let undated = 0;
  for (let i = 0; i < messages.length; i++) {
    const j = messages[i] || {};
    const meta = j.metadata || {};
    const t = new Date(str(j.date)).getTime();
    if (!str(j.date) || isNaN(t)) {
      // The pre-flight fell back to INTERNALDATE here; the trigger does not
      // expose it. Counted and reported rather than guessed.
      undated++;
      continue;
    }
    const recipients = addresses(j.to).concat(addresses(j.cc), addresses(meta.bcc)).filter(function (a, k, all) {
      return all.indexOf(a) === k;
    });
    rows.push({
      message_id: messageId(meta['message-id']) || fallbackId(j),
      sent_at: new Date(t).toISOString(),
      recipients: recipients,
      external: recipients.some(function (a) { return countsTowardCeiling(a, operator); }),
    });
  }
  return { rows: rows, seen: messages.length, undated: undated };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

// Load Settings sits between the trigger and this node: it runs once per batch
// and hands on its one row, so the messages themselves are read back from the
// trigger node by name.
const settings = $input.first().json;
const messages = $('Sent Folder').all().map(function (it) { return it.json; });
return [{ json: { payload: sentRows(messages, settings.operator_email) } }];

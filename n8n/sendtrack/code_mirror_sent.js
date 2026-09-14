// Normalise Sent -- n8n Code node (Run Once for All Items).
//
// Turns a batch of Sent-folder messages from the IMAP trigger into rows for
// mailbox_sent. It is the IMAP pre-flight's reading, kept current: when each
// message went, and whether any recipient was outside the mailbox's own domain.
// Only "external" messages count toward Section 5's warm-up ceiling -- a note
// to yourself or to a colleague on the same domain warms nothing.
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

// Every address in a raw header value: "A <a@x.com>, b@y.com" -> [a@x.com, b@y.com].
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

function sentRows(messages) {
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
      external: recipients.some(function (a) { return domainOf(a) !== OWN_DOMAIN; }),
    });
  }
  return { rows: rows, seen: messages.length, undated: undated };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const messages = $input.all().map(function (it) { return it.json; });
return [{ json: { payload: sentRows(messages) } }];

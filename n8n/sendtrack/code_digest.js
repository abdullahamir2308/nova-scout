// Build Digest -- n8n Code node (Run Once for All Items).
//
// The operator's daily digest (migration 014): what the workflows approved on
// their own, what went out, and what is waiting for a person and why. Since
// auto-approval, the review queue is the exceptions queue; this email is how
// the operator sees the rest without opening it.
//
// Runs every 30 minutes and sends at most once per operator day (Asia/Karachi),
// at the first tick at or after DIGEST_HOUR -- not on one exact tick, because
// the host is often asleep (Section 9, Workflow 6: a schedule that must catch
// one moment misses it). Each digest covers from where the last one stopped, so
// a day the host was off is folded into the next digest, not lost.
//
// It goes only to settings.operator_email, so like a reply notification it is
// not external and uses no warm-up slot. No operator address, no digest.

// docker-compose's GENERIC_TIMEZONE (no DST), baked at build time.
const SENDER_ZONE = __SENDER_ZONE__;
const SENDER_OFFSET_MIN = __SENDER_OFFSET__;

function dStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// '2026-10-05T03:00:00.000Z' -> '2026-10-05 08:00 PKT' on the sender's clock.
function local(iso, label) {
  const t = new Date(iso);
  if (isNaN(t.getTime())) return '?';
  const s = new Date(t.getTime() + SENDER_OFFSET_MIN * 60000).toISOString();
  return s.slice(0, 10) + ' ' + s.slice(11, 16) + ' ' + label;
}

function kindOf(variant) {
  const v = dStr(variant);
  const fu = /^follow-up-(\d+)/.exec(v);
  if (fu) return 'follow-up ' + fu[1];
  if (/^low-context/.test(v)) return 'low-context note';
  // Workflow 4's other no-model branch (2026-10-07): the lead's country has no
  // Send business-hours clock, so nothing could ever go out to it.
  if (/^no-send-clock/.test(v)) return 'no-send-clock note';
  return 'first touch';
}

function company(r) {
  return dStr(r.company_name) || dStr(r.domain) || ('lead ' + r.lead_id);
}

function zoneLabel() {
  return SENDER_ZONE === 'Asia/Karachi' ? 'PKT' : SENDER_ZONE;
}

// row: Load Digest's one row. cfg: { digest_hour }.
function buildDigest(row, cfg) {
  const z = zoneLabel();
  const autoApproved = Array.isArray(row.auto_approved) ? row.auto_approved : [];
  const sent = Array.isArray(row.sent) ? row.sent : [];
  const held = Array.isArray(row.held) ? row.held : [];
  const notifyTo = dStr(row.operator_email);
  const hour = Number(row.local_hour);
  const at = Number(cfg && cfg.digest_hour);

  let reason = null;
  if (!notifyTo) reason = 'no-operator-address';
  else if (row.already_sent === true) reason = 'already-sent-today';
  else if (!(hour >= at)) reason = 'before-digest-hour';

  const newHeld = held.filter(function (h) { return h.new === true; }).length;
  const subject = '[Nova Scout] Digest ' + dStr(row.digest_day) + ': ' + autoApproved.length + ' auto-approved, ' +
    sent.length + ' sent, ' + held.length + ' held';

  const lines = [
    'Nova Scout daily digest for ' + dStr(row.digest_day) + '.',
    'Covers ' + local(row.covers_from, z) + ' to ' + local(row.covers_to, z) + '.',
    'Auto-approval of email drafts: ' + (row.auto_approve_email === true ? 'ON' : 'OFF') +
      ' (settings.auto_approve_email).',
    '',
    'AUTO-APPROVED (' + autoApproved.length + ') -- no person read these. Each goes out in its recipient\'s',
    'business hours; reject it in the review queue to stop it.',
  ];
  if (!autoApproved.length) lines.push('  none');
  autoApproved.forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + ' (' + dStr(r.domain) + ', ' + dStr(r.country) + ')  ' +
      kindOf(r.variant) + '  approved ' + local(r.approved_at, z) + '  -- now ' + dStr(r.status));
  });
  lines.push('', 'SENT (' + sent.length + ')');
  if (!sent.length) lines.push('  none');
  sent.forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + ' -> ' + dStr(r.to_addr) + '  ' + kindOf(r.variant) + '  ' +
      local(r.sent_at, z) + '  approved by ' + (dStr(r.approved_by) || 'unknown'));
  });
  lines.push('', 'HELD FOR A PERSON (' + held.length + (newHeld ? ', ' + newHeld + ' new' : '') +
    ') -- the exceptions queue: every pending draft, and why.');
  if (!held.length) lines.push('  none');
  held.forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + '  ' + dStr(r.channel) + ' ' + kindOf(r.variant) +
      (r.new === true ? '  [new]' : '') + '  since ' + local(r.created_at, z));
    lines.push('        ' + (dStr(r.hold_reason) || 'no reason recorded (drafted before auto-approval existed)'));
  });
  lines.push('', '-- Nova Scout (Workflow 6, Daily Digest). Sent only to this address; it uses no warm-up slot.');

  return {
    send: reason === null,
    reason: reason || 'due',
    notify_to: notifyTo,
    subject: subject,
    text: lines.join('\n'),
    record: {
      digest_day: dStr(row.digest_day),
      covers_from: row.covers_from,
      covers_to: row.covers_to,
      summary: {
        auto_approved: autoApproved.map(function (r) { return r.draft_id; }),
        sent: sent.map(function (r) { return r.draft_id; }),
        held: held.length,
        held_new: newHeld,
        auto_approve_email: row.auto_approve_email === true,
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Node body (Run Once for All Items)
// ---------------------------------------------------------------------------

const row = $input.first().json;
const cfg = $('Config').first().json;
return [{ json: buildDigest(row, cfg) }];

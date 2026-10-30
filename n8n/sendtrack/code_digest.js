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

// The A/B arm a template draft carries in its variant (template/<A|B>/... or
// follow-up-N/template/<A|B>), or '-' for anything else.
function armOf(variant) {
  const m = /(?:^|\/)template\/([AB])(?:[/+]|$)/.exec(dStr(variant));
  return m ? m[1] : '-';
}

function nicheOf(r) {
  return dStr(r.niche) || 'none';
}

// Counts per niche and arm (migration 025): this window's auto-approved and
// sent emails, then every template email sent so far with its replies -- the
// numbers the A/B test is read off.
function nicheArmLines(autoApproved, sent, held, totals) {
  const keys = {};
  function bump(r, field) {
    const k = nicheOf(r) + '|' + armOf(r.variant);
    keys[k] = keys[k] || { auto: 0, sent: 0, held: 0 };
    keys[k][field]++;
  }
  autoApproved.forEach(function (r) { bump(r, 'auto'); });
  sent.forEach(function (r) { bump(r, 'sent'); });
  held.filter(function (r) { return dStr(r.channel) === 'email'; }).forEach(function (r) { bump(r, 'held'); });
  const lines = ['BY NICHE AND A/B ARM -- this digest\'s window'];
  const ks = Object.keys(keys).sort();
  if (!ks.length) lines.push('  none');
  ks.forEach(function (k) {
    const p = k.split('|');
    lines.push('  ' + p[0] + ' / arm ' + p[1] + ':  ' + keys[k].auto + ' auto-approved, ' + keys[k].sent + ' sent, ' +
      keys[k].held + ' email(s) held');
  });
  lines.push('', 'BY NICHE AND A/B ARM -- every template email sent so far');
  const t = Array.isArray(totals) ? totals : [];
  if (!t.length) lines.push('  none');
  t.forEach(function (r) {
    lines.push('  ' + nicheOf(r) + ' / arm ' + (dStr(r.arm) || '-') + ':  ' + Number(r.first_touches || 0) +
      ' first touches, ' + Number(r.follow_ups || 0) + ' follow-ups, ' + Number(r.replied_leads || 0) +
      ' lead(s) replied, ' + Number(r.bounced || 0) + ' bounced');
  });
  return lines;
}

// At most this many held emails listed one by one when Config gives no cap.
const DEFAULT_HELD_CAP = 15;
// Classes CONTACT fixes (migration 023), so their count line says how.
const ADDRESS_CLASSES = ['no-address', 'unverified-address'];

function pad(n) {
  const s = String(n);
  return s.length >= 4 ? s : '    '.slice(s.length) + s;
}

// One line per hold class (Load Digest's hold_reason_class), most first: the
// count, whether the class was ever seen before, and one sample reason.
function heldCounts(held) {
  const by = {};
  held.forEach(function (r) {
    const cls = dStr(r.hold_class) || 'none';
    by[cls] = by[cls] || { cls: cls, n: 0, known: r.class_known !== false, sample: dStr(r.hold_reason) };
    by[cls].n++;
  });
  return Object.keys(by).map(function (k) { return by[k]; }).sort(function (a, b) {
    return b.n - a.n || (a.cls < b.cls ? -1 : a.cls > b.cls ? 1 : 0);
  });
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
  const byNiche = nicheArmLines(autoApproved, sent, held, row.niche_totals);
  if (!autoApproved.length) lines.push('  none');
  autoApproved.forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + ' (' + dStr(r.domain) + ', ' + dStr(r.country) + ')  ' +
      kindOf(r.variant) + '  [' + nicheOf(r) + ' ' + armOf(r.variant) + ']  approved ' + local(r.approved_at, z) +
      '  -- now ' + dStr(r.status));
  });
  lines.push('', 'SENT (' + sent.length + ')');
  if (!sent.length) lines.push('  none');
  sent.forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + ' -> ' + dStr(r.to_addr) + '  ' + kindOf(r.variant) +
      '  [' + nicheOf(r) + ' ' + armOf(r.variant) + ']  ' + local(r.sent_at, z) + '  approved by ' + (dStr(r.approved_by) || 'unknown'));
  });
  // HELD, since 2026-10-30 (migration 028): counted by the class of its reason,
  // and listed one by one only when a person can decide it. Until then every
  // pending draft was listed with a code and re-sent every 4 hours -- 166 codes,
  // not one of them a question about the words.
  const counts = heldCounts(held);
  const fresh = counts.filter(function (c) { return !c.known; });
  const people = held.filter(function (r) { return r.needs_person === true; });
  const cap = Number(row.held_cap) > 0 ? Number(row.held_cap) : DEFAULT_HELD_CAP;
  lines.push('', 'HELD (' + held.length + (newHeld ? ', ' + newHeld + ' new' : '') +
    ') -- the exceptions queue, by reason. Only a hold a person can decide is listed one by one.');
  if (!held.length) lines.push('  none');
  counts.forEach(function (c) {
    lines.push('  ' + pad(c.n) + '  ' + c.cls + (c.known ? '' : '  [NEW]') + (ADDRESS_CLASSES.indexOf(c.cls) !== -1
      ? '   (CONTACT <lead domain> <email address> gives one an address)' : ''));
  });
  if (fresh.length) {
    lines.push('', 'NEW HOLD REASON -- never seen before. Silent (no codes) until allowlisted in hold_reason_classes:');
    fresh.forEach(function (c) {
      lines.push('  ' + c.cls + '  (' + c.n + ')  e.g. ' + JSON.stringify(c.sample));
    });
  }
  if (held.length) {
    lines.push('', 'NEEDS YOU (' + people.length + ') -- held for a decision about the email itself.');
    if (!people.length) lines.push('  none');
  }
  people.slice(0, cap).forEach(function (r) {
    lines.push('  #' + r.draft_id + '  ' + company(r) + '  ' + dStr(r.channel) + ' ' + kindOf(r.variant) +
      (r.new === true ? '  [new]' : '') + '  since ' + local(r.created_at, z));
    lines.push('        ' + (dStr(r.hold_reason) || 'no reason recorded (drafted before auto-approval existed)'));
    // One-time codes on held EMAIL drafts, 2026-10-09 (Section 9, Workflow 7):
    // a held email a person can decide can be approved, rejected or edited by
    // answering this digest, exactly as a drafted reply can. Load Digest issues
    // the code only on the tick that sends, and reuses an open one, so the code
    // here is the one the database holds.
    if (dStr(r.code)) {
      lines.push('        reply with:  APPROVE ' + dStr(r.code) + '   |   REJECT ' + dStr(r.code) +
        '   |   EDIT ' + dStr(r.code) + ' <your text>');
    }
  });
  if (people.length > cap) {
    lines.push('  +' + (people.length - cap) + ' more (in the review queue; the next digest lists them)');
  }
  const codes = held.filter(function (r) { return dStr(r.code); }).length;
  if (codes) {
    lines.push('');
    lines.push('Those ' + codes + ' code(s) work once each and expire 48 hours from now. Only this address');
    lines.push('can use them, and only with SPF and DKIM passing; anything else is logged and');
    lines.push('ignored. No reminder is sent: a code still open tomorrow is in tomorrow\'s digest.');
  }
  lines.push('');
  byNiche.forEach(function (l) { lines.push(l); });
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
      // The one-time codes this digest carried. Record Digest stamps
      // reply_approvals.reminded_at for each one, because the digest IS how the
      // operator was told about them -- otherwise the reply assistant's
      // reminder lane would read them as never mentioned and nudge about every
      // held draft half an hour later.
      coded: held.filter(function (r) { return dStr(r.code); }).map(function (r) { return dStr(r.code); }),
      // Hold classes flagged as never seen before: Record Digest marks them seen
      // (never allowlisted) once SMTP accepted this digest (migration 028).
      new_classes: fresh.map(function (c) { return c.cls; }),
      summary: {
        auto_approved: autoApproved.map(function (r) { return r.draft_id; }),
        sent: sent.map(function (r) { return r.draft_id; }),
        held: held.length,
        held_new: newHeld,
        held_coded: codes,
        held_needs_person: people.length,
        held_by_class: counts.reduce(function (o, c) { o[c.cls] = c.n; return o; }, {}),
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

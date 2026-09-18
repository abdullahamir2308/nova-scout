// Assess Health -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 6: Mailbox Watch is proven live only when it fires, and
// nothing proves its IMAP connection is up in between. Every tick of the IMAP
// Health workflow asks two things and this node judges the answers:
//
//   1. Does a fresh read-only IMAP session to the mailbox work?  (Check IMAP --
//      imap_preflight.py, run by the imap-health service)
//   2. Has Mailbox Watch recorded every INBOX and Sent message that landed more
//      than grace_min ago?  (Load Health State -- the pre-flight's Message-IDs
//      against inbound_messages and mailbox_sent)
//
// The second is the one that proves the triggers are listening: a login that
// works says the mailbox is reachable, not that n8n is still connected to it.
//
// Nothing alerts on one bad check. Both outages on 2026-09-16 were a host
// waking or starting, and on a wake this workflow can tick before Mailbox Watch
// has reconnected -- mail that arrived during sleep looks "missed" for a few
// seconds. confirm_after consecutive failing checks are needed first. Then one
// email, a reminder every remind_hours while the same problem lasts, a new
// email if the problem changes, and one when it clears.
//
// The operator's address comes from the settings table at runtime (migration
// 008) -- nothing is baked into this node.

const ADDRESS = /^[^\s@<>(),;:"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

// The problem codes, in the order an email lists them. Section 8 lists the same
// set for mailbox_health.problems; the build refuses if they drift.
const PROBLEMS = {
  'checker-unreachable': 'the health checker is not answering',
  'imap-failed': 'the mailbox is not reachable over IMAP',
  'inbox-missed': 'Mailbox Watch has missed INBOX mail',
  'sent-missed': 'Mailbox Watch has missed Sent mail',
};

const LIST_MAX = 10;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function arr(v) {
  return Array.isArray(v) ? v : [];
}

function ms(iso) {
  const t = new Date(str(iso)).getTime();
  return isNaN(t) ? null : t;
}

function operatorAddress(db) {
  const a = str(db.operator_email).toLowerCase();
  return ADDRESS.test(a) ? a : '';
}

// What the n8n HTTP Request node hands on: the checker's JSON body, or -- with
// continue-on-error -- { error: ... } when nothing answered.
function checkerProblem(check) {
  if (!check || check.error !== undefined || typeof check.ok !== 'boolean') {
    const e = check ? check.error : undefined;
    const why = typeof e === 'string' ? e : e && (e.message || e.code) ? str(e.message || e.code) : 'no usable answer';
    return { code: 'checker-unreachable', why: why.slice(0, 300) };
  }
  if (!check.ok) return { code: 'imap-failed' };
  return null;
}

function listMessages(rows, line) {
  const out = rows.slice(0, LIST_MAX).map(line);
  if (rows.length > LIST_MAX) out.push('  ... and ' + (rows.length - LIST_MAX) + ' more');
  return out;
}

function problemLines(code, ctx) {
  const { check, db, cfg, why } = ctx;
  if (code === 'checker-unreachable') {
    return [
      'The health checker did not answer: ' + why,
      'Nothing was checked, so this says nothing about the mailbox itself -- but until it answers, a dead ' +
        'Mailbox Watch goes unnoticed. The checker is the imap-health service in docker-compose.yml ' +
        '(docker compose ps imap-health; docker compose logs imap-health).',
    ];
  }
  if (code === 'imap-failed') {
    const lines = ['A fresh read-only IMAP session failed at ' + (str(check.stage) || '?') + ': ' + (str(check.detail) || '?')];
    if (str(check.hint)) lines.push('(hint: ' + str(check.hint) + ')');
    lines.push(
      'Mailbox Watch uses the same server and account, so it is almost certainly not receiving mail either -- ' +
        'replies and opt-outs are not being recorded. n8n retries it by itself, but after a long outage its next ' +
        'attempt can be hours away (Master Ref Section 3). Once the mailbox answers again, re-publish Mailbox Watch ' +
        'in the n8n UI, or restart n8n, to reconnect it at once.');
    return lines;
  }
  if (code === 'inbox-missed') {
    const rows = arr(db.missing_inbox);
    return [
      rows.length + ' INBOX message(s) landed more than ' + cfg.grace_min + ' minutes ago and Mailbox Watch has ' +
        'not recorded them. Its Inbox trigger is not running: replies and opt-outs are not being recorded, ' +
        'blocklisted or notified.',
      'Re-publish Mailbox Watch in the n8n UI, or restart n8n -- on activation it catches up on what it missed. ' +
        'Until then, read these by hand and blocklist any opt-out:',
    ].concat(listMessages(rows, (r) => '  ' + str(r.at) + '  ' + str(r.from) + '  "' + str(r.subject) + '"'));
  }
  const rows = arr(db.missing_sent);
  return [
    rows.length + ' Sent message(s) landed more than ' + cfg.grace_min + ' minutes ago and are not in the Sent ' +
      'mirror (mailbox_sent), so the warm-up ceiling does not count them. Re-publishing Mailbox Watch re-reads ' +
      'the whole folder.',
  ].concat(listMessages(rows, (r) => '  ' + str(r.at) + '  to ' + (str(r.to) || '?') + '  ' + str(r.message_id)));
}

function assess(check, db, cfg) {
  const now = ms(db.now);
  const found = checkerProblem(check);
  const problems = [];
  if (found) problems.push(found.code);
  if (arr(db.missing_inbox).length) problems.push('inbox-missed');
  if (arr(db.missing_sent).length) problems.push('sent-missed');
  const healthy = problems.length === 0;

  const prevAlertedAt = str(db.prev_last_alerted_at) || null;
  const prevAlerted = arr(db.prev_alerted_problems);
  const consecutive = healthy ? 0 : (Number(db.prev_consecutive_failures) || 0) + 1;
  const failingSince = healthy ? null : str(db.prev_failing_since) || str(db.now);
  const confirmed = !healthy && consecutive >= cfg.confirm_after;
  const sameProblems = problems.slice().sort().join(',') === prevAlerted.slice().sort().join(',');
  const reminderDue = prevAlertedAt !== null && now - ms(prevAlertedAt) >= cfg.remind_hours * 3600 * 1000;

  let alertKind = null;
  if (confirmed && (prevAlertedAt === null || !sameProblems || reminderDue)) alertKind = 'problem';
  else if (healthy && prevAlertedAt !== null) alertKind = 'recovered';

  const ctx = { check: check || {}, db, cfg, why: found && found.why };
  let subject = '';
  const lines = [];
  if (alertKind === 'problem') {
    subject = '[Nova Scout] IMAP health: ' + problems.map((p) => PROBLEMS[p]).join('; ');
    problems.forEach((p, i) => {
      if (i) lines.push('');
      problemLines(p, ctx).forEach((l) => lines.push(l));
    });
    lines.push('');
    lines.push('Failing since ' + failingSince + ' (' + consecutive + ' consecutive checks); checked ' + str(db.now) + '.');
    lines.push('A reminder follows every ' + cfg.remind_hours + ' hours while this lasts, and one email when it clears.');
  } else if (alertKind === 'recovered') {
    subject = '[Nova Scout] IMAP health: OK again';
    const was = arr(db.prev_problems);
    lines.push('IMAP health is OK again: the mailbox answers, and every INBOX and Sent message from the last ' +
      (Number(ctx.check.hours) || '?') + ' hours is in Mailbox Watch\'s records.');
    lines.push('It was: ' + (was.length ? was.map((p) => PROBLEMS[p] || p).join('; ') : '?') + ' -- from ' +
      (str(db.prev_failing_since) || '?') + ' until ' + str(db.now) + '.');
    if (was.some((p) => p !== 'checker-unreachable')) {
      const synced = ms(db.sent_synced_at);
      const since = ms(db.prev_failing_since);
      lines.push('');
      if (synced !== null && since !== null && synced > since) {
        lines.push('Mailbox Watch read the Sent folder at ' + str(db.sent_synced_at) + ', after the problem began, ' +
          'so it was connected again at that point.');
      } else {
        lines.push('Mailbox Watch has not read the Sent folder since the problem began. Either its connection held ' +
          'throughout, or n8n is still waiting to retry it (Master Ref Section 3) -- if so, the next message it ' +
          'misses is reported here.');
      }
    }
  }

  const detail = healthy ? null : problems.map((p) => {
    if (p === 'checker-unreachable') return p + ': ' + found.why;
    if (p === 'imap-failed') return p + ' at ' + (str(ctx.check.stage) || '?') + ': ' + (str(ctx.check.detail) || '?');
    if (p === 'inbox-missed') return p + ': ' + arr(db.missing_inbox).length + ' message(s)';
    return p + ': ' + arr(db.missing_sent).length + ' message(s)';
  }).join('; ').slice(0, 2000);

  const notifyTo = operatorAddress(db);
  return {
    healthy: healthy,
    problems: problems,
    confirmed: confirmed,
    alert_kind: alertKind,
    notify: alertKind !== null && notifyTo !== '',
    notify_to: notifyTo,
    subject: subject.slice(0, 200),
    text: lines.join('\n'),
    state: {
      healthy: healthy,
      problems: problems,
      detail: detail,
      consecutive_failures: consecutive,
      failing_since: failingSince,
      alert_kind: alertKind,
      prev_last_alerted_at: prevAlertedAt,
      prev_alerted_problems: prevAlerted,
    },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

// Load Health State (a Postgres node) replaced the items, so the checker's
// answer and the Config values are read back from their nodes by name.
const check = $('Check IMAP').first().json;
const cfg = $('Config').first().json;
return [{ json: assess(check, $input.first().json, cfg) }];

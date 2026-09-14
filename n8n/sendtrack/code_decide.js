// Decide Send -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 6: "Send trigger: Cron, business hours only, randomised
// intervals. Guard: query today's send count; abort if at the warm-up ceiling.
// Ceiling is enforced in the workflow, not by discipline."
//
// Every decision the send path takes is made here, from the one row "Load Send
// State" returns and ONE clock value -- so a tick cannot straddle midnight and
// count "today" against two different days. At most one message per tick:
// Section 5 wants "irregular send intervals ... never a synchronised burst".
//
// This node is not the last line of defence. "Claim Send" re-checks the
// channel, the approval, the blocklist, the unchanged body and the ceiling in
// one SQL statement under an advisory lock. A bug here can make the workflow
// send LESS, never more.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses each out of NovaScout_MasterRef.md
// and refuses to build on drift.
// ---------------------------------------------------------------------------

// Section 5, warm-up schedule: [week, max sends/day]. The last row is
// open-ended -- "Week 4+ | 20 (hard ceiling)".
const WARMUP = [
  [1, 5],
  [2, 10],
  [3, 15],
  [4, 20],
];

// Section 5, message rules. Verbatim -- this sentence is the opt-out mechanism.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";
const MAX_URLS = 1;

// Section 9: "Maximum two follow-ups, then mark lost."
const MAX_FOLLOW_UPS = 2;

// The day the ceiling counts is the SENDER's day. The ceiling is a property of
// one mailbox, so it needs one clock: Asia/Karachi, docker-compose's
// GENERIC_TIMEZONE and the IMAP pre-flight's clock. No DST there, so +05:00 is
// exact.
const SENDER_UTC_OFFSET_MIN = 300;

// Section 5: "within the recipient's business hours". The recipient's, not the
// sender's -- leads span every Section 12 geography, UTC-6 to UTC+5:30.
//
// Approximate on purpose: fixed standard-time offsets, no DST (the operator's
// call, 2026-09-14 -- at 5-20 sends/day an hour of drift is not worth a tz
// database). The countries that do shift (Poland, Czech Republic, Hungary,
// Romania, Egypt) run one hour LATER than this table in summer, so a send the
// table places at 16:30 lands at 17:30 local. Egypt's working week is
// Sunday-Thursday; everyone else here works Monday-Friday.
//
// Keys must be exactly Section 12's geography list -- the build refuses
// otherwise, so a new target country cannot ship without a clock.
const COUNTRY_CLOCKS = {
  'Turkey':         { utc_offset_min: 180,  weekend: [6, 0] },
  'Mexico':         { utc_offset_min: -360, weekend: [6, 0] },
  'India':          { utc_offset_min: 330,  weekend: [6, 0] },
  'Pakistan':       { utc_offset_min: 300,  weekend: [6, 0] },
  'Egypt':          { utc_offset_min: 120,  weekend: [5, 6] },
  'Poland':         { utc_offset_min: 60,   weekend: [6, 0] },
  'Romania':        { utc_offset_min: 120,  weekend: [6, 0] },
  'Hungary':        { utc_offset_min: 60,   weekend: [6, 0] },
  'Czech Republic': { utc_offset_min: 60,   weekend: [6, 0] },
  'UAE':            { utc_offset_min: 240,  weekend: [6, 0] },
  'South Africa':   { utc_offset_min: 120,  weekend: [6, 0] },
  'Brazil':         { utc_offset_min: -180, weekend: [6, 0] },
  'Argentina':      { utc_offset_min: -180, weekend: [6, 0] },
};

// Local business hours, minutes after local midnight, half-open [start, end).
const BUSINESS_START_MIN = 9 * 60;
const BUSINESS_END_MIN = 17 * 60;

// A workflow send the Sent-folder mirror has not seen after this long means the
// mirror is not reading the folder. Zoho files a submitted message into Sent
// within seconds; fifteen minutes is generous.
const MIRROR_GRACE_MIN = 15;

// The signature this build signs with (Section 5: name / title / phone). A body
// that does not carry it -- opt-out line, blank line, signature -- was drafted
// under a different identity or had its compliance footer edited. Substituted
// at build time.
const SIGNATURE = __SIGNATURE__;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MS_MIN = 60 * 1000;
const MS_DAY = 24 * 60 * MS_MIN;

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;
const EMAIL_RE = /^[^\s@<>(),;:"']+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function ms(v) {
  if (v === null || v === undefined || v === '') return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : t;
}

function iso(t) {
  return new Date(t).toISOString();
}

// What gets checked is what gets sent: line endings normalised, trailing
// whitespace off each line (a grid editor adds both), nothing else touched.
function normaliseBody(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}

function countUrls(v) {
  const m = String(v).match(URL_RE);
  return m ? m.length : 0;
}

function ceilingForWeek(week) {
  let cap = null;
  for (let i = 0; i < WARMUP.length; i++) {
    if (week >= WARMUP[i][0]) cap = WARMUP[i][1];
  }
  if (cap === null) throw new Error('no warm-up row covers week ' + week);
  return cap;
}

// 'YYYY-MM-DD' of the sender's calendar day containing instant t.
function senderDay(t) {
  return new Date(t + SENDER_UTC_OFFSET_MIN * MS_MIN).toISOString().slice(0, 10);
}

// The UTC instant at which sender-day `day` begins.
function senderDayStart(day) {
  return Date.parse(day + 'T00:00:00Z') - SENDER_UTC_OFFSET_MIN * MS_MIN;
}

// The same derivation the IMAP pre-flight printed: warm-up starts on the day
// of the first external send; week = floor(days since then / 7) + 1. An empty
// history is "not started" and the first send is day 1 of week 1 -- derived
// from the mailbox, not assumed.
function warmupState(state, clock) {
  const today = senderDay(clock);
  const dayStart = senderDayStart(today);
  const dayEnd = dayStart + MS_DAY;
  const first = ms(state.first_external_send_at);
  let dayIndex = 0;
  let firstDay = null;
  if (first !== null) {
    firstDay = senderDay(first);
    dayIndex = Math.max(0, Math.round((senderDayStart(today) - senderDayStart(firstDay)) / MS_DAY));
  }
  const week = Math.floor(dayIndex / 7) + 1;
  const ceiling = ceilingForWeek(week);
  const sends = (state.recent_sends || []).map(function (s) { return ms(s.at); }).filter(function (t) {
    return t !== null;
  });
  const sentToday = sends.filter(function (t) { return t >= dayStart && t < dayEnd; }).length;
  return {
    history: first === null ? 'empty' : 'mirrored',
    first_send_day: firstDay,
    sender_day: today,
    day_index: dayIndex,
    week: week,
    ceiling: ceiling,
    sent_today: sentToday,
    remaining: Math.max(0, ceiling - sentToday),
    day_start: iso(dayStart),
    day_end: iso(dayEnd),
  };
}

// Local wall-clock for a country at instant t, and whether it is business time.
function localClock(country, t) {
  const zone = COUNTRY_CLOCKS[str(country)];
  if (!zone) return null;
  const local = new Date(t + zone.utc_offset_min * MS_MIN);
  const minutes = local.getUTCHours() * 60 + local.getUTCMinutes();
  const dow = local.getUTCDay();
  const weekend = zone.weekend.indexOf(dow) !== -1;
  const hh = String(local.getUTCHours()).padStart(2, '0');
  const mm = String(local.getUTCMinutes()).padStart(2, '0');
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return {
    local: days[dow] + ' ' + hh + ':' + mm,
    in_window: !weekend && minutes >= BUSINESS_START_MIN && minutes < BUSINESS_END_MIN,
  };
}

// Why this approved email draft may not be sent at all, or null if it may.
// Business hours are NOT checked here -- an out-of-hours draft is eligible,
// just not yet.
function ineligibility(c) {
  if (str(c.channel) !== 'email') {
    // Section 6: "LinkedIn sending is manual. Always. No exceptions." The query
    // already filters on channel, so reaching this line means the SQL changed.
    // Throwing stops the whole tick rather than quietly skipping.
    throw new Error('Section 6 violation: a ' + JSON.stringify(c.channel) +
      ' draft reached the send path (draft ' + c.draft_id + ')');
  }
  if (str(c.status) !== 'approved') return 'not-approved';
  const followUp = /^follow-up-/.test(str(c.variant));
  const body = normaliseBody(c.body);

  if (followUp) {
    if (str(c.lead_status) !== 'sent') return 'lead-status:' + str(c.lead_status);
    if (!(Number(c.prior_sends) > 0)) return 'follow-up-without-first-touch';
    if (Number(c.follow_ups_sent) >= MAX_FOLLOW_UPS) return 'follow-ups-exhausted';
  } else {
    if (['drafted', 'approved'].indexOf(str(c.lead_status)) === -1) return 'lead-status:' + str(c.lead_status);
    if (Number(c.prior_sends) > 0 || Number(c.prior_manual_sends) > 0) return 'already-contacted';
  }
  if (c.replied) return 'lead-replied';
  if (c.bounced) return 'lead-bounced';
  if (c.lead_domain_blocked) return 'blocklisted-domain';
  if (c.recipient_domain_blocked) return 'blocklisted-recipient-domain';

  // Section 9 Workflow 3b: "contacts.verified ... Workflow 6 is the consumer;
  // this is the flag a send gates on."
  if (!c.verified) return 'contact-unverified';
  if (!EMAIL_RE.test(str(c.to_addr))) return 'no-valid-address';

  if (/^low-context/.test(str(c.variant)) || /^NOT DRAFTED/.test(body)) return 'low-context';
  if (!str(c.subject)) return 'no-subject';
  if (body.indexOf(OPT_OUT) === -1) return 'no-opt-out';
  if (body.indexOf(OPT_OUT + '\n\n' + SIGNATURE) === -1) return 'stale-signature';
  if (countUrls(body) > MAX_URLS) return 'too-many-urls';
  if (!COUNTRY_CLOCKS[str(c.country)]) return 'unknown-country:' + str(c.country);
  return null;
}

function decide(state, rng) {
  const clock = ms(state.clock);
  if (clock === null) throw new Error('Load Send State returned no clock');
  const minGap = Number(state.min_gap_min);
  const probability = Number(state.send_probability);

  const w = warmupState(state, clock);
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
    const lc = localClock(c.country, clock);
    eligible.push({
      draft_id: c.draft_id,
      lead_id: c.lead_id,
      domain: c.domain,
      country: c.country,
      local_time: lc.local,
      in_window: lc.in_window,
    });
  }

  const out = {
    send: false,
    reason: null,
    detail: null,
    clock: iso(clock),
    warmup: w,
    eligible: eligible,
    excluded: excluded,
    payload: null,
  };
  function stop(reason, detail) {
    out.reason = reason;
    out.detail = detail;
    return out;
  }

  // 1. Can the count be trusted at all? The ceiling is only as good as the Sent
  //    history behind it, and a mirror that never ran or has stopped reading
  //    the folder undercounts the operator's manual sends without any error.
  if (!state.sent_mirror_synced_at) {
    return stop('sent-mirror-never-ran',
      'The Sent-folder mirror has never read the mailbox, so the manual warm-up sends are ' +
      'invisible to the ceiling. Publish the Mailbox Watch workflow; it syncs on activation ' +
      'once the Sent folder holds a message.');
  }
  const behind = (state.unconfirmed_workflow_sends || []).filter(function (s) {
    const t = ms(s.sent_at);
    return t !== null && clock - t > MIRROR_GRACE_MIN * MS_MIN;
  });
  if (behind.length) {
    return stop('sent-mirror-behind',
      behind.length + ' workflow send(s) older than ' + MIRROR_GRACE_MIN + ' min have not appeared ' +
      'in the Sent folder mirror (first: ' + behind[0].message_id + '). The mirror is not reading ' +
      'the folder, so today\'s count cannot be trusted.');
  }

  // 2. The warm-up ceiling (Section 5). A mailbox-level abort: nothing below
  //    this line can override it.
  if (w.sent_today >= w.ceiling) {
    return stop('ceiling-reached',
      'Week ' + w.week + ' ceiling is ' + w.ceiling + '/day; ' + w.sent_today +
      ' external send(s) already on sender-day ' + w.sender_day + '.');
  }

  // 3. Is there anything to send, and is its recipient at work?
  if (!eligible.length) {
    return stop('no-eligible-draft', 'No approved email draft passes the send checks.');
  }
  const now = eligible.filter(function (e) { return e.in_window; });
  if (!now.length) {
    return stop('no-recipient-in-business-hours',
      eligible.length + ' eligible draft(s), none inside the recipient\'s business hours right now.');
  }

  // 4. Pacing. Irregular intervals, never a burst: a floor on the gap since the
  //    mailbox's last external send (manual or not), then a coin flip per tick.
  const last = ms(state.last_external_send_at);
  if (last !== null && minGap > 0 && clock - last < minGap * MS_MIN) {
    return stop('min-gap',
      'Last external send was ' + Math.round((clock - last) / MS_MIN) + ' min ago; minimum gap is ' +
      minGap + ' min.');
  }
  if (!(rng() < probability)) {
    return stop('random-gate', 'Skipped this tick at random (p=' + probability + ') to keep intervals irregular.');
  }

  // 5. Oldest approved draft first -- Section 7's queue rule.
  const pick = now[0];
  const c = candidates.filter(function (x) { return x.draft_id === pick.draft_id; })[0];
  out.send = true;
  out.reason = 'send';
  out.detail = 'Sending draft ' + c.draft_id + ' to ' + c.domain + ' (' + c.country + ', ' +
    pick.local_time + ' local). Send ' + (w.sent_today + 1) + ' of ' + w.ceiling + ' today.';
  out.payload = {
    draft_id: c.draft_id,
    lead_id: c.lead_id,
    to_addr: str(c.to_addr),
    subject: str(c.subject),
    body: normaliseBody(c.body),
    raw_body: c.body,
    clock: iso(clock),
    day_start: w.day_start,
    day_end: w.day_end,
    ceiling: w.ceiling,
  };
  return out;
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const state = $input.first().json;
return [{ json: decide(state, Math.random) }];

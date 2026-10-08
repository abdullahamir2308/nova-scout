// Build Reply -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 7. A prospect has answered a cold email; this builds the
// request that drafts our answer. Claude Reply writes it, Assemble Reply checks
// it against the first touch's own claim rules and frames it, and the operator
// approves it by email with a one-time code. NOTHING a model writes here
// reaches a prospect without that approval.
//
// FOUR SOURCES, AND NOTHING ELSE (the operator's rule, 2026-10-09):
//   1. what they wrote -- the text above the quoted thread, as Classify Inbound
//      cut it;
//   2. the thread -- our first touch exactly as it was sent, and any follow-up
//      that went out;
//   3. the lead's enrichment record, worded with the same boundary sentences
//      Workflow 4's fact sheet uses, so a fact cannot be joined to another one;
//   4. the confirmed claims_library lines (migration 013 narrowed each to what
//      the Nova Agent Kit code does), plus how we found them -- leads.source.
//
// A question none of those four can answer is not answered. The five topics
// that come back in practice -- pricing, integrations, languages, security,
// timelines -- are detected in THEIR text before the call (code_reply_topics.js,
// prepended to this node), the model is told to say we will confirm them, and
// the draft carries the flag so the review email can put the question in front
// of the operator, who knows the answer.
//
// A reply is never auto-approved and never claim-checked: the operator reads
// every one. That is why there is no Approval Gate in this workflow and why
// migration 018 refuses an `approved_by = 'auto'` reply on the table as well.

// Section 5 / Section 9, substituted at build time.
const REPLY_SYSTEM_PROMPT = __REPLY_SYSTEM_PROMPT__;
const CLAUDE_REQUEST = __CLAUDE_REQUEST__;
const CACHE_CONTROL = { type: 'ephemeral' };
const SENDER_NAME = __SENDER_NAME__;
const THERAPEUTIC_AREAS = __THERAPEUTIC_AREAS__;

// A reply is an answer, not a pitch: long enough to answer what they asked and
// name one next step, short enough to read on a phone. Assemble Reply tags
// anything outside this.
const REPLY_MIN_WORDS = 40;
const REPLY_MAX_WORDS = 120;

// Operator instruction, 2026-10-09: a reply never reuses the "two CROs" proof
// line, and never calls Vertex Clinical Research a CRO -- it is a clinical
// research centre. The rule is written against the TEXT of a claims_library
// line rather than against a code, because the table is the library and a
// reworded row must not escape it. A lead whose country the library can only
// serve with that line is simply offered no proof, and the reply makes no proof
// claim: a missing claim is not a defect in an answer to a question.
const REPLY_PROOF_REFUSED = [/\btwo CROs?\b/i, /\bVertex\b[^.]{0,40}\bCRO\b/i];

function rStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function rFold(v) {
  return rStr(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function rWords(v) {
  const t = rStr(v);
  return t ? t.split(/\s+/).length : 0;
}

// --- the claims this reply may draw on ---------------------------------------

function replyLibrary(raw) {
  const out = [];
  for (const row of Array.isArray(raw) ? raw : []) {
    if (!row || typeof row !== 'object') continue;
    out.push({
      code: rStr(row.code),
      slot: rStr(row.slot),
      body: rStr(row.body),
      countries: Array.isArray(row.countries) && row.countries.length ? row.countries.map(rFold) : null,
      measured: row.measured === true,
      confirmed: row.confirmed === true,
      active: row.active === true,
      capabilities: Array.isArray(row.capabilities) ? row.capabilities.map(rStr).filter(Boolean) : [],
    });
  }
  return out;
}

// The proof for this lead's country, resolved exactly as Assess Grounding and
// Build Follow-Up resolve it (a measured line wins; a row with no countries
// serves everyone else) -- and then the refused wordings are dropped.
function replyProof(rows, country) {
  const key = rFold(country);
  const serving = rows.filter(function (x) { return x.countries && x.countries.indexOf(key) !== -1; });
  const pick = serving.length ? serving : rows.filter(function (x) { return !x.countries; });
  const measured = pick.filter(function (x) { return x.measured; });
  const chosen = measured.length ? measured : pick;
  return chosen.filter(function (x) {
    return !REPLY_PROOF_REFUSED.some(function (re) { return re.test(x.body); });
  });
}

function replyLines(rows) {
  return rows.map(function (x) {
    return { code: x.code, body: x.body, confirmed: x.confirmed, capabilities: x.capabilities };
  });
}

// Everything a reply may say about the product, and the one link it may carry.
// Only active lines with text: an empty or retired row offers nothing.
function replyPools(r) {
  const lib = replyLibrary(r.library).filter(function (l) { return l.active && l.body; });
  const bySlot = function (slot) { return lib.filter(function (l) { return l.slot === slot; }); };
  return {
    description: replyLines(bySlot('description')),
    angle: replyLines(bySlot('angle')),
    benefit: replyLines(bySlot('benefit')),
    proof: replyLines(replyProof(bySlot('proof'), r.country)),
    ask: replyLines(bySlot('ask')),
    // Section 9, Workflow 4's link policy: the only URL allowed anywhere is the
    // library's own `link` line, and it is offered only while it is active AND
    // confirmed and actually holds a URL. L-NP is empty and inactive today, so
    // a reply carries no link at all and Assemble Reply tags any URL in one.
    link: replyLines(bySlot('link').filter(function (l) {
      return l.confirmed && /\bhttps?:\/\/\S+/i.test(l.body);
    })),
  };
}

// --- the prospect, as a fact sheet with boundaries ---------------------------
//
// Workflow 4's wording, for Workflow 4's reason: a sentence can use no invented
// word and still assert an untrue RELATIONSHIP between two true facts, so each
// fact carries the sentence that says what it does not mean.
function replyRecord(r) {
  const facts = [];
  const areas = Array.isArray(r.therapeutic_areas) ? r.therapeutic_areas.map(rStr).filter(Boolean) : [];
  if (areas.length) {
    facts.push('Therapeutic areas listed on their own website: ' + areas.join(', ') +
      '. This says nothing about which area any particular trial belongs to.');
  }
  const phases = Array.isArray(r.phases) ? r.phases.map(rStr).filter(Boolean) : [];
  if (phases.length) facts.push('Trial phases listed on their own website: ' + phases.join(', ') + '.');
  if (rStr(r.city)) {
    facts.push('The company is based in ' + rStr(r.city) +
      '. This says nothing about where any trial runs or where any staff sit.');
  }
  if (rStr(r.founder_name)) {
    facts.push('Named on their own site as founder or MD: ' + rStr(r.founder_name) +
      '. This says nothing about which trials or clients this person personally handles.');
  }
  const n = Number(r.employee_estimate);
  if (r.employee_estimate !== null && r.employee_estimate !== undefined && Number.isInteger(n) && n > 0) {
    facts.push('Their own site states a team of ' + n + ' people. This says nothing about what they do.');
  }
  if (rStr(r.source)) {
    facts.push('How we found them, and the only answer to "where did you hear about us": ' + rStr(r.source) +
      '. Nothing else about how they came to our attention is on record.');
  }
  facts.push('No trial is part of this record. The first email may name one; nothing here confirms it.');
  return ['Company: ' + rStr(r.company_name), 'Country: ' + rStr(r.country)]
    .concat(facts.map(function (f, i) { return String(i + 1) + '. ' + f; })).join('\n');
}

// Our side of the thread, as they read it: the first touch and any follow-up,
// oldest first, without their signatures and quoted tails.
function threadText(r) {
  const out = [];
  for (const m of Array.isArray(r.thread) ? r.thread : []) {
    if (!m || !rStr(m.body)) continue;
    const when = new Date(m.sent_at);
    out.push('--- ' + (isNaN(when.getTime()) ? 'earlier' : when.toUTCString().slice(0, 16)) +
      ', we wrote (subject "' + rStr(m.subject) + '"):');
    out.push(rStr(m.body));
  }
  return out.join('\n');
}

function sheet(heading, list, withCaps) {
  if (!list.length) return heading + '\n  (none)';
  return [heading].concat(list.map(function (l) {
    return '  [' + l.code + '] ' + l.body +
      (withCaps && l.capabilities.length ? '  (about: ' + l.capabilities.join(', ') + ')' : '');
  })).join('\n');
}

function replyRequest(r, item) {
  const theirs = rStr(r.reply_text);
  if (!theirs) return null;          // nothing to answer; nothing is written
  const pools = replyPools(r);
  if (!pools.ask.length) return null; // the library cannot complete a reply

  const record = replyRecord(r);
  const thread = threadText(r);
  const open = topicsIn(theirs);
  const deferrals = open.map(function (t) { return '  - ' + t + ': ' + topicLabel(t); }).join('\n');

  const prompt = [
    'THEIR MESSAGE, which arrived ' + rStr(r.received_at) + ' from ' + rStr(r.from_addr) +
      ' with the subject "' + rStr(r.subject) + '". This is what you are answering:',
    '---',
    theirs,
    '---',
    '',
    'THE THREAD SO FAR -- our own messages, exactly as they were sent:',
    thread || '  (nothing on record)',
    '',
    'THEIR RECORD. Every fact you may state about them, and only what it says:',
    record,
    '',
    'APPROVED CLAIMS. Everything you may say about what we built, the problem it solves and who',
    'uses it comes from these lines. Rephrase freely; never widen what a line says.',
    '',
    sheet('WHAT IT IS:', pools.description, true),
    '',
    sheet('PAIN AND STAKES:', pools.angle, false),
    '',
    sheet('WHAT IT DOES:', pools.benefit, true),
    '',
    sheet('PROOF -- only if you name a deployment; it is matched to their country:', pools.proof, false),
    '',
    sheet('THE ONE LINK you may include, at most once, and only if it genuinely helps:', pools.link, false),
    '',
    sheet('NEXT STEP -- end with ONE of these, rephrased to fit what they said:', pools.ask, false),
    '',
  ].concat(open.length ? [
    'QUESTIONS YOU MAY NOT ANSWER. They raised these, and nothing above settles any of them:',
    deferrals,
    'For each one, say in a few words that you will confirm it and come back -- a real sentence,',
    'not a brush-off, and no hint of what the answer might be. Name every one of them in',
    '`deferred`, and say in `flag` what the operator has to confirm. Never guess a price, a',
    'product it connects to, a language, anything about hosting or data, or a date.',
    '',
  ] : []).concat([
    'WRITE THE REPLY. Answer what they actually said, in their order, from the sources above.',
    'If they declined, accept it in one line and leave one door open -- never push back.',
    'If they asked something the record answers, answer it plainly.',
    'Then exactly ONE next step, from the list above, sized to what they wrote.',
    'The body plus the next step is ' + REPLY_MIN_WORDS + '-' + REPLY_MAX_WORDS + ' words.',
    'Write no greeting, no sign-off and no opt-out line: all three are added afterwards.',
    'In `claims`, list the code of every approved line the reply used, the next step included.',
  ]).join('\n');

  // Every taxonomy area nothing in front of the model names: the reply may not
  // name one either (Assemble Reply tags it `ungrounded-area`).
  const corpus = [theirs, thread, record].concat(
    ['description', 'angle', 'benefit', 'proof', 'ask', 'link'].map(function (s) {
      return pools[s].map(function (l) { return l.body; }).join('\n');
    })).join('\n');
  const plain = ' ' + rFold(corpus).replace(/[^a-z0-9]+/g, ' ') + ' ';
  const absent = THERAPEUTIC_AREAS.filter(function (a) {
    return a !== 'Other' && plain.indexOf(' ' + rFold(a).replace(/[^a-z0-9]+/g, ' ') + ' ') === -1;
  });

  return {
    json: {
      lead_id: r.lead_id,
      inbound_message_id: r.inbound_message_id,
      country: r.country,
      company_name: r.company_name,
      domain: r.domain,
      from_addr: r.from_addr,
      their_subject: r.subject,
      their_text: theirs,
      received_at: r.received_at,
      // The subject threads the conversation: theirs with one 'Re:' on it.
      subject: 'Re: ' + rStr(r.subject).replace(/^(?:(?:re|fwd|fw|aw|sv|vs)\s*:\s*)+/i, ''),
      greeting: rStr(r.their_name) ? 'Hello ' + rStr(r.their_name) + ',' : 'Hello,',
      // The From display name is not stored, so the quoted header names the
      // address that wrote. A name is used only in the greeting, and only when
      // it is the contact on file (their_name) -- a first name guessed out of a
      // local part would be an invented fact in the first line of the reply.
      quote_attribution: rStr(r.their_name) ? rStr(r.their_name) + ' <' + rStr(r.from_addr) + '>'
        : rStr(r.from_addr),
      library_pools: pools,
      open_topics: open,
      absent_areas: absent,
      corpus: corpus,
      sender_name: SENDER_NAME,
      prompt: prompt,
      // Prompt caching (Section 3, on since 2026-10-08). The cached block is
      // exactly the system prompt: build-substituted, so byte-identical for
      // every reply and every run, and comfortably over Sonnet 5.5's 512-token
      // minimum. Everything about this prospect is in the user message after
      // it, where a cache entry would be read by nothing. The 5-minute TTL and
      // why a run's own calls cannot read each other's write are recorded in
      // drafting's code_assess.js.
      request: Object.assign({}, CLAUDE_REQUEST, {
        system: [{ type: 'text', text: REPLY_SYSTEM_PROMPT, cache_control: CACHE_CONTROL }],
        messages: [{ role: 'user', content: prompt }],
      }),
    },
    pairedItem: { item: item },
  };
}

// ---------------------------------------------------------------------------
// Node body (Run Once for All Items)
// ---------------------------------------------------------------------------
//
// An empty queue can arrive as one { success: true } item: the Postgres node
// emits that placeholder when it does not classify a statement as a SELECT, and
// a WITH ... SELECT is exactly the shape its parser has to guess at. Keeping
// only rows that carry a lead_id turns that into zero items, so nothing
// downstream runs -- and no paid call is made.
const out = [];
$input.all().forEach(function (it, i) {
  if (!it.json || it.json.lead_id === undefined || it.json.lead_id === null) return;
  const built = replyRequest(it.json, i);
  if (built) out.push(built);
});
return out;

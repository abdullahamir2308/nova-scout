// Build Follow-Up -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 6: "if no reply after 6 days, generate follow-up draft
// back into the review queue. Maximum two follow-ups, then mark lost."
//
// Drafting skill v3 section 8 (2026-10-02): a follow-up is composed by the
// drafting model under the first touch's claim rules -- it is no longer a fixed
// template. This node builds the request for each due lead: the first email
// exactly as it was sent, and the approved claims that email did not already
// use. Claude Follow-Up writes the note; Assemble Follow-Up checks it against
// the same rules code_assemble.js enforces on a first touch and appends the
// fixed frame (greeting, opt-out, signature, the quoted first email).
//
// Nothing about the prospect reaches the model except the first email itself.
// A follow-up adds no fact about the lead, so build rule 6 still holds by
// construction: the only facts are ones a human already approved and sent.
//
// A mark-lost row needs no model and goes straight to Write Follow-Up.

// Section 5 (verbatim) and Section 9, parsed from the doc at build time.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";
const MAX_FOLLOW_UPS = 2;

// Skill v3 section 8, parsed from the skill at build time: follow-up #1 is
// 40-70 words, #2 a short final note of at most 40. Repeated in the per-lead
// message so the number the model sees is the number Assemble Follow-Up checks.
const FOLLOW_UP_1_MIN = 40;
const FOLLOW_UP_1_MAX = 70;
const FOLLOW_UP_2_MAX = 40;

const SENDER_NAME = __SENDER_NAME__;
const SYSTEM_PROMPT = __FOLLOWUP_SYSTEM_PROMPT__;
// Model, max_tokens, effort and the JSON schema -- the drafting node's request
// parameters (Master Ref Section 3), substituted at build time.
const CLAUDE_REQUEST = __CLAUDE_REQUEST__;
// Prompt caching (Section 3). The same breakpoint the drafting node uses, so
// the two cannot drift; the build checks it against drafting's own constant.
const CACHE_CONTROL = { type: 'ephemeral' };
// Section 9's locked taxonomy: a follow-up may name only the areas the first
// email named.
const THERAPEUTIC_AREAS = __THERAPEUTIC_AREAS__;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function greetingOf(firstBody) {
  const first = str(String(firstBody || '').replace(/\r\n?/g, '\n').split('\n')[0]);
  return /^(hi|hello|dear)\b[^\n]{0,40},$/i.test(first) ? first : 'Hello,';
}

// The first email as the prospect read it, without its greeting and without
// the opt-out line and signature: those are fixed text, and quoting them to the
// model only invites a second copy.
function firstCore(firstBody) {
  const b = String(firstBody || '').replace(/\r\n?/g, '\n');
  const at = b.indexOf(OPT_OUT);
  const lines = (at === -1 ? b : b.slice(0, at)).trim().split('\n');
  if (lines.length && /^(hi|hello|dear)\b[^\n]{0,40},$/i.test(str(lines[0]))) lines.shift();
  return lines.join('\n').trim();
}

// The claim codes a draft's variant records: 'role-inbox/D1.ANG-HOURS.PR-TR.A1+tags'.
function codesOf(variant) {
  const v = str(variant);
  const slash = v.indexOf('/');
  return slash === -1 ? [] : v.slice(slash + 1).split('+')[0].split('.').filter(Boolean);
}

// The active claims-library rows, as Find Due Follow-Ups aggregated them.
function libraryRows(raw) {
  const out = [];
  for (const row of Array.isArray(raw) ? raw : []) {
    if (!row || typeof row !== 'object' || !str(row.body)) continue;
    out.push({
      code: str(row.code),
      slot: str(row.slot),
      body: str(row.body),
      countries: Array.isArray(row.countries) && row.countries.length ? row.countries.map(fold) : null,
      measured: row.measured === true,
      confirmed: row.confirmed === true,
      capabilities: Array.isArray(row.capabilities) ? row.capabilities.map(str).filter(Boolean) : [],
    });
  }
  return out;
}

// The proof line for this lead's country -- the same resolution Assess
// Grounding makes for the first touch (a measured line wins; a row with no
// countries serves everyone else).
function forCountry(rows, country) {
  const key = fold(country);
  const serving = rows.filter(function (x) { return x.countries && x.countries.indexOf(key) !== -1; });
  const pick = serving.length ? serving : rows.filter(function (x) { return !x.countries; });
  const measured = pick.filter(function (x) { return x.measured; });
  return measured.length ? measured : pick;
}

function lines(rows) {
  return rows.map(function (x) {
    return { code: x.code, body: x.body, confirmed: x.confirmed, capabilities: x.capabilities };
  });
}

// What a follow-up may draw on. #1 adds one angle or benefit the first email
// did not use: a line it used is not offered, and neither is a benefit that
// shares a capability with one it used (skill section 3, No repeats -- BEN-247
// after D2 would say "it answers sponsors" a second time). #2 adds no claim, so
// it is offered only the asks, and the proof in case it names a deployment.
function poolsFor(r, n) {
  const lib = libraryRows(r.library);
  const known = lib.map(function (l) { return l.code; });
  const recorded = codesOf(r.first_variant);
  const firstCodes = recorded.filter(function (c) { return known.indexOf(c) !== -1; });
  const firstCaps = [];
  lib.filter(function (l) { return firstCodes.indexOf(l.code) !== -1; }).forEach(function (l) {
    l.capabilities.forEach(function (c) { if (firstCaps.indexOf(c) === -1) firstCaps.push(c); });
  });
  const fresh = function (l) {
    return firstCodes.indexOf(l.code) === -1 &&
      !l.capabilities.some(function (c) { return firstCaps.indexOf(c) !== -1; });
  };
  const bySlot = function (slot) { return lib.filter(function (l) { return l.slot === slot; }); };
  const pools = {
    angle: n === 1 ? lines(bySlot('angle').filter(fresh)) : [],
    benefit: n === 1 ? lines(bySlot('benefit').filter(fresh)) : [],
    proof: lines(forCountry(bySlot('proof'), r.country)),
    ask: lines(bySlot('ask')),
  };
  // Codes the first email recorded that this library no longer holds -- a v2
  // email's P2/O2 ("role-inbox/P2.O2.PR-TR.A2"). Its claims cannot all be read
  // off the variant, so the model is told to judge them from the text.
  const retired = recorded.filter(function (c) { return known.indexOf(c) === -1; });
  return { pools: pools, firstCodes: firstCodes, retired: retired };
}

// --- For the claim check only (auto-approval, migration 014) ----------------
//
// Neither of these reaches the composing model, which still sees only the first
// email. They go to the Approval Gate and the claim check, which compares each
// prospect fact in the follow-up with the enrichment record and each claim with
// the confirmed lines.
//
// The record is worded as Workflow 4's fact sheet words it, boundaries
// included. It holds no trial: Workflow 4 looks the trial up at
// ClinicalTrials.gov on each run and stores it nowhere, so a follow-up that
// says something about one cannot be checked here and is held for a person
// (a reference back to the first email that only mentions it passes as a
// reference -- measured 2026-10-03, draft 107).
function prospectRecord(r) {
  const facts = [];
  const areas = Array.isArray(r.therapeutic_areas) ? r.therapeutic_areas.map(str).filter(Boolean) : [];
  if (areas.length) {
    facts.push('Therapeutic areas listed on their own website: ' + areas.join(', ') +
      '. This says nothing about which area any particular trial belongs to.');
  }
  const phases = Array.isArray(r.phases) ? r.phases.map(str).filter(Boolean) : [];
  if (phases.length) facts.push('Trial phases listed on their own website: ' + phases.join(', ') + '.');
  if (str(r.city)) {
    facts.push('The company is based in ' + str(r.city) +
      '. This says nothing about where any trial runs or where any staff sit.');
  }
  if (str(r.founder_name)) {
    facts.push('Named on their own site as founder or MD: ' + str(r.founder_name) +
      '. This says nothing about which trials or clients this person personally handles.');
  }
  const n = Number(r.employee_estimate);
  if (r.employee_estimate !== null && r.employee_estimate !== undefined && Number.isInteger(n) && n > 0) {
    facts.push('Their own site states a team of ' + n + ' people. This says nothing about what they do.');
  }
  facts.push('No trial is part of this record. The first email may name one; nothing here confirms it.');
  return ['Company: ' + str(r.company_name) + ' (the email never names it)', 'Country: ' + str(r.country)]
    .concat(facts.map(function (f, i) { return String(i + 1) + '. ' + f; })).join('\n');
}

// Every line a follow-up may draw on or refer back to, with its confirmation:
// the descriptions, angles, benefits and asks, and only this lead's proof. A
// follow-up opens by referring to the first email's angle, which its own pool
// leaves out, so the checker gets the whole set.
function checkClaims(r) {
  const lib = libraryRows(r.library);
  const proof = forCountry(lib.filter(function (l) { return l.slot === 'proof'; }), r.country);
  return lib.filter(function (l) { return ['description', 'angle', 'benefit', 'ask'].indexOf(l.slot) !== -1; })
    .concat(proof)
    .map(function (l) { return { code: l.code, slot: l.slot, body: l.body, confirmed: l.confirmed }; });
}

function sheet(heading, list, withCaps) {
  return [heading].concat(list.map(function (l) {
    return '  [' + l.code + '] ' + l.body + (withCaps && l.capabilities.length ? '  (about: ' + l.capabilities.join(', ') + ')' : '');
  })).join('\n');
}

function followUpRequest(r, item) {
  const n = Number(r.next_follow_up);
  if (!(n >= 1 && n <= MAX_FOLLOW_UPS)) {
    throw new Error('no follow-up #' + r.next_follow_up + ' (max ' + MAX_FOLLOW_UPS + ')');
  }
  const firstBody = String(r.first_body || '').replace(/\r\n?/g, '\n').trim();
  const core = firstCore(firstBody);
  const sent = new Date(r.first_sent_at);
  const when = isNaN(sent.getTime()) ? 'earlier' : sent.toUTCString().slice(5, 16);
  const resolved = poolsFor(r, n);
  const pools = resolved.pools;

  // A #1 with nothing new left to say cannot be written to the skill. It is
  // held (no output item, so nothing is written and the lead stays due) rather
  // than sent with a claim the first email already made.
  if (n === 1 && !pools.angle.length && !pools.benefit.length) return null;
  if (!pools.ask.length) return null;

  // What the first email already said. From its codes when the current library
  // holds every one; otherwise (an email written under an older claims list)
  // the model reads the text and reports first_email_covers, which Assemble
  // Follow-Up checks the added claim against.
  const firstNote = resolved.retired.length || !resolved.firstCodes.length
    ? 'The first email was written before the current claims list, so its claims cannot be read off codes.\n' +
      'Read it, and in first_email_covers list the code of every angle and benefit below whose point it\n' +
      'already made -- in any words. Never add one of those.'
    : 'The first email used these approved claims: ' + resolved.firstCodes.join(', ') + '. None of them, and no\n' +
      'line repeating what they say, is offered again below. Still list in first_email_covers any angle or\n' +
      'benefit below whose point it made in other words, and never add one of those.';

  const prior = str(r.last_follow_up_body)
    ? ['', 'YOUR FIRST FOLLOW-UP, also unanswered:', '---', firstCore(r.last_follow_up_body), '---']
    : [];

  // When the first email's codes say what it used, the lists below are already
  // filtered; when they cannot (an older claims list), the lists are complete and
  // the headings say so -- "did not use" would contradict the instruction above.
  const byCode = !(resolved.retired.length || !resolved.firstCodes.length);
  const claims = n === 1
    ? [sheet(byCode ? 'PAIN AND STAKES ANGLES the first email did not use:'
                    : 'PAIN AND STAKES ANGLES -- leave out any whose point the first email already made:', pools.angle, false),
       sheet(byCode ? 'BENEFITS the first email did not use:'
                    : 'BENEFITS -- leave out any whose point the first email already made:', pools.benefit, true)]
    : [];
  claims.push(sheet('PROOF -- only if you mention a deployment; it is matched to their country:', pools.proof, false));
  claims.push(sheet('ASK -- end with ONE of these, rephrased if you like:', pools.ask, false));

  const task = n === 1
    ? ['This is follow-up 1 of ' + MAX_FOLLOW_UPS + '.',
       'Open by referring back to the first email in a few words, then add exactly ONE angle or benefit from',
       'the lists above that the first email did not make -- in that line\'s own words, almost word for word,',
       'rephrased only for grammar, with nothing added about what it achieves. Then the one ask.',
       'The body plus the ask is ' + FOLLOW_UP_1_MIN + '-' + FOLLOW_UP_1_MAX + ' words. Put the added line\'s code in added_claim.']
    : ['This is follow-up 2 of ' + MAX_FOLLOW_UPS + ' -- the short final note.',
       'Say this is the last note, and restate the offer as the one ask. Add no new claim: added_claim is "".',
       'The body plus the ask is at most ' + FOLLOW_UP_2_MAX + ' words.'];

  const prompt = [
    'Country: ' + str(r.country),
    '',
    'THE FIRST EMAIL, sent ' + when + ' with the subject "' + str(r.first_subject) + '". The prospect has not',
    'replied. It is everything you may say about them, and only what it says:',
    '---',
    core,
    '---',
  ].concat(prior, [
    '',
    firstNote,
    '',
    'APPROVED CLAIMS FOR THIS NOTE. Nothing about the product, the problem or the proof may come from',
    'anywhere else. Rephrase freely; never widen what a line says.',
    '',
    claims.join('\n\n'),
    '',
  ], task, [
    'Write no greeting, no sign-off and no opt-out line -- those are added afterwards. In claims, list the',
    'code of every approved claim the note used.',
  ]).join('\n');

  // Every taxonomy area the first email does not name: the follow-up may not
  // name one either (Assemble Follow-Up tags it `ungrounded-area`).
  const plain = ' ' + fold(core).replace(/[^a-z0-9]+/g, ' ') + ' ';
  const absent = THERAPEUTIC_AREAS.filter(function (a) {
    return a !== 'Other' && plain.indexOf(' ' + fold(a).replace(/[^a-z0-9]+/g, ' ') + ' ') === -1;
  });
  // The numbers a follow-up may state: the first email's and the claims'.
  const corpus = [core].concat(['angle', 'benefit', 'proof', 'ask'].map(function (s) {
    return pools[s].map(function (l) { return l.body; }).join('\n');
  })).join('\n');

  return {
    json: {
      lead_id: r.lead_id,
      needs_model: true,
      follow_up: n,
      country: r.country,
      first_subject: r.first_subject,
      first_body: firstBody,
      first_sent_at: r.first_sent_at,
      greeting: greetingOf(firstBody),
      first_codes: resolved.firstCodes,
      library_pools: pools,
      absent_areas: absent,
      corpus: corpus,
      // Auto-approval (migration 014): for the Approval Gate and the claim
      // check, never for the composing model.
      auto_approve: r.auto_approve_email === true,
      prospect_record: prospectRecord(r),
      check_claims: checkClaims(r),
      first_core: core,
      prompt: prompt,
      // Prompt caching, on since 2026-10-08. SYSTEM_PROMPT is 1,169 tokens,
      // over Sonnet 5.5's 512-token minimum, and build-time substituted so it is
      // byte-identical for every follow-up and every run; everything about the
      // lead is in the user message after it. The default 5-minute TTL, the
      // measurements behind it, and why a run's own calls cannot read each
      // other's write are all in drafting's code_assess.js.
      request: Object.assign({}, CLAUDE_REQUEST, {
        system: [{ type: 'text', text: SYSTEM_PROMPT, cache_control: CACHE_CONTROL }],
        messages: [{ role: 'user', content: prompt }],
      }),
    },
    pairedItem: { item: item },
  };
}

function followUpAction(r, item) {
  if (r.action === 'mark-lost') {
    return {
      json: { lead_id: r.lead_id, needs_model: false, payload: { lead_id: r.lead_id, action: 'mark-lost', draft: null } },
      pairedItem: { item: item },
    };
  }
  return followUpRequest(r, item);
}

// ---------------------------------------------------------------------------
// Node body (Run Once for All Items)
// ---------------------------------------------------------------------------

// An empty queue can arrive here as one { success: true } item: the Postgres
// node emits that placeholder when it does not classify a statement as a
// SELECT, and a WITH ... SELECT is exactly the case its parser has to guess at.
// Keeping only rows that carry a lead_id turns that into zero items, so nothing
// downstream runs -- instead of this node throwing on a row that was never a
// lead. Each output names the input row it came from (pairedItem), so Assemble
// Follow-Up can read this node's item for the response it is checking.
const out = [];
$input.all().forEach(function (it, i) {
  if (!it.json || it.json.lead_id === undefined || it.json.lead_id === null) return;
  const built = followUpAction(it.json, i);
  if (built) out.push(built);
});
return out;

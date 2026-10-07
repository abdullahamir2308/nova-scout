// ===========================================================================
// Approval Gate -- n8n Code node (Run Once for Each Item).
//
// Auto-approval of email drafts (migration 014), shared by Workflow 4
// (Drafting) and Workflow 6 (Follow-Ups): both builds embed this file, and
// code_approval_apply.js after everything above this file's "Node body" marker,
// so the two workflows judge a draft with the same functions.
//
// An email draft -- first touch or follow-up -- is approved by the workflow
// that wrote it only if ALL of these hold:
//
//   1. settings.auto_approve_email is on          (read at runtime, migration 014)
//   2. it carries no rule tag                     (nothing after '+' in variant)
//   3. it is not a low-context note, nor a no-send-clock note
//   3b. its lead's country has a Send business-hours clock (Section 12)
//   4. every claim code it records is confirmed   (claims_library.confirmed)
//   5. a second Sonnet 5.5 call compares each product claim with the confirmed
//      claims and each prospect fact with the enrichment record, and every one
//      is supported. A widened claim is a failure.
//
// 1-4 are deterministic and decided HERE, before any money is spent: a draft
// that fails one is held, and the model is not called. 5 is the Claude Claim
// Check node; Apply Claim Check (code_approval_apply.js) reads its answer.
// A LinkedIn draft is never auto-approved (Section 6) and never checked.
//
// A held draft is written 'pending' with hold_reason saying why. The database
// re-checks 1-4 itself (migration 014's drafts_approval_rules) and turns an
// auto-approval that breaks one into a hold, so a bug here can make the
// workflow approve less, never more.
//
// THE REPAIR LOOP (2026-10-03). When 5 holds an email for a widened or
// unsupported statement, the drafting model is sent the email and the
// checker's exact reasons and rewrites only the flagged sentences
// (code_approval_apply.js builds that request; code_approval_repair.js
// applies the rewrites -- by code, so nothing else in the email can change).
// The repaired composition then runs through a second copy of the Assemble
// node (every rule again, the same code), this gate, and the claim check
// again. At most MAX_REPAIRS repairs; the workflows unroll exactly that many
// rounds. Nothing about rules 1-5 is relaxed: a repair is one more chance to
// pass the same checks. A draft is HELD for a claim-check failure only after
// the repairs, and hold_reason then lists every attempt's reasons.
//
// The item this node reads carries `payload` (the drafts Write will insert --
// `payload.drafts` in Workflow 4, `payload.draft` in Follow-Ups) and
// `approval`, the context its Assemble node built:
//
//   approval.kind          'first-touch' | 'follow-up'
//   approval.auto_approve  settings.auto_approve_email, as the batch query read it
//   approval.record        the prospect record: the facts the draft may state
//   approval.claims        [{code, slot, body, confirmed}] -- the lines it may use
//   approval.first_email   the first email as sent (follow-ups only), context
//   approval.check         {subject, text}: the generated text the email carries
//   approval.repair_fields the composition fields that text came from
//
// plus `composition` (the model's parsed answer, which a repair edits) and,
// after a repair, `repair` (the attempts so far, for the record).
//
// A low-context item (Workflow 4's other branch) has no `approval`; its drafts
// are held for what they are.
// ===========================================================================

// Section 3: the claim check is a second call to the drafting model, with the
// drafting node's request parameters -- no temperature/top_p (a 400 on this
// model), adaptive thinking by default, effort set explicitly, JSON through
// output_config.format with a strict schema. Both builds assert these against
// the Master Ref.
const CHECK_MODEL = 'claude-sonnet-5-5';
const CHECK_EFFORT = 'high';
const CHECK_MAX_TOKENS = 16000;

// Section 12's business-hours clocks, as a list of country names, substituted
// at build time from the doc's own table by whichever generator embeds this
// file -- the same table code_decide.js's COUNTRY_CLOCKS is checked against.
//
// Rule 3b. Send refuses an email to a country with no clock for ever
// (`unknown-country`), so approving one would put a draft in the digest's
// auto-approved list that can never go out, and pay for a claim check to get
// there. Assess Grounding already stops such a lead before the drafting call;
// this is the gate's own copy of that rule, because a follow-up, a redraft
// under an older build, or a country removed from the table later would all
// reach here without passing through that branch.
const SEND_CLOCK_COUNTRIES = __SEND_CLOCK_COUNTRIES__;

// One entry per statement, in the email's order. Every object closes with
// additionalProperties:false, which structured outputs requires.
const CHECK_SCHEMA = {
  type: 'object',
  properties: {
    statements: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          sentence: { type: 'string' },
          statement: { type: 'string' },
          kind: { type: 'string', enum: ['claim', 'prospect', 'none'] },
          source: { type: 'string' },
          why: { type: 'string' },
          verdict: { type: 'string', enum: ['supported', 'widened', 'unsupported', 'joined', 'none'] },
        },
        required: ['sentence', 'statement', 'kind', 'source', 'why', 'verdict'],
        additionalProperties: false,
      },
    },
  },
  required: ['statements'],
  additionalProperties: false,
};

// The worked example of a widening is deliberately NOT draft 99's sentence
// ("..., so you know exactly what came in."): that one is the held-out case the
// dry run proves the check catches, and a check shown its own test case proves
// nothing.
const CHECK_SYSTEM_PROMPT = [
  'You check one outbound sales email before it is sent with no human review. A small company',
  'sells a website assistant to contract research organisations (CROs). Your only job is to find',
  'any statement in the email that says more than its sources support.',
  '',
  'You are given:',
  '- APPROVED CLAIMS: the only things the email may say about what we built, what it does, the',
  '  problem it addresses, who uses it, and the offer. Each has a code. The email may rephrase a',
  '  claim; it may never widen one.',
  '- PROSPECT RECORD: the only facts known about the recipient. A fact may say what it does NOT',
  '  establish; that limit is part of the fact.',
  '- Sometimes THE FIRST EMAIL we already sent them, as context. It is not evidence.',
  '- THE EMAIL TO CHECK. Its greeting, opt-out line and signature are fixed text and are not shown.',
  '',
  'Work through the email one sentence at a time, in order. The subject line, when given, counts',
  'as a sentence. Copy each sentence into "sentence" exactly as it appears. A sentence that makes',
  'several statements gets one entry per statement, each with the same "sentence" and its own',
  '"statement" (the words of the sentence that make that statement).',
  '',
  'Classify each statement:',
  '- "claim": anything about what we built, what it does or will do for them, the problem it',
  '  addresses, who uses it, or the offer (the demo).',
  '- "prospect": anything about the recipient -- their company, people, trials, work or website.',
  '- "none": a question that only asks, a reference back to our earlier email that adds nothing',
  '  new, or wording that asserts nothing.',
  'Words an approved claim itself uses about "you" -- your website, your team, your working',
  'hours -- belong to that claim. Judge them as part of the claim, not as prospect facts.',
  'The product\'s name in brackets, "(we call it Nova)", only names what the sentence describes:',
  'it is "none", not a claim. Judge the rest of that sentence as usual.',
  '',
  'Judge each statement:',
  '- claim: "supported" when an approved claim says the same thing or more -- rephrasing,',
  '  shortening and reordering are fine. "widened" when it rests on an approved claim but says',
  '  more: a broader scope, a stronger degree, a certainty or completeness the claim does not',
  '  state, or an added outcome, consequence or capability. "unsupported" when no approved claim',
  '  makes it.',
  '- prospect: "supported" when the record states it. "joined" when it links facts the record',
  '  keeps separate (a trial "in" their city, an area "of" a trial). "unsupported" when the record',
  '  does not state it.',
  '- none: "none".',
  '',
  'An example of widened, for a different claim. Approved: "It sends your capabilities deck the',
  'moment a sponsor asks for it." Email: "It sends your capabilities deck the moment a sponsor',
  'asks, so no request ever goes unanswered." The added clause claims an outcome -- every',
  'request answered -- that the approved claim does not make.',
  '',
  'When you are unsure whether a statement says more than its source, judge it widened or',
  'unsupported: a held email costs a person a minute, and a widened claim sent costs trust.',
  'Do not judge tone, length or style -- only whether each statement is supported.',
  '',
  'For every claim and prospect statement, "source" is the code of the approved claim, or the',
  'number of the record fact, you compared it with ("" when there is none). "why" is one short',
  'sentence.',
].join('\n');

const KINDS = ['claim', 'prospect', 'none'];
const VERDICTS = ['supported', 'widened', 'unsupported', 'joined', 'none'];

function apStr(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function apFold(v) {
  return apStr(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// The words of a text, for comparing what the checker quoted with what the
// email says: case, accents, punctuation and typographic quotes all fold away.
function apWords(v) {
  return apFold(v).replace(/[^a-z0-9]+/g, ' ').trim();
}

function excerpt(v, n) {
  const t = apStr(v).replace(/\s+/g, ' ');
  const max = n || 90;
  return t.length > max ? t.slice(0, max - 1) + '…' : t;
}

// 'unnamed/D2.ANG-HOURS.BEN-SEE.PR-BOTH.A1+long,claim-repeat'
//   -> codes ['D2','ANG-HOURS','BEN-SEE','PR-BOTH','A1'], tags ['long','claim-repeat']
function variantCodes(variant) {
  const v = apStr(variant);
  const slash = v.indexOf('/');
  return slash === -1 ? [] : v.slice(slash + 1).split('+')[0].split('.').map(apStr).filter(Boolean);
}

function variantTags(variant) {
  const v = apStr(variant);
  const plus = v.indexOf('+');
  return plus === -1 ? [] : v.slice(plus + 1).split(/[,+]/).map(apStr).filter(Boolean);
}

function isLowContext(variant) {
  return /^low-context(\/|$)/.test(apStr(variant));
}

// The other no-model branch (2026-10-07): a lead whose country has no Send
// business-hours clock. Its own prefix, so a reviewer is not told "low context"
// about a lead whose facts were fine.
function isNoSendClock(variant) {
  return /^no-send-clock(\/|$)/.test(apStr(variant));
}

// Section 12's clock table, case-insensitively -- the same way code_decide.js
// looks it up, so the gate and Send can never disagree about one lead.
const SEND_CLOCK_KEYS = SEND_CLOCK_COUNTRIES.map(function (c) { return String(c).toLowerCase(); });

function hasSendClock(country) {
  return SEND_CLOCK_KEYS.indexOf(apStr(country).toLowerCase()) !== -1;
}

// The drafts an item carries, whichever workflow built it.
function draftsOf(payload) {
  if (!payload || typeof payload !== 'object') return [];
  if (Array.isArray(payload.drafts)) return payload.drafts;
  return payload.draft && typeof payload.draft === 'object' ? [payload.draft] : [];
}

function confirmedCodes(ctx) {
  return ((ctx && ctx.claims) || []).filter(function (c) { return c && c.confirmed === true; })
    .map(function (c) { return apStr(c.code); });
}

// Rules 1-4 and the channel rule. Every reason that applies, in this order, so
// the digest and the reviewer see all of them -- not just the first.
function gateReasons(draft, ctx) {
  const reasons = [];
  const variant = apStr(draft && draft.variant);
  if (!draft || draft.channel !== 'email') {
    reasons.push('linkedin: always reviewed and sent by hand');
    return reasons;
  }
  if (isLowContext(variant)) {
    reasons.push('low-context: a note to a person, not an email to send');
    return reasons;
  }
  if (isNoSendClock(variant)) {
    reasons.push('no-send-clock: a note to a person, not an email to send');
    return reasons;
  }
  if (!ctx || typeof ctx !== 'object') {
    reasons.push('no-context: the draft arrived without its approval context');
    return reasons;
  }
  const country = apStr(ctx.country);
  if (!hasSendClock(country)) {
    // Held, not repaired and never checked: no rewrite can give a country a
    // business-hours clock, and Send would refuse the email anyway.
    reasons.push('no-send-clock: ' + (country || '(no country)') +
      ' has no business-hours clock, so this email can never be sent (Section 12)');
    return reasons;
  }
  if (ctx.auto_approve !== true) reasons.push('auto-approve-off: settings.auto_approve_email is false');
  const tags = variantTags(variant);
  if (tags.length) reasons.push('rule-tags: ' + tags.join(', '));
  const codes = variantCodes(variant);
  if (!codes.length) {
    reasons.push('no-claims: the draft records no claim codes');
  } else {
    const ok = confirmedCodes(ctx);
    const bad = codes.filter(function (c) { return ok.indexOf(c) === -1; });
    if (bad.length) reasons.push('unconfirmed-claim: ' + bad.join(', '));
  }
  if (!apStr(ctx.check && ctx.check.text)) reasons.push('no-context: no generated text to check');
  return reasons;
}

function checkText(check) {
  const subject = apStr(check && check.subject);
  return (subject ? subject + '\n' : '') + apStr(check && check.text);
}

function checkPrompt(ctx) {
  const claims = (ctx.claims || []).filter(function (c) { return c && c.confirmed === true; });
  const followUp = ctx.kind === 'follow-up';
  const subject = apStr(ctx.check && ctx.check.subject);
  const out = [
    'APPROVED CLAIMS:',
  ].concat(claims.map(function (c) { return '  [' + apStr(c.code) + '] (' + apStr(c.slot) + ') ' + apStr(c.body); }), [
    '',
    'PROSPECT RECORD:',
    apStr(ctx.record) || '(nothing is known about them)',
  ]);
  if (followUp && apStr(ctx.first_email)) {
    out.push('', 'THE FIRST EMAIL, already sent to them -- context only, not evidence:', '---',
      apStr(ctx.first_email), '---');
  }
  out.push('', 'THE EMAIL TO CHECK (' + (followUp ? 'a follow-up to the first email' : 'a first email') + '):');
  if (subject) out.push('Subject: ' + subject);
  out.push('---', apStr(ctx.check && ctx.check.text), '---', '',
    'Check every sentence' + (subject ? ', the subject line included' : '') + ', in order.');
  return out.join('\n');
}

function checkRequest(ctx) {
  return {
    model: CHECK_MODEL,
    max_tokens: CHECK_MAX_TOKENS,
    output_config: { effort: CHECK_EFFORT, format: { type: 'json_schema', schema: CHECK_SCHEMA } },
    system: CHECK_SYSTEM_PROMPT,
    messages: [{ role: 'user', content: checkPrompt(ctx) }],
  };
}

// Did the checker read the whole email? Every sentence it quoted must be in the
// email, and once they are all taken out, nothing may be left. A statement it
// skipped is a statement nobody judged -- the one way a check passes a draft it
// never read.
//
// The prompt shows the subject as "Subject: ...", and the checker copies the
// label with it (measured on the first dry run: "Subject: Your Minimally
// Invasive trial and sponsor inquiries"). The label is not in the email, so it
// is dropped from a quote before matching -- only when the quote leads with it.
function coverage(text, statements) {
  const full = ' ' + apWords(text) + ' ';
  let rest = full;
  const seen = {};
  const foreign = [];
  (statements || []).forEach(function (s) {
    const w = apWords(s && s.sentence).replace(/^subject /, '');
    if (!w || seen[w]) return;
    seen[w] = true;
    const needle = ' ' + w + ' ';
    const at = rest.indexOf(needle);
    if (at !== -1) {
      rest = rest.slice(0, at) + ' ' + rest.slice(at + needle.length);
    } else if (full.indexOf(needle) === -1) {
      foreign.push(apStr(s.sentence));
    }
  });
  const left = rest.trim() ? rest.trim().split(' ') : [];
  return { foreign: foreign, left: left };
}

function sourceCodes(source) {
  return apStr(source).replace(/[\[\]]/g, ' ').split(/[\s,;]+/).map(apStr).filter(Boolean);
}

// The model's answer -> pass, or hold with every reason. The model judges each
// statement; this decides. It never reads a summary verdict from the model --
// there is none to read -- and a claim the model calls "supported" must name a
// confirmed code that exists.
function judgeCheck(resp, ctx) {
  const rec = {
    result: 'hold',
    model: resp && resp.model ? resp.model : null,
    checked_at: new Date().toISOString(),
    usage: resp && resp.usage ? resp.usage : null,
    statements: [],
    reasons: [],
  };
  function fail(reason) {
    rec.reasons = [reason];
    return rec;
  }
  if (!resp || typeof resp !== 'object') return fail('claim-check-failed: no response');
  if (resp.error) {
    const e = resp.error;
    return fail('claim-check-failed: ' + excerpt(typeof e === 'string' ? e : JSON.stringify(e), 200));
  }
  if (resp.stop_reason !== 'end_turn') {
    const why = resp.stop_details && resp.stop_details.category ? ' (' + resp.stop_details.category + ')' : '';
    return fail('claim-check-failed: stop_reason ' + JSON.stringify(resp.stop_reason) + why);
  }
  const text = (Array.isArray(resp.content) ? resp.content : [])
    .filter(function (b) { return b && b.type === 'text'; })
    .map(function (b) { return b.text; })
    .join('');
  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    parsed = null;
  }
  const list = parsed && Array.isArray(parsed.statements) ? parsed.statements : null;
  const wellFormed = list && list.every(function (s) {
    return s && typeof s === 'object' && typeof s.sentence === 'string' && typeof s.statement === 'string' &&
      typeof s.source === 'string' && typeof s.why === 'string' &&
      KINDS.indexOf(s.kind) !== -1 && VERDICTS.indexOf(s.verdict) !== -1;
  });
  if (!wellFormed) return fail('claim-check-failed: the answer was not the check schema: ' + excerpt(text, 120));
  rec.statements = list;

  const reasons = [];
  const cov = coverage(checkText(ctx.check), list);
  if (cov.foreign.length) {
    reasons.push('claim-check-incomplete: quoted text that is not in the email ("' + excerpt(cov.foreign[0], 60) + '")');
  }
  if (cov.left.length) {
    reasons.push('claim-check-incomplete: ' + cov.left.length + ' word' + (cov.left.length === 1 ? '' : 's') +
      ' never reviewed ("' + excerpt(cov.left.join(' '), 60) + '")');
  }
  if (ctx.kind === 'first-touch' && !list.some(function (s) { return s.kind === 'claim'; })) {
    reasons.push('claim-check-incomplete: no product claim found in a first email');
  }

  const ok = confirmedCodes(ctx);
  const problems = [];
  list.forEach(function (s) {
    const what = '"' + excerpt(s.statement || s.sentence) + '"';
    const src = apStr(s.source) ? ' (' + apStr(s.source) + ')' : '';
    if (s.kind === 'claim') {
      const codes = sourceCodes(s.source);
      if (s.verdict !== 'supported') {
        problems.push(s.verdict + ' ' + what + src);
      } else if (!codes.length || codes.some(function (c) { return ok.indexOf(c) === -1; })) {
        problems.push('no confirmed claim behind ' + what + src);
      }
    } else if (s.kind === 'prospect') {
      if (s.verdict !== 'supported') problems.push(s.verdict + ' fact ' + what + src);
    } else if (s.verdict !== 'none') {
      problems.push(s.verdict + ' ' + what + src);
    }
  });
  if (problems.length) reasons.push('claim-check: ' + problems.join('; '));

  rec.reasons = reasons;
  rec.result = reasons.length ? 'hold' : 'pass';
  return rec;
}

// ---------------------------------------------------------------------------
// The repair loop
// ---------------------------------------------------------------------------

// The workflows unroll exactly this many repair rounds; both builds refuse a
// different count.
const MAX_REPAIRS = 2;

// What a repair may fix: a statement the checker judged to say more than its
// source. A check that failed as a process (an HTTP error, a refusal, quotes
// that do not cover the email, a "supported" claim citing no confirmed code) is
// not a sentence the composer can rewrite, so it is held as it is.
const REPAIRABLE = ['widened', 'unsupported', 'joined'];

const REPAIR_SCHEMA = {
  type: 'object',
  properties: {
    rewrites: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          original: { type: 'string' },
          replacement: { type: 'string' },
        },
        required: ['original', 'replacement'],
        additionalProperties: false,
      },
    },
  },
  required: ['rewrites'],
  additionalProperties: false,
};

const REPAIR_SYSTEM_PROMPT = [
  'You repair one outbound sales email that an automatic claim check held. A small company sells a',
  'website assistant to contract research organisations (CROs). The check found sentences that say',
  'more than their sources support. Rewrite ONLY those sentences, so that each says no more than its',
  'source. Every other sentence stays exactly as it is -- you are not shown it to change it.',
  '',
  'You are given APPROVED CLAIMS (the only things the email may say about what we built, what it does,',
  'the problem it addresses, who uses it, and the offer), the PROSPECT RECORD (the only facts about the',
  'recipient), the email, and each flagged sentence with the checker\'s exact reason.',
  '',
  'For each flagged sentence:',
  '- Remove what the checker says goes beyond the source. Say what the approved claim says, in its own',
  '  words, rephrased only for grammar and to fit the sentence.',
  '- Add nothing: no new claim, fact, number, outcome, consequence or name. Shorter is better than',
  '  wider.',
  '- If nothing in the sentence can stay without the widening, make it the approved claim it rests on,',
  '  or return an empty replacement to delete the sentence.',
  '- Keep "(we call it Nova)" if the sentence has it. No greeting, no links.',
  '',
  'Return JSON: rewrites, one per flagged sentence -- "original" (the flagged sentence exactly as it is',
  'given to you) and "replacement".',
].join('\n');

// The flagged sentences, each with every reason the checker gave for it.
function flaggedSentences(statements) {
  const out = [];
  const byKey = {};
  (statements || []).forEach(function (s) {
    if (!s || REPAIRABLE.indexOf(s.verdict) === -1) return;
    const key = apWords(s.sentence).replace(/^subject /, '');
    if (!key) return;
    if (!byKey[key]) {
      byKey[key] = { sentence: apStr(s.sentence).replace(/^subject:\s*/i, ''), reasons: [] };
      out.push(byKey[key]);
    }
    byKey[key].reasons.push({ statement: apStr(s.statement), kind: s.kind, verdict: s.verdict,
      source: apStr(s.source), why: apStr(s.why) });
  });
  return out;
}

function repairPrompt(ctx, flagged) {
  const claims = (ctx.claims || []).filter(function (c) { return c && c.confirmed === true; });
  const subject = apStr(ctx.check && ctx.check.subject);
  const out = ['APPROVED CLAIMS:'].concat(claims.map(function (c) {
    return '  [' + apStr(c.code) + '] (' + apStr(c.slot) + ') ' + apStr(c.body);
  }), ['', 'PROSPECT RECORD:', apStr(ctx.record) || '(nothing is known about them)']);
  if (ctx.kind === 'follow-up' && apStr(ctx.first_email)) {
    out.push('', 'THE FIRST EMAIL, already sent to them -- context only:', '---', apStr(ctx.first_email), '---');
  }
  out.push('', 'THE EMAIL:');
  if (subject) out.push('Subject: ' + subject);
  out.push('---', apStr(ctx.check && ctx.check.text), '---', '', 'FLAGGED SENTENCES -- rewrite these, and only these:');
  flagged.forEach(function (f, i) {
    out.push(String(i + 1) + '. ' + f.sentence);
    f.reasons.forEach(function (r) {
      out.push('   checker: ' + r.verdict + ' -- "' + r.statement + '"' + (r.source ? ' (compared with ' + r.source + ')' : '') +
        ': ' + r.why);
    });
  });
  return out.join('\n');
}

function repairRequest(ctx, flagged) {
  return {
    model: CHECK_MODEL,
    max_tokens: CHECK_MAX_TOKENS,
    output_config: { effort: CHECK_EFFORT, format: { type: 'json_schema', schema: REPAIR_SCHEMA } },
    system: REPAIR_SYSTEM_PROMPT,
    messages: [{ role: 'user', content: repairPrompt(ctx, flagged) }],
  };
}

// Where a quoted sentence sits in a field of the composition: compared word by
// word, folded the same way the coverage check folds, so curly quotes, case and
// accents do not stop a match. The span runs to the end of its trailing
// punctuation.
function tokenSpans(text) {
  const re = /[\p{L}\p{N}]+/gu;
  const out = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    out.push({ w: apWords(m[0]), s: m.index, e: m.index + m[0].length });
  }
  return out.filter(function (t) { return t.w; });
}

function locate(text, quote) {
  const q = apWords(quote).replace(/^subject /, '').split(' ').filter(Boolean);
  if (!q.length) return null;
  const t = tokenSpans(text);
  for (let i = 0; i + q.length <= t.length; i++) {
    let ok = true;
    for (let j = 0; j < q.length; j++) {
      if (t[i + j].w !== q[j]) { ok = false; break; }
    }
    if (ok) {
      let end = t[i + q.length - 1].e;
      while (end < text.length && !/\s/.test(text[end])) end++;
      return { s: t[i].s, e: end };
    }
  }
  return null;
}

// The model's rewrites, applied BY CODE to the fields the email's generated
// text came from. A rewrite whose original is not a flagged sentence is
// ignored, so a repair can change nothing the checker did not flag.
function applyRewrites(composition, fields, flagged, rewrites) {
  const out = JSON.parse(JSON.stringify(composition || {}));
  const keys = {};
  flagged.forEach(function (f) { keys[apWords(f.sentence)] = f; });
  const applied = [];
  const ignored = [];
  (rewrites || []).forEach(function (r) {
    const original = apStr(r && r.original).replace(/^subject:\s*/i, '');
    const replacement = apStr(r && r.replacement);
    const f = keys[apWords(original)];
    if (!f) {
      ignored.push({ original: original, why: 'not a flagged sentence' });
      return;
    }
    for (let i = 0; i < (fields || []).length; i++) {
      const field = fields[i];
      if (typeof out[field] !== 'string') continue;
      const at = locate(out[field], f.sentence);
      if (!at) continue;
      const before = out[field].slice(0, at.s);
      const after = out[field].slice(at.e);
      if (replacement) {
        out[field] = before + replacement + after;
      } else {
        // An empty replacement deletes the sentence, and the space beside it.
        const b = before.replace(/[ \t]+$/, '');
        const a = after.replace(/^[ \t]+/, '');
        out[field] = b + (b && a && !/\n$/.test(b) && !/^\n/.test(a) ? ' ' : '') + a;
      }
      applied.push({ field: field, original: f.sentence, replacement: replacement });
      delete keys[apWords(original)];
      return;
    }
    ignored.push({ original: original, why: 'not found in the email' });
  });
  const changed = applied.some(function (a) { return apWords(a.original) !== apWords(a.replacement); });
  return { composition: out, applied: applied, ignored: ignored, changed: changed };
}

// Every attempt's reasons, for hold_reason. One attempt reads as it always
// did; after repairs each attempt is labelled.
function attemptLabel(n) {
  return n === 1 ? 'first draft' : 'after repair ' + (n - 1);
}

function historyText(history) {
  const list = (history || []).filter(function (h) { return h && Array.isArray(h.reasons); });
  if (list.length === 1 && !list[0].repair_failed) return list[0].reasons.join('; ');
  return list.map(function (h) {
    return attemptLabel(h.attempt) + ': ' + h.reasons.join('; ') + (h.repair_failed ? '; repair ' + h.attempt + ' failed: ' + h.repair_failed : '');
  }).join(' | ');
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const item = $input.item.json;
const ctx = item.approval || null;
// A copy: n8n hands this node the upstream item, and Apply Claim Check reads
// this node's output, not the input.
const payload = JSON.parse(JSON.stringify(item.payload || {}));
const list = draftsOf(payload);

// After a repair: the attempts so far belong to the draft the repair was for.
// If this gate now holds it (a rewrite that broke a rule), every earlier
// attempt's reasons stay on it.
const repair = item.repair && typeof item.repair === 'object' ? item.repair : null;
const prior = repair && Array.isArray(repair.history) ? repair.history : [];

const candidates = [];
list.forEach(function (d, i) {
  const reasons = gateReasons(d, ctx);
  d.status = 'pending';
  d.approved_by = null;
  d.claim_check = null;
  d.hold_reason = reasons.length ? reasons.join('; ') : null;
  if (reasons.length && prior.length && repair.target === i) {
    const history = prior.concat([{ attempt: prior.length + 1, result: 'hold', reasons: reasons, statements: [] }]);
    d.hold_reason = historyText(history);
    d.claim_check = { result: 'hold', reasons: reasons, repairs: prior.length, attempts: history,
      compose_usage: repair.compose_usage || null };
  }
  if (!reasons.length) candidates.push(i);
});

// One email draft per item can reach the check (Workflow 4 writes one email and
// one DM per lead; a follow-up is one email). More would mean a payload this
// node was not written for: hold them all rather than guess.
if (candidates.length > 1) {
  candidates.forEach(function (i) { list[i].hold_reason = 'internal: more than one email draft in one item'; });
  candidates.length = 0;
}

return {
  json: Object.assign({}, item, {
    payload: payload,
    needs_check: candidates.length === 1,
    check_target: candidates.length === 1 ? candidates[0] : null,
    check_request: candidates.length === 1 ? checkRequest(ctx) : null,
  }),
};

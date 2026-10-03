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
//   3. it is not a low-context note
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
  if (!ctx || typeof ctx !== 'object') {
    reasons.push('no-context: the draft arrived without its approval context');
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
// Node body
// ---------------------------------------------------------------------------

const item = $input.item.json;
const ctx = item.approval || null;
// A copy: n8n hands this node the upstream item, and Apply Claim Check reads
// this node's output, not the input.
const payload = JSON.parse(JSON.stringify(item.payload || {}));
const list = draftsOf(payload);

const candidates = [];
list.forEach(function (d, i) {
  const reasons = gateReasons(d, ctx);
  d.status = 'pending';
  d.approved_by = null;
  d.claim_check = null;
  d.hold_reason = reasons.length ? reasons.join('; ') : null;
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

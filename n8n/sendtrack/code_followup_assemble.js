// ===========================================================================
// Assemble Follow-Up -- n8n Code node (Run Once for Each Item).
//
// build_workflow.py ships this node as drafting's code_assemble.js -- every
// line above its "Node body" marker, verbatim -- followed by this file. So a
// follow-up is checked by the first touch's own rule functions (askFlags,
// claimFlags, productFlags, aiFlags, repeatFlags, ...), not by a copy that
// could drift from them: drafting skill v3 section 8, "composed by the drafting
// model under every rule above".
//
// What this adds is the follow-up's own rules (skill section 8):
//
//   length     #1 is 40-70 words, #2 at most 40 (body plus ask)
//   new claim  #1 adds exactly one angle or benefit the first email did not use
//              (`fu-no-new-claim`, `fu-repeat`); #2 adds none (`fu-new-claim`)
//   frame      greeting, opt-out, signature and the quoted first email are
//              appended here, never generated -- the same frame the send path
//              checks every follow-up for
//
// A violation tags `variant` rather than discarding the draft, as in Workflow
// 4: the reviewer sees the tag next to the text. A failed call writes nothing,
// and the lead is due again on the next run (skill section 6: no fallback).
// ===========================================================================

// Skill v3 section 8, parsed from the skill at build time.
const FOLLOW_UP_1_MIN = 40;
const FOLLOW_UP_1_MAX = 70;
const FOLLOW_UP_2_MAX = 40;

const SIGNATURE = __SIGNATURE__;
const SENDER_NAME = __SENDER_NAME__;

const src = $('Build Follow-Up').item.json;
const resp = $input.item.json;
const n = Number(src.follow_up);

function failed(detail) {
  return {
    json: {
      lead_id: src.lead_id,
      write: false,
      skip_reason: 'Claude follow-up call did not answer usably: ' + detail,
      usage: resp && resp.usage ? resp.usage : null,
    },
  };
}

if (!resp || typeof resp !== 'object') return failed('no response item');
if (resp.error) {
  const e = resp.error;
  return failed((typeof e === 'string' ? e : JSON.stringify(e)).slice(0, 300));
}
if (resp.stop_reason !== 'end_turn') {
  const why = resp.stop_details && resp.stop_details.category ? ' (' + resp.stop_details.category + ')' : '';
  return failed('stop_reason ' + JSON.stringify(resp.stop_reason) + why);
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
const FU_FIELDS = ['body', 'ask', 'added_claim'];
const FU_LISTS = ['claims', 'first_email_covers'];
if (!parsed || typeof parsed !== 'object' ||
    FU_FIELDS.some(function (f) { return typeof parsed[f] !== 'string'; }) ||
    FU_LISTS.some(function (f) { return !Array.isArray(parsed[f]); })) {
  return failed('response text was not the follow-up schema: ' + text.slice(0, 200));
}

// --- The claims this note used ------------------------------------------------

const pools = src.library_pools || {};
const offered = {};
SLOT_ORDER.forEach(function (slot) {
  (pools[slot] || []).forEach(function (l) {
    offered[l.code] = { slot: slot, confirmed: l.confirmed, capabilities: l.capabilities || [] };
  });
});

const body = cleanText(parsed.body, src.greeting);
const ask = cleanText(parsed.ask, src.greeting);
const core = [body, ask].filter(Boolean).join('\n\n');

// Reported codes, checked against what this note was offered. As in Workflow 4,
// an ask or proof the model left uncoded is attributed from the text -- never
// rely on a model to copy an identifier.
const reported = unique(parsed.claims.map(str).filter(Boolean));
const unknown = reported.filter(function (c) { return !offered[c]; });
const added = str(parsed.added_claim);
let used = reported.filter(function (c) { return offered[c]; });
if (added && offered[added] && used.indexOf(added) === -1) used.push(added);
if (!used.some(function (c) { return offered[c].slot === 'ask'; })) {
  let best = null;
  (pools.ask || []).forEach(function (l) {
    const j = jaccard(ask, l.body);
    if (j >= 0.5 && (!best || j > best.j)) best = { code: l.code, j: j };
  });
  if (best) used.push(best.code);
}
if (!used.some(function (c) { return offered[c].slot === 'proof'; })) {
  (pools.proof || []).forEach(function (l) {
    const names = DEPLOYMENTS.filter(function (d) { return fold(l.body).indexOf(fold(d)) !== -1; });
    if (names.some(function (d) { return fold(core).indexOf(fold(d)) !== -1; }) && used.indexOf(l.code) === -1) used.push(l.code);
  });
}
used = used.sort(function (a, b) { return SLOT_ORDER.indexOf(offered[a].slot) - SLOT_ORDER.indexOf(offered[b].slot); });
const covers = parsed.first_email_covers.map(str).filter(Boolean);

// --- The checks ------------------------------------------------------------------

function followUpFlags() {
  let flags = [];
  const wc = words(core);
  if (!body) flags.push('empty');
  if (n === 1 && wc > FOLLOW_UP_1_MAX) flags.push('long');
  if (n === 1 && wc < FOLLOW_UP_1_MIN) flags.push('short');
  if (n === 2 && wc > FOLLOW_UP_2_MAX) flags.push('long');
  if (bannedAdjectivesIn(core).length) flags.push('adjective');
  if (MERGE_TAG.test(core)) flags.push('merge-tag');
  flags = flags.concat(askFlags(body, ask));
  flags = flags.concat(productFlags(core));
  flags = flags.concat(aiFlags(core));
  if (prospectSponsor(core)) flags.push('prospect-sponsor');
  flags = flags.concat(claimFlags(core, src.corpus));
  if (absentAreasNamed(core, src.absent_areas).length) flags.push('ungrounded-area');
  // A follow-up need not name a deployment, but one it names must be this
  // lead's (proof-geo). No link is approved for a follow-up at all.
  flags = flags.concat(proofFlags(core, pools.proof).filter(function (f) { return f === 'proof-geo'; }));
  flags = flags.concat(linkFlags(core, LINK_FREE_WEEKS + 1, null));
  if (unknown.length || !reported.length) flags.push('claim-code');
  flags = flags.concat(repeatFlags(used, function (c) { return offered[c].capabilities; }));
  // Skill section 8: #1 adds exactly one angle or benefit the first email did
  // not use; #2 adds none.
  if (n === 1) {
    const newOnes = used.filter(function (c) { return ['angle', 'benefit'].indexOf(offered[c].slot) !== -1; });
    if (!added || !offered[added] || ['angle', 'benefit'].indexOf(offered[added].slot) === -1 || newOnes.length !== 1) {
      flags.push('fu-no-new-claim');
    }
    if (added && ((src.first_codes || []).indexOf(added) !== -1 || covers.indexOf(added) !== -1)) flags.push('fu-repeat');
  }
  if (n === 2 && (added || used.some(function (c) { return ['angle', 'benefit'].indexOf(offered[c].slot) !== -1; }))) {
    flags.push('fu-new-claim');
  }
  if (used.some(function (c) { return !offered[c].confirmed; })) flags.push('unconfirmed-claim');
  return unique(flags);
}

const flags = followUpFlags();

// --- The sendable text -----------------------------------------------------------
//
// Greeting / note / ask / opt-out / signature, then the first email quoted. The
// opt-out-then-signature tail is exactly what Workflow 6's send path checks for.

const firstBody = String(src.first_body || '').replace(/\r\n?/g, '\n').trim();
const sent = new Date(src.first_sent_at);
const when = isNaN(sent.getTime()) ? 'earlier' : sent.toUTCString().slice(0, 16);
const quoted = firstBody.split('\n').map(function (ln) { return ln ? '> ' + ln : '>'; }).join('\n');
const full = [
  src.greeting,
  '',
  core,
  '',
  OPT_OUT,
  '',
  SIGNATURE,
  '',
  'On ' + when + ', ' + SENDER_NAME + ' wrote:',
  quoted,
].join('\n');

const variant = 'follow-up-' + n + '/' + used.join('.') + (flags.length ? '+' + flags.join(',') : '');

return {
  json: {
    lead_id: src.lead_id,
    write: true,
    follow_up: n,
    flags: flags,
    words: words(core),
    claims: used,
    first_email_covers: covers,
    model: resp.model,
    usage: resp.usage || null,
    // Auto-approval (migration 014): what the shared Approval Gate and the claim
    // check need. Only the note itself is checked -- not the subject (Re: + the
    // subject already sent) and not the quoted first email, which went out.
    approval: {
      kind: 'follow-up',
      auto_approve: src.auto_approve === true,
      country: src.country,
      record: src.prospect_record,
      claims: src.check_claims,
      first_email: src.first_core,
      check: { subject: null, text: core },
      repair_fields: ['body', 'ask'],
    },
    // The repair loop edits the model's answer and runs it through a second
    // copy of this node; `repair` (set by Apply Repair) carries the attempts.
    composition: parsed,
    repair: resp.repair || null,
    payload: {
      lead_id: src.lead_id,
      action: 'draft-follow-up',
      draft: {
        channel: 'email',
        variant: variant,
        subject: 'Re: ' + str(src.first_subject).replace(/^(?:re:\s*)+/i, ''),
        body: full,
      },
    },
  },
};

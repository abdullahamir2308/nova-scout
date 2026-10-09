// ===========================================================================
// Assemble Reply -- n8n Code node (Run Once for Each Item).
//
// build_workflow.py ships this node as three parts: drafting's code_assemble.js
// above its "Node body" marker (verbatim -- the first touch's own claim rules),
// then code_reply_topics.js (the five questions a reply may not answer), then
// this file. So a reply is judged by askFlags, claimFlags, productFlags,
// aiFlags, proofFlags, linkFlags and the rest exactly as a first touch and a
// follow-up are, rather than by a copy of them that could drift.
//
// What this adds is the reply's own rules:
//
//   length    body plus next step, 40-120 words (`short`, `long`)
//   proof     the "two CROs" wording is never reused and Vertex Clinical
//             Research is never called a CRO -- it is a clinical research
//             centre (operator instruction, 2026-10-09): `proof-refused`
//   deferral  every topic they raised that no claim and no record fact can
//             settle must be named in `deferred` (`deferral-missing`) and must
//             be answered with "we will confirm", not with an answer
//             (`answered-open`)
//   frame     greeting, signature and their quoted message are appended here,
//             never generated
//
// NO OPT-OUT LINE, and that is deliberate. Section 5's "reply 'no' and I won't
// follow up" is the opt-out mechanism for a message the prospect did not ask
// for. This is an answer to a message they sent us, inside their own thread,
// and the opt-out sentence in it would read as a form letter; replying is
// already the trivial opt-out the GDPR/KVKK position requires. The send path
// checks a reply for the SIGNATURE rather than for the opt-out tail
// (code_reply_decide.js), and nothing else about the frame changes.
//
// A violation tags `variant` rather than discarding the draft, as everywhere
// else in Workflow 4: the operator reads the tag next to the text in the
// review email. A failed call writes nothing and the reply is queued again on
// the next run (no fallback model -- skill section 6).
// ===========================================================================

const REPLY_MIN_WORDS = 40;
const REPLY_MAX_WORDS = 120;
const SIGNATURE = __SIGNATURE__;

// Operator instruction, 2026-10-09, checked on the TEXT and not on a code, so a
// reworded library row cannot escape it. The same list Build Reply filters the
// offered proof with -- a reply may still name a deployment the library offers
// for this country, just never in these words.
const REPLY_PROOF_REFUSED = [/\btwo CROs?\b/i, /\bVertex\b[^.]{0,40}\bCRO\b/i];

const src = $('Build Reply').item.json;
const resp = $input.item.json;

function failed(detail) {
  return {
    json: {
      lead_id: src.lead_id,
      inbound_message_id: src.inbound_message_id,
      write: false,
      skip_reason: 'Claude reply call did not answer usably: ' + detail,
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
const REPLY_FIELDS = ['body', 'ask', 'flag'];
const REPLY_LISTS = ['claims', 'deferred'];
if (!parsed || typeof parsed !== 'object' ||
    REPLY_FIELDS.some(function (f) { return typeof parsed[f] !== 'string'; }) ||
    REPLY_LISTS.some(function (f) { return !Array.isArray(parsed[f]); })) {
  return failed('response text was not the reply schema: ' + text.slice(0, 200));
}

// --- the claims this reply used ----------------------------------------------

const pools = src.library_pools || {};
const offered = {};
SLOT_ORDER.forEach(function (slot) {
  (pools[slot] || []).forEach(function (l) {
    offered[l.code] = { slot: slot, confirmed: l.confirmed, capabilities: l.capabilities || [] };
  });
});
const approvedLink = (pools.link || [])[0] || null;

const body = cleanText(parsed.body, src.greeting);
const ask = cleanText(parsed.ask, src.greeting);
const core = [body, ask].filter(Boolean).join('\n\n');

// Reported codes against what this reply was offered. As in Workflow 4 and the
// follow-ups, an ask or a proof the model left uncoded is attributed from the
// text: the model is never trusted to copy an identifier.
const reported = unique(parsed.claims.map(str).filter(Boolean));
const unknown = reported.filter(function (c) { return !offered[c]; });
let used = reported.filter(function (c) { return offered[c]; });
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
    if (names.some(function (d) { return fold(core).indexOf(fold(d)) !== -1; }) && used.indexOf(l.code) === -1) {
      used.push(l.code);
    }
  });
}
used = unique(used).sort(function (a, b) {
  return SLOT_ORDER.indexOf(offered[a].slot) - SLOT_ORDER.indexOf(offered[b].slot);
});

const deferred = unique(parsed.deferred.map(str).filter(Boolean).map(function (d) { return d.toLowerCase(); }));
const openTopics = Array.isArray(src.open_topics) ? src.open_topics : [];
const flag = str(parsed.flag);

// --- the checks ---------------------------------------------------------------

function replyFlags() {
  let flags = [];
  const wc = words(core);
  if (!body) flags.push('empty');
  if (wc > REPLY_MAX_WORDS) flags.push('long');
  if (wc && wc < REPLY_MIN_WORDS) flags.push('short');
  if (bannedAdjectivesIn(core).length) flags.push('adjective');
  if (MERGE_TAG.test(core)) flags.push('merge-tag');
  flags = flags.concat(askFlags(body, ask));
  flags = flags.concat(productFlags(core));
  flags = flags.concat(aiFlags(core));
  if (prospectSponsor(core)) flags.push('prospect-sponsor');
  flags = flags.concat(claimFlags(core, src.corpus));
  if (absentAreasNamed(core, src.absent_areas).length) flags.push('ungrounded-area');
  // A reply need not name a deployment at all; one it DOES name must be this
  // lead's, so only proof-geo applies (the follow-ups make the same cut).
  flags = flags.concat(proofFlags(core, pools.proof).filter(function (f) { return f === 'proof-geo'; }));
  if (REPLY_PROOF_REFUSED.some(function (re) { return re.test(core); })) flags.push('proof-refused');
  // Sites and SMOs (2026-10-09): a site's reply never says "CRO websites" or
  // calls them a CRO. ("Two CROs" and Vertex-as-CRO are proof-refused above.)
  flags = flags.concat(siteFlags(core, src.company_type).filter(function (f) {
    return f === 'cro-website' || f === 'prospect-cro';
  }));
  // The link policy, with the warm-up week deliberately out of range: a reply
  // is not cold outreach, so the week-1-2 link ban does not apply to it. What
  // does apply is the cap -- at most one URL, and only the library's own
  // confirmed `link` line (none is confirmed today, so any URL is tagged).
  flags = flags.concat(linkFlags(core, LINK_FREE_WEEKS + 1, approvedLink));
  if (unknown.length || !reported.length) flags.push('claim-code');
  flags = flags.concat(repeatFlags(used, function (c) { return offered[c].capabilities; }));
  if (used.some(function (c) { return !offered[c].confirmed; })) flags.push('unconfirmed-claim');
  // Every topic they raised has to be named as deferred...
  if (openTopics.some(function (t) { return deferred.indexOf(t) === -1; })) flags.push('deferral-missing');
  // ... and deferred means "we will confirm", not an answer. A reply that talks
  // about one of those topics with no such sentence anywhere has answered it.
  if (openTopics.length && !deferralIn(core)) flags.push('answered-open');
  else if (openTopics.some(function (t) {
    const re = topicRe(t);
    return re && re.test(core) && !deferralIn(core);
  })) {
    flags.push('answered-open');
  }
  return unique(flags);
}

const flags = replyFlags();

// --- the sendable text --------------------------------------------------------
//
// Greeting / reply / next step / signature, then THEIR message quoted. An EDIT
// from the operator replaces only the middle and keeps this frame
// (code_operator_command.js's framedEdit), which is why the signature is the
// marker it splits on.

const theirs = String(src.their_text || '').replace(/\r\n?/g, '\n').trim();
const when = new Date(src.received_at);
const whenText = isNaN(when.getTime()) ? 'earlier' : when.toUTCString().slice(0, 16);
const quoted = theirs.split('\n').map(function (ln) { return ln ? '> ' + ln : '>'; }).join('\n');
const full = [
  src.greeting,
  '',
  core,
  '',
  SIGNATURE,
  '',
  'On ' + whenText + ', ' + str(src.quote_attribution) + ' wrote:',
  quoted,
].join('\n');

const variant = 'reply/' + used.join('.') + (flags.length ? '+' + flags.join(',') : '');

return {
  json: {
    lead_id: src.lead_id,
    inbound_message_id: src.inbound_message_id,
    write: true,
    flags: flags,
    words: words(core),
    claims: used,
    deferred: deferred,
    open_topics: openTopics,
    flag: flag,
    model: resp.model,
    usage: resp.usage || null,
    composition: parsed,
    // What the review email shows the operator, alongside the draft itself.
    review: {
      company_name: src.company_name,
      domain: src.domain,
      country: src.country,
      from_addr: src.from_addr,
      their_subject: src.their_subject,
      their_text: theirs,
      received_at: src.received_at,
      deferred: deferred.map(function (t) { return topicLabel(t); }),
      flag: flag,
      flags: flags,
      words: words(core),
      claims: used,
    },
    payload: {
      lead_id: src.lead_id,
      inbound_message_id: src.inbound_message_id,
      draft: {
        channel: 'email',
        variant: variant,
        subject: src.subject,
        body: full,
      },
    },
  },
};

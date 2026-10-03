// ===========================================================================
// Apply Repair -- n8n Code node (Run Once for Each Item).
//
// Shipped as code_approval.js above its "Node body" marker (the rules, verbatim)
// followed by this file, in Workflow 4 and in Follow-Ups alike, once per repair
// round. The build bakes in which Apply Claim Check copy this one follows.
//
// Reads that node's item -- a draft the claim check held, with the flagged
// sentences and the composition they came from -- and the Claude Repair
// node's answer: one rewrite per flagged sentence. The rewrites are applied BY
// CODE (applyRewrites), only to flagged sentences and only inside the fields
// the email's generated text came from, so a repair cannot touch anything the
// checker did not flag.
//
// The repaired composition leaves this node shaped like a model response, so
// the next node -- a second copy of the Assemble node, the same code -- runs
// every rule on it again, and the Approval Gate and the claim check run again
// after that. `repair` rides along with it, carrying the attempts so far.
//
// A repair that fails -- an HTTP error, a refusal, a cut-off, an answer that is
// not the schema, rewrites that change no flagged sentence -- ends the loop:
// the draft is held (repaired: false) with every reason so far, plus this one.
// ===========================================================================

// Node body

const prev = $('__CHECKED__').item.json;
const resp = $input.item.json;
const st = prev.repair_state || {};
const ctx = prev.approval || {};

function hold(reason) {
  const payload = JSON.parse(JSON.stringify(prev.payload || {}));
  const d = draftsOf(payload)[st.target];
  const history = (st.history || []).slice();
  if (history.length) history[history.length - 1] = Object.assign({}, history[history.length - 1], { repair_failed: reason });
  if (d) {
    d.status = 'pending';
    d.approved_by = null;
    d.hold_reason = historyText(history);
    d.claim_check = Object.assign({}, d.claim_check || {}, {
      result: 'hold', repairs: history.length, attempts: history,
      repair_failed: { reason: reason, usage: resp && resp.usage ? resp.usage : null },
    });
  }
  return {
    json: Object.assign({}, prev, {
      repaired: false, payload: payload, needs_repair: false, repair_request: null, repair_state: null,
    }),
  };
}

if (!resp || typeof resp !== 'object') return hold('no response');
if (resp.error) {
  const e = resp.error;
  return hold(excerpt(typeof e === 'string' ? e : JSON.stringify(e), 200));
}
if (resp.stop_reason !== 'end_turn') {
  const why = resp.stop_details && resp.stop_details.category ? ' (' + resp.stop_details.category + ')' : '';
  return hold('stop_reason ' + JSON.stringify(resp.stop_reason) + why);
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
const wellFormed = parsed && Array.isArray(parsed.rewrites) && parsed.rewrites.every(function (r) {
  return r && typeof r.original === 'string' && typeof r.replacement === 'string';
});
if (!wellFormed) return hold('the answer was not the repair schema: ' + excerpt(text, 120));
if (!st.composition || !Array.isArray(st.flagged)) return hold('no composition to repair');

const res = applyRewrites(st.composition, ctx.repair_fields || [], st.flagged, parsed.rewrites);
if (!res.changed) {
  return hold('the rewrites changed no flagged sentence' +
    (res.ignored.length ? ' (' + res.ignored.map(function (x) { return x.why; }).join(', ') + ')' : ''));
}

return {
  json: {
    repaired: true,
    model: resp.model,
    stop_reason: 'end_turn',
    stop_details: null,
    content: [{ type: 'text', text: JSON.stringify(res.composition) }],
    usage: null,
    repair: {
      round: (st.history || []).length,
      target: st.target,
      history: st.history || [],
      compose_usage: st.compose_usage || null,
      last_repair_usage: resp.usage || null,
      rewrites: res.applied,
      ignored: res.ignored,
    },
  },
};

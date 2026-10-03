// ===========================================================================
// Apply Claim Check -- n8n Code node (Run Once for Each Item).
//
// Shipped as code_approval.js above its "Node body" marker (the rules, verbatim)
// followed by this file, in Workflow 4 and in Follow-Ups alike.
//
// Reads the Approval Gate's item -- the draft that passed rules 1-4 -- and the
// Claude Claim Check node's answer for it, and decides rule 5: approved by the
// workflow ('approved', approved_by 'auto') only if every statement the model
// found is supported AND the model's quotes account for the whole email. Any
// failed call -- an HTTP error, a refusal, a cut-off, an answer that is not the
// schema -- holds the draft for a human: the draft itself is fine, so it is
// written, pending, with the reason. Nothing is retried and nothing falls back
// to another model; the check is Sonnet 5.5's or it is a human's.
//
// The verdicts, the model and the usage go into the draft's claim_check either
// way, so a held draft shows the reviewer exactly which sentence failed.
// ===========================================================================

// Node body

const gate = $('Approval Gate').item.json;
const resp = $input.item.json;
const payload = JSON.parse(JSON.stringify(gate.payload || {}));
const target = draftsOf(payload)[gate.check_target];

if (!target) {
  throw new Error('Apply Claim Check: the Approval Gate item has no draft at check_target ' +
    JSON.stringify(gate.check_target));
}

const verdict = judgeCheck(resp, gate.approval);
target.claim_check = verdict;
if (verdict.result === 'pass') {
  target.status = 'approved';
  target.approved_by = 'auto';
  target.hold_reason = null;
} else {
  target.status = 'pending';
  target.approved_by = null;
  target.hold_reason = verdict.reasons.join('; ');
}

return {
  json: Object.assign({}, gate, {
    payload: payload,
    check_request: null,
    check_result: verdict.result,
    check_reasons: verdict.reasons,
    check_usage: verdict.usage,
  }),
};

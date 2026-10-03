// ===========================================================================
// Apply Claim Check -- n8n Code node (Run Once for Each Item).
//
// Shipped as code_approval.js above its "Node body" marker (the rules, verbatim)
// followed by this file, in Workflow 4 and in Follow-Ups alike -- once per
// attempt: the first draft, and after each repair. The build bakes in which
// Approval Gate copy this one follows ('Approval Gate', 'Approval Gate R1',
// ...).
//
// Reads that gate's item -- the draft that passed rules 1-4 -- and the Claude
// Claim Check node's answer for it, and decides rule 5: approved by the
// workflow ('approved', approved_by 'auto') only if every statement the model
// found is supported AND the model's quotes account for the whole email. Any
// failed call -- an HTTP error, a refusal, a cut-off, an answer that is not the
// schema -- holds the draft for a human: the draft itself is fine, so it is
// written, pending, with the reason. Nothing falls back to another model; the
// check is Sonnet 5.5's or it is a human's.
//
// The repair loop: when the hold names statements that say more than their
// source (widened, unsupported, joined) and fewer than MAX_REPAIRS repairs
// have been made, the item asks for a repair (needs_repair, repair_request)
// instead of ending here: the drafting model is sent the email and the
// checker's exact reasons, and code_approval_repair.js applies its rewrites.
// Otherwise the hold is final, and hold_reason lists every attempt's reasons.
//
// The verdicts of every attempt, the model and the usage go into the draft's
// claim_check either way, so a held draft shows the reviewer exactly which
// sentence failed each time, and the cost of a draft includes its repairs.
// ===========================================================================

// Node body

const gate = $('__GATE__').item.json;
const resp = $input.item.json;
const payload = JSON.parse(JSON.stringify(gate.payload || {}));
const target = draftsOf(payload)[gate.check_target];

if (!target) {
  throw new Error('Apply Claim Check: the Approval Gate item has no draft at check_target ' +
    JSON.stringify(gate.check_target));
}

const verdict = judgeCheck(resp, gate.approval);
const repairState = gate.repair && typeof gate.repair === 'object' ? gate.repair : null;
const prior = repairState && Array.isArray(repairState.history) ? repairState.history : [];
const attempt = {
  attempt: prior.length + 1,
  result: verdict.result,
  reasons: verdict.reasons,
  statements: verdict.statements,
  check_usage: verdict.usage,
  // The repair that produced this text, if any: its rewrites and its cost.
  repair_usage: repairState ? repairState.last_repair_usage || null : null,
  rewrites: repairState ? repairState.rewrites || [] : [],
};
const history = prior.concat([attempt]);
const composeUsage = repairState ? repairState.compose_usage || null : gate.usage || null;

const flagged = verdict.result === 'hold' ? flaggedSentences(verdict.statements) : [];
const fields = (gate.approval && gate.approval.repair_fields) || [];
const canRepair = flagged.length > 0 && prior.length < MAX_REPAIRS && fields.length > 0 &&
  !!gate.composition && typeof gate.composition === 'object';

target.claim_check = Object.assign({}, verdict, {
  repairs: prior.length,
  attempts: history,
  compose_usage: composeUsage,
});
if (verdict.result === 'pass') {
  target.status = 'approved';
  target.approved_by = 'auto';
  target.hold_reason = null;
} else {
  target.status = 'pending';
  target.approved_by = null;
  target.hold_reason = historyText(history);
}

return {
  json: Object.assign({}, gate, {
    payload: payload,
    check_request: null,
    check_result: verdict.result,
    check_reasons: verdict.reasons,
    check_usage: verdict.usage,
    needs_repair: canRepair,
    repair_request: canRepair ? repairRequest(gate.approval, flagged) : null,
    repair_state: canRepair ? {
      target: gate.check_target,
      history: history,
      flagged: flagged,
      composition: gate.composition,
      compose_usage: composeUsage,
    } : null,
  }),
};

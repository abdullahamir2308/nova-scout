// Attach Rationale — n8n Code node (Run Once for Each Item).
//
// Swaps the model's paragraph in for the deterministic one, keeping the score
// breakdown that Compute Fit Score already assembled. Nothing here changes the
// number: the score is decided before the model is ever called, so a bad or
// missing rationale can never move a lead up or down the queue.

const scored = $('Compute Fit Score').item.json;
const resp = $input.item.json;

// The HTTP node continues on error, so a failed call arrives without a
// `response` field. Unlike the ClinicalTrials.gov lookup, that is NOT a reason
// to drop the lead: the score is already complete and correct, and the
// deterministic rationale is a genuine fallback rather than a placeholder. An
// Ollama outage costs prose quality, not a scoring run.
const rawText = resp && typeof resp.response === 'string' ? resp.response : null;

let paragraph = null;
let source = 'deterministic';
let note = null;

if (rawText === null) {
  note = 'ollama call failed: ' + JSON.stringify(resp && resp.error ? resp.error : resp).slice(0, 200);
} else {
  let parsed = null;
  try {
    parsed = JSON.parse(rawText);
  } catch (e) {
    // Grammar-constrained output should always parse; fall back to a fenced
    // block rather than losing the paragraph.
    const m = rawText.match(/\{[\s\S]*\}/);
    if (m) {
      try {
        parsed = JSON.parse(m[0]);
      } catch (e2) {
        note = 'unparseable rationale JSON: ' + e2.message;
      }
    } else {
      note = 'no JSON object in rationale response';
    }
  }
  if (parsed && typeof parsed.rationale === 'string') {
    const t = parsed.rationale.replace(/\s+/g, ' ').trim();
    // A one-word or empty answer is a failed generation, not a rationale.
    // Falling back beats storing something that makes review slower.
    if (t.length >= 40) {
      paragraph = t;
      source = 'model';
    } else {
      note = 'rationale too short (' + t.length + ' chars), kept the deterministic one';
    }
  } else if (!note) {
    note = 'rationale field missing from model response';
  }
}

const payload = Object.assign({}, scored.payload);
if (paragraph) {
  payload.rationale = paragraph + '\n\n' + scored.breakdown_line;
}
payload.rationale_source = source;
if (note) payload.rationale_note = note;

return {
  json: {
    write: true,
    lead_id: scored.lead_id,
    domain: scored.domain,
    fit_score: scored.fit_score,
    payload: payload,
  },
};

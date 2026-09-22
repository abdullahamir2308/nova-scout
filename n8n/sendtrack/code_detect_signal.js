// Detect Positive Signal -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 6 reply notification: a deterministic, no-model check
// (build rule 3, same as Classify Inbound) of what the operator notification
// says about a reply Classify Inbound / Record Inbound has ALREADY labelled --
// it does not change that label, and Record Inbound's own SQL is untouched.
//
// Only a classification of 'reply' is scored. Classify Inbound's own
// precedence -- bounce > auto-reply > opt-out > reply -- has already spoken
// for the other three, and scoring a stray positive word inside one of them
// would contradict the decision already made: "Yes, please remove me." is an
// OPT-OUT on "remove" (code_classify_reply.js, unchanged) before this node
// ever runs, and the "Yes" in it is never read as a positive signal here.

const POSITIVE_PATTERNS = [
  [/\byes\b/, 'yes'],
  [/\byeah\b/, 'yeah'],
  [/\byep\b/, 'yep'],
  [/\bsure\b/, 'sure'],
  [/\binterested\b/, 'interested'],
  [/\bsounds good\b/, 'sounds good'],
  [/\bsounds great\b/, 'sounds great'],
  [/\bsend it\b/, 'send it'],
  [/\bsend (?:it |that )?over\b/, 'send over'],
  [/\bgo ahead\b/, 'go ahead'],
  [/\blet'?s (?:talk|chat|do it)\b/, "let's talk"],
  [/\bhappy to\b/, 'happy to'],
  [/\bworks for me\b/, 'works for me'],
  [/\bcount me in\b/, 'count me in'],
];

function str(v) {
  return v === null || v === undefined ? '' : String(v);
}

function positiveSignal(r) {
  if (r.classification !== 'reply') return { signal: 'neutral', signal_keyword: null };
  const t = str(r.body_excerpt).toLowerCase();
  for (let i = 0; i < POSITIVE_PATTERNS.length; i++) {
    const pair = POSITIVE_PATTERNS[i];
    if (pair[0].test(t)) return { signal: 'positive', signal_keyword: pair[1] };
  }
  return { signal: 'neutral', signal_keyword: null };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const r = $input.item.json;
return { json: Object.assign({}, r, positiveSignal(r)) };

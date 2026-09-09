// Assemble Drafts -- n8n Code node (Run Once for Each Item).
//
// Turns one model response into the two draft rows Section 9 asks for ("email
// variant + LinkedIn variant per lead into `drafts`"), and enforces every
// message rule that a string match can enforce.
//
// Master Ref build rule 3 -- never spend tokens on deterministic work. So the
// model writes prose and nothing else: the opt-out sentence, the signature, the
// greeting and every constraint check are all handled here. That is not just
// economy. Section 5 locks the opt-out sentence VERBATIM and makes it the whole
// basis for GDPR/KVKK legitimate interest -- asking a 9B model to reproduce a
// compliance string exactly, every time, is a bet with no upside.
//
// Constraint violations do not discard a draft. They tag it, and the reviewer
// sees the tag in the queue next to the text. A silently-dropped draft teaches
// nobody anything; a draft marked `named+long` is a visible, fixable defect.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses each out of NovaScout_MasterRef.md
// and refuses to build on drift.
// ---------------------------------------------------------------------------

// Section 5, "Message rules -- LOCKED". Verbatim, including the straight
// apostrophes -- this string is the opt-out mechanism, not a paraphrase of it.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

// Section 9, Workflow 4: "under 80 words".
const MAX_WORDS = 80;

// Section 5: "Maximum one plain URL. No buttons."
const MAX_URLS = 1;

// Section 9, Workflow 4: "no adjectives like 'revolutionary' or 'cutting-edge'".
// The doc names two; the rest are the same register and fail the same way.
const BANNED_ADJECTIVES = [
  'revolutionary',
  'cutting-edge',
  'cutting edge',
  'game-changing',
  'game changing',
  'best-in-class',
  'world-class',
  'state-of-the-art',
  'seamless',
  'innovative',
  'leverage',
  'synergy',
  'unlock',
  'supercharge',
];

// Section 9, Workflow 4: "mention the live NoblePath demo".
const DEMO_REFERENT = 'noblepath';

// Section 9, Workflow 4: "no merge-tag phrasing tells". Catches the literal
// tags a template would leave behind ([First Name], {{company}}, <domain>) and
// nothing else -- a bracketed clinical abbreviation in real prose is rare, and
// a false positive here costs a review flag, not a dropped draft.
const MERGE_TAG = /(\{\{[^}]*\}\})|(\[[A-Za-z][A-Za-z _-]{1,30}\])|(<[A-Za-z][A-Za-z _-]{1,30}>)/;

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function words(v) {
  const t = str(v);
  return t ? t.split(/\s+/).filter(Boolean).length : 0;
}

// A model that ignores the "no greeting, no sign-off" instruction produces a
// duplicate greeting once we prepend our own, and a sign-off that collides with
// the signature. Both are deterministic to strip, so strip them rather than
// flagging the reviewer with something a regex can fix.
const SIGNOFFS = [
  'best regards', 'kind regards', 'warm regards', 'best wishes', 'regards',
  'best', 'thanks', 'thank you', 'cheers', 'sincerely', 'yours sincerely',
];

function stripGreeting(body, greeting) {
  let out = str(body);
  const g = str(greeting).replace(/[,\s]+$/, '');
  // Any leading "Hi X," / "Hello," / "Dear X," line, ours or the model's.
  //
  // The trailing comma or colon is REQUIRED, not optional. Optional, this ate up
  // to 40 characters of any body that merely started with the word "Hi" or
  // "Hello" -- silently deleting real content is a worse failure than leaving a
  // duplicate greeting, which a reviewer can see and delete.
  out = out.replace(/^\s*(hi|hello|hey|dear|greetings)\b[^\n,]{0,40}[,:]\s*\n?/i, '');
  if (g && out.toLowerCase().indexOf(g.toLowerCase()) === 0) {
    out = out.slice(g.length).replace(/^[,\s]+/, '');
  }
  return out.trim();
}

function stripSignoff(body) {
  const lines = str(body).split('\n');
  while (lines.length) {
    const last = lines[lines.length - 1].trim().toLowerCase().replace(/[,.!]+$/, '');
    if (!last) { lines.pop(); continue; }
    // A trailing bare name line (the model signing off as someone) goes too.
    if (SIGNOFFS.indexOf(last) !== -1 || (lines.length > 1 && /^[a-z]{2,20}$/.test(last))) {
      lines.pop();
      continue;
    }
    break;
  }
  return lines.join('\n').trim();
}

function countUrls(v) {
  const m = str(v).match(URL_RE);
  return m ? m.length : 0;
}

function bannedAdjectivesIn(v) {
  const t = ' ' + str(v).toLowerCase() + ' ';
  return BANNED_ADJECTIVES.filter(function (a) { return t.indexOf(a) !== -1; });
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const src = $('Assess Grounding').item.json;
const resp = $input.item.json;

const base = {
  lead_id: src.lead_id,
  domain: src.domain,
  company_name: src.company_name,
  fit_score: src.fit_score,
};

// --- Did Ollama answer with the schema we asked for? ------------------------
//
// The HTTP node continues on error, so a refusal arrives as a normal item.
// Unlike the ClinicalTrials.gov lookup this is NOT droppable-and-retryable in
// the same breath: a model outage should not mark a lead low-context either.
// Drop it, leave it at 'contact_found', let the next run redraft.
let parsed = null;
if (resp && typeof resp === 'object' && !resp.error && typeof resp.response === 'string') {
  try {
    parsed = JSON.parse(resp.response);
  } catch (e) {
    parsed = null;
  }
}

if (!parsed || typeof parsed !== 'object') {
  const err = resp && (resp.error || resp.message);
  const detail = err
    ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300)
    : 'response was not parseable JSON';
  return {
    json: Object.assign({}, base, {
      write: false,
      skip_reason: 'Ollama drafting call did not answer usably: ' + detail,
    }),
  };
}

const emailSubject = str(parsed.email_subject);
let emailBody = stripSignoff(stripGreeting(parsed.email_body, src.email_greeting));
let linkedinBody = stripSignoff(stripGreeting(parsed.linkedin_body, src.linkedin_greeting));

// Section 5: the signature is name / one line of title / phone. Whatever of
// that is configured at build time is appended verbatim; nothing is invented to
// fill a gap. An unset phone leaves the signature short and the build prints a
// warning -- it does not put a plausible-looking number in a cold email.
const SIGNATURE = __SIGNATURE__;

// --- Compose the sendable text ---------------------------------------------
//
// Email: greeting / body / opt-out / signature. The opt-out is inside the
// counted message because it is text the recipient reads; the signature is not,
// because Section 5 lists it as a separate rule from the 80-word ceiling.
const emailMessage = [src.email_greeting, '', emailBody, '', OPT_OUT].join('\n');
const emailFull = SIGNATURE ? emailMessage + '\n\n' + SIGNATURE : emailMessage;

// LinkedIn: greeting / body. No opt-out sentence and no signature -- Section 5's
// message rules govern the mailbox, and Section 6 makes this a DM a human reads
// and sends by hand from their own account. A signature block on a LinkedIn DM
// from your own profile reads as automation, which is the one thing Section 6
// exists to prevent.
const linkedinFull = [src.linkedin_greeting, '', linkedinBody].join('\n');

// --- Deterministic constraint checks ---------------------------------------

function checkEmail() {
  const flags = [];
  if (!emailBody) flags.push('empty');
  if (words(emailMessage) > MAX_WORDS) flags.push('long');
  if (bannedAdjectivesIn(emailFull).length) flags.push('adjective');
  if (MERGE_TAG.test(emailFull)) flags.push('merge-tag');
  if (countUrls(emailFull) > MAX_URLS) flags.push('urls');
  if (emailFull.toLowerCase().indexOf(DEMO_REFERENT) === -1) flags.push('no-demo');
  if (!emailSubject || words(emailSubject) > 8) flags.push('subject');
  return flags;
}

function checkLinkedin() {
  const flags = [];
  if (!linkedinBody) flags.push('empty');
  if (words(linkedinFull) > MAX_WORDS) flags.push('long');
  if (bannedAdjectivesIn(linkedinFull).length) flags.push('adjective');
  if (MERGE_TAG.test(linkedinFull)) flags.push('merge-tag');
  if (countUrls(linkedinFull) > MAX_URLS) flags.push('urls');
  return flags;
}

function variantTag(addressing, flags) {
  return flags.length ? addressing + '+' + flags.join(',') : addressing;
}

const emailFlags = checkEmail();
const linkedinFlags = checkLinkedin();

return {
  json: Object.assign({}, base, {
    write: true,
    low_context: false,
    fact_count: src.fact_count,
    fact_kinds: src.fact_kinds,
    email_flags: emailFlags,
    linkedin_flags: linkedinFlags,
    email_words: words(emailMessage),
    linkedin_words: words(linkedinFull),
    payload: {
      lead_id: src.lead_id,
      advance: true,
      drafts: [
        {
          channel: 'email',
          variant: variantTag(src.email_addressing, emailFlags),
          subject: emailSubject,
          body: emailFull,
        },
        {
          channel: 'linkedin',
          variant: variantTag(src.linkedin_addressing, linkedinFlags),
          subject: null,
          body: linkedinFull,
        },
      ],
    },
  }),
};

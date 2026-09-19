// Assemble Drafts -- n8n Code node (Run Once for Each Item).
//
// Turns one model response into the two draft rows Section 9 asks for ("email
// variant + LinkedIn variant per lead into `drafts`"), and enforces every
// message rule that a string match can enforce.
//
// Drafting skill v2 (NovaScout_DraftingSkill.md, section 2) fixes the shape of a
// first touch -- hook, problem, outcome, proof, ask -- and "only the hook and
// subject are freely generated". So the model answers with a subject and two
// hooks and nothing else, and this node builds the rest out of the approved
// claims library that Assess Grounding resolved for this lead: one problem, one
// outcome, one geography-matched proof, EXACTLY ONE ask, each inserted verbatim.
// The model never saw those lines and has no output field to write them in; it
// cannot compose them, paraphrase them, or join a prospect fact onto them.
//
// Master Ref build rule 3 -- never spend tokens on deterministic work. The
// opt-out sentence, the signature, the greeting, the choice of library line and
// every constraint check are all handled here. That is not just economy.
// Section 5 locks the opt-out sentence VERBATIM and makes it the whole basis for
// GDPR/KVKK legitimate interest -- asking a 9B model to reproduce a compliance
// string exactly, every time, is a bet with no upside.
//
// Constraint violations do not discard a draft. They tag it, and the reviewer
// sees the tag in the queue next to the text. A silently-dropped draft teaches
// nobody anything; a draft marked `named/...+long` is a visible, fixable defect.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses each out of NovaScout_MasterRef.md
// or NovaScout_DraftingSkill.md and refuses to build on drift.
// ---------------------------------------------------------------------------

// Section 5, "Message rules -- LOCKED". Verbatim, including the straight
// apostrophes -- this string is the opt-out mechanism, not a paraphrase of it.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

// Section 9, Workflow 4: "under 80 words". Skill section 2 says what it
// measures: "Body (1-5) stays under 80 words" -- hook, problem, outcome, proof
// and ask. The greeting, the opt-out and the signature are the fixed frame
// around the body, and are not counted.
const MAX_WORDS = 80;

// Section 5: "Maximum one plain URL. No buttons."
const MAX_URLS = 1;

// Skill section 3, Links: "Warm-up weeks 1-2: zero links." From week 3 the one
// URL Section 5 allows may appear -- and only the library's link line.
const LINK_FREE_WEEKS = 2;

// Skill section 3, Subject: "30-50 characters", no "Re:" or "Fwd:", and never
// "free," "demo," "offer," "opportunity."
const SUBJECT_MIN_CHARS = 30;
const SUBJECT_MAX_CHARS = 50;
const SUBJECT_REPLY_PREFIXES = ['re', 'fwd'];
const SUBJECT_BANNED_WORDS = ['free', 'demo', 'offer', 'opportunity'];

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

// Section 9, Workflow 4: "no merge-tag phrasing tells". Catches the literal
// tags a template would leave behind ([First Name], {{company}}, <domain>) and
// nothing else -- a bracketed clinical abbreviation in real prose is rare, and
// a false positive here costs a review flag, not a dropped draft.
const MERGE_TAG = /(\{\{[^}]*\}\})|(\[[A-Za-z][A-Za-z _-]{1,30}\])|(<[A-Za-z][A-Za-z _-]{1,30}>)/;

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;

// What a hook must not contain. The hook is one prospect fact, stated; skill
// section 3 puts the product in the outcome line, the ask in the ask line, and
// a question only in the problem line. Anything of that kind in a hook is the
// model composing a part it was told is added for it.
const HOOK_PITCH = /\bnova\b|\bassistants?\b|\bchat ?bots?\b|\bAI\b/i;
const HOOK_ASK = /\b(reply|call|chat|meeting|demo|schedule|calendar|book|interested|recording|let me know)\b/i;

// Skill section 3: the hook is "About them ('You're sponsoring...'), not about
// the source ('ClinicalTrials.gov lists...')". The trial fact reaches the model
// phrased from the source, and a 9B model copies it verbatim if let.
const HOOK_SOURCE_OPENING = /^\s*(clinicaltrials\.gov|according to)\b/i;

// The other way to get the source wrong: a trial credited to their website.
// Measured on a real lead -- "Your site lists work on the Efficacy of INM004
// ..." -- when the model was nudged to vary its wording and borrowed the
// therapeutic-area sentence's frame. The trial is on ClinicalTrials.gov; their
// site says nothing about it. Every word traced to the record; the sentence was
// still untrue -- the Section 9 "joining" failure, one level up.
const SITE_CLAIM = /\b(your|their|the)\s+(web\s?)?site\b/i;
const TRIAL_WORD = /\b(trials?|stud(y|ies))\b/i;

// Acronyms a subject may carry in capitals without shouting.
const SUBJECT_ACRONYMS = ['CRO', 'CROS'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// A word is a token with a letter or a digit in it. A dash between clauses is
// punctuation, not a word -- counting "—" would put the skill's own 79-word
// example over the ceiling.
function words(v) {
  const t = str(v);
  return t ? t.split(/\s+/).filter(function (w) { return /[\p{L}\p{N}]/u.test(w); }).length : 0;
}

function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
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

// A hook is one sentence of one paragraph; a line break inside it is the model
// laying out an email it was not asked to write.
function cleanHook(raw, greeting) {
  return stripSignoff(stripGreeting(raw, greeting)).replace(/\s*\n+\s*/g, ' ').trim();
}

function urlsIn(v) {
  return str(v).match(URL_RE) || [];
}

function bannedAdjectivesIn(v) {
  const t = ' ' + str(v).toLowerCase() + ' ';
  return BANNED_ADJECTIVES.filter(function (a) { return t.indexOf(a) !== -1; });
}

// Trial titles are quoted from ClinicalTrials.gov and can hold anything -- a
// question mark, a digit, the word "call". The hook checks judge what the model
// wrote around a title, not the title it was given.
function withoutTitles(text, titles) {
  let out = str(text);
  for (const t of titles || []) {
    const title = str(t);
    if (!title) continue;
    let at = out.toLowerCase().indexOf(title.toLowerCase());
    while (at !== -1) {
      out = out.slice(0, at) + ' ' + out.slice(at + title.length);
      at = out.toLowerCase().indexOf(title.toLowerCase());
    }
  }
  return out;
}

// Grounding, as far as a string match reaches. A token carrying a digit -- a
// trial code, a count, a headcount, "11pm", "48-hour" -- must appear in the
// facts this lead actually has; and a therapeutic area the lead does not have
// must not be named. Those are exactly the facts the worked examples in the
// system prompt carry (INM004, 40, oncology), so they are exactly what a 9B
// model copies from an example into the wrong company's email.
function digitTokens(v) {
  return (str(v).match(/[\p{L}\p{N}]*\p{N}[\p{L}\p{N}]*/gu) || []).map(function (t) { return t.toLowerCase(); });
}

function ungrounded(text, corpus, absentAreas) {
  const have = {};
  digitTokens(corpus).forEach(function (t) { have[t] = true; });
  const bad = digitTokens(text).filter(function (t) { return !have[t]; });
  const plain = ' ' + fold(text).replace(/[^a-z0-9]+/g, ' ') + ' ';
  for (const a of absentAreas || []) {
    const key = ' ' + fold(a).replace(/[^a-z0-9]+/g, ' ') + ' ';
    if (plain.indexOf(key) !== -1) bad.push(a);
  }
  return bad;
}

function sentences(v) {
  const m = str(v).match(/[.!?](\s|$)/g);
  return m ? m.length : (str(v) ? 1 : 0);
}

// Does the text open on this name (or its first word)? Plain string compare:
// a name or a city is data, not a pattern.
function startsWithName(text, name) {
  const t = fold(text);
  const n = fold(name);
  if (!n) return false;
  const first = n.split(/\s+/)[0];
  const opensOn = function (w) {
    return t.indexOf(w) === 0 && (t.length === w.length || !/[a-z0-9]/.test(t.charAt(w.length)));
  };
  return opensOn(n) || (first.length >= 3 && opensOn(first));
}

// ---------------------------------------------------------------------------
// Choosing the library lines
// ---------------------------------------------------------------------------

function rotate(list, seed) {
  const n = list.length;
  return list.map(function (_, i) { return list[(seed + i) % n]; });
}

// One line per slot, rotated on lead_id so a batch does not open every second
// sentence on the same question -- the merge-tag tell produced without a merge
// tag -- while staying deterministic per lead. Walks the combinations in that
// rotated order and takes the first whose body fits MAX_WORDS with this hook;
// if none fits, the shortest, which the length check then tags `long`.
function chooseLines(pools, seed, hookWords, extraWords) {
  const P = rotate(pools.problem, seed);
  const O = rotate(pools.outcome, seed);
  const A = rotate(pools.ask, seed);
  const proof = rotate(pools.proof, seed)[0];
  let shortest = null;
  for (let i = 0; i < P.length; i++) {
    for (let j = 0; j < O.length; j++) {
      for (let k = 0; k < A.length; k++) {
        const pick = { problem: P[i], outcome: O[j], proof: proof, ask: A[k] };
        const total = hookWords + extraWords +
          words(P[i].body) + words(O[j].body) + words(proof.body) + words(A[k].body);
        if (total <= MAX_WORDS) return pick;
        if (!shortest || total < shortest.total) shortest = { pick: pick, total: total };
      }
    }
  }
  return shortest.pick;
}

// Skill section 5's layout: the hook and the question it raises, then what we
// built and where it runs, then the one ask.
function composeBody(hook, pick, link) {
  return [
    [hook, pick.problem.body].filter(Boolean).join(' '),
    [pick.outcome.body, pick.proof.body].concat(link ? [link.body] : []).join(' '),
    pick.ask.body,
  ].join('\n\n');
}

function codesOf(pick, link) {
  return [pick.problem.code, pick.outcome.code, pick.proof.code, pick.ask.code]
    .concat(link ? [link.code] : [])
    .join('.');
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

const emailSubject = str(parsed.email_subject).replace(/\s+/g, ' ');
const emailHook = cleanHook(parsed.email_hook, src.email_greeting);
const linkedinHook = cleanHook(parsed.linkedin_hook, src.linkedin_greeting);

// Section 5: the signature is name / one line of title / phone. Whatever of
// that is configured at build time is appended verbatim; nothing is invented to
// fill a gap. An unset phone leaves the signature short and the build prints a
// warning -- it does not put a plausible-looking number in a cold email.
const SIGNATURE = __SIGNATURE__;

// --- Pick the library lines -------------------------------------------------

const pools = src.library_pools;
const seed = Math.abs(parseInt(src.lead_id, 10) || 0);
const week = Number(src.warmup_week) || 1;

// Skill section 3: zero links in warm-up weeks 1-2; after that at most one, and
// it is the library's link line. The LinkedIn DM never carries it -- a human
// sends that from their own profile (Section 6).
const linkLines = week > LINK_FREE_WEEKS ? (src.library_link || []) : [];
const link = linkLines.length ? rotate(linkLines, seed)[0] : null;

const emailPick = chooseLines(pools, seed, words(emailHook), link ? words(link.body) : 0);
// The DM rotates one step further, so where the library has a choice it asks
// its question in different words from the email's.
const linkedinPick = chooseLines(pools, seed + 1, words(linkedinHook), 0);

const emailBody = composeBody(emailHook, emailPick, link);
const linkedinBody = composeBody(linkedinHook, linkedinPick, null);

// --- Compose the sendable text ---------------------------------------------
//
// Email: greeting / body / opt-out / signature. The opt-out-then-signature
// tail is exactly what Workflow 6 checks for before it will send.
const emailMessage = [src.email_greeting, '', emailBody, '', OPT_OUT].join('\n');
const emailFull = SIGNATURE ? emailMessage + '\n\n' + SIGNATURE : emailMessage;

// LinkedIn: greeting / body. No opt-out sentence and no signature -- Section 5's
// message rules govern the mailbox, and Section 6 makes this a DM a human reads
// and sends by hand from their own account. A signature block on a LinkedIn DM
// from your own profile reads as automation, which is the one thing Section 6
// exists to prevent.
const linkedinFull = [src.linkedin_greeting, '', linkedinBody].join('\n');

// --- Deterministic constraint checks ---------------------------------------

const factCorpus = str(src.fact_sheet);
const titles = src.trial_titles || [];

function hookFlags(hook) {
  const flags = [];
  const own = withoutTitles(hook, titles);
  if (own.indexOf('?') !== -1) flags.push('hook-question');
  if (HOOK_ASK.test(own)) flags.push('hook-ask');
  if (HOOK_PITCH.test(own)) flags.push('hook-pitch');
  if (ungrounded(own, factCorpus, src.absent_areas).length) flags.push('hook-ungrounded');
  // Section 9 Workflow 4: founder_name and city never open a message.
  if (startsWithName(hook, src.founder_name) || startsWithName(hook, src.city)) flags.push('hook-opener');
  // "One prospect fact, two at most" -- and a second trial title is a second
  // fact in the same sentence, plus twenty words the budget does not have.
  const titlesNamed = titles.filter(function (t) {
    return str(t) && hook.toLowerCase().indexOf(str(t).toLowerCase()) !== -1;
  }).length;
  const trialOnSite = SITE_CLAIM.test(own) && (titlesNamed > 0 || TRIAL_WORD.test(own));
  if (HOOK_SOURCE_OPENING.test(hook) || trialOnSite) flags.push('hook-source');
  if (sentences(own) > 2 || titlesNamed > 1) flags.push('hook-long');
  return flags;
}

function subjectFlags(subject) {
  const flags = [];
  const chars = Array.from(subject).length;
  if (!subject || chars < SUBJECT_MIN_CHARS || chars > SUBJECT_MAX_CHARS) flags.push('subject');
  if (/\bnova\b/i.test(subject)) flags.push('subject-product');
  const prefix = new RegExp('\\b(' + SUBJECT_REPLY_PREFIXES.join('|') + ')\\s*:', 'i');
  if (prefix.test(subject)) flags.push('subject-reply');
  const banned = new RegExp('\\b(' + SUBJECT_BANNED_WORDS.join('|') + ')s?\\b', 'i');
  if (banned.test(subject) || /\bopportunities\b/i.test(subject)) flags.push('subject-word');
  const corpusWords = {};
  (factCorpus.match(/[\p{L}\p{N}]+/gu) || []).forEach(function (t) { corpusWords[t] = true; });
  const shouting = (subject.match(/[\p{L}\p{N}]+/gu) || []).filter(function (t) {
    return /^\p{Lu}{2,}$/u.test(t) && !corpusWords[t] && SUBJECT_ACRONYMS.indexOf(t.toUpperCase()) === -1;
  });
  if (shouting.length) flags.push('subject-caps');
  const corpus = factCorpus + (src.headcount ? ' ' + src.headcount : '');
  if (ungrounded(withoutTitles(subject, titles), corpus, src.absent_areas).length) {
    flags.push('subject-ungrounded');
  }
  return flags;
}

function linkFlags(text) {
  const flags = [];
  const urls = urlsIn(text);
  if (urls.length > MAX_URLS) flags.push('urls');
  if (urls.length && week <= LINK_FREE_WEEKS) flags.push('link-in-warmup');
  if (week > LINK_FREE_WEEKS) {
    const approved = link ? urlsIn(link.body) : [];
    if (urls.some(function (u) { return approved.indexOf(u) === -1; })) flags.push('unapproved-link');
  }
  if (urls.some(function (u) { return /linkedin\.com/i.test(u); })) flags.push('linkedin-link');
  if (urls.some(function (u) { return /\.pdf\b/i.test(u); })) flags.push('pdf-link');
  return flags;
}

function unconfirmed(pick, extra) {
  const used = [pick.problem, pick.outcome, pick.proof, pick.ask].concat(extra ? [extra] : []);
  return used.some(function (l) { return !l.confirmed; });
}

function checkEmail() {
  let flags = [];
  if (!emailHook) flags.push('empty');
  if (words(emailBody) > MAX_WORDS) flags.push('long');
  if (bannedAdjectivesIn(emailFull + '\n' + emailSubject).length) flags.push('adjective');
  if (MERGE_TAG.test(emailFull) || MERGE_TAG.test(emailSubject)) flags.push('merge-tag');
  flags = flags.concat(linkFlags(emailFull + '\n' + emailSubject));
  flags = flags.concat(subjectFlags(emailSubject));
  if (emailHook) flags = flags.concat(hookFlags(emailHook));
  if (unconfirmed(emailPick, link)) flags.push('unconfirmed-claim');
  return flags;
}

// The DM is sent by a human from their own account (Section 6), so the subject
// and link rules, which govern the mailbox, do not apply to it.
function checkLinkedin() {
  let flags = [];
  if (!linkedinHook) flags.push('empty');
  if (words(linkedinBody) > MAX_WORDS) flags.push('long');
  if (bannedAdjectivesIn(linkedinFull).length) flags.push('adjective');
  if (MERGE_TAG.test(linkedinFull)) flags.push('merge-tag');
  if (urlsIn(linkedinFull).length > MAX_URLS) flags.push('urls');
  if (linkedinHook) flags = flags.concat(hookFlags(linkedinHook));
  if (unconfirmed(linkedinPick, null)) flags.push('unconfirmed-claim');
  return flags;
}

// variant = addressing / the library codes used + flags. The codes are what
// the learning loop needs to tell which problem, outcome, proof and ask earned
// a reply (Section 9 Workflow 6: draft_id records which variant went out).
function variantTag(addressing, codes, flags) {
  const v = addressing + '/' + codes;
  return flags.length ? v + '+' + flags.join(',') : v;
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
    email_words: words(emailBody),
    linkedin_words: words(linkedinBody),
    email_lines: codesOf(emailPick, link),
    linkedin_lines: codesOf(linkedinPick, null),
    payload: {
      lead_id: src.lead_id,
      advance: true,
      drafts: [
        {
          channel: 'email',
          variant: variantTag(src.email_addressing, codesOf(emailPick, link), emailFlags),
          subject: emailSubject,
          body: emailFull,
        },
        {
          channel: 'linkedin',
          variant: variantTag(src.linkedin_addressing, codesOf(linkedinPick, null), linkedinFlags),
          subject: null,
          body: linkedinFull,
        },
      ],
    },
  }),
};

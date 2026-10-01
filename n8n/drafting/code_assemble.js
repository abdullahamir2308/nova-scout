// Assemble Drafts -- n8n Code node (Run Once for Each Item).
//
// Turns one Claude response into the two draft rows Section 9 asks for ("email
// variant + LinkedIn variant per lead into `drafts`"), and enforces every rule
// of NovaScout_DraftingSkill.md v3, section 3, that a string match can enforce.
//
// Skill v3 moved from verbatim line-picking to composition: Claude Sonnet 5.5
// writes the whole email -- subject, body and one ask -- from one prospect fact
// plus the approved claims Assess Grounding handed it. That makes this node the
// only thing standing between the model's prose and the review queue, so every
// rule the skill states as a rule is checked here, deterministically (build
// rule 3), on what actually came back:
//
//   length        body 70-110 words is the target; above 125 is tagged `long`
//   ask           exactly one: one question in the ask, no request in the body
//   links         none in warm-up weeks 1-2, then only the library's link line
//   product name  at most once, in brackets, never in the subject
//   "AI"          at most once, never in the subject, never "AI-powered"
//   sponsor       the prospect is never the one sponsoring ("You're sponsoring")
//   claims        no guarantee, no number the library and facts do not hold, no
//                 visitor identification, no named CRM or tool, no languages,
//                 never "chatbot"
//   proof         the geography-matched deployment is named; no other one is
//   grounding     no therapeutic area the lead lacks; no founder or city opener;
//                 a trial is never credited to their website
//
// The opt-out sentence, the signature and the greeting are appended HERE, not
// generated. Section 5 locks the opt-out verbatim and makes it the whole basis
// for GDPR/KVKK legitimate interest.
//
// A violation tags the draft's `variant` rather than discarding it. The reviewer
// sees the tag next to the text; a silently dropped draft teaches nobody
// anything. Every draft still needs a human approval before Workflow 6 sends it.

// ---------------------------------------------------------------------------
// Locked constants. build_workflow.py parses each out of NovaScout_MasterRef.md
// or NovaScout_DraftingSkill.md and refuses to build on drift.
// ---------------------------------------------------------------------------

// Section 5, "Message rules -- LOCKED". Verbatim, including the straight
// apostrophes -- this string is the opt-out mechanism, not a paraphrase of it.
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";

// Skill v3 section 3, Length: "The body (everything above the opt-out line)
// should be 70-110 words and never more than 125." Counted over the body and
// the ask -- the greeting is the fixed frame, as in v2. Only the ceiling tags;
// "shorter is fine if nothing useful is lost".
const BODY_TARGET_MIN = 70;
const BODY_TARGET_MAX = 110;
const BODY_CEILING = 125;

// Section 5: "Maximum one plain URL. No buttons."
const MAX_URLS = 1;

// Master Ref Section 9, Workflow 4, Links: none in warm-up weeks 1-2. From week
// 3 the one URL Section 5 allows may appear -- and only the library's link
// line, appended here, never written by the model.
const LINK_FREE_WEEKS = 2;

// Skill v3 section 3, Subject: "30-55 characters ... Never the product name.
// Never 'AI'. No 'Re:', no 'Fwd:', no capitals."
const SUBJECT_MIN_CHARS = 30;
const SUBJECT_MAX_CHARS = 55;
const SUBJECT_REPLY_PREFIXES = ['re', 'fwd'];

// Skill v3 section 3, Naming the product: "The name appears at most once, in
// brackets". "The word 'AI' appears at most once".
const PRODUCT_NAME = 'Nova';
const PRODUCT_NAME_MAX = 1;
const AI_MAX = 1;

// Skill v3 section 3, Everywhere: "No banned adjectives (revolutionary,
// cutting-edge, innovative, game-changing, seamless)." The rest are the same
// register and fail the same way.
const BANNED_ADJECTIVES = [
  'revolutionary',
  'cutting-edge',
  'cutting edge',
  'innovative',
  'game-changing',
  'game changing',
  'seamless',
  'best-in-class',
  'world-class',
  'state-of-the-art',
  'leverage',
  'synergy',
  'unlock',
  'supercharge',
];

// The live deployments a proof line can name. Which of them this lead may hear
// about is decided by its proof pool (Assess Grounding, by country).
const DEPLOYMENTS = ['NoblePath', 'Vertex'];

// Skill v3 section 3, Claims, forbidden: "naming a specific CRM, tool or
// integration". Generic words ("your CRM", "a spreadsheet") are fine. Only
// names that are not also ordinary English words are listed, so a false
// positive costs a review tag, not a wrong one.
const NAMED_TOOLS = [
  'hubspot', 'salesforce', 'pipedrive', 'zoho', 'microsoft dynamics', 'dynamics 365', 'monday.com',
  'freshsales', 'freshworks', 'zendesk', 'google sheets', 'google sheet', 'airtable', 'slack',
  'microsoft teams', 'calendly', 'gmail', 'whatsapp', 'veeva', 'intercom', 'resend', 'mailchimp',
];

// Section 9, Workflow 4: "no merge-tag phrasing tells". The literal tags a
// template would leave behind ([First Name], {{company}}, <domain>).
const MERGE_TAG = /(\{\{[^}]*\}\})|(\[[A-Za-z][A-Za-z _-]{1,30}\])|(<[A-Za-z][A-Za-z _-]{1,30}>)/;

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;

// Skill v3 section 3, Hook: "Never describe the prospect as sponsoring anything
// ('You're sponsoring...' is banned)." Every way the v2 drafts and fact sheet
// put it.
const PROSPECT_SPONSOR = [
  /\byou(?:['’]re| are)\s+(?:also\s+|currently\s+|now\s+|actively\s+)?sponsor(?:ing|s)?\b/i,
  /\byou(?:['’]re| are)\s+(?:the|a|an)\s+(?:\w+\s+)?sponsor\b/i,
  /\byou\s+sponsor(?:ed|s)?\b/i,
  /\byou as (?:the |a )?sponsor\b/i,
  /\bsponsored by you\b/i,
  /\byour (?:own )?(?:sponsored|sponsorship)\b/i,
];

// Skill v3 section 3, Claims, forbidden. Each is a pattern for one rule.
const GUARANTEE = /\bguarantee\w*|\bnever (?:again )?(?:lose|miss)\b|\bno (?:lead|inquiry|inquiries|sponsor|sponsors) (?:is |are |gets |get |will be )?(?:ever )?(?:lost|missed)\b|\b100 ?%|\bwill (?:win|double|triple|increase|grow|boost)\b|\byou(?:['’]ll| will) (?:never|always|win|get more)\b|\b(?:ensures?|makes? sure) (?:you|that you) never\b/i;
const VISITOR_ID = /\b(?:identif\w*|reveal\w*|unmask\w*|de-?anonymi\w*|track\w*)\b[^.?!]{0,60}\b(?:visit\w*|visitors?|anonymous|traffic|browse\w*)\b|\banonymous (?:visitors?|traffic)\b|\b(?:see|know|tells? you|shows? you)\s+(?:exactly\s+)?(?:who|which \w+)\s+(?:visit\w*|browse\w*|land\w* on|came to)\b/i;
const LANGUAGES = /\bmulti-?lingual\b|\blanguages?\b|\b(?:turkish|spanish|arabic|german|french|portuguese|english|polish|romanian|hungarian|czech|hindi|urdu)\b/i;
const CHATBOT = /\bchat ?bots?\b|\bQ ?& ?A bots?\b/i;
const AI_POWERED = /\bAI[- ]?(?:powered|driven|based)\b/i;

// Skill v3 section 3, Claims: "any number, percentage or multiplier that is not
// in section 4". Digit tokens are checked against the library and the facts;
// these are the spelled-out numbers and multipliers a model reaches for. "one"
// is not here -- "one word back", "one question" are the skill's own words.
const NUMBER_WORDS = [
  'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'twenty',
  'thirty', 'fifty', 'hundred', 'hundreds', 'thousand', 'thousands', 'million', 'millions', 'billion',
  'dozen', 'dozens', 'double', 'doubles', 'doubled', 'triple', 'triples', 'tripled', 'twice', 'half',
  'percent', 'tenfold',
];
// A digit in the corpus licenses its word: the fact sheet says "2 recruiting
// trials", the model may write "two recruiting trials".
const DIGIT_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
  'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'];

// Skill v3 section 3, Ask: "Exactly one ask. The reply costs one word. No
// links, no scheduling link, no '30-minute call'." The ask is its own field in
// the model's schema, so exactly one ask means: one question there, and no
// request anywhere in the body.
const REQUEST_IN_BODY = /\b(?:would you|could you|are you open|worth a (?:look|chat|call|try|conversation)|let me know|reply (?:yes|no|with|to)|one word back|shall i|should i|want me to|happy to (?:set|send|show|share|walk)|interested in (?:a|seeing|trying|hearing)|open to a|up for a|free for a)\b/i;
const ASK_SCHEDULING = /\b\d+[- ]?min(?:ute)?s?\b|\b(?:fifteen|twenty|thirty)[- ]minutes?\b|\bcalendly\b|\bschedul\w*|\bbook (?:a |some )?(?:time|slot|call|meeting)\b|\b(?:time|slot)s? (?:that )?works?\b/i;

// Skill v3 section 3, Hook: "Never open on the founder's name or the city."
// And the trial fact is on ClinicalTrials.gov, not on their site.
const HOOK_SOURCE_OPENING = /^\s*(clinicaltrials\.gov|according to)\b/i;
const SITE_CLAIM = /\b(your|their|the)\s+(web\s?)?site\b/i;
const TRIAL_WORD = /\b(trials?|stud(y|ies))\b/i;

// Acronyms a subject may carry in capitals.
const SUBJECT_ACRONYMS = ['CRO', 'CROS', 'BD'];

// The order claim codes are recorded in `variant`.
const SLOT_ORDER = ['description', 'angle', 'benefit', 'proof', 'ask', 'link'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

// A word is a token with a letter or a digit in it. A dash between clauses is
// punctuation, not a word.
function words(v) {
  const t = str(v);
  return t ? t.split(/\s+/).filter(function (w) { return /[\p{L}\p{N}]/u.test(w); }).length : 0;
}

function fold(v) {
  return str(v).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// A model that ignores "no greeting, no sign-off" produces a duplicate greeting
// once we prepend our own, and a sign-off that collides with the signature.
// Both are deterministic to strip.
const SIGNOFFS = [
  'best regards', 'kind regards', 'warm regards', 'best wishes', 'regards',
  'best', 'thanks', 'thank you', 'cheers', 'sincerely', 'yours sincerely',
];

function stripGreeting(body, greeting) {
  let out = str(body);
  const g = str(greeting).replace(/[,\s]+$/, '');
  // The trailing comma or colon is REQUIRED: optional, this ate the start of any
  // body that merely began with "Hi" or "Hello".
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
    if (SIGNOFFS.indexOf(last) !== -1 || (lines.length > 1 && /^[a-z]{2,20}$/.test(last))) {
      lines.pop();
      continue;
    }
    break;
  }
  return lines.join('\n').trim();
}

// Paragraphs as the model wrote them, blank-line separated, inner whitespace
// tidied; never more than one blank line in a row.
function cleanText(raw, greeting) {
  return stripSignoff(stripGreeting(raw, greeting))
    .split(/\n\s*\n/)
    .map(function (p) { return p.replace(/\s*\n\s*/g, ' ').trim(); })
    .filter(Boolean)
    .join('\n\n');
}

function urlsIn(v) {
  return str(v).match(URL_RE) || [];
}

function bannedAdjectivesIn(v) {
  const t = ' ' + str(v).toLowerCase() + ' ';
  return BANNED_ADJECTIVES.filter(function (a) { return t.indexOf(a) !== -1; });
}

// Trial titles are quoted from ClinicalTrials.gov and can hold anything -- a
// digit, a question mark, a therapeutic area. The checks judge what the model
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

function digitTokens(v) {
  return (str(v).match(/[\p{L}\p{N}]*\p{N}[\p{L}\p{N}]*/gu) || []).map(function (t) { return t.toLowerCase(); });
}

// Numbers the text states that the corpus (this lead's facts plus every claim it
// was offered) does not: a digit token, a spelled-out number or multiplier, or a
// percent sign.
function unlicensedNumbers(text, corpus) {
  const haveDigits = {};
  digitTokens(corpus).forEach(function (t) { haveDigits[t] = true; });
  const bad = digitTokens(text).filter(function (t) { return !haveDigits[t]; });
  let corpusWords = ' ' + fold(corpus).replace(/[^a-z0-9]+/g, ' ') + ' ';
  Object.keys(haveDigits).forEach(function (t) {
    if (/^\d+$/.test(t) && Number(t) < DIGIT_WORDS.length) corpusWords += DIGIT_WORDS[Number(t)] + ' ';
  });
  const textWords = fold(text).replace(/[^a-z0-9]+/g, ' ').split(' ').filter(Boolean);
  textWords.forEach(function (w) {
    if (NUMBER_WORDS.indexOf(w) !== -1 && corpusWords.indexOf(' ' + w + ' ') === -1) bad.push(w);
  });
  if (str(text).indexOf('%') !== -1 && str(corpus).indexOf('%') === -1) bad.push('%');
  return bad;
}

function absentAreasNamed(text, absentAreas) {
  const plain = ' ' + fold(text).replace(/[^a-z0-9]+/g, ' ') + ' ';
  return (absentAreas || []).filter(function (a) {
    return plain.indexOf(' ' + fold(a).replace(/[^a-z0-9]+/g, ' ') + ' ') !== -1;
  });
}

// Does the text open on this name (or its first word)? Plain string compare.
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

function firstSentence(text) {
  const m = str(text).match(/^[\s\S]*?[.!?](?=\s|$)/);
  return m ? m[0] : str(text);
}

function countOf(re, text) {
  return (str(text).match(new RegExp(re.source, re.flags.indexOf('g') === -1 ? re.flags + 'g' : re.flags)) || []).length;
}

// ---------------------------------------------------------------------------
// The rule checks. Each returns the tags it found; checkMessage runs them all.
// ---------------------------------------------------------------------------

// "Exactly one ask."
function askFlags(body, ask) {
  const flags = [];
  const q = countOf(/\?/, ask);
  if (!str(ask) || q === 0) flags.push('ask-none');
  if (q > 1 || REQUEST_IN_BODY.test(body)) flags.push('ask-multiple');
  if (ASK_SCHEDULING.test(ask)) flags.push('ask-scheduling');
  return flags;
}

// "The name appears at most once, in brackets." Stripping every bracketed
// segment leaves nothing that may still say the name.
function productFlags(text) {
  const flags = [];
  const name = new RegExp('\\b' + PRODUCT_NAME + '\\b', 'gi');
  const n = countOf(name, text);
  if (n > PRODUCT_NAME_MAX) flags.push('product-name-repeat');
  const unbracketed = str(text).replace(/\([^()]*\)/g, ' ');
  if (new RegExp('\\b' + PRODUCT_NAME + '\\b', 'i').test(unbracketed)) flags.push('product-name-unbracketed');
  return flags;
}

// "The word 'AI' appears at most once ... Never 'AI-powered'."
function aiFlags(text) {
  const flags = [];
  if (countOf(/\bA\.?I\b\.?/, text) > AI_MAX) flags.push('ai-repeat');
  if (AI_POWERED.test(text)) flags.push('ai-powered');
  return flags;
}

function prospectSponsor(text) {
  return PROSPECT_SPONSOR.some(function (re) { return re.test(text); });
}

// Skill v3 section 3, Claims, forbidden.
function claimFlags(text, corpus) {
  const flags = [];
  if (GUARANTEE.test(text)) flags.push('claim-guarantee');
  if (unlicensedNumbers(text, corpus).length) flags.push('claim-number');
  if (VISITOR_ID.test(text)) flags.push('claim-visitor-id');
  const t = ' ' + fold(text) + ' ';
  if (NAMED_TOOLS.some(function (n) { return new RegExp('(^|[^a-z0-9])' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '([^a-z0-9]|$)').test(t); })) {
    flags.push('claim-named-tool');
  }
  if (LANGUAGES.test(text)) flags.push('claim-language');
  if (CHATBOT.test(text)) flags.push('claim-chatbot');
  return flags;
}

// "Proof: one named live deployment, matched to the lead's geography."
function proofFlags(text, proofPool) {
  const flags = [];
  const pool = proofPool || [];
  const allowed = DEPLOYMENTS.filter(function (d) {
    return pool.some(function (p) { return fold(p.body).indexOf(fold(d)) !== -1; });
  });
  const both = pool.some(function (p) { return /\btwo CROs\b/i.test(p.body); });
  const named = DEPLOYMENTS.filter(function (d) { return fold(text).indexOf(fold(d)) !== -1; });
  if (named.some(function (d) { return allowed.indexOf(d) === -1; })) flags.push('proof-geo');
  const present = named.some(function (d) { return allowed.indexOf(d) !== -1; }) ||
    (both && /\btwo (?:CROs|CRO clients|clients)\b/i.test(text));
  if (!present) flags.push('proof-missing');
  return flags;
}

// The first sentence opens on the prospect fact. Section 9 Workflow 4:
// founder_name and city never open a message; a trial is never credited to
// their website.
function hookFlags(hook, src, titles) {
  const flags = [];
  if (startsWithName(hook, src.founder_name) || startsWithName(hook, src.city)) flags.push('hook-opener');
  const own = withoutTitles(hook, titles);
  const titlesNamed = (titles || []).filter(function (t) {
    return str(t) && hook.toLowerCase().indexOf(str(t).toLowerCase()) !== -1;
  }).length;
  const trialOnSite = SITE_CLAIM.test(own) && (titlesNamed > 0 || TRIAL_WORD.test(own));
  if (HOOK_SOURCE_OPENING.test(hook) || trialOnSite) flags.push('hook-source');
  return flags;
}

function linkFlags(text, week, approvedLink) {
  const flags = [];
  const urls = urlsIn(text);
  if (urls.length > MAX_URLS) flags.push('urls');
  if (urls.length && week <= LINK_FREE_WEEKS) flags.push('link-in-warmup');
  const approved = approvedLink ? urlsIn(approvedLink.body) : [];
  if (urls.some(function (u) { return approved.indexOf(u) === -1; })) flags.push('unapproved-link');
  if (urls.some(function (u) { return /linkedin\.com/i.test(u); })) flags.push('linkedin-link');
  if (urls.some(function (u) { return /\.pdf\b/i.test(u); })) flags.push('pdf-link');
  return flags;
}

function subjectFlags(subject, factCorpus, headcount, titles, absentAreas) {
  const flags = [];
  const chars = Array.from(subject).length;
  if (!subject || chars < SUBJECT_MIN_CHARS || chars > SUBJECT_MAX_CHARS) flags.push('subject');
  if (new RegExp('\\b' + PRODUCT_NAME + '\\b', 'i').test(subject)) flags.push('subject-product');
  if (/\bA\.?I\b/i.test(subject)) flags.push('subject-ai');
  const prefix = new RegExp('\\b(' + SUBJECT_REPLY_PREFIXES.join('|') + ')\\s*:', 'i');
  if (prefix.test(subject)) flags.push('subject-reply');
  // "No capitals": sentence case. A capitalised word after the first is allowed
  // only when the facts spell it that way (a trial code, a city) or it is an
  // acronym a CRO uses.
  const corpusWords = {};
  (factCorpus.match(/[\p{L}\p{N}-]+/gu) || []).forEach(function (t) { corpusWords[t] = true; });
  const tokens = subject.match(/[\p{L}\p{N}-]+/gu) || [];
  const capped = tokens.filter(function (t, i) {
    if (!/\p{Lu}/u.test(t)) return false;
    if (i === 0 && !/^\p{Lu}{2,}$/u.test(t)) return false;
    return !corpusWords[t] && SUBJECT_ACRONYMS.indexOf(t.toUpperCase()) === -1;
  });
  if (capped.length) flags.push('subject-caps');
  const corpus = factCorpus + (headcount ? ' ' + headcount : '');
  const bare = withoutTitles(subject, titles);
  if (digitTokens(bare).some(function (t) { return digitTokens(corpus).indexOf(t) === -1; }) ||
      absentAreasNamed(bare, absentAreas).length) {
    flags.push('subject-ungrounded');
  }
  return flags;
}

// Every rule that applies to a message body, either channel.
function checkMessage(m) {
  let flags = [];
  if (!str(m.body)) flags.push('empty');
  if (words(m.core) > BODY_CEILING) flags.push('long');
  if (bannedAdjectivesIn(m.core).length) flags.push('adjective');
  if (MERGE_TAG.test(m.core)) flags.push('merge-tag');
  flags = flags.concat(askFlags(m.body, m.ask));
  flags = flags.concat(productFlags(m.core));
  flags = flags.concat(aiFlags(m.core));
  if (prospectSponsor(m.core)) flags.push('prospect-sponsor');
  const own = withoutTitles(m.core, m.titles);
  flags = flags.concat(claimFlags(own, m.corpus));
  if (absentAreasNamed(own, m.absentAreas).length) flags.push('ungrounded-area');
  if (str(m.body)) flags = flags.concat(hookFlags(firstSentence(m.body), m.src, m.titles));
  flags = flags.concat(proofFlags(m.core, m.proofPool));
  return flags;
}

function unique(list) {
  return list.filter(function (v, i) { return list.indexOf(v) === i; });
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

// --- Did Claude answer, completely, with the schema we asked for? -----------
//
// The HTTP node continues on error, so a failed call arrives as a normal item.
// Skill v3 section 6: "If the API call fails, the lead stays queued and the
// next run retries. No fallback to the local model." So any failure -- an HTTP
// error, a refusal, a response cut off at max_tokens, text that is not the
// schema -- writes nothing, and the lead stays 'contact_found'.
function failed(detail) {
  return {
    json: Object.assign({}, base, {
      write: false,
      skip_reason: 'Claude drafting call did not answer usably: ' + detail,
      usage: resp && resp.usage ? resp.usage : null,
    }),
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
const FIELDS = ['email_subject', 'email_body', 'email_ask', 'linkedin_body', 'linkedin_ask'];
const CLAIM_LISTS = ['email_claims', 'linkedin_claims'];
if (!parsed || typeof parsed !== 'object' ||
    FIELDS.some(function (f) { return typeof parsed[f] !== 'string'; }) ||
    CLAIM_LISTS.some(function (f) { return !Array.isArray(parsed[f]); })) {
  return failed('response text was not the drafting schema: ' + text.slice(0, 200));
}

// Section 5: the signature is name / one line of title / phone, appended
// verbatim; nothing is invented to fill a gap.
const SIGNATURE = __SIGNATURE__;

const week = Number(src.warmup_week) || 1;
const pools = src.library_pools || {};

// --- Which claims the model says each message used --------------------------
//
// Reported per message, because `variant` is what the learning loop reads to
// see which claims earn replies: one list for both messages recorded the DM's
// ask against the email (measured on the first v3 run). Every code is checked
// against what this lead was offered; one that was not is tagged `claim-code`
// (and so is reporting none). The known codes, in slot order, go into that
// message's `variant`.
const offered = {};
SLOT_ORDER.forEach(function (slot) {
  (pools[slot] || []).forEach(function (l) { offered[l.code] = { slot: slot, confirmed: l.confirmed }; });
});
// The ask and the proof are also attributed in code, from the text, when the
// model leaves their code out. Measured on the 2026-10-02 run: three of eight
// messages ended on an approved ask -- one of them word for word -- and listed
// no ask code, so `variant` lost the one slot the learning loop most needs.
// Same rule as enrichment: never rely on a model to copy an identifier; name
// the thing, then match it deterministically. The ask is the library line whose
// words it shares most (at least half, by Jaccard on word sets); the proof is
// the line whose deployment it names, or PR-BOTH's "two CROs".
function wordSet(v) {
  const set = {};
  fold(v).replace(/[^a-z0-9]+/g, ' ').split(' ').filter(Boolean).forEach(function (w) { set[w] = true; });
  return set;
}
function jaccard(a, b) {
  const A = wordSet(a);
  const B = wordSet(b);
  const keys = unique(Object.keys(A).concat(Object.keys(B)));
  const both = keys.filter(function (k) { return A[k] && B[k]; }).length;
  return keys.length ? both / keys.length : 0;
}
function inferAsk(askText) {
  let best = null;
  (pools.ask || []).forEach(function (l) {
    const j = jaccard(askText, l.body);
    if (j >= 0.5 && (!best || j > best.j)) best = { code: l.code, j: j };
  });
  return best ? best.code : null;
}
function inferProof(core) {
  const hit = (pools.proof || []).filter(function (l) {
    const names = DEPLOYMENTS.filter(function (d) { return fold(l.body).indexOf(fold(d)) !== -1; });
    return names.length ? names.some(function (d) { return fold(core).indexOf(fold(d)) !== -1; })
      : (/\btwo CROs\b/i.test(l.body) && /\btwo (?:CROs|CRO clients|clients)\b/i.test(core));
  });
  return hit.length ? hit[0].code : null;
}
function claimsOf(list, askText, core) {
  const reported = unique(list.map(str).filter(Boolean));
  const used = reported.filter(function (c) { return offered[c]; });
  const hasSlot = function (slot) { return used.some(function (c) { return offered[c].slot === slot; }); };
  const inferred = [];
  if (!hasSlot('ask')) { const a = inferAsk(askText); if (a) inferred.push(a); }
  if (!hasSlot('proof')) { const p = inferProof(core); if (p) inferred.push(p); }
  return {
    reported: reported,
    inferred: inferred,
    unknown: reported.filter(function (c) { return !offered[c]; }),
    used: used.concat(inferred).sort(function (a, b) {
      return SLOT_ORDER.indexOf(offered[a].slot) - SLOT_ORDER.indexOf(offered[b].slot);
    }),
  };
}

// Existing link policy: none in warm-up weeks 1-2; after that, at most the
// library's link line, appended by code. The LinkedIn DM never carries it -- a
// human sends that from their own profile (Section 6).
const linkLines = week > LINK_FREE_WEEKS ? (src.library_link || []) : [];
const link = linkLines.length ? linkLines[Math.abs(parseInt(src.lead_id, 10) || 0) % linkLines.length] : null;

// --- Compose the sendable text ----------------------------------------------

const emailSubject = str(parsed.email_subject).replace(/\s+/g, ' ');
const emailBody = cleanText(parsed.email_body, src.email_greeting);
const emailAsk = cleanText(parsed.email_ask, src.email_greeting);
const linkedinBody = cleanText(parsed.linkedin_body, src.linkedin_greeting);
const linkedinAsk = cleanText(parsed.linkedin_ask, src.linkedin_greeting);

// The body the length rule counts: everything between the greeting and the
// opt-out line.
const emailCore = [emailBody].concat(link ? [link.body] : [], [emailAsk]).filter(Boolean).join('\n\n');
const linkedinCore = [linkedinBody, linkedinAsk].filter(Boolean).join('\n\n');

// Email: greeting / body / ask / opt-out / signature. The opt-out-then-
// signature tail is exactly what Workflow 6 checks for before it will send.
const emailMessage = [src.email_greeting, '', emailCore, '', OPT_OUT].join('\n');
const emailFull = SIGNATURE ? emailMessage + '\n\n' + SIGNATURE : emailMessage;

// LinkedIn: greeting / body / ask. No opt-out sentence and no signature --
// Section 6 makes this a DM a human reads and sends from their own account.
const linkedinFull = [src.linkedin_greeting, '', linkedinCore].join('\n');

const emailClaims = claimsOf(parsed.email_claims, emailAsk, emailCore);
const linkedinClaims = claimsOf(parsed.linkedin_claims, linkedinAsk, linkedinCore);

// --- Deterministic constraint checks ----------------------------------------

const titles = src.trial_titles || [];
const factCorpus = str(src.fact_sheet);
const claimCorpus = SLOT_ORDER.map(function (slot) {
  return (pools[slot] || []).map(function (l) { return l.body; }).join('\n');
}).join('\n');
// The fact sheet's own numbering ("1. ...", "2. ...") is layout, not a fact: left
// in, it licensed "two" and "three" in every draft.
const corpus = factCorpus.replace(/^\d+\.\s/gm, '') + '\n' + claimCorpus + (link ? '\n' + link.body : '');

function message(body, ask, core) {
  return {
    body: body, ask: ask, core: core, titles: titles, corpus: corpus, src: src,
    absentAreas: src.absent_areas, proofPool: pools.proof,
  };
}

function claimTags(claims, withLink) {
  const flags = [];
  if (claims.unknown.length || !claims.reported.length) flags.push('claim-code');
  const anyUnconfirmed = claims.used.some(function (c) { return !offered[c].confirmed; }) ||
    (withLink && !withLink.confirmed);
  if (anyUnconfirmed) flags.push('unconfirmed-claim');
  return flags;
}

function checkEmail() {
  let flags = checkMessage(message(emailBody, emailAsk, emailCore));
  flags = flags.concat(linkFlags(emailCore + '\n' + emailSubject, week, link));
  flags = flags.concat(subjectFlags(emailSubject, factCorpus, src.headcount, titles, src.absent_areas));
  if (prospectSponsor(emailSubject)) flags.push('prospect-sponsor');
  return unique(flags.concat(claimTags(emailClaims, link)));
}

// The DM is sent by a human from their own account (Section 6): no subject, and
// no link of any kind.
function checkLinkedin() {
  let flags = checkMessage(message(linkedinBody, linkedinAsk, linkedinCore));
  flags = flags.concat(linkFlags(linkedinCore, week, null));
  return unique(flags.concat(claimTags(linkedinClaims, null)));
}

// variant = addressing / claim codes used + flags.
function variantTag(addressing, codes, flags) {
  const v = addressing + '/' + codes;
  return flags.length ? v + '+' + flags.join(',') : v;
}

const codes = emailClaims.used.concat(link ? [link.code] : []).join('.');
const linkedinCodes = linkedinClaims.used.join('.');
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
    email_words: words(emailCore),
    linkedin_words: words(linkedinCore),
    email_claims: emailClaims.used,
    linkedin_claims: linkedinClaims.used,
    unknown_claims: unique(emailClaims.unknown.concat(linkedinClaims.unknown)),
    model: resp.model,
    usage: resp.usage || null,
    payload: {
      lead_id: src.lead_id,
      advance: true,
      drafts: [
        {
          channel: 'email',
          variant: variantTag(src.email_addressing, codes, emailFlags),
          subject: emailSubject,
          body: emailFull,
        },
        {
          channel: 'linkedin',
          variant: variantTag(src.linkedin_addressing, linkedinCodes, linkedinFlags),
          subject: null,
          body: linkedinFull,
        },
      ],
    },
  }),
};

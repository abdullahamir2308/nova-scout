// ===========================================================================
// The questions a reply may not answer -- shared by Build Reply and Assemble
// Reply (Section 9, Workflow 7). build_workflow.py prepends this file to both
// nodes, so the topic one node detects in the prospect's message is the same
// topic the other checks the draft against; two copies would drift and the
// drift would be invisible.
// ===========================================================================
//
// WHAT A REPLY MAY SAY is exactly what a first touch may say: the confirmed
// `claims_library` lines, and the facts in the lead's own enrichment record.
// Migration 013 narrowed every claim to what the Nova Agent Kit code actually
// does (`capabilities`), so "what the claims support" and "what the code
// supports" are the same set by construction, and a sentence outside it is an
// invention whether or not it happens to be true.
//
// A prospect's first reply, though, is usually a QUESTION, and the questions
// that come back are mostly about things no claim covers: what it costs, what
// it plugs into, which languages it speaks, where the data lives, how long it
// takes. The first touch never raised any of them, so there is nothing to
// widen -- which is precisely why a model will answer them from nowhere.
//
// So the answer is not "tag it afterwards", it is "do not answer it": the topic
// is detected in THEIR text before the model is called, the model is told to
// say we will confirm it, and the draft is flagged to the operator, who knows
// the answer and can put it in with EDIT. The five topics are the operator's
// list (2026-10-09).
//
// The patterns are deliberately broad. A false positive costs one hedged
// sentence and one line in the review email; a false negative is a made-up
// price or a made-up integration sent to a prospect over the operator's name.
const OPEN_TOPICS = [
  {
    topic: 'pricing',
    label: 'what it costs',
    re: /\b(?:pric(?:e|es|ing)|cost|costs|costly|how much|fee|fees|budget|quote|quotation|licen[cs]\w*|subscription|per (?:month|year|seat|user)|monthly|annual(?:ly)?|retainer|discount|invoice|payment terms)\b/i,
  },
  {
    topic: 'integrations',
    label: 'what it connects to',
    re: /\b(?:integrat\w*|API|APIs|webhook\w*|CRM|salesforce|hubspot|zapier|SSO|single sign-?on|LDAP|CTMS|eTMV|eTMF|EDC|SharePoint|Teams|Slack|calendar|google workspace|office ?365|plug(?:s|ged)? into|connect(?:s|ed|ion|ions)? (?:to|with))\b/i,
  },
  {
    topic: 'languages',
    label: 'which languages it speaks',
    re: /\b(?:language|languages|multi-?lingual|translat\w*|english|turkish|spanish|arabic|german|french|portuguese|polish|romanian|hungarian|czech|hindi|urdu|mandarin|japanese)\b/i,
  },
  {
    topic: 'security',
    label: 'security, hosting and data protection',
    re: /\b(?:secur\w*|GDPR|KVKK|HIPAA|GxP|21 CFR|part 11|data protection|privacy|confidential\w*|NDA|DPA|encrypt\w*|anonymi[sz]\w*|pseudonymi[sz]\w*|hosted?|hosting|on-?prem\w*|server location|where (?:is|are) (?:the )?data|data (?:residency|centre|center|retention|storage)|SOC ?2|ISO ?27001|penetration test|audit trail|validat\w*)\b/i,
  },
  {
    topic: 'timelines',
    label: 'how long it takes and when it can start',
    re: /\b(?:timeline|time-?line|how long|lead ?time|turn ?around|go[- ]live|deadline|availabilit\w*|when (?:can|could|would|will)|start date|roll ?out|implementation (?:time|period)|SLA|uptime|support hours|response time|maintenance)\b/i,
  },
];

// A sentence that says we will come back with the answer, rather than giving
// one. Assemble Reply requires one of these for every topic the prospect
// raised: without it, a paragraph that mentions pricing is answering about
// pricing.
const DEFERRAL_RE = /\b(?:I(?:'| wi)?ll (?:confirm|check|find out|come back|get back|send you the detail)|let me (?:confirm|check|find out)|I (?:do not|don't) want to guess|rather than guess|confirm (?:that|this|these|the detail)|get back to you (?:on|with|about)|come back to you (?:on|with|about)|check (?:that|this) (?:and|then|with)|I will ask)\b/i;

// Which of the five the prospect actually raised, in the order above.
function topicsIn(text) {
  const t = String(text === null || text === undefined ? '' : text);
  return OPEN_TOPICS.filter(function (o) { return o.re.test(t); }).map(function (o) { return o.topic; });
}

function topicLabel(topic) {
  const hit = OPEN_TOPICS.filter(function (o) { return o.topic === topic; })[0];
  return hit ? hit.label : topic;
}

function topicRe(topic) {
  const hit = OPEN_TOPICS.filter(function (o) { return o.topic === topic; })[0];
  return hit ? hit.re : null;
}

function deferralIn(text) {
  return DEFERRAL_RE.test(String(text === null || text === undefined ? '' : text));
}

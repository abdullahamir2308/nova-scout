// Is this a whole cold email? -- shared by Decide Send and Decide Command.
//
// Section 9, Workflow 6 (2026-10-28). A first touch or a follow-up is only sent
// if, between the greeting and the sender's identity, it reads like an email: a
// greeting line, at least COLD_MIN_WORDS words of message, and no email address
// in that message. Whoever wrote or edited the text, the template, a person in
// the NocoDB grid or an EDIT by mail, Send applies the same rule.
//
// WHY. On 2026-10-24 the operator answered the hold on JSS Medical Research
// (draft 350, no address) with "EDIT <code> Send to this email mailto:bdteam@...",
// meaning "use this address". EDIT replaces a draft's text: it kept the first
// paragraph ("Hi there,") and the signature and put the instruction in between,
// and the draft was approved. It carried a greeting and a signature, so every
// existing check passed; only the missing address kept it from going to JSS.
// This rule is what refuses it: five words, and an address in the text. The
// operator's way to give an address is now CONTACT (code_operator_command.js).
//
// The shortest real cold email is follow-up #2 (about 35 words above the name);
// a reply (`reply/…`) never comes through here, it has its own lane.
//
// Library only: build_workflow.py places this above the node body of Decide
// Send and of Decide Command (and so of Classify Inbound), and the unit tests
// load it the same way.

const COLD_IDENTITY = __SENDER_IDENTITY__;
const COLD_MIN_WORDS = 20;
const COLD_GREETING_RE = /^(?:hi|hello|dear|good (?:morning|afternoon|evening))\b[^\n]{0,60},$/i;
const COLD_ADDRESS_RE = /mailto:|[^\s@<>()]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/i;

// null when the text is a whole cold email; otherwise a short reason code.
function coldEmailProblem(body) {
  const b = String(body === null || body === undefined ? '' : body).replace(/\r\n?/g, '\n').trim();
  // The identity as whole lines, the same test Decide Send's signature check makes.
  const at = ('\n' + b + '\n').indexOf('\n' + COLD_IDENTITY + '\n');
  if (at === -1) return 'no-signature';
  const lines = b.slice(0, at).split('\n');
  while (lines.length && !lines[0].trim()) lines.shift();
  if (!lines.length || !COLD_GREETING_RE.test(lines[0].trim())) return 'no-greeting';
  const message = lines.slice(1).join('\n');
  const words = message.split(/\s+/).filter(Boolean).length;
  if (words < COLD_MIN_WORDS) return 'too-short:' + words + '-words';
  if (COLD_ADDRESS_RE.test(message)) return 'address-in-text';
  return null;
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

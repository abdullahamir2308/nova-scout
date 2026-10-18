// Unit tests for the template follow-ups -- code_followup.js (Build Follow-Up),
// Section 9 Workflow 6 since 2026-10-19.
//
//   node test_followup.js
//
// Build Follow-Up ships as drafting's code_template.js above its "Node body"
// marker followed by code_followup.js; the tests run exactly that
// concatenation, with the same build substitution, so they exercise the JS that
// ships rather than a copy of it. The wording itself is pinned in
// n8n/drafting/test_template.js; what is pinned here is the node: which rows
// become which drafts, the gate's holds, and -- the test worth reading -- that
// every follow-up and first touch the templates write passes the SEND path's
// own eligibility function. A draft the send path would refuse would sit
// approved forever, so it has to be sendable by construction.
const fs = require('fs');
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const CLOCKS = ['Turkey', 'Poland', 'India', 'USA', 'Argentina'];
const BUILD = path.join(__dirname, 'code_followup.js');
const TEMPLATE = path.join(__dirname, '..', 'drafting', 'code_template.js');
const TEMPLATE_SRC = fs.readFileSync(TEMPLATE, 'utf8');
const RULES = TEMPLATE_SRC.slice(0, TEMPLATE_SRC.indexOf('// Node body'));
const bake = (s) => RULES + '\n' + s.replace('__SEND_CLOCK_COUNTRIES__', JSON.stringify(CLOCKS));

// The identity the shipped Send checks for: the template's sign-off lines 2-3,
// which the build asserts are NOVASCOUT_SENDER_NAME / _TITLE.
const IDENTITY = 'Abdullah Amir\nFounder, Amitrix Labs';
const { ineligibility } = extractFunctions(
  path.join(__dirname, 'code_decide.js'), '// Node body', ['ineligibility'],
  (s) => s.replace('__SENDER_IDENTITY__', JSON.stringify(IDENTITY)));
const T = new Function(RULES + '\nreturn { renderFirstTouch, leadFacts };')();

const t = runner('Workflow 6 -- template follow-ups');

const OPT = "If this isn't relevant, reply 'no' and I won't follow up.";
// A first email as it really went out before 2026-10-19: composed, with the
// opt-out line and the name / title / phone signature. It is only quoted.
const OLD_FIRST = 'Hello,\n\nYour site lists oncology and cardiovascular. Sponsors often research CROs outside your ' +
  'working hours.\n\nWould a 48-hour demo be worth a look?\n\n' + OPT + '\n\nAbdullah Amir\nFounder, Amitrix Labs\n+923178485713';

function due(over) {
  return Object.assign({
    lead_id: 104, action: 'draft-follow-up', next_follow_up: 1, first_subject: 'Sponsor inquiries in oncology',
    first_body: OLD_FIRST, first_sent_at: '2026-10-05T10:00:00.000Z', last_sent_at: '2026-10-05T10:00:00.000Z',
    country: 'Turkey', domain: 'atlantclinical.com', company_type: 'CRO', auto_approve_email: true,
    company_name: 'Atlant Clinical Ltd', contact_name: '', email: 'contact@atlantclinical.com', verified: true,
    lead_domain_blocked: false, recipient_domain_blocked: false,
  }, over || {});
}
function run(rows) {
  return runOnceForAll(BUILD, rows.map((j) => ({ json: j })), {}, bake);
}

// --- which rows become which drafts ----------------------------------------------

{
  const out = run([due(), due({ lead_id: 91, next_follow_up: 2, company_name: 'BiTrial', domain: 'bitrial.pl',
    email: 'anna.kowalska@bitrial.pl', contact_name: 'Anna Kowalska', country: 'Poland' }),
  due({ lead_id: 7, action: 'mark-lost', next_follow_up: 3 })]);
  t.check('three due rows -> three items, each paired to its row', out.map((o) => o.pairedItem.item), [0, 1, 2]);
  const [one, two, lost] = out.map((o) => o.json);
  t.check('#1, lead 104 (even -> arm A): approved by auto', [one.payload.action, one.payload.draft.status,
    one.payload.draft.approved_by, one.payload.draft.variant], ['draft-follow-up', 'approved', 'auto', 'follow-up-1/template/A']);
  t.check('#1: threaded on the first subject', one.payload.draft.subject, 'Re: Sponsor inquiries in oncology');
  t.check('#1: the note, then arm A\'s close, then the sign-off',
    one.payload.draft.body.split('\n\nOn ')[0].split('\n\n').slice(1),
    ["Just bringing this back to the top of your inbox in case it got buried. Every sponsor who visits Atlant " +
     "Clinical's website is a potential study, and we help make sure those visits turn into conversations.",
     'Do you have time over the next week or two to learn more? Let me know what works for you, and I\'ll send a ' +
     'calendar invite along accordingly.',
     'Cheers!\nAbdullah Amir\nFounder, Amitrix Labs']);
  t.check('#1: the first email quoted below the attribution line',
    one.payload.draft.body.indexOf('\n\nOn Mon, 05 Oct 2026, Abdullah Amir wrote:\n> Hello,\n>\n> Your site lists') !== -1, true);
  t.check('#2, lead 91 (odd -> arm B): the final note, no close, greeted by name',
    [two.payload.draft.variant, two.payload.draft.body.split('\n')[0],
      two.payload.draft.body.split('\n\nOn ')[0].indexOf('Worth a look') === -1],
    ['follow-up-2/template/B', 'Hi Anna,', true]);
  t.check('mark-lost -> no draft, straight to the write', lost.payload, { lead_id: 7, action: 'mark-lost', draft: null });
  const oneB = run([due({ lead_id: 91, company_name: 'BiTrial', domain: 'bitrial.pl', country: 'Poland' })])[0].json;
  t.check('#1, lead 91 (odd -> arm B): closes with arm B\'s close, not A\'s',
    [oneB.payload.draft.variant, oneB.payload.draft.body.split('\n\nOn ')[0].split('\n\n')[2]],
    ['follow-up-1/template/B', 'Worth a look for BiTrial?']);
  t.check('no model request anywhere on the item', JSON.stringify(out).indexOf('"request"') === -1 &&
    JSON.stringify(out).indexOf('anthropic') === -1, true);
}
t.check('an empty queue (the Postgres { success: true } placeholder) -> no items', run([{ success: true }]), []);
t.check('a site or SMO row (the query changed) -> nothing written', run([due({ company_type: 'site' })]), []);
{
  let threw = '';
  try { run([due({ next_follow_up: 3 })]); } catch (e) { threw = e.message; }
  t.check('a third follow-up is never drafted (throws, the whole run stops)', /no follow-up #3 \(max 2\)/.test(threw), true);
}

// --- the gate's holds ------------------------------------------------------------------

function draftOf(over) { return run([due(over)])[0].json.payload.draft; }
t.check('an unverified address -> held, tagged',
  [draftOf({ verified: false }).status, draftOf({ verified: false }).variant], ['pending', 'follow-up-1/template/A+unverified-address']);
t.check('no address -> held no-address', draftOf({ email: null }).variant, 'follow-up-1/template/A+no-address');
t.check('no Send clock -> held no-send-clock', draftOf({ country: 'Atlantis' }).hold_reason,
  'no-send-clock: Atlantis has no business-hours clock, so this email can never be sent (Section 12)');
t.check('the recipient domain blocklisted -> held blocklisted',
  draftOf({ email: 'x@blocked.example', recipient_domain_blocked: true }).hold_reason, 'blocklisted: blocked.example is on the blocklist');
t.check('auto-approval off -> pending, untagged',
  [draftOf({ auto_approve_email: false }).status, draftOf({ auto_approve_email: false }).variant, draftOf({ auto_approve_email: false }).approved_by],
  ['pending', 'follow-up-1/template/A', null]);

// --- sendable by construction: Send's own eligibility function -----------------------------

function sendCand(d, over) {
  return Object.assign({ draft_id: 1, lead_id: 104, channel: 'email', status: 'approved', variant: d.variant,
    subject: d.subject, body: d.body, domain: 'atlantclinical.com', country: 'Turkey', to_addr: 'contact@atlantclinical.com',
    verified: true, lead_domain_blocked: false, recipient_domain_blocked: false, prior_manual_sends: 0, replied: false,
    bounced: false }, over);
}
{
  const fu1 = draftOf({});
  const fu2 = draftOf({ next_follow_up: 2 });
  t.check('follow-up #1 as written passes Send (no opt-out, no phone, the three-line sign-off)',
    ineligibility(sendCand(fu1, { lead_status: 'sent', prior_sends: 1, follow_ups_sent: 0 })), null);
  t.check('follow-up #2 as written passes Send',
    ineligibility(sendCand(fu2, { lead_status: 'sent', prior_sends: 2, follow_ups_sent: 1 })), null);
  t.check('... and the identity it passes on is its own sign-off, not the quoted email\'s',
    ineligibility(sendCand({ variant: fu1.variant, subject: fu1.subject, body: fu1.body.replace('Cheers!\nAbdullah Amir\nFounder, Amitrix Labs', 'Cheers!') },
      { lead_status: 'sent', prior_sends: 1, follow_ups_sent: 0 })), 'stale-signature');
  const facts = T.leadFacts({ therapeutic_areas: ['Oncology'], phases: ['Phase I', 'Phase II'], city: 'Istanbul' }, 2);
  for (const id of [104, 105]) {
    const ft = T.renderFirstTouch({ lead_id: id, company_name: 'Atlant Clinical Ltd', domain: 'atlantclinical.com',
      email: 'contact@atlantclinical.com', contact_name: '', facts: facts });
    t.check('a template first touch (arm ' + ft.arm + ') as written passes Send',
      ineligibility(sendCand(ft, { lead_status: 'drafted', prior_sends: 0, follow_ups_sent: 0 })), null);
  }
}

t.done();

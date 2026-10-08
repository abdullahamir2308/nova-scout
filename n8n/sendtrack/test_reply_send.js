// Unit tests for the reply send lane -- code_reply_decide.js (Decide Reply Send),
// Section 9, Workflow 7.
//
//   node test_reply_send.js
//
// What is proven here is the sentence "replies skip the cold ceiling and
// business hours; the blocklist still applies", clause by clause, plus the
// threading headers the whole lane exists for. Claim Reply Send re-checks every
// one of these in SQL, and the dry run proves that; a bug here can make the
// lane send less, never more.
const path = require('path');
const { extractFunctions, runOnceForAll, runner } = require('./harness');

const t = runner('Workflow 7 -- Send Reply');

const SIGNATURE = 'Abdullah Amir\nFounder, Amitrix Labs\n+923178485713';
const DECIDE = path.join(__dirname, 'code_reply_decide.js');
const bake = (s) => s.replace('__SIGNATURE__', JSON.stringify(SIGNATURE)).replace('__MAX_URLS__', '1');
const { decide, ineligibility, threadHeaders, normaliseBody } =
  extractFunctions(DECIDE, '// Node body', ['decide', 'ineligibility', 'threadHeaders', 'normaliseBody'], bake);

const OURS = '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>';
const THEIRS = '<01bd01dd562e$5fe83240$1fb896c0$@pharmahungary.com>';

const BODY = [
  'Hello,',
  '',
  'Thank you for the clear answer, and for keeping us in mind.',
  '',
  'Would a 48-hour demo built on your own material be worth a look?',
  '',
  SIGNATURE,
  '',
  'On Wed, 07 Oct 2026, andras.nogradi@pharmahungary.com wrote:',
  '> We are currently not planning a project.',
].join('\n');

const CAND = {
  draft_id: 261, lead_id: 26, channel: 'email', status: 'approved', variant: 'reply/BEN-BRIEF.A1',
  subject: 'Re: Oncology and cardiovascular on your site', body: BODY,
  domain: 'pharmahungary.com', country: 'Hungary', lead_status: 'replied',
  code: 'NS-2345678ABC', approval_used: true, approval_outcome: 'approved',
  to_addr: 'andras.nogradi@pharmahungary.com', their_message_id: THEIRS,
  references_raw: OURS, thread_ids: [OURS], inbound_exists: true,
  our_message_ids: [OURS], lead_domain_blocked: false, recipient_domain_blocked: false,
};
const cand = (over) => Object.assign({}, CAND, over || {});
const state = (over, cands) => ({ clock: '2026-10-09T14:00:00.000Z',
  candidates: cands || [cand(over)], replies_by_status: { approved: 1 } });

// ---------------------------------------------------------------------------
// What a reply skips
// ---------------------------------------------------------------------------

console.log('\nWhat a reply skips -- the ceiling, business hours, pacing');

let d = decide(state());
t.check('an approved reply goes out at 14:00 UTC -- 16:00 in Budapest, outside business hours, and it sends',
  [d.send, d.reason, d.payload.draft_id], [true, 'send', 261]);
t.check('... and at 03:00 on a Sunday too: there is no clock and no weekend in this lane',
  decide(Object.assign(state(), { clock: '2026-10-11T03:00:00.000Z' })).send, true);
t.check('no warm-up state is read at all -- nothing in the decision mentions a ceiling',
  [Object.keys(d).indexOf('warmup'), JSON.stringify(d).indexOf('ceiling')], [-1, -1]);
t.check('no pacing: the same tick would send again next time, with no gap and no coin flip',
  decide(state()).send, true);
t.check('the payload is what the claim and the sender need, and nothing else',
  Object.keys(d.payload).sort(),
  ['body', 'clock', 'code', 'draft_id', 'in_reply_to', 'lead_id', 'raw_body', 'references', 'subject', 'to_addr']);

// ---------------------------------------------------------------------------
// What it does not skip
// ---------------------------------------------------------------------------

console.log('\nWhat it never skips');

t.check('a blocklisted lead domain stops it -- Section 5, honoured instantly and permanently',
  [ineligibility(cand({ lead_domain_blocked: true })), decide(state({ lead_domain_blocked: true })).send],
  ['blocklisted-domain', false]);
t.check('a blocklisted recipient domain stops it too (they replied, then opted out)',
  ineligibility(cand({ recipient_domain_blocked: true })), 'blocklisted-recipient-domain');
t.check('no used approval code: nothing goes out',
  ineligibility(cand({ approval_used: false })), 'no-used-approval-code');
t.check('a code used to REJECT it does not make it sendable',
  ineligibility(cand({ approval_outcome: 'rejected' })), 'approval-outcome:rejected');
t.check('an edited draft IS sendable -- EDIT approves it',
  ineligibility(cand({ approval_outcome: 'edited' })), null);
t.check('not approved: not sent', ineligibility(cand({ status: 'pending' })), 'not-approved');
t.check('a lead that is not `replied` is refused -- a reply answers a message they sent',
  ineligibility(cand({ lead_status: 'sent' })), 'lead-status:sent');
t.check('the inbound message must still be there', ineligibility(cand({ inbound_exists: false })),
  'no-inbound-message');
t.check('a body that lost the current signature is refused, not sent under the wrong name',
  ineligibility(cand({ body: BODY.replace(SIGNATURE, 'Fatima') })), 'stale-signature');
t.check('two URLs is refused -- Section 5 allows one',
  ineligibility(cand({ body: BODY.replace('worth a look?',
    'worth a look? https://a.example/x https://b.example/y') })), 'too-many-urls');
t.check('... one is fine',
  ineligibility(cand({ body: BODY.replace('worth a look?', 'worth a look? https://a.example/x') })), null);
t.check('no subject, no send', ineligibility(cand({ subject: '' })), 'no-subject');
t.check('a recipient that is not one bare address is refused',
  [ineligibility(cand({ to_addr: 'Andras <a@b.com>' })), ineligibility(cand({ to_addr: '' }))],
  ['no-valid-address', 'no-valid-address']);
t.check('a reply with nothing to thread to is refused rather than sent unthreaded',
  ineligibility(cand({ their_message_id: null, thread_ids: [], references_raw: null, our_message_ids: [] })),
  'no-thread-headers');

// Section 6, three times over: the query never selects LinkedIn, migration
// 017's CHECK refuses a reply variant on one, and this throws.
let threw = null;
try { ineligibility(cand({ channel: 'linkedin' })); } catch (e) { threw = e.message; }
t.check('a LinkedIn draft in this lane throws, stopping the tick', /Section 6 violation/.test(threw), true);
threw = null;
try { ineligibility(cand({ variant: 'follow-up-1/A1' })); } catch (e) { threw = e.message; }
t.check('a non-reply draft in this lane throws too -- the two lanes must never overlap',
  /non-reply draft reached the reply send path/.test(threw), true);

t.check('nothing eligible ends the tick with a reason',
  [decide(state({ status: 'pending' })).send, decide(state({ status: 'pending' })).reason],
  [false, 'no-eligible-reply']);
t.check('the excluded list says why, per draft',
  decide(state({ approval_used: false })).excluded,
  [{ draft_id: 261, lead_id: 26, domain: 'pharmahungary.com', reason: 'no-used-approval-code' }]);
t.check('oldest approved reply first, one per tick',
  decide(state(null, [cand({ draft_id: 300 }), cand()])).payload.draft_id, 261);

// ---------------------------------------------------------------------------
// The threading headers -- the reason this lane exists
// ---------------------------------------------------------------------------

console.log('\nThe threading headers (migration 017)');

t.check('In-Reply-To is their Message-ID; References is their chain plus their own id',
  threadHeaders(CAND), { in_reply_to: THEIRS, references: [OURS, THEIRS] });
t.check('... which is exactly what Section 9, Workflow 6 recorded for lead 26',
  [d.payload.in_reply_to, d.payload.references], [THEIRS, [OURS, THEIRS]]);
t.check('a stored References chain is preferred over the parsed ids, and kept in order',
  threadHeaders(cand({ references_raw: '<a@x.com> <b@y.com>', thread_ids: ['<b@y.com>'] })).references,
  ['<a@x.com>', '<b@y.com>', THEIRS]);
t.check('no stored References: the parsed thread_ids stand in',
  threadHeaders(cand({ references_raw: null })).references, [OURS, THEIRS]);
t.check('neither: our own sends on this lead -- correct for a two-message thread, honest about a longer one',
  threadHeaders(cand({ references_raw: null, thread_ids: [] })).references, [OURS, THEIRS]);
t.check('their own id is never duplicated into References',
  threadHeaders(cand({ references_raw: OURS + ' ' + THEIRS })).references, [OURS, THEIRS]);
t.check('a half-parsed header contributes nothing -- only <local@domain> tokens are kept',
  threadHeaders(cand({ references_raw: 'garbage, <notanid>, local-part-only',
    thread_ids: [], our_message_ids: [] })).references, [THEIRS]);

t.check('what is checked is what is sent: line endings normalised, trailing blanks trimmed',
  normaliseBody('Hello,\r\n\r\ntext  \r\n\r\n' + SIGNATURE + '\n\n'), 'Hello,\n\ntext\n\n' + SIGNATURE);

const node = runOnceForAll(DECIDE, [{ json: state() }], {}, bake);
t.check('the node emits exactly one item: the decision', [node.length, node[0].json.send], [1, true]);

t.done();

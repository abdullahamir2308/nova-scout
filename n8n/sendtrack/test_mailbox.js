// Unit tests for the Mailbox Watch Code nodes:
//   code_mirror_sent.js    -- Sent folder -> mailbox_sent rows (the ceiling's input)
//   code_classify_reply.js -- INBOX message -> reply / opt-out / auto-reply / bounce
//   code_notify.js         -- the operator's notification email
//
//   node test_mailbox.js
//
// The input shape is the IMAP trigger's "simple" format as the installed node
// builds it (EmailReadImap/v2/utils.js): cc, date, from, subject and to as raw
// header strings at the top level; every other header, lower-cased, under
// `metadata`; the body in textPlain / textHtml.
const path = require('path');
const { extractFunctions, runOnceForAll, runForEachItem, runner } = require('./harness');

const OWN = 'amitrixlabs.com';
const OPT = "If this isn't relevant, reply 'no' and I won't follow up.";
const ownDomain = (s) => s.replace('__OWN_DOMAIN__', JSON.stringify(OWN));

const t = runner('Workflow 6 -- Mailbox Watch');

// --- Sent mirror --------------------------------------------------------------
const MIRROR = path.join(__dirname, 'code_mirror_sent.js');
const { sentRows, messageId } = extractFunctions(MIRROR, '// Node body', ['sentRows', 'messageId'], ownDomain);

const sent = (over, meta) => Object.assign({
  date: 'Mon, 14 Sep 2026 21:05:03 +0500',
  from: 'Abdullah Amir <abdullah@amitrixlabs.com>',
  to: 'Enrique <hello@klixar.com>',
  subject: 'hello',
  textPlain: 'body',
  metadata: Object.assign({ 'message-id': '<abc123@amitrixlabs.com>' }, meta || {}),
  attributes: { uid: 1 },
}, over);

let m = sentRows([sent()]);
t.check('a manual send to a prospect -> one external row, on the UTC clock',
  m.rows, [{ message_id: '<abc123@amitrixlabs.com>', sent_at: '2026-09-14T16:05:03.000Z', recipients: ['hello@klixar.com'], external: true }]);
t.check('a note to yourself does not warm anything -> not external',
  sentRows([sent({ to: 'abdullah@amitrixlabs.com' })]).rows[0].external, false);
m = sentRows([sent({ to: 'ops@amitrixlabs.com', cc: 'A B <a@partner.com>' }, { bcc: 'hidden@other.org' })]).rows[0];
t.check('To + Cc + Bcc all count; any outside address makes it external',
  [m.recipients, m.external], [['ops@amitrixlabs.com', 'a@partner.com', 'hidden@other.org'], true]);
t.check('a Date header with a zone comment still parses',
  sentRows([sent({ date: 'Mon, 14 Sep 2026 21:05:03 +0500 (PKT)' })]).rows[0].sent_at, '2026-09-14T16:05:03.000Z');
m = sentRows([sent({ date: '' }), sent()]);
t.check('no usable Date -> counted as undated, never guessed', [m.rows.length, m.undated, m.seen], [1, 1, 2]);
const a = sentRows([sent({}, { 'message-id': '' })]).rows[0].message_id;
const b = sentRows([sent({}, { 'message-id': '' })]).rows[0].message_id;
t.check('no Message-ID -> a stable stand-in key, so a re-read hits the same row', [a === b, /@mirror\.invalid>$/.test(a)], [true, true]);
t.check('Message-IDs are kept with their brackets', [messageId('  <x@y>  '), messageId('x@y')], ['<x@y>', '<x@y>']);
const mirrored = runOnceForAll(MIRROR, [{ json: sent() }, { json: sent({}, { 'message-id': '<def@amitrixlabs.com>' }) }], {}, ownDomain);
t.check('the shipped node body folds a trigger batch into ONE payload item',
  [mirrored.length, mirrored[0].json.payload.rows.length], [1, 2]);

// --- inbound classification -----------------------------------------------------
const CLASSIFY = path.join(__dirname, 'code_classify_reply.js');
const { classify, freshText, optOutKeyword } = extractFunctions(
  CLASSIFY, '// Node body', ['classify', 'freshText', 'optOutKeyword'], ownDomain);

const OURS = 'Hello,\n\nYour site lists oncology work. Nova is an assistant trained on your own SOPs.\n\n' +
  OPT + '\n\nAbdullah Amir\nFounder, Amitrix Labs';
const quoted = (lines) => lines.split('\n').map((l) => '> ' + l).join('\n');
const GMAIL_QUOTE = '\n\nOn Mon, 14 Sep 2026 at 21:05, Abdullah Amir <abdullah@amitrixlabs.com> wrote:\n' + quoted(OURS);

const inbound = (text, over, meta) => Object.assign({
  from: 'Enrique Gaubeca <hello@klixar.com>',
  to: 'abdullah@amitrixlabs.com',
  subject: 'Re: question about your protocol docs',
  date: 'Tue, 15 Sep 2026 10:00:00 -0300',
  textPlain: text,
  textHtml: '',
  metadata: Object.assign({
    'message-id': '<r1@klixar.com>',
    'in-reply-to': '<a1b2c3@amitrixlabs.com>',
    references: '<a1b2c3@amitrixlabs.com>',
  }, meta || {}),
}, over);
const kind = (text, over, meta) => {
  const c = classify(inbound(text, over, meta));
  return c.classification + (c.opt_out_keyword ? ':' + c.opt_out_keyword : '');
};

// The case that matters most: our own email says "reply 'no'", and every reply quotes it.
t.check("a positive reply quoting our email is NOT an opt-out, though the quote says 'no'",
  kind('Sounds useful, please send the recording.' + GMAIL_QUOTE), 'reply');
t.check('... because only what they wrote is read', freshText('Sounds useful.' + GMAIL_QUOTE), 'Sounds useful.');
t.check('... and an unmarked copy of the opt-out line is stripped too', kind('Yes please.\n\n' + OPT), 'reply');

[
  ['No' + GMAIL_QUOTE, 'opt-out:no'],
  ['No thanks.', 'opt-out:no'],
  ['no.', 'opt-out:no'],
  ['Hi Abdullah,\nNo, not for us.', 'opt-out:no'],
  ['Hi Abdullah, no thanks.', 'opt-out:no'],
  ['Please remove me from your list.', 'opt-out:remove'],
  ['unsubscribe', 'opt-out:unsubscribe'],
  ['Stop emailing us.', 'opt-out:stop'],
  ['Não, obrigado.', 'opt-out:no'],
  ['Nie, dziękuję.', 'opt-out:no'],
  ['Hayır, teşekkürler.', 'opt-out:no'],
  ['Nem, köszönöm.', 'opt-out:no'],
  ['Nu, mulțumesc.', 'opt-out:no'],
  ['We have no chatbot today. Tell me more.', 'reply'],
  ['Nova sounds good, when can we talk?', 'reply'],
  ['Notice: we are interested.', 'reply'],
  ['Ne zaman konuşabiliriz?', 'reply'],
].forEach(([text, want]) => t.check(JSON.stringify(text.split('\n')[0].slice(0, 40)) + ' -> ' + want, kind(text), want));

t.check("Gmail's two-line quote header is cut at its first line",
  freshText('Yes.\n\nOn Mon, 14 Sep 2026 at 21:05, Abdullah Amir <\nabdullah@amitrixlabs.com> wrote:\n' + quoted(OURS)), 'Yes.');
t.check('a Spanish quote header is recognised',
  kind('Sí, me interesa.\n\nEl lun, 14 sept 2026 a las 21:05, Abdullah Amir (<abdullah@amitrixlabs.com>) escribió:\n' + quoted(OURS)), 'reply');
t.check("Outlook's From:/Sent: block is recognised",
  freshText('Interested.\n\nFrom: Abdullah Amir <abdullah@amitrixlabs.com>\nSent: Monday, September 14, 2026 9:05 PM\nTo: info\nSubject: q\n\n' + OURS), 'Interested.');
t.check('an HTML-only reply is read too',
  kind('', { textHtml: '<div>No thanks</div><div class="gmail_quote">On Mon, 14 Sep 2026, Abdullah Amir wrote:<blockquote>' + OPT + '</blockquote></div>' }), 'opt-out:no');

t.check('an out-of-office is not a reply, even when it says "no access"',
  kind('I am out of office with no access to email.', {}, { 'auto-submitted': 'auto-replied' }), 'auto-reply');
t.check('... also recognised by subject', kind('Back Monday.', { subject: 'Automatic reply: question about your protocol docs' }), 'auto-reply');
t.check('"Auto-Submitted: no" is a person', kind('Tell me more.', {}, { 'auto-submitted': 'no' }), 'reply');

const ndr = classify(inbound(
  'Delivery to the following recipient failed permanently:\n\n    hello@klixar.com\n\n' +
  '----- Original message -----\nMessage-ID: <a1b2c3@amitrixlabs.com>\nFrom: abdullah@amitrixlabs.com',
  { from: 'Mail Delivery Subsystem <mailer-daemon@zoho.com>', subject: 'Undelivered Mail Returned to Sender' },
  { 'in-reply-to': '', references: '' }));
t.check('a bounce is a bounce, found by sender and subject', ndr.classification, 'bounce');
t.check("... and carries the original Message-ID from the NDR body, to match the send", ndr.thread_ids, ['<a1b2c3@amitrixlabs.com>']);
t.check('... and the failed address, never our own', ndr.mentioned_addrs, ['hello@klixar.com']);

const c = classify(inbound('Tell me more.', {}, { references: '<x@a.com> <a1b2c3@amitrixlabs.com>' }));
t.check('thread ids: In-Reply-To then References, de-duplicated', c.thread_ids, ['<a1b2c3@amitrixlabs.com>', '<x@a.com>']);
t.check('reply metadata', [c.message_id, c.received_at, c.from_addr, c.from_domain, c.freemail, c.own_domain],
  ['<r1@klixar.com>', '2026-09-15T13:00:00.000Z', 'hello@klixar.com', 'klixar.com', false, false]);
t.check('a gmail sender is flagged freemail (never matched or blocklisted by domain)',
  classify(inbound('hi', { from: 'X <someone@gmail.com>' })).freemail, true);
t.check('optOutKeyword on an empty body is null', optOutKeyword(''), null);
const node = runForEachItem(CLASSIFY, [{ json: inbound('Stop.') }], {}, ownDomain);
t.check('the shipped node body wraps the classification as payload', node[0].json.payload.classification, 'opt-out');

// --- operator notification ------------------------------------------------------
const NOTIFY = path.join(__dirname, 'code_notify.js');
const withOperator = (op) => (s) => s.replace('__OPERATOR_EMAIL__', JSON.stringify(op));
const row = {
  notify: true, classification: 'opt-out', opt_out_keyword: 'stop', company_name: 'KLIXAR',
  domain: 'klixar.com', country: 'Argentina', fit_score: 74, contact_email: 'hello@klixar.com',
  from_addr: 'hello@klixar.com', subject: 'Re: question', received_at: '2026-09-15T13:00:00.000Z',
  matched_by: 'thread', body_excerpt: 'Stop emailing us.', blocklisted: ['klixar.com'], lead_id: 7,
};
let n = runForEachItem(NOTIFY, [{ json: row }], {}, withOperator('ops@example.com'))[0].json;
t.check('an opt-out notifies the operator', [n.notify, n.notify_to, n.subject], [true, 'ops@example.com', '[Nova Scout] OPT-OUT from KLIXAR']);
t.check('... saying what matched and what was blocklisted', /matched "stop"\. Blocklisted: klixar\.com\./.test(n.text), true);
t.check('... and what they wrote', /Stop emailing us\./.test(n.text), true);
n = runForEachItem(NOTIFY, [{ json: row }], {}, withOperator(''))[0].json;
t.check('no operator address configured -> no email, nothing else changes', n.notify, false);
n = runForEachItem(NOTIFY, [{ json: Object.assign({}, row, { notify: false }) }], {}, withOperator('ops@example.com'))[0].json;
t.check('a message already recorded once -> no second notification', n.notify, false);
n = runForEachItem(NOTIFY, [{ json: Object.assign({}, row, { classification: 'reply', opt_out_keyword: null }) }], {}, withOperator('ops@example.com'))[0].json;
t.check('a plain reply is labelled REPLY', [n.subject, /follow-ups are stopped/.test(n.text)], ['[Nova Scout] REPLY from KLIXAR', true]);

t.done();

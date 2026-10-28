// Unit tests for the Mailbox Watch Code nodes:
//   code_mirror_sent.js    -- Sent folder -> mailbox_sent rows (the ceiling's input)
//   code_classify_reply.js -- INBOX message -> reply / opt-out / auto-reply / bounce
//   code_detect_signal.js  -- deterministic positive/neutral signal on a reply's own text
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

// Since 2026-10-09 the shipped Classify Inbound node is two files: the
// operator-command library (code_operator_command.js above its own 'Node body'
// marker) and then this one, because the command has to be read before the
// classification (Section 9, Workflow 7). The node-body test below therefore
// gets the same two-part source build_workflow.py ships, or `operatorCommand`
// would be undefined -- which is exactly the kind of wiring a unit test on one
// file alone cannot see.
const fs = require('fs');
const SIGNATURE = 'Abdullah Amir\nFounder, Amitrix Labs\n+923178485713';
const operatorLib = fs.readFileSync(path.join(__dirname, 'code_operator_command.js'), 'utf8')
  .split('// Node body')[0]
  .replace('__SIGNATURE__', JSON.stringify(SIGNATURE))
  .replace('__MAX_URLS__', '1');
const classifyNode = (s) => operatorLib + '\n' + ownDomain(s);

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
// The operator's own notification address is exempt the same way the own domain is.
const OP = 'operator@example.net';
t.check("a reply notification to the operator's own address does not count toward the ceiling",
  sentRows([sent({ to: 'Nova Scout <Operator@Example.NET>' })], OP).rows[0].external, false);
t.check('... the address from settings is compared case-insensitively too',
  sentRows([sent({ to: OP })], 'OPERATOR@example.net').rows[0].external, false);
t.check('... the operator plus an own-domain colleague still warms nothing',
  sentRows([sent({ to: OP, cc: 'ops@amitrixlabs.com' })], OP).rows[0].external, false);
t.check('... but copy a prospect in and it counts',
  sentRows([sent({ to: OP, cc: 'hello@klixar.com' })], OP).rows[0].external, true);
t.check('only that exact address is exempt -- someone else on its domain counts',
  sentRows([sent({ to: 'someone.else@example.net' })], OP).rows[0].external, true);
t.check('no operator address configured (NULL or empty) -> it counts, as before',
  [sentRows([sent({ to: OP })], null).rows[0].external, sentRows([sent({ to: OP })], '').rows[0].external], [true, true]);
const batch = [{ json: sent() }, { json: sent({ to: OP }, { 'message-id': '<note@amitrixlabs.com>' }) }];
const mirrored = runOnceForAll(MIRROR, [{ json: { operator_email: OP } }], { 'Sent Folder': batch }, ownDomain);
t.check('the shipped node body: messages from the trigger, the address from Load Settings, ONE payload item',
  [mirrored.length, mirrored[0].json.payload.rows.map((r) => [r.message_id, r.external])],
  [1, [['<abc123@amitrixlabs.com>', true], ['<note@amitrixlabs.com>', false]]]);

// --- inbound classification -----------------------------------------------------
const CLASSIFY = path.join(__dirname, 'code_classify_reply.js');
const { classify, freshText, optOutKeyword, bounceKind, BOUNCE_PERMANENT, BOUNCE_TRANSIENT, STATUS_CODE } = extractFunctions(
  CLASSIFY, '// Node body',
  ['classify', 'freshText', 'optOutKeyword', 'bounceKind', 'BOUNCE_PERMANENT', 'BOUNCE_TRANSIENT', 'STATUS_CODE'], ownDomain);

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

// DMARC reports -- the two real ones Mailbox Watch recorded, as stored in
// inbound_messages (subject, sender, the text it read). Microsoft's asks to
// "remove" an address from the rua tag; on 2026-09-24 it was recorded with
// opt_out_keyword 'remove'.
const NO_THREAD = { 'in-reply-to': '', references: '', 'message-id': '<r@report.example>' };
const GOOGLE_DMARC = { from: 'noreply-dmarc-support@google.com',
  subject: 'Report domain: amitrixlabs.com Submitter: google.com Report-ID: 17022299783852949353' };
const MS_DMARC = { from: 'dmarcreport@microsoft.com',
  subject: '[Preview] Report Domain: amitrixlabs.com Submitter: enterprise.protection.outlook.com Report-ID: 0a8dc2598f5e458fa04240ca471d7654' };
const MS_DMARC_TEXT = 'This is a DMARC aggregate report from Microsoft Corporation. For Emails received between ' +
  '2026-09-22 00:00:00 UTC to 2026-09-23 00:00:00 UTC.\n\nYou\'re receiving this email because you have included ' +
  'your email address in the \'rua\' tag of your DMARC record in DNS for amitrixlabs.com. Please remove your email ' +
  'address from the \'rua\' tag if you don\'t want to receive this email.\n\nPlease do not respond to this e-mail. ' +
  'This mailbox is not monitored and you will not receive a response. For any feedback/suggestions, kindly mail to ' +
  'dmarcreportfeedback@microsoft.com.\n\nMicrosoft respects your privacy. Review';
const dmarc = (text, over, meta) => {
  const c = classify(inbound(text, over, Object.assign({}, NO_THREAD, meta || {})));
  return [c.classification, c.opt_out_keyword, c.spam_complaint, c.bounce_kind, c.dmarc_report];
};
const MACHINE = ['auto-reply', null, false, null, true];
t.check("Google's real DMARC aggregate report (empty body) is machine mail", dmarc('', GOOGLE_DMARC), MACHINE);
t.check("Microsoft's real report is NOT an opt-out, though it says \"Please remove\"", dmarc(MS_DMARC_TEXT, MS_DMARC), MACHINE);
t.check("... nor when a prospect's own mail server sends it (it would match that lead by domain)",
  dmarc(MS_DMARC_TEXT, Object.assign({}, MS_DMARC, { from: 'dmarc-reports@klixar.com' })), MACHINE);
t.check('... nor a bounce when it comes from postmaster@ (the brake counts bounces)',
  dmarc(MS_DMARC_TEXT, Object.assign({}, MS_DMARC, { from: 'postmaster@klixar.com' })), MACHINE);
t.check('a DMARC failure report (ARF auth-failure) quoting our email is machine mail, not "no"',
  dmarc('Feedback-Type: auth-failure\nAuth-Failure: dmarc\n\nNo\n' + OURS, { from: 'dmarc@isp.example', subject: 'FW: question' }), MACHINE);
t.check('"DMARC aggregate report" in a subject is recognised too',
  dmarc('', { from: 'reports@mimecast.example', subject: 'DMARC Aggregate Report for amitrixlabs.com' }), MACHINE);
t.check('a person who mentions DMARC still opts out with "No thanks"',
  kind('No thanks.', { subject: 'Re: DMARC report setup' }), 'opt-out:no');
t.check('... and "remove" in a reply about a report domain is still an opt-out',
  kind('Please remove me. Our report domain is elsewhere.'), 'opt-out:remove');
t.check('... and an ordinary reply carries dmarc_report false', classify(inbound('Tell me more.')).dmarc_report, false);

const ndr = classify(inbound(
  'Delivery to the following recipient failed permanently:\n\n    hello@klixar.com\n\n' +
  '----- Original message -----\nMessage-ID: <a1b2c3@amitrixlabs.com>\nFrom: abdullah@amitrixlabs.com',
  { from: 'Mail Delivery Subsystem <mailer-daemon@zoho.com>', subject: 'Undelivered Mail Returned to Sender' },
  { 'in-reply-to': '', references: '' }));
t.check('a bounce is a bounce, found by sender and subject', ndr.classification, 'bounce');
t.check("... and carries the original Message-ID from the NDR body, to match the send", ndr.thread_ids, ['<a1b2c3@amitrixlabs.com>']);
t.check('... and the failed address, never our own', ndr.mentioned_addrs, ['hello@klixar.com']);
t.check('... and is a permanent one ("failed permanently")', ndr.bounce_kind, 'permanent');
t.check('anything but a bounce carries no bounce kind', classify(inbound('Tell me more.')).bounce_kind, null);

// --- permanent or transient (operator decision 2026-10-24, migration 022) --------
// Only a permanent bounce counts toward the Send safety brake, and a permanent
// one unverifies the contact. The first five are the wording of the five real
// bounces in inbound_messages (addresses swapped for .example ones).
const ZOHO = 'Mail Delivery Subsystem <mailer-daemon@mail.zoho.com>';
const bounce = (subject, text, from) => classify(inbound(text, { from: from || ZOHO, subject: subject },
  { 'in-reply-to': '', references: '' }));
const ZOHO_DELAY = 'This message was created automatically by mail delivery system.\n\nTHIS IS A WARNING MESSAGE ONLY.\n\n' +
  'YOU DO NOT NEED TO RESEND YOUR MESSAGE.\n\nThe original message was received at Tue, 06 Oct 2026 04:45:38 -0700\n' +
  ' from abdullah@amitrixlabs.com [abdullah@amitrixlabs.com] \n\n    ----- The following addresses had fatal errors -----    \n' +
  '   [info@cro-a.example]\n\nMessage will be retried for 4 more day(s)';
const ZOHO_FINAL = (line) => 'This message was created automatically by mail delivery software.\n A message that you sent could ' +
  'not be delivered to one or more of its recipients. This is a permanent error. \n\n' + line;
[
  ['Zoho delay warning (lead 44, 2026-10-06): "fatal errors" yet retried for 4 more days',
    'Mail Delivery Status Notification (Delay)', ZOHO_DELAY, null, 'transient'],
  ['Zoho final report (lead 44, 2026-10-10): an SMTP 421 the report itself calls permanent',
    'Undelivered Mail Returned to Sender', ZOHO_FINAL('info@cro-a.example, ERROR CODE :421 - Host not reachable.'), null, 'permanent'],
  ['Zoho final report (lead 1378): 550 5.4.1',
    'Undelivered Mail Returned to Sender', ZOHO_FINAL('someone@cro-b.example, ERROR CODE :550 - 5.4.1 Recipient address ' +
      'rejected: Access denied. For more information see https://aka.ms/EXOSmtpErrors [OS3PEPF0000003D.JPNP286.PROD.OUTLOOK.COM ' +
      '2026-10-09T00:30:56.592Z 08DF25390417C375]'), null, 'permanent'],
  ['Office 365 NDR (lead 16): "couldn\'t be delivered", no code in the readable part',
    'Undeliverable: Oncology and metabolic disorders', 'Your message to office@cro-c.example couldn\'t be delivered.\n' +
      'office wasn\'t found at cro-c.example.\nAction Required                 Recipient\nUnknown To address',
    'postmaster@cro-c.example', 'permanent'],
  ['Gmail delay: "temporary problem", and its "if the delivery fails permanently" is a promise, not a verdict',
    'Delivery Status Notification (Delay)', 'Delivery incomplete\n\nThere was a temporary problem delivering your message ' +
      'to someone@cro-d.example. Gmail will retry for 46 more hours. You\'ll be notified if the delivery fails permanently.',
    'Mail Delivery Subsystem <mailer-daemon@googlemail.com>', 'transient'],
  ['Gmail failure: "Address not found"',
    'Delivery Status Notification (Failure)', 'Address not found\n\nYour message wasn\'t delivered to someone@cro-d.example ' +
      'because the address couldn\'t be found, or is unable to receive mail.\n\nThe response was:\n\n550 5.1.1 The email ' +
      'account that you tried to reach does not exist.', 'Mail Delivery Subsystem <mailer-daemon@googlemail.com>', 'permanent'],
  ['Exchange delay: its "if the message can\'t be delivered by that time" is a promise too',
    'Delivery delayed:Klixar + sponsor inquiries', 'Delivery is delayed to these recipients or groups:\n\nsomeone@cro-e.example\n\n' +
      'This message hasn\'t been delivered yet. Delivery will continue to be attempted.\n\nThe server will keep trying to ' +
      'deliver this message for the next 1 days, 19 hours and 51 minutes. You\'ll be notified if the message can\'t be ' +
      'delivered by that time.', 'postmaster@cro-e.example', 'transient'],
  ['Postfix delay: "THIS IS A WARNING ONLY"',
    'Delayed Mail (still being retried)', '####### THIS IS A WARNING ONLY.  YOU DO NOT NEED TO RESEND YOUR MESSAGE. #######\n\n' +
      'Delivery to the following recipient has been delayed:\n\n    someone@cro-f.example\n\nReporting-MTA: dns; mx.example\n' +
      'Action: delayed\nStatus: 4.4.1', 'MAILER-DAEMON@mx.example', 'transient'],
  ['Postfix final after giving up on a 4.x.x: the verdict ("could not be delivered", wrapped) beats its "Temporary failure"',
    'Undelivered Mail Returned to Sender', "I'm sorry to have to inform you that your message could not\nbe delivered to " +
      'one or more recipients.\n\n<someone@cro-f.example>: host mx.cro-f.example said: 451 4.3.0 Temporary failure, ' +
      'please try again later', 'MAILER-DAEMON@mx.example', 'permanent'],
  ['RFC 3464 fields alone: Action: failed with a 4.4.7 (gave up) is permanent',
    'Returned mail', 'Final-Recipient: rfc822; someone@cro-g.example\nAction: failed\nStatus: 4.4.7', null, 'permanent'],
  ['RFC 3464 fields alone: Action: delayed is transient',
    'Returned mail', 'Final-Recipient: rfc822; someone@cro-g.example\nAction: delayed\nStatus: 4.4.1', null, 'transient'],
  ['no wording, a 5.x.x code -> permanent', 'Returned mail', "Remote Server returned '550 5.1.10 RESOLVER.ADR.RecipientNotFound'",
    null, 'permanent'],
  ['no wording, only a 4.x.x code -> transient', 'Returned mail', 'Remote server said: 452 4.2.2 Mailbox full', null, 'transient'],
  ['no wording, a 4.x.x and then a 5.x.x -> permanent: any 5.x.x decides',
    'Returned mail', 'First attempt: 421 4.4.2 timeout.\nLast attempt: 550 5.1.1 User unknown', null, 'permanent'],
  ['an IP address is not a status code: 10.5.1.1 beside a 4.x.x stays transient', 'Returned mail',
    'Connected to 10.5.1.1; it said 421 4.4.2 timeout', null, 'transient'],
  ['no wording and no code at all -> permanent (a delay always says it is one; the brake errs towards pausing)',
    'Undelivered Mail Returned to Sender', 'The mail system\n\n<someone@cro-h.example>: host mx.cro-h.example refused to talk to me',
    'MAILER-DAEMON@mx.example', 'permanent'],
].forEach(([label, subject, text, from, want]) => t.check(label, bounce(subject, text, from).bounce_kind, want));

// Our own email rides along in many reports. Its words must never decide: the
// three LOCKED templates (Section 9), quoted, change no verdict.
const TEMPLATES = '> We have yet to be properly introduced, but I\'m Abdullah Amir and I\'m the Founder of Amitrix Labs.\n' +
  '> That\'s precisely what we do at Amitrix Labs. We build AI agents for CRO websites that answer sponsor and visitor ' +
  'questions, collect their details, and turn more of your visitors into qualified leads, so you win more studies.\n' +
  '> Do you have time over the next week or two to learn more? Let me know what works for you, and I\'ll send a ' +
  'calendar invite along accordingly.\n> Just bringing this back to the top of your inbox in case it got buried. Every ' +
  'sponsor who visits Klixar\'s website is a potential study, and we help make sure those visits turn into conversations.\n' +
  '> I\'ll keep this short and won\'t follow up again after this. If turning more of Klixar\'s website visitors into ' +
  'qualified leads is ever on your list, just reply and I\'ll send over the details.\n> Cheers!\n> Abdullah Amir';
STATUS_CODE.lastIndex = 0;
t.check('our quoted templates hold no bounce wording of either kind, and no status code',
  [BOUNCE_PERMANENT.filter((re) => re.test(TEMPLATES)).map(String), BOUNCE_TRANSIENT.filter((re) => re.test(TEMPLATES)).map(String),
    STATUS_CODE.test(TEMPLATES)], [[], [], false]);
STATUS_CODE.lastIndex = 0;
t.check('... so a delay quoting them stays a delay',
  bounce('Mail Delivery Status Notification (Delay)', ZOHO_DELAY + '\n\n' + TEMPLATES).bounce_kind, 'transient');
t.check('... and a bare 5.x.x quoting them stays permanent',
  bounce('Returned mail', '550 5.1.1 User unknown\n\n' + TEMPLATES).bounce_kind, 'permanent');

// --- spam complaints: the Send safety brake (Section 9, operator decision 2026-10-19) ---
const complaint = (text, over, meta) => {
  const x = classify(inbound(text, over, meta));
  return [x.classification, x.opt_out_keyword, x.spam_complaint];
};
const arf = classify(inbound(
  'This is an email abuse report for an email message received from IP 136.143.188.1.\n\n' +
  'Feedback-Type: abuse\nUser-Agent: Yahoo!-Mail-Feedback/2.0\nVersion: 1\n\n' +
  'Message-ID: <a1b2c3@amitrixlabs.com>\nFrom: abdullah@amitrixlabs.com',
  { from: 'Yahoo! Mail AntiSpam Feedback <feedback@arf.mail.yahoo.com>', subject: 'FW: Klixar + sponsor inquiries' },
  { 'content-type': 'multipart/report; report-type=feedback-report; boundary="x"', 'in-reply-to': '', references: '' }));
t.check('a feedback-loop report (ARF) is an opt-out and a spam complaint',
  [arf.classification, arf.opt_out_keyword, arf.spam_complaint, arf.complaint], ['opt-out', 'spam-report', true, 'feedback-report']);
t.check("... and carries the complained-about Message-ID from its body, to match the send", arf.thread_ids, ['<a1b2c3@amitrixlabs.com>']);
t.check('... found by its "Feedback-Type: abuse" part too, without the content-type',
  complaint('Feedback-Type: abuse\nVersion: 1', { from: 'fbl@isp.example' }, { 'in-reply-to': '', references: '' }),
  ['opt-out', 'spam-report', true]);
t.check('... and never read as a bounce, though it is a multipart/report',
  classify(inbound('Feedback-Type: abuse', { from: 'MAILER-DAEMON@isp.example', subject: 'Undelivered Mail' },
    { 'content-type': 'multipart/report; report-type=feedback-report' })).classification, 'opt-out');
[
  ['This is spam.', ['opt-out', 'spam-complaint', true]],
  ["It's spam, plain and simple.", ['opt-out', 'spam-complaint', true]],
  ['Your email is pure spam.', ['opt-out', 'spam-complaint', true]],
  ['Stop spamming me.', ['opt-out', 'stop', true]],
  ['Please stop spamming us', ['opt-out', 'stop', true]],
  ['You people are spammers.', ['opt-out', 'spam-complaint', true]],
  ['I have reported you as spam.', ['opt-out', 'spam-complaint', true]],
  ['Marked this as spam.', ['opt-out', 'spam-complaint', true]],
  ["I'm reporting you to Zoho.", ['opt-out', 'spam-complaint', true]],
  ['We do not accept unsolicited emails.', ['opt-out', 'spam-complaint', true]],
  ['Remove me, this is junk.', ['opt-out', 'remove', true]],
  // Not complaints: an interested prospect, a question, a filter's doing, someone else's spam.
  ['It went to my spam folder, but yes, let us talk next week.', ['reply', null, false]],
  ['Is this spam? Who gave you my address?', ['reply', null, false]],
  ['Your email was marked as spam by our filter. Interested though.', ['reply', null, false]],
  ['We get so much spam from vendors, but this one is relevant.', ['reply', null, false]],
  ['This is not spam to us -- tell me more.', ['reply', null, false]],
].forEach(([text, want]) => t.check(JSON.stringify(text.slice(0, 44)) + ' -> ' + JSON.stringify(want), complaint(text), want));
t.check('"this is spam" in the QUOTED thread is not a complaint -- only what they wrote counts',
  complaint('Sounds good, call me Tuesday.\n\nOn Mon, 14 Sep 2026 at 21:05, Abdullah Amir <abdullah@amitrixlabs.com> wrote:\n' +
    quoted('Is this spam? This is spam.')), ['reply', null, false]);
t.check('an out-of-office that mentions spam filters is not a complaint',
  complaint('I am away. This is spam-filtered.', {}, { 'auto-submitted': 'auto-replied' }), ['auto-reply', null, false]);
t.check('a bounce quoting "this is spam" from a filter is a bounce, not a complaint',
  complaint('Rejected: this is spam (550 5.7.1).', { from: 'mailer-daemon@zoho.com', subject: 'Undelivered Mail Returned to Sender' }),
  ['bounce', null, false]);
t.check('a normal reply carries spam_complaint false, not undefined',
  classify(inbound('Tell me more.')).spam_complaint, false);

const c = classify(inbound('Tell me more.', {}, { references: '<x@a.com> <a1b2c3@amitrixlabs.com>' }));
t.check('thread ids: In-Reply-To then References, de-duplicated', c.thread_ids, ['<a1b2c3@amitrixlabs.com>', '<x@a.com>']);
t.check('reply metadata', [c.message_id, c.received_at, c.from_addr, c.from_domain, c.freemail, c.own_domain],
  ['<r1@klixar.com>', '2026-09-15T13:00:00.000Z', 'hello@klixar.com', 'klixar.com', false, false]);
t.check('a gmail sender is flagged freemail (never matched or blocklisted by domain)',
  classify(inbound('hi', { from: 'X <someone@gmail.com>' })).freemail, true);
t.check('optOutKeyword on an empty body is null', optOutKeyword(''), null);
const node = runForEachItem(CLASSIFY, [{ json: inbound('Stop.') }], {}, classifyNode);
t.check('the shipped node body wraps the classification as payload', node[0].json.payload.classification, 'opt-out');
// The operator fork: one message, read twice, in the shipped node. A prospect's
// message is not a command; the thread headers now ride along on the payload
// (migration 017), because a reply draft has to thread to them.
t.check('... and reads the same message for an operator command -- a prospect reply is not one',
  node[0].json.command, { is_command: false, command: null, code: null, replacement: null, contact_lines: null,
    spf_pass: false, dkim_pass: false, refused_reason: null });
const threaded = runForEachItem(CLASSIFY, [{ json: inbound('Tell me more.', {},
  { 'in-reply-to': '<seed@amitrixlabs.com>', references: '<seed@amitrixlabs.com>' }) }], {}, classifyNode);
t.check('the payload carries the thread headers as received (migration 017)',
  [threaded[0].json.payload.in_reply_to, threaded[0].json.payload.references_raw,
    threaded[0].json.payload.thread_ids],
  ['<seed@amitrixlabs.com>', '<seed@amitrixlabs.com>', ['<seed@amitrixlabs.com>']]);
t.check('... and NULL, not an empty string, when the message carried none',
  [classify(inbound('hi', {}, { 'in-reply-to': '', references: '' })).in_reply_to,
    classify(inbound('hi', {}, { 'in-reply-to': '', references: '' })).references_raw], [null, null]);
// The hazard this fork exists for: the operator's own "APPROVE NS-..." is read
// as a command from the same text the classifier reads, so it can never reach
// the opt-out path. The decision about WHO sent it is SQL's (the operator
// address is runtime data), which is why is_command is all this file reports.
const opcmd = runForEachItem(CLASSIFY, [{ json: inbound('APPROVE NS-2345678ABC' + GMAIL_QUOTE, {},
  { 'authentication-results': 'mx; dkim=pass; spf=pass' }) }], {}, classifyNode);
t.check('an operator APPROVE in the same mailbox is recognised as a command, with its code',
  [opcmd[0].json.command.is_command, opcmd[0].json.command.command, opcmd[0].json.command.code,
    opcmd[0].json.command.spf_pass, opcmd[0].json.command.dkim_pass, opcmd[0].json.command.refused_reason],
  [true, 'APPROVE', 'NS-2345678ABC', true, true, null]);

// --- positive-signal detection -----------------------------------------------
// Deterministic (build rule 3, no model), and does NOT touch classification --
// it only decides what Build Notification shows, and only for classification
// 'reply'. code_classify_reply.js above, and its precedence, are untouched;
// this reuses `classify`'s own output.
const DETECT = path.join(__dirname, 'code_detect_signal.js');
const { positiveSignal } = extractFunctions(DETECT, '// Node body', ['positiveSignal']);

[
  ['Yes, this sounds great -- send it over, we are interested!', 'positive', 'yes'],
  ["Sure, let's talk next week.", 'positive', 'sure'],
  ['Can you tell me more about how this fits our workflow?', 'neutral', null],
  ['We already use a similar tool, not a fit right now.', 'neutral', null],
].forEach(([text, wantSignal, wantKeyword]) => {
  const c = classify(inbound(text + GMAIL_QUOTE));
  t.check(JSON.stringify(text.slice(0, 40)) + ' -> ' + wantSignal,
    [c.classification, positiveSignal(c)], ['reply', { signal: wantSignal, signal_keyword: wantKeyword }]);
});

const removed = classify(inbound('Yes, please remove me.' + GMAIL_QUOTE));
t.check("'Yes, please remove me.' is an OPT-OUT (classification untouched), and its 'yes' is never scored",
  [removed.classification, removed.opt_out_keyword, positiveSignal(removed)],
  ['opt-out', 'remove', { signal: 'neutral', signal_keyword: null }]);

const ooo = classify(inbound('Yes, I am out of office until Monday.', {}, { 'auto-submitted': 'auto-replied' }));
t.check('an auto-reply is never scored, even if it happens to say "yes"',
  [ooo.classification, positiveSignal(ooo)], ['auto-reply', { signal: 'neutral', signal_keyword: null }]);

t.check('a bounce is never scored', positiveSignal({ classification: 'bounce', body_excerpt: 'Yes indeed' }),
  { signal: 'neutral', signal_keyword: null });

const detectNode = runForEachItem(DETECT,
  [{ json: { classification: 'reply', body_excerpt: 'Sounds great, send it over.' } }], {});
t.check('the shipped node body merges the signal fields onto the row, not a wrapper',
  [detectNode[0].json.classification, detectNode[0].json.signal, detectNode[0].json.signal_keyword],
  ['reply', 'positive', 'sounds great']);

// --- operator notification ------------------------------------------------------
// The address arrives on Record Inbound's row, read from the settings table at
// runtime -- nothing is baked into the node.
const NOTIFY = path.join(__dirname, 'code_notify.js');
const row = {
  notify: true, classification: 'opt-out', opt_out_keyword: 'stop', company_name: 'KLIXAR',
  domain: 'klixar.com', country: 'Argentina', fit_score: 74, contact_email: 'hello@klixar.com',
  from_addr: 'hello@klixar.com', subject: 'Re: question', received_at: '2026-09-15T13:00:00.000Z',
  matched_by: 'thread', body_excerpt: 'Stop emailing us.', blocklisted: ['klixar.com'], lead_id: 7,
  operator_email: 'ops@example.com',
};
const note = (over) => runForEachItem(NOTIFY, [{ json: Object.assign({}, row, over) }], {})[0].json;
let n = note({});
t.check('an opt-out notifies the operator, at the address on the row', [n.notify, n.notify_to, n.subject], [true, 'ops@example.com', '[Nova Scout] OPT-OUT from KLIXAR']);
t.check('... saying what matched and what was blocklisted', /matched "stop"\. Blocklisted: klixar\.com\./.test(n.text), true);
t.check('... and what they wrote', /Stop emailing us\./.test(n.text), true);
t.check('no operator address in settings (NULL) -> no email, nothing else changes', note({ operator_email: null }).notify, false);
t.check('... an empty one likewise', note({ operator_email: '' }).notify, false);
t.check('a value that is not one address -> no email, rather than a send that fails',
  [note({ operator_email: 'ops at example.com' }).notify, note({ operator_email: 'ops@localhost' }).notify,
    note({ operator_email: 'a@example.com, b@example.com' }).notify], [false, false, false]);
t.check('the address is trimmed and lower-cased', note({ operator_email: ' Ops@Example.COM ' }).notify_to, 'ops@example.com');
t.check('a message already recorded once -> no second notification', note({ notify: false }).notify, false);
n = note({ classification: 'reply', opt_out_keyword: null });
t.check('a plain reply is labelled REPLY', [n.subject, /follow-ups are stopped/.test(n.text)], ['[Nova Scout] REPLY from KLIXAR', true]);

// Bounces by kind (migration 022): the operator is told which one it was, and
// whether the contact was unverified.
n = note({ classification: 'bounce', opt_out_keyword: null, bounce_kind: 'permanent', contact_unverified: true });
t.check('a permanent bounce: labelled BOUNCE, counted by the brake, the contact unverified',
  [n.subject, /bounced permanently/.test(n.text), /counts toward the safety brake/.test(n.text),
    /now marked unverified \(contacts\.verified = false\)/.test(n.text)], ['[Nova Scout] BOUNCE from KLIXAR', true, true, true]);
n = note({ classification: 'bounce', opt_out_keyword: null, bounce_kind: 'permanent', contact_unverified: false });
t.check('... and when the bounce does not name the address on file, it says the contact was not changed',
  [/was not changed/.test(n.text), /now marked unverified/.test(n.text)], [true, false]);
n = note({ classification: 'bounce', opt_out_keyword: null, bounce_kind: 'transient', contact_unverified: false });
t.check('a delay warning: labelled DELIVERY DELAY, not counted by the brake, contact untouched',
  [n.subject, /does not count toward the safety brake/.test(n.text), /unverified/.test(n.text)],
  ['[Nova Scout] DELIVERY DELAY from KLIXAR', true, false]);

// --- notification content: reply text, the positive/neutral flag, and assets ----
n = note({ classification: 'reply', opt_out_keyword: null, body_excerpt: 'Yes, sounds great -- send it over.',
  signal: 'positive', signal_keyword: 'yes' });
t.check('a positive-flagged reply notifies, showing the flag and the reply text inline',
  [n.notify, /Signal:\s+POSITIVE \(matched "yes"\)/.test(n.text), /Yes, sounds great -- send it over\./.test(n.text)],
  [true, true, true]);

n = note({ classification: 'reply', opt_out_keyword: null, body_excerpt: 'Can you tell me more about pricing?',
  signal: 'neutral', signal_keyword: null });
t.check('a neutral reply notifies too, showing NEUTRAL and no false POSITIVE',
  [n.notify, /Signal:\s+NEUTRAL$/m.test(n.text), n.text.includes('POSITIVE')],
  [true, true, false]);

n = note({ classification: 'opt-out', opt_out_keyword: 'remove', body_excerpt: 'Yes, please remove me.',
  signal: 'neutral', signal_keyword: null });
t.check("an opt-out never shows a Signal line, even for a reply body that says \"yes\" (would be a false positive)",
  [n.notify, n.text.includes('Signal:'), n.text.includes('POSITIVE')], [true, false, false]);

n = note({});
t.check('every notification links the one-pager, and no recording link exists (not built yet)',
  [n.text.includes('nova-one-pager.docx'), /recording/i.test(n.text)], [true, false]);
t.check('the asset section is one line per asset -- today, exactly one',
  n.text.match(/^  [^:]+: https:\/\//gm).length, 1);

// The reply link by company type (2026-10-09): a site or SMO gets the site one-pager.
const assetUrl = (type) => (note({ company_type: type }).text.match(/^  [^:]+: (https:\/\/\S+)$/m) || [])[1];
t.check('a CRO reply links the CRO one-pager', assetUrl('CRO'), 'https://raw.githubusercontent.com/abdullahamir2308/nova-scout/main/nova-one-pager.docx');
t.check('a site reply links the site one-pager', assetUrl('site'), 'https://raw.githubusercontent.com/abdullahamir2308/nova-scout/main/nova-one-pager-sites.docx');
t.check('an SMO reply links the site one-pager', assetUrl('SMO'), 'https://raw.githubusercontent.com/abdullahamir2308/nova-scout/main/nova-one-pager-sites.docx');
t.check('no company type (an unmatched sender, an old row) falls back to the CRO one-pager', assetUrl(undefined), assetUrl('CRO'));

t.done();

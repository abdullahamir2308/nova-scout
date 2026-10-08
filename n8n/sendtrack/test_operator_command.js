// Unit tests for code_operator_command.js -- the operator-approval parser and
// its authentication gate (Section 9, Workflow 7).
//
// What is proven here is everything that can be decided from one message alone:
// the verb, the code, EDIT's replacement, and SPF/DKIM from the real shape of
// the Authentication-Results header Zoho writes. The three checks that need the
// database -- is the sender the operator's address, does the code exist, is it
// unused and unexpired -- are SQL, and the dry run proves those end to end.
//
// run: node n8n/sendtrack/test_operator_command.js

const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, 'code_operator_command.js');
// Everything above the 'Node body' marker, with the build's substitutions
// applied -- the same source build_workflow.py prepends to Classify Inbound and
// ships as the Decide Command node. Slicing at the marker is what lets the node
// body itself ($input, which cannot run outside n8n) live in the same file.
const SIGNATURE = 'Abdullah Amir\nFounder, Amitrix Labs\n+923178485713';
const lib = fs.readFileSync(SRC, 'utf8')
  .split('// Node body')[0]
  .replace('__SIGNATURE__', JSON.stringify(SIGNATURE))
  .replace('__MAX_URLS__', '1');
const M = new Function(
  lib + '\nreturn { operatorCommand, parseCommand, authResults, stripComments, commandDecision, framedEdit };')();

// The header exactly as it arrived on the Pharmahungary reply (n8n execution
// 2336) -- tabs between the method results, a parenthesised SPF comment.
const REAL_AUTH =
  'mx.zohomail.com;\tdkim=pass;\tspf=pass (zohomail.com: domain of pharmahungary.com ' +
  'designates 185.51.190.242 as permitted sender)  smtp.mailfrom=andras.nogradi@pharmahungary.com; ' +
  'dmarc=pass(p=none dis=none)  header.from=pharmahungary.com';

let pass = 0;
let fail = 0;

function ok(name, cond, got) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (got === undefined ? '' : '  got: ' + JSON.stringify(got))); }
}

function msg(auth, body) {
  return { metadata: auth === null ? {} : { 'authentication-results': auth } };
}

function run(auth, body) {
  return M.operatorCommand(msg(auth, body), body);
}

console.log('\nAuthentication-Results parsing');
{
  const a = M.authResults(REAL_AUTH);
  ok('the real Zoho header reads spf=pass and dkim=pass', a.spf === true && a.dkim === true, a);

  ok('no header at all is not a pass',
    M.authResults(undefined).spf === false && M.authResults(undefined).dkim === false);
  ok('an empty header is not a pass',
    M.authResults('').spf === false && M.authResults('').dkim === false);

  ok('spf=fail is not a pass', M.authResults('mx;\tdkim=pass;\tspf=fail').spf === false);
  ok('dkim=fail is not a pass', M.authResults('mx;\tdkim=fail;\tspf=pass').dkim === false);
  ok('spf=softfail is not a pass', M.authResults('mx; spf=softfail; dkim=pass').spf === false);
  ok('spf=neutral is not a pass', M.authResults('mx; spf=neutral; dkim=pass').spf === false);
  ok('dkim missing entirely is not a pass', M.authResults('mx; spf=pass').dkim === false);

  // The comment-stripping guard: text inside a parenthesised comment must never
  // be read as a method result.
  const spoofComment = 'mx.zohomail.com; spf=fail (sender claims spf=pass dkim=pass); dkim=fail';
  const sc = M.authResults(spoofComment);
  ok('"spf=pass" inside a parenthesised comment does not count',
    sc.spf === false && sc.dkim === false, sc);

  // Two headers joined (n8n gives one string per header name, but a relay can
  // fold several): a method that fails anywhere fails.
  const two = 'mx.a; spf=pass; dkim=pass\nmx.b; spf=fail; dkim=pass';
  ok('two headers disagreeing about SPF is not a pass', M.authResults(two).spf === false);
  ok('two headers agreeing is a pass',
    M.authResults('mx.a; spf=pass; dkim=pass\nmx.b; spf=pass; dkim=pass').spf === true);

  ok('case does not matter', M.authResults('MX; DKIM=PASS; SPF=PASS').spf === true);
  ok('spaces around = do not matter', M.authResults('mx; dkim = pass; spf = pass').dkim === true);
  // A method name that merely ends in "spf" must not be read as spf.
  ok('arc-authentication-style method names are not read as spf',
    M.authResults('mx; iprev=pass; dkim=pass').spf === false);
}

console.log('\nCommand parsing');
{
  const CODE = 'NS-ABCDEFGHJK';

  let r = M.parseCommand('APPROVE ' + CODE);
  ok('APPROVE <code>', r.is_command && r.command === 'APPROVE' && r.code === CODE && r.replacement === null, r);

  r = M.parseCommand('approve ' + CODE.toLowerCase());
  ok('lower case verb and code are accepted and upper-cased',
    r.command === 'APPROVE' && r.code === CODE, r);

  r = M.parseCommand('REJECT ' + CODE);
  ok('REJECT <code>', r.command === 'REJECT' && r.code === CODE && r.replacement === null, r);

  r = M.parseCommand('EDIT ' + CODE + '\nThanks for getting back to me. Could you send your site URL?');
  ok('EDIT <code> + replacement on the next lines',
    r.command === 'EDIT' && r.code === CODE &&
    r.replacement === 'Thanks for getting back to me. Could you send your site URL?', r);

  r = M.parseCommand('EDIT ' + CODE + ' same line replacement');
  ok('EDIT with the replacement on the same line',
    r.command === 'EDIT' && r.replacement === 'same line replacement', r);

  r = M.parseCommand('APPROVE: ' + CODE);
  ok('a colon after the verb is allowed', r.command === 'APPROVE' && r.code === CODE, r);

  r = M.parseCommand('APPROVE\n' + CODE);
  ok('the code on the line after the verb is found', r.command === 'APPROVE' && r.code === CODE, r);

  // Not commands.
  ok('a plain "no" is not a command', M.parseCommand('no').is_command === false);
  ok('"No, thanks" is not a command', M.parseCommand('No, thanks').is_command === false);
  ok('an ordinary prospect reply is not a command',
    M.parseCommand('Thank you for introducing your services. We are not planning a project.').is_command === false);
  ok('"approved" alone is not the verb APPROVE',
    M.parseCommand('approved, go ahead').code === null);
  ok('a sentence merely containing the word approve is not a command',
    M.parseCommand('Please let me know if you approve of this.').is_command === false);
}

console.log('\nThe five refusals the operator asked to see');
{
  const CODE = 'NS-ABCDEFGHJK';

  // 1. A spoofed sender. This file cannot see the address -- it reports the
  //    sender for SQL to judge -- so what it must NOT do is pre-approve.
  //    `sender_ok` is absent from its output by design.
  const spoof = run(REAL_AUTH, 'APPROVE ' + CODE);
  ok('1. a spoofed sender: this node never asserts the sender is the operator',
    !('sender_ok' in spoof), Object.keys(spoof));

  // 2. A wrong code -- not a code at all. Refused here, with a reason.
  const nocode = run(REAL_AUTH, 'APPROVE please');
  ok('2a. a command with no code is refused',
    nocode.is_command === true && nocode.code === null &&
    nocode.refused_reason === 'no one-time code in the message', nocode);

  // A code of the wrong shape never parses as a code at all (migration 017
  // refuses the shape too, so an unknown code is a database miss, which the dry
  // run proves).
  ok('2b. a malformed code is not read as a code',
    M.parseCommand('APPROVE NS-SHORT').code === null);
  ok('2c. a code using the excluded alphabet (I, L, O, U, 0, 1) is not read as a code',
    M.parseCommand('APPROVE NS-ABCDEFGHI0').code === null);

  // 3 and 4. An expired code and a reused code are database facts
  //    (reply_approval_usable()), not message facts. What this file guarantees
  //    is that it hands the code over unchanged for that check.
  ok('3+4. an expired or reused code is passed through for the database to refuse',
    run(REAL_AUTH, 'APPROVE ' + CODE).code === CODE);

  // 5. A plain "no" from the operator. The whole point: it is not a command, so
  //    it never reaches the approval path -- and because it is not a command,
  //    Record Inbound's prospect branch must be the one that sees it. The dry
  //    run proves the other half: that it is then NOT treated as an opt-out
  //    when it comes from the operator address.
  const plainNo = run(REAL_AUTH, 'no');
  ok('5. a plain "no" from the operator is not a command',
    plainNo.is_command === false && plainNo.command === null && plainNo.code === null, plainNo);
}

console.log('\nFailing closed');
{
  const CODE = 'NS-ABCDEFGHJK';
  let r = run(null, 'APPROVE ' + CODE);
  ok('a command with no Authentication-Results header is refused on SPF',
    r.is_command && r.refused_reason === "the message's authentication results do not show SPF pass", r);

  r = run('mx; spf=pass', 'APPROVE ' + CODE);
  ok('a command whose header shows SPF but not DKIM is refused on DKIM',
    r.refused_reason === "the message's authentication results do not show DKIM pass", r);

  r = run('mx; spf=fail; dkim=pass', 'APPROVE ' + CODE);
  ok('a command whose SPF failed is refused on SPF',
    r.refused_reason === "the message's authentication results do not show SPF pass", r);

  r = run(REAL_AUTH, 'EDIT ' + CODE);
  ok('EDIT with no replacement text is refused',
    r.refused_reason === 'EDIT with no replacement text after the code', r);

  r = run(REAL_AUTH, 'APPROVE ' + CODE);
  ok('a well-formed, authenticated command has no refusal reason from this node',
    r.refused_reason === null && r.spf_pass === true && r.dkim_pass === true, r);

  // A non-command is never given a refusal reason: it is not being refused, it
  // is simply a prospect message.
  ok('a non-command carries no refusal reason', run(null, 'no').refused_reason === null);
}

// ---------------------------------------------------------------------------
// The decision -- Decide Command, with the database facts Load Command Context
// reads: the operator's address, the code's state, and the draft's.
//
// Three outputs, and they are deliberately not the same question:
//   record   -- goes into operator_commands, accepted or refused
//   handled  -- really the operator's mail, so never a prospect reply
//   accepted -- a draft moves
// ---------------------------------------------------------------------------

const OPERATOR = 'abdullah@amitrixlabs.com';
const CODE2 = 'NS-2345678ABC';
const REPLY_BODY = [
  'Hello,',
  '',
  'Thanks for coming back to me. I will confirm the hosting detail and come back on that.',
  '',
  'Would a 48-hour demo on your own material be worth a look?',
  '',
  SIGNATURE,
  '',
  'On Wed, 07 Oct 2026, andras.nogradi@pharmahungary.com wrote:',
  '> We are currently not planning a project.',
].join('\n');

function ctx(over) {
  const body = 'APPROVE ' + CODE2;
  return Object.assign({
    payload: { message_id: '<op-1@amitrixlabs.com>', from_addr: OPERATOR, subject: 'Re: REPLY DRAFTED' },
    command: M.operatorCommand(msg(REAL_AUTH, body), body),
    operator_email: OPERATOR,
    now: '2026-10-09T10:00:00.000Z',
    code_found: true,
    code_used_at: null,
    code_expires_at: '2026-10-11T08:00:00.000Z',
    code_kind: 'reply',
    code_outcome: null,
    draft_id: 261,
    draft_status: 'pending',
    draft_channel: 'email',
    draft_variant: 'reply/D2.BEN-BRIEF',
    draft_body: REPLY_BODY,
  }, over || {});
}

function withBody(body, over) {
  return ctx(Object.assign({ command: M.operatorCommand(msg(REAL_AUTH, body), body) }, over || {}));
}

console.log('\nDecide Command -- the verdict');
{
  let d = M.commandDecision(ctx());
  ok('a clean APPROVE from the operator: recorded, handled, accepted',
    d.record === true && d.handled === true && d.accepted === true && d.refused_reason === null &&
    d.outcome === 'approved' && d.draft_id === 261, d);

  // THE SPOOF. SPF and DKIM pass -- an attacker's own domain can pass its own
  // -- but the address is not the operator's. Recorded as refused, and NOT
  // handled, so it still reaches Record Inbound as the prospect message it may
  // actually be.
  d = M.commandDecision(ctx({ payload: { message_id: '<spoof@evil.example>',
    from_addr: 'andras.nogradi@pharmahungary.com', subject: 'Re: REPLY DRAFTED' } }));
  ok('a spoofed sender with passing SPF/DKIM is refused, logged, and falls through to Record Inbound',
    d.record === true && d.handled === false && d.accepted === false &&
    d.refused_reason === 'not sent from the operator address', d);

  d = M.commandDecision(ctx({ operator_email: null }));
  ok('no operator address configured: nothing is accepted',
    d.accepted === false && d.handled === false &&
    d.refused_reason === 'no operator address is configured (settings.operator_email)', d);

  d = M.commandDecision(ctx({ code_found: false }));
  ok('a wrong code is refused -- and it IS handled, because the operator really sent it',
    d.record === true && d.handled === true && d.accepted === false &&
    d.refused_reason === 'no such one-time code', d);

  d = M.commandDecision(ctx({ code_expires_at: '2026-10-09T09:59:59.000Z' }));
  ok('an expired code is refused, naming when it expired',
    d.accepted === false && /expired at 2026-10-09T09:59:59/.test(d.refused_reason), d);

  d = M.commandDecision(ctx({ code_used_at: '2026-10-09T09:00:00.000Z', code_outcome: 'approved' }));
  ok('a reused code is refused, naming when and how it was used',
    d.accepted === false && /already used at 2026-10-09T09:00:00/.test(d.refused_reason), d);

  d = M.commandDecision(ctx({ draft_status: 'rejected' }));
  ok('a draft that is no longer waiting is refused',
    d.accepted === false && d.refused_reason === 'draft 261 is no longer waiting (it is rejected)', d);

  // A failed SPF on a message that really is from the operator's address: still
  // refused, and still not handled -- the authentication is the gate, not the
  // From line.
  d = M.commandDecision(withBody('APPROVE ' + CODE2, { command: M.operatorCommand(
    msg('mx; dkim=pass; spf=fail', 'APPROVE ' + CODE2), 'APPROVE ' + CODE2) }));
  ok('SPF fail from the operator address: refused and not handled',
    d.accepted === false && d.handled === false && /SPF pass/.test(d.refused_reason), d);

  // THE PLAIN "no". Not command-shaped at all, so nothing is recorded and
  // nothing is handled: Record Inbound sees it, and refuses to match it to a
  // lead because it came from the operator (its own guard).
  d = M.commandDecision(withBody('no'));
  ok('a plain "no" from the operator is not a command: nothing recorded, nothing handled, nothing accepted',
    d.record === false && d.handled === false && d.accepted === false &&
    d.refused_reason === 'not an operator command' && d.outcome === null, d);
  ok('... and it carries no code and no draft to act on', d.code === null && d.command === null);

  d = M.commandDecision(withBody('REJECT ' + CODE2));
  ok('REJECT is accepted and its outcome is rejected', d.accepted === true && d.outcome === 'rejected', d);
}

console.log('\nEDIT -- the operator\'s own words, in the draft\'s own frame');
{
  let d = M.commandDecision(withBody('EDIT ' + CODE2 + ' Thanks Andras. We host in the EU and the data stays there.'));
  ok('EDIT is accepted and its outcome is edited', d.accepted === true && d.outcome === 'edited', d);
  ok('... the greeting and the signature are the draft\'s, not regenerated',
    d.edited_body.indexOf('Hello,\n\nThanks Andras. We host in the EU and the data stays there.\n\n' + SIGNATURE) === 0,
    d.edited_body);
  ok('... and the quoted message being answered is kept verbatim',
    d.edited_body.indexOf('> We are currently not planning a project.') !== -1, d.edited_body);
  ok('... so nothing of the model\'s text survives',
    d.edited_body.indexOf('48-hour demo') === -1 && d.edited_body.indexOf('hosting detail') === -1);

  // Section 5 allows one plain URL. Two is refused -- and the code is NOT spent,
  // so the same code works for a corrected EDIT.
  d = M.commandDecision(withBody('EDIT ' + CODE2 + ' See https://a.example/x and https://b.example/y'));
  ok('an EDIT that would carry two links is refused, and says the code still works',
    d.accepted === false && d.edited_body === null &&
    /carries 2 links/.test(d.refused_reason) && /still good/.test(d.refused_reason), d);
  d = M.commandDecision(withBody('EDIT ' + CODE2 + ' One link is fine: https://a.example/x'));
  ok('... one link is fine', d.accepted === true, d);

  d = M.commandDecision(withBody('EDIT ' + CODE2));
  ok('EDIT with no replacement text is refused', d.accepted === false &&
    d.refused_reason === 'EDIT with no replacement text after the code', d);

  // A draft whose body has no signature at all (nothing this workflow writes,
  // but a hand-edited row could): the frame falls back rather than throwing.
  d = M.commandDecision(withBody('EDIT ' + CODE2 + ' Short answer.', { draft_body: 'Hello,\n\nold text' }));
  ok('a draft with no signature in it still frames the edit, with the signature appended',
    d.accepted === true && d.edited_body === 'Hello,\n\nShort answer.\n\n' + SIGNATURE, d.edited_body);
}

console.log('\n%d passed, %d failed\n', pass, fail);
process.exit(fail === 0 ? 0 : 1);

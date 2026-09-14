// Dry-run SMTP sink: speaks enough SMTP for nodemailer, writes every message it
// is handed to disk, and delivers nothing.
//
//   node smtp_sink.js <outdir> [port]
//
// dryrun.py runs it INSIDE the n8n container (`docker exec -d`), listening on
// loopback only. The dry-run SMTP credential points at 127.0.0.1:2525, so the
// only process that credential can ever reach is this one -- and this process
// never opens an outbound connection of its own.
//
// A RCPT TO whose local part starts with "reject" is refused with 550 5.1.1, so
// the send path's revert branch can be exercised without a real bounce.
const net = require('net');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2] || '/tmp/novascout_sink';
const PORT = Number(process.argv[3] || 2525);
fs.mkdirSync(OUT, { recursive: true });
let seq = fs.readdirSync(OUT).filter((f) => f.endsWith('.eml')).length;

net.createServer((sock) => {
  let buf = '';
  let mode = 'cmd';
  let env = { from: null, rcpt: [] };
  let auth = null;
  let data = [];
  const say = (l) => sock.write(l + '\r\n');

  say('220 novascout-dryrun-sink ESMTP -- captures only, delivers nothing');
  sock.on('error', () => {});
  sock.on('data', (chunk) => {
    buf += chunk.toString('latin1');
    let i;
    while ((i = buf.indexOf('\r\n')) !== -1) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 2);

      if (mode === 'data') {
        if (line === '.') {
          mode = 'cmd';
          seq += 1;
          const file = path.join(OUT, String(seq).padStart(3, '0') + '.eml');
          fs.writeFileSync(file, Buffer.from(data.join('\r\n') + '\r\n', 'latin1'));
          fs.appendFileSync(path.join(OUT, 'log.jsonl'), JSON.stringify({
            seq, file, mail_from: env.from, rcpt_to: env.rcpt, auth, at: new Date().toISOString(),
          }) + '\n');
          data = [];
          env = { from: null, rcpt: [] };
          say('250 2.0.0 Ok: captured as #' + seq + ', not delivered');
        } else {
          data.push(line.startsWith('..') ? line.slice(1) : line);
        }
        continue;
      }
      if (mode === 'auth-plain') { mode = 'cmd'; auth = 'PLAIN'; say('235 2.7.0 Authentication successful'); continue; }
      if (mode === 'auth-user') { mode = 'auth-pass'; say('334 UGFzc3dvcmQ6'); continue; }
      if (mode === 'auth-pass') { mode = 'cmd'; auth = 'LOGIN'; say('235 2.7.0 Authentication successful'); continue; }

      const parts = line.split(' ');
      const verb = parts[0].toUpperCase();
      if (verb === 'EHLO') {
        sock.write('250-novascout-dryrun-sink\r\n250-AUTH PLAIN LOGIN\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n');
      } else if (verb === 'HELO') {
        say('250 novascout-dryrun-sink');
      } else if (verb === 'AUTH') {
        const mech = (parts[1] || '').toUpperCase();
        if (mech === 'PLAIN' && parts[2]) { auth = 'PLAIN'; say('235 2.7.0 Authentication successful'); }
        else if (mech === 'PLAIN') { mode = 'auth-plain'; say('334 '); }
        else if (mech === 'LOGIN' && parts[2]) { mode = 'auth-pass'; say('334 UGFzc3dvcmQ6'); }
        else if (mech === 'LOGIN') { mode = 'auth-user'; say('334 VXNlcm5hbWU6'); }
        else say('504 5.5.4 Unrecognized authentication type');
      } else if (verb === 'MAIL') {
        env.from = line.replace(/^MAIL FROM:\s*/i, '');
        say('250 2.1.0 Ok');
      } else if (verb === 'RCPT') {
        const to = line.replace(/^RCPT TO:\s*/i, '');
        if (/^<?reject/i.test(to)) {
          say('550 5.1.1 ' + to + ': Recipient address rejected: User unknown (dry-run sink)');
        } else {
          env.rcpt.push(to);
          say('250 2.1.5 Ok');
        }
      } else if (verb === 'DATA') {
        if (!env.rcpt.length) say('554 5.5.1 No valid recipients');
        else { mode = 'data'; say('354 End data with <CR><LF>.<CR><LF>'); }
      } else if (verb === 'RSET') {
        env = { from: null, rcpt: [] };
        say('250 2.0.0 Ok');
      } else if (verb === 'NOOP') {
        say('250 2.0.0 Ok');
      } else if (verb === 'QUIT') {
        say('221 2.0.0 Bye');
        sock.end();
      } else {
        say('502 5.5.2 Command not recognized');
      }
    }
  });
}).listen(PORT, '127.0.0.1', () => {
  console.log('dry-run sink on 127.0.0.1:' + PORT + ', capturing to ' + OUT);
});

// Dry-run HTTP sink for the Send Reply lane (Section 9, Workflow 7): stands in
// for the smtp-send sidecar's POST /send, answers in the SAME nodemailer shape
// smtp_send_server.py returns (see that file's `send()`), and never opens a
// socket of its own -- there is no SMTP conversation here at all, so this sink
// cannot reach a real mailbox even by accident.
//
//   node http_sink.js <outdir> [port]
//
// reply_dryrun.py runs it INSIDE the n8n container (`docker exec -d`),
// listening on loopback only, and the dry-run Send Reply variant's Config
// points send_url at 127.0.0.1:<port> instead of the real smtp-send:8766 --
// the one extra difference build_workflow.py's dry_variant() asserts for this
// lane, on top of credentials, Config and the schedule trigger.
//
// A `to` address whose local part starts with "reject" is answered as a
// recipient-side SMTP failure, so Revert Claim's branch can be exercised
// without needing a real bounce.
const http = require('http');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2] || '/tmp/novascout_http_sink';
const PORT = Number(process.argv[3] || 18766);
fs.mkdirSync(OUT, { recursive: true });
let seq = 0;

function send(res, code, obj) {
  const body = Buffer.from(JSON.stringify(obj), 'utf-8');
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': body.length });
  res.end(body);
}

http.createServer((req, res) => {
  if (req.method !== 'POST' || req.url.split('?')[0] !== '/send') {
    send(res, 404, { error: 'not found' });
    return;
  }
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    let body;
    try {
      body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
    } catch (e) {
      send(res, 200, { accepted: [], rejected: [], messageId: null, response: null,
        error: 'refused before sending: body is not JSON (' + e.message + ')' });
      return;
    }
    seq += 1;
    fs.appendFileSync(path.join(OUT, 'log.jsonl'), JSON.stringify({
      seq, to: body.to, subject: body.subject, text: body.text,
      in_reply_to: body.in_reply_to, references: body.references, at: new Date().toISOString(),
    }) + '\n');

    if (/^<?reject/i.test(String(body.to || ''))) {
      send(res, 200, { accepted: [], rejected: [String(body.to)], messageId: null,
        response: '550 5.1.1 ' + body.to + ': mailbox unavailable (dry-run sink)',
        error: 'rejected by server: 550 5.1.1 ' + body.to + ': mailbox unavailable (dry-run sink)' });
      return;
    }
    const messageId = '<dryrun-sink-' + seq + '@amitrixlabs.com>';
    send(res, 200, { accepted: [String(body.to)], rejected: [], messageId: messageId,
      response: '250 accepted by dry-run sink', error: null });
  });
}).listen(PORT, '127.0.0.1', () => {
  console.log('dry-run HTTP sink on 127.0.0.1:' + PORT + ', capturing to ' + OUT);
});

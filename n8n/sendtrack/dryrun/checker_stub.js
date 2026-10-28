// Dry-run stand-in for the imap-health checker (2026-10-28): GET /check answers
// with the JSON in <answer-file>, re-read on every request, so a dry run can say
// exactly what "the Sent folder" holds -- the real listing plus a simulated gap,
// a checker that failed, a folder that lost a message. It opens no IMAP session.
//
//   node checker_stub.js <answer-file> [port]
//
// sent_gap_dryrun.py runs it INSIDE the n8n container (`docker exec -d`),
// listening on loopback only, and points the dry-run variants' Config
// checker_url at it -- the one value it changes, asserted there.
const http = require('http');
const fs = require('fs');

const file = process.argv[2];
const port = Number(process.argv[3] || 8799);
if (!file) {
  console.error('usage: node checker_stub.js <answer-file> [port]');
  process.exit(2);
}

http.createServer((req, res) => {
  if (req.method !== 'GET' || req.url.split('?')[0] !== '/check') {
    res.writeHead(404);
    res.end();
    return;
  }
  let body;
  try {
    body = fs.readFileSync(file, 'utf8');
    JSON.parse(body);
  } catch (e) {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'stub has no answer: ' + e.message }));
    return;
  }
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}).listen(port, '127.0.0.1', () => {
  fs.writeFileSync(file + '.up', String(process.pid));
});

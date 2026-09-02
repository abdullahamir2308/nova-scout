// Unit test for the sandbox-safe URL resolver, checked against Node's real
// WHATWG URL so the hand-rolled version cannot quietly drift from correct.
//
// The resolver is sliced out of code_fetch.js rather than copied here, so this
// tests the exact code that build_workflow.py embeds in the workflow.
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'code_fetch.js'), 'utf8');
const start = src.indexOf('function parseAbsolute');
const end = src.indexOf('function discoverLinks');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('could not slice parseAbsolute/resolveUrl out of code_fetch.js');
}
const fn = new Function(src.slice(start, end) + '\nreturn { parseAbsolute, resolveUrl };')();
const { resolveUrl } = fn;

const base = 'https://www.example.com/a/b/page.html';
const cases = [
  ['/about', 'https://www.example.com/about'],
  ['about-us', 'https://www.example.com/a/b/about-us'],
  ['../team', 'https://www.example.com/a/team'],
  ['./services', 'https://www.example.com/a/b/services'],
  ['https://other.com/x', 'https://other.com/x'],
  ['//cdn.example.com/y', 'https://cdn.example.com/y'],
  ['/quem-somos?utm=1', 'https://www.example.com/quem-somos'],
  ['/equipe#top', 'https://www.example.com/equipe'],
  ['#anchor', null],
  ['mailto:a@b.com', null],
  ['tel:+123', null],
  ['javascript:void(0)', null],
  ['', null],
];

let pass = 0;
for (const [href, expect] of cases) {
  const got = resolveUrl(href, base);
  const gotStr = got ? got.origin + got.pathname : null;
  // Compare against Node's own URL for the non-null cases.
  let ref = null;
  if (expect !== null) {
    const u = new URL(href, base);
    ref = u.origin + u.pathname;
  }
  const ok = gotStr === expect && (expect === null || ref === expect);
  pass += ok ? 1 : 0;
  console.log(
    `${ok ? 'PASS' : 'FAIL'}  ${JSON.stringify(href).padEnd(24)} -> ${gotStr}` +
      (ok ? '' : `   (expected ${expect}, node says ${ref})`)
  );
}

// Port handling and host matching
const p = resolveUrl('/x', 'http://sub.example.com:8080/');
console.log(`\nport case: origin=${p.origin} hostname=${p.hostname} path=${p.pathname}`);
const okPort = p.origin === 'http://sub.example.com:8080' && p.hostname === 'sub.example.com';
pass += okPort ? 1 : 0;
console.log(`${okPort ? 'PASS' : 'FAIL'}  port stripped from hostname but kept in origin`);

const total = cases.length + 1;
console.log(`\n${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);

// Unit test for the chatbot detection regexes, using real widget snippets.
// Chatbot detection is a Code node with regex, never an LLM call (build rule 3),
// and it feeds Workflow 3's "already has a chatbot" hard disqualifier — so a
// false positive silently deletes a real lead.
//
// The pattern table is sliced out of code_fetch.js rather than copied here, so
// this tests the exact table that build_workflow.py embeds in the workflow.
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'code_fetch.js'), 'utf8');
const start = src.indexOf('const CHATBOT_PATTERNS = [');
const end = src.indexOf('];', start);
if (start < 0 || end < 0) {
  throw new Error('could not slice CHATBOT_PATTERNS out of code_fetch.js');
}
const { CHATBOT_PATTERNS } = new Function(
  src.slice(start, end + 2) + '\nreturn { CHATBOT_PATTERNS };'
)();

function detect(html) {
  const v = CHATBOT_PATTERNS.filter(([, re]) => re.test(html)).map(([n]) => n);
  return v.length ? v.join(', ') : null;
}

// Real embed snippets as the vendors publish them.
const fixtures = [
  ['Intercom', `<script>window.intercomSettings={app_id:"abc123"};</script><script>(function(){var w=window;var d=document;var s=d.createElement('script');s.src='https://widget.intercom.io/widget/abc123';})()</script>`],
  ['Drift', `<script>!function(){var t=window.driftt=window.drift;}();drift.load('abc123');</script><script src="https://js.driftt.com/include/abc/def.js"></script>`],
  ['Tidio', `<script src="//code.tidio.co/abcdefghijklmnop.js" async></script>`],
  ['Tawk', `<script type="text/javascript">var Tawk_API=Tawk_API||{};(function(){var s1=document.createElement("script");s1.src='https://embed.tawk.to/5f0/default';})();</script>`],
  ['Crisp', `<script type="text/javascript">window.$crisp=[];window.CRISP_WEBSITE_ID="abc";(function(){d=document;s=d.createElement("script");s.src="https://client.crisp.chat/l.js";})();</script>`],
  ['HubSpot Chat', `<script type="text/javascript" id="hs-script-loader" async defer src="//js.hs-scripts.com/1234567.js"></script>`],
  ['LiveChat', `<script>window.__lc=window.__lc||{};__lc.license=12345678;(function(){var lc=document.createElement('script');lc.src='https://cdn.livechatinc.com/tracking.js';})()</script>`],
  ['Zendesk', `<script id="ze-snippet" src="https://static.zdassets.com/ekr/snippet.js?key=abc"></script>`],
  ['Zendesk', `<script>window.zEmbed||function(e,t){/*zopim*/}(document,'zopim');</script>`],
];

let pass = 0;
for (const [expect, html] of fixtures) {
  const got = detect(html);
  const ok = got !== null && got.split(', ').includes(expect);
  pass += ok ? 1 : 0;
  console.log(`${ok ? 'PASS' : 'FAIL'}  expect=${expect.padEnd(13)} got=${got}`);
}

// Negatives: must NOT fire on ordinary marketing/analytics markup.
const negatives = [
  `<script src="https://www.googletagmanager.com/gtag/js?id=G-X"></script>`,
  `<script src="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/js/bootstrap.bundle.min.js"></script>`,
  `<p>Contact our team by live chat during business hours, or call us.</p>`,
  `<a href="/services">Clinical services</a><script src="/wp-includes/js/jquery.js"></script>`,
];
for (const html of negatives) {
  const got = detect(html);
  const ok = got === null;
  pass += ok ? 1 : 0;
  console.log(`${ok ? 'PASS' : 'FAIL'}  expect=none          got=${got}`);
}

const total = fixtures.length + negatives.length;
console.log(`\n${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);

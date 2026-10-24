// Unit test for chat-widget detection, using real widget snippets.
// Chatbot detection is a Code node with regex, never an LLM call (build rule 3),
// and it feeds Workflow 3's "already has a chatbot" hard disqualifier — so a
// false positive silently deletes a real lead.
//
// The detection code (the pattern table, the HubSpot loader check and
// detectChat) is sliced out of code_fetch.js rather than copied here, so this
// tests the exact code that build_workflow.py embeds in the workflow. The one
// thing replaced is get(), the node's HTTP call: it serves the real HubSpot
// loaders saved in fixtures/chat_loaders/ instead of the network.
//
// NOVASCOUT_CODE_FETCH points it at another copy of code_fetch.js (the mutation
// test uses that).
const fs = require('fs');
const path = require('path');

const srcPath = process.env.NOVASCOUT_CODE_FETCH || path.join(__dirname, 'code_fetch.js');
const src = fs.readFileSync(srcPath, 'utf8');

function slice(from, to) {
  const a = src.indexOf(from);
  const b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error('could not slice ' + JSON.stringify(from) + ' out of code_fetch.js');
  return src.slice(a, b);
}
const detection =
  slice('const CHATBOT_PATTERNS = [', '\nconst LINKEDIN_RE') +
  '\n' +
  slice('// The text a pattern matched', '// Leads are fetched CONCURRENTLY.');

const FIX = path.join(__dirname, 'fixtures');
function makeDetector(loaderTable) {
  const fetched = [];
  const get = async (url, o) => {
    fetched.push(url);
    // As HubSpot does (measured 2026-10-24): an HTML Accept header gets 406.
    const accept = (o && o.accept) || 'text/html,application/xhtml+xml';
    if (!/javascript|\*\/\*/.test(accept)) return { statusCode: 406, body: '' };
    const l = loaderTable[url];
    if (l === undefined) return { statusCode: 404, body: '' };
    if (l === 'throw') throw new Error('getaddrinfo ENOTFOUND');
    return { statusCode: l.status, body: l.file ? fs.readFileSync(path.join(FIX, l.file), 'utf8') : '' };
  };
  const { detectChat, CHATBOT_PATTERNS } = new Function(
    'get',
    detection + '\nreturn { detectChat, CHATBOT_PATTERNS };'
  )(get);
  return { detectChat, CHATBOT_PATTERNS, fetched };
}

let pass = 0;
let total = 0;
function check(label, ok, detail) {
  total++;
  pass += ok ? 1 : 0;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${ok ? '' : '  -- ' + detail}`);
}

(async () => {
  // 1. Real pages, captured live on 2026-10-24: each widget, and each lookalike
  //    that names the vendor or its domain without running its chat.
  const samples = JSON.parse(fs.readFileSync(path.join(FIX, 'chat_samples.json'), 'utf8'));
  for (const s of samples) {
    const d = makeDetector(s.loaders);
    const got = (await d.detectChat(s.html)).vendors;
    check(`real ${s.expect.length ? 'widget   ' : 'lookalike'} ${s.name.padEnd(42)} -> ${got.join(', ') || 'none'}`,
      JSON.stringify(got) === JSON.stringify(s.expect), `expected ${s.expect.join(', ') || 'none'}`);
  }

  // 2. Embed snippets as the vendors publish them, for vendors no lead's site
  //    showed live.
  const published = [
    ['Drift', `<script>!function(){var t=window.driftt=window.drift;}();drift.load('abc123');</script><script src="https://js.driftt.com/include/abc/def.js"></script>`],
    ['Crisp', `<script type="text/javascript">window.$crisp=[];window.CRISP_WEBSITE_ID="abc";(function(){d=document;s=d.createElement("script");s.src="https://client.crisp.chat/l.js";})();</script>`],
    ['LiveChat', `<script>window.__lc=window.__lc||{};__lc.license=12345678;(function(){var lc=document.createElement('script');lc.src='https://cdn.livechatinc.com/tracking.js';})()</script>`],
    ['Zendesk', `<script>window.zEmbed||function(e,t){/*zopim*/}(document,'zopim');</script>`],
    ['HubSpot Chat', `<script type="text/javascript" id="hubspot-messages-loader" src="https://js.usemessages.com/conversations-embed.js" data-hsjs-portal="123"></script>`],
  ];
  for (const [expect, html] of published) {
    const got = (await makeDetector({}).detectChat(html)).vendors;
    check(`vendor snippet ${expect.padEnd(12)} -> ${got.join(', ') || 'none'}`, got.includes(expect), `expected ${expect}`);
  }

  // 3. Ordinary markup with no vendor at all.
  const plain = [
    `<script src="https://www.googletagmanager.com/gtag/js?id=G-X"></script>`,
    `<script src="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/js/bootstrap.bundle.min.js"></script>`,
    `<p>Contact our team by live chat during business hours, or call us.</p>`,
    `<a href="/services">Clinical services</a><script src="/wp-includes/js/jquery.js"></script>`,
  ];
  for (const html of plain) {
    const got = (await makeDetector({}).detectChat(html)).vendors;
    check(`plain markup -> ${got.join(', ') || 'none'}`, got.length === 0, 'expected none');
  }

  // 4. The loader check's own rules.
  const imperial = samples.find((s) => s.name === 'hubspot-tracking-imperialcrs');
  const ace = samples.find((s) => s.name === 'hubspot-chat-ace-research');
  const aceUrl = Object.keys(ace.loaders)[0];

  let d = makeDetector({ [Object.keys(imperial.loaders)[0]]: 'throw' });
  let r = await d.detectChat(imperial.html);
  check('a loader that cannot be fetched is unknown, and does not disqualify',
    r.vendors.length === 0 && r.evidence.hubspot_loaders[0].chat === null, JSON.stringify(r));

  const dead = samples.find((s) => s.name === 'hubspot-dead-portal-indusbiotech');
  r = await makeDetector(dead.loaders).detectChat(dead.html);
  check('a portal HubSpot says is gone (410) is "no chat", not unknown', r.evidence.hubspot_loaders[0].chat === false, JSON.stringify(r.evidence));

  r = await makeDetector({ [Object.keys(imperial.loaders)[0]]: { status: 503 } }).detectChat(imperial.html);
  check('any other answer (503) is unknown, and does not disqualify',
    r.vendors.length === 0 && r.evidence.hubspot_loaders[0].chat === null, JSON.stringify(r.evidence));

  d = makeDetector(ace.loaders);
  r = await d.detectChat(ace.html + ace.html.replace('https:', ''));
  check('the same portal referenced twice is fetched once', d.fetched.length === 1, d.fetched.join(' '));

  const three = ['111', '222', '333'].map((p) => `<script src="//js.hs-scripts.com/${p}.js"></script>`).join('');
  d = makeDetector({});
  await d.detectChat(three);
  check('at most two portal loaders are fetched per lead', d.fetched.length === 2, d.fetched.join(' '));

  d = makeDetector(ace.loaders);
  r = await d.detectChat(ace.html);
  check('the evidence names the loader that has chat',
    /js\.hs-scripts\.com\/3272060\.js injects conversations-embed/.test(r.evidence['HubSpot Chat'] || ''), JSON.stringify(r.evidence));

  d = makeDetector(imperial.loaders);
  r = await d.detectChat(published[4][1] + imperial.html);
  check('a direct chat embed is decided without fetching a loader', d.fetched.length === 0 && r.vendors[0] === 'HubSpot Chat', JSON.stringify(r));

  const prelude = samples.find((s) => s.name === 'hubspot-tracking-regional-prelude');
  d = makeDetector(prelude.loaders);
  await d.detectChat(prelude.html);
  check('a regional loader (js-na2) is fetched from its regional host',
    d.fetched.length === 1 && d.fetched[0] === 'https://js-na2.hs-scripts.com/45025171.js', d.fetched.join(' '));

  const sci = samples.find((s) => s.name === 'intercom-scientist');
  r = await makeDetector({}).detectChat(sci.html);
  check('the evidence quotes the matched text', /intercomSettings/.test(r.evidence.Intercom || ''), JSON.stringify(r.evidence));

  console.log(`\n${pass}/${total} passed`);
  process.exit(pass === total ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

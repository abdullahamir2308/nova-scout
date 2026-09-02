// Runs an n8n Code-node body standalone with a mocked n8n context, so the exact
// JS that ships inside enrichment.json is tested before it is embedded.
//
// Run directly (`node harness.js`) for a live smoke test: it fetches the real
// sites in fixtures/test_leads.json over the network and writes fetch_out.json.
// That is an integration check, not a unit test — the test_*.js files are the
// offline ones.
const fs = require('fs');
const path = require('path');

async function httpRequest(opts) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts.timeout || 12000);
  try {
    const res = await fetch(opts.url, {
      method: opts.method || 'GET',
      headers: opts.headers || {},
      redirect: 'follow',
      signal: ctrl.signal,
    });
    const body = await res.text();
    return { statusCode: res.status, body, request: { uri: { href: res.url } } };
  } finally {
    clearTimeout(t);
  }
}

// Globals that n8n's Code sandbox does NOT provide (verified against the live
// instance). They are shadowed as undefined parameters here so code relying on
// them fails in this harness too, instead of passing locally and silently
// no-op-ing in production the way `new URL()` did.
const SANDBOX_MISSING = ['URL', 'URLSearchParams', 'fetch', 'AbortController'];

async function runCodeNode(file, inputItems) {
  const src = fs.readFileSync(file, 'utf8');
  const ctx = { helpers: { httpRequest } };
  const $input = { all: () => inputItems };
  const fn = new Function(
    '$input',
    ...SANDBOX_MISSING,
    '"use strict"; return (async () => {' + src + '})()'
  );
  return await fn.call(ctx, $input, ...SANDBOX_MISSING.map(() => undefined));
}

module.exports = { runCodeNode, httpRequest };

if (require.main === module) {
  (async () => {
    const leads = JSON.parse(
      fs.readFileSync(path.join(__dirname, 'fixtures', 'test_leads.json'), 'utf8')
    );
    const items = leads.map((l) => ({ json: l }));
    const t0 = Date.now();
    const out = await runCodeNode(path.join(__dirname, 'code_fetch.js'), items);
    console.log(`fetched ${out.length} leads in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`);
    for (const o of out) {
      const r = o.json;
      console.log(
        `${r.domain.padEnd(24)} ok=${String(r.fetch_ok).padEnd(5)} pages=${r.pages_fetched} ` +
          `chars=${String(r.prompt_chars).padStart(6)} thin=${String(r.text_too_thin).padEnd(5)} ` +
          `bot=${r.has_chatbot ? r.chatbot_vendor : '-'} li=${r.linkedin_candidates.length} ` +
          `disc=${r.discovered.length} errs=${r.fetch_errors.length}`
      );
    }
    // Scratch output of the live run, for eyeballing what the model would see. Gitignored.
    fs.writeFileSync(
      path.join(__dirname, 'fetch_out.json'),
      JSON.stringify(out.map((o) => o.json), null, 2)
    );
  })();
}

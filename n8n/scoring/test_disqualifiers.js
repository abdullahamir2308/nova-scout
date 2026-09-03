// Unit test for the Workflow 3 hard disqualifiers.
//
// These rules decide whether a lead is ever contacted, and two of them are the
// exact places Section 9 warns the naive implementation gets it wrong:
//   - null must never be read as evidence (employee_estimate is null on 85% of
//     leads because extraction correctly refuses to guess);
//   - "unreachable" over-counts, and a confirmed HTTP response proves the site
//     exists, so it must not be treated as "no functioning website".
//
// Sliced out of code_evaluate.js rather than copied, so this tests the exact
// code build_workflow.py embeds in the workflow.
const path = require('path');
const { extractFunctions, runner } = require('./harness');

const { hardDisqualifiers, siteEvidence } = extractFunctions(
  path.join(__dirname, 'code_evaluate.js'),
  '// Weighted factors (Section 9, Workflow 3)',
  ['hardDisqualifiers', 'siteEvidence']
);

const t = runner('Workflow 3 hard disqualifiers');

function codes(lead) {
  return hardDisqualifiers(lead).map((r) => r.code);
}

// A lead that passes everything, used as the base for each case.
function ok(over) {
  return Object.assign(
    {
      domain: 'example.com',
      company_name: 'Example CRO',
      country: 'India',
      has_chatbot: false,
      employee_estimate: null,
      blocklisted: false,
      raw_extraction: { enrichment_status: 'ok', is_cro: true, fetch: { ok: true, errors: [] } },
    },
    over || {}
  );
}

function withRaw(over) {
  return ok({ raw_extraction: Object.assign({ enrichment_status: 'ok', is_cro: true, fetch: { ok: true, errors: [] } }, over) });
}

// --- the happy path -------------------------------------------------------
t.check('a clean lead is not disqualified', codes(ok()), []);

// --- null is never evidence ----------------------------------------------
t.check('null employee_estimate does NOT trigger the >500 rule',
  codes(ok({ employee_estimate: null })), []);
t.check('undefined employee_estimate does NOT trigger the >500 rule',
  codes(ok({ employee_estimate: undefined })), []);
t.check('null is_cro does NOT trigger the not-a-CRO rule',
  codes(withRaw({ is_cro: null })), []);
t.check('missing is_cro does NOT trigger the not-a-CRO rule',
  codes(ok({ raw_extraction: { enrichment_status: 'ok', fetch: { ok: true, errors: [] } } })), []);

// --- employee count -------------------------------------------------------
t.check('exactly 500 employees is not over the cutoff',
  codes(ok({ employee_estimate: 500 })), []);
t.check('501 employees disqualifies',
  codes(ok({ employee_estimate: 501 })), ['enterprise_scale']);
t.check('a real enterprise CRO disqualifies (gvkbio.com, 3412)',
  codes(ok({ employee_estimate: 3412 })), ['enterprise_scale']);
t.check('a small headcount does not disqualify',
  codes(ok({ employee_estimate: 12 })), []);

// --- the other deterministic rules ---------------------------------------
t.check('explicit is_cro false disqualifies',
  codes(withRaw({ is_cro: false })), ['not_a_cro']);
t.check('an existing chatbot disqualifies',
  codes(ok({ has_chatbot: true, chatbot_vendor: 'Tawk' })), ['has_chatbot']);
t.check('blocklisted domain disqualifies',
  codes(ok({ blocklisted: true, blocklist_reason: 'competitor' })), ['blocklist']);

// --- "no functioning website": the WAF distinction ------------------------
//
// Real fetch.errors from the live corpus. A rung that returned any HTTP status
// proves a server answered; a rung that failed at the transport layer does not.
const DEAD_DNS = [
  'https / -> getaddrinfo ENOTFOUND laxai.net',
  'https+www / -> getaddrinfo ENOTFOUND www.laxai.net',
  'https+insecure / -> getaddrinfo ENOTFOUND laxai.net',
  'http / -> getaddrinfo ENOTFOUND laxai.net',
];
const DEAD_TIMEOUT = [
  'https / -> timeout of 12000ms exceeded',
  'https+www / -> timeout of 20000ms exceeded',
  'https+insecure / -> timeout of 20000ms exceeded',
  'http / -> timeout of 20000ms exceeded',
];
const WAF_403 = [
  'https / -> HTTP 403',
  'https+www / -> HTTP 403',
  'https+insecure / -> HTTP 403',
  'http / -> HTTP 403',
];
const WAF_MIXED = [
  'https / -> timeout of 12000ms exceeded',
  'https+www / -> HTTP 403',
  'https+insecure / -> HTTP 403',
  'http / -> HTTP 403',
];
const CERT_THEN_403 = [
  "https / -> Hostname/IP does not match certificate's altnames: Host: antaea.com.",
  "https+www / -> Hostname/IP does not match certificate's altnames: Host: www.antaea.com.",
  'https+insecure / -> HTTP 403',
  'http / -> HTTP 403',
];

function unreachable(errors) {
  return ok({
    raw_extraction: {
      enrichment_status: 'unreachable',
      is_cro: null,
      fetch: { ok: false, errors: errors },
    },
  });
}

t.check('siteEvidence: DNS failure on every rung is no_response',
  siteEvidence({ ok: false, errors: DEAD_DNS }), 'no_response');
t.check('siteEvidence: timeout on every rung is no_response',
  siteEvidence({ ok: false, errors: DEAD_TIMEOUT }), 'no_response');
t.check('siteEvidence: a 403 anywhere is an http_block',
  siteEvidence({ ok: false, errors: WAF_403 }), 'http_block');
t.check('siteEvidence: one 403 among timeouts still proves the host answered',
  siteEvidence({ ok: false, errors: WAF_MIXED }), 'http_block');
t.check('siteEvidence: a cert failure is not an HTTP response, but the later 403 is',
  siteEvidence({ ok: false, errors: CERT_THEN_403 }), 'http_block');
t.check('siteEvidence: a fetch that succeeded is an http_block, not a failure',
  siteEvidence({ ok: true, errors: [] }), 'http_block');
t.check('siteEvidence: no fetch record at all is unknown',
  siteEvidence(null), 'unknown');
t.check('siteEvidence: no errors recorded is unknown, not a failure',
  siteEvidence({ ok: false, errors: [] }), 'unknown');

t.check('a genuinely dead domain (DNS) IS disqualified',
  codes(unreachable(DEAD_DNS)), ['no_website']);
t.check('a genuinely dead domain (timeout) IS disqualified',
  codes(unreachable(DEAD_TIMEOUT)), ['no_website']);
t.check('a WAF-blocked site is NOT disqualified -- a 403 proves it exists',
  codes(unreachable(WAF_403)), []);
t.check('a WAF block among timeouts is NOT disqualified',
  codes(unreachable(WAF_MIXED)), []);
t.check('antaea.com (cert failures, then 403) is NOT disqualified',
  codes(unreachable(CERT_THEN_403)), []);

// A JS shell: the server answered 200, there was just nothing to read. The site
// functions; we could not parse it. That is not "no functioning website".
t.check('a JS-shell site (no_readable_text, fetch ok) is NOT disqualified',
  codes(ok({
    raw_extraction: {
      enrichment_status: 'no_readable_text',
      is_cro: null,
      fetch: { ok: true, errors: [], prompt_chars: 16 },
    },
  })), []);

// --- multiple reasons -----------------------------------------------------
t.check('every matching rule is recorded, most-decisive first',
  codes(ok({
    blocklisted: true,
    has_chatbot: true,
    employee_estimate: 900,
    raw_extraction: { enrichment_status: 'ok', is_cro: false, fetch: { ok: true, errors: [] } },
  })),
  ['blocklist', 'not_a_cro', 'enterprise_scale', 'has_chatbot']);

// --- the reason text is for a human --------------------------------------
const chatbotReason = hardDisqualifiers(ok({ has_chatbot: true, chatbot_vendor: 'Intercom' }))[0].text;
t.check('the chatbot reason names the vendor',
  chatbotReason.indexOf('Intercom') !== -1, true);
const scaleReason = hardDisqualifiers(ok({ employee_estimate: 3412 }))[0].text;
t.check('the enterprise reason quotes the actual headcount',
  scaleReason.indexOf('3412') !== -1, true);
const croReason = hardDisqualifiers(withRaw({ is_cro: false, company_type: 'RTSM/IRT vendor' }))[0].text;
t.check('the not-a-CRO reason quotes what the site actually looked like',
  croReason.indexOf('RTSM/IRT vendor') !== -1, true);
const deadReason = hardDisqualifiers(unreachable(DEAD_DNS))[0].text;
t.check('the dead-site reason quotes the actual first failure',
  deadReason.indexOf('ENOTFOUND') !== -1, true);

t.done();

// Pick Best Contact — n8n Code node (Run Once for Each Item).
//
// Ranks an Apollo people-search response and decides whether any result is
// worth spending an enrichment credit on.
//
// Section 12's ICP names the buyer: "CRO founder, Managing Director, or BD
// Director". This node encodes that as an ordered preference rather than a
// filter, so a company whose only senior person is titled something unexpected
// still surfaces -- but never outranks an actual founder.
//
// Three outcomes, and the difference between the last two is the whole point of
// the node:
//
//   retry     the call itself failed (rate limit, timeout, outage). Nothing is
//             written to `contacts`; the lead stays 'scored' and the next run
//             tries again. Recording "no match" here would be a lie about what
//             Apollo said.
//   no match  Apollo answered, and nothing at this domain clears the bar. A
//             tombstone row is written so the lead is not re-queried every run.
//   match     one person clears the bar; the enrichment call is worth a credit.
//
// EVERY outcome now also carries an `attempt` -- what to record in
// `contact_attempts` (migration 015), which is what stops a lead nobody can
// find from taking a slot in every batch for ever. Two values:
//
//   exhausted  every source has answered and there is no contact. Permanent.
//              That is "Apollo answered, nobody there", and also a refusal this
//              account can never get past: the Free plan's 403 API_INACCESSIBLE
//              is a property of the plan, not a five-minute outage, and it will
//              answer identically on every call until somebody upgrades.
//   refused    anything else -- a 429, a 5xx, a timeout, a response that is not
//              the shape we expect. Retried, but counted, so even a refusal
//              nobody recognised stops after the stage's max_attempts instead
//              of starving the queue.
//
// THE BUG THIS FIXES, measured 2026-10-07: nine leads with no scraped address,
// no address published on their own site and no founder LinkedIn were going to
// Apollo, being refused, and coming straight back to the top of the next hour's
// batch of 10 -- `ORDER BY fit_score DESC` put the same stuck leads first every
// time. At ten such leads no new lead would ever have been looked up again.

const src = $('Resolve Contact').item.json;
const resp = $input.item.json;

// Ordered best-first. Matched as substrings against a lowercased title, so
// "Founder & CEO" and "Co-Founder" both land in tier 0.
const TITLE_TIERS = [
  ['founder', 'co-founder', 'cofounder', 'owner', 'proprietor'],
  ['managing director', 'general manager', 'director general', 'managing partner'],
  ['ceo', 'chief executive'],
  ['business development', 'bd director', 'bd manager', 'commercial director'],
];

// Not a tier -- a tiebreak within one. Keeps a "Business Development Director"
// ahead of a "Business Development Representative" without inventing a fifth
// preference level the spec does not have.
const SENIORITY = ['chief', 'president', 'partner', 'head of', 'vp', 'vice president', 'director', 'manager'];

function tierOf(title) {
  const t = (title || '').toLowerCase();
  if (!t) return -1;
  for (let i = 0; i < TITLE_TIERS.length; i++) {
    for (const kw of TITLE_TIERS[i]) {
      if (t.indexOf(kw) >= 0) return i;
    }
  }
  return -1;
}

function seniorityOf(title) {
  const t = (title || '').toLowerCase();
  for (let i = 0; i < SENIORITY.length; i++) {
    if (t.indexOf(SENIORITY[i]) >= 0) return i;
  }
  return SENIORITY.length;
}

function normDomain(d) {
  if (typeof d !== 'string') return '';
  return d.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '')
    .replace(/[/].*$/, '').replace(/[./]+$/, '');
}

const base = {
  lead_id: src.lead_id,
  domain: src.domain,
  company_name: src.company_name,
  country: src.country,
  fit_score: src.fit_score,
  source: 'apollo',
};

// ---------------------------------------------------------------------------
// Did Apollo actually answer?
// ---------------------------------------------------------------------------
//
// The HTTP node continues on error, so a refusal arrives as a normal item
// carrying an `error` key. API_INACCESSIBLE (the endpoint is not on the account's
// plan) and a 429 are both this shape. Neither is evidence about the company.
// Apollo's Free plan refuses these endpoints outright: 403 with
// API_INACCESSIBLE and "not included in your Free plan" (measured 2026-09-06 on
// both the API-key and the OAuth path, on an account showing 125 unused
// credits -- Section 9's table). That is a permanent property of the plan, so
// the lead has genuinely run out of sources and is retired rather than retried
// every hour for ever. Positive recognition only: anything this does not
// clearly identify as the plan gate stays a retry, because "a refusal is not
// evidence" is still the rule for every refusal that could pass next time.
function isPlanGate(text) {
  const t = String(text || '').toLowerCase();
  if (/\b(429|too many requests|rate limit|timeout|etimedout|econn|socket|enotfound|eai_again|5\d\d)\b/.test(t)) {
    return false;
  }
  return t.indexOf('api_inaccessible') !== -1 ||
    t.indexOf('not included in your') !== -1 ||
    (t.indexOf('403') !== -1 && t.indexOf('forbidden') !== -1) ||
    (t.indexOf('403') !== -1 && t.indexOf('plan') !== -1);
}

// What the free sources did, carried from Resolve Contact so the attempt record
// and the manual-contact queue can say what was actually tried.
function triedWhat() {
  const f = src.free_sources || {};
  const bits = ['no e-mail on the ichgcp profile page'];
  if (f.site_lookup === 'found') bits.push('an address was published on the site');
  else if (f.site_lookup === 'unreachable') bits.push('their website did not load');
  else if (f.site_lookup === 'no-address-found') {
    bits.push('no address published on ' + (Number(f.site_pages_fetched) || 0) + ' pages of their own site' +
      ((f.site_rejected || []).length
        ? ' (found only ' + (f.site_rejected || []).join(', ') + ', which is never a contact mailbox)'
        : ''));
  } else bits.push('site not read (' + (f.site_lookup || 'skipped') + ')');
  bits.push('founder LinkedIn: ' + (f.founder_linkedin || 'none found'));
  return bits.join('; ');
}

if (!resp || typeof resp !== 'object' || resp.error || resp.error_code || !Array.isArray(resp.people)) {
  // JSON.stringify, not String(): n8n wraps an HTTP failure as an object, and
  // String() renders that '[object Object]' -- which turns the one field an
  // operator needs (why did Apollo refuse?) into nothing at all. Same shape
  // code_score.js uses for the ClinicalTrials.gov failure path.
  const err = resp && (resp.error || resp.message);
  const detail = err
    ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300)
    : 'no people[] array in the response';
  const permanent = isPlanGate(detail) || isPlanGate(JSON.stringify(resp || null));
  return {
    json: Object.assign({}, base, {
      write: false,
      matched: false,
      // A plan gate will answer the same way next hour, so there is nothing to
      // come back for. Anything else is retried.
      retry: !permanent,
      skip_reason: 'Apollo people-search did not answer: ' + detail,
      attempt_row: {
        lead_id: src.lead_id,
        outcome: permanent ? 'exhausted' : 'refused',
        detail: permanent
          ? 'Apollo cannot be called on this account\'s plan (' + detail + '). ' + triedWhat() +
            '. Find a contact by hand, add a `contacts` row and set the lead to contact_found; ' +
            'delete its contact_attempts row to put it back in the queue.'
          : 'Apollo did not answer: ' + detail + '. Retried next run. ' + triedWhat(),
      },
    }),
  };
}

// ---------------------------------------------------------------------------
// Rank what came back
// ---------------------------------------------------------------------------

const leadDomain = normDomain(src.domain);
const knownFounder = (src.known_founder_name || '').trim().toLowerCase();

const ranked = resp.people
  .map(function (p) {
    const org = p.organization || {};
    const orgDomain = normDomain(org.primary_domain || org.website_url || p.organization_domain);
    return {
      person: p,
      tier: tierOf(p.title),
      seniority: seniorityOf(p.title),
      // Apollo can attach a person to an organisation whose domain is not the
      // one searched (former employer, parent company, a same-name business in
      // another country). Spending a credit on that person buys the wrong
      // company's contact, so it is a hard gate, not a ranking penalty.
      domain_ok: !orgDomain || orgDomain === leadDomain,
      // The site named this person as founder. Independent corroboration from a
      // second source outranks a title string alone.
      corroborated:
        !!knownFounder &&
        typeof p.name === 'string' &&
        p.name.trim().toLowerCase() === knownFounder,
    };
  })
  .filter(function (c) { return c.tier >= 0 && c.domain_ok; })
  .sort(function (a, b) {
    if (a.corroborated !== b.corroborated) return a.corroborated ? -1 : 1;
    if (a.tier !== b.tier) return a.tier - b.tier;
    return a.seniority - b.seniority;
  });

if (!ranked.length) {
  const seen = resp.people.length;
  const reason =
    seen === 0
      ? 'Apollo has no people indexed at this domain'
      : 'Apollo returned ' + seen + ' ' + (seen === 1 ? 'person' : 'people') +
        ' at this domain, none in a founder / managing director / CEO / business ' +
        'development role at the searched domain';
  return {
    json: Object.assign({}, base, {
      write: true,
      matched: false,
      retry: false,
      no_match_reason: reason,
      // A tombstone: the lead gets a contacts row carrying no contact. It is
      // written for one reason -- without it the batch query has no way to tell
      // "not looked up yet" from "looked up, nothing there", and every cron run
      // would re-spend a search on the same dead domains forever.
      //
      // It deliberately does NOT advance the lead. Section 8's status flow has
      // no state for "asked and found nothing", and `contact_found` would put a
      // contactless lead in front of Workflow 4, which is exactly what the
      // grounding guard exists to prevent. The lead stays 'scored'.
      payload: {
        lead_id: src.lead_id,
        name: null,
        title: null,
        email: null,
        linkedin_url: null,
        apollo_id: null,
        // Nothing was found, so there is nothing to have verified.
        verified: false,
        advance: false,
        note: reason,
        // Apollo answered: every source has now been asked and none of them
        // reached anybody. Permanent, so the lead leaves the queue and turns up
        // in needs_manual_contact with this detail as its `why`. Inside the
        // payload because Write Contact & Advance records it in the same
        // statement as the tombstone -- one crash point, not two.
        attempt: {
          outcome: 'exhausted',
          detail: reason + '. ' + triedWhat() +
            '. Find a contact by hand, add a `contacts` row and set the lead to contact_found.',
        },
      },
    }),
  };
}

const best = ranked[0];
const p = best.person;

return {
  json: Object.assign({}, base, {
    write: true,
    matched: true,
    retry: false,
    apollo_id: p.id || null,
    candidate: {
      name: p.name || [p.first_name, p.last_name].filter(Boolean).join(' ') || null,
      first_name: p.first_name || null,
      last_name: p.last_name || null,
      title: p.title || null,
      linkedin_url: p.linkedin_url || null,
    },
    match_tier: best.tier,
    corroborated: best.corroborated,
    // Nothing to record here: the payload builders downstream set
    // payload.attempt = null, which makes Write Contact & Advance DELETE any
    // earlier record -- the lead has a channel now, and a stale row would show
    // it in needs_manual_contact as still waiting for a human.
    candidates_considered: resp.people.length,
    candidates_in_role: ranked.length,
  }),
};

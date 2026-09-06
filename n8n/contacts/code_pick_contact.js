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
//   retry     the call itself failed (plan gate, rate limit, timeout). Nothing
//             is written; the lead stays 'scored' and the next run tries again.
//             Recording "no match" here would be a lie about what Apollo said.
//   no match  Apollo answered, and nothing at this domain clears the bar. A
//             tombstone row is written so the lead is not re-queried every run.
//   match     one person clears the bar; the enrichment call is worth a credit.

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
if (!resp || typeof resp !== 'object' || resp.error || resp.error_code || !Array.isArray(resp.people)) {
  // JSON.stringify, not String(): n8n wraps an HTTP failure as an object, and
  // String() renders that '[object Object]' -- which turns the one field an
  // operator needs (why did Apollo refuse?) into nothing at all. Same shape
  // code_score.js uses for the ClinicalTrials.gov failure path.
  const err = resp && (resp.error || resp.message);
  const detail = err
    ? (typeof err === 'string' ? err : JSON.stringify(err)).slice(0, 300)
    : 'no people[] array in the response';
  return {
    json: Object.assign({}, base, {
      write: false,
      matched: false,
      retry: true,
      skip_reason: 'Apollo people-search did not answer: ' + detail,
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
    candidates_considered: resp.people.length,
    candidates_in_role: ranked.length,
  }),
};

// Covers the credit-spending half of Workflow 3b: ranking an Apollo people
// search, and turning an enrichment response into a contacts row.
//
// The case this file exists for is the three-way split. "Apollo refused the
// call" and "Apollo answered, nobody there" look nearly identical at the node
// boundary and mean opposite things: the first must be retried, the second must
// never be. Collapsing them either burns credits on a loop or writes a permanent
// "no contact" for a company that has one.
const path = require('path');
const { runForEachItem, runner } = require('./harness');

const PICK = path.join(__dirname, 'code_pick_contact.js');
const PAYLOAD = path.join(__dirname, 'code_payload_apollo.js');

const t = runner('Workflow 3b — Apollo ranking and payload');

function src(over) {
  return Object.assign(
    {
      needs_apollo: true,
      source: 'apollo',
      lead_id: 55,
      domain: 'jssresearch.com',
      company_name: 'JSS Medical Research',
      country: 'India',
      fit_score: 71,
      known_founder_name: null,
      known_founder_linkedin: null,
      known_founder_title: null,
    },
    over || {}
  );
}

function person(over) {
  return Object.assign(
    {
      id: '000000000000000000000001',
      name: 'A Person',
      first_name: 'A',
      last_name: 'Person',
      title: 'Clinical Research Associate',
      linkedin_url: 'https://www.linkedin.com/in/aperson',
      organization: { primary_domain: 'jssresearch.com', name: 'JSS Medical Research' },
    },
    over || {}
  );
}

function pick(resp, over) {
  const s = src(over);
  return runForEachItem(PICK, [{ json: resp }], { 'Resolve Contact': [{ json: s }] })[0].json;
}

// ---------------------------------------------------------------------------
// Apollo did not answer — retry, never tombstone
// ---------------------------------------------------------------------------

// The live shape as of this build: the account's plan does not include the
// people-search endpoint, so every call returns this.
const planGate = pick({
  error: 'The api/v1/mixed_people/search API is not included in your Free plan and is not accessible.',
  error_code: 'API_INACCESSIBLE',
});
t.check('a plan refusal writes nothing', planGate.write, false);
t.check('a plan refusal is retryable', planGate.retry, true);
t.check('a plan refusal is not a no-match', planGate.matched, false);
t.check('the refusal text is carried for the operator',
  planGate.skip_reason.indexOf('not included in your Free plan') > 0, true);

const rateLimited = pick({ error: 'rate limit exceeded', error_code: 'RATE_LIMITED' });
t.check('a rate limit is retryable too', rateLimited.retry, true);

// n8n wraps an HTTP-level failure as an object rather than a string. Observed
// live on the first run of this stage, where it rendered as '[object Object]'
// and threw away the only field the operator needed.
const objectError = pick({ error: { message: 'Forbidden', httpCode: '403' } });
t.check('an object-shaped error is retryable', objectError.retry, true);
t.check('an object-shaped error is serialised, not stringified to [object Object]',
  objectError.skip_reason.indexOf('[object Object]') < 0, true);
t.check('and the operator can still read why',
  objectError.skip_reason.indexOf('Forbidden') > 0, true);

const garbage = pick({ ok: true });
t.check('a response with no people[] is treated as no answer', garbage.retry, true);

const nulled = pick(null);
t.check('a null response is treated as no answer', nulled.retry, true);

// ---------------------------------------------------------------------------
// Apollo answered, nothing usable — tombstone, never retry
// ---------------------------------------------------------------------------

const nobody = pick({ people: [] });
t.check('an empty roster is written', nobody.write, true);
t.check('an empty roster is not retried', nobody.retry, false);
t.check('an empty roster records no match', nobody.matched, false);
t.check('an empty roster does not advance the lead', nobody.payload.advance, false);
t.check('an empty roster leaves every contact field null',
  [nobody.payload.name, nobody.payload.title, nobody.payload.email, nobody.payload.linkedin_url],
  [null, null, null, null]);
t.check('nothing found means nothing verified', nobody.payload.verified, false);
t.check('the reason names the absence', nobody.no_match_reason, 'Apollo has no people indexed at this domain');

const wrongRoles = pick({
  people: [person({ title: 'Clinical Research Associate' }), person({ title: 'Data Manager' })],
});
t.check('a roster with no ICP role is a no-match', wrongRoles.matched, false);
t.check('a roster with no ICP role is not retried', wrongRoles.retry, false);
t.check('the reason counts who was seen',
  wrongRoles.no_match_reason.indexOf('returned 2 people') > 0, true);

// A person Apollo attached to a different company's domain is the wrong
// company's contact, however senior the title reads.
const wrongDomain = pick({
  people: [person({ title: 'Founder', organization: { primary_domain: 'someoneelse.com' } })],
});
t.check('a founder at another domain is not a match', wrongDomain.matched, false);

const noOrgDomain = pick({
  people: [person({ title: 'Founder', organization: {} })],
});
t.check('an unknown org domain does not disqualify a match', noOrgDomain.matched, true);

// ---------------------------------------------------------------------------
// Ranking
// ---------------------------------------------------------------------------

const tiers = pick({
  people: [
    person({ id: 'bd', name: 'BD Person', title: 'Business Development Director' }),
    person({ id: 'ceo', name: 'Chief Person', title: 'Chief Executive Officer' }),
    person({ id: 'md', name: 'MD Person', title: 'Managing Director' }),
    person({ id: 'fdr', name: 'Founder Person', title: 'Co-Founder & CTO' }),
  ],
});
t.check('founder outranks MD, CEO and BD', tiers.candidate.name, 'Founder Person');
t.check('the tier is recorded', tiers.match_tier, 0);
t.check('every in-role candidate is counted', tiers.candidates_in_role, 4);
t.check('every returned person is counted', tiers.candidates_considered, 4);

t.check('MD outranks CEO and BD', pick({
  people: [
    person({ id: 'bd', name: 'BD Person', title: 'Head of Business Development' }),
    person({ id: 'ceo', name: 'Chief Person', title: 'CEO' }),
    person({ id: 'md', name: 'MD Person', title: 'Managing Director' }),
  ],
}).candidate.name, 'MD Person');

t.check('CEO outranks BD', pick({
  people: [
    person({ id: 'bd', name: 'BD Person', title: 'Business Development Manager' }),
    person({ id: 'ceo', name: 'Chief Person', title: 'CEO' }),
  ],
}).candidate.name, 'Chief Person');

t.check('seniority breaks a tie inside one tier', pick({
  people: [
    person({ id: 'rep', name: 'Junior Person', title: 'Business Development Representative' }),
    person({ id: 'dir', name: 'Senior Person', title: 'Business Development Director' }),
  ],
}).candidate.name, 'Senior Person');

// The site named this person as founder in Workflow 2. Two independent sources
// agreeing on the person beats a better-sounding title on a stranger.
const corroborated = pick(
  {
    people: [
      person({ id: 'other', name: 'Someone Else', title: 'Founder' }),
      person({ id: 'known', name: 'Dr. John S. Sampalis', title: 'Business Development Director' }),
    ],
  },
  { known_founder_name: 'Dr. John S. Sampalis' }
);
t.check('a site-corroborated person wins', corroborated.candidate.name, 'Dr. John S. Sampalis');
t.check('corroboration is recorded', corroborated.corroborated, true);
t.check('an uncorroborated pick says so', tiers.corroborated, false);

// ---------------------------------------------------------------------------
// Build Contact Payload (Apollo)
// ---------------------------------------------------------------------------

function payload(resp, picked) {
  return runForEachItem(PAYLOAD, [{ json: resp }], { 'Pick Best Contact': [{ json: picked }] })[0].json;
}

const good = payload(
  {
    person: {
      id: 'apollo123', name: 'Founder Person', first_name: 'Founder', last_name: 'Person',
      title: 'Co-Founder & CTO', email: 'founder@jssresearch.com', email_status: 'verified',
      linkedin_url: 'https://www.linkedin.com/in/founderperson',
    },
  },
  tiers
);
t.check('a verified address is written', good.payload.email, 'founder@jssresearch.com');
t.check('a verified address is marked verified', good.payload.verified, true);
t.check('the apollo id is recorded', good.payload.apollo_id, 'apollo123');
t.check('a matched lead advances', good.payload.advance, true);
t.check('the credit is attributed', good.credits_spent, 1);

const guessed = payload(
  { person: { id: 'a2', name: 'Founder Person', title: 'Founder', email: 'f@jssresearch.com', email_status: 'guessed' } },
  tiers
);
t.check('a guessed address is kept', guessed.payload.email, 'f@jssresearch.com');
t.check('a guessed address is not verified', guessed.payload.verified, false);
t.check('a guessed address still advances the lead', guessed.payload.advance, true);

// Apollo's placeholder for an address the account cannot see. Writing it would
// put an undeliverable string in contacts.email that reads like a real one.
const masked = payload(
  {
    person: {
      id: 'a3', name: 'Founder Person', title: 'Founder',
      email: 'email_not_unlocked@domain.com', email_status: 'unavailable',
      linkedin_url: 'https://www.linkedin.com/in/founderperson',
    },
  },
  tiers
);
t.check('a masked address is not written', masked.payload.email, null);
t.check('a masked address is not verified', masked.payload.verified, false);
t.check('LinkedIn alone is still a channel', masked.payload.advance, true);

// The search result's LinkedIn URL is a fallback for a gap in the enrichment
// response, so "no channel" means neither call produced one.
const noLinkedinAnywhere = pick({
  people: [person({ title: 'Founder', linkedin_url: null })],
});
t.check('the search candidate can itself carry no LinkedIn', noLinkedinAnywhere.candidate.linkedin_url, null);

const maskedNoLinkedin = payload(
  { person: { id: 'a4', name: 'Founder Person', title: 'Founder', email: 'email_not_unlocked@domain.com' } },
  noLinkedinAnywhere
);
t.check('a masked address with no LinkedIn anywhere does not advance', maskedNoLinkedin.payload.advance, false);
t.check('and that row is written, not retried', maskedNoLinkedin.write, true);

const matchFailed = payload({ error: 'timeout', error_code: 'TIMEOUT' }, tiers);
t.check('a failed enrichment writes nothing', matchFailed.write, false);
t.check('a failed enrichment spends nothing', matchFailed.credits_spent, 0);

const noPerson = payload({ person: null }, tiers);
t.check('an empty enrichment result writes nothing', noPerson.write, false);

// Apollo can return a person with fields the search already had; the search
// values are the fallback, never an override.
const sparse = payload({ person: { id: 'a5', email: 'x@jssresearch.com', email_status: 'verified' } }, tiers);
t.check('the search title fills a gap in the enrichment', sparse.payload.title, 'Co-Founder & CTO');
t.check('the search name fills a gap in the enrichment', sparse.payload.name, 'Founder Person');

t.done();

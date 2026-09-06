// Covers the zero-credit half of Workflow 3b: reading the scraped address back
// out of the CSV, and deciding per lead whether Apollo is needed at all.
//
// This is the half that spends nothing, so the failure it guards against is
// silent. A normalisation slip that misses a domain does not error — it just
// quietly buys an address from Apollo that we already had on disk.
const path = require('path');
const { runForEachItem, runForAllItems, runner } = require('./harness');

const INDEX = path.join(__dirname, 'code_index_csv.js');
const RESOLVE = path.join(__dirname, 'code_resolve.js');
const PAYLOAD = path.join(__dirname, 'code_payload_scrape.js');

const t = runner('Workflow 3b — scraped-email resolution');

// Shapes taken from real rows of data/ichgcp_leads.csv.
const csv = [
  { json: { domain: 'atlantclinical.com', email: 'contact@atlantclinical.com', company_name: 'Atlant Clinical' } },
  { json: { domain: 'klixar.com', email: 'hello@klixar.com', company_name: 'KLIXAR' } },
  { json: { domain: 'innovate-research.com', email: 'Devesh.kumar@innovate-research.com', company_name: 'Innovate Research' } },
  { json: { domain: 'cebisinternational.com', email: '', company_name: 'CEBIS International' } },
  { json: { domain: 'jssresearch.com', company_name: 'JSS Medical Research' } },
  { json: { domain: 'WWW.Bitrial.HU ', email: '  info@bitrial.hu ', company_name: 'BiTrial' } },
  { json: { domain: 'broken.example', email: 'not-an-email', company_name: 'Broken' } },
  { json: { domain: 'spacey.example', email: 'a b@c.com', company_name: 'Spacey' } },
  { json: { domain: 'atlantclinical.com', email: 'second@atlantclinical.com', company_name: 'Atlant dupe' } },
];

const index = runForAllItems(INDEX, csv);
const idx = index[0].json;

t.check('index counts only well-formed addresses', idx.domains_with_email, 4);
t.check('index counts malformed separately', idx.malformed_emails, 2);
t.check('index sees every CSV row', idx.csv_rows, csv.length);
t.check('blank email is not indexed', idx.by_domain['cebisinternational.com'], undefined);
t.check('absent email column is not indexed', idx.by_domain['jssresearch.com'], undefined);
t.check('malformed address is not indexed', idx.by_domain['broken.example'], undefined);
t.check('whitespace inside an address is malformed', idx.by_domain['spacey.example'], undefined);
t.check('domain is lowercased, trimmed, www-stripped', idx.by_domain['bitrial.hu'], 'info@bitrial.hu');
t.check('first row wins on a duplicate domain', idx.by_domain['atlantclinical.com'], 'contact@atlantclinical.com');

// ---------------------------------------------------------------------------
// Resolve Contact
// ---------------------------------------------------------------------------

const leads = [
  // Role inbox, and the site named nobody.
  { json: { lead_id: 104, domain: 'atlantclinical.com', company_name: 'Atlant Clinical', country: 'Turkey', fit_score: 74, founder_name: null, founder_linkedin: null, founder_title: null } },
  // Role inbox AND a founder the site named, with LinkedIn.
  { json: { lead_id: 7, domain: 'klixar.com', company_name: 'KLIXAR', country: 'Argentina', fit_score: 71, founder_name: 'Enrique Gaubeca', founder_linkedin: 'https://www.linkedin.com/in/enriquegaubeca', founder_title: 'General Manager, Founder and Co-owner' } },
  // A named mailbox, no founder on the site.
  { json: { lead_id: 50, domain: 'innovate-research.com', company_name: 'Innovate Research', country: 'India', fit_score: 63, founder_name: null, founder_linkedin: null, founder_title: null } },
  // No scraped address: the only path that may reach Apollo.
  { json: { lead_id: 55, domain: 'jssresearch.com', company_name: 'JSS Medical Research', country: 'India', fit_score: 71, founder_name: 'Dr. John S. Sampalis', founder_linkedin: 'https://ca.linkedin.com/in/johnsampalis', founder_title: 'Founder & Advisor' } },
  // Same, and the site named nobody either.
  { json: { lead_id: 92, domain: 'cebisinternational.com', company_name: 'CEBIS International', country: 'Romania', fit_score: 63, founder_name: null, founder_linkedin: null, founder_title: null } },
];

const up = { 'Index Scraped Emails': index };
const resolved = runForEachItem(RESOLVE, leads, up).map(function (r) { return r.json; });

t.check('scraped address short-circuits the Apollo call',
  resolved.map(function (r) { return r.needs_apollo; }), [false, false, false, true, true]);
t.check('credits saved is counted per skipped lookup',
  resolved.reduce(function (n, r) { return n + r.credits_saved; }, 0), 3);
t.check('role inbox is flagged', resolved[0].role_inbox, true);
t.check('named mailbox is not flagged as a role inbox', resolved[2].role_inbox, false);
t.check('role inbox with no named person carries no name', resolved[0].contact.name, null);
t.check('scraped email is written', resolved[0].contact.email, 'contact@atlantclinical.com');
t.check('first-party address is written verified', resolved[0].contact.verified, true);
t.check('apollo_id is null on the scrape branch', resolved[0].contact.apollo_id, null);
t.check('site-named founder rides along with the role inbox', resolved[1].contact.name, 'Enrique Gaubeca');
t.check('so does the title', resolved[1].contact.title, 'General Manager, Founder and Co-owner');
t.check('so does the LinkedIn URL', resolved[1].contact.linkedin_url, 'https://www.linkedin.com/in/enriquegaubeca');
t.check('a name is never invented for a bare mailbox', resolved[2].contact.name, null);
t.check('Apollo branch carries the known founder forward', resolved[3].known_founder_name, 'Dr. John S. Sampalis');
t.check('Apollo branch with no known founder passes null', resolved[4].known_founder_name, null);
t.check('Apollo branch claims no credit saving', resolved[3].credits_saved, 0);

// ---------------------------------------------------------------------------
// Build Contact Payload (Scraped)
// ---------------------------------------------------------------------------

const scraped = resolved
  .filter(function (r) { return !r.needs_apollo; })
  .map(function (r) { return { json: r }; });
const payloads = runForEachItem(PAYLOAD, scraped).map(function (r) { return r.json; });

t.check('every scraped lead is written',
  payloads.map(function (p) { return p.write; }), [true, true, true]);
t.check('no credit is attributed to the scrape branch',
  payloads.map(function (p) { return p.credits_spent; }), [0, 0, 0]);
t.check('an address is a channel, so the lead advances',
  payloads.map(function (p) { return p.payload.advance; }), [true, true, true]);
t.check('provenance survives as a null apollo_id',
  payloads.map(function (p) { return p.payload.apollo_id; }), [null, null, null]);
t.check('payload carries the founder through', payloads[1].payload.name, 'Enrique Gaubeca');

// A lead with neither channel must not be advanced into the drafting queue.
const empty = runForEachItem(PAYLOAD, [
  { json: { lead_id: 999, domain: 'nothing.example', contact: { name: 'Someone', title: 'CEO', email: null, linkedin_url: null, verified: false } } },
]).map(function (r) { return r.json; });
t.check('a contact with no reachable channel does not advance', empty[0].payload.advance, false);

t.done();

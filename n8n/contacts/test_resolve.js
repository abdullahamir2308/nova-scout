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
  // No scraped address, but the site named its founder AND gave a LinkedIn profile:
  // written LinkedIn-only, Apollo not called (2026-10-06).
  { json: { lead_id: 55, domain: 'jssresearch.com', company_name: 'JSS Medical Research', country: 'India', fit_score: 71, founder_name: 'Dr. John S. Sampalis', founder_linkedin: 'https://ca.linkedin.com/in/johnsampalis', founder_title: 'Founder & Advisor' } },
  // No scraped address and the site named nobody: the only path that may reach Apollo.
  { json: { lead_id: 92, domain: 'cebisinternational.com', company_name: 'CEBIS International', country: 'Romania', fit_score: 63, founder_name: null, founder_linkedin: null, founder_title: null } },
  // No scraped address, a founder LinkedIn profile on a www host, no title.
  { json: { lead_id: 14, domain: 'rarascro.com', company_name: 'Rara SCRO', country: 'Brazil', fit_score: 66, founder_name: 'Claudia Rodriguez Verde', founder_linkedin: 'https://www.linkedin.com/in/claudia-rodriguez-49236754', founder_title: null } },
  // A founder named, but no LinkedIn: Apollo is still the only way to learn more.
  { json: { lead_id: 998, domain: 'namedonly.example', company_name: 'Named Only', country: 'India', fit_score: 55, founder_name: 'Jane Roe', founder_linkedin: null, founder_title: 'Managing Director' } },
  // A LinkedIn COMPANY page is not a person to write to: not a channel, so Apollo.
  { json: { lead_id: 997, domain: 'companypage.example', company_name: 'Company Page', country: 'India', fit_score: 55, founder_name: 'John Doe', founder_linkedin: 'https://www.linkedin.com/company/company-page', founder_title: null } },
];

const up = { 'Index Scraped Emails': index };
const resolved = runForEachItem(RESOLVE, leads, up).map(function (r) { return r.json; });

t.check('a scraped address, or a founder LinkedIn profile, short-circuits the Apollo call',
  resolved.map(function (r) { return r.needs_apollo; }), [false, false, false, false, true, false, true, true]);
t.check('credits saved is counted per skipped lookup',
  resolved.reduce(function (n, r) { return n + r.credits_saved; }, 0), 5);
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
t.check('Apollo branch carries the known founder forward', resolved[6].known_founder_name, 'Jane Roe');
t.check('Apollo branch with no known founder passes null', resolved[4].known_founder_name, null);
t.check('Apollo branch claims no credit saving', resolved[6].credits_saved, 0);

// --- the LinkedIn-only branch (2026-10-06) ---------------------------------------
t.check('a scraped address wins over a LinkedIn profile: the email branch is used', resolved[1].source, 'ichgcp_scrape');
t.check('a lead with no address and a founder profile is the LinkedIn branch', resolved[3].source, 'enrichment_linkedin');
t.check('...with no email at all, never inferred', resolved[3].contact.email, null);
t.check('...the profile URL the site gave', resolved[3].contact.linkedin_url, 'https://ca.linkedin.com/in/johnsampalis');
t.check('...the name and title the site gave', [resolved[3].contact.name, resolved[3].contact.title],
  ['Dr. John S. Sampalis', 'Founder & Advisor']);
t.check('...not verified: that flag means a confirmed address, and there is none', resolved[3].contact.verified, false);
t.check('...scrape-sourced: a null apollo_id', resolved[3].contact.apollo_id, null);
t.check('...not a role inbox', resolved[3].role_inbox, false);
t.check('...the evidence says it was written LinkedIn-only', /LinkedIn-only, Apollo not called/.test(resolved[3].evidence), true);
t.check('a regional subdomain and a www host of linkedin.com/in/ are both profiles',
  [resolved[3].source, resolved[5].source], ['enrichment_linkedin', 'enrichment_linkedin']);
t.check('a title is optional', resolved[5].contact.title, null);
t.check('a LinkedIn company page is not a channel: the lead goes to Apollo', resolved[7].needs_apollo, true);
t.check('...and its URL is still handed to Pick Best Contact', resolved[7].known_founder_linkedin, 'https://www.linkedin.com/company/company-page');
t.check('a lead with nothing free to reach them with still goes to Apollo', resolved[4].source, 'apollo');

// ---------------------------------------------------------------------------
// Build Contact Payload (Scraped)
// ---------------------------------------------------------------------------

const scraped = resolved
  .filter(function (r) { return !r.needs_apollo; })
  .map(function (r) { return { json: r }; });
const payloads = runForEachItem(PAYLOAD, scraped).map(function (r) { return r.json; });

t.check('every scraped lead is written',
  payloads.map(function (p) { return p.write; }), [true, true, true, true, true]);
t.check('no credit is attributed to the scrape branch',
  payloads.map(function (p) { return p.credits_spent; }), [0, 0, 0, 0, 0]);
t.check('an address or a LinkedIn profile is a channel, so the lead advances',
  payloads.map(function (p) { return p.payload.advance; }), [true, true, true, true, true]);
t.check('provenance survives as a null apollo_id',
  payloads.map(function (p) { return p.payload.apollo_id; }), [null, null, null, null, null]);
t.check('payload carries the founder through', payloads[1].payload.name, 'Enrique Gaubeca');
t.check('the payload says which source it came from',
  payloads.map(function (p) { return p.source; }),
  ['ichgcp_scrape', 'ichgcp_scrape', 'ichgcp_scrape', 'enrichment_linkedin', 'enrichment_linkedin']);
t.check('a LinkedIn-only contact is written with a null email, the profile, and verified false',
  [payloads[3].payload.email, payloads[3].payload.linkedin_url, payloads[3].payload.verified],
  [null, 'https://ca.linkedin.com/in/johnsampalis', false]);

// A lead with neither channel must not be advanced into the drafting queue.
const empty = runForEachItem(PAYLOAD, [
  { json: { lead_id: 999, domain: 'nothing.example', contact: { name: 'Someone', title: 'CEO', email: null, linkedin_url: null, verified: false } } },
]).map(function (r) { return r.json; });
t.check('a contact with no reachable channel does not advance', empty[0].payload.advance, false);

// ---------------------------------------------------------------------------
// The site-published address (2026-10-07) -- the third free source
//
// Harvest Site Emails runs immediately upstream and passes the lead row through
// with site_email and its evidence added. It only fetches a site the CSV has no
// address for, so site_email and a scraped address never both exist -- except
// by a bug, and the first case below pins which one wins if they ever do.
// ---------------------------------------------------------------------------

function siteLead(over) {
  return {
    json: Object.assign({
      lead_id: 733, domain: 'hvivo.com', company_name: 'hVIVO', country: 'United Kingdom',
      fit_score: 53, founder_name: null, founder_linkedin: null, founder_title: null,
      site_email: 'bd@hvivo.com', site_email_how: 'mailto', site_email_role_inbox: false,
      site_email_is_founder: false, site_email_candidates: ['bd@hvivo.com', 'careers@hvivo.com'],
      site_email_rejected: ['careers@hvivo.com'], site_pages_fetched: 4,
      site_lookup: 'found',
    }, over || {}),
  };
}

function one(leadItem) {
  return runForEachItem(RESOLVE, [leadItem], up)[0].json;
}

let r = one(siteLead());
t.check('a site-published address skips Apollo', r.needs_apollo, false);
t.check('... and is recorded as its own source', r.source, 'site_published');
t.check('... and is written verified -- published by the company about itself', r.contact.verified, true);
t.check('... with apollo_id null', r.contact.apollo_id, null);
t.check('... and the evidence says where it came from and how',
  [r.evidence.indexOf('published on the company') !== -1,
   r.evidence.indexOf('as mailto') !== -1,
   r.evidence.indexOf('4 pages') !== -1],
  [true, true, true]);
t.check('... and it counts as a credit saved', r.credits_saved, 1);
t.check('the rejected candidates travel too, so a reviewer can see what was passed over',
  r.site_email_candidates, ['bd@hvivo.com', 'careers@hvivo.com']);

// The CSV wins if both somehow exist: it is a committed file, so it is the same
// address on every run for ever, while a site is whatever it serves today.
r = one(siteLead({ domain: 'atlantclinical.com', site_email: 'other@atlantclinical.com' }));
t.check('a scraped address still beats a site one', [r.source, r.contact.email],
  ['ichgcp_scrape', 'contact@atlantclinical.com']);

r = one(siteLead({ site_email: 'info@hvivo.com', site_email_role_inbox: true }));
t.check('a site role inbox is flagged as one', r.role_inbox, true);
r = one(siteLead({ site_email: 'support@hvivo.com', site_email_role_inbox: false }));
t.check('the role list is applied here too, even if upstream did not flag it', r.role_inbox, true);

r = one(siteLead({
  domain: 'ee-cro.com', site_email: 'ilse.eder@ee-cro.com', site_email_is_founder: true,
  founder_name: 'Ilse Eder', founder_title: 'Managing Director',
}));
t.check('a mailbox carrying the founder name says so -- two sources agree on the person',
  r.evidence.indexOf('two sources agree on the person') !== -1, true);
t.check('... and the name and title are written', [r.contact.name, r.contact.title],
  ['Ilse Eder', 'Managing Director']);

r = one(siteLead({ site_email: 'info@hvivo.com', founder_name: 'Jane Roe', founder_title: 'MD' }));
t.check('a role inbox never claims the founder owns it',
  r.evidence.indexOf('not asserted to own this inbox') !== -1, true);
t.check('... though the name is still on the row, because the site did say it', r.contact.name, 'Jane Roe');

// No address anywhere: the LinkedIn-only and Apollo branches, with the site
// lookup now part of what the evidence reports.
r = one(siteLead({
  site_email: null, site_lookup: 'no-address-found', site_pages_fetched: 5,
  site_email_candidates: [], site_email_rejected: [],
  founder_name: 'Jane Roe', founder_linkedin: 'https://www.linkedin.com/in/janeroe',
}));
t.check('no site address falls through to the LinkedIn-only branch', r.source, 'enrichment_linkedin');
t.check('... and the evidence says the site was read and had nothing',
  [r.evidence.indexOf('none published on the site') !== -1, r.evidence.indexOf('5 pages read') !== -1],
  [true, true]);

r = one(siteLead({
  site_email: null, site_lookup: 'no-address-found', site_pages_fetched: 5,
  site_email_candidates: ['careers@hvivo.com'], site_email_rejected: ['careers@hvivo.com'],
}));
t.check('nothing at all reaches Apollo', [r.needs_apollo, r.source], [true, 'apollo']);
t.check('... carrying what each free source actually did, for the attempt record',
  [r.free_sources.site_lookup, r.free_sources.site_pages_fetched,
   r.free_sources.site_rejected, r.free_sources.founder_linkedin],
  ['no-address-found', 5, ['careers@hvivo.com'], 'none found']);
r = one(siteLead({ site_email: null, site_lookup: 'unreachable', site_pages_fetched: 0 }));
t.check('a site that would not load is reported as unreachable, not as "no address"',
  r.free_sources.site_lookup, 'unreachable');
r = one(siteLead({ site_email: null, site_lookup: 'no-address-found',
  founder_name: 'John Doe', founder_linkedin: 'https://www.linkedin.com/company/x' }));
t.check('a company page is still not a person, so Apollo',
  [r.needs_apollo, r.free_sources.founder_linkedin], [true, 'not a personal profile']);
r = one(siteLead({ site_email: '   ' }));
t.check('a blank site_email is no address at all', r.needs_apollo, true);

t.done();

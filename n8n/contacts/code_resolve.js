// Resolve Contact — n8n Code node (Run Once for Each Item).
//
// Decides, per qualified lead, whether Apollo needs to be called at all.
//
// Section 7's ordering rule keeps Apollo on the free tier by spending only on
// leads that already cleared Section 9's score gate (Config.min_fit_score,
// parsed from the doc). This node applies the same principle one level further
// in: of those, spend only on the ones we do not already have a way to reach.
// Two free sources are checked first, both already on disk:
//
//   1. contacts.email  <- the ichgcp profile-page email, via the CSV index
//   2. name/title/linkedin <- enrichments, harvested in Workflow 2
//
// A lead with a scraped email skips the Apollo call entirely. So does a lead
// with no address but a founder LinkedIn profile the company's own site gave
// (2026-10-06): it is written as a LinkedIn-only contact -- no email, not
// verified -- so Workflow 4 drafts the DM Section 6 has a human send. Apollo's
// free plan cannot answer anyway (Section 9), and a profile that regex-matched
// the named founder (code_normalise.js) is a channel nobody had to buy.
//
// Note what is NOT inferred here. A role inbox (info@, contact@) is a company
// address, not a person's, and pairing it with the founder's name would assert
// something no source states. Both facts are still carried -- Workflow 4 drafts
// an email variant and a LinkedIn variant separately, so the address and the
// person feed different channels -- but the row records only what a source
// actually said, per build rule 6.

const lead = $input.item.json;
const index = $('Index Scraped Emails').first().json;
const byDomain = (index && index.by_domain) || {};

function normDomain(d) {
  if (typeof d !== 'string') return '';
  return d.trim().toLowerCase().replace(/^www\./, '').replace(/[./]+$/, '');
}

function clean(v) {
  if (typeof v !== 'string') return null;
  const s = v.trim();
  return s ? s : null;
}

const domain = normDomain(lead.domain);
const scrapedEmail = clean(byDomain[domain]);

// A role inbox is still a real, published, first-party address -- it is how the
// company asked to be contacted. It is flagged rather than rejected so the
// distinction survives into the report and the review queue.
const ROLE_LOCALPARTS = [
  'info', 'contact', 'hello', 'connect', 'office', 'admin', 'enquiry',
  'enquiries', 'inquiry', 'inquiries', 'mail', 'sales', 'support', 'general',
];
const localpart = scrapedEmail ? scrapedEmail.split('@')[0].toLowerCase() : '';
const isRoleInbox = ROLE_LOCALPARTS.indexOf(localpart) >= 0;

// Enrichment already found these on the company's own site in Workflow 2. They
// cost nothing to reuse and are the only person-level facts available before a
// credit is spent.
const founderName = clean(lead.founder_name);
const founderLinkedin = clean(lead.founder_linkedin);
const founderTitle = clean(lead.founder_title);

if (scrapedEmail) {
  return {
    json: {
      needs_apollo: false,
      source: 'ichgcp_scrape',
      lead_id: lead.lead_id,
      domain: lead.domain,
      company_name: lead.company_name,
      country: lead.country,
      fit_score: lead.fit_score,
      contact: {
        // Null unless the site itself named someone. A role inbox gets no name.
        name: founderName,
        title: founderTitle,
        email: scrapedEmail,
        linkedin_url: founderLinkedin,
        apollo_id: null,
        // A first-party address the company published about itself. Not a guess
        // and not an inference, so it is written verified. See README for how
        // this flag is defined across both sources.
        verified: true,
      },
      evidence:
        'email from the company profile page on ichgcp.net (' +
        (isRoleInbox ? 'role inbox' : 'named mailbox') + '), captured at ingestion' +
        (founderName
          ? '; name/title/LinkedIn from the Workflow 2 site extraction'
          : '; no person named on the site'),
      role_inbox: isRoleInbox,
      credits_saved: 1,
    },
  };
}

// No address, but the site named its founder and gave a LinkedIn profile for
// them. Only a personal profile counts (/in/...): enrichment can also see a
// company page, which is not a person to write to. Nothing is inferred -- the
// email stays null, so the row says exactly what the sources said, and it is not
// `verified` (that flag means a confirmed address; there is none).
const PROFILE_URL = /^https?:\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/in\/[^\s/?#]+/i;
if (founderLinkedin && PROFILE_URL.test(founderLinkedin)) {
  return {
    json: {
      needs_apollo: false,
      source: 'enrichment_linkedin',
      lead_id: lead.lead_id,
      domain: lead.domain,
      company_name: lead.company_name,
      country: lead.country,
      fit_score: lead.fit_score,
      contact: {
        name: founderName,
        title: founderTitle,
        email: null,
        linkedin_url: founderLinkedin,
        apollo_id: null,
        verified: false,
      },
      evidence:
        'no email on the ichgcp profile page; LinkedIn profile' +
        (founderName ? ', name' : '') + (founderTitle ? ' and title' : '') +
        ' from the Workflow 2 site extraction -- written LinkedIn-only, Apollo not called',
      role_inbox: false,
      credits_saved: 1,
    },
  };
}

// Nothing free to reach them with. This is the only path that reaches Apollo.
return {
  json: {
    needs_apollo: true,
    source: 'apollo',
    lead_id: lead.lead_id,
    domain: domain,
    company_name: lead.company_name,
    country: lead.country,
    fit_score: lead.fit_score,
    // Passed through so Pick Best Contact can prefer the person the site itself
    // named, when Apollo returns several plausible people.
    known_founder_name: founderName,
    known_founder_linkedin: founderLinkedin,
    known_founder_title: founderTitle,
    credits_saved: 0,
  },
};

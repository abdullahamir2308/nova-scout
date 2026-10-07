// Resolve Contact — n8n Code node (Run Once for Each Item).
//
// Decides, per qualified lead, whether Apollo needs to be called at all.
//
// Section 7's ordering rule keeps Apollo on the free tier by spending only on
// leads that already cleared Section 9's score gate (Config.min_fit_score,
// parsed from the doc). This node applies the same principle one level further
// in: of those, spend only on the ones we do not already have a way to reach.
// Three free sources are checked first:
//
//   1. the ichgcp profile-page email, via the committed CSV index
//   2. an address the company publishes on its own website (Harvest Site
//      Emails, 2026-10-07) -- literally on the page, its own domain only
//   3. name/title/linkedin <- enrichments, harvested in Workflow 2
//
// A lead with an address from 1 or 2 skips the Apollo call entirely. So does a
// lead with no address but a founder LinkedIn profile the company's own site
// gave (2026-10-06): it is written as a LinkedIn-only contact -- no email, not
// verified -- so Workflow 4 drafts the DM Section 6 has a human send. Apollo's
// free plan cannot answer anyway (Section 9), and a profile that regex-matched
// the named founder (code_normalise.js) is a channel nobody had to buy.
//
// WHY 1 BEATS 2. The ichgcp address is in a file this repo committed, so it is
// the same address on every run for ever; the site is whatever it serves today.
// Nothing is lost by preferring the stable one -- both are first-party, both
// are `verified`, and where the site has a better address the CSV usually has
// none at all (that is why this source was added).
//
// Note what is NOT inferred here. A role inbox (info@, contact@) is a company
// address, not a person's, and pairing it with the founder's name would assert
// something no source states. Both facts are still carried -- Workflow 4 drafts
// an email variant and a LinkedIn variant separately, so the address and the
// person feed different channels -- but the row records only what a source
// actually said, per build rule 6. The one exception is a site mailbox whose
// local part carries the founder's own name (ilse.eder@ee-cro.com for "Ilse
// Eder"): there two independent sources agree on the person, which is the same
// corroboration code_pick_contact.js ranks an Apollo candidate by.

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

// Harvest Site Emails ran immediately upstream and passed the lead's row
// through with these added. It only fetches a site when the CSV has no address
// for the domain, so `site_email` is null whenever `scrapedEmail` is set.
const siteEmail = clean(lead.site_email);

// A role inbox is still a real, published, first-party address -- it is how the
// company asked to be contacted. It is flagged rather than rejected so the
// distinction survives into the report and the review queue.
const ROLE_LOCALPARTS = [
  'info', 'contact', 'hello', 'connect', 'office', 'admin', 'enquiry',
  'enquiries', 'inquiry', 'inquiries', 'mail', 'sales', 'support', 'general',
];

function isRole(address) {
  if (!address) return false;
  return ROLE_LOCALPARTS.indexOf(address.split('@')[0].toLowerCase()) >= 0;
}

// Enrichment already found these on the company's own site in Workflow 2. They
// cost nothing to reuse and are the only person-level facts available before a
// credit is spent.
const founderName = clean(lead.founder_name);
const founderLinkedin = clean(lead.founder_linkedin);
const founderTitle = clean(lead.founder_title);

const base = {
  lead_id: lead.lead_id,
  domain: lead.domain,
  company_name: lead.company_name,
  country: lead.country,
  fit_score: lead.fit_score,
};

if (scrapedEmail) {
  const roleInbox = isRole(scrapedEmail);
  return {
    json: Object.assign({}, base, {
      needs_apollo: false,
      source: 'ichgcp_scrape',
      contact: {
        // Null unless the site itself named someone. A role inbox gets no name.
        name: founderName,
        title: founderTitle,
        email: scrapedEmail,
        linkedin_url: founderLinkedin,
        apollo_id: null,
        // A first-party address the company published about itself. Not a guess
        // and not an inference, so it is written verified. See README for how
        // this flag is defined across every source.
        verified: true,
      },
      evidence:
        'email from the company profile page on ichgcp.net (' +
        (roleInbox ? 'role inbox' : 'named mailbox') + '), captured at ingestion' +
        (founderName
          ? '; name/title/LinkedIn from the Workflow 2 site extraction'
          : '; no person named on the site'),
      role_inbox: roleInbox,
      credits_saved: 1,
    }),
  };
}

// No address in the CSV, but the company publishes one on its own site. Taken
// verbatim from the page -- a mailto: link or text on it -- and only on the
// lead's own domain (Harvest Site Emails has the rules and the evidence for
// each of them). Published, so `verified` is true for the same reason the
// ichgcp address is.
if (siteEmail) {
  const roleInbox = lead.site_email_role_inbox === true || isRole(siteEmail);
  const isFounderMailbox = lead.site_email_is_founder === true;
  const how = clean(lead.site_email_how) || 'text';
  const pages = Number(lead.site_pages_fetched) || 0;
  return {
    json: Object.assign({}, base, {
      needs_apollo: false,
      source: 'site_published',
      contact: {
        // The name goes on the row either way -- it is a real fact from the
        // site -- but only a mailbox carrying the founder's own name is a case
        // where any source says the person owns the address, and `evidence`
        // is where that difference is recorded.
        name: founderName,
        title: founderTitle,
        email: siteEmail,
        linkedin_url: founderLinkedin,
        apollo_id: null,
        verified: true,
      },
      evidence:
        'email published on the company\'s own website, found as ' + how +
        ' across ' + pages + ' page' + (pages === 1 ? '' : 's') + ' (' +
        (isFounderMailbox
          ? 'mailbox carries the founder\'s name, so two sources agree on the person'
          : roleInbox ? 'role inbox' : 'named mailbox') + ')' +
        (founderName && !isFounderMailbox
          ? '; name/title/LinkedIn from the Workflow 2 site extraction, not asserted to own this inbox'
          : ''),
      role_inbox: roleInbox,
      site_email_candidates: lead.site_email_candidates || [],
      credits_saved: 1,
    }),
  };
}

// No address anywhere, but the site named its founder and gave a LinkedIn
// profile for them. Only a personal profile counts (/in/...): enrichment can
// also see a company page, which is not a person to write to. Nothing is
// inferred -- the email stays null, so the row says exactly what the sources
// said, and it is not `verified` (that flag means a confirmed address; there is
// none).
const PROFILE_URL = /^https?:\/\/(?:[a-z0-9-]+\.)?linkedin\.com\/in\/[^\s/?#]+/i;
if (founderLinkedin && PROFILE_URL.test(founderLinkedin)) {
  return {
    json: Object.assign({}, base, {
      needs_apollo: false,
      source: 'enrichment_linkedin',
      contact: {
        name: founderName,
        title: founderTitle,
        email: null,
        linkedin_url: founderLinkedin,
        apollo_id: null,
        verified: false,
      },
      evidence:
        'no email on the ichgcp profile page and none published on the site' +
        (Number(lead.site_pages_fetched) ? ' (' + lead.site_pages_fetched + ' pages read)' : '') +
        '; LinkedIn profile' + (founderName ? ', name' : '') + (founderTitle ? ' and title' : '') +
        ' from the Workflow 2 site extraction -- written LinkedIn-only, Apollo not called',
      role_inbox: false,
      credits_saved: 1,
    }),
  };
}

// Nothing free to reach them with. This is the only path that reaches Apollo --
// which, on this account's Free plan, refuses (Section 9). What happens then is
// code_pick_contact.js's business: the lead is recorded in `contact_attempts`
// so it stops filling every batch, and it surfaces in `needs_manual_contact`.
// The two free lookups are summarised here so that record can say what was
// actually tried.
return {
  json: Object.assign({}, base, {
    needs_apollo: true,
    source: 'apollo',
    domain: domain,
    // Passed through so Pick Best Contact can prefer the person the site itself
    // named, when Apollo returns several plausible people.
    known_founder_name: founderName,
    known_founder_linkedin: founderLinkedin,
    known_founder_title: founderTitle,
    // What the free sources did, for the attempt record and the manual queue.
    free_sources: {
      scraped_email: false,
      site_lookup: clean(lead.site_lookup) || 'skipped',
      site_pages_fetched: Number(lead.site_pages_fetched) || 0,
      site_candidates: lead.site_email_candidates || [],
      site_rejected: lead.site_email_rejected || [],
      founder_linkedin: founderLinkedin ? 'not a personal profile' : 'none found',
    },
    credits_saved: 0,
  }),
};

// Build Contact Payload (Scraped) — n8n Code node (Run Once for Each Item).
//
// The zero-credit branch. Everything written here came from a source already on
// disk: the address from the company's own ichgcp profile page, the person from
// the Workflow 2 site extraction. No API was called and no field is inferred.

const r = $input.item.json;
const c = r.contact || {};

// Section 8 gives `contacts` no column for provenance, and this branch needs the
// distinction to survive: a NULL apollo_id is what marks a row as scrape-sourced.
// It is not a placeholder for a lookup that failed -- those rows are written by
// Pick Best Contact and carry a NULL email too.
const hasChannel = !!(c.email || c.linkedin_url);

return {
  json: {
    write: true,
    lead_id: r.lead_id,
    domain: r.domain,
    source: 'ichgcp_scrape',
    credits_spent: 0,
    payload: {
      lead_id: r.lead_id,
      name: c.name || null,
      title: c.title || null,
      email: c.email || null,
      linkedin_url: c.linkedin_url || null,
      apollo_id: null,
      // First-party published address: the company stated it about itself.
      verified: c.verified === true,
      // At least one reachable channel, so Workflow 4 has something to draft to.
      advance: hasChannel,
      note: r.evidence || null,
    },
  },
};

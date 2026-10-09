// Build Harvest Requests -- n8n Code node (Run Once for All Items).
//
// Workflow 1b, Trialsites. Input: the live /countries list (the HTTP node splits
// its JSON array into one item per country, {country, sites}). Output: one item
// per Trialsites country spelling that maps to an included country, per tier
// (A and B) -- each becomes one paginated /sites request. Core countries first,
// so if the run is cut short the countries worth the full geography weight are
// the ones already read.
//
// The country list is read live rather than written down because Trialsites
// spells some countries two ways ("Czech Republic" and "Czechia"; "United
// States" and "United States Of America") and a request for one spelling misses
// the rows filed under the other. A country with no sites at all costs no
// request.

const TIERS = ['A', 'B'];

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

const rows = [];
$input.all().forEach(function (i) {
  const j = i.json || {};
  if (Array.isArray(j)) { j.forEach(function (r) { rows.push(r); }); return; }
  if (Array.isArray(j.countries)) { j.countries.forEach(function (r) { rows.push(r); }); return; }
  rows.push(j);
});

const seen = {};
const requests = [];
rows.forEach(function (r) {
  const name = str(r && r.country);
  if (!name || seen[name] || !(Number(r.sites) > 0)) return;
  seen[name] = true;
  const canonical = canonicalCountry(name);
  if (!canonical) return;
  TIERS.forEach(function (tier) {
    requests.push({ trialsites_country: name, country: canonical, tier: tier, core: isCore(canonical) });
  });
});
requests.sort(function (a, b) {
  return (a.core === b.core ? 0 : a.core ? -1 : 1) || a.country.localeCompare(b.country) ||
    a.trialsites_country.localeCompare(b.trialsites_country) || a.tier.localeCompare(b.tier);
});

if (!requests.length) {
  // The list came back empty or unreadable. Throwing fails the execution
  // loudly instead of logging a harvest that read nothing: a harvest log row
  // would otherwise stop the next attempt for a week.
  throw new Error('Trialsites /countries returned no harvestable country (' + rows.length + ' rows read)');
}
return requests.map(function (r) { return { json: r }; });

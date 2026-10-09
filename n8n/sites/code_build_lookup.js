// Build Lookup Request -- n8n Code node (Run Once for Each Item).
//
// Workflow 1b, website lookup. One Anthropic Messages request per Trialsites
// candidate: the cheapest model that does this reliably (Section 3 and Section
// 4 record the measurement), with the server-side web search tool, and a
// structured answer so code -- Verify Website -- makes the decision.
//
// The request carries only what Trialsites published about the organisation:
// its name, city and country. Nothing about any investigator is sent or asked.

// Substituted at build time by n8n/sites/build_workflow.py: model, max_tokens,
// effort, the web search tool (with its max_uses) and the answer schema.
const LOOKUP_REQUEST = __LOOKUP_REQUEST__;
const SYSTEM_PROMPT = __LOOKUP_SYSTEM_PROMPT__;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

const c = $input.item.json;
const place = [str(c.city), str(c.state), str(c.country)].filter(Boolean).join(', ');
const user = [
  'Organisation, as a public trial-site registry lists it: ' + str(c.canonical_name),
  'Location: ' + place,
  '',
  'Find its official website.',
].join('\n');

return {
  json: Object.assign({}, c, {
    request: Object.assign({}, LOOKUP_REQUEST, {
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: user }],
    }),
  }),
};

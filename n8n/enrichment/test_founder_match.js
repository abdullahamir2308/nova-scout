// Unit test for the deterministic founder_name -> LinkedIn URL matcher.
// The LLM never picks the URL; it only names the founder, and this function
// matches that name against the URLs regex-harvested from the page.
//
// The matcher is sliced out of code_normalise.js rather than copied here, so
// this tests the exact code that build_workflow.py embeds in the workflow.
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'code_normalise.js'), 'utf8');
const start = src.indexOf('function deaccent');
const end = src.indexOf('function cleanStr');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('could not slice matchFounderLinkedin out of code_normalise.js');
}
const { matchFounderLinkedin } = new Function(
  src.slice(start, end) + '\nreturn { matchFounderLinkedin };'
)();

const cases = [
  ['Enrique Gaubeca', ['https://www.linkedin.com/in/enriquegaubeca', 'https://www.linkedin.com/in/florencia-licastro-prado-0aa97952', 'https://www.linkedin.com/in/raul-yepez-2751739'], 'https://www.linkedin.com/in/enriquegaubeca'],
  ['Leylen Colmegna', ['https://ar.linkedin.com/in/leylen-colmegna-ab34819', 'https://ar.linkedin.com/in/otro-persona-123'], 'https://ar.linkedin.com/in/leylen-colmegna-ab34819'],
  ['Claudia Rodriguez Verde', ['https://www.linkedin.com/in/claudia-rodriguez-49236754', 'https://www.linkedin.com/in/leonardo-abizaid-7b5a821bb', 'https://www.linkedin.com/in/sandra-facincone-65a506a8'], 'https://www.linkedin.com/in/claudia-rodriguez-49236754'],
  ['José Ramón Muñoz', ['https://es.linkedin.com/in/jose-ramon-munoz-1234'], 'https://es.linkedin.com/in/jose-ramon-munoz-1234'],
  ['Someone Unlisted', ['https://www.linkedin.com/in/different-person-99'], null],
  [null, ['https://www.linkedin.com/in/whoever'], null],
  ['Ana Silva', [], null],
  // opaque slug that contains no name -> must refuse, not guess
  ['Maria Fernanda', ['https://www.linkedin.com/in/abc123xyz'], null],
  // a non-LinkedIn URL in the candidate list must be skipped, not matched
  ['Rita Iesalniece', ['https://example.com/rita-iesalniece', 'https://lv.linkedin.com/in/rita-iesalniece-88b0a1'], 'https://lv.linkedin.com/in/rita-iesalniece-88b0a1'],
];

let pass = 0;
for (const [name, cands, expect] of cases) {
  const got = matchFounderLinkedin(name, cands);
  const ok = got === expect;
  pass += ok ? 1 : 0;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${JSON.stringify(name)} -> ${got}${ok ? '' : `   (expected ${expect})`}`);
}
console.log(`\n${pass}/${cases.length} passed`);
process.exit(pass === cases.length ? 0 : 1);

// Unit tests for the open site sources (migration 025): robots.txt, the contact-
// form detector, and candidates.js's screens and match, on fixtures.
//   node n8n/sites/open_sources/test_open_sources.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { parseRobots, pathAllowed, FORM_RE } = require('./verify.js');

let passed = 0, failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log('  ' + (ok ? 'ok  ' : 'FAIL') + ' ' + name + (ok ? '' : '\n       got  ' + JSON.stringify(got) + '\n       want ' + JSON.stringify(want)));
  ok ? passed++ : failed++;
}

// --- robots.txt -----------------------------------------------------------------------
const star = parseRobots('User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n');
check('robots: a disallowed directory', pathAllowed(star, '/wp-admin/x'), false);
check('robots: the longer Allow wins', pathAllowed(star, '/wp-admin/admin-ajax.php'), true);
check('robots: the homepage stays allowed', pathAllowed(star, '/'), true);
check('robots: Disallow: / blocks the homepage', pathAllowed(parseRobots('User-agent: *\nDisallow: /\n'), '/'), false);
const mine = parseRobots('User-agent: *\nDisallow: /\n\nUser-agent: NovaScoutBot\nDisallow: /private\n');
check('robots: a group naming NovaScoutBot replaces *', [pathAllowed(mine, '/'), pathAllowed(mine, '/private/a')], [true, false]);
check('robots: other bots\' groups do not apply', pathAllowed(parseRobots('User-agent: GPTBot\nDisallow: /\n'), '/'), true);
check('robots: wildcards and $', [pathAllowed(parseRobots('User-agent: *\nDisallow: /*.pdf$\n'), '/a/b.pdf'),
  pathAllowed(parseRobots('User-agent: *\nDisallow: /*.pdf$\n'), '/a/b.pdf?x')], [false, true]);
check('robots: an empty Disallow allows everything', pathAllowed(parseRobots('User-agent: *\nDisallow:\n'), '/'), true);
check('robots: a group of several user-agents', pathAllowed(parseRobots('User-agent: a\nUser-agent: *\nDisallow: /x\n'), '/x'), false);

// --- contact form --------------------------------------------------------------------------
check('form: a form with an email field', FORM_RE.test('<form action="/c"><input type="email" name="e"></form>'), true);
check('form: a form with a textarea', FORM_RE.test('<form><textarea name="msg"></textarea></form>'), true);
check('form: Contact Form 7 / HubSpot forms', [FORM_RE.test('<div class="wpcf7">'), FORM_RE.test('hbspt.forms.create({})')], [true, true]);
check('form: a search box is not a contact form', FORM_RE.test('<form role="search"><input type="text" name="q"></form>'), false);

// --- candidates.js on fixtures --------------------------------------------------------------
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ns-open-'));
const ct = [
  { nct: 'N1', sponsor: 'X', facility: 'Axis Clinical Research Center', city: 'Mexico City', country: 'Mexico', status: 'RECRUITING' },
  { nct: 'N2', sponsor: 'X', facility: 'Axis Clinical Research Center S.A. de C.V.', city: 'Mexico City', country: 'Mexico', status: 'RECRUITING' },
  { nct: 'N3', sponsor: 'X', facility: 'Research Site', city: 'Madrid', country: 'Spain', status: 'RECRUITING' },
  { nct: 'N4', sponsor: 'X', facility: 'Novartis Investigative Site', city: 'Paris', country: 'France', status: 'RECRUITING' },
  { nct: 'N5', sponsor: 'X', facility: 'Hospital Universitario de Monterrey', city: 'Monterrey', country: 'Mexico', status: 'RECRUITING' },
  { nct: 'N6', sponsor: 'X', facility: 'Kyiv Research Clinic', city: 'Moscow', country: 'Russian Federation', status: 'RECRUITING' },
  { nct: 'N7', sponsor: 'X', facility: 'Valley Research Partners', city: 'Austin', country: 'United States', status: 'RECRUITING' },
  { nct: 'N8', sponsor: 'X', facility: 'Done Research Center', city: 'Lima', country: 'Peru', status: 'COMPLETED' },
];
const ov = [
  { id: 'o1', name: 'Axis Clinical Research Center', cat: 'medical_research_institute', website: 'http://www.axisclinicals.com/', locality: 'Mexico City', country: 'MX', src: ['meta:CDLA-Permissive-2.0'] },
  { id: 'o2', name: 'Valley Research Partners', cat: 'health_care', website: 'https://valleyresearch.com', locality: 'Dallas', country: 'US', src: [] },
  { id: 'o3', name: 'Centro de Investigación Clínica del Norte', cat: 'doctors_office', website: 'https://cicn.mx', locality: 'Monterrey', country: 'MX', src: [] },
  { id: 'o4', name: 'Pine Dental Studio', cat: 'dental_clinic', website: 'https://pine.example', locality: 'Austin', country: 'US', src: [] },
  { id: 'o5', name: 'Clinical Research Hub', cat: 'research_institute', website: 'https://www.facebook.com/crhub', locality: 'Lima', country: 'PE', src: [] },
  { id: 'o6', name: 'Instituto Nacional de Pesquisa Clínica', cat: 'research_institute', website: 'https://inpc.gov.br', locality: 'Rio', country: 'BR', src: [] },
  { id: 'o7', name: 'Moscow Clinical Research', cat: 'research_institute', website: 'https://mcr.ru', locality: 'Moscow', country: 'RU', src: [] },
  { id: 'o8', name: 'Pfizer Clinical Research Unit', cat: 'research_institute', website: 'https://pfizer.example', locality: 'Brussels', country: 'BE', src: [] },
  { id: 'o9', name: 'Crimea Clinical Research', cat: 'research_institute', website: 'https://ccr.ua', locality: 'Sevastopol', region: 'Crimea', country: 'UA', src: [] },
];
fs.writeFileSync(path.join(dir, 'ct.jsonl'), ct.map((r) => JSON.stringify(r)).join('\n'));
fs.writeFileSync(path.join(dir, 'ov.json'), JSON.stringify(ov));
const wf = path.join(__dirname, '..', '..', 'workflows', 'trialsites.json');
const log = execFileSync('node', [path.join(__dirname, 'candidates.js'), wf, path.join(dir, 'ct.jsonl'), path.join(dir, 'ov.json'),
  path.join(dir, 'out.json')], { encoding: 'utf8' });
const out = JSON.parse(fs.readFileSync(path.join(dir, 'out.json'), 'utf8'));
const tally = JSON.parse(log).tally;
const byHost = {}; out.forEach((c) => { byHost[c.host] = c; });
check('match: the CT.gov facility gets the Overture website of the same name and city, both studies counted',
  [byHost['axisclinicals.com'] && byHost['axisclinicals.com'].source, byHost['axisclinicals.com'] && byHost['axisclinicals.com'].ctgov_recruiting],
  ['sites-ctgov', 2]);
check('match: same name in another city is no match (never guessed)', !!byHost['valleyresearch.com'] &&
  byHost['valleyresearch.com'].source === 'sites-overture', true);
check('overture-only: a research word in the name, no CT.gov facility', byHost['cicn.mx'] && byHost['cicn.mx'].source, 'sites-overture');
check('overture-only: no research word -> not a candidate', !!byHost['pine.example'], false);
check('screens: a social site, a .gov domain, an excluded country, a sponsor, occupied territory -> none',
  ['facebook.com', 'inpc.gov.br', 'mcr.ru', 'pfizer.example', 'ccr.ua'].filter((h) => byHost[h]), []);
check('screens: CT.gov placeholders and hospitals are counted, never matched',
  [tally.ctgov['sponsor-placeholder'], tally.ctgov['institution-name'], tally.ctgov['country-not-included'],
    tally.ctgov['not-recruiting-here']], [2, 1, 1, 1]);
check('the licences travel with the candidate', byHost['axisclinicals.com'].licences, ['meta:CDLA-Permissive-2.0']);
check('no contact field is carried', out.some((c) => 'emails' in c || 'phones' in c || 'email' in c), false);

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);

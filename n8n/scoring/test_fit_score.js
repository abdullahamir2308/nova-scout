// Unit test for the Workflow 3 weighted factors.
//
// The thing worth protecting here is Section 9's null-handling rule: "a
// confirmed miss should score lower than an honest unknown. Don't let
// extraction's correct refusal to guess become a scoring penalty." That is an
// ordering constraint (miss < unknown < confirmed), so it is asserted as one,
// not just as a set of magic numbers.
//
// Sliced out of code_evaluate.js rather than copied, so this tests the exact
// code build_workflow.py embeds in the workflow.
const path = require('path');
const { extractFunctions, runner } = require('./harness');

const F = extractFunctions(
  path.join(__dirname, 'code_evaluate.js'),
  '// Node body',
  [
    'WEIGHTS', 'TARGET_GEOGRAPHIES', 'canonicalAreas', 'inTargetGeography',
    'scoreGeography', 'scoreFounder', 'scoreOncology', 'scoreEmployees', 'scoreSite',
  ]
);

const t = runner('Workflow 3 weighted factors');

// --- the weights themselves ----------------------------------------------
const WEIGHT_TRIALS = 20; // lives in code_score.js; build_workflow.py guards both
const total = Object.keys(F.WEIGHTS).reduce((s, k) => s + F.WEIGHTS[k], 0);
t.check('the five in-node weights plus trials sum to 100', total + WEIGHT_TRIALS - F.WEIGHTS.trials, 100);
t.check('trials weight matches the spec table', F.WEIGHTS.trials, WEIGHT_TRIALS);

// --- geography ------------------------------------------------------------
t.check('a target country scores full weight',
  F.scoreGeography({ country: 'India' }).points, F.WEIGHTS.geography);
t.check('geography matching is case-insensitive',
  F.scoreGeography({ country: 'india' }).points, F.WEIGHTS.geography);
t.check('a multi-word target country matches',
  F.scoreGeography({ country: 'South Africa' }).points, F.WEIGHTS.geography);
t.check('surrounding whitespace is tolerated',
  F.scoreGeography({ country: '  Poland  ' }).points, F.WEIGHTS.geography);
t.check('a non-target country scores zero',
  F.scoreGeography({ country: 'Germany' }).points, 0);
t.check('a null country scores zero, not half -- geography is never unknown-neutral',
  F.scoreGeography({ country: null }).points, 0);
t.check('all 13 spec geographies are recognised',
  F.TARGET_GEOGRAPHIES.filter((g) => !F.inTargetGeography(g)).length, 0);

// --- founder: miss < unknown < confirmed ----------------------------------
const founderFull = F.scoreFounder({
  founder_name: 'Enrique Gaubeca',
  founder_linkedin: 'https://www.linkedin.com/in/enriquegaubeca',
  raw_extraction: { founder_linkedin_candidates: ['https://www.linkedin.com/in/enriquegaubeca'] },
});
const founderMiss = F.scoreFounder({
  founder_name: 'Jane Doe',
  founder_linkedin: null,
  raw_extraction: { founder_linkedin_candidates: ['https://www.linkedin.com/in/someone-else'] },
});
const founderNoCandidates = F.scoreFounder({
  founder_name: 'Jane Doe',
  founder_linkedin: null,
  raw_extraction: { founder_linkedin_candidates: [] },
});
const founderUnknown = F.scoreFounder({
  founder_name: null,
  founder_linkedin: null,
  raw_extraction: {},
});

t.check('founder + LinkedIn scores full weight', founderFull.points, F.WEIGHTS.founder);
t.check('founder + LinkedIn is basis=confirmed', founderFull.basis, 'confirmed');
t.check('no founder named is an honest unknown, scored at half weight',
  founderUnknown.points, F.WEIGHTS.founder / 2);
t.check('no founder named is basis=unknown', founderUnknown.basis, 'unknown');
t.check('named founder, candidates harvested, none matched, is basis=miss',
  founderMiss.basis, 'miss');
t.check('SECTION 9 ORDERING: a confirmed miss scores BELOW an honest unknown',
  founderMiss.points < founderUnknown.points, true);
t.check('SECTION 9 ORDERING: an honest unknown scores below a confirmed hit',
  founderUnknown.points < founderFull.points, true);
t.check('a named founder with no candidate URLs on the site is unknown, NOT a miss -- '
  + 'the matcher never had anything to match against',
  founderNoCandidates.basis, 'unknown');
t.check('...and so scores the same as any other honest unknown',
  founderNoCandidates.points, F.WEIGHTS.founder / 2);

// --- employees: miss < unknown < confirmed --------------------------------
const empIn = F.scoreEmployees({ employee_estimate: 60 });
const empOut = F.scoreEmployees({ employee_estimate: 300 });
const empNull = F.scoreEmployees({ employee_estimate: null });

t.check('headcount inside 5-100 scores full weight', empIn.points, F.WEIGHTS.employees);
t.check('the band is inclusive at the top', F.scoreEmployees({ employee_estimate: 100 }).points, F.WEIGHTS.employees);
t.check('the band is inclusive at the bottom', F.scoreEmployees({ employee_estimate: 5 }).points, F.WEIGHTS.employees);
t.check('a two-person shop is outside the band', F.scoreEmployees({ employee_estimate: 2 }).points, 0);
t.check('headcount outside the band scores zero', empOut.points, 0);
t.check('null headcount is an honest unknown at half weight', empNull.points, F.WEIGHTS.employees / 2);
t.check('SECTION 9 ORDERING: a confirmed out-of-band headcount scores BELOW a null one',
  empOut.points < empNull.points, true);
t.check('null headcount is basis=unknown, not miss', empNull.basis, 'unknown');

// --- oncology: the case-insensitivity that matters ------------------------
//
// The live store holds pre-enum rows in lowercase free text. A case-sensitive
// check would silently miss every one of them -- which is all 27 oncology leads
// currently in the database.
t.check('Title Case Oncology scores full weight',
  F.scoreOncology({ therapeutic_areas: ['Oncology'] }).points, F.WEIGHTS.oncology);
t.check('OLD LOWERCASE ROWS: "oncology" scores full weight too',
  F.scoreOncology({ therapeutic_areas: ['oncology'] }).points, F.WEIGHTS.oncology);
t.check('mixed old and new casing in one row both match',
  F.scoreOncology({ therapeutic_areas: ['dermatology', 'Oncology'] }).points, F.WEIGHTS.oncology);
t.check('ALL CAPS matches',
  F.scoreOncology({ therapeutic_areas: ['ONCOLOGY'] }).points, F.WEIGHTS.oncology);
t.check('oncology among many areas still scores',
  F.scoreOncology({ therapeutic_areas: ['cardiology', 'oncology', 'respiratory'] }).points,
  F.WEIGHTS.oncology);
t.check('a non-oncology CRO scores zero',
  F.scoreOncology({ therapeutic_areas: ['dermatology', 'respiratory'] }).points, 0);
t.check('...and is a confirmed miss, since areas were stated',
  F.scoreOncology({ therapeutic_areas: ['dermatology'] }).basis, 'miss');
t.check('no areas extracted at all is unknown, not a miss',
  F.scoreOncology({ therapeutic_areas: [] }).basis, 'unknown');
t.check('a null areas column does not throw',
  F.scoreOncology({ therapeutic_areas: null }).points, 0);
t.check('off-enum values are dropped, never guessed into a neighbour '
  + '(the same rule code_normalise.js applies)',
  F.canonicalAreas(['cardiology', 'medtech', 'oncology']), ['Oncology']);
t.check('British "haematology" is off-enum and dropped, not mapped',
  F.canonicalAreas(['haematology']), []);
t.check('case-variant duplicates collapse to one',
  F.canonicalAreas(['Oncology', 'oncology', 'ONCOLOGY']), ['Oncology']);

// --- site quality ---------------------------------------------------------
function site(fetchInfo) {
  return F.scoreSite({ raw_extraction: { fetch: fetchInfo } });
}
const rich = site({ prompt_chars: 24000, discovered: ['a', 'b', 'c', 'd'], reached_via: 'https' });
const thin = site({ prompt_chars: 334, discovered: [], reached_via: 'https' });

t.check('a deep, content-rich site scores full weight', rich.points, F.WEIGHTS.site);
t.check('a one-page brochure scores zero', thin.points, 0);
t.check('a mid-weight site lands in between',
  site({ prompt_chars: 7000, discovered: ['a'], reached_via: 'https' }).points, 6);
t.check('no fetch record is an honest unknown at half weight',
  F.scoreSite({ raw_extraction: {} }).points, F.WEIGHTS.site / 2);
t.check('...and is basis=unknown', F.scoreSite({ raw_extraction: {} }).basis, 'unknown');
t.check('a broken certificate costs 2 points',
  site({ prompt_chars: 24000, discovered: ['a', 'b', 'c'], reached_via: 'https+insecure' }).points,
  F.WEIGHTS.site - 2);
t.check('plain-HTTP-only costs 2 points',
  site({ prompt_chars: 24000, discovered: ['a', 'b', 'c'], reached_via: 'http' }).points,
  F.WEIGHTS.site - 2);
t.check('a null reached_via (rows predating the field) is NOT penalised',
  site({ prompt_chars: 24000, discovered: ['a', 'b', 'c'], reached_via: null }).points,
  F.WEIGHTS.site);
t.check('the TLS penalty cannot push a score below zero',
  site({ prompt_chars: 100, discovered: [], reached_via: 'http' }).points, 0);
t.check('no factor can ever exceed its own weight',
  [rich, founderFull, empIn].filter((f) => f.points > f.max).length, 0);

t.done();

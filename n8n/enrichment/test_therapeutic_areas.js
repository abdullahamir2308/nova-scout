// Unit test for the therapeutic-area enum canonicaliser.
//
// The schema `enum` in build_workflow.py is the primary constraint -- Ollama's
// grammar cannot emit an off-list value. This function is the second line of
// defence for the one path that bypasses the grammar (the fenced-block JSON
// rescue in code_normalise.js), and it is what guarantees casing is canonical.
//
// Sliced out of code_normalise.js rather than copied, so this tests the exact
// code build_workflow.py embeds in the workflow.
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'code_normalise.js'), 'utf8');
const start = src.indexOf('function cleanStr');
const end = src.indexOf('// The HTTP node is set to continue on error');
if (start < 0 || end < 0 || end <= start) {
  throw new Error('could not slice canonicalAreas out of code_normalise.js');
}
const { canonicalAreas, THERAPEUTIC_AREAS } = new Function(
  src.slice(start, end) + '\nreturn { canonicalAreas, THERAPEUTIC_AREAS };'
)();

// [label, input, expected kept, expected dropped]
const cases = [
  ['exact enum values pass through',
    ['Oncology', 'Respiratory'], ['Oncology', 'Respiratory'], []],
  ['lowercase is canonicalised, not dropped',
    ['oncology'], ['Oncology'], []],
  ['multi-word value, any casing',
    ['central nervous system', 'RARE DISEASES'], ['Central Nervous System', 'Rare Diseases'], []],
  ['surrounding whitespace tolerated',
    ['  Immunology  '], ['Immunology'], []],
  ['the exact drift this change fixes: cardiology is not the enum value',
    ['cardiology', 'Cardiovascular'], ['Cardiovascular'], ['cardiology']],
  ['singular/plural drift is dropped, not silently accepted',
    ['Infectious Diseases'], [], ['Infectious Diseases']],
  ['industry-term leakage is dropped',
    ['medtech', 'pharma', 'Oncology'], ['Oncology'], ['medtech', 'pharma']],
  // The six categories added after the first build showed Other absorbing real
  // volume. Each was previously an off-enum drop; each must now be a keeper.
  ['all six added categories are accepted',
    ['Dermatology', 'Rheumatology', 'Ophthalmology', 'Gastroenterology', 'Nephrology', 'Hematology'],
    ['Dermatology', 'Rheumatology', 'Ophthalmology', 'Gastroenterology', 'Nephrology', 'Hematology'], []],
  ['added categories canonicalise from lowercase, as the old free-text rows are stored',
    ['dermatology', 'rheumatology', 'ophthalmology', 'gastroenterology', 'nephrology', 'hematology'],
    ['Dermatology', 'Rheumatology', 'Ophthalmology', 'Gastroenterology', 'Nephrology', 'Hematology'], []],
  ['British "haematology" is NOT the enum spelling -- the prompt maps it, the normaliser does not',
    ['haematology'], [], ['haematology']],
  ['a specialty still outside the expanded list is dropped by the normaliser',
    ['urology', 'Dermatology'], ['Dermatology'], ['urology']],
  ['case-variant duplicates collapse to one',
    ['Oncology', 'oncology', 'ONCOLOGY'], ['Oncology'], []],
  ['duplicate drops are recorded once',
    ['pharma', 'Pharma'], [], ['pharma', 'Pharma']],
  ['Other survives as the catch-all',
    ['Other'], ['Other'], []],
  ['nulls and empty strings are skipped, not dropped-with-a-record',
    [null, '', '  ', 'n/a', 'Oncology'], ['Oncology'], []],
  ['non-array input yields an empty result', null, [], []],
  ['undefined input yields an empty result', undefined, [], []],
  ['empty array stays empty', [], [], []],
];

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

let pass = 0;
for (const [label, input, wantKept, wantDropped] of cases) {
  const got = canonicalAreas(input);
  const ok = eq(got.kept, wantKept) && eq(got.dropped, wantDropped);
  pass += ok ? 1 : 0;
  console.log(
    `${ok ? 'PASS' : 'FAIL'}  ${label}` +
      (ok ? '' : `\n        got  kept=${JSON.stringify(got.kept)} dropped=${JSON.stringify(got.dropped)}` +
                 `\n        want kept=${JSON.stringify(wantKept)} dropped=${JSON.stringify(wantDropped)}`)
  );
}

// The taxonomy is LOCKED in NovaScout_MasterRef.md, which is the single source
// of truth. Read it from the doc rather than pinning a copy here -- a third
// hardcoded list is a third thing to forget. This mirrors the check
// build_workflow.py runs, but without needing the build to have been run.
const MASTER_REF =
  process.env.NOVASCOUT_MASTER_REF || path.join(__dirname, '..', '..', 'NovaScout_MasterRef.md');
// Normalise CRLF: the doc is edited on Windows, and Node (unlike Python's
// universal newlines) hands back the raw \r\n, which would break the fence match.
const doc = fs.readFileSync(MASTER_REF, 'utf8').replace(/\r\n/g, '\n');
const anchor = doc.match(/\*\*Therapeutic area taxonomy[^\n]*\*\*/);
if (!anchor) throw new Error(`taxonomy section not found in ${MASTER_REF}`);
const block = doc.slice(anchor.index + anchor[0].length).match(/\n```\n([\s\S]*?)\n```/);
if (!block) throw new Error(`no fenced list follows the taxonomy heading in ${MASTER_REF}`);
const LOCKED = block[1].split('\n').map((l) => l.trim()).filter(Boolean);

const taxonomyOk = eq(THERAPEUTIC_AREAS, LOCKED);
pass += taxonomyOk ? 1 : 0;
console.log(
  `${taxonomyOk ? 'PASS' : 'FAIL'}  code_normalise.js matches the Master Ref (${LOCKED.length} categories)` +
    (taxonomyOk
      ? ''
      : `\n        doc: ${JSON.stringify(LOCKED)}\n        js:  ${JSON.stringify(THERAPEUTIC_AREAS)}`)
);

const total = cases.length + 1;
console.log(`\n${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);

// company_type, a closed list since 2026-10-09 (Section 9, Workflow 3: "Not a
// CRO, site or SMO"). The schema enum is the real constraint; this proves the
// normaliser's second line of defence and the schema agree.
//
//     python n8n/enrichment/build_workflow.py && node n8n/enrichment/test_company_type.js
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'code_normalise.js'), 'utf8');
const start = src.indexOf('const COMPANY_TYPES');
const end = src.indexOf('// The LOCKED therapeutic-area taxonomy');
const { companyType, COMPANY_TYPES } = new Function(src.slice(start, end) + '\nreturn { companyType, COMPANY_TYPES };')();
const wf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'enrichment.json'), 'utf8'));
const shipped = JSON.stringify(wf);

let failed = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed++;
  console.log((ok ? '  ok   ' : '  FAIL ') + label + (ok ? '' : '\n         expected ' + JSON.stringify(expected) + '\n         actual   ' + JSON.stringify(actual)));
}

console.log('Workflow 2 -- company_type\n');
check('the five values, in order', COMPANY_TYPES, ['CRO', 'site', 'SMO', 'other', 'unclear']);
check('each value passes through', COMPANY_TYPES.map(companyType), COMPANY_TYPES);
check('case is normalised to the enum spelling', [companyType('cro'), companyType('Site'), companyType('smo')], ['CRO', 'site', 'SMO']);
check('a free-text label from the rescue path is null, never a guess',
  [companyType('site management organisation'), companyType('consultancy'), companyType('')], [null, null, null]);
check('null and undefined are null', [companyType(null), companyType(undefined)], [null, null]);
// The schema ships pretty-printed inside an escaped expression string (the
// `}}` rule), so flatten escapes and whitespace before looking for it.
const flat = shipped.split('\\n').join('').split('\\"').join('"').replace(/\s+/g, '');
check('the shipped schema carries the enum',
  flat.indexOf('"company_type":{"type":"string","enum":["CRO","site","SMO","other","unclear"]}') !== -1, true);
check('the shipped schema no longer asks for is_cro', /"is_cro":\s*\{/.test(shipped), false);
check('the shipped prompt defines all five', ['CRO:', 'site:', 'SMO:', 'other:', 'unclear:'].every((k) => shipped.indexOf(k) !== -1), true);

console.log('\n' + (8 - failed) + '/8 passed');
if (failed) process.exit(1);

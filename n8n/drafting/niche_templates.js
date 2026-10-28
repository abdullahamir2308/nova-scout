// The approved wording of every niche, as one JSON object per niche -- the
// shape migration 025 stores in niches.templates. Read straight out of the
// shipped sources (code_template.js for 'cro', code_site_template.js for
// 'research_site'), never retyped, so `node niche_templates.js` prints exactly
// what the code sends. test_site_drift_guards.py compares it with the
// migration's seed and the live table.
const fs = require('fs');
const path = require('path');

function constants(file, names, prefix) {
  const src = (prefix || '') + fs.readFileSync(path.join(__dirname, file), 'utf8');
  const lib = src.slice(0, src.lastIndexOf('// Node body'));
  return new Function(lib + '\nreturn {' + names.join(',') + '};')();
}

function templates() {
  const cro = constants('code_template.js', [
    'SUBJECT_WITH_FACT', 'SUBJECT_NO_FACT', 'SUBJECT_FACT_TRIALS', 'SUBJECT_FACT_AREAS', 'SUBJECT_FACT_CITY',
    'GREETING_NAMED', 'GREETING_UNNAMED', 'INTRO_LINE', 'SMYKM_TRIALS_A', 'SMYKM_TRIALS_B', 'SMYKM_AREAS_A',
    'SMYKM_AREAS_B', 'SMYKM_CITY', 'SMYKM_COMPANY', 'PITCH_LINE', 'CLOSE_A', 'CLOSE_B', 'SIGN_OFF',
    'FOLLOW_UP_1_LINE', 'FOLLOW_UP_2_LINE', 'SMYKM_ORDER', 'ARM_BY_PARITY',
  ]);
  const shared = fs.readFileSync(path.join(__dirname, 'code_template.js'), 'utf8');
  const sharedLib = shared.slice(0, shared.indexOf('// Node body'));
  const site = constants('code_site_template.js', [
    'SITE_SUBJECT_WITH_FACT', 'SITE_SUBJECT_NO_FACT', 'SITE_INTRO_LINE', 'SITE_SMYKM', 'SITE_PITCH_LINE',
    'SITE_SIGN_OFF', 'SITE_FOLLOW_UP_1_LINE', 'SITE_FOLLOW_UP_2_LINE', 'CLOSE_A', 'CLOSE_B', 'GREETING_NAMED',
    'GREETING_UNNAMED', 'SUBJECT_FACT_TRIALS', 'SUBJECT_FACT_AREAS', 'SUBJECT_FACT_CITY', 'SMYKM_ORDER',
    'ARM_BY_PARITY',
  ], sharedLib);
  return {
    cro: {
      subject: { with_fact: cro.SUBJECT_WITH_FACT, no_fact: cro.SUBJECT_NO_FACT,
        facts: { trials: cro.SUBJECT_FACT_TRIALS, areas: cro.SUBJECT_FACT_AREAS, city: cro.SUBJECT_FACT_CITY } },
      greeting: { named: cro.GREETING_NAMED, unnamed: cro.GREETING_UNNAMED },
      intro: cro.INTRO_LINE,
      smykm: { CRO: { trials_A: cro.SMYKM_TRIALS_A, trials_B: cro.SMYKM_TRIALS_B, areas_A: cro.SMYKM_AREAS_A,
        areas_B: cro.SMYKM_AREAS_B, city: cro.SMYKM_CITY, company: cro.SMYKM_COMPANY } },
      smykm_order: cro.SMYKM_ORDER,
      pitch: { CRO: cro.PITCH_LINE },
      close: { A: cro.CLOSE_A, B: cro.CLOSE_B },
      arms: cro.ARM_BY_PARITY,
      sign_off: cro.SIGN_OFF,
      follow_up: { '1': cro.FOLLOW_UP_1_LINE, '2': cro.FOLLOW_UP_2_LINE },
    },
    research_site: {
      subject: { with_fact: site.SITE_SUBJECT_WITH_FACT, no_fact: site.SITE_SUBJECT_NO_FACT,
        facts: { trials: site.SUBJECT_FACT_TRIALS, areas: site.SUBJECT_FACT_AREAS, city: site.SUBJECT_FACT_CITY } },
      greeting: { named: site.GREETING_NAMED, unnamed: site.GREETING_UNNAMED },
      intro: site.SITE_INTRO_LINE,
      smykm: { site: site.SITE_SMYKM.site, SMO: site.SITE_SMYKM.smo },
      smykm_order: site.SMYKM_ORDER,
      pitch: { site: site.SITE_PITCH_LINE.site, SMO: site.SITE_PITCH_LINE.smo },
      close: { A: site.CLOSE_A, B: site.CLOSE_B },
      arms: site.ARM_BY_PARITY,
      sign_off: site.SITE_SIGN_OFF,
      follow_up: { '1': site.SITE_FOLLOW_UP_1_LINE, '2': site.SITE_FOLLOW_UP_2_LINE },
    },
  };
}

module.exports = { templates };
if (require.main === module) process.stdout.write(JSON.stringify(templates(), null, 2) + '\n');

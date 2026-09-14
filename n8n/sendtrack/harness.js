// Runs a Workflow 6 Code-node body standalone with a mocked n8n context, so the
// exact JS that ships inside the workflow JSON is what the tests exercise.
//
// Same contract as ../drafting/harness.js. `transform` mirrors the substitutions
// build_workflow.py makes on the way in (the __SIGNATURE__ and __OWN_DOMAIN__
// build constants); without it the tests would exercise a different string than
// the workflow ships. Workflow 6 adds "Run Once for All Items" nodes, so this
// also mocks `$input.first()` and `$(name).first()`.
const fs = require('fs');
const path = require('path');

// Globals the n8n Code sandbox does NOT provide (verified against the live
// instance during Workflow 2). Shadowed as undefined so code relying on them
// fails here too, instead of passing locally and silently no-op-ing in
// production the way `new URL()` did.
const SANDBOX_MISSING = ['URL', 'URLSearchParams', 'fetch', 'AbortController'];

// Slices the helper functions out of a Code-node source, so a unit test calls
// the real function rather than a copy of it. `endMarker` marks the start of
// the node body, which cannot run outside n8n.
function extractFunctions(file, endMarker, names, transform) {
  let src = fs.readFileSync(file, 'utf8');
  if (transform) src = transform(src);
  const end = src.indexOf(endMarker);
  if (end < 0) {
    throw new Error('marker ' + JSON.stringify(endMarker) + ' not found in ' + path.basename(file));
  }
  const fn = new Function(
    ...SANDBOX_MISSING,
    '"use strict";\n' + src.slice(0, end) + '\nreturn { ' + names.join(', ') + ' };'
  );
  return fn(...SANDBOX_MISSING.map(() => undefined));
}

function mockDollar(upstream, i) {
  return (name) => {
    const feed = (upstream || {})[name];
    if (!feed) throw new Error('no mocked upstream node named ' + JSON.stringify(name));
    return { item: feed[i], first: () => feed[0], all: () => feed };
  };
}

function compile(file, transform) {
  let src = fs.readFileSync(file, 'utf8');
  if (transform) src = transform(src);
  return new Function(
    '$input',
    '$',
    ...SANDBOX_MISSING,
    '"use strict"; return (() => {' + src + '})()'
  );
}

// "Run Once for Each Item": the body runs once per item, `$input.item` is that
// item, `$(name).item` the positionally paired upstream item.
function runForEachItem(file, items, upstream, transform) {
  const fn = compile(file, transform);
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const $input = { item: items[i], first: () => items[0], all: () => items };
    out.push(fn($input, mockDollar(upstream, i), ...SANDBOX_MISSING.map(() => undefined)));
  }
  return out;
}

// "Run Once for All Items": the body runs once and returns an array of items.
function runOnceForAll(file, items, upstream, transform) {
  const fn = compile(file, transform);
  const $input = { item: items[0], first: () => items[0], all: () => items };
  return fn($input, mockDollar(upstream, 0), ...SANDBOX_MISSING.map(() => undefined));
}

// Minimal assertion helpers, matching the style of the Workflow 2 and 4 tests:
// print every case, exit non-zero on any failure.
function runner(title) {
  let failed = 0;
  let passed = 0;
  console.log(title + '\n');
  return {
    check(label, actual, expected) {
      const a = JSON.stringify(actual);
      const e = JSON.stringify(expected);
      if (a === e) {
        passed++;
        console.log('  ok   ' + label);
      } else {
        failed++;
        console.log('  FAIL ' + label + '\n         expected ' + e + '\n         actual   ' + a);
      }
    },
    done() {
      console.log('\n' + passed + ' passed, ' + failed + ' failed');
      if (failed) process.exit(1);
    },
  };
}

module.exports = { extractFunctions, runForEachItem, runOnceForAll, runner, SANDBOX_MISSING };

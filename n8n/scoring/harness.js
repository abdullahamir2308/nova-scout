// Runs a Workflow 3 Code-node body standalone with a mocked n8n context, so the
// exact JS that ships inside scoring.json is what the tests exercise.
//
// Workflow 2's harness only needed `$input.all()`. Every Code node here runs in
// "Run Once for Each Item" mode and reaches back to a named upstream node
// (`$('Evaluate Lead').item.json`), so this adds `$input.item` and `$(name)`.
const fs = require('fs');
const path = require('path');

// Globals the n8n Code sandbox does NOT provide (verified against the live
// instance during Workflow 2). Shadowed as undefined so code relying on them
// fails here too, instead of passing locally and silently no-op-ing in
// production the way `new URL()` did.
const SANDBOX_MISSING = ['URL', 'URLSearchParams', 'fetch', 'AbortController'];

// Slices the helper functions out of a Code-node source, so a unit test calls
// the real function rather than a copy of it. `end` marks the start of the node
// body, which cannot run outside n8n.
function extractFunctions(file, endMarker, names) {
  const src = fs.readFileSync(file, 'utf8');
  const end = src.indexOf(endMarker);
  if (end < 0) {
    throw new Error('marker ' + JSON.stringify(endMarker) + ' not found in ' + path.basename(file));
  }
  return new Function(src.slice(0, end) + '\nreturn { ' + names.join(', ') + ' };')();
}

// Runs a full node body once per item. `upstream` maps a node name to the array
// of items that node produced, positionally paired with `items` -- which is how
// n8n resolves `$('Some Node').item` inside a Run Once for Each Item node.
function runForEachItem(file, items, upstream) {
  const src = fs.readFileSync(file, 'utf8');
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const $input = { item: items[i], all: () => items };
    const $ = (name) => {
      const feed = (upstream || {})[name];
      if (!feed) throw new Error('no mocked upstream node named ' + JSON.stringify(name));
      return { item: feed[i] };
    };
    const fn = new Function(
      '$input',
      '$',
      ...SANDBOX_MISSING,
      '"use strict"; return (() => {' + src + '})()'
    );
    out.push(fn($input, $, ...SANDBOX_MISSING.map(() => undefined)));
  }
  return out;
}

// Minimal assertion helpers, matching the style of the Workflow 2 tests: print
// every case, exit non-zero on any failure.
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

module.exports = { extractFunctions, runForEachItem, runner };

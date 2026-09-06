// Runs a Workflow 3b Code-node body standalone with a mocked n8n context, so the
// exact JS that ships inside apollo-contacts.json is what the tests exercise.
//
// Extends the Workflow 3 harness in two ways this stage needs:
//   - `$('Node').first()`, because the CSV email index is one item consulted by
//     every lead, not an item paired with each lead;
//   - `runForAllItems`, because Index Scraped Emails runs in Run Once for All
//     Items mode and returns an array.
const fs = require('fs');
const path = require('path');

// Globals the n8n Code sandbox does NOT provide (verified against the live
// instance during Workflow 2). Shadowed as undefined so code relying on them
// fails here too, instead of passing locally and silently no-op-ing in
// production the way `new URL()` did.
const SANDBOX_MISSING = ['URL', 'URLSearchParams', 'fetch', 'AbortController'];

function extractFunctions(file, endMarker, names) {
  const src = fs.readFileSync(file, 'utf8');
  const end = src.indexOf(endMarker);
  if (end < 0) {
    throw new Error('marker ' + JSON.stringify(endMarker) + ' not found in ' + path.basename(file));
  }
  return new Function(src.slice(0, end) + '\nreturn { ' + names.join(', ') + ' };')();
}

// `upstream` maps a node name to the items that node produced. For a node read
// with `.item`, the array is paired positionally with `items`; for one read with
// `.first()`, only element 0 is ever consulted.
function mockDollar(upstream, i) {
  return function (name) {
    const feed = (upstream || {})[name];
    if (!feed) throw new Error('no mocked upstream node named ' + JSON.stringify(name));
    return {
      item: feed[i],
      first: () => feed[0],
      last: () => feed[feed.length - 1],
      all: () => feed,
    };
  };
}

function runBody(src, $input, $) {
  const fn = new Function(
    '$input',
    '$',
    ...SANDBOX_MISSING,
    '"use strict"; return (() => {' + src + '})()'
  );
  return fn($input, $, ...SANDBOX_MISSING.map(() => undefined));
}

function runForEachItem(file, items, upstream) {
  const src = fs.readFileSync(file, 'utf8');
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const $input = { item: items[i], all: () => items };
    out.push(runBody(src, $input, mockDollar(upstream, i)));
  }
  return out;
}

function runForAllItems(file, items, upstream) {
  const src = fs.readFileSync(file, 'utf8');
  const $input = { all: () => items, first: () => items[0] };
  return runBody(src, $input, mockDollar(upstream, 0));
}

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

module.exports = { extractFunctions, runForEachItem, runForAllItems, runner };

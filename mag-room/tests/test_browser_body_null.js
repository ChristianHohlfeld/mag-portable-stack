#!/usr/bin/env node
'use strict';
// The browser can briefly have no document.body during navigation.
// Run the actual read-only JS expressions embedded in the C++ controller.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'native', 'browser.cpp'), 'utf8');
const read = source.match(/out = c\.eval\("([^"]+)"\s*"([^"]+)"\);/);
assert(read, 'read expression located in browser controller');
const expr = read[1] + read[2];
assert(expr.includes('body?.innerText'), 'browser read must permit a missing body');
const snap = source.match(/out\["text"\] = c\.eval\("([^"]+)"\);/);
assert(snap, 'snapshot text expression located');
for (const [body,expected] of [[null,''],[{innerText:'Hello from page'},'Hello from page']]) {
  const ctx = {location: {href:'https://example.test'},document: {title:'Page',body}};
  assert.equal(vm.runInNewContext(expr,ctx).text, expected);
  assert.equal(vm.runInNewContext(snap[1],ctx), expected);
}
console.log('Browser read/snapshot: transient body=null does not produce script HTTP 500');

#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const src=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const html=src('web/app/panels.js'),native=src('native/assist.hpp');
assert(html.includes("type: 'number', min: 1, max: 44640, step: 1"));
assert(!html.includes("type: 'number', min: 15, step: 15"));
assert(native.includes('!std::isfinite(n) || n < 1 || n > 60.0 * 24 * 31 || std::floor(n) != n'));
assert(native.includes('w["every_minutes"] = n;'));
assert(native.includes('from + (int64_t)(jnum(w, "every_minutes") * 60)'));
console.log('bot routine minutes: 1 and 3 permitted, invalid intervals rejected, scheduler uses real minutes OK');

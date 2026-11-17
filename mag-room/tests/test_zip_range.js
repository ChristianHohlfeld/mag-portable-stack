#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const api=source('native/api.hpp'),ui=source('web/app/files.js');
const section=api.slice(api.indexOf('void files_zip('),api.indexOf('// ---- the bots\' sites'));
assert(section.includes('::open(temp.c_str()'), 'ZIP must be created before response');
assert(section.includes('zipwrite('));
assert(section.includes('http::send_file(fd, r, temp, "application/zip", h)'),'Range-aware send_file');
assert(section.includes('unlink(temp.c_str())'));
assert(!section.includes('http::write_all(fd,'),'no direct streaming ZIP');
assert(api.includes('filename=\\\"') || api.includes('filename=\\\"'), 'quoted ASCII filename');
assert(api.includes('filename*=UTF-8'), 'RFC5987 name preserved');
assert(ui.includes("const zip = el('button'"), 'one explicit ZIP download button');
assert(ui.includes('await fetch(url,'), 'fetch to inspect HTTP status');
assert(ui.includes('response.blob()'), 'one download blob');
assert(ui.includes("MAG.toast('ZIP-Download fehlgeschlagen: "));
console.log('ZIP: temp-file Range response, dual Content-Disposition, single fetch and toast OK');

#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const read = p => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const engine = read('native/engine.hpp'), ui = read('web/app/messages.js');
const tts = read('web/app/tts-worker.js');
assert(engine.includes('while (!t.empty())'), 'no hardcoded eight-label cap in C++');
assert(engine.includes('strip_self(t.held, name, names())'));
assert(ui.includes('const botNames = (S.bots || [])'));
assert(ui.includes('for (;;)'), 'all leading labels stripped in UI');
assert(!ui.includes("lone PASS');"), 'no injected comment in regex replacement');
assert(!tts.includes('(?:Lena|Noah)'), 'TTS must not hardcode roster');
const {spoken} = require('../web/app/tts-worker.js');
for (const input of [
  '[Ada]: **Mira:** Zora: Hallo!',
  '**Ada:** [Mira]: Zora, Hallo!',
  '[Ada] [Mira]: Hallo!',
  'Ada: Ada: Ada: Hallo!',
  'Ada:\nMira:\tHallo!'
]) assert.equal(spoken(input, 'de'), 'Hallo!', input);
assert.equal(spoken('Hallo Ada: und Mira.', 'de'), 'Hallo Ada: und Mira.');
console.log('speaker labels: unlimited stacks and dynamic names in engine, UI and TTS OK');

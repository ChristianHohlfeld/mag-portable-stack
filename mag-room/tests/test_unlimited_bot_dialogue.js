#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const src = fs.readFileSync(path.join(__dirname,'../native/engine.hpp'),'utf8');
assert(src.includes('static bool effective_pass('));
assert(src.includes('(long)jnum(settings, "bot_rounds", default_rounds) != 0'));
assert.equal((src.match(/const bool pass_enabled = effective_pass\(settings, room_\.cfg\.bot_rounds\);/g) || []).length, 2,
  'runtime and prompt must use same effective PASS policy');
assert(src.includes('if (passed && pass_enabled_)'));
assert(src.includes('pass_enabled_ = effective_pass(startup_settings, room_.cfg.bot_rounds);'), 'restart must restore effective policy');
assert(src.includes('round_limit_ = (long)jnum(startup_settings, "bot_rounds", room_.cfg.bot_rounds);'));
assert(src.includes('body["messages"].push(cue);'), 'directive must be request-only');
assert(!src.includes('invite["_auto_chat"] = true;'), 'no synthetic user turn persisted');
assert(!src.includes('last["_note"] = notes;'), 'PASS cannot contaminate saved wire');
assert(src.includes('if (!visible) return;'),
  'an invisible or rejected reply must not start another bot round, even when unlimited');
assert(src.includes('if (dialogue_stopped_ || (limit > 0 && bot_turns_ >= limit)) return;'));
console.log('unlimited PASS policy: one predicate for settings, prompt and handoff');

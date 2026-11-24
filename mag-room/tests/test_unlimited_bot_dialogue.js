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
assert(src.includes('if (!recovery_handoff || !auto_burst_ || !auto_enabled_ || pass_enabled_) return;'),
  'invisible replies cannot hand off except an explicit recovery yield in active unattended PASS-off chat');
assert(src.includes('if (++passes_ >= (int)std::max<size_t>(1, active)) {') &&
       src.includes('auto_last_ms_ = now_ms();'),
  'recovery yields must stop after all active bots fail and enter the normal cooldown');
assert(src.includes('if (dialogue_stopped_ || (limit > 0 && bot_turns_ >= limit)) return;'));
console.log('unlimited PASS policy: one predicate for settings, prompt and handoff');

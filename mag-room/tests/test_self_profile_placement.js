#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const src = fs.readFileSync(path.join(__dirname, '../web/app/panels.js'), 'utf8');
new Function(src);
const bot = src.slice(src.indexOf('function botSheet('), src.indexOf('function selfProfile('));
const global = src.slice(src.indexOf('function settingsSheet('));
const within = (parts, text) => parts.indexOf(text);
const order = [
  "row('Name'", "row('Nachdenken'", "'Art'", "selfProfile(b)",
  "text: 'Stimme und Tempo'", "// Plugins: which",
  "text: 'Erinnerungen'", "text: 'Jobs'", "text: 'Sicherungen der VM'"
].map((label) => within(bot, label));
assert(order.every((n) => n >= 0), 'every expected per-bot control is present');
assert(order.every((n, i) => i === 0 || n > order[i - 1]), 'bot settings appear in the requested order');
assert(bot.includes("text: 'Selbstbild'"), 'self profile has its own title');
assert(bot.includes("'Art', el('small', { text: 'Wie dieser Bot arbeiten soll. Das ist eine Vorgabe.' })"),
  'persona is labeled Art and described as instruction');
assert(bot.includes("{ persona: e.target.value }"), 'existing persona persisted without migration');
assert(bot.includes("'Die Aufgabe ist die Nachricht im Chat.'"), 'context hint follows self fields');
assert(!global.includes('S.bots.map(selfProfile)') && !global.includes('selfProfile(b)'),
  'global settings do not duplicate per-bot profile');
assert(global.includes("'Inneres Selbstgespräch'"), 'room-wide thought visibility switch remains');
const voiceControls = bot.slice(bot.indexOf("// One voice and playback rate per bot"), bot.indexOf("// Plugins: which"));
assert(voiceControls.includes('MAG.voices.of(id)'), 'individual bot voice loaded by ID');
assert(voiceControls.includes("MAG.voices.set(id, { voice })") && voiceControls.includes("MAG.voices.set(id, { rate: +rate })"),
  'individual bot voice and tempo are saved using the original localStorage-backed API');
const globalVoice = global.slice(global.indexOf("// Global reading settings"), global.indexOf("const sys ="));
assert(globalVoice.includes("MAG.voices.of('chris')"), 'global Read aloud applies to owner');
assert(!globalVoice.includes('S.bots') && !globalVoice.includes('MAG.voices.of(b.id)'),
  'global Read aloud no longer shows bot voices');
assert(src.includes("call('GET', path + '/self')") && src.includes("call('PUT', path, { self: { [key]: value } })"),
  'existing private self API is untouched');
assert(src.includes("status.textContent = 'Nicht gespeichert – erneut versuchen'"),
  'unsaved edits remain available for retry');
console.log('Bot sheet placement, persona, voice persistence, global settings and syntax OK');

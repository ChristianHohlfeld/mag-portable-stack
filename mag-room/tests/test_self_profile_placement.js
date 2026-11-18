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
  "text: 'Eigenes Ziel'", "text: 'Stimme und Tempo'", "// Plugins: which",
  "text: 'Erinnerungen'", "text: 'Jobs'", "text: 'Sicherungen der VM'"
].map((label) => bot.indexOf(label));
assert(order.every((n) => n >= 0), 'every expected per-bot control is present');
assert(order.every((n, i) => i === 0 || n > order[i - 1]), 'bot settings appear in the requested order');
assert(bot.includes("text: 'Selbstbild'"), 'narrator has its own title');
assert(bot.includes("'Art', el('small', { text: 'Wie dieser Bot arbeiten soll. Das ist eine Vorgabe.' })"),
  'persona still distinct and unchanged');
assert(bot.includes("{ persona: e.target.value }"), 'existing persona stored separately');
assert(bot.includes("willCard(b)"), 'owner goal card belongs to bot sheet');
assert(!global.includes('S.bots.map(selfProfile)') && !global.includes('selfProfile(b)'), 'self is not global');
assert(global.includes("'Inneres Selbstgespräch'"), 'room-wide switch remains');
assert(src.includes("call('GET', path + '/self')"), 'owner-only self API preserved');
assert(src.includes("self: { narrator: value }"), 'single multiline narrator field');
assert(src.includes("call('GET', path).then(render)"), 'private owner will loaded');
assert(src.includes("call('PUT', path, { action: kind"), 'owner will update supported');
assert(src.includes("status.textContent = 'Nicht gespeichert – erneut versuchen'"), 'failed narrator save retains draft');
const controls = bot.slice(bot.indexOf("// One voice and playback rate per bot"), bot.indexOf("// Plugins: which"));
assert(controls.includes('MAG.voices.of(id)') && controls.includes("MAG.voices.set(id, { voice })"),
  'voice and tempo remain stored by the original API');
const voices = global.slice(global.indexOf("// Global reading settings"), global.indexOf("const sys ="));
assert(voices.includes("MAG.voices.of('chris')") && !voices.includes('MAG.voices.of(b.id)'),
  'global voice remains Chris-only');
console.log('Bot sheet placement, persona, voice persistence, global settings and syntax OK');

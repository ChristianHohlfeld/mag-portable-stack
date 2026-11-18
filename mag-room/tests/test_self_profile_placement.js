#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const src = fs.readFileSync(path.join(__dirname, '../web/app/panels.js'), 'utf8');
new Function(src);
const bot = src.slice(src.indexOf('function botSheet('), src.indexOf('function selfProfile('));
const global = src.slice(src.indexOf('function settingsSheet('));
assert(bot.includes("text: 'Selbstbild'") && bot.includes('selfProfile(b)'), 'per-bot self profile belongs in bot sheet');
assert(bot.includes("'Art und Auftrag'") && bot.includes('b.persona'), 'persona remains separate');
assert(!global.includes('S.bots.map(selfProfile)') && !global.includes('selfProfile(b)'), 'global settings must not duplicate per-bot fields');
assert(global.includes("'Inneres Selbstgespräch'"), 'room-wide visibility switch stays global');
assert(src.includes("call('GET', path + '/self')") && src.includes("call('PUT', path, { self: { [key]: value } })"), 'existing private API preserved');
assert(src.includes("status.textContent = 'Nicht gespeichert – erneut versuchen'"), 'unsaved drafts remain on error');
console.log('Bot sheet: Selbstbild separate, global switch preserved, private API unchanged');

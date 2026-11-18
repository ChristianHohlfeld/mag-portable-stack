#!/usr/bin/env node
'use strict';
// What the bots say quietly to each other stays readable in the chat but is
// not read aloud; a copied "[Sprecher …]" head is neither shown nor read.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const read = [];
const synth = { getVoices: () => [{ name: 'Deutsch', lang: 'de-DE', localService: true }], speak: (u) => read.push(u), cancel() {} };
function SpeechSynthesisUtterance(text) { this.text = text; }
const messages = [
  { id: '0001', role: 'bot', from: 'lena', text: 'Hallo Chris.\n*leise zu Noah:* Er merkt nichts.\nWeiter geht es.' },
  { id: '0002', role: 'bot', from: 'noah', text: '(flüstert) Psst, Blake.' }
];
const state = { messages: new Map(messages.map(m => [m.id, m])), bots: [] };
const listeners = {};
const window = {
  speechSynthesis: synth, SpeechSynthesisUtterance,
  MAG: { state, on(t, h) { (listeners[t] ||= []).push(h); }, icons: {}, tidy: (t) => t, toast() {} },
  addEventListener() {}
};
const document = { visibilityState: 'visible', documentElement: { dataset: {}, style: { setProperty() {} } }, querySelectorAll: () => [], addEventListener() {} };
const localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'speak.js'), 'utf8'),
  { window, document, localStorage, navigator: {}, SpeechSynthesisUtterance, performance: { now: () => 0 }, setTimeout, clearTimeout, console },
  { filename: 'speak.js' });
const { unwhispered } = window.MAG.speaking;
assert.equal(unwhispered('Ja. *leise zu Noah:* er merkt nichts. Gut.'), 'Ja.');
assert.equal(unwhispered('**Flüsternd:** wir warten.'), '');
assert.equal(unwhispered('Leise zu Noah: er schläft.\nGuten Morgen, Chris!'), '\nGuten Morgen, Chris!');
assert.equal(unwhispered('Klar. *flüstert: er merkt wirklich gar nichts davon* Also los.'), 'Klar.  Also los.');
assert.equal(unwhispered('Ich (ganz leise) hoffe es.'), 'Ich');
// Ordinary words stay: quiet music is no whisper, nor is an emphasis.
assert.equal(unwhispered('Ich höre (leise Musik) und *freue* mich.'), 'Ich höre (leise Musik) und *freue* mich.');
assert.equal(unwhispered('Sprich leise, bitte.'), 'Sprich leise, bitte.');
// Read aloud: the whispered line is skipped, a fully whispered message is not read at all.
window.MAG.speaking.startFrom('0001', 1);
assert.equal(read.map(u => u.text).join(' ').replace(/\s+/g, ' '), 'Hallo Chris. Weiter geht es.');
read[read.length - 1].onend?.();
assert.equal(read.length <= 2, true, 'the whispered message is not spoken');
// The copied speaker head of the prompt is gone from what the page shows.
const ui = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'messages.js'), 'utf8');
assert(ui.includes('\\[Sprecher '), 'tidy drops a copied [Sprecher …] head');
console.log('tts whisper ok');

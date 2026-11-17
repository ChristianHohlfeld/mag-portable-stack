#!/usr/bin/env node
'use strict';
// Test the real speak.js with a synchronous system-voice mock. A single user
// tap reads one finite snapshot in order, with per-session speed and STOP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'speak.js'), 'utf8');
const read = [];
let cancelled = 0;
const synth = {
  getVoices: () => [{ name: 'Deutsch', lang: 'de-DE', localService: true }],
  speak: (u) => { read.push(u); },
  cancel: () => { cancelled++; }
};
function SpeechSynthesisUtterance(text) { this.text = text; }
const fence = String.fromCharCode(96).repeat(3);
const messages = [
  { id: '0001', role: 'user', from: 'chris', text: 'Chris beginnt.' },
  { id: '0002', role: 'bot', from: 'lena', text: 'Lena antwortet.' },
  { id: '0003', role: 'bot', from: 'noah', text: 'Noah übernimmt.' },
  { id: '0004', role: 'bot', from: 'lena', text: fence + 'js\nconsole.log(1)\n' + fence },
  { id: '0005', role: 'service', from: 'idol', text: 'Ein Dienst.' },
  { id: '0006', role: 'user', from: 'chris', text: 'Chris beendet die Folge.' },
  { id: '0007', role: 'bot', from: 'noah', state: 'streaming', text: 'Unfertig.' }
];
const state = { messages: new Map(messages.slice().reverse().map(m => [m.id, m])), bots: [] };
const listeners = {};
const window = {
  speechSynthesis: synth,
  SpeechSynthesisUtterance,
  MAG: {
    state,
    on(type, handler) { (listeners[type] ||= []).push(handler); },
    icons: { stop: '■', speak: '▶' },
    tidy: (text) => text,
    toast: () => {}
  },
  addEventListener() {}
};
const document = {
  visibilityState: 'visible',
  documentElement: { dataset: {}, style: { setProperty() {} } },
  querySelectorAll: () => [],
  addEventListener() {}
};
const storage = new Map();
const localStorage = {
  getItem: key => storage.get(key) || null,
  setItem: (key, val) => storage.set(key, val),
  removeItem: key => storage.delete(key)
};
const ctx = {
  window, document, localStorage, navigator: {},
  SpeechSynthesisUtterance, performance: { now: () => 0 },
  setTimeout, clearTimeout, console
};
vm.runInNewContext(source, ctx, { filename: 'speak.js' });
const speak = window.MAG.speaking;
assert(speak.startFrom && speak.readingFrom, 'read-from-here controller exposed');
assert.equal(speak.startFrom('0002', 1.5), true);
assert.equal(speak.readingFrom(), true);
assert.equal(read.length, 1);
assert.equal(read[0].text, 'Lena antwortet.');
assert.equal(read[0].rate, 1.5);
read[0].onend();
assert.equal(read[1].text, 'Noah übernimmt.', 'correct order despite reversed Map insertion');
assert.equal(read[1].rate, 1.5);
read[1].onend();
assert.equal(read[2].text, 'Chris beendet die Folge.', 'code-only, service and streaming skipped');
assert.equal(read[2].rate, 1.5);
read[2].onend();
assert.equal(speak.readingFrom(), false, 'finite snapshot stops at the end');
assert.equal(speak.busy(), false);
assert.equal(read.length, 3, 'no extra silent utterance');

assert.equal(speak.startFrom('0003', 1.25), true);
assert.equal(read[3].text, 'Noah übernimmt.');
assert.equal(read[3].rate, 1.25);
read[3].onend();
assert.equal(read[4].rate, 1.25);
read[4].onend();

assert.equal(speak.startFrom('0002', 2), true);
assert.equal(read[5].rate, 2);
speak.stop();
assert.equal(speak.readingFrom(), false);
const lengthAfterStop = read.length;
read[5].onend();
assert.equal(read.length, lengthAfterStop, 'stale completion cannot restart stopped queue');
assert.equal(speak.busy(), false);

assert.equal(speak.startFrom('0002', 1.75), true);
assert.equal(read[6].rate, 1.75);
window.MAG.speak(messages[2]);
assert.equal(speak.readingFrom(), false, 'single-bubble play interrupts read-from-here');
assert.equal(read[7].text, 'Noah übernimmt.');
read[7].onend();
assert.equal(read.length, 8, 'single reading does not continue former queue');

assert.equal(speak.startFrom('0002', 1), true);
read[8].onerror();
assert.equal(read[9].text, 'Noah übernimmt.', 'voice error skips a message without stranding queue');
read[9].onend();
read[10].onend();
assert.equal(speak.readingFrom(), false);

assert.equal(speak.startFrom('0006', 2), true);
assert.equal(read[11].text, 'Chris beendet die Folge.');
read[11].onend();
assert.equal(speak.startFrom('0007', 1), false, 'incomplete message is not read as finished');
assert.equal(speak.readingFrom(), false);
assert(cancelled >= 4, 'starting or stopping a queue cancels prior speech');

const actionSource = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'messages.js'), 'utf8');
assert(actionSource.includes("label: 'Play from here'"), 'play-from-here appears in existing dots menu');
assert(actionSource.includes("label: 'Vorlesen stoppen'"), 'dots menu stops the sequence');
assert(actionSource.includes('[1.25, 1.5, 1.75, 2]'), 'all requested speeds share one implementation');
assert(actionSource.includes('MAG.speaking.startFrom(m.id, rate)'), 'speed applies from selected bubble');
assert(!actionSource.includes("button('play', 'Play from here'"), 'no extra play button in message bar');
console.log('Play from here: order, speakers, speeds, stop and interruption OK');

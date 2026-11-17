#!/usr/bin/env node
'use strict';
// Test the real speak.js with a synchronous system-voice mock. A single user
// tap reads from that message on, including a message that arrives later,
// and stays open until Stop.
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
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
(async () => {
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
assert.equal(read[2].text, 'Chris beendet die Folge.', 'code-only and service are skipped');
assert.equal(read[2].rate, 1.5);
read[2].onend();
assert.equal(read.length, 3, 'a streaming message with no finished sentence waits');
assert.equal(speak.readingFrom(), true, 'the chain stays open on a streaming message');
messages[6].state = 'done';
listeners.message.forEach((fn) => fn(messages[6]));
await flush();
assert.equal(read[3].text, 'Unfertig.', 'the finished stream is then spoken');
read[3].onend();
assert.equal(speak.readingFrom(), true, 'the chain waits for whatever arrives next');
assert.equal(speak.busy(), true);
const later = { id: '0008', role: 'bot', from: 'lena', text: 'Lena macht weiter.' };
state.messages.set(later.id, later);
listeners.message.forEach((fn) => fn(later));
assert.equal(read[4].text, 'Lena macht weiter.', 'a message that arrives later is spoken');
assert.equal(read[4].rate, 1.5);
speak.stop();
assert.equal(speak.readingFrom(), false);

assert.equal(speak.startFrom('0003', 1.25), true);
assert.equal(read[5].text, 'Noah übernimmt.');
assert.equal(read[5].rate, 1.25);
read[5].onend();
assert.equal(read[6].text, 'Chris beendet die Folge.');
assert.equal(read[6].rate, 1.25);
read[6].onend();
await flush();
assert.equal(read[7].text, 'Unfertig.');
speak.stop();

assert.equal(speak.startFrom('0002', 2), true);
assert.equal(read[8].rate, 2);
speak.stop();
assert.equal(speak.readingFrom(), false);
const lengthAfterStop = read.length;
read[8].onend();
assert.equal(read.length, lengthAfterStop, 'stale completion cannot restart stopped queue');
assert.equal(speak.busy(), false);

assert.equal(speak.startFrom('0002', 1.75), true);
assert.equal(read[9].rate, 1.75);
window.MAG.speak(messages[2]);
assert.equal(speak.readingFrom(), true, 'a normal Vorlesen is the same open chain');
assert.equal(read[10].text, 'Noah übernimmt.');
read[10].onend();
assert.equal(read[11].text, 'Chris beendet die Folge.', 'Vorlesen continues into the following messages');
speak.stop();

assert.equal(speak.startFrom('0002', 1), true);
read[12].onerror();
assert.equal(read[13].text, 'Noah übernimmt.', 'voice error skips a message without stranding queue');
read[13].onend();
assert.equal(read[14].text, 'Chris beendet die Folge.');
speak.stop();
assert.equal(speak.readingFrom(), false);

assert.equal(speak.startFrom('0006', 2), true);
assert.equal(read[15].text, 'Chris beendet die Folge.');
read[15].onend();
await flush();
speak.stop();
assert.equal(speak.startFrom('0007', 1), true, 'a streaming message is read, not treated as finished text');
assert.equal(speak.readingFrom(), true);
speak.stop();
assert.equal(speak.readingFrom(), false);
assert(cancelled >= 4, 'starting or stopping a queue cancels prior speech');

const actionSource = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'messages.js'), 'utf8');
assert(actionSource.includes("label: 'Play from here'"), 'play-from-here appears in existing dots menu');
assert(!actionSource.includes("owner' && !streaming) row.append(button('dots'"), 'dots stay while an answer is still being written');
assert(actionSource.includes("!streaming ? { icon: 'trash'"), 'a streaming answer is not deleted from the menu');
assert(actionSource.includes("label: 'Vorlesen stoppen'"), 'dots menu stops the sequence');
assert(actionSource.includes('[1.25, 1.5, 1.75, 2]'), 'all requested speeds share one implementation');
assert(actionSource.includes('MAG.speaking.startFrom(m.id, rate)'), 'speed applies from selected bubble');
assert(!actionSource.includes("button('play', 'Play from here'"), 'no extra play button in message bar');
console.log('Play from here: order, later messages, speeds, stop and interruption OK');
})().catch((e) => { console.error(e); process.exitCode = 1; });

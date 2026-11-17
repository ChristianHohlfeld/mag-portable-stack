#!/usr/bin/env node
'use strict';
// The ONNX worker accepts requests serially. Reproduce its slow/no-audio
// condition: prior code submitted up to 24 pieces and still said "Vorlesen".
// No browser GPU, test server, credentials or external downloads required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'speak.js'), 'utf8');
let now = 1, serial = 1;
const timers = new Map(), intervals = new Map(), calls = [], errors = [];
const later = (fn) => { const id = serial++; timers.set(id, fn); return id; };
const every = (fn) => { const id = serial++; intervals.set(id, fn); return id; };
class FakeWorker {
  constructor() { this.messages = []; }
  postMessage(msg) {
    this.messages.push(msg);
    calls.push(msg);
    if (msg.type === 'warm')
      Promise.resolve().then(() => this.onmessage({ data: {
        id: msg.id, type: 'ready', provider: 'wasm', threads: 1
      } }));
    // Deliberately NEVER return audio: a slow serial worker cannot keep up.
  }
  terminate() {}
}
class AudioContext {
  constructor() { this.state = 'running'; this.currentTime = 0; }
  resume() { this.state = 'running'; return Promise.resolve(); }
}
const button = {
  dataset: { speak: '0001' }, attrs: {},
  setAttribute(key, value) { this.attrs[key] = value; },
  getAttribute(key) { return this.attrs[key]; },
  set innerHTML(v) { this.html = v; }
};
const state = { messages: new Map([['0001', {
  id: '0001', role: 'bot', from: 'lena', state: 'done', text: 'Testnachricht. '.repeat(300)
}]]), bots: [] };
const window = {
  AudioContext, MAG: {
    state, icons: { stop: '■', speak: '▶' },
    on() {}, tidy: (s) => s, toast: (t) => errors.push(t)
  }, addEventListener() {}
};
const document = {
  visibilityState: 'visible', documentElement: {
    dataset: {}, style: { setProperty() {} }
  },
  querySelectorAll(selector) {
    return selector === '.act[data-speak]' ? [button] : [];
  },
  addEventListener() {}
};
const ctx = {
  window, document, Worker: FakeWorker, fetch() {}, navigator: {},
  localStorage: { getItem: () => null, setItem() {} },
  performance: { now: () => now },
  setTimeout: later, clearTimeout: (id) => timers.delete(id),
  setInterval: every, clearInterval: (id) => intervals.delete(id),
  console
};
(async () => {
  vm.runInNewContext(src, ctx, { filename: 'speak.js' });
  const speaking = window.MAG.speaking;
  assert(speaking.startFrom('0001', 2), 'a click starts reading');
  for (let i = 0; i < 40; i++) await Promise.resolve();
  const spoken = calls.filter(x => x.type === 'speak');
  assert(spoken.length > 0, 'a worker speech request was submitted');
  assert(spoken.length <= 3, 'serial worker never has >3 queued/in-flight fragments');
  assert.equal(intervals.size, 1, 'one audio monitor for a running reading');

  for (const tick of intervals.values()) tick();
  assert.equal(button.attrs['aria-pressed'], 'true');
  assert.equal(button.attrs['aria-busy'], 'true', 'no audio means waiting, not audible reading');
  assert.match(button.attrs['aria-label'], /Wartet auf Audiodaten/);
  assert.equal(speaking.readingFrom(), true, 'do not truncate on a short inference gap');

  // A real stalled ONNX job eventually exits the false-active state.
  now = 135002;
  for (const tick of [...intervals.values()]) tick();
  assert.equal(speaking.readingFrom(), false, 'stalled speech releases read-from-here');
  assert.equal(speaking.busy(), false);
  assert.equal(button.attrs['aria-pressed'], 'false');
  assert.equal(intervals.size, 0, 'stop must clear the audio monitor');
  assert(errors.some(s => /keine neuen Audiodaten/.test(s)), 'user sees an actionable failure');
  const before = calls.filter(x => x.type === 'speak').length;
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(calls.filter(x => x.type === 'speak').length, before,
    'stopped producer does not enqueue more audio work');

  // The normal one-message control must not inherit a stale queue/monitor.
  assert(speaking.startFrom('0001', 1.25));
  for (let i = 0; i < 30; i++) await Promise.resolve();
  assert.equal(intervals.size, 1);
  speaking.stop();
  assert.equal(intervals.size, 0);
  assert.equal(speaking.readingFrom(), false);
  console.log('TTS stall: serial lookahead bounded, waiting announced, watchdog releases false-active UI');
})().catch((e) => { console.error(e); process.exitCode = 1; });

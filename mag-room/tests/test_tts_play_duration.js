#!/usr/bin/env node
'use strict';
// A 1.5x ONNX waveform is shorter in SAMPLES, not faster via WebAudio.
// A computed AudioContext end time must never terminate the entire
// Play-from-here session before the actual source.onended fires.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'speak.js'), 'utf8');

const tasks = new Map(), intervals = new Map(), errors = [];
let serial = 0, running = null;
const later = (fn, ms) => { const id = ++serial; tasks.set(id, { fn, ms }); return id; };
const every = (fn) => { const id = ++serial; intervals.set(id, fn); return id; };
class FakeContext {
  constructor() { this.currentTime = 0; this.state = 'running'; this.sources = []; running = this; }
  resume() { this.state = 'running'; return Promise.resolve(); }
  createBuffer(channels, samples, rate) {
    assert.equal(channels, 1);
    return { duration: samples / rate, copyToChannel() {} };
  }
  createBufferSource() {
    const node = {
      buffer: null, onended: null,
      connect() {},
      start(at) { this.at = at; running.sources.push(this); },
      stop() { this.onended?.(); }
    };
    return node;
  }
}
class FakeWorker {
  postMessage(msg) {
    if (msg.type === 'warm') {
      Promise.resolve().then(() => this.onmessage({ data: {
        id: msg.id, type: 'ready', provider: 'wasm'
      } }));
    } else if (msg.type === 'speak') {
      // At 1.5x, 1 second of unaccelerated speech produces 2/3 s
      // of samples. Their actual sample rate stays 16 kHz.
      const audio = new Float32Array(Math.round(16000 / msg.rate)).fill(0.1);
      Promise.resolve().then(() => this.onmessage({ data: {
        id: msg.id, type: 'audio', audio, rate: 16000,
        provider: 'wasm', synthMs: 30, steps: 5
      } }));
    }
  }
  terminate() {}
}
const m = { id: '1', role: 'bot', from: 'lena', state: 'done', text: 'Guten Morgen.' };
const S = { messages: new Map([[m.id, m]]), bots: [] };
const window = { AudioContext: FakeContext, MAG: {
  state: S, on() {}, tidy: t => t,
  toast: m => errors.push(m), icons: { speak: '▶', stop: '■' }
}, addEventListener() {} };
const doc = {
  visibilityState: 'visible',
  documentElement: { dataset: {}, style: { setProperty() {} } },
  addEventListener() {}, querySelectorAll() { return []; }
};
const env = {
  window, document: doc, navigator: {},
  Worker: FakeWorker, fetch() {}, URLSearchParams,
  localStorage: { getItem() { return null; }, setItem() {} },
  performance: { now() { return 1000; } },
  setTimeout: later, clearTimeout: id => tasks.delete(id),
  setInterval: every, clearInterval: id => intervals.delete(id),
  console
};
vm.runInNewContext(source, env, { filename: 'speak.js' });
const tick = async () => {
  for (let i = 0; i < 30; i++) await Promise.resolve();
};
const advance = async (time) => {
  running.currentTime = time;
  for (let i = 0; i < 12; i++) {
    const due = [...tasks].filter(([, t]) => t.ms <= 250);
    for (const [id, t] of due) { tasks.delete(id); t.fn(); }
    await tick();
    if (!due.length) break;
  }
  for (const fn of intervals.values()) fn();
  await tick();
};
(async () => {
  const playing = window.MAG.speaking;
  assert(playing.startFrom('1', 1.5));
  await tick();
  assert(running, 'WebAudio created from click');
  assert.equal(running.sources.length, 1, 'one waveform scheduled');
  const first = running.sources[0];
  assert.equal(first.buffer.duration, Math.round(16000 / 1.5) / 16000,
    'real buffer duration is based only on samples / sample rate, no second /1.5');
  assert.equal(first.at, 0.03);
  await advance(0.2);
  assert(playing.busy(), 'the source is scheduled and reading has begun');
  assert(playing.readingFrom());

  // Simulate a browser whose AudioContext clock runs ahead of onended.
  // Previously reach(pb, pb.end) would finish before source playback ended.
  await advance(10);
  assert.equal(playing.busy(), true,
    'a computed duration/clock jump is NOT proof the sound completed');
  assert.equal(playing.readingFrom(), true);
  first.onended();
  await tick();
  assert.equal(playing.busy(), false, 'actual onended finishes the reading');
  assert.equal(playing.readingFrom(), false);
  assert.equal(intervals.size, 0, 'monitor stops after completion');
  assert.deepEqual(errors, []);

  // Stop while a source is outstanding: no leaked pending completion
  // promise may resume playback from a stale callback.
  assert(playing.startFrom('1', 2));
  await tick();
  assert.equal(running.sources.length, 1, 'a fresh context is created if not retained');
  await advance(0.2);
  playing.stop();
  await tick();
  assert.equal(playing.busy(), false);
  assert.equal(playing.readingFrom(), false);
  console.log('TTS duration: 1.5x sample timing, WebAudio actual onended and stop cleanup OK');
})().catch(e => { console.error(e); process.exitCode = 1; });

#!/usr/bin/env node
'use strict';
// The first sentence of a long reply is scheduled as soon as it exists.
// It must not wait out the preroll that covers the sentences still being made,
// and startup must not put every voice on the synthesis queue in front of it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'speak.js'), 'utf8');
assert(!source.includes('setTimeout(warmUp, 2500)'), 'the model is warmed when the page arrives');
assert(source.includes("type: 'prefetch'"), 'unused voices are fetched off the synthesis queue');

const calls = [];
class FakeContext {
  constructor() { this.currentTime = 0; this.state = 'running'; this.sources = []; }
  resume() { this.state = 'running'; return Promise.resolve(); }
  createBuffer(channels, samples, rate) {
    return { duration: samples / rate, copyToChannel() {} };
  }
  createBufferSource() {
    const node = {
      buffer: null, onended: null, connect() {},
      start(at) { this.at = at; this.context = running; running.sources.push(this); }
    };
    return node;
  }
}
let running = null;
const Real = FakeContext;
class AudioContext extends Real { constructor() { super(); running = this; } }
class FakeWorker {
  postMessage(msg) {
    calls.push(msg.type + ':' + (msg.model || ''));
    if (msg.type === 'warm') {
      Promise.resolve().then(() => this.onmessage({ data: { id: msg.id, type: 'ready', provider: 'wasm' } }));
    } else if (msg.type === 'speak') {
      const audio = new Float32Array(8000).fill(0.2);
      Promise.resolve().then(() => this.onmessage({ data: {
        id: msg.id, type: 'audio', audio, rate: 16000, provider: 'wasm', synthMs: 20, steps: 5
      } }));
    }
  }
  terminate() {}
}
const text = 'Der erste Satz ist fertig. Der zweite Satz ist noch nicht gemacht und darf den Anfang nicht festhalten.';
const m = { id: '1', role: 'bot', from: 'lena', state: 'done', text };
const button = { dataset: { speak: '1' }, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; }, set innerHTML(v) { this.html = v; } };
const window = { AudioContext, MAG: { state: { messages: new Map([[m.id, m]]), bots: [] }, on() {}, tidy: t => t, toast() {}, icons: { speak: '▶', stop: '■' } }, addEventListener() {} };
const document = {
  visibilityState: 'visible',
  documentElement: { dataset: {}, style: { setProperty() {} } },
  addEventListener() {},
  querySelectorAll(sel) { return sel === '.act[data-speak]' ? [button] : []; }
};
vm.runInNewContext(source, {
  window, document, navigator: {}, Worker: FakeWorker, fetch() {}, URLSearchParams,
  localStorage: { getItem() { return null; }, setItem() {} },
  performance: { now() { return 1000; } },
  setTimeout() { return 1; }, clearTimeout() {}, setInterval() { return 2; }, clearInterval() {},
  console
}, { filename: 'speak.js' });
(async () => {
  const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
  assert.equal(window.MAG.speaking.startFrom('1', 1), true);
  await tick();
  const speakAt = calls.findIndex((c) => c.startsWith('speak:'));
  assert(speakAt > 0, 'a sentence was sent to the model');
  const warmsBefore = calls.slice(0, speakAt).filter((c) => c.startsWith('warm:')).length;
  assert.equal(warmsBefore, 1, 'only the engine voice is on the queue before the first sentence');
  assert(running && running.sources.length >= 1, 'the first sentence is scheduled without waiting for the rest');
  assert.equal(button.attrs['aria-pressed'], 'true');
  console.log('TTS first chunk: one warm, first sentence scheduled at once');
})().catch((e) => { console.error(e); process.exitCode = 1; });

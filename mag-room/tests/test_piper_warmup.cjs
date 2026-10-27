// mag-test-timeout: 60
require('./mag_deadline.cjs').arm(60);
// Piper warm-up: the voice loads in the background after page load; a tap before
// it is ready shows a loading state (never the robotic system voice), then plays
// with Piper. Only a Piper failure falls back to a neural system voice.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'mag-room/static/message-audio.js'), 'utf8');

function setup(warmOk, opts = {}) {
  const store = opts.store || {}, rates = [];
  const spoken = [], played = [], posted = [], events = new Map(), buttons = [], timers = [];
  let workerRef = null;
  class FakeWorker {
    constructor() { workerRef = this; }
    postMessage(m) {
      posted.push(m);
      if (m.type === 'warm') this.pendingWarm = m;
      if (m.type === 'speak') Promise.resolve().then(() => this.onmessage({data: {type: 'audio', id: m.id, audio: new Float32Array(4), rate: 22050}}));
    }
    finishWarm() {
      const m = this.pendingWarm;
      this.onmessage({data: warmOk ? {type: 'ready', id: m.id} : {type: 'error', id: m.id, error: 'wasm'}});
    }
  }
  class FakeCtx {
    resume() {}
    createBuffer() { return {copyToChannel() {}}; }
    createBufferSource() { const src = {playbackRate: {value: 1}, connect() {}, start() { played.push(1); rates.push(this.playbackRate.value); Promise.resolve().then(() => this.onended()); }, stop() {}}; return src; }
  }
  const voices = [
    {lang: 'de-DE', localService: true, name: 'eSpeak German', voiceURI: 'espeak-ng de'},
    {lang: 'de-DE', localService: false, name: 'Microsoft Katja Online (Natural) - German'},
    {lang: 'en-US', localService: false, name: 'Microsoft Aria Online (Natural) - English'},
  ];
  const window = {
    speechSynthesis: {cancel() {}, speak(u) { spoken.push(u); }, getVoices() { return voices; }},
    SpeechSynthesisUtterance: class { constructor(t) { this.text = t; } },
    AudioContext: FakeCtx,
    addEventListener(n, cb) { events.set(n, cb); },
  };
  const document = {
    readyState: 'loading',
    querySelectorAll() { return buttons; },
    getElementById(id) { return id === 'language' ? {value: opts.language || 'de'} : null; },
    dispatchEvent() {},
  };
  const fetch = async () => ({ok: true, json: async () => ({dir: 'tts-x', voices: [{id: 'thorsten', label: 'Thorsten', model: 'de_DE-thorsten-high.onnx'}]})});
  vm.runInNewContext(source, {window, document, navigator: {language: opts.navLang || 'de-DE'}, Worker: FakeWorker, fetch,
    CustomEvent: class {}, localStorage: {getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }}, setTimeout: (f) => timers.push(f),
    SpeechSynthesisUtterance: window.SpeechSynthesisUtterance});
  function makeButton() {
    const attrs = {};
    const b = {dataset: {}, classList: {add() {}}, setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k]; }};
    buttons.push(b); return b;
  }
  return {window, store, rates, events, timers, spoken, played, posted, makeButton, worker: () => workerRef};
}
const tick = () => new Promise(r => setTimeout(r, 0));

(async () => {
  // 1) background warm-up after load, Thorsten by default
  let t = setup(true);
  assert.equal(t.posted.length, 0, 'nothing loads before the page is loaded');
  t.events.get('load')();
  assert.equal(t.timers.length, 1, 'warm-up deferred after load');
  t.timers.shift()();
  await tick(); await tick();
  assert.deepEqual(t.posted.map(m => [m.type, m.model]), [['warm', 'de_DE-thorsten-high.onnx']], 'Thorsten warmed in background');

  // 2) tap while loading -> busy button, no system voice; then Piper speaks
  const read = t.window.magMessageAudio;
  const b = t.makeButton();
  read.attach(b, 'Hallo Chris', 'voice-1');
  b.onclick();
  assert.equal(b.getAttribute('aria-busy'), 'true', 'loading state on the play button');
  assert.equal(b.getAttribute('aria-label'), 'Stimme wird geladen');
  assert.equal(t.spoken.length, 0, 'no robotic stand-in while Thorsten loads');
  t.worker().finishWarm();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(t.spoken.length, 0, 'Piper, not the system voice, speaks');
  assert(t.played.length >= 1, 'Piper audio played');
  assert(t.posted.some(m => m.type === 'speak' && m.model === 'de_DE-thorsten-high.onnx'));
  assert.equal(b.getAttribute('aria-busy'), 'false');

  // 3) Piper failure -> neural system voice, never espeak
  t = setup(false);
  const read2 = t.window.magMessageAudio;
  assert.equal(read2.bestVoice('de-DE').name, 'Microsoft Katja Online (Natural) - German', 'espeak voices excluded');
  const c = t.makeButton();
  read2.attach(c, 'Hallo', 'voice-2');
  c.onclick();
  await tick(); await tick();
  assert.equal(t.spoken.length, 0);
  t.worker().finishWarm();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(t.spoken.length, 1, 'system fallback only after Piper failed');
  assert(/Natural/.test(t.spoken[0].voice.name), 'neural system voice preferred');
  // 4) English device, auto setting: German text still uses Piper Thorsten
  t = setup(true, {language: 'auto', navLang: 'en-US'});
  t.events.get('load')(); t.timers.shift()();
  await tick(); await tick();
  assert.deepEqual(t.posted.map(m => m.model), ['de_DE-thorsten-high.onnx'], 'Thorsten warmed on an English device');
  const read3 = t.window.magMessageAudio;
  const d = t.makeButton();
  read3.attach(d, 'Ich habe die Seite geöffnet und das Ergebnis ist da', 'voice-3');
  d.onclick();
  t.worker().finishWarm();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(t.spoken.length, 0, 'no English system voice for German text');
  assert(t.posted.some(m => m.type === 'speak' && m.model === 'de_DE-thorsten-high.onnx'), 'Piper speaks German text');
  // German without umlauts, still German by default
  read3.stop?.();
  const e = t.makeButton();
  read3.attach(e, 'Hallo Chris', 'voice-4');
  e.onclick();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(t.spoken.length, 0, 'ambiguous text defaults to German/Piper');

  // 5) clearly English text in auto -> en-US system voice
  t = setup(true, {language: 'auto', navLang: 'de-DE'});
  const read4 = t.window.magMessageAudio;
  const f = t.makeButton();
  read4.attach(f, 'The page is open and you can see the result in the browser', 'voice-5');
  f.onclick();
  assert.equal(t.spoken.length, 1, 'English text read by the system voice');
  assert.equal(t.spoken[0].lang, 'en-US');
  assert(!t.posted.some(m => m.type === 'speak'), 'Piper not used for English text');

  // 6) read-aloud speed: default 1x, invalid -> 1x, persisted, applied to Piper and system voice
  t = setup(true, {store: {'mag-tts-rate': 'banana'}});
  const r6 = t.window.magMessageAudio;
  assert.deepEqual([...r6.rates], [1, 1.25, 1.5, 1.75, 2]);
  assert.equal(r6.ratePref(), 1, 'invalid stored rate -> 1x');
  assert.equal(setup(true, {store: {'mag-tts-rate': '3'}}).window.magMessageAudio.ratePref(), 1, 'out-of-range rate -> 1x');
  assert.equal(r6.setRate('1.75'), 1.75);
  assert.equal(t.store['mag-tts-rate'], '1.75', 'rate persisted');
  assert.equal(setup(true, {store: t.store}).window.magMessageAudio.ratePref(), 1.75, 'rate restored after reload');
  t.events.get('load')(); t.timers.shift()();
  await tick(); await tick();
  t.worker().finishWarm();
  const g = t.makeButton();
  r6.attach(g, 'Hallo Chris. Wie geht es dir heute so?', 'voice-6');
  g.onclick();
  for (let i = 0; i < 8; i++) await tick();
  assert(t.rates.length >= 1 && t.rates.every(v => v === 1.75), 'Piper playbackRate = chosen rate');
  t = setup(true, {language: 'en', store: {'mag-tts-rate': '1.5'}});
  const h = t.makeButton();
  t.window.magMessageAudio.attach(h, 'Hello there. '.repeat(30), 'voice-7');
  h.onclick();
  assert.equal(t.spoken[0].rate, 1.5, 'utterance.rate = chosen rate');
  t.window.magMessageAudio.setRate(2);
  t.spoken[0].onend();
  assert.equal(t.spoken[1].rate, 2, 'speed change applies to the next part');
  console.log('MAG Piper warm-up: background load, loading state, Piper playback, neural-only fallback PASS');
})().catch(e => { console.error(e); process.exit(1); });

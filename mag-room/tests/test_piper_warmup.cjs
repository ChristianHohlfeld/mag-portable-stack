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
  const sources = [];
  class FakeWorker {
    constructor() { workerRef = this; }
    postMessage(m) {
      posted.push(m);
      if (m.type === 'warm') this.pendingWarm = m;
      if (m.type === 'speak' && !opts.holdSpeak) Promise.resolve().then(() => this.onmessage({data: {type: 'audio', id: m.id, audio: new Float32Array(4), rate: 22050}}));
    }
    terminate() {}
    finishWarm() {
      const m = this.pendingWarm;
      this.onmessage({data: warmOk ? {type: 'ready', id: m.id} : {type: 'error', id: m.id, error: 'wasm'}});
    }
  }
  class FakeCtx {
    constructor() { this.state = 'running'; this.currentTime = 0; }
    resume() { return Promise.resolve(); }
    createBuffer() { return {copyToChannel() {}, duration: 1}; }
    createBufferSource() {
      const src = {playbackRate: {value: 1}, connect() {}, stopped: false,
        start() { played.push(1); rates.push(this.playbackRate.value); sources.push(this); if (!opts.holdPlay) Promise.resolve().then(() => this.onended()); },
        stop() { this.stopped = true; Promise.resolve().then(() => this.onended && this.onended()); }};
      return src;
    }
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
    querySelectorAll(q) { return q === '.message-speak' ? buttons : []; },
    createElement() { const n = {dataset: {}, style: {}, setAttribute() {}, append() {}, after() {}, querySelector() { return null; }}; return n; },
    head: {append() {}},
    getElementById(id) { return id === 'language' ? {value: opts.language || 'de'} : null; },
    dispatchEvent() {},
  };
  const fetch = async (url, init) => (init && init.method === 'PUT') ? {ok: true, status: 200, json: async () => ({})} : ({ok: true, json: async () => ({dir: 'tts-x', voices: [{id: 'thorsten', label: 'Thorsten', model: 'de_DE-thorsten-medium.onnx'}]})});
  vm.runInNewContext(source, {window, document, navigator: {language: opts.navLang || 'de-DE'}, Worker: FakeWorker, fetch,
    CustomEvent: class {}, localStorage: {getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }},
    // Deferred timers (warm-up, deadlines) run only when a test calls them.
    setTimeout: (f) => { timers.push(f); return timers.length; }, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, queueMicrotask,
    SpeechSynthesisUtterance: window.SpeechSynthesisUtterance});
  function makeButton() {
    const attrs = {};
    const b = {dataset: {}, classList: {add() {}}, setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k]; }};
    buttons.push(b); return b;
  }
  return {window, store, rates, events, timers, spoken, played, posted, sources, makeButton, worker: () => workerRef};
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
  assert.deepEqual(t.posted.map(m => [m.type, m.model]), [['warm', 'de_DE-thorsten-medium.onnx']], 'Thorsten warmed in background');

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
  assert(t.posted.some(m => m.type === 'speak' && m.model === 'de_DE-thorsten-medium.onnx'));
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
  assert.deepEqual(t.posted.map(m => m.model), ['de_DE-thorsten-medium.onnx'], 'Thorsten warmed on an English device');
  const read3 = t.window.magMessageAudio;
  const d = t.makeButton();
  read3.attach(d, 'Ich habe die Seite geöffnet und das Ergebnis ist da', 'voice-3');
  d.onclick();
  t.worker().finishWarm();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(t.spoken.length, 0, 'no English system voice for German text');
  assert(t.posted.some(m => m.type === 'speak' && m.model === 'de_DE-thorsten-medium.onnx'), 'Piper speaks German text');
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

  // 6) tempo per speaker: Piper length_scale (no playbackRate pitch shift),
  // stored per speaker, applied to Piper and system voice from the next part
  t = setup(true, {store: {'mag-tts-speakers': JSON.stringify({'bot:qwen-numa0': {rate: 3}})}});
  const r6 = t.window.magMessageAudio;
  assert.deepEqual([...r6.rates], [0.75, 0.85, 1, 1.15, 1.3, 1.5, 1.75, 2]);
  assert.equal(r6.rateFor('bot:qwen-numa0'), 1, 'out-of-range rate -> 1x');
  assert.equal(r6.setRate('bot:qwen-numa0', '1.75'), 1.75);
  assert.equal(JSON.parse(t.store['mag-tts-speakers'])['bot:qwen-numa0'].rate, 1.75, 'rate cached per speaker');
  assert.equal(r6.rateFor('user'), 1, 'other speakers unaffected');
  t.events.get('load')(); t.timers.shift()();
  await tick(); await tick();
  t.worker().finishWarm();
  const g = t.makeButton();
  r6.attach(g, 'Hallo Chris. Wie geht es dir heute so?', 'voice-6', {bot: 'qwen-numa0', name: 'Lena'});
  g.onclick();
  for (let i = 0; i < 12; i++) await tick();
  assert(t.rates.length >= 1 && t.rates.every(v => v === 1), 'no playbackRate shifting');
  const speaks = t.posted.filter(m => m.type === 'speak');
  assert.deepEqual(speaks.map(m => m.text), ['Hallo Chris.', 'Wie geht es dir heute so?'], 'first sentence starts playback; the next is separate');
  assert(speaks.every(m => m.rate === 1.75), 'tempo sent to Piper as length_scale factor');
  t = setup(true, {language: 'en', store: {'mag-tts-speakers': JSON.stringify({'user': {rate: 1.5}})}});
  const h = t.makeButton();
  t.window.magMessageAudio.attach(h, 'Hello there. '.repeat(30), 'voice-7', {user: true});
  h.onclick();
  assert.equal(t.spoken[0].rate, 1.5, 'utterance.rate = speaker tempo');
  t.window.magMessageAudio.setRate('user', 2);
  t.spoken[0].onend();
  assert.equal(t.spoken[1].rate, 2, 'speed change applies to the next part');
  // 7) a long sentence is capped so playback does not wait for the whole message
  t = setup(true);
  t.events.get('load')(); t.timers.shift()();
  await tick(); await tick();
  t.worker().finishWarm();
  await tick();
  const read7 = t.window.magMessageAudio;
  const j = t.makeButton();
  const long = 'Dies ist ein sehr langer Satz ohne Punkt ' + 'Wort '.repeat(40);
  read7.attach(j, long, 'voice-8');
  j.onclick();
  for (let i = 0; i < 4; i++) await tick();
  const first = t.posted.find(m => m.type === 'speak');
  assert(first && first.text.length <= 120, 'first Piper chunk stays within the start cap: ' + (first && first.text));
  assert(first.text.length < long.length, 'first chunk is not the whole message');
  const sw = fs.readFileSync(path.join(root, 'mag-room/web/sw.js'), 'utf8');
  assert(sw.includes('(?:speech|tts)'), 'service worker caches hashed TTS runtime');
  assert(sw.includes('tts-[a-f0-9]{12}\\/[^/]+\\.onnx'), 'ONNX weights are not copied into the service worker cache');
  // 8) streaming: a live message keeps growing while it is read; complete
  // sentences are appended to the running playback, nothing is read twice,
  // and the final text (new id, same button) ends it without a restart.
  t = setup(true, {holdPlay: true});
  t.events.get('load')(); t.timers.shift()(); await tick(); await tick();
  t.worker().finishWarm(); await tick();
  const r8 = t.window.magMessageAudio;
  const live = t.makeButton(), bot = {bot: 'qwen-numa1', name: 'Noah'};
  r8.attach(live, 'Erster Satz ist fertig. Zweiter Sa', 'live:qwen-numa1:1', {...bot, live: true});
  live.onclick();
  for (let i = 0; i < 8; i++) await tick();
  const texts = () => t.posted.filter(m => m.type === 'speak').map(m => m.text);
  assert.deepEqual(texts(), ['Erster Satz ist fertig.'], 'unfinished sentence waits');
  r8.attach(live, 'Erster Satz ist fertig. Zweiter Satz kommt dazu. Dritt', 'live:qwen-numa1:1', {...bot, live: true});
  for (let i = 0; i < 8; i++) await tick();
  assert.deepEqual(texts(), ['Erster Satz ist fertig.', 'Zweiter Satz kommt dazu.'], 'new sentence appended while playing');
  assert.equal(t.sources.length, 1, 'still the first buffer playing');
  r8.attach(live, 'Erster Satz ist fertig. Zweiter Satz kommt dazu. Dritter Satz ohne Punkt', 'final-9', bot);
  assert(r8.isPlaying('final-9'), 'playback follows the final message id');
  assert.equal(live.getAttribute('aria-pressed'), 'true', 'no restart, button still playing');
  for (let k = 0; k < 4; k++) { const src = t.sources.at(-1); src.onended && src.onended(); for (let i = 0; i < 8; i++) await tick(); }
  assert.deepEqual(texts(), ['Erster Satz ist fertig.', 'Zweiter Satz kommt dazu.', 'Dritter Satz ohne Punkt'], 'final tail read once');
  assert.equal(t.sources.length, 3, 'each part played exactly once');
  assert(!r8.isPlaying('final-9'), 'playback ends after the final text');

  // 9) Stop is immediate: the buffer stops, queued synthesis is cancelled,
  // and a quick second tap never produces two voices.
  t = setup(true, {holdPlay: true});
  t.events.get('load')(); t.timers.shift()(); await tick(); await tick();
  t.worker().finishWarm(); await tick();
  const r9 = t.window.magMessageAudio;
  const s9 = t.makeButton();
  r9.attach(s9, 'Eins ist hier. Zwei ist da. Drei ist dort.', 'voice-9');
  s9.onclick();
  for (let i = 0; i < 8; i++) await tick();
  assert.equal(t.sources.length, 1);
  s9.onclick();
  assert(t.sources[0].stopped, 'audio stops at once');
  assert.equal(s9.getAttribute('aria-pressed'), 'false');
  assert(t.posted.some(m => m.type === 'cancel'), 'queued synthesis cancelled');
  for (let i = 0; i < 8; i++) await tick();
  assert.equal(t.sources.length, 1, 'nothing plays after stop');
  s9.onclick(); s9.onclick(); s9.onclick();
  for (let i = 0; i < 8; i++) await tick();
  assert.equal(t.sources.filter(x => !x.stopped).length, 1, 'never two voices at once');
  console.log('MAG Piper warm-up: background load, sentence start, Piper playback, neural-only fallback PASS');
})().catch(e => { console.error(e); process.exit(1); });

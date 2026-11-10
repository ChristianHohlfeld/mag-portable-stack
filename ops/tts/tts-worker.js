// Piper read-aloud worker (classic Worker, copied into /static/tts-<hash>/
// by ops/vendor-tts-assets.sh). espeak-ng phonemization (piper-wasm) +
// Piper VITS voice in onnxruntime-web. The voice stays in Cache Storage, so
// a playback does not touch the server again. WASM threads when the room is
// cross-origin isolated; one thread otherwise. WebGPU is not used: Piper's
// VITS graph is unreliable on that provider, and a failed attempt would add
// the delay this worker exists to avoid.
/* global ort, createPiperPhonemize */
importScripts('ort.wasm.min.js', 'piper_phonemize.js');
const base = self.location.href.replace(/[^/]*$/, '');
ort.env.wasm.wasmPaths = base;
ort.env.wasm.proxy = false;
ort.env.wasm.numThreads = (function () {
  try {
    if (!self.crossOriginIsolated) return 1;
    const n = (self.navigator && self.navigator.hardwareConcurrency) || 1;
    return Math.max(1, Math.min(4, n));
  } catch (e) { return 1; }
})();
const CACHE = 'mag-tts-v1';
const voices = new Map();
let phonemizer = null;
const phonemeLines = [];

async function cachedFetch(url, progress) {
  let cache = null;
  try { cache = await caches.open(CACHE); } catch (e) { /* private mode / quota */ }
  if (cache) {
    const hit = await cache.match(url);
    if (hit) return hit;
  }
  const net = await fetch(url);
  if (!net.ok) throw new Error('HTTP ' + net.status + ' ' + url);
  const total = +net.headers.get('content-length') || 0;
  const store = async (response) => {
    if (!cache) return;
    try { await cache.put(url, response.clone()); } catch (e) { /* still play from memory */ }
  };
  if (!progress || !net.body || !total) { await store(net); return net; }
  const reader = net.body.getReader(), parts = [];
  let got = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    parts.push(value); got += value.length;
    progress(Math.min(99, Math.round(100 * got / total)));
  }
  const r = new Response(new Blob(parts), {headers: {'content-type': net.headers.get('content-type') || 'application/octet-stream'}});
  await store(r);
  return r;
}
function getPhonemizer() {
  if (!phonemizer) phonemizer = createPiperPhonemize({
    print: line => { try { const ids = JSON.parse(line).phoneme_ids; if (ids && ids.length) phonemeLines.push(ids); } catch (e) { /* not JSON */ } },
    printErr: () => {},
    locateFile: f => base + f,
  });
  return phonemizer;
}
async function createSession(buf) {
  try {
    return await ort.InferenceSession.create(buf, {executionProviders: ['wasm']});
  } catch (e) {
    if (ort.env.wasm.numThreads === 1) throw e;
    ort.env.wasm.numThreads = 1;
    return await ort.InferenceSession.create(buf, {executionProviders: ['wasm']});
  }
}
// Phones keep at most two voice sessions in the WASM heap (three ~63 MB
// models ran them out of memory); devices with more memory keep all three
// speakers' voices. The least recently used one is released; it reloads from
// Cache Storage when needed again.
const MAX_VOICES = (self.navigator && self.navigator.deviceMemory >= 8) ? 3 : 2;
async function loadVoice(model, id) {
  if (voices.has(model)) { const v = voices.get(model); voices.delete(model); voices.set(model, v); }
  while (!voices.has(model) && voices.size >= MAX_VOICES) {
    const [old, p] = voices.entries().next().value;
    voices.delete(old);
    try { (await p).session.release?.(); } catch (e) { /* already gone */ }
  }
  if (!voices.has(model)) voices.set(model, (async () => {
    const config = await (await cachedFetch(base + model + '.json')).json();
    const buf = await (await cachedFetch(base + model, p => postMessage({type: 'progress', id, progress: p}))).arrayBuffer();
    const session = await createSession(buf);
    return {config, session};
  })());
  try { return await voices.get(model); } catch (e) { voices.delete(model); throw e; }
}
// Tempo is Piper's own length_scale: faster speech without a pitch shift.
async function runIds(session, config, ids, speaker, rate) {
  const inf = config.inference || {};
  const r = rate >= 0.5 && rate <= 2.5 ? rate : 1;
  const feeds = {
    input: new ort.Tensor('int64', BigInt64Array.from(ids.map(BigInt)), [1, ids.length]),
    input_lengths: new ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]),
    scales: new ort.Tensor('float32', Float32Array.from([inf.noise_scale ?? 0.667, (inf.length_scale ?? 1) / r, inf.noise_w ?? 0.8]), [3]),
  };
  if ((config.num_speakers || 1) > 1) {
    const n = config.num_speakers, sid = Number.isInteger(speaker) && speaker >= 0 && speaker < n ? speaker : 0;
    feeds.sid = new ort.Tensor('int64', BigInt64Array.from([BigInt(sid)]), [1]);
  }
  const out = await session.run(feeds);
  const audio = out.output.data;
  return audio instanceof Float32Array ? audio : Float32Array.from(audio);
}
// The voices come from different recordings and differ in loudness. Every
// part is brought to the same speech level (RMS of the louder half of 20 ms
// frames, so pauses do not count) with a peak ceiling, and gets 8 ms fades so
// part boundaries do not click.
function level(audio, rate) {
  const n = Math.max(1, Math.round(rate * 0.02)), frames = [];
  for (let i = 0; i + n <= audio.length; i += n) {
    let e = 0;
    for (let j = i; j < i + n; j++) e += audio[j] * audio[j];
    frames.push(e / n);
  }
  if (!frames.length) return audio;
  frames.sort((a, b) => b - a);
  const loud = frames.slice(0, Math.max(1, frames.length >> 1));
  const rms = Math.sqrt(loud.reduce((a, b) => a + b, 0) / loud.length);
  let peak = 0;
  for (let i = 0; i < audio.length; i++) { const a = Math.abs(audio[i]); if (a > peak) peak = a; }
  if (rms < 1e-4 || peak < 1e-4) return audio;
  const gain = Math.min(0.12 / rms, 0.95 / peak, 6);
  const fade = Math.min(Math.round(rate * 0.008), audio.length >> 1);
  for (let i = 0; i < audio.length; i++) {
    let g = gain;
    if (i < fade) g *= i / fade; else if (i >= audio.length - fade) g *= (audio.length - 1 - i) / fade;
    audio[i] *= g;
  }
  return audio;
}
async function synth(text, model, id, speaker, rate) {
  const {config, session} = await loadVoice(model, id);
  const ph = await getPhonemizer();
  phonemeLines.length = 0;
  const t0 = Date.now();
  ph.callMain(['-l', (config.espeak && config.espeak.voice) || 'de', '--input', JSON.stringify([{text}]), '--espeak_data', '/espeak-ng-data']);
  const batch = phonemeLines.splice(0, phonemeLines.length);
  if (!batch.length) throw new Error('Phonemisierung fehlgeschlagen');
  const sampleRate = (config.audio && config.audio.sample_rate) || 22050;
  const gap = Math.round(sampleRate * 0.06);
  const parts = [];
  for (let i = 0; i < batch.length; i++) {
    parts.push(await runIds(session, config, batch[i], speaker, rate));
    if (i + 1 < batch.length) parts.push(new Float32Array(gap));
  }
  let n = 0;
  for (const p of parts) n += p.length;
  const audio = new Float32Array(n);
  let o = 0;
  for (const p of parts) { audio.set(p, o); o += p.length; }
  return {audio: level(audio, sampleRate), rate: sampleRate, synthMs: Date.now() - t0};
}
// One request at a time: a stopped playback may still have a part in flight,
// and two overlapping session.run calls in the shared WASM heap can fail or
// mix up the shared phoneme buffer. 'cancel' is handled at once: speak
// requests of a stopped playback that are still queued are answered as
// cancelled without synthesis.
let chain = Promise.resolve();
let cancelledBefore = 0;
self.onmessage = ev => {
  const data = ev.data || {};
  if (data.type === 'cancel') { cancelledBefore = Math.max(cancelledBefore, +data.before || 0); return; }
  // Download-only prefetch touches no WASM state and must not hold up speech.
  if (data.type === 'prefetch') {
    Promise.all([cachedFetch(base + data.model + '.json'), cachedFetch(base + data.model)])
      .then(() => postMessage({type: 'ready', id: data.id}), e => postMessage({type: 'error', id: data.id, error: String(e && e.message || e)}));
    return;
  }
  chain = chain.then(() => handle(data));
};
async function handle({type, id, text, model, speaker, rate}) {
  try {
    if (type === 'warm') { await loadVoice(model, id); postMessage({type: 'ready', id, threads: ort.env.wasm.numThreads}); return; }
    if (type === 'speak') {
      if (id <= cancelledBefore) { postMessage({type: 'error', id, error: 'cancelled'}); return; }
      const out = await synth(text, model, id, speaker, rate);
      postMessage({type: 'audio', id, audio: out.audio, rate: out.rate, synthMs: out.synthMs}, [out.audio.buffer]);
    }
  } catch (e) { postMessage({type: 'error', id, error: String(e && e.message || e)}); }
}

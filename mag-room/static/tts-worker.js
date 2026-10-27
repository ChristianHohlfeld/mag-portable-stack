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
async function loadVoice(model, id) {
  if (!voices.has(model)) voices.set(model, (async () => {
    const config = await (await cachedFetch(base + model + '.json')).json();
    const buf = await (await cachedFetch(base + model, p => postMessage({type: 'progress', id, progress: p}))).arrayBuffer();
    const session = await createSession(buf);
    return {config, session};
  })());
  try { return await voices.get(model); } catch (e) { voices.delete(model); throw e; }
}
async function runIds(session, config, ids) {
  const inf = config.inference || {};
  const feeds = {
    input: new ort.Tensor('int64', BigInt64Array.from(ids.map(BigInt)), [1, ids.length]),
    input_lengths: new ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]),
    scales: new ort.Tensor('float32', Float32Array.from([inf.noise_scale ?? 0.667, inf.length_scale ?? 1, inf.noise_w ?? 0.8]), [3]),
  };
  if ((config.num_speakers || 1) > 1) feeds.sid = new ort.Tensor('int64', BigInt64Array.from([0n]), [1]);
  const out = await session.run(feeds);
  const audio = out.output.data;
  return audio instanceof Float32Array ? audio : Float32Array.from(audio);
}
async function synth(text, model, id) {
  const {config, session} = await loadVoice(model, id);
  const ph = await getPhonemizer();
  phonemeLines.length = 0;
  const t0 = Date.now();
  ph.callMain(['-l', (config.espeak && config.espeak.voice) || 'de', '--input', JSON.stringify([{text}]), '--espeak_data', '/espeak-ng-data']);
  const batch = phonemeLines.splice(0, phonemeLines.length);
  if (!batch.length) throw new Error('Phonemisierung fehlgeschlagen');
  const rate = (config.audio && config.audio.sample_rate) || 22050;
  const gap = Math.round(rate * 0.06);
  const parts = [];
  for (let i = 0; i < batch.length; i++) {
    parts.push(await runIds(session, config, batch[i]));
    if (i + 1 < batch.length) parts.push(new Float32Array(gap));
  }
  let n = 0;
  for (const p of parts) n += p.length;
  const audio = new Float32Array(n);
  let o = 0;
  for (const p of parts) { audio.set(p, o); o += p.length; }
  return {audio, rate, synthMs: Date.now() - t0};
}
self.onmessage = async ev => {
  const {type, id, text, model} = ev.data || {};
  try {
    if (type === 'warm') { await loadVoice(model, id); postMessage({type: 'ready', id, threads: ort.env.wasm.numThreads}); return; }
    if (type === 'speak') {
      const {audio, rate, synthMs} = await synth(text, model, id);
      postMessage({type: 'audio', id, audio, rate, synthMs}, [audio.buffer]);
    }
  } catch (e) { postMessage({type: 'error', id, error: String(e && e.message || e)}); }
};

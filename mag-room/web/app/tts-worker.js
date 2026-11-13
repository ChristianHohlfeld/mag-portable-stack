// Read-aloud worker: Piper voices (VITS) in onnxruntime-web, their phonemes
// from espeak-ng (piper-wasm). Nothing of it comes from the room: the runtime
// is fetched from jsDelivr and the voices from Hugging Face, each pinned by
// version and SHA256, and everything stays in this browser's Cache Storage,
// so after the first time a reading touches no network at all. Every voice
// is one language (German or English); the page picks the one for the text.
'use strict';
/* global ort, createPiperPhonemize */
const ORT = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.30.0/dist/';
const PIPER = 'https://cdn.jsdelivr.net/npm/@diffusionstudio/piper-wasm@1.0.0/build/';
const HF = 'https://huggingface.co/rhasspy/piper-voices/resolve/6b9cfaacb4fb2e02a9621227e3c1a48bec9710c3/';
// url, SHA256, bytes
const RUNTIME = {
  ort: [ORT + 'ort.wasm.min.js', 'faece07e84faa9001f5104bbe4e5c70861d06cb2376a0dea1fa0ad33a67adc81', 50196],
  mjs: [ORT + 'ort-wasm-simd-threaded.mjs', 'e13f7f94fc51b4ca72b12faeb1ee95f4ace6dfbc8939bc718aabdc0a27c4299b', 24381],
  wasm: [ORT + 'ort-wasm-simd-threaded.wasm', '3398c10d07d229bd91b364548e130e0e51a8e5704b88c7c083ebbeb78842dee2', 14239897],
  piper: [PIPER + 'piper_phonemize.js', 'fef0c2fc442d24fdef5c7c7cc37d5da2314407640fe11ab1bfe347c723dff19b', 120714],
  piperWasm: [PIPER + 'piper_phonemize.wasm', 'b777cd107a91d2bcc6a1ea46f2c26a662a7407394fe84589198aeaa83dd7a9d6', 635212],
  piperData: [PIPER + 'piper_phonemize.data', '29f1025eb23a5b5c192cd14a6efbce4509402ff265405072ee6f7d1a09b78f8c', 18077249],
};
// name: folder, SHA256 of the model, of its config, bytes of the model
const VOICES = {
  'de_DE-thorsten-medium': ['de/de_DE/thorsten/medium/', 'fd270eb3d1ebf91d763f8a0f949b1b1bebdfefaea18c3aaf8577dd34536e064f', 'cf245c91668e74cb6716f38647ae3d6d9bac42965872596f65f9ab2b7d7221ed', 63221984],
  'de_DE-kerstin-low': ['de/de_DE/kerstin/low/', 'd352a7641892cebf2903859af94e9ba81a141110215fe3943bcda7f7da401b7a', '56e708556b7b9b7a53c4f8957e021421e69f11a600962bba554cffbe72cf2d47', 63104526],
  'de_DE-karlsson-low': ['de/de_DE/karlsson/low/', 'f9793bf7e461a472b3916cf260774bfc9812e9eb72601b147ed649d01064e8fe', '3b2095adbfeede316f22007a0a09d4bfc25514b7189850740b06800a6cfb9838', 63104526],
  'de_DE-ramona-low': ['de/de_DE/ramona/low/', 'a1068592532aad8c49bf3d1a01b8749c11d7dba726689ee28ad64e7b627ae4c5', '64906ab7a050ac685fa3dcea044baa798f67ed77be164f9c668a69298c6487d2', 63104526],
  'en_GB-alba-medium': ['en/en_GB/alba/medium/', '1f9da809b6da1ef4190bb5dd6cce4f4af803274539e9cf9e0693085150bac3d7', '6214603a0dd60e9dcd05502e23ef07fbe3d9af36d3189c8cc3eb129e1a099d6f', 63221984],
  'en_US-joe-medium': ['en/en_US/joe/medium/', 'abf4b998e34045cbd00eaf050de2de620f81ded6eb8b85a24b022a1c2b3f08d2', '7b5f0293c0bf5bda8e377be1696da84af798befeef31038e43beefc01fe858da', 63221984],
};
const CACHE = 'mag-tts-piper';
// The voices before (Piper from the room, then Supertonic 3, about 200 MB) leave the device.
for (const old of ['mag-tts-v1', 'mag-tts-s3', 'mag-tts-s3h']) { try { caches.delete(old); } catch (e) { /* no Cache Storage */ } }

let store = null;
async function cache() {
  if (store === null) { try { store = await caches.open(CACHE); } catch (e) { store = false; } }
  return store || null;
}
async function sha256(buf) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}
// A file as an ArrayBuffer: from Cache Storage, else fetched, checked against
// its SHA256 and kept. A download that breaks is asked for again, by itself.
const got = new Map();                  // url -> bytes downloaded so far (for the progress)
async function file(url, sha) {
  const c = await cache();
  const hit = c && await c.match(url);
  if (hit) return hit.arrayBuffer();
  for (let attempt = 0; ; attempt++) {
    try {
      const net = await fetch(url, { credentials: 'omit' });
      if (!net.ok) throw new Error('HTTP ' + net.status);
      const parts = [];
      let n = 0;
      if (net.body) {
        const reader = net.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parts.push(value); n += value.length;
          got.set(url, n); progress();
        }
      } else parts.push(new Uint8Array(await net.arrayBuffer()));
      const buf = await new Blob(parts).arrayBuffer();
      if (await sha256(buf) !== sha) throw new Error('Prüfsumme stimmt nicht: ' + url.split('/').pop());
      if (c) { try { await c.put(url, new Response(buf.slice(0), { headers: { 'content-type': 'application/octet-stream' } })); } catch (e) { /* a full device: played from memory */ } }
      return buf;
    } catch (e) {
      got.delete(url); progress();
      if (attempt >= 30) throw e;
      await new Promise((go) => setTimeout(go, Math.min(30000, 1000 * 2 ** Math.min(attempt, 5))));
    }
  }
}
// How far what is being loaded has come, over all files a load needs.
let wanted = new Map();                 // url -> bytes, for the files the current loads still fetch
let lastShown = -1;
function progress() {
  let total = 0, have = 0;
  for (const [url, size] of wanted) { total += size; have += Math.min(size, got.get(url) || 0); }
  if (!total) return;
  const p = Math.min(99, Math.floor(100 * have / total));
  if (p !== lastShown) { lastShown = p; postMessage({ type: 'progress', progress: p }); }
}
async function need(list) {
  const c = await cache();
  for (const [url, , size] of list) if (!(c && await c.match(url))) wanted.set(url, size);
}
const voiceFiles = (name) => {
  const v = VOICES[name];
  if (!v) throw new Error('Unbekannte Stimme ' + name);
  return [[HF + v[0] + name + '.onnx.json', v[2], 6000], [HF + v[0] + name + '.onnx', v[1], v[3]]];
};
const blobUrl = (buf) => URL.createObjectURL(new Blob([buf], { type: 'text/javascript' }));

// The runtime: onnxruntime-web and the phonemizer, loaded once per worker.
let runtime = null, phonemizer = null;
const phonemeLines = [];
function boot() {
  if (!runtime) runtime = (async () => {
    const r = RUNTIME;
    await need(Object.values(r));
    const [ortJs, mjs, wasm, piperJs, piperWasm, piperData] = await Promise.all(
      [r.ort, r.mjs, r.wasm, r.piper, r.piperWasm, r.piperData].map(([url, sha]) => file(url, sha)));
    importScripts(blobUrl(ortJs), blobUrl(piperJs));
    ort.env.wasm.wasmPaths = { mjs: blobUrl(mjs) };
    ort.env.wasm.wasmBinary = wasm;
    ort.env.wasm.proxy = false;
    ort.env.wasm.numThreads = (() => {
      try { return self.crossOriginIsolated ? Math.max(1, Math.min(4, navigator.hardwareConcurrency || 1)) : 1; } catch (e) { return 1; }
    })();
    phonemizer = await createPiperPhonemize({
      print: (line) => { try { const ids = JSON.parse(line).phoneme_ids; if (ids && ids.length) phonemeLines.push(ids); } catch (e) { /* not JSON */ } },
      printErr: () => {},
      wasmBinary: piperWasm,
      getPreloadedPackage: () => piperData,
      locateFile: (f) => PIPER + f,
    });
  })().catch((e) => { runtime = null; throw e; });
  return runtime;
}
async function createSession(buf) {
  try {
    return await ort.InferenceSession.create(buf, { executionProviders: ['wasm'] });
  } catch (e) {
    if (ort.env.wasm.numThreads === 1) throw e;
    ort.env.wasm.numThreads = 1;
    return await ort.InferenceSession.create(buf, { executionProviders: ['wasm'] });
  }
}
// Phones keep at most two voices in the WASM heap (three ~63 MB models ran
// them out of memory); a device with more memory keeps three. The least
// recently used one is let go; it comes back from Cache Storage.
const MAX_VOICES = (self.navigator && self.navigator.deviceMemory >= 8) ? 3 : 2;
const voices = new Map();
async function loadVoice(name) {
  if (voices.has(name)) { const v = voices.get(name); voices.delete(name); voices.set(name, v); }
  while (!voices.has(name) && voices.size >= MAX_VOICES) {
    const [old, p] = voices.entries().next().value;
    voices.delete(old);
    try { (await p).session.release?.(); } catch (e) { /* already gone */ }
  }
  if (!voices.has(name)) voices.set(name, (async () => {
    const files = voiceFiles(name);
    await need(files);
    await boot();
    const [json, model] = await Promise.all(files.map(([url, sha]) => file(url, sha)));
    const config = JSON.parse(new TextDecoder().decode(json));
    const session = await createSession(model);
    return { config, session };
  })());
  try { return await voices.get(name); } catch (e) { voices.delete(name); throw e; }
}
// Tempo is Piper's own length_scale: faster speech without a change of pitch.
async function runIds(session, config, ids, rate) {
  const inf = config.inference || {};
  const r = rate >= 0.5 && rate <= 2.5 ? rate : 1;
  const feeds = {
    input: new ort.Tensor('int64', BigInt64Array.from(ids.map(BigInt)), [1, ids.length]),
    input_lengths: new ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]),
    scales: new ort.Tensor('float32', Float32Array.from([inf.noise_scale ?? 0.667, (inf.length_scale ?? 1) / r, inf.noise_w ?? 0.8]), [3]),
  };
  if ((config.num_speakers || 1) > 1) feeds.sid = new ort.Tensor('int64', BigInt64Array.from([0n]), [1]);
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
// Pictures and symbols have no sound; espeak-ng reads numbers, dates and units itself.
const speakable = (text) => String(text || '').replace(/[\p{Extended_Pictographic}‍️]/gu, ' ').replace(/\s+/g, ' ').trim();
async function synth(text, name, rate) {
  const { config, session } = await loadVoice(name);
  const clean = speakable(text);
  const sampleRate = (config.audio && config.audio.sample_rate) || 22050;
  if (!clean) return { audio: new Float32Array(0), rate: sampleRate, synthMs: 0 };
  const t0 = Date.now();
  phonemeLines.length = 0;
  phonemizer.callMain(['-l', (config.espeak && config.espeak.voice) || 'de', '--input', JSON.stringify([{ text: clean }]), '--espeak_data', '/espeak-ng-data']);
  const batch = phonemeLines.splice(0, phonemeLines.length);
  if (!batch.length) throw new Error('Phonemisierung fehlgeschlagen');
  const gap = Math.round(sampleRate * 0.06), parts = [];
  for (let i = 0; i < batch.length; i++) {
    parts.push(await runIds(session, config, batch[i], rate));
    if (i + 1 < batch.length) parts.push(new Float32Array(gap));
  }
  const audio = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { audio.set(p, o); o += p.length; }
  return { audio: level(audio, sampleRate), rate: sampleRate, synthMs: Date.now() - t0 };
}
// One request at a time: two session.run calls in the shared WASM heap can
// fail or mix up the phoneme lines. 'cancel' is handled at once: what a
// stopped reading still has waiting is answered as cancelled, unmade.
let chain = Promise.resolve();
let cancelledBefore = 0;
self.onmessage = (ev) => {
  const data = ev.data || {};
  if (data.type === 'cancel') { cancelledBefore = Math.max(cancelledBefore, +data.before || 0); return; }
  // A download only: it touches no WASM state and does not hold speech up.
  if (data.type === 'prefetch') {
    (async () => {
      const files = [...Object.values(RUNTIME), ...voiceFiles(data.model)];
      for (const [url, sha] of files) await file(url, sha);
    })().then(() => postMessage({ type: 'ready', id: data.id }), (e) => postMessage({ type: 'error', id: data.id, error: String((e && e.message) || e) }));
    return;
  }
  chain = chain.then(() => handle(data));
};
async function handle({ type, id, text, model, rate }) {
  try {
    if (type === 'warm') {
      await loadVoice(model);
      wanted = new Map(); lastShown = -1;
      postMessage({ type: 'ready', id, threads: ort.env.wasm.numThreads });
      return;
    }
    if (type === 'speak') {
      if (id <= cancelledBefore) { postMessage({ type: 'error', id, error: 'cancelled' }); return; }
      const out = await synth(text, model, rate);
      postMessage({ type: 'audio', id, audio: out.audio, rate: out.rate, synthMs: out.synthMs }, [out.audio.buffer]);
    }
  } catch (e) { postMessage({ type: 'error', id, error: String((e && e.message) || e) }); }
}

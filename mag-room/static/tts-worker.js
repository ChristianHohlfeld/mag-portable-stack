// Piper read-aloud worker (classic Worker, copied into /static/tts-<hash>/
// by ops/vendor-tts-assets.sh). espeak-ng phonemization (piper-wasm) +
// Piper VITS voice in onnxruntime-web (wasm, one thread). Voices are fetched
// once and kept in Cache Storage, so later visits work offline.
/* global ort, createPiperPhonemize */
importScripts('ort.wasm.min.js', 'piper_phonemize.js');
const base = self.location.href.replace(/[^/]*$/, '');
ort.env.wasm.wasmPaths = base;
ort.env.wasm.numThreads = 1;
const CACHE = 'mag-tts-v1';
const voices = new Map();
let phonemizer = null, lastIds = null;

async function cachedFetch(url, progress) {
  const cache = await caches.open(CACHE);
  let r = await cache.match(url);
  if (!r) {
    const net = await fetch(url);
    if (!net.ok) throw new Error('HTTP ' + net.status + ' ' + url);
    const total = +net.headers.get('content-length') || 0;
    if (!progress || !net.body || !total) { await cache.put(url, net.clone()); return net; }
    const reader = net.body.getReader(), parts = [];
    let got = 0;
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      parts.push(value); got += value.length;
      progress(Math.min(99, Math.round(100 * got / total)));
    }
    r = new Response(new Blob(parts), {headers: {'content-type': net.headers.get('content-type') || 'application/octet-stream'}});
    await cache.put(url, r.clone());
  }
  return r;
}
function getPhonemizer() {
  if (!phonemizer) phonemizer = createPiperPhonemize({
    print: line => { try { lastIds = JSON.parse(line).phoneme_ids; } catch (e) { /* not JSON */ } },
    printErr: () => {},
    locateFile: f => base + f,
  });
  return phonemizer;
}
async function loadVoice(model, id) {
  if (!voices.has(model)) voices.set(model, (async () => {
    const config = await (await cachedFetch(base + model + '.json')).json();
    const buf = await (await cachedFetch(base + model, p => postMessage({type: 'progress', id, progress: p}))).arrayBuffer();
    const session = await ort.InferenceSession.create(buf, {executionProviders: ['wasm']});
    return {config, session};
  })());
  try { return await voices.get(model); } catch (e) { voices.delete(model); throw e; }
}
async function synth(text, model, id) {
  const {config, session} = await loadVoice(model, id);
  const ph = await getPhonemizer();
  lastIds = null;
  ph.callMain(['-l', (config.espeak && config.espeak.voice) || 'de', '--input', JSON.stringify([{text}]), '--espeak_data', '/espeak-ng-data']);
  if (!lastIds || !lastIds.length) throw new Error('Phonemisierung fehlgeschlagen');
  const ids = lastIds, inf = config.inference || {};
  const feeds = {
    input: new ort.Tensor('int64', BigInt64Array.from(ids.map(BigInt)), [1, ids.length]),
    input_lengths: new ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]),
    scales: new ort.Tensor('float32', Float32Array.from([inf.noise_scale ?? 0.667, inf.length_scale ?? 1, inf.noise_w ?? 0.8]), [3]),
  };
  if ((config.num_speakers || 1) > 1) feeds.sid = new ort.Tensor('int64', BigInt64Array.from([0n]), [1]);
  const out = await session.run(feeds);
  const audio = out.output.data;
  return {audio: audio instanceof Float32Array ? audio : Float32Array.from(audio), rate: (config.audio && config.audio.sample_rate) || 22050};
}
self.onmessage = async ev => {
  const {type, id, text, model} = ev.data || {};
  try {
    if (type === 'warm') { await loadVoice(model, id); postMessage({type: 'ready', id}); return; }
    if (type === 'speak') {
      const {audio, rate} = await synth(text, model, id);
      postMessage({type: 'audio', id, audio, rate}, [audio.buffer]);
    }
  } catch (e) { postMessage({type: 'error', id, error: String(e && e.message || e)}); }
};

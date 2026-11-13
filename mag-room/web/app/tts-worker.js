// Read-aloud worker: Supertonic 3 in onnxruntime-web, on the device alone.
// Nothing of it comes from the room: the browser fetches the model from
// Hugging Face (Supertone/supertonic-3, one pinned commit, four networks and
// ten voice styles, about 400 MB) and the runtime from jsDelivr
// (onnxruntime-web 1.30.0), checks every file against its SHA256 once and
// keeps it in Cache Storage, so it is downloaded once and a reading touches
// no network after that. The page asks for the model in the background right
// after it has loaded, so the first "Vorlesen" does not wait for a download.
// Where the page allows it (?gpu=1) the networks run on the device's GPU
// (WebGPU), many times faster; where that fails, is silent or takes too long
// (the page then ends this worker), they run in WASM.
/* global ort */
const inWorker = typeof importScripts === 'function';   // not so in the test of the text rules
const RUNTIME = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.30.0/dist/';
const MODEL = 'https://huggingface.co/Supertone/supertonic-3/resolve/3cadd1ee6394adea1bd021217a0e650ede09a323/';
// Every file by its SHA256 (the published model as it is, and the runtime).
const SHA = {
  'ort.webgpu.min.js': '7d65dad7eb4564ad98db9924a259af2e2c68ebfbdbf254fa5d728f1c5736e07a',
  'ort-wasm-simd-threaded.asyncify.mjs': '3d1c85995364bb643302fc6fd877a0c3ba5ae72401815e0f24828a53d9191e28',
  'ort-wasm-simd-threaded.asyncify.wasm': '39f9f0894d478800487ed9f7dbe92618498db320cf55c8e3d89adff8dce658da',
  'duration_predictor.onnx': 'c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db',
  'text_encoder.onnx': 'c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff',
  'vector_estimator.onnx': '883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c',
  'vocoder.onnx': '085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba',
  'tts.json': '42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09',
  'unicode_indexer.json': '9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f',
  'F1.json': 'bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2',
  'F2.json': '7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6',
  'F3.json': '12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab',
  'F4.json': 'c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b',
  'F5.json': '45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d',
  'M1.json': 'e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b',
  'M2.json': 'b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50',
  'M3.json': 'ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b',
  'M4.json': 'ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303',
  'M5.json': 'dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2',
};
// Where a file of the model lies in its repository.
const at = (file) => MODEL + (/^[FM][1-5]\.json$/.test(file) ? 'voice_styles/' : 'onnx/') + file;
async function sha256(buf) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}
let gpuWanted = false;
let booted = null;            // the runtime, loaded once
function boot() {
  if (!booted) booted = (async () => {
    const [js, mjs, wasm] = await Promise.all(['ort.webgpu.min.js', 'ort-wasm-simd-threaded.asyncify.mjs', 'ort-wasm-simd-threaded.asyncify.wasm']
      .map(async (f) => { const buf = await (await cachedFetch(RUNTIME + f, f)).arrayBuffer(); return buf; }));
    const blob = (buf) => URL.createObjectURL(new Blob([buf], {type: 'text/javascript'}));
    importScripts(blob(js));
    ort.env.wasm.wasmPaths = {mjs: blob(mjs)};
    ort.env.wasm.wasmBinary = wasm;
    ort.env.wasm.proxy = false;
    ort.env.wasm.numThreads = (function () {
      try {
        if (!self.crossOriginIsolated) return 1;
        const n = (self.navigator && self.navigator.hardwareConcurrency) || 1;
        return Math.max(1, Math.min(4, n - 2));   // the big cores, and two left for the page
      } catch (e) { return 1; }
    })();
  })().catch((e) => { booted = null; throw e; });
  return booted;
}
if (inWorker) gpuWanted = new URL(self.location.href).searchParams.get('gpu') === '1' && !!(self.navigator && self.navigator.gpu);
const CACHE = 'mag-tts-s3o';
// The four networks, each one file with its weights (float32, as published).
const NETWORKS = ['duration_predictor', 'text_encoder', 'vector_estimator', 'vocoder'];
const LANGS = ['en', 'ko', 'ja', 'ar', 'bg', 'cs', 'da', 'de', 'el', 'es', 'et', 'fi', 'fr', 'hi', 'hr', 'hu', 'id', 'it', 'lt', 'lv', 'nl', 'pl', 'pt', 'ro', 'ru', 'sk', 'sl', 'sv', 'tr', 'uk', 'vi'];
// How many steps the sound is refined in: more is cleaner and takes longer.
const STEPS = {webgpu: 5, wasm: 5};
let engine = null;            // promise of {cfg, indexer, dp, enc, est, voc, provider}
const styles = new Map();     // voice file -> promise of {ttl, dp}

// The readers before this one (Piper, Supertonic from the room) are not needed any more: their files leave the device.
if (inWorker) { for (const old of ['mag-tts-v1', 'mag-tts-s3', 'mag-tts-s3h', 'mag-tts-piper']) { try { caches.delete(old); } catch (e) { /* no Cache Storage */ } } }

let store = null;             // Cache Storage, or false where there is none (then the pieces stay in memory)
const memory = new Map();
async function opened() {
  if (store === null) { try { store = await caches.open(CACHE); } catch (e) { store = false; } }
  return store;
}
// A small file from Cache Storage, or fetched, checked against its SHA256 and kept there.
async function cachedFetch(url, name) {
  const cache = await opened();
  if (cache) {
    const hit = await cache.match(url);
    if (hit) return hit;
  }
  const net = await fetch(url);
  if (!net.ok) throw new Error('HTTP ' + net.status + ' ' + url);
  const buf = await net.arrayBuffer();
  if (name && SHA[name] && await sha256(buf) !== SHA[name]) throw new Error('Prüfsumme stimmt nicht: ' + name);
  const kept = new Response(buf, {headers: {'content-type': net.headers.get('content-type') || 'application/octet-stream'}});
  if (cache) { try { await cache.put(url, kept.clone()); } catch (e) { /* still use it from memory */ } }
  return kept;
}

// The weights are fetched in pieces of 8 MB, and every piece is kept as soon
// as it is there: a download that breaks off (a phone that sleeps, a weak
// connection, the page reloaded by a deploy) goes on where it stopped, the
// next time, instead of starting from nothing. A piece that fails is asked
// for again after a pause, for as long as the page is open. The pieces are
// what is kept: they are never put together into one large file, which a
// phone has to hold in memory and write a second time.
const PIECE = 8 << 20;
// (A piece is kept under an address of its own: Cache Storage does not tell addresses apart by their #part.)
const pieceKey = (url, n) => url + (url.includes('?') ? '&' : '?') + 'piece=' + n;
const pause = (ms) => new Promise((go) => setTimeout(go, ms));
const pieceSize = (total, n) => Math.max(0, Math.min(PIECE, total - n * PIECE));
async function keepPiece(url, n, bytes, total) {
  const cache = await opened();
  if (!cache) { memory.set(pieceKey(url, n), {bytes, total}); return; }
  try { await cache.put(pieceKey(url, n), new Response(bytes, {headers: {'x-total': String(total)}})); }
  catch (e) { memory.set(pieceKey(url, n), {bytes, total}); }   // a full device: kept for as long as the page is open
}
// The size of the whole file if piece n is kept, else 0 (the piece is not read).
async function kept(url, n) {
  const key = pieceKey(url, n);
  if (memory.has(key)) return memory.get(key).total;
  const cache = await opened();
  const hit = cache ? await cache.match(key) : null;
  return hit ? +hit.headers.get('x-total') || 0 : 0;
}
async function readPiece(url, n) {
  const key = pieceKey(url, n);
  if (memory.has(key)) return memory.get(key).bytes;
  const cache = await opened();
  const hit = cache ? await cache.match(key) : null;
  return hit ? hit.arrayBuffer() : null;
}
async function dropPiece(url, n) {
  memory.delete(pieceKey(url, n));
  const cache = await opened();
  if (cache) { try { await cache.delete(pieceKey(url, n)); } catch (e) { /* gone */ } }
}
// The worker says it is alive while bytes come in (at most every few
// seconds): the page then knows a slow download from a stuck one.
let lastSign = 0;
function sign() {
  const now = Date.now();
  if (now - lastSign < 4000 || current === undefined) return;
  lastSign = now;
  postMessage({type: 'progress', id: current, progress: -1});
}
let current;                  // the id of the 'warm' that the download belongs to
// A piece that gets no byte for this long is given up and asked for again: a
// connection that silently died (a phone that slept, a network that changed)
// must not hold the download forever.
const SILENT_MS = 25000;
async function piece(url, n) {
  const quiet = new AbortController();
  let watch = setTimeout(() => quiet.abort(), SILENT_MS);
  const awake = () => { clearTimeout(watch); watch = setTimeout(() => quiet.abort(), SILENT_MS); sign(); };
  try {
    const net = await fetch(url, {headers: {Range: 'bytes=' + n * PIECE + '-' + ((n + 1) * PIECE - 1)}, signal: quiet.signal});
    if (net.status !== 206 && net.status !== 200) throw new Error('HTTP ' + net.status);
    awake();
    if (!net.body || !net.body.getReader) return {net, bytes: await net.arrayBuffer()};
    const reader = net.body.getReader(), chunks = [];
    let size = 0;
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      chunks.push(value); size += value.byteLength;
      awake();
    }
    const bytes = new Uint8Array(size);
    let at = 0;
    for (const c of chunks) { bytes.set(c, at); at += c.byteLength; }
    return {net, bytes: bytes.buffer};
  } finally { clearTimeout(watch); }
}
// Fetches piece n and keeps it; the size of the whole file.
async function fetchPiece(url, n) {
  for (let tries = 0; ; tries++) {
    try {
      const {net, bytes} = await piece(url, n);
      if (net.status === 200) {          // the whole file at once: kept in pieces like the rest
        const total = bytes.byteLength;
        for (let k = 0; k * PIECE < total; k++) await keepPiece(url, k, bytes.slice(k * PIECE, (k + 1) * PIECE), total);
        return total;
      }
      const total = +((net.headers.get('content-range') || '').split('/')[1]) || 0;
      if (!total || bytes.byteLength !== pieceSize(total, n)) throw new Error('short piece');
      await keepPiece(url, n, bytes, total);
      return total;
    } catch (e) {
      await pause(Math.min(15000, 1000 * 2 ** Math.min(tries, 4)));
    }
  }
}
// The size of a file of weights, its first piece fetched if it is not kept yet.
async function sizeOf(url) { return (await kept(url, 0)) || fetchPiece(url, 0); }
// Every piece of a file of weights kept; progress(bytes there). Three pieces
// come in at once: one connection over a slow or relayed link is slower
// than three, and three pieces of 8 MB are little to hold.
const LANES = 3;
async function keepAll(url, progress) {
  const total = await sizeOf(url);
  const count = Math.ceil(total / PIECE);
  let got = 0, next = 0;
  const lane = async () => {
    while (next < count) {
      const n = next++;
      if (!(await kept(url, n))) await fetchPiece(url, n);
      got += pieceSize(total, n);
      if (progress) progress(got);
    }
  };
  await Promise.all(Array.from({length: Math.min(LANES, count)}, lane));
  return total;
}
// A network as one buffer, read from its kept pieces. The first time it is
// checked against its SHA256 (a broken piece is dropped and fetched again
// next time); after that a mark says it is good.
const took = {read: 0, create: 0};
async function whole(url, name) {
  const t = Date.now();
  const total = await keepAll(url);
  const out = new Uint8Array(total);
  for (let n = 0; n * PIECE < total; n++) {
    const bytes = await readPiece(url, n);
    if (!bytes || bytes.byteLength !== pieceSize(total, n)) {   // lost or broken: fetched again next time
      await dropPiece(url, n);
      throw new Error('piece ' + n + ' of ' + url);
    }
    out.set(new Uint8Array(bytes), n * PIECE);
  }
  const cache = await opened(), mark = url + '?checked';
  if (!(cache && await cache.match(mark))) {
    if (await sha256(out.buffer) !== SHA[name]) {
      for (let n = 0; n * PIECE < total; n++) await dropPiece(url, n);
      throw new Error('Prüfsumme stimmt nicht: ' + name);
    }
    if (cache) { try { await cache.put(mark, new Response('ok')); } catch (e) { /* checked again next time */ } }
  }
  took.read += Date.now() - t;
  return out;
}

// Everything the model needs, fetched (and kept); the page shows how far it is.
async function fetchAll(id) {
  current = id;
  await boot();
  await Promise.all(['tts.json', 'unicode_indexer.json'].map((f) => cachedFetch(at(f), f)));
  const files = NETWORKS.map((n) => at(n + '.onnx'));
  // The sizes first (the first piece of each), so the line of the page fills evenly.
  const size = [];
  for (const f of files) size.push(await sizeOf(f));
  const all = size.reduce((a, b) => a + b, 0), got = size.map(() => 0);
  let shown = -1;
  const say = () => {
    const p = Math.min(99, Math.floor(100 * got.reduce((a, b) => a + b, 0) / all));
    if (p !== shown && id !== undefined) { shown = p; postMessage({type: 'progress', id, progress: p}); }
  };
  // One file after the other (each in its lanes): four at once starve a phone.
  for (let i = 0; i < files.length; i++) await keepAll(files[i], (g) => { got[i] = g; say(); });
}

// On the GPU what one network hands to the next stays on the GPU: the text
// for the steps, and the sound from step to step to the vocoder. Only the
// length of the speech and the finished sound are read back, two waits for
// the GPU per part instead of one for every step.
const ON_GPU = {text_encoder: {text_emb: 'gpu-buffer'}, vector_estimator: {denoised_latent: 'gpu-buffer'}};
async function sessions(provider, alive) {
  const made = [];
  took.read = took.create = 0;
  try {
    for (const name of NETWORKS) {
      // A network is handed over and let go before the next is read: all four at once would not fit a phone.
      const model = await whole(at(name + '.onnx'), name + '.onnx');
      const t = Date.now();
      made.push(await ort.InferenceSession.create(model, {executionProviders: [provider], graphOptimizationLevel: 'all',
        ...(provider === 'webgpu' && ON_GPU[name] ? {preferredOutputLocation: ON_GPU[name]} : {})}));
      took.create += Date.now() - t;
      alive();
    }
  } catch (e) {
    for (const s of made) { try { await s.release(); } catch (_) { /* gone */ } }
    throw e;
  }
  return made;
}

async function build(id) {
  await fetchAll(id);
  current = undefined;
  // From here on nothing is fetched, but it takes a while on a phone: the page
  // hears that the worker is alive after every step, or it would take it for stuck.
  const alive = () => { if (id !== undefined) postMessage({type: 'progress', id, progress: 100, making: true}); };
  alive();
  const cfg = await (await cachedFetch(at('tts.json'), 'tts.json')).json();
  const indexer = await (await cachedFetch(at('unicode_indexer.json'), 'unicode_indexer.json')).json();
  // On the GPU where there is one (the page gives that a time limit), else in
  // WASM, on several threads where the page is isolated.
  const tries = gpuWanted ? ['webgpu', 'wasm'] : ['wasm'];
  let last = null;
  for (const provider of tries) {
    let made = null;
    try {
      if (provider === 'webgpu') {
        if (!(await self.navigator.gpu.requestAdapter())) throw new Error('no GPU adapter');
        if (id !== undefined) postMessage({type: 'progress', id, progress: 100, trying: 'webgpu'});
      }
      const t0 = Date.now();
      made = await sessions(provider, alive);
      const e = {cfg, indexer, dp: made[0], enc: made[1], est: made[2], voc: made[3], provider, makeMs: Date.now() - t0,
                 readMs: took.read, createMs: took.create};
      // A provider that takes the networks but speaks nonsense (or nothing) is no provider: one word decides.
      const style = await loadStyle('F1.json');
      const t1 = Date.now();
      const probe = await infer(e, 'Hallo, guten Tag.', 'de', style, STEPS[provider] || 5, 1.05);
      e.probeMs = Date.now() - t1;
      let energy = 0, sound = true;
      for (let i = 0; i < probe.length; i++) { if (!Number.isFinite(probe[i])) { sound = false; break; } energy += probe[i] * probe[i]; }
      if (!sound || probe.length < 2000 || energy / probe.length < 1e-7) throw new Error('silent or broken output');
      alive();
      // The first runs of a network are its slowest (the browser is still making its code fast).
      // They happen here, in the background, not when somebody taps "Vorlesen".
      await infer(e, 'Das ist ein etwas längerer Satz, damit alles warm ist.', 'de', style, 3, 1.05);
      return e;
    } catch (err) {
      last = err;
      if (made) for (const s of made) { try { await s.release(); } catch (_) { /* gone */ } }
      if (provider === 'webgpu' && id !== undefined) postMessage({type: 'nogpu', id});
      if (provider === 'wasm' && ort.env.wasm.numThreads > 1) {
        // Threads need an isolated page; one thread always works.
        ort.env.wasm.numThreads = 1;
        tries.push('wasm');
      }
    }
  }
  throw last || new Error('no provider');
}
function getEngine(id) {
  if (!engine) engine = build(id).catch((e) => { engine = null; throw e; });
  return engine;
}
function loadStyle(file) {
  if (!styles.has(file)) styles.set(file, (async () => {
    if (!SHA[file]) throw new Error('Unbekannte Stimme ' + file);
    const s = await (await cachedFetch(at(file), file)).json();
    const tensor = (v) => new ort.Tensor('float32', Float32Array.from(v.data.flat(Infinity)), v.dims);
    return {ttl: tensor(s.style_ttl), dp: tensor(s.style_dp)};
  })().catch((e) => { styles.delete(file); throw e; }));
  return styles.get(file);
}

// ---- what is read: text as it is spoken -----------------------------------
const ONES = ['null', 'ein', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwölf', 'dreizehn', 'vierzehn', 'fünfzehn', 'sechzehn', 'siebzehn', 'achtzehn', 'neunzehn'];
const TENS = ['', '', 'zwanzig', 'dreißig', 'vierzig', 'fünfzig', 'sechzig', 'siebzig', 'achtzig', 'neunzig'];
function below1000(n, alone) {
  let out = '';
  if (n >= 100) { out += ONES[Math.floor(n / 100)] + 'hundert'; n %= 100; }
  if (n >= 20) out += (n % 10 ? ONES[n % 10] + 'und' : '') + TENS[Math.floor(n / 10)];
  else if (n > 0) out += n === 1 && alone ? 'eins' : ONES[n];
  return out;
}
function cardinal(n) {
  if (!Number.isFinite(n) || n < 0 || n > 999999999999) return null;
  if (n === 0) return 'null';
  const billions = Math.floor(n / 1e9), millions = Math.floor(n / 1e6) % 1000, thousands = Math.floor(n / 1000) % 1000, rest = n % 1000;
  const parts = [];
  if (billions) parts.push(billions === 1 ? 'eine Milliarde' : below1000(billions) + ' Milliarden');
  if (millions) parts.push(millions === 1 ? 'eine Million' : below1000(millions) + ' Millionen');
  let tail = '';
  if (thousands) tail += (thousands === 1 ? 'ein' : below1000(thousands)) + 'tausend';
  if (rest) tail += below1000(rest, true);
  if (tail) parts.push(tail);
  return parts.join(' ');
}
function year(n) {
  if (n >= 1100 && n <= 1999) return below1000(Math.floor(n / 100)) + 'hundert' + (n % 100 ? below1000(n % 100, true) : '');
  return cardinal(n);
}
function ordinal(n) {
  if (n < 1 || n > 999) return null;
  const special = {1: 'erste', 3: 'dritte', 7: 'siebte', 8: 'achte'};
  if (special[n]) return special[n];
  const word = below1000(n);
  return word + (n < 20 ? 'te' : 'ste');
}
const MONTHS = 'Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember';
// Letters as they are named in German, for what is spelled out.
const NAMED = {A: 'A', B: 'Be', C: 'Ce', D: 'De', E: 'E', F: 'Eff', G: 'Ge', H: 'Ha', I: 'I', J: 'Jott', K: 'Ka', L: 'Ell', M: 'Emm', N: 'Enn', O: 'O', P: 'Pe',
               Q: 'Ku', R: 'Err', S: 'Ess', T: 'Te', U: 'U', V: 'Vau', W: 'We', X: 'Ix', Y: 'Ypsilon', Z: 'Zett'};
const spelled = (letters) => letters.split('').map((c) => NAMED[c] || c).join(' ');
// Short words in capitals that are spelled out, not read as a word.
const SPELLED = new Set(('KI VM GPU CPU PDF URL USB API UI PR SSH HTML CSS GLB STL PLY UV TTS STT LLM RAM SSD PC TV DVD CD EU USA BRD DDR ZDF ARD LKW PKW ICE WC SMS ' +
                         'DNS VPN IP TCP UDP HTTP HTTPS JSON XML SQL PNG JPG GIF MP WLAN LAN NUMA ONNX WASM GGUF CMU IK FPS KVM QR OCR MCP').split(' '));
// German as it is said: numbers, times, dates and the signs around them in
// words. The model reads letters; left alone it guesses at digits.
function german(text) {
  let t = text;
  // "3D" is "drei De", "MP3" is "Emm Pe drei", "KI" is "Ka I".
  t = t.replace(/\b(\d{1,4})([A-Z]{1,3})(?![a-zäöüß])/g, (_, n, letters) => n + ' ' + spelled(letters) + ' ')
       .replace(/\b([A-Z]{1,3})(\d{1,4})\b/g, (_, letters, n) => spelled(letters) + ' ' + n)
       .replace(/\b[A-Z]{2,5}\b/g, (word) => (SPELLED.has(word) ? spelled(word) : word))
       .replace(/ -(?=[A-Za-zÄÖÜäöü])/g, ' ');
  t = t.replace(/\bz\.\s?B\./g, 'zum Beispiel').replace(/\bd\.\s?h\./g, 'das heißt').replace(/\bu\.\s?a\./g, 'unter anderem')
       .replace(/\busw\./g, 'und so weiter').replace(/\bbzw\./g, 'beziehungsweise').replace(/\bca\./g, 'circa').replace(/\bggf\./g, 'gegebenenfalls')
       .replace(/\bNr\./g, 'Nummer').replace(/\bDr\./g, 'Doktor').replace(/\bProf\./g, 'Professor').replace(/\bevtl\./g, 'eventuell');
  t = t.replace(/(\d{1,2}):(\d{2})(?::\d{2})?\s*Uhr/g, (_, h, m) => cardinal(+h) + ' Uhr' + (+m ? ' ' + cardinal(+m) : ''));
  t = t.replace(/\b(\d{1,2}):(\d{2})\b/g, (_, h, m) => (+h < 24 && +m < 60 ? cardinal(+h) + ' Uhr' + (+m ? ' ' + cardinal(+m) : '') : _));
  // "am 3. März" is "am dritten März"; "3. März" alone is "dritter März".
  const bent = (before) => (/(?:^|\s)(?:am|dem|den|vom|zum|des|beim|bis|ab|seit)\s*$/i.test(before) ? 'n' : 'r');
  t = t.replace(new RegExp('\\b(\\d{1,2})\\.\\s?(' + MONTHS + ')', 'g'), (_, d, m, at, all) => (ordinal(+d) || d) + bent(all.slice(0, at)) + ' ' + m);
  t = t.replace(/\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b/g, (_, d, m, y, at, all) => (ordinal(+d) || d) + bent(all.slice(0, at)) + ' ' + (ordinal(+m) || m) + bent(all.slice(0, at)) + ' ' + year(+y));
  t = t.replace(/(\d)\s?[-–]\s?(\d)/g, '$1 bis $2');
  t = t.replace(/(\d)\s?°\s?C\b/g, '$1 Grad Celsius').replace(/(\d)\s?°/g, '$1 Grad').replace(/(\d)\s?%/g, '$1 Prozent')
       .replace(/(\d)\s?€/g, '$1 Euro').replace(/€\s?(\d[\d.,]*)/g, '$1 Euro').replace(/(\d)\s?\$/g, '$1 Dollar').replace(/\$\s?(\d[\d.,]*)/g, '$1 Dollar')
       .replace(/(\d)\s?km\/h\b/g, '$1 Kilometer pro Stunde').replace(/(\d)\s?km\b/g, '$1 Kilometer').replace(/(\d)\s?cm\b/g, '$1 Zentimeter')
       .replace(/(\d)\s?mm\b/g, '$1 Millimeter').replace(/(\d)\s?kg\b/g, '$1 Kilogramm').replace(/(\d)\s?GB\b/g, '$1 Gigabyte').replace(/(\d)\s?MB\b/g, '$1 Megabyte')
       .replace(/(\d)\s?m\b/g, '$1 Meter').replace(/(\d)\s?g\b/g, '$1 Gramm').replace(/(\d)\s?s\b/g, '$1 Sekunden').replace(/(\d)\s?W\b/g, '$1 Watt');
  t = t.replace(/&/g, ' und ').replace(/\+/g, ' plus ').replace(/=/g, ' gleich ').replace(/(\d)\s?[x×]\s?(\d)/g, '$1 mal $2');
  // 1.234.567 and 3,5; a year stands as a year.
  t = t.replace(/\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b|\b\d+(?:,\d+)?\b/g, (number, at, all) => {
    const [whole, frac] = number.replace(/\./g, '').split(',');
    if (whole.length > 12) return whole.split('').map((d) => cardinal(+d)).join(' ');
    const n = +whole;
    let said = !frac && whole.length === 4 && n >= 1100 && n <= 2099 && !/[.,]\d{3}/.test(number) ? year(n) : cardinal(n);
    if (said === null) return number;
    if (frac) said += ' Komma ' + frac.split('').map((d) => cardinal(+d)).join(' ');
    else if (n === 1 && /^\s+[A-Za-zÄÖÜäöü]/.test(all.slice(at + number.length))) said = 'ein';
    return said;
  });
  return t;
}
function spoken(text, lang) {
  let t = String(text || '');
  if (lang === 'de') t = german(t);
  t = t.normalize('NFKD').replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F1E6}-\u{1F1FF}\u{FE0F}\u{200D}]+/gu, '');
  // (Decomposed on purpose: the model's table knows "a" and the two dots above it, not "ä" as one sign.)
  const swap = {'\u2013': '-', '\u2011': '-', '\u2014': '-', '_': ' ', '\u201C': '"', '\u201D': '"', '\u201E': '"', '\u2018': "'", '\u2019': "'", '\u00B4': "'", '`': "'",
                '[': ' ', ']': ' ', '|': ' ', '/': ' ', '#': ' ', '\u2192': ' ', '\u2190': ' ', '*': ' ', '\u00AB': '"', '\u00BB': '"', '\u00A0': ' '};
  t = t.replace(/[\u2013\u2011\u2014_\u201C\u201D\u201E\u2018\u2019\u00B4`\[\]|\/#\u2192\u2190*\u00AB\u00BB\u00A0]/g, (c) => swap[c]);
  t = t.replace(/[♥☆♡©\\]/g, '').replace(/@/g, lang === 'de' ? ' ät ' : ' at ');
  t = t.replace(/ ([,.!?;:])/g, '$1').replace(/"{2,}/g, '"').replace(/'{2,}/g, "'").replace(/\s+/g, ' ').trim();
  if (t && !/[.!?;:,'")\]}…]$/.test(t)) t += '.';
  return t;
}
// A part no longer than the model reads well in one go, cut at the end of a sentence or a clause.
function pieces(text, most) {
  const out = [];
  let rest = text;
  while (rest.length > most) {
    const head = rest.slice(0, most);
    let cut = Math.max(head.lastIndexOf('. '), head.lastIndexOf('! '), head.lastIndexOf('? '));
    if (cut < most * 0.4) cut = Math.max(head.lastIndexOf('; '), head.lastIndexOf(': '), head.lastIndexOf(', '));
    if (cut < most * 0.4) cut = head.lastIndexOf(' ');
    if (cut < 1) cut = most - 1;
    out.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) out.push(rest);
  return out;
}

// ---- the model -----------------------------------------------------------------
function noise() {
  const u1 = Math.max(1e-4, Math.random()), u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}
// One piece of text as sound (Float32Array at the model's sample rate).
async function infer(e, text, lang, style, steps, speed) {
  const tagged = '<' + lang + '>' + text + '</' + lang + '>';
  const ids = new BigInt64Array(tagged.length);
  for (let i = 0; i < tagged.length; i++) {
    const code = tagged.codePointAt(i);
    ids[i] = BigInt(code < e.indexer.length ? e.indexer[code] : -1);
  }
  const textIds = new ort.Tensor('int64', ids, [1, tagged.length]);
  const textMask = new ort.Tensor('float32', new Float32Array(tagged.length).fill(1), [1, 1, tagged.length]);
  const duration = (await e.dp.run({text_ids: textIds, style_dp: style.dp, text_mask: textMask})).duration.data[0] / speed;
  const textEmb = (await e.enc.run({text_ids: textIds, style_ttl: style.ttl, text_mask: textMask})).text_emb;
  const gone = (t) => { if (t && t.location === 'gpu-buffer') { try { t.dispose(); } catch (_) { /* gone */ } } };
  const rate = e.cfg.ae.sample_rate, chunk = e.cfg.ae.base_chunk_size * e.cfg.ttl.chunk_compress_factor;
  const length = Math.max(1, Math.floor((Math.floor(duration * rate) + chunk - 1) / chunk));
  const dim = e.cfg.ttl.latent_dim * e.cfg.ttl.chunk_compress_factor;
  const start = new Float32Array(dim * length);
  for (let i = 0; i < start.length; i++) start[i] = noise();
  let latent = new ort.Tensor('float32', start, [1, dim, length]);
  const latentMask = new ort.Tensor('float32', new Float32Array(length).fill(1), [1, 1, length]);
  const total = new ort.Tensor('float32', Float32Array.from([steps]), [1]);
  let wav;
  try {
    for (let step = 0; step < steps; step++) {
      const out = await e.est.run({noisy_latent: latent, text_emb: textEmb, style_ttl: style.ttl,
                                   latent_mask: latentMask, text_mask: textMask, current_step: new ort.Tensor('float32', Float32Array.from([step]), [1]), total_step: total});
      gone(latent);
      latent = out.denoised_latent;
    }
    wav = (await e.voc.run({latent})).wav_tts.data;
  } finally { gone(latent); gone(textEmb); }
  // The vocoder fills its last chunk; the speech is as long as the model said.
  const samples = Math.min(wav.length, Math.max(1, Math.floor(duration * rate)));
  return Float32Array.from(wav.subarray ? wav.subarray(0, samples) : Array.prototype.slice.call(wav, 0, samples));
}
// Every part is brought to the same speech level (RMS of the louder half of
// 20 ms frames, so pauses do not count) with a peak ceiling, and gets 8 ms
// fades so part boundaries do not click.
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
async function synth(text, model, id, rate, lang, steps) {
  const e = await getEngine(id);
  const style = await loadStyle(model);
  const tongue = LANGS.includes(lang) ? lang : 'de';
  const said = spoken(text, tongue);
  const sampleRate = e.cfg.ae.sample_rate;
  if (!said || !/[\p{L}\p{N}]/u.test(said)) return {audio: new Float32Array(0), rate: sampleRate, synthMs: 0, provider: e.provider};
  const t0 = Date.now();
  const speed = 1.05 * (rate >= 0.5 && rate <= 2.5 ? rate : 1);
  const parts = [], gap = Math.round(sampleRate * 0.12);
  const list = pieces(said, tongue === 'ko' || tongue === 'ja' ? 120 : 280);
  const taken = steps >= 2 && steps <= 12 ? Math.round(steps) : STEPS[e.provider] || 5;
  for (let i = 0; i < list.length; i++) {
    parts.push(await infer(e, list[i], tongue, style, taken, speed));
    if (i + 1 < list.length) parts.push(new Float32Array(gap));
  }
  let n = 0;
  for (const p of parts) n += p.length;
  const audio = new Float32Array(n);
  let o = 0;
  for (const p of parts) { audio.set(p, o); o += p.length; }
  return {audio: level(trimmed(audio, sampleRate, /[.!?…]["')\]]?$/.test(said)), sampleRate), rate: sampleRate, synthMs: Date.now() - t0, provider: e.provider, steps: taken};
}
// The model leaves silence before and after what it says, sometimes much.
// Between the parts of one reading that would be a pause where nobody
// pauses: a part keeps a breath after the end of a sentence and next to
// nothing after a clause.
function trimmed(audio, rate, sentence) {
  let peak = 0;
  for (let i = 0; i < audio.length; i++) { const a = Math.abs(audio[i]); if (a > peak) peak = a; }
  if (peak < 1e-4) return audio;
  const quiet = peak * 0.02;
  let from = 0, to = audio.length - 1;
  while (from < to && Math.abs(audio[from]) < quiet) from++;
  while (to > from && Math.abs(audio[to]) < quiet) to--;
  from = Math.max(0, from - Math.round(rate * 0.02));
  to = Math.min(audio.length, to + 1 + Math.round(rate * (sentence ? 0.22 : 0.05)));
  return from === 0 && to === audio.length ? audio : audio.slice(from, to);
}
// One request at a time: a stopped playback may still have a part in flight,
// and two overlapping runs of one session can fail. 'cancel' is handled at
// once: speak requests of a stopped playback that are still queued are
// answered as cancelled without synthesis.
let chain = Promise.resolve();
let cancelledBefore = 0;
if (inWorker) self.onmessage = ev => {
  const data = ev.data || {};
  if (data.type === 'cancel') { cancelledBefore = Math.max(cancelledBefore, +data.before || 0); return; }
  // A voice is a small file; fetching it holds nothing up.
  if (data.type === 'prefetch') {
    (SHA[data.model] ? cachedFetch(at(data.model), data.model) : Promise.reject(new Error('Unbekannte Stimme'))).then(() => postMessage({type: 'ready', id: data.id}), e => postMessage({type: 'error', id: data.id, error: String(e && e.message || e)}));
    return;
  }
  chain = chain.then(() => handle(data));
};
async function handle({type, id, text, model, rate, lang, steps}) {
  try {
    if (type === 'warm') {
      const e = await getEngine(id);
      await loadStyle(model);
      postMessage({type: 'ready', id, threads: ort.env.wasm.numThreads, provider: e.provider, makeMs: e.makeMs, probeMs: e.probeMs,
                   readMs: e.readMs, createMs: e.createMs});
      return;
    }
    if (type === 'speak') {
      if (id <= cancelledBefore) { postMessage({type: 'error', id, error: 'cancelled'}); return; }
      const out = await synth(text, model, id, rate, lang, steps);
      postMessage({type: 'audio', id, audio: out.audio, rate: out.rate, synthMs: out.synthMs, provider: out.provider, threads: ort.env.wasm.numThreads, steps: out.steps}, [out.audio.buffer]);
    }
  } catch (e) { postMessage({type: 'error', id, error: String(e && e.message || e)}); }
}
// For the tests of the text rules (node): nothing else leaves this file.
if (typeof module !== 'undefined') module.exports = {spoken, pieces, german};

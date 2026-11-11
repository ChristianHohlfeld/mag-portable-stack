// Read-aloud worker: Supertonic 3 in onnxruntime-web (classic Worker, handed
// out by the room under /tts/<folder>/). The speech is made on the device:
// in WASM, on several threads where the page is isolated. The model (four
// networks, about 400 MB) is fetched once and stays in Cache Storage; a
// voice is a small style file. The page asks for the model in the
// background right after it has loaded, so the first "Vorlesen" does not
// wait for a download. Nothing is sent to a server.
/* global ort */
const inWorker = typeof importScripts === 'function';   // not so in the test of the text rules
let base = '', home = '';     // where the runtime lies, and where the model does
if (inWorker) {
  importScripts('ort.webgpu.min.js');
  base = self.location.href.replace(/[?#].*$/, '').replace(/[^/]*$/, '');
  // The model has a folder of its own (?m=...), so it stays in the browser when this worker changes.
  const folder = new URL(self.location.href).searchParams.get('m') || '';
  home = /^[\w.-]+$/.test(folder) ? base + '../' + folder + '/' : base;
  ort.env.wasm.wasmPaths = base;
  ort.env.wasm.proxy = false;
  ort.env.wasm.numThreads = (function () {
    try {
      if (!self.crossOriginIsolated) return 1;
      const n = (self.navigator && self.navigator.hardwareConcurrency) || 1;
      return Math.max(1, Math.min(4, n - 2));   // the big cores, and two left for the page
    } catch (e) { return 1; }
  })();
}
const CACHE = 'mag-tts-s3';
const NETWORKS = ['duration_predictor.onnx', 'text_encoder.onnx', 'vector_estimator.onnx', 'vocoder.onnx'];
const LANGS = ['en', 'ko', 'ja', 'ar', 'bg', 'cs', 'da', 'de', 'el', 'es', 'et', 'fi', 'fr', 'hi', 'hr', 'hu', 'id', 'it', 'lt', 'lv', 'nl', 'pl', 'pt', 'ro', 'ru', 'sk', 'sl', 'sv', 'tr', 'uk', 'vi'];
// How many steps the sound is refined in: more is cleaner and takes longer.
const STEPS = {webgpu: 5, wasm: 5};
let engine = null;            // promise of {cfg, indexer, dp, enc, est, voc, provider}
const styles = new Map();     // voice file -> promise of {ttl, dp}

// The voices of the reader before this one are not needed any more.
if (inWorker) { try { caches.delete('mag-tts-v1'); } catch (e) { /* no Cache Storage */ } }

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
    progress(got, total);
  }
  const r = new Response(new Blob(parts), {headers: {'content-type': net.headers.get('content-type') || 'application/octet-stream'}});
  await store(r);
  return r;
}

// Everything the model needs, fetched (and kept); the page shows how far it is.
async function fetchAll(id) {
  const got = new Array(NETWORKS.length).fill(0), size = new Array(NETWORKS.length).fill(0);
  let shown = -1;
  const say = () => {
    const all = size.reduce((a, b) => a + b, 0);
    if (!all || id === undefined) return;
    const p = Math.min(99, Math.round(100 * got.reduce((a, b) => a + b, 0) / all));
    if (p !== shown) { shown = p; postMessage({type: 'progress', id, progress: p}); }
  };
  await Promise.all([cachedFetch(home + 'tts.json'), cachedFetch(home + 'unicode_indexer.json')]);
  // One after the other: four parallel downloads of this size starve a phone.
  for (let i = 0; i < NETWORKS.length; i++)
    await cachedFetch(home + NETWORKS[i], (g, t) => { got[i] = g; size[i] = t; say(); });
}

async function sessions(provider) {
  const made = [];
  try {
    for (const name of NETWORKS) {
      // A network is handed over and let go before the next is read: all four at once would not fit a phone.
      const buf = await (await cachedFetch(home + name)).arrayBuffer();
      made.push(await ort.InferenceSession.create(buf, {executionProviders: [provider], graphOptimizationLevel: 'all'}));
    }
  } catch (e) {
    for (const s of made) { try { await s.release(); } catch (_) { /* gone */ } }
    throw e;
  }
  return made;
}

async function build(id) {
  await fetchAll(id);
  const cfg = await (await cachedFetch(home + 'tts.json')).json();
  const indexer = await (await cachedFetch(home + 'unicode_indexer.json')).json();
  // In WASM, on several threads where the page is isolated. WebGPU is not
  // tried: on a phone, making the networks for its GPU can take minutes or
  // never end, and nobody can hear a model that is still getting ready.
  const tries = ['wasm'];
  let last = null;
  for (const provider of tries) {
    let made = null;
    try {
      if (provider === 'wasm' && last) ort.env.wasm.numThreads = Math.min(ort.env.wasm.numThreads, 4);
      made = await sessions(provider);
      const e = {cfg, indexer, dp: made[0], enc: made[1], est: made[2], voc: made[3], provider};
      // A provider that takes the networks but speaks nonsense (or nothing) is no provider: one word decides.
      const style = await loadStyle('F1.json');
      const probe = await infer(e, 'Hallo, guten Tag.', 'de', style, STEPS[provider] || 5, 1.05);
      let energy = 0, sound = true;
      for (let i = 0; i < probe.length; i++) { if (!Number.isFinite(probe[i])) { sound = false; break; } energy += probe[i] * probe[i]; }
      if (!sound || probe.length < 2000 || energy / probe.length < 1e-7) throw new Error('silent or broken output');
      // The first runs of a network are its slowest (the browser is still making its code fast).
      // They happen here, in the background, not when somebody taps "Vorlesen".
      await infer(e, 'Das ist ein etwas längerer Satz, damit alles warm ist.', 'de', style, 3, 1.05);
      return e;
    } catch (err) {
      last = err;
      if (made) for (const s of made) { try { await s.release(); } catch (_) { /* gone */ } }
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
    const s = await (await cachedFetch(home + file)).json();
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
  const rate = e.cfg.ae.sample_rate, chunk = e.cfg.ae.base_chunk_size * e.cfg.ttl.chunk_compress_factor;
  const length = Math.max(1, Math.floor((Math.floor(duration * rate) + chunk - 1) / chunk));
  const dim = e.cfg.ttl.latent_dim * e.cfg.ttl.chunk_compress_factor;
  let latent = new Float32Array(dim * length);
  for (let i = 0; i < latent.length; i++) latent[i] = noise();
  const latentMask = new ort.Tensor('float32', new Float32Array(length).fill(1), [1, 1, length]);
  const total = new ort.Tensor('float32', Float32Array.from([steps]), [1]);
  for (let step = 0; step < steps; step++) {
    const out = await e.est.run({noisy_latent: new ort.Tensor('float32', latent, [1, dim, length]), text_emb: textEmb, style_ttl: style.ttl,
                                 latent_mask: latentMask, text_mask: textMask, current_step: new ort.Tensor('float32', Float32Array.from([step]), [1]), total_step: total});
    latent = Float32Array.from(out.denoised_latent.data);
  }
  const wav = (await e.voc.run({latent: new ort.Tensor('float32', latent, [1, dim, length])})).wav_tts.data;
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
    cachedFetch(home + data.model).then(() => postMessage({type: 'ready', id: data.id}), e => postMessage({type: 'error', id: data.id, error: String(e && e.message || e)}));
    return;
  }
  chain = chain.then(() => handle(data));
};
async function handle({type, id, text, model, rate, lang, steps}) {
  try {
    if (type === 'warm') {
      const e = await getEngine(id);
      await loadStyle(model);
      postMessage({type: 'ready', id, threads: ort.env.wasm.numThreads, provider: e.provider});
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

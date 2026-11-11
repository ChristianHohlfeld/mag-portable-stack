// Read aloud, on the device: every answer can be spoken with a local voice.
// Supertonic 3 runs in the browser (a Web Worker with onnxruntime-web; ops/tts;
// the names below still say "piper", the voice this replaced): the model
// downloads once into Cache Storage and after that a playback touches no
// server. Playback starts with the first words while the next parts are made,
// laid end to end on the audio clock. Nothing plays by itself, and no speech
// is ever made on the server.
//
// Every speaker has a voice and a tempo of their own (Lena, Noah, Chris), kept
// on this device. Until the model is there, or where it fails, the best
// system voice reads instead, but never in the middle of a message that the
// model has begun.
'use strict';
(function () {
  const { MAG } = window, S = MAG.state;
  const synth = window.speechSynthesis;
  const systemOk = !!synth && typeof window.SpeechSynthesisUtterance === 'function';
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const piperPossible = typeof Worker === 'function' && !!AudioCtx && typeof fetch === 'function';
  const GOOD_VOICE = /natural|premium|siri|enhanced|neural|online/i;
  const ROBOT_VOICE = /espeak|mbrola|festival|flite|pico|ekho/i;
  const LIVE_IDLE_MS = 30000;
  // A part may take a while on a phone without WebGPU. Loading the model is a
  // download of about 200 MB the first time: it has no limit at all (the
  // worker asks again for what does not come, by itself, for as long as the
  // page is open), and making the model ready after it has one for standing
  // still (the worker reports every step). Time in which the page was not
  // in view does not count: a phone holds a page in the background.
  const SPEAK_TIMEOUT_MS = 120000, WARM_TIMEOUT_MS = 180000;
  // The device's GPU gets this long to make the model ready and speak its
  // first word; a GPU that does not is not asked again on this device, and
  // the model is made in WASM instead (it is already downloaded).
  const GPU_LIMIT_MS = 90000;
  const gpuOk = () => !!navigator.gpu && localStorage.getItem('mag-tts-gpu') !== 'no';
  function noGpu() { try { localStorage.setItem('mag-tts-gpu', 'no'); } catch {} }
  let current = null;

  function normalize(text) {
    return String(text || '')
      .replace(/\[\[MAG:(?:WEITER|ENDE)\]\]/g, '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/```[\s\S]*$/, ' ') // code block still streaming
      .replace(/[*_`#>|]+/g, ' ')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/\r\n?/g, '\n')
      .trim();
  }

  // Safari may stop speaking mid-way through long utterances. Keep each one short,
  // using a word/sentence boundary, but avoid inserting extra speech pauses.
  function split(text, max = 170) {
    const out = [];
    let rest = text;
    while (rest.length > max) {
      let cut = Math.max(rest.lastIndexOf('. ', max), rest.lastIndexOf('! ', max),
                         rest.lastIndexOf('? ', max), rest.lastIndexOf('\n', max));
      if (cut < max / 3) cut = rest.lastIndexOf(' ', max);
      if (cut < max / 3) cut = max;
      else cut += 1;
      out.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trimStart();
    }
    if (rest.trim()) out.push(rest.trim());
    return out.filter(Boolean);
  }

  // One sentence per Piper call, capped so a long sentence still starts soon.
  // Abbreviations (z.B., Dr.) are not treated as sentence ends.
  const ABBREV = /^(dr|nr|bzw|ca|ggf|usw|etc|prof|dipl|ing|str|tel|fax|abs|vgl)$/i;
  // The very first part of a playback is short (`first`): a clause, not a
  // whole sentence. The sound starts after what a few words take to make,
  // and the rest is made while they play. How long the first parts are
  // follows from how fast this device makes speech (see line()): each is as
  // long as can be made while what is ready plays, and never so short that
  // it plays for less time than it takes to make (a GPU spends about a
  // second on every part, however short): then the reading runs on without
  // a pause from the first word.
  function piperParts(text, max = partMax(), first = false) {
    const out = [];
    let rest = String(text || '').trim();
    const { a, b } = line();
    // Letters below which a part in `steps` plays for less time than it takes to make.
    const least = (steps) => (b * steps < 0.95 ? 14 * a / (1 - b * steps) : 14 * a * 20);
    let planning = first, ahead = 0;     // seconds of sound that are ready ahead of what is being made
    const advance = (piece) => {
      if (!planning) return;
      const sec = piece.length / 14;
      ahead = out.length === 1 ? sec : Math.max(0, ahead + sec - (a + b * 2 * sec));
    };
    if (first && rest.length > 40) {
      const lo = Math.min(60, Math.max(8, Math.ceil(1.3 * least(3))));
      const head = rest.slice(0, Math.max(44, lo + 30));
      let cut = -1;
      for (const m of head.matchAll(/[,;:–—]\s|[.!?]\s|\s(?=(?:und|oder|aber|denn|weil|dass|wenn|als|wie)\s)/g))
        if (m.index >= lo && cut < 0) cut = m.index + (/\s/.test(m[0][0]) ? 0 : 1);       // the first place a speaker would breathe
      if (cut < 0) cut = head.lastIndexOf(' ', Math.max(30, lo + 16));
      if (cut >= lo) { out.push(rest.slice(0, cut).trim()); advance(out[0]); rest = rest.slice(cut).trimStart(); }
    }
    while (rest) {
      let cut = rest.length;
      const re = /[.!?](?:\s+|$)/g;
      let m;
      while ((m = re.exec(rest))) {
        const at = m.index + m[0].length;
        const token = rest.slice(0, m.index).split(/\s/).pop() || '';
        if (m[0][0] === '.' && (token.includes('.') || ABBREV.test(token))) continue;
        if (at >= 8) { cut = at; break; }
      }
      let most = max;
      if (planning) {
        if (!out.length) most = Math.min(max, Math.max(36, Math.ceil(1.3 * least(3))));
        else {
          const lo = Math.ceil(1.3 * least(2)), can = Math.floor(14 * (0.85 * ahead - a) / (b * 2));
          most = Math.min(max, Math.max(24, can >= lo ? can : lo));
        }
      }
      if (cut > most) {
        const word = rest.lastIndexOf(' ', most);
        cut = word >= Math.floor(most / 3) ? word + 1 : most;
      }
      const piece = rest.slice(0, cut).trim();
      if (!piece) break;
      out.push(piece);
      advance(piece);
      if (planning && most >= max) planning = false;
      rest = rest.slice(cut).trimStart();
    }
    return out;
  }
  // End of the last complete sentence (or line) in a text that is still
  // growing; the unfinished tail waits for more text. A very long tail is cut
  // at a word so a long sentence does not hold playback back.
  function completeLength(text) {
    let end = 0;
    const re = /[.!?:;](?=\s)|\n/g;
    let m;
    while ((m = re.exec(text))) {
      const token = text.slice(0, m.index).split(/\s/).pop() || '';
      if (m[0] === '.' && (token.includes('.') || ABBREV.test(token) || /^\d+$/.test(token))) continue;
      end = m.index + 1;
    }
    if (text.length - end > 160) { const w = text.lastIndexOf(' ', text.length - 20); if (w > end) end = w + 1; }
    return end;
  }

  // MAG speaks German: Piper reads unless a text is clearly English.
  const deWords = new Set('der die das und ist nicht ich du wir sie ein eine mit auf für von zu den dem des sich auch es bitte danke ja nein oder aber wie was kann noch nur schon hier jetzt'.split(' '));
  const enWords = new Set('the and is are was were you your i it this that with for of to in on not have has be can will what please thanks yes no but how do does'.split(' '));
  function textLocale(text) {
    if (/[äöüß]/i.test(text)) return 'de-DE';
    const words = String(text || '').toLowerCase().match(/[a-z']+/g) || [];
    let de = 0, en = 0;
    for (const w of words) { if (deWords.has(w)) de++; if (enWords.has(w)) en++; }
    return !de && en >= 2 && en * 4 >= words.length ? 'en-US' : 'de-DE';
  }
  // Best system voice: same language, prefer natural-sounding names, then local.
  function bestVoice(lang) {
    const voices = (synth?.getVoices?.() || []).filter(v => !ROBOT_VOICE.test((v.name || '') + ' ' + (v.voiceURI || '')));
    const l = lang.toLowerCase(), base = l.split('-')[0];
    const same = voices.filter(v => v.lang?.toLowerCase() === l);
    const near = voices.filter(v => v.lang?.toLowerCase().split('-')[0] === base);
    for (const pool of [same, near]) {
      const pick = pool.find(v => GOOD_VOICE.test(v.name || '')) || pool.find(v => v.localService) || pool[0];
      if (pick) return pick;
    }
    return null;
  }

  // ---- who speaks with which voice, how fast --------------------------------
  const RATES = [0.75, 0.85, 1, 1.15, 1.3, 1.5, 1.75, 2];
  const DEFAULT_VOICE = { user: 'm1', lena: 'f1', noah: 'm2' };
  const FALLBACK_VOICES = [
    { id: 'f1', label: 'Frau 1', model: 'F1.json' }, { id: 'm1', label: 'Mann 1', model: 'M1.json' }, { id: 'm2', label: 'Mann 2', model: 'M2.json' }];
  // A speaker is a bot (by its id, which a rename does not change) or Chris.
  const speakerKey = (who) => (who === 'chris' || who === 'user' ? 'user' : 'bot:' + who);
  function prefs() { try { return JSON.parse(localStorage.getItem('mag-tts-speakers') || '{}') || {}; } catch { return {}; } }
  function savePref(key, entry) {
    const all = prefs();
    all[key] = { ...(all[key] || {}), ...entry };
    try { localStorage.setItem('mag-tts-speakers', JSON.stringify(all)); } catch {}
  }
  function voiceList() { return piper.config?.voices?.length ? piper.config.voices : FALLBACK_VOICES; }
  function profile(who) {
    const key = speakerKey(who), saved = prefs()[key] || {}, voices = voiceList();
    // A voice that was chosen from an earlier set of voices is gone: the speaker's own default takes its place.
    const usual = piper.config?.defaults?.[key === 'user' ? 'user' : who] || DEFAULT_VOICE[key === 'user' ? 'user' : who] || 'm1';
    const wanted = saved.voice === 'system' || voices.some((v) => v.id === saved.voice) ? saved.voice : usual;
    const voice = wanted === 'system' ? null : (voices.find((v) => v.id === wanted) || voices.find((v) => v.id === usual) || voices[0]);
    return { key, voiceId: voice ? voice.id : 'system', voice, rate: RATES.includes(+saved.rate) ? +saved.rate : 1 };
  }
  function rateFor(key) { const r = +(prefs()[key] || {}).rate; return RATES.includes(r) ? r : 1; }

  // ---- the buttons ------------------------------------------------------------
  function setButton(button) {
    const playing = !!current && current.key === button.dataset.speak;
    const busy = playing && !!current.loading;
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-busy', String(busy));
    const label = busy ? 'Stimme wird geladen' : playing ? 'Vorlesen stoppen' : voiceState.state && voiceState.state !== 'ready' ? 'Vorlesen (' + stateText() + ')' : 'Vorlesen';
    button.setAttribute('aria-label', label);
    button.title = label;
    button.innerHTML = busy ? '<span class="spin"></span>' : playing ? MAG.icons.stop : MAG.icons.speak;
  }
  function setButtons() { document.querySelectorAll('.act[data-speak]').forEach(setButton); }

  // ---- Piper (worker) ---------------------------------------------------------
  const piper = { config: null, configP: null, failed: new Map(), worker: null, ready: new Set(), loading: new Map(), ctx: null, seq: 0, pending: new Map(), sources: new Set(),
                };
  function loadConfig() {
    if (!S.features?.tts) return Promise.resolve(null);
    if (!piper.configP) piper.configP = (async () => {
      try {
        const r = await MAG.fetch('/tts/config.json', { cache: 'no-store' });
        if (r.ok) piper.config = await r.json();
      } catch { piper.config = null; }
      if (!piper.config) piper.configP = null; // retry on the next tap
      return piper.config;
    })();
    return piper.configP;
  }
  function worker() {
    if (!piper.worker) {
      // The model lies in a folder of its own, so it stays in the browser when the worker changes.
      const query = new URLSearchParams();
      if (piper.config.models) query.set('m', piper.config.models);
      if (gpuOk()) query.set('gpu', '1');
      const w = piper.worker = new Worker('/tts/' + piper.config.dir + '/tts-worker.js?' + query);
      piper.worker.onmessage = (ev) => {
        const m = ev.data || {};
        if (m.type === 'nogpu') { clearTimeout(piper.gpuTimer); noGpu(); report({ gpu_fail: 1 }); return; }
        if (m.trying === 'webgpu') {
          clearTimeout(piper.gpuTimer);
          piper.gpuTimer = setTimeout(() => {
            if (piper.worker !== w || piper.ready.size) return;
            // The GPU is still at it: this worker ends, and the next makes the model in WASM.
            noGpu(); report({ gpu_fail: 2 });
            const again = [...piper.loading.keys()];
            resetWorker();
            setTimeout(() => again.forEach((model) => { piper.failed.delete(model); warm({ model }); }), 0);
          }, GPU_LIMIT_MS);
        }
        if (m.type === 'progress') {     // the model is coming in: that is not standing still
          for (const loading of piper.pending.values()) {
            if (!loading.again) continue;
            clearTimeout(loading.timer);
            // While it downloads, the worker itself asks again for what does not come; after that, a step must not take forever.
            loading.timer = m.making ? setTimeout(loading.again, WARM_TIMEOUT_MS) : 0;
          }
          if (!piper.ready.size) {
            if (m.making) shown('making', 1);
            else if (m.progress >= 0) shown('loading', m.progress / 100);
          }
          if (m.progress >= next) { report({ load_pct: m.progress }); next = 25 * (Math.floor(m.progress / 25) + 1); }
          return;
        }
        const p = piper.pending.get(m.id);
        if (!p) return;
        piper.pending.delete(m.id);
        clearTimeout(p.timer);
        if (m.type === 'ready') clearTimeout(piper.gpuTimer);
        if (m.type === 'error') p.reject(new Error(m.error)); else p.resolve(m);
      };
      piper.worker.onerror = () => resetWorker();
    }
    return piper.worker;
  }
  // Every request has a deadline: a stuck WASM call or a crashed worker
  // must not leave the button spinning.
  function ask(msg, timeout) {
    const id = ++piper.seq;
    return new Promise((resolve, reject) => {
      const late = () => {
        if (!piper.pending.has(id)) return;
        // A page out of view is held by the phone: that is no reason to give up on the worker.
        if (document.visibilityState !== 'visible' || performance.now() - seen < 20000) {
          const p = piper.pending.get(id);
          p.timer = setTimeout(late, 20000);
          return;
        }
        // A slow background download is not a stuck worker.
        if (msg.type === 'prefetch') piper.pending.delete(id); else resetWorker();
        reject(new Error('Zeitüberschreitung der Stimme'));
      };
      const timer = setTimeout(late, timeout);
      piper.pending.set(id, {resolve, reject, timer, again: msg.type === 'warm' ? late : null});
      try { worker().postMessage({...msg, id}); } catch (e) { piper.pending.delete(id); clearTimeout(timer); reject(e); }
    });
  }
  let seen = performance.now();          // when the page came into view last
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') seen = performance.now(); });
  // Resolves true once the model is usable, false if Piper failed for it. A
  // failure is retried after a short pause instead of disabling the voice
  // until reload (mobile networks drop).
  // Whether the voice is there, shown without taking room: while the model
  // loads, a thin line under every "Vorlesen" button fills up (its title
  // says how far), and it breathes while the model is made ready; once it is
  // there, nothing is shown in the chat. The settings say it in words.
  let next = 25;                         // the next quarter of the download that is reported
  const voiceState = { state: '', part: 0, provider: '' };
  function stateText() {
    const { state, part, provider } = voiceState;
    if (state === 'ready') return 'Stimme bereit' + (provider === 'webgpu' ? ' (Grafikchip)' : provider === 'wasm' ? ' (Prozessor)' : '');
    if (state === 'making') return 'Stimme wird bereit gemacht … solange liest die Gerätestimme';
    if (state === 'loading') return 'Stimme lädt' + (part ? ' ' + Math.round(100 * part) + ' %' : '') + ' … solange liest die Gerätestimme';
    return 'Stimme noch nicht geladen; solange liest die Gerätestimme';
  }
  function shown(state, part) {
    const root = document.documentElement;
    if (root.dataset.voice !== state) root.dataset.voice = state;
    voiceState.state = state;
    if (part !== undefined) { voiceState.part = Math.max(0, Math.min(1, part)); root.style.setProperty('--voice-p', String(voiceState.part)); }
    const title = state === 'ready' ? 'Vorlesen' : 'Vorlesen (' + stateText() + ')';
    document.querySelectorAll('.act[data-speak][aria-pressed="false"]').forEach((b) => { b.title = title; });
    document.querySelectorAll('.voice-state').forEach((n) => { n.textContent = stateText(); });
  }
  function warm(v) {
    if (piper.ready.has(v.model)) return Promise.resolve(true);
    if (!piper.ready.size) shown('loading');
    if ((piper.failed.get(v.model) || 0) > Date.now()) return Promise.resolve(false);
    if (piper.loading.has(v.model)) return piper.loading.get(v.model);
    const began = performance.now();
    const p = ask({type: 'warm', model: v.model}, WARM_TIMEOUT_MS)
      .then((m) => { piper.ready.add(v.model); voiceState.provider = m?.provider || voiceState.provider; shown('ready'); setTimeout(paint, 0); if (m?.provider) report({ warm_ms: Math.round(performance.now() - began), make_ms: m.makeMs | 0, probe_ms: m.probeMs | 0, provider: m.provider, threads: m.threads | 0, isolated: self.crossOriginIsolated ? 1 : 0 }); return true; })
      .catch(() => {
        report({ warm_fail: 1, warm_ms: Math.round(performance.now() - began) });
        // Not there yet (a broken connection, a full device): it is tried again in a while, by itself.
        piper.failed.set(v.model, Date.now() + 20000);
        setTimeout(() => { if (!piper.ready.size) warm(v); }, 21000);
        return false;
      })
      .finally(() => piper.loading.delete(v.model));
    piper.loading.set(v.model, p);
    return p;
  }
  // How the reading went on this device, as numbers for the room's journal
  // (which provider, how long until the first sound, how long a part took to
  // make against how long it plays): never what was read.
  let reports = 0;
  function report(numbers) {
    if (++reports > 12) return;
    MAG.api('POST', '/api/tts/stat', numbers).catch(() => {});
  }
  // A crashed or stuck worker is replaced and the part is synthesised again.
  function resetWorker() {
    clearTimeout(piper.gpuTimer);
    try { piper.worker?.terminate(); } catch (_) {}
    piper.worker = null; piper.ready.clear(); piper.loading.clear();
    for (const p of piper.pending.values()) { clearTimeout(p.timer); p.reject(new Error('worker reset')); }
    piper.pending.clear();
  }
  // ---- how fast this device makes speech, and in how many steps --------------
  // In how many steps a part is made. The model refines its sound in steps:
  // five is its usual quality, four and three are heard a little, two more;
  // a device that makes eight in a quarter of the time they play (a GPU)
  // gets eight, the cleanest. A
  // part gets as many as there is time for: it must be made by the moment the
  // reading gets to it, or the reading would stop there. So the first words of
  // a reading are made in three (the sound starts at once), and what follows
  // in five as soon as what is ready covers the time it takes to make; a
  // device that cannot keep up even so (WASM on a phone) reads on in two
  // rather than stop between sentences. How
  // fast the device is, is measured on every part (work per second of
  // speech and step) and kept.
  //
  // A part takes a fixed time (four networks are started; on a GPU every
  // run costs its dispatch, about a second for a part on Chris's phone) and
  // a time that grows with its length and its steps:
  //   seconds to make = a + b * steps * seconds of speech.
  // a and b are a line through what this device took for its parts (older
  // ones weigh less), one for the GPU and one for the CPU, kept on the
  // device; what a phone usually takes holds the line until there is more.
  const PRIOR = { webgpu: [1.0, 0.06], wasm: [0.3, 0.3] };
  let fits = {};
  try { fits = JSON.parse(localStorage.getItem('mag-tts-fit') || '{}') || {}; } catch { fits = {}; }
  function line(provider) {
    const p = provider || voiceState.provider || (gpuOk() ? 'webgpu' : 'wasm');
    const [pa, pb] = PRIOR[p] || PRIOR.wasm, f = fits[p] || {};
    // The prior is two points (at 3 and 24) of half a weight each, so a line stands even when all parts were alike.
    let n = 1, x = 13.5, y = pa + 13.5 * pb, xx = 0.5 * (9 + 576), xy = 0.5 * (3 * (pa + 3 * pb) + 24 * (pa + 24 * pb));
    if (f.n > 0) { n += f.n; x += f.x; y += f.y; xx += f.xx; xy += f.xy; }
    const d = n * xx - x * x;
    const b = Math.max(0.005, d > 1e-6 ? (n * xy - x * y) / d : pb);
    const a = Math.min(3, Math.max(0, (y - b * x) / n));
    return { a, b };
  }
  const cost = (steps, sec) => { const { a, b } = line(); return a + b * steps * sec; };
  const partMax = () => (line().a > 0.5 ? 200 : 120);   // where every part costs much, parts are longer
  const topSteps = () => (cost(8, 8) / 8 < 0.25 ? 8 : 5);
  function stepsFor(pb, text) {
    const length = Math.max(0.4, text.length / 14);                     // seconds of speech, about
    const now = performance.now() / 1000;
    let reached = Math.max(now, pb.until || now), made = now;           // when the reading gets here, when the parts before it are made
    for (const part of pb.waiting) { reached += part.length; made += part.work; }
    let steps = topSteps();
    const least = pb.until || pb.waiting.length ? 2 : 3;
    while (steps > least && made + cost(steps, length) + 0.05 > reached) steps--;
    const part = { length, work: cost(steps, length) + 0.05 };
    pb.waiting.push(part);
    return [steps, part];
  }
  function measured(res) {
    if (!(res.synthMs > 0) || !(res.audio.length > res.rate / 4) || !(res.steps > 0)) return;
    const p = res.provider === 'webgpu' ? 'webgpu' : 'wasm', f = fits[p] || (fits[p] = { n: 0, x: 0, y: 0, xx: 0, xy: 0 });
    const x = res.steps * res.audio.length / res.rate, y = res.synthMs / 1000;
    for (const k of ['n', 'x', 'y', 'xx', 'xy']) f[k] *= 0.92;
    f.n += 1; f.x += x; f.y += y; f.xx += x * x; f.xy += x * y;
    try { localStorage.setItem('mag-tts-fit', JSON.stringify(fits)); } catch {}
  }
  // The first words of the newest answer are made before anybody asks for
  // them (see prepare): a tap on "Vorlesen" then speaks at once.
  const prepared = new Map();           // what is asked -> its sound, in the making or made
  const asked = (model, rate, lang, text) => model + '|' + rate + '|' + lang + '|' + text;
  async function synthPart(pb, text, rate) {
    const lang = (pb.lang || 'de').slice(0, 2);
    const early = prepared.get(asked(pb.voice.model, rate, lang, text));
    if (early) {
      try { const res = await early; if (res?.audio?.length) { pb.until = Math.max(performance.now() / 1000, pb.until || 0) + res.audio.length / res.rate; return { ...res, audio: res.audio.slice() }; } }
      catch { /* made anew below */ }
    }
    const [steps, part] = stepsFor(pb, text);
    const msg = {type: 'speak', text, model: pb.voice.model, rate, lang, steps};
    const done = (res) => {
      pb.waiting = pb.waiting.filter((p) => p !== part);
      if (res) { measured(res); pb.until = Math.max(performance.now() / 1000, pb.until || 0) + res.audio.length / res.rate; }
      return res;
    };
    try { return done(await ask(msg, SPEAK_TIMEOUT_MS)); }
    catch (e) {
      done(null);
      if (current !== pb) throw e;
      // Nothing speakable (only symbols/emoji): skip this part, keep reading.
      if (/Phonemisierung/.test(e.message)) return {audio: new Float32Array(0), rate: 16000};
      resetWorker();
      return ask(msg, SPEAK_TIMEOUT_MS);
    }
  }
  function request(pb, text) {
    const rate = rateFor(pb.speaker);
    const p = synthPart(pb, text, rate);
    p.catch(() => {}); // awaited later; avoid an unhandled rejection
    return {text, rate, p};
  }
  // The parts of a reading are laid end to end on the audio clock: a part
  // that is ready while the one before it still plays starts on the very
  // sample where that one ends, without a gap of the page's own timers.
  function schedule(pb, res) {
    const ctx = piper.ctx;
    const buf = ctx.createBuffer(1, Math.max(1, res.audio.length), res.rate);
    if (res.audio.length) buf.copyToChannel(res.audio, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination);
    const at = Math.max(ctx.currentTime + 0.03, pb.end || 0);
    piper.sources.add(src);
    src.onended = () => piper.sources.delete(src);
    try { src.start(at); } catch (_) { piper.sources.delete(src); return ctx.currentTime; }
    pb.end = at + buf.duration;
    return at;
  }
  // Resolves true once the audio clock reaches t, false if the reading ended
  // first. The clock stands while the context is suspended (iOS audio
  // interruptions): it is woken, and a reading that cannot go on is stopped.
  function reach(pb, t) {
    return new Promise((done) => {
      const ctx = piper.ctx;
      let since = 0;
      const tick = () => {
        if (current !== pb) return done(false);
        if (ctx.state !== 'running') {
          since = since || Date.now();
          resume(ctx);
          if (Date.now() - since > 8000) { stop(); return done(false); }
        } else {
          since = 0;
          if (ctx.currentTime >= t) return done(true);
        }
        setTimeout(tick, Math.max(15, Math.min(250, 1000 * (t - ctx.currentTime) - 10)));
      };
      tick();
    });
  }
  // Old WebKit returns undefined instead of a promise.
  function resume(ctx) { try { Promise.resolve(ctx.resume?.()).catch(() => {}); } catch (_) {} }
  // Parts are made ahead of what plays: up to three are ready or being made
  // at any time, in order. So a part that takes longer to make than the one
  // before it takes to play costs no pause, and the reading does not stop
  // between sentences.
  async function piperPlay(pb) {
    const ahead = [];
    let more = true;
    (async () => {
      while (current === pb && more) {
        if (ahead.length >= 3) { await new Promise((go) => { pb.room = go; }); continue; }
        const text = await nextText(pb);
        if (text == null || current !== pb) break;
        ahead.push(request(pb, text));
        pb.have?.();
      }
      more = false;
      pb.have?.();
    })();
    for (;;) {
      while (!ahead.length && more && current === pb) await new Promise((go) => { pb.have = go; });
      if (current !== pb) return;
      if (!ahead.length) {
        if (pb.end && !(await reach(pb, pb.end))) return;
        return finish(pb);
      }
      let cur = ahead.shift();
      pb.room?.();
      // Tempo changed while this part was prepared: synthesise it again.
      if (cur.rate !== rateFor(pb.speaker)) cur = request(pb, cur.text);
      const res = await cur.p;
      if (current !== pb) return;
      if (!pb.spoken) report({ first_ms: Math.round(performance.now() - pb.t0), synth_ms: res.synthMs | 0, audio_ms: Math.round(1000 * res.audio.length / res.rate), provider: res.provider, threads: res.threads | 0, steps: res.steps | 0 });
      pb.spoken = true;
      // Laid behind what plays; the next part is awaited while this one begins and plays.
      if (!(await reach(pb, schedule(pb, res)))) return;
    }
  }

  // ---- Playback text (grows while a message streams) ----
  function feed(pb, text, live) {
    const norm = normalize(text);
    pb.live = !!live;
    // A message whose parts were made ahead is cut as it was then, so the tap finds them; the rest after it as usual.
    if (pb.de && !pb.queued && !pb.live) {
      const cut = cuts.get(pb.key);
      if (cut && cut.text && norm.startsWith(cut.text)) { pb.parts.push(...cut.parts); pb.queued = cut.text.length; }
    }
    if (norm.length > pb.queued) {
      const rest = norm.slice(pb.queued);
      const take = pb.live ? completeLength(rest) : rest.length;
      if (take > 0) {
        const piece = rest.slice(0, take);
        pb.parts.push(...(pb.de ? piperParts(piece, partMax(), !pb.queued) : split(piece)));
        pb.queued += take;
      }
    }
    const wake = pb.wake; pb.wake = null; wake?.();
  }
  // Next part to speak; waits while a live message is still growing.
  async function nextText(pb) {
    for (;;) {
      if (current !== pb) return null;
      if (pb.at < pb.parts.length) return pb.parts[pb.at++];
      if (!pb.live) return null;
      const grew = await new Promise(resolve => {
        const t = setTimeout(() => { if (pb.wake === wake) pb.wake = null; resolve(false); }, LIVE_IDLE_MS);
        const wake = () => { clearTimeout(t); resolve(true); };
        pb.wake = wake;
      });
      if (!grew && pb.live) feed(pb, pb.text, false); // stalled: read the rest
    }
  }
  function finish(pb) { if (current === pb) { current = null; setButtons(); } }

  function stop() {
    const pb = current;
    current = null; // make pending callbacks and loops harmless
    if (pb) { const w = pb.wake; pb.wake = null; w?.(); }
    if (systemOk) synth.cancel();
    for (const src of piper.sources) { try { src.stop(); } catch (_) {} }
    piper.sources.clear();
    // Drop synthesis that is queued for the stopped playback.
    if (piper.worker && pb) { try { piper.worker.postMessage({type: 'cancel', before: piper.seq}); } catch (_) {} }
    setButtons();
  }

  // A ready part is spoken synchronously (iOS Safari needs the first
  // speak() inside the tap); only a live message waits for more text.
  function systemNext(pb) {
    if (current !== pb) return;
    if (pb.at < pb.parts.length) return utter(pb, pb.parts[pb.at++]);
    if (!pb.live) return finish(pb);
    nextText(pb).then(t => t == null ? finish(pb) : utter(pb, t));
  }
  function utter(pb, text) {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = pb.lang;
    utterance.rate = rateFor(pb.speaker);
    const match = bestVoice(pb.lang);
    if (match) utterance.voice = match;
    utterance.onend = () => { if (current === pb) systemNext(pb); };
    utterance.onerror = () => finish(pb);
    try { synth.speak(utterance); } catch (_) { finish(pb); }
  }

  function toggle(key, text, who) {
    if (!systemOk && !piperPossible) return false;
    const wasPlaying = !!current && current.key === key;
    stop();
    if (wasPlaying) return false;
    const normalized = normalize(text);
    if (!normalized) return false;
    const lang = textLocale(normalized);
    const prof = profile(who);
    const de = piperPossible && !!S.features?.tts && prof.voiceId !== 'system';   // the model reads German and English alike
    const pb = { key, who, speaker: prof.key, lang, de, parts: [], at: 0, queued: 0, text, live: false, wake: null, spoken: false, voice: prof.voice, t0: performance.now(), waiting: [], until: 0 };
    feed(pb, text, false);
    if (!pb.parts.length) return false;
    // What still waits to be made ahead (for this or another message) must not hold this reading up: it is
    // dropped, what is made stays, and this reading asks for its own parts in its own order.
    if (de && pb.voice && prepared.size && piper.worker) {
      try { piper.worker.postMessage({type: 'cancel', before: piper.seq}); } catch (_) {}
    }
    current = pb;
    setButtons();
    if (!de) {
      if (!systemOk) { current = null; setButtons(); return false; }
      // The first speech call happens inside the tap (iOS Safari wants that).
      systemNext(pb);
      return true;
    }
    // The audio context must be made or woken inside the tap (iOS).
    try { piper.ctx = piper.ctx && piper.ctx.state !== 'closed' ? piper.ctx : new AudioCtx(); resume(piper.ctx); }
    catch { return fallback(pb); }
    const start = () => piperPlay(pb).catch(() => fallback(pb));
    // The model is one for all voices (a voice is a small file the worker fetches by itself): loaded once, every voice speaks.
    if (piper.config && pb.voice && piper.ready.size) { start(); return true; }
    // Not loaded yet. A tap on "Vorlesen" is answered with sound, not with a
    // wait: the device's own voice reads this one, and the model goes on
    // loading behind it for the next. Only a device without a voice of its
    // own waits for the model.
    if (systemOk) {
      loadConfig().then((cfg) => { if (cfg) { const v = profile(who).voice || pb.voice; if (v) warm(v); } }).catch(() => {});
      return fallback(pb);
    }
    pb.loading = true;
    setButtons();
    loadConfig().then((cfg) => {
      if (current !== pb) return false;
      if (!cfg) return null;
      pb.voice = profile(who).voice || pb.voice; // the real config has the real model names
      return warm(pb.voice);
    }).then((ok) => {
      if (current !== pb) return;
      pb.loading = false;
      setButtons();
      if (ok) return start();
      fallback(pb);
    }).catch(() => fallback(pb));
    return true;
  }
  // Piper failed or is not there: the best system voice reads, but never after
  // Piper has spoken a part of this message (no second voice in mid-message).
  function fallback(pb) {
    if (current !== pb) return false;
    pb.loading = false;
    if (!systemOk || pb.spoken) {
      current = null;
      setButtons();
      MAG.toast('Vorlesen: Die Stimme konnte nicht geladen werden.', true);
      return false;
    }
    pb.de = false;
    pb.parts = split(pb.parts.slice(Math.max(0, pb.at - 1)).join(' '));
    pb.at = 0;
    setButtons();
    systemNext(pb);
    return true;
  }

  // ---- the page ----------------------------------------------------------------
  // Reads a message aloud, or stops if it is the one being read.
  MAG.speak = function (m, button) {
    if (button && !button.dataset.speak) button.dataset.speak = m.id;
    toggle(m.id, m.text, m.role === 'user' ? 'chris' : m.from);
  };
  // The newest answers are made ready before anybody asks for them, whole,
  // while they come in and nothing else is read: the tap that most often
  // comes finds its sound there, from the first word to the last, and plays
  // it without a pause on a phone that makes speech slower than it plays.
  // The first two parts get the device's best quality; the rest as many
  // steps as the device makes in the time they play (so a tap while they are
  // still being made does not catch up with them). Only with the model
  // loaded and the page in view; about four minutes of sound are kept.
  const cuts = new Map();               // message id -> {text: how far it is cut, parts}
  function cutFor(id, text, growing) {
    let cut = cuts.get(id);
    if (!cut || !text.startsWith(cut.text)) cut = { text: '', parts: [] };
    const rest = text.slice(cut.text.length);
    const take = growing ? completeLength(rest) : rest.length;
    if (take > 0) {
      const piece = rest.slice(0, take);
      cut = { text: cut.text + piece, parts: [...cut.parts, ...piperParts(piece, partMax(), !cut.text)] };
    }
    cuts.delete(id); cuts.set(id, cut);
    while (cuts.size > 10) cuts.delete(cuts.keys().next().value);
    return cut;
  }
  const keptFor = new Map();            // what is asked -> seconds of sound it holds
  const KEEP_S = 240;
  function keep(key, res) {
    keptFor.set(key, res?.audio?.length ? res.audio.length / res.rate : 0);
    let sum = 0;
    for (const v of keptFor.values()) sum += v;
    for (const [k, v] of keptFor) {
      if (sum <= KEEP_S) break;
      if (k === key) continue;
      keptFor.delete(k); prepared.delete(k); sum -= v;
    }
  }
  // As many steps as the device makes in the time the sound plays (two at least).
  function keepUpSteps() {
    let steps = topSteps();
    while (steps > 2 && cost(steps, 8) / 8 > 0.85) steps--;
    return steps;
  }
  let preparedFor = '';                 // the newest message being made ready
  function prepare(m, head = false) {
    if (!m || current || !piperPossible || !S.features?.tts || document.visibilityState !== 'visible' || !piper.config) return;
    const who = m.role === 'user' ? 'chris' : m.from, prof = profile(who);
    if (!prof.voice || !piper.ready.size) return;
    const text = normalize(m.text || '');
    if (!text) return;
    // A newer answer comes first: what is still waiting to be made for an older one is dropped (what is made stays).
    if (!head) {
      if (preparedFor && preparedFor !== m.id && piper.worker) { try { piper.worker.postMessage({type: 'cancel', before: piper.seq}); } catch (_) {} }
      preparedFor = m.id;
    }
    const lang = textLocale(text).slice(0, 2);
    const parts = cutFor(m.id, text, m.state !== 'done').parts;
    (head ? parts.slice(0, 1) : parts).forEach((part, i) => {
      const key = asked(prof.voice.model, prof.rate, lang, part);
      if (prepared.has(key)) return;
      const sound = ask({type: 'speak', text: part, model: prof.voice.model, rate: prof.rate, lang, steps: i < 2 ? topSteps() : keepUpSteps()}, SPEAK_TIMEOUT_MS);
      sound.then((res) => { measured(res); keep(key, res); }, () => { prepared.delete(key); keptFor.delete(key); });
      prepared.set(key, sound);
      keptFor.set(key, 0);
    });
  }
  let preparing = 0;
  function paint() {
    setButtons();
    // The newest answer, also while it still comes in (what of it is complete); at most every 400 ms.
    if (preparing) return;
    preparing = setTimeout(() => {
      preparing = 0;
      const answers = [];
      for (const x of S.messages.values()) if (x.role !== 'user' && x.text && !x.model3d && MAG.bot?.(x.from)) answers.push(x);
      // The newest whole; of the five before it the first words (a tap there starts at once too), once they are complete.
      const newest = answers.pop();
      if (!newest) return;
      prepare(newest);
      for (const m of answers.slice(-5).reverse()) if (m.state === 'done') prepare(m, true);
    }, 400);
  }
  MAG.speaking = { paint, stop, busy: () => !!current };
  // Voices and tempo for the settings: [{ who, name, voice, rate }], the choices, and a setter.
  MAG.voices = {
    possible: () => piperPossible && !!S.features?.tts,
    rates: RATES,
    async options() {
      await loadConfig();
      return [...voiceList().map((v) => ({ id: v.id, label: v.label })), { id: 'system', label: 'Systemstimme' }];
    },
    // Whether the model is there, in words, for the settings (kept up to date while they are open).
    state: () => stateText(),
    of: (who) => { const p = profile(who); return { voice: p.voiceId, rate: p.rate }; },
    set(who, entry) {
      savePref(speakerKey(who), entry);
      const v = entry.voice && voiceList().find((x) => x.id === entry.voice);
      if (v) loadConfig().then((cfg) => cfg && warm(v));
    },
  };
  // Shortly after the page is there, the model and the voices of the bots are
  // made ready in the background: fetched into Cache Storage once (about
  // 200 MB, then never again) and loaded, so the first tap speaks at once.
  // Over a mobile connection too (it is fetched once, and Chris wants the
  // first tap to speak); only with data saving on the first tap loads it.
  function warmUp() {
    // What is downloaded once must stay: the browser is asked not to clear it when space runs short.
    try { navigator.storage?.persist?.().catch(() => {}); } catch {}
    const link = navigator.connection;
    if (link && link.saveData) return;
    loadConfig().then((cfg) => {
      if (!cfg) return;
      const models = [...new Set(S.bots.map((b) => profile(b.id).voice?.model).filter(Boolean))];
      if (!models.length) return;
      warm({ model: models[0] });
      for (const model of models.slice(1)) ask({ type: 'prefetch', model }, WARM_TIMEOUT_MS).catch(() => {});
    });
  }
  // For the film of a scene: what a figure says, in a voice of its own (the
  // k-th of the voices, so two figures do not sound alike), made here on the
  // device like every reading. Returns { audio: Float32Array, rate } or null.
  MAG.voices.make = async function (text, k) {
    if (!piperPossible || !S.features?.tts) return null;
    const cfg = await loadConfig();
    const list = voiceList().filter((v) => v.model);
    if (!cfg || !list.length) return null;
    const v = list[((k | 0) % list.length + list.length) % list.length], clean = normalize(text || '');
    if (!clean) return null;
    const lang = textLocale(clean).slice(0, 2), sounds = [];
    for (const part of piperParts(clean, 120, false)) {
      const res = await ask({ type: 'speak', text: part, model: v.model, rate: 1, lang, steps: topSteps() }, SPEAK_TIMEOUT_MS);
      if (res?.audio?.length) sounds.push(res);
    }
    if (!sounds.length) return null;
    const rate = sounds[0].rate, audio = new Float32Array(sounds.reduce((n, r) => n + r.audio.length, 0));
    let at = 0;
    for (const r of sounds) { audio.set(r.audio, at); at += r.audio.length; }
    return { audio, rate };
  };
  let warmed = false;
  MAG.on('reset', () => { if (!warmed && piperPossible && S.features?.tts) { warmed = true; setTimeout(warmUp, 2500); } });
  window.addEventListener('pagehide', stop);
})();

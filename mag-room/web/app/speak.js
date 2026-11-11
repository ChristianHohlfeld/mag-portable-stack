// Read aloud, on the device: every answer can be spoken with a local voice.
// German text is spoken by Piper voices that run in the browser (a Web Worker
// with onnxruntime-web and espeak-ng phonemes; ops/tts): each voice downloads
// once into Cache Storage and after that a playback touches no server.
// Playback starts with the first sentence while the next one is prepared.
// Nothing plays by itself, and no speech is ever made on the server.
//
// Every speaker has a voice and a tempo of their own (Lena, Noah, Chris), kept
// on this device. The tempo is Piper's own length scale, so the pitch stays.
// Where Piper is not installed or fails, the best system voice reads instead,
// but never in the middle of a message that Piper has begun.
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
  // download of about 400 MB the first time: it has no fixed limit, only one
  // for standing still (the worker reports its progress).
  const SPEAK_TIMEOUT_MS = 120000, WARM_TIMEOUT_MS = 180000;
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
  function piperParts(text, max = 120) {
    const out = [];
    let rest = String(text || '').trim();
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
      if (cut > max) {
        const word = rest.lastIndexOf(' ', max);
        cut = word >= Math.floor(max / 3) ? word + 1 : max;
      }
      const piece = rest.slice(0, cut).trim();
      if (!piece) break;
      out.push(piece);
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
    const label = busy ? 'Stimme wird geladen' : playing ? 'Vorlesen stoppen' : 'Vorlesen';
    button.setAttribute('aria-label', label);
    button.title = label;
    button.innerHTML = busy ? '<span class="spin"></span>' : playing ? MAG.icons.stop : MAG.icons.speak;
  }
  function setButtons() { document.querySelectorAll('.act[data-speak]').forEach(setButton); }

  // ---- Piper (worker) ---------------------------------------------------------
  const piper = { config: null, configP: null, failed: new Map(), worker: null, ready: new Set(), loading: new Map(), ctx: null, seq: 0, pending: new Map(), source: null };
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
      piper.worker = new Worker('/tts/' + piper.config.dir + '/tts-worker.js' + (piper.config.models ? '?m=' + encodeURIComponent(piper.config.models) : ''));
      piper.worker.onmessage = (ev) => {
        const m = ev.data || {};
        if (m.type === 'progress') {     // the model is coming in: that is not standing still
          const loading = piper.pending.get(m.id);
          if (loading?.again) { clearTimeout(loading.timer); loading.timer = setTimeout(loading.again, WARM_TIMEOUT_MS); }
          return;
        }
        const p = piper.pending.get(m.id);
        if (!p) return;
        piper.pending.delete(m.id);
        clearTimeout(p.timer);
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
        // A slow background download is not a stuck worker.
        if (msg.type === 'prefetch') piper.pending.delete(id); else resetWorker();
        reject(new Error('Zeitüberschreitung der Stimme'));
      };
      const timer = setTimeout(late, timeout);
      piper.pending.set(id, {resolve, reject, timer, again: msg.type === 'warm' ? late : null});
      try { worker().postMessage({...msg, id}); } catch (e) { piper.pending.delete(id); clearTimeout(timer); reject(e); }
    });
  }
  // Resolves true once the model is usable, false if Piper failed for it. A
  // failure is retried after a short pause instead of disabling the voice
  // until reload (mobile networks drop).
  function warm(v) {
    if (piper.ready.has(v.model)) return Promise.resolve(true);
    if ((piper.failed.get(v.model) || 0) > Date.now()) return Promise.resolve(false);
    if (piper.loading.has(v.model)) return piper.loading.get(v.model);
    const p = ask({type: 'warm', model: v.model}, WARM_TIMEOUT_MS)
      .then(() => { piper.ready.add(v.model); return true; })
      .catch(() => { piper.failed.set(v.model, Date.now() + 20000); return false; })
      .finally(() => piper.loading.delete(v.model));
    piper.loading.set(v.model, p);
    return p;
  }
  // A crashed or stuck worker is replaced and the part is synthesised again.
  function resetWorker() {
    try { piper.worker?.terminate(); } catch (_) {}
    piper.worker = null; piper.ready.clear(); piper.loading.clear();
    for (const p of piper.pending.values()) { clearTimeout(p.timer); p.reject(new Error('worker reset')); }
    piper.pending.clear();
  }
  async function synthPart(pb, text, rate) {
    const msg = {type: 'speak', text, model: pb.voice.model, rate, lang: (pb.lang || 'de').slice(0, 2)};
    try { return await ask(msg, SPEAK_TIMEOUT_MS); }
    catch (e) {
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
  // Plays one buffer. onended can get lost (iOS audio interruptions, a
  // suspended context), so a watchdog follows the context clock and ends
  // playback cleanly if audio cannot resume.
  function playBuffer(pb, res) {
    return new Promise(done => {
      const ctx = piper.ctx;
      const buf = ctx.createBuffer(1, Math.max(1, res.audio.length), res.rate);
      if (res.audio.length) buf.copyToChannel(res.audio, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      let finished = false, startAt = ctx.currentTime, suspendedSince = 0;
      const end = () => {
        if (finished) return;
        finished = true; clearInterval(watch);
        if (piper.source === src) piper.source = null;
        done();
      };
      const watch = setInterval(() => {
        if (current !== pb) return end();
        if (ctx.state !== 'running') {
          suspendedSince = suspendedSince || Date.now();
          resume(ctx);
          if (Date.now() - suspendedSince > 8000) { stop(); end(); }
          return;
        }
        suspendedSince = 0;
        if (ctx.currentTime > startAt + buf.duration + 0.5) end();
      }, 250);
      src.onended = end;
      piper.source = src;
      try { src.start(); startAt = ctx.currentTime; } catch (_) { end(); }
    });
  }
  // Old WebKit returns undefined instead of a promise.
  function resume(ctx) { try { Promise.resolve(ctx.resume?.()).catch(() => {}); } catch (_) {} }
  async function piperPlay(pb) {
    let text = await nextText(pb);
    if (text == null) return finish(pb);
    let cur = request(pb, text);
    for (;;) {
      // Tempo changed while this part was prepared: synthesise it again.
      if (cur.rate !== rateFor(pb.speaker)) cur = request(pb, cur.text);
      const res = await cur.p;
      if (current !== pb) return;
      pb.spoken = true;
      // The next part is requested as soon as its text exists, so its
      // synthesis overlaps the playback of this one.
      const following = nextText(pb).then(t => (t == null || current !== pb) ? null : request(pb, t));
      await playBuffer(pb, res);
      if (current !== pb) return;
      cur = await following;
      if (current !== pb) return;
      if (!cur) return finish(pb);
    }
  }

  // ---- Playback text (grows while a message streams) ----
  function feed(pb, text, live) {
    const norm = normalize(text);
    pb.live = !!live;
    if (norm.length > pb.queued) {
      const rest = norm.slice(pb.queued);
      const take = pb.live ? completeLength(rest) : rest.length;
      if (take > 0) {
        const piece = rest.slice(0, take);
        pb.parts.push(...(pb.de ? piperParts(piece) : split(piece)));
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
    try { piper.source?.stop(); } catch (_) {}
    piper.source = null;
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
    const pb = { key, who, speaker: prof.key, lang, de, parts: [], at: 0, queued: 0, text, live: false, wake: null, spoken: false, voice: prof.voice };
    feed(pb, text, false);
    if (!pb.parts.length) return false;
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
    if (piper.config && pb.voice && piper.ready.has(pb.voice.model)) { start(); return true; }
    // Not loaded yet: the button shows that, instead of a robot voice standing in.
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
  MAG.speaking = { paint: setButtons, stop };
  // Voices and tempo for the settings: [{ who, name, voice, rate }], the choices, and a setter.
  MAG.voices = {
    possible: () => piperPossible && !!S.features?.tts,
    rates: RATES,
    async options() {
      await loadConfig();
      return [...voiceList().map((v) => ({ id: v.id, label: v.label })), { id: 'system', label: 'Systemstimme' }];
    },
    of: (who) => { const p = profile(who); return { voice: p.voiceId, rate: p.rate }; },
    set(who, entry) {
      savePref(speakerKey(who), entry);
      const v = entry.voice && voiceList().find((x) => x.id === entry.voice);
      if (v) loadConfig().then((cfg) => cfg && warm(v));
    },
  };
  // Shortly after the page is there, the model and the voices of the bots are
  // made ready in the background: fetched into Cache Storage once (about
  // 400 MB, then never again) and loaded, so the first tap speaks at once.
  // Not over a mobile connection or with data saving on: there the first
  // tap loads it.
  function warmUp() {
    const link = navigator.connection;
    if (link && (link.saveData || link.type === 'cellular')) return;
    loadConfig().then((cfg) => {
      if (!cfg) return;
      const models = [...new Set(S.bots.map((b) => profile(b.id).voice?.model).filter(Boolean))];
      if (!models.length) return;
      warm({ model: models[0] });
      for (const model of models.slice(1)) ask({ type: 'prefetch', model }, WARM_TIMEOUT_MS).catch(() => {});
    });
  }
  let warmed = false;
  MAG.on('reset', () => { if (!warmed && piperPossible && S.features?.tts) { warmed = true; setTimeout(warmUp, 2500); } });
  window.addEventListener('pagehide', stop);
})();

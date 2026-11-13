// Read aloud, on the device: every answer can be spoken with a local voice.
// Piper voices run in the browser (a Web Worker with onnxruntime-web and
// espeak-ng, app/tts-worker.js): runtime and voices come straight from where
// they are published (jsDelivr, Hugging Face), pinned, once into Cache
// Storage, and after that a reading touches no network. The room hands out
// nothing for it and no speech is ever made on the server. Playback starts
// with the first words while the next parts are made, laid end to end on the
// audio clock. Nothing plays by itself.
//
// Every speaker has a voice and a tempo of their own (Lena, Noah, Chris), kept
// on this device; a voice has a German and an English side, and the language
// of the text picks which reads. Until a voice is there, the best system
// voice reads instead, but never in the middle of a message that the model
// has begun.
'use strict';
(function () {
  const { MAG } = window, S = MAG.state;
  const synth = window.speechSynthesis;
  const systemOk = !!synth && typeof window.SpeechSynthesisUtterance === 'function';
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const modelPossible = typeof Worker === 'function' && !!AudioCtx && typeof fetch === 'function' && !!window.crypto?.subtle && typeof caches === 'object';
  const GOOD_VOICE = /natural|premium|siri|enhanced|neural|online/i;
  const ROBOT_VOICE = /espeak|mbrola|festival|flite|pico|ekho/i;
  const LIVE_IDLE_MS = 30000;
  // A voice is a download of about 63 MB the first time (with the runtime
  // about 33 MB more): that has no limit (the worker asks again for what does
  // not come, by itself), and a part to make has one. Time in which the page
  // was not in view does not count: a phone holds a page in the background.
  const SPEAK_TIMEOUT_MS = 60000, WARM_TIMEOUT_MS = 120000;
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

  // One sentence per call of the model, capped so a long sentence still starts soon.
  // Abbreviations (z.B., Dr.) are not treated as sentence ends. The very first
  // part of a playback is short (`first`): a clause, so the sound starts after
  // a few words are made and the rest is made while they play.
  const ABBREV = /^(dr|nr|bzw|ca|ggf|usw|etc|prof|dipl|ing|str|tel|fax|abs|vgl|mr|mrs|ms|vs)$/i;
  const PART_MAX = 200;
  function modelParts(text, max = PART_MAX, first = false) {
    const out = [];
    let rest = String(text || '').trim();
    if (first && rest.length > 60) {
      const head = rest.slice(0, 90);
      let cut = -1;
      for (const m of head.matchAll(/[,;:–—]\s|[.!?]\s|\s(?=(?:und|oder|aber|denn|weil|dass|wenn|als|wie|and|but|because|when)\s)/g))
        if (m.index >= 20 && cut < 0) cut = m.index + (/\s/.test(m[0][0]) ? 0 : 1);       // the first place a speaker would breathe
      if (cut >= 20) { out.push(rest.slice(0, cut).trim()); rest = rest.slice(cut).trimStart(); }
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

  // MAG speaks German: the German voice reads unless a text is clearly English.
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
  // Each voice reads German with a Piper voice and English with one of the
  // same kind (all free to use: Thorsten and Kerstin CC0, Karlsson and Ramona
  // M-AILABS, Joe CC0, Alba CC BY 4.0).
  const VOICES = [
    { id: 'kerstin', label: 'Kerstin (Frau)', de: 'de_DE-kerstin-low', en: 'en_GB-alba-medium' },
    { id: 'ramona', label: 'Ramona (Frau)', de: 'de_DE-ramona-low', en: 'en_GB-alba-medium' },
    { id: 'thorsten', label: 'Thorsten (Mann)', de: 'de_DE-thorsten-medium', en: 'en_US-joe-medium' },
    { id: 'karlsson', label: 'Karlsson (Mann)', de: 'de_DE-karlsson-low', en: 'en_US-joe-medium' },
  ];
  const DEFAULT_VOICE = { user: 'karlsson', lena: 'kerstin', noah: 'thorsten' };
  const VOICE_SET = 'piper-1';           // what kept sound was made with
  const modelOf = (voice, lang) => (String(lang || 'de').startsWith('en') ? voice.en : voice.de);
  // A speaker is a bot (by its id, which a rename does not change) or Chris.
  const speakerKey = (who) => (who === 'chris' || who === 'user' ? 'user' : 'bot:' + who);
  function prefs() { try { return JSON.parse(localStorage.getItem('mag-tts-speakers') || '{}') || {}; } catch { return {}; } }
  function savePref(key, entry) {
    const all = prefs();
    all[key] = { ...(all[key] || {}), ...entry };
    try { localStorage.setItem('mag-tts-speakers', JSON.stringify(all)); } catch {}
  }
  function profile(who) {
    const key = speakerKey(who), saved = prefs()[key] || {};
    // A voice that was chosen from an earlier set of voices is gone: the speaker's own default takes its place.
    const usual = DEFAULT_VOICE[key === 'user' ? 'user' : who] || 'thorsten';
    const wanted = saved.voice === 'system' || VOICES.some((v) => v.id === saved.voice) ? saved.voice : usual;
    const voice = wanted === 'system' ? null : (VOICES.find((v) => v.id === wanted) || VOICES.find((v) => v.id === usual) || VOICES[0]);
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

  // ---- the model (worker) -----------------------------------------------------
  const tts = { failed: new Map(), worker: null, ready: new Set(), loading: new Map(), ctx: null, seq: 0, pending: new Map(), sources: new Set() };
  function worker() {
    if (!tts.worker) {
      tts.worker = new Worker('/app/tts-worker.js?v=' + encodeURIComponent(MAG.assets));
      tts.worker.onmessage = (ev) => {
        const m = ev.data || {};
        if (m.type === 'progress') {     // a voice is coming in: that is not standing still
          for (const loading of tts.pending.values()) {
            if (!loading.again) continue;
            clearTimeout(loading.timer);
            loading.timer = setTimeout(loading.again, WARM_TIMEOUT_MS);
          }
          if (m.progress >= 0) shown('loading', m.progress / 100);
          return;
        }
        const p = tts.pending.get(m.id);
        if (!p) return;
        tts.pending.delete(m.id);
        clearTimeout(p.timer);
        if (m.type === 'error') p.reject(new Error(m.error)); else p.resolve(m);
      };
      tts.worker.onerror = () => resetWorker();
    }
    return tts.worker;
  }
  // Every request has a deadline: a stuck WASM call or a crashed worker
  // must not leave the button spinning.
  function ask(msg, timeout) {
    const id = ++tts.seq;
    return new Promise((resolve, reject) => {
      const late = () => {
        if (!tts.pending.has(id)) return;
        // A page out of view is held by the phone: that is no reason to give up on the worker.
        if (document.visibilityState !== 'visible' || performance.now() - seen < 20000) {
          const p = tts.pending.get(id);
          p.timer = setTimeout(late, 20000);
          return;
        }
        // A slow background download is not a stuck worker.
        if (msg.type === 'prefetch') tts.pending.delete(id); else resetWorker();
        reject(new Error('Zeitüberschreitung der Stimme'));
      };
      const timer = setTimeout(late, timeout);
      tts.pending.set(id, {resolve, reject, timer, again: msg.type === 'warm' ? late : null});
      try { worker().postMessage({...msg, id}); } catch (e) { tts.pending.delete(id); clearTimeout(timer); reject(e); }
    });
  }
  let seen = performance.now();          // when the page came into view last
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') seen = performance.now(); });
  // Whether the voice is there, shown without taking room: while it loads, a
  // thin line under every "Vorlesen" button fills up (its title says how
  // far); once it is there, nothing is shown in the chat. The settings say it
  // in words.
  const voiceState = { state: '', part: 0 };
  function stateText() {
    const { state, part } = voiceState;
    if (state === 'ready') return 'Stimme bereit';
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
  // Resolves true once the voice (one Piper model) is usable, false if it
  // failed. A failure is tried again after a short pause instead of
  // disabling the voice until a reload (mobile networks drop).
  function warm(model) {
    if (tts.ready.has(model)) return Promise.resolve(true);
    if (!tts.ready.size) shown('loading');
    if ((tts.failed.get(model) || 0) > Date.now()) return Promise.resolve(false);
    if (tts.loading.has(model)) return tts.loading.get(model);
    const p = ask({type: 'warm', model}, WARM_TIMEOUT_MS)
      .then(() => { tts.ready.add(model); shown('ready'); setTimeout(paint, 0); return true; })
      .catch(() => {
        tts.failed.set(model, Date.now() + 20000);
        setTimeout(() => { if (!tts.ready.has(model)) warm(model); }, 21000);
        return false;
      })
      .finally(() => tts.loading.delete(model));
    tts.loading.set(model, p);
    return p;
  }
  // A crashed or stuck worker is replaced and the part is made again.
  function resetWorker() {
    try { tts.worker?.terminate(); } catch (_) {}
    tts.worker = null; tts.ready.clear(); tts.loading.clear();
    for (const p of tts.pending.values()) { clearTimeout(p.timer); p.reject(new Error('worker reset')); }
    tts.pending.clear();
  }
  // The first words of the newest answer are made before anybody asks for
  // them (see prepare): a tap on "Vorlesen" then speaks at once.
  const prepared = new Map();           // what is asked -> its sound, in the making or made
  const asked = (model, rate, text) => model + '|' + rate + '|' + text;
  async function synthPart(pb, text, rate) {
    const early = prepared.get(asked(pb.model, rate, text));
    if (early) {
      try { const res = await early; if (res?.audio?.length) return { ...res, audio: res.audio.slice() }; }
      catch { /* made anew below */ }
    }
    const msg = {type: 'speak', text, model: pb.model, rate};
    try { return await ask(msg, SPEAK_TIMEOUT_MS); }
    catch (e) {
      if (current !== pb) throw e;
      // Nothing speakable (only symbols): skip this part, keep reading.
      if (/Phonemisierung/.test(e.message)) return {audio: new Float32Array(0), rate: 22050};
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
    const ctx = tts.ctx;
    const buf = ctx.createBuffer(1, Math.max(1, res.audio.length), res.rate);
    if (res.audio.length) buf.copyToChannel(res.audio, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination);
    const at = Math.max(ctx.currentTime + 0.03, pb.end || 0);
    tts.sources.add(src);
    src.onended = () => tts.sources.delete(src);
    try { src.start(at); } catch (_) { tts.sources.delete(src); return ctx.currentTime; }
    pb.end = at + buf.duration;
    return at;
  }
  // Resolves true once the audio clock reaches t, false if the reading ended
  // first. The clock stands while the context is suspended (iOS audio
  // interruptions): it is woken, and a reading that cannot go on is stopped.
  function reach(pb, t) {
    return new Promise((done) => {
      const ctx = tts.ctx;
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
  // at any time, in order, so the reading does not stop between sentences.
  async function modelPlay(pb) {
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
      // Tempo changed while this part was prepared: make it again.
      if (cur.rate !== rateFor(pb.speaker)) cur = request(pb, cur.text);
      const res = await cur.p;
      if (current !== pb) return;
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
    if (pb.model && !pb.queued && !pb.live) {
      const cut = cuts.get(pb.key);
      if (cut && cut.text && norm.startsWith(cut.text)) { pb.parts.push(...cut.parts); pb.queued = cut.text.length; }
    }
    if (norm.length > pb.queued) {
      const rest = norm.slice(pb.queued);
      const take = pb.live ? completeLength(rest) : rest.length;
      if (take > 0) {
        const piece = rest.slice(0, take);
        pb.parts.push(...(pb.model ? modelParts(piece, PART_MAX, !pb.queued) : split(piece)));
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
    for (const src of tts.sources) { try { src.stop(); } catch (_) {} }
    tts.sources.clear();
    // Drop what is queued for the stopped playback.
    if (tts.worker && pb) { try { tts.worker.postMessage({type: 'cancel', before: tts.seq}); } catch (_) {} }
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
    if (!systemOk && !modelPossible) return false;
    const wasPlaying = !!current && current.key === key;
    stop();
    if (wasPlaying) return false;
    const normalized = normalize(text);
    if (!normalized) return false;
    const lang = textLocale(normalized);
    const prof = profile(who);
    const model = modelPossible && prof.voice ? modelOf(prof.voice, lang) : null;
    const pb = { key, who, speaker: prof.key, lang, model, parts: [], at: 0, queued: 0, text, live: false, wake: null, spoken: false };
    feed(pb, text, false);
    if (!pb.parts.length) return false;
    // What still waits to be made ahead (for this or another message) must not hold this reading up: it is
    // dropped, what is made stays, and this reading asks for its own parts in its own order.
    if (model && prepared.size && tts.worker) {
      try { tts.worker.postMessage({type: 'cancel', before: tts.seq}); } catch (_) {}
    }
    current = pb;
    setButtons();
    if (!model) {
      if (!systemOk) { current = null; setButtons(); return false; }
      // The first speech call happens inside the tap (iOS Safari wants that).
      systemNext(pb);
      return true;
    }
    // The audio context must be made or woken inside the tap (iOS).
    try { tts.ctx = tts.ctx && tts.ctx.state !== 'closed' ? tts.ctx : new AudioCtx(); resume(tts.ctx); }
    catch { return fallback(pb); }
    const start = () => modelPlay(pb).catch(() => fallback(pb));
    const tempo = rateFor(pb.speaker);
    // The voice is loaded, or every part of this one was made before and kept: it plays in its own voice.
    if (tts.ready.has(model) || pb.parts.every((t) => prepared.has(asked(model, tempo, t)))) { start(); return true; }
    // Not loaded yet. A tap on "Vorlesen" is answered with sound, not with a
    // wait: the device's own voice reads this one, and the voice goes on
    // loading behind it for the next. Only a device without a voice of its
    // own waits for the model.
    if (systemOk) { warm(model); return fallback(pb); }
    pb.loading = true;
    setButtons();
    warm(model).then((ok) => {
      if (current !== pb) return;
      pb.loading = false;
      setButtons();
      if (ok) return start();
      fallback(pb);
    }).catch(() => fallback(pb));
    return true;
  }
  // The model failed or is not there: the best system voice reads, but never after
  // the model has spoken a part of this message (no second voice in mid-message).
  function fallback(pb) {
    if (current !== pb) return false;
    pb.loading = false;
    if (!systemOk || pb.spoken) {
      current = null;
      setButtons();
      MAG.toast('Vorlesen: Die Stimme konnte nicht geladen werden.', true);
      return false;
    }
    pb.model = null;
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
    toggle(m.id, m.role === 'bot' && MAG.tidy ? MAG.tidy(m.text || '') : m.text, m.role === 'user' ? 'chris' : m.from);
  };
  // The newest answers are made ready before anybody asks for them, whole,
  // while they come in and nothing else is read: the tap that most often
  // comes finds its sound there and plays it at once. Only with the voice
  // loaded and the page in view; about four minutes of sound are kept.
  const cuts = new Map();               // message id -> {text: how far it is cut, parts}
  function cutFor(id, text, growing) {
    let cut = cuts.get(id);
    if (!cut || !text.startsWith(cut.text)) cut = { text: '', parts: [] };
    const rest = text.slice(cut.text.length);
    const take = growing ? completeLength(rest) : rest.length;
    if (take > 0) {
      const piece = rest.slice(0, take);
      cut = { text: cut.text + piece, parts: [...cut.parts, ...modelParts(piece, PART_MAX, !cut.text)] };
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
  let preparedFor = '';                 // the newest message being made ready
  function prepare(m, head = false) {
    if (!m || current || !modelPossible || document.visibilityState !== 'visible') return;
    const who = m.role === 'user' ? 'chris' : m.from, prof = profile(who);
    if (!prof.voice) return;
    const text = normalize((m.role === 'bot' && MAG.tidy ? MAG.tidy(m.text || '') : m.text) || '');
    if (!text) return;
    const model = modelOf(prof.voice, textLocale(text));
    if (!tts.ready.has(model)) return;
    // A newer answer comes first: what is still waiting to be made for an older one is dropped (what is made stays).
    if (!head) {
      if (preparedFor && preparedFor !== m.id && tts.worker) { try { tts.worker.postMessage({type: 'cancel', before: tts.seq}); } catch (_) {} }
      preparedFor = m.id;
    }
    const parts = cutFor(m.id, text, m.state !== 'done').parts;
    (head ? parts.slice(0, 1) : parts).forEach((part) => {
      const key = asked(model, prof.rate, part);
      if (prepared.has(key)) return;
      const sound = ask({type: 'speak', text: part, model, rate: prof.rate}, SPEAK_TIMEOUT_MS);
      sound.then((res) => keep(key, res), () => { prepared.delete(key); keptFor.delete(key); });
      prepared.set(key, sound);
      keptFor.set(key, 0);
    });
    if (!head && m.state === 'done') {
      const sounds = parts.map((part) => prepared.get(asked(model, prof.rate, part)));
      if (sounds.every(Boolean)) said.put(m.id, cuts.get(m.id), sounds, model, prof.rate);
    }
  }
  // ---- what was said is kept across a reload -------------------------------------
  // The newest answers, made whole ahead, are kept in Cache Storage as they
  // were made (16 bit, the last four answers): after a reload the tap on one
  // of them plays its own voice at once, while the voice is still being loaded.
  const said = (() => {
    const NAME = 'mag-tts-said', MOST = 4, done = new Set();
    const address = (id) => location.origin + '/tts-said/' + encodeURIComponent(id);   // a name in the cache, never fetched
    async function open() { try { return await caches.open(NAME); } catch { return null; } }
    async function put(id, cut, sounds, model, rate) {
      const mark = id + '|' + model + '|' + rate + '|' + (cut?.text.length || 0);
      if (!cut || done.has(mark)) return;
      done.add(mark);
      let made;
      try { made = await Promise.all(sounds); } catch { done.delete(mark); return; }
      if (!made.every((r) => r?.audio && r.rate === made[0].rate)) return;
      const meta = new TextEncoder().encode(JSON.stringify({ id, text: cut.text, parts: cut.parts, lens: made.map((r) => r.audio.length), rate: made[0].rate, model, voiceRate: rate, models: VOICE_SET }));
      const head = (4 + meta.length + 1) & ~1, n = made.reduce((a, r) => a + r.audio.length, 0);
      const bytes = new Uint8Array(head + 2 * n);
      new DataView(bytes.buffer).setUint32(0, meta.length, true);
      bytes.set(meta, 4);
      const pcm = new Int16Array(bytes.buffer, head, n);
      let at = 0;
      for (const r of made) for (let i = 0; i < r.audio.length; i++) pcm[at++] = Math.max(-32768, Math.min(32767, Math.round(r.audio[i] * 32767)));
      const cache = await open();
      if (!cache) return;
      try {
        await cache.delete(address(id));          // the newest last: the oldest go first
        await cache.put(address(id), new Response(bytes, { headers: { 'content-type': 'application/octet-stream' } }));
        const keys = await cache.keys();
        for (const k of keys.slice(0, Math.max(0, keys.length - MOST))) await cache.delete(k);
      } catch { /* a full device: not kept */ }
    }
    // After a reload: what is kept becomes made sound again, cut as it was cut.
    async function restore() {
      const cache = await open();
      if (!cache) return;
      for (const k of await cache.keys()) {
        try {
          const bytes = new Uint8Array(await (await cache.match(k)).arrayBuffer());
          const size = new DataView(bytes.buffer).getUint32(0, true);
          const meta = JSON.parse(new TextDecoder().decode(bytes.subarray(4, 4 + size)));
          if (meta.models !== VOICE_SET) { await cache.delete(k); continue; }
          const head = (4 + size + 1) & ~1, pcm = new Int16Array(bytes.buffer, head, (bytes.length - head) >> 1);
          let at = 0;
          meta.parts.forEach((part, i) => {
            const audio = new Float32Array(meta.lens[i]);
            for (let j = 0; j < audio.length; j++) audio[j] = pcm[at++] / 32767;
            const key = asked(meta.model, meta.voiceRate, part);
            if (prepared.has(key)) return;
            prepared.set(key, Promise.resolve({ audio, rate: meta.rate, synthMs: 0 }));
            keptFor.set(key, audio.length / meta.rate);
          });
          if (!cuts.has(meta.id)) cuts.set(meta.id, { text: meta.text, parts: meta.parts });
          done.add(meta.id + '|' + meta.model + '|' + meta.voiceRate + '|' + meta.text.length);
        } catch { try { await cache.delete(k); } catch {} }
      }
    }
    return { put, restore };
  })();
  let preparing = 0;
  function paint() {
    setButtons();
    later();
  }
  // The newest answer, also while it still comes in (what of it is complete); at most every 400 ms.
  function later() {
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
      // And the first words of every older answer whose "Vorlesen" is on the screen or a screen away:
      // scrolled back, the answer that can be tapped starts at once as well (nearest the end first).
      const bots = new Set(answers.map((m) => m.id)), near = innerHeight, seen = [];
      for (const b of document.querySelectorAll('button[data-speak]')) {
        if (!bots.has(b.dataset.speak)) continue;
        const r = b.getBoundingClientRect();
        if (r.height && r.bottom > -near && r.top < 2 * near) seen.push(S.messages.get(b.dataset.speak));
      }
      for (const m of seen.reverse().slice(0, 8)) if (m?.state === 'done') prepare(m, true);
    }, 400);
  }
  // Scrolling (in the list of messages or anywhere) brings other answers into view.
  document.addEventListener('scroll', later, { capture: true, passive: true });
  MAG.speaking = { paint, stop, busy: () => !!current };
  // Voices and tempo for the settings: [{ who, name, voice, rate }], the choices, and a setter.
  MAG.voices = {
    possible: () => modelPossible,
    rates: RATES,
    async options() {
      return [...VOICES.map((v) => ({ id: v.id, label: v.label })), { id: 'system', label: 'Systemstimme' }];
    },
    // Whether the voice is there, in words, for the settings (kept up to date while they are open).
    state: () => stateText(),
    of: (who) => { const p = profile(who); return { voice: p.voiceId, rate: p.rate }; },
    set(who, entry) {
      savePref(speakerKey(who), entry);
      const v = entry.voice && VOICES.find((x) => x.id === entry.voice);
      if (v && modelPossible) warm(v.de);
    },
  };
  // Shortly after the page is there, the German voices of the bots are made
  // ready in the background (fetched once into Cache Storage, then never
  // again) and their English voices fetched, so the first tap speaks at
  // once. Only with data saving on, the first tap loads them.
  function warmUp() {
    // What is downloaded once must stay: the browser is asked not to clear it when space runs short.
    try { navigator.storage?.persist?.().catch(() => {}); } catch {}
    said.restore().then(() => setButtons()).catch(() => {});
    if (navigator.connection?.saveData) return;
    const voices = [...new Set(S.bots.map((b) => profile(b.id).voice).filter(Boolean))];
    const de = [...new Set(voices.map((v) => v.de))], en = [...new Set(voices.map((v) => v.en))];
    de.reduce((p, model) => p.then(() => warm(model)), Promise.resolve())
      .then(() => { for (const model of en) ask({ type: 'prefetch', model }, WARM_TIMEOUT_MS).catch(() => {}); });
  }
  // For the film of a scene: what a figure says, in a voice of its own (the
  // k-th of the voices, so two figures do not sound alike), made here on the
  // device like every reading. With `seconds` (how long the mouth moves in the
  // scene) the tempo is fitted so voice and lips end together, within what
  // still sounds natural (0.8x to 1.3x). Returns { audio: Float32Array, rate } or null.
  MAG.voices.make = async function (text, k, seconds) {
    if (!modelPossible) return null;
    const v = VOICES[((k | 0) % VOICES.length + VOICES.length) % VOICES.length], clean = normalize(text || '');
    if (!clean) return null;
    const model = modelOf(v, textLocale(clean));
    const say = async (tempo) => {
      const sounds = [];
      for (const part of modelParts(clean, PART_MAX, false)) {
        const res = await ask({ type: 'speak', text: part, model, rate: tempo }, WARM_TIMEOUT_MS);
        if (res?.audio?.length) sounds.push(res);
      }
      if (!sounds.length) return null;
      const rate = sounds[0].rate, audio = new Float32Array(sounds.reduce((n, r) => n + r.audio.length, 0));
      let at = 0;
      for (const r of sounds) { audio.set(r.audio, at); at += r.audio.length; }
      return { audio, rate };
    };
    const first = await say(1);
    if (!first || !(seconds > 0.3)) return first;
    const natural = first.audio.length / first.rate, tempo = Math.min(1.3, Math.max(0.8, natural / seconds));
    if (Math.abs(tempo - 1) < 0.04) return first;
    return (await say(tempo)) || first;
  };
  let warmed = false;
  MAG.on('reset', () => { if (!warmed && modelPossible) { warmed = true; setTimeout(warmUp, 2500); } });
  window.addEventListener('pagehide', stop);
})();

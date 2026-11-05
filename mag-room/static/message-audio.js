// Manual, device-local read-aloud for every MAG chat message (bot and human).
// Deliberately no autoplay and no server-side speech. German text uses the
// self-hosted Piper voices (ops/vendor-tts-assets.sh) in a Web Worker; each
// model downloads once into Cache Storage. Playback starts at the first
// sentence while the next one is synthesized.
//
// Voice and tempo belong to the speaker, not to the device: Chris ("user"),
// Lena and Noah each have their own Piper voice and tempo, stored on the room
// server (/api/tts-voices, state.tts_voices) and cached in localStorage. The
// tempo control sits next to every speaker button. Tempo is Piper's own
// length_scale (no playbackRate pitch shift), so it applies from the next
// sentence on.
//
// A message that is still streaming keeps growing while it is read: new
// complete sentences are appended to the running playback, and it only ends
// once the final text has been read. Exactly one playback exists at a time;
// Stop silences the current buffer immediately and drops queued synthesis.
(() => {
  'use strict';
  const synth = window.speechSynthesis;
  const systemOk = !!synth && typeof window.SpeechSynthesisUtterance === 'function';
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const piperPossible = typeof Worker === 'function' && !!AudioCtx && typeof fetch === 'function';
  const supported = systemOk || piperPossible;
  const playIcon = '<svg class="i sm" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15 9a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12"/></svg>';
  const busyIcon = '<svg class="i sm" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.2-8.56"/></svg>';
  const stopIcon = '<svg class="i sm" aria-hidden="true"><use href="#ic-stop"></use></svg>';
  const GOOD_VOICE = /natural|premium|siri|enhanced|neural|online/i;
  const ROBOT_VOICE = /espeak|mbrola|festival|flite|pico|ekho/i;
  // A live message that stops growing for this long is read to its end.
  const LIVE_IDLE_MS = 30000;
  const SPEAK_TIMEOUT_MS = 45000, WARM_TIMEOUT_MS = 180000;
  let current = null;
  let nextEphemeral = 0;

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

  // MAG is German-first: 'auto' reads with Piper (de-DE) regardless of the
  // device language and only switches to en-US for clearly English text.
  const deWords = new Set('der die das und ist nicht ich du wir sie ein eine mit auf für von zu den dem des sich auch es bitte danke ja nein oder aber wie was kann noch nur schon hier jetzt'.split(' '));
  const enWords = new Set('the and is are was were you your i it this that with for of to in on not have has be can will what please thanks yes no but how do does'.split(' '));
  function textLocale(text) {
    if (/[äöüß]/i.test(text)) return 'de-DE';
    const words = String(text || '').toLowerCase().match(/[a-z']+/g) || [];
    let de = 0, en = 0;
    for (const w of words) { if (deWords.has(w)) de++; if (enWords.has(w)) en++; }
    return !de && en >= 2 && en * 4 >= words.length ? 'en-US' : 'de-DE';
  }
  function chosenLocale(text) {
    const setting = document.getElementById('language')?.value || 'auto';
    if (setting === 'de') return 'de-DE';
    if (setting === 'en') return 'en-US';
    return textLocale(text);
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

  // ---- Speaker profiles: voice + tempo per speaker ----
  const RATES = [0.75, 0.85, 1, 1.15, 1.3, 1.5, 1.75, 2];
  // Default voice per speaker. Lena and Noah are matched by bot id and by
  // display name, so a renamed or re-created bot keeps a sensible voice.
  const DEFAULT_VOICE = {user: 'thorsten', lena: 'kerstin', noah: 'karlsson'};
  const FALLBACK_VOICES = [
    {id: 'thorsten', label: 'Thorsten (männlich)', model: 'de_DE-thorsten-medium.onnx'},
    {id: 'kerstin', label: 'Kerstin (weiblich)', model: 'de_DE-kerstin-low.onnx'},
    {id: 'ramona', label: 'Ramona (weiblich)', model: 'de_DE-ramona-low.onnx'},
    {id: 'karlsson', label: 'Karlsson (männlich)', model: 'de_DE-karlsson-low.onnx'}];
  function speakerKey(opts) {
    if (opts?.user) return 'user';
    const id = String(opts?.bot || '').trim();
    if (id) return 'bot:' + id;
    const name = String(opts?.name || '').trim().toLowerCase().replace(/[^a-z0-9_.-]/g, '');
    return name ? 'bot:' + name : 'bot:unknown';
  }
  function persona(opts) {
    if (opts?.user) return 'user';
    const id = String(opts?.bot || '').toLowerCase(), name = String(opts?.name || '').trim().toLowerCase();
    if (name === 'lena' || (!name && id === 'qwen-numa0')) return 'lena';
    if (name === 'noah' || (!name && id === 'qwen-numa1')) return 'noah';
    if (id) {
      const a = roomState()?.agents?.find(x => x.id === opts.bot);
      const n = String(a?.name || '').toLowerCase();
      if (n === 'lena' || n === 'noah') return n;
      if (id === 'qwen-numa0') return 'lena';
      if (id === 'qwen-numa1') return 'noah';
    }
    return '';
  }
  // index.html keeps the room state in a top-level `state` binding.
  function roomState() { try { return typeof state !== 'undefined' ? state : null; } catch (_) { return null; } }
  function localPrefs() { try { return JSON.parse(localStorage.getItem('mag-tts-speakers') || '{}') || {}; } catch (_) { return {}; } }
  function storeLocal(key, entry) {
    const all = localPrefs(); all[key] = {...(all[key] || {}), ...entry};
    try { localStorage.setItem('mag-tts-speakers', JSON.stringify(all)); } catch (_) {}
  }
  function savedPrefs(key) {
    const server = roomState()?.tts_voices?.[key];
    return {...(localPrefs()[key] || {}), ...(server && typeof server === 'object' ? server : {})};
  }
  function voiceList() { return piper.config?.voices?.length ? piper.config.voices : FALLBACK_VOICES; }
  function profile(opts) {
    const key = speakerKey(opts), saved = savedPrefs(key);
    const voices = voiceList();
    const who = persona(opts);
    const wanted = saved.voice || piper.config?.defaults?.[who] || DEFAULT_VOICE[who] || 'thorsten';
    const voice = wanted === 'system' ? null : (voices.find(v => v.id === wanted) || voices.find(v => v.id === 'thorsten') || voices[0]);
    const rate = RATES.includes(+saved.rate) ? +saved.rate : 1;
    return {key, voiceId: voice ? voice.id : 'system', voice, rate};
  }
  function rateFor(key) { const r = +savedPrefs(key).rate; return RATES.includes(r) ? r : 1; }
  async function savePref(key, entry) {
    storeLocal(key, entry);
    const st = roomState();
    if (st) { st.tts_voices = st.tts_voices || {}; st.tts_voices[key] = {...(st.tts_voices[key] || {}), ...entry}; }
    try {
      const r = await fetch('/api/tts-voices', {method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify({key, ...entry})});
      if (!r.ok && r.status !== 403) throw new Error((await r.json().catch(() => ({}))).error || ('HTTP ' + r.status));
    } catch (e) { window.notice?.('Vorlese-Einstellung nur auf diesem Gerät gespeichert: ' + e.message); }
  }
  function setRate(key, v) {
    v = RATES.includes(+v) ? +v : 1;
    savePref(key, {rate: v});
    paintRates(key);
    return v;
  }
  function stepRate(key, dir) {
    const i = RATES.indexOf(rateFor(key));
    return setRate(key, RATES[Math.max(0, Math.min(RATES.length - 1, (i < 0 ? 2 : i) + dir))]);
  }
  function setSpeakerVoice(key, voiceId) {
    if (voiceId !== 'system' && !voiceList().some(v => v.id === voiceId)) throw new Error('Unbekannte Stimme');
    savePref(key, {voice: voiceId});
    const v = voiceList().find(x => x.id === voiceId);
    if (v) loadConfig().then(() => warm(v));
  }

  // ---- Buttons ----
  function setButton(button) {
    const playing = !!current && current.key === button.dataset.audioKey;
    const busy = playing && !!current.loading;
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-busy', String(busy));
    if (busy) { button.setAttribute('aria-label', 'Stimme wird geladen'); button.title = 'Stimme wird geladen'; button.innerHTML = busyIcon; return; }
    button.setAttribute('aria-label', playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen');
    button.title = playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen';
    button.innerHTML = playing ? stopIcon : playIcon;
  }
  function setButtons() { document.querySelectorAll('.message-speak').forEach(setButton); }
  function rateLabel(r) { return String(r).replace('.', ',') + '×'; }
  function paintRates(key) {
    document.querySelectorAll('.tts-rate').forEach(box => {
      if (key && box.dataset.speaker !== key) return;
      const r = rateFor(box.dataset.speaker), i = RATES.indexOf(r);
      box.querySelector('.tts-rate-value').textContent = rateLabel(r);
      box.querySelector('.tts-slower').disabled = i <= 0;
      box.querySelector('.tts-faster').disabled = i >= RATES.length - 1;
    });
  }
  function css() {
    if (document.getElementById('mag-tts-css')) return;
    const s = document.createElement('style'); s.id = 'mag-tts-css';
    s.textContent = '.tts-rate{display:inline-flex;align-items:center;gap:0;border-radius:6px;color:var(--muted);font-size:11px;line-height:1}' +
      '.tts-rate button{min-width:26px;min-height:32px;padding:0 4px!important;justify-content:center;font:600 13px/1 inherit;color:inherit;touch-action:manipulation}' +
      '.tts-rate button:disabled{opacity:.35;cursor:default}.tts-rate-value{min-width:34px;text-align:center;font-variant-numeric:tabular-nums}';
    document.head.append(s);
  }
  function rateControl(key, label) {
    css();
    const box = document.createElement('span');
    box.className = 'tts-rate'; box.dataset.speaker = key;
    box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'Vorlesetempo ' + label);
    const slower = document.createElement('button'), value = document.createElement('span'), faster = document.createElement('button');
    slower.type = faster.type = 'button';
    slower.className = 'tts-slower'; slower.textContent = '−'; slower.title = 'Langsamer vorlesen'; slower.setAttribute('aria-label', 'Langsamer vorlesen');
    faster.className = 'tts-faster'; faster.textContent = '+'; faster.title = 'Schneller vorlesen'; faster.setAttribute('aria-label', 'Schneller vorlesen');
    value.className = 'tts-rate-value'; value.setAttribute('aria-live', 'polite');
    slower.onclick = () => stepRate(box.dataset.speaker, -1);
    faster.onclick = () => stepRate(box.dataset.speaker, 1);
    box.append(slower, value, faster);
    return box;
  }

  // ---- Piper (worker) ----
  const piper = {config: null, configP: null, failed: new Map(), worker: null, ready: new Set(), loading: new Map(), ctx: null, seq: 0, pending: new Map(), source: null};
  function loadConfig() {
    if (!piper.configP) piper.configP = (async () => {
      try {
        const r = await fetch('/static/tts-config.json', {cache: 'no-store'});
        if (r.ok) piper.config = await r.json();
      } catch (_) { piper.config = null; }
      if (!piper.config) piper.configP = null; // retry on the next tap
      return piper.config;
    })();
    return piper.configP;
  }
  function worker() {
    if (!piper.worker) {
      piper.worker = new Worker('/static/' + piper.config.dir + '/tts-worker.js');
      piper.worker.onmessage = ev => {
        const m = ev.data || {};
        if (m.type === 'progress') { document.dispatchEvent(new CustomEvent('mag-tts-progress', {detail: m.progress})); return; }
        const p = piper.pending.get(m.id);
        if (!p) return;
        piper.pending.delete(m.id); clearTimeout(p.timer);
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
      const timer = setTimeout(() => { if (piper.pending.has(id)) { resetWorker(); reject(new Error('Zeitüberschreitung der Stimme')); } }, timeout);
      piper.pending.set(id, {resolve, reject, timer});
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
    const msg = {type: 'speak', text, model: pb.voice.model, speaker: 0, rate};
    try { return await ask(msg, SPEAK_TIMEOUT_MS); }
    catch (e) {
      if (current !== pb) throw e;
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

  function toggle(key, text, opts) {
    if (!supported) return false;
    const wasPlaying = !!current && current.key === key;
    stop();
    if (wasPlaying) return false;
    const normalized = normalize(text);
    if (!normalized) return false;
    const lang = chosenLocale(normalized);
    const prof = profile(opts);
    const de = lang.startsWith('de') && piperPossible && prof.voiceId !== 'system';
    const pb = {key, opts, speaker: prof.key, lang, de, parts: [], at: 0, queued: 0, text, live: false, wake: null, spoken: false, voice: prof.voice};
    feed(pb, text, !!opts?.live);
    if (!pb.parts.length && !pb.live) return false;
    current = pb;
    setButtons();
    if (!de) {
      if (!systemOk) { current = null; setButtons(); return false; }
      // First speech call is synchronous in the user's tap (required by iOS Safari).
      systemNext(pb);
      return true;
    }
    // AudioContext must be created/resumed inside the tap (iOS).
    try { piper.ctx = piper.ctx && piper.ctx.state !== 'closed' ? piper.ctx : new AudioCtx(); resume(piper.ctx); }
    catch (_) { return fallback(pb); }
    const start = () => piperPlay(pb).catch(() => fallback(pb));
    if (piper.config && pb.voice && piper.ready.has(pb.voice.model)) { start(); return true; }
    // Not loaded yet: show a loading state instead of a robotic stand-in.
    pb.loading = true;
    setButtons();
    loadConfig().then(cfg => {
      if (current !== pb) return false;
      if (!cfg) return null;
      pb.voice = profile(opts).voice || pb.voice; // real config: real model names
      return warm(pb.voice);
    }).then(ok => {
      if (current !== pb) return;
      pb.loading = false;
      setButtons();
      if (ok) return start();
      fallback(pb);
    }).catch(() => fallback(pb));
    return true;
  }
  // Piper failed or is unavailable: the best neural system voice, but never
  // after a Piper part was already spoken (no second, different voice
  // mid-message). The user is told why it is silent.
  function fallback(pb) {
    if (current !== pb) return false;
    pb.loading = false;
    if (!systemOk || pb.spoken) {
      current = null; setButtons();
      window.notice?.('Vorlesen: Stimme konnte nicht geladen werden.');
      return false;
    }
    pb.de = false;
    // Restart from the first unspoken part with system-sized chunks.
    pb.parts = split(pb.parts.slice(Math.max(0, pb.at - 1)).join(' ')); pb.at = 0;
    setButtons();
    systemNext(pb);
    return true;
  }

  // (Re-)binds a speaker button to a message. Called again whenever a
  // streamed message grows or receives its final id; the running playback
  // follows that button (same message), so it is neither restarted nor
  // doubled, and new sentences are appended to it.
  function attach(button, text, id, opts) {
    const key = id || 'temporary-message-' + (++nextEphemeral);
    const oldKey = button.dataset.audioKey;
    if (current && oldKey && current.key === oldKey && oldKey !== key) current.key = key;
    else if (current && current.key !== key && current.live && current.speaker === speakerKey(opts)) {
      // Same live message re-rendered into a new node.
      const head = normalize(current.text).slice(0, 60);
      if (head.length >= 12 && normalize(text).startsWith(head)) current.key = key;
    }
    button.classList.add('message-speak');
    button.dataset.audioKey = key;
    button.type = 'button';
    button.disabled = !supported;
    button.onclick = () => toggle(key, button._magAudioText, button._magAudioOpts);
    button._magAudioText = text;
    button._magAudioOpts = opts;
    if (current && current.key === key) { current.text = text; feed(current, text, !!opts?.live); }
    setButton(button);
    if (!supported) { button.title = 'Vorlesen auf diesem Gerät nicht verfügbar'; return; }
    // Tempo control right next to the speaker button, per speaker.
    const speaker = speakerKey(opts);
    let box = button._magRate;
    if (!box) { box = button._magRate = rateControl(speaker, opts?.name || ''); }
    box.dataset.speaker = speaker;
    const place = () => { if (button.parentNode && button.nextSibling !== box) button.after(box); paintRates(speaker); };
    if (button.parentNode) place(); else queueMicrotask(place);
  }

  async function voiceOptions() {
    await loadConfig();
    const list = voiceList().map(v => ({id: v.id, label: v.label}));
    list.push({id: 'system', label: 'Systemstimme'});
    return list;
  }
  // Background warm-up after load: fetch the speakers' voices into Cache
  // Storage once (no inference sessions beyond the first), so the first tap
  // does not wait for a download.
  function warmUp() {
    loadConfig().then(cfg => {
      if (!cfg) return;
      const models = [...new Set([profile({name: 'Lena'}), profile({name: 'Noah'}), profile({user: true})]
        .map(p => p.voice?.model).filter(Boolean))];
      if (!models.length) return;
      warm({model: models[0]});
      for (const m of models.slice(1)) ask({type: 'prefetch', model: m}, WARM_TIMEOUT_MS).catch(() => {});
    });
  }
  if (piperPossible) {
    if (document.readyState === 'complete') setTimeout(warmUp, 1500);
    else window.addEventListener('load', () => setTimeout(warmUp, 1500), {once: true});
  }

  window.magMessageAudio = {attach, toggle, stop, supported, bestVoice, rates: RATES,
    voiceOptions, speakerKey, profile, rateFor, setRate, stepRate, setSpeakerVoice,
    voiceFor: async opts => { await loadConfig(); const p = profile(opts); return {voice: p.voiceId, model: p.voice?.model || null, rate: p.rate}; },
    isPlaying: id => !!current && current.key === id};
  window.addEventListener('pagehide', stop);
})();

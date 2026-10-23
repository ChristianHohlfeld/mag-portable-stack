// Manual, device-local read-aloud for every MAG chat message (bot and human).
// Deliberately no autoplay and no server-side speech. German text uses the
// self-hosted Piper voices (ops/vendor-tts-assets.sh) in a Web Worker; the
// model is fetched once on first use and cached. Until it is ready, or where
// Workers/WebAudio are missing, the best system voice speaks instead
// (preferring Natural/Premium/Siri/Enhanced voices).
(() => {
  'use strict';
  const synth = window.speechSynthesis;
  const systemOk = !!synth && typeof window.SpeechSynthesisUtterance === 'function';
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const piperPossible = typeof Worker === 'function' && !!AudioCtx && typeof fetch === 'function';
  const supported = systemOk || piperPossible;
  const playIcon = '<svg class="i sm" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15 9a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12"/></svg>';
  const stopIcon = '<svg class="i sm" aria-hidden="true"><use href="#ic-stop"></use></svg>';
  const GOOD_VOICE = /natural|premium|siri|enhanced|neural|online/i;
  let current = null;
  let nextEphemeral = 0;

  function normalize(text) {
    return String(text || '')
      .replace(/\[\[MAG:(?:WEITER|ENDE)\]\]/g, '')
      .replace(/```[\s\S]*?```/g, ' ')
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

  function chosenLocale() {
    const setting = document.getElementById('language')?.value || 'auto';
    if (setting === 'de') return 'de-DE';
    if (setting === 'en') return 'en-US';
    const locale = navigator.language || 'de-DE';
    return locale.startsWith('de') ? 'de-DE' : locale.startsWith('en') ? 'en-US' : locale;
  }

  // Best system voice: same language, prefer natural-sounding names, then local.
  function bestVoice(lang) {
    const voices = synth?.getVoices?.() || [];
    const l = lang.toLowerCase(), base = l.split('-')[0];
    const same = voices.filter(v => v.lang?.toLowerCase() === l);
    const near = voices.filter(v => v.lang?.toLowerCase().split('-')[0] === base);
    for (const pool of [same, near]) {
      const pick = pool.find(v => GOOD_VOICE.test(v.name || '')) || pool.find(v => v.localService) || pool[0];
      if (pick) return pick;
    }
    return null;
  }

  function setButton(button) {
    const playing = !!current && current.key === button.dataset.audioKey;
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-label', playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen');
    button.title = playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen';
    button.innerHTML = playing ? stopIcon : playIcon;
  }
  function setButtons() {
    document.querySelectorAll('.message-speak').forEach(setButton);
  }

  // ---- Piper (worker) ----
  const piper = {config: null, configTried: false, worker: null, ready: new Set(), loading: null, ctx: null, seq: 0, pending: new Map(), source: null};
  function voicePref() { try { return localStorage.getItem('mag-tts-voice') || 'thorsten'; } catch (_) { return 'thorsten'; } }
  function piperVoice() {
    const pref = voicePref();
    if (pref === 'system' || !piper.config) return null;
    return piper.config.voices.find(v => v.id === pref) || piper.config.voices[0];
  }
  async function loadConfig() {
    if (piper.configTried) return piper.config;
    piper.configTried = true;
    try {
      const r = await fetch('/static/tts-config.json', {cache: 'no-store'});
      if (r.ok) piper.config = await r.json();
    } catch (_) { piper.config = null; }
    return piper.config;
  }
  function worker() {
    if (!piper.worker) {
      piper.worker = new Worker('/static/' + piper.config.dir + '/tts-worker.js');
      piper.worker.onmessage = ev => {
        const m = ev.data || {};
        if (m.type === 'progress') { document.dispatchEvent(new CustomEvent('mag-tts-progress', {detail: m.progress})); return; }
        const p = piper.pending.get(m.id);
        if (!p) return;
        piper.pending.delete(m.id);
        if (m.type === 'error') p.reject(new Error(m.error)); else p.resolve(m);
      };
    }
    return piper.worker;
  }
  function ask(msg) {
    const id = ++piper.seq;
    return new Promise((resolve, reject) => { piper.pending.set(id, {resolve, reject}); worker().postMessage({...msg, id}); });
  }
  function warm(v) {
    if (piper.ready.has(v.model) || piper.loading) return;
    piper.loading = ask({type: 'warm', model: v.model})
      .then(() => { piper.ready.add(v.model); })
      .catch(() => {})
      .finally(() => { piper.loading = null; });
  }
  async function piperPlay(playback, v) {
    // Synthesis of the next part overlaps playback of the current one.
    let next = ask({type: 'speak', text: playback.parts[0], model: v.model});
    for (let i = 0; i < playback.parts.length; i++) {
      const res = await next;
      if (current !== playback) return;
      next = i + 1 < playback.parts.length ? ask({type: 'speak', text: playback.parts[i + 1], model: v.model}) : null;
      await new Promise(done => {
        const buf = piper.ctx.createBuffer(1, res.audio.length, res.rate);
        buf.copyToChannel(res.audio, 0);
        const src = piper.ctx.createBufferSource();
        src.buffer = buf; src.connect(piper.ctx.destination);
        src.onended = done; piper.source = src; src.start();
      });
      if (current !== playback) return;
    }
    if (current === playback) { current = null; setButtons(); }
  }

  function stop() {
    current = null; // make pending onend/onerror callbacks harmless
    if (systemOk) synth.cancel();
    try { piper.source?.stop(); } catch (_) {}
    piper.source = null;
    setButtons();
  }

  function nextPart(playback) {
    if (current !== playback) return;
    const text = playback.parts[playback.at++];
    if (!text) { current = null; setButtons(); return; }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = playback.lang;
    utterance.rate = 1;
    const match = bestVoice(playback.lang);
    if (match) utterance.voice = match;
    utterance.onend = () => nextPart(playback);
    utterance.onerror = () => {
      if (current === playback) { current = null; setButtons(); }
    };
    try { synth.speak(utterance); }
    catch (_) { if (current === playback) { current = null; setButtons(); } }
  }

  function toggle(key, text) {
    if (!supported) return false;
    const wasPlaying = !!current && current.key === key;
    stop();
    if (wasPlaying) return false;
    const parts = split(normalize(text));
    if (!parts.length) return false;
    const playback = {key, parts, at: 0, lang: chosenLocale()};
    current = playback;
    setButtons();
    const v = playback.lang.startsWith('de') && piperPossible ? piperVoice() : null;
    if (v && piper.ready.has(v.model)) {
      // AudioContext must be created/resumed inside the tap (iOS).
      piper.ctx = piper.ctx || new AudioCtx();
      piper.ctx.resume?.();
      piperPlay(playback, v).catch(() => { if (current === playback && systemOk) nextPart(playback); });
      return true;
    }
    if (v) warm(v); // first use: load the voice once in the background
    if (!systemOk) {
      if (!v) { current = null; setButtons(); return false; }
      piper.ctx = piper.ctx || new AudioCtx();
      piper.ctx.resume?.();
      (piper.loading || Promise.resolve()).then(() => current === playback && piperPlay(playback, v))
        .catch(() => { if (current === playback) { current = null; setButtons(); } });
      return true;
    }
    // First speech call is synchronous in the user's tap (required by iOS Safari).
    nextPart(playback);
    return true;
  }

  function attach(button, text, id) {
    const key = id || 'temporary-message-' + (++nextEphemeral);
    button.classList.add('message-speak');
    button.dataset.audioKey = key;
    button.type = 'button';
    button.disabled = !supported;
    button.onclick = () => toggle(key, text);
    setButton(button);
    if (!supported) button.title = 'Vorlesen auf diesem Gerät nicht verfügbar';
  }

  function setVoice(id) { try { localStorage.setItem('mag-tts-voice', id); } catch (_) {} const v = piperVoice(); if (v) warm(v); }
  async function voiceOptions() {
    await loadConfig();
    const list = (piper.config?.voices || []).map(v => ({id: v.id, label: v.label}));
    list.push({id: 'system', label: 'Systemstimme'});
    return list;
  }
  if (piperPossible) loadConfig();

  window.magMessageAudio = {attach, toggle, stop, supported, setVoice, voiceOptions, voicePref, bestVoice,
    isPlaying: id => !!current && current.key === id, piperReady: () => { const v = piperVoice(); return !!v && piper.ready.has(v.model); }};
  window.addEventListener('pagehide', stop);
})();

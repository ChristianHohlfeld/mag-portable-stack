// Dictation. The microphone's audio goes to the room as it is spoken (16 kHz
// PCM in small chunks, one request after the other), the speech service on
// the server recognizes it, and every answer carries the text so far: it
// appears in the message field while you speak.
//
// Nothing here waits: the microphone is asked for when the button goes down,
// not when it comes up; the recognizer is always loaded; and after a
// dictation the microphone stays open for a minute, so the next one starts at
// once and even has the half second before the tap.
'use strict';
(function () {
  const { MAG } = window, { $ } = MAG;
  const WARM_MS = 60000, GAP_MS = 160;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const can = () => !!(navigator.mediaDevices?.getUserMedia && window.AudioWorkletNode && (window.AudioContext || window.webkitAudioContext));
  let input, button;
  let ctx = null, stream = null, source = null, node = null, module = null; // the audio path, kept while warm
  let asked = null;      // a getUserMedia that is under way
  let warm = 0;          // timer that closes the microphone
  let current = null;    // the dictation that is running
  let level = 0, painted = 0;

  const mic = () => navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const live = () => !!stream && stream.getAudioTracks().some((t) => t.readyState === 'live');
  function ask() {
    if (live()) return Promise.resolve(stream);
    asked ||= mic().then((got) => { asked = null; stream = got; return got; }, (err) => { asked = null; throw err; });
    return asked;
  }
  function release() {
    clearTimeout(warm);
    warm = 0;
    try { source?.disconnect(); node?.disconnect(); } catch {}
    stream?.getTracks().forEach((t) => t.stop());
    stream = source = node = null;
    if (ctx && ctx.state === 'running') ctx.suspend().catch(() => {});
  }
  function why(err) {
    const name = err?.name || '';
    if (name === 'NotAllowedError' || name === 'SecurityError') return 'Mikrofon nicht freigegeben. Bitte im Browser für diese Seite erlauben.';
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Kein Mikrofon gefunden';
    if (name === 'NotReadableError') return 'Das Mikrofon wird gerade von etwas anderem benutzt';
    return err?.message || 'Diktat fehlgeschlagen';
  }

  function paint() {
    const state = current ? (current.stopping ? 'busy' : current.hearing ? 'rec' : 'arm') : '';
    button.classList.toggle('rec', state === 'rec' || state === 'arm');
    button.classList.toggle('arm', state === 'arm');
    button.classList.toggle('busy', state === 'busy');
    button.setAttribute('aria-pressed', String(!!current));
    button.setAttribute('aria-label', current ? 'Diktat beenden' : 'Diktieren');
    button.title = current ? 'Diktat beenden' : 'Diktieren';
    if (!current) button.style.removeProperty('--level');
  }
  // The ring around the button follows the voice.
  function meter() {
    if (!current || performance.now() - painted < 60) return;
    painted = performance.now();
    button.style.setProperty('--level', Math.min(1, Math.sqrt(level) * 3.2).toFixed(2));
  }
  function show(s) {
    if (s.detached) return;
    // After what was there already: on the same line, or on the new one it ended with.
    const head = s.base.replace(/[ \t]+$/, ''), said = [s.committed, s.partial].map((part) => (part || '').trim()).filter(Boolean).join(' ');
    input.value = head + (head && said && !head.endsWith('\n') ? ' ' : '') + said;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.scrollTop = input.scrollHeight;
  }

  // One request after the other: whatever was spoken since the last one.
  async function pump(s) {
    let failures = 0, sent = 0;
    for (;;) {
      const end = s.stopped;
      if (!s.queue.length && !end) { await sleep(40); continue; }
      const wait = GAP_MS - (performance.now() - sent);
      if (wait > 0 && !end) { await sleep(wait); continue; }
      const parts = s.queue.splice(0), size = parts.reduce((n, part) => n + part.byteLength, 0);
      const body = new Uint8Array(size);
      let offset = 0;
      for (const part of parts) { body.set(new Uint8Array(part), offset); offset += part.byteLength; }
      for (;;) {
        try {
          sent = performance.now();
          const res = await MAG.fetch('/api/stt/live?sid=' + s.id + '&seq=' + s.seq + '&lang=de' + (end ? '&end=1' : ''),
            { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body });
          const out = await res.json().catch(() => ({}));
          if (!res.ok) throw Object.assign(new Error(out.error || 'Spracherkennung nicht erreichbar (' + res.status + ')'), { fatal: res.status < 500 || res.status === 503 });
          s.seq++;
          failures = 0;
          s.committed = out.committed || '';
          s.partial = out.partial || '';
          show(s);
          break;
        } catch (err) {
          // A lost answer is asked for again with the same number; the server hears nothing twice.
          if (err.fatal || ++failures > 4 || s.discarded) throw err;
          await sleep(300 * failures);
        }
      }
      if (end) return;
    }
  }

  async function start() {
    if (current || !can()) return;
    const s = { id: Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join(''),
      base: input.value, committed: '', partial: '', queue: [], seq: 0, hearing: false, stopped: false, stopping: null };
    current = s;
    clearTimeout(warm);
    paint();
    try {
      // The audio context has to start inside the tap (iOS); the microphone may already be on its way.
      ctx ||= new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });
      const running = ctx.state === 'running' ? null : ctx.resume();
      module ||= ctx.audioWorklet.addModule('/app/capture.js?v=' + encodeURIComponent(MAG.version)).catch((err) => { module = null; throw err; });
      const [media] = await Promise.all([ask(), module, running]);
      // Stopped before the microphone was there: do not leave it open.
      if (current !== s) { if (!current && !warm) release(); return; }
      if (!node || !source || source.mediaStream !== media) {
        try { source?.disconnect(); node?.disconnect(); } catch {}
        source = ctx.createMediaStreamSource(media);
        node = new AudioWorkletNode(ctx, 'mag-capture', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: 'explicit' });
        source.connect(node);
        node.connect(ctx.destination); // the worklet plays nothing; a node without a way out is not run everywhere
      }
      node.port.onmessage = (e) => {
        if (current !== s) return;
        if (e.data.stopped) { s.flushed?.(); return; }
        if (!s.hearing) { s.hearing = true; paint(); }
        s.queue.push(e.data.pcm);
        level = e.data.level;
        meter();
      };
      node.port.postMessage('start');
      s.pumping = pump(s).catch((err) => { if (current === s && !s.discarded) { MAG.toast(why(err), true); void stop({ failed: true }); } });
    } catch (err) {
      if (current === s) { current = null; paint(); release(); MAG.toast(why(err), true); }
    }
  }

  // Ends the dictation and waits for its last words. `discard` drops them
  // (the message was sent, or the text was edited by hand meanwhile).
  function stop(opts) {
    const s = current;
    if (!s) return Promise.resolve();
    if (opts?.discard) { s.detached = true; s.discarded = true; }
    if (s.stopping) return s.stopping;
    s.stopping = (async () => {
      paint();
      if (node && s.hearing) {
        // The worklet hands over what it still holds, then says so.
        const flushed = new Promise((resolve) => { s.flushed = resolve; });
        node.port.postMessage('stop');
        await Promise.race([flushed, sleep(300)]);
      }
      s.stopped = true;
      if (s.discarded || opts?.failed || !s.pumping) MAG.fetch('/api/stt/live?sid=' + s.id, { method: 'DELETE' }).catch(() => {});
      else await Promise.race([s.pumping, sleep(15000)]);
      if (current === s) current = null;
      paint();
      // Stay ready for the next sentence, but not for ever, and not out of sight.
      clearTimeout(warm);
      if (document.hidden || !live()) release();
      else warm = setTimeout(release, WARM_MS);
    })();
    return s.stopping;
  }

  MAG.dictation = {
    active: () => !!current,
    // For the composer: the text is complete before it is sent.
    finish: () => stop(),
    discard: () => stop({ discard: true }),
  };

  document.addEventListener('DOMContentLoaded', () => {
    input = $('#input');
    button = $('#mic');
    // The finger is down: get the microphone now, the tap follows in a moment.
    button.addEventListener('pointerdown', () => {
      if (current || !can() || live()) return;
      ask().then(() => setTimeout(() => { if (!current && !warm) release(); }, 1500), () => {});
    });
    button.addEventListener('click', () => (current ? void stop() : void start()));
    // Typing takes over: what is there stays, the dictation ends.
    input.addEventListener('input', (e) => { if (e.isTrusted && current && !current.stopping) { current.detached = true; void stop(); } });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && current && !current.stopping) void stop(); });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) return;
      if (current) void stop();
      else release();
    });
    window.addEventListener('pagehide', release);
  });
  MAG.on('settings', () => { if (button) button.hidden = !(MAG.state.features.stt && can()); });
})();

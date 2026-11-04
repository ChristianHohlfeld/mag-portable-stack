// MAG speech input. Primary: the browser/OS built-in recognizer (Web Speech API,
// same engine as phone/desktop dictation). Fallback only when that API is missing
// or unusable (e.g. Firefox): the bundled local Moonshine model in a WASM worker.
const $ = id => document.getElementById(id);
const field = $('message');
const button = $('mic');
const status = $('speech-status');
const version = document.querySelector('meta[name="mag-version"]')?.content || '';
const VERSIONED = /^speech-[a-f0-9]{12}$/;
const MODEL_FILES = [
  'adapter.ort', 'cross_kv.ort', 'decoder_kv.ort', 'encoder.ort',
  'frontend.model.ort', 'frontend.weights.ort', 'streaming_config.json', 'tokenizer.bin'
];
let engine = null;
let enginePromise = null;
let current = null;
let seq = 0;

function show(label, progress) {
  status.hidden = !label;
  status.textContent = label || '';
  status.setAttribute('role', 'status');
  if (progress !== undefined) status.dataset.progress = String(progress);
  else delete status.dataset.progress;
}
function buttonState(active) {
  button.classList.toggle('is-recording', !!active);
  button.setAttribute('aria-pressed', String(!!active));
  button.setAttribute('aria-label', active ? 'Aufnahme stoppen' : 'Spracheingabe starten');
  button.title = active ? 'Aufnahme stoppen' : 'Spracheingabe';
  button.innerHTML = '<svg class="i" aria-hidden="true"><use href="#ic-' +
    (active ? 'stop' : 'mic') + '"></use></svg>';
}
function updateComposer(s) {
  if (current !== s) return;
  const parts = [s.base, ...s.final, s.partial].map(v => (v || '').trim()).filter(Boolean);
  field.value = parts.join(' ');
  field.dispatchEvent(new Event('input', { bubbles: true }));
}
function assertCurrent(s) {
  if (s !== current) throw new Error('Aufnahme beendet');
}
function reportError(err) {
  const code = err?.name || '';
  if (code === 'NotAllowedError' || code === 'PermissionDeniedError')
    return 'Mikrofon nicht freigegeben – bitte in Safari/Browser erlauben.';
  if (code === 'NotFoundError') return 'Kein Mikrofon gefunden.';
  return 'Lokale Spracherkennung: ' + (err?.message || String(err));
}
async function getEngine() {
  if (engine) return engine;
  if (enginePromise) return enginePromise;
  enginePromise = (async () => {
    const res = await fetch('/static/speech-config.json', {cache: 'no-store'});
    if (!res.ok) throw new Error(res.status === 404
      ? 'Sprachmodell fehlt im installierten MAG-Release (Server-Assets prüfen).'
      : 'Sprachmodell nicht erreichbar (HTTP ' + res.status + ').');
    const cfg = await res.json();
    if (!VERSIONED.test(cfg.runtime) || !VERSIONED.test(cfg.model) || cfg.arch !== 4)
      throw new Error('Ungültige lokale Sprachmodell-Konfiguration');
    const {SttWorkerHost, ModelArch} = await import('/static/' + cfg.runtime +
      '/index.js?v=' + encodeURIComponent(version));
    const host = new SttWorkerHost();
    const files = Object.fromEntries(MODEL_FILES.map(name =>
      [name, '/static/' + cfg.model + '/' + name]));
    host.onProgress = (_, loaded, total) => {
      if (!current) return;
      const percent = total ? Math.min(100, Math.round(100 * loaded / total)) : null;
      show(percent === null ? 'Sprachmodell wird geladen …' :
        'Sprachmodell wird geladen: ' + percent + ' %', percent ?? undefined);
    };
    try {
      await host.loadTranscriber({
        transcriberId: 'de-small',
        modelArch: ModelArch.SmallStreaming,
        source: {kind: 'urls', files}
      });
    } catch (e) {
      host.close();
      throw e;
    }
    return {host};
  })();
  try {
    engine = await enginePromise;
    return engine;
  } finally {
    enginePromise = null;
  }
}
function workletCapture(s, ctx, stream, host) {
  return (async () => {
    const source = ctx.createMediaStreamSource(stream);
    s.source = source;
    const onChunk = data => {
      if (current !== s || !s.recording) return;
      const pcm = data instanceof Float32Array ? data : new Float32Array(data);
      // Worker owns all inference; only PCM is sent across the thread boundary.
      host.addAudio('mic-' + s.id, pcm, ctx.sampleRate);
    };
    if (ctx.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
      await ctx.audioWorklet.addModule('/static/speech-capture.js?v=' + encodeURIComponent(version));
      assertCurrent(s);
      const node = new AudioWorkletNode(ctx, 'mag-speech-capture');
      node.port.onmessage = event => onChunk(event.data);
      source.connect(node);
      node.connect(ctx.destination); // The worklet explicitly outputs silence.
      s.node = node;
    } else {
      const node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = event =>
        onChunk(new Float32Array(event.inputBuffer.getChannelData(0)));
      source.connect(node);
      node.connect(ctx.destination);
      s.node = node;
    }
  })();
}
async function localStart() {
  if (current) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.Worker ||
      !(window.AudioContext || window.webkitAudioContext)) {
    show('Dieses Gerät unterstützt die lokale Spracheingabe nicht.');
    return;
  }
  // The pinned Moonshine WASM package uses pthreads. Do not download a
  // 122-MB model if nginx did not preserve cross-origin isolation headers.
  if (!window.crossOriginIsolated || typeof SharedArrayBuffer === 'undefined') {
    show('Lokale Spracherkennung benötigt sichere COOP/COEP-Header (nginx).');
    return;
  }
  const s = {id: ++seq, base: field.value, final: [], partial: '', recording: false};
  current = s;
  buttonState(true);
  show('Mikrofon wird geöffnet …');
  try {
    // Start permission request and AudioContext in the actual user gesture (iOS Safari).
    const Context = window.AudioContext || window.webkitAudioContext;
    s.ctx = new Context({latencyHint: 'interactive'});
    const resume = s.ctx.resume();
    const media = navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}
    });
    await resume;
    s.media = await media;
    assertCurrent(s);
    show('Lokales Sprachmodell wird vorbereitet …');
    const {host} = await getEngine();
    assertCurrent(s);
    s.host = host;
    s.streamId = 'mic-' + s.id;
    await host.createStream('de-small', s.streamId, {updateInterval: 0.5});
    host.setListener(s.streamId, {
      onLineTextChanged: ({line}) => {
        if (current !== s) return;
        s.partial = line.text || '';
        updateComposer(s);
      },
      onLineCompleted: ({line}) => {
        if (current !== s) return;
        if (!s.done) s.done = new Set();
        if (s.done.has(line.id)) return;
        s.done.add(line.id);
        s.final.push(line.text || '');
        s.partial = '';
        updateComposer(s);
      },
      onError: ({error}) => { if (current === s) show(reportError(error)); }
    });
    await host.start(s.streamId);
    assertCurrent(s);
    if (s.ctx.state === 'suspended') await s.ctx.resume();
    assertCurrent(s);
    s.recording = true;
    await workletCapture(s, s.ctx, s.media, host);
    assertCurrent(s);
    show('Aufnahme läuft · tippen zum Beenden');
  } catch (err) {
    if (current === s) {
      // Keep the real error visible: stop() normally displays a success label.
      const failure = reportError(err);
      await stop();
      show(failure);
    } else {
      // Stop may have been clicked while permission/model download was pending.
      // Late getUserMedia results must never leave an orphaned live microphone.
      s.media?.getTracks().forEach(t => t.stop());
      if (s.host && s.streamId) {
        try { await s.host.closeStream(s.streamId); } catch (e) {}
      }
      try { await s.ctx?.close(); } catch (e) {}
    }
  }
}
async function localStop(opts) {
  const s = current;
  if (!s) return;
  if (opts?.discard) {
    // Send already took the text: detach now so a late final pass can never
    // write stale words back into the emptied composer.
    current = null;
    buttonState(false);
    show('');
  }
  if (s.stopping) return s.stopping;
  // Preserve session until the worker delivers its forced final pass.
  s.stopping = (async () => {
    s.recording = false;
    s.node?.disconnect();
    s.source?.disconnect();
    s.media?.getTracks().forEach(t => t.stop());
    if (s.host && s.streamId) {
      try { await s.host.stop(s.streamId); } catch (e) { show(reportError(e)); }
      try { await s.host.closeStream(s.streamId); } catch (e) { console.warn(e); }
      s.streamId = null;
    }
    try { await s.ctx?.close(); } catch (e) { console.warn(e); }
    if (current === s) {
      current = null;
      buttonState(false);
      show('');
      field.focus();
    }
  })();
  return s.stopping;
}
// ---- Native Web Speech API (primary) ----
const NativeRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let nativeBroken = !NativeRecognition;
let native = null;
const coarse = () => matchMedia('(pointer:coarse)').matches;
function nativeCompose(n) {
  if (native !== n) return;
  const parts = [n.base, n.committed, n.final, n.interim].map(v => (v || '').trim()).filter(Boolean);
  field.value = parts.join(' ');
  field.dispatchEvent(new Event('input', { bubbles: true }));
}
function nativeFinish(n, keepText) {
  if (native !== n) return;
  n.interim = '';
  if (keepText) nativeCompose(n);
  native = null;
  clearTimeout(n.timer);
  buttonState(false);
  show('');
  if (keepText && !coarse()) field.focus();
}
function nativeOpen(n) {
  const rec = new NativeRecognition();
  n.rec = rec;
  rec.lang = 'de-DE';
  // Android Chrome duplicates results in continuous mode: there we use
  // single-utterance sessions and restart them until the user stops.
  rec.continuous = !coarse();
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.onstart = () => { if (native === n) show('Zuhören … · Mikro tippen zum Beenden'); };
  rec.onresult = event => {
    if (native !== n || n.rec !== rec) return;
    n.heard = true;
    let fin = '', inter = '';
    for (let i = 0; i < event.results.length; i++) {
      const r = event.results[i];
      const t = (r[0]?.transcript || '').trim();
      if (!t) continue;
      if (r.isFinal) fin += (fin ? ' ' : '') + t;
      else inter += (inter ? ' ' : '') + t;
    }
    n.final = fin;
    n.interim = inter;
    nativeCompose(n);
  };
  rec.onerror = event => {
    if (native !== n || n.rec !== rec) return;
    const code = event.error || '';
    if (code === 'no-speech' || code === 'aborted') return; // onend decides
    n.interim = '';
    if (!n.heard && (code === 'network' || code === 'service-not-allowed' ||
        code === 'language-not-supported')) {
      // Built-in recognizer unusable here: fall back to the local model once.
      nativeBroken = true;
      n.fallback = true;
      return;
    }
    n.error = code === 'not-allowed'
      ? 'Mikrofon nicht freigegeben – bitte im Browser erlauben.'
      : code === 'audio-capture' ? 'Kein Mikrofon gefunden.'
      : 'Spracherkennung: ' + code;
  };
  rec.onend = () => {
    if (native !== n || n.rec !== rec) return;
    // Commit this session's final text; interim text is always dropped.
    n.committed = [n.committed, n.final].map(v => (v || '').trim()).filter(Boolean).join(' ');
    n.final = '';
    n.interim = '';
    if (n.fallback) {
      nativeFinish(n, true);
      void localStart();
      return;
    }
    if (n.error) {
      const msg = n.error;
      nativeFinish(n, true);
      show(msg);
      clearTimeout(n.errTimer);
      setTimeout(() => { if (!native && !current && status.textContent === msg) show(''); }, 6000);
      return;
    }
    if (!n.stopping && !document.hidden) {
      try { nativeOpen(n); return; } catch (e) { /* fall through */ }
    }
    nativeFinish(n, true);
  };
  rec.start();
}
function nativeStart() {
  const n = {base: field.value, committed: '', final: '', interim: ''};
  native = n;
  buttonState(true);
  show('Mikrofon wird geöffnet …');
  try {
    nativeOpen(n);
  } catch (e) {
    native = null;
    buttonState(false);
    nativeBroken = true;
    void localStart();
  }
}
function nativeStop(opts) {
  const n = native;
  if (!n) return Promise.resolve();
  n.stopping = true;
  if (opts?.discard) {
    // Message was sent: never write the recognized text back.
    native = null;
    clearTimeout(n.timer);
    buttonState(false);
    show('');
    try { n.rec?.abort(); } catch (e) {}
    return Promise.resolve();
  }
  return new Promise(resolve => {
    const done = () => { nativeFinish(n, true); resolve(); };
    const prevEnd = n.rec?.onend;
    if (n.rec) n.rec.onend = ev => { try { prevEnd?.(ev); } finally { done(); } };
    n.timer = setTimeout(() => { try { n.rec?.abort(); } catch (e) {} done(); }, 2500);
    try { n.rec?.stop(); } catch (e) { done(); }
  });
}

function start() { return nativeBroken ? localStart() : nativeStart(); }
function stop(opts) {
  if (native) return nativeStop(opts);
  return localStop(opts);
}
buttonState(false);
button.onclick = () => { if (native || current) void stop(); else void start(); };
document.addEventListener('visibilitychange', () => {
  if (document.hidden && (native || current?.recording)) void stop();
});
field.addEventListener('input', event => {
  // Typing or OS keyboard dictation takes over: stop our capture, never block it.
  if (event.isTrusted && (native || current?.recording)) void stop();
});
window.magSpeechStop = stop;
window.magSpeechBusy = () => !!(native || current);
window.addEventListener('pagehide', () => {
  if (native) { try { native.rec?.abort(); } catch (e) {} native = null; }
  const s = current;
  current = null;
  s?.media?.getTracks().forEach(t => t.stop());
  if (s?.ctx) void s.ctx.close();
  engine?.host.close();
});

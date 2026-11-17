// Three compact audio lanes on an existing video card (microphone or music file).
// Recordings and mixdowns stay ordinary room attachments; no additional UI
// player or job queue. Never acquire a microphone outside a direct owner tap.
'use strict';
(function () {
  const { MAG } = window, { el } = MAG;
  const url = (a, suffix = '') => '/api/file/' + encodeURIComponent(a.file) + suffix;
  const LANES = [['voice', 'Stimme'], ['melody', 'Melodie'], ['effects', 'Geräusche']];
  const TYPES = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg'];
  const supportedMime = () => typeof MediaRecorder === 'undefined' ? ''
    : TYPES.find((t) => MediaRecorder.isTypeSupported?.(t)) || '';
  const formatOf = (t) => /^audio\/(mp4|webm|ogg)/i.exec(t || '')?.[1]?.toLowerCase() || '';
  const micError = (e) => ['NotAllowedError', 'PermissionDeniedError'].includes(e?.name)
    ? 'Mikrofon verweigert. Bitte die Berechtigung für diese HTTPS-Seite erlauben.'
    : e?.name === 'NotFoundError' ? 'Kein Mikrofon vorhanden.'
    : e?.name === 'NotReadableError' ? 'Das Mikrofon ist belegt.'
    : e?.message || 'Das Mikrofon konnte nicht geöffnet werden.';

  const laneOf = (a) => a.voiceover_lane || 'voice';  // backwards-compatible old takes
  function latest(message, video) {
    const out = { voice: null, melody: null, effects: null, mix: null, baked: null };
    for (const a of message.attachments || []) {
      if (a.voiceover_for !== video) continue;
      if (a.voiceover_kind === 'take' && laneOf(a) in out) out[laneOf(a)] = a;
      if (a.voiceover_kind === 'mix') out.mix = a;
      if (a.voiceover_kind === 'baked') out.baked = a;
    }
    return out;
  }

  function player(message, source, klass = '') {
    const original = source.file, owner = MAG.state?.you?.role === 'owner';
    let tracks = latest(message, original), lane = 'voice', stream = null, recorder = null;
    let busy = false, stopping = false, discard = false, rewinding = false;
    let savedMute = false, observer = null;
    const video = el('video', { class: klass, controls: true, preload: 'metadata',
      playsInline: true, src: url(source) });
    const box = el('div', { class: 'voiceover', dataset: { keep: 'video:' + original } }, video);
    const announce = el('small', { class: 'voice-status note', role: 'status', 'aria-live': 'polite' });
    const status = (msg, error = false) => {
      announce.textContent = msg;
      announce.classList.toggle('err', error);
    };
    const buttons = new Map();
    const laneBar = el('div', { class: 'voice-lanes seg', role: 'group',
      'aria-label': 'Tonspur wählen' });
    LANES.forEach(([key, title]) => {
      const b = el('button', { type: 'button', 'aria-label': title,
        title: title + ' auswählen',
        onclick: () => { if (recorder?.state === 'recording') return; lane = key; paint(); }
      }, el('span', { class: 'voice-take-dot', 'aria-hidden': 'true' }), title);
      buttons.set(key, b);
      laneBar.append(b);
    });
    const mic = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Mikrofon freigeben', title: 'Mikrofon freigeben',
      html: MAG.icons.mic });
    // Melody-only file control; two small offsets instead of a timeline editor.
    const songFile = el('input', { type: 'file', hidden: true,
      accept: '.mp3,.wav,.m4a,.ogg,.weba,.flac,audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/webm,audio/flac' });
    const songUpload = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Musikdatei hochladen', title: 'MP3, WAV, M4A, Ogg, WebM oder FLAC hochladen',
      html: MAG.icons.file });
    const songName = el('span', { class: 'voice-song-name', title: 'Aktuelle Melodie',
      text: 'Musikdatei wählen' });
    const videoAt = el('input', { type: 'number', class: 'voice-cue-input',
      min: '0', step: '0.1', value: '0', inputMode: 'decimal',
      'aria-label': 'Melodie ab Videosekunde' });
    const songFrom = el('input', { type: 'number', class: 'voice-cue-input',
      min: '0', step: '0.1', value: '0', inputMode: 'decimal',
      'aria-label': 'Song ab Sekunde' });
    const cues = el('span', { class: 'voice-cues' },
      el('label', {}, 'Video ab', videoAt, 's'),
      el('label', {}, 'Song ab', songFrom, 's'));
    const discardButton = el('button', { type: 'button', class: 'icon-btn voice-discard',
      'aria-label': 'Aufnahme verwerfen', title: 'Aufnahme verwerfen',
      html: MAG.icons.x, hidden: true });
    const preview = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Mit Tonspuren anhören', title: 'Mit Tonspuren anhören',
      html: MAG.icons.speak, hidden: true });
    const bake = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Mix und MP4 erstellen', title: 'Mix und MP4 erstellen',
      html: MAG.icons.film, hidden: true });
    const downloads = el('span', { class: 'voice-exports', 'aria-label': 'Fertige Dateien' });
    const actions = el('div', { class: 'voice-actions', role: 'group',
      'aria-label': 'Tonspuren bearbeiten' },
      owner ? mic : null, owner ? discardButton : null, preview, owner ? bake : null);
    const toolbar = el('div', { class: 'voice-controls' }, laneBar, actions);
    const melodyRow = el('div', { class: 'voice-melody', hidden: true },
      owner ? songUpload : null, owner ? songFile : null,
      songName, owner ? cues : null);
    const footer = el('div', { class: 'voice-footer' }, downloads, announce);
    const audios = Object.fromEntries(LANES.map(([id]) =>
      [id, el('audio', { preload: 'metadata', hidden: true })]));
    const audiosBox = el('div', { hidden: true }, ...Object.values(audios));
    box.append(audiosBox, toolbar, melodyRow, footer);
    // One stable audio element per lane, shared with the original video
    // timeline. The video controls remain native and work on iOS/Android.
    function sync() {
      if (recorder?.state === 'recording') return;
      for (const [key] of LANES) {
        const audio = audios[key], take = tracks[key];
        if (!take) { audio.pause(); continue; }
        if (audio.dataset.file !== take.file) {
          audio.dataset.file = take.file;
          audio.src = url(take);
        }
        const at = key === 'melody' ? Number(take.voiceover_at || 0) : 0;
        const from = key === 'melody' ? Number(take.voiceover_from || 0) : 0;
        const position = video.currentTime - at + from;
        const audible = video.currentTime >= at && position >= 0 &&
          position < Number(take.duration || Infinity);
        if (!audible) { audio.pause(); continue; }
        try {
          if (Math.abs((audio.currentTime || 0) - position) > 0.2)
            audio.currentTime = position;
        } catch (_) {}
        audio.playbackRate = video.playbackRate;
        audio.volume = video.volume;
        if (video.paused || video.ended) audio.pause();
        else if (audio.paused) audio.play().catch(() => {
          status('Bitte die Vorschau antippen.');
        });
      }
    }
    const have = () => LANES.some(([key]) => !!tracks[key]);
    function paint() {
      for (const [key] of LANES) {
        const b = buttons.get(key);
        b.classList.toggle('on', lane === key);
        b.classList.toggle('has-take', !!tracks[key]);
        b.setAttribute('aria-pressed', String(lane === key));
      }
      preview.hidden = !have();
      bake.hidden = !have() || !owner;
      bake.disabled = busy || recorder?.state === 'recording';
      mic.disabled = busy || stopping || !compatible;
      const micLabel = recorder?.state === 'recording' ? 'Aufnahme stoppen und behalten'
        : stream ? 'Aufnahme starten' : 'Mikrofon freigeben';
      mic.setAttribute('aria-label', micLabel);
      mic.title = micLabel;
      mic.innerHTML = recorder?.state === 'recording' ? MAG.icons.stop : MAG.icons.mic;
      mic.classList.toggle('voice-recording', recorder?.state === 'recording');
      melodyRow.hidden = !owner || lane !== 'melody';
      cues.hidden = !tracks.melody;
      songUpload.disabled = busy || recorder?.state === 'recording';
      videoAt.disabled = songFrom.disabled = busy || recorder?.state === 'recording' || !tracks.melody;
      songName.textContent = tracks.melody?.name || 'Musikdatei wählen';
      songName.title = tracks.melody?.name || 'Musikdatei hochladen';
      if (tracks.melody) {
        const at = String(Number(tracks.melody.voiceover_at || 0));
        const from = String(Number(tracks.melody.voiceover_from || 0));
        if (document.activeElement !== videoAt) videoAt.value = at;
        if (document.activeElement !== songFrom) songFrom.value = from;
        if (Number.isFinite(video.duration)) videoAt.max = String(video.duration);
        if (Number.isFinite(Number(tracks.melody.duration)))
          songFrom.max = String(tracks.melody.duration);
        songUpload.title = 'Musikdatei wählen oder ersetzen';
      }
      discardButton.hidden = !stream && recorder?.state !== 'recording';
      discardButton.title = recorder?.state === 'recording' ? 'Aufnahme verwerfen' : 'Mikrofon schließen';
      discardButton.setAttribute('aria-label', discardButton.title);
      downloads.replaceChildren();
      if (tracks.mix) downloads.append(el('a', {
        href: url(tracks.mix, '?download=1'), download: tracks.mix.name,
        title: 'Fertigen Audiomix herunterladen', text: 'Audiomix'
      }));
      if (tracks.baked) downloads.append(el('a', {
        href: url(tracks.baked, '?download=1'), download: tracks.baked.name,
        title: 'MP4 mit Nachvertonung herunterladen', text: 'MP4'
      }));
    }
    // Called by the chat's generic preservation path after an SSE update.
    // Never recreate or unmount the player during an active recording.
    box._voiceoverRefresh = (m) => {
      tracks = latest(m, original);
      paint();
      if (recorder?.state !== 'recording') sync();
    };
    for (const event of ['play', 'pause', 'seeking', 'seeked', 'timeupdate',
      'ratechange', 'volumechange', 'ended']) video.addEventListener(event, sync);
    preview.addEventListener('click', () => {
      video.pause();
      video.currentTime = 0;
      for (const [key] of LANES) {
        const take = tracks[key], audio = audios[key];
        if (!take) continue;
        if (audio.dataset.file !== take.file) { audio.dataset.file = take.file; audio.src = url(take); }
        const at = key === 'melody' ? Number(take.voiceover_at || 0) : 0;
        audio.currentTime = key === 'melody' ? Number(take.voiceover_from || 0) : 0;
        if (at > 0) { audio.pause(); continue; }
        audio.play().catch(() => status('Tonspur konnte nicht abgespielt werden.', true));
      }
      // Synchronous inside click: explicit user activation on mobile Safari.
      video.play().catch(() => status('Video: bitte im Player auf Play drücken.', true));
    });
    const compatible = !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== 'undefined' && window.isSecureContext;
    if (!owner) { paint(); return box; }

    if (!compatible) {
      mic.disabled = true;
      status('Mikrofon benötigt HTTPS und MediaRecorder.', true);
    }

    songUpload.addEventListener('click', () => {
      if (!busy && recorder?.state !== 'recording') songFile.click();
    });
    songFile.addEventListener('change', async () => {
      const file = songFile.files?.[0];
      songFile.value = ''; // selecting the same file again is always allowed
      if (!file || busy || recorder?.state === 'recording') return;
      if (file.size < 128 || file.size > 32 * 1024 * 1024)
        return status('Melodie muss zwischen 128 Byte und 32 MB groß sein.', true);
      busy = true; paint(); status('Musikdatei wird gespeichert …');
      try {
        const uploaded = await MAG.api('POST', '/api/video/voiceover?message=' +
          encodeURIComponent(message.id) + '&video=' + encodeURIComponent(original) +
          '&lane=melody&name=' + encodeURIComponent(file.name), file);
        tracks.melody = uploaded;
        status('Melodie geladen. Start im Video und im Song einstellbar.');
      } catch (e) { status('Musik-Upload fehlgeschlagen: ' + e.message, true); }
      finally { busy = false; paint(); }
    });
    async function saveCue() {
      const take = tracks.melody;
      if (!take || busy || recorder?.state === 'recording') return;
      const at = Number(videoAt.value), from = Number(songFrom.value);
      const videoLength = Number.isFinite(video.duration) ? video.duration : Infinity;
      if (!videoAt.value || !songFrom.value || !Number.isFinite(at) || !Number.isFinite(from) ||
          at < 0 || from < 0 || at >= videoLength ||
          (Number.isFinite(Number(take.duration)) && from >= Number(take.duration))) {
        status('Startzeiten müssen innerhalb von Video und Song liegen.', true);
        videoAt.value = String(Number(take.voiceover_at || 0));
        songFrom.value = String(Number(take.voiceover_from || 0));
        return;
      }
      busy = true; paint();
      try {
        const saved = await MAG.api('POST', '/api/video/voiceover/cue', {
          message: message.id, video: original, file: take.file, at, from
        });
        tracks.melody = { ...take, voiceover_at: saved.at, voiceover_from: saved.from };
        status('Melodie-Position gespeichert.');
        sync();
      } catch (e) {
        status('Position konnte nicht gespeichert werden: ' + e.message, true);
        videoAt.value = String(Number(take.voiceover_at || 0));
        songFrom.value = String(Number(take.voiceover_from || 0));
      } finally { busy = false; paint(); }
    }
    videoAt.addEventListener('change', saveCue);
    songFrom.addEventListener('change', saveCue);

    function stopMic() {
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      observer?.disconnect(); observer = null;
      window.removeEventListener('pagehide', leave);
      document.removeEventListener('visibilitychange', hidden);
    }
    function stopRecording(reject = false) {
      if (!recorder || recorder.state !== 'recording' || stopping) return;
      stopping = true; discard = reject; busy = true;
      try { recorder.stop(); } catch (_) { discard = true; busy = false; }
      video.pause();
      paint();
    }
    function leave() {
      if (recorder?.state === 'recording') stopRecording(true);
      stopMic();
    }
    function hidden() {
      if (document.hidden) {
        if (recorder?.state === 'recording') stopRecording(true);
        else stopMic();
        paint();
      }
    }
    function watchMic() {
      window.addEventListener('pagehide', leave);
      document.addEventListener('visibilitychange', hidden);
      if (window.MutationObserver && document.body) {
        observer = new MutationObserver(() => { if (!box.isConnected) leave(); });
        observer.observe(document.body, { childList: true, subtree: true });
      }
    }

    mic.addEventListener('click', async () => {
      if (recorder?.state === 'recording') return stopRecording();
      if (busy) return;
      if (!stream) {
        busy = true; paint();
        try {
          // iOS and Android must ask permission as part of a user gesture.
          const granted = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false
          });
          stream = granted;
          if (!granted.getAudioTracks().length || !supportedMime())
            throw new Error('Dieser Browser unterstützt keine Mikrofonaufnahme.');
          watchMic();
          if (!box.isConnected) return leave();
          status('Mikrofon bereit – erneut tippen zum Starten.');
        } catch (e) {
          stopMic();
          status(micError(e), true);
        } finally { busy = false; paint(); }
        return;
      }
      if (!video.readyState) return status('Video erst laden und dann aufnehmen.', true);
      const activeLane = lane, chunks = [];
      discard = false; stopping = false;
      try {
        recorder = new MediaRecorder(stream, {
          mimeType: supportedMime(), audioBitsPerSecond: 96000
        });
        recorder.addEventListener('dataavailable', (e) => { if (e.data?.size) chunks.push(e.data); });
        recorder.addEventListener('error', () => {
          stopRecording(true);
          status('Mikrofonaufnahme fehlgeschlagen.', true);
        });
        recorder.addEventListener('stop', async () => {
          const blob = new Blob(chunks, { type: recorder?.mimeType || '' });
          const aborted = discard;
          video.muted = savedMute;
          stopMic();
          recorder = null; stopping = false;
          busy = !aborted;
          paint();
          if (aborted) return status('Aufnahme verworfen.');
          if (!formatOf(blob.type) || blob.size < 128 || blob.size > 12 * 1024 * 1024) {
            busy = false; paint();
            return status('Audioaufnahme ist leer oder zu groß.', true);
          }
          status('Tonspur wird gespeichert …');
          try {
            await MAG.api('POST', '/api/video/voiceover?message=' + encodeURIComponent(message.id) +
              '&video=' + encodeURIComponent(original) + '&lane=' + activeLane, blob);
            status('Tonspur gespeichert.');
          } catch (e) { status('Speichern fehlgeschlagen: ' + e.message, true); }
          finally { busy = false; paint(); }
        }, { once: true });
        // Record video and audio at the same 0-second reference position.
        video.pause();
        for (const audio of Object.values(audios)) audio.pause();
        savedMute = video.muted;
        video.muted = true; // avoid echo/feedback during the microphone take
        rewinding = video.currentTime > 0.02;
        if (rewinding) video.currentTime = 0;
        recorder.start(250);
        paint();
        // User-activated play: do not await any permission prompt here.
        video.play().catch((e) => { stopRecording(true); status('Video startet nicht: ' + e.message, true); });
        status('● ' + LANES.find(([key]) => key === activeLane)[1] + ' wird aufgenommen');
      } catch (e) {
        stopMic();
        recorder = null;
        video.muted = savedMute;
        status(micError(e), true);
        paint();
      }
    });
    discardButton.addEventListener('click', () => {
      if (recorder?.state === 'recording') stopRecording(true);
      else { stopMic(); paint(); status('Mikrofon geschlossen.'); }
    });
    video.addEventListener('ended', () => stopRecording());
    video.addEventListener('pause', () => {
      if (recorder?.state === 'recording') stopRecording();
    });
     const desync = () => {
      if (rewinding && video.currentTime < 0.02) { rewinding = false; return; }
      if (recorder?.state === 'recording') {
        stopRecording(true);
        status('Aufnahme verworfen: Videozeit geändert.', true);
      }
    };
    video.addEventListener('seeking', desync);
    video.addEventListener('ratechange', desync);
    bake.addEventListener('click', async () => {
      if (busy || recorder?.state === 'recording') return;
      const lanes = Object.fromEntries(LANES.filter(([key]) => tracks[key])
        .map(([key]) => [key, tracks[key].file]));
      if (!Object.keys(lanes).length) return;
      busy = true; paint();
      status('Drei Spuren werden zu einem Mix und einem neuen MP4 gebacken …');
      try {
        await MAG.api('POST', '/api/video/bake', {
          message: message.id, video: original, lanes, mode: 'mix'
        });
        status('Mix und MP4 gespeichert. Original und Rohspuren bleiben erhalten.');
      } catch (e) { status('Baken fehlgeschlagen: ' + e.message, true); }
      finally { busy = false; paint(); }
    });
    paint();
    return box;
  }

  MAG.voiceover = { player, supportedMime, formatOf, micError, latest, LANES };
})();

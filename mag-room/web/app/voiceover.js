// Microphone overdubs for an existing video card. No codec, mixer or
// recording runs on the server until the owner explicitly saves or bakes.
// Microphone access comes only from an owner click (iOS/Android/Desktop).
'use strict';
(function () {
  const { MAG } = window, { el } = MAG;
  const url = (a, suffix = '') => '/api/file/' + encodeURIComponent(a.file) + suffix;
  const modes = [['original', 'Originalton'], ['dub', 'Nur Nachvertonung'], ['mix', 'Beide Tonspuren']];
  const types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg'];
  const supportedMime = () => {
    if (typeof MediaRecorder === 'undefined') return '';
    return types.find((type) => MediaRecorder.isTypeSupported?.(type)) || '';
  };
  const formatOf = (type) => {
    if (/^audio\/mp4/i.test(type)) return 'mp4';
    if (/^audio\/webm/i.test(type)) return 'webm';
    if (/^audio\/ogg/i.test(type)) return 'ogg';
    return '';
  };
  const micError = (e) => e?.name === 'NotAllowedError' || e?.name === 'PermissionDeniedError'
    ? 'Mikrofon verweigert. Erlaube es in den Einstellungen des Browsers für diese HTTPS-Seite.'
    : e?.name === 'NotFoundError' ? 'Kein Mikrofon verfügbar.'
    : e?.name === 'NotReadableError' ? 'Mikrofon wird bereits verwendet oder kann nicht geöffnet werden.'
    : e?.message || 'Mikrofon konnte nicht geöffnet werden.';

  // The user keeps the source, every raw take and every baked export. This
  // player only references files that belong to this video in this message.
  function player(message, source, klass = '') {
    const sourceFile = source.file;
    const takes = (message.attachments || []).filter((a) =>
      a.voiceover_kind === 'take' && a.voiceover_for === sourceFile && a.kind === 'audio');
    const video = el('video', { class: klass, controls: true, preload: 'metadata',
      playsInline: true, src: url(source) });
    const box = el('div', { class: 'voiceover', dataset: {
      keep: sourceFile + ':' + takes.map((a) => a.file).join(',')
    } }, video);
    const owner = MAG.state?.you?.role === 'owner';
    if (!owner && !takes.length) return box;

    const audio = el('audio', { preload: 'metadata' });
    audio.hidden = true;
    let selected = takes[takes.length - 1] || null;
    let mode = 'original';
    const announce = el('small', { class: 'voice-status', role: 'status', 'aria-live': 'polite', text: '' });
    const status = (msg, bad = false) => { announce.textContent = msg; announce.classList.toggle('err', bad); };

    // Keep the selected microphone take alongside the video on play, seek,
    // pause, speed changes and scrubbing. No WebAudio context required.
    const active = () => selected && mode !== 'original';
    function sync() {
      // During microphone capture, do not mix in a previously selected take
      // or override the user's explicit monitor/mute choice.
      if (recorder?.state === 'recording') return;
      if (!active()) { audio.pause(); video.muted = false; return; }
      video.muted = mode === 'dub';
      if (audio.src !== location.origin + url(selected)) audio.src = url(selected);
      if (Number.isFinite(video.currentTime) && Math.abs((audio.currentTime || 0) - video.currentTime) > 0.18)
        try { audio.currentTime = video.currentTime; } catch (_) {}
      audio.playbackRate = video.playbackRate;
      audio.volume = video.volume;
      if (video.paused || video.ended) audio.pause();
      else if (audio.paused) audio.play().catch(() => status('Für die Nachvertonung bitte „Mit Tonspur abspielen“ antippen.'));
    }
    for (const event of ['play', 'pause', 'seeking', 'seeked', 'timeupdate', 'ratechange', 'volumechange', 'ended'])
      video.addEventListener(event, sync);
    const options = (values) => values.map(([value, text]) => el('option', { value, text }));
    const modePicker = el('select', { 'aria-label': 'Tonspuren', onchange: () => {
      mode = modePicker.value;
      sync();
    } }, options(modes));
    const takePicker = el('select', { 'aria-label': 'Mikrofonaufnahme', onchange: () => {
      selected = takes.find((a) => a.file === takePicker.value) || null;
      audio.pause(); audio.removeAttribute('src'); audio.load(); sync();
    } }, options(takes.map((a, i) => [a.file, 'Aufnahme ' + (i + 1)])));
    if (selected) takePicker.value = selected.file;
    const playDub = el('button', { class: 'pill', type: 'button', text: 'Mit Tonspur abspielen', onclick: () => {
      if (!active()) mode = modePicker.value = 'dub';
      video.currentTime = Math.min(video.currentTime, video.duration || 0);
      sync();
      // Called synchronously from the click: required on iOS Safari for audio.
      audio.play().catch(() => status('Audio konnte nicht gestartet werden.', true));
      video.play().catch(() => status('Video: Tippe auf Play im Player.', true));
    } });
    const tracks = el('div', { class: 'voice-controls' }, el('b', { text: 'Tonspuren' }),
      ...(takes.length ? [takePicker, modePicker, playDub] : []));
    box.append(audio, tracks);
    if (!takes.length) tracks.append(el('small', { text: 'Originalton · noch keine Nachvertonung' }));
    if (!owner) return box;

    let stream = null, recorder = null, discarded = false;
    const permit = el('button', { class: 'pill', type: 'button', text: 'Mikrofon freigeben' });
    const record = el('button', { class: 'pill', type: 'button', text: 'Aufnahme starten', disabled: true });
    const cancel = el('button', { class: 'pill', type: 'button', text: 'Verwerfen', hidden: true });
    const hear = el('label', { class: 'voice-monitor' },
      el('input', { type: 'checkbox' }), ' Originalton mithören (Kopfhörer empfohlen)');
    const monitor = hear.querySelector('input');
    let observer = null;
    function stopTracks() {
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      observer?.disconnect(); observer = null;
      window.removeEventListener('pagehide', onLeave);
      document.removeEventListener('visibilitychange', onHidden);
    }
    function onLeave() {
      if (recorder?.state === 'recording') stopRecording(true);
      stopTracks();
    }
    function onHidden() {
      if (!document.hidden) return;
      if (recorder?.state === 'recording') stopRecording();
      else if (stream) {
        stopTracks();
        permit.textContent = 'Mikrofon freigeben';
        record.disabled = true;
        status('Mikrofon beim Verlassen geschlossen.');
      }
    }
    function watchMicrophone() {
      window.addEventListener('pagehide', onLeave);
      document.addEventListener('visibilitychange', onHidden);
      if (window.MutationObserver && document.body) {
        observer = new MutationObserver(() => { if (!box.isConnected) onLeave(); });
        observer.observe(document.body, { childList: true, subtree: true });
      }
      if (!box.isConnected) onLeave();
    }
    const stopRecording = (discard = false) => {
      if (!recorder || recorder.state === 'inactive') return;
      discarded = discard;
      recorder.stop();
      video.pause();
      record.disabled = true;
      cancel.hidden = true;
    };
    const allow = !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined' &&
      window.isSecureContext;
    if (!allow) {
      permit.disabled = true;
      status('Mikrofonaufnahme benötigt HTTPS und einen Browser mit MediaRecorder.', true);
    }
    permit.addEventListener('click', async () => {
      // Never ask automatically: this click is the browser permission gesture.
      if (stream) {
        stopTracks();
        record.disabled = true;
        permit.textContent = 'Mikrofon freigeben';
        return status('Mikrofon geschlossen.');
      }
      permit.disabled = true;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false
        });
        if (!stream.getAudioTracks().length) throw new Error('Keine Mikrofonspur erhalten.');
        if (!supportedMime()) throw new Error('Dieser Browser kann keine unterstützte Audiodatei aufnehmen.');
        watchMicrophone();
        if (!stream) return; // view disappeared while permission prompt was open
        record.disabled = false;
        permit.disabled = false;
        permit.textContent = 'Mikrofon schließen';
        status('Mikrofon erlaubt. Aufnahme startet mit dem Video ab Sekunde 0.');
      } catch (err) {
        stopTracks();
        permit.disabled = false;
        status(micError(err), true);
      }
    });
    record.addEventListener('click', () => {
      if (recorder?.state === 'recording') return stopRecording();
      if (!stream || !supportedMime()) return;
      if (!video.readyState) { status('Video lädt noch. Bitte kurz im Player öffnen.', true); return; }
      discarded = false;
      let chunks = [], bad = false;
      try {
        recorder = new MediaRecorder(stream, { mimeType: supportedMime(), audioBitsPerSecond: 96000 });
        recorder.addEventListener('dataavailable', (e) => { if (e.data?.size) chunks.push(e.data); });
        recorder.addEventListener('error', () => { bad = true; stopRecording(true); status('Aufnahmefehler.', true); });
        recorder.addEventListener('stop', async () => {
          const type = recorder.mimeType || chunks[0]?.type || '', fmt = formatOf(type);
          const blob = new Blob(chunks, { type });
          chunks = [];
          stopTracks();
          record.disabled = true;
          permit.disabled = false;
          permit.textContent = 'Mikrofon freigeben';
          record.textContent = 'Aufnahme starten';
          video.muted = false;
          if (discarded) return status('Aufnahme verworfen.');
          if (bad || !fmt || blob.size < 128 || blob.size > 12 * 1024 * 1024)
            return status('Aufnahme leer, zu groß oder Format nicht unterstützt.', true);
          status('Nachvertonung wird gespeichert …');
          try {
            await MAG.api('POST', '/api/video/voiceover?message=' + encodeURIComponent(message.id) +
              '&video=' + encodeURIComponent(sourceFile), blob);
            status('Nachvertonung gespeichert. Original bleibt erhalten.');
          } catch (err) { status('Speichern fehlgeschlagen: ' + err.message, true); }
        }, { once: true });
        video.pause();
        audio.pause();
        video.currentTime = 0;
        video.muted = !monitor.checked;
        recorder.start(250);
        permit.disabled = true;
        video.play().catch((err) => { stopRecording(true); status('Video startet nicht: ' + err.message, true); });
        record.textContent = 'Aufnahme stoppen';
        cancel.hidden = false;
        status('● Aufnahme läuft · Video und Mikrofon ab Sekunde 0');
      } catch (err) { stopTracks(); record.disabled = true; status(micError(err), true); }
    });
    // The same button starts and stops; never register a second click
    // listener that would immediately stop the recording just started.
    cancel.addEventListener('click', () => stopRecording(true));
    video.addEventListener('ended', () => { if (recorder?.state === 'recording') stopRecording(); });
    // A microphone take is aligned to the video's time from second zero.
    // Pausing ends the take; seeking or changing speed would desynchronize it,
    // so those edits discard the unfinished take instead of saving bad audio.
    video.addEventListener('pause', () => {
      if (recorder?.state === 'recording') stopRecording();
    });
    const stopUnsynced = () => {
      if (recorder?.state === 'recording') {
        stopRecording(true);
        status('Aufnahme verworfen: Zeitposition oder Geschwindigkeit geändert.', true);
      }
    };
    video.addEventListener('seeking', stopUnsynced);
    video.addEventListener('ratechange', stopUnsynced);
    const acts = el('div', { class: 'voice-controls' }, permit, record, cancel, hear);
    const bakeMode = el('select', { 'aria-label': 'Tonmischung beim Einbrennen' }, options([
      ['mix', 'Original und Nachvertonung mischen'], ['replace', 'Nur Nachvertonung als Standard']
    ]));
    const bake = el('button', { class: 'pill', type: 'button', text: 'Als neues MP4 einbrennen', disabled: !selected,
      onclick: async () => {
        if (!selected) return;
        bake.disabled = true;
        status('Erzeuge neues MP4. Original und Aufnahmen bleiben erhalten …');
        try {
          await MAG.api('POST', '/api/video/bake', { message: message.id, video: sourceFile,
            audio: selected.file, mode: bakeMode.value });
          status('Neues MP4 gespeichert. Original und Mikrofonaufnahme bleiben erhalten.');
        } catch (err) { status('Einbrennen fehlgeschlagen: ' + err.message, true); }
        finally { bake.disabled = false; }
      } });
    if (takes.length) acts.append(bakeMode, bake);
    box.append(acts, announce);
    return box;
  }
  MAG.voiceover = { player, supportedMime, formatOf, micError };
})();

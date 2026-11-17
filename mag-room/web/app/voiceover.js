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
    let activeFile = (message.voiceover_active ?? message.model3d?.voiceover_active) || '';
    let previewing = false, posterFrame = '', audioContext = null, syncFrame = 0;
    const gains = { original: 0.55, voice: 1, melody: 0.55, effects: 0.85,
      ...(message.voiceover_gains || message.model3d?.voiceover_gains || {}) };
    const gainNodes = new Map();
    const video = el('video', { class: klass, controls: true, preload: 'metadata',
      playsInline: true, src: url(tracks.baked?.file === activeFile ? tracks.baked : source) });
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
    const songRemove = el('button', { type: 'button', class: 'icon-btn voice-remove',
      'aria-label': 'Musikdatei entfernen', title: 'Musikdatei entfernen',
      html: MAG.icons.x, hidden: true });
    // Compact two-handle timeline: the chosen song is a visible clip on
    // the video clock. A separate source in-point handle picks the song start.
    const displayTime = seconds => {
      const n = Math.max(0,Math.round(Number(seconds || 0) * 10) / 10);
      return String(Math.floor(n / 60)).padStart(2,'0') + ':' +
        String(Math.floor(n % 60)).padStart(2,'0');
    };
    const editor = { at:0, from:0, end:0, dirty:false };
    const timelineClock = el('span', { class:'voice-time',text:'00:00 / 00:00' });
    const playhead = el('span', { class:'voice-playhead','aria-hidden':'true' });
    const clip = el('div', { class:'voice-clip', title:'Melodie auf der Videozeitleiste verschieben' });
    const leftGrip = el('button', {type:'button',class:'voice-grip left','aria-label':'Clip-Anfang ziehen',
      title:'Clip-Anfang verschieben'});
    const rightGrip = el('button', {type:'button',class:'voice-grip right','aria-label':'Clip-Ende ziehen',
      title:'Clip-Ende verschieben'});
    clip.append(leftGrip,el('span',{text:'Melodie'}),rightGrip);
    const melodyTrack = el('div',{class:'voice-track melody-track'},clip);
    const sourceGrip = el('button',{type:'button',class:'voice-source-grip',
      'aria-label':'Song-Start ziehen',title:'Startpunkt im Song verschieben'});
    const sourceTrack = el('div',{class:'voice-track source-track'},sourceGrip);
    const ruler = el('div',{class:'voice-ruler'},
      el('span',{text:'00:00'}),timelineClock,el('span',{class:'voice-duration',text:'00:00'}));
    const timeline = el('div',{class:'voice-timeline',role:'group',
      'aria-label':'Schnittzeitleiste mit Videoclip, Melodie und Song-Start'},
      ruler,
      el('div',{class:'voice-track-row'},el('small',{text:'Video'}),
        el('div',{class:'voice-track video-track'},el('span',{class:'voice-video-span'}),playhead)),
      el('div',{class:'voice-track-row'},el('small',{text:'Melodie'}),melodyTrack),
      el('div',{class:'voice-track-row'},el('small',{text:'Song'}),sourceTrack));
    const videoLength = () => Number.isFinite(video.duration) && video.duration > 0
      ? video.duration : Math.max(1,Number(message.model3d?.total_seconds || message.model3d?.seconds || 5));
    const songLength = () => Math.max(.1,Number(tracks.melody?.duration || 1));
    const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
    function paintTimeline() {
      const total = videoLength(), sourceTotal = songLength();
      const at = editor.dirty ? editor.at : Number(tracks.melody?.voiceover_at || 0);
      const from = editor.dirty ? editor.from : Number(tracks.melody?.voiceover_from || 0);
      const until = editor.dirty ? editor.end : Number(tracks.melody?.voiceover_end || 0);
      const end = until > at ? until : Math.min(total,at + Math.max(.05,sourceTotal - from));
      clip.style.left = (100 * at / total) + '%';
      clip.style.width = (100 * Math.max(.05,end - at) / total) + '%';
      sourceGrip.style.left = (100 * from / sourceTotal) + '%';
      playhead.style.left = (100 * clamp(video.currentTime || 0,0,total) / total) + '%';
      timelineClock.textContent = displayTime(video.currentTime) + ' / ' + displayTime(total);
      timeline.querySelector('.voice-duration').textContent = displayTime(total);
      leftGrip.title = 'Clip-Anfang ' + displayTime(at);
      rightGrip.title = 'Clip-Ende ' + displayTime(end);
      sourceGrip.title = 'Song-Start ' + displayTime(from);
      clip.setAttribute('aria-label','Melodie ' + displayTime(at) + ' bis ' + displayTime(end));
      timeline.hidden = !tracks.melody;
    }
    function moveClip(e,part) {
      if (!owner || !tracks.melody || busy || recorder?.state === 'recording') return;
      if (e.type === 'pointerdown' && e.button !== undefined && e.button !== 0) return;
      const total = videoLength(), sourceTotal = songLength();
      const oldAt = Number(tracks.melody.voiceover_at || 0);
      const oldFrom = Number(tracks.melody.voiceover_from || 0);
      const oldEnd = Number(tracks.melody.voiceover_end || 0) ||
        Math.min(total,oldAt + Math.max(.05,sourceTotal - oldFrom));
      editor.at = oldAt; editor.from = oldFrom; editor.end = oldEnd; editor.dirty = true;
      const area = part === 'source' ? sourceTrack : melodyTrack;
      const rect = area.getBoundingClientRect();
      if (!rect.width) {editor.dirty=false;return;}
      const initialX = e.clientX, initial = {...editor};
      const changed = ev => {
        const d = ((ev.clientX - initialX) / rect.width) * (part === 'source' ? sourceTotal : total);
        if (part === 'start') {
          editor.at = clamp(initial.at + d,0,editor.end-.05);
          editor.from = clamp(initial.from + (editor.at - initial.at),0,sourceTotal-.05);
        } else if (part === 'end')
          editor.end = clamp(initial.end + d,editor.at+.05,total);
        else if (part === 'source') {
          editor.from = clamp(initial.from + d,0,sourceTotal-.05);
          editor.end = Math.min(editor.end, editor.at + sourceTotal - editor.from);
        }
        else {
          const delta = clamp(d,-initial.at,total-initial.end);
          editor.at = initial.at + delta;editor.end = initial.end + delta;
        }
        paintTimeline();
      };
      const finish = ev => {
        area.removeEventListener('pointermove',changed);
        area.removeEventListener('pointerup',finish);
        area.removeEventListener('pointercancel',cancel);
        try {area.releasePointerCapture?.(ev.pointerId);} catch (_) {}
        saveCue();
      };
      const cancel = ev => {
        area.removeEventListener('pointermove',changed);
        area.removeEventListener('pointerup',finish);
        area.removeEventListener('pointercancel',cancel);
        editor.dirty=false;paintTimeline();
      };
      try {area.setPointerCapture?.(e.pointerId);} catch (_) {}
      area.addEventListener('pointermove',changed);
      area.addEventListener('pointerup',finish);
      area.addEventListener('pointercancel',cancel);
      e.preventDefault();
    }
    leftGrip.addEventListener('pointerdown',e=>{e.stopPropagation();moveClip(e,'start')});
    rightGrip.addEventListener('pointerdown',e=>{e.stopPropagation();moveClip(e,'end')});
    clip.addEventListener('pointerdown',e=>moveClip(e,'move'));
    sourceGrip.addEventListener('pointerdown',e=>moveClip(e,'source'));
    for (const [node,key] of [[leftGrip,'start'],[rightGrip,'end'],[sourceGrip,'source']])
      node.addEventListener('keydown',e=>{
        if (!owner || !['ArrowLeft','ArrowRight'].includes(e.key) || !tracks.melody) return;
        e.preventDefault();
        editor.at = Number(tracks.melody.voiceover_at || 0);
        editor.from = Number(tracks.melody.voiceover_from || 0);
        editor.end = Number(tracks.melody.voiceover_end || 0) || Math.min(videoLength(),editor.at+songLength()-editor.from);
        const d = e.key === 'ArrowRight' ? .1 : -.1;
        if (key === 'start') editor.at = clamp(editor.at+d,0,editor.end-.05);
        if (key === 'end') editor.end = clamp(editor.end+d,editor.at+.05,videoLength());
        if (key === 'source') { editor.from = clamp(editor.from+d,0,songLength()-.05);
          editor.end = Math.min(editor.end,editor.at+songLength()-editor.from); }
        editor.dirty=true;paintTimeline();saveCue();
      });
    timeline.querySelector('.video-track').addEventListener('click',e=>{
      const bounds=e.currentTarget.getBoundingClientRect();
      if(bounds.width) video.currentTime = clamp((e.clientX-bounds.left)/bounds.width,0,1)*videoLength();
    });
    const discardButton = el('button', { type: 'button', class: 'icon-btn voice-discard',
      'aria-label': 'Aufnahme verwerfen', title: 'Aufnahme verwerfen',
      html: MAG.icons.x, hidden: true });
    const preview = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Mit Tonspuren anhören', title: 'Mit Tonspuren anhören',
      html: MAG.icons.speak, hidden: true });
    const bake = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Finale Videoversion erstellen', title: 'Finale Videoversion erstellen',
      html: MAG.icons.film, hidden: true });
    const restore = el('button', { type: 'button', class: 'icon-btn',
      'aria-label': 'Originalvideo wiederherstellen', title: 'Originalvideo mit Originalton wiederherstellen',
      html: MAG.icons.reset, hidden: true });
    // Finished MP4 is the active video; the native video controls handle download.
    const actions = el('div', { class: 'voice-actions', role: 'group',
      'aria-label': 'Tonspuren bearbeiten' },
      owner ? mic : null, owner ? discardButton : null, preview, owner ? bake : null, owner ? restore : null);
    const toolbar = el('div', { class: 'voice-controls' }, laneBar, actions);
    const melodyRow = el('div', { class: 'voice-melody', hidden: true },
      owner ? songUpload : null, owner ? songFile : null,
      songName, owner ? songRemove : null);
    const levels = el('div', { class: 'voice-levels', role: 'group',
      'aria-label': 'Lautstärke der Tonspuren' });
    const gainControls = new Map();
    for (const [key, label] of [['original','Original'], ...LANES]) {
      const input = el('input', { type: 'range', min: '0', max: '1', step: '0.01',
        value: String(gains[key]), 'aria-label': label + ' Lautstärke' });
      input.addEventListener('input', () => {
        gains[key] = Math.max(0, Math.min(1, Number(input.value) || 0));
        sync();
      });
      gainControls.set(key, input);
      levels.append(el('label', { class: 'voice-level' },
        el('span', { text: label }), input));
    }
    const footer = el('div', { class: 'voice-footer' }, announce);
    const audios = Object.fromEntries(LANES.map(([id]) =>
      [id, el('audio', { preload: 'metadata', hidden: true,
        dataset: { lane: id } })]));
    const master = el('audio', { preload: 'metadata', hidden: true, src: url(source),
      dataset: { lane: 'original' } });
    const audiosBox = el('div', { hidden: true }, master, ...Object.values(audios));
    box.append(audiosBox, toolbar, melodyRow, timeline, levels, footer);
    // One stable audio element per lane, shared with the original video
    // timeline. The video controls remain native and work on iOS/Android.
    // WebAudio gains provide click-free 10ms ramps on Android/iOS. The
    // fallback remains per-element volume when WebAudio is unavailable.
    function outputGain(key, audio, value) {
      const node = gainNodes.get(key);
      if (node && audioContext) {
        const now = audioContext.currentTime;
        node.gain.cancelScheduledValues(now);
        node.gain.setValueAtTime(node.gain.value, now);
        node.gain.linearRampToValueAtTime(Math.max(0, Math.min(1, value)), now + 0.01);
      } else audio.volume = Math.max(0, Math.min(1, value));
    }
    function audioGraph() {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context || audioContext) return;
      try {
        audioContext = new Context();
        for (const [key, audio] of [['original',master], ...LANES.map(([k])=>[k,audios[k]])]) {
          const source = audioContext.createMediaElementSource(audio);
          const gain = audioContext.createGain();
          gain.gain.value = 0;
          source.connect(gain).connect(audioContext.destination);
          gainNodes.set(key,gain);
        }
        audioContext.resume?.().catch(()=>{});
      } catch (_) { audioContext = null; gainNodes.clear(); }
    }
    function tick() {
      syncFrame = 0;
      if (!previewing || video.paused || !box.isConnected) return;
      sync();
      syncFrame = requestAnimationFrame(tick);
    }
    function startTick() {
      if (!syncFrame && typeof requestAnimationFrame === 'function')
        syncFrame = requestAnimationFrame(tick);
    }
    function stopTick() {
      if (syncFrame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(syncFrame);
      syncFrame = 0;
    }
    function sync(hardSeek = false) {
      if (recorder?.state === 'recording') return;
      if (!previewing) return;
      video.muted = true;  // original soundtrack comes only from its own level
      const sample = [['original', master, {duration:video.duration || Infinity,voiceover_at:0,voiceover_from:0}],
        ...LANES.map(([key]) => [key,audios[key],tracks[key]])];
      for (const [key,audio,take] of sample) {
        if (!take) { try { audio.pause(); } catch (_) {} continue; }
        if (key !== 'original' && audio.dataset.file !== take.file) {
          audio.dataset.file = take.file;
          audio.src = url(take);
        }
        const at = key === 'melody' ? Number(take.voiceover_at || 0) : 0;
        const from = key === 'melody' ? Number(take.voiceover_from || 0) : 0;
        const position = Math.max(0, video.currentTime - at + from);
        const length = Number(take.duration || Infinity);
        const trimEnd = key === 'melody' && Number(take.voiceover_end || 0) > 0
          ? Number(take.voiceover_end) : Infinity;
        const audible = video.currentTime >= at && video.currentTime < trimEnd && position < length;
        const cueEntry = key === 'melody' && audible && audio.dataset.audible !== '1';
        audio.dataset.audible = audible ? '1' : '0';
        audio.muted = false;
        const remaining = Math.min(length - position, trimEnd - video.currentTime, (Number.isFinite(video.duration)
          ? video.duration - video.currentTime : Infinity));
        const fadeIn = key === 'melody' ? Math.min(1, Math.max(0,(video.currentTime - at) / 0.01)) : 1;
        const fadeOut = Math.min(1,Math.max(0,remaining / 0.01));
        outputGain(key,audio,audible && !video.paused && !video.ended
          ? gains[key] * fadeIn * fadeOut : 0);
        audio.playbackRate = video.playbackRate;
        const drift = (audio.currentTime || 0) - position;
        try {
          if (hardSeek || cueEntry || Math.abs(drift) > 1.5) audio.currentTime = position;
          else if (Math.abs(drift) > 0.04)
            audio.playbackRate = Math.max(.5,Math.min(4,video.playbackRate * (drift > 0 ? .98 : 1.02)));
        } catch (_) {}
        if (video.paused || video.ended) { try { audio.pause(); } catch (_) {} }
        // Never call play() from timeupdate/sync: one play per lane is
        // initiated by the explicit preview gesture below.
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
      levels.hidden = !have() && !activeFile;
      restore.hidden = !activeFile || !owner;
      restore.disabled = busy;
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
      paintTimeline();
      songRemove.hidden = !tracks.melody;
      songRemove.disabled = busy || recorder?.state === 'recording';
      songUpload.disabled = busy || recorder?.state === 'recording';

      songName.textContent = tracks.melody?.name || 'Musikdatei wählen';
      songName.title = tracks.melody?.name || 'Musikdatei hochladen';
      if (tracks.melody) songUpload.title = 'Musikdatei wählen oder ersetzen';
      discardButton.hidden = !stream && recorder?.state !== 'recording';
      discardButton.title = recorder?.state === 'recording' ? 'Aufnahme verwerfen' : 'Mikrofon schließen';
      discardButton.setAttribute('aria-label', discardButton.title);

    }
    // Called by the chat's generic preservation path after an SSE update.
    // Never recreate or unmount the player during an active recording.
    function chooseVersion(nextFile) {
      activeFile = nextFile;
      const file = activeFile && tracks.baked?.file === activeFile ? tracks.baked : source;
      if (video.getAttribute('src') !== url(file)) {
        previewing = false;
        stopTick();
        video.pause();
        for (const a of [master,...Object.values(audios)]) { try { a.pause(); } catch (_) {} }
        video.muted = false;
        video.src = url(file);
        video.load?.();
      }
      paint();
    }
    box._voiceoverRefresh = (m) => {
      tracks = latest(m, original);
      chooseVersion((m.voiceover_active ?? m.model3d?.voiceover_active) || '');
      if (recorder?.state !== 'recording') sync();
    };
    for (const event of ['play', 'pause', 'seeked', 'timeupdate',
      'ratechange', 'volumechange', 'ended']) video.addEventListener(event, () => sync(false));
    video.addEventListener('seeking', () => sync(true));
    video.addEventListener('timeupdate',paintTimeline);
    video.addEventListener('loadedmetadata',paintTimeline);
    video.addEventListener('pause', stopTick);
    video.addEventListener('ended', stopTick);
    video.addEventListener('play', () => {
      if (!previewing) return;
      startTick();
      for (const [key,audio] of [['original',master],...LANES.map(([k])=>[k,audios[k]])]) {
        if (key !== 'original' && !tracks[key]) continue;
        // Resume once per lane when native play is tapped after a pause.
        if (audio.paused) audio.play().catch(() => {});
      }
    });
    // Android PWA often releases the last decoded frame when the file picker
    // backgrounds Chrome. A small in-memory poster survives that transition.
    document.addEventListener('visibilitychange', () => {
      if (!box.isConnected || document.visibilityState !== 'visible' || !posterFrame) return;
      video.poster = posterFrame;
      try { const position = video.currentTime; video.currentTime = position; } catch (_) {}
    });
    preview.addEventListener('click', () => {
      if (!have()) return;
      video.pause();
      video.currentTime = 0;
      previewing = true;
      startTick();
      audioGraph();
      video.muted = true;
      if (audioContext) audioContext.resume?.().catch(()=>{});
      for (const [key,audio,take] of [['original',master, {voiceover_from:0}],
        ...LANES.map(([id]) => [id,audios[id],tracks[id]])]) {
        if (!take) continue;
        if (key !== 'original' && audio.dataset.file !== take.file) {
          audio.dataset.file = take.file;
          audio.src = url(take);
        }
        audio.currentTime = key === 'melody' ? Number(take.voiceover_from || 0) : 0;
        audio.dataset.audible = '0';
        audio.muted = false;
        outputGain(key,audio,key === 'melody' && Number(take.voiceover_at || 0) > 0
          ? 0 : gains[key]);
        // Exactly one call for each selected lane, inside the user gesture.
        Promise.resolve(audio.play()).catch(err => {
          if (err?.name === 'AbortError' || /interrupted by a call to pause/i.test(err?.message || '')) return;
          if (previewing && !video.paused) status('Bitte Vorschau erneut antippen.', true);
        });
      }
      const start = video.play();
      Promise.resolve(start).catch((err) => {
        // pause() during a source switch, picker or stop is normal in Chrome.
        if (err?.name === 'AbortError' || /interrupted by a call to pause/i.test(err?.message || '')) return;
        if (!previewing || video.paused) return;
        for (const audio of [master,...Object.values(audios)]) { try { audio.pause(); } catch (_) {} }
        status('Video konnte nicht gestartet werden: ' + (err?.message || 'Unbekannter Fehler'), true);
      });
    });
    const compatible = !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== 'undefined' && window.isSecureContext;
    if (!owner) { paint(); return box; }

    if (!compatible) {
      mic.disabled = true;
      status('Mikrofon benötigt HTTPS und MediaRecorder.', true);
    }

    function freezePoster() {
      if (!video.videoWidth || !video.videoHeight) return;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(360, video.videoWidth);
        canvas.height = Math.max(1, Math.round(canvas.width * video.videoHeight / video.videoWidth));
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        posterFrame = canvas.toDataURL('image/jpeg', .75);
        video.poster = posterFrame;
      } catch (_) {} // fail open for protected/missing frames
    }
    songUpload.addEventListener('click', () => {
      if (busy || recorder?.state === 'recording') return;
      freezePoster();
      songFile.click();
    });
    songFile.addEventListener('change', async () => {
      const file = songFile.files?.[0];
      if (!file || busy || recorder?.state === 'recording') return;
      busy = true; paint(); status('Musikdatei wird geladen …');
      try {
        // Android content:// files may become zero-size when the input is
        // cleared early. Materialize the bytes BEFORE resetting the picker.
        const bytes = await file.arrayBuffer();
        const blob = new Blob([bytes], { type: file.type || 'application/octet-stream' });
        songFile.value = '';
        if (blob.size < 128 || blob.size > 32 * 1024 * 1024)
          throw new Error('Melodie muss zwischen 128 Byte und 32 MB groß sein.');
        const uploaded = await MAG.api('POST', '/api/video/voiceover?message=' +
          encodeURIComponent(message.id) + '&video=' + encodeURIComponent(original) +
          '&lane=melody&name=' + encodeURIComponent(file.name), blob);
        tracks.melody = uploaded;
        status('Melodie geladen.');
      } catch (e) { status('Musik-Upload fehlgeschlagen: ' + e.message, true); }
      finally { songFile.value = ''; busy = false; paint(); }
    });
    songRemove.addEventListener('click', async () => {
      const take = tracks.melody;
      if (!take || busy || recorder?.state === 'recording') return;
      busy = true; paint();
      try {
        await MAG.api('DELETE','/api/video/voiceover', {
          message: message.id,video:original,file:take.file
        });
        try { audios.melody.pause(); } catch (_) {}
        audios.melody.removeAttribute('src');
        delete audios.melody.dataset.file;
        tracks.melody = null;
        status('Melodie entfernt.');
      } catch (e) { status(e.message,true); }
      finally { busy = false; paint(); }
    });
    restore.addEventListener('click', async () => {
      if (busy || !activeFile) return;
      busy = true; paint();
      try {
        await MAG.api('POST','/api/video/restore',{message:message.id,video:original});
        chooseVersion('');
        status('Originalvideo mit Originalton wiederhergestellt.');
      } catch (e) { status(e.message,true); }
      finally { busy = false; paint(); }
    });
    async function saveCue() {
      const take = tracks.melody;
      if (!take || busy || recorder?.state === 'recording') { editor.dirty=false;return; }
      const at = editor.at, from = editor.from, end = editor.end;
      if (!Number.isFinite(at) || !Number.isFinite(from) || !Number.isFinite(end) ||
          at < 0 || from < 0 || end <= at || end > videoLength() ||
          from >= songLength()) {
        editor.dirty=false;paintTimeline();
        return status('Clip-Grenzen müssen innerhalb von Video und Song liegen.', true);
      }
      busy = true; paint();
      try {
        const saved = await MAG.api('POST', '/api/video/voiceover/cue', {
          message: message.id, video: original, file: take.file, at, from, end
        });
        tracks.melody = { ...take, voiceover_at: saved.at, voiceover_from: saved.from,
          voiceover_end: saved.end };
        status('Clip gespeichert.');
        sync(true);
      } catch (e) {
        status('Clip konnte nicht gespeichert werden: ' + e.message, true);
      } finally { editor.dirty=false;busy=false;paint(); }
    }

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
        for (const audio of [master,...Object.values(audios)]) { try { audio.pause(); } catch (_) {} }
        savedMute = previewing ? false : video.muted;
        previewing = false;
        stopTick();
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
      status('Finale Videoversion wird erstellt …');
      try {
        const result = await MAG.api('POST', '/api/video/bake', {
          message: message.id, video: original, lanes, gains: { ...gains }, mode: 'mix'
        });
        tracks.baked = result.video;
        tracks.mix = result.mix;
        chooseVersion(result.video.file);
        status('Finales Video aktiv. Original jederzeit wiederherstellbar.');
      } catch (e) { status('Baken fehlgeschlagen: ' + e.message, true); }
      finally { busy = false; paint(); }
    });
    paint();
    return box;
  }

  MAG.voiceover = { player, supportedMime, formatOf, micError, latest, LANES };
})();

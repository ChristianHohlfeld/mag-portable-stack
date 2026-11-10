// The composer: text, attachments, photo-to-3D with its quality, dictation.
'use strict';
(function () {
  const { MAG } = window, { el, $ } = MAG;
  const coarse = matchMedia('(pointer: coarse)').matches;
  let input, sendBtn, stagedBox, micBtn;
  let staged = [];           // {att, url} uploaded, not sent yet
  let uploading = 0, sending = false;
  let mode3d = false;
  let quality = localStorage.getItem('mag-3d-quality') || 'normal';

  // One line to about seven; beyond that the text scrolls (without a bar).
  function resize() {
    const style = getComputedStyle(input);
    const frame = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const max = Math.round((parseFloat(style.lineHeight) || 22) * 7 + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + frame);
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight + frame, max) + 'px';
  }
  function refresh() {
    const has3dImage = staged.some((s) => s.att.kind === 'image');
    sendBtn.disabled = sending || uploading > 0 || (mode3d ? !has3dImage : !input.value.trim() && !staged.length);
    sendBtn.setAttribute('aria-label', mode3d ? '3D-Modell erstellen' : 'Senden');
    sendBtn.innerHTML = mode3d ? MAG.icons.cube : MAG.icons.send;
    input.placeholder = mode3d ? 'Foto zu 3D' : 'Nachricht';
  }
  function paintStaged() {
    stagedBox.replaceChildren();
    for (const s of staged) {
      stagedBox.append(el('div', { class: 'staged' },
        s.att.kind === 'image' ? el('img', { src: s.url, alt: '' }) : MAG.icon('file'),
        el('span', { class: 'nm', text: s.att.name }),
        el('button', { class: 'x', 'aria-label': 'Entfernen', html: MAG.icons.x, onclick: () => { staged = staged.filter((x) => x !== s); paintStaged(); } })));
    }
    if (uploading) stagedBox.append(el('div', { class: 'staged' }, el('span', { class: 'spin' }), el('span', { class: 'nm', text: 'Lädt hoch …' })));
    if (mode3d) {
      const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Qualität' });
      for (const [key, label] of [['fast', 'Schnell'], ['normal', 'Normal'], ['high', 'Hoch']])
        seg.append(el('button', { class: key === quality ? 'on' : '', text: label, onclick: () => { quality = key; localStorage.setItem('mag-3d-quality', key); paintStaged(); } }));
      stagedBox.append(el('div', { class: 'staged' }, MAG.icon('cube'), el('span', { text: 'Foto zu 3D' }), seg,
        el('button', { class: 'x', 'aria-label': '3D abbrechen', html: MAG.icons.x, onclick: () => { mode3d = false; paintStaged(); } })));
    }
    refresh();
  }

  async function addFiles(files) {
    for (const file of files) {
      if (file.size > 64 * 1024 * 1024) { MAG.toast(file.name + ' ist größer als 64 MB', true); continue; }
      if (!file.size) continue;
      uploading++;
      paintStaged();
      try {
        const att = await MAG.api('POST', '/api/upload?name=' + encodeURIComponent(file.name || 'datei'), file);
        staged.push({ att, url: '/api/file/' + encodeURIComponent(att.file) });
      } catch (err) {
        MAG.toast('Upload fehlgeschlagen: ' + err.message, true);
      }
      uploading--;
      paintStaged();
    }
  }
  function pick(accept, capture, then) {
    const chooser = el('input', { type: 'file', accept, multiple: !capture && !then, hidden: true });
    if (capture) chooser.capture = 'environment';
    chooser.addEventListener('change', async () => { await addFiles([...chooser.files]); chooser.remove(); then?.(); });
    document.body.append(chooser);
    chooser.click();
  }

  async function send() {
    if (sendBtn.disabled) return;
    const text = input.value.trim(), body = { text, attachments: staged.map((s) => s.att.file), client_id: crypto.randomUUID?.() || String(Math.random()) };
    if (mode3d) Object.assign(body, { mode: '3d', quality, text: '' });
    sending = true;
    refresh();
    try {
      await MAG.api('POST', '/api/message', body);
      if (!mode3d) { input.value = ''; sessionStorage.removeItem('mag-draft'); }
      staged = [];
      mode3d = false;
      resize();
      MAG.scrollToEnd();
      if (coarse) input.blur();
    } catch (err) {
      MAG.toast(err.message, true);
    }
    sending = false;
    paintStaged();
  }

  // ---- dictation: record, transcribe on the server (whisper) --------------
  let recorder = null, chunks = [];
  async function toggleMic() {
    if (recorder) return recorder.stop();
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return MAG.toast('Dieses Gerät kann hier nicht aufnehmen', true);
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { return MAG.toast('Kein Zugriff auf das Mikrofon', true); }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      recorder = null;
      micBtn.classList.remove('rec');
      if (blob.size < 800) return;
      micBtn.disabled = true;
      try {
        const out = await MAG.api('POST', '/api/stt', blob);
        if (out.text) { input.value = (input.value ? input.value.replace(/\s*$/, ' ') : '') + out.text; resize(); refresh(); }
        else MAG.toast('Nichts verstanden');
      } catch (err) { MAG.toast(err.message, true); }
      micBtn.disabled = false;
    };
    recorder.start();
    micBtn.classList.add('rec');
  }

  document.addEventListener('DOMContentLoaded', () => {
    input = $('#input'); sendBtn = $('#send'); stagedBox = $('#staged'); micBtn = $('#mic');
    input.value = sessionStorage.getItem('mag-draft') || '';
    input.addEventListener('input', () => { resize(); refresh(); sessionStorage.setItem('mag-draft', input.value); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !coarse) { e.preventDefault(); send(); }
    });
    input.addEventListener('paste', (e) => {
      const files = [...(e.clipboardData?.files || [])];
      if (files.length) { e.preventDefault(); addFiles(files); }
    });
    sendBtn.addEventListener('click', send);
    micBtn.addEventListener('click', toggleMic);
    $('#attach').addEventListener('click', (e) => MAG.menu(e.currentTarget, [
      { icon: 'image', label: 'Datei oder Foto', run: () => pick('', false) },
      coarse ? { icon: 'camera', label: 'Kamera', run: () => pick('image/*', true) } : null,
      { icon: 'cube', label: 'Foto zu 3D', run: () => { mode3d = true; paintStaged(); if (!staged.some((s) => s.att.kind === 'image')) pick('image/png,image/jpeg,image/webp', false, paintStaged); } },
    ]));
    // Files dropped anywhere on the page are attachments, never a navigation.
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer?.files?.length) addFiles([...e.dataTransfer.files]); });
    resize();
    paintStaged();
  });
  MAG.on('settings', () => { if (micBtn) micBtn.hidden = !MAG.state.features.stt; });
})();

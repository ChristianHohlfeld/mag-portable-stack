// The composer: text, attachments, photo-to-3D with its quality. Dictation
// writes into the same field (dictate.js).
'use strict';
(function () {
  const { MAG } = window, { el, $ } = MAG;
  const coarse = matchMedia('(pointer: coarse)').matches;
  let input, sendBtn, stagedBox;
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
    MAG.mentions?.paint();
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
      // The photo comes from the gallery or straight from the camera.
      if (!staged.some((s) => s.att.kind === 'image') && !uploading)
        stagedBox.append(el('div', { class: 'staged source' },
          el('button', { class: 'pill', onclick: () => pick('image/png,image/jpeg,image/webp', false, paintStaged) }, MAG.icon('image'), el('b', { text: 'Galerie' })),
          el('button', { class: 'pill', onclick: () => shoot(paintStaged) }, MAG.icon('camera'), el('b', { text: 'Kamera' }))));
      // A photo with several things in it: Chris says which one is meant.
      const photo = staged.find((s) => s.att.kind === 'image');
      if (photo && !photo.cut && !uploading)
        stagedBox.append(el('div', { class: 'staged source' },
          el('button', { class: 'pill', onclick: () => choose(photo) }, MAG.icon('cube'), el('b', { text: 'Objekt wählen' }))));
    }
    refresh();
  }

  async function addFiles(files) {
    for (const file of files) {
      if (file.size > 64 * 1024 * 1024) { MAG.toast(file.name + ' ist größer als 64 MB', true); continue; }
      if (!file.size) continue;
      uploading++;
      paintStaged();
      // A photo that was just taken may still be written by the phone: the room
      // says when a picture ends in its middle, and the file is read again
      // twice before it is sent as it is.
      for (let again = 0; ; again++) {
        try {
          const att = await MAG.api('POST', '/api/upload?name=' + encodeURIComponent(file.name || 'datei') + (again === 2 ? '&force=1' : ''), file);
          if (att.cut) MAG.toast((file.name || 'Das Bild') + ' ist unvollständig: die Datei endet mitten im Bild', true);
          staged.push({ att, url: '/api/file/' + encodeURIComponent(att.file) });
        } catch (err) {
          if (err.data?.code === 'truncated' && again < 2) { await new Promise((done) => setTimeout(done, 1500 * (again + 1))); continue; }
          MAG.toast('Upload fehlgeschlagen: ' + err.message, true);
        }
        break;
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

  // A photo from the camera. A phone opens its own camera; a desktop shows the
  // webcam with a release button. Where neither works, a file is chosen.
  async function shoot(then) {
    if (coarse || !navigator.mediaDevices?.getUserMedia) return pick('image/*', true, then);
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } } });
    } catch {
      return pick('image/png,image/jpeg,image/webp', false, then);
    }
    const video = el('video', { autoplay: true, playsinline: true, muted: true });
    video.srcObject = stream;
    const sheet = MAG.sheet('Kamera', (content, _, close) => {
      content.append(el('div', { class: 'shoot' }, video,
        el('button', { class: 'pill', onclick: () => {
          if (!video.videoWidth) return;
          const canvas = el('canvas', { width: video.videoWidth, height: video.videoHeight });
          canvas.getContext('2d').drawImage(video, 0, 0);
          canvas.toBlob(async (blob) => {
            close();
            if (blob) await addFiles([new File([blob], 'kamera-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.jpg', { type: 'image/jpeg' })]);
            then?.();
          }, 'image/jpeg', 0.92);
        } }, MAG.icon('camera'), el('b', { text: 'Auslösen' }))));
    });
    sheet.addEventListener('closed', () => stream.getTracks().forEach((t) => t.stop()));
  }

  // ---- which object of the photo ------------------------------------------
  const picture = (src) => new Promise((done, fail) => { const i = new Image(); i.onload = () => done(i); i.onerror = () => fail(new Error('Bild lässt sich nicht lesen')); i.src = src; });
  // The photo with everything but one object cut away (transparent), no
  // larger than the object: a canvas.
  async function cutOut(image, mask) {
    const scale = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
    const w = Math.round(image.naturalWidth * scale), h = Math.round(image.naturalHeight * scale);
    const shape = el('canvas', { width: w, height: h }), sc = shape.getContext('2d', { willReadFrequently: true });
    sc.drawImage(await picture('data:image/png;base64,' + mask.png), 0, 0, w, h);
    const px = sc.getImageData(0, 0, w, h);
    for (let i = 0; i < px.data.length; i += 4) px.data[i + 3] = px.data[i];
    sc.putImageData(px, 0, 0);
    const whole = el('canvas', { width: w, height: h }), wc = whole.getContext('2d');
    wc.drawImage(image, 0, 0, w, h);
    wc.globalCompositeOperation = 'destination-in';
    wc.drawImage(shape, 0, 0);
    const k = w / mask.width, pad = Math.round(0.04 * Math.max(w, h));
    const [l, t, r, b] = [Math.max(0, Math.floor(mask.box[0] * k) - pad), Math.max(0, Math.floor(mask.box[1] * k) - pad), Math.min(w, Math.ceil(mask.box[2] * k) + pad), Math.min(h, Math.ceil(mask.box[3] * k) + pad)];
    const out = el('canvas', { width: Math.max(1, r - l), height: Math.max(1, b - t) });
    out.getContext('2d').drawImage(whole, l, t, r - l, b - t, 0, 0, r - l, b - t);
    return out;
  }
  async function uploadCut(canvas, name) {
    const blob = await new Promise((done) => canvas.toBlob(done, 'image/png'));
    if (!blob) throw new Error('Das Objekt ließ sich nicht ausschneiden');
    const att = await MAG.api('POST', '/api/upload?name=' + encodeURIComponent(name), blob);
    return { att, url: '/api/file/' + encodeURIComponent(att.file), cut: true };
  }
  // The sheet: tap an object and see what was understood, or have every
  // object found and take those that are wanted, each as a model of its own.
  function choose(photo) {
    const base = (photo.att.name || 'foto').replace(/\.[a-z0-9]+$/i, '');
    MAG.sheet('Objekt wählen', async (content, _, close) => {
      const view = el('div', { class: 'choose' }), img = el('img', { src: photo.url, alt: '' }), over = el('div', { class: 'cut' });
      const hint = el('p', { class: 'hint', text: 'Tippe auf das Objekt, aus dem das Modell werden soll, oder nenne es.' });
      const bar = el('div', { class: 'row' }), list = el('div', { class: 'objects' });
      // Or its name: a bot that sees finds it in the photo.
      const named = el('input', { type: 'text', placeholder: 'oder nennen: z. B. Waschbecken', enterkeyhint: 'search', autocomplete: 'off' });
      const byName = el('form', { class: 'name', onsubmit: (e) => { e.preventDefault(); if (named.value.trim() && !busy) { named.blur(); find({ name: named.value.trim() }, '»' + named.value.trim() + '« wird im Foto gesucht …'); } } },
        named, el('button', { class: 'pill', type: 'submit' }, el('b', { text: 'Suchen' })));
      view.append(img, over);
      content.append(hint, view, byName, bar, list);
      let busy = false, one = null, many = [], room = null;   // room: the photo's whole room is wanted, as one scene
      const say = (text) => { hint.textContent = text; };
      const paint = () => {
        bar.replaceChildren();
        if (busy) return bar.append(el('span', { class: 'spin' }));
        if (one) bar.append(el('button', { class: 'pill on', onclick: take }, MAG.icon('cube'), el('b', { text: 'Dieses Objekt' })));
        const wanted = many.filter((o) => o.on).length;
        if (wanted) bar.append(el('button', { class: 'pill on', onclick: takeAll }, MAG.icon('cube'),
          el('b', { text: room ? 'Szene aus ' + wanted + (wanted === 1 ? ' Objekt' : ' Objekten') + ' erstellen' : wanted === 1 ? '1 Modell erstellen' : wanted + ' Modelle erstellen' })));
        bar.append(el('button', { class: 'pill', onclick: () => all(false) }, el('b', { text: 'Alle einzeln' })));
        bar.append(el('button', { class: 'pill', onclick: () => all(true) }, el('b', { text: 'Ganze Szene' })));
      };
      const ask = async (body, wait) => {
        busy = true; paint(); say(wait);
        try { return await MAG.api('POST', '/api/3d/select', Object.assign({ file: photo.att.file }, body)); }
        catch (err) { say(err.message); return null; }
        finally { busy = false; paint(); }
      };
      const masks = (found) => found.masks.map((m) => Object.assign(m, { width: found.width }));
      async function find(where, wait) {
        const found = await ask(where, wait);
        if (!found) return;
        if (!found.masks.length) return say('Dort ist kein Objekt zu erkennen. Tippe noch einmal.');
        one = await cutOut(await picture(photo.url), masks(found)[0]);
        over.replaceChildren(el('img', { src: one.toDataURL(), alt: '', style: place(found.masks[0], found) }));
        view.classList.add('picked');
        say('Das wird das Modell. Tippe woanders hin, wenn etwas anderes gemeint ist.');
        paint();
      }
      img.addEventListener('click', (e) => {
        if (busy) return;
        const box = img.getBoundingClientRect();
        find({ x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height }, 'Das Objekt wird gesucht …');
      });
      // Where the cut lies on the photo, in per cent of it.
      function place(mask, found) {
        const pad = 0.04 * Math.max(found.width, found.height);
        const l = Math.max(0, mask.box[0] - pad), t = Math.max(0, mask.box[1] - pad), r = Math.min(found.width, mask.box[2] + pad), b = Math.min(found.height, mask.box[3] + pad);
        return `left:${100 * l / found.width}%;top:${100 * t / found.height}%;width:${100 * (r - l) / found.width}%;height:${100 * (b - t) / found.height}%`;
      }
      // Every object by itself; with `whole` also where each stands, for one scene of the photo's room.
      async function all(whole) {
        const found = await ask(whole ? { scene: true } : {}, 'Alle Objekte werden gesucht. Das dauert etwa eine halbe Minute …');
        if (!found) return;
        if (!found.masks.length) return say('Im Foto ist kein einzelnes Objekt zu erkennen.');
        const image = await picture(photo.url);
        many = [];
        room = whole && found.room ? found.room : null;
        // A scene has room for eight figures: the eight largest.
        for (const mask of masks(found).slice(0, room ? 8 : 99)) many.push({ canvas: await cutOut(image, mask), on: true, place: mask.place });
        one = null;
        over.replaceChildren();
        view.classList.remove('picked');
        list.replaceChildren(...many.map((o) => {
          const b = el('button', { class: 'object on', onclick: () => { o.on = !o.on; b.classList.toggle('on', o.on); paint(); } }, el('img', { src: o.canvas.toDataURL(), alt: '' }));
          return b;
        }));
        say(room ? 'Aus jedem markierten Objekt wird ein Modell, und alle zusammen werden der Raum aus dem Foto: jedes dort, wo es stand, einzeln beweglich. Tippe ab, was nicht dazugehört.'
          : 'Aus jedem markierten Objekt wird ein eigenes Modell. Tippe ab, was nicht gebraucht wird.');
        paint();
      }
      async function take() {
        busy = true; paint();
        try {
          const cut = await uploadCut(one, base + '-objekt.png');
          staged = staged.map((s) => s === photo ? cut : s);
          close();
          paintStaged();
        } catch (err) { busy = false; paint(); say(err.message); }
      }
      async function takeAll() {
        busy = true; paint();
        try {
          let n = 0;
          const wanted = many.filter((o) => o.on);
          const group = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, '0')).join('');
          for (const o of wanted) {
            const cut = await uploadCut(o.canvas, base + '-objekt-' + (++n) + '.png');
            const order = { text: '', attachments: [cut.att.file], mode: '3d', quality, chosen: true, client_id: crypto.randomUUID?.() || String(Math.random()) };
            if (room && o.place) order.scene = Object.assign({ group, index: n - 1, count: wanted.length, room }, o.place);
            await MAG.api('POST', '/api/message', order);
          }
          staged = staged.filter((s) => s !== photo);
          mode3d = false;
          close();
          paintStaged();
          MAG.scrollToEnd();
        } catch (err) { busy = false; paint(); say(err.message); }
      }
      paint();
    });
  }

  async function send() {
    if (sendBtn.disabled) return;
    // A dictation that is still running ends here, with its last words.
    if (MAG.dictation?.active()) await MAG.dictation.finish();
    const text = input.value.trim(), body = { text, attachments: staged.map((s) => s.att.file), client_id: crypto.randomUUID?.() || String(Math.random()) };
    // An object Chris chose himself is ordered as it is; a whole photo is looked at first (several things in it: one model each).
    if (mode3d) Object.assign(body, { mode: '3d', quality, text: '', chosen: staged.some((s) => s.cut) });
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

  document.addEventListener('DOMContentLoaded', () => {
    input = $('#input'); sendBtn = $('#send'); stagedBox = $('#staged');
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
    $('#attach').addEventListener('click', (e) => MAG.menu(e.currentTarget, [
      { icon: 'image', label: 'Datei oder Foto', run: () => pick('', false) },
      { icon: 'camera', label: 'Kamera', run: () => shoot() },
      { icon: 'cube', label: 'Foto zu 3D', run: () => { mode3d = true; paintStaged(); } },
    ]));
    // Files dropped anywhere on the page are attachments, never a navigation.
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer?.files?.length) addFiles([...e.dataTransfer.files]); });
    resize();
    paintStaged();
  });
})();

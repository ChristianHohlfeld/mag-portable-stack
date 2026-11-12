// The chat's files in one place: every upload, picture, model, scene, film,
// video and document of the chat, newest first, by kind, with a preview,
// a download and a way back to the message it is in. Several at once come
// as one ZIP. The files stay where they are; this is only a view of them.
'use strict';
(function () {
  const { MAG } = window, { el, $ } = MAG;
  const fileUrl = (f, extra) => '/api/file/' + encodeURIComponent(f.file) + (extra || '');
  const groups = ['Bilder', '3D', 'Szenen', 'Videos', 'Audio', 'Dokumente'];
  const when = (ts) => new Date(ts).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
  const ext = (f) => (f.name.split('.').pop() || '').toUpperCase();

  // What can be shown as a picture of itself (rendered once on the server:
  // a 3D file by Blender, a video's frame, a sound's waveform, a PDF's page).
  const MODEL = /\.(glb|gltf|stl|ply|obj|fbx|blend)$/i;
  const pictured = (f) => ['image', 'video', 'audio'].includes(f.kind) || MODEL.test(f.name) || /\.pdf$/i.test(f.name);
  const fallback = (f) => el('span', { class: 'ico-big' }, MAG.icon(f.group === 'Szenen' ? 'film' : MODEL.test(f.name) ? 'cube' : 'file'), el('b', { text: ext(f) }));
  const badge = (f) => f.kind === 'video' ? 'play' : f.kind === 'audio' ? 'speak' : MODEL.test(f.name) ? (f.group === 'Szenen' ? 'film' : 'cube') : null;
  // The first lines of a file whose content is text (a text file, a document), loaded when the tile comes into view.
  const reader = 'IntersectionObserver' in window ? new IntersectionObserver((seen) => {
    for (const e of seen) if (e.isIntersecting) { reader.unobserve(e.target); e.target._load?.(); }
  }, { rootMargin: '200px' }) : null;
  function excerpt(f) {
    const box = el('span', { class: 'excerpt' }, el('b', { text: ext(f) }));
    box._load = () => MAG.api('GET', '/api/file/' + encodeURIComponent(f.file) + '/excerpt').then((out) => {
      const text = (out.text || '').trim();
      box.replaceWith(text ? el('span', { class: 'excerpt' }, el('b', { text: ext(f) }), el('span', { class: 'lines', text: text.slice(0, 600) })) : fallback(f));
    }, () => box.replaceWith(fallback(f)));
    if (reader) reader.observe(box); else box._load();
    return box;
  }
  function thumb(f) {
    if (!pictured(f)) return excerpt(f);
    const mark = badge(f);
    return el('span', { class: 'pic' + (f.kind === 'audio' ? ' wave' : '') },
      el('img', { src: fileUrl(f, '/thumb'), alt: f.description || f.name, loading: 'lazy', decoding: 'async',
        onerror: (e) => e.target.closest('.pic')?.replaceWith(/\.pdf$/i.test(f.name) ? excerpt(f) : fallback(f)) }),
      mark ? el('span', { class: 'badge' }, MAG.icon(mark)) : null);
  }

  // One file large: the picture over the chat, a video or sound that plays,
  // a model that turns, a text to read; everything else opens in a tab.
  function preview(f, done) {
    if (f.kind === 'image') return MAG.lightbox(fileUrl(f), f.name, f.description, f.file);
    MAG.sheet(f.name, (content, _, close) => {
      let body = null;
      if (f.kind === 'video') body = el('video', { class: 'big', controls: true, autoplay: true, playsInline: true, src: fileUrl(f) });
      else if (f.kind === 'audio') body = el('audio', { controls: true, autoplay: true, src: fileUrl(f) });
      else if (/\.(glb|gltf)$/i.test(f.name)) {
        const canvas = el('canvas', {});
        body = el('div', { class: 'm3' }, el('div', { class: 'stage' }, canvas, el('span', { class: 'wait', text: 'Modell wird geladen …' }),
          el('div', { class: 'tools' },
            el('button', { 'aria-label': 'Ansicht zurücksetzen', title: 'Ansicht zurücksetzen', html: MAG.icons.reset, onclick: () => MAG.viewer3d.reset(canvas) }))));
        setTimeout(() => MAG.viewer3d.attach(canvas, fileUrl(f)), 0);
      } else if (/^text\/|json|xml|csv/.test(f.mime || '') && f.size < 512 * 1024) {
        body = el('pre', { class: 'text', text: 'Lädt …' });
        fetch(fileUrl(f)).then((r) => r.text()).then((t) => { body.textContent = t; }, () => { body.textContent = 'Nicht lesbar.'; });
      } else if (pictured(f)) {
        // A 3D file the viewer cannot turn (STL, PLY, BLEND …), a PDF's first page: its picture, large.
        body = el('img', { class: 'big-thumb', src: fileUrl(f, '/thumb'), alt: f.name, onerror: (e) => e.target.remove() });
      }
      if (!body || /\.pdf$/i.test(f.name) || /officedocument|opendocument/.test(f.mime || '')) {
        // What a document says, below its picture or on its own.
        const text = el('pre', { class: 'text', text: 'Lädt …' });
        MAG.api('GET', '/api/file/' + encodeURIComponent(f.file) + '/excerpt').then((out) => { if (out.text) text.textContent = out.text; else text.remove(); }, () => text.remove());
        body = body ? el('div', {}, body, text) : text;
      }
      const show = el('button', { class: 'pill', onclick: () => { close(); done?.(); if (!MAG.showMessage?.(f.message)) MAG.toast('Die Nachricht ist älter als der geladene Verlauf: oben „Ältere Nachrichten laden“.'); } }, MAG.icon('chev'), 'Im Chat zeigen');
      content.append(body || el('p', { class: 'fine', text: 'Für diese Datei gibt es hier keine Vorschau.' }),
        el('p', { class: 'fine', text: [f.title, MAG.name(f.from), when(f.ts), MAG.bytes(f.size)].filter(Boolean).join(' · ') }),
        el('div', { class: 'btns files-acts' },
          el('a', { class: 'pill', href: fileUrl(f, '?download=1'), download: f.name }, MAG.icon('down'), 'Herunterladen'),
          el('a', { class: 'pill', href: fileUrl(f, '?view=1'), target: '_blank', rel: 'noopener' }, MAG.icon('open'), 'In neuem Tab öffnen'),
          show));
    });
  }

  function open() {
    let all = [], group = '', query = '';
    MAG.sheet('Dateien', (content, sheet, close) => {
      const search = el('input', { type: 'search', placeholder: 'Suchen …', 'aria-label': 'Dateien suchen', value: query });
      const seg = el('div', { class: 'seg files-seg' }), grid = el('div', { class: 'files-grid' }), foot = el('p', { class: 'fine' });
      const zip = el('a', { class: 'pill', download: '' }, MAG.icon('down'), 'Als ZIP');
      sheet.classList.add('wide');
      content.append(el('div', { class: 'files-bar' }, search, zip), seg, grid, foot);
      const shown = () => all.filter((f) => (!group || f.group === group) &&
        (!query || (f.name + ' ' + (f.title || '') + ' ' + (f.description || '')).toLowerCase().includes(query)));
      const draw = () => {
        const counts = {};
        all.forEach((f) => { counts[f.group] = (counts[f.group] || 0) + 1; });
        seg.replaceChildren(...[['', 'Alle', all.length], ...groups.filter((g) => counts[g]).map((g) => [g, g, counts[g]])].map(([key, label, n]) =>
          el('button', { class: group === key ? 'on' : '', text: label + ' ' + n, onclick: () => { group = key; draw(); } })));
        const list = shown();
        grid.replaceChildren(...list.map((f) => el('button', { class: 'file-tile', title: f.name, onclick: () => preview(f, close) },
          el('span', { class: 'th' }, thumb(f)),
          el('span', { class: 'nm', text: f.title && f.group === 'Szenen' ? f.title : f.name }),
          el('small', { text: when(f.ts) + ' · ' + MAG.bytes(f.size) }))));
        const bytes = list.reduce((sum, f) => sum + (f.size || 0), 0);
        foot.textContent = list.length ? list.length + (list.length === 1 ? ' Datei · ' : ' Dateien · ') + MAG.bytes(bytes) : all.length ? 'Nichts gefunden.' : 'Im Chat liegen noch keine Dateien.';
        // The ZIP holds what is shown; everything when nothing is filtered.
        zip.hidden = !list.length;
        zip.href = '/api/files.zip' + (list.length === all.length ? '' : '?files=' + list.map((f) => encodeURIComponent(f.file)).join(','));
      };
      search.addEventListener('input', () => { query = search.value.trim().toLowerCase(); draw(); });
      foot.textContent = 'Lädt …';
      MAG.api('GET', '/api/files').then((out) => { all = out.files || []; draw(); }, (x) => { foot.textContent = x.message; });
    });
  }

  MAG.chatFiles = open;
  $('#files')?.addEventListener('click', open);
})();

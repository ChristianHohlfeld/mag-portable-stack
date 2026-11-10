// The timeline: every message in order, streamed text and live steps in the
// order they happen, and scrolling that follows only when you are at the end.
'use strict';
(function () {
  const { MAG } = window, { el, $, state: S } = MAG;
  let list, scroller, jump, activity, empty, olderBtn;
  let pinned = true, unread = 0;
  const nodes = new Map(); // message id -> article
  const dirty = new Set(); // "id:part" text parts waiting for a markdown pass

  // ---- markdown -----------------------------------------------------------
  if (window.marked) marked.setOptions({ gfm: true, breaks: true });
  function markdown(text) {
    if (!window.marked || !window.DOMPurify) return el('div', { class: 'md', text });
    const html = DOMPurify.sanitize(marked.parse(text || ''), { FORBID_TAGS: ['style', 'form', 'input', 'iframe', 'object', 'embed'], FORBID_ATTR: ['style', 'onerror', 'onclick'] });
    const box = el('div', { class: 'md', html });
    box.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    box.querySelectorAll('img').forEach((img) => { img.loading = 'lazy'; });
    box.querySelectorAll('pre').forEach((pre) => {
      const code = pre.querySelector('code');
      if (code && window.hljs && code.textContent.length < 40000) { try { hljs.highlightElement(code); } catch {} }
      pre.append(el('button', { class: 'copy', text: 'Kopieren', onclick: (e) => copy(code ? code.textContent : pre.textContent, e.target) }));
    });
    return box;
  }
  async function copy(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      if (button) { const old = button.textContent; button.textContent = 'Kopiert'; setTimeout(() => (button.textContent = old), 1200); }
      else MAG.toast('Kopiert');
    } catch { MAG.toast('Kopieren nicht möglich', true); }
  }

  // ---- scrolling ----------------------------------------------------------
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 70;
  function toBottom() { scroller.scrollTop = scroller.scrollHeight; pinned = true; unread = 0; paintJump(); }
  function paintJump() {
    jump.hidden = pinned;
    jump.lastChild.textContent = unread > 0 ? (unread === 1 ? 'Neue Nachricht' : unread + ' neue Nachrichten') : 'Nach unten';
  }
  // Run a change to the timeline; follow it only when the reader was at the end.
  function grow(change, isNewMessage) {
    const stick = pinned;
    change();
    if (stick) scroller.scrollTop = scroller.scrollHeight;
    else if (isNewMessage) { unread++; paintJump(); }
    else paintJump();
  }
  MAG.scrollToEnd = () => toBottom();

  // ---- steps (tool calls) -------------------------------------------------
  const stepMark = { running: '<span class="spin"></span>', ok: MAG.icons.check, error: MAG.icons.warn, timeout: MAG.icons.warn, cancelled: MAG.icons.stop };
  function stepInput(step) {
    const i = step.input || {};
    if (step.tool === 'shell') return i.command || '';
    if (step.tool === 'write_file') return (i.path || '') + '\n\n' + (i.content || '');
    const keys = Object.keys(i);
    if (keys.length === 1 && typeof i[keys[0]] === 'string') return i[keys[0]];
    return keys.length ? JSON.stringify(i, null, 2) : '';
  }
  function stepTime(step) { return MAG.stopwatch((step.t1 || Date.now()) - (step.t0 || Date.now())); }
  function buildStep(m, index) {
    const step = m.parts[index];
    const node = el('details', { class: 'step ' + step.state, dataset: { part: index } });
    const cancel = step.state === 'running'
      ? el('button', { class: 'cancel', text: 'Abbrechen', onclick: (e) => { e.preventDefault(); MAG.api('POST', '/api/stop', { bot: m.from }).catch((x) => MAG.toast(x.message, true)); } })
      : null;
    // Collapsed, a running step still shows its last lines as they arrive.
    node.append(el('summary', {},
      el('span', { class: 'mark', html: stepMark[step.state] || '' }),
      el('span', { class: 'title', text: step.title || step.tool }),
      el('span', { class: 'time', text: stepTime(step) + (step.exit ? ' · Exit ' + step.exit : '') }),
      cancel,
      el('span', { class: 'chev', html: MAG.icons.chev }),
      step.state === 'running' ? el('span', { class: 'peek', text: lastLines(step.output || '') }) : null));
    const out = el('pre', { class: 'out', text: step.output || '' });
    const input = stepInput(step);
    node.append(el('div', { class: 'body' },
      input ? el('div', { class: 'label', text: step.tool === 'shell' ? 'Befehl' : 'Eingabe' }) : null,
      input ? el('pre', { class: 'cmd', text: input }) : null,
      el('div', { class: 'label', text: step.state === 'running' ? 'Ausgabe (live)' : 'Ausgabe' + (step.cut ? ' (Ende; vollständig im Log der VM)' : '') }),
      out));
    node.addEventListener('toggle', () => { if (node.open) out.scrollTop = out.scrollHeight; });
    return node;
  }
  const lastLines = (text) => text.replace(/\s+$/, '').split('\n').slice(-3).join('\n');

  // ---- pages: a bot's HTML, running in a frame from the bot's own site -----
  // Same fingerprint as the server (FNV-1a over the bytes without whitespace):
  // it tells which code block of the answer a published page came from.
  function sigOf(text) {
    const bytes = new TextEncoder().encode(text.replace(/[ \t\n\r\f\v]+/g, ''));
    let h = 0x811c9dc5;
    for (let i = 0; i < bytes.length; i++) h = Math.imul(h ^ bytes[i], 0x01000193) >>> 0;
    return h.toString(16).padStart(8, '0');
  }
  // Frames load when they come into view, and only a few stay alive: a
  // timeline full of WebGL pages must not eat the phone.
  const framed = [];
  let seen = null;
  function watch(card) {
    seen ||= new IntersectionObserver((entries) => {
      for (const e of entries) {
        const card = e.target;
        if (!card.isConnected) { seen.unobserve(card); unmount(card); continue; }
        card._visible = e.isIntersecting;
        if (e.isIntersecting) mount(card);
      }
    }, { root: scroller, rootMargin: '250px 0px' });
    seen.observe(card);
  }
  function mount(card) {
    if (card._frame || card._mode !== 'preview') return;
    const frame = el('iframe', { title: card._title, src: card._src });
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-pointer-lock allow-downloads');
    frame.setAttribute('allow', 'fullscreen; autoplay; clipboard-write; accelerometer; gyroscope; gamepad; xr-spatial-tracking');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    card._frame = frame;
    card._stage.replaceChildren(frame);
    framed.push(card);
    while (framed.length > 3) {
      const idle = framed.find((c) => c !== card && !c._visible);
      if (!idle) break;
      unmount(idle);
    }
  }
  function unmount(card) {
    const at = framed.indexOf(card);
    if (at >= 0) framed.splice(at, 1);
    if (!card._frame) return;
    card._frame = null;
    card._stage.replaceChildren(el('button', { class: 'idle', text: 'Vorschau laden', onclick: () => mount(card) }));
  }
  function buildPage(m, page, pre) {
    const base = MAG.bot(page.bot || m.from)?.site;
    if (!base || !page.path) return null;
    const href = base + '/' + page.path.split('/').map(encodeURIComponent).join('/');
    const stage = el('div', { class: 'stage' }), code = el('div', { class: 'code md', hidden: true });
    const source = () => (pre ? (pre.querySelector('code') || pre).textContent : m.parts?.[page.part]?.input?.content || '');
    const tab = (mode, label) => el('button', { role: 'tab', text: label, onclick: () => show(mode) });
    const tabs = { preview: tab('preview', 'Vorschau'), code: tab('code', 'Code') };
    const copied = el('span', { text: 'Kopieren' });
    const card = el('div', { class: 'page', dataset: { path: page.path } },
      el('div', { class: 'bar' },
        el('div', { class: 'seg', role: 'tablist' }, tabs.preview, tabs.code),
        el('span', { class: 'grow' }),
        el('button', { class: 'tool', 'aria-label': 'Code kopieren', onclick: () => copy(source(), copied) }, MAG.icon('copy'), copied),
        el('a', { class: 'tool', href, target: '_blank', rel: 'noopener', 'aria-label': 'Vollbild in neuem Tab' }, MAG.icon('full'), el('span', { text: 'Vollbild' }))),
      stage, code,
      el('a', { class: 'addr', href, target: '_blank', rel: 'noopener', text: href.replace(/^https?:\/\//, '') }));
    Object.assign(card, { _src: href + '?v=' + (page.v || m.ts), _title: page.title || page.path, _stage: stage, _mode: 'preview', _visible: false, _frame: null });
    function show(mode) {
      card._mode = mode;
      for (const [key, node] of Object.entries(tabs)) { node.classList.toggle('on', key === mode); node.setAttribute('aria-selected', String(key === mode)); }
      stage.hidden = mode !== 'preview';
      code.hidden = mode !== 'code';
      if (mode === 'code') {
        if (!code.firstChild) {
          const block = pre || el('pre', {}, el('code', { class: 'language-html', text: source() }));
          if (!pre && window.hljs && source().length < 40000) { try { hljs.highlightElement(block.firstChild); } catch {} }
          code.append(block);
        }
        unmount(card);
        stage.replaceChildren();
      } else if (card._visible) mount(card);
    }
    if (pre) { pre.before(card); code.append(pre); }
    show('preview');
    watch(card);
    return card;
  }
  // Puts each page where it belongs: in place of the code block it was made
  // from, or below the step that wrote it.
  function placePages(m, body) {
    if (!(m.pages || []).length) return;
    const blocks = [...body.querySelectorAll('.md pre')].filter((pre) => pre.querySelector('code'))
      .map((pre) => ({ pre, sig: sigOf(pre.querySelector('code').textContent), used: false }));
    for (const page of m.pages) {
      const hit = page.sig ? blocks.find((b) => !b.used && b.sig === page.sig) : null;
      if (hit) hit.used = true;
      const card = buildPage(m, page, hit?.pre);
      if (!card || hit) continue;
      const anchor = page.part !== undefined ? body.querySelector(':scope > [data-part="' + page.part + '"]') : null;
      if (anchor) anchor.after(card);
      else body.append(card);
    }
  }

  // ---- attachments and 3D -------------------------------------------------
  const fileUrl = (a, extra) => '/api/file/' + encodeURIComponent(a.file) + (extra || '');
  function buildAttachment(a) {
    if (a.kind === 'image') return el('button', { class: 'att-img', onclick: () => lightbox(fileUrl(a)) }, el('img', { src: fileUrl(a), alt: a.name, loading: 'lazy' }));
    if (a.kind === 'audio') return el('audio', { controls: true, preload: 'none', src: fileUrl(a) });
    if (a.kind === 'video') return el('video', { controls: true, preload: 'metadata', src: fileUrl(a), playsInline: true });
    return el('a', { class: 'att-file', href: fileUrl(a, '?download=1'), download: a.name }, MAG.icon(a.kind === 'model' ? 'cube' : 'file'),
      el('span', { text: a.name }), el('small', { text: MAG.bytes(a.size) }));
  }
  function lightbox(src) {
    const close = () => { box.remove(); document.removeEventListener('keydown', key); };
    const key = (e) => e.key === 'Escape' && close();
    const box = el('div', { id: 'lightbox', onclick: (e) => e.target === box && close() }, el('img', { src }),
      el('button', { class: 'icon-btn', 'aria-label': 'Schließen', html: MAG.icons.x, onclick: close }));
    document.addEventListener('keydown', key);
    document.body.append(box);
  }
  const quality = { fast: 'Schnell', normal: 'Normal', high: 'Hoch' };
  function build3d(m) {
    const m3 = m.model3d, card = el('div', { class: 'm3' });
    const running = m3.state === 'queued' || m3.state === 'running';
    const label = m3.state === 'done' ? 'Qualität ' + (quality[m3.quality] || m3.quality)
      : m3.state === 'error' ? (m3.error || 'Fehler') : m3.state === 'cancelled' ? 'Abgebrochen'
      : (m3.stage || 'Warteschlange') + (m3.source ? ' · ' + m3.source : '');
    card.append(el('div', { class: 'head' }, MAG.icon('cube'), el('b', { text: 'Foto zu 3D' }), el('span', { class: 'grow', text: label }),
      running ? el('button', { class: 'pill danger', text: 'Abbrechen', onclick: () => MAG.api('POST', '/api/3d/' + m.id + '/cancel').catch((x) => MAG.toast(x.message, true)) }) : null));
    if (running) card.append(el('div', { class: 'bar' }, el('i', { style: 'width:' + Math.round((m3.progress || 0) * 100) + '%' })));
    const glb = (m.attachments || []).find((a) => a.name.endsWith('.glb'));
    if (m3.state === 'done' && glb) {
      const canvas = el('canvas', {});
      const stage = el('div', { class: 'stage' }, canvas, el('span', { class: 'hint', text: 'Ziehen dreht · zwei Finger zoomen' }),
        el('button', { class: 'full', 'aria-label': 'Vollbild', html: MAG.icons.full, onclick: () => MAG.viewer3d.fullscreen(stage) }));
      card.append(stage);
      MAG.viewer3d.attach(canvas, fileUrl(glb));
      card.append(el('div', { class: 'links' }, (m.attachments || []).map((a) =>
        el('a', { class: 'pill', href: fileUrl(a, '?download=1'), download: a.name, text: a.name.split('.').pop().toUpperCase() + ' · ' + MAG.bytes(a.size) }))));
    }
    return card;
  }

  // ---- one message --------------------------------------------------------
  // Actions come after the text, like everywhere else: copy, read aloud, and
  // a menu for what must not happen by accident.
  function actions(m) {
    if (m.state === 'streaming') return null;
    const user = m.role === 'user', row = el('div', { class: 'acts' });
    const button = (icon, label, run) => el('button', { class: 'act', 'aria-label': label, title: label, html: MAG.icons[icon], onclick: run });
    if (user) row.append(el('span', { class: 'time', text: MAG.clock(m.ts), title: MAG.day(m.ts) }));
    if (m.text) row.append(button('copy', 'Kopieren', async (e) => {
      const b = e.currentTarget;
      try {
        await navigator.clipboard.writeText(m.text);
        b.innerHTML = MAG.icons.check;
        setTimeout(() => { b.innerHTML = MAG.icons.copy; }, 1300);
      } catch { MAG.toast('Kopieren nicht möglich', true); }
    }));
    if (m.text && !user && MAG.speak) row.append(button('speak', 'Vorlesen', (e) => MAG.speak(m, e.currentTarget)));
    if (S.you.role === 'owner') row.append(button('dots', 'Mehr', (e) => MAG.menu(e.currentTarget, [
      { icon: 'trash', label: 'Nachricht löschen', danger: true, run: () => MAG.api('DELETE', '/api/message/' + m.id).catch((x) => MAG.toast(x.message, true)) },
    ])));
    return row.children.length ? row : null;
  }
  function build(m) {
    const user = m.role === 'user';
    const node = el('article', { class: 'msg ' + (user ? 'user' : m.role === 'service' ? 'service' : 'bot'), dataset: { id: m.id, from: m.from } });
    const files = m.model3d ? [] : m.attachments || [];
    const atts = files.length ? el('div', { class: 'atts' }, files.map(buildAttachment)) : null;
    if (user) {
      // The bubble, what was attached, and (on tap or hover) the actions.
      const bubble = m.text ? el('div', { class: 'bubble' }, markdown(m.text)) : null;
      const col = el('div', { class: 'col' }, bubble, atts);
      col.addEventListener('click', (e) => {
        if (e.target.closest('a, button, video, audio') || String(window.getSelection() || '')) return;
        node.classList.toggle('show-acts');
      });
      node.append(el('div', { class: 'line' }, actions(m), col));
      return node;
    }
    node.append(el('div', { class: 'meta' }, el('span', { class: 'who', text: MAG.name(m.from) }),
      el('span', { class: 'time', text: MAG.clock(m.ts), title: MAG.day(m.ts) })));
    if (m.model3d) {
      node.append(build3d(m));
    } else {
      if (m.reasoning) node.append(buildThoughts(m));
      const body = el('div', { class: 'body' });
      (m.parts || []).forEach((p, i) => body.append(buildPart(m, i)));
      if (!(m.parts || []).length && m.text) body.append(markdown(m.text));
      node.append(body);
      placePages(m, body);
    }
    if (atts) node.append(atts);
    const note = footer(m);
    if (note) node.append(note);
    const acts = actions(m);
    if (acts) node.append(acts);
    markStreaming(node, m);
    return node;
  }
  function buildThoughts(m) {
    return el('details', { class: 'think' }, el('summary', {}, el('span', { text: 'Gedanken' })), markdown(m.reasoning));
  }
  function buildPart(m, index) {
    const p = m.parts[index];
    if (p.k === 'step') return buildStep(m, index);
    const node = markdown(p.text);
    node.dataset.part = index;
    return node;
  }
  function footer(m) {
    if (m.state === 'error' && !m.model3d) return el('div', { class: 'note err', text: m.error || 'Fehler' });
    if (m.state === 'stopped' && !m.model3d) return el('div', { class: 'note', text: 'Gestoppt' });
    if (m.page_note) return el('div', { class: 'note', text: m.page_note });
    return null;
  }
  function markStreaming(node, m) {
    node.querySelectorAll('.cursor').forEach((n) => n.classList.remove('cursor'));
    if (m.state !== 'streaming' || m.model3d) return;
    const parts = node.querySelectorAll('.body > .md');
    const last = parts[parts.length - 1];
    const lastPart = (m.parts || [])[m.parts.length - 1];
    if (last && lastPart && lastPart.k === 'text') (last.lastElementChild || last).classList.add('cursor');
  }

  function upsert(m, fresh) {
    const old = nodes.get(m.id);
    // The whole message is rebuilt: a queued re-render of one of its parts is obsolete.
    for (const key of dirty) if (key.startsWith(m.id + ':')) dirty.delete(key);
    const node = build(m);
    if (old) {
      // Keep what the reader opened.
      if (old.classList.contains('show-acts')) node.classList.add('show-acts');
      old.querySelectorAll('details[open]').forEach((d) => {
        const twin = d.classList.contains('think') ? node.querySelector('.think') : node.querySelector('details[data-part="' + d.dataset.part + '"]');
        if (twin) twin.open = true;
      });
      grow(() => old.replaceWith(node));
    } else {
      // Ids sort by time: a new message goes where its id belongs (almost always the end).
      let after = null;
      for (const kid of list.children) if (kid.dataset.id > m.id) { after = kid; break; }
      grow(() => list.insertBefore(node, after), fresh && !after);
    }
    nodes.set(m.id, node);
    empty.hidden = S.messages.size > 0;
  }

  function renderAll() {
    list.replaceChildren();
    nodes.clear();
    dirty.clear();
    const ordered = [...S.messages.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    for (const m of ordered) { const node = build(m); nodes.set(m.id, node); list.append(node); }
    empty.hidden = ordered.length > 0;
    olderBtn.hidden = !S.more;
    toBottom();
  }

  // Streamed text: re-render the touched part at most once per frame.
  function flush() {
    for (const key of dirty) {
      const [id, part] = key.split(':');
      const m = S.messages.get(id), node = nodes.get(id);
      if (!m || !node) continue;
      const body = node.querySelector('.body');
      if (!body) continue;
      const fresh = buildPart(m, +part);
      const old = body.querySelector(':scope > [data-part="' + part + '"]');
      grow(() => { if (old) old.replaceWith(fresh); else body.append(fresh); markStreaming(node, m); });
    }
    dirty.clear();
  }
  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; flush(); }, 60);
  }

  // ---- activity row: who is doing what right now --------------------------
  const phases = { reading: 'liest', thinking: 'denkt nach', writing: 'schreibt', tool: 'arbeitet', warming: 'bereitet sich vor', resetting: 'leert den Kontext' };
  function paintActivity() {
    const rows = [];
    for (const b of S.bots) {
      if (!b.busy && b.phase === 'idle') continue;
      let what = phases[b.phase] || 'arbeitet';
      if (b.phase === 'reading' && b.total > 0) what += ' ' + Math.min(99, Math.round((b.processed / b.total) * 100)) + ' %';
      rows.push(el('div', { class: 'act', dataset: { bot: b.id, since: b.phase_since } },
        el('span', { class: 'dots' }, el('i'), el('i'), el('i')), el('b', { text: b.name }), el('span', { class: 'what', text: what }),
        el('span', { class: 't', text: MAG.stopwatch(Date.now() - b.phase_since) }),
        b.busy ? el('button', { class: 'stop', text: 'Stop', onclick: () => MAG.api('POST', '/api/stop', { bot: b.id }).catch((x) => MAG.toast(x.message, true)) }) : null));
    }
    grow(() => activity.replaceChildren(...rows));
  }
  setInterval(() => {
    if (!activity) return;
    activity.querySelectorAll('.act').forEach((row) => { row.querySelector('.t').textContent = MAG.stopwatch(Date.now() - +row.dataset.since); });
    list.querySelectorAll('.step.running').forEach((node) => {
      const article = node.closest('.msg'), m = S.messages.get(article?.dataset.id);
      const step = m?.parts?.[+node.dataset.part];
      if (step) node.querySelector('.time').textContent = stepTime(step);
    });
  }, 1000);

  // ---- wiring -------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    scroller = $('#scroll'); list = $('#list'); jump = $('#jump'); activity = $('#activity'); empty = $('#empty'); olderBtn = $('#older');
    scroller.addEventListener('scroll', () => {
      const now = nearBottom();
      if (now !== pinned) { pinned = now; if (pinned) unread = 0; paintJump(); }
    }, { passive: true });
    jump.addEventListener('click', toBottom);
    olderBtn.addEventListener('click', () => MAG.loadOlder().catch((x) => MAG.toast(x.message, true)));
    // The keyboard and the composer change the viewport: stay at the end.
    new ResizeObserver(() => { if (pinned) scroller.scrollTop = scroller.scrollHeight; }).observe(scroller);
  });
  MAG.on('reset', renderAll);
  MAG.on('message', (m) => upsert(m, !nodes.has(m.id)));
  MAG.on('gone', (id) => { nodes.get(id)?.remove(); nodes.delete(id); empty.hidden = S.messages.size > 0; });
  MAG.on('delta', (m, part) => { dirty.add(m.id + ':' + part); schedule(); });
  MAG.on('reasoning', (m) => {
    const node = nodes.get(m.id);
    if (!node) return;
    const old = node.querySelector('.think'), fresh = buildThoughts(m);
    if (old) { fresh.open = old.open; grow(() => old.replaceWith(fresh)); }
    else grow(() => node.querySelector('.meta').after(fresh));
  });
  MAG.on('step', (m, part) => {
    const node = nodes.get(m.id);
    if (!node) return;
    flush();
    const body = node.querySelector('.body'), old = body.querySelector(':scope > [data-part="' + part + '"]'), fresh = buildStep(m, part);
    if (old) { fresh.open = old.open; grow(() => old.replaceWith(fresh)); }
    else grow(() => body.append(fresh));
    markStreaming(node, m);
  });
  MAG.on('step_output', (m, part, text) => {
    const node = nodes.get(m.id)?.querySelector('.body > [data-part="' + part + '"]');
    if (!node) return;
    const out = node.querySelector('pre.out'), peek = node.querySelector('.peek');
    const follow = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
    grow(() => {
      out.append(text);
      if (peek) peek.textContent = lastLines(m.parts[part].output || '');
    });
    if (node.open && follow) out.scrollTop = out.scrollHeight;
  });
  MAG.on('bots', paintActivity);
  MAG.on('older', () => {
    const before = scroller.scrollHeight;
    const ordered = [...S.messages.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    list.replaceChildren();
    nodes.clear();
    for (const m of ordered) { const node = build(m); nodes.set(m.id, node); list.append(node); }
    olderBtn.hidden = !S.more;
    scroller.scrollTop += scroller.scrollHeight - before; // stay on the same message
  });
})();

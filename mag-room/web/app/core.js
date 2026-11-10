// MAG room UI: shared helpers, the store, and the live connection.
// Plain scripts, no build step; everything hangs off window.MAG.
'use strict';
(function () {
  const MAG = (window.MAG = {});
  const token = document.querySelector('meta[name="mag-token"]')?.content || '';
  MAG.version = document.querySelector('meta[name="mag-version"]')?.content || '';

  // ---- DOM helpers --------------------------------------------------------
  MAG.$ = (sel, root) => (root || document).querySelector(sel);
  MAG.el = function (tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k in node && k !== 'list') node[k] = v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
    return node;
  };
  const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  MAG.icons = {
    trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>'),
    gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    send: svg('<path d="M12 19V5M5 12l7-7 7 7"/>'),
    mic: svg('<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
    stop: svg('<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>'),
    x: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    down: svg('<path d="M12 5v14M5 12l7 7 7-7"/>'),
    chev: svg('<path d="M9 6l6 6-6 6"/>'),
    check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    warn: svg('<path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="9"/>'),
    dots: svg('<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>'),
    file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5"/>'),
    image: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>'),
    camera: svg('<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>'),
    cube: svg('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5"/>'),
    full: svg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
    copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>'),
    speak: svg('<path d="M4 10v4h4l5 4V6L8 10zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>'),
    globe: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  };
  MAG.icon = (name) => MAG.el('span', { class: 'ico', html: MAG.icons[name] }).firstChild;

  // ---- formatting ---------------------------------------------------------
  MAG.clock = (ms) => new Date(ms).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  MAG.day = (ms) => new Date(ms).toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' });
  MAG.dur = function (ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + ' s';
    const m = Math.floor(s / 60);
    if (m < 60) return m + ':' + String(s % 60).padStart(2, '0') + ' min';
    return Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  };
  MAG.stopwatch = function (ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  };
  MAG.bytes = function (n) {
    if (n >= 1e9) return (n / 1e9).toFixed(1).replace('.', ',') + ' GB';
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' MB';
    if (n >= 1e3) return Math.round(n / 1e3) + ' KB';
    return n + ' B';
  };

  // ---- API ----------------------------------------------------------------
  MAG.api = async function (method, path, body, opts) {
    const init = { method, headers: { 'X-MAG-Token': token }, credentials: 'same-origin' };
    if (body instanceof Blob || body instanceof ArrayBuffer) init.body = body;
    else if (body !== undefined && body !== null) {
      init.body = JSON.stringify(body);
      init.headers['Content-Type'] = 'application/json';
    }
    if (opts?.signal) init.signal = opts.signal;
    let res;
    try {
      res = await fetch(path, init);
    } catch (err) {
      throw Object.assign(new Error('Keine Verbindung zum Raum'), { network: true });
    }
    const type = res.headers.get('content-type') || '';
    const data = type.includes('json') ? await res.json().catch(() => ({})) : await res.text();
    if (!res.ok) throw Object.assign(new Error((data && data.error) || 'Fehler ' + res.status), { status: res.status, data });
    return data;
  };

  // ---- small UI pieces ----------------------------------------------------
  const toasts = MAG.el('div', { id: 'toasts' });
  document.addEventListener('DOMContentLoaded', () => document.body.append(toasts));
  MAG.toast = function (text, bad) {
    const node = MAG.el('div', { class: 'toast' + (bad ? ' err' : ''), text });
    toasts.append(node);
    setTimeout(() => node.remove(), bad ? 6000 : 3200);
  };
  MAG.confirm = function (title, text, okLabel, danger) {
    return new Promise((resolve) => {
      const close = (value) => { root.remove(); resolve(value); };
      const ok = MAG.el('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), text: okLabel || 'OK', onclick: () => close(true) });
      const root = MAG.el('div', { class: 'dialog', onclick: (e) => e.target === root && close(false) },
        MAG.el('div', { class: 'box', role: 'alertdialog' },
          MAG.el('h2', { text: title }), MAG.el('p', { text }),
          MAG.el('div', { class: 'btns' }, MAG.el('button', { class: 'btn', text: 'Abbrechen', onclick: () => close(false) }), ok)));
      document.body.append(root);
      ok.focus();
    });
  };
  // A menu anchored to a button; closes on outside tap or Escape.
  MAG.menu = function (anchor, items) {
    document.querySelector('.menu')?.remove();
    const menu = MAG.el('div', { class: 'menu', role: 'menu' });
    const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', key, true); };
    const outside = (e) => { if (!menu.contains(e.target)) close(); };
    const key = (e) => { if (e.key === 'Escape') close(); };
    for (const item of items) {
      if (!item) continue;
      const node = item.node || MAG.el('button', { class: item.danger ? 'danger' : '', onclick: () => { close(); item.run(); } },
        item.icon ? MAG.icon(item.icon) : null, item.label);
      menu.append(node);
    }
    document.body.append(menu);
    const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left));
    const above = r.top - h - 6;
    menu.style.left = left + 'px';
    menu.style.top = (above > 8 ? above : Math.min(window.innerHeight - h - 8, r.bottom + 6)) + 'px';
    setTimeout(() => { document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', key, true); }, 0);
    return close;
  };
  // A bottom sheet (phone) or side panel (desktop).
  MAG.sheet = function (title, build) {
    document.querySelector('.sheet')?.remove();
    document.querySelector('.scrim')?.remove();
    const content = MAG.el('div', { class: 'content' });
    const close = () => { scrim.remove(); sheet.remove(); document.removeEventListener('keydown', key); sheet.dispatchEvent(new Event('closed')); };
    const key = (e) => { if (e.key === 'Escape') close(); };
    const scrim = MAG.el('div', { class: 'scrim', onclick: close });
    const sheet = MAG.el('section', { class: 'sheet', role: 'dialog', 'aria-label': title },
      MAG.el('header', {}, MAG.el('h2', { text: title }), MAG.el('button', { class: 'icon-btn', 'aria-label': 'Schließen', html: MAG.icons.x, onclick: close })),
      content);
    document.body.append(scrim, sheet);
    document.addEventListener('keydown', key);
    sheet.rebuild = () => { const top = content.scrollTop; content.replaceChildren(); build(content, sheet, close); content.scrollTop = top; };
    sheet.rebuild();
    return sheet;
  };

  // ---- store --------------------------------------------------------------
  const S = (MAG.state = { messages: new Map(), bots: [], settings: {}, you: {}, features: {}, seq: 0, more: false, online: false, ready: false });
  const listeners = {};
  MAG.on = (type, fn) => (listeners[type] ||= []).push(fn);
  const emit = (type, ...args) => (listeners[type] || []).forEach((fn) => fn(...args));
  MAG.bot = (id) => S.bots.find((b) => b.id === id);
  MAG.name = (from) => (from === 'chris' ? S.you.name || 'Chris' : from === 'idol' ? '3D' : MAG.bot(from)?.name || from);

  function apply(ev) {
    if (ev.seq && ev.seq <= S.seq) return; // already part of the snapshot
    if (ev.seq) S.seq = ev.seq;
    const m = ev.id ? S.messages.get(ev.id) : null;
    switch (ev.t) {
      case 'message':
        S.messages.set(ev.m.id, ev.m);
        emit('message', ev.m);
        break;
      case 'delta':
        if (!m) break;
        m.parts ||= [];
        while (m.parts.length <= ev.part) m.parts.push({ k: 'text', text: '' });
        m.parts[ev.part].text += ev.text;
        emit('delta', m, ev.part);
        break;
      case 'reasoning':
        if (!m) break;
        m.reasoning = (m.reasoning || '') + ev.text;
        emit('reasoning', m);
        break;
      case 'step':
        if (!m) break;
        m.parts ||= [];
        m.parts[ev.part] = ev.step;
        emit('step', m, ev.part);
        break;
      case 'step_output': {
        const step = m?.parts?.[ev.part];
        if (!step) break;
        step.output = (step.output || '') + ev.text;
        if (step.output.length > 120000) step.output = step.output.slice(-90000);
        emit('step_output', m, ev.part, ev.text);
        break;
      }
      case 'gone':
        S.messages.delete(ev.id);
        emit('gone', ev.id);
        break;
      case 'cleared':
        S.messages.clear();
        S.more = false;
        emit('reset');
        break;
      case 'bots':
        S.bots = ev.bots;
        emit('bots');
        break;
      case 'settings':
        S.settings = ev.settings;
        emit('settings');
        break;
      case 'notice':
        emit('notice', ev);
        break;
    }
  }

  // The stream is opened first and buffered; then the snapshot is fetched and
  // only newer events are applied. A reconnect repeats this, so the view
  // always equals the server's state and nothing is lost or doubled.
  let source = null, buffer = null, retry = 0;
  async function sync() {
    try {
      const snap = await MAG.api('GET', '/api/state?limit=150');
      if (snap.version && MAG.version && snap.version !== MAG.version) return location.reload();
      S.messages = new Map(snap.messages.map((m) => [m.id, m]));
      Object.assign(S, { bots: snap.bots, settings: snap.settings, you: snap.you, features: snap.features, seq: snap.seq, more: snap.more, ready: true });
      emit('reset');
      emit('bots');
      emit('settings');
      const pending = buffer || [];
      buffer = null;
      pending.forEach(apply);
      setOnline(true);
    } catch (err) {
      setTimeout(sync, 1500);
    }
  }
  function setOnline(on) {
    if (S.online === on) return;
    S.online = on;
    emit('online', on);
  }
  MAG.connect = function () {
    source?.close();
    buffer = [];
    source = new EventSource('/api/events');
    source.onopen = () => { retry = 0; buffer = []; sync(); };
    source.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      if (buffer) buffer.push(ev);
      else apply(ev);
    };
    source.onerror = () => {
      setOnline(false);
      // EventSource retries by itself; when it gave up, start a fresh one.
      if (source.readyState === EventSource.CLOSED) setTimeout(MAG.connect, Math.min(8000, 800 * 2 ** retry++));
    };
  };
  // Phones freeze background tabs: resync when the page is shown again.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && S.ready && (!source || source.readyState !== EventSource.OPEN)) MAG.connect();
  });

  MAG.loadOlder = async function () {
    const first = [...S.messages.keys()].sort()[0];
    if (!first) return;
    const page = await MAG.api('GET', '/api/messages?limit=150&before=' + first);
    for (const m of page.messages) S.messages.set(m.id, m);
    S.more = page.more;
    emit('older', page.messages);
  };
})();

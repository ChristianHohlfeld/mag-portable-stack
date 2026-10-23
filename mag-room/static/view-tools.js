// Browser and console view tools: zoom (pinch, +/-, Ctrl+wheel), pan, fit,
// "Zuschauen" (watch the bot: no input reaches it) vs. "Steuern" (take over:
// taps, typing and scrolling go to the bot's browser/shell), a caption of
// the bot's current browser action, and console font zoom + copy.
// Uses the page's globals: browserOverlay(), terminals, $().
(function () {
  'use strict';
  const view = document.getElementById('browser-view');
  const screen = document.getElementById('screen');
  if (!view || !screen) return;
  const z = {s: 1, x: 0, y: 0};
  const MIN = 1, MAX = 4;
  let mode = 'watch';
  try { mode = localStorage.getItem('mag-view-mode') || 'watch'; } catch (_) {}

  const css = document.createElement('style');
  css.textContent = `
#browser-view{position:relative;touch-action:none}
#screen{transform-origin:0 0;will-change:transform}
#browser-view .browser-view-bar{position:relative;z-index:5}
.vt-bar{display:flex;align-items:center;gap:2px;margin-left:auto}
.vt-bar button{min-width:30px;height:30px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:12px;cursor:pointer;padding:0 6px}
.vt-bar button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.vt-caption{position:absolute;left:8px;bottom:8px;max-width:calc(100% - 16px);z-index:4;padding:4px 10px;border-radius:999px;background:rgba(0,0,0,.62);color:#fff;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;opacity:0;transition:opacity .2s}
.vt-caption.on{opacity:1}
#browser-view.watching #screen{cursor:grab}
.terminal-heading .vt-bar{margin-left:6px}
`;
  document.head.append(css);

  function apply() {
    const vw = screen.clientWidth || 1, vh = screen.clientHeight || 1;
    // Keep the zoomed stream covering the view (no empty gaps while panning).
    z.x = Math.min(0, Math.max(vw - vw * z.s, z.x));
    z.y = Math.min(0, Math.max(vh - vh * z.s, z.y));
    screen.style.transform = z.s === 1 ? '' : `translate(${z.x}px,${z.y}px) scale(${z.s})`;
    pct.textContent = Math.round(z.s * 100) + '%';
  }
  // Zoom keeping the point (cx, cy) in view coordinates fixed.
  function zoomAt(next, cx, cy) {
    next = Math.min(MAX, Math.max(MIN, next));
    const r = screen.getBoundingClientRect(), v = view.getBoundingClientRect();
    const ox = (cx ?? (r.left + r.width / 2)) - (r.left - z.x) , oy = (cy ?? (r.top + r.height / 2)) - (r.top - z.y);
    z.x = ox - (ox - z.x) * next / z.s;
    z.y = oy - (oy - z.y) * next / z.s;
    z.s = next;
    void v;
    apply();
  }
  function fit() { z.s = 1; z.x = 0; z.y = 0; apply(); }

  const bar = document.createElement('div');
  bar.className = 'vt-bar';
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', 'Ansicht');
  const mk = (label, title, fn) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.title = title; b.setAttribute('aria-label', title); b.onclick = fn; bar.append(b); return b; };
  mk('−', 'Verkleinern', () => zoomAt(z.s / 1.25));
  const pct = mk('100%', 'An Breite anpassen', fit);
  pct.classList.add('vt-fit');
  mk('+', 'Vergrößern', () => zoomAt(z.s * 1.25));
  const modeBtn = mk('', '', () => setMode(mode === 'watch' ? 'control' : 'watch'));
  modeBtn.classList.add('vt-mode');
  (view.querySelector('.browser-view-bar') || view).append(bar);

  const caption = document.createElement('div');
  caption.className = 'vt-caption';
  caption.setAttribute('role', 'status');
  caption.setAttribute('aria-live', 'polite');
  view.append(caption);

  function setMode(m) {
    mode = m;
    try { localStorage.setItem('mag-view-mode', m); } catch (_) {}
    view.classList.toggle('watching', m === 'watch');
    modeBtn.textContent = m === 'watch' ? 'Steuern' : 'Zurückgeben';
    modeBtn.title = m === 'watch' ? 'Browser selbst bedienen (übernehmen)' : 'Steuerung an den Bot zurückgeben (zuschauen)';
    modeBtn.setAttribute('aria-label', modeBtn.title);
    modeBtn.setAttribute('aria-pressed', String(m === 'control'));
    for (const t of (typeof terminals !== 'undefined' ? terminals.values() : [])) t.term.options.disableStdin = m === 'watch';
    termMode.textContent = modeBtn.textContent;
    termMode.setAttribute('aria-pressed', String(m === 'control'));
  }

  // Gestures. Capture phase on the view runs before the stream's own input
  // handlers; in watch mode (or with two fingers / Ctrl) they never see it.
  const pts = new Map();
  let pinch = null, pan = null;
  view.addEventListener('pointerdown', ev => {
    if (ev.target !== screen) return;
    pts.set(ev.pointerId, {x: ev.clientX, y: ev.clientY});
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), s: z.s};
      ev.stopPropagation(); ev.preventDefault(); return;
    }
    if (mode === 'watch') {
      pan = {x: ev.clientX, y: ev.clientY, zx: z.x, zy: z.y};
      screen.focus({preventScroll: true});
      try { screen.setPointerCapture(ev.pointerId); } catch (_) {}
      ev.stopPropagation(); ev.preventDefault();
    }
  }, true);
  view.addEventListener('pointermove', ev => {
    if (!pts.has(ev.pointerId)) { if (mode === 'watch' && ev.target === screen) ev.stopPropagation(); return; }
    pts.set(ev.pointerId, {x: ev.clientX, y: ev.clientY});
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()];
      zoomAt(pinch.s * Math.hypot(a.x - b.x, a.y - b.y) / (pinch.d || 1), (a.x + b.x) / 2, (a.y + b.y) / 2);
      ev.stopPropagation(); return;
    }
    if (pan) { z.x = pan.zx + ev.clientX - pan.x; z.y = pan.zy + ev.clientY - pan.y; apply(); ev.stopPropagation(); }
    else if (mode === 'watch') ev.stopPropagation();
  }, true);
  const up = ev => {
    pts.delete(ev.pointerId);
    if (pts.size < 2) pinch = null;
    if (pan || mode === 'watch') { pan = null; ev.stopPropagation(); }
  };
  view.addEventListener('pointerup', up, true);
  view.addEventListener('pointercancel', up, true);
  view.addEventListener('wheel', ev => {
    if (ev.target !== screen) return;
    if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); ev.stopPropagation(); zoomAt(z.s * (ev.deltaY < 0 ? 1.12 : 1 / 1.12), ev.clientX, ev.clientY); return; }
    if (mode === 'watch') { ev.preventDefault(); ev.stopPropagation(); if (z.s > 1) { z.x -= ev.deltaX; z.y -= ev.deltaY; apply(); } }
  }, {capture: true, passive: false});
  view.addEventListener('keydown', ev => {
    if (ev.target !== screen) return;
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === '+' || ev.key === '=')) { ev.preventDefault(); ev.stopPropagation(); zoomAt(z.s * 1.25); }
    else if ((ev.ctrlKey || ev.metaKey) && ev.key === '-') { ev.preventDefault(); ev.stopPropagation(); zoomAt(z.s / 1.25); }
    else if ((ev.ctrlKey || ev.metaKey) && ev.key === '0') { ev.preventDefault(); ev.stopPropagation(); fit(); }
    else if (mode === 'watch') ev.stopPropagation();
  }, true);
  view.addEventListener('contextmenu', ev => { if (mode === 'watch' && ev.target === screen) ev.stopPropagation(); }, true);
  // Rotation / resize: keep zoom, re-clamp pan.
  addEventListener('resize', () => requestAnimationFrame(apply));
  screen.addEventListener('dblclick', ev => { if (mode === 'watch') { ev.stopPropagation(); z.s > 1 ? fit() : zoomAt(2, ev.clientX, ev.clientY); } }, true);

  // Caption of what the bot is doing in its browser.
  let capTimer = 0;
  const ACTION = {click: 'klickt', type: 'tippt', open: 'öffnet', scroll: 'scrollt', back: 'geht zurück', read: 'liest', screenshot: 'macht ein Bildschirmfoto', pointer: 'bewegt die Maus'};
  if (typeof window.browserOverlay === 'function') {
    const orig = window.browserOverlay;
    window.browserOverlay = function (ev) {
      orig(ev);
      try {
        if (!ev || ev.bot !== document.getElementById('browser-agent')?.value) return;
        const what = ACTION[ev.action] || ev.action || 'arbeitet';
        const detail = ev.action === 'type' ? '„' + String(ev.text || '').slice(0, 40) + '“' : ev.url ? String(ev.url).replace(/^https?:\/\//, '').slice(0, 60) : '';
        caption.textContent = 'Bot ' + what + (detail ? ': ' + detail : '');
        caption.classList.add('on');
        clearTimeout(capTimer);
        capTimer = setTimeout(() => caption.classList.remove('on'), 5000);
      } catch (_) {}
    };
  }

  // Console: font zoom, copy, same watch/take-over switch.
  const termHead = document.querySelector('.terminal-heading');
  const tbar = document.createElement('div');
  tbar.className = 'vt-bar';
  const tb = (label, title, fn) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.title = title; b.setAttribute('aria-label', title); b.onclick = fn; tbar.append(b); return b; };
  let font = 12;
  try { font = +localStorage.getItem('mag-term-font') || 12; } catch (_) {}
  function curTerm() { return typeof terminals !== 'undefined' ? terminals.get(document.getElementById('browser-agent')?.value) : null; }
  function setFont(n) {
    font = Math.min(24, Math.max(9, n));
    try { localStorage.setItem('mag-term-font', String(font)); } catch (_) {}
    for (const t of (typeof terminals !== 'undefined' ? terminals.values() : [])) { t.term.options.fontSize = font; try { t.fit.fit(); } catch (_) {} }
  }
  tb('A−', 'Schrift kleiner', () => setFont(font - 1));
  tb('A+', 'Schrift größer', () => setFont(font + 1));
  tb('Kopieren', 'Auswahl oder sichtbaren Inhalt kopieren', () => {
    const t = curTerm(); if (!t) return;
    let text = t.term.getSelection();
    if (!text) { const b = t.term.buffer.active, lines = []; for (let i = Math.max(0, b.length - 400); i < b.length; i++) lines.push(b.getLine(i)?.translateToString(true) || ''); text = lines.join('\n').trimEnd(); }
    navigator.clipboard?.writeText(text).catch(() => {});
  });
  const termMode = tb('', 'Konsole übernehmen / zurückgeben', () => setMode(mode === 'watch' ? 'control' : 'watch'));
  termMode.classList.add('vt-mode');
  if (termHead) termHead.append(tbar);
  // New terminals pick up font and mode.
  setInterval(() => {
    for (const t of (typeof terminals !== 'undefined' ? terminals.values() : [])) {
      if (t.term.options.fontSize !== font) { t.term.options.fontSize = font; try { t.fit.fit(); } catch (_) {} }
      t.term.options.disableStdin = mode === 'watch';
    }
  }, 1000);

  setMode(mode);
  apply();
  window.magViewTools = {zoomAt, fit, setMode, state: () => ({...z, mode})};
})();

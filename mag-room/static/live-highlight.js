// Live action highlight on top of the bot's browser stream. Driven only by
// the browser_action events the room already emits (box = target element in
// viewport CSS px, action, masked text): an outline that pulses before and
// during the action, the agent cursor, click ripple, typed text at the field
// (password/secret fields show dots, the server never sends the value),
// scroll hints and a caption for other bots. Pure client overlay: nothing is
// injected into the page, no model tokens. Scales with the stream, so it
// stays correct under zoom/pan. Toggle in the view bar (default on).
(function () {
  'use strict';
  const view = document.getElementById('browser-view');
  const overlay = document.getElementById('browser-overlay');
  if (!view || !overlay || typeof window.browserOverlay !== 'function') return;
  let on = true;
  try { on = localStorage.getItem('mag-live-highlight') !== 'off'; } catch (_) {}

  const css = document.createElement('style');
  css.textContent = `
#browser-overlay.lh-off{display:none}
.lh-box{position:absolute;border:2px solid #4da3ff;border-radius:6px;box-shadow:0 0 0 3px #4da3ff40,0 0 18px #4da3ff80;background:#4da3ff14;pointer-events:none;z-index:1;animation:lh-pulse 1s ease-in-out infinite;transition:opacity .4s}
.lh-box.secret{border-color:#f5a623;box-shadow:0 0 0 3px #f5a62340;background:#f5a62314}
.lh-box.fade{opacity:0}
@keyframes lh-pulse{50%{box-shadow:0 0 0 6px #4da3ff30,0 0 24px #4da3ffa0}}
.lh-scroll{position:absolute;left:50%;transform:translateX(-50%);padding:4px 12px;border-radius:999px;background:#111c;color:#fff;font-size:18px;line-height:1;pointer-events:none;z-index:2;animation:lh-scroll .9s ease-out forwards}
.lh-scroll.down{bottom:12px}.lh-scroll.up{top:12px}
@keyframes lh-scroll{from{opacity:1}to{opacity:0}}
`;
  document.head.append(css);

  function rect() {
    const c = document.getElementById('screen');
    if (!c || c.hidden) return null;
    const cr = c.getBoundingClientRect(), vr = view.getBoundingClientRect();
    const s = Math.min(cr.width / 1440, cr.height / 900);
    return {x: cr.left - vr.left + (cr.width - 1440 * s) / 2, y: cr.top - vr.top + (cr.height - 900 * s) / 2, s};
  }
  let box = null, boxTimer = 0;
  function drawBox(b, secret) {
    const R = rect();
    if (!R) return;
    if (!box) { box = document.createElement('div'); box.className = 'lh-box'; overlay.append(box); }
    box.className = 'lh-box' + (secret ? ' secret' : '');
    Object.assign(box.style, {left: R.x + b.x * R.s - 3 + 'px', top: R.y + b.y * R.s - 3 + 'px',
      width: b.w * R.s + 6 + 'px', height: b.h * R.s + 6 + 'px'});
    clearTimeout(boxTimer);
    boxTimer = setTimeout(() => box && box.classList.add('fade'), 2600);
  }
  function scrollHint(dir) {
    const h = document.createElement('div');
    h.className = 'lh-scroll ' + dir;
    h.textContent = dir === 'up' ? '▲' : '▼';
    overlay.append(h);
    h.addEventListener('animationend', () => h.remove());
  }
  function otherBotCaption(ev) {
    const cap = view.querySelector('.vt-caption');
    if (!cap) return;
    const a = (typeof state !== 'undefined' && state?.agents || []).find(x => x.id === ev.bot);
    const verb = {click: 'klickt', target: 'zielt auf ein Element', type: 'tippt', scroll_up: 'scrollt hoch', scroll_down: 'scrollt runter', move: 'bewegt die Maus'}[ev.action] || 'arbeitet im Browser';
    cap.textContent = (a ? a.name : 'Bot') + ' ' + verb + ' (anderer Browser)';
    cap.classList.add('on');
    setTimeout(() => cap.classList.remove('on'), 3500);
  }

  const inner = window.browserOverlay;
  window.browserOverlay = function (ev) {
    if (!ev) return inner(ev);
    if (ev.masked) ev.text = '••••••••'; // defence in depth: never render a secret
    const mine = ev.bot === document.getElementById('browser-agent')?.value;
    if (!mine) { otherBotCaption(ev); return inner(ev); }
    if (on) {
      if (ev.box) {
        drawBox(ev.box, !!ev.masked);
        // Typed text appears at the field's caret side, not mid-element.
        if (ev.action === 'type') { ev.x = ev.box.x + Math.min(12, ev.box.w / 2); ev.y = ev.box.y + ev.box.h / 2; }
      }
      if (ev.action === 'scroll_up' || ev.action === 'scroll_down') scrollHint(ev.action === 'scroll_up' ? 'up' : 'down');
    }
    return inner(ev);
  };

  // Toggle next to the zoom controls (view-tools bar) or the nav bar.
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lh-toggle';
  const paint = () => {
    btn.textContent = on ? '◎' : '○';
    btn.title = on ? 'Live-Hervorhebung aus' : 'Live-Hervorhebung an';
    btn.setAttribute('aria-label', 'Live-Hervorhebung');
    btn.setAttribute('aria-pressed', String(on));
    overlay.classList.toggle('lh-off', !on);
  };
  btn.onclick = () => { on = !on; try { localStorage.setItem('mag-live-highlight', on ? 'on' : 'off'); } catch (_) {} paint(); };
  paint();
  (view.querySelector('.vt-bar') || view.querySelector('.browser-view-bar') || view).prepend(btn);
  window.magLiveHighlight = {enabled: () => on, set: v => { on = !!v; paint(); }};
})();

// Keep the visible installed MAG app readable while messages stream.
// Screen Wake Lock is browser/OS-controlled: never simulate touches, play
// silent video, or try to keep the app alive after it is backgrounded.
'use strict';
(function () {
  const KEY = 'mag-screen-awake';
  const standalone = !!(window.navigator?.standalone ||
    window.matchMedia?.('(display-mode: standalone)')?.matches);
  const supported = !!navigator.wakeLock?.request;

  let enabled = standalone;
  try {
    const saved = localStorage.getItem(KEY);
    if (saved !== null) enabled = saved === 'on';
  } catch {}

  let held = null, pending = null, generation = 0, deniedAt = 0;
  const visible = () => !document.hidden && document.visibilityState !== 'hidden';

  async function release() {
    ++generation;
    const old = held;
    held = null;
    if (old) try { await old.release(); } catch {}
  }

  function request(force = false) {
    if (!enabled || !visible() || !supported) return Promise.resolve(false);
    if (held && !held.released) return Promise.resolve(true);
    if (pending) return pending;
    // A denied request (battery-saving mode, unsupported context) must not
    // fire a new permissions attempt on every tap. Explicit toggle bypasses.
    if (!force && Date.now() - deniedAt < 30000) return Promise.resolve(false);

    const ticket = generation;
    const attempt = (async () => {
      try {
        const lock = await navigator.wakeLock.request('screen');
        if (ticket !== generation || !enabled || !visible()) {
          try { await lock.release(); } catch {}
          return false;
        }
        held = lock;
        lock.addEventListener?.('release', () => {
          if (held === lock) held = null;
        });
        deniedAt = 0;
        return true;
      } catch {
        deniedAt = Date.now();
        return false;
      } finally {
        pending = null;
        // A rapid off/on while a request was pending must not strand the
        // enabled app without a lock. Never retry a rejected request in a loop.
        if (ticket !== generation && enabled && visible()) void request();
      }
    })();
    pending = attempt;
    return attempt;
  }

  async function set(on) {
    enabled = !!on;
    try { localStorage.setItem(KEY, enabled ? 'on' : 'off'); } catch {}
    if (!enabled) {
      await release();
      return true;
    }
    ++generation;
    deniedAt = 0;
    return request(true);
  }

  const refresh = () => { if (visible()) void request(); else void release(); };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('pagehide', () => void release());
  window.addEventListener('focus', refresh);
  // Safari may need user activation after restoring a home-screen PWA.
  document.addEventListener('pointerdown', refresh, { passive: true });
  document.addEventListener('keydown', refresh);
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', refresh, { once: true });
  else refresh();

  window.MAG.screenAwake = {
    supported, enabled: () => enabled, active: () => !!held && !held.released,
    set, request,
  };
})();

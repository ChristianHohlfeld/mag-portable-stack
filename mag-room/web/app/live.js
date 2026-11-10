// Watching (and steering) a bot's own browser: a picture about once a second,
// taps go to the page, the address bar navigates. The bot and Chris share one
// browser session; nothing here bypasses the VM.
'use strict';
(function () {
  const { MAG } = window, { el } = MAG;
  MAG.liveView = function (bot) {
    let stopped = false, last = null, timer = 0, misses = 0;
    const img = el('img', { alt: 'Browser von ' + (MAG.bot(bot)?.name || bot) });
    const note = el('div', { class: 'note', text: 'Verbinde …' });
    const address = el('input', { type: 'text', inputMode: 'url', placeholder: 'Adresse', 'aria-label': 'Adresse' });
    const act = (action, body) => MAG.api('POST', '/api/browser/' + bot + '/' + action, body || {}).then((state) => {
      if (state && state.url && document.activeElement !== address) address.value = state.url;
      return state;
    }).catch((err) => { note.textContent = err.message; });
    const go = () => { const url = address.value.trim(); if (url) act('navigate', { url }); };
    address.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); address.blur(); } });
    img.addEventListener('click', (e) => {
      const r = img.getBoundingClientRect();
      act('pointer', { x: Math.round(((e.clientX - r.left) / r.width) * 1440), y: Math.round(((e.clientY - r.top) / r.height) * 900), action: 'tap', button: 'left' });
    });
    const node = el('div', { class: 'live' },
      el('div', { class: 'url' },
        el('button', { class: 'pill', text: '←', 'aria-label': 'Zurück', onclick: () => act('back') }),
        address,
        el('button', { class: 'pill', text: 'Los', onclick: go })),
      img, note);
    async function tick() {
      if (stopped) return;
      if (document.visibilityState === 'visible') {
        try {
          const res = await fetch('/api/screen/' + bot + '?t=' + Date.now(), { credentials: 'same-origin' });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Kein Bild');
          const url = URL.createObjectURL(await res.blob());
          img.src = url;
          if (last) URL.revokeObjectURL(last);
          last = url;
          misses = 0;
          note.hidden = true;
        } catch (err) {
          note.hidden = false;
          note.textContent = err.message;
          misses++;
        }
      }
      timer = setTimeout(tick, misses > 2 ? 3500 : 900);
    }
    tick();
    act('state');
    return { node, stop() { stopped = true; clearTimeout(timer); if (last) URL.revokeObjectURL(last); } };
  };
})();

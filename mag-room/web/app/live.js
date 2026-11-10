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

  // A terminal in the bot's VM: one command at a time as user bot, output as
  // it comes, Stop ends it. The directory carries over to the next command.
  MAG.terminal = function (bot) {
    let cwd = '', running = null, at = 0;
    const history = [];
    const short = (p) => (p || '~').replace(/^\/home\/bot(?=\/|$)/, '~');
    const out = el('pre', { class: 'term-out' });
    const where = el('span', { class: 'term-cwd', text: '~' });
    const input = el('input', { type: 'text', class: 'term-in', autocapitalize: 'off', autocomplete: 'off', spellcheck: false, enterKeyHint: 'send', placeholder: 'Befehl', 'aria-label': 'Befehl' });
    input.setAttribute('autocorrect', 'off');
    const go = el('button', { class: 'pill', text: 'Ausführen', onclick: () => (running ? running.abort() : run()) });
    const node = el('div', { class: 'term' }, out, el('div', { class: 'term-row' }, where, input, go));
    const show = (text, cls) => {
      out.append(cls ? el('span', { class: cls, text }) : text);
      if (out.textContent.length > 240000) out.textContent = out.textContent.slice(-160000);
      out.scrollTop = out.scrollHeight;
    };
    async function run() {
      const command = input.value;
      if (!command.trim() || running) return;
      history.push(command);
      at = history.length;
      input.value = '';
      show(short(cwd) + ' $ ' + command + '\n', 'cmd');
      running = new AbortController();
      go.textContent = 'Stop';
      go.classList.add('danger');
      input.disabled = true;
      try {
        const res = await MAG.fetch('/api/vm/' + bot + '/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command, cwd }), signal: running.signal });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Fehler ' + res.status);
        const reader = res.body.getReader(), decoder = new TextDecoder();
        let tail = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          let text = tail + decoder.decode(value, { stream: true });
          const mark = text.indexOf('\x1e'); // the closing record: exit code and directory
          tail = mark >= 0 ? text.slice(mark) : '';
          if (mark >= 0) text = text.slice(0, mark);
          if (text) show(text);
        }
        let info = {};
        try { info = JSON.parse(tail.slice(1)); } catch {}
        if (info.cwd) cwd = info.cwd;
        if (info.error) show(info.error + '\n', 'err');
        else if (info.cancelled) show('^C\n', 'err');
        else if (info.timeout) show('Zeitlimit erreicht\n', 'err');
        else if (info.exit) show('Exit ' + info.exit + '\n', 'err');
      } catch (err) {
        show((err.name === 'AbortError' ? '^C' : err.message) + '\n', 'err');
      }
      running = null;
      go.textContent = 'Ausführen';
      go.classList.remove('danger');
      input.disabled = false;
      where.textContent = short(cwd);
      if (matchMedia('(pointer: fine)').matches) input.focus();
    }
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); run(); }
      else if (e.key === 'ArrowUp' && at > 0) { e.preventDefault(); input.value = history[--at]; }
      else if (e.key === 'ArrowDown' && at < history.length) { e.preventDefault(); input.value = history[++at] || ''; }
    });
    return { node, stop() { running?.abort(); } };
  };

  // The bot's own files: look around, open, download, delete.
  MAG.files = function (bot) {
    const crumbs = el('div', { class: 'fx-path' }), list = el('div', { class: 'fx-list' }), view = el('div', { class: 'fx-view', hidden: true });
    const node = el('div', { class: 'fx' }, crumbs, list, view);
    const api = (what, path, extra) => '/api/vm/' + bot + '/' + what + '?path=' + encodeURIComponent(path) + (extra || '');
    async function open(path) {
      view.hidden = true;
      list.hidden = false;
      list.replaceChildren(el('div', { class: 'fx-note', text: 'Lädt …' }));
      try {
        const out = await MAG.api('GET', api('files', path || '.'));
        const rel = out.path === out.home ? [] : out.path.slice(out.home.length + 1).split('/');
        crumbs.replaceChildren(el('button', { text: '~', onclick: () => open(out.home) }),
          ...rel.flatMap((part, i) => [el('span', { text: '/' }), el('button', { text: part, onclick: () => open(out.home + '/' + rel.slice(0, i + 1).join('/')) })]));
        const rows = out.entries.sort((a, b) => b.dir - a.dir || a.name.localeCompare(b.name, 'de'));
        list.replaceChildren(...rows.map((entry) => row(entry, out.path)));
        if (!rows.length) list.append(el('div', { class: 'fx-note', text: 'Leer' }));
      } catch (err) {
        list.replaceChildren(el('div', { class: 'fx-note err', text: err.message }));
      }
    }
    function row(entry, dir) {
      const full = dir + '/' + entry.name, url = api('file', full);
      const more = el('button', { class: 'act', 'aria-label': 'Aktionen', html: MAG.icons.dots, onclick: (e) => {
        e.stopPropagation();
        MAG.menu(e.currentTarget, [
          entry.dir ? null : { icon: 'open', label: 'In neuem Tab öffnen', run: () => window.open(url, '_blank', 'noopener') },
          entry.dir ? null : { icon: 'down', label: 'Herunterladen', run: () => { const a = el('a', { href: url + '&download=1', download: entry.name }); document.body.append(a); a.click(); a.remove(); } },
          { icon: 'trash', label: 'Löschen', danger: true, run: async () => {
            if (!(await MAG.confirm(entry.name + ' löschen?', entry.dir ? 'Der Ordner und alles darin wird in der VM gelöscht.' : 'Die Datei wird in der VM gelöscht.', 'Löschen', true))) return;
            MAG.api('DELETE', api('file', full)).then(() => open(dir)).catch((x) => MAG.toast(x.message, true));
          } },
        ]);
      } });
      return el('div', { class: 'fx-row' + (entry.dir ? ' dir' : ''), onclick: () => (entry.dir ? open(full) : look(entry, dir, url)) },
        MAG.icon(entry.dir ? 'folder' : 'file'), el('span', { class: 'nm', text: entry.name }), el('small', { text: entry.dir ? '' : MAG.bytes(entry.size) }), more);
    }
    // Pictures open over the chat, small text files right here, the rest in a tab.
    async function look(entry, dir, url) {
      if (/\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(entry.name)) return MAG.lightbox(url, entry.name);
      if (entry.size > 400000) return window.open(url, '_blank', 'noopener');
      try {
        const text = await MAG.api('GET', url + '&text=1');
        view.replaceChildren(el('div', { class: 'bar' }, el('button', { class: 'pill', text: '← Zurück', onclick: () => open(dir) }), el('b', { text: entry.name })),
          el('pre', { text: typeof text === 'string' ? text : JSON.stringify(text, null, 2) }));
        list.hidden = true;
        view.hidden = false;
      } catch (err) { MAG.toast(err.message, true); }
    }
    open('');
    return { node, stop() {} };
  };
})();

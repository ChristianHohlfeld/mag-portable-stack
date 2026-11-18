// What the bots write, change and delete in their VMs, for Chris alone
// (filewatch.hpp; "Dateiänderungen" in the settings). A quiet button in the
// header with the number of entries he has not opened yet; its sheet lists
// bot, file, kind and time, and an entry unfolds to the file's content or the
// diff against the last state the room saw. Nothing of it is in the chat, and
// the bots know nothing of it. The page asks every few seconds while it is in
// view and the switch is on.
'use strict';
(function () {
  const { MAG } = window, { el, $ } = MAG, S = MAG.state;
  const owner = () => S.you?.role === 'owner';
  const on = () => owner() && S.settings?.file_watch !== false;
  const KEY = 'mag-filewatch-seen';
  let entries = [], last = 0, timer = null, sheetList = null;
  const seen = () => +(localStorage.getItem(KEY) || 0);
  const when = (ts) => new Date(ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
    (new Date(ts).toDateString() === new Date().toDateString() ? '' : ' · ' + new Date(ts).toLocaleDateString('de-DE'));
  const sign = { 'neu': '+', 'geändert': '~', 'gelöscht': '−' };

  const button = el('button', { id: 'filewatch', class: 'icon-btn', type: 'button', hidden: true,
    'aria-label': 'Dateiänderungen der Bots', title: 'Dateiänderungen der Bots',
    html: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 13h6M9 17h4"/></svg><span class="fw-count"></span>' });
  $('#files')?.before(button);
  function paintCount() {
    const fresh = entries.filter((e) => e.n > seen()).length;
    button.querySelector('.fw-count').textContent = fresh ? (fresh > 99 ? '99+' : String(fresh)) : '';
    button.hidden = !on();
  }

  async function poll() {
    if (!on() || document.visibilityState !== 'visible') return;
    try {
      const out = await MAG.api('GET', '/api/filewatch?after=' + last);
      const got = out.entries || [];
      if (got.length) {
        entries = entries.concat(got).slice(-400);
        last = got[got.length - 1].n;
        if (sheetList?.isConnected) { sheetList.querySelector(':scope > p.fine')?.remove(); sheetList.prepend(...got.slice().reverse().map(row)); localStorage.setItem(KEY, String(last)); }
      }
    } catch (_) { /* next time */ }
    paintCount();
  }
  function schedule() {
    clearInterval(timer);
    timer = on() ? setInterval(poll, 4000) : null;
    paintCount();
    if (on()) poll();
  }

  function diffView(text, how) {
    const pre = el('pre', { class: 'fw-detail' });
    if (how !== 'diff') { pre.textContent = text; return pre; }
    for (const line of text.split('\n')) {
      const cls = /^\+(?!\+\+)/.test(line) ? 'add' : /^-(?!--)/.test(line) ? 'del' : /^@@/.test(line) ? 'hunk' : '';
      pre.append(el('span', { class: cls, text: line + '\n' }));
    }
    return pre;
  }
  function row(e) {
    const label = { diff: 'Änderung', inhalt: 'Inhalt', zuletzt: 'Zuletzt bekannter Inhalt', anfang: 'Anfang (Datei größer als 64 KB)', binär: 'Binärdatei' }[e.detail_kind] || '';
    const head = el('summary', {},
      el('span', { class: 'fw-kind fw-' + (sign[e.kind] ? e.kind.replace('ä', 'ae').replace('ö', 'oe') : 'more'), text: sign[e.kind] || '…', title: e.kind }),
      el('span', { class: 'fw-path', text: e.path }),
      el('small', { text: [MAG.name(e.bot), e.kind, when(e.ts), e.size != null ? MAG.bytes(e.size) : ''].filter(Boolean).join(' · ') }));
    const item = el('details', { class: 'fw-row' }, head);
    item.addEventListener('toggle', () => {
      if (!item.open || item.dataset.loaded) return;
      item.dataset.loaded = '1';
      if (!e.has_detail) { item.append(el('p', { class: 'fine', text: label || 'Kein Inhalt (zu groß, nicht lesbar oder gelöscht).' })); return; }
      const wait = el('p', { class: 'fine', text: 'Lädt …' });
      item.append(wait);
      MAG.api('GET', '/api/filewatch/' + e.n).then((full) => {
        wait.replaceWith(el('p', { class: 'fine', text: label }), diffView(full.detail || '', full.detail_kind));
      }, (x) => { wait.textContent = x.message; });
    });
    return item;
  }

  function open() {
    MAG.sheet('Dateiänderungen', (content, sheet) => {
      sheet.classList.add('wide');
      sheetList = el('div', { class: 'fw-list' });
      const names = (S.bots || []).map((b) => b.name).join(' und ') || 'die Bots';
      content.append(el('p', { class: 'fine', text: 'Was ' + names + ' in ihren VMs anlegen, ändern oder löschen. Nur du siehst das; die Bots wissen nichts davon.' }), sheetList);
      sheetList.append(...entries.slice().reverse().map(row));
      if (!entries.length) sheetList.append(el('p', { class: 'fine', text: 'Noch nichts geändert.' }));
      localStorage.setItem(KEY, String(last));
      paintCount();
    });
  }

  button.addEventListener('click', open);
  MAG.on('settings', schedule);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') poll(); });
  MAG.fileWatch = { open, poll };
})();

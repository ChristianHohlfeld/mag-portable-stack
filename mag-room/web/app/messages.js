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
  // `live`: the text is still being written. Long code is then left plain
  // and coloured once at the end, so a phone keeps up with the stream.
  function markdown(text, live) {
    if (!window.marked || !window.DOMPurify) return el('div', { class: 'md', text });
    const html = DOMPurify.sanitize(marked.parse(text || ''), { FORBID_TAGS: ['style', 'form', 'input', 'iframe', 'object', 'embed'], FORBID_ATTR: ['style', 'onerror', 'onclick'] });
    const box = el('div', { class: 'md', html });
    box.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    MAG.mentions?.decorate(box);
    box.querySelectorAll('img').forEach((img) => { img.loading = 'lazy'; });
    box.querySelectorAll('pre').forEach((pre) => {
      const code = pre.querySelector('code');
      const lang = ((code?.className || '').match(/language-([\w+#.-]+)/) || [])[1] || '';
      const known = lang && window.hljs?.getLanguage(lang);
      if (code && known && code.textContent.length < (live ? 6000 : 60000)) { try { hljs.highlightElement(code); } catch {} }
      // Language and copy in a quiet head, the code below.
      const block = el('div', { class: 'codeblock' });
      pre.replaceWith(block);
      block.append(el('div', { class: 'head' }, el('span', { text: lang || 'Code' }),
        el('button', { class: 'copy', text: 'Kopieren', onclick: (e) => copy(code ? code.textContent : pre.textContent, e.target) })), pre);
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
    // A small round arrow above the composer; it carries words only when
    // something new arrived below.
    jump.hidden = pinned;
    jump.classList.toggle('news', unread > 0);
    jump.lastChild.textContent = unread > 0 ? (unread === 1 ? 'Neue Nachricht' : unread + ' neue Nachrichten') : '';
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
    // A plugin call that changes something waits here for a yes or a no (not one a restart ended).
    const ask = step.state === 'running' ? step.approval : null;
    const node = el('details', { class: 'step ' + step.state + (ask ? ' asking' : ''), dataset: { part: index } });
    const decide = (decision) => (e) => {
      e.preventDefault();
      e.currentTarget.parentNode.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      MAG.api('POST', '/api/approval/' + ask.id, { decision }).catch((x) => MAG.toast(x.message, true));
    };
    // A plugin tool, or an action the bot asked about (consent: send, buy, delete, publish, a routine).
    const euro = (n) => n.toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
    const asking = !ask ? null : ask.action === 'login' ? loginCard(m, ask) : ask.action === 'pay' ? payCard(m, ask) : ask.action ? el('span', { class: 'ask' },
      el('b', { text: MAG.name(m.from) + ' bittet um Freigabe: ' + ask.label + (ask.amount_eur ? ' · ' + euro(ask.amount_eur) : '') + (ask.to ? ' · ' + ask.to : '') }),
      el('span', { class: 'what', text: ask.what }),
      S.you.role === 'owner' ? el('span', { class: 'btns' },
        el('button', { class: 'yes', text: 'Erlauben', onclick: decide('allow') }),
        el('button', { class: 'no', text: 'Ablehnen', onclick: decide('deny') })) : null)
      : el('span', { class: 'ask' },
      el('b', { text: MAG.name(m.from) + ' möchte bei ' + ask.server + ' „' + ask.tool + '“ ausführen.' }),
      el('code', { text: ask.args }),
      S.you.role === 'owner' ? el('span', { class: 'btns' },
        el('button', { class: 'yes', text: 'Erlauben', onclick: decide('allow') }),
        el('button', { text: 'Immer erlauben', title: 'Dieses Werkzeug darf ' + MAG.name(m.from) + ' künftig ohne Nachfrage nutzen', onclick: decide('always') }),
        el('button', { class: 'no', text: 'Ablehnen', onclick: decide('deny') })) : null);
    const cancel = step.state === 'running'
      ? el('button', { class: 'cancel', text: 'Abbrechen', onclick: (e) => { e.preventDefault(); MAG.api('POST', '/api/stop', { bot: m.from }).catch((x) => MAG.toast(x.message, true)); } })
      : null;
    // Collapsed, a running step still shows its last lines as they arrive.
    const shot = step.shot ? '/api/shot/' + m.id + '-' + index + '.jpg' : '';
    node.append(el('summary', {},
      el('span', { class: 'mark', html: stepMark[step.state] || '' }),
      el('span', { class: 'title', text: step.title || step.tool }),
      el('span', { class: 'time', text: stepTime(step) + (step.exit ? ' · Exit ' + step.exit : '') }),
      cancel,
      el('span', { class: 'chev', html: MAG.icons.chev }),
      asking,
      step.state === 'running' && !ask ? el('span', { class: 'peek', text: lastLines(step.output || '') }) : null,
      // The browser as this step left it: small in the line, large when the step is open.
      shot && latestShot(m) === index ? el('img', { class: 'shot-mini', src: shot, alt: '', loading: 'lazy' }) : null));
    const out = el('pre', { class: 'out', text: step.output || '' });
    const input = stepInput(step);
    node.append(el('div', { class: 'body' },
      shot ? el('img', { class: 'shot', src: shot, alt: 'Browser nach diesem Schritt', loading: 'lazy' }) : null,
      input ? el('div', { class: 'label', text: step.tool === 'shell' ? 'Befehl' : 'Eingabe' }) : null,
      input ? el('pre', { class: 'cmd', text: input }) : null,
      el('div', { class: 'label', text: step.state === 'running' ? 'Ausgabe (live)' : 'Ausgabe' + (step.cut ? ' (Ende; vollständig im Log der VM)' : '') }),
      out));
    node.addEventListener('toggle', () => { if (node.open) out.scrollTop = out.scrollHeight; });
    return node;
  }
  // A page in a bot's browser wants a login. Chris types it here (the room
  // fills it into the page; the bot never sees it, nothing is stored; the
  // phone's password manager can fill the form) or takes the browser over.
  function loginCard(m, ask) {
    const name = MAG.name(m.from), owner = S.you.role === 'owner';
    const note = el('small', { class: 'note' });
    const decide = (decision) => MAG.api('POST', '/api/approval/' + ask.id, { decision }).catch((x) => MAG.toast(x.message, true));
    const user = el('input', { type: 'text', name: 'username', autocomplete: 'username', placeholder: 'E-Mail oder Benutzer', autocapitalize: 'off', spellcheck: false });
    const pass = el('input', { type: 'password', name: 'password', autocomplete: 'current-password', placeholder: 'Passwort' });
    const go = el('button', { class: 'yes', type: 'submit', text: 'Anmelden' });
    const form = el('form', { class: 'login', onsubmit: (e) => {
      e.preventDefault();
      go.disabled = true;
      note.textContent = 'Wird in den Browser von ' + name + ' eingetragen …';
      MAG.api('POST', '/api/login/' + ask.id, { user: user.value, password: pass.value })
        .then(() => { pass.value = ''; note.textContent = 'Angemeldet; ' + name + ' macht weiter.'; },
          (x) => { go.disabled = false; note.textContent = x.message; });
    } }, user, pass, go);
    // The bot's browser in a sheet: Chris logs in himself (passkey, code), then lets the bot go on.
    const takeOver = () => browserSheet(m.from, 'Melde dich hier selbst an (Passkey, Code per SMS, Captcha). Danach macht ' + name + ' weiter.', () => decide('done'));
    return el('span', { class: 'ask' },
      el('b', { text: name + ' braucht eine Anmeldung bei ' + (ask.site || 'einer Seite') }),
      ask.what ? el('span', { class: 'what', text: ask.what }) : null,
      owner ? form : null,
      owner ? el('span', { class: 'btns' },
        el('button', { text: 'Selbst im Browser', onclick: (e) => { e.preventDefault(); takeOver(); } }),
        el('button', { class: 'no', text: 'Abbrechen', onclick: (e) => { e.preventDefault(); decide('cancel'); } })) : null,
      note);
  }

  // A checkout in a bot's browser: Chris picks a saved card and gives its
  // security code; the room types the card into the page (the bot never
  // sees it). Or he pays himself in the bot's browser.
  function payCard(m, ask) {
    const name = MAG.name(m.from), owner = S.you.role === 'owner';
    const note = el('small', { class: 'note' });
    const decide = (decision) => MAG.api('POST', '/api/approval/' + ask.id, { decision }).catch((x) => MAG.toast(x.message, true));
    const choice = el('select', { 'aria-label': 'Karte' });
    const cvc = el('input', { type: 'password', inputMode: 'numeric', autocomplete: 'cc-csc', placeholder: 'Prüfnummer (CVC)', maxLength: 4 });
    const go = el('button', { class: 'yes', type: 'submit', text: 'Karte eintragen' });
    if (owner) MAG.api('GET', '/api/pay/cards').then((out) => {
      if (!out.cards.length) { note.textContent = 'Noch keine Karte gespeichert (Einstellungen › Zahlung). Oder bezahle selbst im Browser.'; go.disabled = true; }
      for (const c of out.cards) choice.append(el('option', { value: c.id, text: c.brand + ' •••• ' + c.last4 + ' · ' + c.exp }));
    }).catch(() => {});
    const form = el('form', { class: 'login', onsubmit: (e) => {
      e.preventDefault();
      go.disabled = true;
      note.textContent = 'Wird in den Browser von ' + name + ' eingetragen …';
      MAG.api('POST', '/api/pay/fill/' + ask.id, { card: choice.value, cvc: cvc.value })
        .then(() => { cvc.value = ''; note.textContent = 'Eingetragen; ' + name + ' schließt die Bestellung ab.'; },
          (x) => { go.disabled = false; note.textContent = x.message; });
    } }, choice, cvc, go);
    const euro = ask.amount_eur ? ask.amount_eur.toLocaleString('de-DE', { style: 'currency', currency: 'EUR' }) : '';
    return el('span', { class: 'ask' },
      el('b', { text: name + ' ist an der Kasse: ' + (ask.site || '') + (euro ? ' · ' + euro : '') }),
      ask.what ? el('span', { class: 'what', text: ask.what }) : null,
      owner ? form : null,
      owner ? el('span', { class: 'btns' },
        el('button', { text: 'Selbst im Browser', onclick: (e) => { e.preventDefault(); browserSheet(m.from, 'Bezahle hier selbst. Danach macht ' + name + ' weiter.', () => decide('done')); } }),
        el('button', { class: 'no', text: 'Abbrechen', onclick: (e) => { e.preventDefault(); decide('cancel'); } })) : null,
      note);
  }
  // The bot's browser in a sheet, with one button that lets the bot go on.
  function browserSheet(bot, hint, done) {
    let live = null;
    const sheet = MAG.sheet('Browser von ' + MAG.name(bot), (content, _, close) => {
      live?.stop();
      live = MAG.liveView(bot);
      content.append(el('p', { class: 'fine', text: hint }), live.node,
        el('div', { class: 'btns' }, el('button', { class: 'btn', text: 'Fertig, weiter', onclick: () => { done(); close(); } })));
    });
    sheet.addEventListener('closed', () => live?.stop());
  }

  // Only the newest browser picture of an answer stands in its line; the others open with their step.
  const latestShot = (m) => (m.parts || []).reduce((at, p, i) => (p.shot ? i : at), -1);
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
    frame.setAttribute('credentialless', '');   // a bot's page may load from anywhere, without anybody's cookies
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
    if (pre) {
      const block = pre.closest('.codeblock') || pre;
      block.before(card);
      code.append(pre);
      if (block !== pre) block.remove();
    }
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
  // A picture of the chat goes on: to a 3D model (which can then be animated)
  // or to a video. Both are jobs of the MI50's one line.
  function makeModel(a) {
    MAG.api('POST', '/api/image/act', { file: a.file, do: '3d', quality: localStorage.getItem('mag-3d-quality') || 'normal' })
      .then(() => MAG.scrollToEnd(), (x) => MAG.toast(x.message, true));
  }
  function makeVideo(a) {
    MAG.sheet('Video aus dem Bild', (content, _, close) => {
      const words = el('textarea', { rows: 3, placeholder: 'Was soll passieren? z. B. „dreht sich lächelnd zur Kamera und winkt“' });
      const go = el('button', { class: 'pill on', type: 'submit' }, MAG.icon('play'), el('b', { text: 'Video erzeugen' }));
      const form = el('form', { class: 'video-ask', onsubmit: (e) => {
        e.preventDefault();
        go.disabled = true;
        MAG.api('POST', '/api/image/act', { file: a.file, do: 'video', prompt: words.value.trim() })
          .then(() => { close(); MAG.scrollToEnd(); }, (x) => { go.disabled = false; MAG.toast(x.message, true); });
      } }, el('img', { src: fileUrl(a), alt: '' }), words,
        el('p', { class: 'hint', text: 'Etwa fünf Sekunden, 480p. Läuft auf der Grafikkarte, wenn sie frei ist.' }), go);
      content.append(form);
      setTimeout(() => words.focus(), 50);
    });
  }
  function buildAttachment(a) {
    // What the bots were told about a picture is its caption (and its alt text).
    if (a.kind === 'image') {
      const pic = el('button', { class: 'att-img', title: a.description || a.name, onclick: () => lightbox(fileUrl(a), a.name, a.description, a.file) },
        el('img', { src: fileUrl(a), alt: a.description || a.name, loading: 'lazy' }));
      if (S.you?.role !== 'owner') return pic;
      return el('div', { class: 'att-pic' }, pic, el('div', { class: 'pic-acts' },
        el('button', { class: 'pill', title: '3D-Modell aus diesem Bild (danach animierbar)', onclick: () => makeModel(a) }, MAG.icon('cube'), el('b', { text: '3D' })),
        el('button', { class: 'pill', title: 'Video aus diesem Bild', onclick: () => makeVideo(a) }, MAG.icon('play'), el('b', { text: 'Video' }))));
    }
    if (a.kind === 'audio') return el('audio', { controls: true, preload: 'none', src: fileUrl(a) });
    if (a.kind === 'video') return el('video', { controls: true, preload: 'metadata', src: fileUrl(a), playsInline: true });
    return el('a', { class: 'att-file', href: fileUrl(a, '?download=1'), download: a.name }, MAG.icon(a.kind === 'model' ? 'cube' : 'file'),
      el('span', { text: a.name }), el('small', { text: MAG.bytes(a.size) }));
  }
  // A picture over the chat: Esc, the back gesture, a swipe or the X close it.
  function lightbox(src, name, caption, file) {
    const img = el('img', { src, alt: caption || name || '' });
    // What the bots were told the picture shows. If that is wrong, the owner has it looked at again.
    const again = file && caption && S.you.role === 'owner'
      ? el('button', { class: 'pill', title: 'Die Beschreibung stimmt nicht: ein Bot sieht das Bild noch einmal an', onclick: () =>
          MAG.api('POST', '/api/file/' + encodeURIComponent(file) + '/describe').then(() => { MAG.toast('Das Bild wird neu beschrieben'); close(); }, (x) => MAG.toast(x.message, true)) },
          MAG.icon('reset'), 'Neu beschreiben')
      : null;
    const leave = () => {
      box.remove();
      document.removeEventListener('keydown', key);
      window.removeEventListener('popstate', leave);
    };
    const close = () => (history.state?.magbox ? history.back() : leave());
    const key = (e) => e.key === 'Escape' && close();
    const box = el('div', { id: 'lightbox', onclick: (e) => e.target === box && close() }, img,
      el('div', { class: 'bar' },
        caption ? el('p', { class: 'cap', text: caption }) : null, again,
        el('a', { class: 'pill', href: src, target: '_blank', rel: 'noopener' }, MAG.icon('open'), 'In neuem Tab öffnen'),
        el('a', { class: 'pill', href: src + '?download=1', download: name || '' }, MAG.icon('down'), 'Herunterladen')),
      el('button', { class: 'icon-btn', 'aria-label': 'Schließen', html: MAG.icons.x, onclick: close }));
    let from = null;
    box.addEventListener('touchstart', (e) => { from = e.touches.length === 1 ? e.touches[0].clientY : null; }, { passive: true });
    box.addEventListener('touchmove', (e) => {
      if (from === null || e.touches.length !== 1) return;
      const dy = e.touches[0].clientY - from;
      img.style.transform = 'translateY(' + dy + 'px)';
      box.style.setProperty('--fade', String(Math.max(0.35, 1 - Math.abs(dy) / 400)));
    }, { passive: true });
    box.addEventListener('touchend', (e) => {
      if (from === null) return;
      const dy = e.changedTouches[0].clientY - from;
      from = null;
      if (Math.abs(dy) > 90) return close();
      img.style.transform = '';
      box.style.removeProperty('--fade');
    });
    history.pushState({ magbox: true }, '');
    window.addEventListener('popstate', leave);
    document.addEventListener('keydown', key);
    document.body.append(box);
  }
  // The description a seeing bot wrote for a room file, if there is one.
  function captionOf(file) {
    for (const m of S.messages.values())
      for (const a of m.attachments || []) if (a.file === file && a.description) return a.description;
    return '';
  }
  MAG.lightbox = lightbox;
  // A message of the loaded history, scrolled to and marked for a moment.
  MAG.showMessage = (id) => {
    const node = nodes.get(id);
    if (!node) return false;
    node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    node.classList.remove('flash');
    void node.offsetWidth;
    node.classList.add('flash');
    return true;
  };
  // What a model or a scene should do is said right below it. The line goes
  // into the chat as a message about it, to the bot that built the scene if
  // it is there, else to one that is. A scene shows the request it was made
  // from, ready to be changed.
  const askBot = (id) => {
    const there = (b) => b.enabled !== false, ready = (b) => there(b) && b.model?.state === 'ready';
    const own = MAG.bot(id);
    return (own && ready(own) ? own : S.bots.find(ready) || S.bots.find(there) || S.bots[0])?.name || '';
  };
  function wish(m, label, hint, words) {
    const input = el('input', { type: 'text', value: words || '', placeholder: hint, 'aria-label': label, maxLength: 400, autocomplete: 'off', enterKeyHint: 'send' });
    const clear = el('button', { type: 'button', class: 'clear', 'aria-label': 'Feld leeren', title: 'Feld leeren', html: MAG.icons.x });
    const go = el('button', { class: 'go', 'aria-label': 'An einen Bot senden', title: 'An einen Bot senden', html: MAG.icons.send });
    const form = el('form', { class: 'wish' }, el('small', { text: label }), el('label', {}, MAG.icon('film'), input, clear), go);
    const paint = () => { clear.hidden = !input.value; go.disabled = !input.value.trim() || form.classList.contains('busy'); };
    clear.addEventListener('click', () => { input.value = ''; paint(); input.focus(); });
    input.addEventListener('input', paint);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = input.value.trim(), bot = askBot(m.model3d.by);
      if (go.disabled) return;
      form.classList.add('busy');
      paint();
      try {
        await MAG.api('POST', '/api/message', { text: (bot ? '@' + bot + ' ' : '') + text, about: m.id, client_id: crypto.randomUUID?.() || String(Math.random()) });
        input.value = words || '';
        input.blur();
        MAG.scrollToEnd();
      } catch (err) {
        MAG.toast(err.message, true);
      }
      form.classList.remove('busy');
      paint();
    });
    paint();
    return form;
  }
  // The view and the line below it stay while the card around them changes (see upsert).
  const play = (key, stage, line) => el('div', { class: 'play', dataset: { keep: key } }, stage, line);
  const quality = { fast: 'Schnell', normal: 'Normal', high: 'Hoch' };
  const kinds = { glb: 'Modell mit Textur', stl: 'für den 3D-Druck', ply: 'Punktwolke (Gaussian Splat)' };
  // Runtime, and what the sampler itself expects to remain for its step.
  // "Fortgesetzt": the job went on after a restart of the room or the worker.
  function clock3d(d) {
    const now = Date.now() / 1000;
    let text = +d.since ? (d.resumed ? 'Fortgesetzt · läuft seit ' : 'Läuft seit ') + MAG.stopwatch((now - d.since) * 1000) : 'Wartet auf die Grafikkarte';
    const left = +d.eta ? d.eta - (now - d.etaAt) : 0;
    if (left > 20) text += ' · dieser Schritt noch etwa ' + (left < 90 ? Math.round(left / 10) * 10 + ' s' : Math.round(left / 60) + ' min');
    return text;
  }
  // "Schritt 2 von 5" with the step's real name; one segment per step. Only
  // the sampler counts its work (n/50), so only its segment fills gradually.
  function progress3d(m3) {
    const n = m3.phases || 0, k = m3.state === 'running' ? m3.phase || 0 : 0;
    const counted = k === 2 && m3.steps > 0;
    const title = k ? 'Schritt ' + k + ' von ' + n + ' · ' + m3.stage + (counted ? ' · ' + (m3.step || 0) + '/' + m3.steps : '') : waiting(m3);
    const segs = el('div', { class: 'segs', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': n || 1, 'aria-valuenow': k, 'aria-valuetext': title });
    for (let i = 1; i <= (n || 1); i++) {
      const fill = i < k ? 1 : i === k && counted ? (m3.step || 0) / m3.steps : 0;
      segs.append(el('i', { class: (i === 2 ? 'long' : '') + (i === k && !counted ? ' busy' : '') }, el('b', { style: 'width:' + Math.round(fill * 100) + '%' })));
    }
    const clock = el('span', { class: 'clock', dataset: { since: m3.started || 0, eta: m3.eta || 0, etaAt: m3.eta_at || 0, resumed: m3.resumed ? '1' : '' } });
    clock.textContent = clock3d(clock.dataset);
    return el('div', { class: 'prog' }, el('div', { class: 'what', text: title }), segs, clock);
  }
  // A waiting job of the MI50's line, moved where Chris wants it (cancelling is beside it).
  function lineActs(m) {
    const move = (to) => (e) => { e.currentTarget.disabled = true; MAG.api('POST', '/api/3d/' + m.id + '/move', { to }).catch((x) => MAG.toast(x.message, true)); };
    return el('div', { class: 'line-acts' },
      el('button', { class: 'pill', title: 'Einen Platz nach vorn in der Warteschlange', disabled: !(m.model3d.ahead > 0), onclick: move('up') }, el('b', { text: '↑ Nach vorn' })),
      el('button', { class: 'pill', title: 'Einen Platz nach hinten in der Warteschlange', onclick: move('down') }, el('b', { text: '↓ Nach hinten' })));
  }
  // Its place in the MI50's one line: whoever has the card has it alone.
  const waiting = (m3) => 'In der Warteschlange' + (m3.ahead > 0 ? ' · ' + (m3.ahead === 1 ? '1 Auftrag' : m3.ahead + ' Aufträge') + ' davor' : '');
  // A picture being painted from words: what was asked, how far it is, and
  // a way to stop it or try again. The picture itself is the message's image.
  function buildPicture(m) {
    const m3 = m.model3d, card = el('div', { class: 'm3 image ' + m3.state });
    const running = m3.state === 'queued' || m3.state === 'running';
    const video = m3.kind === 'video', model = video ? 'Wan 2.2' : 'FLUX.2 klein';
    const sub = m3.state === 'done' ? (m3.took ? model + ' · ' + MAG.dur(m3.took * 1000) : model)
      : m3.state === 'error' ? (m3.error || 'Fehler') : m3.state === 'cancelled' ? 'Abgebrochen' : m3.state === 'queued' ? waiting(m3) : m3.stage || 'Malt';
    // Text to video: the picture comes first, the video follows by itself.
    const next = m3.then_video && m3.state !== 'error' && m3.state !== 'cancelled' ? ' · danach das Video'
      : m3.video_error ? ' · Video: ' + m3.video_error : '';
    // A video shows the picture it comes from.
    const src = video && m3.image ? el('span', { class: 'src' }, el('img', { src: '/api/file/' + encodeURIComponent(m3.image), alt: '', loading: 'lazy' }))
      : el('span', { class: 'src' }, MAG.icon(video ? 'play' : 'image'));
    card.append(el('div', { class: 'head' }, src,
      el('div', { class: 'txt' }, el('b', { text: m3.prompt || (video ? 'Video' : 'Bild') }), el('small', { text: sub + next })),
      running ? el('button', { class: 'pill danger', text: 'Abbrechen', onclick: () => MAG.api('POST', '/api/3d/' + m.id + '/cancel').catch((x) => MAG.toast(x.message, true)) })
        : m3.state === 'error' || m3.state === 'cancelled' ? el('button', { class: 'pill', text: 'Noch einmal', onclick: (e) => { e.currentTarget.disabled = true; MAG.api('POST', '/api/3d/' + m.id + '/retry').catch((x) => MAG.toast(x.message, true)); } }) : null));
    if (running) {
      const part = m3.state === 'running' && m3.steps ? (m3.phase === 2 ? (m3.step || 0) / m3.steps : 0.05) : 0;
      card.append(el('div', { class: 'prog' }, el('div', { class: 'segs' }, el('i', { style: 'width:' + Math.round(100 * part) + '%' }))));
    }
    if (m3.state === 'queued') card.append(lineActs(m));
    return card;
  }
  function build3d(m) {
    const m3 = m.model3d, card = el('div', { class: 'm3 ' + m3.state });
    const running = m3.state === 'queued' || m3.state === 'running';
    const src = m3.image ? '/api/file/' + encodeURIComponent(m3.image) : '';
    // The photo it is made from: a thumbnail that opens over the chat.
    const thumb = src
      ? el('button', { class: 'src', 'aria-label': 'Ausgangsbild ansehen', title: 'Ausgangsbild', onclick: () => lightbox(src, m3.source, captionOf(m3.image), m3.image) },
          el('img', { src, alt: '', loading: 'lazy', onerror: (e) => e.target.closest('.src')?.replaceWith(el('span', { class: 'src' }, MAG.icon('cube'))) }))
      : el('span', { class: 'src' }, MAG.icon('cube'));
    const label = 'Qualität ' + (quality[m3.quality] || m3.quality);
    const sub = m3.state === 'done' ? label + (m3.took ? ' · ' + MAG.dur(m3.took * 1000) : '')
      : m3.state === 'error' ? (m3.error || 'Fehler') : m3.state === 'cancelled' ? 'Abgebrochen' : label;
    // A job that failed or was cancelled is taken up again where it stopped: what was done is not done twice.
    const again = (e) => { e.currentTarget.disabled = true; MAG.api('POST', '/api/3d/' + m.id + '/retry').catch((x) => { e.target.disabled = false; MAG.toast(x.message, true); }); };
    card.append(el('div', { class: 'head' }, thumb, el('div', { class: 'txt' }, el('b', { text: 'Foto zu 3D' }), el('small', { text: sub })),
      running ? el('button', { class: 'pill danger', text: 'Abbrechen', onclick: () => MAG.api('POST', '/api/3d/' + m.id + '/cancel').catch((x) => MAG.toast(x.message, true)) })
        : m3.state === 'error' || m3.state === 'cancelled' ? el('button', { class: 'pill', text: 'Noch einmal', title: 'Geht dort weiter, wo es aufgehört hat', onclick: again })
        // A finished model, made anew from what was sampled: a couple of minutes, nothing is sampled again.
        : m3.state === 'done' && S.you.role === 'owner' ? el('button', { class: 'pill', text: 'Netz neu', title: 'Baut das Modell aus dem vorhandenen Ergebnis neu, ohne neues Sampling',
          onclick: (e) => { e.currentTarget.disabled = true; MAG.api('POST', '/api/3d/' + m.id + '/retry', { remesh: true }).catch((x) => { e.target.disabled = false; MAG.toast(x.message, true); }); } }) : null));
    if (running) card.append(progress3d(m3));
    if (m3.state === 'queued') card.append(lineActs(m));
    const glb = (m.attachments || []).find((a) => a.name.endsWith('.glb'));
    if (m3.state === 'done' && glb) {
      const canvas = el('canvas', {});
      const stage = el('div', { class: 'stage' }, canvas, el('span', { class: 'wait', text: 'Modell wird geladen …' }), el('span', { class: 'hint' }),
        el('div', { class: 'tools' },
          el('button', { 'aria-label': 'Ansicht zurücksetzen', title: 'Ansicht zurücksetzen', html: MAG.icons.reset, onclick: () => MAG.viewer3d.reset(canvas) }),
          el('button', { class: 'full', 'aria-label': 'Vollbild', title: 'Vollbild', html: MAG.icons.full + MAG.icons.x, onclick: () => MAG.viewer3d.fullscreen(stage) })));
      card.append(play(glb.file, stage, wish(m, 'Dieses Modell animieren', 'z. B. „läuft im Kreis und winkt“')));
      MAG.viewer3d.attach(canvas, fileUrl(glb));
      card.append(el('div', { class: 'links' }, (m.attachments || []).map((a) => {
        const ext = a.name.split('.').pop().toLowerCase();
        return el('a', { class: 'pill', href: fileUrl(a, '?download=1'), download: a.name, title: (kinds[ext] || a.name) + ' herunterladen' },
          MAG.icon('down'), el('b', { text: ext.toUpperCase() }), el('small', { text: (kinds[ext] ? kinds[ext] + ' · ' : '') + MAG.bytes(a.size) }));
      })));
    }
    return card;
  }
  // A scene a bot built from 3D models: it plays in the chat at once, from
  // every side. Its film is rendered when it is asked for and joins the card,
  // and so is its file for Blender.
  const sceneFiles = { glb: 'Szene mit Animation', mp4: 'Film', blend: 'für Blender' };
  async function fetchBlend(m, button) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const { file } = await MAG.api('POST', '/api/3d/' + m.id + '/blend');
      el('a', { href: fileUrl(file, '?download=1'), download: file.name }).click();
    } catch (err) {
      MAG.toast(err.message, true);
    }
    button.disabled = false;
  }
  // The sound of a scene's film: what its figures say, each in a voice of its
  // own, made on this device and laid where it is said; a WAV as base64, or ''
  // (the film is silent) where no voice can be made here.
  // What the figures of a scene say, as one WAV (the voices are made here on
  // the device, each fitted to how long its mouth moves): sent along with the
  // film, and played in the chat in step with the scene. Made once per scene.
  const voiced = new Map(); // message id -> Promise<Uint8Array | null>
  function sceneVoices(m) {
    if (!voiced.has(m.id)) voiced.set(m.id, mixVoices(m.model3d).catch(() => null));
    return voiced.get(m.id);
  }
  async function mixVoices(m3) {
    const said = (m3.speech || []).filter((s) => s && s.text);
    if (!said.length || !MAG.voices?.make) return null;
    let rate = 0, parts = [];
    for (const s of said) {
      const made = await MAG.voices.make(s.text, s.voice, +s.seconds || 0).catch(() => null);
      if (!made) continue;
      rate = rate || made.rate;
      parts.push({ at: Math.max(0, +s.start || 0), audio: made.audio });
    }
    if (!rate || !parts.length) return null;
    const total = Math.ceil(rate * Math.max(+m3.seconds || 0, ...parts.map((p) => p.at + p.audio.length / rate)));
    const mix = new Float32Array(total);
    for (const p of parts) {
      const from = Math.round(p.at * rate);
      for (let i = 0; i < p.audio.length && from + i < total; i++) mix[from + i] += p.audio[i];
    }
    const bytes = new DataView(new ArrayBuffer(44 + 2 * total));
    const text = (at, word) => { for (let i = 0; i < word.length; i++) bytes.setUint8(at + i, word.charCodeAt(i)); };
    text(0, 'RIFF'); bytes.setUint32(4, 36 + 2 * total, true); text(8, 'WAVE'); text(12, 'fmt ');
    bytes.setUint32(16, 16, true); bytes.setUint16(20, 1, true); bytes.setUint16(22, 1, true); bytes.setUint32(24, rate, true);
    bytes.setUint32(28, 2 * rate, true); bytes.setUint16(32, 2, true); bytes.setUint16(34, 16, true); text(36, 'data'); bytes.setUint32(40, 2 * total, true);
    for (let i = 0; i < total; i++) bytes.setInt16(44 + 2 * i, Math.max(-1, Math.min(1, mix[i])) * 32767, true);
    return new Uint8Array(bytes.buffer);
  }
  function base64(raw) {
    let bin = '';
    for (let i = 0; i < raw.length; i += 0x8000) bin += String.fromCharCode.apply(null, raw.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  // The scene's voices in the chat: they follow the viewer's clock (play,
  // pause, from the start, the loop), so the lips and the voice go together.
  function sceneSound(m, canvas, button) {
    let audio = null, timer = 0;
    const follow = () => {
      const st = canvas._viewer?.state?.();
      if (!audio || !st) return;
      if (!st.playing || st.time >= st.duration) { if (!audio.paused) audio.pause(); return; }
      if (Math.abs(audio.currentTime - st.time) > 0.12) audio.currentTime = Math.min(st.time, audio.duration || st.time);
      if (audio.paused && st.time < (audio.duration || 0)) audio.play().catch(() => {});
    };
    const off = () => { clearInterval(timer); timer = 0; audio?.pause(); button.classList.remove('on'); };
    button.addEventListener('click', async () => {
      if (timer) return off();
      button.disabled = true;
      const wav = await sceneVoices(m);
      button.disabled = false;
      if (!wav) return MAG.toast('Die Stimmen sind auf diesem Gerät nicht geladen', true);
      audio ||= new Audio(URL.createObjectURL(new Blob([wav], { type: 'audio/wav' })));
      button.classList.add('on');
      MAG.viewer3d.reset(canvas);
      audio.currentTime = 0;
      timer = setInterval(() => { if (!canvas.isConnected) off(); else follow(); }, 80);
      follow();
    });
  }
  function buildScene(m) {
    const m3 = m.model3d, card = el('div', { class: 'm3 scene ' + m3.state });
    const running = m3.state === 'queued' || m3.state === 'running';
    const files = m.attachments || [], glb = files.find((a) => a.name.endsWith('.glb')), film = files.find((a) => a.kind === 'video');
    const by = MAG.bot(m3.by)?.name;
    const facts = [m3.seconds ? String(Math.round(m3.seconds * 10) / 10).replace('.', ',') + ' s' : '', (m3.cast || []).join(', '), by ? 'von ' + by : ''].filter(Boolean).join(' · ');
    const sub = m3.state === 'error' ? 'Kein Film: ' + (m3.error || 'Fehler') : m3.state === 'cancelled' ? facts + ' · Film abgebrochen' : facts;
    const order = (path, body) => MAG.api('POST', '/api/3d/' + m.id + path, body).catch((x) => MAG.toast(x.message, true));
    card.append(el('div', { class: 'head' }, el('span', { class: 'src' }, MAG.icon('film')),
      el('div', { class: 'txt' }, el('b', { text: m3.title || '3D-Szene' }), el('small', { text: sub })),
      running ? el('button', { class: 'pill danger', text: 'Film abbrechen', onclick: () => order('/cancel') })
        // No film yet: it is rendered when it is asked for (of one that failed, what was rendered stays).
        : !film && glb ? el('button', { class: 'pill', title: 'Rendert die Szene als Video (MP4)', onclick: async (e) => {
            const b = e.currentTarget, label = b.querySelector('b');
            b.disabled = true;
            // What the figures say is spoken in the film, in voices made here first.
            if (m3.speech?.length && label) label.textContent = 'Stimmen …';
            const wav = m3.speech?.length ? await sceneVoices(m) : null, voice = wav ? base64(wav) : '';
            await order('/film', voice ? { voice } : undefined);
            if (label) label.textContent = 'Film rendern';
            b.disabled = false; } },
            MAG.icon('film'), el('b', { text: 'Film rendern' })) : null));
    if (glb) {
      const canvas = el('canvas', {});
      const stage = el('div', { class: 'stage' }, canvas, el('span', { class: 'wait', text: 'Szene wird geladen …' }), el('span', { class: 'hint' }),
        el('div', { class: 'tools' },
          el('button', { class: 'play', 'aria-label': 'Abspielen oder anhalten', title: 'Abspielen oder anhalten', html: MAG.icons.pause + MAG.icons.play, onclick: () => MAG.viewer3d.toggle(canvas) }),
          el('button', { 'aria-label': 'Von vorn', title: 'Von vorn', html: MAG.icons.reset, onclick: () => MAG.viewer3d.reset(canvas) }),
          m3.speech?.length ? el('button', { class: 'sound', 'aria-label': 'Mit Stimmen abspielen', title: 'Mit Stimmen abspielen (auf diesem Gerät gemacht)', html: MAG.icons.speak }) : null,
          el('button', { class: 'full', 'aria-label': 'Vollbild', title: 'Vollbild', html: MAG.icons.full + MAG.icons.x, onclick: () => MAG.viewer3d.fullscreen(stage) })));
      const sound = stage.querySelector('.sound');
      if (sound) sceneSound(m, canvas, sound);
      card.append(play(glb.file, stage, wish(m, 'Neue Bewegung für diese Szene', 'z. B. „Der Fuchs springt hoch“', m3.prompt)));
      MAG.viewer3d.attach(canvas, fileUrl(glb));
    }
    if (running) card.append(progress3d(m3));
    if (film) card.append(el('video', { class: 'film', controls: true, loop: true, preload: 'metadata', playsInline: true, src: fileUrl(film) }));
    const link = (a) => {
      const ext = a.name.split('.').pop().toLowerCase();
      return el('a', { class: 'pill', href: fileUrl(a, '?download=1'), download: a.name, title: (sceneFiles[ext] || a.name) + ' herunterladen' },
        MAG.icon('down'), el('b', { text: ext.toUpperCase() }), el('small', { text: (sceneFiles[ext] ? sceneFiles[ext] + ' · ' : '') + MAG.bytes(a.size) }));
    };
    card.append(el('div', { class: 'links' }, files.map(link),
      glb && !files.some((a) => a.name.endsWith('.blend'))
        ? el('button', { class: 'pill', title: 'Die Szene als Datei für Blender holen und herunterladen', onclick: (e) => fetchBlend(m, e.currentTarget) },
            MAG.icon('down'), el('b', { text: 'BLEND' }), el('small', { text: sceneFiles.blend })) : null));
    return card;
  }
  // A part of a message that must live on while the message changes (a 3D
  // view that is playing, perhaps in fullscreen): it stays where it is, and
  // everything around it becomes the new message. False: nothing to keep.
  function keep(old, node) {
    const kept = old.querySelector('[data-keep]'), twin = kept && [...node.querySelectorAll('[data-keep]')].find((n) => n.dataset.keep === kept.dataset.keep);
    if (!twin) return false;
    twin.querySelectorAll('canvas').forEach((canvas) => MAG.viewer3d.drop(canvas));
    for (let a = old, b = node; ;) {
      a.className = b.className;
      const stay = [...a.children].find((c) => c === kept || c.contains(kept)), gone = [...b.children].find((c) => c === twin || c.contains(twin));
      const before = [], after = [];
      let passed = false;
      for (const kid of [...b.childNodes]) { if (kid === gone) passed = true; else (passed ? after : before).push(kid); }
      for (const kid of [...a.childNodes]) if (kid !== stay) kid.remove();
      stay.before(...before);
      stay.after(...after);
      if (stay === kept) return true;
      a = stay;
      b = gone;
    }
  }

  // ---- one message --------------------------------------------------------
  // Actions come after the text, like everywhere else: copy, read aloud, and
  // a menu for what must not happen by accident.
  function actions(m) {
    if (m.state === 'streaming') return null;
    const user = m.role === 'user', row = el('div', { class: 'acts' });
    const button = (icon, label, run) => el('button', { class: 'act', 'aria-label': label, title: label, html: MAG.icons[icon], onclick: run });
    if (user) row.append(el('span', { class: 'time', text: MAG.clock(m.ts), title: MAG.day(m.ts) }));
    const words = m.model3d ? '' : m.text; // a 3D result has nothing to copy or read aloud
    if (words) row.append(button('copy', 'Kopieren', async (e) => {
      const b = e.currentTarget;
      try {
        await navigator.clipboard.writeText(m.text);
        b.innerHTML = MAG.icons.check;
        setTimeout(() => { b.innerHTML = MAG.icons.copy; }, 1300);
      } catch { MAG.toast('Kopieren nicht möglich', true); }
    }));
    if (words && MAG.speak) {
      const speak = button('speak', 'Vorlesen', (e) => MAG.speak(m, e.currentTarget));
      speak.dataset.speak = m.id; // the one that is being read shows it (speak.js)
      row.append(speak);
    }
    if (S.you.role === 'owner') row.append(button('dots', 'Mehr', (e) => MAG.menu(e.currentTarget, [
      { icon: 'trash', label: 'Nachricht löschen', danger: true, run: () => MAG.api('DELETE', '/api/message/' + m.id).catch((x) => MAG.toast(x.message, true)) },
    ])));
    return row.children.length ? row : null;
  }
  // A message written at a scene or a 3D model says so; a tap leads there.
  function aboutChip(m) {
    const ref = m.about && S.messages.get(m.about), m3 = ref?.model3d;
    if (!m3) return null;
    const scene = m3.kind === 'scene', glb = (ref.attachments || []).find((a) => a.name.endsWith('.glb'));
    return el('button', { class: 'about', title: 'Zur ' + (scene ? 'Szene' : 'Vorlage') + ' springen',
      onclick: () => nodes.get(ref.id)?.scrollIntoView({ block: 'center', behavior: 'smooth' }) },
      MAG.icon(scene ? 'film' : 'cube'), el('span', { text: scene ? 'zur Szene „' + (m3.title || 'Szene') + '“' : 'zum 3D-Modell ' + (glb?.name || '') }));
  }
  function build(m) {
    const user = m.role === 'user';
    const node = el('article', { class: 'msg ' + (user ? 'user' : m.role === 'service' ? 'service' : 'bot'), dataset: { id: m.id, from: m.from } });
    const picture = m.model3d?.kind === 'image' || m.model3d?.kind === 'video';   // its result is an ordinary image or video
    const files = m.model3d && !picture ? [] : m.attachments || [];
    const atts = files.length ? el('div', { class: 'atts' }, files.map(buildAttachment)) : null;
    if (user) {
      // The bubble, what was attached, and (on tap or hover) the actions.
      // A routine that came due speaks like a request, marked as the routine's.
      const bubble = m.text ? el('div', { class: 'bubble' + (m.routine ? ' routine' : '') }, m.routine ? el('small', { class: 'tag', text: '⏰ Routine' }) : null, markdown(m.text)) : null;
      const col = el('div', { class: 'col' }, aboutChip(m), bubble, atts);
      col.addEventListener('click', (e) => {
        if (e.target.closest('a, button, video, audio') || String(window.getSelection() || '')) return;
        node.classList.toggle('show-acts');
      });
      node.append(el('div', { class: 'line' }, actions(m), col));
      return node;
    }
    node.append(el('div', { class: 'meta' }, el('span', { class: 'who', text: picture ? (m.model3d.kind === 'video' ? 'Video' : 'Bild') : MAG.name(m.from) }),
      el('span', { class: 'time', text: MAG.clock(m.ts), title: MAG.day(m.ts) })));
    if (picture) {
      node.append(buildPicture(m));
      if (atts) node.append(atts);
    } else if (m.model3d) {
      node.append(m.model3d.kind === 'scene' ? buildScene(m) : build3d(m));
    } else {
      const body = el('div', { class: 'body' });
      (m.parts || []).forEach((p, i) => body.append(buildPart(m, i)));
      if (!(m.parts || []).length && m.text) body.append(markdown(m.role === 'bot' ? tidy(m.text) : m.text));
      node.append(body);
      placePages(m, body);
      // A finished answer of nothing but what is not shown (a request in words, a written-out call) is no message.
      const texts = (m.parts || []).length ? m.parts : [{ k: 'text', text: m.text || '' }];
      if (m.role === 'bot' && m.state !== 'streaming' && !(m.attachments || []).length && texts.every((p) => p.k === 'text' && !tidy(p.text || '')))
        node.hidden = true;
    }
    if (atts) node.append(atts);
    const note = footer(m);
    if (note) node.append(note);
    const acts = actions(m);
    if (acts) node.append(acts);
    markStreaming(node, m);
    return node;
  }
  // What a bot wrote, as the chat shows it (old messages too): a line it
  // wrote again in English next to the German one, a tool call it wrote out
  // as JSON instead of calling it, a request for approval in words (the card
  // is the only way) and a bare <br> are not shown.
  const EN = new Set("the i i'll i'm i've will to and of a is it you your this that for with on let me first try find again now then open login sign in click button page we let's".split(' '));
  const DE = new Set('ich die der das und zu ist nicht ein eine mit auf für du dich dein deine jetzt bitte den dem es wird werde sie wir kann noch auch einloggen anmelden öffne klicke seite'.split(' '));
  function tidy(text) {
    if (!text) return text;
    // A fenced block that is a tool call written out (JSON with an action or a function name).
    text = text.replace(/```(?:json)?\s*\n([\s\S]*?)\n```/g, (all, inner) => {
      try { const v = JSON.parse(inner); return v && typeof v === 'object' && (v.action || v.name || v.arguments || v.tool) ? '' : all; } catch { return all; }
    });
    const lines = text.split('\n');
    const lang = (line) => {
      const words = line.toLowerCase().match(/[a-zäöüß']+/g) || [];
      let en = 0, de = 0;
      for (const w of words) { if (EN.has(w)) en++; if (DE.has(w)) de++; }
      return en >= 2 && en > de * 2 ? 'en' : de > en ? 'de' : '';
    };
    const german = lines.some((l) => lang(l) === 'de');
    let inCode = false;
    const kept = lines.filter((line) => {
      if (/^\s*```/.test(line)) { inCode = !inCode; return true; }
      if (inCode) return true;
      const t = line.trim();
      if (/^<br\s*\/?>$/i.test(t)) return false;
      if (/\bconsent\b/i.test(t)) return false;
      if (/^\{.*\}$/.test(t)) { try { JSON.parse(t); return false; } catch { /* text */ } }
      return !(german && lang(t) === 'en');
    });
    return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  MAG.tidy = tidy;
  function buildPart(m, index) {
    const p = m.parts[index];
    if (p.k === 'step') return buildStep(m, index);
    const node = markdown(m.role === 'bot' ? tidy(p.text) : p.text, m.state === 'streaming');
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

  // The first message of a day carries the day (shown above it).
  function dayOf(ms) {
    const d = new Date(ms), now = new Date(), key = (x) => x.getFullYear() * 10000 + x.getMonth() * 100 + x.getDate();
    if (key(d) === key(now)) return 'Heute';
    if (key(d) === key(new Date(now.getTime() - 86400000))) return 'Gestern';
    return d.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
  }
  function paintDays() {
    let last = '';
    for (const node of list.children) {
      const m = S.messages.get(node.dataset.id), day = m ? dayOf(m.ts) : '';
      if (day && day !== last) node.dataset.day = day;
      else delete node.dataset.day;
      if (day) last = day;
    }
  }

  function upsert(m, fresh) {
    const old = nodes.get(m.id);
    // The whole message is rebuilt: a queued re-render of one of its parts is obsolete.
    for (const key of dirty) if (key.startsWith(m.id + ':')) dirty.delete(key);
    let node = build(m);
    if (old) {
      // Keep what the reader opened.
      if (old.classList.contains('show-acts')) node.classList.add('show-acts');
      old.querySelectorAll('details[open]').forEach((d) => {
        const twin = d.classList.contains('think') ? node.querySelector('.think') : node.querySelector('details[data-part="' + d.dataset.part + '"]');
        if (twin) twin.open = true;
      });
      grow(() => { if (keep(old, node)) node = old; else old.replaceWith(node); });
    } else {
      // Ids sort by time: a new message goes where its id belongs (almost always the end).
      let after = null;
      for (const kid of list.children) if (kid.dataset.id > m.id) { after = kid; break; }
      grow(() => list.insertBefore(node, after), fresh && !after);
    }
    nodes.set(m.id, node);
    empty.hidden = S.messages.size > 0;
    paintDays();
    MAG.speaking?.paint();
  }

  function renderAll() {
    list.replaceChildren();
    nodes.clear();
    dirty.clear();
    const ordered = [...S.messages.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    for (const m of ordered) { const node = build(m); nodes.set(m.id, node); list.append(node); }
    empty.hidden = ordered.length > 0;
    olderBtn.hidden = !S.more;
    paintDays();
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
  let scheduled = false, cost = 0;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    // A long answer takes longer to render: leave the phone time to breathe.
    setTimeout(() => {
      scheduled = false;
      const t0 = performance.now();
      flush();
      cost = performance.now() - t0;
    }, Math.max(60, Math.min(700, cost * 4)));
  }

  // ---- activity row: who is doing what right now --------------------------
  const phases = { reading: 'liest', thinking: 'denkt nach', writing: 'schreibt', tool: 'arbeitet', approval: 'wartet auf deine Freigabe', warming: 'bereitet sich vor', resetting: 'leert den Kontext' };
  function paintActivity() {
    const rows = [];
    for (const b of S.bots) {
      if (!b.busy && b.phase === 'idle') continue;
      let what = phases[b.phase] || 'arbeitet';
      if (b.phase === 'reading' && b.total > 0) what += ' ' + Math.min(99, Math.round((b.processed / b.total) * 100)) + ' %';
      rows.push(el('div', { class: 'act-row', dataset: { bot: b.id, since: b.phase_since } },
        el('span', { class: 'dots' }, el('i'), el('i'), el('i')), el('b', { text: b.name }), el('span', { class: 'what', text: what }),
        el('span', { class: 't', text: MAG.stopwatch(Date.now() - b.phase_since) }),
        b.busy ? el('button', { class: 'stop', text: 'Stop', onclick: () => MAG.api('POST', '/api/stop', { bot: b.id }).catch((x) => MAG.toast(x.message, true)) }) : null));
    }
    grow(() => activity.replaceChildren(...rows));
  }
  setInterval(() => {
    if (!activity) return;
    activity.querySelectorAll('.act-row').forEach((row) => { row.querySelector('.t').textContent = MAG.stopwatch(Date.now() - +row.dataset.since); });
    list.querySelectorAll('.m3 .clock').forEach((node) => { node.textContent = clock3d(node.dataset); });
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
  MAG.on('gone', (id) => { nodes.get(id)?.remove(); nodes.delete(id); empty.hidden = S.messages.size > 0; paintDays(); });
  MAG.on('delta', (m, part) => { dirty.add(m.id + ':' + part); schedule(); });
  // A model's thoughts are its own: raw, often English, never shown in the chat (Chris, 2026-11-12).
  MAG.on('step', (m, part) => {
    const node = nodes.get(m.id);
    if (!node) return;
    flush();
    const body = node.querySelector('.body'), old = body.querySelector(':scope > [data-part="' + part + '"]'), fresh = buildStep(m, part);
    if (old) { fresh.open = old.open; grow(() => old.replaceWith(fresh)); }
    else grow(() => body.append(fresh));
    if (fresh.querySelector('.shot-mini')) node.querySelectorAll('.shot-mini').forEach((img) => { if (!fresh.contains(img)) img.remove(); });
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
    paintDays();
    scroller.scrollTop += scroller.scrollHeight - before; // stay on the same message
  });
})();

// The timeline: every message in order, streamed text and live steps in the
// order they happen, and scrolling that follows only when you are at the end.
'use strict';
(function () {
  const { MAG } = window, { el, $, state: S } = MAG;
  let list, scroller, jump, activity, empty, olderBtn;
  let pinned = true, unread = 0;
  // This is a local presentation preference, never a server-side filter:
  // every message and tool event continues to arrive and remains in S.messages.
  let mediaOnly = false, mediaLoading = false, mediaMore = false, mediaCursor = '', mediaRequest = null;
  let mediaGeneration = 0; // reset invalidates all earlier async page responses
  try { mediaOnly = localStorage.getItem('mag-media-only') === '1'; } catch (_) {}
  let lastScrollTop = 0, touchY = null;
  let inputDirection = 'none', inputUntil = 0; // only an explicit user gesture may re-pin
  const nodes = new Map(); // message id -> article
  const dirty = new Set(); // "id:part" text parts waiting for a markdown pass
  const groupPrefs = new Map(); // goal ID / individual non-goal turn -> explicit user expansion
  const goalPhases = new Map(); // last painted state to avoid redrawing groups on every status pulse

  // ---- markdown -----------------------------------------------------------
  if (window.marked) marked.setOptions({ gfm: true, breaks: true });
  // `live`: the text is still being written. Long code is then left plain
  // and coloured once at the end, so a phone keeps up with the stream.
  function markdown(text, live, scope = '') {
    if (!window.marked || !window.DOMPurify) return el('div', { class: 'md', text });
    const html = DOMPurify.sanitize(marked.parse(text || ''), { FORBID_TAGS: ['style', 'form', 'input', 'iframe', 'object', 'embed'], FORBID_ATTR: ['style', 'onerror', 'onclick'] });
    const box = el('div', { class: 'md', html });
    box.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    MAG.mentions?.decorate(box);
    box.querySelectorAll('img').forEach((img) => { img.loading = 'lazy'; });
    box.querySelectorAll('pre').forEach((pre, index) => {
      const code = pre.querySelector('code');
      const rawLang = ((code?.className || '').match(/language-([\w+#.-]+)/) || [])[1] || '';
      const source = code ? code.textContent : pre.textContent;
      const key = (scope || 'snippet:' + sigOf(source)) + ':code:' + index;
      if (live) {
        // Reinstated lightweight streaming path: no textarea, draft lookup,
        // localStorage or live iframe while model output is still growing.
        // Keep the code block cheap to rebuild as more tokens arrive.
        const block = el('div', { class: 'codeblock codeblock-live', dataset: { codeKey: key } });
        const head = el('div', { class: 'head' },
          el('span', { class: 'code-lang', text: rawLang || 'Code' }),
          el('button', { class: 'copy', type: 'button', text: 'Kopieren',
            onclick: (e) => { e.preventDefault(); copy(source, e.currentTarget); } }));
        block._sourceOriginal = source;
        pre.replaceWith(block);
        block.append(head, pre);
        return;
      }
      const lang = MAG.codeEditor.language(rawLang, source);
      const canRun = MAG.codeEditor.previewable(lang, source);
      // Stable across streaming re-renders. Published pages use this same
      // draft key when replacing their original fenced code block.
      const pane = el('div', { class: 'code-pane' });
      const preview = el('div', { class: 'code-preview', hidden: true });
      const block = el('div', { class: 'codeblock', dataset: { codeKey: key } });
      block._sourceOriginal = source;
      let previewFrame = null, previewTimer = null, mode = 'code';
      const edited = MAG.codeEditor.editor(key, source, lang, () => {
        if (mode === 'preview' && previewFrame) {
          clearTimeout(previewTimer);
          previewTimer = setTimeout(() => previewFrame?._refresh?.(), 400);
        }
      });
      block._editor = edited;
      // Keep the original code in the DOM only for page-signature matching.
      // It is never executed or edited; the textarea is the source of truth.
      pre.hidden = true;
      const codeTab = el('button', { role: 'tab', type: 'button', text: 'Code' });
      const previewTab = canRun ? el('button', { role: 'tab', type: 'button', text: 'Vorschau' }) : null;
      function show(next) {
        mode = next;
        pane.hidden = next !== 'code';
        preview.hidden = next !== 'preview';
        for (const [name, button] of [['code', codeTab], ['preview', previewTab]]) {
          if (!button) continue;
          button.classList.toggle('on', name === next);
          button.setAttribute('aria-selected', String(name === next));
        }
        if (next === 'preview' && !previewFrame) {
          previewFrame = MAG.codeEditor.frame('Live-Code-Vorschau',
            () => MAG.codeEditor.html(lang, edited.get()));
          preview.replaceChildren(previewFrame);
        }
      }
      codeTab.addEventListener('click', (e) => { e.preventDefault(); show('code'); });
      previewTab?.addEventListener('click', (e) => { e.preventDefault(); show('preview'); });
      const copyButton = el('button', { class: 'copy', type: 'button', text: 'Kopieren',
        onclick: (e) => { e.preventDefault(); copy(edited.get(), e.currentTarget); } });
      const fullButton = el('button', { class: 'copy', type: 'button', text: 'Vollbild',
        onclick: (e) => {
          e.preventDefault();
          block.classList.toggle('code-fullscreen');
          fullButton.textContent = block.classList.contains('code-fullscreen') ? 'Schließen' : 'Vollbild';
        } });
      block._closeFull = () => { block.classList.remove('code-fullscreen'); fullButton.textContent = 'Vollbild'; };
      const header = el('div', { class: 'head' },
        el('span', { class: 'code-lang', text: lang || 'Code' }),
        el('div', { class: 'seg code-tabs', role: 'tablist' }, codeTab, previewTab),
        edited.status, edited.resetButton, copyButton, fullButton);
      pre.replaceWith(block);
      // Keep the original hidden pre for server page-signature matching.
      pane.append(edited.input, pre);
      block.append(header, pane, preview);
      show('code');
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
  // No 70px tolerance for *re*-pinning. A person who reads 20px up
  // must stay there even when layout changes or an update shifts the page.
  const atTrueEnd = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 1;
  function setScrollTop(top) {
    scroller.scrollTop = top;
    lastScrollTop = scroller.scrollTop; // programmatic scroll is never a user gesture
  }
  function toBottom() { setScrollTop(scroller.scrollHeight); pinned = true; unread = 0; paintJump(); }
  function paintJump() {
    jump.hidden = pinned;
    jump.classList.toggle('news', unread > 0);
    jump.lastChild.textContent = unread > 0 ? (unread === 1 ? 'Neue Nachricht' : unread + ' neue Nachrichten') : '';
  }
  // Keep the viewport anchored on the message the reader is looking at,
  // even if a task history above gets rebuilt or expands in a live update.
  function preserveReadingPosition(change) {
    const viewport = scroller.getBoundingClientRect();
    const anchor = [...list.children].find((node) => {
      const r = node.getBoundingClientRect();
      return r.bottom > viewport.top + 1 && r.top < viewport.bottom - 1;
    });
    const before = anchor?.getBoundingClientRect().top;
    const previous = scroller.scrollTop;
    change();
    if (anchor?.isConnected && before !== undefined) {
      const moved = anchor.getBoundingClientRect().top - before;
      if (Math.abs(moved) >= 1) setScrollTop(scroller.scrollTop + moved);
    } else setScrollTop(previous);
  }
  function grow(change, isNewMessage) {
    const stick = pinned;
    if (stick) { change(); setScrollTop(scroller.scrollHeight); }
    else preserveReadingPosition(change);
    if (!stick && isNewMessage) unread++;
    paintJump();
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
    // What the bot asked for in words, one line per field ("Beschreibung: …", "Format: quadratisch");
    // only what has parts of its own (a scene's figures) stays in its structure.
    return keys.map((k) => {
      const v = i[k], label = INPUT_LABEL[k] || k;
      if (v && typeof v === 'object') return label + ':\n' + JSON.stringify(v, null, 2);
      return label + ': ' + (INPUT_VALUE[v] || String(v));
    }).join('\n');
  }
  const INPUT_LABEL = { prompt: 'Beschreibung', shape: 'Format', image: 'Bild', seconds: 'Sekunden', quality: 'Qualität', continue: 'Fortsetzen von',
    as: 'Bild als', site: 'Seite', reason: 'Grund', url: 'Adresse', query: 'Suche', path: 'Datei', text: 'Text', n: 'Element', key: 'Taste',
    enter: 'Enter drücken', what: 'Was', shop: 'Shop', amount_eur: 'Betrag (€)', action: 'Aktion', name: 'Name', scope: 'Umfang', fact: 'Notiz', title: 'Titel' };
  const INPUT_VALUE = { square: 'quadratisch', wide: 'quer', tall: 'hoch', first: 'erstes Bild', reference: 'Figur', newest: 'neuestes Video', true: 'ja', false: 'nein' };
  function stepTime(step) { return MAG.stopwatch((step.t1 || Date.now()) - (step.t0 || Date.now())); }
  function buildStep(m, index, grouped = false) {
    const step = m.parts[index];
    // A plugin call that changes something waits here for a yes or a no (not one a restart ended).
    const ask = step.state === 'running' ? step.approval : null;
    const node = el('details', { class: 'step ' + step.state + (ask ? ' asking' : ''), dataset: { part: index, message: m.id } });
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
    // No pictures of a browser in the chat (Chris, 2026-11-14): only the real one, live.
    node.append(el('summary', {},
      el('span', { class: 'mark', html: stepMark[step.state] || '' }),
      el('span', { class: 'title', text: step.title || step.tool }),
      el('span', { class: 'time', text: stepTime(step) + (step.exit ? ' · Exit ' + step.exit : '') }),
      cancel,
      el('span', { class: 'chev', html: MAG.icons.chev }),
      asking,
      step.state === 'running' && !ask && !(step.tool || '').startsWith('browser_') ? el('span', { class: 'peek', text: lastLines(step.output || '') }) : null));
    const out = el('pre', { class: 'out', text: step.output || '' });
    const input = stepInput(step);
    // A card's step (approval, login, payment) is the card and nothing more: no raw input, no output.
    if (['consent', 'browser_login', 'browser_pay'].includes(step.tool)) {
      node.classList.add('card-step');
      // While it waits, the card is the browser live; afterwards the card is done and shows nothing.
      return node;
    }
    // A browser step is the browser and nothing more: no raw input or output
    // (the long address, the list of elements), no picture, only a failure in
    // one line. Open, it is the bot's browser live over its realtime link (one
    // view per bot, it goes where it is opened), to tap and type in like the
    // card. The newest browser step of a bot stands open by itself.
    if (step.tool.startsWith('browser_')) {
      node.classList.add('browser-step');
      node.dataset.bot = m.from;
      const failed = step.state === 'error' ? el('div', { class: 'fail', text: (step.output || '').split('\n')[0].slice(0, 200) }) : null;
      const body = el('div', { class: 'body' }, failed);
      node.append(body);
      if (S.you.role === 'owner' && !grouped) {
        const slot = el('div', { class: 'live-slot' });
        body.prepend(slot);
        const swap = () => {
          if (node.open && !slot.firstChild) slot.append(liveIn(m.from, 'step-' + m.from));
          else if (!node.open) slot.replaceChildren();
        };
        node.addEventListener('toggle', swap);
        if (newestBrowser(m.from) === m.id + '-' + index && !opened.has(m.id + '-' + index)) {
          opened.add(m.id + '-' + index);
          // The live view moves on to the newest step: the one before closes.
          document.querySelectorAll('details.browser-step[open]').forEach((d) => { if (d.dataset.bot === m.from) d.open = false; });
          node.open = true;
        }
      }
      return node;
    }
    node.append(el('div', { class: 'body' },
      input ? el('div', { class: 'label', text: step.tool === 'shell' ? 'Befehl' : 'Eingabe' }) : null,
      input ? el('pre', { class: 'cmd', text: input }) : null,
      el('div', { class: 'label', text: step.state === 'running' ? 'Ausgabe (live)' : 'Ausgabe' + (step.cut ? ' (Ende; vollständig im Log der VM)' : '') }),
      out));
    node.addEventListener('toggle', () => { if (node.open) out.scrollTop = out.scrollHeight; });
    return node;
  }
  // A page in a bot's browser wants a login: the card is that browser, live
  // (WebRTC over the VM's UDP link) and his to use right here: tap, type,
  // the keyboard, also on the phone. Chris logs in himself (password, code,
  // passkey, captcha); nothing of it goes through a form of the room, is kept
  // or reaches a model. "Angemeldet, weiter" hands the page back to the bot.
  // Typing, tapping or choosing inside a card lives in the step's summary line: it does not fold the step.
  const keepOpen = (e) => { if (e.target.closest('input, select, textarea, .live')) e.preventDefault(); };
  // One live view per waiting card, kept while the card is drawn anew and let go when it is gone.
  const liveCards = new Map();          // card id -> live view
  let sweeping = 0;
  function sweep() {
    sweeping = 0;
    for (const [id, v] of liveCards) {
      if (v.node.isConnected) { v.away = 0; continue; }
      if (++v.away >= 2) { v.stop(); liveCards.delete(id); }
    }
    if (liveCards.size) sweeping = setTimeout(sweep, 1500);
  }
  function liveIn(bot, id) {
    let v = liveCards.get(id);
    if (!v) { v = MAG.liveView(bot); v.away = 0; liveCards.set(id, v); }
    if (!sweeping) sweeping = setTimeout(sweep, 1500);
    return v.node;
  }
  function loginCard(m, ask) {
    const name = MAG.name(m.from), owner = S.you.role === 'owner';
    const decide = (decision) => (e) => {
      e.preventDefault();
      e.currentTarget.parentNode.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      MAG.api('POST', '/api/approval/' + ask.id, { decision }).catch((x) => MAG.toast(x.message, true));
    };
    return el('span', { class: 'ask', onclick: keepOpen },
      el('b', { text: name + ' braucht eine Anmeldung bei ' + (ask.site || 'einer Seite') }),
      ask.what ? el('span', { class: 'what', text: ask.what }) : null,
      owner ? liveIn(m.from, ask.id) : null,
      owner ? el('small', { class: 'fine', text: 'Das ist der Browser von ' + name + ', live. Melde dich direkt darin an; ' + name + ' sieht nicht, was du tippst.' }) : null,
      owner ? el('span', { class: 'btns' },
        el('button', { class: 'yes', text: 'Angemeldet, weiter', onclick: decide('done') }),
        el('button', { class: 'no', text: 'Abbrechen', onclick: decide('cancel') })) : null);
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
    return el('span', { class: 'ask', onclick: keepOpen },
      el('b', { text: name + ' ist an der Kasse: ' + (ask.site || '') + (euro ? ' · ' + euro : '') }),
      ask.what ? el('span', { class: 'what', text: ask.what }) : null,
      owner ? form : null,
      owner ? liveIn(m.from, ask.id) : null,
      owner ? el('small', { class: 'fine', text: 'Oder bezahle selbst, direkt im Browser von ' + name + ' oben.' }) : null,
      owner ? el('span', { class: 'btns' },
        el('button', { text: 'Bezahlt, weiter', onclick: (e) => { e.preventDefault(); decide('done'); } }),
        el('button', { class: 'no', text: 'Abbrechen', onclick: (e) => { e.preventDefault(); decide('cancel'); } })) : null,
      note);
  }

  // The newest browser step of a bot in the whole chat ("<message>-<part>"): it stands open, live.
  // Opened once by the page; after that it is the reader's to close.
  const opened = new Set();
  const isBrowser = (p) => p.k === 'step' && (p.tool || '').startsWith('browser_') && !['browser_login', 'browser_pay'].includes(p.tool);
  function newestBrowser(bot) {
    let best = '', at = '';
    for (const m of S.messages.values()) {
      if (m.from !== bot || m.id <= best) continue;
      const i = (m.parts || []).reduce((found, p, j) => (isBrowser(p) ? j : found), -1);
      if (i >= 0) { best = m.id; at = m.id + '-' + i; }
    }
    return at;
  }
  const lastLines = (text) => text.replace(/\s+$/, '').split('\n').slice(-3).join('\n');


  // ---- one compact task group for every run of tools ----------------------
  // A browser goal can span several bot messages (after a retry/deploy).
  // New messages carry the immutable server goal_id; legacy/unrelated tool
  // turns get a separate per-message group instead of a page of step cards.
  const groupKey = (m) => m.goal_id ? 'goal:' + m.from + ':' + m.goal_id : 'turn:' + m.id;
  const stepsOf = (m) => (m.parts || []).map((p, i) => ({ m, p, i })).filter((x) => x.p.k === 'step');
  function groupMembers(m) {
    const key = groupKey(m);
    return [...S.messages.values()].filter((x) => x.from === m.from && groupKey(x) === key)
      .sort((a, b) => a.id.localeCompare(b.id));
  }
  function groupEntries(m) { return groupMembers(m).flatMap(stepsOf); }
  function groupAnchor(m) {
    return groupMembers(m).find((x) => (x.parts || []).some((p) => p.k === 'step')) || m;
  }
  function groupInfo(m) {
    const entries = groupEntries(m), members = groupMembers(m), bot = MAG.bot(m.from),
      goal = m.goal_id && bot?.goal?.id === m.goal_id ? bot.goal : null;
    const unfinished = entries.some(({ p }) => p.state === 'running') || members.some((x) => x.state === 'streaming');
    const pending = goal?.progress === 'paused' || goal?.progress === 'stalled';
    const active = unfinished || (goal && (goal.state === 'pending' || goal.state === 'active') && !pending);
    const ended = goal && ['done', 'blocked', 'ended'].includes(goal.state);
    const failed = entries.some(({ p }) => ['error', 'timeout', 'cancelled'].includes(p.state)) || members.some((x) => x.state === 'error');
    const state = pending ? 'paused' : active ? 'running' : ended ? (goal.state === 'done' ? 'done' : 'paused')
      : failed && members.every((x) => x.state !== 'streaming') ? 'paused' : 'done';
    const current = entries.findLastIndex(({ p }) => p.state === 'running' && p.approval)
      >= 0 ? entries.findLastIndex(({ p }) => p.state === 'running' && p.approval)
      : Math.max(0, entries.findLastIndex(({ p }) => p.state === 'running') >= 0
          ? entries.findLastIndex(({ p }) => p.state === 'running') : entries.length - 1);
    const first = entries[0]?.p.t0 || members[0]?.ts || Date.now();
    const last = entries[entries.length - 1]?.p;
    const finished = (state === 'running' || state === 'paused' && unfinished) ? Date.now()
      : last?.t1 || last?.t0 || first;
    return { entries, state, current, first, finished, last, hasBrowser: entries.some(({ p }) => isBrowser(p)),
      awaitingApproval: entries.some(({ p }) => p.state === 'running' && p.approval) };
  }
  function goalGroup(m) {
    const info = groupInfo(m), key = groupKey(m);
    if (!info.entries.length) return null;
    const manual = groupPrefs.get(key), active = info.state === 'running' || info.state === 'paused' && info.entries.some(({ p }) => p.state === 'running');
    const node = el('details', { class: 'goal-group ' + info.state, dataset: { groupkey: key, since: info.first,
      done: info.finished, status: info.state } });
    const duration = el('span', { class: 'goal-duration', text: MAG.stopwatch(info.finished - info.first) });
    const title = el('span', { class: 'goal-title', text: 'Auftragsschritte' });
    const latest = el('span', { class: 'goal-latest', text: info.last?.title || info.last?.tool || '' });
    const status = info.state === 'running' ? 'Läuft' : info.state === 'paused' ? 'Pausiert' : 'Abgeschlossen';
    const count = info.entries.length === 1 ? '1 Schritt' : info.entries.length + ' Schritte';
    node.append(el('summary', { 'aria-label': 'Auftragsschritte ' + status + ', ' + count },
      el('span', { class: 'goal-status-dot' }),
      title,
      el('span', { class: 'goal-status', text: status }),
      el('span', { class: 'goal-count', text: count }),
      duration,
      el('span', { class: 'goal-chevron', html: MAG.icons.chev }),
      latest));
    const body = el('div', { class: 'goal-steps' });
    info.entries.forEach(({ m: owner, i }, j) => {
      const step = buildStep(owner, i, true);
      if (j === info.current) step.classList.add('current');
      body.append(step);
    });
    node.append(body);
    node.open = manual === undefined ? active : manual;
    node.classList.toggle('history-open', manual === true);
    node.querySelector(':scope > summary').addEventListener('click', () => {
      // A human click, not a synthetic toggle event, is the only override.
      const next = !node.open;
      groupPrefs.set(key, next);
      node.classList.toggle('history-open', next);
    });
    return node;
  }
  function goalBrowser(m) {
    const info = groupInfo(m);
    if (!info.hasBrowser || S.you.role !== 'owner') return null;
    const key = groupKey(m), node = el('details', { class: 'goal-browser', dataset: { groupkey: key } });
    const active = info.state === 'running' && !info.awaitingApproval;
    node.append(el('summary', {}, el('span', { text: 'Live-Browser' }),
      el('span', { class: 'goal-browser-caption', text: active ? 'Live bedienen' : 'Öffnen' }),
      el('span', { class: 'goal-chevron', html: MAG.icons.chev })));
    const slot = el('div', { class: 'goal-browser-slot' });
    node.append(slot);
    // The browser is deliberately BELOW, never inside the foldable step
    // history. It stays accessible when the task summary is collapsed.
    node.addEventListener('toggle', () => {
      if (node.open && !slot.firstChild) slot.append(liveIn(m.from, 'group-live-' + m.from));
      else if (!node.open) slot.replaceChildren();
    });
    node.open = active;
    return node;
  }
  function taskPartLocation(m, index) {
    return stepsOf(m).some(({ i }) => i === index) ? groupKey(m) : '';
  }
  function locateStep(m, index) {
    const owner = groupAnchor(m), article = nodes.get(owner.id);
    return article?.querySelector('.goal-group .step[data-message="' + m.id + '"][data-part="' + index + '"]')
      || nodes.get(m.id)?.querySelector('.body > .step[data-part="' + index + '"]');
  }
  function refreshGroup(m) {
    if (!(m.parts || []).some((p) => p.k === 'step') && !m.goal_id) return;
    const anchor = groupAnchor(m), article = nodes.get(anchor.id);
    if (!article) return;
    const fresh = goalGroup(anchor), old = article.querySelector('.goal-group');
    if (!fresh) return;
    const oldBrowser = article.querySelector('.goal-browser');
    const browser = goalBrowser(anchor);
    // The live viewer's WebRTC node is reused, not recreated for every
    // tool-state event. A closed task keeps a one-tap browser launcher.
    const live = oldBrowser?.querySelector('.live');
    if (live && browser?.open) browser.querySelector('.goal-browser-slot')?.append(live);
    if (old) old.replaceWith(fresh);
    else {
      const body = article.querySelector(':scope > .body');
      if (body) body.prepend(fresh);
    }
    if (oldBrowser) { if (browser) oldBrowser.replaceWith(browser); else oldBrowser.remove(); }
    else if (browser) fresh.after(browser);
    goalPhases.set(groupKey(m), groupInfo(m).state);
  }

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
    const edited = card._editable?.changed();
    const frame = edited
      ? MAG.codeEditor.frame(card._title, () => MAG.codeEditor.html('html', card._editable.get(), card._base))
      : el('iframe', { title: card._title, src: card._src });
    if (!edited)
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-pointer-lock allow-downloads');
    frame.setAttribute('allow', 'fullscreen; autoplay; clipboard-write; accelerometer; gyroscope; gamepad; xr-spatial-tracking');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    if (!edited) frame.setAttribute('credentialless', '');
    // Edited frames GET a same-origin shell under the normal viewer's
    // authentication, but run with an opaque origin (no allow-same-origin).
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
    const botId = page.bot || m.from;
    const base = MAG.bot(botId)?.site;
    if (!base || !page.path) return null;
    const href = base + '/' + page.path.split('/').map(encodeURIComponent).join('/');
    const stage = el('div', { class: 'stage' }), code = el('div', { class: 'code', hidden: true });
    const original = pre ? (pre.querySelector('code') || pre).textContent
      : m.parts?.[page.part]?.input?.content || '';
    const block = pre?.closest('.codeblock');
    const key = block?.dataset.codeKey || m.id + ':page:' + page.path;
    let card = null, timer = null;
    const fullscreenLabel = el('span', { text: 'Vollbild' });
    const full = el('a', { class: 'tool', href, target: '_blank', rel: 'noopener',
      'aria-label': 'Vollbild' }, MAG.icon('full'), fullscreenLabel);
    const editor = MAG.codeEditor.editor(key, original, 'html', () => {
      if (!card) return;
      const modified = editor.changed();
      const frameModified = card._frame?.classList.contains('code-preview-frame') || false;
      if (card._frame && modified !== frameModified) {
        unmount(card);
        if (card._visible && card._mode === 'preview') mount(card);
      } else if (card._frame && modified) {
        clearTimeout(timer);
        timer = setTimeout(() => card._frame?._refresh?.(), 400);
      }
      if (!modified) card._closeFull?.();
      card.classList.toggle('code-edited', modified);
      full.setAttribute('aria-label', modified ? 'Bearbeitete Vorschau als Vollbild' : 'Vollbild im neuen Tab');
    });
    const tab = (mode, label) => el('button', { role: 'tab', type: 'button',
      text: label, onclick: () => show(mode) });
    const tabs = { preview: tab('preview', 'Vorschau'), code: tab('code', 'Code') };
    const copied = el('span', { text: 'Kopieren' });
    card = el('div', { class: 'page', dataset: { path: page.path } },
      el('div', { class: 'bar' },
        el('div', { class: 'seg', role: 'tablist' }, tabs.preview, tabs.code),
        el('span', { class: 'grow' }),
        el('button', { class: 'tool', type: 'button', 'aria-label': 'Code kopieren',
          onclick: () => copy(editor.get(), copied) }, MAG.icon('copy'), copied),
        full),
      stage, code,
      el('a', { class: 'addr', href, target: '_blank', rel: 'noopener',
        text: href.replace(/^https?:\/\//, '') + ' · lokale Änderungen werden nicht veröffentlicht' }));
    Object.assign(card, {
      _src: href + '?v=' + (page.v || m.ts), _title: page.title || page.path,
      _sourceOriginal: original, _base: base, _editable: editor,
      _stage: stage, _mode: 'preview', _visible: false, _frame: null
    });
    card.classList.toggle('code-edited', editor.changed());
    card._closeFull = () => {
      card.classList.remove('code-fullscreen');
      fullscreenLabel.textContent = 'Vollbild';
    };
    full.addEventListener('click', (event) => {
      if (!editor.changed()) return; // original published page keeps its real URL
      event.preventDefault();
      if (card.classList.contains('code-fullscreen')) return card._closeFull();
      card.classList.add('code-fullscreen');
      fullscreenLabel.textContent = 'Schließen';
      show('preview');
      mount(card);
    });
    function show(mode) {
      card._mode = mode;
      for (const [key, node] of Object.entries(tabs)) {
        node.classList.toggle('on', key === mode);
        node.setAttribute('aria-selected', String(key === mode));
      }
      stage.hidden = mode !== 'preview';
      code.hidden = mode !== 'code';
      if (mode === 'code') unmount(card);
      else if (card._visible || card.classList.contains('code-fullscreen')) mount(card);
    }
    code.append(editor.input, el('div', { class: 'code-edit-note' },
      editor.status, editor.resetButton));
    if (pre) {
      const old = pre.closest('.codeblock') || pre;
      old.before(card);
      // The original hidden pre remains inside the page, but the editable
      // textarea is the only code shown. No draft is written to the VM.
      code.append(pre);
      if (old !== pre) old.remove();
    } else if (!original && S.you.role === 'owner' && MAG.bot(botId)?.vm) {
      // Pages written by write_file can have no fenced code in the chat.
      // Reuse the existing read-only owner VM-file endpoint to fill the
      // editor; reading never publishes or mutates the bot's files.
      const path = 'www/' + page.path;
      MAG.fetch('/api/vm/' + encodeURIComponent(botId) + '/file?path=' +
        encodeURIComponent(path) + '&text=1')
        .then((response) => response.ok ? response.text() : null)
        .then((text) => { if (text !== null) editor.setOriginal(text); })
        .catch(() => {});
    }
    show('preview');
    watch(card);
    return card;
  }
  // Escape always leaves edited fullscreen, even after a chat rerender.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.querySelector('.code-fullscreen')?._closeFull?.();
  });
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
  // A video with its sound (HyperFlow H3 Flash): 5 or 10 seconds, and a
  // picture as its first frame or as who is in it. Kept on the device.
  MAG.videoChoice = (onChange) => {
    const pick = { seconds: +(localStorage.getItem('mag-video-seconds') || 5), use: localStorage.getItem('mag-video-use') || 'first' };
    const box = el('div', { class: 'video-choice' });
    const seg = (label, key, options) => {
      const s = el('div', { class: 'seg', role: 'group', 'aria-label': label });
      for (const [value, text] of options)
        s.append(el('button', { type: 'button', class: pick[key] === value ? 'on' : '', text, onclick: () => {
          pick[key] = value; localStorage.setItem('mag-video-' + key, value); paint(); onChange?.(pick); } }));
      return s;
    };
    const paint = () => box.replaceChildren(seg('Dauer', 'seconds', [[5, '5 s'], [10, '10 s']]));
    paint();
    return { box, pick, uses: (picture) => picture ? seg('Bild als', 'use', [['first', 'Anfang'], ['ref', 'Figur']]) : null };
  };
  MAG.videoHint = (pick) => 'HyperFlow H3 Flash (MiniMax H3): mit Ton, ' + pick.seconds + ' s, 480p, als KI-generiert gekennzeichnet. '
    + 'Gesprochenes in Anführungszeichen („…“) wird wörtlich gesprochen, nicht übersetzt. '
    + (pick.seconds > 5 ? 'Dauert etwa dreimal so lange wie 5 s.' : 'Läuft auf der Grafikkarte, wenn sie frei ist.');
  function makeVideo(a) {
    MAG.sheet('Video aus dem Bild', (content, _, close) => {
      const words = el('textarea', { rows: 3, placeholder: 'Was passiert, was hört man? z. B. „dreht sich lächelnd zur Kamera und sagt: Hallo!“' });
      const go = el('button', { class: 'pill on', type: 'submit' }, MAG.icon('play'), el('b', { text: 'Video erzeugen' }));
      const hint = el('p', { class: 'hint' }), uses = el('div');
      const choice = MAG.videoChoice((pick) => { hint.textContent = MAG.videoHint(pick); uses.replaceChildren(choice.uses(true)); });
      hint.textContent = MAG.videoHint(choice.pick);
      uses.replaceChildren(choice.uses(true));
      const form = el('form', { class: 'video-ask', onsubmit: (e) => {
        e.preventDefault();
        go.disabled = true;
        const { seconds, use } = choice.pick;
        MAG.api('POST', '/api/image/act', { file: a.file, do: 'video', prompt: words.value.trim(), seconds, use })
          .then(() => { close(); MAG.scrollToEnd(); }, (x) => { go.disabled = false; MAG.toast(x.message, true); });
      } }, el('img', { src: fileUrl(a), alt: '' }), words, choice.box, uses, hint, go);
      content.append(form);
      setTimeout(() => words.focus(), 50);
    });
  }
  function buildAttachment(a, m) {
    // What the bots were told about a picture is its caption (and its alt text).
    if (a.kind === 'image') {
      const pic = el('button', { class: 'att-img', title: a.description || a.name, onclick: () => lightbox(fileUrl(a), a.name, a.description, a.file) },
        el('img', { src: fileUrl(a), alt: a.description || a.name, loading: 'lazy' }));
      if (S.you?.role !== 'owner') return pic;
      return el('div', { class: 'att-pic' }, pic, el('div', { class: 'pic-acts' },
        el('button', { class: 'pill', title: '3D-Modell aus diesem Bild (danach animierbar)', onclick: () => makeModel(a) }, MAG.icon('cube'), el('b', { text: '3D' })),
        el('button', { class: 'pill', title: 'Video aus diesem Bild', onclick: () => makeVideo(a) }, MAG.icon('play'), el('b', { text: 'Video' }))));
    }
    // Voice, melody, effects, consolidated mix and baked MP4 belong to the
    // video card's compact controls, never become extra chat rows/players.
    if (a.voiceover_kind) return null;
    if (a.kind === 'audio') return el('audio', { controls: true, preload: 'none', src: fileUrl(a) });
    if (a.kind === 'video') return MAG.voiceover ? MAG.voiceover.player(m, a)
      : el('video', { controls: true, preload: 'metadata', src: fileUrl(a), playsInline: true });
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
  // Deep-link from a push when opening a sleeping PWA, or a postMessage to
  // a window already open. Wait for the state snapshot if it is not yet here.
  let pushTarget = new URLSearchParams(location.search).get('m') || '';
  const showPushTarget = () => {
    if (!pushTarget || !MAG.showMessage(pushTarget)) return;
    pushTarget = '';
  };
  navigator.serviceWorker?.addEventListener('message', (e) => {
    if (typeof e.data?.show !== 'string') return;
    pushTarget = e.data.show;
    showPushTarget();
  });
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
  function retryVideoPart(m) {
    const button = el('button', {
      class: 'icon-btn video-retry',
      type: 'button',
      title: 'Diesen Videoteil mit demselben Prompt neu generieren',
      'aria-label': 'Videoteil erneut generieren',
      onclick: (e) => {
        const target = e.currentTarget;
        if (target.disabled) return;
        target.disabled = true;
        MAG.api('POST', '/api/video/retry', { message: m.id })
          .then(() => MAG.scrollToEnd(), (x) => {
            target.disabled = false;
            MAG.toast(x.message, true);
          });
      }
    }, MAG.icon('reset'));
    return button;
  }
  function buildPicture(m) {
    const m3 = m.model3d, card = el('div', { class: 'm3 image ' + m3.state });
    const running = m3.state === 'queued' || m3.state === 'running';
    const video = m3.kind === 'video', h3 = video && m3.model === 'h3';
    const model = (h3 ? 'KI-generiert · MiniMax H3 · mit Ton' : video ? 'Wan 2.2' : 'FLUX.2 klein')   // Wan: videos from before H3
      + (h3 && m3.continues ? ' · fortgesetzt, ' + (m3.total_seconds || '') + ' s' : '');
    const sub = m3.state === 'done' ? (m3.took ? model + ' · ' + MAG.dur(m3.took * 1000) : model)
      : m3.state === 'error' ? (m3.error || 'Fehler') : m3.state === 'cancelled' ? 'Abgebrochen' : m3.state === 'queued' ? waiting(m3) : m3.stage || 'Malt';
    // Text to video: the picture comes first, the video follows by itself.
    const next = m3.then_video && m3.state !== 'error' && m3.state !== 'cancelled' ? ' · danach das Video'
      : m3.video_error ? ' · Video: ' + m3.video_error : '';
    // A video shows the picture it comes from.
    const src = video && m3.image ? el('span', { class: 'src' }, el('img', { src: '/api/file/' + encodeURIComponent(m3.image), alt: '', loading: 'lazy' }))
      : el('span', { class: 'src' }, MAG.icon(video ? 'play' : 'image'));
    const canRetryVideo = h3 && S.you?.role === 'owner' &&
      (m3.state === 'done' || m3.state === 'error' || m3.state === 'cancelled');
    card.append(el('div', { class: 'head' }, src,
      el('div', { class: 'txt' }, el('b', { text: m3.prompt || (video ? 'Video' : 'Bild') }), el('small', { text: sub + next })),
      canRetryVideo ? retryVideoPart(m) : running ? el('button', { class: 'pill danger', text: 'Abbrechen', onclick: () => MAG.api('POST', '/api/3d/' + m.id + '/cancel').catch((x) => MAG.toast(x.message, true)) })
        : m3.state === 'error' || m3.state === 'cancelled' ? el('button', { class: 'pill', text: 'Noch einmal', onclick: (e) => { e.currentTarget.disabled = true; MAG.api('POST', '/api/3d/' + m.id + '/retry').catch((x) => { e.currentTarget.disabled = false; MAG.toast(x.message, true); }); } }) : null));
    if (running) {
      const part = m3.state === 'running' && m3.steps ? (m3.phase === 2 ? (m3.step || 0) / m3.steps : 0.05) : 0;
      card.append(el('div', { class: 'prog' }, el('div', { class: 'segs' }, el('i', { style: 'width:' + Math.round(100 * part) + '%' }))));
    }
    if (m3.state === 'queued') card.append(lineActs(m));
    // A finished H3 video goes on: a new part from its last frame, the whole video as a new message.
    if (h3 && m3.state === 'done' && S.you?.role === 'owner')
      card.append(el('div', { class: 'line-acts go' }, el('button', { class: 'pill', title: 'Das Video ab seinem letzten Bild weiterführen', onclick: () => continueVideo(m) },
        MAG.icon('play'), el('b', { text: 'Fortsetzen' }))));
    return card;
  }
  function continueVideo(m) {
    MAG.sheet('Video fortsetzen', (content, _, close) => {
      const words = el('textarea', { rows: 3, placeholder: 'Was passiert als Nächstes, was hört man? Figuren, Ort und Klang bleiben.' });
      const go = el('button', { class: 'pill on', type: 'submit' }, MAG.icon('play'), el('b', { text: 'Fortsetzen' }));
      const hint = el('p', { class: 'hint' });
      const choice = MAG.videoChoice((pick) => { hint.textContent = continueHint(pick); });
      const continueHint = (pick) => 'Der neue Teil (' + pick.seconds + ' s) beginnt mit dem letzten Bild; das ganze Video kommt als neue Nachricht, nahtlos zusammengefügt.';
      hint.textContent = continueHint(choice.pick);
      const form = el('form', { class: 'video-ask', onsubmit: (e) => {
        e.preventDefault();
        go.disabled = true;
        MAG.api('POST', '/api/video/continue', { message: m.id, prompt: words.value.trim(), seconds: choice.pick.seconds })
          .then(() => { close(); MAG.scrollToEnd(); }, (x) => { go.disabled = false; MAG.toast(x.message, true); });
      } }, words, choice.box, hint, go);
      content.append(form);
      setTimeout(() => words.focus(), 50);
    });
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
    const files = m.attachments || [], glb = files.find((a) => a.name.endsWith('.glb')),
      film = files.find((a) => a.kind === 'video' && !a.voiceover_kind);
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
    if (film) card.append(MAG.voiceover && film.voiceover_kind !== 'baked'
      ? MAG.voiceover.player(m, film, 'film') : el('video', { class: 'film', controls: true, preload: 'metadata', playsInline: true, src: fileUrl(film) }));
    const link = (a) => {
      const ext = a.name.split('.').pop().toLowerCase();
      return el('a', { class: 'pill', href: fileUrl(a, '?download=1'), download: a.name, title: (sceneFiles[ext] || a.name) + ' herunterladen' },
        MAG.icon('down'), el('b', { text: ext.toUpperCase() }), el('small', { text: (sceneFiles[ext] ? sceneFiles[ext] + ' · ' : '') + MAG.bytes(a.size) }));
    };
    card.append(el('div', { class: 'links' }, files.filter((a) => !a.voiceover_kind).map(link),
      glb && !files.some((a) => a.name.endsWith('.blend'))
        ? el('button', { class: 'pill', title: 'Die Szene als Datei für Blender holen und herunterladen', onclick: (e) => fetchBlend(m, e.currentTarget) },
            MAG.icon('down'), el('b', { text: 'BLEND' }), el('small', { text: sceneFiles.blend })) : null));
    return card;
  }
  // A part of a message that must live on while the message changes (a 3D
  // view that is playing, perhaps in fullscreen): it stays where it is, and
  // everything around it becomes the new message. False: nothing to keep.
  function keep(old, node, message) {
    const prior = [...old.querySelectorAll('[data-keep]')];
    const kept = prior[0], twin = kept && [...node.querySelectorAll('[data-keep]')]
      .find((n) => n.dataset.keep === kept.dataset.keep);
    // A scene keeps BOTH the 3D canvas (first) and the separate video player
    // (second). Migrate every other live element before reconstructing the
    // surrounding DOM, otherwise a recorder is unmounted and discarded.
    for (const extra of prior.slice(1)) {
      if (kept?.contains(extra)) continue;
      const partner = [...node.querySelectorAll('[data-keep]')]
        .find((n) => n.dataset.keep === extra.dataset.keep);
      if (partner) {
        partner.replaceWith(extra);
        extra._voiceoverRefresh?.(message);
      }
    }
    if (!twin) return false;
    kept._voiceoverRefresh?.(message);
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
  // Actions come after every message, the same for all (Chris's, a bot's, a
  // picture, a video, a 3D model, a file): copy, read aloud, and a menu with
  // what must not happen by accident. A job that is still under way can be
  // deleted already; an answer that is still being written cannot.
  function actions(m) {
    const streaming = m.state === 'streaming';
    const user = m.role === 'user', row = el('div', { class: 'acts' });
    const button = (icon, label, run) => el('button', { class: 'act', 'aria-label': label, title: label, html: MAG.icons[icon], onclick: run });
    if (user) row.append(el('span', { class: 'time', text: MAG.clock(m.ts), title: MAG.day(m.ts) }));
    // While the message grows its text lives in the text parts, not m.text.
    const words = m.model3d ? '' : ((m.parts || []).filter(p=>p.k==='text')
      .map(p=>p.text || '').join('') || m.text || '');
    if (words) row.append(button('copy', 'Kopieren', async (e) => {
      const b = e.currentTarget;
      try {
        await navigator.clipboard.writeText(words);
        b.innerHTML = MAG.icons.check;
        setTimeout(() => { b.innerHTML = MAG.icons.copy; }, 1300);
      } catch { MAG.toast('Kopieren nicht möglich', true); }
    }));
    if (words && MAG.speak) {
      const speak = button('speak', 'Vorlesen', (e) => MAG.speak(m, e.currentTarget));
      speak.dataset.speak = m.id; // the one that is being read shows it (speak.js)
      row.append(speak);
    }
    // Teilen: the message's files (a 3D result's model, scene and film too), or its words.
    const files = (m.attachments || []).filter((a) => a.file);
    const shareable = files.length || (words && !m.model3d);
    // Ab hier stays available while the answer is still being written. The
    // reading follows the growing text and whatever arrives after it.
    // Deleting that answer waits until it is finished.
    if (S.you.role === 'owner' && (MAG.speaking?.startFrom || !streaming)) row.append(button('dots', 'Mehr', (e) => { MAG.shareReady(files); MAG.menu(e.currentTarget, [
      // One tap reads this and every subsequent loaded text message in
      // chronological order. Speeds apply only to that playback session.
      MAG.speaking?.startFrom ? { icon: 'play', label: 'Play from here',
        run: () => MAG.speaking.startFrom(m.id, 1) } : null,
      ...[1.25, 1.5, 1.75, 2].map((rate) => MAG.speaking?.startFrom ? {
        icon: 'speak', label: (rate === 2 ? '2,0' : String(rate).replace('.', ',')) + '× Talk Speed · ab hier',
        run: () => MAG.speaking.startFrom(m.id, rate)
      } : null),
      MAG.speaking?.readingFrom?.() ? { icon: 'stop', label: 'Vorlesen stoppen',
        run: () => MAG.speaking.stop() } : null,
      !streaming && shareable ? { icon: 'share', label: 'Teilen', run: () => MAG.share(files, files.length ? '' : m.text) } : null,
      // A video goes back to a 3D scene whose figures do what the video shows.
      !streaming && files.find((a) => a.kind === 'video') && MAG.sceneFromVideo ? { icon: 'cube', label: '3D-Szene aus diesem Video', run: () => MAG.sceneFromVideo(files.find((a) => a.kind === 'video')) } : null,
      !streaming ? { icon: 'trash', label: 'Nachricht löschen', danger: true, run: () => MAG.api('DELETE', '/api/message/' + m.id).catch((x) => MAG.toast(x.message, true)) } : null,
    ]); }));
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
  // Every queued/running/completed/failed/aborted media job uses model3d.
  // This is view state only; the room and live players are not changed.
  function isMediaMessage(m) {
    return !!m.model3d || (m.attachments || []).some(a =>
      !a.voiceover_kind && (a.kind === 'video' || a.kind === 'audio' ||
        (a.kind === 'image' && m.role !== 'user')));
  }
  function build(m) {
    const user = m.role === 'user';
    const node = el('article', { class: 'msg ' +
      (user ? 'user' : m.role === 'service' ? 'service' : 'bot'),
      dataset: { id: m.id, from: m.from, state: m.state || '',
        media: isMediaMessage(m) ? '1' : '' } });
    const picture = m.model3d?.kind === 'image' || m.model3d?.kind === 'video';   // its result is an ordinary image or video
    const files = (m.model3d && !picture ? [] : m.attachments || []).filter((a) => !a.voiceover_kind);
    const atts = files.length ? el('div', { class: 'atts' }, files.map((a) => buildAttachment(a, m))) : null;
    if (user) {
      // The bubble, what was attached, and below them the actions.
      // A routine that came due speaks like a request, marked as the routine's.
      const bubble = m.text ? el('div', { class: 'bubble' + (m.routine ? ' routine' : '') }, m.routine ? el('small', { class: 'tag', text: '⏰ Routine' }) : null, markdown(m.text, false, m.id + ':user')) : null;
      const col = el('div', { class: 'col' }, aboutChip(m), bubble, atts);
      node.append(el('div', { class: 'line' }, col, actions(m)));
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
      const anchor = groupAnchor(m);
      let placed = false;
      (m.parts || []).forEach((p, i) => {
        if (p.k === 'step') {
          if (!placed && anchor.id === m.id) {
            placed = true;
            const group = goalGroup(m);
            if (group) body.append(group);
            const browser = goalBrowser(m);
            if (browser) body.append(browser);
          }
        } else body.append(buildPart(m, i));
      });
      if (!(m.parts || []).length && m.text) body.append(markdown(m.role === 'bot' ? tidy(m.text) : m.text, false, m.id + ':fallback'));
      node.append(body);
      placePages(m, body);
      // A finished answer of nothing but what is not shown (a request in words, a written-out call) is no message.
      const texts = (m.parts || []).length ? m.parts : [{ k: 'text', text: m.text || '' }];
      if (m.role === 'bot' && m.state !== 'streaming' && !(m.attachments || []).length && texts.every((p) => p.k === 'text' && !tidy(p.text || '')))
        node.hidden = true;
      // Continuation messages may contain only tool steps. Their steps
      // appear in the original goal group, not as a second empty bot row.
      if (m.goal_id && groupAnchor(m).id !== m.id &&
          !(m.attachments || []).length &&
          (m.parts || []).every((p) => p.k === 'step' || p.k === 'text' && !tidy(p.text || '')))
        node.hidden = true;
      // The other bot carried the task out; this one stood back.
      if (m.yielded || (m.state !== 'streaming' && (m.parts || []).some((p) => p.k === 'step' && / erledigt diese Aufgabe schon/.test(p.output || '')))) node.hidden = true;
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
    // Only configured bot names count as presentation labels; all stacked
    // leading labels are removed, without ever editing mid-sentence names.
    const botNames = (S.bots || []).map(b => b.name).filter(Boolean);
    const labels = botNames.map(name => {
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return new RegExp('^\\s*(?:\\*\\*' + escaped + ':\\*\\*|\\*\\*' +
        escaped + '\\*\\*:|\\[' + escaped + '\\]:?|' + escaped + '[:：,])\\s*', 'i');
    });
    for (;;) {
      const before = text;
      for (const re of labels) {
        if (re.test(text)) { text = text.replace(re, ''); break; }
      }
      if (text === before) break;
    }
    // The room's markers and a lone PASS are for the room (it takes them out too; this is for a page from before).
    text = text.replace(/\[\[MAG:[^\]]*\]\]/g, '');
    if (/^[\s*_`"'.()[\]]*pass[\s*_`"'.!()[\]]*$/i.test(text)) return '';
    // Thoughts, closed or not: <thought, <think>, <thinking>, and an analysis channel.
    // A "<thought" that never got its ">" still hides everything after it.
    text = text.replace(/<\|channel\|>\s*analysis\b[\s\S]*?(?:<\|end\|>|<\|start\|>|$)/gi, '');
    text = text.replace(/<\/?think(?:ing)?\b[^>]*>[\s\S]*?(?:<\/think(?:ing)?>|$)/gi, '');
    text = text.replace(/<thought\b[^>]*>[\s\S]*?(?:<\/thought>|$)/gi, '');
    text = text.replace(/<thought\b[\s\S]*$/gi, '');
    text = text.replace(/<\/thought>/gi, '');
    // A fenced block that is a tool call written out (JSON with an action or a function name).
    text = text.replace(/```(?:json)?\s*\n([\s\S]*?)\n```/g, (all, inner) => {
      try { const v = JSON.parse(inner); return v && typeof v === 'object' && (v.action || v.name || v.arguments || v.tool) ? '' : all; } catch { return all; }
    });
    // The same call without a fence, written over several lines: from a line
    // that starts with { to a line that ends with }, when the object names a
    // tool (action, name, tool) or carries arguments.
    {
      const lines = text.split('\n');
      const drop = new Array(lines.length).fill(false);
      let fence = false;
      for (let i = 0; i < lines.length; i++) {
        if (/^\s*```/.test(lines[i])) { fence = !fence; continue; }
        if (fence || drop[i] || !/^\s*\{/.test(lines[i])) continue;
        for (let j = i; j < lines.length; j++) {
          if (j > i && /^\s*```/.test(lines[j])) break;
          if (!/\}\s*$/.test(lines[j])) continue;
          let v;
          try { v = JSON.parse(lines.slice(i, j + 1).join('\n')); } catch { continue; }
          if (v && typeof v === 'object' && !Array.isArray(v) && (v.action || v.name || v.tool || v.arguments)) {
            for (let k = i; k <= j; k++) drop[k] = true;
            i = j;
          }
          break;
        }
      }
      text = lines.filter((_, i) => !drop[i]).join('\n');
    }
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
      // "Step 1: …" is the model's plan. The step card already shows the action.
      if (/^\s*step\s+\d+\s*:/i.test(t)) return false;
      if (/^\{.*\}$/.test(t)) { try { JSON.parse(t); return false; } catch { /* text */ } }
      return !(german && lang(t) === 'en');
    });
    return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  MAG.tidy = tidy;
  function buildPart(m, index) {
    const p = m.parts[index];
    if (p.k === 'step') return buildStep(m, index);
    const node = markdown(m.role === 'bot' ? tidy(p.text) : p.text, m.state === 'streaming', m.id + ':' + index);
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
      const m = S.messages.get(node.dataset.id);
      // Hidden bot rows must never eat the day's separator from the first
      // visible generation. This also respects the room's own hidden rows.
      if (node.hidden || (mediaOnly && node.dataset.media !== '1')) {
        delete node.dataset.day;
        continue;
      }
      const day = m ? dayOf(m.ts) : '';
      if (day && day !== last) node.dataset.day = day;
      else delete node.dataset.day;
      if (day) last = day;
    }
  }
  function paintEmpty() {
    const hasMedia = [...S.messages.values()].some(isMediaMessage);
    empty.hidden = mediaOnly ? hasMedia : S.messages.size > 0;
    if (mediaOnly && !hasMedia) empty.textContent = mediaLoading
      ? 'Medien werden geladen …' : 'Noch keine Medien vorhanden.';
    else if (empty.dataset.originalHtml) empty.innerHTML = empty.dataset.originalHtml;
  }
  async function loadMedia(reset = false) {
    if (reset) {
      // A new view must fetch its own first page, even when an older-page
      // request is still in flight. Do not let the stale response change
      // the cursor or the live message DOM after this reset.
      mediaGeneration++;
      mediaCursor = '';
      mediaMore = true;
      mediaRequest = null;
    }
    if (mediaRequest) return mediaRequest;
    if (!mediaMore) return;
    const generation = mediaGeneration;
    const cursor = mediaCursor;
    mediaLoading = true;
    paintEmpty();
    const request = (async () => {
      try {
        const page = await MAG.api('GET', '/api/media?limit=200' +
          (cursor ? '&before=' + encodeURIComponent(cursor) : ''));
        if (generation !== mediaGeneration) return; // stale page or reset
        // Preserve existing chat/SSE objects; only add historical media.
        for (const m of page.messages || []) if (!S.messages.has(m.id)) S.messages.set(m.id, m);
        mediaCursor = page.messages?.[0]?.id || cursor;
        mediaMore = !!page.more;
        if (mediaOnly) MAG.onMediaPage?.();
      } catch (err) {
        if (generation === mediaGeneration)
          MAG.toast?.('Medien konnten nicht geladen werden: ' + err.message, true);
      } finally {
        if (generation === mediaGeneration) {
          mediaLoading = false;
          mediaRequest = null;
          if (mediaOnly) { paintEmpty(); olderBtn.hidden = !mediaMore; }
        }
      }
    })();
    mediaRequest = request;
    return request;
  }
  function setMediaOnly(value, resetPosition = true) {
    mediaOnly = !!value;
    $('#app')?.classList.toggle('media', mediaOnly);
    const button = $('#media-filter');
    if (button) {
      button.setAttribute('aria-pressed', String(mediaOnly));
      button.classList.toggle('on', mediaOnly);
      button.title = mediaOnly ? 'Alle Nachrichten anzeigen' : 'Nur Medien anzeigen';
    }
    try { localStorage.setItem('mag-media-only', mediaOnly ? '1' : '0'); } catch (_) {}
    paintDays();
    paintEmpty();
    olderBtn.hidden = mediaOnly ? !mediaMore : !S.more;
    if (mediaOnly && S.ready) loadMedia(true);
    if (resetPosition) toBottom();
  }

  // Reuse an existing editor/preview when the server's ORIGINAL code did
  // not change. This keeps focus, selection, scroll and WebGL iframe state
  // across status events and other text arriving in the same message.
  function preserveEditors(old, next) {
    const selector = '.page[data-path], .codeblock[data-code-key]';
    const identity = (n) => n.classList.contains('page')
      ? 'page:' + n.dataset.path : 'code:' + n.dataset.codeKey;
    const prior = new Map();
    old.querySelectorAll(selector).forEach((n) => {
      if (n.closest('.page') && !n.classList.contains('page')) return;
      prior.set(identity(n), n);
    });
    next.querySelectorAll(selector).forEach((n) => {
      if (n.closest('.page') && !n.classList.contains('page')) return;
      const former = prior.get(identity(n));
      if (former && former._sourceOriginal === n._sourceOriginal &&
          // A completed code block must upgrade from display to editor.
          !(former.classList.contains('codeblock-live') &&
            !n.classList.contains('codeblock-live'))) {
        n.replaceWith(former);
        // Building the discarded twin briefly registered a new status
        // observer for this draft. Give persistence updates back to the
        // textarea we actually kept on screen.
        (former._editor || former._editable)?.activate?.();
      }
    });
  }
  function upsert(m, fresh) {
    const old = nodes.get(m.id);
    if (old && !m.model3d && m.state === 'streaming' && old.dataset.state === 'streaming' &&
        (m.parts || []).every((p) => p.k === 'text') &&
        !(m.pages || []).length && !(m.attachments || []).length) {
      // Some backends send complete text snapshots even while streaming.
      // Never rebuild the entire message or a live editor for each token.
      // Job cards (including empty parts arrays), tool steps, attachments
      // and published pages still use the full path for live status updates.
      // The ordinary throttled delta path updates only the touched text part.
      (m.parts || []).forEach((p, index) => {
        if (p.k === 'text') dirty.add(m.id + ':' + index);
      });
      schedule();
      return;
    }
    // The whole message is rebuilt only for a state transition or changed
    // published content. A queued re-render of its parts is then obsolete.
    for (const key of dirty) if (key.startsWith(m.id + ':')) dirty.delete(key);
    let node = build(m);
    if (old) {
      preserveEditors(old, node);
      // Keep what the reader opened.
      old.querySelectorAll('details[open]').forEach((d) => {
        const twin = d.classList.contains('think') ? node.querySelector('.think')
          : d.classList.contains('codeblock') ? node.querySelectorAll('details.codeblock')[[...old.querySelectorAll('details.codeblock')].indexOf(d)]
          : d.classList.contains('goal-group') ? node.querySelector('.goal-group')
          : d.classList.contains('goal-browser') ? node.querySelector('.goal-browser')
          : node.querySelector('details[data-part="' + d.dataset.part + '"]');
        if (twin && (!d.classList.contains('goal-group') || groupPrefs.has(d.dataset.groupkey))) twin.open = true;
      });
      grow(() => { if (keep(old, node, m)) node = old; else old.replaceWith(node); });
    } else {
      // Ids sort by time: a new message goes where its id belongs (almost always the end).
      let after = null;
      for (const kid of list.children) if (kid.dataset.id > m.id) { after = kid; break; }
      grow(() => list.insertBefore(node, after), fresh && !after &&
        (!mediaOnly || isMediaMessage(m)));
    }
    nodes.set(m.id, node);
    if (m.goal_id && groupAnchor(m).id !== m.id) refreshGroup(m);
    paintEmpty();
    paintDays();
    MAG.speaking?.paint();
  }

  function renderAll() {
    list.replaceChildren();
    nodes.clear();
    dirty.clear();
    const ordered = [...S.messages.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    for (const m of ordered) { const node = build(m); nodes.set(m.id, node); list.append(node); }
    paintEmpty();
    olderBtn.hidden = mediaOnly ? !mediaMore : !S.more;
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
      grow(() => {
        if (old) {
          preserveEditors(old, fresh);
          old.replaceWith(fresh);
        } else body.append(fresh);
        markStreaming(node, m);
      });
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
  // All model activity is displayed by the header chips. No growing
  // activity card belongs in the timeline: it steals mobile reading space.
  function paintActivity() {}

  setInterval(() => {
    if (!list) return; // not mounted yet; the activity row was intentionally removed
    list.querySelectorAll('.m3 .clock').forEach((node) => { node.textContent = clock3d(node.dataset); });
    list.querySelectorAll('.step.running').forEach((node) => {
      const m = S.messages.get(node.dataset.message || node.closest('.msg')?.dataset.id);
      const step = m?.parts?.[+node.dataset.part];
      if (step && node.querySelector('.time')) node.querySelector('.time').textContent = stepTime(step);
    });
    list.querySelectorAll('.goal-group.running').forEach((node) => {
      const target = node.querySelector('.goal-duration');
      if (target) target.textContent = MAG.stopwatch(Date.now() - +node.dataset.since);
    });
  }, 1000);

  // ---- wiring -------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    scroller = $('#scroll'); list = $('#list'); jump = $('#jump'); activity = $('#activity');
    empty = $('#empty'); olderBtn = $('#older');
    if (empty) empty.dataset.originalHtml = empty.innerHTML;
    const button = $('#media-filter');
    if (button && $('#app')) {
      button.addEventListener('click', () => setMediaOnly(!mediaOnly));
      setMediaOnly(mediaOnly, false);
    } else mediaOnly = false; // Old embedded jsdom fixtures work without the optional toggle.
    // Scroll position alone is not intent: native anchoring or a group
    // redraw can produce small downward scroll events. Only a fresh USER
    // gesture toward the true bottom is allowed to re-enable follow mode.
    const gesture = (dir) => {
      inputDirection = dir;
      inputUntil = performance.now() + 600;
      if (dir === 'up' && pinned) { pinned = false; paintJump(); }
    };
    scroller.addEventListener('wheel', (e) => {
      if (e.deltaY < -1) gesture('up');
      else if (e.deltaY > 1) gesture('down');
    }, { passive: true });
    scroller.addEventListener('touchstart', (e) => { touchY = e.touches?.[0]?.clientY ?? null; }, { passive: true });
    scroller.addEventListener('touchmove', (e) => {
      const y = e.touches?.[0]?.clientY;
      if (touchY !== null && y != null && Math.abs(y - touchY) > 2)
        gesture(y > touchY ? 'up' : 'down');
      if (y != null) touchY = y;
    }, { passive: true });
    scroller.addEventListener('keydown', (e) => {
      if (['PageUp', 'ArrowUp', 'Home'].includes(e.key) || (e.key === ' ' && e.shiftKey)) gesture('up');
      if (['PageDown', 'ArrowDown', 'End'].includes(e.key) || (e.key === ' ' && !e.shiftKey)) gesture('down');
    });
    scroller.addEventListener('scroll', () => {
      const current = scroller.scrollTop;
      const up = current < lastScrollTop - 1, down = current > lastScrollTop + 1;
      const userIntent = performance.now() <= inputUntil;
      lastScrollTop = current;
      if (up && userIntent && inputDirection === 'up' && pinned) {
        pinned = false;
        paintJump();
      } else if (!pinned && down && userIntent && inputDirection === 'down' && atTrueEnd()) {
        pinned = true; unread = 0; inputDirection = 'none'; paintJump();
      }
    }, { passive: true });
    jump.addEventListener('click', toBottom);
    olderBtn.addEventListener('click', () =>
      (mediaOnly ? loadMedia(false) : MAG.loadOlder()).catch((x) => MAG.toast(x.message, true)));
    new ResizeObserver(() => { if (pinned) setScrollTop(scroller.scrollHeight); }).observe(scroller);
  });
  MAG.on('reset', () => {
    mediaCursor = ''; mediaMore = false;
    renderAll(); showPushTarget();
    if (mediaOnly) loadMedia(true);
  });
  MAG.on('message', (m) => { upsert(m, !nodes.has(m.id)); showPushTarget(); });
  MAG.on('gone', (id) => {
    nodes.get(id)?.remove(); nodes.delete(id);
    // The deleted message may have been the anchor for a multi-turn goal.
    // Rebuild remaining groups using their next historical owner.
    for (const m of S.messages.values()) {
      if (!m.goal_id || !(m.parts || []).some((p) => p.k === 'step')) continue;
      if (groupAnchor(m).id === m.id) upsert(m, false);
    }
    paintEmpty(); paintDays();
  });
  MAG.on('delta', (m, part) => {
    dirty.add(m.id + ':' + part);schedule();
    // Initial streaming card may have been empty. Add the read/stop button
    // as soon as the first text delta arrives; don't rebuild the message.
    const node=nodes.get(m.id);
    if(node && !node.querySelector('.act[data-speak]') &&
       (m.parts || []).some(p=>p?.k==='text' && p.text?.trim())) {
      node.querySelector('.acts')?.remove();
      const bar=actions(m);
      if(bar) { node.append(bar); MAG.speaking?.paint?.(); }
    }
  });
  // A model's thoughts are its own: raw, often English, never shown in the chat (Chris, 2026-11-12).
  MAG.on('step', (m, part) => {
    const node = nodes.get(m.id);
    if (!node) return;
    flush();
    if (taskPartLocation(m, part)) {
      const anchor = groupAnchor(m);
      if (!nodes.get(anchor.id)?.querySelector('.goal-group')) upsert(anchor, false);
      else refreshGroup(m);
      return;
    }
    const body = node.querySelector('.body'), old = body.querySelector(':scope > [data-part="' + part + '"]'), fresh = buildStep(m, part);
    if (old) {
      fresh.open = old.open;
      // The live picture is one node per bot. Move it across before the old
      // step is dropped, so a step update does not detach it and drop the frames.
      const live = old.querySelector('.live');
      if (live) {
        const slot = fresh.querySelector('.live-slot');
        if (slot) { if (fresh.open) slot.append(live); }
        else fresh.querySelector('.ask')?.insertBefore(live, fresh.querySelector('.ask .fine'));
      }
      grow(() => old.replaceWith(fresh));
    } else grow(() => body.append(fresh));

    markStreaming(node, m);
  });
  MAG.on('step_output', (m, part, text) => {
    const node = locateStep(m, part);
    if (!node) return;
    const out = node.querySelector('pre.out'), peek = node.querySelector('.peek');
    if (!out) return;
    const follow = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
    grow(() => {
      out.append(text);
      if (peek) peek.textContent = lastLines(m.parts[part].output || '');
    });
    if (node.open && follow) out.scrollTop = out.scrollHeight;
  });
  MAG.on('bots', () => {
    paintActivity();
    for (const m of S.messages.values()) {
      if (!m.goal_id || groupAnchor(m).id !== m.id) continue;
      const phase = groupInfo(m).state, key = groupKey(m);
      if (goalPhases.get(key) !== phase) refreshGroup(m);
    }
  });
  MAG.onMediaPage = () => {
    // Media history arrives as an ordinary append-only snapshot, but must not
    // replace existing player DOM nodes that are currently playing.
    const ordered = [...S.messages.values()].sort((a,b) => a.id < b.id ? -1 : 1);
    for (const m of ordered) if (!nodes.has(m.id)) upsert(m, false);
    paintDays();
    olderBtn.hidden = !mediaMore;
  };
  MAG.on('older', () => {
    const before = scroller.scrollHeight;
    const ordered = [...S.messages.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    list.replaceChildren();
    nodes.clear();
    for (const m of ordered) { const node = build(m); nodes.set(m.id, node); list.append(node); }
    olderBtn.hidden = !S.more;
    paintEmpty();
    paintDays();
    scroller.scrollTop += scroller.scrollHeight - before; // stay on the same message
  });
})();

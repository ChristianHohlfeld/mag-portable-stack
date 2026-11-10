// Top bar, the trash button, and the one place for all settings.
'use strict';
(function () {
  const { MAG } = window, { el, $, state: S } = MAG;
  const owner = () => S.you.role === 'owner';
  const call = (method, path, body) => MAG.api(method, path, body).catch((err) => { MAG.toast(err.message, true); throw err; });

  // ---- bot state in words -------------------------------------------------
  function modelText(b) {
    const m = b.model || {}, f = m.model || {};
    const pct = f.total ? Math.floor((f.bytes / f.total) * 100) : 0;
    switch (m.state) {
      case 'ready': return 'bereit';
      case 'downloading': return m.detail && f.bytes >= f.total ? m.detail : 'lädt herunter ' + pct + ' %' + (f.eta ? ' · ' + MAG.dur(f.eta * 1000) : '');
      case 'missing': return 'Modell fehlt';
      case 'verifying': return 'wird geprüft' + (f.verified ? ' ' + Math.floor(f.verified * 100) + ' %' : '');
      case 'loading': return 'lädt ins RAM';
      case 'paused': return 'pausiert';
      case 'stopped': return 'abgeschaltet';
      case 'failed': return 'Start fehlgeschlagen';
      case 'corrupt': return 'Datei beschädigt';
      default: return 'nicht erreichbar';
    }
  }
  const phases = { reading: 'liest', thinking: 'denkt nach', writing: 'schreibt', tool: 'arbeitet', warming: 'wärmt auf', resetting: 'leert Kontext' };
  // The exact weights, e.g. "gpt-oss-120b-heretic-v2 MXFP4".
  const modelName = (b) => (b.model?.file ? b.model.file.replace(/-([A-Za-z0-9_]+)$/, ' $1') : b.model?.label || '');
  const siteLink = (b) => (b.site ? el('a', { href: b.site + '/', target: '_blank', rel: 'noopener', text: b.site.replace(/^https?:\/\//, '') }) : null);
  function chipState(b) {
    const st = b.model?.state;
    if (st === 'stopped') return ['off', 'aus'];
    if (!b.enabled) return ['off paused', 'pausiert'];
    if (st !== 'ready') {
      // The chip stays short; the sheet has the details.
      const f = b.model?.model || {};
      const short = st === 'downloading' && f.total && f.bytes < f.total ? 'lädt ' + Math.floor((f.bytes / f.total) * 100) + ' %' : modelText(b);
      return [st === 'failed' || st === 'corrupt' ? 'bad' : st === 'unknown' || !st ? 'off' : 'wait', short];
    }
    if (b.busy || b.phase !== 'idle') return ['busy', phases[b.phase] || 'arbeitet'];
    return ['ready', 'bereit'];
  }
  const nudged = {}; // bot -> until when its chip moves
  // Pause: the bot stops answering, its model stays in memory, so it is back
  // at once. The room remembers it across reloads and deploys.
  const pause = (b, on) => MAG.api('PUT', '/api/bot/' + b.id, { enabled: !on })
    .then(() => MAG.toast(on ? b.name + ' pausiert. Antwortet nicht, bis du fortsetzt.' : b.name + ' ist wieder dabei.'), (err) => MAG.toast(err.message, true));
  function paintTop() {
    const box = $('#chips');
    if (!box) return;
    box.replaceChildren(...S.bots.map((b) => {
      const [cls, text] = chipState(b);
      // The name opens the bot's sheet; the dot in front of it pauses the bot
      // and brings it back. Ready is just the green dot; every other state
      // says what it is.
      const stopped = b.model?.state === 'stopped';
      const tip = stopped ? b.name + ' ist abgeschaltet. Einschalten im Bot-Menü.'
        : b.enabled ? b.name + ' pausieren: antwortet dann nicht mehr, das Modell bleibt geladen' : b.name + ' fortsetzen';
      let held = 0, explained = false;
      const dot = el('button', { class: 'tog', disabled: !owner(), 'aria-label': tip, title: tip, 'aria-pressed': String(!b.enabled),
        // A long press explains instead of switching.
        onpointerdown: () => { explained = false; held = setTimeout(() => { explained = true; MAG.toast(tip); }, 550); },
        onpointerup: () => clearTimeout(held), onpointerleave: () => clearTimeout(held), oncontextmenu: (e) => e.preventDefault(),
        onclick: () => { if (explained) return; if (stopped) return botSheet(b.id); pause(b, b.enabled); } }, el('span', { class: 'dot' }));
      return el('div', { class: 'chip ' + cls + (nudged[b.id] > Date.now() ? ' nudge' : ''), dataset: { bot: b.id } }, dot,
        el('button', { class: 'open', 'aria-label': b.name + ': ' + text, title: b.name + ': ' + text, onclick: () => botSheet(b.id) },
          el('span', { class: 'name', text: b.name }), cls === 'ready' ? null : el('span', { class: 'state', text })));
    }));
    $('#clear').hidden = !owner();
    document.querySelector('.sheet')?.refresh?.();
  }

  // ---- trash --------------------------------------------------------------
  async function clearRoom() {
    if (!(await MAG.confirm('Raum leeren?', 'Alle Nachrichten, Dateien und 3D-Ergebnisse werden gelöscht und der Kontext beider Bots geleert. Die Modelle bleiben geladen.', 'Leeren', true))) return;
    try {
      const out = await MAG.api('POST', '/api/room/clear', {});
      const failed = Object.entries(out.bots || {}).filter(([, v]) => v !== 'ok' && v !== 'offline');
      MAG.toast(failed.length ? 'Raum geleert; Kontext von ' + failed.map(([k]) => MAG.bot(k)?.name || k).join(', ') + ': ' + failed[0][1] : 'Raum und Bot-Kontext geleert', failed.length > 0);
    } catch (err) {
      MAG.toast(err.message, true);
    }
  }

  // ---- building blocks ----------------------------------------------------
  const row = (title, hint, control) => el('div', { class: 'set' }, el('div', { class: 'txt' }, title, hint ? el('small', { text: hint }) : null), control);
  const toggle = (on, change, disabled) => el('label', { class: 'switch' }, el('input', { type: 'checkbox', checked: !!on, disabled: !!disabled, onchange: (e) => change(e.target.checked) }), el('i'));
  const select = (value, options, change, disabled) => {
    const node = el('select', { disabled: !!disabled, onchange: (e) => change(e.target.value) }, options.map(([v, label]) => el('option', { value: v, text: label })));
    node.value = String(value);
    return node;
  };

  // ---- one bot ------------------------------------------------------------
  function botSheet(id) {
    let live = null;
    const opened = MAG.sheet(MAG.bot(id)?.name || id, (content, sheet) => {
      const b = MAG.bot(id);
      if (!b) return;
      const status = el('div', { class: 'card' }), acts = el('div', { class: 'btns' }), actsTitle = el('h3', { text: 'Steuerung' });
      const actsHint = el('p', { class: 'fine', text: 'Pausiert: antwortet nicht mehr, das Modell bleibt im Arbeitsspeicher und ist sofort zurück. Abgeschaltet: das Modell ist entladen und der Arbeitsspeicher frei; Einschalten lädt es neu. Beides bleibt über Neuladen und Updates hinweg so.' });
      // Status and controls follow the live state; the rest is built once.
      sheet.refresh = () => {
        const b = MAG.bot(id);
        if (!b) return;
        sheet.querySelector('header h2').textContent = b.name;
        const m = b.model || {}, f = m.model || {};
        const info = el('dl', { class: 'kv' });
        const add = (k, v) => { if (v !== undefined && v !== null && v !== '') info.append(el('dt', { text: k }), v instanceof Node ? el('dd', {}, v) : el('dd', { text: String(v) })); };
        add('Name', b.name);
        add('Modell', modelName(b));
        add('Website', siteLink(b));
        add('Zustand', modelText(b) + (!b.enabled && m.state !== 'stopped' ? ' · pausiert, antwortet nicht' : ''));
        if (m.state === 'downloading' && f.total) add('Download', MAG.bytes(f.bytes) + ' von ' + MAG.bytes(f.total) + (f.rate > 1 ? ' · ' + MAG.bytes(f.rate) + '/s' : ''));
        if (m.detail && m.state !== 'downloading') add('Hinweis', m.detail);
        if (m.draft) add('Drafter (MTP)', m.draft.active ? 'aktiv' : m.draft.ok ? 'bereit, aktiv nach Neustart' : 'nicht vorhanden');
        if (m.mmproj && !b.vision) add('Bilder sehen', m.mmproj.ok ? 'bereit, aktiv nach Neustart' : m.mmproj.total ? 'wird geladen ' + Math.floor(((m.mmproj.bytes || 0) / m.mmproj.total) * 100) + ' %' : 'nicht vorhanden');
        else add('Bilder', b.vision ? 'sieht sie selbst' : 'liest ihre Beschreibung');
        if (m.state === 'ready') {
          add('Kontext', b.ctx_tokens ? b.ctx_tokens.toLocaleString('de-DE') + ' von ' + b.ctx_budget.toLocaleString('de-DE') + ' Tokens' : 'leer');
          if (b.tps) add('Tempo', b.tps.toFixed(1).replace('.', ',') + ' Tokens/s');
        }
        if (m.numa !== undefined) add('Rechner', 'NUMA ' + m.numa + ' · nur CPU');
        add('VM', b.vm_note === 'creating' ? 'wird erstellt …' : b.vm || 'keine (nur Chat)');
        if (b.vm_note && b.vm_note !== 'creating') add('VM-Fehler', b.vm_note.replace(/^error: /, ''));
        if (m.restart_pending) add('Neustart', 'neue Startparameter warten auf einen Neustart');
        status.replaceChildren(info);
        if (m.state === 'downloading' && f.total) status.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('div', { class: 'progress' }, el('i', { style: 'width:' + (f.bytes / f.total) * 100 + '%' })))));
        acts.replaceChildren();
        if (owner()) {
          if (b.busy) acts.append(el('button', { class: 'btn', text: 'Antwort stoppen', onclick: () => call('POST', '/api/stop', { bot: id }) }));
          const model = (action, label, danger) => el('button', { class: 'btn' + (danger ? ' danger' : ''), text: label, onclick: () => call('POST', '/api/bot/' + id + '/model', { action }).then(() => MAG.toast(label + ' angefordert')) });
          // Two ways to take a bot out. Paused: it stops answering, its model
          // stays in memory. Switched off: the model server stops and frees its
          // memory; the weights stay on disk.
          if (m.state !== 'stopped') acts.append(el('button', { class: 'btn', text: b.enabled ? 'Pausieren' : 'Fortsetzen', onclick: () => pause(b, b.enabled) }));
          if (m.state === 'paused') acts.append(model('thaw', 'Modell auftauen'));
          if (m.state === 'stopped' || m.state === 'failed') acts.append(model('start', 'Einschalten'));
          else {
            if (m.state === 'ready') acts.append(model('restart', 'Modell neu laden'));
            acts.append(el('button', { class: 'btn danger', text: 'Abschalten', onclick: async () => {
              if (await MAG.confirm(b.name + ' abschalten?', 'Das Modell wird aus dem Arbeitsspeicher entladen und antwortet nicht mehr. Die Modelldatei bleibt. Einschalten lädt es neu, das dauert je nach Modell einige Minuten.', 'Abschalten', true))
                call('POST', '/api/bot/' + id + '/model', { action: 'stop' }).then(() => MAG.toast(b.name + ' wird abgeschaltet'));
            } }));
          }
          if (!b.vm && b.vm_note !== 'creating')
            acts.append(el('button', { class: 'btn', text: 'VM erstellen', onclick: () => call('POST', '/api/bot/' + id + '/vm').then(() => MAG.toast('VM wird erstellt (einige Minuten)')) }));
        }
        actsTitle.hidden = acts.hidden = actsHint.hidden = !acts.children.length;
      };
      content.append(el('h3', { text: 'Status' }), status, actsTitle, acts, actsHint);
      sheet.refresh();

      if (owner()) {
        // The name is the address: the site follows it, earlier names lead there.
        const zone = S.zone ? '.' + S.zone : '';
        const name = el('input', { type: 'text', value: b.name, maxLength: 24, autocapitalize: 'words', autocomplete: 'off', spellcheck: false, 'aria-label': 'Name' });
        const rename = () => {
          const wanted = name.value.trim();
          if (!wanted || wanted === MAG.bot(id)?.name) return;
          call('PUT', '/api/bot/' + id, { name: wanted }).then(() => MAG.toast('Heißt jetzt ' + wanted)).catch(() => { name.value = MAG.bot(id)?.name || ''; });
        };
        name.addEventListener('change', rename);
        name.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); name.blur(); } });
        content.append(el('h3', { text: 'Verhalten' }), el('div', { class: 'card' },
          row('Name', 'Buchstaben, Ziffern, Bindestrich. Die Website folgt dem Namen (name' + zone + '), frühere Namen leiten weiter.', name),
          id === 'lena'
            ? row('Nachdenken', 'Denkt vor der Antwort; gründlicher, langsamer', toggle(b.thinking, (v) => call('PUT', '/api/bot/' + id, { thinking: v })))
            : row('Denktiefe', 'Wie lange vor der Antwort überlegt wird', select(b.effort, [['low', 'Kurz'], ['medium', 'Mittel'], ['high', 'Gründlich']], (v) => call('PUT', '/api/bot/' + id, { effort: v }))),
          el('div', { class: 'set col' }, el('div', { class: 'txt' }, 'Art und Auftrag', el('small', { text: 'Persönlichkeit und feste Vorgaben, in eigenen Worten' })),
            el('textarea', { value: b.persona || '', placeholder: 'z. B. Antworte knapp und direkt.', onchange: (e) => call('PUT', '/api/bot/' + id, { persona: e.target.value }).then(() => MAG.toast('Gespeichert')) }))));

        const mem = el('div', { class: 'card' }, el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Lädt …' }))));
        content.append(el('h3', { text: 'Erinnerungen' }), mem);
        const loadMemory = () => MAG.api('GET', '/api/bot/' + id + '/memory').then((out) => {
          mem.replaceChildren();
          if (!out.memory.length) mem.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Noch nichts gemerkt.' }))));
          out.memory.forEach((entry, i) => mem.append(el('div', { class: 'mem' }, el('span', { text: entry.text }),
            el('button', { class: 'icon-btn', 'aria-label': 'Vergessen', html: MAG.icons.x, onclick: () => call('DELETE', '/api/bot/' + id + '/memory/' + i).then(loadMemory) }))));
        }).catch(() => mem.replaceChildren());
        loadMemory();
      }

      // The bot's own VM: its browser, a terminal and its files.
      if (b.vm && MAG.liveView) {
        const box = el('div', { class: 'vm-view' }), tabs = el('div', { class: 'seg vm-tabs' });
        const views = { browser: ['Browser', MAG.liveView], terminal: ['Terminal', MAG.terminal], files: ['Dateien', MAG.files] };
        const show = (key) => {
          live?.stop();
          live = views[key][1](id);
          box.replaceChildren(live.node);
          for (const tab of tabs.children) tab.classList.toggle('on', tab.dataset.key === key);
        };
        for (const [key, [label]] of Object.entries(views))
          if (key === 'browser' || owner()) tabs.append(el('button', { text: label, dataset: { key }, onclick: () => show(key) }));
        content.append(el('h3', { text: 'VM ' + b.vm }), tabs, box);
      }
    });
    opened.addEventListener('closed', () => live?.stop());
  }

  // ---- settings: everything in one place ----------------------------------
  function settingsSheet() {
    MAG.sheet('Einstellungen', (content, sheet) => {
      const s = S.settings;
      content.append(el('h3', { text: 'Bots' }), el('div', { class: 'card' }, S.bots.map((b) =>
        el('div', { class: 'set', onclick: (e) => { if (!e.target.closest('label, a')) botSheet(b.id); }, style: 'cursor:pointer' },
          el('div', { class: 'txt' }, b.name, el('small', { text: modelName(b) + ' · ' + modelText(b) }), b.site ? el('small', {}, siteLink(b)) : null),
          owner() ? toggle(b.enabled, (v) => call('PUT', '/api/bot/' + b.id, { enabled: v })) : null))));

      content.append(el('h3', { text: 'Gespräch' }), el('div', { class: 'card' },
        row('Bots untereinander', 'Wie oft sich Lena und Noah nach einer Nachricht von dir gegenseitig antworten dürfen. Unbegrenzt ruht nach 24 Antworten ohne dich.',
          select(s.bot_rounds ?? 0, [[0, 'Unbegrenzt'], [1, '1 Runde'], [2, '2 Runden'], [3, '3 Runden'], [5, '5 Runden'], [10, '10 Runden']], (v) => call('PUT', '/api/settings', { bot_rounds: +v }), !owner())),
        row('Internet für Bots', 'Websuche und Browser dürfen ins Netz', toggle(s.web !== false, (v) => call('PUT', '/api/settings', { web: v }), !owner()))));

      const theme = localStorage.getItem('mag-theme') || 'auto', size = localStorage.getItem('mag-size') || '16';
      content.append(el('h3', { text: 'Darstellung' }), el('div', { class: 'card' },
        row('Farben', null, select(theme, [['auto', 'Wie das Gerät'], ['dark', 'Dunkel'], ['light', 'Hell']], (v) => { localStorage.setItem('mag-theme', v); applyLook(); })),
        row('Schriftgröße', null, select(size, [['15', 'Klein'], ['16', 'Normal'], ['18', 'Groß'], ['20', 'Sehr groß']], (v) => { localStorage.setItem('mag-size', v); applyLook(); }))));

      if (S.features.push && 'serviceWorker' in navigator && 'PushManager' in window) {
        const state = el('small', { text: 'wird geprüft …' });
        const sw = toggle(false, (v) => setPush(v).then(() => sheet.rebuild()));
        content.append(el('h3', { text: 'Mitteilungen' }), el('div', { class: 'card' }, el('div', { class: 'set' }, el('div', { class: 'txt' }, 'Auf diesem Gerät', state), sw)));
        pushState().then((on) => { sw.firstChild.checked = on; state.textContent = Notification.permission === 'denied' ? 'im Browser blockiert' : on ? 'bei fertigen langen Antworten und Fehlern' : 'aus'; });
      }

      const sys = el('dl', { class: 'kv' }, el('dt', { text: 'Version' }), el('dd', { text: MAG.version }));
      content.append(el('h3', { text: 'System' }), el('div', { class: 'card' }, sys));
      MAG.api('GET', '/api/system').then((info) => {
        sys.append(el('dt', { text: 'Foto zu 3D' }), el('dd', { text: info.idol ? 'bereit (MI50)' : 'nicht erreichbar' }));
        sys.append(el('dt', { text: '3D-Szenen' }), el('dd', { text: info.scene ? 'bereit (Blender, CPU)' : 'nicht erreichbar' }));
        if (info.backup) sys.append(el('dt', { text: 'Backup' }), el('dd', { text: info.backup.healthy ? 'aktuell · ' + MAG.day(info.backup.last_success * 1000) : 'veraltet' }));
      }).catch(() => {});
      if (owner()) content.append(el('h3', { text: 'Raum' }), el('div', { class: 'btns' }, el('button', { class: 'btn danger', text: 'Raum und Bot-Kontext leeren', onclick: clearRoom })));
    });
  }

  // ---- look ---------------------------------------------------------------
  function applyLook() {
    const theme = localStorage.getItem('mag-theme') || 'auto';
    if (theme === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    document.documentElement.style.setProperty('--size', (localStorage.getItem('mag-size') || '16') + 'px');
    const dark = theme === 'dark' || (theme === 'auto' && !matchMedia('(prefers-color-scheme: light)').matches);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#17181b' : '#ffffff');
  }
  applyLook();

  // ---- push ---------------------------------------------------------------
  const b64 = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
  async function pushState() {
    try { return !!(await (await navigator.serviceWorker.ready).pushManager.getSubscription()); } catch { return false; }
  }
  async function setPush(on) {
    try {
      const reg = await navigator.serviceWorker.ready, current = await reg.pushManager.getSubscription();
      if (!on) {
        if (current) { await MAG.api('DELETE', '/api/push/subscribe', { endpoint: current.endpoint }); await current.unsubscribe(); }
        return;
      }
      if ((await Notification.requestPermission()) !== 'granted') return MAG.toast('Mitteilungen sind im Browser nicht erlaubt', true);
      const key = (await MAG.api('GET', '/api/push/vapid')).publicKey;
      const sub = current || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key) }));
      await MAG.api('POST', '/api/push/subscribe', sub.toJSON());
    } catch (err) {
      MAG.toast('Mitteilungen: ' + err.message, true);
    }
  }

  // ---- read aloud (device voice) ------------------------------------------
  if ('speechSynthesis' in window) {
    let reading = null; // message id being read
    MAG.speak = function (m, button) {
      const again = reading === m.id && speechSynthesis.speaking;
      speechSynthesis.cancel();
      document.querySelectorAll('.act.on').forEach((b) => b.classList.remove('on'));
      reading = null;
      if (again) return; // a second tap stops it
      const text = (m.text || '').replace(/```[\s\S]*?```/g, ' Codeblock. ').replace(/[#*_`>|]/g, ' ').slice(0, 6000);
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'de-DE';
      const voice = speechSynthesis.getVoices().filter((v) => v.lang.startsWith('de')).sort((a, b) => (b.localService ? 1 : 0) - (a.localService ? 1 : 0))[0];
      if (voice) u.voice = voice;
      u.onend = u.onerror = () => { if (reading === m.id) reading = null; button?.classList.remove('on'); };
      reading = m.id;
      button?.classList.add('on');
      speechSynthesis.speak(u);
    };
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('#clear').addEventListener('click', clearRoom);
    $('#settings').addEventListener('click', settingsSheet);
    // The keyboard shrinks the visual viewport, not the layout viewport.
    const fit = () => document.documentElement.style.setProperty('--vh', (window.visualViewport?.height || window.innerHeight) + 'px');
    window.visualViewport?.addEventListener('resize', fit);
    window.addEventListener('resize', fit);
    fit();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    MAG.connect();
  });
  MAG.on('bots', paintTop);
  MAG.on('settings', paintTop);
  MAG.on('online', (on) => { $('#offline').hidden = on; });
  // A bot that cannot answer right now: its chip in the header says why and
  // moves once. Nothing covers the chat.
  MAG.on('notice', (ev) => { if (ev.bot) nudged[ev.bot] = Date.now() + 1200; if (ev.text) MAG.toast(ev.text); paintTop(); });
})();

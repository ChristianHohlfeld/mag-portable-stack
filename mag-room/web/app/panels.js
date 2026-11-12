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
  const phases = { reading: 'liest', thinking: 'denkt nach', writing: 'schreibt', tool: 'arbeitet', approval: 'wartet auf dich', warming: 'wärmt auf', resetting: 'leert Kontext' };
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

  // A save's name is its time (UTC) and kind: 20261112T091500Z-manual.
  const saveKinds = { manual: 'von Hand', chat: 'auf Wunsch im Chat', daily: 'nächtlich', 'vor-wiederherstellung': 'Stand vor einer Wiederherstellung' };
  function saveDate(id) {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)Z/.exec(id || '');
    if (!m) return id || '';
    return new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
  }

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
        // A reset of the bot, step by step, and in words if it did not work.
        const reset = b.reset;
        if (reset?.state === 'running')
          add('Zurücksetzen', el('span', {}, el('span', { class: 'spin' }), ' ' + ({ stop: 'Antwort wird beendet …', memory: 'Gedächtnis wird geleert …', vm: 'VM ' + reset.vm + ' wird neu erstellt, das dauert einige Minuten …' }[reset.step] || 'läuft …')));
        if (reset?.state === 'failed') add('Zurücksetzen', 'nicht gelungen: ' + (reset.error || 'unbekannter Fehler'));
        // Saves of the VM: one under way, how the last ended, a restore step by step.
        const back = b.restore;
        if (b.saving) add('Sicherung', el('span', {}, el('span', { class: 'spin' }), ' VM wird gesichert, sie läuft dabei weiter …'));
        else if (b.save_note) add('Sicherung', b.save_note);
        if (back?.state === 'running') add('Wiederherstellen', el('span', {}, el('span', { class: 'spin' }), ' VM wird auf den Stand vom ' + saveDate(back.save) + ' gesetzt …'));
        if (back?.state === 'failed') add('Wiederherstellen', 'nicht gelungen: ' + (back.error || 'unbekannter Fehler'));
        if (sheet.saves && sheet.savesBusy !== (b.saving || back?.state === 'running')) sheet.saves();
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
          // Back to its first day: a new VM from the golden image, nothing remembered.
          if (reset?.state !== 'running' && back?.state !== 'running' && b.vm_note !== 'creating')
            acts.append(el('button', { class: 'btn danger', text: reset?.state === 'failed' ? 'Noch einmal zurücksetzen' : 'Zurücksetzen', onclick: async () => {
              if (await MAG.confirm(b.name + ' zurücksetzen?', 'Die VM wird gelöscht und neu erstellt: alle Dateien, Programme, Browser-Daten und die veröffentlichte Website sind weg, ebenso das Gedächtnis und der Gesprächskontext von ' + b.name + '. Eine laufende Antwort bricht sofort ab. Name, Adresse und Modell bleiben. Das lässt sich nicht rückgängig machen.', 'Zurücksetzen', true))
                call('POST', '/api/vm/' + id + '/reset').then(() => MAG.toast(b.name + ' wird zurückgesetzt'));
            } }));
          if (!b.vm && b.vm_note !== 'creating' && !reset)
            acts.append(el('button', { class: 'btn', text: 'VM erstellen', onclick: () => call('POST', '/api/bot/' + id + '/vm').then(() => MAG.toast('VM wird erstellt (einige Minuten)')) }));
        }
        actsTitle.hidden = acts.hidden = actsHint.hidden = !acts.children.length;
      };
      content.append(el('h3', { text: 'Status' }), status, actsTitle, acts, actsHint);
      if (owner() && b.vm) {
        // Saves of the VM: everything the bot has in it (files, programs,
        // browser data, its site), daily and whenever Chris asks; a restore
        // keeps the state it replaces as a save of its own.
        const list = el('div', { class: 'card' });
        content.append(el('h3', { text: 'Sicherungen der VM' }), list,
          el('p', { class: 'fine', text: 'Jede Nacht und auf Wunsch: alles, was ' + b.name + ' in der VM hat (Dateien, Programme, Browser-Daten, Website). Die VM läuft beim Sichern weiter. Wiederherstellen setzt die VM auf den Stand der Sicherung; der jetzige Stand bleibt vorher als Sicherung erhalten. Zurücksetzen lässt die Sicherungen stehen.' }));
        sheet.saves = () => {
          const now = MAG.bot(id);
          sheet.savesBusy = !!(now?.saving || now?.restore?.state === 'running');
          return MAG.api('GET', '/api/vm/' + id + '/saves').then((out) => {
            const busy = sheet.savesBusy;
            list.replaceChildren(el('div', { class: 'set' }, el('div', { class: 'txt' }, out.saves.length ? out.saves.length + (out.saves.length === 1 ? ' Sicherung' : ' Sicherungen') : el('small', { text: 'Noch keine Sicherung.' })),
              el('button', { class: 'btn', text: 'Jetzt sichern', disabled: busy, onclick: () => call('POST', '/api/vm/' + id + '/save').then(() => MAG.toast('VM von ' + b.name + ' wird gesichert')) })));
            for (const one of out.saves)
              list.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, saveDate(one.id), el('small', { text: (saveKinds[one.kind] || one.kind) + ' · ' + MAG.bytes(one.bytes) })),
                el('button', { class: 'btn', text: 'Wiederherstellen', disabled: busy, onclick: async () => {
                  if (await MAG.confirm('VM von ' + b.name + ' wiederherstellen?', 'Die VM wird angehalten und auf den Stand vom ' + saveDate(one.id) + ' gesetzt: Dateien, Programme, Browser-Daten und Website wie damals. Der jetzige Stand bleibt als Sicherung erhalten. Eine laufende Antwort bricht ab; das Gespräch und die Erinnerungen bleiben.', 'Wiederherstellen', true))
                    call('POST', '/api/vm/' + id + '/restore', { save: one.id }).then(() => MAG.toast(b.name + ': VM wird wiederhergestellt'));
                } })));
          }).catch(() => list.replaceChildren(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Sicherungen nicht lesbar.' })))));
        };
        sheet.saves();
      }
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

        // Plugins: which of the connected services this bot may use, and how freely.
        const plug = el('div', { class: 'card' });
        const plugTitle = el('h3', { text: 'Plugins', hidden: true });
        content.append(plugTitle, plug);
        const loadPlugins = () => MAG.api('GET', '/api/plugins').then((out) => {
          const mine = MAG.bot(id)?.plugins || {};
          const save = (next) => call('PUT', '/api/bot/' + id, { plugins: next }).then(loadPlugins, loadPlugins);
          plugTitle.hidden = plug.hidden = !out.plugins.length;
          plug.replaceChildren(...out.plugins.map((p) => {
            const b = mine[p.id], off = new Set(b?.off || []), always = new Set(b?.always || []);
            const change = (patch) => save({ ...mine, [p.id]: { approval: b.approval, off: [...off], always: [...always], ...patch } });
            const head = row(p.name, p.status === 'ok' ? p.tools.length + ' Werkzeuge' : 'nicht erreichbar: ' + (p.error || ''),
              toggle(!!b, (on) => { const next = { ...mine }; if (on) next[p.id] = { approval: 'ask' }; else delete next[p.id]; save(next); }));
            if (!b) return head;
            // With the owner's own account behind it, nothing is changed unasked, whatever is chosen here.
            const how = p.auth === 'connector' ? el('small', { text: 'Änderungen immer mit Nachfrage' })
              : select(b.approval, [['ask', 'Änderungen: nachfragen'], ['auto', 'Änderungen: ohne Nachfrage']], (v) => change({ approval: v }));
            // Every tool that is on stands at the head of the bot's prompt and is read once after each change.
            const cost = p.tools.reduce((sum, t) => sum + (off.has(t.name) ? 0 : t.tokens || 0), 0);
            const tools = el('details', { class: 'mem tools' },
              el('summary', {}, el('small', { text: 'Werkzeuge (' + (p.tools.length - off.size) + ' von ' + p.tools.length + ' an · etwa ' + (Math.round(cost / 100) * 100).toLocaleString('de-DE') + ' Tokens im Kontext)' })),
              p.tools.map((t) => el('label', { class: 'tool' },
                el('input', { type: 'checkbox', checked: !off.has(t.name), onchange: (e) => { if (e.target.checked) off.delete(t.name); else off.add(t.name); change({}); } }),
                el('span', {}, el('b', { text: t.title || t.name }), el('small', { text: (t.read_only ? 'liest nur' : always.has(t.name) ? 'ändert etwas · immer erlaubt' : 'ändert etwas') + (t.description ? ' · ' + t.description : '') })),
                always.has(t.name) ? el('button', { class: 'pill', text: 'Wieder nachfragen', onclick: (e) => { e.preventDefault(); always.delete(t.name); change({}); } }) : null)));
            return el('div', { class: 'plugin' }, head, el('div', { class: 'set' }, el('div', { class: 'txt' }, how)), tools);
          }));
        }).catch(() => { plugTitle.hidden = plug.hidden = true; });
        loadPlugins();

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

      // What the bots wrote down for each other: how they do recurring work.
      const skills = el('div', { class: 'card' }, el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'wird geladen …' }))));
      content.append(el('h3', { text: 'Skills der Bots' }), skills);
      const loadSkills = () => MAG.api('GET', '/api/skills').then((out) => {
        skills.replaceChildren();
        if (!out.skills.length) skills.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Noch keiner. Die Bots speichern hier, wie sie wiederkehrende Arbeit erledigen.' }))));
        for (const skill of out.skills)
          skills.append(el('details', { class: 'mem skill' }, el('summary', {}, el('b', { text: skill.name }), el('small', { text: ' · ' + (MAG.bot(skill.by)?.name || skill.by || '') })),
            el('p', {}, el('b', { text: 'Wann: ' }), skill.when), el('p', {}, el('b', { text: 'Schritte: ' }), skill.steps),
            el('p', {}, el('b', { text: 'Prüfen: ' }), skill.verify), el('p', {}, el('b', { text: 'Freigabe: ' }), skill.approval),
            owner() ? el('button', { class: 'pill danger', text: 'Löschen', onclick: () => call('DELETE', '/api/skills/' + encodeURIComponent(skill.name)).then(loadSkills) }) : null));
      }).catch(() => {});
      loadSkills();
      // Plugins: remote services (MCP servers) the bots may use. Which bot gets which is set in the bot's sheet.
      if (owner()) {
        const plugins = el('div', { class: 'card' }, el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'wird geladen …' }))));
        content.append(el('h3', { text: 'Plugins' }), plugins);
        const field = (label, props) => { const input = el(props.rows ? 'textarea' : 'input', { 'aria-label': label, placeholder: label, autocomplete: 'off', spellcheck: false, ...props }); return input; };
        const loadPlugins = () => MAG.api('GET', '/api/plugins').then((out) => {
          plugins.replaceChildren();
          if (!out.plugins.length) plugins.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Noch keines. Ein Plugin ist ein Dienst im Netz, der den Bots Werkzeuge gibt (MCP).' }))));
          for (const p of out.plugins) {
            const users = S.bots.filter((b) => b.plugins?.[p.id]).map((b) => b.name);
            const busy = (button, work) => { button.disabled = true; return work().then(loadPlugins, loadPlugins); };
            plugins.append(el('details', { class: 'mem skill' },
              el('summary', {}, el('b', { text: p.name }), el('small', { class: p.status === 'ok' ? '' : 'bad', text: ' · ' + (p.status === 'ok' ? p.tools.length + ' Werkzeuge' : 'nicht erreichbar') + (users.length ? ' · ' + users.join(', ') : ' · keinem Bot gegeben') })),
              el('p', {}, el('b', { text: 'Adresse: ' }), p.url),
              p.status === 'ok' ? null : el('p', { class: 'bad' }, el('b', { text: 'Fehler: ' }), p.error || 'unbekannt'),
              p.guide ? el('p', {}, el('b', { text: 'Hinweis für die Bots: ' }), p.guide) : null,
              el('p', {}, el('b', { text: 'Werkzeuge: ' }), p.tools.map((t) => t.name + (t.read_only ? '' : ' (ändert etwas)')).join(', ') || 'keine'),
              el('span', { class: 'pair' },
                el('button', { class: 'pill', text: 'Werkzeuge neu lesen', onclick: (e) => busy(e.currentTarget, () => call('POST', '/api/plugins/' + p.id + '/refresh')) }),
                el('button', { class: 'pill danger', text: 'Entfernen', onclick: async (e) => {
                  const button = e.currentTarget;
                  if (await MAG.confirm(p.name + ' entfernen?', 'Die Bots verlieren seine Werkzeuge. Ein gespeichertes Token wird gelöscht.', 'Entfernen', true)) busy(button, () => call('DELETE', '/api/plugins/' + p.id));
                } }))));
          }
          const name = field('Name', { type: 'text', maxLength: 60 }), url = field('Adresse (https://…/mcp)', { type: 'url', inputMode: 'url' });
          const secret = field('Token', { type: 'password', hidden: true }), header = field('Name des Headers', { type: 'text', hidden: true });
          const guide = field('Hinweis für die Bots (wofür das Plugin da ist)', { rows: 2, maxLength: 4000 });
          const auth = select('none', [['none', 'Ohne Anmeldung'], ['bearer', 'Mit Token (Bearer)'], ['header', 'Mit eigenem Header']], (v) => { secret.hidden = v === 'none'; header.hidden = v !== 'header'; });
          const connect = el('button', { class: 'btn', text: 'Verbinden', onclick: () => {
            connect.disabled = true;
            call('POST', '/api/plugins', { name: name.value, url: url.value, auth: auth.value, header: header.value, secret: secret.value, guide: guide.value })
              .then((out) => { MAG.toast(out.plugin.status === 'ok' ? out.plugin.name + ' verbunden: ' + out.plugin.tools.length + ' Werkzeuge' : out.plugin.name + ' gespeichert, aber nicht erreichbar', out.plugin.status !== 'ok'); loadPlugins(); },
                () => { connect.disabled = false; });
          } });
          plugins.append(el('details', { class: 'mem skill add' }, el('summary', {}, el('b', { text: 'Plugin verbinden' })), el('div', { class: 'form' }, name, url, auth, header, secret, guide, connect),
            el('p', {}, el('small', { text: 'Der Raum spricht den Dienst von diesem Rechner aus an. Ein Token bleibt auf dem Server und erreicht weder die Seite noch einen Bot. Welcher Bot das Plugin nutzen darf, stellst du bei ihm ein.' }))));
        }).catch(() => {});
        loadPlugins();
      }
      // Read aloud: a voice and a tempo for every bot, on this device.
      if (MAG.voices) {
        const voices = el('div', { class: 'card' });
        content.append(el('h3', { text: 'Vorlesen' }), voices);
        if (!MAG.voices.possible()) voices.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, 'Stimme des Geräts', el('small', { text: 'Die lokalen Stimmen sind auf diesem Server nicht eingerichtet.' }))));
        else MAG.voices.options().then((options) => {
          // Chris's own messages are read too, with a voice of his choice.
          for (const b of [{ id: 'chris', name: 'Chris' }, ...S.bots]) {
            const now = MAG.voices.of(b.id);
            voices.append(row(b.name, null, el('span', { class: 'pair' },
              select(now.voice, options.map((o) => [o.id, o.label]), (v) => MAG.voices.set(b.id, { voice: v })),
              select(String(now.rate), MAG.voices.rates.map((r) => [String(r), String(r).replace('.', ',') + '×']), (v) => MAG.voices.set(b.id, { rate: +v })))));
          }
          voices.append(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('span', { class: 'voice-state', text: MAG.voices.state() }),
            el('small', { text: 'Die Stimmen laufen in diesem Browser. Das Sprachmodell wird einmal im Hintergrund geladen (rund 200 MB) und bleibt dann auf dem Gerät.' }))));
        });
      }

      const sys = el('dl', { class: 'kv' }, el('dt', { text: 'Version' }), el('dd', { text: MAG.version }));
      content.append(el('h3', { text: 'System' }), el('div', { class: 'card' }, sys));
      MAG.api('GET', '/api/system').then((info) => {
        sys.append(el('dt', { text: 'Foto zu 3D' }), el('dd', { text: info.idol ? 'bereit (MI50)' : 'nicht erreichbar' }));
        sys.append(el('dt', { text: '3D-Szenen' }), el('dd', { text: info.scene ? 'bereit (Blender, CPU)' : 'nicht erreichbar' }));
        if (info.backup) sys.append(el('dt', { text: 'Backup' }), el('dd', { text: info.backup.healthy ? 'aktuell · ' + MAG.day(info.backup.last_success * 1000) : 'veraltet' }));
      }).catch(() => {});
      if (owner()) {
        // Everything at once, as a bot does it when asked in the chat: the
        // room, Gitea and configuration, and a save of every bot VM.
        const backup = el('div', { class: 'card' });
        content.append(el('h3', { text: 'Sicherung' }), backup, el('p', { class: 'fine', text: 'Nachts automatisch; hier oder im Chat („mach ein Backup“) jederzeit. Gesichert werden Chat, Dateien, Erinnerungen, Skills, Einstellungen, Gitea und Konfiguration (verschlüsselt, restic) und jede Bot-VM mit allem darin. Modelle werden nicht gesichert; sie werden neu geladen. Eine VM stellst du in ihrem Bot-Blatt wieder her.' }));
        const draw = () => MAG.api('GET', '/api/backup').then((b) => {
          const vms = b.bots.map((one) => one.name + ': ' + (one.saves.length ? one.saves.length + ' · zuletzt ' + saveDate(one.saves[0].id) : 'noch keine'));
          backup.replaceChildren(el('div', { class: 'set' }, el('div', { class: 'txt' },
            b.running ? el('span', {}, el('span', { class: 'spin' }), ' Sicherung läuft …') : 'Zuletzt: ' + (b.last_success ? MAG.day(b.last_success * 1000) : 'nie') + (b.healthy ? '' : ' (veraltet)'),
            el('small', { text: (b.last_error ? 'Letzter Fehler: ' + b.last_error + ' · ' : '') + 'VMs: ' + (vms.join(' · ') || 'keine') })),
            el('button', { class: 'btn', text: 'Jetzt alles sichern', disabled: b.running, onclick: () => call('POST', '/api/backup').then(() => { MAG.toast('Sicherung läuft'); setTimeout(draw, 1500); }) })));
          // The whole server, for a new machine: weekly, and here.
          const s = b.system || {}, where = { local: 'auf mags eigener Platte (schützt nicht vor einem Plattenschaden)', usb: 'auf der USB-Platte', configured: 'am eingerichteten Ziel' }[s.target];
          backup.append(el('div', { class: 'set' }, el('div', { class: 'txt' },
            s.running ? el('span', {}, el('span', { class: 'spin' }), ' Gesamtsystem wird gesichert …') : 'Gesamtsystem: ' + (s.last_success ? MAG.day(s.last_success * 1000) + (where ? ' · ' + where : '') : 'noch nie'),
            el('small', { text: s.healthy === false && s.last_error ? 'Fehler: ' + s.last_error : 'Betriebssystem, Dienste, jede VM, alle Daten; für neue Hardware. Wöchentlich, Sonntag früh.' })),
            el('button', { class: 'btn', text: 'Gesamtsystem sichern', disabled: !!s.running, onclick: () => call('POST', '/api/backup', { scope: 'system' }).then(() => { MAG.toast('Gesamtsicherung läuft'); setTimeout(draw, 1500); }) })));
          if (b.running || s.running) setTimeout(() => backup.isConnected && draw(), 15000);
        }).catch(() => backup.replaceChildren(el('div', { class: 'set' }, el('div', { class: 'txt' }, el('small', { text: 'Sicherung nicht lesbar.' })))));
        draw();
      }
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

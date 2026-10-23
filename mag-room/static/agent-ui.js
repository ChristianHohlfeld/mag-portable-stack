// NUMA model chips + swap sheet, bot->node assignment in the bot hub, and
// background task chips. Plain JS; uses the room's api(), magConfirm(),
// refresh() and state (shared global scope of the classic page scripts).
(function () {
  'use strict';
  let numa = null, tasks = [], sheet = null;
  const picks = {};
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const STATE_TEXT = {serving: 'bereit', loading: 'lädt', swapping: 'wechselt', rollback: 'Rückfall', error: 'Fehler', idle: 'leer'};

  function css() {
    if (document.getElementById('agent-ui-css')) return;
    const s = el('style'); s.id = 'agent-ui-css';
    s.textContent = `
#model-choice .numa-chip{display:inline-flex;align-items:center;gap:6px}
#model-choice .numa-ring{width:14px;height:14px;border-radius:50%;flex:none;background:conic-gradient(var(--accent) calc(var(--p,100)*1%),color-mix(in srgb,var(--muted),transparent 70%) 0)}
#model-choice .numa-ring.serving{background:#2fa36b}
#model-choice .numa-ring.error{background:#d33}
#model-choice .numa-ring.loading,#model-choice .numa-ring.swapping,#model-choice .numa-ring.rollback{animation:numa-pulse 1.2s ease-in-out infinite}
@keyframes numa-pulse{50%{opacity:.55}}
.numa-sheet{border:0;padding:0;border-radius:18px;max-width:min(460px,calc(100vw - 16px));width:100%;background:var(--surface,var(--panel));color:var(--text);box-shadow:0 20px 50px rgba(0,0,0,.3)}
.numa-sheet::backdrop{background:rgba(0,0,0,.35)}
@media(max-width:600px){.numa-sheet{margin:auto 0 0;max-width:100vw;border-radius:18px 18px 0 0}}
.numa-sheet .sheet-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 6px;font-weight:650}
.numa-sheet .sheet-body{padding:6px 16px 16px;display:flex;flex-direction:column;gap:10px}
.numa-node{border:1px solid var(--line);border-radius:14px;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.numa-node .row{display:flex;align-items:center;gap:8px;justify-content:space-between}
.numa-node select{flex:1;min-height:36px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 8px}
.numa-node button,.numa-sheet .close{min-height:36px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 12px;cursor:pointer}
.numa-node button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.numa-state{font-size:12px;color:var(--muted)}
.numa-state.error{color:#d33}
.numa-bots{display:flex;flex-direction:column;gap:6px}
.numa-bots .row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.seg button{min-height:30px;min-width:44px;border:0;background:transparent;color:var(--text);cursor:pointer}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff}
.agent .numa-pick{margin-top:4px}
#task-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
#task-chips:empty{display:none}
.task-chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;border:1px solid var(--line);border-radius:999px;padding:3px 4px 3px 10px;font-size:12px;background:var(--panel)}
.task-chip .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46vw}
.task-chip .spin{width:10px;height:10px;border-radius:50%;border:2px solid var(--accent);border-right-color:transparent;animation:task-spin .9s linear infinite}
@keyframes task-spin{to{transform:rotate(1turn)}}
.task-chip button{border:0;background:transparent;color:var(--muted);cursor:pointer;width:24px;height:24px;border-radius:50%}
.routine-dl{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;margin:8px 0;font-size:13px}
.routine-dl dt{color:var(--muted)}.routine-dl dd{margin:0;white-space:pre-wrap;word-break:break-word}
.secret-form{display:flex;gap:8px;margin-top:8px}
.secret-form input{flex:1;min-height:36px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 10px}
.mcp-approve.decided{opacity:.6}
.secret-input{min-height:38px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 10px}
.secret-bots{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
.task-chip button:hover{background:var(--panel-3,var(--hover))}
`;
    document.head.append(s);
  }

  function modelOf(id) { return (numa?.catalog || []).find(m => m.id === id); }

  function paintChips() {
    const box = document.getElementById('model-choice');
    if (!box || !numa?.enabled) return;
    box.replaceChildren();
    box.title = 'Modelle pro NUMA-Knoten';
    for (const n of numa.nodes || []) {
      const b = el('button', 'model-button numa-chip');
      b.type = 'button';
      b.dataset.node = n.node;
      const ring = el('span', 'numa-ring ' + n.state);
      ring.style.setProperty('--p', n.state === 'serving' ? 100 : Math.max(3, n.progress || 0));
      const label = n.state === 'serving' || n.state === 'error' ? n.node + ' · ' + n.short
        : n.node + ' · ' + Math.round(n.progress || 0) + '%';
      b.append(ring, el('span', '', label));
      b.setAttribute('aria-label', 'NUMA ' + n.node + ': ' + n.label + ', ' + (STATE_TEXT[n.state] || n.state));
      b.setAttribute('aria-pressed', 'false');
      b.onclick = () => openSheet();
      box.append(b);
    }
  }

  async function swap(node, id) {
    const cur = (numa.nodes || []).find(n => n.node === node);
    const next = modelOf(id);
    if (!cur || !next || cur.model === id) return;
    const ok = await magConfirm({
      title: 'Modell wechseln',
      text: 'Ersetze ' + cur.label + ' auf NUMA ' + node + ' durch ' + next.label + '? Das Modell wird aus dem RAM entladen.',
      ok: 'Ersetzen', cancel: 'Abbrechen', danger: true,
    });
    if (!ok) return false;
    try {
      const r = await api('/api/numa/swap', {node, model: id, confirm: true});
      numa = r; render();
      return true;
    } catch (e) { (window.notice || console.warn)(e.message); return false; }
  }

  async function assign(bot, node) {
    try { numa = await api('/api/agents/' + encodeURIComponent(bot) + '/numa', {node}, 'PUT'); await refresh(); }
    catch (e) { (window.notice || console.warn)(e.message); }
  }

  function seg(bot, node) {
    const s = el('div', 'seg'); s.setAttribute('role', 'group'); s.setAttribute('aria-label', 'NUMA-Knoten');
    for (const n of [0, 1]) {
      const b = el('button', '', 'N' + n); b.type = 'button';
      const m = (numa.nodes || [])[n];
      b.title = 'NUMA ' + n + (m ? ' · ' + m.label : '');
      b.setAttribute('aria-pressed', String(node === n));
      b.onclick = ev => { ev.stopPropagation(); if (node !== n) assign(bot, n); };
      s.append(b);
    }
    return s;
  }

  function fillSheet() {
    if (!sheet) return;
    const body = sheet.querySelector('.sheet-body');
    body.replaceChildren();
    const owner = typeof state !== 'undefined' && state?.you?.role === 'owner';
    for (const n of numa.nodes || []) {
      const card = el('div', 'numa-node');
      const head = el('div', 'row');
      head.append(el('strong', '', 'NUMA ' + n.node + ' · ' + n.label));
      const st = el('span', 'numa-state' + (n.state === 'error' ? ' error' : ''),
        (STATE_TEXT[n.state] || n.state) + (n.state !== 'serving' && n.state !== 'error' ? ' ' + Math.round(n.progress || 0) + '%' : ''));
      head.append(st);
      card.append(head);
      if (n.error) card.append(el('div', 'numa-state error', n.error));
      const row = el('div', 'row');
      const sel = el('select'); sel.setAttribute('aria-label', 'Modell für NUMA ' + n.node);
      for (const m of numa.catalog || []) {
        const o = el('option', '', m.label + (m.fits?.[n.node] ? '' : ' – passt nicht'));
        o.value = m.id; o.disabled = !m.fits?.[n.node] && m.id !== n.model; sel.append(o);
      }
      // Keep the user's pending choice across live re-renders.
      sel.value = picks[n.node] && [...sel.options].some(o => o.value === picks[n.node] && !o.disabled) ? picks[n.node] : n.model;
      const go = el('button', 'primary', 'Wechseln'); go.type = 'button';
      const busy = numa.swapping >= 0;
      go.disabled = sel.disabled = busy || !owner;
      if (!owner) go.title = 'Nur der Besitzer';
      sel.onchange = () => { picks[n.node] = sel.value; go.disabled = busy || !owner || sel.value === n.model; };
      go.disabled = go.disabled || sel.value === n.model;
      go.onclick = async () => { const ok = await swap(n.node, sel.value); delete picks[n.node]; fillSheet(); return ok; };
      row.append(sel, go);
      card.append(row);
      body.append(card);
    }
    const bots = el('div', 'numa-bots');
    bots.append(el('strong', '', 'Welcher Bot spricht mit welchem Knoten'));
    for (const a of (typeof state !== 'undefined' && state?.agents) || []) {
      const r = el('div', 'row');
      r.append(el('span', '', a.name + (a.swapping ? ' · wartet' : '')), seg(a.id, a.numa));
      bots.append(r);
    }
    body.append(bots);
    body.append(el('div', 'numa-state', 'Höchstens zwei Modelle gleichzeitig: eines pro Knoten. Gewechselt wird nur nach Bestätigung.'));
  }

  function openSheet() {
    if (!numa?.enabled) return;
    if (!sheet) {
      sheet = el('dialog', 'numa-sheet'); sheet.id = 'numa-sheet';
      const head = el('div', 'sheet-head');
      head.append(el('span', '', 'Modelle'));
      const close = el('button', 'close', '✕'); close.type = 'button'; close.setAttribute('aria-label', 'Schließen');
      close.onclick = () => sheet.close();
      head.append(close);
      sheet.append(head, el('div', 'sheet-body'));
      sheet.addEventListener('click', ev => { if (ev.target === sheet) sheet.close(); });
      sheet.addEventListener('close', () => { for (const k in picks) delete picks[k]; });
      document.body.append(sheet);
    }
    fillSheet();
    if (!sheet.open) sheet.showModal();
  }

  function paintHub() {
    if (!numa?.enabled) return;
    for (const card of document.querySelectorAll('#agents .agent[data-bot]')) {
      const a = (state?.agents || []).find(x => x.id === card.dataset.bot);
      if (!a) continue;
      card.querySelector('.numa-pick')?.remove();
      const wrap = el('div', 'numa-pick'); wrap.append(seg(a.id, a.numa));
      if (a.swapping) wrap.append(el('small', 'numa-state', ' Modell lädt – Nachrichten warten'));
      card.append(wrap);
    }
  }

  function paintTasks() {
    let box = document.getElementById('task-chips');
    if (!box) {
      const lanes = document.getElementById('bot-lanes');
      if (!lanes) return;
      box = el('div'); box.id = 'task-chips'; box.setAttribute('aria-label', 'Hintergrundaufgaben');
      lanes.before(box);
    }
    box.replaceChildren();
    for (const t of tasks.filter(t => t.status === 'running')) {
      const bot = ((typeof state !== 'undefined' && state?.agents) || []).find(a => a.id === t.bot);
      const c = el('span', 'task-chip');
      const todo = t.todo?.items || [];
      const done = todo.filter(i => i.status !== 'open').length;
      c.append(el('span', 'spin'), el('span', 't', (bot ? bot.name + ' · ' : '') + t.title + (todo.length ? ' ' + done + '/' + todo.length : '')));
      const x = el('button', '', '✕'); x.type = 'button'; x.setAttribute('aria-label', 'Aufgabe abbrechen');
      x.onclick = () => api('/api/tasks/' + encodeURIComponent(t.id) + '/cancel', {}).catch(() => {});
      c.append(x);
      box.append(c);
    }
  }

  // Secrets in the bot hub: names, purpose, last use, which bot may use it.
  let secrets = [], secretsAt = 0;
  async function loadSecrets(force) {
    if (typeof state === 'undefined' || state?.you?.role !== 'owner') return;
    if (!force && Date.now() - secretsAt < 15000) return;
    secretsAt = Date.now();
    try { secrets = (await api('/api/secrets', undefined, 'GET')).secrets || []; paintSecrets(); } catch {}
  }
  function askSecret(name) {
    return new Promise(resolve => {
      const d = el('dialog', 'numa-sheet');
      const f = el('form', 'sheet-body'); f.method = 'dialog'; f.autocomplete = 'off';
      f.append(el('strong', '', name ? 'Secret ' + name + ' ersetzen' : 'Neues Secret'));
      const n = el('input'); n.placeholder = 'NAME (A-Z, 0-9, _)'; n.value = name || ''; n.disabled = !!name; n.setAttribute('aria-label', 'Name');
      const v = el('input'); v.type = 'password'; v.autocomplete = 'new-password'; v.placeholder = 'Wert'; v.setAttribute('aria-label', 'Wert');
      const p = el('input'); p.placeholder = 'Zweck (optional)'; p.setAttribute('aria-label', 'Zweck');
      for (const i of [n, v, p]) { i.className = 'secret-input'; f.append(i); }
      const row = el('div', 'mcp-approve-row');
      const c = el('button', 'pill-btn ghost', 'Abbrechen'); c.type = 'button'; c.onclick = () => { d.close(); };
      const ok = el('button', 'pill-btn primary', 'Speichern'); ok.type = 'submit';
      row.append(c, ok); f.append(row); d.append(f); document.body.append(d);
      let result = null;
      f.onsubmit = ev => { ev.preventDefault(); if (n.value && v.value) { result = {name: n.value.trim(), value: v.value, purpose: p.value}; d.close(); } };
      d.addEventListener('close', () => { v.value = ''; d.remove(); resolve(result); });
      d.showModal(); (name ? v : n).focus();
    });
  }
  async function editSecret(name) {
    const r = await askSecret(name);
    if (!r) return;
    try { await api('/api/secrets/' + encodeURIComponent(r.name), {value: r.value, purpose: r.purpose}, 'PUT'); }
    catch (e) { (window.notice || console.warn)(e.message); }
    loadSecrets(true);
  }
  function paintSecrets() {
    const owner = typeof state !== 'undefined' && state?.you?.role === 'owner';
    let sec = document.getElementById('secrets-section');
    if (!owner) { sec?.remove(); return; }
    if (!sec) {
      const anchor = document.getElementById('jobs-section');
      if (!anchor) return;
      sec = el('div', 'section'); sec.id = 'secrets-section';
      const head = el('div', 'side-section-head');
      head.append(el('h2', '', 'Secrets'));
      const add = el('button', 'pill-btn ghost', '+ Neu'); add.type = 'button'; add.onclick = () => editSecret('');
      head.append(add);
      sec.append(head, el('div', 'secrets-list'));
      anchor.after(sec);
    }
    const list = sec.querySelector('.secrets-list');
    list.replaceChildren();
    if (!secrets.length) { list.append(el('p', 'control-desc', 'Keine Secrets. Bots fragen bei Bedarf über eine maskierte Karte.')); return; }
    for (const x of secrets) {
      const row = el('div', 'job secret-row');
      row.append(el('div', 'job-name', x.name));
      const used = x.last_used ? new Date(x.last_used * 1000).toLocaleString() : 'nie';
      row.append(el('div', 'job-meta', (x.purpose ? x.purpose + ' · ' : '') + 'zuletzt benutzt: ' + used));
      const access = el('div', 'secret-bots');
      for (const a of state.agents || []) {
        const on = (x.bots || []).includes(a.id);
        const b = el('button', 'pill-btn ' + (on ? 'primary' : 'ghost'), a.name); b.type = 'button';
        b.setAttribute('aria-pressed', String(on)); b.title = on ? 'Zugriff entziehen' : 'Zugriff erlauben';
        b.onclick = async () => {
          const bots = on ? x.bots.filter(id => id !== a.id) : [...(x.bots || []), a.id];
          try { await api('/api/secrets/' + encodeURIComponent(x.name) + '/bots', {bots}, 'PUT'); } catch (e) { (window.notice || console.warn)(e.message); }
          loadSecrets(true);
        };
        access.append(b);
      }
      row.append(access);
      const act = el('div', 'job-actions');
      const rep = el('button', '', 'Ersetzen'); rep.type = 'button'; rep.onclick = () => editSecret(x.name);
      const del = el('button', 'danger', 'Löschen'); del.type = 'button';
      del.onclick = async () => {
        if (!await magConfirm({title: 'Secret löschen', text: x.name + ' endgültig löschen? Bots können es danach nicht mehr verwenden.', ok: 'Löschen', danger: true})) return;
        try { await api('/api/secrets/' + encodeURIComponent(x.name), undefined, 'DELETE'); } catch (e) { (window.notice || console.warn)(e.message); }
        loadSecrets(true);
      };
      act.append(rep, del); row.append(act);
      list.append(row);
    }
  }

  // Read-aloud voice choice next to the language setting.
  async function voiceSelect() {
    const lang = document.getElementById('language');
    if (!lang || document.getElementById('tts-voice') || !window.magMessageAudio?.voiceOptions) return;
    const sel = el('select', 'side-select'); sel.id = 'tts-voice'; sel.setAttribute('aria-label', 'Vorlesestimme');
    sel.style.marginTop = '8px';
    lang.after(sel);
    for (const v of await window.magMessageAudio.voiceOptions()) { const o = el('option', '', 'Vorlesen: ' + v.label); o.value = v.id; sel.append(o); }
    sel.value = window.magMessageAudio.voicePref();
    if (sel.selectedIndex < 0) sel.value = 'system';
    sel.onchange = () => window.magMessageAudio.setVoice(sel.value);
  }

  function render() { css(); voiceSelect(); paintChips(); paintHub(); paintTasks(); loadSecrets(false); if (sheet?.open) fillSheet(); }

  function schedText(sc) {
    if (!sc || typeof sc !== 'object') return 'ohne Zeitplan';
    const k = sc.kind || sc.type;
    if (k === 'every_minutes' || k === 'every_hours' || k === 'every_days' || k === 'every_weeks') return 'alle ' + (sc.every || sc.n || 1) + ' ' + {every_minutes: 'Min.', every_hours: 'Std.', every_days: 'Tage', every_weeks: 'Wochen'}[k];
    if (k === 'weekdays') return 'werktags ' + (sc.time || '');
    if (k === 'weekly') return 'wöchentlich ' + (sc.time || '');
    if (k === 'interval') return 'alle ' + (sc.every || sc.minutes || '?') + ' ' + (sc.unit || 'min');
    if (k === 'daily') return 'täglich ' + (sc.time || '');
    if (k === 'monthly') return 'monatlich am ' + (sc.mday || sc.day || '?') + '. ' + (sc.time || '');
    if (k === 'once') return 'einmal ' + (sc.at ? new Date(sc.at * 1000).toLocaleString() : '');
    if (k === 'webhook') return 'bei Webhook-Ereignis';
    return JSON.stringify(sc);
  }
  async function decide(id, decision, card) {
    card.querySelectorAll('button,input').forEach(x => x.disabled = true);
    try { await api('/api/plugins/approvals/' + encodeURIComponent(id), {decision}); card.classList.add('decided'); }
    catch (e) { (window.notice || console.warn)(e.message); card.querySelectorAll('button,input').forEach(x => x.disabled = false); }
  }
  // Confirmation card for a routine a bot proposes; masked card for a secret.
  function approvalCard(a) {
    let info = {};
    try { info = JSON.parse(a.args || '{}'); } catch {}
    const owner = typeof state !== 'undefined' && state?.you?.role === 'owner';
    const card = el('div', 'mcp-approve ' + (a.kind === 'secret' ? 'secret-card' : 'routine-card'));
    card.dataset.id = a.id;
    card.setAttribute('role', 'group');
    const head = el('div', 'mcp-approve-head');
    const row = el('div', 'mcp-approve-row');
    if (a.kind === 'routine') {
      const verb = {create: 'schlägt eine Routine vor', update: 'möchte eine Routine ändern', resume: 'möchte eine Routine fortsetzen'}[info.action] || 'schlägt eine Routine vor';
      head.append(el('span', '', (a.bot_name || 'Bot') + ' ' + verb));
      card.setAttribute('aria-label', 'Routine bestätigen');
      card.append(head);
      const dl = el('dl', 'routine-dl');
      for (const [k, v] of [['Name', info.name || '–'], ['Zeitplan', schedText(info.schedule)], ['Prompt', info.prompt || '–']]) {
        dl.append(el('dt', '', k), el('dd', '', v));
      }
      card.append(dl);
      if (!owner) row.append(el('span', 'hint', 'Wartet auf Bestätigung durch den Besitzer …'));
      else {
        const no = el('button', 'pill-btn ghost', 'Ablehnen'); no.type = 'button'; no.dataset.decision = 'deny';
        const yes = el('button', 'pill-btn primary', 'Speichern'); yes.type = 'button'; yes.dataset.decision = 'allow';
        no.onclick = () => decide(a.id, 'deny', card);
        yes.onclick = () => decide(a.id, 'allow', card);
        row.append(no, yes);
      }
      card.append(row);
      return card;
    }
    if (a.kind === 'secret') {
      head.append(el('span', '', (a.bot_name || 'Bot') + ' braucht ' + (info.label || info.name || 'ein Secret')));
      card.setAttribute('aria-label', 'Secret eingeben');
      card.append(head);
      if (info.purpose) card.append(el('div', 'hint', info.purpose));
      const form = el('form', 'secret-form');
      form.autocomplete = 'off';
      const input = el('input');
      input.type = 'password'; input.name = 'secret-value'; input.autocomplete = 'new-password';
      input.placeholder = info.name || 'Wert'; input.setAttribute('aria-label', 'Secret ' + (info.name || ''));
      input.spellcheck = false;
      form.append(input);
      card.append(form);
      if (!owner) row.append(el('span', 'hint', 'Nur der Besitzer kann Secrets eingeben.'));
      else {
        const no = el('button', 'pill-btn ghost', 'Ablehnen'); no.type = 'button';
        const yes = el('button', 'pill-btn primary', 'Sicher speichern'); yes.type = 'submit';
        no.onclick = () => decide(a.id, 'deny', card);
        form.onsubmit = async ev => {
          ev.preventDefault();
          if (!input.value) return;
          const value = input.value; input.value = '';
          card.querySelectorAll('button,input').forEach(x => x.disabled = true);
          try { await api('/api/secrets/requests/' + encodeURIComponent(a.id), {value}); card.classList.add('decided'); }
          catch (e) { (window.notice || console.warn)(e.message); card.querySelectorAll('button,input').forEach(x => x.disabled = false); }
        };
        form.append(yes);
        row.append(no);
      }
      card.append(row);
      card.append(el('div', 'hint', 'Der Wert geht verschlüsselt in den Secret-Speicher – nie in den Chat oder zum Modell.'));
      return card;
    }
    return null;
  }

  window.magAgentUI = {
    approvalCard,
    sync(next) { if (!next) return; if (next.numa) numa = next.numa; if (next.tasks) tasks = next.tasks; render(); },
    event(ev) {
      if (ev.type === 'numa') { numa = ev.numa; if (typeof state !== 'undefined' && state) state.numa = numa; render(); return true; }
      if (ev.type === 'tasks') { tasks = ev.tasks || []; render(); return true; }
      if (ev.type === 'todo') {
        const t = tasks.find(x => ev.key === x.bot + '#' + x.id);
        if (t) { t.todo = ev.todo; paintTasks(); }
        return true;
      }
      return false;
    },
    openSheet,
  };
})();

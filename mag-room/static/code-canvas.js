// Live code canvas for code a bot writes through tools (write_file content,
// run_command heredocs). The text never appears in the chat answer, so the
// room streams it as "code_draft" events and this module shows it inside the
// message: syntax highlighting, line numbers, lines appearing as they are
// generated, then - once the tool succeeded and the code parses - the
// rendered website in a sandboxed frame with a Code/Vorschau switch.
// Uses the page's htmlPreviewable/htmlPreviewDoc/copyText when present.
(function () {
  'use strict';
  const root = typeof window !== 'undefined' ? window : globalThis;
  const EXT = {html: 'html', htm: 'html', xhtml: 'html', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    jsx: 'javascript', ts: 'typescript', tsx: 'typescript', css: 'css', scss: 'scss', py: 'python', sh: 'bash',
    bash: 'bash', json: 'json', md: 'markdown', c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', rs: 'rust',
    go: 'go', java: 'java', rb: 'ruby', php: 'php', sql: 'sql', yml: 'yaml', yaml: 'yaml', toml: 'ini',
    xml: 'xml', svg: 'xml', vue: 'xml', glsl: 'glsl', frag: 'glsl', vert: 'glsl'};
  const INTERP = {python: 'python', python3: 'python', node: 'javascript', bash: 'bash', sh: 'bash', php: 'php',
    ruby: 'ruby', perl: 'perl'};

  function langFor(path, text, hint) {
    const ext = (String(path || '').toLowerCase().match(/\.([a-z0-9]+)$/) || [])[1];
    if (ext && EXT[ext]) return EXT[ext];
    if (/^\s*(?:<!--[\s\S]*?-->\s*)*(?:<!doctype\s+html|<html[\s>])/i.test(text || '')) return 'html';
    if (hint) return hint;
    const bang = (String(text || '').match(/^#!\S*?(?:env\s+)?(\w+)/) || [])[1];
    return (bang && INTERP[bang]) || '';
  }

  // The last heredoc of a shell command, possibly still unterminated while
  // the command streams: {path, lang, text, done} or null.
  function heredoc(cmd) {
    cmd = String(cmd || '');
    const re = /<<(-?)[ \t]*(['"]?)([A-Za-z_][\w.-]*)\2[^\n]*\n/g;
    let m, last = null;
    while ((m = re.exec(cmd))) {
      const start = m.index + m[0].length, delim = m[3], strip = m[1] === '-';
      const lines = cmd.slice(start).split('\n');
      const body = [];
      let done = false, used = 0;
      for (const raw of lines) {
        const line = strip ? raw.replace(/^\t+/, '') : raw;
        used += raw.length + 1;
        if (line === delim) { done = true; break; }
        body.push(raw);
      }
      if (!done && body.length && body[body.length - 1] === '') body.pop();
      const ls = cmd.lastIndexOf('\n', m.index) + 1;
      const head = cmd.slice(ls, start - 1);
      let path = '';
      for (const t of head.matchAll(/(?:\d?>>?|\btee(?:\s+-a)?)\s*(['"]?)([^\s'";|&<>()]+)\1/g))
        if (!/^\/dev\//.test(t[2]) && !/^&/.test(t[2])) path = t[2];
      const interp = (head.match(/\b(python3?|node|bash|sh|php|ruby|perl)\b/) || [])[1];
      const text = body.join('\n') + (body.length ? '\n' : '');
      last = {path, lang: langFor(path, text, path ? '' : INTERP[interp] || ''), text, done};
      re.lastIndex = Math.min(cmd.length, start + used);
    }
    return last && last.text ? last : null;
  }

  function bytes(s) { try { return new TextEncoder().encode(s || '').length; } catch (e) { return (s || '').length; } }

  // Code of a tool call: {key, path, lang, text|null, ref, state}.
  function toolCode(t, i) {
    const name = t && (t.name || t.tool), a = t && t.args && typeof t.args === 'object' ? t.args : {};
    const file = name === 'write_file' || name === 'append_file';
    if (!file && name !== 'run_command') return null;
    const state = t.result == null ? 'run' : t.ok === false ? 'fail' : 'ok';
    const src = file ? a.content : a.command;
    if (typeof src !== 'string') {
      if (!t.code_ref) return null;
      return {key: name + '|' + (a.path || '') + '|' + (t.code_bytes || 0), tool: name, path: a.path || '',
        lang: langFor(a.path, ''), text: null, ref: t.code_ref, file, state, index: i};
    }
    const key = name + '|' + (a.path || '') + '|' + bytes(src);
    if (file) return {key, tool: name, path: a.path || '', lang: langFor(a.path, src), text: src, file, state, index: i};
    const h = heredoc(src);
    return h ? {key, tool: name, path: h.path, lang: h.lang, text: h.text, file: !!h.path, state, index: i} : null;
  }

  // Text loaded by content hash; also seeded from live tool calls so a
  // finished message does not refetch what the page just streamed.
  const textByKey = new Map(), fetches = new Map();
  function loadRef(ref) {
    if (!fetches.has(ref)) fetches.set(ref, fetch('/api/code/' + encodeURIComponent(ref), {credentials: 'same-origin'})
      .then(r => r.ok ? r.text() : Promise.reject(Error('HTTP ' + r.status))));
    return fetches.get(ref);
  }

  function previewable(c) {
    if (!c || c.text == null || !c.text.trim()) return false;
    if (typeof root.htmlPreviewable === 'function') return root.htmlPreviewable(c.lang || '', c.text);
    return c.lang === 'html';
  }

  // "Kompilierbar": classic inline scripts and plain JS must parse. Module
  // scripts cannot be checked without loading their imports; they pass.
  function syntaxError(c) {
    const check = src => { try { new Function(src); return ''; } catch (e) { return e instanceof SyntaxError ? e.message : ''; } };
    if (c.lang === 'javascript') return /^\s*(?:import|export)\b/m.test(c.text) ? '' : check(c.text);
    if (c.lang !== 'html' || typeof DOMParser !== 'function') return '';
    const doc = new DOMParser().parseFromString(c.text, 'text/html');
    for (const s of doc.querySelectorAll('script:not([src])')) {
      const type = (s.getAttribute('type') || '').trim().toLowerCase();
      if (type && type !== 'text/javascript' && type !== 'application/javascript') continue;
      const err = check(s.textContent || '');
      if (err) return err;
    }
    return '';
  }

  function pick(tools, live) {
    if (live && live.text) return live;
    const list = (tools || []).map(toolCode).filter(Boolean);
    if (!list.length) return null;
    for (let i = list.length - 1; i >= 0; i--) if (list[i].state !== 'fail' && previewable(list[i])) return list[i];
    for (let i = list.length - 1; i >= 0; i--) if (list[i].file) return list[i];
    return list[list.length - 1];
  }

  const modeByKey = new Map();
  const seen = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver(es => { for (const e of es) if (e.isIntersecting) { seen.unobserve(e.target); e.target._mountFrame && e.target._mountFrame(); } }, {rootMargin: '200px'})
    : null;

  function button(text, title, fn) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'copy'; b.textContent = text; b.title = title; b.onclick = fn;
    return b;
  }

  function build() {
    const box = document.createElement('div');
    box.className = 'code-canvas';
    box.innerHTML = '<div class="cc-bar"><span class="cc-title"></span><span class="cc-meta"></span>' +
      '<span class="cc-tabs" role="tablist"></span><span class="cc-tools"></span></div>' +
      '<div class="cc-body"><div class="cc-scroll"><pre class="cc-gutter" aria-hidden="true"></pre>' +
      '<pre class="cc-pre"><code class="hljs"></code><span class="cc-caret" aria-hidden="true"></span></pre></div>' +
      '<div class="cc-note" hidden></div></div>';
    const scroll = box.querySelector('.cc-scroll');
    box._follow = true;
    scroll.addEventListener('scroll', () => { box._follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 24; });
    box._tabCode = button('Code', 'Quellcode', () => setMode(box, 'code', true));
    box._tabView = button('Vorschau', 'Gerenderte Website', () => setMode(box, 'preview', true));
    for (const t of [box._tabCode, box._tabView]) { t.classList.add('code-tab'); t.setAttribute('role', 'tab'); }
    box.querySelector('.cc-tabs').append(box._tabCode, box._tabView);
    const copy = button('Kopieren', 'Code kopieren', () => {
      if (typeof root.copyText === 'function') root.copyText(box._text || '', copy);
      else navigator.clipboard && navigator.clipboard.writeText(box._text || '');
    });
    const reload = button('Neu laden', 'Vorschau neu laden', () => { box._mounted = false; mountFrame(box); });
    const full = button('Vollbild', 'Im Vollbild anzeigen', () => {
      const on = !box.classList.contains('max');
      box.classList.toggle('max', on); full.textContent = on ? 'Schließen' : 'Vollbild';
      document.documentElement.classList.toggle('code-preview-open', on);
    });
    box._reload = reload;
    box.addEventListener('keydown', e => { if (e.key === 'Escape' && box.classList.contains('max')) full.click(); });
    box.querySelector('.cc-tools').append(copy, reload, full);
    box._mountFrame = () => mountFrame(box);
    return box;
  }

  function mountFrame(box) {
    if (box._mounted || box._mode !== 'preview' || box._text == null) return;
    box._mounted = true;
    let frame = box.querySelector('.cc-frame');
    if (!frame) {
      frame = document.createElement('iframe');
      frame.className = 'cc-frame'; frame.title = 'Isolierte Website-Vorschau';
      frame.setAttribute('sandbox', 'allow-scripts'); frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.setAttribute('allow', 'fullscreen');
      box.querySelector('.cc-body').append(frame);
    }
    frame.srcdoc = typeof root.htmlPreviewDoc === 'function' ? root.htmlPreviewDoc(box._text, box._lang) : box._text;
  }

  function setMode(box, mode, manual) {
    if (mode === 'preview' && !box._canPreview) mode = 'code';
    if (manual) modeByKey.set(box._key, mode);
    box._mode = mode;
    box.classList.toggle('mode-preview', mode === 'preview');
    box._tabCode.setAttribute('aria-selected', String(mode === 'code'));
    box._tabView.setAttribute('aria-selected', String(mode === 'preview'));
    const frame = box.querySelector('.cc-frame');
    if (frame) frame.hidden = mode !== 'preview';
    if (mode === 'preview') { if (seen && !box.classList.contains('max')) seen.observe(box); else mountFrame(box); }
  }

  function paintText(box, text, live) {
    const code = box.querySelector('.cc-pre code'), gutter = box.querySelector('.cc-gutter');
    clearTimeout(box._hlTimer);
    if (box._text !== text || box._hlLang !== box._lang) {
      const now = Date.now();
      // Re-highlight at most ~8x/s while streaming; the trailing call paints the rest.
      if (live && box._hlAt && now - box._hlAt < 120) {
        box._hlTimer = setTimeout(() => { if (box._state === 'live') paintText(box, text, true); }, 130);
        return;
      }
      box._hlAt = now; box._text = text; box._hlLang = box._lang;
      let html = null;
      try {
        const hl = root.hljs;
        if (hl && box._lang && hl.getLanguage(box._lang) && text.length < 1000000)
          html = hl.highlight(text, {language: box._lang, ignoreIllegals: true}).value;
        else if (hl && text.length < 100000) html = hl.highlightAuto(text).value;
      } catch (e) { html = null; }
      if (html == null) code.textContent = text; else code.innerHTML = html;
      code.className = 'hljs' + (box._lang ? ' language-' + box._lang : '');
    }
    const n = text ? text.replace(/\n$/, '').split('\n').length : 0;
    if (gutter._n !== n) {
      let s = '';
      for (let i = 1; i <= n; i++) s += i + '\n';
      gutter.textContent = s; gutter._n = n;
    }
    const meta = box.querySelector('.cc-meta');
    const what = live ? 'schreibt …' : box._state === 'run' ? 'wird ausgeführt …' : box._state === 'fail' ? 'Fehler' : '';
    meta.textContent = n.toLocaleString('de-DE') + (n === 1 ? ' Zeile' : ' Zeilen') + (what ? ' · ' + what : '');
    const scroll = box.querySelector('.cc-scroll');
    if (live && box._follow) scroll.scrollTop = scroll.scrollHeight;
  }

  function apply(box, c, live) {
    box._key = c.key || ('live|' + (c.path || ''));
    box._lang = c.lang || '';
    box._state = live ? 'live' : c.state;
    box.dataset.state = box._state;
    box.querySelector('.cc-title').textContent = (c.path ? c.path.replace(/^\/home\/bot\//, '~/') : (c.lang || 'code')) +
      (c.path && c.lang ? ' · ' + c.lang : '');
    const text = c.text;
    if (text == null) {
      box.querySelector('.cc-meta').textContent = 'lädt …';
      if (c.ref && box._ref !== c.ref) {
        box._ref = c.ref;
        loadRef(c.ref).then(full => {
          if (!box.isConnected && !box._parked) return;
          let resolved = full;
          if (c.tool === 'run_command') { const h = heredoc(full); resolved = h ? h.text : full; if (h) { c.path = c.path || h.path; c.lang = h.lang; } }
          else c.lang = langFor(c.path, full);
          textByKey.set(c.key, {text: resolved, path: c.path, lang: c.lang});
          apply(box, {...c, text: resolved}, false);
        }, () => { box.querySelector('.cc-meta').textContent = 'Code nicht mehr verfügbar'; });
      }
      return;
    }
    if (!live && c.key) textByKey.set(c.key, {text, path: c.path, lang: c.lang});
    paintText(box, text, live);
    const can = !live && c.state === 'ok' && previewable(c);
    const err = can ? syntaxError(c) : '';
    const note = box.querySelector('.cc-note');
    note.hidden = !err; note.textContent = err ? 'Syntaxfehler: ' + err : '';
    box._canPreview = can;
    box._tabView.disabled = !can;
    box._tabView.textContent = live && previewable({...c, state: 'ok'}) ? 'Vorschau nach Abschluss' : 'Vorschau';
    box._tabView.hidden = !live && !previewable(c);
    box._reload.hidden = !can;
    const chosen = modeByKey.get(box._key);
    const want = chosen || (can && !err ? 'preview' : 'code');
    if (box._mode !== want || (want === 'preview' && !box._mounted)) setMode(box, want, false);
  }

  // Paints (or removes) the canvas of one message node.
  function paint(node, tools, live) {
    if (!node) return;
    const c = pick(tools, live && live.text ? live : null);
    let box = node.querySelector(':scope > .code-canvas');
    if (!c) { if (box) box.remove(); return; }
    if (c.text == null && textByKey.has(c.key)) Object.assign(c, textByKey.get(c.key));
    const isLive = c === live;
    const key = isLive ? 'live|' + (c.path || '') : c.key;
    if (box && box._key !== key && !(box._key.startsWith('live|') && !isLive)) { box.remove(); box = null; }
    if (!box) {
      box = build();
      const content = node.querySelector(':scope > .content');
      if (content) content.before(box); else node.append(box);
    }
    apply(box, c, isLive);
  }

  // Full re-renders rebuild every message; keep mounted frames alive.
  function park(area) {
    if (typeof Element.prototype.moveBefore !== 'function') return null;
    let lot = document.getElementById('code-canvas-park');
    if (!lot) { lot = document.createElement('div'); lot.id = 'code-canvas-park'; lot.style.display = 'none'; document.body.append(lot); }
    const kept = new Map();
    for (const b of area.querySelectorAll('.code-canvas')) {
      if (!b._mounted || b._state === 'live' || kept.has(b._key)) continue;
      try { lot.moveBefore(b, null); b._parked = true; kept.set(b._key, b); } catch (e) {}
    }
    return kept;
  }
  function restore(inner, kept) {
    if (!kept) return;
    for (const b of inner.querySelectorAll('.code-canvas')) {
      const old = kept.get(b._key);
      if (!old || old._text !== b._text) continue;
      try { b.parentNode.moveBefore(old, b); } catch (e) { continue; }
      old._parked = false; kept.delete(b._key); seen && seen.unobserve(b); b.remove();
    }
    for (const old of kept.values()) old.remove();
  }

  root.magCodeCanvas = {paint, park, restore, heredoc, toolCode, langFor};
  if (typeof module === 'object' && module.exports) module.exports = root.magCodeCanvas;
})();

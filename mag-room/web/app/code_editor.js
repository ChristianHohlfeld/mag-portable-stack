// Local editable drafts for EVERY fenced code block. Only HTML, CSS,
// browser-JS, SVG and Markdown have a live browser preview; no fake
// compilation for Python, C++, etc. Drafts never modify bot VM files.
// The preview shell has its OWN sandboxed CSP: do not use srcdoc directly
// in the MAG application (it would inherit MAG's strict script-src).
'use strict';
(function () {
  const { MAG } = window, { el } = MAG;
  const PREFIX = 'mag-code-draft:';
  const drafts = new Map();
  const known = new Set(['html', 'htm', 'css', 'js', 'javascript', 'mjs', 'svg', 'md', 'markdown']);
  const escapeText = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const escapeAttr = (s) => escapeText(s).replace(/"/g, '&quot;');

  function language(lang, code) {
    const name = (lang || '').toLowerCase();
    if (known.has(name)) return name;
    // Only unlabelled HTML documents are inferred; never run an unknown
    // program merely because it contains JavaScript-like syntax.
    if (!name && /^\s*(?:<!doctype\s+html\b|<html\b|<svg\b)/i.test(code || '')) return 'html';
    return name;
  }
  const previewable = (lang, code) => known.has(language(lang, code));

  function draft(key, original) {
    let d = drafts.get(key);
    if (!d) {
      d = { base: original, text: original };
      try {
        const saved = JSON.parse(localStorage.getItem(PREFIX + key) || 'null');
        if (saved?.base === original && typeof saved.text === 'string')
          d.text = saved.text;
      } catch {}
      drafts.set(key, d);
    } else if (d.base !== original) {
      // A bot may still be streaming or a server correction may replace code.
      // Preserve an edited draft in this tab; otherwise follow new server text.
      if (d.text === d.base) d.text = original;
      d.base = original;
    }
    return d;
  }

  function save(key, d) {
    try {
      if (d.text === d.base) localStorage.removeItem(PREFIX + key);
      else localStorage.setItem(PREFIX + key, JSON.stringify({ base: d.base, text: d.text }));
    } catch {} // A full storage quota must never destroy the in-memory edit.
  }

  function editor(key, original, lang, onChange) {
    const d = draft(key, original);
    const input = el('textarea', {
      class: 'source-editor', value: d.text, rows: Math.min(22, Math.max(6, original.split('\n').length)),
      spellcheck: false, autocapitalize: 'off', autocomplete: 'off', autocorrect: 'off',
      'aria-label': 'Code direkt bearbeiten'
    });
    const status = el('span', { class: 'edit-state' });
    const resetButton = el('button', { class: 'copy code-reset', type: 'button', text: 'Zurücksetzen' });
    const refreshStatus = () => {
      const changed = d.text !== d.base;
      status.textContent = changed ? 'Lokal geändert · nicht veröffentlicht' : 'Code bearbeiten';
      resetButton.hidden = !changed;
    };
    const notify = () => {
      d.text = input.value;
      save(key, d);
      refreshStatus();
      onChange?.();
    };
    input.addEventListener('input', notify);
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab' || e.ctrlKey || e.metaKey || e.altKey) return;
      e.preventDefault();
      const start = input.selectionStart, end = input.selectionEnd;
      input.setRangeText('  ', start, end, 'end');
      notify();
    });
    resetButton.addEventListener('click', (e) => {
      e.preventDefault();
      input.value = d.base;
      notify();
      input.focus();
    });
    refreshStatus();
    return {
      input, status, resetButton, get: () => input.value,
      changed: () => d.text !== d.base,
      setOriginal: (text) => {
        const edited = d.text !== d.base;
        d.base = text;
        if (!edited) { d.text = text; input.value = text; }
        save(key, d);
        refreshStatus();
        onChange?.();
      }
    };
  }

  function html(lang, source, base = '') {
    const mode = language(lang, source);
    let doc = '';
    if (mode === 'html' || mode === 'htm') {
      doc = source;
    } else if (mode === 'css') {
      doc = '<!doctype html><html><head><meta charset="utf-8"><style>' +
        source.replace(/<\/style/gi, '<\\/style') +
        '</style></head><body><h1>CSS-Vorschau</h1><p>Ein Absatz mit <a href="#">Link</a>.</p>' +
        '<button type="button">Beispielbutton</button><div class="card">Beispielkarte</div></body></html>';
    } else if (mode === 'js' || mode === 'javascript' || mode === 'mjs') {
      doc = '<!doctype html><html><head><meta charset="utf-8"></head><body>' +
        '<main id="app">JavaScript-Vorschau · Element #app</main>' +
        '<script>' + source.replace(/<\/script/gi, '<\\/script') + '<\/script></body></html>';
    } else if (mode === 'svg') {
      doc = '<!doctype html><html><body>' + source + '</body></html>';
    } else if (mode === 'md' || mode === 'markdown') {
      const rendered = window.marked && window.DOMPurify
        ? DOMPurify.sanitize(marked.parse(source), { FORBID_TAGS: ['iframe', 'object', 'embed', 'script'] })
        : '<pre>' + escapeText(source) + '</pre>';
      doc = '<!doctype html><html><head><meta charset="utf-8">' +
        '<meta name="viewport" content="width=device-width,initial-scale=1">' +
        '<style>body{font:16px/1.6 system-ui;margin:20px;max-width:900px}pre{overflow:auto}</style>' +
        '</head><body>' + rendered + '</body></html>';
    }
    if (doc && base && /^https:\/\//i.test(base)) {
      const tag = '<base href="' + escapeAttr(base.endsWith('/') ? base : base + '/') + '">';
      if (/<head\b[^>]*>/i.test(doc)) doc = doc.replace(/<head\b[^>]*>/i, (head) => head + tag);
      else doc = '<!doctype html><head>' + tag + '</head>' + doc;
    }
    return doc;
  }

  // A single delegated listener works across timeline redraws without
  // leaking one handler per preview. Always verify the exact frame window.
  window.addEventListener('message', (event) => {
    if (event.data?.type !== 'mag-preview-ready') return;
    document.querySelectorAll('iframe.code-preview-frame').forEach((frame) => {
      if (event.source === frame.contentWindow) frame._refresh?.();
    });
  });

  function frame(title, getHTML) {
    const iframe = el('iframe', {
      class: 'code-preview-frame', title,
      src: '/app/code-preview.html'
    });
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.setAttribute('credentialless', '');
    iframe._refresh = () => {
      const doc = getHTML();
      if (doc.length > 1500000) return;
      iframe.contentWindow?.postMessage({ type: 'mag-preview-render', html: doc }, '*');
    };
    iframe.addEventListener('load', () => iframe._refresh());
    return iframe;
  }

  MAG.codeEditor = { draft, editor, html, language, previewable, frame };
})();

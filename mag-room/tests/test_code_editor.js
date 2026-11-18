#!/usr/bin/env node
'use strict';
// Editable code is a LOCAL draft. Only browser-native languages preview in
// the isolated shell; no VM changes, privilege expansion or fake compiler.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'outside-only'
});
const { window } = dom, { document } = window;
window.MAG = {
  el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k in node) node[k] = v;
      else node.setAttribute(k, v);
    }
    for (const child of children.flat()) if (child != null) node.append(child);
    return node;
  }
};
window.eval(read('web/app/code_editor.js'));
const edit = window.MAG.codeEditor;
assert(edit, 'code editor loaded');
assert(edit.previewable('html', '<h1>Hi</h1>'));
assert(edit.previewable('css', 'body{color:red}'));
assert(edit.previewable('js', 'document.body.textContent="Hi"'));
assert(edit.previewable('javascript', 'document.body.textContent="Hi"'));
assert(edit.previewable('svg', '<svg></svg>'));
assert(edit.previewable('md', '# Header'));
assert(edit.previewable('', '<!doctype html><html></html>'));
for (const language of ['python', 'cpp', 'c++', 'rust', 'bash', 'typescript', 'json'])
  assert(!edit.previewable(language, 'print("hello")'), language + ' needs a real compiler/runtime');

const key = 'message-123:part-0:code:0';
let updates = 0;
const first = edit.editor(key, 'print("original")', 'python', () => updates++);
document.body.append(first.input, first.status, first.resetButton);
assert.equal(first.get(), 'print("original")');
assert.equal(first.changed(), false);
assert.equal(first.resetButton.hidden, true);
first.input.value = 'print("edited")';
first.input.dispatchEvent(new window.Event('input', { bubbles: true }));
assert.equal(updates, 1);
assert(first.changed());
assert.match(first.status.textContent, /wird gespeichert/);
assert.equal(window.localStorage.getItem('mag-code-draft:' + key), null,
  'typing never serializes to synchronous localStorage');
window.dispatchEvent(new window.Event('pagehide'));
const written = JSON.parse(window.localStorage.getItem('mag-code-draft:' + key));
assert.equal(written.text, 'print("edited")');
assert.match(written.hash, /^\d+:[0-9a-f]{8}$/);
assert(!Object.hasOwn(written, 'base'), 'base source must not be duplicated in storage');
assert.match(first.status.textContent, /nicht veröffentlicht/);
assert.equal(edit.editor(key, 'print("original")', 'python').get(), 'print("edited")',
  'a message redraw retains the local draft');
first.resetButton.click();
assert.equal(first.get(), 'print("original")');
assert.equal(first.changed(), false);
assert.equal(window.localStorage.getItem('mag-code-draft:' + key), null);
const next = edit.editor(key, 'print("new model source")', 'python');
assert.equal(next.get(), 'print("new model source")', 'new source replaces untouched prior code');
const pageKey = 'message-123:page:demo.html';
window.localStorage.setItem('mag-code-draft:' + pageKey, JSON.stringify({
  base: '<h1>Original</h1>', text: '<h1>My draft</h1>'
}));
const deferred = edit.editor(pageKey, '', 'html');
assert.equal(deferred.get(), '', 'page source may be loaded asynchronously');
deferred.setOriginal('<h1>Original</h1>');
assert.equal(deferred.get(), '<h1>My draft</h1>',
  'local edits survive reload even when page source was fetched later');
window.dispatchEvent(new window.Event('pagehide'));
const migrated = JSON.parse(window.localStorage.getItem('mag-code-draft:' + pageKey));
assert(!Object.hasOwn(migrated, 'base'), 'legacy page drafts migrate to compact records');
assert.equal(migrated.text, '<h1>My draft</h1>');

const hugeKey = 'message-big:part-0:code:0';
const huge = edit.editor(hugeKey, 'start', 'html');
huge.input.value = 'a'.repeat(200001);
huge.input.dispatchEvent(new window.Event('input'));
assert.match(huge.status.textContent, /zu groß zum Speichern/);
window.dispatchEvent(new window.Event('pagehide'));
assert.equal(window.localStorage.getItem('mag-code-draft:' + hugeKey), null,
  'oversized draft is RAM-only, not a quota-filling storage write');
assert.equal(huge.get().length, 200001, 'oversized edits remain available in memory');

// Real browser-JS must have a complete script element with safe close tag.
const js = edit.html('javascript', 'const s = "</script>"; document.querySelector("#app").textContent=s;');
assert(js.includes('<main id="app">'));
assert(js.includes('<\\/script>'), 'textual closing script in source must be escaped');
assert(js.endsWith('</script></body></html>'), 'wrapper closes the real script');
assert(edit.html('css', 'h1 { color: red; }').includes('<style>h1 { color: red; }</style>'));
assert(edit.html('html', '<html><head></head><body>Hi</body></html>',
  'https://lena.bot.stardenburg.de').includes('<base href="https://lena.bot.stardenburg.de/">'));
assert.equal(edit.html('python', 'print(1)'), '');

const frame = edit.frame('Vorschau', () => edit.html('html', '<!doctype html><h1>Changed</h1>'));
document.body.append(frame);
assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
assert(!frame.getAttribute('sandbox').includes('allow-same-origin'));
assert.equal(frame.getAttribute('src'), '/app/code-preview.html');
assert(!frame.hasAttribute('credentialless'), 'authenticated same-origin shell GET must work on mobile');
const messages = [];
frame.contentWindow.postMessage = (data, origin) => messages.push({ data, origin });
frame._refresh();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'mag-preview-render');
assert(messages[0].data.html.includes('<h1>Changed</h1>'));
assert.equal(messages[0].origin, '*');
window.dispatchEvent(new window.MessageEvent('message', {
  source: frame.contentWindow, data: { type: 'mag-preview-ready' }
}));
assert.equal(messages.length, 2, 'sandbox shell signals readiness and receives the current code');
window.dispatchEvent(new window.MessageEvent('message', {
  source: null, data: { type: 'mag-preview-ready' }
}));
assert.equal(messages.length, 2, 'spoofed ready events cannot control a preview');

const shell = read('web/app/code-preview.html');
const api = read('native/api.hpp');
const timeline = read('web/app/messages.js');
const html = read('web/index.html');
assert(shell.includes('sandbox="allow-scripts allow-forms'), 'nested sandbox required');
assert(shell.includes('event.source !== parent'), 'preview only receives code from owning parent');
assert(shell.includes('result.srcdoc = event.data.html'), 'shell renders local source in nested sandbox');
assert(api.includes('p == "/app/code-preview.html"'), 'CSP-isolated shell has an explicit route');
assert(api.includes('"sandbox allow-scripts; default-src'), 'route is opaque-origin');
const previewRoute = api.slice(api.indexOf('p == "/app/code-preview.html"'),
  api.indexOf('if (starts_with(p, "/static/")'));
const previewCsp = previewRoute.match(/"sandbox allow-scripts; default-src[^"]*"/)?.[0];
assert(previewCsp, 'preview route has a dedicated sandbox CSP');
assert(!previewCsp.includes('allow-same-origin'), 'preview CSP never grants same-origin');
assert(html.indexOf('/app/code_editor.js') < html.indexOf('/app/messages.js'), 'editor loads first');
assert(timeline.includes('MAG.codeEditor.editor(key, source, lang'), 'every fenced snippet has an editor');
assert(timeline.includes('MAG.codeEditor.editor(key, original,'), 'published pages have an editor');
assert(timeline.includes('code.append(editor.input'), 'published source is editable');
assert(timeline.includes('const frame = edited'), 'edited preview is not the published page');
assert(timeline.includes("'/api/site/' + encodeURIComponent(botId)"), 'published preview is loaded same-origin');
assert(!timeline.includes('allow-same-origin'), 'same-origin preview does not share the room origin');
console.log('Editable code: every block, drafts, reset, HTML/CSS/JS, sandbox and preview update OK');
dom.window.close();

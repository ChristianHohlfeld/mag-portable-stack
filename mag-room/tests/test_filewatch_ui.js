#!/usr/bin/env node
'use strict';
// The owner's file notification on the page: a quiet count in the header,
// rows that unfold to a diff, nothing for anyone else and nothing when off.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<header id="top"><button id="files"></button></header>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'https://mag.test/' });
const w = dom.window, d = w.document;
const listeners = {}, calls = [];
const entries = [
  { n: 1, bot: 'lena', path: '/home/bot/a.sh', kind: 'neu', ts: Date.now(), size: 12, has_detail: true },
  { n: 2, bot: 'lena', path: '/home/bot/a.sh', kind: 'geändert', ts: Date.now(), size: 20, has_detail: true }
];
const el = (tag, attrs = {}, ...kids) => {
  const n = d.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') n.textContent = v; else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'hidden') n.hidden = !!v; else n.setAttribute(k, v);
  }
  n.append(...kids.filter(Boolean));
  return n;
};
w.MAG = {
  el, $: (s) => d.querySelector(s),
  state: { you: { role: 'owner' }, settings: { file_watch: true }, bots: [{ id: 'lena', name: 'Blake' }] },
  on: (t, f) => (listeners[t] ||= []).push(f),
  name: (id) => id === 'lena' ? 'Blake' : id, bytes: (n) => n + ' B',
  api: async (method, url) => {
    calls.push(url);
    if (url.startsWith('/api/filewatch?')) return { enabled: true, entries: url.endsWith('after=0') ? entries : [] };
    return { ...entries[1], detail_kind: 'diff', detail: '--- vorher\n+++ nachher\n@@ -1 +1,2 @@\n echo eins\n+echo zwei\n' };
  },
  sheet: (title, build) => { const content = el('div'), sheet = el('section', { class: 'sheet' }, content); d.body.append(sheet); build(content, sheet, () => sheet.remove()); return sheet; }
};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'filewatch.js'), 'utf8'));
const flush = () => new Promise((r) => setTimeout(r, 10));
(async () => {
  const button = d.getElementById('filewatch');
  assert(button && button.nextElementSibling.id === 'files', 'the button stands in the header');
  listeners.settings.forEach((f) => f());
  await flush();
  assert.equal(button.hidden, false);
  assert.equal(button.querySelector('.fw-count').textContent, '2');
  button.click();
  const rows = d.querySelectorAll('.fw-row');
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /\/home\/bot\/a\.sh.*Blake · geändert/);
  assert.equal(button.querySelector('.fw-count').textContent, '', 'opened is read');
  rows[0].open = true;
  rows[0].dispatchEvent(new w.Event('toggle'));
  await flush();
  assert.equal(rows[0].querySelector('.fw-detail .add').textContent, '+echo zwei\n');
  // Switched off, or not the owner: no button and no request.
  w.MAG.state.settings.file_watch = false;
  listeners.settings.forEach((f) => f());
  assert.equal(button.hidden, true);
  w.MAG.state.settings.file_watch = true;
  w.MAG.state.you.role = 'local';
  const before = calls.length;
  listeners.settings.forEach((f) => f());
  await flush();
  assert.equal(button.hidden, true);
  assert.equal(calls.length, before);
  console.log('filewatch ui ok');
  w.close();
  process.exit(0);
})();

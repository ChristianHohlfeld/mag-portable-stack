#!/usr/bin/env node
'use strict';
// MAG in the system's share menu: the manifest names /share as a share target,
// the service worker keeps what was shared in Cache Storage and sends the page
// to /?share=<id>, and the page sends it the composer's way: every file through
// /api/upload, words and link as the text of one /api/message. Something
// Chris was already writing is not sent for him: the share waits next to it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { File } = require('node:buffer');
const { JSDOM } = require('jsdom');

const web = path.join(__dirname, '..', 'web');
const read = (p) => fs.readFileSync(path.join(web, p), 'utf8');

// One Cache Storage shared by the service worker and the page.
const store = new Map();
const cacheOf = (name) => {
  if (!store.has(name)) store.set(name, new Map());
  const m = store.get(name);
  const key = (req) => new URL(typeof req === 'string' ? req : req.url, 'https://mag.test').pathname;
  return {
    async put(req, res) { m.set(key(req), { type: res.headers.get('Content-Type'), body: Buffer.from(await res.arrayBuffer()) }); },
    async match(req) { return m.get(key(req)); },
    async delete(req) { return m.delete(key(req)); },
    async keys() { return [...m.keys()].map((p) => ({ url: 'https://mag.test' + p })); },
  };
};

(async () => {
  // The manifest the room serves declares the target.
  const api = fs.readFileSync(path.join(__dirname, '..', 'native', 'api.hpp'), 'utf8');
  for (const part of ['share_target', '/share', 'multipart/form-data', 'image/*', 'video/*', 'audio/*', 'application/pdf', '*/*'])
    assert.ok(api.includes(part), 'manifest share target names ' + part);
  assert.ok(/POST" && p == "\/share"\) return http::send\(fd, 303/.test(api), 'a share that reaches the room is sent back to the page');

  // The service worker takes the post.
  const handlers = {};
  const env = {
    URL, Response, console, location: { origin: 'https://mag.test' },
    self: { location: { origin: 'https://mag.test' }, addEventListener(n, fn) { handlers[n] = fn; }, clients: { async claim() {} }, registration: {} },
    caches: { async open(name) { return cacheOf(name); }, async keys() { return [...store.keys()]; }, async delete(n) { return store.delete(n); } },
    fetch() { throw new Error('a share never goes to the network from the worker'); },
  };
  vm.runInNewContext(read('sw.js').replace('{{V}}', 'test'), env, { filename: 'sw.js' });
  async function share(fields, files) {
    let answer;
    const form = { get: (k) => fields[k] ?? null, getAll: (k) => (k === 'files' ? files : []) };
    handlers.fetch({ request: { method: 'POST', url: 'https://mag.test/share', mode: 'navigate', formData: async () => form },
      respondWith(p) { answer = p; } });
    const res = await answer;
    assert.equal(res.status, 303);
    return new URL(res.headers.get('Location'), 'https://mag.test').searchParams.get('share');
  }
  const pdf = new File([Buffer.from('%PDF-1.4 hallo')], 'Rechnung.pdf', { type: 'application/pdf' });
  const photo = new File([Buffer.from([0xff, 0xd8, 0xff, 0xd9])], 'foto.jpg', { type: 'image/jpeg' });
  const id = await share({ title: 'Seite', text: 'Schau mal', url: 'https://example.org/a' }, [pdf, photo]);
  assert.match(id, /^[a-z0-9]+-[a-z0-9]+$/);
  assert.equal(store.get('mag-share').size, 3, 'meta and both files are kept for the page');

  // A new release does not throw away a share that waits.
  let activated;
  handlers.activate({ waitUntil(p) { activated = p; } });
  await activated;
  assert.ok(store.has('mag-share'));

  // The page sends it.
  async function page(url, draft) {
    const html = `<!doctype html><html><body><div id="list"></div><div id="staged"></div><button id="attach"></button>
<textarea id="input"></textarea><button id="send"></button>
<script>window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.ResizeObserver = class { observe() {} disconnect() {} };</script>
<script>${read('app/core.js').replace(/<\/script/gi, '<\\/script')}</script>
<script>${read('app/composer.js').replace(/<\/script/gi, '<\\/script')}</script></body></html>`;
    const dom = new JSDOM(html, { url, pretendToBeVisual: true, runScripts: 'dangerously' });
    const w = dom.window, calls = [], toasts = [];
    if (draft) w.sessionStorage.setItem('mag-draft', draft);
    w.caches = { async open(name) {
      const c = cacheOf(name);
      return { ...c, async match(req) {
        const hit = await c.match(req);
        return hit && { json: async () => JSON.parse(hit.body.toString()), blob: async () => new w.Blob([hit.body], { type: hit.type }) };
      } };
    } };
    w.MAG.scrollToEnd = () => {};
    w.MAG.toast = (t) => toasts.push(t);
    w.MAG.api = async (method, u, body) => {
      calls.push({ method, u, body });
      if (u.startsWith('/api/upload')) return { file: 'f' + calls.length + '-' + decodeURIComponent(u.split('name=')[1]), kind: 'file', size: body.size };
      return { ok: true };
    };
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    for (let i = 0; i < 50 && !calls.some((c) => c.u === '/api/message') && !toasts.length; i++) await new Promise((r) => setTimeout(r, 10));
    return { w, calls, toasts };
  }

  const first = await page('https://mag.test/?share=' + id);
  const uploads = first.calls.filter((c) => c.u.startsWith('/api/upload'));
  assert.deepEqual(uploads.map((c) => c.u), ['/api/upload?name=Rechnung.pdf', '/api/upload?name=foto.jpg'], 'every file goes through the normal upload');
  assert.equal(uploads[0].body.size, pdf.size);
  const sent = first.calls.find((c) => c.u === '/api/message');
  assert.ok(sent, 'the share is sent as a message');
  assert.equal(sent.body.text, 'Seite\nSchau mal\nhttps://example.org/a');
  assert.equal(sent.body.attachments.length, 2);
  assert.equal(sent.body.mode, undefined, 'a normal message, not an order');
  assert.equal(first.w.location.search, '', 'the share id leaves the address');
  assert.equal(store.get('mag-share').size, 0, 'nothing of it stays in the cache');

  // A link from the browser: the address is the text once.
  const link = await share({ title: '', text: 'https://example.org/b', url: '' }, []);
  const second = await page('https://mag.test/?share=' + link);
  assert.equal(second.calls.length, 1);
  assert.equal(second.calls[0].body.text, 'https://example.org/b');
  assert.equal(second.calls[0].body.attachments.length, 0);

  // Chris was writing: the share joins his draft and waits for him.
  const words = await share({ text: 'Zitat' }, []);
  const third = await page('https://mag.test/?share=' + words, 'Meine Frage');
  assert.equal(third.calls.length, 0, 'a draft is never sent for him');
  assert.equal(third.w.document.getElementById('input').value, 'Meine Frage\nZitat');

  // A share that never reached the worker (or is gone) is said, not dropped silently.
  const lost = await page('https://mag.test/?share=lost');
  assert.equal(lost.calls.length, 0);
  assert.match(lost.toasts[0], /noch einmal teilen/);
  console.log('share target ok');
})().catch((e) => { console.error(e); process.exit(1); });

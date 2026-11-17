#!/usr/bin/env node
'use strict';
// A model streams a long fenced code answer over many chunks. Rendering must
// stay lightweight until completion; editing must preserve focus and preview.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web', 'app');
const source = (filename) => fs.readFileSync(path.join(root, filename), 'utf8');
(async () => {
  const docHtml = '<!doctype html><html><body>' +
    '<div id="scroll"><button id="older" hidden></button><div id="empty" hidden></div>' +
    '<div id="list"></div><div id="activity"></div></div>' +
    '<button id="jump" hidden><span></span></button></body></html>';
  const dom = new JSDOM(docHtml, {
    url: 'https://mag.test/', pretendToBeVisual: true,
    runScripts: 'outside-only'
  });
  const w = dom.window, doc = w.document;
  w.TextEncoder = TextEncoder;
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  // Minimal Markdown fixture to exercise the REAL editor DOM code, without
  // relying on the parsing behavior of a particular Markdown version.
  const fence = String.fromCharCode(96).repeat(3);
  w.marked = {
    setOptions() {},
    parse(input) {
      const start = input.indexOf(fence);
      const end = input.indexOf(fence, start + fence.length);
      if (start < 0) return '<p>' + input.replaceAll('<', '&lt;') + '</p>';
      const opening = input.slice(start + fence.length).split('\n')[0];
      const bodyStart = input.indexOf('\n', start + fence.length) + 1;
      const raw = input.slice(bodyStart, end < 0 ? undefined : end).replace(/\n$/, '');
      const escaped = raw.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
      return '<pre><code class="language-' + opening + '">' + escaped + '</code></pre>';
    }
  };
  w.DOMPurify = { sanitize: (text) => text };
  class Stream {
    static instance = null;
    static OPEN = 1;
    static CLOSED = 2;
    constructor() { this.readyState = 1; Stream.instance = this; }
    close() { this.readyState = 2; }
    event(ev) { this.onmessage({ data: JSON.stringify(ev) }); }
  }
  w.EventSource = Stream;
  try {
    w.eval(source('core.js'));
    w.eval(source('code_editor.js'));
    w.eval(source('messages.js'));
    doc.dispatchEvent(new w.Event('DOMContentLoaded'));
    const firstSource = '<!doctype html>\n<h1>Hi</h1>';
    const m = {
      id: 'm-111', role: 'bot', from: 'lena', state: 'streaming',
      ts: Date.now(), text: '', pages: [], parts: [
        { k: 'text', text: fence + 'html\n' + firstSource + '\n' + fence }
      ]
    };
    const snapshot = { messages: [m], bots: [], settings: {}, you: { role: 'owner', name: 'Chris' },
      features: {}, seq: 1, more: false };
    w.MAG.api = async () => snapshot;
    w.MAG.connect();
    Stream.instance.onopen();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const article = doc.querySelector('.msg[data-id="m-111"]');
    assert(article, 'streaming message is mounted');
    assert(article.querySelector('.codeblock-live'), 'streaming code uses plain view');
    assert.equal(article.querySelectorAll('textarea.source-editor').length, 0,
      'no heavy editor during stream');
    assert.equal(article.querySelectorAll('iframe').length, 0, 'no eager iframe during stream');
    const sameArticle = article;
    Stream.instance.event({ t: 'message', seq: 2, m: {
      ...m, parts: m.parts.map((p) => ({ ...p }))
    } });
    assert.strictEqual(doc.querySelector('.msg[data-id="m-111"]'), sameArticle,
      'streaming message snapshots do not replace the article');

    for (let i = 0; i < 12; i++)
      Stream.instance.event({ t: 'delta', seq: 3 + i, id: m.id, part: 0, text: ' ' });
    await new Promise((resolve) => setTimeout(resolve, 160));
    assert.equal(doc.querySelectorAll('textarea.source-editor').length, 0,
      'growing source still has no editor and cannot steal focus');
    assert(doc.querySelector('.codeblock-live'), 'streaming block stayed lightweight');

    const completed = { ...m, state: 'done', text: 'Code fertig',
      parts: [{ k: 'text', text: fence + 'html\n' + firstSource + '\n' + fence }] };
    Stream.instance.event({ t: 'message', seq: 20, m: completed });
    const input = doc.querySelector('.codeblock textarea.source-editor');
    assert(input, 'editor is created when the model finishes');
    assert.equal(doc.querySelector('.codeblock-live'), null, 'plain display upgrades to editor');
    input.focus();
    input.setSelectionRange(5, 5);
    const preview = [...doc.querySelectorAll('.codeblock .code-tabs button')]
      .find((button) => button.textContent === 'Vorschau');
    assert(preview, 'HTML has a live preview tab');
    preview.click();
    const frame = doc.querySelector('.code-preview-frame');
    assert(frame, 'preview only mounts when requested');

    Stream.instance.event({ t: 'message', seq: 21, m: {
      ...completed, error: '', parts: completed.parts.map((p) => ({ ...p }))
    } });
    assert.strictEqual(doc.querySelector('.codeblock textarea.source-editor'), input,
      'status update must preserve editor node and user selection');
    assert.strictEqual(doc.querySelector('.code-preview-frame'), frame,
      'status update must preserve mounted preview, not restart WebGL/audio');
    assert.equal(input.selectionStart, 5);
    console.log('Streaming code: lightweight output, completed editor and active preview survive status updates');
  } finally {
    dom.window.close();
  }
})().catch((err) => { console.error(err); process.exitCode = 1; });

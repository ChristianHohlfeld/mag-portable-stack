#!/usr/bin/env node
'use strict';
// The media filter is presentation-only. Neither SSE nor generated cards,
// bot messages, tool state, uploads, playback nodes or scroll anchors disappear.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const wait = () => new Promise((done) => setTimeout(done, 0));
const msg = (id, role, from, extras = {}) => ({
  id, role, from, ts: Date.now(), state: 'done', text: '', parts: [], attachments: [], ...extras
});
const image = (id, state = 'queued') => msg(id, 'service', 'idol', {
  state, model3d: { kind: 'image', state, prompt: 'Berge bei Sonnenaufgang', phase: 0 }, text: 'Bild wird erstellt'
});
const audio = { file: 'sample.mp3', name: 'sample.mp3', kind: 'audio', size: 400 };
const file = (kind, name) => ({ file: name, name, kind, size: 400 });
const fixture = (snap, saved = null) => {
  const html = read('index.html');
  const dom = new JSDOM(html, { url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'outside-only' });
  const { window: w } = dom, { document: d } = w;
  // Verify the actual app stylesheet, rather than simulating a second
  // filtering implementation inside the test.
  const css = d.createElement('style');
  css.textContent = read('app/app.css');
  d.head.append(css);
  if (saved) w.localStorage.setItem('mag-chat-view', saved);
  w.ResizeObserver = class { observe() {} disconnect() {} };
  w.IntersectionObserver = class { observe() {} disconnect() {} };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  w.HTMLMediaElement.prototype.pause = function () {};
  // No actual player should be remounted merely because the view changes.
  let instance = null;
  class Stream {
    static OPEN = 1;
    static CLOSED = 2;
    constructor() { instance = this; this.readyState = 1; }
    close() { this.readyState = 2; }
    event(v) { this.onmessage({ data: JSON.stringify(v) }); }
  }
  w.EventSource = Stream;
  w.eval(read('app/core.js'));
  const S = w.MAG.state;
  w.MAG.api = async (method, endpoint) => {
    if (endpoint.startsWith('/api/state')) return snap;
    if (endpoint.startsWith('/api/messages')) return { messages: [], more: false };
    throw Error('Unexpected request ' + method + ' ' + endpoint);
  };
  w.eval(read('app/messages.js'));
  d.dispatchEvent(new w.Event('DOMContentLoaded'));
  w.MAG.connect();
  instance.onopen();
  return { dom, w, d, S, event: (e) => instance.event(e) };
};
const row = (d, id) => d.querySelector('#list > .msg[data-id="' + id + '"]');
const visible = (w, node) => !!node && !node.hidden && w.getComputedStyle(node).display !== 'none';

(async () => {
  const user = msg('000001', 'user', 'chris', { text: 'Erstelle ein Video' });
  const lena = msg('000002', 'bot', 'lena', { text: 'Ich suche nach Dateien',
    parts: [{ k: 'text', text: 'Ich suche nach Dateien' }] });
  const noah = msg('000003', 'bot', 'noah', { text: 'Arbeitsschritte folgen',
    parts: [{ k: 'text', text: 'Arbeitsschritte folgen' }] });
  const serviceText = msg('000004', 'service', 'idol', { text: 'Reiner Servicehinweis' });
  const picture = image('000005');
  const video = msg('000006', 'service', 'idol', { state: 'running',
    model3d: { kind: 'video', state: 'running', prompt: 'Ein kurzer Clip' } });
  const mesh = msg('000007', 'service', 'idol', { state: 'queued',
    model3d: { kind: '3d', state: 'queued', quality: 'normal' } });
  const scene = msg('000008', 'service', 'idol', { state: 'running',
    model3d: { kind: 'scene', state: 'running', cast: [], title: 'Szene' } });
  const mixedBot = msg('000009', 'bot', 'noah', {
    text: 'Ich generiere etwas', parts: [{ k: 'text', text: 'Ich generiere etwas' }],
    attachments: [file('image', 'result.png')]
  });
  const movie = msg('000010', 'service', 'idol', {
    attachments: [file('video', 'generated.mp4')]
  });
  const loneAudio = msg('000011', 'bot', 'lena', {
    text: 'Musik ist fertig', parts: [{ k: 'text', text: 'Musik ist fertig' }], attachments: [audio]
  });
  const staged = msg('000012', 'user', 'chris', { attachments: [file('image', 'uploaded.png')] });
  const regularAudio = msg('000013', 'bot', 'noah', {
    text: 'Rohspur', attachments: [{ ...audio, voiceover_kind: 'take' }]
  });
  const snap = { messages: [user, lena, noah, serviceText, picture, video, mesh, scene,
    mixedBot, movie, loneAudio, staged, regularAudio],
    bots: [{ id: 'lena', name: 'Lena' }, { id: 'noah', name: 'Noah' }],
    you: { role: 'owner', name: 'Chris' }, settings: {}, features: {}, seq: 1, more: false };
  const f = fixture(snap);
  const { d, w, S } = f;
  await wait();
  const picker = d.querySelector('#viewbar');
  const all = picker.querySelector('[data-view=all]'), media = picker.querySelector('[data-view=media]');
  assert.equal(d.querySelectorAll('#viewbar .seg button').length, 2,
    'native MAG segmented view; no modal or new page');
  assert(visible(w, row(d, user.id)), 'ordinary chat visible by default');
  assert(visible(w, row(d, lena.id)), 'bot messages visible by default');
  const videoNode = row(d, movie.id).querySelector('video');
  assert(videoNode, 'existing player is already mounted in ordinary view');

  media.click();
  assert(d.querySelector('#list').classList.contains('media-only'));
  assert.equal(media.getAttribute('aria-pressed'), 'true');
  assert.equal(all.getAttribute('aria-pressed'), 'false');
  assert.equal(w.localStorage.getItem('mag-chat-view'), 'media');
  for (const m of [user, lena, noah, serviceText, staged, regularAudio])
    assert(!visible(w, row(d, m.id)), 'non-generated content is hidden: ' + m.id);
  for (const m of [picture, video, mesh, scene, mixedBot, movie, loneAudio])
    assert(visible(w, row(d, m.id)), 'queued/running/done media visible: ' + m.id);
  assert.equal(w.getComputedStyle(row(d, mixedBot.id).querySelector('.body')).display, 'none',
    'bot text is hidden even when bot message carries a generated image');
  assert.equal(w.getComputedStyle(row(d, loneAudio.id).querySelector('.body')).display, 'none');
  assert.strictEqual(row(d, movie.id).querySelector('video'), videoNode,
    'switch must not remount or restart an existing video player');
  assert.equal(S.messages.size, snap.messages.length, 'filter never removes chat history');
  assert.equal(row(d, picture.id).dataset.day, 'Heute',
    'day header belongs to the first visible media message');

  // New bot text and tool events still reach the store, but remain hidden.
  f.event({ t: 'message', seq: 2, m: msg('000014', 'bot', 'lena',
    { state: 'streaming', parts: [{ k: 'text', text: 'Ein neuer Gedanke' }] }) });
  f.event({ t: 'delta', seq: 3, id: '000014', part: 0, text: ' und mehr' });
  assert(!visible(w, row(d, '000014')));
  assert.match(S.messages.get('000014').parts[0].text, /und mehr/);
  assert(d.querySelector('#jump').hidden, 'hidden text must not count as new visible messages');

  // An active image job is already visible before it has image bytes. The
  // same logical card keeps updating when its attachment arrives via SSE.
  const completed = { ...picture, state: 'done',
    model3d: { ...picture.model3d, state: 'done', took: 2 },
    attachments: [file('image', 'fresh.png')] };
  f.event({ t: 'message', seq: 4, m: completed });
  assert(visible(w, row(d, picture.id)), 'completed job remains visible');
  assert(row(d, picture.id).querySelector('.atts'), 'new generated media is visible');
  assert(d.querySelector('#list').classList.contains('media-only'), 'SSE cannot reset the view');

  // New media becomes visible without touching the setting.
  f.event({ t: 'message', seq: 5, m: image('000015') });
  assert(visible(w, row(d, '000015')));
  assert.equal(d.querySelector('#media-empty').hidden, true);

  all.click();
  assert(visible(w, row(d, user.id)), 'all original messages return immediately');
  assert(visible(w, row(d, lena.id)), 'bot text returns without refetching');
  assert.strictEqual(row(d, movie.id).querySelector('video'), videoNode);
  assert.equal(w.localStorage.getItem('mag-chat-view'), 'all');
  f.dom.window.close();

  // Preference is restored on a fresh page, even before media has arrived.
  const g = fixture({ ...snap, messages: [user, lena], seq: 10 }, 'media');
  await wait();
  assert(g.d.querySelector('#list').classList.contains('media-only'));
  assert.equal(g.d.querySelector('#media-empty').hidden, false);
  assert.equal(g.d.querySelector('#empty').hidden, true);
  g.event({ t: 'message', seq: 11, m: image('000020', 'queued') });
  assert(visible(g.w, row(g.d, '000020')));
  assert.equal(g.d.querySelector('#media-empty').hidden, true);
  g.event({ t: 'gone', seq: 12, id: '000020' });
  assert.equal(g.d.querySelector('#media-empty').hidden, false,
    'empty state reacts when the last generated media is removed');
  g.d.querySelector('#viewbar [data-view=all]').click();
  assert(visible(g.w, row(g.d, user.id)), 'unfiltered timeline remains intact');
  g.dom.window.close();
  console.log('Media filter: MAG tabs, bot/step hiding, queued jobs, SSE, player identity, persistence, empty state OK');
})().catch((e) => { console.error(e); process.exitCode = 1; });

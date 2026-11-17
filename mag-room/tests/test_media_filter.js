#!/usr/bin/env node
'use strict';
// MAG's only media filter: header toggle, no second timeline or backend state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
const wait = () => new Promise(resolve => setTimeout(resolve, 0));
const message = (id, role, from, extra = {}) => ({
  id, role, from, ts: Date.now(), state: 'done', text: '', parts: [], attachments: [], ...extra
});
const image = (id, state = 'queued') => message(id, 'service', 'idol', {
  state, model3d: { kind: 'image', state, prompt: 'Sonnenaufgang' }
});
const file = (kind, name) => ({ file: name, name, kind, size: 300 });
const fixture = async (messages, mode = null, legacy = false) => {
  const dom = new JSDOM(read('index.html'), {
    url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'outside-only'
  });
  const w = dom.window, d = w.document;
  if (legacy) d.querySelector('#media-filter').remove();
  const style = d.createElement('style');
  style.textContent = read('app/app.css');
  d.head.append(style);
  if (mode !== null) w.localStorage.setItem('mag-media-only', mode);
  w.ResizeObserver = class { observe() {} disconnect() {} };
  w.IntersectionObserver = class { observe() {} disconnect() {} };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  w.HTMLMediaElement.prototype.pause = function () {};
  let stream;
  class EventStream {
    static OPEN = 1;
    static CLOSED = 2;
    constructor() { stream = this; this.readyState = 1; }
    close() { this.readyState = 2; }
    push(event) { this.onmessage({ data: JSON.stringify(event) }); }
  }
  w.EventSource = EventStream;
  w.eval(read('app/core.js'));
  w.MAG.api = async (method, url) => {
    if (url.startsWith('/api/state')) return {
      messages, bots: [{ id: 'lena', name: 'Lena' }, { id: 'noah', name: 'Noah' }],
      you: { role: 'owner', name: 'Chris' }, settings: {}, features: {}, more: false, seq: 1
    };
    throw Error('unexpected request ' + method + ' ' + url);
  };
  w.eval(read('app/messages.js'));
  // jsdom dispatches DOMContentLoaded itself after parsing. Sending a second
  // synthetic event registers the toggle handler twice and one click undoes
  // itself. Wait for the real event, just as a browser would.
  if (d.readyState === 'loading') {
    await new Promise(resolve => d.addEventListener('DOMContentLoaded', resolve, { once: true }));
  }
  w.MAG.connect();
  stream.onopen();
  return { dom, w, d, S: w.MAG.state, push: (event) => stream.push(event) };
};
const row = (d,id) => d.querySelector('#list > .msg[data-id="' + id + '"]');
const visible = (w,node) => !!node && !node.hidden && w.getComputedStyle(node).display !== 'none';
(async () => {
  const human = message('000001', 'user', 'chris', { text: 'Bild bitte' });
  const lena = message('000002', 'bot', 'lena', { text: 'Ich plane', parts: [{ k: 'text', text: 'Ich plane' }] });
  const noah = message('000003', 'bot', 'noah', { text: 'Arbeitsschritt',
    parts: [{ k: 'text', text: 'Arbeitsschritt' }] });
  const queued = image('000004', 'queued');
  const running = image('000005', 'running');
  const failed = image('000006', 'error');
  const video = message('000007', 'service', 'idol', {
    model3d: { kind: 'video', state: 'done', prompt: 'Film' },
    attachments: [file('video', 'film.mp4')]
  });
  const scene = message('000008', 'service', 'scene', {
    model3d: { kind: 'scene', state: 'cancelled', cast: [], title: 'Szene' }
  });
  const model = message('000009', 'service', 'idol', {
    model3d: { kind: '3d', state: 'running', quality: 'normal' }
  });
  const uploaded = message('000010', 'user', 'chris', { attachments: [file('image','upload.png')] });
  const service = message('000011', 'service', 'idol', { text: 'Hinweis' });
  const f = await fixture([human, lena, noah, queued, running, failed, video, scene, model, uploaded, service]);
  const { d, w, S } = f; await wait();
  const button = d.querySelector('#media-filter');
  assert.equal(button.getAttribute('aria-label'), 'Nur Medien');
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.equal(d.querySelector('#viewbar'), null, 'no extra tab/navigation row');
  assert(visible(w,row(d,lena.id)), 'bot text visible by default');
  const player = row(d,video.id).querySelector('video');
  assert(player, 'generated video player mounted');
  button.click();
  assert.equal(d.querySelector('#app').classList.contains('media'), true);
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(w.localStorage.getItem('mag-media-only'), '1');
  for(const m of [human,lena,noah,uploaded,service])
    assert(!visible(w,row(d,m.id)), 'non-generation hidden ' + m.id);
  for(const m of [queued,running,failed,video,scene,model])
    assert(visible(w,row(d,m.id)), 'all generation states visible ' + m.id);
  assert.equal(S.messages.size, 11, 'all messages still in client state');
  assert.strictEqual(row(d,video.id).querySelector('video'), player,
    'filter must not remount video players');
  assert.equal(row(d,queued.id).dataset.day, 'Heute',
    'first visible media receives the day separator');

  f.push({t:'message',seq:2,m:message('000012','bot','lena',{
    state:'streaming',parts:[{k:'text',text:'Neue Antwort'}]})});
  f.push({t:'delta',seq:3,id:'000012',part:0,text:' mehr'});
  assert(!visible(w,row(d,'000012')), 'streamed bot text remains hidden');
  assert.match(S.messages.get('000012').parts[0].text,/mehr/,
    'hidden messages continue receiving SSE');
  f.push({t:'message',seq:4,m:image('000013','running')});
  assert(visible(w,row(d,'000013')), 'new running generation visible');
  assert.strictEqual(row(d,video.id).querySelector('video'), player);
  button.click();
  assert(visible(w,row(d,lena.id)), 'bot texts return without refetch');
  assert(visible(w,row(d,human.id)), 'human text returns without refetch');
  assert.strictEqual(row(d,video.id).querySelector('video'),player);
  f.dom.window.close();

  const g = await fixture([human,lena], '1'); await wait();
  assert(g.d.querySelector('#app').classList.contains('media'),
    'filter restored after reload');
  assert.equal(g.d.querySelector('#empty').textContent,'Noch kein Bild, Video oder 3D.');
  assert.equal(g.d.querySelector('#empty').hidden,false);
  g.push({t:'message',seq:2,m:image('000020','queued')});
  assert.equal(g.d.querySelector('#empty').hidden,true,'empty message clears when generation arrives');
  g.push({t:'gone',seq:3,id:'000020'});
  assert.equal(g.d.querySelector('#empty').hidden,false);
  g.d.querySelector('#media-filter').click();
  assert(visible(g.w,row(g.d,human.id)));
  g.dom.window.close();

  const legacy = await fixture([human,lena], '1', true); await wait();
  assert(visible(legacy.w,row(legacy.d,lena.id)),
    'older DOM fixtures without the new button still render normally');
  legacy.dom.window.close();
  console.log('media filter: header toggle, queued/running/error/done jobs, SSE, persistence, player identity and legacy DOM OK');
})().catch(err=>{
  console.error(err);
  // A failed jsdom regression must fail CI immediately, not leave active
  // handles blocking the next test for twenty minutes.
  process.exit(1);
});

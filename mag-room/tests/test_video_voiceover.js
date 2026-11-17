#!/usr/bin/env node
'use strict';
// Real browser DOM for compact three-lane voiceover, permission/recording and
// generic message SSE preservation (including the scene's SECOND keep node).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const read = (name) => fs.readFileSync(path.join(__dirname, '..', 'web', 'app', name), 'utf8');
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function fixture(scene = false) {
  const html = '<!doctype html><html><body><div id="scroll"><button id="older" hidden></button>' +
    '<div id="empty" hidden></div><div id="list"></div><div id="activity"></div></div>' +
    '<button id="jump" hidden><span></span></button></body></html>';
  const dom = new JSDOM(html, { url: 'https://mag.test/', pretendToBeVisual: true,
    runScripts: 'outside-only' });
  const w = dom.window, d = w.document;
  Object.defineProperty(w, 'isSecureContext', { value: true, configurable: true });
  Object.defineProperty(w.HTMLMediaElement.prototype, 'readyState', { get() { return 4; }, configurable: true });
  Object.defineProperty(w.HTMLMediaElement.prototype, 'duration', { get() { return 5; }, configurable: true });
  w.HTMLMediaElement.prototype.play = function () {
    Object.defineProperty(this, 'paused', { value: false, configurable: true, writable: true });
    this.dispatchEvent(new w.Event('play')); return Promise.resolve();
  };
  w.HTMLMediaElement.prototype.pause = function () {
    Object.defineProperty(this, 'paused', { value: true, configurable: true, writable: true });
    this.dispatchEvent(new w.Event('pause'));
  };
  w.HTMLMediaElement.prototype.load = function () {};
  w.ResizeObserver = class { observe() {} disconnect() {} };
  w.IntersectionObserver = class { observe() {} disconnect() {} };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  let streamStopped = 0;
  const perms = [];
  w.navigator.mediaDevices = { getUserMedia: async (constraints) => {
    perms.push(constraints);
    return { getAudioTracks: () => [{}], getTracks: () => [{ stop() { streamStopped++; } }] };
  } };
  let type = 'audio/mp4';
  class Recorder extends w.EventTarget {
    static last = null;
    constructor(stream, options) { super(); this.mimeType = options.mimeType; this.state = 'inactive'; Recorder.last = this; }
    static isTypeSupported(t) { return t === type; }
    start() { this.state = 'recording'; }
    stop() {
      assert.equal(this.state, 'recording');
      this.state = 'inactive';
      const event = new w.Event('dataavailable');
      event.data = new w.Blob(['X'.repeat(512)], { type: this.mimeType });
      this.dispatchEvent(event);
      this.dispatchEvent(new w.Event('stop'));
    }
  }
  w.MediaRecorder = Recorder;
  w.eval(read('core.js'));
  w.MAG.state.you = { role: 'owner' };
  if (scene) w.MAG.viewer3d = { attach() {}, drop() {}, toggle() {}, reset() {}, fullscreen() {} };
  w.eval(read('voiceover.js'));
  if (scene) w.eval(read('messages.js'));
  d.dispatchEvent(new w.Event('DOMContentLoaded'));
  return { dom, w, d, perms, Recorder, setType: (t) => { type = t; }, get stopped() { return streamStopped; } };
}
const find = (node, text) => [...node.querySelectorAll('button')].find((b) => b.textContent.includes(text));

(async () => {
  const f = fixture(), { w, d } = f, { MAG } = w;
  const uploads = [], bakes = [];
  MAG.api = async (method, endpoint, body) => {
    if (endpoint.startsWith('/api/video/voiceover')) { uploads.push({ method, endpoint, body }); return {}; }
    if (endpoint === '/api/video/bake') { bakes.push({ method, body }); return {}; }
    throw Error('Unexpected request ' + endpoint);
  };
  const original = { file: 'original.mp4', name: 'original.mp4', kind: 'video' };
  let message = { id: 'abc12344', attachments: [original] };
  const ui = MAG.voiceover.player(message, original);
  d.body.append(ui);
  assert.equal(ui.querySelectorAll('video').length, 1);
  assert.equal(ui.querySelectorAll('select').length, 0, 'no bloated source/mix selectors');
  assert.equal(ui.querySelectorAll('.voice-lanes button').length, 3);
  assert.equal(f.perms.length, 0, 'microphone permission never on mount');
  const record = async (lane) => {
    ui.querySelector('.voice-lanes button[aria-label="' + lane + '"]').click();
    find(ui, 'Aufnehmen').click(); // permission gesture
    await flush();
    assert.equal(f.perms.at(-1).video, false);
    find(ui, 'Start').click(); // active user tap, starts MediaRecorder and video
    assert.equal(f.Recorder.last.state, 'recording');
    assert.equal(ui.querySelectorAll('video').length, 1);
    find(ui, 'Stoppen').click();
    await flush();
    assert.equal(f.Recorder.last.state, 'inactive');
    const t = uploads.at(-1);
    assert(t.body instanceof w.Blob && t.body.size >= 128);
    assert(t.endpoint.includes('lane=' + (lane === 'Stimme' ? 'voice' : lane === 'Melodie' ? 'melody' : 'effects')));
    return t;
  };
  const voice = await record('Stimme');
  assert.equal(uploads.length, 1);
  const v1 = { file: 'v1.m4a', kind: 'audio', voiceover_kind: 'take',
    voiceover_lane: 'voice', voiceover_for: original.file };
  message = { ...message, attachments: [...message.attachments, v1] };
  ui._voiceoverRefresh(message);
  assert(ui.querySelector('.voice-lanes [aria-label="Stimme"]').textContent.includes('✓'));

  await record('Melodie');
  const mel = { file: 'mel.weba', kind: 'audio', voiceover_kind: 'take',
    voiceover_lane: 'melody', voiceover_for: original.file };
  message = { ...message, attachments: [...message.attachments, mel] };
  ui._voiceoverRefresh(message);
  f.setType('audio/webm;codecs=opus');
  await record('Geräusche');
  assert.equal(uploads.at(-1).body.type, 'audio/webm;codecs=opus');
  const fx = { file: 'fx.weba', kind: 'audio', voiceover_kind: 'take',
    voiceover_lane: 'effects', voiceover_for: original.file };
  message = { ...message, attachments: [...message.attachments, fx] };
  ui._voiceoverRefresh(message);
  assert.equal(ui.querySelectorAll('.voice-lanes .on').length, 1);
  assert.equal(ui.querySelectorAll('select').length, 0);

  await record('Stimme');
  const v2 = { ...v1, file: 'v2.weba' };
  message = { ...message, attachments: [
    ...message.attachments.filter((a) =>
      !(a.voiceover_kind === 'take' && a.voiceover_for === original.file &&
        a.voiceover_lane === 'voice')), v2
  ] };
  ui._voiceoverRefresh(message);
  assert.equal(MAG.voiceover.latest(message, original.file).voice.file, 'v2.weba',
    'new voice take replaces the previous active lane');
  assert.equal(message.attachments.filter((a) =>
    a.voiceover_kind === 'take' && a.voiceover_lane === 'voice').length, 1,
    'no stale voice attachment in the SSE snapshot');
  assert.equal(ui.querySelectorAll('video').length, 1, 'retake does not add a player');
  find(ui, 'Mix & MP4').click();
  await flush();
  assert.equal(bakes.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(bakes[0].body)), {
    message: message.id, video: original.file, mode: 'mix',
    lanes: { voice: 'v2.weba', melody: 'mel.weba', effects: 'fx.weba' }
  });
  const mix = { file: 'mix.m4a', name: 'mix.m4a', kind: 'audio',
    voiceover_kind: 'mix', voiceover_for: original.file };
  const baked = { file: 'baked.mp4', name: 'baked.mp4', kind: 'video',
    voiceover_kind: 'baked', voiceover_for: original.file };
  message = { ...message, attachments: [...message.attachments, mix, baked] };
  ui._voiceoverRefresh(message);
  assert.equal(ui.querySelectorAll('video').length, 1, 'only ONE video player after bake');
  assert.equal(ui.querySelectorAll('.voice-exports a').length, 2, 'one mix + one MP4 download');
  assert.equal(ui.querySelectorAll('.voice-lanes button').length, 3);

  MAG.state.you = { role: 'guest' };
  const guest = MAG.voiceover.player(message, original);
  assert(!find(guest, 'Aufnehmen') && !find(guest, 'Mix & MP4'));
  assert(find(guest, 'Anhören'), 'guests can hear all three lanes');
  MAG.state.you = { role: 'owner' };
  w.navigator.mediaDevices.getUserMedia = async () => {
    const e = new Error('denied'); e.name = 'NotAllowedError'; throw e;
  };
  const refused = MAG.voiceover.player(message, original);
  d.body.append(refused);
  find(refused, 'Aufnehmen').click();
  await flush();
  assert.match(refused.textContent, /Mikrofon verweigert/);
  assert.equal(uploads.length, 4, 'denial does not save a take');
  f.dom.window.close();

  // Scene cards have TWO independent live keep nodes: the WebGL stage first,
  // the video/recording second. Both must keep exact DOM identity over SSE.
  const sc = fixture(true), ws = sc.w, ds = sc.d;
  let snapshot = null;
  ws.MAG.api = async () => snapshot;
  class Stream {
    static instance = null;
    static OPEN = 1;
    constructor() { this.readyState = 1; Stream.instance = this; }
    close() {}
    event(e) { this.onmessage({ data: JSON.stringify(e) }); }
  }
  ws.EventSource = Stream;
  const glb = { file: 'scene.glb', name: 'scene.glb', kind: 'model', size: 512 };
  const film = { file: 'scene.mp4', name: 'scene.mp4', kind: 'video', size: 1024 };
  const scene = { id: 'abc99999', role: 'service', from: 'scene', ts: Date.now(), state: 'done',
    parts: [], attachments: [glb, film], model3d: { kind: 'scene', state: 'done', cast: [], seconds: 5 } };
  snapshot = { messages: [scene], bots: [], you: { role: 'owner', name: 'Chris' },
    settings: {}, features: {}, seq: 1, more: false };
  ws.MAG.connect();
  Stream.instance.onopen();
  await new Promise((done) => setTimeout(done, 0));
  const mounted = ds.querySelector('.msg[data-id="abc99999"]');
  assert(mounted, 'scene is rendered');
  const canvas = mounted.querySelector('canvas');
  const video = mounted.querySelector('video');
  assert(canvas && video);
  const inline = mounted.querySelector('.voiceover');
  inline.querySelector('button:not(.on)'); // native control exists
  find(inline, 'Aufnehmen').click();
  await flush();
  find(inline, 'Start').click();
  assert.equal(sc.Recorder.last.state, 'recording');
  Stream.instance.event({ t: 'message', seq: 2, m: {
    ...scene, attachments: [...scene.attachments, {
      file: 'oldvoice.m4a', name: 'oldvoice.m4a', kind: 'audio',
      voiceover_kind: 'take', voiceover_for: film.file, voiceover_lane: 'voice'
    }]
  } });
  await flush();
  const after = ds.querySelector('.msg[data-id="abc99999"]');
  assert.strictEqual(after.querySelector('canvas'), canvas, 'scene WebGL stays mounted');
  assert.strictEqual(after.querySelector('video'), video, 'recording video stays mounted');
  assert.strictEqual(after.querySelector('.voiceover'), inline, 'same microphone controller survives SSE');
  assert.equal(sc.Recorder.last.state, 'recording', 'recording remains live');
  assert.equal(sc.stopped, 0, 'SSE did not close microphone');
  assert.equal(after.querySelectorAll('video').length, 1, 'no duplicate baked/take players');
  find(inline, 'Verwerfen').click();
  await flush();
  assert.equal(sc.stopped, 1, 'recording cleanup still works after reconciliation');
  sc.dom.window.close();
  console.log('Voiceover: three compact lanes, mic permission, latest-take selection, mix+bake and multi-keep scene SSE OK');
})().catch((e) => { console.error(e); process.exitCode = 1; });

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
const find = (node, text) => [...node.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') || b.textContent).includes(text));

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
  assert(ui.querySelector('.voice-lanes.seg'), 'use MAG native segmented control');
  assert.equal(ui.querySelectorAll('.voice-actions .icon-btn').length, 4,
    'use existing MAG icon actions including contextual discard');
  assert.equal(ui.querySelectorAll('.voice-controls .pill').length, 0,
    'no crowded pills or separate button styles');
  assert.equal(f.perms.length, 0, 'microphone permission never on mount');
  const record = async (lane) => {
    ui.querySelector('.voice-lanes button[aria-label="' + lane + '"]').click();
    find(ui, 'Mikrofon freigeben').click(); // permission gesture
    await flush();
    assert.equal(f.perms.at(-1).video, false);
    find(ui, 'Aufnahme starten').click(); // active user tap, starts MediaRecorder and video
    assert.equal(f.Recorder.last.state, 'recording');
    assert.equal(ui.querySelectorAll('video').length, 1);
    find(ui, 'Aufnahme stoppen').click();
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
  assert(ui.querySelector('.voice-lanes [aria-label="Stimme"]').classList.contains('has-take'), 'existing take is a discreet dot');

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
  find(ui, 'Mix und MP4 erstellen').click();
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
  assert.equal(ui.querySelectorAll('.voice-exports .pill').length, 0,
    'exports are plain links, not more buttons');
  assert.match(ui.querySelector('.voice-exports').textContent, /Audiomix.*MP4/);
  assert.equal(ui.querySelectorAll('.voice-lanes button').length, 3);

  MAG.state.you = { role: 'guest' };
  const guest = MAG.voiceover.player(message, original);
  assert(!find(guest, 'Mikrofon') && !find(guest, 'Mix und MP4 erstellen'));
  assert(find(guest, 'Mit Tonspuren anhören'), 'guests can hear all three lanes');
  MAG.state.you = { role: 'owner' };
  w.navigator.mediaDevices.getUserMedia = async () => {
    const e = new Error('denied'); e.name = 'NotAllowedError'; throw e;
  };
  const refused = MAG.voiceover.player(message, original);
  d.body.append(refused);
  find(refused, 'Mikrofon freigeben').click();
  await flush();
  assert.match(refused.textContent, /Mikrofon verweigert/);
  assert.equal(uploads.length, 4, 'denial does not save a take');
  f.dom.window.close();

  // A melody song can be a real uploaded File, separately cued on video
  // and song timelines. No microphone permission or second player involved.
  const mf = fixture(), wm = mf.w, dm = mf.d, musicCalls = [], cueCalls = [];
  const track = { file: 'imported.mp3', name: 'music.mp3', kind: 'audio',
    mime: 'audio/mpeg', duration: 2.8, voiceover_kind: 'take', voiceover_lane: 'melody',
    voiceover_for: 'clip.mp4', voiceover_at: 0, voiceover_from: 0 };
  wm.MAG.api = async (method, endpoint, payload) => {
    if (endpoint.startsWith('/api/video/voiceover?')) {
      musicCalls.push({ method, endpoint, payload });
      return track;
    }
    if (endpoint === '/api/video/voiceover/cue') {
      cueCalls.push(payload);
      return { at: payload.at, from: payload.from };
    }
    throw Error('Unexpected melody API request ' + endpoint);
  };
  const clip = { file: 'clip.mp4', name: 'clip.mp4', kind: 'video' };
  let melodyMessage = { id: 'melody999', attachments: [clip] };
  const musicUI = wm.MAG.voiceover.player(melodyMessage, clip);
  dm.body.append(musicUI);
  assert(!find(musicUI, 'Musikdatei hochladen'), 'file upload only in the selected melody lane');
  musicUI.querySelector('.voice-lanes button[aria-label="Melodie"]').click();
  assert(find(musicUI, 'Musikdatei hochladen'), 'one compact file button in melody lane');
  assert.equal(mf.perms.length, 0, 'file upload must never activate microphone');
  const picker = musicUI.querySelector('input[type=file]');
  const file = new wm.File(['X'.repeat(512)], 'cue-melodie.mp3', { type: 'audio/mpeg' });
  Object.defineProperty(picker, 'files', { value: [file], configurable: true });
  picker.dispatchEvent(new wm.Event('change'));
  await flush();
  assert.equal(musicCalls.length, 1);
  assert(musicCalls[0].endpoint.includes('lane=melody&name=cue-melodie.mp3'));
  assert.equal(musicCalls[0].payload, file);
  assert.equal(musicUI.querySelectorAll('video').length, 1);
  assert.equal(musicUI.querySelector('.voice-cues').hidden, false);
  const at = musicUI.querySelector('[aria-label="Melodie ab Videosekunde"]');
  const from = musicUI.querySelector('[aria-label="Song ab Sekunde"]');
  at.value = '0.4';
  at.dispatchEvent(new wm.Event('change'));
  await flush();
  assert.equal(cueCalls[0].at, 0.4);
  assert.equal(cueCalls[0].from, 0);
  from.value = '0.9';
  from.dispatchEvent(new wm.Event('change'));
  await flush();
  assert.equal(cueCalls[1].from, 0.9);
  assert.equal(cueCalls[1].at, 0.4);
  melodyMessage = { ...melodyMessage, attachments: [...melodyMessage.attachments,
    { ...track, voiceover_at: 0.4, voiceover_from: 0.9 }] };
  musicUI._voiceoverRefresh(melodyMessage);
  assert.equal(Number(at.value), 0.4, 'SSE preserves stored video cue');
  assert.equal(Number(from.value), 0.9, 'SSE preserves song in-point');
  const mv = musicUI.querySelector('video');
  const ma = musicUI.querySelector('audio');
  find(musicUI, 'Mit Tonspuren anhören').click();
  await flush();
  mv.currentTime = 0.25;
  mv.dispatchEvent(new wm.Event('timeupdate'));
  assert.equal(ma.paused, true, 'song must not begin before its video cue');
  mv.currentTime = 0.75;
  mv.dispatchEvent(new wm.Event('timeupdate'));
  await flush();
  assert.equal(ma.paused, false);
  assert(Math.abs(ma.currentTime - 1.25) < 0.02,
    'preview must seek into the song: 0.75 video - 0.4 cue + 0.9 song');
  assert.equal(mf.perms.length, 0);
  wm.MAG.state.you = { role: 'guest' };
  const musicGuest = wm.MAG.voiceover.player(melodyMessage, clip);
  assert(!find(musicGuest, 'Musikdatei hochladen'), 'guest has no file-upload control');
  assert.equal(musicGuest.querySelector('.voice-cues'), null,
    'guest has no editable cue fields mounted');
  mf.dom.window.close();

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
  find(inline, 'Mikrofon freigeben').click();
  await flush();
  find(inline, 'Aufnahme starten').click();
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
  find(inline, 'Aufnahme verwerfen').click();
  await flush();
  assert.equal(sc.stopped, 1, 'recording cleanup still works after reconciliation');
  sc.dom.window.close();
  console.log('Voiceover: three compact lanes, mic permission, latest-take selection, mix+bake and multi-keep scene SSE OK');
})().catch((e) => { console.error(e); process.exitCode = 1; });

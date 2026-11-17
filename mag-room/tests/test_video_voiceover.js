#!/usr/bin/env node
'use strict';
// Browser-level microphone permission and recording UI, including iOS MP4
// and Chrome WebM MIME negotiation. No real microphone or device required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const read = (name) => fs.readFileSync(path.join(__dirname, '..', 'web', 'app', name), 'utf8');

(async () => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>',
    { url: 'https://mag.test/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, doc = w.document, uploads = [], bakes = [], permissions = [];
  Object.defineProperty(w, 'isSecureContext', { value: true, configurable: true });
  Object.defineProperty(w.HTMLMediaElement.prototype, 'readyState', { get() { return 4; }, configurable: true });
  Object.defineProperty(w.HTMLMediaElement.prototype, 'duration', { get() { return 5; }, configurable: true });
  w.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  w.HTMLMediaElement.prototype.pause = function () {};
  w.HTMLMediaElement.prototype.load = function () {};
  w.eval(read('core.js'));
  w.MAG.state.you = { role: 'owner' };
  w.MAG.api = async (method, url, body) => {
    if (url.startsWith('/api/video/voiceover')) {
      uploads.push({ method, url, body });
      return { file: 'take.weba' };
    }
    if (url === '/api/video/bake') {
      bakes.push({ method, body });
      return { file: 'baked.mp4' };
    }
    throw Error('unexpected request ' + url);
  };
  let streamsStopped = 0;
  const stream = () => ({
    getAudioTracks: () => [{ kind: 'audio' }],
    getTracks: () => [{ stop() { streamsStopped++; } }]
  });
  w.navigator.mediaDevices = { getUserMedia: async (constraints) => {
    permissions.push(constraints);
    return stream();
  } };
  let mime = 'audio/mp4';
  class Recorder extends w.EventTarget {
    constructor(s, options) {
      super();
      this.stream = s;
      this.mimeType = options.mimeType;
      this.state = 'inactive';
    }
    static isTypeSupported(type) { return type === mime; }
    start() { this.state = 'recording'; }
    stop() {
      assert.equal(this.state, 'recording', 'single click must not stop immediately');
      this.state = 'inactive';
      const event = new w.Event('dataavailable');
      event.data = new w.Blob(['fake m4a file', 'more bytes'], { type: this.mimeType });
      // Server validation is integration-tested separately.
      this.dispatchEvent(event);
      this.dispatchEvent(new w.Event('stop'));
    }
  }
  w.MediaRecorder = Recorder;
  w.eval(read('voiceover.js'));
  const source = { file: 'source.mp4', kind: 'video', name: 'source.mp4' };
  const message = { id: '123abc456def', attachments: [source] };
  const ui = w.MAG.voiceover.player(message, source);
  doc.body.append(ui);
  const find = (words, within = ui) => [...within.querySelectorAll('button')]
    .find((x) => x.textContent.includes(words));
  const flush = async () => { for (let i = 0; i < 15; ++i) await Promise.resolve(); };
  assert.equal(permissions.length, 0, 'no automatic permission on rendering');
  assert.equal(w.MAG.voiceover.supportedMime(), 'audio/mp4');
  assert(find('Mikrofon freigeben'));
  find('Mikrofon freigeben').click();
  await flush();
  assert.equal(permissions.length, 1);
  assert.equal(permissions[0].video, false, 'permission is audio-only');
  const record = find('Aufnahme starten');
  assert.equal(record.disabled, false);
  record.click();
  assert.equal(record.textContent, 'Aufnahme stoppen');
  assert.equal(uploads.length, 0, 'not uploaded while recording');
  record.click();
  await flush();
  assert.equal(uploads.length, 1, 'one click to stop produces exactly one upload');
  assert(uploads[0].body instanceof w.Blob);
  assert(uploads[0].url.includes('message=' + message.id));
  assert(uploads[0].url.includes('video=source.mp4'));
  assert.equal(uploads[0].body.type, 'audio/mp4');
  assert.equal(streamsStopped, 1, 'microphone track closed after stop');
  assert.match(ui.textContent, /gespeichert/);

  // Seeking or speeding up mid-recording must not save a misaligned take.
  find('Mikrofon freigeben').click();
  await flush();
  record.click();
  assert.equal(record.textContent, 'Aufnahme stoppen');
  ui.querySelector('video').dispatchEvent(new w.Event('seeking'));
  await flush();
  assert.equal(uploads.length, 1, 'seeking discards unsynchronized microphone take');
  assert.equal(streamsStopped, 2);

  // Permission can be explicitly relinquished without recording.
  find('Mikrofon freigeben').click();
  await flush();
  find('Mikrofon schließen').click();
  assert.equal(streamsStopped, 3, 'closing mic without recording releases tracks');
  assert.equal(record.disabled, true);

  const take = { file: 'take.m4a', name: 'take.m4a', kind: 'audio',
    voiceover_kind: 'take', voiceover_for: source.file };
  const augmented = w.MAG.voiceover.player({ ...message, attachments: [source, take] }, source);
  doc.body.append(augmented);
  assert(augmented.textContent.includes('Beide Tonspuren'));
  assert(find('Als neues MP4 einbrennen', augmented));
  find('Als neues MP4 einbrennen', augmented).click();
  await flush();
  assert.equal(bakes.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(bakes[0].body)), {
    message: message.id, video: 'source.mp4', audio: 'take.m4a', mode: 'mix'
  });
  assert.match(augmented.textContent, /Original und Mikrofonaufnahme bleiben erhalten/);

  mime = 'audio/webm;codecs=opus';
  assert.equal(w.MAG.voiceover.supportedMime(), 'audio/webm;codecs=opus');
  assert.equal(w.MAG.voiceover.formatOf(mime), 'webm');
  assert.equal(w.MAG.voiceover.formatOf('audio/mp4;codecs=mp4a.40.2'), 'mp4');

  const guest = w.MAG.voiceover.player({ ...message, attachments: [source, take] }, source);
  w.MAG.state.you = { role: 'guest' };
  const view = w.MAG.voiceover.player({ ...message, attachments: [source, take] }, source);
  assert(!find('Mikrofon freigeben', view), 'guests do not receive recording controls');
  assert(!find('Als neues MP4 einbrennen', view), 'guests do not receive bake controls');
  assert(guest.querySelector('video'));

  w.MAG.state.you = { role: 'owner' };
  w.navigator.mediaDevices.getUserMedia = async () => {
    const error = new Error('denied'); error.name = 'NotAllowedError'; throw error;
  };
  const rejected = w.MAG.voiceover.player(message, source);
  doc.body.append(rejected);
  find('Mikrofon freigeben', rejected).click();
  await flush();
  assert.match(rejected.textContent, /Mikrofon verweigert/);
  assert.equal(uploads.length, 1, 'denied permission never saves audio');
  dom.window.close();
  console.log('Voiceover: click-only permission, record/stop once, Safari/Chrome MIME, bake and guest scope OK');
})().catch((err) => { console.error(err); process.exitCode = 1; });

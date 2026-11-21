#!/usr/bin/env node
// The live browser view: one finger, two fingers, and when the phone keyboard
// may open. The rules are the ones in live.js. A headless Chrome pass, when
// Chrome is installed, checks the header at 390px and that a mocked live card
// does not focus the keyboard by itself.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', 'web', 'app');
const liveSrc = fs.readFileSync(path.join(root, 'live.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app.css'), 'utf8');

function el(window, tag, props, ...kids) {
  const node = window.document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k in node && k !== 'list') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
  return node;
}

function installClock(window) {
  let now = 0;
  const q = [];
  let seq = 1;
  window.performance.now = () => now;
  window.setTimeout = (fn, ms = 0) => {
    const id = seq++;
    q.push({ id, fn, at: now + (+ms || 0), interval: false });
    return id;
  };
  window.setInterval = (fn, ms = 0) => {
    const id = seq++;
    const every = +ms || 0;
    q.push({ id, fn, at: now + every, every, interval: true });
    return id;
  };
  const clear = (id) => {
    const i = q.findIndex((t) => t.id === id);
    if (i >= 0) q.splice(i, 1);
  };
  window.clearTimeout = clear;
  window.clearInterval = clear;
  async function advance(ms) {
    const end = now + ms;
    for (let guard = 0; guard < 10000; guard++) {
      q.sort((a, b) => a.at - b.at || a.id - b.id);
      const t = q.find((item) => item.at <= end);
      if (!t) break;
      now = t.at;
      if (t.interval) t.at = now + t.every;
      else q.splice(q.indexOf(t), 1);
      t.fn();
      for (let i = 0; i < 12; i++) await Promise.resolve();
    }
    now = end;
    for (let i = 0; i < 12; i++) await Promise.resolve();
  }
  return { advance, now: () => now };
}

async function boot(opts = {}) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'dangerously' });
  const { window } = dom;
  window.TextEncoder = TextEncoder;
  window.TextDecoder = TextDecoder;
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const clock = installClock(window);
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    const box = this.classList.contains('screen') || this.classList.contains('view');
    const w = box ? 320 : 0, h = box ? 200 : 0;
    return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: h, width: w, height: h, toJSON() { return {}; } };
  };
  window.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
  window.createImageBitmap = async () => ({ width: 1440, height: 900, close() {} });
  if (typeof window.PointerEvent !== 'function') {
    window.PointerEvent = class PointerEvent extends window.MouseEvent {
      constructor(type, init = {}) {
        super(type, init);
        this.pointerId = init.pointerId || 0;
        this.pointerType = init.pointerType || '';
        this.isPrimary = init.isPrimary !== false;
      }
    };
  }
  if (!window.HTMLCanvasElement.prototype.setPointerCapture) window.HTMLCanvasElement.prototype.setPointerCapture = () => {};
  if (!window.HTMLCanvasElement.prototype.releasePointerCapture) window.HTMLCanvasElement.prototype.releasePointerCapture = () => {};
  const fetches = [];
  window.fetch = async (url) => {
    fetches.push(String(url));
    return { ok: true, status: 200, blob: async () => new window.Blob(['jpeg'], { type: 'image/jpeg' }) };
  };
  window.__state = opts.state || {
    url: 'https://example.test/field', editable: true, secret: false,
    field: [160, 80, 200, 40], can_go_back: false, can_go_forward: false,
  };
  const focusLog = [];
  const origFocus = window.HTMLInputElement.prototype.focus;
  window.HTMLInputElement.prototype.focus = function (o) {
    if (this.classList && this.classList.contains('keys')) focusLog.push('keys');
    return origFocus.call(this, o);
  };
  window.fetches = fetches;
  window.MAG = {
    el: (tag, props, ...kids) => el(window, tag, props, ...kids),
    bot: () => ({ name: 'Lena', vm: 'agent3' }),
    api: async () => window.__state,
    fetch: async () => ({ ok: true, json: async () => ({ type: 'answer', sdp: 'v=0' }) }),
    icons: { full: '<svg></svg>', x: '<svg></svg>', dots: '', folder: '', file: '' },
    assets: 't', bytes: (n) => String(n), menu() {}, confirm: async () => false, toast() {}, lightbox() {},
    icon: () => window.document.createElement('span'),
  };
  window.RTCPeerConnection = class {
    constructor() {
      window.lastPC = this;
      this.connectionState = 'connecting';
      this.iceGatheringState = 'new';
      this.ondatachannel = null;
      this.control = null;
    }
    createDataChannel(label) {
      const ch = {
        label, readyState: opts.controlOpen === false ? 'connecting' : 'open', sent: [],
        send(data) { this.sent.push(String(data)); }, close() { this.readyState = 'closed'; },
      };
      Object.defineProperty(ch, 'onopen', { configurable: true, set(fn) { this._onopen = fn; if (this.readyState === 'open' && fn) fn(); } });
      this.control = ch;
      return ch;
    }
    createOffer() { return Promise.resolve({ type: 'offer', sdp: 'v=0' }); }
    async setLocalDescription() { this.iceGatheringState = 'complete'; }
    async setRemoteDescription() {}
    close() { this.connectionState = 'closed'; }
    addEventListener() {}
    removeEventListener() {}
  };
  const script = window.document.createElement('script');
  script.textContent = liveSrc;
  window.document.body.append(script);
  const view = window.MAG.liveView('lena');
  window.document.body.append(view.node);
  const viewBox = view.node.querySelector('.view');
  Object.defineProperty(viewBox, 'clientWidth', { configurable: true, get() { return 320; } });
  Object.defineProperty(viewBox, 'clientHeight', { configurable: true, get() { return 200; } });
  for (let i = 0; i < 40; i++) await Promise.resolve();
  const canvas = view.node.querySelector('canvas.screen');
  const keys = view.node.querySelector('input.keys');
  const lens = view.node.querySelector('button.lens');
  const status = view.node.querySelector('.note');
  const sent = () => (window.lastPC && window.lastPC.control ? window.lastPC.control.sent : []);
  return { window, view, canvas, keys, lens, status, focusLog, clock, fetches, sent };
}

function ptr(window, canvas, type, x, y, id = 1) {
  canvas.dispatchEvent(new window.PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId: id, pointerType: 'touch', isPrimary: id === 1,
    clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' || type === 'pointercancel' ? 0 : 1,
  }));
}
const toClient = (px, py) => [px * 320 / 1440, py * 200 / 900];
function tap(window, canvas, px, py) {
  const [x, y] = toClient(px, py);
  ptr(window, canvas, 'pointerdown', x, y);
  ptr(window, canvas, 'pointerup', x, y);
  return [x, y];
}

(async () => {
  const g = await boot();
  const { onField, keysAction, pinchStep, fingerDrag, pictureFallback } = g.window.MAG.liveGesture;

  // The decision itself: a field tap focuses, a miss closes, and nothing
  // that is not that tap focuses.
  assert.strictEqual(onField({ x: 200, y: 100 }, [160, 80, 200, 40]), true);
  assert.strictEqual(onField({ x: 20, y: 20 }, [160, 80, 200, 40]), false);
  assert.strictEqual(onField({ x: 200, y: 100 }, null), null);
  assert.strictEqual(keysAction({ kind: 'tap', pointerType: 'touch', moved: false, fingers: 1, onField: true }), 'focus');
  assert.strictEqual(keysAction({ kind: 'tap', pointerType: 'touch', moved: false, fingers: 1, onField: false }), 'blur');
  assert.strictEqual(keysAction({ kind: 'tap', pointerType: 'touch', moved: false, fingers: 1, onField: null }), 'none',
    'a tap while no field is known does not open the phone keyboard');
  assert.strictEqual(keysAction({ kind: 'tap', pointerType: 'touch', moved: true, fingers: 1, onField: true }), 'none');
  assert.strictEqual(keysAction({ kind: 'tap', pointerType: 'mouse', moved: false, fingers: 1, onField: true }), 'none');
  for (const kind of ['frame', 'state', 'timer', 'visibility', 'reconnect', 'render', 'drag', 'pinch']) {
    const ev = { kind, editable: true, onField: true };
    assert.strictEqual(keysAction(kind === 'state' ? { kind, editable: true } : ev), kind === 'state' ? 'none' : 'none', kind);
  }
  assert.strictEqual(keysAction({ kind: 'state', editable: false }), 'blur');
  assert.strictEqual(fingerDrag(1), false);
  assert.strictEqual(fingerDrag(2), true);
  const anchor = { d: 40, s: 1, tx: 0, ty: 0, mx: 120, my: 100, L: 0, T: 0 };
  const still = pinchStep(anchor, 40, 120, 130);
  assert.strictEqual(still.scroll, true);
  assert.strictEqual(still.z, 1);
  assert.strictEqual(still.tx, 0);
  assert.strictEqual(still.ty, 0);
  const spread = pinchStep(anchor, 80, 120, 100);
  assert.strictEqual(spread.scroll, false);
  assert.strictEqual(spread.z, 2);
  assert.ok(Math.abs(spread.tx + 120) < 0.01 && Math.abs(spread.ty + 100) < 0.01);
  assert.strictEqual(pictureFallback(false, 1999), 'none');
  assert.strictEqual(pictureFallback(false, 2000), 'https');
  assert.strictEqual(pictureFallback(true, 2000), 'none');
  assert.strictEqual(pictureFallback(true, 5000), 'wait');
  assert(!liveSrc.includes('function shell('), 'the terminal is the PTY, not a separate command field');
  assert(liveSrc.includes("sendConsole('terminal_open')"), 'a missing prompt is asked for again on the UDP shell');
  assert(liveSrc.includes("readyState === 'open'") && liveSrc.includes('e.channel.onopen = arm'), 'an already-open console still starts the shell');
  g.view.stop();

  // No tap: frames, state polls, visibility and a moved card never focus the field.
  const idle = await boot();
  assert.strictEqual(idle.focusLog.length, 0, 'opening the view focuses nothing');
  await idle.clock.advance(12000);
  assert.strictEqual(idle.focusLog.length, 0, 'twelve seconds of state and timers focus nothing');
  const frames = idle.window.lastPC;
  const frameCh = { label: 'frames' };
  frames.ondatachannel({ channel: frameCh });
  const jpeg = new Uint8Array(16);
  new DataView(jpeg.buffer).setUint32(0, 1);
  new DataView(jpeg.buffer).setUint16(6, 1);
  frameCh.onmessage({ data: jpeg.buffer });
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.strictEqual(idle.focusLog.length, 0, 'a frame focuses nothing');
  assert.ok(/live|HTTPS|verbunden|Bild/.test(idle.status.textContent), idle.status.textContent);
  const slot = idle.window.document.createElement('div');
  idle.window.document.body.append(slot);
  slot.append(idle.view.node);
  await idle.clock.advance(3000);
  assert.strictEqual(idle.focusLog.length, 0, 'moving the card focuses nothing');
  assert.notStrictEqual(idle.window.document.activeElement, idle.keys);
  const address = idle.view.node.querySelector('input[aria-label="Adresse"]');
  address.focus();
  assert.notStrictEqual(idle.window.document.activeElement, address, 'redrawing does not focus the address bar');
  idle.window.document.dispatchEvent(new idle.window.Event('visibilitychange'));
  await idle.clock.advance(2000);
  assert.strictEqual(idle.focusLog.length, 0, 'becoming visible focuses nothing');
  idle.view.stop();

  // A tap on the known field focuses once, inside the tap. Later answers do not.
  const field = await boot();
  const before = field.focusLog.length;
  tap(field.window, field.canvas, 200, 100);
  assert.strictEqual(field.focusLog.length, before + 1, 'the tap on the field focuses once');
  assert.strictEqual(field.window.document.activeElement, field.keys);
  field.window.__state = { ...field.window.__state, editable: true, secret: true };
  await field.clock.advance(800);
  assert.strictEqual(field.focusLog.length, before + 1, 'the guest saying it is a field does not focus again');
  assert.ok(field.lens.textContent !== '100%', 'the field is brought up: ' + field.lens.textContent);
  const texts = field.sent().filter((s) => s.includes('"kind":"text"') || s.includes('"kind":"key"') || s.includes('"kind":"pointer"'));
  assert.ok(texts.some((s) => s.includes('"action":"tap"')), texts.join('\n'));
  field.keys.value = 'Hi';
  field.keys.dispatchEvent(new field.window.Event('input', { bubbles: true }));
  field.keys.value = '';
  field.keys.dispatchEvent(new field.window.KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
  field.keys.dispatchEvent(new field.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  const afterType = field.sent().join('\n');
  assert.ok(afterType.includes('"kind":"text"') && afterType.includes('Hi'), afterType);
  assert.ok(afterType.includes('"key":"Backspace"') && afterType.includes('"key":"Enter"'), afterType);
  // The same field, tapped again, may focus once more. A click is not a tap.
  tap(field.window, field.canvas, 200, 100);
  const afterSecond = field.focusLog.length;
  field.canvas.dispatchEvent(new field.window.MouseEvent('click', { bubbles: true, clientX: 44, clientY: 22 }));
  await field.clock.advance(5000);
  assert.strictEqual(field.focusLog.length, afterSecond, 'a click and more state do not focus');
  // Outside the field: close, and do not focus.
  tap(field.window, field.canvas, 20, 20);
  assert.strictEqual(field.focusLog.length, afterSecond, 'a tap off the field does not focus');
  assert.notStrictEqual(field.window.document.activeElement, field.keys, 'a tap off the field closes the keyboard');
  field.view.stop();

  // The guest says the spot is not a field: blur once, then stay blurred.
  const miss = await boot();
  tap(miss.window, miss.canvas, 200, 100);
  assert.strictEqual(miss.focusLog.length, 1);
  miss.window.__state = { url: 'https://example.test/', editable: false, field: null, can_go_back: false, can_go_forward: false };
  await miss.clock.advance(900);
  assert.strictEqual(miss.focusLog.length, 1, 'the answer does not focus');
  assert.notStrictEqual(miss.window.document.activeElement, miss.keys, 'a non-field answer closes the keyboard');
  await miss.clock.advance(8000);
  miss.window.document.dispatchEvent(new miss.window.Event('visibilitychange'));
  assert.strictEqual(miss.focusLog.length, 1, 'it stays closed');
  miss.view.stop();

  // No field anywhere: taps take control of the page, the keyboard stays shut.
  const plain = await boot({ state: { url: 'https://example.test/', editable: false, field: null, can_go_back: false, can_go_forward: false } });
  await plain.clock.advance(100);
  for (const [px, py] of [[200, 100], [700, 400], [1200, 800]]) {
    tap(plain.window, plain.canvas, px, py);
    await plain.clock.advance(1200);
  }
  assert.strictEqual(plain.focusLog.length, 0, 'taps on a page without a field never focus the keyboard');
  assert.notStrictEqual(plain.window.document.activeElement, plain.keys);
  assert.strictEqual(plain.sent().filter((s) => s.includes('"action":"tap"')).length, 3, 'every tap reaches the page');
  // The tap lands on a field the page did not name yet: the guest's answer,
  // right after that tap, offers the keyboard once. The next tap on it
  // focuses from inside the gesture (a phone that kept it shut opens it now).
  plain.window.__state = { url: 'https://example.test/', editable: true, secret: false, field: [160, 80, 200, 40], can_go_back: false, can_go_forward: false };
  tap(plain.window, plain.canvas, 200, 100);
  assert.strictEqual(plain.focusLog.length, 0, 'an unknown spot does not focus inside the tap');
  await plain.clock.advance(800);
  assert.strictEqual(plain.focusLog.length, 1, 'the page focusing the tapped field offers the keyboard');
  assert.strictEqual(plain.window.document.activeElement, plain.keys);
  tap(plain.window, plain.canvas, 200, 100);
  assert.strictEqual(plain.focusLog.length, 2, 'a tap on the known field focuses inside the gesture');
  await plain.clock.advance(5000);
  assert.strictEqual(plain.focusLog.length, 2, 'polls do not focus again');
  plain.view.stop();

  // One finger at 100% does nothing. Magnified, it pans. It never scrolls.
  const hand = await boot();
  const [x0, y0] = toClient(200, 100);
  ptr(hand.window, hand.canvas, 'pointerdown', x0, y0);
  ptr(hand.window, hand.canvas, 'pointermove', x0 + 30, y0);
  ptr(hand.window, hand.canvas, 'pointerup', x0 + 30, y0);
  assert.strictEqual(hand.canvas.style.transform, '', 'a drag at 100% does not pan');
  assert.ok(!hand.sent().some((s) => s.includes('"kind":"wheel"')), 'a one-finger drag does not scroll');
  assert.strictEqual(hand.focusLog.length, 0, 'a drag does not open the keyboard');
  // Pinch and pan together.
  ptr(hand.window, hand.canvas, 'pointerdown', 100, 100, 1);
  ptr(hand.window, hand.canvas, 'pointerdown', 140, 100, 2);
  ptr(hand.window, hand.canvas, 'pointermove', 80, 100, 1);
  ptr(hand.window, hand.canvas, 'pointermove', 160, 100, 2);
  assert.ok(hand.canvas.style.transform.includes('scale(2)'), hand.canvas.style.transform);
  assert.ok(hand.canvas.style.transform.includes('-120px'), hand.canvas.style.transform);
  assert.strictEqual(hand.lens.textContent, '200%');
  assert.strictEqual(hand.focusLog.length, 0, 'a pinch does not open the keyboard');
  hand.lens.click();
  assert.strictEqual(hand.lens.textContent, '100%', 'the percent button fits the page');
  assert.strictEqual(hand.canvas.style.transform, '');
  // At 100%, two fingers that do not spread scroll. The first scroll of a
  // gesture is sent once 30 ms have passed since the page's clock started.
  await hand.clock.advance(40);
  ptr(hand.window, hand.canvas, 'pointerdown', 100, 100, 1);
  ptr(hand.window, hand.canvas, 'pointerdown', 140, 100, 2);
  ptr(hand.window, hand.canvas, 'pointermove', 100, 140, 1);
  ptr(hand.window, hand.canvas, 'pointermove', 140, 140, 2);
  assert.ok(hand.sent().some((s) => s.includes('"kind":"wheel"')), hand.sent().join('\n'));
  assert.strictEqual(hand.lens.textContent, '100%');
  hand.view.stop();

  // No control channel: the picture comes over HTTPS after about two seconds,
  // and showing the page again does not postpone it.
  const https = await boot({ controlOpen: false, state: { url: 'https://example.test/', editable: false, field: null, can_go_back: false, can_go_forward: false } });
  assert.ok(/verbinde/.test(https.status.textContent), https.status.textContent);
  await https.clock.advance(1000);
  https.window.document.dispatchEvent(new https.window.Event('visibilitychange'));
  assert.ok(!https.fetches.some((u) => u.includes('/api/screen')), 'not yet');
  await https.clock.advance(1000);
  assert.ok(https.fetches.some((u) => u.includes('/api/screen/lena')), https.fetches.join(','));
  for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.strictEqual(https.canvas.hidden, false, 'the HTTPS picture is shown');
  assert.ok(/HTTPS/.test(https.status.textContent), https.status.textContent);
  assert.strictEqual(https.focusLog.length, 0, 'the fallback does not open the keyboard');
  https.view.stop();

  if (process.env.MAG_SKIP_CHROME || !fs.existsSync('/usr/bin/google-chrome')) {
    console.log('live view ok (chrome skipped)');
    return;
  }
  await chromePass();
  console.log('live view ok');
})().catch((err) => { console.error(err); process.exit(1); });

function waitFor(url) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      fetch(url).then((r) => r.json()).then(resolve, () => {
        if (Date.now() - start > 8000) reject(new Error('chrome did not open a debugging port'));
        else setTimeout(tick, 100);
      });
    };
    tick();
  });
}

async function chromePass() {
  const dir = fs.mkdtempSync('/tmp/mag-chrome-');
  const port = 9400 + Math.floor(Math.random() * 400);
  const header = path.join(dir, 'header.html');
  const card = path.join(dir, 'card.html');
  fs.writeFileSync(header, `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>
<div id="app"><header id="top"><div id="chips" style="display:contents">
<div class="chip busy" data-bot="lena"><button class="tog" type="button"><span class="dot"></span></button><button class="open" type="button"><span class="name">Lena</span><span class="state">denkt nach</span></button></div>
<div class="chip busy" data-bot="noah"><button class="tog" type="button"><span class="dot"></span></button><button class="open" type="button"><span class="name">Noah</span><span class="state">arbeitet gerade</span></button></div>
</div><span class="spacer"></span>
<button class="icon-btn" type="button"></button><button class="icon-btn" type="button"></button><button class="icon-btn" type="button"></button>
</header></div>`);
  fs.writeFileSync(card, `<!doctype html><meta charset="utf-8"><body>
<script>
window.__out = null;
window.focusLog = [];
const orig = HTMLInputElement.prototype.focus;
HTMLInputElement.prototype.focus = function (o) {
  if (this.classList && this.classList.contains('keys')) window.focusLog.push(1);
  return orig.call(this, o);
};
HTMLElement.prototype.getBoundingClientRect = function () {
  const box = this.classList.contains('screen') || this.classList.contains('view');
  const w = box ? 320 : 0, h = box ? 200 : 0;
  return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: h, width: w, height: h };
};
HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
HTMLCanvasElement.prototype.setPointerCapture = () => {};
const state = { url: 'https://example.test/', editable: true, field: [160, 80, 200, 40], can_go_back: false, can_go_forward: false };
window.MAG = {
  el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k in node && k !== 'list') node[k] = v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
    return node;
  },
  bot: () => ({ name: 'Lena', vm: 'agent3' }),
  api: async () => state,
  fetch: async () => ({ ok: true, json: async () => ({ type: 'answer', sdp: 'v=0' }) }),
  icons: { full: '<svg></svg>', x: '<svg></svg>' }, assets: 't', bytes: String, menu() {}, toast() {},
};
window.RTCPeerConnection = class {
  constructor() { this.connectionState = 'connecting'; this.iceGatheringState = 'complete'; this.ondatachannel = null; }
  createDataChannel() { const ch = { readyState: 'open', sent: [], send() {}, close() {} }; Object.defineProperty(ch, 'onopen', { set(fn) { if (fn) fn(); } }); return ch; }
  createOffer() { return Promise.resolve({ type: 'offer', sdp: 'v=0' }); }
  async setLocalDescription() {}
  async setRemoteDescription() {}
  close() {}
  addEventListener() {}
  removeEventListener() {}
};
window.createImageBitmap = async () => ({ width: 8, height: 8, close() {} });
window.fetch = async () => ({ ok: true, blob: async () => new Blob(['x']) });
</script>
<script>${liveSrc.replace(/<\/script/gi, '<\\/script')}</script>
<script>
(async () => {
  const view = MAG.liveView('lena');
  document.body.append(view.node);
  await new Promise((r) => setTimeout(r, 50));
  const opened = window.focusLog.length;
  await new Promise((r) => setTimeout(r, 1200));
  const afterWait = window.focusLog.length;
  const canvas = view.node.querySelector('canvas');
  const keys = view.node.querySelector('input.keys');
  const x = 200 * 320 / 1440, y = 100 * 200 / 900;
  for (const type of ['pointerdown', 'pointerup']) {
    canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'touch', clientX: x, clientY: y }));
  }
  const afterTap = window.focusLog.length;
  const focused = document.activeElement === keys;
  await new Promise((r) => setTimeout(r, 800));
  window.__out = { opened, afterWait, afterTap, focused, still: window.focusLog.length, active: document.activeElement === keys };
  view.stop();
})().catch((e) => { window.__out = { error: String(e && e.stack || e) }; });
</script>`);
  const proc = spawn('google-chrome', [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
    `--remote-debugging-port=${port}`, `--user-data-dir=${dir}/profile`,
    '--no-first-run', '--disable-extensions', 'about:blank',
  ], { stdio: 'ignore' });
  try {
    const version = await waitFor(`http://127.0.0.1:${port}/json/version`);
    const ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.addEventListener('open', resolve); ws.addEventListener('error', reject); });
    let n = 0;
    const waiters = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
    });
    const send = (method, params = {}, sessionId) => new Promise((resolve) => {
      const id = ++n;
      waiters.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params, sessionId }));
    });
    async function page(file) {
      const created = await send('Target.createTarget', { url: 'about:blank' });
      const attached = await send('Target.attachToTarget', { targetId: created.result.targetId, flatten: true });
      const sessionId = attached.result.sessionId;
      await send('Page.enable', {}, sessionId);
      await send('Runtime.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
      await send('Page.navigate', { url: 'file://' + file }, sessionId);
      return sessionId;
    }
    const headerSession = await page(header);
    await new Promise((r) => setTimeout(r, 300));
    const measured = await send('Runtime.evaluate', {
      expression: `(() => {
        const names = [...document.querySelectorAll('.name')].map((n) => {
          const r = n.getBoundingClientRect();
          return { text: n.textContent, left: r.left, right: r.right, sw: n.scrollWidth, cw: n.clientWidth };
        });
        const icons = [...document.querySelectorAll('.icon-btn')].map((n) => {
          const r = n.getBoundingClientRect();
          return { left: r.left, right: r.right, w: r.width, h: r.height };
        });
        const top = document.querySelector('#top').getBoundingClientRect();
        return { names, icons, scroll: document.documentElement.scrollWidth, inner: window.innerWidth, topRight: top.right, topBottom: top.bottom, state: getComputedStyle(document.querySelector('.state')).display };
      })()`,
      returnByValue: true,
    }, headerSession);
    const shot = await send('Page.captureScreenshot', { format: 'png' }, headerSession);
    fs.mkdirSync('/tmp/mag-live-shots', { recursive: true });
    fs.writeFileSync('/tmp/mag-live-shots/header-390.png', Buffer.from(shot.result.data, 'base64'));
    const box = measured.result.result.value;
    assert.ok(box.inner <= 400, JSON.stringify(box));
    assert.ok(box.scroll <= box.inner + 1, 'header page scrolls sideways: ' + JSON.stringify(box));
    assert.strictEqual(box.state, 'none', 'status hides on a narrow phone');
    for (const name of box.names) {
      assert.ok(name.cw + 1 >= name.sw, name.text + ' is cut: ' + JSON.stringify(name));
      assert.ok(name.right <= box.topRight + 1 && name.left >= -1, name.text + ' is outside the bar: ' + JSON.stringify(box));
    }
    assert.strictEqual(box.icons.length, 3, JSON.stringify(box.icons));
    for (const icon of box.icons) {
      assert.ok(icon.w >= 30 && icon.h >= 30, 'icon shrunk away: ' + JSON.stringify(icon));
      assert.ok(icon.right <= box.topRight + 1 && icon.left >= box.names[1].right - 1, 'icon is not beside the names: ' + JSON.stringify(box));
      assert.ok(icon.right <= box.inner + 1, 'icon is off the phone: ' + JSON.stringify(box));
    }
    const cardSession = await page(card);
    let out = null;
    for (let i = 0; i < 40 && !out; i++) {
      await new Promise((r) => setTimeout(r, 200));
      const ev = await send('Runtime.evaluate', { expression: 'window.__out', returnByValue: true }, cardSession);
      const value = ev.result.result && ev.result.result.value;
      if (value && (value.still !== undefined || value.error)) out = value;
    }
    assert.ok(out && !out.error, JSON.stringify(out));
    assert.strictEqual(out.opened, 0, 'chrome: the card focuses on open');
    assert.strictEqual(out.afterWait, 0, 'chrome: waiting focuses the keyboard');
    assert.strictEqual(out.afterTap, 1, 'chrome: the field tap focuses once');
    assert.strictEqual(out.focused, true, 'chrome: the field has the keyboard');
    assert.strictEqual(out.still, 1, 'chrome: it does not focus again');
    ws.close();
  } finally {
    proc.kill('SIGKILL');
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

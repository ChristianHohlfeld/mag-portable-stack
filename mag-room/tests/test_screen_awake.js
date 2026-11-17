#!/usr/bin/env node
// Screen wake lock contract: PWA default, lifecycle, opt-out and OS denial.
// No real screen/device is controlled by this test.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'wake.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const panels = fs.readFileSync(path.join(__dirname, '..', 'web', 'app', 'panels.js'), 'utf8');
assert(html.indexOf('/app/core.js') < html.indexOf('/app/wake.js'), 'wake manager must follow MAG core');
assert(html.indexOf('/app/wake.js') < html.indexOf('/app/panels.js'), 'wake manager must precede settings');
assert(panels.includes("row('Bildschirm wach halten'"), 'user-visible preference required');
assert(panels.includes('MAG.screenAwake.set(v)'), 'toggle must modify real device wake behavior');

class Events {
  constructor() { this.handlers = new Map(); }
  addEventListener(name, fn) {
    if (!this.handlers.has(name)) this.handlers.set(name, []);
    this.handlers.get(name).push(fn);
  }
  fire(name) { for (const cb of this.handlers.get(name) || []) cb(); }
}

function lock() {
  const events = new Events();
  return {
    released: false,
    addEventListener: (type, cb) => events.addEventListener(type, cb),
    async release() { this.released = true; events.fire('release'); },
  };
}

function mount({ installed = false, stored = null, supported = true, request = null } = {}) {
  const document = Object.assign(new Events(), {
    hidden: false, visibilityState: 'visible', readyState: 'complete',
  });
  const window = Object.assign(new Events(), {
    MAG: {},
    navigator: { standalone: installed },
    matchMedia: () => ({ matches: installed }),
  });
  const locks = [], calls = [];
  const wakeLock = supported ? {
    request: async (type) => {
      calls.push(type);
      if (request) return request(type);
      const sentinel = lock();
      locks.push(sentinel);
      return sentinel;
    },
  } : undefined;
  const navigator = { wakeLock };
  const values = new Map();
  if (stored !== null) values.set('mag-screen-awake', stored);
  const localStorage = {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, value),
  };
  vm.runInNewContext(source, { window, document, navigator, localStorage, Date, Promise });
  return { document, window, locks, calls, values, control: window.MAG.screenAwake };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const pwa = mount({ installed: true });
  await settle();
  assert.equal(pwa.control.enabled(), true, 'installed home-screen PWA should default on');
  assert.equal(pwa.control.active(), true, 'visible PWA must acquire screen lock');
  assert.deepEqual(pwa.calls, ['screen']);

  pwa.document.hidden = true;
  pwa.document.visibilityState = 'hidden';
  pwa.document.fire('visibilitychange');
  await settle();
  assert(pwa.locks[0].released, 'hidden PWA must release lock');
  assert(!pwa.control.active());
  pwa.document.hidden = false;
  pwa.document.visibilityState = 'visible';
  pwa.document.fire('visibilitychange');
  await settle();
  assert.equal(pwa.locks.length, 2, 'PWA must reacquire on foreground');
  assert(pwa.control.active());

  await pwa.control.set(false);
  assert(!pwa.control.enabled());
  assert(!pwa.control.active());
  assert.equal(pwa.values.get('mag-screen-awake'), 'off');
  pwa.document.fire('visibilitychange');
  await settle();
  assert.equal(pwa.locks.length, 2, 'OFF disables future reacquisition');

  assert(await pwa.control.set(true));
  assert(pwa.control.active());
  assert.equal(pwa.values.get('mag-screen-awake'), 'on');
  pwa.window.fire('pagehide');
  await settle();
  assert(!pwa.control.active(), 'navigation must relinquish wake lock');
  pwa.window.fire('pageshow');
  await settle();
  assert(pwa.control.active(), 'returning to PWA resumes wake lock');

  const web = mount();
  await settle();
  assert.equal(web.control.enabled(), false, 'ordinary web tab defaults to no battery drain');
  assert.equal(web.calls.length, 0);
  await web.control.set(true);
  assert(web.control.active(), 'browser can explicitly opt in');
  assert.equal(web.values.get('mag-screen-awake'), 'on');

  const off = mount({ installed: true, stored: 'off' });
  await settle();
  assert(!off.control.enabled(), 'stored device preference overrides PWA default');
  assert.equal(off.calls.length, 0);

  const unsupported = mount({ installed: true, supported: false });
  assert.equal(unsupported.control.supported, false);
  assert.equal(await unsupported.control.set(true), false, 'unsupported API fails gracefully');

  let denied = 0;
  const os = mount({ installed: true, request: async () => {
    ++denied;
    throw new Error('NotAllowedError: low power mode');
  } });
  await settle();
  assert.equal(denied, 1);
  assert(!os.control.active());
  os.document.fire('pointerdown');
  await settle();
  assert.equal(denied, 1, 'OS denial is throttled, not retried on every tap');
  assert.equal(await os.control.set(true), false, 'explicit toggle may retry');
  assert.equal(denied, 2);

  let resolvePending;
  const race = mount({ installed: true, request: () => new Promise(resolve => { resolvePending = resolve; }) });
  await race.control.set(false);
  const abandoned = lock();
  resolvePending(abandoned);
  await settle();
  assert(abandoned.released, 'pending request must be released after opt-out');
  assert(!race.control.active());
  assert(!race.control.enabled());

  console.log('PWA wake lock: lifecycle, opt-out, battery/permission denial and race ok');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

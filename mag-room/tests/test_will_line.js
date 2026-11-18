#!/usr/bin/env node
// The owner's will line is part of the real timeline (messages.js), not a
// copy of the markup. Two bot messages, mock orientation, then a headless
// screenshot of the opened paragraphs.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..');
const coreSrc = fs.readFileSync(path.join(root, 'web/app/core.js'), 'utf8');
const msgSrc = fs.readFileSync(path.join(root, 'web/app/messages.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'web/app/app.css'), 'utf8');

const narrators = {
  'm-lena': {
    narrator: 'Du bist Blake. Du bist nicht Chris und nicht Noah. Über dich gilt: „Blake-Erzähler sichtbar.“ Gerade verfolgst du: „Eine lokale Datei prüfen“.',
    objective: 'Eine lokale Datei prüfen',
  },
  'm-noah': {
    narrator: 'Du bist Noah. Du bist nicht Chris und nicht Blake. Über dich gilt: „Noah-Erzähler sichtbar.“ Gerade verfolgst du: „Eine Notiz schreiben“.',
    objective: 'Eine Notiz schreiben',
  },
};

function message(id, from, step) {
  return {
    id, role: 'bot', from, state: 'done', ts: 1,
    parts: [
      { k: 'text', text: from === 'lena' ? 'Ich prüfe die Datei.' : 'Ich schreibe die Notiz.' },
      step,
    ],
  };
}

const lenaStep = {
  k: 'step', tool: 'shell', state: 'ok', title: 'shell',
  input: { command: 'echo blake-datei' }, output: 'blake-datei\n', t0: 1, t1: 2,
};
const noahStep = {
  k: 'step', tool: 'write_file', state: 'error', title: 'Datei',
  input: { path: '/home/bot/notiz.txt' }, output: 'fehlgeschlagen', t0: 1, t1: 2,
};

function boot(role) {
  const html = `<!doctype html><html><head></head><body>
    <main id="scroll"><div id="list"></div></main>
    <button id="jump" hidden></button>
    <button id="older" hidden></button>
    <div id="empty" hidden></div>
  </body></html>`;
  const dom = new JSDOM(html, { url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'dangerously' });
  const { window } = dom;
  window.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.EventSource = class {
    constructor() {
      this.readyState = 1;
      queueMicrotask(() => { if (this.onopen) this.onopen(); });
    }
    close() { this.readyState = 2; }
  };
  window.EventSource.CLOSED = 2;
  const script = window.document.createElement('script');
  script.textContent = coreSrc + '\n' + msgSrc;
  window.document.body.append(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  const snap = {
    messages: [
      message('m-lena', 'lena', lenaStep),
      message('m-noah', 'noah', noahStep),
    ],
    bots: [{ id: 'lena', name: 'Blake' }, { id: 'noah', name: 'Noah' }],
    settings: { show_self_orientation: false },
    you: { role, name: 'Chris' },
    features: {}, zone: 'bot.stardenburg.de', seq: 1, more: false, token: '', version: 'test', ui: '',
  };
  window.MAG.api = async (_method, reqPath) => {
    const p = String(reqPath);
    if (p.startsWith('/api/state')) return snap;
    if (p.includes('/api/self-orientation')) {
      const id = new URL('https://mag.test' + p).searchParams.get('message');
      return narrators[id] || {};
    }
    return {};
  };
  window.MAG.connect();
  return window;
}

async function settle() {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 10));
    await Promise.resolve();
  }
}

function lines(window) {
  return [...window.document.querySelectorAll('.self-orientation')];
}

(async () => {
  const owner = boot('owner');
  await settle();
  const shown = lines(owner);
  assert.equal(shown.length, 2, 'owner sees one will line per bot');
  const text = shown.map((d) => d.querySelector('summary').textContent);
  assert.ok(text.some((t) => t.includes('Wille · Blake') && t.includes('Eine lokale Datei prüfen') && t.includes('shell') && t.includes('ok')), text.join(' | '));
  assert.ok(text.some((t) => t.includes('Wille · Noah') && t.includes('Eine Notiz schreiben') && t.includes('Datei') && t.includes('fehlgeschlagen')), text.join(' | '));
  for (const d of shown) {
    assert.equal(d.hidden, false, 'the line is not hidden');
    assert.equal(d.open, false, 'the narrator starts folded');
    d.open = true;
  }
  const bodies = shown.map((d) => d.querySelector('.self-orientation-body').textContent);
  assert.ok(bodies.some((t) => t.includes('Blake-Erzähler sichtbar.')), bodies.join('\n'));
  assert.ok(bodies.some((t) => t.includes('Noah-Erzähler sichtbar.')), bodies.join('\n'));

  const guest = boot('local');
  await settle();
  assert.equal(lines(guest).length, 0, 'a non-owner does not get the will line');

  if (process.env.MAG_SKIP_CHROME === '1') {
    owner.close();
    guest.close();
    console.log('will line visible for Blake and Noah; chrome screenshot skipped');
    process.exit(0);
  }
  const chrome = ['google-chrome', 'chromium', 'chromium-browser'].find((bin) => {
    const found = spawnSync('bash', ['-lc', 'command -v ' + bin], { encoding: 'utf8' });
    return found.status === 0 && found.stdout.trim();
  });
  if (!chrome) {
    console.log('will line visible for Blake and Noah; no chrome for a screenshot');
    return;
  }
  for (const d of shown) d.open = true;
  const shotDir = process.env.MAG_WILL_SHOT || '/opt/cursor/artifacts';
  fs.mkdirSync(shotDir, { recursive: true });
  const page = path.join(shotDir, 'will-line-blake-noah.html');
  const png = path.join(shotDir, 'will-line-blake-noah.png');
  const list = owner.document.querySelector('#list').outerHTML;
  fs.writeFileSync(page, `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body style="background:#17181b;margin:24px"><main id="scroll" style="height:auto">${list}</main></body></html>`);
  const shot = spawnSync(chrome, [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--hide-scrollbars',
    '--force-device-scale-factor=2', '--window-size=900,1100', '--screenshot=' + png, 'file://' + page,
  ], { encoding: 'utf8', timeout: 20000 });
  owner.close();
  guest.close();
  if (shot.status !== 0 || !fs.existsSync(png)) {
    throw new Error('screenshot failed: ' + (shot.stderr || shot.stdout));
  }
  console.log('will line visible for Blake and Noah; screenshot ' + png);
  process.exit(0);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});

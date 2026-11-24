#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const root = path.join(__dirname, '..', 'web');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const font = fs.readFileSync(path.join(root, 'static/InterVariable.woff2')).toString('base64');
const css = read('app/app.css').replace('/static/InterVariable.woff2', 'data:font/woff2;base64,' + font);
const bots = ['Blake', 'Noah', 'Qwen'];
const fixture = read('index.html')
  .replace(/<link\b[^>]*>|<script\b[^>]*><\/script>/g, '')
  .replace('</head>', '<style>' + css + '</style></head>')
  .replace('</body>', () => '<script>' + read('app/core.js') + `
    const on = MAG.on;
    MAG.on = (type, listener) => {
      on(type, listener);
      if (type === 'bots') document.addEventListener('DOMContentLoaded', listener);
    };
    MAG.connect = () => {};
    MAG.state.you = { role: 'owner', name: 'Chris' };
    MAG.state.bots = ${JSON.stringify(bots)}.map((name, index) => ({
      id: ['lena', 'noah', 'qwen'][index], name, enabled: true,
      busy: true, phase: 'writing', model: { state: 'ready' }
    }));
    if (location.search) MAG.state.bots[2].name = 'Qwen'.repeat(20);
  </script><script>` + read('app/panels.js') + '</script></body>');

function checkLayout({ width, longName, bots }) {
  const doc = document;
  const box = node => node.getBoundingClientRect();
  const check = (condition, message) => {
    if (!condition) throw new Error(width + 'px: ' + message);
  };
  const header = box(doc.querySelector('#top'));
  const row = doc.querySelector('#chips');
  const chips = [...row.querySelectorAll('.chip')];
  const actions = ['media-filter', 'files', 'clear', 'settings'].map(id => doc.getElementById(id));
  check(chips.length === 3, 'all bot chips render');
  const reachable = node => {
    const rect = box(node);
    return rect.width > 0 && rect.left >= header.left && rect.right <= header.right &&
      node.contains(doc.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  };
  for (const [index, chip] of chips.entries()) {
    const name = chip.querySelector('.name');
    const open = chip.querySelector('.open');
    const stop = chip.querySelector('.chip-stop');
    const toggle = chip.querySelector('.tog');
    check(name.textContent === (longName && index === 2 ? 'Qwen'.repeat(20) : bots[index]),
      'full bot name is rendered');
    check(box(name).left >= box(open).left && box(name).right <= box(stop).left,
      name.textContent + ' fits between the status toggle and stop button');
    if (index) check(box(chips[index - 1]).right <= box(chip).left, 'chips do not overlap');
    if (!longName) check([toggle, open, stop].every(reachable), 'all chip controls are in view');
  }
  check(actions.every(reachable), 'all four header actions are in view');
  for (let index = 1; index < actions.length; index++) {
    check(box(actions[index - 1]).right <= box(actions[index]).left, 'actions do not overlap');
  }
  if (width <= 520) {
    check(box(row).bottom <= box(actions[0]).top, 'actions have a separate row');
    if (longName) {
      check(row.scrollWidth > row.clientWidth, 'long bot names can scroll');
      row.scrollLeft = row.scrollWidth;
      check(reachable(chips[2].querySelector('.chip-stop')), 'last chip control is reachable after scrolling');
      check(actions.every(reachable), 'scrolling chips keeps actions in view');
    } else {
      check(row.scrollWidth === row.clientWidth, 'all three busy chips fit without scrolling');
    }
  } else {
    check(box(chips[0]).right <= box(actions[0]).left &&
      box(chips[0]).top < box(actions[0]).bottom && box(chips[0]).bottom > box(actions[0]).top,
      'desktop keeps chips and actions on one row');
    check(box(chips[0].querySelector('.state')).width > 0, 'desktop shows status text');
  }
}

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome' });
  try {
    const page = await browser.newPage({ serviceWorkers: 'block' });
    await page.route('https://mag.test/**', route => route.fulfill({ contentType: 'text/html', body: fixture }));
    for (const width of [320, 390, 420, 430, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto('https://mag.test/');
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(checkLayout, { width, longName: false, bots });
    }

    const width = 390;
    await page.setViewportSize({ width, height: 800 });
    await page.goto('https://mag.test/?long-name');
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(checkLayout, { width, longName: true, bots });
  } finally {
    await browser.close();
  }
  console.log('chat header layout ok');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

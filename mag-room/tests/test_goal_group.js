#!/usr/bin/env node
// Browser timeline regression: every multi-step task is ONE expandable
// history, with its live browser outside the fold. jsdom also checks a
// 390px mobile content surface (real Chrome layout is a separate smoke run).
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web', 'app');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const messages = read('messages.js').replace(/\}\)\(\);\s*$/, 'window.__testGoalTimeline = {renderAll, upsert, refreshGroup};})();');
assert(messages.includes('__testGoalTimeline'), 'test hooks must load inside the timeline closure');
const html = `<!doctype html><html><head><style>${read('app.css')}</style></head><body>
<main id="scroll" style="width:390px"><button id="older" hidden></button>
<div id="empty" hidden></div><div id="list"></div><div id="activity"></div></main>
<button id="jump" hidden><span></span></button>
<script>
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
</script>
<script>${read('core.js').replace(/<\/script/gi, '<\\/script')}</script>
<script>${messages.replace(/<\/script/gi, '<\\/script')}</script>
</body></html>`;
const dom = new JSDOM(html, { url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'dangerously' });
const { window } = dom, { document, MAG } = window;
if (document.readyState !== 'complete') document.dispatchEvent(new window.Event('DOMContentLoaded'));
const S = MAG.state, test = window.__testGoalTimeline;
assert(test && typeof test.renderAll === 'function', 'timeline test hook not installed');
S.you = { role: 'owner', name: 'Chris' };
S.bots = [{ id: 'lena', name: 'Lena', goal: {id: 'g1', state: 'active', progress: 'running'} }];
MAG.liveView = (bot) => {
  const node = document.createElement('div');
  node.className = 'live'; node.dataset.bot = bot;
  return {node, stop() {}};
};
const t = 1731770000000;
const step = (tool, title, state, started, ended, approval) =>
  ({ k: 'step', tool, title, state, t0: started, t1: ended,
      input: {}, output: '', ...(approval ? { approval } : {}) });
const first = {
  id: '0002', from: 'lena', role: 'bot', goal_id: 'g1',
  ts: t, state: 'streaming', parts: [
    step('browser_open', 'Öffnet eine Seite', 'ok', t, t + 3000),
    step('browser_type', 'Tippt Suchbegriff', 'running', t + 3000, null),
  ], text: '',
};
S.messages.set(first.id, first);
test.renderAll();
let groups = document.querySelectorAll('.goal-group');
assert.strictEqual(groups.length, 1, 'one task -> one group, not two cards');
assert(groups[0].open, 'live task defaults expanded');
assert(groups[0].classList.contains('running'));
assert.strictEqual(groups[0].querySelector('.goal-count').textContent, '2 Schritte');
assert.strictEqual(groups[0].querySelectorAll('.goal-steps > .step').length, 2);
assert.strictEqual(groups[0].querySelectorAll('.goal-steps > .step.current').length, 1);
assert.strictEqual(groups[0].querySelector('.step.current .title').textContent, 'Tippt Suchbegriff');
assert.strictEqual(window.getComputedStyle(groups[0].querySelector('.step:not(.current)')).display, 'none',
                   'while running, old steps must not fill the chat');
assert.strictEqual(document.querySelectorAll('.goal-browser').length, 1);
assert.strictEqual(groups[0].contains(document.querySelector('.goal-browser')), false,
                   'live browser must be available BELOW the foldable step history');
assert(document.querySelector('.goal-browser').open, 'live browser available while working');

// A human approval is inside the current step and must remain actionable.
first.parts.push(step('consent', 'Braucht Freigabe', 'running', t + 4000, null, {
  id: 'approval1', server: 'example', tool: 'write', args: '{}',
}));
test.upsert(first, false);
assert(document.querySelector('.goal-group .step.current.asking .ask'),
       'approval card cannot be hidden by collapsing old work');

// Multiple server messages belonging to the same goal are still one group.
first.state = 'done';
first.parts[1].state = 'ok'; first.parts[1].t1 = t + 6000;
first.parts[2].state = 'ok'; first.parts[2].t1 = t + 7000;
const second = {id: '0003', from: 'lena', role: 'bot', goal_id: 'g1', ts: t + 7000,
  state: 'streaming', parts: [step('browser_scroll','Scrollt auf der Seite','running',t+7000,null)], text: ''};
S.messages.set(second.id, second);
test.upsert(first, false);
test.upsert(second, true);
groups = document.querySelectorAll('.goal-group');
assert.strictEqual(groups.length, 1, 'multi-turn goal must not split into multiple groups');
assert.strictEqual(groups[0].querySelector('.goal-count').textContent, '4 Schritte');
assert.strictEqual(groups[0].querySelectorAll('.goal-steps > .step').length, 4);
assert.strictEqual(groups[0].querySelector('.step.current .title').textContent, 'Scrollt auf der Seite');
assert(document.querySelector('[data-id="0003"]').hidden, 'step-only continuation must not leave a blank message');

// Once finished, the whole history is collapsed, but the result and browser remain.
second.parts[0].state = 'ok'; second.parts[0].t1 = t + 8000;
second.state = 'done';
S.bots[0].goal.state = 'done';
S.bots[0].goal.progress = 'done';
test.upsert(second, false);
test.upsert(first, false);
groups = document.querySelectorAll('.goal-group');
assert.strictEqual(groups.length, 1);
assert.strictEqual(groups[0].open, false, 'finished task auto-collapses');
assert.strictEqual(groups[0].querySelector('.goal-count').textContent, '4 Schritte');
assert(groups[0].querySelector('.goal-duration').textContent, 'duration not visible');
assert(document.querySelector('.goal-browser'), 'collapsed history must not hide browser launcher');
groups[0].querySelector('summary').click();
assert(groups[0].open && groups[0].classList.contains('history-open'), 'history is expandable on tap');
test.upsert(first, false);
assert(document.querySelector('.goal-group').open, 'user expansion survives a refreshed message');

// Mobile: stable narrow layout, no forced minimum widths or hidden live controls.
assert(read('app.css').includes('@media (max-width: 430px)'), 'phone-specific compact group style missing');
assert(read('app.css').includes('.goal-browser-slot > .live { width: 100%; max-width: 100%'),
       'live browser must remain inside available phone width');
assert.strictEqual(document.getElementById('scroll').style.width, '390px');
window.close();
console.log('goal group timeline ok (desktop state + 390px mobile rules)');

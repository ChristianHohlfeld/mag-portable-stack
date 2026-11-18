#!/usr/bin/env node
// What the chat shows of a written-out tool call, and what the + menu attaches.
// tidy() drops an unfenced multi-line call; ordinary JSON inside a sentence stays.
// The attach menu offers the gallery (image types, several files) and a file
// (every type) as two items.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', 'web', 'app');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

const html = `<!doctype html><html><body>
<div id="scroll"><button id="older" hidden></button><div id="empty" hidden></div><div id="list"></div><div id="activity"></div></div>
<button id="jump" hidden></button>
<div id="staged"></div>
<button id="attach"></button>
<textarea id="input"></textarea>
<button id="send"></button>
<script>
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
</script>
<script>${read('core.js').replace(/<\/script/gi, '<\\/script')}</script>
<script>${read('code_editor.js').replace(/<\/script/gi, '<\\/script')}</script>
<script>${read('messages.js').replace(/<\/script/gi, '<\\/script')}</script>
<script>${read('composer.js').replace(/<\/script/gi, '<\\/script')}</script>
</body></html>`;

const dom = new JSDOM(html, { url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'dangerously' });
const { window } = dom;
const { document, MAG } = window;
// jsdom leaves the document at "loading" and does not fire DOMContentLoaded,
// which is when the page wires the attach menu.
if (document.readyState !== 'complete') document.dispatchEvent(new window.Event('DOMContentLoaded'));

const noah = '{\n  "action": "image_generate",\n  "prompt": "a red fox in the snow, watercolor, soft winter light"\n}';
assert.strictEqual(MAG.tidy(noah), '');
assert.strictEqual(MAG.tidy('\n' + noah + '\n'), '');
assert.strictEqual(MAG.tidy('```json\n' + noah + '\n```'), '');
assert.strictEqual(MAG.tidy('Die Zahl ist {"ok": true} und das bleibt so.'), 'Die Zahl ist {"ok": true} und das bleibt so.');
assert.strictEqual(MAG.tidy('Ruf das so: {"action": "image_generate", "prompt": "x"} bitte.'), 'Ruf das so: {"action": "image_generate", "prompt": "x"} bitte.');
const plain = '{\n  "ok": true,\n  "n": 1\n}';
assert.strictEqual(MAG.tidy(plain), plain);
assert.strictEqual(MAG.tidy('Schau:\n' + noah + '\nFertig.'), 'Schau:\nFertig.');
const plan = 'Step 1: Type search term in element 3. Step 2: Press Enter. Step 3: Look at results.';
const scroll = 'Step 1: Scroll down to see more results...';
assert.strictEqual(MAG.tidy('Ich schaue nach.\n' + plan + '\nFertig.'), 'Ich schaue nach.\nFertig.');
assert.strictEqual(MAG.tidy('Weiter im Blick.\n<thought\n' + scroll), 'Weiter im Blick.');
assert.strictEqual(MAG.tidy('Ja.<|channel|>analysis<|message|>Hm.<|end|>Passt.'), 'Ja.Passt.');

document.getElementById('attach').click();
const labels = [...document.querySelectorAll('.menu button')].map((b) => b.textContent.trim());
assert.deepStrictEqual(labels, ['Galerie', 'Datei', 'Kamera', 'Foto zu 3D', 'Bild erzeugen', 'Video erzeugen']);
assert.ok(!labels.includes('Datei oder Foto'));
assert(read('composer.js').includes('cameraRecovery: true'), 'Android losing native capture must offer a usable fallback');
assert(read('composer.js').includes('async function shootInPage'), 'fallback is only exposed after native failure');
assert(read('composer.js').includes("const shoot = (then) => pick('image/*', true, then)"), 'camera uses native capture');


function choose(label) {
  document.querySelectorAll('input[type=file]').forEach((n) => n.remove());
  document.querySelector('.menu')?.remove();
  document.getElementById('attach').click();
  const button = [...document.querySelectorAll('.menu button')].find((b) => b.textContent.trim() === label);
  assert.ok(button, label);
  button.click();
  const input = document.querySelector('input[type=file]');
  assert.ok(input, label + ' opened no file chooser');
  return input;
}
const gallery = choose('Galerie');
assert.strictEqual(gallery.accept, 'image/png,image/jpeg,image/webp');
assert.strictEqual(gallery.multiple, true);
const file = choose('Datei');
assert.strictEqual(file.accept, '');
assert.strictEqual(file.multiple, true);

const index = fs.readFileSync(path.join(root, '..', 'index.html'), 'utf8');
assert(!index.includes('<div id="activity"></div>'), 'thinking must only occupy the header status');
const panels = read('panels.js');
assert(panels.includes("class: 'chip-stop'"), 'stopping a busy bot remains one tap away');
assert(panels.includes("'/api/stop'"), 'header stop must hit backend stop endpoint');

// Self-talk is one compact settings group, not separate conflicting
// "Unbegrenzt" and "Bots untereinander" controls. Every field persists
// through the server and applies identically to either bot.
assert(panels.includes("text: 'Bots miteinander'"), 'both agents share one conversation settings group');
assert(panels.includes("row('Eigenständig reden'"), 'background chat has an explicit opt-in');
assert(panels.includes("toggle(s.auto_chat === true"), 'background chat uses persisted setting');
assert(panels.includes("row('Gesprächslänge'"), 'there is one authoritative round-length selector');
assert(panels.includes("[0, 'Unbegrenzt']"), 'zero means truly unlimited');
assert(panels.includes("bot_rounds: +v"), 'selection updates the backend round limit');
assert(!panels.includes("row('Unbegrenzt unterhalten'"), 'duplicate unlimited toggle was removed');
assert(panels.includes("row('Selbst beenden (PASS)'"), 'voluntary stopping is a separate option');
assert(panels.includes("toggle(s.pass_enabled !== false"), 'PASS setting is shared and persisted');
assert(panels.includes("row('Mitlesen bekannt'"), 'room readership appears in same group');
assert(panels.includes("toggle(s.auto_chat_visible !== false"), 'observer-awareness persists independently');

// Both model-specific thinking variants have a real off switch. When
// effort is enabled for the first time, the displayed "Kurz" is saved,
// not merely a placeholder. Preserve the existing header Stop tests.
assert(!panels.includes("id === 'lena'"), 'thinking UI must not depend on bot identity');
assert(panels.includes("['toggle', 'effort'].includes(b.model?.thinking_control)"),
       'all capable models get the off switch');
assert(panels.includes("b.model?.thinking_control === 'effort'"), 'effort only where supported');
assert(panels.includes("{ effort: b.effort || 'low' }"), 'enabling thinking must persist the visible default');
assert(panels.includes("!b.thinking"), 'effort selector disabled until enabled');
// Execute the real private settings group in jsdom, without exposing it as a global.
const selfProfileSource = panels.slice(panels.indexOf('  function selfProfile(bot)'), panels.indexOf('  // ---- settings: everything'));
const profileCalls = [], profileToasts = [];
let rejectProfile = true;
const fakeCall = async (method, url, body) => {
  profileCalls.push({ method, url, body });
  if (method === 'GET') return { who: 'Original', care: '', pursue: '' };
  if (rejectProfile) { profileToasts.push('failed'); throw Error('offline'); }
};
const makeProfile = new Function('el', 'call', 'owner', selfProfileSource + '\nreturn selfProfile;')(MAG.el, fakeCall, () => true);
async function checkPrivateProfile() {
  const group = makeProfile({ id: 'custom-id', name: 'Renamed bot' });
  document.body.append(group);
  await new Promise(resolve => setTimeout(resolve, 0));
  const input = group.querySelector('input');
  assert.strictEqual(input.value, 'Original');
  input.value = 'New draft';
  input.dispatchEvent(new window.Event('blur'));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.strictEqual(input.value, 'New draft', 'failed save retains typed text');
  assert.strictEqual(profileToasts.length, 1);
  assert(group.textContent.includes('Nicht gespeichert'));
  rejectProfile = false;
  group.querySelector('button').click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.strictEqual(input.value, 'New draft');
  assert.deepStrictEqual(profileCalls.at(-1), { method: 'PUT', url: '/api/bot/custom-id', body: { self: { who: 'New draft' } } });
  input.value = '';
  input.dispatchEvent(new window.Event('blur'));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepStrictEqual(profileCalls.at(-1).body, { self: { who: '' } });
  group.remove();
}
const engineSrc = fs.readFileSync(path.join(root, '..', '..', 'native', 'engine.hpp'), 'utf8');
assert(engineSrc.includes('if (thinking) k["reasoning_effort"]'), 'off must not send effort');
const apiSrc = fs.readFileSync(path.join(root, '..', '..', 'native', 'api.hpp'), 'utf8');
assert(apiSrc.includes('if (v->b && jstr(b, "effort").empty()) b["effort"] = "low"'),
       'old clients must save default effort when turning thinking on');

// Keep ordinary fields usable and private credentials masked. This includes
// the #615 privacy check without losing the prior Stop assertions.
const browserSrc = fs.readFileSync(path.join(root, '..', '..', 'native', 'browser.cpp'), 'utf8');
const privacyExpression = /password|secret|token|one-time-code|cc-(?:number|csc|cvc|exp)|(?:^|[^a-z0-9])(?:pin|otp)(?:code|number|value)?(?:$|[^a-z0-9])/i;
const privacyCamel = /(?:PIN|OTP|Pin|Otp)(?=$|[A-Z0-9])/;
const privateField = (value) => privacyExpression.test(value) || privacyCamel.test(value);
for (const field of ['shipping', 'shopping', 'shippingAddress', 'shoppingCart', 'coupon', 'recipient'])
  assert(!privateField(field), field + ' must remain a normal field');
for (const field of ['password', 'one-time-code', 'cc-number', 'cc-cvc', 'pin', 'user-PIN', 'account_otp', 'securityPIN', 'accountPin', 'otpCode'])
  assert(privateField(field), field + ' must remain private');
assert(!browserSrc.includes('cc-exp|pin|otp/i'), 'no broad substring PIN/OTP rule may remain');
assert(browserSrc.includes('const bool append_mode'), 'typing into focused fields must permit append with proof');


checkPrivateProfile().then(() => {
  window.close();
  console.log('web ui ok');
  process.exit(0);
}, err => { console.error(err); window.close(); process.exit(1); });

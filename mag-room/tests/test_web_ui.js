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

// Browser privacy checks must not reject ordinary fields such as shipping
// or shopping merely because their names contain "pin".
const browserSrc = fs.readFileSync(path.join(root, '..', 'native', 'browser.cpp'), 'utf8');
const privacyExpression = /password|secret|token|one-time-code|cc-(?:number|csc|cvc|exp)|(?:^|[^a-z0-9])(?:pin|otp)(?:$|[^a-z0-9])|(?:PIN|OTP)(?=$|[A-Z0-9])/i;
for (const field of ['shipping', 'shopping', 'shippingAddress', 'shoppingCart', 'coupon', 'recipient'])
  assert(!privacyExpression.test(field), field + ' must remain a normal input');
for (const field of ['password', 'one-time-code', 'cc-number', 'cc-cvc', 'pin', 'user-PIN', 'account_otp', 'securityPIN'])
  assert(privacyExpression.test(field), field + ' must remain private');
assert(!browserSrc.includes('cc-exp|pin|otp/i'), 'no broad substring PIN/OTP rule may remain');
assert(browserSrc.includes('const bool append_mode'), 'typed text may append to a focused field');

window.close();
console.log('web ui ok');
process.exit(0);

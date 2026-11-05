// mag-test-timeout: 60
require('./mag_deadline.cjs').arm(60);
// Node-only regression: no browser/network required to verify playback controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'mag-room/web/index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'mag-room/static/message-audio.js'), 'utf8');
assert(html.includes('<script src="/static/message-audio.js"></script>'), 'audio module not loaded');
assert(html.includes('window.magMessageAudio.attach(audio,text,msg.id,'), 'no per-message speaker action');
assert(!html.includes('id="tts"'), 'old autoplay setting still visible');
assert(!html.includes("$('tts').checked"), 'automatic playback remains');
assert(html.includes('const human=msg.role'), 'render path for human and bot messages absent');
// Every message (user, bot, history) keeps its read-aloud button visible; only copy is newest-only.
assert(!/\.message \.message-tools\{display:none\}/.test(html), 'read-aloud hidden on older messages');
assert(html.includes('.message .message-tools .message-copy{display:none}'), 'copy should stay newest-only');
const renderLine = html.split('\n').find(l => l.includes('window.magMessageAudio.attach(audio,text,msg.id,'));
assert(/^ if\(text\)\{/.test(renderLine), 'speak button must not depend on role/kind, only on text');
const agentUi = fs.readFileSync(path.join(root, 'mag-room/static/agent-ui.js'), 'utf8');
// Tempo is per speaker and sits next to the speaker button, not in settings.
assert(!agentUi.includes("'Vorlesegeschwindigkeit"), 'global speed select must be gone from settings');
assert(source.includes("'Langsamer vorlesen'") && source.includes("'Schneller vorlesen'"), 'per-message tempo control missing');

const buttons = [];
const events = new Map();
let language = 'de';
let cancelled = 0;
const spoken = [];
class FakeUtterance {
  constructor(text) { this.text = text; this.lang = ''; }
}
const rateBoxes = [];
function element(tag) {
  const attrs = {}, kids = [];
  const node = {
    tagName: tag, dataset: {}, classList: {add() {}}, isConnected: true, disabled: false, textContent: '', kids,
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute(k) { return attrs[k]; },
    append(...n) { for (const k of n) { k.parentNode = node; kids.push(k); } },
    after(n) { n.parentNode = node.parentNode; node.nextSibling = n; if (n.className === 'tts-rate' && !rateBoxes.includes(n)) rateBoxes.push(n); },
    querySelector(q) { return kids.find(k => '.' + k.className === q) || null; },
  };
  return node;
}
const toolbar = element('div');
function makeButton() {
  const node = element('button');
  node.parentNode = toolbar;
  buttons.push(node);
  return node;
}
const speechSynthesis = {
  cancel() { cancelled++; },
  speak(u) { spoken.push(u); },
  getVoices() { return [{lang:'de-DE', localService: true, name:'Deutsch'}]; },
};
const window = {
  speechSynthesis, SpeechSynthesisUtterance: FakeUtterance,
  addEventListener(n, cb) { events.set(n, cb); },
};
const document = {
  querySelectorAll(q) { if (q === '.message-speak') return buttons; assert.equal(q, '.tts-rate'); return rateBoxes; },
  getElementById(id) { return id === 'language' ? {value:language} : null; },
  createElement: element, head: element('head'),
};
const store = {}, puts = [];
const fetch = async (url, init) => { if (init && init.method === 'PUT') puts.push(JSON.parse(init.body)); return {ok: true, status: 200, json: async () => ({})}; };
vm.runInNewContext(source, {window, document, navigator: {language:'de-DE'}, SpeechSynthesisUtterance: FakeUtterance, fetch,
  localStorage: {getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }},
  setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask});
const read = window.magMessageAudio;
assert(read.supported);
const bot = makeButton(), human = makeButton();
read.attach(bot, 'Hallo vom Bot', 'bot-1');
read.attach(human, 'Hallo von einem Menschen', 'user-1');
assert.equal(spoken.length, 0, 'audio must NEVER autoplay on message rendering');
assert.equal(bot.getAttribute('aria-label'), 'Nachricht vorlesen');
bot.onclick();
assert.equal(spoken.length, 1);
assert.equal(spoken[0].text, 'Hallo vom Bot');
assert.equal(spoken[0].lang, 'de-DE');
assert.equal(bot.getAttribute('aria-pressed'), 'true');
bot.onclick();
assert.equal(bot.getAttribute('aria-pressed'), 'false', 'second click stops');
assert.equal(spoken.length, 1, 'second click must not start another voice');
human.onclick();
assert.equal(spoken.at(-1).text, 'Hallo von einem Menschen', 'human speech action');
assert.equal(human.getAttribute('aria-pressed'), 'true');
bot.onclick();
assert.equal(human.getAttribute('aria-pressed'), 'false', 'switching cancels prior speech');
assert.equal(bot.getAttribute('aria-pressed'), 'true');
const rerendered = makeButton();
read.attach(rerendered, 'Hallo vom Bot', 'bot-1');
assert.equal(rerendered.getAttribute('aria-pressed'), 'true', 'rerender retains playing state');
spoken.at(-1).onend();
assert.equal(rerendered.getAttribute('aria-pressed'), 'false', 'speech ending resets icon');
assert.equal(spoken.length, 3, 'no speech queued after completion');

language = 'en';
const long = makeButton();
read.attach(long, 'Hello there. '.repeat(120), 'long');
long.onclick();
const countBefore = spoken.length;
for (let i = 0; i < 40 && read.isPlaying('long'); i++) {
  const utterance = spoken.at(-1);
  assert(utterance.text.length <= 170, 'Safari chunks must be short');
  assert.equal(utterance.lang, 'en-US');
  utterance.onend();
}
assert(!read.isPlaying('long'), 'long text does not terminate');
assert(spoken.length > countBefore + 2, 'long text not chunked');
long.onclick();
events.get('pagehide')();
assert(!read.isPlaying('long'), 'page exit must stop speech');
assert(cancelled >= 4, 'stop must cancel the platform queue');

// Tempo control next to every speaker button, one setting per speaker.
language = 'de';
const lena = makeButton(), noah = makeButton();
read.attach(lena, 'Ich bin Lena.', 'm-lena', {bot: 'qwen-numa0', name: 'Lena'});
read.attach(noah, 'Ich bin Noah.', 'm-noah', {bot: 'qwen-numa1', name: 'Noah'});
const lenaBox = lena.nextSibling, noahBox = noah.nextSibling;
assert(lenaBox && lenaBox.className === 'tts-rate' && noahBox && noahBox.className === 'tts-rate', 'tempo control sits right after the speaker button');
read.attach(lena, 'Ich bin Lena. Mehr Text.', 'm-lena', {bot: 'qwen-numa0', name: 'Lena'});
assert.equal(lena.nextSibling, lenaBox, 're-attach keeps a single tempo control');
assert.equal(lenaBox.querySelector('.tts-rate-value').textContent, '1×');
lenaBox.querySelector('.tts-faster').onclick();
assert.equal(read.rateFor('bot:qwen-numa0'), 1.15, 'faster for Lena');
assert.equal(read.rateFor('bot:qwen-numa1'), 1, 'Noah keeps his own tempo');
assert.equal(lenaBox.querySelector('.tts-rate-value').textContent, '1,15×');
assert.equal(noahBox.querySelector('.tts-rate-value').textContent, '1×');
noahBox.querySelector('.tts-slower').onclick();
assert.equal(read.rateFor('bot:qwen-numa1'), 0.85, 'slower for Noah');
assert.deepEqual(puts.slice(-2), [{key: 'bot:qwen-numa0', rate: 1.15}, {key: 'bot:qwen-numa1', rate: 0.85}], 'tempo stored on the server per speaker');
for (let i = 0; i < 20; i++) lenaBox.querySelector('.tts-faster').onclick();
assert.equal(read.rateFor('bot:qwen-numa0'), 2, 'tempo is capped');
assert.equal(lenaBox.querySelector('.tts-faster').disabled, true);
// Default voices: Lena female, Noah male, Chris Thorsten.
assert.equal(read.profile({bot: 'qwen-numa0', name: 'Lena'}).voiceId, 'kerstin');
assert.equal(read.profile({bot: 'x', name: 'Noah'}).voiceId, 'karlsson');
assert.equal(read.profile({user: true}).voiceId, 'thorsten');
console.log('MAG per-message audio: human/bot, manual-only, pause/stop, rerender, Safari chunks PASS');

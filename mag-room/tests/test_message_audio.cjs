// Node-only regression: no browser/network required to verify playback controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'mag-room/web/index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'mag-room/static/message-audio.js'), 'utf8');
assert(html.includes('<script src="/static/message-audio.js"></script>'), 'audio module not loaded');
assert(html.includes('window.magMessageAudio.attach(audio,text,msg.id)'), 'no per-message speaker action');
assert(!html.includes('id="tts"'), 'old autoplay setting still visible');
assert(!html.includes("$('tts').checked"), 'automatic playback remains');
assert(html.includes('const human=msg.role'), 'render path for human and bot messages absent');
// Every message (user, bot, history) keeps its read-aloud button visible; only copy is newest-only.
assert(!/\.message \.message-tools\{display:none\}/.test(html), 'read-aloud hidden on older messages');
assert(html.includes('.message .message-tools .message-copy{display:none}'), 'copy should stay newest-only');
const renderLine = html.split('\n').find(l => l.includes('window.magMessageAudio.attach(audio,text,msg.id)'));
assert(/^ if\(text\)\{/.test(renderLine), 'speak button must not depend on role/kind, only on text');
const agentUi = fs.readFileSync(path.join(root, 'mag-room/static/agent-ui.js'), 'utf8');
assert(agentUi.includes("'Vorlesegeschwindigkeit'"), 'German speed control missing');

const buttons = [];
const events = new Map();
let language = 'de';
let cancelled = 0;
const spoken = [];
class FakeUtterance {
  constructor(text) { this.text = text; this.lang = ''; }
}
function makeButton() {
  const attrs = {};
  const node = {
    dataset: {}, classList: {add() {}}, isConnected: true, disabled: false,
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute(k) { return attrs[k]; },
  };
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
  querySelectorAll(q) { assert.equal(q, '.message-speak'); return buttons; },
  getElementById(id) { return id === 'language' ? {value:language} : null; },
};
vm.runInNewContext(source, {window, document, navigator: {language:'de-DE'}, SpeechSynthesisUtterance: FakeUtterance});
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
console.log('MAG per-message audio: human/bot, manual-only, pause/stop, rerender, Safari chunks PASS');

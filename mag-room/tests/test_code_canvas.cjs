// Heredoc/tool-call extraction of the chat's live code canvas.
const assert = require('assert');
const cc = require('../static/code-canvas.js');

let h = cc.heredoc("cd /home/bot/room && cat > site/index.html <<'EOF'\n<!doctype html>\n<p>x</p>\nEOF\necho ok");
assert.deepStrictEqual([h.path, h.lang, h.text, h.done], ['site/index.html', 'html', '<!doctype html>\n<p>x</p>\n', true]);
// Still streaming: unterminated body is shown up to the last line.
h = cc.heredoc("cat <<EOF > app.js\nconst a = 1;\nconsole.log(a);\n");
assert.deepStrictEqual([h.path, h.lang, h.text, h.done], ['app.js', 'javascript', 'const a = 1;\nconsole.log(a);\n', false]);
h = cc.heredoc("python3 - <<'PY' 2>/dev/null\nprint(1 << 2)\nPY");
assert.deepStrictEqual([h.path, h.lang, h.text], ['', 'python', 'print(1 << 2)\n']);
h = cc.heredoc("tee -a notes.md <<-END\n\t# Titel\n\tEND\n");
assert.deepStrictEqual([h.path, h.lang], ['notes.md', 'markdown']);
assert.strictEqual(cc.heredoc('ls -la && node --check x.js'), null);

let c = cc.toolCode({name: 'write_file', args: {path: '/home/bot/a.html', content: '<h1>Hi</h1>\n'}, result: null});
assert.deepStrictEqual([c.path, c.lang, c.state, c.file], ['/home/bot/a.html', 'html', 'run', true]);
assert.strictEqual(c.key, 'write_file|/home/bot/a.html|12');
c = cc.toolCode({name: 'write_file', args: {path: 'ü.py'}, code_ref: 'f'.repeat(64), code_bytes: 900, result: '', ok: true});
assert.deepStrictEqual([c.text, c.ref, c.lang, c.key], [null, 'f'.repeat(64), 'python', 'write_file|ü.py|900']);
assert.strictEqual(cc.toolCode({name: 'run_command', args: {command: 'ls'}, result: ''}), null);
assert.strictEqual(cc.toolCode({name: 'browser_open', args: {url: 'x'}}), null);
console.log('code canvas ok');

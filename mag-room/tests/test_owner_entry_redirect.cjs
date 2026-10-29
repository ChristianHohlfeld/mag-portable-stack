// mag-test-timeout: 60
require('./mag_deadline.cjs').arm(60);
// Port 8446 is a recovery proxy, not a signed owner ingress.
// Keep the redirect extremely narrow so the independent rescue route remains.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const code = html.match(/<!-- MAG_SERVE_OWNER_REDIRECT_START -->\s*<script>([\s\S]*?)<\/script>\s*<!-- MAG_SERVE_OWNER_REDIRECT_END -->/)?.[1];
assert.ok(code, 'redirect script exists');
function navigation(input) {
  const url = new URL(input);
  const calls = [];
  const location = {
    href: url.href, hostname: url.hostname, port: url.port,
    protocol: url.protocol, search: url.search,
    replace: destination => calls.push(destination)
  };
  vm.runInNewContext(code, { location, URL, URLSearchParams });
  return calls;
}

test('recovery port navigates to owner-authenticated nginx without losing path', () => {
  assert.deepEqual(navigation('https://mag.tail3e02ae.ts.net:8446/api/jobs?view=all#now'),
    ['https://mag.stardenburg.de/api/jobs?view=all#now']);
});
test('recovery mode, other Tailscale ports and canonical domain remain untouched', () => {
  for (const url of [
    'https://mag.tail3e02ae.ts.net:8446/?recovery=1',
    'https://mag.tail3e02ae.ts.net:8445/',
    'https://mag.tail3e02ae.ts.net/',
    'https://mag.stardenburg.de/',
    'http://mag.tail3e02ae.ts.net:8446/'
  ]) assert.deepEqual(navigation(url), [], url);
});

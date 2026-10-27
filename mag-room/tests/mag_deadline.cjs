// mag-test-timeout helper for node contract scripts.
const {spawn} = require('child_process');
const kids = [];
function killAll() {
  for (const kid of kids) {
    try { process.kill(-kid.pid, 'SIGKILL'); } catch (e) { try { kid.kill('SIGKILL'); } catch (e2) {} }
  }
}
function arm(seconds) {
  const timer = setTimeout(() => {
    console.error('mag-test-timeout: exceeded ' + seconds + 's');
    killAll();
    process.exit(124);
  }, seconds * 1000);
  // A finished script must be able to exit. The timer still fires while
  // other handles (a hung request, an open server) keep the process alive.
  if (typeof timer.unref === 'function') timer.unref();
  process.on('exit', killAll);
  process.on('SIGTERM', () => { killAll(); process.exit(124); });
  const orig = spawn;
  require('child_process').spawn = function(cmd, args, opts) {
    opts = Object.assign({detached: true}, opts || {});
    const kid = orig(cmd, args, opts);
    kids.push(kid);
    return kid;
  };
  return timer;
}
module.exports = {arm};

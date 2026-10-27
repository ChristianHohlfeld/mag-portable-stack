"""Real Moonshine WASM/model load in Chromium (no fake model implementation).

Requires: bash ops/vendor-speech-assets.sh build/speech-assets
This is deliberately a separate, slower acceptance smoke. It cannot establish
iPhone Safari performance; it does exercise the shipped ONNX Runtime WASM.
"""
# mag-test-timeout: 600
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(600)
import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mag_pwa_harness_real", HERE / "test_pwa.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
ROOT = harness.ROOT
VENDORED = ROOT / 'build/speech-assets'
AUDIO_MOCK = """(() => {
  const NativeWorker = window.Worker;
  window.Worker = class DebugWorker extends NativeWorker {
    constructor(url,options) {
      super(url,options);
      this.addEventListener('error',e => {
        (window.__speechWorkerErrors ||= []).push({
          message:e.message,filename:e.filename,line:e.lineno,column:e.colno,
          url:String(url)
        });
      });
      this.addEventListener('messageerror',e => {
        (window.__speechWorkerErrors ||= []).push({message:'messageerror'});
      });
    }
  };
  Object.defineProperty(navigator,'mediaDevices',{
    configurable:true,
    value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}
  });
  class FakeAudioContext {
    constructor(){this.sampleRate=16000;this.destination={};this.state='running'}
    resume(){return Promise.resolve()}
    close(){this.state='closed';return Promise.resolve()}
    createMediaStreamSource(){return {connect(){},disconnect(){}}}
    createScriptProcessor(){return {connect(){},disconnect(){}}}
  }
  window.AudioContext=FakeAudioContext;
  window.webkitAudioContext=FakeAudioContext;
  window.AudioWorkletNode=undefined;
})();"""

class RealWasmLoad(unittest.TestCase):
    def test_real_german_small_streaming_worker_loads(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        if not (ROOT / 'build/magroom').is_file():
            self.skipTest('MAG executable not built')
        if not (VENDORED / 'speech-config.json').is_file():
            self.skipTest('Run ops/vendor-speech-assets.sh first')
        cfg = json.loads((VENDORED / 'speech-config.json').read_text())
        with tempfile.TemporaryDirectory(prefix='mag-real-stt-') as temp:
            temp = pathlib.Path(temp)
            static = temp / 'static'
            shutil.copytree(ROOT / 'mag-room/static', static)
            shutil.copytree(ROOT / 'build/terminal-assets', static, dirs_exist_ok=True)
            shutil.copy2(VENDORED / 'speech-config.json', static)
            for name in (cfg['runtime'],cfg['model']):
                (static/name).symlink_to((VENDORED/name).resolve(),target_is_directory=True)
            proc = log = model = None
            pw = browser = context = None
            try:
                proc,log,model,port,_ = harness.start_room(
                    temp, static, 'speech-real-smoke')
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True,args=[
                    '--no-sandbox','--disable-dev-shm-usage'])
                context = browser.new_context()
                context.add_init_script(AUDIO_MOCK)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.on('console', lambda msg: errors.append('console: '+msg.text)
                        if msg.type == 'error' else None)
                page.on('response', lambda resp: errors.append(
                    'HTTP '+str(resp.status)+' '+resp.url)
                    if resp.status >= 400 else None)
                offsite = []
                page.on('request', lambda req: offsite.append(req.url)
                        if not req.url.startswith(f'http://127.0.0.1:{port}/')
                        else None)
                page.goto(f'http://127.0.0.1:{port}/', wait_until='domcontentloaded')
                for terminal_asset in ('xterm.js', 'xterm-addon-fit.js', 'xterm.css'):
                    with urllib.request.urlopen(
                        f'http://127.0.0.1:{port}/static/{terminal_asset}') as resp:
                        self.assertEqual(resp.status, 200)
                        expected_mime = 'text/javascript' if terminal_asset.endswith('.js') else 'text/css'
                        self.assertIn(expected_mime, resp.headers['Content-Type'])
                self.assertTrue(page.evaluate('window.crossOriginIsolated'),
                                'WASM threads need COOP/COEP')
                self.assertEqual(page.locator('#mic').count(), 1)
                page.locator('#mic').click()
                try:
                    page.wait_for_function("""() => {
                      const status = document.querySelector('#speech-status')?.textContent || '';
                      return status.startsWith('Aufnahme läuft') ||
                             status.startsWith('Lokale Spracherkennung:') ||
                             status.includes('Mikrofon nicht freigegeben') ||
                             status.includes('benötigt sichere COOP/COEP-Header');
                    }""",timeout=90000)
                except Exception as err:
                    label = page.locator('#speech-status').inner_text()
                    raise AssertionError('Actual model stalled: '+label+'; '+str(errors)) from err
                label = page.locator('#speech-status').inner_text()
                worker_errors=page.evaluate('window.__speechWorkerErrors || []')
                self.assertTrue(label.startswith('Aufnahme läuft'),
                    'Actual model did not initialize: '+label+'; '+str(errors)+
                    '; worker errors: '+str(worker_errors))
                self.assertEqual(page.locator('#mic').get_attribute('aria-pressed'),'true')
                self.assertFalse(any('/api/stt' in u or 'download.moonshine.ai' in u
                                     or 'cdn.' in u for u in offsite), offsite)
                # A real, browser-owned cache should contain our pinned model files.
                count = page.evaluate("""async () => {
                    let count=0;
                    for(const key of await caches.keys()){
                      const cache=await caches.open(key);
                      for(const request of await cache.keys())
                        if(request.url.includes('/static/speech-') &&
                           request.url.endsWith('.ort'))count++;
                    }
                    return count;
                }""")
                self.assertGreaterEqual(count, 6)
                page.locator('#mic').click()
                page.wait_for_function("""() => document.querySelector('#mic')
                    .getAttribute('aria-pressed') === 'false'""",timeout=30000)
            finally:
                if context: context.close()
                if browser: browser.close()
                if pw: pw.stop()
                if proc: harness.stop_room(proc,log,model)

if __name__ == '__main__':
    unittest.main()

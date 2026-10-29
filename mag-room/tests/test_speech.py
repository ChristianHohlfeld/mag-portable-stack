"""Playwright regression: lazy, fully local MAG speech capture and immutable PWA assets.

Run after building magroom:
  python3 -m unittest discover -s mag-room/tests -p test_speech.py
Real German accuracy and memory use require physical iPhone/Android acceptance tests.
"""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util
import pathlib
import shutil
import tempfile
import unittest
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mag_pwa_harness", HERE / "test_pwa.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

ROOT = harness.ROOT
FAKE_RUNTIME = """export const ModelArch={SmallStreaming:4};
export class SttWorkerHost {
  constructor(){ this.listener=null;globalThis.__speechFake=this }
  async loadTranscriber(config) {
    if(config.modelArch!==4 || config.source.kind!=='urls')
      throw new Error('Wrong model/runtime');
    globalThis.__speechModelFiles=Object.keys(config.source.files);
  }
  async createStream(id,name){this.streamId=name}
  setListener(id,listener){this.listener=listener}
  async start(){this.recording=true}
  async stop(){this.recording=false;
    this.listener?.onLineCompleted({line:{id:'end',text:'und weiter'}})}
  async closeStream(){}
  addAudio(){}
  close(){}
}"""
FAKE_AUDIO = """(() => {
  const tracks=[{stop(){globalThis.__speechTrackStopped=true}}];
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable:true,value:{getUserMedia:async()=>({getTracks:()=>tracks})}
  });
  class FakeAudioContext {
    constructor(){this.state='running';this.sampleRate=16000;this.destination={}}
    resume(){return Promise.resolve()}
    close(){this.state='closed';return Promise.resolve()}
    createMediaStreamSource(){return {connect(){},disconnect(){}}}
    createScriptProcessor(){return {connect(){},disconnect(){},onaudioprocess:null}}
  }
  window.AudioContext=FakeAudioContext;
  window.webkitAudioContext=FakeAudioContext;
  window.AudioWorkletNode=undefined;
})();"""

class BrowserSpeech(unittest.TestCase):
    def setUp(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright not installed')
        self.sync_playwright = sync_playwright
        if not (ROOT / 'build/magroom').is_file():
            self.skipTest('Build magroom before browser test')
        self.tmp = tempfile.TemporaryDirectory(prefix='mag-speech-test-')
        self.work = pathlib.Path(self.tmp.name)
        static = self.work / 'static'
        shutil.copytree(ROOT / 'mag-room/static', static)
        shutil.copytree(ROOT / 'build/terminal-assets', static, dirs_exist_ok=True)
        import json
        (static / 'speech-config.json').write_text(json.dumps({
            'runtime':'speech-0123456789ab',
            'model':'speech-abcdef123456',
            'arch':4
        }))
        (static / 'speech-0123456789ab').mkdir()
        (static / 'speech-0123456789ab/index.js').write_text(FAKE_RUNTIME)
        (static / 'speech-0123456789ab/moonshine.wasm').write_bytes(b'FAKE WASM ASSET')
        (static / 'speech-abcdef123456').mkdir()
        (static / 'speech-abcdef123456/encoder.ort').write_bytes(b'fake model file')
        (static / 'tts-0123456789ab').mkdir()
        (static / 'tts-0123456789ab/ort.wasm').write_bytes(b'FAKE TTS WASM')
        (static / 'tts-0123456789ab/voice.onnx').write_bytes(b'FAKE ONNX')
        self.proc, self.log, self.model, self.port, _ = harness.start_room(
            self.work, static, 'speech-test-a')
        self.pw = self.sync_playwright().start()
        try:
            self.browser = self.pw.chromium.launch(
                headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
        except Exception:
            self.pw.stop()
            harness.stop_room(self.proc, self.log, self.model)
            self.tmp.cleanup()
            self.skipTest('Playwright chromium unavailable')
        self.context = self.browser.new_context()
        self.context.add_init_script(FAKE_AUDIO)
        self.page = self.context.new_page()
        self.requests = []
        self.page.on('request', lambda request: self.requests.append(request.url))
        self.page_errors = []
        self.page.on('pageerror', lambda exc: self.page_errors.append(str(exc)))
        try:
            response = self.page.goto(f'http://127.0.0.1:{self.port}/',
                                      wait_until='domcontentloaded')
            self.assertIsNotNone(response, 'Room document not served')
            self.assertEqual(response.status, 200, 'Room document must return HTTP 200')
            # A first-visit page can install the service worker without
            # becoming controlled until its next navigation. Check the
            # registration explicitly, then reload once; never skip SW tests.
            registration = self.page.evaluate("""async () => {
              if (!('serviceWorker' in navigator)) throw Error('SW API unavailable');
              return await Promise.race([
                navigator.serviceWorker.ready.then(r => ({scope:r.scope,active:!!r.active})),
                new Promise((_,reject)=>setTimeout(()=>reject(Error('SW not ready')),12000))
              ]);
            }""")
            self.assertTrue(registration['active'])
            if not self.page.evaluate('!!navigator.serviceWorker.controller'):
                self.page.reload(wait_until='domcontentloaded')
            self.page.wait_for_function(
                '() => !!navigator.serviceWorker?.controller',
                timeout=12000)
        except Exception as exc:
            # setUp failures do NOT call tearDown: close Chromium/Playwright
            # so a stale event loop cannot poison subsequent sync tests.
            try:
                status = self.page.evaluate("""async () => ({
                  href:location.href,
                  controlled:!!navigator.serviceWorker?.controller,
                  registrations:(await navigator.serviceWorker.getRegistrations())
                    .map(r=>({scope:r.scope,active:r.active?.state})),
                  errors:[]})""")
            except Exception:
                status = {}
            self.context.close()
            self.browser.close()
            self.pw.stop()
            harness.stop_room(self.proc, self.log, self.model)
            self.tmp.cleanup()
            self.context = None
            self.proc = None
            self.tmp = None
            raise AssertionError(f'Speech SW bootstrap failed: {status}; '
                                 f'page errors={self.page_errors}') from exc

    def tearDown(self):
        if getattr(self, 'context', None):
            self.context.close()
            self.browser.close()
            self.pw.stop()
        if getattr(self, 'proc', None):
            harness.stop_room(self.proc, self.log, self.model)
        if getattr(self, 'tmp', None):
            self.tmp.cleanup()

    def test_lazy_worker_partial_final_and_no_server_stt(self):
        self.assertFalse(any('speech-config.json' in x for x in self.requests))
        self.assertFalse(any('download.moonshine.ai' in x for x in self.requests))
        self.assertEqual(self.page.locator('#mic').count(), 1)
        self.page.locator('#message').fill('Vorhandener Text')
        self.page.locator('#mic').click()
        self.page.wait_for_function(
            "() => !!window.__speechFake?.recording && !!window.__speechModelFiles",
            timeout=15000)
        self.assertEqual(self.page.locator('#mic').get_attribute('aria-pressed'), 'true')
        self.assertEqual(len(self.page.evaluate('window.__speechModelFiles')), 8)
        self.page.evaluate("""() => window.__speechFake.listener.onLineTextChanged({
          line:{id:'part',text:'Hallo Welt'}
        })""")
        self.assertEqual(self.page.locator('#message').input_value(),
                         'Vorhandener Text Hallo Welt')
        self.page.evaluate("""() => {
          window.__speechFake.listener.onLineCompleted({line:{id:'part',text:'Hallo Welt'}});
          window.__speechFake.listener.onLineCompleted({line:{id:'part',text:'Hallo Welt'}});
        }""")
        self.page.locator('#mic').click()
        self.page.wait_for_function(
            "() => document.querySelector('#mic').getAttribute('aria-pressed') === 'false'")
        self.assertEqual(self.page.locator('#message').input_value(),
                         'Vorhandener Text Hallo Welt und weiter')
        self.assertTrue(self.page.evaluate('window.__speechTrackStopped'))
        self.assertFalse(any('/api/stt' in x for x in self.requests))
        self.assertFalse(any('download.moonshine.ai' in x or
                             'cdn.jsdelivr.net' in x for x in self.requests))
        self.assertEqual(len([x for x in self.requests if
                              'speech-config.json' in x]), 1)

    def test_hashed_runtime_is_sw_cache_first(self):
        url = '/static/speech-0123456789ab/moonshine.wasm'
        self.page.evaluate('async url => {const r=await fetch(url); if(!r.ok)throw Error(r.status)}', url)
        self.context.set_offline(True)
        result = self.page.evaluate("""async url => {
          const res=await fetch(url);
          return {status:res.status,body:await res.text()};
        }""", url)
        self.assertEqual(result, {'status':200, 'body':'FAKE WASM ASSET'})
        self.context.set_offline(False)

    def test_model_is_not_duplicated_in_sw_cache(self):
        url = '/static/speech-abcdef123456/encoder.ort'
        self.page.evaluate('async url => {const r=await fetch(url); if(!r.ok)throw Error(r.status)}', url)
        cached = self.page.evaluate("""async url => {
          for(const key of await caches.keys()) {
            const cache=await caches.open(key);
            if(await cache.match(url))return key;
          }
          return null;
        }""", url)
        self.assertIsNone(cached)

    def test_tts_runtime_is_cached_and_weights_are_not(self):
        wasm = '/static/tts-0123456789ab/ort.wasm'
        onnx = '/static/tts-0123456789ab/voice.onnx'
        self.page.evaluate('async url => {const r=await fetch(url); if(!r.ok)throw Error(r.status)}', wasm)
        self.page.evaluate('async url => {const r=await fetch(url); if(!r.ok)throw Error(r.status)}', onnx)
        cached = self.page.evaluate("""async onnx => {
          for (const key of await caches.keys()) {
            const cache = await caches.open(key);
            if (await cache.match(onnx)) return key;
          }
          return null;
        }""", onnx)
        self.assertIsNone(cached, 'Piper weights must stay out of the service worker cache')
        self.context.set_offline(True)
        result = self.page.evaluate("""async url => {
          const res = await fetch(url);
          return {status: res.status, body: await res.text()};
        }""", wasm)
        self.assertEqual(result, {'status': 200, 'body': 'FAKE TTS WASM'})
        self.context.set_offline(False)

    def test_rejects_traversal_and_serves_mime(self):
        for url, expected in [
            ('/static/speech-0123456789ab/moonshine.wasm', 'application/wasm'),
            ('/static/speech-0123456789ab/index.js', 'text/javascript'),
        ]:
            with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}{url}') as resp:
                self.assertIn(expected, resp.headers['Content-Type'])
                self.assertEqual(resp.headers.get('Cross-Origin-Embedder-Policy'), 'require-corp')
        for path in ['/static/speech-0123456789ab/notfound/x.js',
                     '/static/speech-zzzzzzzzzzzz/index.js']:
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}{path}')
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/') as resp:
            self.assertEqual(resp.headers.get('Cross-Origin-Opener-Policy'), 'same-origin')
            self.assertEqual(resp.headers.get('Cross-Origin-Embedder-Policy'), 'require-corp')

    def test_coep_keeps_native_event_stream_and_same_origin_images(self):
        # A pthread WASM worker needs isolation on the document itself.
        self.assertTrue(self.page.evaluate("window.crossOriginIsolated"))
        result = self.page.evaluate("""async () => {
          const response = await fetch('/api/state', {cache:'no-store'});
          const image = await new Promise(resolve => {
            const pic = new Image();
            pic.onload = () => resolve(true);
            pic.onerror = () => resolve(false);
            pic.src = '/static/icon-192.png';
          });
          const events = await new Promise(resolve => {
            const source = new EventSource('/api/events');
            let finished = false;
            const done = value => {
              if (finished) return;
              finished = true;
              clearTimeout(timer);
              source.close();
              resolve(value);
            };
            source.onopen = () => done(true);
            source.onerror = () => done(false);
            const timer = setTimeout(() => done(false), 5000);
          });
          return {state:response.status, image, events};
        }""")
        self.assertEqual(result, {'state': 200, 'image': True, 'events': True})

if __name__ == '__main__':
    unittest.main()

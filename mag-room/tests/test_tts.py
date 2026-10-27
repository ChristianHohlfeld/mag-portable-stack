"""Read-aloud: system-voice fallback prefers Natural/Premium/Siri/Enhanced
voices; with staged Piper assets (MAG_TTS_ASSETS=build/tts-assets) a real
German sentence is synthesized in the worker and the voice is cached."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util,os,pathlib,shutil,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('test_pwa',ROOT/'mag-room/tests/test_pwa.py');pwa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pwa)
class TTS(unittest.TestCase):
 def test_fallback_and_piper(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  tmp=pathlib.Path('/tmp/mag-tts-ui')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  assets=os.environ.get('MAG_TTS_ASSETS')
  if assets:shutil.copytree(assets,static,dirs_exist_ok=True)
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'tts')
  pw=sync_playwright().start();browser=pwa.launch_chromium(pw,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required'])
  try:
   page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
   pick=page.evaluate("""()=>{const fake=[{name:'Anna',lang:'de-DE',localService:true},{name:'Google Deutsch',lang:'de-DE',localService:false},
     {name:'Petra (Premium)',lang:'de-DE',localService:true},{name:'Samantha',lang:'en-US',localService:true}];
     speechSynthesis.getVoices=()=>fake;return [magMessageAudio.bestVoice('de-DE').name,magMessageAudio.bestVoice('de-AT').name,magMessageAudio.bestVoice('en-US').name]}""")
   self.assertEqual(pick,['Petra (Premium)','Petra (Premium)','Samantha'])
   if not assets:
    self.assertEqual(page.evaluate("async()=>(await magMessageAudio.voiceOptions()).map(v=>v.id)"),['system'])
    return
   self.assertEqual(page.evaluate("async()=>(await magMessageAudio.voiceOptions()).map(v=>v.id)"),['thorsten','kerstin','system'])
   def measure(voice, text):
    return page.evaluate("""async({voice,text})=>{const cfg=await (await fetch('/static/tts-config.json')).json();const v=cfg.voices.find(x=>x.id===voice);
      const w=new Worker('/static/'+cfg.dir+'/tts-worker.js');
      const once=pred=>new Promise((ok,bad)=>{const on=e=>{if(e.data.type==='error'){w.removeEventListener('message',on);bad(new Error(e.data.error))}
        else if(pred(e.data)){w.removeEventListener('message',on);ok(e.data)}};w.addEventListener('message',on)});
      const warmP=once(m=>m.type==='ready'); const tWarm=performance.now();
      w.postMessage({type:'warm',id:1,model:v.model}); await warmP; const warmMs=performance.now()-tWarm;
      const audioP=once(m=>m.type==='audio'&&m.id===2); const t0=performance.now();
      w.postMessage({type:'speak',id:2,text,model:v.model}); const audio=await audioP;
      return {samples:audio.audio.length,rate:audio.rate,synthMs:audio.synthMs,wallMs:performance.now()-t0,warmMs};
    }""", {'voice': voice, 'text': text})
   sentence='Hallo Chris, das ist die neue Vorlesestimme.'
   for voice in ('thorsten','kerstin'):
    n=measure(voice, sentence)
    self.assertGreater(n['samples'], n['rate']*1.0, voice)  # more than one second of audio
    self.assertIsInstance(n['synthMs'], int)
    self.assertLess(n['wallMs'], 20000, voice)
    print('TTS %s hot-start %.0f ms (synth %s ms, warm %.0f ms, audio %.2f s)'%(
      voice, n['wallMs'], n['synthMs'], n['warmMs'], n['samples']/n['rate']))
   long=measure('thorsten', 'Dies ist ein längerer Absatz ohne Punkt. '*8)
   print('TTS thorsten paragraph hot-start %.0f ms (synth %s ms, audio %.2f s)'%(
     long['wallMs'], long['synthMs'], long['samples']/long['rate']))
   cached=page.evaluate("async()=>(await (await caches.open('mag-tts-v1')).keys()).map(r=>r.url.split('/').pop())")
   self.assertIn('de_DE-thorsten-medium.onnx',cached)
   self.assertEqual(errors,[])
  finally:
   pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)

"""Read-aloud: system-voice fallback prefers Natural/Premium/Siri/Enhanced
voices; with staged Piper assets (MAG_TTS_ASSETS=build/tts-assets) a real
German sentence is synthesized in the worker and the voice is cached."""
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
  pw=sync_playwright().start();browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required'])
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
   for voice in ('thorsten','kerstin'):
    n=page.evaluate("""async(voice)=>{const cfg=await (await fetch('/static/tts-config.json')).json();const v=cfg.voices.find(x=>x.id===voice);
      const w=new Worker('/static/'+cfg.dir+'/tts-worker.js');
      return await new Promise((ok,bad)=>{w.onmessage=e=>{if(e.data.type==='audio')ok([e.data.audio.length,e.data.rate]);if(e.data.type==='error')bad(e.data.error)};
        w.postMessage({type:'speak',id:1,text:'Hallo Chris, das ist die neue Vorlesestimme.',model:v.model})})}""",voice)
    self.assertGreater(n[0],n[1]*1.0,voice)  # more than one second of audio
   cached=page.evaluate("async()=>(await (await caches.open('mag-tts-v1')).keys()).map(r=>r.url.split('/').pop())")
   self.assertIn('de_DE-thorsten-high.onnx',cached)
   self.assertEqual(errors,[])
  finally:
   pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)

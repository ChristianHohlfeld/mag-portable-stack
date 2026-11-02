"""MAG coding auto-deploy integration contracts (offline)."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)

import hashlib, importlib.util, json, pathlib, subprocess, tempfile, unittest
ROOT = pathlib.Path(__file__).resolve().parents[3]
C = ROOT / "ops/coding"
def source(path): return (ROOT / path).read_text()

class CodingDeploy(unittest.TestCase):
 def test_shell_syntax(self):
  for p in ["ops/coding/prepare.sh","ops/coding/desired.sh","ops/coding/start-live.sh",
            "ops/coding/layout.sh","ops/qwen/layout.sh","ops/deploy.sh","ops/auto-update/apply.sh"]:
   r=subprocess.run(["bash","-n",str(ROOT/p)],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,p+": "+r.stderr)
 def test_model_pins(self):
  data=json.loads(source("ops/coding/models.json"))
  self.assertEqual(data["coding_output_tokens"],16384)
  self.assertEqual(data["context_tokens"],65536)
  self.assertEqual([data["models"][i]["numa"] for i in ("general","coder")],[0,1])
  self.assertEqual(source("ops/room/layout").strip(),"coding")
  self.assertIn("MAG_ROOM_MAX_TOKENS=16384",source("ops/coding/80-coding-output.conf"))
  self.assertIn("MAG_ROOM_MODEL_ID_1=qwen3-coder-30b-a3b-instruct",source("ops/room/coding.env"))
 def test_stage_before_stop(self):
  auto=source("ops/auto-update/apply.sh")
  self.assertLess(auto.index("coding/prepare.sh"),auto.index("ops/deploy.sh"))
  self.assertIn('exec bash "$ROOT/ops/coding/layout.sh"',source("ops/qwen/layout.sh"))
  layout=source("ops/coding/layout.sh")
  self.assertLess(layout.index('stage.py" --model both'),layout.index('systemctl stop mag-llm-glm@q2'))
  self.assertIn('wait_ready mag-llm-glm@q2.service 8192',layout)
  self.assertIn('wait_ready mag-llm-mistral-room.service 8191',layout)
  self.assertIn('coding/80-coding-output.conf',layout)
  self.assertIn('qwen-layout/coding',source("ops/deploy.sh"))
  self.assertIn('mag-llm-coding@1.service',source("ops/boot/ensure-boot.sh"))
 def test_unhealthy_old_model_must_not_block_replacement(self):
  src=source("ops/coding/layout.sh")
  self.assertIn("old :$port is unhealthy; continuing with staged replacement",src)
  self.assertNotIn("fail 'old Mistral not running'",src)
  self.assertNotIn("fail 'old NUMA0 unhealthy'",src)
  self.assertNotIn("fail 'old NUMA1 unhealthy'",src)
  self.assertIn("/etc/mag-stack/room-layout.pinned",src)
  self.assertIn('rm -f /etc/mag-stack/room-layout.pinned',src)
  self.assertIn('wait_ready mag-llm-coding@0.service 8191 360',src)
  self.assertIn('wait_ready mag-llm-coding@1.service 8192 360',src)
 def test_operator_pin_migration(self):
  script=source("ops/coding/desired.sh")
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/"pinned"
   script=script.replace("/etc/mag-stack/room-layout.pinned",str(p))
   runner=pathlib.Path(d)/"desired.sh"
   runner.write_text(script)
   for pin,env,expected in [
      ("glm",{}, "coding"),
      ("mixed",{}, "mixed"),
      ("glm",{"MAG_ROOM_LAYOUT":"glm"},"glm"),
      ("glm",{"MAG_ROOM_LAYOUT":"repo"},"coding")]:
    p.write_text(pin+"\\n")
    import os
    proc=subprocess.run(["bash",str(runner)],cwd=ROOT,
       env={**os.environ,**env},text=True,capture_output=True,timeout=10)
    # Script resolves root relative to its location; point at source tree.
    self.assertEqual(proc.returncode,0,proc.stderr)
    self.assertEqual(proc.stdout.strip(),expected)
 def test_stock_names_renamed_only_after_model_change(self):
  room=source("mag-room/native/magroom.cpp")
  self.assertIn('previous_model == "mistral-large4" && old == "Mistral"',room)
  self.assertIn('previous_model == "glm-5.3-flash" && old == "GLM-Q2"',room)
  self.assertIn('a["name"] = backend.label',room)
  ui=source("mag-room/web/index.html")
  self.assertNotIn("selected.has(a.id)?'antwortet':'pausiert'",ui)
  self.assertIn("selected.has(a.id)?'eingeschaltet':'pausiert'",ui)
 def test_verified_file(self):
  spec=importlib.util.spec_from_file_location("coding_stage",C/"stage.py")
  mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t)/"test.gguf";p.write_bytes(b"good")
   sha=hashlib.sha256(b"good").hexdigest()
   self.assertTrue(mod.verified_file(p,sha))
   p.write_bytes(b"bad");self.assertFalse(mod.verified_file(p,sha))
if __name__=="__main__": unittest.main()

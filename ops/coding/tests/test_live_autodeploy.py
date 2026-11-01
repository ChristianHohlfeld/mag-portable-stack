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
 def test_verified_file(self):
  spec=importlib.util.spec_from_file_location("coding_stage",C/"stage.py")
  mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t)/"test.gguf";p.write_bytes(b"good")
   sha=hashlib.sha256(b"good").hexdigest()
   self.assertTrue(mod.verified_file(p,sha))
   p.write_bytes(b"bad");self.assertFalse(mod.verified_file(p,sha))
if __name__=="__main__": unittest.main()

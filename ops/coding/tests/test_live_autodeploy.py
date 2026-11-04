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
  self.assertEqual(data["context_tokens"],262144)
  self.assertEqual([data["models"][i]["numa"] for i in ("general","coder")],[0,1])
  self.assertEqual(source("ops/room/layout").strip(),"coding")
  self.assertIn("MAG_ROOM_MAX_TOKENS=16384",source("ops/coding/80-coding-output.conf"))
  self.assertIn("MAG_ROOM_MODEL_ID_1=flint",source("ops/room/coding.env"))
 def test_stage_before_stop(self):
  auto=source("ops/auto-update/apply.sh")
  self.assertLess(auto.index("coding/prepare.sh"),auto.index("ops/deploy.sh"))
  self.assertIn('exec bash "$ROOT/ops/coding/layout.sh"',source("ops/qwen/layout.sh"))
  layout=source("ops/coding/layout.sh")
  self.assertLess(layout.index('stage.py" --model general'),layout.index('systemctl stop mag-llm-mistral-room'))
  self.assertLess(layout.index('stage.py" --model coder'),layout.index('systemctl stop mag-llm-glm@q2'))
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
  self.assertIn('wait_ready mag-llm-coding@0.service 8191 1440',src)
  self.assertIn('wait_ready mag-llm-coding@1.service 8192 1440',src)
 def test_operator_pin_migration(self):
  script=source("ops/coding/desired.sh")
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/"pinned"
   script=script.replace("/etc/mag-stack/room-layout.pinned",str(p))
   script=script.replace('root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"', 'root="'+str(ROOT)+'"')
   runner=pathlib.Path(d)/"desired.sh"
   runner.write_text(script)
   for pin,env,expected in [
      ("glm",{}, "coding"),
      ("mixed",{}, "mixed"),
      ("glm",{"MAG_ROOM_LAYOUT":"glm"},"glm"),
      ("glm",{"MAG_ROOM_LAYOUT":"repo"},"coding")]:
    p.write_text(pin+chr(10))
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
 def test_coding_room_long_replies_and_chat_only(self):
  room=source("mag-room/native/magroom.cpp")
  self.assertIn('static bool long_reply_model(const std::string &model)',room)
  self.assertIn('const bool glm_generation = long_reply_model(model);',room)
  self.assertIn('const bool glm_generation_turn = long_reply_model(bot_model_id(bot_idx));',room)
  self.assertIn('coding_room_model(model);\n}',room)
  self.assertIn('? MAX_TOKENS // one round; longer answers continue (no total cap)',room)
  self.assertIn('if (coding_model) glm_stops.pop_back();',room)
  env=source("ops/room/coding.env")
  self.assertIn("MAG_ROOM_MODEL_ID_0=flint-numa0",env)
  self.assertIn('CODING_FLINT0_MODEL = "flint-numa0"',room)
  self.assertIn('CODING_GENERAL_MODEL = "qwen3.8-flash-next"',room)
  self.assertIn('CODING_CODER_MODEL = "flint"',room)
 def test_node_scoped_interim(self):
  layout=source("ops/coding/layout.sh")
  self.assertIn('target_env="$ROOT/ops/room/coding0.env"',layout)
  self.assertIn("GLM-Q2 must stay active on NUMA1",layout)
  self.assertLess(layout.index("systemctl stop mag-llm-mistral-room.service"),layout.index("if (( coder_ready )) && ! systemctl is-active --quiet mag-llm-coding@1.service"))
  env=source("ops/room/coding0.env")
  self.assertIn("MAG_ROOM_MODEL_ID_0=qwen3.8-flash-next",env)
  self.assertIn("MAG_ROOM_MODEL_ID_1=glm-5.3-flash",env)
  import json as _j
  ids=[[b["model"] for b in x["ports"]] for x in _j.loads(source("ops/room/presets.json")) if all("model" in b for b in x["ports"])]
  self.assertIn(["qwen3.8-flash-next","glm-5.3-flash"],ids)
  self.assertIn(["flint-numa0","flint"],ids)
  boot=source("ops/boot/ensure-boot.sh")
  self.assertIn("Interim coding room: Qwen3.8 NUMA0; GLM-Q2 boot state is never altered.",boot)
  room=source("mag-room/native/magroom.cpp")
  self.assertIn('ROOM_MODEL.backend(1).id == "qwen3.8-flash-next") &&',room)
  self.assertIn('if (coding_room_model(bot_model_id(idx))) return false;',room)
 def test_link_from_never_duplicates(self):
  spec=importlib.util.spec_from_file_location("coding_stage",C/"stage.py")
  mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  with tempfile.TemporaryDirectory() as t:
   t=pathlib.Path(t); src=t/"src.gguf"; src.write_bytes(b"model")
   sha=hashlib.sha256(b"model").hexdigest()
   mod.MANIFEST={"models":{"general":{"filename":"x.gguf","sha256":sha,"repo":"a/b","link_from":str(src)}}}
   mod.stage("general",t/"root",download=False)
   dst=t/"root/general/x.gguf"
   self.assertEqual(dst.stat().st_ino,src.stat().st_ino)
   src2=t/"bad.gguf"; src2.write_bytes(b"partial")
   mod.MANIFEST["models"]["general"]["link_from"]=str(src2)
   mod.MANIFEST["models"]["general"]["filename"]="y.gguf"
   with self.assertRaises(RuntimeError): mod.stage("general",t/"root",download=True)
   self.assertFalse((t/"root/general/y.gguf.part").exists())
 def test_numa1_only_flint_swap(self):
  layout=source("ops/coding/layout.sh")
  # Only nodes whose models.json entry changed restart; NUMA0 Qwen3.8 stays.
  snippet=layout.split("<<'PY'\n",2)[1].split("\nPY\n",1)[0]
  import subprocess,tempfile,os,copy
  new=json.loads(source("ops/coding/models.json"))
  old=copy.deepcopy(new); old["models"]["coder"]["model_id"]="qwen3-coder-next"
  with tempfile.TemporaryDirectory() as d:
   a,b=os.path.join(d,"n.json"),os.path.join(d,"o.json")
   json.dump(new,open(a,"w")); json.dump(old,open(b,"w"))
   out=subprocess.run(["python3","-I","-",a,b],input=snippet,text=True,capture_output=True,check=True).stdout
   self.assertEqual(out.split(),["1"])
   json.dump(new,open(b,"w"))
   out=subprocess.run(["python3","-I","-",a,b],input=snippet,text=True,capture_output=True,check=True).stdout
   self.assertEqual(out.split(),[])
  self.assertIn("new NUMA1 coder GGUF not staged; current pair left running",layout)
  self.assertLess(layout.index('touch "$TXN/started"'),layout.index('if (( swap )); then\n  touch "$TXN/switch-started"'))
  self.assertIn("/usr/local/lib/mag-coding/runtime-coder.sha\n",layout.split("wait_port_free()")[0])
  self.assertIn('install -m 0644 "$ROOT/ops/coding/runtime.sha" /usr/local/lib/mag-coding/runtime-coder.sha',layout)
  self.assertIn('install -m 0644 "$ROOT/ops/qwen/runtime.sha" /usr/local/lib/mag-coding/runtime.sha',layout)
  start=source("ops/coding/start-live.sh")
  self.assertIn('DIR="/home/chris/.local/mag-coding-rt/$REV/bin"',start)
  self.assertIn("alias=flint; file=Flint-Q4_K_M.gguf",start)
  self.assertIn("alias=flint-numa0; file=Flint-Q4_K_M.gguf",start)
  # Chat without thinking: Flint template override, installed with the launcher.
  self.assertIn('TEMPLATE_ARGS=(--chat-template-file "$TPL")',start)
  self.assertIn('install -m 0644 "$ROOT/ops/coding/flint-chat.jinja" /usr/local/lib/mag-coding/flint-chat.jinja',layout)
  tpl=source("ops/coding/flint-chat.jinja")
  self.assertIn("{%- if enable_thinking is defined and enable_thinking is false -%}",tpl)
  self.assertIn("FLINT",source("ops/coding/build-runtime.sh"))
  self.assertRegex(source("ops/coding/runtime.sha").strip(),r"^[a-f0-9]{40}$")
  self.assertEqual([new["models"][i]["model_id"] for i in ("general","coder")],["flint-numa0","flint"])
  room=source("mag-room/native/magroom.cpp")
  # No per-round token cap on either coding bot (Flint reasons every turn; Chris: no caps).
  self.assertIn("const bool uncapped = coding_model && !TL_SELFTALK;",room)
  self.assertIn('if (!uncapped) body["max_tokens"] = max_output;',room)
if __name__=="__main__": unittest.main()

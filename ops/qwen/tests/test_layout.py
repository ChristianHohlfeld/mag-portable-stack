"""Run the deploy transaction against an isolated filesystem and fake systemd."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import hashlib, json, os, pathlib, shutil, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
class Layout(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.base=pathlib.Path(self.tmp.name)
  self.repo=self.base/'repo'
  for name in ('qwen','room','glm'):
   shutil.copytree(ROOT/'ops'/name,self.repo/'ops'/name,symlinks=True)
  # The repo-declared layout is set per test; the legacy cases run on mixed.
  (self.repo/'ops/room/layout').write_text('mixed\n')
  shutil.copy2(ROOT/'ops'/'mag-units.sh',self.repo/'ops'/'mag-units.sh')
  self.host=self.base/'host';self.bin=self.base/'bin';self.bin.mkdir()
  helper=self.repo/'ops/qwen/layout.sh';s=helper.read_text().replace("[[ $EUID == 0 ]] || fail 'root required'",'')
  for path in ('/etc/','/usr/local/','/home/chris/'):
   s=s.replace(path,str(self.host)+path)
  helper.write_text(s)
  self.write('/etc/mag-stack/room-model.env',(ROOT/'ops/room/model.env').read_text())
  self.write('/etc/systemd/system/mag-llm-ds-node@.service','old-ds-unit\n')
  rev=(ROOT/'ops/qwen/runtime.sha').read_text().strip()
  self.write('/home/chris/.local/qwen4exp/'+rev+'-mag-cp1/bin/llama-server','binary')
  (self.host/('home/chris/.local/qwen4exp/'+rev+'-mag-cp1/bin/llama-server')).chmod(0o755)
  self.write('/home/chris/.local/qwen4exp/'+rev+'-mag-cp1/bin/source-commit',rev+'\n')
  # Match the patched-runtime identity expected by layout.sh without building llama.cpp.
  patch_files=[str(self.repo/'ops/qwen/end-checkpoint.patch'),str(self.repo/'ops/glm/arch-compat.patch')]
  patch_id=hashlib.sha256(subprocess.check_output(['sha256sum',*patch_files])).hexdigest()
  self.write('/home/chris/.local/qwen4exp/'+rev+'-mag-cp1/bin/patch-identity',patch_id+'\n')
  self.write('/home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256','verified upstream pin\n')
  stage=self.repo/'ops/qwen/install.sh';stage.write_text('''#!/bin/bash
set -eu
for name in start.sh verify-model.sh runtime.sha; do
 install -D -m 0755 "$REPO/ops/qwen/$name" "$HOST/usr/local/lib/mag-qwen/$name"
done
install -D -m 0644 "$REPO/ops/qwen/mag-llm-qwen.service" "$HOST/etc/systemd/system/mag-llm-qwen.service"
''')
  glm_stage=self.repo/'ops/glm/install.sh';glm_stage.write_text('''#!/bin/bash
set -eu
for name in start.sh verify-model.sh model.sizes; do
 install -D -m 0755 "$REPO/ops/glm/$name" "$HOST/usr/local/lib/mag-glm/$name"
done
install -D -m 0644 "$REPO/ops/qwen/runtime.sha" "$HOST/usr/local/lib/mag-glm/runtime.sha"
install -D -m 0644 "$REPO/ops/glm/mag-llm-glm.service" "$HOST/etc/systemd/system/mag-llm-glm.service"
''')
  (self.repo/'ops/qwen/smoke.py').write_text("import os,sys;open(os.environ['SMOKE_LOG'],'a').write(' '.join(sys.argv[1:])+'\\n');sys.exit(int(os.environ.get('SMOKE_FAIL','0')))\n")
  ctl=self.bin/'systemctl';ctl.write_text('''#!/usr/bin/env python3
import json,os,sys
p=os.environ['SYSTEMD_STATE'];s=json.load(open(p));a=sys.argv[1:]
with open(os.environ['SYSTEMD_LOG'],'a') as f:f.write(' '.join(a)+'\\n')
if a[0]=='cat':sys.exit(0 if os.path.isfile(os.environ['HOST']+'/etc/systemd/system/'+a[-1]) or a[-1].startswith('mag-llm-ds-node@') else 1)
if a[0]=='disable' and a[-1]=='mag-llm-qwen.service' and not os.path.isfile(os.environ['HOST']+'/etc/systemd/system/'+a[-1]):sys.exit(1)
if a[0] in ('is-active','is-enabled'):sys.exit(0 if a[-1] in s['active' if a[0]=='is-active' else 'enabled'] else 3)
if a[0] in ('start','stop','enable','disable'):
 key='active' if a[0] in ('start','stop') else 'enabled'
 for u in a[1:]:
  if a[0] in ('start','enable'):
   if u not in s[key]:s[key].append(u)
  elif u in s[key]:s[key].remove(u)
json.dump(s,open(p,'w'))
''');ctl.chmod(0o755)
  curl=self.bin/'curl';curl.write_text('#!/bin/sh\nexit 0\n');curl.chmod(0o755)
  self.state=self.base/'state.json';self.log=self.base/'calls'
  self.original={'active':['mag-llm-ds-node@0.service','mag-llm-ds-node@1.service'],'enabled':['mag-llm-ds-node@0.service','mag-llm-ds-node@1.service']}
  self.state.write_text(json.dumps(self.original));self.backup=self.base/'backup'
  self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],HOST=str(self.host),REPO=str(self.repo),SYSTEMD_STATE=str(self.state),SYSTEMD_LOG=str(self.log),
   SMOKE_LOG=str(self.base/'smoke'),MAG_LAYOUT_FAILED_FILE=str(self.host/'var/lib/mag-stack/layout-failed'))
 def write(self,path,text):
  p=self.host/path.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
 def run_layout(self,action='apply',backup=None,**env):
  return subprocess.run(['bash',str(self.repo/'ops/qwen/layout.sh'),action,str(backup or self.backup)],env=dict(self.env,**env),capture_output=True,text=True)
 def tearDown(self):
  if self.log.exists():
   calls=self.log.read_text().splitlines()
   self.assertFalse([c for c in calls if 'mag-llm-ds-node@0.service' in c],calls)
  self.tmp.cleanup()
 def test_mixed_idempotent_and_rollback(self):
  p=self.run_layout();self.assertEqual(p.returncode,0,p.stderr)
  state=json.loads(self.state.read_text());self.assertIn('mag-llm-qwen.service',state['active']);self.assertNotIn('mag-llm-ds-node@1.service',state['enabled'])
  self.assertIn('MAG_ROOM_MODEL_ID_1=qwen', (self.host/'etc/mag-stack/room-model.env').read_text())
  mark=self.log.stat().st_size
  p=self.run_layout(backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  self.assertFalse([c for c in self.log.read_text()[mark:].splitlines() if c.startswith(('stop ','start '))])
  p=self.run_layout('rollback');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual(json.loads(self.state.read_text()),self.original)
  self.assertEqual((self.host/'etc/mag-stack/room-model.env').read_text(),(ROOT/'ops/room/model.env').read_text())
 def test_failed_live_smoke_restores_deepseek_and_boot_state(self):
  p=self.run_layout(SMOKE_FAIL='1');self.assertNotEqual(p.returncode,0)
  self.assertEqual(json.loads(self.state.read_text()),self.original)
  self.assertEqual((self.host/'etc/systemd/system/mag-llm-ds-node@.service').read_text(),'old-ds-unit\n')
 def test_explicit_deepseek_rollback_persists(self):
  self.assertEqual(self.run_layout().returncode,0)
  p=self.run_layout(backup=self.base/'backup2',MAG_ROOM_LAYOUT='deepseek');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual((self.host/'etc/mag-stack/room-layout').read_text(),'deepseek\n')
  state=json.loads(self.state.read_text());self.assertIn('mag-llm-ds-node@1.service',state['active']);self.assertNotIn('mag-llm-qwen.service',state['enabled'])
  p=self.run_layout(backup=self.base/'backup3');self.assertEqual(p.returncode,0,p.stderr)
 def test_deepseek_on_host_without_qwen_unit(self):
  p=self.run_layout(MAG_ROOM_LAYOUT='deepseek');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual(json.loads(self.state.read_text()),self.original)
  p=self.run_layout('rollback');self.assertEqual(p.returncode,0,p.stderr)
 def test_rollback_helper_pins_stack_root(self):
  # The deployed copy runs as $BACKUP/qwen-layout/helper.sh. Resolving
  # mag-units.sh from that directory looks in /var/backups/mag-stack/ops/.
  p=self.run_layout(); self.assertEqual(p.returncode,0,p.stderr)
  helper=self.backup/'qwen-layout'/'helper.sh'
  text=helper.read_text().splitlines()
  root_line=next(line for line in text if line.startswith('ROOT='))
  self.assertEqual(root_line, "ROOT='%s'"%self.repo)
  self.assertNotIn('readlink -f "$0"', '\n'.join(text))
  p=subprocess.run(['bash',str(helper),'rollback',str(self.backup)],env=self.env,capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr+p.stdout)
  self.assertNotIn('mag-units.sh', p.stderr)
  self.assertNotIn('/var/backups/mag-stack/ops/mag-units.sh', p.stderr)
  self.assertEqual(json.loads(self.state.read_text()),self.original)
 def test_conflicts_do_not_stop_numa0(self):
  ds=(ROOT/'ops/room/mag-llm-ds-node@.service').read_text();q=(ROOT/'ops/qwen/mag-llm-qwen.service').read_text()
  self.assertNotIn('mag-llm-qwen.service',ds)
  self.assertIn('Conflicts=mag-llm-ds-node@1.service mag-llm-iq3.service',q)
  self.assertNotIn('mag-llm-ds-node@0.service',q)
 def test_repo_glm_replaces_qwen_and_keeps_numa0(self):
  self.assertEqual(self.run_layout().returncode,0)
  (self.repo/'ops/room/layout').write_text('glm\n')
  mark=self.log.stat().st_size
  probe=self.run_layout('probe',backup=self.base/'backup2')
  self.assertEqual(probe.returncode,0,probe.stderr)
  self.assertEqual(self.log.stat().st_size,mark,'probe must never mutate systemd')
  p=self.run_layout(backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  state=json.loads(self.state.read_text())
  self.assertIn('mag-llm-glm.service',state['active']);self.assertIn('mag-llm-glm.service',state['enabled'])
  self.assertNotIn('mag-llm-qwen.service',state['active']);self.assertNotIn('mag-llm-qwen.service',state['enabled'])
  self.assertNotIn('mag-llm-ds-node@1.service',state['active'])
  env=(self.host/'etc/mag-stack/room-model.env').read_text()
  self.assertIn('MAG_ROOM_MODEL_ID_1=glm-5.3-flash',env);self.assertIn('MAG_ROOM_MODEL_ID_0=deepseek-v4-flash-0731',env)
  self.assertEqual((self.host/'etc/mag-stack/room-layout').read_text(),'glm\n')
  self.assertFalse((self.host/'etc/mag-stack/room-layout.pinned').exists())
  self.assertIn('--port 8192 --model glm-5.3-flash',(self.base/'smoke').read_text())
  # Qwen weights and unit file stay on the host; only the stack stops using them.
  self.assertTrue((self.host/'etc/systemd/system/mag-llm-qwen.service').exists())
  mark=self.log.stat().st_size
  p=self.run_layout(backup=self.base/'backup3');self.assertEqual(p.returncode,0,p.stderr)
  self.assertFalse([c for c in self.log.read_text()[mark:].splitlines() if c.startswith(('stop ','start '))])
  p=self.run_layout('rollback',backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  state=json.loads(self.state.read_text());self.assertIn('mag-llm-qwen.service',state['active']);self.assertNotIn('mag-llm-glm.service',state['active'])
 def test_auto_updater_probes_before_deploy(self):
  body=(ROOT/'ops/auto-update/apply.sh').read_text()
  self.assertIn('"$ROOT/ops/qwen/layout.sh" probe "$STATE"',body)
  self.assertLess(body.index('layout.sh" probe'),body.index('bash "$ROOT/ops/deploy.sh"'))
  self.assertIn('deployed-sha unchanged',body)
 def test_failed_glm_restores_qwen_once_then_stays_put(self):
  self.assertEqual(self.run_layout().returncode,0)
  (self.repo/'ops/room/layout').write_text('glm\n')
  p=self.run_layout(backup=self.base/'backup2',SMOKE_FAIL='1');self.assertNotEqual(p.returncode,0)
  state=json.loads(self.state.read_text());self.assertIn('mag-llm-qwen.service',state['active']);self.assertNotIn('mag-llm-glm.service',state['active'])
  self.assertIn('MAG_ROOM_MODEL_ID_1=qwen',(self.host/'etc/mag-stack/room-model.env').read_text())
  self.assertTrue((self.host/'var/lib/mag-stack/layout-failed').read_text().startswith('glm '))
  # Same candidate is held before backup/staging, and never marked deployed.
  mark=self.log.stat().st_size
  p=self.run_layout('probe',backup=self.base/'backup3',SMOKE_FAIL='1')
  self.assertEqual(p.returncode,78,p.stderr)
  self.assertIn('previously failed live',p.stderr)
  self.assertFalse([c for c in self.log.read_text()[mark:].splitlines() if c.startswith(('stop ','start ','enable ','disable '))])
  p=self.run_layout(backup=self.base/'backup3',SMOKE_FAIL='1')
  self.assertNotEqual(p.returncode,0,p.stderr)
  self.assertIn('refusing to claim deployment success',p.stderr)
  self.assertFalse([c for c in self.log.read_text()[mark:].splitlines() if c.startswith(('stop ','start ','enable ','disable '))])
  self.assertEqual(json.loads(self.state.read_text())['active'],state['active'])
  # A changed GLM definition (new commit) is attempted again.
  with open(self.repo/'ops/glm/start.sh','a') as f:f.write('# tuned\n')
  p=self.run_layout('probe',backup=self.base/'backup3')
  self.assertEqual(p.returncode,0,p.stderr)
  p=self.run_layout(backup=self.base/'backup4');self.assertEqual(p.returncode,0,p.stderr)
  self.assertIn('mag-llm-glm.service',json.loads(self.state.read_text())['active'])
  self.assertFalse((self.host/'var/lib/mag-stack/layout-failed').exists())
 def test_operator_pin_beats_repo_until_unpinned(self):
  (self.repo/'ops/room/layout').write_text('glm\n')
  p=self.run_layout(MAG_ROOM_LAYOUT='mixed');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual((self.host/'etc/mag-stack/room-layout.pinned').read_text(),'mixed\n')
  p=self.run_layout(backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  self.assertIn('mag-llm-qwen.service',json.loads(self.state.read_text())['active'])
  p=self.run_layout(backup=self.base/'backup3',MAG_ROOM_LAYOUT='repo');self.assertEqual(p.returncode,0,p.stderr)
  self.assertFalse((self.host/'etc/mag-stack/room-layout.pinned').exists())
  self.assertIn('mag-llm-glm.service',json.loads(self.state.read_text())['active'])
 def test_gguf_arch_compat_uses_c_string_for_printf_formatter(self):
  # Regression: passing a std::string object to ::format("%s", ...) emitted
  # random prefixes in Qwen's .context_length key and broke *both* NUMA1 models.
  p=(ROOT/'ops/glm/arch-compat.patch').read_text()
  self.assertIn('const char * arch_name = arch_override != nullptr ? arch_override : LLM_ARCH_NAMES.at(arch);',p)
  self.assertIn('::format(LLM_KV_NAMES.at(kv), arch_name)',p)
  self.assertNotIn('const std::string arch_name',p)
  self.assertIn('if (name == "glm5next")',p)
  self.assertIn('arch_name == "glm5next" ? "glm5next" : nullptr',p)
  # This patch must change the binary identity, not silently reuse the old server.
  build=(ROOT/'ops/qwen/build-runtime.sh').read_text()
  layout=(ROOT/'ops/qwen/layout.sh').read_text()
  self.assertIn('git -C "$SRC" apply "$ROOT/ops/glm/arch-compat.patch"',build)
  self.assertIn('"$ROOT/ops/glm/arch-compat.patch" | sha256sum',build)
  self.assertIn('"$ROOT/ops/glm/arch-compat.patch" | sha256sum',layout)
 def test_glm_gguf_metadata_preflight_before_qwen_stop(self):
  # A legacy GGUF can pass split sizes and still lack a required KDA key.
  # The live 6block IQ1_M first shard failed on glm5next.kda.head_dim.
  import struct
  model=self.base/'glm-header.gguf'
  scanner=ROOT/'ops/glm/inspect-gguf.py'
  def make(fields):
   data=bytearray(b'GGUF'+struct.pack('<IQQ',3,0,len(fields)))
   for key,value in fields.items():
    k=key.encode('utf-8');is_str=isinstance(value,str)
    data+=struct.pack('<Q',len(k))+k+struct.pack('<I',8 if is_str else 4)
    if is_str:
     v=value.encode('utf-8');data+=struct.pack('<Q',len(v))+v
    else:data+=struct.pack('<I',value)
   model.write_bytes(data)
  run=lambda:subprocess.run(['python3','-I',str(scanner),str(model),'--require-kda-head-dim'],
                             capture_output=True,text=True)
  for arch in ('glm5next','glm5-next'):
   make({'general.architecture':arch,'tokenizer.ggml.model':'gpt2'})
   p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
   self.assertIn(arch+'.'+('ssm.state_size' if arch=='glm5next' else 'kda.head_dim'),p.stderr)
   make({'general.architecture':arch,arch+'.kda.head_dim':128})
   p=run();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  # 6block's legacy metadata is a supported *pair*: KDA head width and
  # k-pool dimension have different but documented GGUF key names.
  legacy={'general.architecture':'glm5next',
          'glm5next.ssm.state_size':128,
          'glm5next.attention.indexer.block_size':4}
  make(legacy);p=run();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  for missing in ('glm5next.ssm.state_size','glm5next.attention.indexer.block_size'):
   make({k:v for k,v in legacy.items() if k!=missing})
   p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
   self.assertIn(missing,p.stderr)
  # The canonical, modern architecture must never accept legacy aliases.
  make({k.replace('glm5next','glm5-next'):v for k,v in legacy.items()})
  p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
  self.assertIn('glm5-next.kda.head_dim',p.stderr)
  model.write_bytes(b'GGUF\\x03')
  p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
  self.assertIn('invalid header',p.stderr)
  stage=(ROOT/'ops/glm/install.sh').read_text()
  self.assertIn('--require-kda-head-dim',stage)
  self.assertLess(stage.index('verify-model.sh'),stage.index('inspect-gguf.py'))
  layout=(ROOT/'ops/qwen/layout.sh').read_text()
  self.assertLess(layout.index('ops/glm/install.sh'),layout.index('systemctl stop "$unit"'))
 def test_glm_legacy_adapter_isolated_from_qwen(self):
  p=(ROOT/'ops/glm/arch-compat.patch').read_text()
  # Only modify GLM5-Next hparams; metadata keys and tensor names of Qwen stay
  # untouched. The old and new model paths have separate recurrent layouts.
  self.assertIn('--- a/src/models/glm5-next.cpp',p)
  self.assertIn('ml.arch_name == "glm5next"',p)
  self.assertIn('LLM_KV_SSM_STATE_SIZE',p)
  self.assertIn('LLM_KV_ATTENTION_INDEXER_BLOCK_SIZE',p)
  self.assertIn('LLM_KV_ATTENTION_RECURRENT_LAYERS',p)
  self.assertIn('LLM_KV_FULL_ATTENTION_INTERVAL',p)
  self.assertIn('hparams.indexer_kpool_select_tail = true',p)
  self.assertIn('hparams.kda_gate_lower_bound = -5.0f',p)
  build=(ROOT/'ops/qwen/build-runtime.sh').read_text()
  self.assertIn('git -C "$SRC" apply --check "$ROOT/ops/glm/arch-compat.patch"',build)
  self.assertIn('git -C "$SRC" apply "$ROOT/ops/glm/arch-compat.patch"',build)
 def test_glm_unit_and_start_are_numa1_only(self):
  u=(ROOT/'ops/glm/mag-llm-glm.service').read_text();st=(ROOT/'ops/glm/start.sh').read_text()
  self.assertIn('Conflicts=mag-llm-ds-node@1.service mag-llm-iq3.service mag-llm-qwen.service',u)
  self.assertNotIn('ds-node@0',u);self.assertIn('NUMAMask=1',u);self.assertIn('AllowedCPUs=14-27',u)
  self.assertIn('--physcpubind=14-27 --membind=1',st);self.assertIn('--load-mode mlock',st)
  self.assertIn('--port "${GLM_PORT:-8192}"',st);self.assertNotIn('--no-mmap',st)
  self.assertEqual((ROOT/'ops/room/layout').read_text().strip(),'glm')
  sizes=[l.split() for l in (ROOT/'ops/glm/model.sizes').read_text().splitlines()]
  self.assertEqual(len(sizes),15)
 def test_verify_model_checks_sizes_and_header(self):
  d=self.base/'glm';d.mkdir();sizes=self.base/'sizes'
  lines=[]
  for i in range(1,16):
   n='GLM-5.3-Flash-IQ1_M-%05d-of-00015.gguf'%i;(d/n).write_bytes(b'GGUF'+b'x'*i);lines.append('%d  %s'%(4+i,n))
  sizes.write_text('\n'.join(lines)+'\n')
  env=dict(os.environ,MAG_GLM_DIR=str(d),MAG_GLM_SIZES=str(sizes))
  run=lambda:subprocess.run(['bash',str(ROOT/'ops/glm/verify-model.sh')],env=env,capture_output=True,text=True)
  p=run();self.assertEqual(p.returncode,0,p.stderr)
  (d/'GLM-5.3-Flash-IQ1_M-00004-of-00015.gguf').write_bytes(b'GGUFxx')
  p=run();self.assertNotEqual(p.returncode,0);self.assertIn('size mismatch',p.stderr)
if __name__=='__main__':unittest.main()

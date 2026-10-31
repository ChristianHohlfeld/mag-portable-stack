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
  self.write('/etc/systemd/system/mag-llm-glm@.service','existing GLM template\n')
  self.write('/etc/systemd/system/mag-llm-glm.service','legacy GLM service\n')
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
install -D -m 0644 "$REPO/ops/qwen/mag-llm-qwen-room.service" "$HOST/etc/systemd/system/mag-llm-qwen-room.service"
''')
  glm_stage=self.repo/'ops/glm/install.sh';glm_stage.write_text('''#!/bin/bash
set -eu
for name in start.sh verify-model.sh model.sizes model.sha256; do
 install -D -m 0755 "$REPO/ops/glm/$name" "$HOST/usr/local/lib/mag-glm/$name"
done
install -D -m 0644 "$REPO/ops/qwen/runtime.sha" "$HOST/usr/local/lib/mag-glm/runtime.sha"
install -D -m 0644 "$REPO/ops/glm/chat-template.jinja" "$HOST/usr/local/lib/mag-glm/chat-template.jinja"
install -D -m 0644 "$REPO/ops/glm/mag-llm-glm@.service" "$HOST/etc/systemd/system/mag-llm-glm@.service"
for name in q1.env q2.env; do
 install -D -m 0644 "$REPO/ops/glm/$name" "$HOST/etc/mag-glm/$name"
done
''')
  (self.repo/'ops/qwen/smoke.py').write_text("import os,sys;open(os.environ['SMOKE_LOG'],'a').write(' '.join(sys.argv[1:])+'\\n');sys.exit(int(os.environ.get('SMOKE_FAIL','0')))\n")
  ctl=self.bin/'systemctl';ctl.write_text('''#!/usr/bin/env python3
import json,os,sys
p=os.environ['SYSTEMD_STATE'];s=json.load(open(p));a=sys.argv[1:]
with open(os.environ['SYSTEMD_LOG'],'a') as f:f.write(' '.join(a)+'\\n')
if a[0]=='cat':
 unit=a[-1]
 path=os.environ['HOST']+'/etc/systemd/system/'+unit
 ok=os.path.isfile(path) or unit.startswith('mag-llm-ds-node@')
 if unit.startswith('mag-llm-glm@'):ok=os.path.isfile(os.environ['HOST']+'/etc/systemd/system/mag-llm-glm@.service')
 sys.exit(0 if ok else 1)
if a[0]=='disable' and a[-1]=='mag-llm-qwen.service' and not os.path.isfile(os.environ['HOST']+'/etc/systemd/system/'+a[-1]):sys.exit(1)
if a[0]=='show':
 print('4242' if 'mag-llm-glm@q2.service' in a else '0');sys.exit(0)
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
 def seed_live_glm(self):
  # Live pre-cutover host: GLM-Q1 and GLM-Q2 resident, with Q2 protected.
  self.state.write_text(json.dumps({
   'active':['mag-llm-glm@q1.service','mag-llm-glm@q2.service'],
   'enabled':['mag-llm-glm@q1.service','mag-llm-glm@q2.service']}))
  self.write('/etc/mag-stack/room-layout','glm\n')
  self.write('/etc/mag-stack/room-model.env',
   'MAG_ROOM_MODEL_ID=glm-5.3-flash\nMAG_ROOM_MODEL_PORTS=8191:0,8192:1\n'
   'MAG_ROOM_MAX_BOTS=1\nMAG_ROOM_MODEL_ID_0=glm-5.3-flash\n'
   'MAG_ROOM_MODEL_LABEL_0=GLM-Q2\n')
 def run_layout(self,action='apply',backup=None,**env):
  return subprocess.run(['bash',str(self.repo/'ops/qwen/layout.sh'),action,str(backup or self.backup)],env=dict(self.env,**env),capture_output=True,text=True)
 def tearDown(self):
  if self.log.exists():
   calls=self.log.read_text().splitlines()
   if not list(self.base.glob('backup*/qwen-layout/glm-dual')):
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
 def test_room_qwen_conflicts_only_with_q1_not_q2(self):
  ds=(ROOT/'ops/room/mag-llm-ds-node@.service').read_text()
  q=(ROOT/'ops/qwen/mag-llm-qwen.service').read_text()
  room=(ROOT/'ops/qwen/mag-llm-qwen-room.service').read_text()
  self.assertNotIn('mag-llm-qwen.service',ds)
  self.assertIn('Conflicts=mag-llm-ds-node@1.service mag-llm-iq3.service',q)
  self.assertNotIn('mag-llm-ds-node@0.service',q)
  self.assertIn('Conflicts=mag-llm-glm@q1.service',room)
  self.assertNotIn('mag-llm-glm@q2.service',room)
 def test_repo_glm_adds_qwen_room_and_never_touches_q2(self):
  self.assertEqual(self.run_layout().returncode,0)
  self.seed_live_glm()
  (self.repo/'ops/room/layout').write_text('glm\n')
  self.write('/etc/systemd/system/mag-llm-ds-node@0.service.d/50-old.conf',
             'Environment=PORT=8191\n')
  mark=self.log.stat().st_size
  probe=self.run_layout('probe',backup=self.base/'backup2')
  self.assertEqual(probe.returncode,0,probe.stderr)
  self.assertEqual(self.log.stat().st_size,mark,'probe must never mutate systemd')
  p=self.run_layout(backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  state=json.loads(self.state.read_text())
  self.assertIn('mag-llm-qwen-room.service',state['active'])
  self.assertIn('mag-llm-qwen-room.service',state['enabled'])
  self.assertIn('mag-llm-glm@q2.service',state['active'])
  self.assertIn('mag-llm-glm@q2.service',state['enabled'])
  self.assertNotIn('mag-llm-glm@q1.service',state['active'])
  self.assertNotIn('mag-llm-glm@q1.service',state['enabled'])
  self.assertNotIn('mag-llm-qwen.service',state['active'])
  self.assertNotIn('mag-llm-qwen.service',state['enabled'])
  self.assertNotIn('mag-llm-ds-node@0.service',state['active'])
  self.assertNotIn('mag-llm-ds-node@0.service',state['enabled'])
  self.assertNotIn('mag-llm-ds-node@1.service',state['active'])
  env=(self.host/'etc/mag-stack/room-model.env').read_text()
  self.assertIn('MAG_ROOM_MODEL_ID_0=qwen3.8-flash-next-uncensored',env)
  self.assertIn('MAG_ROOM_MODEL_ID_1=glm-5.3-flash',env)
  self.assertIn('MAG_ROOM_MODEL_PORTS=8191:0,8192:1',env)
  runtime=(self.host/'etc/mag-qwen/runtime.env').read_text()
  self.assertIn('QWEN_NODE=0',runtime);self.assertIn('QWEN_PORT=8191',runtime)
  self.assertIn('QWEN_THREADS_BATCH=28',runtime)
  self.assertEqual((self.host/'etc/mag-stack/room-layout').read_text(),'glm\n')
  smoke=(self.base/'smoke').read_text()
  self.assertIn('--port 8191 --model qwen3.8-flash-next-uncensored --startup-only',smoke)
  self.assertNotIn('--port 8192',smoke,'GLM-Q2 must not receive a smoke decode')
  actions=self.log.read_text()[mark:].splitlines()
  for verb in ('stop','start','enable','disable'):
   self.assertNotIn(verb+' mag-llm-glm@q2.service',actions)
  self.assertIn('show -p MainPID --value mag-llm-glm@q2.service',actions)
  self.assertIn('GLM-Q2 PID changed during Qwen cold start',(ROOT/'ops/qwen/layout.sh').read_text())
  self.assertTrue((self.host/'etc/systemd/system/mag-llm-qwen.service').exists())
  self.assertTrue((self.host/'etc/systemd/system/mag-llm-qwen-room.service').exists())
  mark=self.log.stat().st_size
  p=self.run_layout(backup=self.base/'backup3');self.assertEqual(p.returncode,0,p.stderr)
  self.assertFalse([c for c in self.log.read_text()[mark:].splitlines() if c.startswith(('stop ','start '))])
  actions=self.log.read_text()[mark:].splitlines()
  self.assertNotIn('stop mag-llm-glm@q2.service',actions)
  self.assertNotIn('start mag-llm-glm@q2.service',actions)
  p=self.run_layout('rollback',backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  state=json.loads(self.state.read_text())
  self.assertIn('mag-llm-glm@q1.service',state['active'])
  self.assertIn('mag-llm-glm@q2.service',state['active'])
  self.assertNotIn('mag-llm-qwen-room.service',state['active'])
  self.assertNotIn('mag-llm-glm@q2.service',[
   c.split(' ',1)[1] for c in self.log.read_text().splitlines()
   if c.startswith(('stop ','start ','enable ','disable '))])

 def test_qwen_room_change_restarts_only_qwen_and_never_q2(self):
  self.seed_live_glm()
  (self.repo/'ops/room/layout').write_text('glm\n')
  p=self.run_layout();self.assertEqual(p.returncode,0,p.stderr)
  altered=self.repo/'ops/qwen/start.sh'
  altered.write_text(altered.read_text()+'\n# room-only test change\n')
  mark=self.log.stat().st_size
  p=self.run_layout(backup=self.base/'backup2')
  self.assertEqual(p.returncode,0,p.stderr)
  actions=self.log.read_text()[mark:].splitlines()
  self.assertIn('stop mag-llm-qwen-room.service',actions)
  self.assertIn('start mag-llm-qwen-room.service',actions)
  for verb in ('stop','start','enable','disable'):
   self.assertNotIn(verb+' mag-llm-glm@q2.service',actions)
  state=json.loads(self.state.read_text())
  self.assertIn('mag-llm-qwen-room.service',state['active'])
  self.assertIn('mag-llm-glm@q2.service',state['active'])
  mark=self.log.stat().st_size
  p=self.run_layout('rollback',backup=self.base/'backup2')
  self.assertEqual(p.returncode,0,p.stderr)
  rollback=self.log.read_text()[mark:].splitlines()
  self.assertIn('stop mag-llm-qwen-room.service',rollback)
  self.assertIn('start mag-llm-qwen-room.service',rollback)
  for verb in ('stop','start','enable','disable'):
   self.assertNotIn(verb+' mag-llm-glm@q2.service',rollback)

 def test_iq1_single_and_split_sha_pin(self):
  # Tiny synthetic GGUF fixtures; no real model I/O or inference.
  script=ROOT/'ops/glm/verify-iq1.sh'
  def verify(parts,mutate=None,checksum='0'):
   with tempfile.TemporaryDirectory() as wd:
    folder=pathlib.Path(wd)/'weights';folder.mkdir()
    pin=pathlib.Path(wd)/'pin'
    for name in parts:
     (folder/name).write_bytes(b'GGUF'+b'a'*4092)
    env=dict(os.environ,MAG_GLM_IQ1_DIR=str(folder),
             MAG_GLM_IQ1_PIN_DIR=str(pin),
             MAG_GLM_IQ1_VERIFY_SHA256='0')
    def call():return subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True)
    first=call();self.assertEqual(first.returncode,0,first.stderr)
    self.assertEqual(len((pin/'iq1.sizes').read_text().splitlines()),len(parts))
    self.assertEqual(len((pin/'iq1.sha256').read_text().splitlines()),len(parts))
    self.assertEqual(call().returncode,0)
    env['MAG_GLM_IQ1_VERIFY_SHA256']='1'
    self.assertEqual(call().returncode,0)
    if mutate:
     path=folder/parts[0]
     if mutate=='content':path.write_bytes(b'GGUF'+b'b'*4092)
     else:path.write_bytes(b'GGUF'+b'b'*4093)
     self.assertNotEqual(call().returncode,0)
  verify(['GLM-5.3-Flash-IQ1_M.gguf'],mutate='content')
  verify(['GLM-IQ1_M-00001-of-00002.gguf',
          'GLM-IQ1_M-00002-of-00002.gguf'],mutate='size')

 def test_glm_per_unit_numa_dropins_no_start_flag_change(self):
  q1=(ROOT/'ops/glm/mag-llm-glm@q1.service.d/10-numa.conf').read_text()
  q2=(ROOT/'ops/glm/mag-llm-glm@q2.service.d/10-numa.conf').read_text()
  self.assertIn('AllowedCPUs=0-13,28-41',q1)
  self.assertIn('AllowedCPUs=14-27,42-55',q2)
  self.assertIn('NUMAPolicy=bind',q1);self.assertIn('NUMAPolicy=bind',q2)
  self.assertIn('NUMAMask=0',q1);self.assertIn('NUMAMask=1',q2)
  install=(ROOT/'ops/glm/install.sh').read_text()
  self.assertIn('mag-llm-glm@$id.service.d/10-numa.conf',install)
  self.assertIn('verify-iq1.sh',install)
  self.assertIn('no model restart',install.lower()+' no model restart')

 def test_auto_updater_probes_before_deploy(self):
  body=(ROOT/'ops/auto-update/apply.sh').read_text()
  self.assertIn('"$ROOT/ops/qwen/layout.sh" probe "$STATE"',body)
  self.assertLess(body.index('layout.sh" probe'),body.index('bash "$ROOT/ops/deploy.sh"'))
  self.assertIn('deployed-sha unchanged',body)
 def test_failed_glm_restores_qwen_once_then_stays_put(self):
  self.assertEqual(self.run_layout().returncode,0)
  (self.repo/'ops/room/layout').write_text('glm\n')
  p=self.run_layout(backup=self.base/'backup2',SMOKE_FAIL='1');self.assertNotEqual(p.returncode,0)
  state=json.loads(self.state.read_text());self.assertIn('mag-llm-qwen.service',state['active']);self.assertNotIn('mag-llm-glm@q1.service',state['active']);self.assertNotIn('mag-llm-glm@q2.service',state['active'])
  self.assertIn('--model qwen3.8-flash-next-uncensored --quick',(self.base/'smoke').read_text())
  self.assertIn('rollback smoke failed',p.stderr)
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
  self.assertIn('mag-llm-glm@q1.service',json.loads(self.state.read_text())['active']);self.assertIn('mag-llm-glm@q2.service',json.loads(self.state.read_text())['active'])
  self.assertFalse((self.host/'var/lib/mag-stack/layout-failed').exists())
 def test_operator_pin_beats_repo_until_unpinned(self):
  (self.repo/'ops/room/layout').write_text('glm\n')
  p=self.run_layout(MAG_ROOM_LAYOUT='mixed');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual((self.host/'etc/mag-stack/room-layout.pinned').read_text(),'mixed\n')
  p=self.run_layout(backup=self.base/'backup2');self.assertEqual(p.returncode,0,p.stderr)
  self.assertIn('mag-llm-qwen.service',json.loads(self.state.read_text())['active'])
  p=self.run_layout(backup=self.base/'backup3',MAG_ROOM_LAYOUT='repo');self.assertEqual(p.returncode,0,p.stderr)
  self.assertFalse((self.host/'etc/mag-stack/room-layout.pinned').exists())
  self.assertIn('mag-llm-glm@q1.service',json.loads(self.state.read_text())['active']);self.assertIn('mag-llm-glm@q2.service',json.loads(self.state.read_text())['active'])
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
  make({k.replace('glm5next','glm5-next'):
        v.replace('glm5next','glm5-next') if isinstance(v,str) else v
        for k,v in legacy.items()})
  p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
  self.assertIn('glm5-next.kda.head_dim',p.stderr)
  model.write_bytes(b'GGUF\\x03')
  p=run();self.assertEqual(p.returncode,4,p.stdout+p.stderr)
  self.assertIn('invalid header',p.stderr)
  stage=(ROOT/'ops/glm/install.sh').read_text()
  self.assertIn('--require-kda-head-dim',stage)
  self.assertLess(stage.index('verify-model.sh'),stage.index('inspect-gguf.py'))
  layout=(ROOT/'ops/qwen/layout.sh').read_text()
  # Apply-time model preflight must precede the first apply-time stop;
  # the rollback function above legitimately contains its own stop.
  stage=layout.index('ops/glm/install.sh')
  self.assertLess(stage,layout.index('systemctl stop "$unit"',stage))
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
 def test_glm_iq4nl_ape_tensor_diagnostic_and_cast(self):
  # Minimal GGUF with two tensor descriptors, no payload required.
  import struct
  path=self.base/'ape-directory.gguf'
  def ggstr(value):
   blob=value.encode('utf-8')
   return struct.pack('<Q',len(blob))+blob
  entries=[
   ('blk.3.indexer_compressor_ape.weight',20),
   ('blk.3.ssm_a',0),
  ]
  data=bytearray(b'GGUF'+struct.pack('<IQQ',3,len(entries),1))
  data+=ggstr('general.architecture')+struct.pack('<I',8)+ggstr('glm5next')
  for name,typ in entries:
   data+=ggstr(name)+struct.pack('<I',2)+struct.pack('<QQ',128,4)
   data+=struct.pack('<IQ',typ,0)
  path.write_bytes(data)
  scan=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                       str(path),'--show-elementwise-weights'],
                      capture_output=True,text=True)
  self.assertEqual(scan.returncode,0,scan.stderr)
  self.assertIn('blk.3.indexer_compressor_ape.weight: iq4_nl',scan.stdout)
  self.assertIn('blk.3.ssm_a: f32',scan.stdout)
  # Type-level fail-fast is permitted to accept the APE IQ4_NL because
  # the pinned GLM graph now casts this operand before ggml_add.
  safe=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                       str(path),'--require-elementwise-safe'],capture_output=True,text=True)
  self.assertEqual(safe.returncode,0,safe.stderr)
  # Other shards in a split GGUF omit general.architecture. Reading their
  # tensor types is allowed only for diagnostics, never for deploy preflight.
  split=bytearray(b'GGUF'+struct.pack('<IQQ',3,len(entries),0))
  for name,typ in entries:
   split+=ggstr(name)+struct.pack('<I',2)+struct.pack('<QQ',128,4)
   split+=struct.pack('<IQ',typ,0)
  path.write_bytes(split)
  diag=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                       str(path),'--show-elementwise-weights'],
                      capture_output=True,text=True)
  self.assertEqual(diag.returncode,0,diag.stderr)
  self.assertIn('blk.3.indexer_compressor_ape.weight: iq4_nl',diag.stdout)
  split_safe=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                             str(path),'--require-elementwise-safe'],
                            capture_output=True,text=True)
  self.assertEqual(split_safe.returncode,0,split_safe.stderr)
  # Unknown quantized elementwise weight fails before any model is stopped.
  bad=split.replace(struct.pack('<IQ',20,0),struct.pack('<IQ',29,0),1)
  path.write_bytes(bad)
  rejected=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                           str(path),'--require-elementwise-safe'],
                          capture_output=True,text=True)
  self.assertEqual(rejected.returncode,4)
  self.assertIn('unsupported elementwise operand',rejected.stderr)
  path.write_bytes(split)
  strict=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),
                         str(path),'--require-kda-head-dim'],
                        capture_output=True,text=True)
  self.assertEqual(strict.returncode,4,strict.stdout+strict.stderr)
  self.assertIn('architecture=None',strict.stderr)
  no_flag=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),str(path)],
                         capture_output=True,text=True)
  self.assertEqual(no_flag.returncode,4,no_flag.stdout+no_flag.stderr)
  patch=(ROOT/'ops/glm/arch-compat.patch').read_text()
  self.assertIn('--- a/src/models/glm5-next.cpp',patch)
  # Type-driven: every non-F32 elementwise weight is cast, not one tensor name.
  self.assertIn('w->type == GGML_TYPE_F32',patch)
  self.assertIn('return ggml_cast(ctx, w, GGML_TYPE_F32);',patch)
  for use in ('ggml_add(ctx0, pg, glm5_f32(ctx0, layer.indexer_kpool_ape))',
              'glm5_f32(ctx0, layer.ssm_dt_b)','glm5_f32(ctx0, layer.ssm_a)',
              'hc_scale = glm5_f32(ctx0, hc_scale);','hc_base  = glm5_f32(ctx0, hc_base);'):
   self.assertIn(use,patch)
  self.assertEqual(patch.count('glm5_f32(ctx0, layer.ffn_exp_probs_b)'),2)
  # Fail-fast at load with the tensor name, instead of GGML_ABORT on decode.
  self.assertIn('glm5_check_weight_types(*this);',patch)
  self.assertIn('throw std::runtime_error',patch)
  self.assertNotIn('--- a/ggml/src/ggml-cpu/binary-ops.cpp',patch)
  self.assertNotIn('+++ b/ggml/',patch)
 def test_glm_scanner_requires_thinking_switch(self):
  import struct
  def ggstr(v):
   b=v.encode();return struct.pack('<Q',len(b))+b
  def gguf(template):
   kv=[('general.architecture','glm5next')]+([('tokenizer.chat_template',template)] if template else [])
   d=bytearray(b'GGUF'+struct.pack('<IQQ',3,0,len(kv)))
   for k,v in kv: d+=ggstr(k)+struct.pack('<I',8)+ggstr(v)
   return bytes(d)
  scan=lambda: subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),str(path),
                               '--require-thinking-switch','--dump-chat-template'],capture_output=True,text=True)
  path=self.base/'tmpl.gguf'
  path.write_bytes(gguf("<|assistant|>{{ '</think>' if (enable_thinking is defined and not enable_thinking) else '<think>' }}"))
  ok=scan();self.assertEqual(ok.returncode,0,ok.stderr);self.assertIn('enable_thinking switch present',ok.stdout)
  self.assertIn('--- tokenizer.chat_template ---',ok.stdout)
  path.write_bytes(gguf("<|assistant|><think>"))
  bad=scan();self.assertEqual(bad.returncode,4);self.assertIn('ignores enable_thinking',bad.stderr)
  path.write_bytes(gguf(None))
  none=scan();self.assertEqual(none.returncode,4);self.assertIn('no tokenizer.chat_template',none.stderr)
  # The real GLM-5.3 template ignores enable_thinking: deploy uses the reviewed pin,
  # and the scanner checks the pin plus the embedded template family.
  emb=(ROOT/'ops/glm/chat-template.jinja').read_text()
  path.write_bytes(gguf(emb.replace("'<think></think>' if (enable_thinking is defined and not enable_thinking) else '<think>'","'<think>'")))
  pinned=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),str(path),'--require-glm-template-family',
                         '--chat-template-file',str(ROOT/'ops/glm/chat-template.jinja'),'--require-thinking-switch'],
                        capture_output=True,text=True)
  self.assertEqual(pinned.returncode,0,pinned.stderr)
  self.assertIn('pinned GLM-5.3 family',pinned.stdout)
  path.write_bytes(gguf("<|assistant|>{{ '<think>' }} enable_thinking"))
  other=subprocess.run(['python3','-I',str(ROOT/'ops/glm/inspect-gguf.py'),str(path),'--require-glm-template-family'],
                       capture_output=True,text=True)
  self.assertEqual(other.returncode,4);self.assertIn('review ops/glm/chat-template.jinja',other.stderr)
  inst=(ROOT/'ops/glm/install.sh').read_text()
  self.assertIn('--require-glm-template-family',inst)
  self.assertIn('--chat-template-file "$ROOT/ops/glm/chat-template.jinja" --require-thinking-switch',inst)
 def test_glm_pinned_template_and_runtime_flags(self):
  t=(ROOT/'ops/glm/chat-template.jinja').read_text()
  self.assertIn("'<think></think>' if (enable_thinking is defined and not enable_thinking) else '<think>'",t)
  self.assertIn("in ['low', 'high', 'max'] else 'low'",t)
  self.assertNotIn('enable_thinking',t.split('effective_reasoning_effort',2)[1].split('-%}')[0])
  st=(ROOT/'ops/glm/start.sh').read_text();u=(ROOT/'ops/glm/mag-llm-glm@.service').read_text()
  self.assertIn('--chat-template-file "$TEMPLATE"',st);self.assertIn('--load-mode dio',st)
  self.assertNotIn('IOSchedulingClass=idle',u);self.assertNotIn('Nice=10',u)
  lay=(ROOT/'ops/qwen/layout.sh').read_text()
  self.assertIn('/usr/local/lib/mag-glm/chat-template.jinja',lay)
 def test_runtime_rebuild_restarts_and_rollback_verifies_qwen(self):
  lay=(ROOT/'ops/qwen/layout.sh').read_text()
  self.assertIn("== *' (deleted)'",lay)
  self.assertIn('if runs_stale_binary "$selected"; then change=1; fi',lay)
  b=(ROOT/'ops/qwen/build-runtime.sh').read_text()
  pub=b.index('install -m 0755 "$BUILD/bin/llama-server"')
  self.assertLess(b.index('"$BUILD/bin/mag-glm-elementwise-cast"'),pub)
  self.assertLess(b.index('"$BUILD/bin/mag-glm-chat-template" "$ROOT/ops/glm/chat-template.jinja"'),pub)
 def test_glm_unit_is_parametric_for_both_nodes(self):
  u=(ROOT/'ops/glm/mag-llm-glm@.service').read_text();st=(ROOT/'ops/glm/start.sh').read_text()
  self.assertIn('EnvironmentFile=/etc/mag-glm/%i.env',u)
  self.assertIn('ExecStart=/usr/local/lib/mag-glm/start.sh %i',u)
  self.assertIn('--physcpubind=0-13,28-41 --membind=0',st)
  self.assertIn('--physcpubind=14-27,42-55 --membind=1',st)
  self.assertIn('--threads 14 --threads-batch 28',st);self.assertIn('--load-mode dio',st)
  self.assertIn('--port "$GLM_PORT"',st);self.assertNotIn('--no-mmap',st)
  self.assertIn('GLM_PORT=8191',(ROOT/'ops/glm/q1.env').read_text())
  self.assertIn('GLM_PORT=8192',(ROOT/'ops/glm/q2.env').read_text())
  self.assertEqual((ROOT/'ops/room/layout').read_text().strip(),'glm')
  sizes=[l.split() for l in (ROOT/'ops/glm/model.sizes').read_text().splitlines()]
  self.assertEqual(len(sizes),15)
  self.assertEqual(sum(int(row[0]) for row in sizes),92109221728)
  hashes=[l.split() for l in (ROOT/'ops/glm/model.sha256').read_text().splitlines()]
  self.assertEqual([row[1] for row in hashes],[row[1] for row in sizes])
  self.assertTrue(all(len(row[0])==64 for row in hashes))
 def test_verify_model_checks_sizes_and_header(self):
  d=self.base/'glm';d.mkdir();pins=self.base/'sizes';hashfile=self.base/'sha256'
  lines=[];checks=[]
  for i in range(1,16):
   n='GLM-5.3-Flash-IQ2_XS-%05d-of-00015.gguf'%i
   payload=b'GGUF'+b'x'*i
   (d/n).write_bytes(payload)
   lines.append('%d  %s'%(len(payload),n))
   checks.append('%s  %s'%(hashlib.sha256(payload).hexdigest(),n))
  pins.write_text('\n'.join(lines)+'\n')
  hashfile.write_text('\n'.join(checks)+'\n')
  env=dict(os.environ,MAG_GLM_DIR=str(d),MAG_GLM_SIZES=str(pins),MAG_GLM_SHA256S=str(hashfile))
  run=lambda:subprocess.run(['bash',str(ROOT/'ops/glm/verify-model.sh')],env=env,capture_output=True,text=True)
  p=run();self.assertEqual(p.returncode,0,p.stderr)
  env['MAG_GLM_VERIFY_SHA256']='1'
  p=run();self.assertEqual(p.returncode,0,p.stderr)
  (d/'GLM-5.3-Flash-IQ2_XS-00004-of-00015.gguf').write_bytes(b'GGUFxx')
  p=run();self.assertEqual(p.returncode,4);self.assertIn('size mismatch',p.stderr)
  # Same length and header, different bytes: only SHA256 detects corruption.
  (d/'GLM-5.3-Flash-IQ2_XS-00004-of-00015.gguf').write_bytes(b'GGUFzzzz')
  p=run();self.assertEqual(p.returncode,4);self.assertIn('SHA256 mismatch',p.stderr)
if __name__=='__main__':unittest.main()

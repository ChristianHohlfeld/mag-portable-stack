"""mag-numa-swap against a fake systemctl and fake llama health endpoints.
Never touches real units: everything lives in a temporary directory."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import fcntl,http.server,json,os,pathlib,socket,subprocess,tempfile,threading,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
BIN=ROOT/'build/mag-numa-swap'

FAKE_SYSTEMCTL=r'''#!/bin/sh
d="$FAKE_SD"; mkdir -p "$d/active" "$d/failed"; echo "$*" >> "$d/log"
[ "$1" = "--quiet" ] && shift
case "$1" in
  is-active) test -f "$d/active/$3";;
  is-failed) test -f "$d/failed/$3";;
  stop) rm -f "$d/active/$2" "$d/failed/$2";;
  start)
    rm -f "$d/failed/$2"
    if grep -q 'MAG_NUMA_MODEL=broken' "$FAKE_UNITS/$2" 2>/dev/null; then touch "$d/failed/$2"; exit 0; fi
    touch "$d/active/$2"; ls "$d/active" | wc -l >> "$d/counts";;
  *) :;;
esac
'''

def free_port():
 s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close();return p

class Health(http.server.BaseHTTPRequestHandler):
 """200 when the node's unit is 'active' in the fake systemd."""
 unit='';sd=''
 def log_message(self,*a):pass
 def do_GET(self):
  ok=os.path.exists(os.path.join(self.server.sd,'active',self.server.unit))
  self.send_response(200 if ok else 503);self.send_header('Content-Length','0');self.end_headers()

class NumaSwap(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();t=pathlib.Path(self.tmp.name)
  self.sd=t/'sd';self.units=t/'units';self.units.mkdir();self.sd.mkdir()
  cat=json.loads((ROOT/'ops/numa/catalog.json').read_text())
  self.servers=[]
  for n in cat['nodes']:
   n['port']=free_port()
   srv=http.server.ThreadingHTTPServer(('127.0.0.1',n['port']),Health);srv.unit=f"mag-numa{n['node']}.service";srv.sd=str(self.sd)
   threading.Thread(target=srv.serve_forever,daemon=True).start();self.servers.append(srv)
  cat['models'].append({'id':'broken','label':'Broken','short':'X','ram_gib':50,'bin':'/bin/false','model':'/x.gguf'})
  cat['models'].append({'id':'huge','label':'Huge 300 GB','short':'H','ram_gib':300,'bin':'/bin/false','model':'/h.gguf'})
  self.cat=cat;(t/'catalog.json').write_text(json.dumps(cat))
  sc=t/'systemctl';sc.write_text(FAKE_SYSTEMCTL);sc.chmod(0o755)
  self.state=t/'state.json'
  self.env=dict(os.environ,MAG_NUMA_CATALOG=str(t/'catalog.json'),MAG_NUMA_STATE=str(self.state),MAG_NUMA_UNIT_DIR=str(self.units),
   MAG_NUMA_SYSTEMCTL=str(sc),MAG_NUMA_CGROUP=str(t/'cg'),MAG_NUMA_HEALTH_TIMEOUT_S='3',MAG_NUMA_POLL_MS='50',FAKE_SD=str(self.sd),FAKE_UNITS=str(self.units))
  # Production starts with the legacy units serving the defaults.
  (self.sd/'active').mkdir();(self.sd/'failed').mkdir()
  (self.sd/'active'/'mag-llm.service').touch();(self.sd/'active'/'mag-llm-iq3.service').touch()
 def tearDown(self):
  for s in self.servers:s.shutdown()
  self.tmp.cleanup()
 def run_swap(self,*args):
  return subprocess.run([str(BIN),*args],env=self.env,capture_output=True,text=True,timeout=30)
 def node(self,n):return json.loads(self.state.read_text())['nodes'][str(n)]
 def log(self):return (self.sd/'log').read_text() if (self.sd/'log').exists() else ''

 def test_render_limits_for_every_model_and_node(self):
  for node in (0,1):
   for m in self.cat['models'][:3]:
    u=self.run_swap('render',str(node),m['id']).stdout;ram=m['ram_gib']
    cpus=self.cat['nodes'][node]['cpus']
    for line in [f'NUMAPolicy=bind',f'NUMAMask={node}',f'AllowedMemoryNodes={node}',f'AllowedCPUs={cpus}',f'CPUAffinity={cpus}',
                 f'MemoryMin={ram}G',f'MemoryLow={ram}G',f'MemoryHigh={ram+8}G',f'MemoryMax={ram+16}G','MemorySwapMax=0',
                 'LimitMEMLOCK=infinity','OOMScoreAdjust=-900','WantedBy=multi-user.target']:
     self.assertIn('\n'+line+'\n',u,(node,m['id'],line))
    self.assertIn(f'--physcpubind={cpus} --membind={node} ',u)
    self.assertTrue(' --mlock' in u or '--load-mode mlock' in u)
    self.assertIn(f"--alias {m['id']} ",u)
 def test_refuses_model_over_node_budget(self):
  r=self.run_swap('swap','1','huge')
  self.assertEqual(r.returncode,3);self.assertIn('budget',r.stderr)
  self.assertNotIn('stop',self.log())  # nothing was unloaded
 def test_refuses_over_host_limit(self):
  cat=json.loads(pathlib.Path(self.env['MAG_NUMA_CATALOG']).read_text());cat['host']['limit_gib']=200
  pathlib.Path(self.env['MAG_NUMA_CATALOG']).write_text(json.dumps(cat))
  r=self.run_swap('admit','1','qwen3.8-flash-next-uncensored')
  self.assertEqual(r.returncode,3);self.assertIn('Host limit',r.stderr)
 def test_swap_unloads_first_then_serves_and_persists(self):
  r=self.run_swap('swap','1','qwen3.8-flash-next-uncensored')
  self.assertEqual(r.returncode,0,r.stderr)
  n=self.node(1);self.assertEqual((n['model'],n['state'],n['progress']),('qwen3.8-flash-next-uncensored','serving',100))
  log=self.log()
  self.assertLess(log.index('stop mag-llm-iq3.service'),log.index('start mag-numa1.service'))
  self.assertIn('disable mag-llm-iq3.service',log);self.assertIn('enable mag-numa1.service',log)
  self.assertNotIn('mag-llm.service',log.replace('is-active --quiet mag-llm.service',''))  # node0 untouched
  self.assertTrue(all(int(c)<=2 for c in (self.sd/'counts').read_text().split()))
  self.assertIn('MAG_NUMA_MODEL=qwen3.8-flash-next-uncensored',(self.units/'mag-numa1.service').read_text())
  # Idempotent: the same request again changes nothing.
  before=self.log();r=self.run_swap('swap','1','qwen3.8-flash-next-uncensored')
  self.assertEqual(r.returncode,0);self.assertIn('already serving',r.stdout)
  self.assertNotIn('stop',self.log()[len(before):])
 def test_failed_load_rolls_back_to_previous(self):
  self.assertEqual(self.run_swap('swap','1','qwen3.8-flash-next-uncensored').returncode,0)
  r=self.run_swap('swap','1','broken')
  self.assertEqual(r.returncode,6,r.stderr)
  n=self.node(1);self.assertEqual((n['model'],n['state']),('qwen3.8-flash-next-uncensored','serving'))
  self.assertIn('rolled back',n['error'])
  self.assertIn('MAG_NUMA_MODEL=qwen3.8-flash-next-uncensored',(self.units/'mag-numa1.service').read_text())
  self.assertTrue((self.sd/'active'/'mag-numa1.service').exists())
 def test_concurrent_swap_is_refused(self):
  lock=open(str(self.state)+'.lock','w');fcntl.flock(lock,fcntl.LOCK_EX)
  try:self.assertEqual(self.run_swap('swap','0','deepseek-v4-flash-0731-iq3').returncode,5)
  finally:lock.close()
 def test_catalog_has_the_iq3_41_flash_quant(self):
  ids={m['id']:m for m in json.loads((ROOT/'ops/numa/catalog.json').read_text())['models']}
  self.assertIn('4.1 Flash IQ3',ids['deepseek-v4-flash-0731-iq3']['label'])
  self.assertLessEqual(len(json.loads((ROOT/'ops/numa/catalog.json').read_text())['nodes']),2)

if __name__=='__main__':unittest.main(verbosity=2)

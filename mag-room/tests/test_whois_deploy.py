"""Disposable nginx ingress transaction tests; no root, network or host mutation."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"ops/private-access/deploy-nginx.sh"
TEMPLATE=ROOT/"ops/private-access/mag-room.nginx.conf"

class WhoisDeployment(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  p=self.p=pathlib.Path(self.tmp.name)
  self.site=p/"etc/nginx/sites-available/mag-room.conf"
  self.enabled=p/"etc/nginx/sites-enabled/mag-room.conf"
  self.site.parent.mkdir(parents=True)
  self.enabled.parent.mkdir(parents=True)
  self.site.write_text("old legacy vhost\n")
  self.original=self.site.read_bytes()
  (p/"etc/mag-stack").mkdir(parents=True)
  self.probe=p/"etc/mag-stack/deploy-owner-probe.conf"
  self.probe.write_text("MAG_DEPLOY_OWNER_SSH=chris@trusted-box\nMAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\n")
  self.probe.chmod(0o600)
  (p/"var/lib/magbot/room").mkdir(parents=True)
  (p/"var/lib/magbot/room/access.json").write_text('{"owners":["chris.hohlfeld@gmail.com"]}')
  (p/"etc/mag-nginx/tls").mkdir(parents=True)
  for f in ("cert.pem","key.pem"): (p/"etc/mag-nginx/tls"/f).write_text("test file")
  (p/"etc/credstore").mkdir(parents=True) # Missing optional OAuth secret
  (p/"var/backups/mag-stack").mkdir(parents=True)
  txn=p/"var/backups/mag-stack/check"; txn.mkdir()
  mock=p/"mock";mock.mkdir()
  programs={
   "nginx":'''#!/usr/bin/env bash
if [[ "$1" == -T ]]; then cat "$MOCK_SITE"; fi
if [[ "$1" == -t ]] && grep -q BROKEN "$MOCK_SITE"; then exit 1; fi''',
   "systemctl":'''#!/usr/bin/env bash
if [[ "$1" == reload ]]; then echo reload >> "$MOCK_LOG"; fi''',
   "ip":'''#!/usr/bin/env bash
echo 'inet 100.123.114.16/32 scope global tailscale0' ''',
   "openssl":'''#!/usr/bin/env bash
if [[ "$*" == *-checkhost* ]]; then echo 'Hostname does match certificate'; fi''',
   "ssh":'''#!/usr/bin/env bash
if [[ "§{MOCK_SSH_DOWN:-0}" == 1 ]]; then echo "ssh: connect timed out" >&2; exit 255; fi
arg="§{@: -1}"
if [[ "$arg" == true ]]; then exit 0; fi
if [[ "$arg" == 'tailscale ip -4' ]]; then
 if [[ "§{MOCK_PROBE_SELF:-0}" == 1 ]]; then echo 100.123.114.16; else echo 100.101.102.103; fi
 exit 0
fi
if [[ "$arg" == 'tailscale status --json' ]]; then
 if [[ "§{MOCK_TAGGED_BOX:-0}" == 1 ]]; then
  echo '{"Self":{"Online":true,"UserID":0,"Tags":["tag:mag-guest"]},"User":{}}'
 else
  echo '{"Self":{"Online":true,"UserID":42,"Tags":[]},"User":{"42":{"ID":42,"LoginName":"chris.hohlfeld@gmail.com"}}}'
 fi
 exit 0
fi
if [[ "$MOCK_OWNER_FAIL" == 1 ]]; then exit 22; fi
# For userspace clients the smoke MUST use the remote Tailscale SOCKS5 path.
if [[ "${MOCK_EXPECT_SOCKS:-0}" == 1 &&
      "$arg" != *'--socks5-hostname 127.0.0.1:1055'* ]]; then exit 34; fi
if [[ "${MOCK_PROXY_DOWN:-0}" == 1 &&
      "$arg" == *'--socks5-hostname'* ]]; then exit 7; fi
if [[ "$arg" == *'/api/state'* ]]; then
 if [[ "${MOCK_GUEST_ROLE:-0}" == 1 ]]; then
  echo '{"you":{"role":"guest","login":"tag:mag-guest:123"}}'
 else
  echo '{"you":{"role":"owner","login":"chris.hohlfeld@gmail.com"}}'
 fi
elif [[ "$arg" == *'https://mag.stardenburg.de/'* ]]; then
 echo 200
else exit 8; fi''',
   "curl":'''#!/usr/bin/env bash
echo -n "§{MOCK_CURL_CODE:-403}"''',
  }
  for name,body in programs.items():
   f=mock/name
   f.write_text(body.replace("§{",chr(36)+"{")+"\n")
   f.chmod(0o755)
  code=SCRIPT.read_text()
  for before,after in [
    ("/etc/nginx",str(p/"etc/nginx")),
    ("/etc/mag-stack",str(p/"etc/mag-stack")),
    ("/etc/mag-nginx",str(p/"etc/mag-nginx")),
    ("/etc/credstore",str(p/"etc/credstore")),
    ("/var/lib/magbot",str(p/"var/lib/magbot")),
    ("/var/backups/mag-stack",str(p/"var/backups/mag-stack")),
    ("/run/lock",str(p))
  ]: code=code.replace(before,after)
  code=code.replace("[[ $EUID == 0 ]] || die 'Root required'",": # test-only EUID exemption")
  code=code.replace('[[ $(stat -c %u "$probe_file") == 0 ]]',
                    '[[ $(stat -c %u "$probe_file") == $(id -u) ]]')
  self.script=p/"source/private-access/deploy-nginx.sh"
  self.script.parent.mkdir(parents=True)
  self.script.write_text(code)
  self.script.chmod(0o755)
  shutil.copyfile(TEMPLATE,self.script.parent/"mag-room.nginx.conf")
  self.log=p/"reload.log"
  self.env={**os.environ,"PATH":str(mock)+":"+os.environ["PATH"],
            "MOCK_SITE":str(self.site),"MOCK_LOG":str(self.log),
            "MAG_NGINX_TXN":str(txn)}
 def run_script(self,subcmd,fail=False):
  return subprocess.run(["bash",str(self.script),subcmd],
    env={**self.env,"MOCK_OWNER_FAIL":"1" if fail else "0"},
    text=True,capture_output=True)
 def test_missing_oauth_does_not_block_preflight_or_mutate_nginx(self):
  r=self.run_script("preflight")
  self.assertEqual(r.returncode,0,r.stderr)
  self.assertIn("OAuth absent",r.stderr)
  self.assertEqual(self.original,self.site.read_bytes())
 def run_env(self,subcmd,**env):
  return subprocess.run(["bash",str(self.script),subcmd],
    env={**self.env,"MOCK_OWNER_FAIL":"0",**env},text=True,capture_output=True)
 def test_configured_probe_still_gates_nginx_changes(self):
  r=self.run_env("preflight",MOCK_SSH_DOWN="1")
  self.assertNotEqual(r.returncode,0)
  self.assertIn("Owner SSH probe unavailable",r.stderr)
  self.assertEqual(self.original,self.site.read_bytes())
 def test_without_probe_device_nginx_change_uses_local_check(self):
  self.probe.unlink()
  self.assertIn("no Owner probe device configured",self.run_env("preflight").stderr)
  for command in ("preflight","stage","activate","smoke"):
   r=self.run_env(command,MOCK_SSH_DOWN="1")
   self.assertEqual(r.returncode,0,(command,r.stderr))
  self.assertIn("Smoke OK (local)",r.stdout)
  self.assertIn("auth_request /_mag_whois;",self.site.read_text())
 def test_local_check_failure_rolls_back(self):
  self.probe.unlink()
  for command in ("preflight","stage","activate"):
   self.assertEqual(self.run_env(command).returncode,0,command)
  r=self.run_env("smoke",MOCK_CURL_CODE="502")
  self.assertNotEqual(r.returncode,0)
  self.assertIn("Local ingress check failed (HTTP 502)",r.stderr)
  self.assertEqual(self.run_script("rollback").returncode,0)
  self.assertEqual(self.original,self.site.read_bytes())
 def test_unchanged_nginx_needs_no_probe_device(self):
  self.assertEqual(self.run_script("standalone").returncode,0)
  txn=self.p/"var/backups/mag-stack/check2";txn.mkdir()
  env={"MAG_NGINX_TXN":str(txn),"MOCK_SSH_DOWN":"1"}
  for command in ("preflight","stage","activate","smoke"):
   r=self.run_env(command,**env)
   self.assertEqual(r.returncode,0,(command,r.stderr))
  self.assertIn("Smoke OK (local)",r.stdout)
  self.assertFalse((txn/"nginx.changed").exists())
 def test_same_host_is_not_a_valid_external_owner_probe(self):
  env={**self.env,"MOCK_OWNER_FAIL":"0","MOCK_PROBE_SELF":"1"}
  r=subprocess.run(["bash",str(self.script),"preflight"],env=env,
                   text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0)
  self.assertIn("SECOND real Tailscale device",r.stderr)
  self.assertEqual(self.original,self.site.read_bytes())

 def test_smoke_failure_rolls_back_config_and_enabled_link(self):
  for command in ("preflight","stage","activate"):
   r=self.run_script(command)
   self.assertEqual(r.returncode,0,(command,r.stderr))
  self.assertIn("auth_request /_mag_whois;",self.site.read_text())
  self.assertNotEqual(self.run_script("smoke",fail=True).returncode,0)
  self.assertEqual(self.run_script("rollback").returncode,0)
  self.assertEqual(self.original,self.site.read_bytes())
  self.assertFalse(self.enabled.is_symlink())
  self.assertEqual(self.log.read_text().count("reload"),2)
 def test_invalid_staged_nginx_syntax_cannot_become_live(self):
  # Fake nginx -t refuses a broken candidate; transaction must restore
  # the exact previous file before there is any chance to switch a binary.
  with (self.script.parent/'mag-room.nginx.conf').open('a') as out:
   out.write('\nBROKEN\n')
  r=self.run_script('standalone')
  self.assertNotEqual(r.returncode,0)
  self.assertEqual(self.original,self.site.read_bytes())
  self.assertFalse(self.enabled.is_symlink())
  self.assertIn('nginx -t failed',r.stderr)

 def test_standalone_rolls_back_on_owner_403(self):
  r=self.run_script("standalone",fail=True)
  self.assertNotEqual(r.returncode,0)
  self.assertEqual(self.site.read_bytes(),self.original)
  self.assertFalse(self.enabled.is_symlink())
 def test_userspace_socks5_owner_smoke_with_pinned_box(self):
  self.probe.write_text(
   'MAG_DEPLOY_OWNER_SSH=chris@trusted-box\n'
   'MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\n'
   'MAG_DEPLOY_OWNER_IP=100.101.102.103\n'
   'MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055\n')
  # No OAuth credential, no kernel TUN. Real external-origin HTTPS remains
  # mandatory; mock rejects any curl that omits --socks5-hostname.
  env={**self.env,'MOCK_OWNER_FAIL':'0','MOCK_EXPECT_SOCKS':'1'}
  for command in ('preflight','stage','activate','smoke'):
   r=subprocess.run(['bash',str(self.script),command],env=env,
                    text=True,capture_output=True)
   self.assertEqual(r.returncode,0,(command,r.stderr))
  self.assertIn('auth_request /_mag_whois;',self.site.read_text())
  self.assertTrue(self.enabled.is_symlink())

 def test_userspace_bad_proxy_or_spoofed_box_is_fail_closed(self):
  base='MAG_DEPLOY_OWNER_SSH=chris@trusted-box\nMAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\nMAG_DEPLOY_OWNER_IP=100.101.102.103\n'
  for proxy in ('0.0.0.0:1055','10.0.0.1:1055','127.0.0.1:0',
                '127.0.0.1:65536','127.0.0.1:1055;echo pwn'):
   self.probe.write_text(base+f'MAG_DEPLOY_OWNER_SOCKS5="{proxy}"\n')
   r=self.run_script('preflight')
   self.assertNotEqual(r.returncode,0,proxy)
   self.assertEqual(self.original,self.site.read_bytes())
  self.probe.write_text(base.replace('100.101.102.103','100.99.255.98')+
                        'MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055\n')
  r=self.run_script('preflight')
  self.assertNotEqual(r.returncode,0,'Pinned SSH node mismatch must block')
  self.assertIn('not the pinned Tailnet node',r.stderr)

 def test_userspace_tagged_guest_cannot_satisfy_owner_smoke(self):
  self.probe.write_text(
   'MAG_DEPLOY_OWNER_SSH=chris@trusted-box\n'
   'MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\n'
   'MAG_DEPLOY_OWNER_IP=100.101.102.103\n'
   'MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055\n')
  r=self.run_script('standalone')
  self.assertEqual(r.returncode,0,r.stderr) # baseline Owner device succeeds
  self.site.write_text("old legacy vhost\n") # the next run is an nginx change again
  saved=self.site.read_bytes()
  env={**self.env,'MOCK_OWNER_FAIL':'0','MOCK_EXPECT_SOCKS':'1',
       'MOCK_GUEST_ROLE':'1'}
  r=subprocess.run(['bash',str(self.script),'standalone'],env=env,
                   text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0,'A tagged guest is NOT an Owner')
  self.assertEqual(saved,self.site.read_bytes(),'Failing cutover restores live vhost')

 def test_userspace_tagged_box_rejected_in_preflight_before_any_change(self):
  self.probe.write_text(
   'MAG_DEPLOY_OWNER_SSH=chris@trusted-box\n'
   'MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\n'
   'MAG_DEPLOY_OWNER_IP=100.101.102.103\n'
   'MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055\n')
  env={**self.env,'MOCK_TAGGED_BOX':'1'}
  r=subprocess.run(['bash',str(self.script),'preflight'],env=env,
                   text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0)
  self.assertIn('not an online, untagged Owner',r.stderr)
  self.assertEqual(self.original,self.site.read_bytes())
  self.assertFalse(self.enabled.is_symlink())

 def test_userspace_proxy_unavailable_rolls_back(self):
  self.probe.write_text(
   'MAG_DEPLOY_OWNER_SSH=chris@trusted-box\n'
   'MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com\n'
   'MAG_DEPLOY_OWNER_IP=100.101.102.103\n'
   'MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055\n')
  env={**self.env,'MOCK_OWNER_FAIL':'0','MOCK_EXPECT_SOCKS':'1',
       'MOCK_PROXY_DOWN':'1'}
  r=subprocess.run(['bash',str(self.script),'standalone'],env=env,
                   text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0,'Proxy unavailable must block cutover')
  self.assertEqual(self.original,self.site.read_bytes())
  self.assertFalse(self.enabled.is_symlink())

 def test_idempotent_install_without_oauth(self):
  for i in range(2):
   r=self.run_script("standalone")
   self.assertEqual(r.returncode,0,(i,r.stdout,r.stderr))
  self.assertIn("auth_request /_mag_whois;",self.site.read_text())
  self.assertTrue(self.enabled.is_symlink())

if __name__=="__main__":
 unittest.main(verbosity=2)

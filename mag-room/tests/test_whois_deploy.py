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
arg="§{@: -1}"
if [[ "$arg" == true ]]; then exit 0; fi
if [[ "$MOCK_OWNER_FAIL" == 1 ]]; then exit 22; fi
if [[ "$arg" == *'/api/state'* ]]; then
 echo '{"you":{"role":"owner","login":"chris.hohlfeld@gmail.com"}}'
elif [[ "$arg" == *'https://mag.stardenburg.de/'* ]]; then
 echo 200
else exit 8; fi''',
   "curl":'''#!/usr/bin/env bash
echo -n 403''',
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
 def test_missing_owner_probe_fails_before_cutover(self):
  self.probe.unlink()
  r=self.run_script("preflight")
  self.assertNotEqual(r.returncode,0)
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
 def test_standalone_rolls_back_on_owner_403(self):
  r=self.run_script("standalone",fail=True)
  self.assertNotEqual(r.returncode,0)
  self.assertEqual(self.site.read_bytes(),self.original)
  self.assertFalse(self.enabled.is_symlink())
 def test_idempotent_install_without_oauth(self):
  for i in range(2):
   r=self.run_script("standalone")
   self.assertEqual(r.returncode,0,(i,r.stdout,r.stderr))
  self.assertIn("auth_request /_mag_whois;",self.site.read_text())
  self.assertTrue(self.enabled.is_symlink())

if __name__=="__main__":
 unittest.main(verbosity=2)

"""Existing-account/token migration helpers against fake CLI + real HTTP fixture."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import http.server,json,os,pathlib,subprocess,tempfile,threading,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
class Auth(unittest.TestCase):
 def test_issue_then_commit_preserves_identity_and_lock_blocks_every_credential(self):
  with tempfile.TemporaryDirectory() as td:
   tmp=pathlib.Path(td);events=[];token='fixture-scoped-token-value-0123456789abcdef'
   class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_PATCH(self):
     n=int(self.headers['Content-Length']);body=json.loads(self.rfile.read(n));events.append((self.path,body))
     self.assertion=self.headers.get('Authorization')=='token '+token
     data=b'{}';self.send_response(200 if self.assertion else 403);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
   server=http.server.ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=server.serve_forever,daemon=True).start()
   cli=tmp/'gitea';cli.write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_DIR/cli.log"
shift 4
cmd=$1;shift
case $cmd in
 create) [[ "$*" != *magbot-agent1* ]] ;;
 list) printf 'ID Username Email\\n1 magbot-agent1 bot@local\\n' ;;
 generate-access-token) printf '%s\\n' 'fixture-scoped-token-value-0123456789abcdef' ;;
 delete|change-type) : ;;
esac
''');cli.chmod(0o755)
   rights=tmp/'rights';rights.write_text('#!/bin/sh\nexit 0\n');rights.chmod(0o755)
   session=(ROOT/'ops/gitea-ci/admin-session.sh').read_text().replace('runuser -u git -- /usr/local/bin/gitea',str(cli)).replace('/run/mag-gitea-admin.',str(tmp/'admin.')).replace('http://127.0.0.1:3000',f'http://127.0.0.1:{server.server_port}')
   sessionfile=tmp/'session.sh';sessionfile.write_text(session)
   body=(ROOT/'ops/gitea-ci/bot-auth.sh').read_text().replace('runuser -u git -- /usr/local/bin/gitea',str(cli)).replace('/usr/local/lib/mag-ci/admin-session.sh',str(sessionfile)).replace('/usr/local/sbin/mag-gitea-bot-rights',str(rights))
   script=tmp/'bot-auth.sh';script.write_text(body)
   env=dict(os.environ,FAKE_DIR=td)
   try:
    issued=subprocess.run(['bash',str(script),'issue','magbot-agent1'],env=env,capture_output=True,text=True,timeout=5)
    self.assertEqual(issued.returncode,0,issued.stderr);self.assertEqual(issued.stdout.strip(),token)
    self.assertFalse(events,'issue must not convert or lock existing account before VM receives token')
    for mode,locked in [('commit',False),('lock',True)]:
     r=subprocess.run(['bash',str(script),mode,'magbot-agent1'],env=env,capture_output=True,text=True,timeout=5)
     self.assertEqual(r.returncode,0,r.stderr);self.assertNotIn(token,r.stdout+r.stderr)
     path,body=events[-1];self.assertEqual(path,'/api/v1/admin/users/magbot-agent1');self.assertEqual(body['prohibit_login'],locked)
     self.assertFalse(body['admin']);self.assertTrue(body['restricted']);self.assertFalse(body['allow_create_organization'])
    log=(tmp/'cli.log').read_text();self.assertIn('change-type --username magbot-agent1 --user-type Bot',log)
    self.assertEqual(log.count('delete --username mag-ci-setup-'),2,'temporary privileged sessions are purged')
    self.assertFalse(list(tmp.glob('admin.*')))
   finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main(verbosity=2)

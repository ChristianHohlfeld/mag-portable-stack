"""Tests for fail-closed promotion and migration boundaries; no host mutations."""
import json,pathlib,subprocess,unittest,yaml
ROOT=pathlib.Path(__file__).resolve().parents[3]
class Migration(unittest.TestCase):
 def test_triggers_and_scope(self):
  for p in (ROOT/'.github/workflows').glob('*.yml'):
   obj=yaml.load(p.read_text(),Loader=yaml.BaseLoader)
   self.assertEqual(set(obj['on']),{'workflow_dispatch'},p.name)
  files={p.name for p in (ROOT/'.gitea/workflows').glob('*.yml')}
  self.assertTrue({'native.yml','speech.yml','pages-native.yml','e2e-bootstrap.yml'}<=files)
  for p in (ROOT/'.gitea/workflows').glob('*.yml'):
   obj=yaml.load(p.read_text(),Loader=yaml.BaseLoader)
   for job in obj['jobs'].values():
    self.assertIn(job['runs-on'],('mag-ci','mag-ci-disposable'))
    self.assertTrue(all('run' in s or 'uses' in s for s in job['steps']))
 def test_newest_attempt_and_exact_sha(self):
  query='[.workflow_runs[] | select(.head_sha==$sha)] | sort_by(.id) | last | .conclusion // "pending"'
  cases=[([], 'pending'),([{'id':1,'head_sha':'other','conclusion':'success'}],'pending'),
   ([{'id':1,'head_sha':'head','conclusion':'success'},{'id':2,'head_sha':'head','conclusion':'failure'}],'failure'),
   ([{'id':1,'head_sha':'head','conclusion':'success'},{'id':2,'head_sha':'head','conclusion':None}],'pending'),
   ([{'id':1,'head_sha':'head','conclusion':'success'}],'success')]
  for runs,expected in cases:
   result=subprocess.check_output(['jq','-r','--arg','sha','head',query],input=json.dumps({'workflow_runs':runs}).encode()).decode().strip()
   self.assertEqual(result,expected)
 def test_credentials_and_limits(self):
  runner=(ROOT/'ops/gitea/ci/mag-ci-runner.service').read_text()
  self.assertNotIn('LoadCredential',runner)
  for value in ('CPUAffinity=27 55','CPUQuota=50%','MemoryMax=8G','Nice=19','NoNewPrivileges=yes'):
   self.assertIn(value,runner)
  bridge=(ROOT/'ops/gitea/ci/bridge.sh').read_text()
  self.assertIn('context:"gitea/ci"',bridge)
  self.assertIn('count==${#expected[@]}',bridge)
  self.assertIn('latest == "$sha"',bridge)
  self.assertNotIn('set -x',bridge)
if __name__=='__main__':unittest.main()

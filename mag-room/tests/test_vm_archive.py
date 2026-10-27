#!/usr/bin/env python3
"""magbot despawn archives the bot VM directory (workspace disk + metadata)
instead of deleting it. Runs the real binary against a temporary MAGBOT_ROOT;
needs sudo -n and /usr/bin/virsh (only queried for a non-existent domain)."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import json, pathlib, shutil, subprocess, tempfile, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / 'build/magbot-native'
NAME = 'zzciarchive'  # never a real agentN

class VmArchive(unittest.TestCase):
    def setUp(self):
        if subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode:
            raise unittest.SkipTest('needs sudo -n')
        if not pathlib.Path('/usr/bin/virsh').exists():
            raise unittest.SkipTest('needs /usr/bin/virsh')
        self.tmp = tempfile.mkdtemp()
        bot = pathlib.Path(self.tmp) / 'bots' / NAME
        bot.mkdir(parents=True)
        bot.joinpath('metadata.json').write_text(json.dumps({'id': NAME, 'domain': 'magbot-' + NAME, 'git_user': 'magbot-' + NAME}))
        bot.joinpath('disk.qcow2').write_text('workspace')
        # fake gitea: records calls; the account must be locked, never deleted
        self.gitea = pathlib.Path(self.tmp) / 'gitea'
        self.gitea.write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/gitea.log"\n')
        self.gitea.chmod(0o755)

    def tearDown(self):
        subprocess.run(['sudo', '-n', 'rm', '-rf', self.tmp])

    def test_despawn_archives_workspace(self):
        r = subprocess.run(['sudo', '-n', 'env', 'MAGBOT_ROOT=' + self.tmp, 'MAGBOT_LOCK=' + self.tmp + '/lock', 'MAGBOT_GITEA_TEST=' + str(self.gitea),
                            str(BIN), 'despawn', NAME], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Archived ' + NAME, r.stdout)
        ls = subprocess.run(['sudo', '-n', 'find', self.tmp, '-name', 'disk.qcow2'], capture_output=True, text=True).stdout.split()
        self.assertEqual(len(ls), 1)
        self.assertIn('/archive/' + NAME + '-', ls[0])
        self.assertEqual(subprocess.run(['sudo', '-n', 'cat', ls[0]], capture_output=True, text=True).stdout, 'workspace')
        self.assertFalse(pathlib.Path(self.tmp, 'bots', NAME).exists())
        calls = subprocess.run(['sudo', '-n', 'cat', self.tmp + '/gitea.log'], capture_output=True, text=True).stdout
        self.assertIn('change-password --username magbot-' + NAME, calls)
        self.assertNotIn('delete', calls)
        self.assertNotIn('--purge', calls)

if __name__ == '__main__':
    unittest.main(verbosity=2)

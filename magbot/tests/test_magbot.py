import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'magbot.py'
spec = importlib.util.spec_from_file_location('magbot', SRC)
magbot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(magbot)

class TestMagbot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'magbot'
        self.root.mkdir()
        self.patcher = patch.object(magbot, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.cfg = magbot.DEFAULTS.copy()

    def test_run_stabilizes_virsh_locale(self):
        with patch.object(magbot.subprocess, 'run', return_value=subprocess.CompletedProcess(['virsh'], 0, 'Active: yes', '')) as mock:
            magbot.run(['virsh', 'net-info', 'default'])
            self.assertEqual(mock.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_ensure_network_active_does_not_start_again(self):
        self.cfg['network'] = 'default'
        info = subprocess.CompletedProcess([], 0, 'Name: default\nActive: yes\n', '')
        done = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(magbot, 'run', side_effect=[info, done]) as run_mock:
            magbot.ensure_network(self.cfg)
            self.assertEqual(run_mock.call_count, 2)
            self.assertEqual(run_mock.call_args.args[0][-2:], ['net-autostart','default'])

    def test_valid_ids(self):
        for name in ('a', 'agent-1', 'bot123'):
            self.assertEqual(magbot.valid_id(name), name)

    def test_rejects_unsafe_ids(self):
        for name in ('../root', 'x/../../home', '', 'a_', 'A', 'a'*33, 'a-', '.hidden'):
            with self.assertRaises(magbot.MagbotError):
                magbot.valid_id(name)

    def test_cloud_init_contains_guest_isolation_and_browser(self):
        content, meta = magbot.guest_cloudinit('agent1')
        self.assertIn('lock_passwd: true', content)
        self.assertIn('sudo: false', content)
        self.assertIn('qemu-guest-agent', content)
        self.assertIn('playwright', content)
        self.assertIn('chromium', content)
        self.assertIn("touch /var/lib/magbot/ready", content)
        self.assertIn('magbot-agent1', meta)
        self.assertNotIn('docker.sock', content)

    def test_admission_allows_small_vm(self):
        with patch.object(magbot, 'bytes_available', return_value={'ram':400*magbot.GIB, 'disk':500*magbot.GIB}), \
             patch.object(magbot.os, 'getloadavg', return_value=(0,0,0)):
            magbot.host_admission(self.cfg, [], 2, 4, 20)

    def test_admission_blocks_count_cpu_mem_disk_load(self):
        existing = [{'vcpus':2,'ram_gib':4,'disk_gib':20}]*4
        with patch.object(magbot, 'bytes_available', return_value={'ram':400*magbot.GIB, 'disk':500*magbot.GIB}), \
             patch.object(magbot.os, 'getloadavg', return_value=(0,0,0)):
            with self.assertRaisesRegex(magbot.MagbotError, 'count'):
                magbot.host_admission(self.cfg, existing, 2,4,20)
            with self.assertRaisesRegex(magbot.MagbotError, 'vCPU'):
                magbot.host_admission(self.cfg, existing[:3], 3,4,20)
            with self.assertRaisesRegex(magbot.MagbotError, 'Per-VM'):
                magbot.host_admission(self.cfg, [], 8,4,20)
        with patch.object(magbot, 'bytes_available', return_value={'ram':65*magbot.GIB,'disk':500*magbot.GIB}), \
             patch.object(magbot.os, 'getloadavg', return_value=(0,0,0)):
            with self.assertRaisesRegex(magbot.MagbotError, 'RAM'):
                magbot.host_admission(self.cfg, [], 2,4,20)
        with patch.object(magbot, 'bytes_available', return_value={'ram':400*magbot.GIB,'disk':115*magbot.GIB}), \
             patch.object(magbot.os, 'getloadavg', return_value=(0,0,0)):
            with self.assertRaisesRegex(magbot.MagbotError, 'disk'):
                magbot.host_admission(self.cfg, [], 2,4,20)
        with patch.object(magbot, 'bytes_available', return_value={'ram':400*magbot.GIB,'disk':500*magbot.GIB}), \
             patch.object(magbot.os, 'cpu_count', return_value=56), \
             patch.object(magbot.os, 'getloadavg', return_value=(50,50,50)):
            with self.assertRaisesRegex(magbot.MagbotError, 'load'):
                magbot.host_admission(self.cfg, [], 2,4,20)

    def test_definitions_validate_marker(self):
        p=self.root/'bots'/'agent1'; p.mkdir(parents=True)
        (p/'metadata.json').write_text(json.dumps({'id':'agent1','domain':'magbot-agent1','vcpus':2,'ram_gib':4,'disk_gib':20}))
        self.assertEqual(magbot.definitions()[0]['id'],'agent1')
        (p/'metadata.json').write_text(json.dumps({'id':'wrong','domain':'magbot-agent1'}))
        with self.assertRaises(magbot.MagbotError): magbot.definitions()

    def test_teardown_removes_only_managed_files(self):
        p=self.root/'bots'/'agent1';p.mkdir(parents=True)
        (p/'metadata.json').write_text(json.dumps({'id':'agent1','domain':'magbot-agent1'}))
        (p/'root.qcow2').write_bytes(b'userdata')
        other=self.root/'other';other.write_text('keep')
        with patch.object(magbot,'vm_exists',return_value=False):
            self.assertTrue(magbot.teardown('agent1'))
        self.assertFalse(p.exists());self.assertEqual(other.read_text(),'keep')

    def test_teardown_refuses_missing_marker(self):
        p=self.root/'bots'/'agent1';p.mkdir(parents=True)
        (p/'root.qcow2').write_text('important')
        with self.assertRaisesRegex(magbot.MagbotError,'marker'):
            magbot.teardown('agent1')
        self.assertTrue(p.exists())

    def test_teardown_refuses_symlink(self):
        outside=self.root/'keep';outside.mkdir();(outside/'metadata.json').write_text('secret')
        (self.root/'bots').mkdir()
        (self.root/'bots'/'agent1').symlink_to(outside)
        with self.assertRaises(magbot.MagbotError): magbot.teardown('agent1')
        self.assertTrue(outside.exists())

    def test_registry_atomic_write(self):
        p = self.root / 'bots' / 'agent1'
        p.mkdir(parents=True)
        magbot.write_registry(p / 'metadata.json', {'id':'agent1', 'domain':'magbot-agent1'})
        self.assertEqual(json.loads((p / 'metadata.json').read_text())['id'], 'agent1')
        self.assertEqual([x for x in p.iterdir() if '.tmp' in x.name], [])

    def test_inspect_bot_missing_registry_is_non_destructive(self):
        with patch.object(magbot, 'vm_exists', return_value=True), \
             patch.object(magbot, 'vm_state', return_value='running'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = magbot.inspect_bot('agent1')
            details = json.loads(buf.getvalue())
            self.assertEqual(result, 1)
            self.assertTrue(details['vm_defined'])
            self.assertFalse(details['registry_valid'])
            self.assertFalse((self.root/'bots'/'agent1').exists())

    def test_inspect_bot_valid_registry(self):
        p = self.root / 'bots' / 'agent1'
        p.mkdir(parents=True)
        magbot.write_registry(p / 'metadata.json', {'id':'agent1', 'domain':'magbot-agent1'})
        with patch.object(magbot, 'vm_exists', return_value=True), \
             patch.object(magbot, 'vm_state', return_value='running'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = magbot.inspect_bot('agent1')
            self.assertEqual(result, 0)
            self.assertTrue(json.loads(buf.getvalue())['registry_valid'])

    def test_ready_handles_no_qga(self):
        with patch.object(magbot,'vm_state',return_value='running'), \
             patch.object(magbot,'guest_exec',side_effect=magbot.MagbotError('qga timeout')):
            self.assertEqual(magbot.ready('abc'),'booting')

    def test_ready_distinguishes_failed_from_ready(self):
        with patch.object(magbot,'vm_state',return_value='running'), \
             patch.object(magbot,'guest_exec',side_effect=[(1,'',''),(0,'','')]):
            self.assertEqual(magbot.ready('abc'),'failed')
        with patch.object(magbot,'vm_state',return_value='running'), \
             patch.object(magbot,'guest_exec',return_value=(0,'','')):
            self.assertEqual(magbot.ready('abc'),'ready')

if __name__=='__main__': unittest.main(verbosity=2)

"""Watchdog contract: observational only, never mutate running VMs."""
import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'pm2' / 'magbot-watch.py'
spec = importlib.util.spec_from_file_location('magbot_watch', SRC)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

class WatchdogTest(unittest.TestCase):
    def test_only_status_command_and_resource_reading(self):
        class Stats:
            f_bavail = 150 * 1024 * 1024
            f_frsize = 1024
        with patch.object(watch.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='No bots\n', stderr='')) as cmd, \
             patch.object(watch.os, 'statvfs', return_value=Stats()):
            status, info, warnings = watch.check()
        self.assertEqual(status,'No bots')
        self.assertEqual(cmd.call_args.args[0], ['/usr/bin/sudo','-n','/usr/local/bin/magbot','status'])
        self.assertGreater(info['disk_gib'], 100)
        self.assertNotIn('LOW HOST DISK', warnings)

    def test_unavailable_status_reported_not_restarted(self):
        class Stats:
            f_bavail = 10 * 1024 * 1024
            f_frsize = 1024
        with patch.object(watch.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='sudo: fail')), \
             patch.object(watch.os, 'statvfs', return_value=Stats()):
            status, _, warnings = watch.check()
        self.assertIn('ERROR:', status)
        self.assertIn('LOW HOST DISK', warnings)

if __name__ == '__main__': unittest.main()

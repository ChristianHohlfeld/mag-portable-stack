#!/usr/bin/env python3
"""Isolated lifecycle regression tests; no host accounts or VMs are touched."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/gitea-ci/bot-auth.sh"
DRIFT = ROOT / "ops/agent-drift.sh"

class CleanupTests(unittest.TestCase):
    def run_lock(self, patch="404", lookup="404", listing="[]", admin="true"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fake = tmp / "admin-session.sh"
            fake.write_text(r'''
session="$TEST_TMP"
admin=temporary-test-admin
api() {
  case "$1 $2" in
    "PATCH /admin/users/magbot-deepseek-numa0") api_status="$TEST_PATCH";;
    "GET /user") api_status=200
      printf '{"login":"temporary-test-admin","is_admin":%s}' "$TEST_ADMIN" > "$session/reply";;
    "GET /users/magbot-deepseek-numa0") api_status="$TEST_LOOKUP";;
    "GET /admin/users?q=magbot-deepseek-numa0&limit=1") api_status=200
      printf '%s' "$TEST_LIST" > "$session/reply";;
    *) api_status=500;;
  esac
  [[ $api_status == 200 ]]
}
''')
            # Only the test copy bypasses root and sources a fake API session.
            script = AUTH.read_text().replace("$EUID == 0 && ", "").replace(
                "/usr/local/lib/mag-ci/admin-session.sh", str(fake))
            p = tmp / "bot-auth.sh"
            p.write_text(script)
            env = dict(os.environ, TEST_TMP=str(tmp), TEST_PATCH=patch,
                       TEST_LOOKUP=lookup, TEST_LIST=listing, TEST_ADMIN=admin)
            return subprocess.run(["bash", str(p), "lock", "magbot-deepseek-numa0"],
                                  env=env, capture_output=True, text=True)

    def test_verified_missing_account_is_already_revoked(self):
        r = self.run_lock()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no credential to revoke", r.stderr)

    def test_existing_account_can_be_locked(self):
        self.assertEqual(self.run_lock(patch="200").returncode, 0)

    def test_ambiguity_or_api_failure_never_succeeds(self):
        for kwargs in ({"patch": "403"}, {"patch": "500"}, {"patch": ""},
                       {"lookup": "200"}, {"lookup": "403"},
                       {"listing": '[{"login":"magbot-deepseek-numa0"}]'},
                       {"listing": "{}"}, {"admin": "false"}):
            with self.subTest(**kwargs):
                self.assertNotEqual(self.run_lock(**kwargs).returncode, 0)

    def test_drift_includes_legacy_but_preserves_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "agents.json").write_text(
                '[{"vm":"agent2","pending_vm":"deepseek-numa1"}]')
            domains = tmp / "domains"
            domains.write_text("magbot-agent2\nmagbot-deepseek-numa0\n"
                               "magbot-deepseek-numa1\nmagbot-unrelated\n")
            env = dict(os.environ, MAG_ROOM_STATE=str(tmp),
                       MAGBOT_ROOT=str(tmp), MAG_DRIFT_UNIT_DIR=str(tmp),
                       MAG_DRIFT_DOMAINS_FILE=str(domains), MAG_DRIFT_DRY_RUN="1")
            r = subprocess.run(["bash", str(DRIFT)], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("remove unreferenced deepseek-numa0", r.stdout)
            self.assertIn("keep deepseek-numa1", r.stdout)
            self.assertIn("keep agent2", r.stdout)
            self.assertNotIn("unrelated", r.stdout)

if __name__ == "__main__":
    unittest.main()

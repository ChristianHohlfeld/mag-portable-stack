#!/usr/bin/env python3
"""Tests fuer gitmem.py: lokales Repo + Bare-Remote (simuliert Gitea), MCP ueber stdio."""
import json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); SRV = os.path.join(HERE, "gitmem.py")

class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.remote = os.path.join(self.t, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        self.env = dict(os.environ, GITMEM_DIR=os.path.join(self.t, "mem"), GITMEM_REMOTE=self.remote,
                        GITMEM_CREDENTIALS=os.path.join(self.t, "none"), HOME=self.t,
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def cli(self, *a, stdin=None):
        return subprocess.run([sys.executable, SRV, *a], env=self.env, capture_output=True, text=True, input=stdin)
    def test_cli_roundtrip_and_push(self):
        r = self.cli("remember", "projekt/ziel", "Version 1"); self.assertIn("gepusht", r.stdout, r.stderr)
        self.cli("remember", "projekt/ziel", "Version 2")
        self.assertEqual(self.cli("retrieve", "projekt/ziel").stdout.strip(), "Version 2")
        h = self.cli("history", "projekt/ziel").stdout
        self.assertIn("Version 1", h); self.assertIn("Version 2", h)
        self.assertIn("projekt_ziel", self.cli("search", "version").stdout)
        log = subprocess.run(["git", "-C", self.remote, "log", "--oneline", "main"], capture_output=True, text=True).stdout
        self.assertEqual(log.count("remember projekt_ziel"), 2)
        self.assertEqual(self.cli("retrieve", "fehlt").returncode, 1)
    def test_restore_from_backup(self):
        self.cli("remember", "a", "eins")
        self.env["GITMEM_DIR"] = os.path.join(self.t, "mem2")
        self.assertEqual(self.cli("retrieve", "a").stdout.strip(), "eins")
    def test_mcp_stdio(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "remember", "arguments": {"key": "k", "value": "v"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "retrieve", "arguments": {"key": "k"}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "retrieve", "arguments": {"key": "nix"}}}]
        p = subprocess.run([sys.executable, SRV], env=self.env, capture_output=True, text=True,
                           input="\n".join(json.dumps(m) for m in msgs) + "\n")
        out = [json.loads(l) for l in p.stdout.splitlines()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3, 4, 5])
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "git-memory")
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]}, {"remember", "retrieve", "history", "search"})
        self.assertIn("gepusht", out[2]["result"]["content"][0]["text"])
        self.assertEqual(out[3]["result"]["content"][0]["text"], "v")
        self.assertTrue(out[4]["result"]["isError"])

if __name__ == "__main__":
    unittest.main()

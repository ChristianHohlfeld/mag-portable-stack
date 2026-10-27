#!/usr/bin/env python3
"""Serve/Funnel config, nginx exposure and ROOT_URL. No tailscaled, no secrets."""
import importlib.util
import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def load_merge():
    path = ROOT / "ops/tailscale/merge-gitea-funnel.py"
    spec = importlib.util.spec_from_file_location("merge_gitea_funnel", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FunnelConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_merge()

    def test_checked_in_config_matches_generator(self):
        generated = self.m.merge({})
        self.m.assert_public_gitea_only(generated)
        checked = json.loads((ROOT / "ops/tailscale/gitea-funnel.json").read_text())
        self.assertEqual(generated, checked)
        self.assertEqual(generated["AllowFunnel"], {"mag.tail3e02ae.ts.net:443": True})
        block = generated["TCP"]["443"]
        self.assertIs(block["HTTPS"], True)
        self.assertNotIn("TCPForward", block)
        self.assertNotIn("ProxyProtocol", block)
        self.assertEqual(
            generated["Web"]["mag.tail3e02ae.ts.net:443"]["Handlers"],
            {"/": {"Proxy": "http://127.0.0.1:3000"}},
        )

    def test_only_gitea_stays_public_and_tailnet_serves_remain(self):
        current = {
            "TCP": {
                "443": {"HTTPS": True},
                "8443": {"HTTPS": True},
                "8446": {"HTTPS": True, "TCPForward": "127.0.0.1:8766"},
                "9000": {"HTTPS": True},
            },
            "Web": {
                "mag.tail3e02ae.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3010"}}},
                "mag.tail3e02ae.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18767"}}},
                "mag.tail3e02ae.ts.net:8446": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:1"}, "/extra": {"Text": "keep"}}},
                "mag.tail3e02ae.ts.net:9000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9"}}},
            },
            "AllowFunnel": {
                "mag.tail3e02ae.ts.net:8443": True,
                "mag.tail3e02ae.ts.net:8446": True,
                "mag.tail3e02ae.ts.net:9000": True,
                "mag.tail3e02ae.ts.net:10000": True,
            },
            "Services": {"svc:keep": {"Tun": True}},
        }
        merged = self.m.merge(current)
        self.m.assert_public_gitea_only(merged)
        self.assertEqual(merged, self.m.merge(merged))
        self.assertEqual(merged["Services"], {"svc:keep": {"Tun": True}})
        self.assertEqual(
            merged["Web"]["mag.tail3e02ae.ts.net:9000"]["Handlers"]["/"]["Proxy"],
            "http://127.0.0.1:9",
        )
        self.assertEqual(merged["Web"]["mag.tail3e02ae.ts.net:8446"]["Handlers"]["/extra"], {"Text": "keep"})
        self.assertNotIn("TCPForward", merged["TCP"]["8446"])
        self.assertIs(merged["TCP"]["443"]["HTTPS"], True)
        self.assertNotIn("TCPForward", merged["TCP"]["443"])
        self.assertEqual(
            merged["Web"]["mag.tail3e02ae.ts.net:443"]["Handlers"],
            {"/": {"Proxy": "http://127.0.0.1:3000"}},
        )
        for port in ("8443", "8445", "8446", "10000"):
            self.assertNotIn(f"mag.tail3e02ae.ts.net:{port}", merged["AllowFunnel"])

    def test_null_current_config(self):
        self.m.assert_public_gitea_only(self.m.merge(None))

    def test_dry_run_prints_the_same_config(self):
        out = subprocess.check_output(
            ["bash", "ops/tailscale/apply-gitea-funnel.sh", "--dry-run"],
            cwd=ROOT, text=True,
        )
        lines = out.strip().splitlines()
        self.assertIn("tailnet-only :8443 :8445 :8446", lines[-1])
        self.assertIn("http://127.0.0.1:3000", lines[-1])
        self.assertNotIn("tcp://", lines[-1])
        self.assertNotIn("8766", lines[-1])
        doc = json.loads("\n".join(lines[:-1]))
        self.m.assert_public_gitea_only(doc)

    def test_root_url_matches_funnel_origin(self):
        origin = self.m.FUNNEL_ORIGIN
        self.assertEqual(origin, "https://mag.tail3e02ae.ts.net")
        ini = (ROOT / "ops/gitea/app.ini.example").read_text()
        policy = json.loads((ROOT / "ops/gitea-ci/policy.json").read_text())
        nginx = (ROOT / "ops/gitea-ci/public.nginx.in").read_text()
        proxy = (ROOT / "ops/gitea-ci/gitea-proxy.inc").read_text()
        configure = (ROOT / "ops/gitea-ci/configure-gitea.sh").read_text()
        self.assertIn(f"ROOT_URL = {origin}/", ini)
        self.assertIn(f"DOMAIN = {origin.removeprefix('https://')}", ini)
        self.assertEqual(policy["public_origin"], origin)
        self.assertEqual(policy["public_hostname"], origin.removeprefix("https://"))
        self.assertIn(f"ROOT_URL {origin}/", configure)
        self.assertIn("listen 127.0.0.1:4443 ssl http2 proxy_protocol;", nginx)
        self.assertIn("ssl_reject_handshake on;", nginx)
        self.assertIn("/etc/mag-nginx/tls/tailscale/mag.tail3e02ae.ts.net.crt", nginx)
        self.assertNotIn("listen 100.123.114.16", nginx)
        self.assertNotIn("listen 443", nginx)
        self.assertNotIn("3010", nginx)
        self.assertNotIn("listen 80", nginx)
        self.assertNotIn("git.mag.stardenburg.de", nginx)
        self.assertNotIn("acme-challenge", nginx)
        self.assertNotIn("8766", nginx)
        self.assertNotIn("18767", nginx)
        self.assertIn("limit_req zone=mag_git_login", nginx)
        self.assertIn("location ^~ /user/two_factor", nginx)
        self.assertIn(origin.removeprefix("https://"), proxy)
        bridge = (ROOT / "ops/gitea-ci/bridge.cpp").read_text()
        self.assertIn('origin+"/"+repo+"/actions"', bridge)
        self.assertNotIn("git.mag.stardenburg.de", bridge)
        self.assertNotIn("git.mag.stardenburg.de", (ROOT / "ops/gitea-ci/install.sh").read_text())
        self.assertNotIn("public.env", (ROOT / "ops/gitea-ci/install.sh").read_text())
        self.assertNotIn("install-lego.sh", (ROOT / "ops/gitea-ci/install.sh").read_text())
        install = (ROOT / "ops/gitea-ci/install.sh").read_text()
        self.assertIn("--haproxy-protocol", install)
        self.assertIn("mag.tail3e02ae.ts.net:4443", install)
        self.assertNotIn("127.0.0.1:3010", install)
        apply = (ROOT / "ops/tailscale/apply-gitea-funnel.sh").read_text()
        self.assertIn('attr": ["funnel"]', apply)
        self.assertIn("tailscale funnel status", apply)
        self.assertIn("mag-gitea-funnel.service", apply)
        self.assertIn("mag-gitea-funnel.timer", apply)
        self.assertIn("keep_nginx=1", apply)
        self.assertIn("listen[[:space:]]+100\\.123\\.114\\.16:443", apply)
        self.assertIn("MAG_GITEA_FUNNEL_REQUIRE_PUBLIC", apply)
        unit = (ROOT / "ops/tailscale/mag-gitea-funnel.service").read_text()
        self.assertIn("apply-gitea-funnel.sh", unit)
        self.assertIn("RemainAfterExit=yes", unit)
        timer = (ROOT / "ops/tailscale/mag-gitea-funnel.timer").read_text()
        self.assertIn("Unit=mag-gitea-funnel.service", timer)
        self.assertIn("OnUnitActiveSec=5min", timer)
        boot = (ROOT / "ops/boot/ensure-boot.sh").read_text()
        self.assertIn("mag-gitea-funnel.service", boot)
        self.assertIn("mag-gitea-funnel.timer", boot)

    def test_retarget_moves_only_the_tailscale_listener(self):
        old = (
            "    listen 100.123.114.16:443 ssl http2;\n"
            "    listen 100.123.114.16:443 ssl;\n"
            "listen 192.0.2.10:443 ssl;\n"
        )
        moved = self.m.retarget_nginx(old)
        self.assertEqual(
            moved,
            "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
            "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
            "listen 192.0.2.10:443 ssl;\n",
        )
        self.assertEqual(self.m.retarget_nginx(moved), moved)
        self.assertEqual(self.m.retarget_nginx(self.m.NGINX_LISTEN + "\n"), self.m.NGINX_LISTEN + "\n")

    def test_host_runner_workflow_matches_registered_labels(self):
        import yaml
        host = yaml.safe_load((ROOT / ".gitea/workflows/host-runner.yml").read_text())
        on = host.get("on", host.get(True))
        self.assertIn("push", on)
        self.assertIn("pull_request", on)
        self.assertEqual(set(host["jobs"]["ping"]["runs-on"]), {"self-hosted", "mag"})
        self.assertLessEqual(host["jobs"]["ping"]["timeout-minutes"], 5)
        labels = yaml.safe_load((ROOT / "ops/runners/gitea-runner.yaml").read_text())["runner"]["labels"]
        have = {item.split(":", 1)[0] for item in labels}
        self.assertTrue({"self-hosted", "mag"} <= have, labels)
        ci = yaml.safe_load((ROOT / ".gitea/workflows/mag-ci.yml").read_text())
        ci_on = ci.get("on", ci.get(True))
        self.assertEqual(ci["jobs"]["checks"]["runs-on"], "mag-ci-vm")
        self.assertNotIn("push", ci_on)
        self.assertNotIn("pull_request", ci_on)
        text = (ROOT / ".gitea/workflows/host-runner.yml").read_text()
        self.assertIn("timeout --kill-after=5s 30s", text)
        self.assertNotRegex(text, r"ListenStream|^listen ")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Serve/Funnel config, nginx exposure and ROOT_URL. No tailscaled, no secrets."""
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _write_exec(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _nginx_t_green(files):
    """Real nginx -t on retargeted vhosts. Dummy certs, high loopback port."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        crt, key = tmp / "dummy.crt", tmp / "dummy.key"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-days", "1", "-keyout", str(key), "-out", str(crt),
                "-subj", "/CN=mag.stardenburg.de",
            ],
            check=True, capture_output=True,
        )
        includes = []
        for path in files:
            text = path.read_text()
            text = text.replace(
                "127.0.0.1:4443 ssl http2 proxy_protocol",
                "127.0.0.1:18443 ssl http2",
            )
            if "ssl_certificate" not in text:
                text = text.replace(
                    "ssl http2;",
                    f"ssl http2;\n    ssl_certificate {crt};\n"
                    f"    ssl_certificate_key {key};",
                )
            dest = tmp / path.name
            dest.write_text(text)
            includes.append(f"include {dest};")
        conf = tmp / "nginx.conf"
        conf.write_text(
            f"pid {tmp}/nginx.pid;\nerror_log {tmp}/err;\n"
            f"events {{}}\nhttp {{ access_log off; {' '.join(includes)} }}\n"
        )
        proc = subprocess.run(
            ["nginx", "-t", "-c", str(conf), "-p", str(tmp)],
            capture_output=True, text=True,
        )
        log = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise AssertionError(log)
        return log


def _leftover_host(tree):
    """Live mag after #137: room on 4443, bots/pages still on Tailscale :443."""
    nginx = tree / "nginx"
    mag_ci = tree / "mag-ci"
    systemd = tree / "systemd"
    lib = tree / "lib"
    txn = tree / "txn"
    bin_dir = tree / "bin"
    serve = tree / "serve.json"
    for path in (
        nginx / "sites-available",
        nginx / "sites-enabled",
        nginx / "conf.d",
        nginx / "snippets",
        mag_ci,
        systemd,
        lib,
        txn / "funnel-snapshot" / "snap" / "files",
        bin_dir,
    ):
        path.mkdir(parents=True)
    # Pre-existing snapshot from a rolled-back deploy. `cp -a snap dest`
    # used to nest as dest/snap and abort or leave restore empty.
    (txn / "funnel-snapshot" / "manifest").write_text("stale\n")
    (txn / "funnel-snapshot" / "snap" / "manifest").write_text("nested-stale\n")
    # Live leftover: sites-enabled is a regular file still on Tailscale :443
    # (rewrite_path used to follow a symlink and drop this path).
    room_443 = (
        "server {\n    listen 100.123.114.16:443 ssl http2;\n"
        "    server_name mag.stardenburg.de;\n}\n"
    )
    (nginx / "sites-available" / "mag-room.conf").write_text(room_443)
    (nginx / "sites-enabled" / "mag-room.conf").write_text(room_443)
    (nginx / "conf.d" / "mag-bots.conf").write_text(
        "server {\n    listen 100.123.114.16:443 ssl http2;\n"
        "    server_name ~^agent[0-9]+\\.bot\\.stardenburg\\.de$;\n}\n"
    )
    (nginx / "conf.d" / "mag-pages.conf").write_text(
        "server {\n    listen 100.123.114.16:443 ssl http2;\n"
        "    server_name pages.stardenburg.de;\n}\n"
        "server {\n    listen 100.123.114.16:443 ssl http2;\n"
        "    server_name ~^[a-z0-9-]+\\.pages\\.stardenburg\\.de$;\n}\n"
    )
    serve.write_text(
        json.dumps(
            {
                "TCP": {"8443": {"HTTPS": True}},
                "Web": {
                    "mag.tail3e02ae.ts.net:8443": {
                        "Handlers": {"/": {"Proxy": "http://127.0.0.1:18767"}}
                    }
                },
                "AllowFunnel": {},
            }
        )
    )
    _write_exec(
        bin_dir / "nginx",
        """#!/usr/bin/env bash
set -euo pipefail
root=${MAG_GITEA_FUNNEL_NGINX:?}
if [[ ${1:-} == -T ]]; then
  for f in "$root"/sites-enabled/* "$root"/conf.d/*; do
    [[ -e $f || -L $f ]] || continue
    printf '# configuration file %s:\\n' "$f"
    cat -- "$f"
  done
  exit 0
fi
exit 0
""",
    )
    _write_exec(bin_dir / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
    _write_exec(bin_dir / "ss", "#!/usr/bin/env bash\nexit 0\n")
    _write_exec(
        bin_dir / "tailscale",
        """#!/usr/bin/env bash
if [[ ${1:-} == status ]]; then
  echo '{"Self":{"DNSName":"mag.tail3e02ae.ts.net."}}'
  exit 0
fi
if [[ ${1:-} == funnel ]]; then
  echo 'No funnel'
  exit 0
fi
exit 0
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "MAG_GITEA_FUNNEL_TEST": "1",
            "MAG_GITEA_FUNNEL_NGINX": str(nginx),
            "MAG_GITEA_FUNNEL_MAG_CI": str(mag_ci),
            "MAG_GITEA_FUNNEL_SYSTEMD": str(systemd),
            "MAG_GITEA_FUNNEL_LIB": str(lib),
            "MAG_GITEA_FUNNEL_SERVE": str(serve),
            "MAG_NGINX_TXN": str(txn),
            "MAG_GITEA_FUNNEL_REQUIRE_PUBLIC": "0",
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )
    return nginx, txn, env


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
        self.assertIn("merge-gitea-funnel.py\" candidates", apply)
        self.assertIn("merge-gitea-funnel.py\" scan", apply)
        self.assertIn('"$nginx_root/conf.d"', apply)
        self.assertIn("/etc/nginx", apply)
        self.assertIn("MAG_GITEA_FUNNEL_REQUIRE_PUBLIC", apply)
        self.assertIn('gitea funnel: failed at line', apply)
        self.assertIn("BASH_COMMAND", apply)
        self.assertIn("command failed:", apply)
        self.assertIn("trap 'on_err", apply)
        self.assertIn("trap cleanup EXIT", apply)
        self.assertLess(
            apply.index("trap cleanup EXIT"),
            apply.index('issue-tailscale-cert.sh" --no-reload'),
        )
        self.assertIn('cp -a -- "$snapdir"/.', apply)
        self.assertIn("MAG_GITEA_FUNNEL_TEST", apply)
        activate = (ROOT / "ops/private-access/deploy-nginx.sh").read_text()
        self.assertIn("A managed nginx vhost still listens on the Tailscale :443", activate)
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
            "    listen @TAILNET_BIND@:443 ssl http2;\n"
            "listen 192.0.2.10:443 ssl;\n"
        )
        moved = self.m.retarget_nginx(old)
        self.assertEqual(
            moved,
            "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
            "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
            "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
            "listen 192.0.2.10:443 ssl;\n",
        )
        self.assertEqual(self.m.retarget_nginx(moved), moved)
        self.assertEqual(self.m.retarget_nginx(self.m.NGINX_LISTEN + "\n"), self.m.NGINX_LISTEN + "\n")

    def test_scan_without_args_is_usage_error(self):
        scan = subprocess.run(
            ["python3", "ops/tailscale/merge-gitea-funnel.py", "scan"],
            cwd=ROOT, input="", text=True, capture_output=True,
        )
        self.assertEqual(scan.returncode, 2, scan.stderr)
        self.assertIn("usage:", scan.stderr)
        self.assertIn("nginx -T dump", scan.stderr)

    def test_funnel_fails_if_conf_d_still_listens_on_tailnet_443(self):
        # Live mag after #156 rollback: room (sites-enabled) + bots + pages.
        dump = (
            "# configuration file /etc/nginx/sites-enabled/mag-room.conf:\n"
            "    listen 100.123.114.16:443 ssl http2;\n"
            "# configuration file /etc/nginx/conf.d/mag-bots.conf:\n"
            "    listen 100.123.114.16:443 ssl http2;\n"
            "# configuration file /etc/nginx/conf.d/mag-pages.conf:\n"
            "    listen 100.123.114.16:443 ssl http2;\n"
            "    listen 100.123.114.16:443 ssl http2;\n"
        )
        leftover = self.m.files_from_nginx_t(dump)
        self.assertEqual(
            leftover,
            [
                "/etc/nginx/sites-enabled/mag-room.conf",
                "/etc/nginx/conf.d/mag-bots.conf",
                "/etc/nginx/conf.d/mag-pages.conf",
            ],
        )
        scan = subprocess.run(
            ["python3", "ops/tailscale/merge-gitea-funnel.py", "scan"],
            cwd=ROOT, input=dump, text=True, capture_output=True,
        )
        self.assertEqual(scan.returncode, 1, scan.stderr)
        self.assertIn("mag-room.conf", scan.stderr)
        self.assertIn("sites-enabled", scan.stderr)
        self.assertIn("mag-bots.conf", scan.stderr)
        self.assertIn("mag-pages.conf", scan.stderr)

        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tree = pathlib.Path(raw)
            confd = tree / "conf.d"
            enabled = tree / "sites-enabled"
            available = tree / "sites-available"
            confd.mkdir()
            enabled.mkdir()
            available.mkdir()
            room = (
                "server {\n"
                "    listen 127.0.0.1:4443 ssl http2 proxy_protocol;\n"
                "    server_name mag.stardenburg.de;\n"
                "}\n"
            )
            bots = (
                "server {\n"
                "    listen 100.123.114.16:443 ssl http2;\n"
                "    server_name ~^agent[0-9]+\\.bot\\.stardenburg\\.de$;\n"
                "}\n"
            )
            pages = (
                "server {\n"
                "    listen 100.123.114.16:443 ssl http2;\n"
                "    server_name pages.stardenburg.de;\n"
                "}\n"
                "server {\n"
                "    listen 100.123.114.16:443 ssl http2;\n"
                "    server_name ~^[a-z0-9-]+\\.pages\\.stardenburg\\.de$;\n"
                "}\n"
            )
            (available / "mag-room.conf").write_text(room)
            (enabled / "mag-room.conf").symlink_to("../sites-available/mag-room.conf")
            (confd / "mag-bots.conf").write_text(bots)
            (confd / "mag-pages.conf").write_text(pages)
            (available / "mag-room-old.conf").write_text(
                "server {\n    listen 100.123.114.16:443 ssl http2;\n}\n"
            )
            (enabled / "mag-room-old.conf").symlink_to("../sites-available/mag-room-old.conf")
            found = self.m.candidate_files("", [str(tree)])
            names = [pathlib.Path(p).name for p in found]
            self.assertEqual(
                names,
                ["mag-bots.conf", "mag-pages.conf", "mag-room-old.conf", "mag-room-old.conf"],
            )
            self.assertTrue(any(p.endswith("sites-available/mag-room-old.conf") for p in found))
            self.assertTrue(any(p.endswith("sites-enabled/mag-room-old.conf") for p in found))
            for path in found:
                pathlib.Path(path).write_text(
                    self.m.retarget_nginx(pathlib.Path(path).read_text())
                )
            after = "\n".join(
                f"# configuration file {path}:\n{pathlib.Path(path).read_text()}"
                for path in (
                    enabled / "mag-room.conf",
                    confd / "mag-bots.conf",
                    confd / "mag-pages.conf",
                )
            )
            self.assertEqual(self.m.files_from_nginx_t(after), [])
            self.assertFalse(self.m.has_tailnet_443(after))
            self.assertEqual((confd / "mag-bots.conf").read_text().count(self.m.NGINX_LISTEN), 1)
            self.assertEqual((confd / "mag-pages.conf").read_text().count(self.m.NGINX_LISTEN), 2)
            # Funnel is on only after every managed leftover listen is gone.
            scan_ok = subprocess.run(
                ["python3", "ops/tailscale/merge-gitea-funnel.py", "scan"],
                cwd=ROOT, input=after, text=True, capture_output=True,
            )
            self.assertEqual(scan_ok.returncode, 0, scan_ok.stderr)

        templates = (
            ROOT / "ops/private-access/mag-room.nginx.conf",
            ROOT / "ops/bots/mag-bots.nginx.in",
            ROOT / "ops/pages/mag-pages.nginx.in",
            ROOT / "ops/gitea-ci/public.nginx.in",
        )
        for path in templates:
            text = path.read_text()
            self.assertFalse(self.m.has_tailnet_443(text), path.name)
            self.assertIn(self.m.NGINX_LISTEN, text)
        public = (ROOT / "ops/pages/mag-pages-public.nginx.in").read_text()
        self.assertFalse(self.m.has_tailnet_443(public))
        self.assertNotIn("100.123.114.16", public)

    def test_apply_on_host_with_leftover_conf_d_and_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            nginx, txn, env = _leftover_host(pathlib.Path(raw))
            proc = subprocess.run(
                ["bash", "ops/tailscale/apply-gitea-funnel.sh"],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            log = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, log)
            self.assertIn("tailscale cert: mag.tail3e02ae.ts.net", log)
            self.assertIn("gitea funnel: moved", log)
            self.assertIn("mag-bots.conf", log)
            self.assertIn("mag-pages.conf", log)
            self.assertIn("mag-room.conf", log)
            self.assertNotIn("gitea funnel: failed at line", log)
            self.assertNotIn("previous listeners restored", log)
            room = (nginx / "sites-enabled" / "mag-room.conf").read_text()
            bots = (nginx / "conf.d" / "mag-bots.conf").read_text()
            pages = (nginx / "conf.d" / "mag-pages.conf").read_text()
            self.assertIn(self.m.NGINX_LISTEN, room)
            self.assertIn(self.m.NGINX_LISTEN, bots)
            self.assertEqual(pages.count(self.m.NGINX_LISTEN), 2)
            self.assertNotIn("100.123.114.16:443", room)
            self.assertNotIn("100.123.114.16:443", bots)
            self.assertNotIn("100.123.114.16:443", pages)
            _nginx_t_green(
                [
                    nginx / "sites-enabled" / "mag-room.conf",
                    nginx / "conf.d" / "mag-bots.conf",
                    nginx / "conf.d" / "mag-pages.conf",
                ]
            )
            snap = txn / "funnel-snapshot"
            self.assertTrue((snap / "serve.json").is_file(), list(snap.iterdir()) if snap.exists() else snap)
            self.assertTrue((snap / "manifest").is_file())
            self.assertTrue((snap / "saved.paths").is_file())
            self.assertFalse((snap / "snap").exists(), "pre-existing snapshot must not nest")
            self.assertNotIn("stale", (snap / "manifest").read_text())
            saved = (snap / "saved.paths").read_text()
            self.assertIn("mag-bots.conf", saved)
            self.assertIn("mag-pages.conf", saved)
            self.assertTrue((nginx / "conf.d" / "mag-git.conf").is_file())
            self.assertTrue((pathlib.Path(env["MAG_GITEA_FUNNEL_MAG_CI"]) / "gitea-proxy.inc").is_file())

    def test_apply_err_trap_restores_leftover_listens_after_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            nginx, txn, env = _leftover_host(pathlib.Path(raw))
            env["MAG_GITEA_FUNNEL_FAIL"] = "after-snapshot"
            bots_before = (nginx / "conf.d" / "mag-bots.conf").read_text()
            pages_before = (nginx / "conf.d" / "mag-pages.conf").read_text()
            proc = subprocess.run(
                ["bash", "ops/tailscale/apply-gitea-funnel.sh"],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            log = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0, log)
            self.assertIn("tailscale cert: mag.tail3e02ae.ts.net", log)
            self.assertRegex(
                log, r"gitea funnel: failed at line [0-9]+ command: .* rc=1"
            )
            self.assertIn("previous listeners restored", log)
            self.assertEqual((nginx / "conf.d" / "mag-bots.conf").read_text(), bots_before)
            self.assertEqual((nginx / "conf.d" / "mag-pages.conf").read_text(), pages_before)
            self.assertIn("100.123.114.16:443", bots_before)
            snap = txn / "funnel-snapshot"
            self.assertTrue((snap / "serve.json").is_file())
            self.assertFalse((snap / "snap").exists())

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

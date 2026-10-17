import io
import json
from pathlib import Path
import importlib.util
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("pages", Path(__file__).resolve().parents[1] / "mag_pages.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def tar(members):
    f = io.BytesIO()
    with tarfile.open(fileobj=f, mode="w:gz") as t:
        for name, data, kind in members:
            h = tarfile.TarInfo(name)
            if kind == "symlink":
                h.type, h.linkname = tarfile.SYMTYPE, "/etc/passwd"
                t.addfile(h)
            else:
                data = data.encode()
                h.size = len(data)
                t.addfile(h, io.BytesIO(data))
    f.seek(0)
    return f


class PagesTests(unittest.TestCase):
    def test_dns_and_cname(self):
        self.assertEqual(p.slug("chris/hello"), "hello--chris")
        self.assertLessEqual(len(p.slug("Owner/" + "long-repo" * 20)), 63)
        self.assertNotEqual(p.slug("chris/my_repo"), p.slug("chris/my-repo"))
        self.assertEqual(p.cname({"CNAME": b"www.example.com\n"}), "www.example.com")
        for bad in (b"http://evil.net", b"*.evil.net", b"invalid;cmd", b"a"):
            with self.assertRaises(ValueError):
                p.cname({"CNAME": bad})

    def test_archive_security_and_docs_priority(self):
        files = p.unpack(tar([("repo/index.html", "branch", "file"),
                             ("repo/docs/index.html", "docs", "file"),
                             ("repo/.env", "secret", "file"),
                             ("repo/../../escape", "bad", "file")]))
        self.assertEqual(files["index.html"], b"branch")
        self.assertEqual(p.repo_pages(files, "docs"), {"index.html": b"docs"})
        self.assertNotIn(".env", files)
        with self.assertRaisesRegex(ValueError, "symlink"):
            p.unpack(tar([("repo/link", "", "symlink")]))

    def test_visibility(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "policy.json"
            p.visibility_change("alice/hello", "public", f)
            self.assertEqual(p.get_visibility(f), {"alice/hello": "public"})
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                p.visibility_change("../bad", "public", f)

    def test_render_nginx_private_public(self):
        sha = "a" * 40
        c = {"public_ip": "127.0.0.2", "tailnet_ip": "127.0.0.3",
             "wildcard_cert": "/tmp/cert", "wildcard_key": "/tmp/key"}
        s = {"domain": "hello--alice.pages.stardenburg.de",
             "root": "/var/lib/mag-pages/releases/hello--alice/" + sha,
             "visibility": "tailnet", "custom": None}
        conf = p.render_nginx(c, {"hello--alice": s})
        site = conf.split("server_name hello--alice.pages.stardenburg.de;")[0].split("server {")[-1]
        self.assertIn("127.0.0.3:443", site)
        self.assertNotIn("127.0.0.2:443", site)
        self.assertIn("location ~ (^|/)\\.", conf)
        s["visibility"] = "public"
        conf = p.render_nginx(c, {"hello--alice": s})
        site = conf.split("server_name hello--alice.pages.stardenburg.de;")[0].split("server {")[-1]
        self.assertIn("127.0.0.2:443", site)

    def test_sync_60_sites_caching_and_visibility(self):
        sha = "1" * 40
        repos = [{"id": i, "full_name": "alice/repo" + str(i), "default_branch": "main"} for i in range(60)]
        c = {"pages_domain": "pages.stardenburg.de", "public_ip": "127.0.0.2",
             "tailnet_ip": "127.0.0.3", "wildcard_cert": "/tmp/cert", "wildcard_key": "/tmp/key"}
        archives, rendered = [], []
        def fake_api(_c, path, binary=False):
            archives.append(path)
            self.assertTrue(binary)
            return {"index.html": b"ok"}
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            with (mock.patch.object(p, "RELEASES", base / "releases"),
                  mock.patch.object(p, "STATE", base / "state.json"),
                  mock.patch.object(p, "VISIBILITY", base / "visibility.json"),
                  mock.patch.object(p, "repos", return_value=repos),
                  mock.patch.object(p, "resolve_branch", return_value=("gh-pages", sha)),
                  mock.patch.object(p, "api", side_effect=fake_api),
                  mock.patch.object(p, "publish_nginx", side_effect=lambda cfg: rendered.append(cfg))):
                p.sync(c)
                self.assertEqual(len(archives), 60)
                self.assertEqual(len(json.loads((base / "state.json").read_text())), 60)
                p.sync(c)
                self.assertEqual(len(archives), 60)
                p.visibility_change("alice/repo0", "public", base / "visibility.json")
                p.sync(c)
                state = json.loads((base / "state.json").read_text())
                self.assertEqual(state["repo0--alice"]["visibility"], "public")
                self.assertEqual(state["repo1--alice"]["visibility"], "tailnet")

    def test_nginx_syntax_no_real_host_ip(self):
        if not shutil.which("nginx"):
            self.skipTest("nginx unavailable")
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cert, key = base / "cert.pem", base / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", str(key), "-out", str(cert), "-days", "1",
                            "-subj", "/CN=pages.stardenburg.de"], capture_output=True, check=True)
            c = {"public_ip": "127.0.0.2", "tailnet_ip": "127.0.0.3",
                 "wildcard_cert": str(cert), "wildcard_key": str(key)}
            gen = base / "generated.conf"
            gen.write_text(p.render_nginx(c, {}))
            cfg = base / "nginx.conf"
            cfg.write_text("worker_processes 1;\nerror_log stderr;\n"
                           "events { worker_connections 32; }\n"
                           f"http {{ access_log off; include {gen}; }}\n")
            result = subprocess.run(["nginx", "-t", "-p", str(base) + "/", "-c", str(cfg)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

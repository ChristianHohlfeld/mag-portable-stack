#!/usr/bin/env python3
# mag-test-timeout: 180
"""Every tailnet name is one nginx socket. Nothing else claims :443.

Starts a throwaway nginx on a high loopback port. Does not reload a system
nginx and does not touch a model unit.
"""
import pathlib as _mag_path
import sys as _mag_sys

_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / "ops/ci/mag_deadline.py").is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / "ops/ci"))
import mag_deadline as _mag_deadline

_mag_deadline.arm(180)

import json
import pathlib
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
LISTEN = "listen 127.0.0.1:4443 ssl http2 proxy_protocol;"
TAILNET_ONLY = (
    "set_real_ip_from 127.0.0.1;",
    "real_ip_header proxy_protocol;",
    "allow 100.64.0.0/10;",
    "allow fd7a:115c:a1e0::/48;",
    "deny all;",
    "if ($mag_tailnet_ok = 0) { return 403; }",
)
TAILNET_CLIENT = "100.64.1.2"
FUNNEL_CLIENT = "192.0.2.10"
TAILNET = (
    ROOT / "ops/private-access/mag-room.nginx.conf",
    ROOT / "ops/pages/mag-pages.nginx.in",
    ROOT / "ops/bots/mag-bots.nginx.in",
    ROOT / "ops/gitea-ci/public.nginx.in",
)
NAMES = (
    "mag.stardenburg.de",
    "pages.stardenburg.de",
    "repo.pages.stardenburg.de",
    "agent1.bot.stardenburg.de",
    "mag.tail3e02ae.ts.net",
)


def listen_tokens(text):
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("listen "):
            found.append(stripped.split()[1])
    return found


def certificate(cn, directory):
    key = directory / f"{cn}.key"
    crt = directory / f"{cn}.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={cn}"],
        check=True, capture_output=True)
    return crt, key


def peer_cn(der):
    proc = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-subject"],
        input=der, check=True, capture_output=True)
    subject = proc.stdout.decode()
    marker = "CN = " if "CN = " in subject else "CN="
    return subject.split(marker, 1)[1].strip()


class SniMap(unittest.TestCase):
    def test_templates_share_one_socket_and_do_not_bind_443(self):
        for path in TAILNET:
            text = path.read_text()
            self.assertIn(LISTEN, text, path.name)
            self.assertNotIn("listen 100.123.114.16:443", text, path.name)
            self.assertNotIn("@TAILNET_BIND@", text, path.name)
            for token in listen_tokens(text):
                self.assertEqual(token, "127.0.0.1:4443", path.name)
        public = (ROOT / "ops/pages/mag-pages-public.nginx.in").read_text()
        self.assertIn("listen @PUBLIC_BIND@:443 ssl;", public)
        self.assertNotIn("127.0.0.1:4443", public)
        self.assertNotIn("100.123.114.16", public)
        pages = (ROOT / "ops/pages/mag-pages.nginx.in").read_text()
        bots = (ROOT / "ops/bots/mag-bots.nginx.in").read_text()
        self.assertEqual(pages.count(LISTEN), 2)
        self.assertEqual(bots.count(LISTEN), 1)
        self.assertEqual((ROOT / "ops/private-access/mag-room.nginx.conf").read_text().count(LISTEN), 1)
        gitea = (ROOT / "ops/gitea-ci/public.nginx.in").read_text()
        self.assertEqual(gitea.count(LISTEN), 1)
        self.assertIn("listen 127.0.0.1:4443 ssl http2 proxy_protocol default_server;", gitea)
        self.assertIn("ssl_reject_handshake on;", gitea)
        room_src = (ROOT / "ops/private-access/mag-room.nginx.conf").read_text()
        self.assertIn("location = /readyz", room_src)
        self.assertIn("auth_request off;", room_src)
        self.assertIn("include /opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc;", room_src)
        self.assertIn("geo $remote_addr $mag_tailnet_ok",
                      (ROOT / "ops/nginx/mag-tailnet-ok.inc").read_text())
        for path in (ROOT / "ops/private-access/mag-room.nginx.conf",
                     ROOT / "ops/pages/mag-pages.nginx.in",
                     ROOT / "ops/bots/mag-bots.nginx.in"):
            text = path.read_text()
            for needle in TAILNET_ONLY:
                self.assertIn(needle, text, path.name)
        self.assertEqual(pages.count("allow 100.64.0.0/10;"), 2)
        self.assertEqual(bots.count("allow 100.64.0.0/10;"), 1)
        self.assertEqual(room_src.count("allow 100.64.0.0/10;"), 1)
        self.assertNotIn("allow 100.64.0.0/10;", gitea)
        self.assertNotIn("deny all;", gitea)
        blob = "\n".join(path.read_text() for path in TAILNET)
        self.assertIn("server_name mag.stardenburg.de;", blob)
        self.assertIn("server_name mag.tail3e02ae.ts.net;", blob)
        self.assertIn("server_name @ZONE@;", pages)
        self.assertIn("agent[0-9]+", bots)
        self.assertIn("pageslug", pages)
        # tailscaled TCP-forwards :443 to nginx. HTTPS-on-443 swallows SNI.
        serve = json.loads((ROOT / "ops/tailscale/gitea-funnel.json").read_text())
        self.assertEqual(serve["TCP"]["443"]["TCPForward"], "127.0.0.1:4443")
        self.assertEqual(serve["TCP"]["443"]["ProxyProtocol"], 2)
        self.assertNotIn("HTTPS", serve["TCP"]["443"])
        self.assertNotIn("TerminateTLS", serve["TCP"]["443"])
        self.assertNotIn("mag.tail3e02ae.ts.net:443", serve["Web"])
        self.assertEqual(list(serve["AllowFunnel"]), ["mag.tail3e02ae.ts.net:443"])
        for port in ("8443", "8445", "8446"):
            self.assertNotIn(f"mag.tail3e02ae.ts.net:{port}", serve["AllowFunnel"])

    def test_sni_serves_each_name_and_rejects_unknown(self):
        nginx = subprocess.run(["nginx", "-v"], capture_output=True, text=True)
        if nginx.returncode != 0 and not (nginx.stderr or nginx.stdout):
            self.fail("nginx is not installed")
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            certs = {}
            for cn in ("room", "pages-apex", "pages-wild", "bots", "gitea"):
                certs[cn] = certificate(cn, tmp)
            room = (ROOT / "ops/private-access/mag-room.nginx.conf").read_text()
            speech = "include /opt/mag-stack/current/ops/nginx/mag-speech*.inc;"
            room = room.replace(speech, f"include {ROOT / 'ops/nginx/mag-speech.inc'};")
            room = room.replace(
                "include /opt/mag-stack/current/ops/nginx/mag-tailnet-ok.inc;",
                f"include {ROOT / 'ops/nginx/mag-tailnet-ok.inc'};")
            room = room.replace("/etc/mag-nginx/tls/cert.pem", str(certs["room"][0]))
            room = room.replace("/etc/mag-nginx/tls/key.pem", str(certs["room"][1]))
            pages = (ROOT / "ops/pages/mag-pages.nginx.in").read_text()
            pages = pages.replace("@ZONE@", "pages.stardenburg.de")
            pages = pages.replace("@ZONE_REGEX@", r"pages\.stardenburg\.de")
            pages = pages.replace("/etc/mag-pages/tls/current/cert.pem", str(certs["pages-apex"][0]), 1)
            pages = pages.replace("/etc/mag-pages/tls/current/cert.pem", str(certs["pages-wild"][0]), 1)
            pages = pages.replace("/etc/mag-pages/tls/current/key.pem", str(certs["pages-apex"][1]), 1)
            pages = pages.replace("/etc/mag-pages/tls/current/key.pem", str(certs["pages-wild"][1]), 1)
            pages = pages.replace("/var/lib/mag-pages/tailnet/$pageslug", f"{tmp}/pages/$pageslug")
            bots = (ROOT / "ops/bots/mag-bots.nginx.in").read_text()
            bots = bots.replace("@ZONE_REGEX@", r"bot\.stardenburg\.de")
            bots = bots.replace("/etc/mag-bots/tls/current/cert.pem", str(certs["bots"][0]))
            bots = bots.replace("/etc/mag-bots/tls/current/key.pem", str(certs["bots"][1]))
            bots = bots.replace("/var/lib/mag-bots/www/$botslug", f"{tmp}/bots/$botslug")
            gitea = (ROOT / "ops/gitea-ci/public.nginx.in").read_text()
            gitea = gitea.replace(
                "/etc/mag-nginx/tls/tailscale/mag.tail3e02ae.ts.net.crt", str(certs["gitea"][0]))
            gitea = gitea.replace(
                "/etc/mag-nginx/tls/tailscale/mag.tail3e02ae.ts.net.key", str(certs["gitea"][1]))
            inc = tmp / "gitea-proxy.inc"
            inc.write_text('default_type text/plain;\nreturn 200 "gitea\\n";\n')
            gitea = gitea.replace("include /etc/mag-ci/gitea-proxy.inc;", f"include {inc};")
            (tmp / "pages" / "repo").mkdir(parents=True)
            (tmp / "pages" / "repo" / "index.html").write_text("pages-wild\n")
            (tmp / "bots" / "agent1").mkdir(parents=True)
            (tmp / "bots" / "agent1" / "index.html").write_text("bot-ok\n")
            for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
                (tmp / name).mkdir()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            rendered = []
            for text in (room, pages, bots, gitea):
                rendered.append(text.replace("127.0.0.1:4443", f"127.0.0.1:{port}"))
            # The only listen address in the combined config is the test port.
            combined = "\n".join(rendered)
            tokens = set(listen_tokens(combined))
            self.assertEqual(tokens, {f"127.0.0.1:{port}"})
            self.assertNotIn("listen 100.123.114.16", combined)
            for name, text in zip(("room", "pages", "bots", "gitea"), rendered):
                (tmp / f"{name}.conf").write_text(text)
            config = tmp / "nginx.conf"
            config.write_text(
                "worker_processes 1;\n"
                f"pid {tmp}/nginx.pid;\n"
                f"error_log {tmp}/nginx.err;\n"
                "events { worker_connections 64; }\n"
                "http {\n"
                "  access_log off;\n"
                f"  client_body_temp_path {tmp}/body;\n"
                f"  proxy_temp_path {tmp}/proxy;\n"
                f"  fastcgi_temp_path {tmp}/fastcgi;\n"
                f"  uwsgi_temp_path {tmp}/uwsgi;\n"
                f"  scgi_temp_path {tmp}/scgi;\n"
                f"  include {tmp}/room.conf;\n"
                f"  include {tmp}/pages.conf;\n"
                f"  include {tmp}/bots.conf;\n"
                f"  include {tmp}/gitea.conf;\n"
                "}\n")
            test = subprocess.run(
                ["nginx", "-t", "-c", str(config), "-p", str(tmp)],
                capture_output=True, text=True)
            if test.returncode:
                self.fail(test.stderr)
            proc = subprocess.Popen(
                ["nginx", "-c", str(config), "-p", str(tmp), "-g", "daemon off;"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self._wait(port)
                expect = {
                    "mag.stardenburg.de": "room",
                    "pages.stardenburg.de": "pages-apex",
                    "repo.pages.stardenburg.de": "pages-wild",
                    "agent1.bot.stardenburg.de": "bots",
                    "mag.tail3e02ae.ts.net": "gitea",
                }
                for sni, cn in expect.items():
                    got = self._cn(port, sni)
                    self.assertEqual(got, cn, sni)
                with self.assertRaises(ssl.SSLError):
                    self._cn(port, "unknown.example")
                status, body = self._http(
                    port, "pages.stardenburg.de", "/healthz",
                    proxy_ip=TAILNET_CLIENT)
                self.assertEqual(status, 200)
                self.assertIn("ok", body)
                status, body = self._http(
                    port, "repo.pages.stardenburg.de", "/",
                    proxy_ip=TAILNET_CLIENT)
                self.assertEqual(status, 200)
                self.assertIn("pages-wild", body)
                status, body = self._http(
                    port, "agent1.bot.stardenburg.de", "/",
                    proxy_ip=TAILNET_CLIENT)
                self.assertEqual(status, 200)
                self.assertIn("bot-ok", body)
                status, body = self._http(
                    port, "mag.tail3e02ae.ts.net", "/user/login",
                    proxy_ip=FUNNEL_CLIENT)
                self.assertEqual(status, 200)
                self.assertIn("gitea", body)
                status, body = self._http(
                    port, "mag.tail3e02ae.ts.net", "/",
                    host="agent1.bot.stardenburg.de",
                    proxy_ip=FUNNEL_CLIENT)
                self.assertEqual(status, 403)
                status, body = self._http(
                    port, "mag.tail3e02ae.ts.net", "/",
                    host="repo.pages.stardenburg.de",
                    proxy_ip=FUNNEL_CLIENT)
                self.assertEqual(status, 403)
                status, body = self._http(
                    port, "agent1.bot.stardenburg.de", "/",
                    proxy_ip=FUNNEL_CLIENT)
                self.assertEqual(status, 403)
                status, body = self._http(
                    port, "pages.stardenburg.de", "/healthz",
                    proxy_ip=FUNNEL_CLIENT)
                self.assertEqual(status, 403)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                err = tmp / "nginx.err"
                if err.exists() and err.stat().st_size and proc.returncode not in (0, None, -15):
                    pass

    def test_curl_resolve_readyz_and_gitea(self):
        """Live: curl --resolve mag.stardenburg.de:443:100.123.114.16 …/readyz.

        The TCP proxy is tailscaled (PROXY v2 to 127.0.0.1:4443). This test
        uses loopback ports; the resolve line is the same shape.
        """
        nginx = subprocess.run(["nginx", "-v"], capture_output=True, text=True)
        if nginx.returncode != 0 and not (nginx.stderr or nginx.stdout):
            self.fail("nginx is not installed")
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            room_crt, room_key = certificate("mag.stardenburg.de", tmp)
            git_crt, git_key = certificate("mag.tail3e02ae.ts.net", tmp)
            for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
                (tmp / name).mkdir()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                http_port = probe.getsockname()[1]
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                edge_port = probe.getsockname()[1]
            (tmp / "http.conf").write_text(
                "server {\n"
                f"    listen 127.0.0.1:{http_port} ssl http2 proxy_protocol default_server;\n"
                "    server_name _;\n"
                "    ssl_reject_handshake on;\n"
                "}\n"
                "server {\n"
                f"    listen 127.0.0.1:{http_port} ssl http2 proxy_protocol;\n"
                "    server_name mag.stardenburg.de;\n"
                f"    ssl_certificate {room_crt};\n"
                f"    ssl_certificate_key {room_key};\n"
                "    set_real_ip_from 127.0.0.1;\n"
                "    real_ip_header proxy_protocol;\n"
                "    allow 100.64.0.0/10;\n"
                "    deny all;\n"
                "    if ($mag_tailnet_ok = 0) { return 403; }\n"
                "    location = /readyz { default_type application/json; return 200 '{\"ready\":true}\\n'; }\n"
                "}\n"
                "server {\n"
                f"    listen 127.0.0.1:{http_port} ssl http2 proxy_protocol;\n"
                "    server_name mag.tail3e02ae.ts.net;\n"
                f"    ssl_certificate {git_crt};\n"
                f"    ssl_certificate_key {git_key};\n"
                "    location / { default_type text/plain; return 200 'gitea\\n'; }\n"
                "}\n"
            )
            config = tmp / "nginx.conf"
            config.write_text(
                "worker_processes 1;\n"
                f"pid {tmp}/nginx.pid;\n"
                f"error_log {tmp}/nginx.err;\n"
                "events { worker_connections 64; }\n"
                "http {\n"
                "  access_log off;\n"
                f"  client_body_temp_path {tmp}/body;\n"
                f"  proxy_temp_path {tmp}/proxy;\n"
                f"  fastcgi_temp_path {tmp}/fastcgi;\n"
                f"  uwsgi_temp_path {tmp}/uwsgi;\n"
                f"  scgi_temp_path {tmp}/scgi;\n"
                "  geo $remote_addr $mag_tailnet_ok {\n"
                "    default 0;\n"
                "    100.64.0.0/10 1;\n"
                "    127.0.0.1 1;\n"
                "  }\n"
                f"  include {tmp}/http.conf;\n"
                "}\n"
            )
            test = subprocess.run(
                ["nginx", "-t", "-c", str(config), "-p", str(tmp)],
                capture_output=True, text=True)
            if test.returncode:
                self.fail(test.stderr)
            proc = subprocess.Popen(
                ["nginx", "-c", str(config), "-p", str(tmp), "-g", "daemon off;"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            seen_as = [TAILNET_CLIENT]
            stop = self._start_proxy(edge_port, http_port, seen_as)
            try:
                self._wait(http_port)
                self._wait(edge_port)
                room = self._curl_resolve(
                    edge_port, "mag.stardenburg.de", "/readyz")
                self.assertEqual(room[0], 200, room)
                self.assertIn("ready", room[1])
                gitea = self._curl_resolve(
                    edge_port, "mag.tail3e02ae.ts.net", "/")
                self.assertEqual(gitea[0], 200, gitea)
                self.assertIn("gitea", gitea[1])
                seen_as[0] = FUNNEL_CLIENT
                blocked = self._curl_resolve(
                    edge_port, "mag.stardenburg.de", "/readyz")
                self.assertEqual(blocked[0], 403, blocked)
                gitea = self._curl_resolve(
                    edge_port, "mag.tail3e02ae.ts.net", "/")
                self.assertEqual(gitea[0], 200, gitea)
            finally:
                stop.set()
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _start_proxy(self, listen_port, dest_port, proxy_ip=TAILNET_CLIENT):
        stop = threading.Event()

        def client_ip():
            return proxy_ip[0] if isinstance(proxy_ip, list) else proxy_ip

        def accept_loop():
            srv = socket.socket()
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", listen_port))
            srv.listen(8)
            srv.settimeout(0.2)
            while not stop.is_set():
                try:
                    client, _ = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._splice,
                    args=(client, dest_port, client_ip()),
                    daemon=True,
                ).start()
            srv.close()

        threading.Thread(target=accept_loop, daemon=True).start()
        return stop

    def _splice(self, client, dest_port, proxy_ip=TAILNET_CLIENT):
        backend = None
        try:
            backend = socket.create_connection(("127.0.0.1", dest_port), timeout=3)
            header = f"PROXY TCP4 {proxy_ip} 127.0.0.1 41000 443\r\n".encode()
            backend.sendall(header)
            client.settimeout(5)
            backend.settimeout(5)
            threading.Thread(
                target=self._pump, args=(client, backend), daemon=True
            ).start()
            self._pump(backend, client)
        except OSError:
            pass
        finally:
            for sock in (client, backend):
                if sock is None:
                    continue
                try:
                    sock.close()
                except OSError:
                    pass

    @staticmethod
    def _pump(src, dst):
        try:
            while True:
                chunk = src.recv(4096)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def _curl_resolve(self, port, name, path):
        proc = subprocess.run(
            [
                "curl", "-sS", "-k", "--max-time", "5", "--http1.1",
                "--resolve", f"{name}:{port}:127.0.0.1",
                "-w", "\n%{http_code}",
                f"https://{name}:{port}{path}",
            ],
            capture_output=True, text=True, timeout=8,
        )
        body, _, code = proc.stdout.rpartition("\n")
        if proc.returncode != 0:
            self.fail(f"curl {name}{path}: {proc.returncode} {proc.stderr} {proc.stdout}")
        return int(code), body

    def _wait(self, port):
        import time
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        self.fail("nginx did not bind the test port")

    def _open(self, port, sni, proxy_ip=TAILNET_CLIENT):
        raw = socket.create_connection(("127.0.0.1", port), timeout=3)
        raw.sendall(f"PROXY TCP4 {proxy_ip} 127.0.0.1 41000 4443\r\n".encode())
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(raw, server_hostname=sni)

    def _cn(self, port, sni):
        tls = self._open(port, sni)
        try:
            der = tls.getpeercert(binary_form=True)
        finally:
            tls.close()
        return peer_cn(der)

    def _http(self, port, sni, path, host=None, proxy_ip=TAILNET_CLIENT):
        tls = self._open(port, sni, proxy_ip)
        try:
            tls.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {host or sni}\r\n"
                f"Connection: close\r\n\r\n".encode())
            data = b""
            while True:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            tls.close()
        head, _, body = data.partition(b"\r\n\r\n")
        status = int(head.split()[1])
        return status, body.decode()


if __name__ == "__main__":
    unittest.main(verbosity=2)

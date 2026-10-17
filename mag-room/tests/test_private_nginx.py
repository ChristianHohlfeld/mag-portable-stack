"""Nginx private ingress: syntax, real auth_request, fail closed, header spoofing.
Runs on CI loopback only. Never connects to MAG or restarts the LLM.
"""
import http.server
import pathlib
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX = ROOT / "ops/private-access/mag-room.nginx.conf"
SPEECH = ROOT / "ops/nginx/mag-speech.inc"

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

class MockWhois(http.server.BaseHTTPRequestHandler):
    authorized = False
    seen_paths = []
    def log_message(self,*args): pass
    def do_GET(self):
        type(self).seen_paths.append(self.path)
        self.send_response(200 if self.authorized else 401)
        if self.authorized:
            self.send_header("X-Tailscale-User","chris@github")
            self.send_header("X-Tailscale-Login","chris@github")
            self.send_header("X-Tailscale-Name","Chris")
        self.send_header("Content-Length","0")
        self.end_headers()

class MockBackend(http.server.BaseHTTPRequestHandler):
    last_user = None
    requests = 0
    def log_message(self,*args): pass
    def do_GET(self):
        type(self).requests += 1
        type(self).last_user = self.headers.get("X-Tailscale-User")
        blob = b'{"you":{"role":"owner"}}'
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

class NginxAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = pathlib.Path(cls.temp.name)
        cls.cert = cls.path/"cert.pem"
        cls.key = cls.path/"key.pem"
        subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes",
             "-days","1","-keyout",str(cls.key),"-out",str(cls.cert),
             "-subj","/CN=mag.stardenburg.de"],check=True,capture_output=True)
        cls.whois = http.server.ThreadingHTTPServer(("127.0.0.1",0),MockWhois)
        cls.backend = http.server.ThreadingHTTPServer(("127.0.0.1",0),MockBackend)
        cls.threads=[]
        for srv in (cls.whois,cls.backend):
            th=threading.Thread(target=srv.serve_forever,daemon=True);th.start();cls.threads.append(th)
        cls.port=free_port()
        text=NGINX.read_text()
        speech_include = "include /opt/mag-stack/current/ops/nginx/mag-speech*.inc;"
        assert text.count(speech_include) == 1, "Private server lost speech include"
        assert SPEECH.is_file(), "Real speech include missing"
        # Test the actual included source, not a wildcard silently ignored on CI.
        text=text.replace(speech_include, f"include {SPEECH};")
        assert 'location ~ "^/static/speech-[0-9a-f]{12}/' in SPEECH.read_text()
        assert "auth_request off;" in text.split("location = /_mag_whois {",1)[1].split("}",1)[0], "Recursive auth_request risk"
        text=text.replace("100.123.114.16:443",f"127.0.0.1:{cls.port}")
        text=text.replace("/etc/mag-nginx/tls/cert.pem",str(cls.cert))
        text=text.replace("/etc/mag-nginx/tls/key.pem",str(cls.key))
        text=text.replace("127.0.0.1:8766/_internal/tailscale/whois",f"127.0.0.1:{cls.whois.server_port}/_internal/tailscale/whois")
        text=text.replace("127.0.0.1:8766",f"127.0.0.1:{cls.backend.server_port}")
        site=cls.path/"site.conf";site.write_text(text)
        cls.config=cls.path/"nginx.conf"
        cls.config.write_text(f'pid {cls.path}/nginx.pid;\nerror_log {cls.path}/error.log;\nevents {{}}\nhttp {{ access_log off; include {site}; }}\n')
        test=subprocess.run(["nginx","-t","-c",str(cls.config),"-p",str(cls.path)],capture_output=True,text=True)
        if test.returncode: raise RuntimeError(test.stderr)
        cls.nginx=subprocess.Popen(["nginx","-c",str(cls.config),"-p",str(cls.path),"-g","daemon off;"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.sslctx=ssl._create_unverified_context()
        cls.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=cls.sslctx))
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1",cls.port),timeout=.2):break
            except OSError:time.sleep(.05)
        else:raise RuntimeError("nginx did not bind test port")
    @classmethod
    def tearDownClass(cls):
        cls.nginx.terminate()
        try:cls.nginx.wait(timeout=3)
        except subprocess.TimeoutExpired:cls.nginx.kill()
        for srv in (cls.whois,cls.backend):srv.shutdown();srv.server_close()
        cls.temp.cleanup()
    def test_fail_closed_when_whois_denies(self):
        MockWhois.authorized=False
        before=MockBackend.requests
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.opener.open(f"https://127.0.0.1:{self.port}/api/state",timeout=3)
        self.assertIn(ctx.exception.code,(401,403))
        self.assertEqual(MockBackend.requests,before,"Denied guest must never reach magroom")
    def test_trusted_identity_overwrites_spoofed_header(self):
        MockWhois.authorized=True
        req=urllib.request.Request(f"https://127.0.0.1:{self.port}/api/state",headers={
            "X-Tailscale-User":"forged@attacker.test","X-Tailscale-Name":"Attacker"})
        with self.opener.open(req,timeout=3) as res:
            self.assertEqual(res.status,200)
            self.assertEqual(res.headers.get("Cache-Control"),"no-store")
        self.assertEqual(MockBackend.last_user,"chris@github")
        self.assertIn("/_internal/tailscale/whois", MockWhois.seen_paths)
    def test_speech_assets_cannot_bypass_tailnet_auth(self):
        MockWhois.authorized=False
        before=MockBackend.requests
        for path in ("/static/speech-config.json",
                     "/static/speech-abcdef123456/frontend.ort"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.opener.open(f"https://127.0.0.1:{self.port}{path}",timeout=3)
            self.assertIn(ctx.exception.code,(401,403))
        self.assertEqual(MockBackend.requests,before)

    def test_root_app_shell_no_store(self):
        MockWhois.authorized=True
        with self.opener.open(f"https://127.0.0.1:{self.port}/",timeout=3) as res:
            self.assertEqual(res.status,200)
            self.assertEqual(res.headers.get("Cache-Control"),"no-store")

if __name__ == "__main__":
    unittest.main(verbosity=2)

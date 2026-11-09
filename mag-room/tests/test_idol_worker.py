"""Contract-only tests: never load models or allocate GPU memory."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mag_idol_worker", ROOT / "ops/idol/worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)

class IdolHTTPContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        worker.ROOT=Path(self.tmp.name)
        worker.jobs.clear()
        while not worker.pending.empty():
            worker.pending.get_nowait(); worker.pending.task_done()
        self.server=ThreadingHTTPServer(("127.0.0.1",0),worker.API)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, path, data=None, method=None):
        req=Request(self.base+path,data=data,method=method)
        try:
            with urlopen(req,timeout=3) as response:
                return response.status,json.loads(response.read())
        except HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_health(self):
        status,data=self.request("/api/idol/health")
        self.assertEqual(status,200)
        self.assertTrue(data["ok"])

    def test_upload_and_status_without_gpu(self):
        payload=b"\x89PNG\r\n\x1a\n" + b"dummy-image"
        status,created=self.request("/api/idol/jobs",data=payload)
        self.assertEqual(status,202)
        self.assertRegex(created["id"],r"^[a-f0-9]{32}$")
        self.assertEqual(created["state"],"queued")
        status,job=self.request("/api/idol/jobs/"+created["id"])
        self.assertEqual(status,200)
        self.assertEqual(job["id"],created["id"])
        self.assertTrue((worker.ROOT/created["id"]/"status.json").is_file())
        status,_=self.request("/api/idol/jobs/"+created["id"]+"/mesh.glb")
        self.assertEqual(status,404)

    def test_reject_invalid_and_oversized(self):
        self.assertEqual(self.request("/api/idol/jobs",data=b"not an image")[0],415)
        self.assertEqual(self.request("/api/idol/jobs",data=b"a")[0],413)
        self.assertEqual(self.request("/api/idol/jobs/missing")[0],404)
        self.assertEqual(self.request("/api/idol/jobs/../../etc/passwd")[0],404)

    def test_private_route_uses_existing_whois_nginx_gate(self):
        nginx=(ROOT/"ops/nginx/mag-speech-idol.inc").read_text()
        vhost=(ROOT/"ops/private-access/mag-room.nginx.conf").read_text()
        self.assertIn("location ^~ /api/idol/",nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8793",nginx)
        self.assertIn("auth_request /_mag_whois;",vhost)
        self.assertIn("mag-speech*.inc",vhost)
        self.assertNotIn("auth_request off",nginx)

    def test_ui_is_loaded_from_existing_attachment_menu(self):
        html=(ROOT/"mag-room/web/index.html").read_text()
        js=(ROOT/"mag-room/static/idol-ui.js").read_text()
        self.assertIn('/static/idol-ui.js',html)
        self.assertIn('document.getElementById("plus-menu")',js)
        self.assertIn("Foto zu 3D",js)
        self.assertIn("webgl2",js)

if __name__=="__main__": unittest.main()

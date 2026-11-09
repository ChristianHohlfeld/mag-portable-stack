# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
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
        req=Request(self.base+path,data=data,method=method,headers={"X-Mag-Idol-UI":"1"} if data is not None else {})
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

    def test_cancel_queued_job_without_gpu(self):
        status,created=self.request("/api/idol/jobs",data=b"\x89PNG\r\n\x1a\n" + b"payload")
        self.assertEqual(status,202)
        jid=created["id"]
        status,cancelled=self.request("/api/idol/jobs/"+jid+"/cancel",data=b"")
        self.assertEqual(status,200)
        self.assertTrue(cancelled["cancelled"])
        status,job=self.request("/api/idol/jobs/"+jid)
        self.assertEqual(status,200)
        self.assertEqual(job["state"],"cancelled")
        self.assertEqual(self.request("/api/idol/jobs/"+jid+"/cancel",data=b"")[1]["cancelled"],False)

    def test_verified_rocm_contracts(self):
        runner=(ROOT/"ops/idol/runner.py").read_text()
        installer=(ROOT/"ops/idol/install.sh").read_text()
        unit=(ROOT/"ops/idol/mag-idol.service").read_text()
        worker_src=(ROOT/"ops/idol/worker.py").read_text()
        ui=(ROOT/"mag-room/static/idol-ui.js").read_text()
        for required in ("torch.cuda.set_per_process_memory_fraction", "7.8 * (1024 ** 3)",
                         'runpy.run_module("idol.sample"', "IDOL_DINOV3", "IDOL_FLUX2_VAE",
                         'os.environ["ATTN_BACKEND"] = "sdpa"'):
            self.assertIn(required,runner)
        self.assertIn("--no-deps 'tensordict==0.9.1'",installer)
        self.assertIn('--no-deps -e "$BASE/idol-sdk"',installer)
        self.assertIn("SupplementaryGroups=render video",unit)
        self.assertIn("MAG_IDOL_TIMEOUT_S=4200",unit)
        self.assertIn("RUN_TIMEOUT = max(3600",worker_src)
        self.assertNotIn("rocm-smi",worker_src.split("def execute():")[1].split("class API")[0])
        self.assertIn("step=step, total_steps=50",worker_src)
        self.assertIn('"/cancel"',ui)
        self.assertIn('job.step',ui)

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

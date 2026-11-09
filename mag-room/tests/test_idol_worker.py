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
import ast
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
        self.assertEqual(created["quality"],"normal")
        status,job=self.request("/api/idol/jobs/"+created["id"])
        self.assertEqual(status,200)
        self.assertEqual(job["id"],created["id"])
        self.assertEqual(job["quality"],"normal")
        self.assertEqual(json.loads((worker.ROOT/created["id"]/"status.json").read_text())["quality"],"normal")
        self.assertTrue((worker.ROOT/created["id"]/"status.json").is_file())
        status,_=self.request("/api/idol/jobs/"+created["id"]+"/mesh.glb")
        self.assertEqual(status,404)

    def test_quality_validation_and_status_persistence(self):
        payload=b"\x89PNG\r\n\x1a\n" + b"test-image"
        for quality in ("fast", "normal", "high"):
            status,item=self.request("/api/idol/jobs?quality="+quality,data=payload)
            self.assertEqual(status,202)
            self.assertEqual(item["quality"],quality)
            saved=json.loads((worker.ROOT/item["id"]/"status.json").read_text())
            self.assertEqual(saved["quality"],quality)
            self.assertEqual(self.request("/api/idol/jobs/"+item["id"])[1]["quality"],quality)
        count=len(worker.jobs)
        for suffix in ("?quality=ultra", "?quality=", "?quality=fast&quality=high"):
            status,result=self.request("/api/idol/jobs"+suffix,data=payload)
            self.assertEqual(status,400)
            self.assertIn("Qualität",result["error"])
            self.assertEqual(len(worker.jobs),count)

    def test_preset_mapping_and_mesh_timeouts(self):
        expected={
            "fast": (128, 150000, 150000, 1024, 300),
            "normal": (224, 400000, 400000, 2048, 900),
            "high": (320, 1000000, 800000, 4096, 1800),
        }
        source=(ROOT/"ops/idol/mesh.py").read_text()
        module=ast.parse(source)
        assignment=next(node for node in module.body if isinstance(node,ast.Assign)
                        and any(isinstance(t,ast.Name) and t.id=="QUALITY_PRESETS"
                                for t in node.targets))
        presets=ast.literal_eval(assignment.value)
        self.assertEqual(set(presets),set(expected))
        self.assertEqual(set(worker.MESH_TIMEOUTS),set(expected))
        for name,values in expected.items():
            self.assertEqual(tuple(presets[name][key] for key in
                                   ("resolution","max_points","max_faces","texture")),
                             values[:4])
            self.assertEqual(worker.MESH_TIMEOUTS[name],values[4])
        worker_src=(ROOT/"ops/idol/worker.py").read_text()
        self.assertIn('"--quality", quality',worker_src)
        self.assertIn("MESH_TIMEOUTS[quality]",worker_src)
        self.assertIn("gaussian_albedo(",source)
        self.assertIn("xatlas.parametrize(",source)
        self.assertIn("baseColorTexture=texture",source)
        self.assertIn("binary_dilation(filled, iterations=8)",source)
        self.assertIn("Float32 density",source)
        self.assertIn("filter_taubin(",source)
        self.assertNotIn("filter_laplacian(",source)
        installer=(ROOT/"ops/idol/install.sh").read_text()
        self.assertIn("--no-deps \"$package\"",installer)

    def test_delete_completed_job_files_internal_only(self):
        payload=b"\\x89PNG\\r\\n\\x1a\\n" + b"dummy-image"
        _, created = self.request("/api/idol/jobs",data=payload)
        jid=created["id"]
        folder=worker.ROOT/jid
        (folder/"mesh.glb").write_bytes(b"fake-glb")
        worker.jobs[jid]["state"]="done"
        worker.save(worker.jobs[jid])
        # This new method is deliberately unavailable to normal HTTP clients.
        status,_=self.request("/api/idol/jobs/"+jid,method="DELETE")
        self.assertEqual(status,403)
        self.assertTrue(folder.exists())
        req=Request(self.base+"/api/idol/jobs/"+jid,method="DELETE",
                    headers={"X-Mag-Idol-Internal":"1"})
        with urlopen(req,timeout=3) as response:
            self.assertEqual(response.status,200)
        self.assertFalse(folder.exists())
        self.assertNotIn(jid,worker.jobs)

    def test_delete_queued_job_tombstones_until_worker_drain(self):
        payload=b"\\x89PNG\\r\\n\\x1a\\n" + b"dummy-image"
        _,created=self.request("/api/idol/jobs",data=payload)
        jid=created["id"]
        self.assertTrue(worker.delete_job(jid))
        self.assertEqual(worker.jobs[jid]["state"],"deleted")
        self.assertTrue((worker.ROOT/jid/"status.json").exists())
        self.assertTrue(worker.delete_job(jid)) # idempotent while draining

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
        for required in ('runpy.run_module("idol.sample"', "IDOL_DINOV3", "IDOL_FLUX2_VAE",
                         'os.environ["ATTN_BACKEND"] = "sdpa"'):
            self.assertIn(required,runner)
        self.assertIn("--no-deps 'tensordict==0.9.1'",installer)
        self.assertIn('--no-deps -e "$BASE/idol-sdk"',installer)
        self.assertIn("SupplementaryGroups=render video",unit)
        self.assertIn("MAG_IDOL_TIMEOUT_S=4200",unit)
        # Writable MIOpen cache under ProtectSystem=strict / ProtectHome=read-only.
        tmpfiles=(ROOT/"ops/idol/mag-idol.tmpfiles.conf").read_text()
        self.assertIn("Environment=MIOPEN_USER_DB_PATH=/var/lib/mag-idol/miopen",unit)
        self.assertIn("Environment=MIOPEN_CUSTOM_CACHE_DIR=/var/lib/mag-idol/miopen-cache",unit)
        self.assertIn("d /var/lib/mag-idol/miopen 0700 chris chris -",tmpfiles)
        self.assertIn("d /var/lib/mag-idol/miopen-cache 0700 chris chris -",tmpfiles)
        self.assertIn("ReadWritePaths=/var/lib/mag-idol",unit)
        mesh=(ROOT/"ops/idol/mesh.py").read_text()
        compile(mesh,"ops/idol/mesh.py","exec")
        self.assertIn("rotation_matrix(np.pi, [0.0, 0.0, 1.0])",mesh)
        self.assertIn("rotation_matrix(np.pi / 2, [1.0, 0.0, 0.0])",mesh)
        self.assertIn("mesh.bounds[0, 1]",mesh)
        self.assertIn("stl.bounds[0, 2]",mesh)
        self.assertIn("MAG_IDOL_STL_HEIGHT_MM",mesh)
        self.assertIn("mesh.export(output_dir / \"mesh.glb\"",mesh)
        self.assertIn("stl.export(output_dir / \"mesh.stl\"",mesh)
        self.assertIn("className=\"idol-fullscreen\"",ui)
        self.assertIn("requestFullscreen",ui)
        self.assertIn("closePreviewFullscreen",ui)
        self.assertIn("magIdolFullscreen",ui)
        self.assertIn('window.addEventListener("popstate"',ui)
        self.assertIn("pointers=new Map()",ui)
        self.assertIn("before.distance/now.distance",ui)
        self.assertIn("lastTap<325",ui)
        self.assertNotIn("rotation_matrix",ui)
        self.assertIn("RUN_TIMEOUT = max(3600",worker_src)
        self.assertNotIn("rocm-smi",worker_src.split("def execute():")[1].split("class API")[0])
        self.assertIn("step=step, total_steps=50",worker_src)
        self.assertNotIn("set_per_process_memory_fraction",runner)
        self.assertIn('lena_lease("acquire")',worker_src)
        self.assertIn('lena_lease("restore")',worker_src)
        self.assertIn('lena_health(job_id)',worker_src)
        self.assertIn("ExecStopPost=+/usr/local/lib/mag-idol/gpu-lease.sh restore",unit)
        self.assertIn("mag-llm-coding@0.service",(ROOT/"ops/idol/gpu-lease.sh").read_text())
        self.assertIn("window.magIdolUI={render:renderHistory,park,clear}",ui)
        self.assertIn("registerCurrent(current)",ui)
        self.assertIn('/api/room/idol',ui)
        self.assertIn("msg.idol_job",ui)
        self.assertIn("parkRoot.append(card)",ui)
        self.assertIn("cardFor(id)",ui)
        self.assertIn("Lena pausiert für Foto zu 3D",ui)
        self.assertIn('window.magIdolUI?.render()', (ROOT/"mag-room/web/index.html").read_text())

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
        self.assertIn("mag-idol-quality",js)
        self.assertIn("makeQualitySelect()",js)
        self.assertIn("localStorage.setItem(QUALITY_KEY",js)
        self.assertIn("/api/idol/jobs?quality=",js)
        self.assertIn("TEXCOORD_0",js)
        self.assertIn("baseColorTexture",js)
        self.assertIn("gl.texImage2D",js)

if __name__=="__main__": unittest.main()

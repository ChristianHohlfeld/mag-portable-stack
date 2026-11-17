#!/usr/bin/env python3
"""Standard-library regression checks for the isolated rigging demo.

Does not pretend to run Blender; the real pipeline is checked on MAG with
`python3 ops/scene/demo_fullpath.py --out ...`.
"""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "demo_fullpath.py"
spec = importlib.util.spec_from_file_location("demo_fullpath", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_glb(path, doc):
    blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    blob += b" " * ((-len(blob)) % 4)
    data = struct.pack("<4sII", b"glTF", 2, 20 + len(blob))
    data += struct.pack("<I4s", len(blob), b"JSON") + blob
    path.write_bytes(data)


class TestDemoFullpath(unittest.TestCase):
    def test_glb_accepts_skin_morphs_and_animation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rig.glb"
            write_glb(p, {"asset": {"version": "2.0"}, "skins": [{"joints": [0]}],
                          "meshes": [{"primitives": [{"targets": [{}, {}, {}]}]}],
                          "animations": [{"channels": [{"target": {"node": 0, "path": "weights"}},
                                                       {"target": {"node": 0, "path": "rotation"}}]}]})
            self.assertEqual(module.verify_glb_animation(p)["morph_targets"], 3)

    def test_glb_rejects_static_and_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "static.glb"
            write_glb(p, {"asset": {"version": "2.0"}, "nodes": []})
            with self.assertRaises(RuntimeError):
                module.verify_glb_animation(p)
            p.write_bytes(b"garbage")
            with self.assertRaises(RuntimeError):
                module.verify_glb_animation(p)

    def test_report_is_not_a_fake_success(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for p in (d / "damaged.glb", d / "repaired.glb", d / "spec.json",
                      d / "result" / "scene.glb", d / "result" / "scene.blend",
                      d / "result" / "scene.json", d / "result" / "demo_report.json"):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x" * 200)
            (d / "result" / "demo_report.json").write_text(json.dumps({
                "skinned_figures": 1, "animated_face": True, "animated_fingers": 0,
                "notes": "intentional incomplete mock result" * 5}))
            with self.assertRaises(RuntimeError):
                module.verify_files(d)
            (d / "result" / "demo_report.json").write_text(json.dumps({
                "skinned_figures": 1, "animated_face": True, "animated_fingers": 10,
                "notes": "intentional valid mock result" * 5}))
            module.verify_files(d)

    def test_invalid_path_fails_before_work(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                module.runner(["--out", d, "--blender", "/a/path/that/is/not/blender"])


if __name__ == "__main__":
    unittest.main()

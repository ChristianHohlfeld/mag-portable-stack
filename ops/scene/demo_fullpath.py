#!/usr/bin/env python3
"""MAG isolated damaged-mesh -> repaired mesh -> actual scene.py -> face/hand demo.

This is a PROOF OF CONCEPT, not a generic learned topology completion model.
No existing MAG workers, queues, deployments, or bot configurations are changed.

Run (on MAG):
  taskset -c 28-55 python3 ops/scene/demo_fullpath.py --out /tmp/mag-rig-demo
  taskset -c 28-55 python3 ops/scene/demo_fullpath.py --out /tmp/mag-rig-demo --film
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import struct
import sys


def runner(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Isolated end-to-end MAG Blender rig demonstration")
    parser.add_argument("--out", default="/tmp/mag-rig-demo")
    parser.add_argument("--input", help="Optional GLB with a roughly upright, human-shaped character")
    parser.add_argument("--blender", default=os.environ.get("MAG_SCENE_BLENDER", "/opt/mag-scene/blender/blender"))
    parser.add_argument("--film", action="store_true", help="Also run the existing CPU Cycles film renderer")
    args = parser.parse_args(argv)
    blender = Path(args.blender).resolve()
    script = Path(__file__).resolve()
    scene = script.parent / "scene.py"
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not blender.is_file():
        raise SystemExit("Blender nicht gefunden: " + str(blender))
    if not scene.is_file():
        raise SystemExit("MAG scene.py nicht gefunden: " + str(scene))
    if args.input and not Path(args.input).is_file():
        raise SystemExit("Eingabe GLB fehlt: " + args.input)

    def invoke(path, *params):
        command = [str(blender), "-b", "--factory-startup", "-P", str(path), "--", *map(str, params)]
        print("MAG-DEMO", " ".join(command), flush=True)
        subprocess.run(command, check=True, cwd=str(script.parent))

    invoke(script, "_prepare", out, Path(args.input).resolve() if args.input else "-")
    # The real MAG scene pipeline: existing classifier, skeleton, facial landmarks,
    # motion capture, visemes, Blender export and scene.blend persistence.
    invoke(scene, "build", out / "spec.json", out / "result")
    invoke(script, "_finish", out, "-")
    if args.film:
        # The film is intentionally opt-in: CPU Cycles on the host can take time.
        invoke(scene, "render", out / "result")
    verify_files(out, film=args.film)
    verify_glb_animation(out / "result" / "scene.glb")
    print("MAG-DEMO OK: " + str(out / "result" / "scene.glb"), flush=True)
    return 0


def verify_files(out: Path, film: bool = False) -> None:
    expected = [out / "damaged.glb", out / "repaired.glb", out / "spec.json",
                out / "result" / "scene.glb", out / "result" / "scene.blend",
                out / "result" / "scene.json", out / "result" / "demo_report.json"]
    if film:
        expected.append(out / "result" / "scene.mp4")
    for path in expected:
        if not path.is_file() or path.stat().st_size < 100:
            raise RuntimeError("Demo-Artefakt fehlt/leer: " + str(path))
    report = json.loads((out / "result" / "demo_report.json").read_text())
    if report["skinned_figures"] < 1 or not report["animated_face"] or report["animated_fingers"] < 6:
        raise RuntimeError("Demo-Abnahme gescheitert: " + repr(report))


def verify_glb_animation(path: Path) -> dict:
    """Read the final *exported* GLB, not Blender's in-memory intentions."""
    with path.open("rb") as f:
        header = f.read(12)
        if len(header) != 12:
            raise RuntimeError("GLB ist unvollständig")
        magic, version, length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or length != path.stat().st_size:
            raise RuntimeError("GLB-Header beschädigt")
        chunk_header = f.read(8)
        if len(chunk_header) != 8:
            raise RuntimeError("GLB hat keinen JSON-Block")
        size, kind = struct.unpack("<I4s", chunk_header)
        if kind != b"JSON" or size < 2 or size > length - 20:
            raise RuntimeError("GLB-JSON ungültig")
        document = json.loads(f.read(size))
    skins = document.get("skins", [])
    anims = document.get("animations", [])
    morphs = sum(len(mesh.get("primitives", [{}])[0].get("targets", []))
                 for mesh in document.get("meshes", []) if mesh.get("primitives"))
    paths = {channel.get("target", {}).get("path")
             for animation in anims for channel in animation.get("channels", [])}
    if not skins or morphs < 3 or "weights" not in paths or not ({"rotation", "translation"} & paths):
        raise RuntimeError("Export enthält kein vollständiges Skin+Gesichts/Hand-Morph+Anim-Rig: " +
                           str({"skins": len(skins), "morph_targets": morphs, "animated_paths": sorted(paths)}))
    return {"skins": len(skins), "morph_targets": morphs, "animated_paths": sorted(paths)}


def parse_internal(argv):
    if len(argv) != 3 or argv[0] not in ("_prepare", "_finish"):
        raise SystemExit("Interner Aufruf: _prepare OUT INPUT | _finish OUT -")
    return argv[0], Path(argv[1]), argv[2]


def blender_prepare(out: Path, source: str) -> None:
    import bpy
    import bmesh
    import math
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    src = out / "damaged.glb"

    def ellipsoid(name, at, radius, segments=16, rings=10):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=at)
        obj = bpy.context.object
        obj.name = name
        obj.scale = radius
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        return obj

    def cylinder(name, a, b, width):
        vec = Vector(b) - Vector(a)
        mid = (Vector(a) + Vector(b)) * 0.5
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=width, depth=vec.length, location=mid)
        obj = bpy.context.object
        obj.name = name
        obj.rotation_euler = vec.to_track_quat("Z", "Y").to_euler()
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        return obj

    if source == "-":
        # Deliberately bad input: 14 disconnected pieces, open rear scalp,
        # no right shin at all, missing finger and facial geometry.
        ellipsoid("Torso fragment", (0, 0, 1.16), (.215, .14, .38))
        ellipsoid("Pelvis fragment", (0, 0, .85), (.20, .145, .18))
        cylinder("Neck", (0, 0, 1.46), (0, 0, 1.56), .075)
        ellipsoid("Head open shell", (0, 0, 1.68), (.157, .15, .193))
        for sign, side in ((-1, "L"), (1, "R")):
            cylinder("Thigh " + side, (sign * .13, 0, .86), (sign * .14, 0, .46), .075)
            if sign < 0:
                cylinder("Shin " + side, (sign * .14, 0, .48), (sign * .14, -.018, .08), .056)
            ellipsoid("Foot " + side, (sign * .14, -.065, .07), (.08, .15, .07))
            cylinder("UpperArm " + side, (sign * .20, 0, 1.39), (sign * .39, 0, 1.19), .062)
            cylinder("Forearm " + side, (sign * .39, 0, 1.19), (sign * .55, 0, 1.03), .048)
            ellipsoid("Hand no fingers " + side, (sign * .57, 0, 1.015), (.067, .047, .048))
        head = bpy.data.objects.get("Head open shell")
        # Remove the rear cap: an actual open-boundary hole, not a material trick.
        bm = bmesh.new()
        bm.from_mesh(head.data)
        holes = [f for f in bm.faces if f.calc_center_median().y > .09 and f.calc_center_median().z > 1.68]
        bmesh.ops.delete(bm, geom=holes, context="FACES")
        bm.to_mesh(head.data)
        bm.free()
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
        src_obj = bpy.context.object
        src_obj.name = "Damaged / incomplete body"
        bpy.ops.export_scene.gltf(filepath=str(src), export_format="GLB", export_yup=True)
    else:
        bpy.ops.import_scene.gltf(filepath=source, merge_vertices=True)
        src_obj = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not src_obj:
            raise RuntimeError("GLB enthält kein Mesh")
        if len(src_obj) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in src_obj:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = src_obj[0]
            bpy.ops.object.join()
            src_obj = bpy.context.object
        else:
            src_obj = src_obj[0]
        bpy.ops.object.select_all(action="DESELECT")
        src_obj.select_set(True)
        bpy.context.view_layer.objects.active = src_obj
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        bpy.ops.export_scene.gltf(filepath=str(src), export_format="GLB", export_yup=True)

    # Bounded reconstruction: symmetric humanoid in an approximately frontal
    # pose. This is a geometric PRIOR, never evidence that the missing part
    # existed. Only mirror geometry to right when there is valid left geometry.
    bpy.ops.object.select_all(action="DESELECT")
    src_obj.select_set(True)
    bpy.context.view_layer.objects.active = src_obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    mesh = src_obj.data
    cx = sum(v.co.x for v in mesh.vertices if .9 < v.co.z < 1.42) / max(1, sum(1 for v in mesh.vertices if .9 < v.co.z < 1.42))
    bm = bmesh.new()
    bm.from_mesh(mesh)
    original_faces = tuple(bm.faces)
    new_verts = {}
    for f in original_faces:
        if all(v.co.x < cx - 0.045 for v in f.verts):
            vs = []
            for v in f.verts:
                if v not in new_verts:
                    new_verts[v] = bm.verts.new((2 * cx - v.co.x, v.co.y, v.co.z))
                vs.append(new_verts[v])
            try:
                bm.faces.new(list(reversed(vs)))
            except ValueError:
                pass
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    # A coarse volumetric proxy heals holes and joins overlapping fragments.
    # Retopology and UV projection are intentionally NOT claimed by this demo.
    bpy.context.view_layer.update()
    scale = max(src_obj.dimensions)
    mesh.remesh_voxel_size = max(.005, scale / 130)
    bpy.ops.object.voxel_remesh()
    src_obj.name = "Recovered geometric proxy - INFERRED parts"
    src_obj.data.materials.clear()
    material = bpy.data.materials.new("Proxy matte clay")
    material.diffuse_color = (.56, .68, .81, 1.0)
    material.use_nodes = True
    material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = material.diffuse_color
    src_obj.data.materials.append(material)
    for poly in src_obj.data.polygons:
        poly.use_smooth = True
    bpy.ops.object.select_all(action="DESELECT")
    src_obj.select_set(True)
    bpy.context.view_layer.objects.active = src_obj
    bpy.ops.export_scene.gltf(filepath=str(out / "repaired.glb"), export_format="GLB", export_yup=True, use_selection=True)
    spec = {
        "title": "MAG Full Path · beschädigtes Mesh → Rig → Gesicht + Hände",
        "actors": [{"name": "Figur", "file": str(out / "repaired.glb"), "kind": "human", "faces": "camera"}],
        "actions": [
            {"actor": "Figur", "do": "say", "text": "Hallo, ich kann sprechen und blinzeln.", "seconds": 2.0, "start": 0.25},
            {"actor": "Figur", "do": "face", "expression": "blink", "seconds": 0.5, "start": 1.0},
            {"actor": "Figur", "do": "wave", "seconds": 1.8, "start": 2.4},
        ],
        "seconds": 4.8, "light": "day", "camera": "front",
    }
    (out / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MAG-DEMO PREPARED " + str(src), flush=True)


def blender_finish(out: Path) -> None:
    import bpy
    import math
    from mathutils import Vector

    result = out / "result"
    blendfile = result / "scene.blend"
    bpy.ops.wm.open_mainfile(filepath=str(blendfile))
    rigs = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE" and "Kopf" in obj.data.bones]
    if len(rigs) != 1:
        raise RuntimeError("Kein eindeutiges humanoides Kopf-Skelett: %d" % len(rigs))
    arm = rigs[0]
    head = arm.data.bones["Kopf"]
    # The face is completed from an anatomical PRIOR. No original eyes/lips are
    # claimed. The generated geometry is deliberately visible and animated.
    ht = head.tail.z
    z = max(head.head.z, ht) - .07 * (max(head.head.z, ht) - min(head.head.z, ht))
    # Demo mannequins are approximately 1.8 m. Infer facial scale from bone
    # length and actual skinned figure bounds, not a fixed world origin.
    body_mesh = next((o for o in bpy.context.scene.objects if o.type == "MESH" and
                      any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers)), None)
    if body_mesh is None:
        raise RuntimeError("Skelett hat kein deformiertes Körpermesh")
    width = max(.08, (max(v.co.x for v in body_mesh.data.vertices) - min(v.co.x for v in body_mesh.data.vertices)) * .19)
    eye_span = width * .35
    centre = (head.head + head.tail) * .5
    eye_z = centre.z + .08
    mouth_z = centre.z - .08
    # A visibly frontal character in the demo: source faces toward -Y.
    face_vertices = [v.co.y for v in body_mesh.data.vertices if abs(v.co.z - centre.z) < .12]
    if not face_vertices:
        raise RuntimeError("Keine erkennbare Kopfoberfläche für Gesichtsanbau")
    y = min(face_vertices) - .013
    skin = bpy.data.materials.new("Demo synthetic lids and lips")
    skin.diffuse_color = (.66, .73, .80, 1)
    skin.use_nodes = True
    skin.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = skin.diffuse_color
    black = bpy.data.materials.new("Demo pupils/mouth cavity")
    black.diffuse_color = (.012, .02, .025, 1)
    black.use_nodes = True
    black.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = black.diffuse_color
    whites = bpy.data.materials.new("Demo eye whites")
    whites.diffuse_color = (.91, .94, .97, 1)
    whites.use_nodes = True
    whites.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = whites.diffuse_color

    def ellipsoid(name, at, radius, mat, bone="Kopf"):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, location=at)
        obj = bpy.context.object
        obj.name = name
        obj.scale = radius
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(mat)
        obj.vertex_groups.new(name=bone).add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        modifier = obj.modifiers.new("MAG rig", "ARMATURE")
        modifier.object = arm
        for poly in obj.data.polygons:
            poly.use_smooth = True
        return obj

    def shape_key(obj, name, fn):
        obj.shape_key_add(name="Basis", from_mix=False)
        key = obj.shape_key_add(name=name, from_mix=False)
        for i, v in enumerate(key.data):
            fn(v, i)
        return key

    def animate(key, first, middle, last):
        for frame, value in ((first, 0.0), (middle, 1.0), (last, 0.0)):
            key.value = value
            key.keyframe_insert(data_path="value", frame=frame)

    # Mouth geometry: opening is a dark aperture, while the top and lower lip
    # deform separately. It is not a real hollow oral cavity (clear POC limit).
    mouth = ellipsoid("SYNTHETIC mouth aperture", (centre.x, y - .022, mouth_z), (width * .48, .009, .003), black)
    key = shape_key(mouth, "MouthOpen", lambda v, i: setattr(v.co, "z", v.co.z * 16))
    animate(key, 5, 28, 48)
    for sign, label in ((1, "upper"), (-1, "lower")):
        lip = ellipsoid("SYNTHETIC lip " + label, (centre.x, y - .035, mouth_z + sign * .014), (width * .53, .013, .012), skin)
        key = shape_key(lip, "Speak", lambda v, i, sign=sign: setattr(v.co, "z", v.co.z + sign * .019))
        animate(key, 5, 28, 48)

    for sign, side in ((-1, "L"), (1, "R")):
        x = centre.x + sign * eye_span
        ellipsoid("SYNTHETIC eyeball " + side, (x, y + .007, eye_z), (width * .22, .02, .037), whites)
        ellipsoid("SYNTHETIC pupil " + side, (x, y - .014, eye_z), (width * .09, .007, .018), black)
        lid = ellipsoid("SYNTHETIC eyelid " + side, (x, y - .029, eye_z + .037), (width * .24, .011, .012), skin)
        key = shape_key(lid, "Blink", lambda v, i: setattr(v.co, "z", v.co.z - .047))
        animate(key, 11, 15, 20)
        animate(key, 67, 71, 76)

    fingers = 0
    for sign, side in ((-1, "L"), (1, "R")):
        handname = "Hand links" if sign < 0 else "Hand rechts"
        if handname not in arm.data.bones:
            # No fake result: an automatic rig that could not identify a hand
            # is a failed hand demo and must surface explicitly.
            raise RuntimeError("MAG konnte diese Hand nicht erkennen: " + handname)
        hand = arm.data.bones[handname]
        a = hand.head
        b = hand.tail
        palm_length = max(.035, (b - a).length)
        for n in range(5):
            delta = (n - 2) * .018
            at = b + Vector((sign * (.03 + .012 * n), delta, -.008 + .006 * n))
            finger = ellipsoid("SYNTHETIC finger %s-%d" % (side, n + 1), at,
                               (.020 + .005 * n, .008, .009), skin, bone=handname)
            key = shape_key(finger, "Curl", lambda v, i, sign=sign: setattr(v.co, "z", v.co.z - abs(v.co.x) * .65))
            animate(key, 48, 67, 88)
            fingers += 1

    # Keep existing MAG actor+ground selections and add new facial/finger meshes.
    for obj in bpy.context.scene.objects:
        if obj.type in ("MESH", "ARMATURE"):
            obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(result / "scene.glb"), export_format="GLB", use_selection=True,
                              export_yup=True, export_animations=True, export_animation_mode="SCENE",
                              export_morph=True, export_skins=True, export_force_sampling=True,
                              export_image_format="JPEG", export_cameras=False, export_lights=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(blendfile), compress=True)
    facts = json.loads((result / "scene.json").read_text())
    skinned = sum(1 for obj in bpy.context.scene.objects if obj.type == "MESH" and
                  any(m.type == "ARMATURE" and m.object == arm for m in obj.modifiers))
    report = {"inferred_not_observed": True, "completion": "geometric symmetry + voxel proxy (human prior)",
              "skinned_figures": 1 if skinned else 0, "animated_face": True,
              "animated_fingers": fingers, "fps": facts["fps"], "frames": facts["frames"],
              "limitation": "No learned missing-part inference; stylized overlays, not full anatomical face retopology"}
    (result / "demo_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("MAG-DEMO FINISHED " + json.dumps(report), flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if argv and argv[0].startswith("_"):
        stage, out, source = parse_internal(argv)
        if stage == "_prepare":
            blender_prepare(out, source)
        elif stage == "_finish":
            blender_finish(out)
        return 0
    return runner(argv)


if __name__ == "__main__":
    raise SystemExit(main())

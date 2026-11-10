"""MAG scenes: 3D models from the chat, placed and animated by a description.

Runs inside Blender, headless, on the CPU:
    blender -b --factory-startup -P scene.py -- parts  model.glb out.json
    blender -b --factory-startup -P scene.py -- build  spec.json outdir
    blender -b --factory-startup -P scene.py -- render outdir

A generated model is one mesh. Often it shows several figures (a gnome and a
fox from one picture): `parts` tells them apart, and an actor of a scene is a
model or one of its parts. What a figure is, is read from its shape (rig.py):
an animal and somebody on two legs get a skeleton and move their limbs, only
a thing moves as a whole. `build` writes the animated scene as scene.glb
(what the chat plays) and scene.blend, lit for its film; `render` renders
scene.blend to frames with Cycles.
Nothing here takes code from a request: a scene is data, the motion is ours.
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rig  # noqa: E402  (what a figure is, and its skeleton)

FPS = 24
MAX_ACTORS, MAX_SECONDS = 8, 30
GROUNDS = {  # base colour, roughness
    "snow": ((0.80, 0.84, 0.90), 0.55), "grass": ((0.09, 0.22, 0.05), 0.9), "sand": ((0.60, 0.48, 0.28), 0.9),
    "stone": ((0.30, 0.30, 0.32), 0.8), "studio": ((0.62, 0.62, 0.64), 0.7),
}
# How a scene is lit. The film is to look like the scene in the chat, where
# every figure shows its colours as they are: a key light over the camera's
# left shoulder, and for the figures alone a fill from the right and a rim
# from behind, with the sky around it all. `glow` is the part of its own
# colour a figure shows even where no light falls (the texture of a generated
# model is painted light already). `sky`: a bundled sky, or (None) an even
# white. `backdrop`: what the camera sees behind the scene, a colour, or
# (None) the sky itself, which is small and so looks out of focus; `behind`
# is how bright.
LIGHTS = {
    "day": {"sky": "forest.exr", "ambient": 0.3, "key": 2.8, "colour": (1.0, 0.96, 0.90), "fill": 0.8, "rim": 1.6, "glow": 0.25, "backdrop": None, "behind": 1.0},
    "evening": {"sky": "sunset.exr", "ambient": 0.6, "key": 2.6, "colour": (1.0, 0.74, 0.50), "fill": 0.6, "rim": 1.8, "glow": 0.25, "backdrop": None, "behind": 0.45},
    "night": {"sky": "night.exr", "ambient": 0.6, "key": 1.7, "colour": (0.42, 0.58, 1.0), "fill": 0.4, "rim": 2.0, "glow": 0.45, "backdrop": (0.012, 0.022, 0.06), "behind": 1.0},
    "studio": {"sky": None, "ambient": 0.45, "key": 2.6, "colour": (1.0, 1.0, 1.0), "fill": 0.8, "rim": 1.2, "glow": 0.25, "backdrop": (0.18, 0.18, 0.20), "behind": 1.0},
}
GAITS = {  # stride (body lengths), bounce (heights), speed (body lengths per second), sway (radians)
    "walk": (0.55, 0.035, 1.3, 0.05), "run": (0.9, 0.07, 3.0, 0.07), "hop": (0.8, 0.22, 1.6, 0.03), "sneak": (0.35, 0.015, 0.7, 0.03),
}
# With legs: how far a leg swings (degrees), the part of a stride its foot is
# on the ground, how far the knee bends in the air (degrees), strides per
# second, bounce (heights).
STRIDES = {"walk": (26.0, 0.6, 40.0, 1.7, 0.012), "run": (36.0, 0.45, 62.0, 2.8, 0.03), "sneak": (18.0, 0.7, 30.0, 1.0, 0.006)}
KINDS = {"animal": "quadruped", "human": "biped", "object": "object"}   # what a caller may say a figure is
# A model made from a photo stands as the photo shows it: the photographer is
# at -X and looks along +X, so the left of the picture is +Y. `faces` says
# where a figure looks in that picture.
FACES = {"left": (0.0, 1.0), "right": (0.0, -1.0), "camera": (-1.0, 0.0), "away": (1.0, 0.0)}


def fail(message):
    print("MAG-ERROR " + message, flush=True)
    sys.exit(3)


# ---- models and their parts --------------------------------------------------
def islands_of(mesh):
    """Groups of connected vertices, as lists of vertex indices."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    seen, islands = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, group = [v], []
        seen.add(v.index)
        while stack:
            a = stack.pop()
            group.append(a.index)
            for e in a.link_edges:
                b = e.other_vert(a)
                if b.index not in seen:
                    seen.add(b.index)
                    stack.append(b)
        islands.append(group)
    bm.free()
    return islands


def figures_of(obj):
    """The figures a model consists of: connected pieces, with small pieces
    close to a larger one (a lantern in a hand, a loose tuft) joined to it and
    stray specks dropped. Returns lists of vertex indices, largest first."""
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    islands = sorted(islands_of(obj.data), key=len, reverse=True)
    boxes = [(co[g].min(axis=0), co[g].max(axis=0)) for g in islands]
    reach = 0.04 * float((co.max(axis=0) - co.min(axis=0)).max())
    owner = list(range(len(islands)))

    def root(i):
        while owner[i] != i:
            owner[i] = owner[owner[i]]
            i = owner[i]
        return i

    for i in range(len(islands)):
        for j in range(i):
            gap = np.maximum(0, np.maximum(boxes[i][0] - boxes[j][1], boxes[j][0] - boxes[i][1]))
            if float(np.linalg.norm(gap)) < reach:
                owner[root(i)] = root(j)
    groups = {}
    for i, g in enumerate(islands):
        groups.setdefault(root(i), []).extend(g)
    figures = sorted(groups.values(), key=len, reverse=True)
    return [g for g in figures if len(g) >= max(60, 0.01 * len(figures[0]))][:MAX_ACTORS]


def load_model(path):
    """Imports a GLB and returns its figures as objects standing on the ground,
    each with its origin under its feet."""
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=path, merge_vertices=True)
    except Exception as exc:  # a broken or foreign file is the caller's problem, in words
        fail("Das Modell lässt sich nicht lesen: " + str(exc)[:200])
    meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not meshes:
        fail("Das Modell enthält keine Geometrie.")
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    whole = bpy.context.view_layer.objects.active
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Smooth normals of our own: with the file's per-corner normals every
    # triangle would keep its own three vertices and the scene file triple in size.
    try:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except RuntimeError:
        pass
    whole.data.shade_smooth()
    for o in [o for o in bpy.data.objects if o not in before and o is not whole]:
        bpy.data.objects.remove(o)
    figures = figures_of(whole)
    parts = []
    for group in figures:
        if len(figures) == 1:
            part = whole
        else:
            part = whole.copy()
            part.data = whole.data.copy()
            bpy.context.collection.objects.link(part)
            keep = set(group)
            bm = bmesh.new()
            bm.from_mesh(part.data)
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context="VERTS")
            bm.to_mesh(part.data)
            bm.free()
        parts.append(part)
    if len(figures) > 1:
        bpy.data.objects.remove(whole)
    for part in parts:
        co = np.empty(len(part.data.vertices) * 3)
        part.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        lo, hi = co.min(axis=0), co.max(axis=0)
        foot = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))
        part.data.transform(Matrix.Translation(-foot))
        part.location = foot
        part["mag_size"] = [float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(hi[2] - lo[2])]
        part["mag_at"] = [float(foot.x), float(foot.y)]   # where the picture put it
        # The direction a figure is longest in on the ground: a fox along its body.
        flat = co[:, :2] - co[:, :2].mean(axis=0)
        values, vectors = np.linalg.eigh(np.cov(flat.T))
        axis = vectors[:, 1]
        # Which end is the head: an animal carries it higher than its tail.
        along = flat @ axis
        reach = float(np.abs(along).max()) or 1.0
        fore, aft = co[along > 0.5 * reach, 2], co[along < -0.5 * reach, 2]
        lift = 0.0
        if len(fore) > 20 and len(aft) > 20:
            lift = (float(np.percentile(fore, 98)) - float(np.percentile(aft, 98))) / max(1e-6, float(hi[2] - lo[2]))
        if lift < 0:
            axis = -axis
        part["mag_axis"] = [float(axis[0]), float(axis[1])]
        part["mag_head"] = 1 if abs(lift) > 0.08 else 0   # the axis points to the head, or we cannot tell
        part["mag_long"] = float(math.sqrt(max(values[1], 1e-12) / max(values[0], 1e-12)))
    return parts


def study(part, kind=None):
    """What a figure is and, if it has legs, its skeleton (see rig.py)."""
    mesh = part.data
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2).astype(np.int64)
    try:
        found = rig.analyse(co.reshape(-1, 3), edges, axis=list(part["mag_axis"]) if part["mag_long"] > 1.3 else None, kind=kind)
    except Exception as exc:  # a shape it cannot read moves as a whole
        found = {"kind": "object", "bones": [], "front": None, "sure": False, "notes": ["kein Skelett (" + type(exc).__name__ + ")"]}
    found["edges"] = edges
    return found


def describe(parts):
    """What a bot needs to tell the figures apart, in words and numbers."""
    out = []
    for i, part in enumerate(parts, 1):
        w, d, h = part["mag_size"]
        length = max(w, d)
        shape = "aufrecht (höher als lang)" if h > 1.25 * length else "länglich und niedrig (länger als hoch)" if length > 1.25 * h else "gedrungen"
        # Where it stands in the picture the model was made from (see FACES).
        side = "links" if part["mag_at"][1] > 0.08 * length else "rechts" if part["mag_at"][1] < -0.08 * length else "in der Mitte"
        found = study(part)
        out.append({"part": i, "kind": rig.KINDS[found["kind"]], "limbs": "; ".join(found["notes"]), "height": round(h, 3), "length": round(length, 3),
                    "width": round(min(w, d), 3), "shape": shape, "side": side if len(parts) > 1 else "",
                    "at": [round(part["mag_at"][0], 3), round(part["mag_at"][1], 3)], "vertices": len(part.data.vertices)})
    return out


def skeleton(part, found, name, weigh=True):
    """Gives a figure the bones that were found for it. Returns the skeleton;
    the figure hangs on it and follows its bones."""
    data = bpy.data.armatures.get(part.data.name + " Skelett")
    obj = bpy.data.objects.new(name, data or bpy.data.armatures.new(part.data.name + " Skelett"))
    bpy.context.collection.objects.link(obj)
    obj.location = (part["mag_at"][0], part["mag_at"][1], 0.0)
    if data is None:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        made = []
        for b in found["bones"]:
            bone = obj.data.edit_bones.new(b["name"])
            bone.head, bone.tail = Vector(b["head"]), Vector(b["tail"])
            if b["parent"] >= 0:
                bone.parent = made[b["parent"]]
            made.append(bone)
        bpy.ops.object.mode_set(mode="OBJECT")
    if weigh:
        # Every vertex follows its bone; around a joint it follows both.
        top, weights = rig.blend(found["labels"], len(found["bones"]), found["edges"])
        for b in found["bones"]:
            part.vertex_groups.new(name=b["name"])
        bm = bmesh.new()
        bm.from_mesh(part.data)
        layer = bm.verts.layers.deform.verify()
        top, weights = top.tolist(), weights.tolist()
        for v in bm.verts:
            groups = v[layer]
            for which, weight in zip(top[v.index], weights[v.index]):
                if weight > 0.0:
                    groups[which] = weight
        bm.to_mesh(part.data)
        bm.free()
    for modifier in [m for m in part.modifiers if m.type == "ARMATURE"]:
        part.modifiers.remove(modifier)
    part.parent = obj
    part.matrix_parent_inverse = Matrix.Identity(4)
    part.location = (0.0, 0.0, 0.0)
    part.modifiers.new("Skelett", "ARMATURE").object = obj
    return obj


class Rig:
    """The bones of an actor, and how they stand in every frame."""

    def __init__(self, obj, found):
        self.obj, self.kind = obj, found["kind"]
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in obj.data.bones}
        self.names = [b["name"] for b in found["bones"]]
        self.role = {}
        for b in found["bones"]:
            self.role.setdefault(b["role"], []).append(b["name"])
        self.legs = []
        for i, b in enumerate(found["bones"]):
            if b["role"] != "leg":
                continue
            shin = next(c for c in found["bones"] if c["parent"] == i and c["role"] == "shin")
            self.legs.append({"upper": b["name"], "lower": shin["name"], "fore": b["fore"], "left": b["left"], "length": b["length"],
                              "reach": Vector(shin["tail"]) - Vector(b["head"])})
        self.leg = max((leg["length"] for leg in self.legs), default=0.0)
        self.arms = []
        for i, b in enumerate(found["bones"]):
            if b["role"] == "arm":
                lower = next(c for c in found["bones"] if c["parent"] == i and c["role"] == "forearm")
                reach = Vector(b["reach"])
                self.arms.append({"upper": b["name"], "lower": lower["name"], "left": b["left"], "reach": reach, "out": Vector((b["out"][0], b["out"][1], 0.0)),
                                  "hangs": -reach.z > 0.75 * reach.length})
        self.frames = {}                  # frame -> {bone: how it is turned from rest, seen from the figure}

    def turn(self, frame, name, axis, angle):
        self.apply(frame, name, Quaternion(axis, angle))

    def apply(self, frame, name, rotation):
        pose = self.frames.setdefault(frame, {})
        pose[name] = rotation @ pose.get(name, Quaternion())

    def beat(self, leg, gait):
        """When in a stride a leg leaves the ground: an animal walks one foot
        after the other and trots in diagonal pairs; on two legs they take turns."""
        if self.kind == "biped":
            return 0.0 if leg["left"] else 0.5
        if gait == "run":
            return 0.0 if leg["fore"] == leg["left"] else 0.5
        return (0.0 if leg["left"] else 0.5) + (0.25 if leg["fore"] else 0.0)

    def stride(self, frame, cycle, on, heading, gait):
        """The limbs at one moment of a gait: `cycle` counts the strides so
        far, `on` is how much of the gait is there (0 standing, 1 walking),
        `heading` where it goes as the figure sees it."""
        swing, duty, bend, _, _ = STRIDES[gait]
        swing, bend = math.radians(swing), math.radians(bend)
        ahead = Vector((heading.x, heading.y, 0.0)).normalized()
        across = Vector((ahead.y, -ahead.x, 0.0))    # turning about it swings a leg forwards
        up = Vector((0.0, 0.0, 1.0))
        for leg in self.legs:
            at = (cycle + self.beat(leg, gait)) % 1.0
            if at < duty:                            # the foot is on the ground and the body passes over it
                angle, lift = swing * (1 - 2 * at / duty), 0.0
            else:                                    # the foot swings forward through the air
                air = (at - duty) / (1 - duty)
                angle, lift = swing * (2 * ease(air) - 1), math.sin(math.pi * air)
            # A model stands as it was caught, often in mid-step: the stride starts from an even stance.
            caught = math.atan2(leg["reach"].dot(ahead), -leg["reach"].z)
            self.turn(frame, leg["upper"], across, on * (angle - caught))
            self.turn(frame, leg["lower"], across, -on * bend * lift)
        wave = math.sin(2 * math.pi * cycle)
        if self.kind == "quadruped":
            for name in self.role.get("hips", []):
                self.turn(frame, name, up, on * math.radians(3.0) * wave)
            for name in self.role.get("chest", []):
                self.turn(frame, name, up, -on * math.radians(6.0) * wave)
            for name in self.role.get("neck", []):
                self.turn(frame, name, across, -on * math.radians(2.5) * math.sin(4 * math.pi * cycle))
            for name in self.role.get("head", []):
                self.turn(frame, name, up, on * math.radians(3.0) * wave)
            for n, name in enumerate(self.role.get("tail", [])):
                self.turn(frame, name, up, on * math.radians(9.0 + 4.0 * n) * math.sin(2 * math.pi * cycle - 1.0 - 0.9 * n))
        else:
            for name in self.role.get("hips", []):
                self.turn(frame, name, ahead, on * math.radians(2.5) * wave)
            for name in self.role.get("chest", []):
                self.turn(frame, name, up, -on * math.radians(5.0) * wave)
            for name in self.role.get("head", []):
                self.turn(frame, name, up, on * math.radians(4.0) * wave)
            # An arm that hangs swings against the leg of its side (and what it carries swings with it).
            for arm in self.arms:
                if arm["hangs"]:
                    self.turn(frame, arm["upper"], across, on * math.radians(13.0) * (wave if arm["left"] else -wave))

    def greet(self, frame, on, beat, front):
        """Waves: an arm goes up and its hand to and fro; an animal lifts a
        front paw. `on`: how far the arm is up, `beat`: -1..1, the hand. False:
        there is nothing to wave with."""
        up, ahead = Vector((0.0, 0.0, 1.0)), Vector((front.x, front.y, 0.0)).normalized()
        if self.arms:
            arm = max(self.arms, key=lambda arm: arm["reach"].z)   # the hand that is up is free; what hangs low carries something
            raised = (arm["out"] * 0.5 + up).normalized()
            lift = arm["reach"].normalized().rotation_difference(raised)
            # An arm at its side goes up a good way; one that is bent or holds something only a little.
            most = math.radians(80.0 if arm["hangs"] else 45.0)
            lift = Quaternion().slerp(lift, min(1.0, most / max(1e-6, lift.angle)))
            self.apply(frame, arm["upper"], Quaternion().slerp(lift, on))
            self.turn(frame, arm["lower"], lift.inverted() @ ahead, on * math.radians(24.0) * beat)
            return True
        paws = [leg for leg in self.legs if leg["fore"]]
        if paws:
            leg = paws[0]
            across = Vector((ahead.y, -ahead.x, 0.0))
            caught = math.atan2(leg["reach"].dot(ahead), -leg["reach"].z)
            self.turn(frame, leg["upper"], across, on * (math.radians(62.0) - caught))
            self.turn(frame, leg["lower"], across, -on * math.radians(24.0 + 14.0 * beat))
            return True
        return False

    def tuck(self, frame, air):
        """Legs drawn up in a jump (`air`: 0 on the ground, 1 at the top)."""
        for leg in self.legs:
            reach = Vector((leg["reach"].x, leg["reach"].y, 0.0))
            across = Vector((0.0, 1.0, 0.0)) if reach.length < 1e-6 else Vector((reach.y, -reach.x, 0.0)).normalized()
            self.turn(frame, leg["lower"], across, -math.radians(38.0) * air)

    def write(self, last):
        """The frames as keyframes of the bones."""
        obj = self.obj
        if obj.animation_data is None or obj.animation_data.action is None:
            return
        curves = obj.animation_data.action.fcurves
        frames = np.arange(last + 1, dtype=float)
        for name in self.names:
            obj.pose.bones[name].rotation_mode = "QUATERNION"
            rest = self.rest[name]
            values, before = np.empty((last + 1, 4)), None
            for frame in range(last + 1):
                turned = self.frames.get(frame, {}).get(name)
                q = Quaternion() if turned is None else rest.conjugated() @ turned @ rest
                if before is not None and before.dot(q) < 0:   # the same turn, written the near way
                    q.negate()
                values[frame] = (q.w, q.x, q.y, q.z)
                before = q
            if np.allclose(values, values[0]) and np.allclose(values[0], (1, 0, 0, 0)):
                continue                                       # a bone that never moves needs no keys
            for index in range(4):
                curve = curves.new('pose.bones["%s"].rotation_quaternion' % name, index=index, action_group=name)
                curve.keyframe_points.add(last + 1)
                curve.keyframe_points.foreach_set("co", np.stack([frames, values[:, index]], axis=1).ravel())
                curve.keyframe_points.foreach_set("interpolation", np.ones(last + 1, dtype=np.int32))   # linear
                curve.update()


# ---- motion ------------------------------------------------------------------
def ease(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def find(actors, name):
    """An actor by its name, however the caller capitalised it."""
    if not isinstance(name, str):
        return None
    return actors.get(name) or next((a for key, a in actors.items() if key.lower() == name.strip().lower()), None)


def number(source, key, default, lo, hi):
    """A number of a description, within its bounds; anything else is the default."""
    try:
        value = float(source.get(key))
    except (TypeError, ValueError):
        return default
    return default if value != value else min(hi, max(lo, value))


def pair(value):
    """A point on the ground: [x, y] or "x, y"."""
    if isinstance(value, str):
        value = value.replace(";", ",").replace("[", "").replace("]", "").split(",")
    try:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        pass
    return None


class Actor:
    def __init__(self, name, part, found=None, weigh=True):
        self.name, self.obj, self.part = name, part, part
        self.size = list(part["mag_size"])
        self.pos = Vector(part["mag_at"])
        self.yaw = 0.0                    # turned by this much from how the model stands
        self.axis = Vector(part["mag_axis"]) if part["mag_long"] > 1.3 else None
        self.headed = bool(part["mag_head"])   # the axis points to the head, not just along the body
        self.rig, self.kind, self.notes = None, "object", list(found["notes"]) if found else []
        if found and found["bones"]:
            try:
                self.obj = skeleton(part, found, name, weigh)
                self.rig, self.kind = Rig(self.obj, found), found["kind"]
                if found["front"]:        # its legs say where it looks
                    self.axis, self.headed = Vector(found["front"]), bool(found["sure"])
            except Exception as exc:      # no skeleton after all: it moves as a whole
                self.obj, self.rig, self.kind = part, None, "object"
                self.notes = ["kein Skelett (" + type(exc).__name__ + ")"]
        part.name = name if self.obj is part else name + " Körper"
        self.obj.name = name
        self.scale = 1.0
        self.clock = 0.0                  # until when it is busy (seconds)
        self.placed = False               # put somewhere on purpose, or moved already
        self.keys = {}                    # frame -> (x, y, z, pitch, roll, yaw, squash, scale)

    @property
    def height(self):
        return self.size[2] * self.scale

    @property
    def length(self):
        return max(self.size[0], self.size[1]) * self.scale

    @property
    def radius(self):
        return 0.5 * math.hypot(self.size[0], self.size[1]) * self.scale

    def front(self):
        """Where the figure points now, when its shape says so."""
        if self.axis is None:
            return None
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return Vector((self.axis.x * c - self.axis.y * s, self.axis.x * s + self.axis.y * c))

    def key(self, frame, z=0.0, pitch=0.0, roll=0.0, squash=1.0):
        self.keys[frame] = (self.pos.x, self.pos.y, z, pitch, roll, self.yaw, squash, self.scale)

    def turn_towards(self, direction):
        """The turn that makes the figure look in a direction. Where we only
        know how its body lies and not which end is the head, it is the
        smaller turn that lines the body up: nobody spins around on a guess."""
        front = self.front()
        if front is None or direction.length < 1e-6:
            return 0.0
        angle = math.atan2(front.x * direction.y - front.y * direction.x, front.dot(direction))
        if not self.headed:
            if angle > math.pi / 2:
                angle -= math.pi
            elif angle < -math.pi / 2:
                angle += math.pi
        return angle


def play(actor, action, actors, fps, start):
    """Writes one action of an actor, beginning at `start` seconds. Returns its last frame."""
    kind = action.get("do", "")
    first = int(round(max(0.0, start) * fps))
    actor.key(max(0, first - 1))

    def target_point():
        target = action.get("target")
        other = find(actors, target)
        if other is actor:
            fail("Aktion " + kind + ": " + actor.name + " kann nicht sein eigenes Ziel sein.")
        if other:
            return other.pos.copy(), other.radius
        point = pair(target)
        if point:
            return Vector(point), 0.0
        fail("Aktion " + kind + " von " + actor.name + ": target fehlt oder ist unbekannt. Darsteller sind: " + ", ".join(actors))

    def turn(angle, seconds):
        frames = max(2, int(round(seconds * fps)))
        yaw0 = actor.yaw
        for i in range(1, frames + 1):
            actor.yaw = yaw0 + angle * ease(i / frames)
            actor.key(first + i)
        return frames

    if kind in ("walk_to", "run_to", "hop_to", "sneak_to"):
        stride, bounce, speed, sway = GAITS[kind[:-3]]
        goal, room = target_point()
        way = goal - actor.pos
        if way.length < 1e-6:
            fail(actor.name + " steht schon am Ziel.")
        heading = way.normalized()
        # Where it stops: in front of the other, not inside.
        apart = number(action, "distance", room + 0.55 * actor.length + 0.04 * actor.height, 0.0, 100.0)
        distance = max(0.0, way.length - apart)
        # Figures from one picture often stand side by side already. Somebody
        # who is to walk over then starts from further away.
        needed = 2.4 * actor.length
        if distance < needed and not actor.placed:
            actor.pos = goal - heading * (apart + needed)
            for frame in list(actor.keys):
                actor.keys[frame] = (actor.pos.x, actor.pos.y) + actor.keys[frame][2:]
            distance = needed
        actor.placed = True
        offset = turn(actor.turn_towards(heading), 0.35) if abs(actor.turn_towards(heading)) > 0.03 else 0
        legs = actor.rig is not None and bool(actor.rig.legs)
        origin = actor.pos.copy()
        # As the figure sees it: where it is going.
        ahead = Vector((heading.x * math.cos(actor.yaw) + heading.y * math.sin(actor.yaw), heading.y * math.cos(actor.yaw) - heading.x * math.sin(actor.yaw)))
        if legs and kind[:-3] in STRIDES:
            # It walks on its legs: a stride is as long as they reach, and the body rides along.
            swing, duty, _, rate, bounce = STRIDES[kind[:-3]]
            reach = 2 * actor.rig.leg * actor.scale * math.sin(math.radians(swing)) / duty
            seconds = number(action, "seconds", distance / max(1e-6, reach * rate) + 0.5, 0.6, 20.0)
            frames = max(2, int(round(seconds * fps)))
            for i in range(1, frames + 1):
                u = ease(i / frames)
                actor.pos = origin + heading * (distance * u)
                cycle, on = distance * u / reach, min(1.0, 5 * min(u, 1 - u))
                actor.rig.stride(first + offset + i, cycle, on, ahead, kind[:-3])
                actor.key(first + offset + i, z=on * bounce * actor.height * (0.5 - 0.5 * math.cos(4 * math.pi * cycle)))
        else:
            seconds = number(action, "seconds", distance / max(1e-6, speed * actor.length) + 0.4, 0.6, 20.0)
            frames = max(2, int(round(seconds * fps)))
            steps = max(1.0, distance / (stride * actor.length))
            upright = actor.axis is None
            for i in range(1, frames + 1):
                u = ease(i / frames)
                actor.pos = origin + heading * (distance * u)
                phase = u * steps * math.pi             # one step per half wave
                on = min(1.0, 4 * min(u, 1 - u) + 0.15)
                lift = abs(math.sin(phase)) * bounce * actor.height * on
                lean = math.sin(phase * 2) * sway * 0.5
                rock = math.sin(phase) * sway * (1.6 if upright else 0.6)
                squash = 1.0 + (0.06 * math.sin(phase * 2) if kind == "hop_to" else 0.0)
                if legs:                                # hopping: the legs are drawn up in the air
                    actor.rig.tuck(first + offset + i, abs(math.sin(phase)) * on)
                    lean = rock = 0.0
                actor.key(first + offset + i, z=lift, pitch=lean, roll=rock, squash=squash)
        end = first + offset + frames
    elif kind == "turn_to":
        goal, _ = target_point()
        angle = actor.turn_towards(goal - actor.pos)
        if actor.front() is None:
            fail(actor.name + " hat keine erkennbare Vorderseite; nimm turn mit degrees.")
        end = first + turn(angle, number(action, "seconds", 0.6, 0.2, 10.0))
    elif kind == "turn":
        end = first + turn(math.radians(number(action, "degrees", 180.0, -1080.0, 1080.0)), number(action, "seconds", 0.8, 0.2, 10.0))
    elif kind == "spin":
        turns = int(number(action, "times", 1, 1, 6))
        end = first + turn(2 * math.pi * turns, number(action, "seconds", 0.9 * turns, 0.3, 12.0))
    elif kind == "jump":
        times = int(number(action, "times", 1, 1, 8))
        per = max(6, int(round(0.55 * fps)))
        for n in range(times):
            for i in range(1, per + 1):
                u = i / per
                air = 4 * u * (1 - u)
                if actor.rig:
                    actor.rig.tuck(first + n * per + i, air)
                actor.key(first + n * per + i, z=air * 0.45 * actor.height, squash=1.0 + (0.0 if actor.rig else 0.10 * (air - 0.5)))
        end = first + times * per
    elif kind == "wobble":
        frames = max(6, int(round(number(action, "seconds", 1.2, 0.3, 10.0) * fps)))
        for i in range(1, frames + 1):
            u = i / frames
            actor.key(first + i, roll=math.sin(u * math.pi * 6) * 0.16 * (1 - u))
        end = first + frames
    elif kind in ("grow", "shrink"):
        factor = number(action, "factor", 1.5 if kind == "grow" else 0.6, 0.05, 20.0)
        factor = min(4.0, max(0.2, factor if kind == "grow" or factor < 1 else 1 / factor))
        frames = max(4, int(round(number(action, "seconds", 0.8, 0.2, 10.0) * fps)))
        scale0 = actor.scale
        for i in range(1, frames + 1):
            actor.scale = scale0 * (1 + (factor - 1) * ease(i / frames))
            actor.key(first + i)
        end = first + frames
    elif kind == "wait":
        end = first + max(1, int(round(number(action, "seconds", 1.0, 0.1, 20.0) * fps)))
        actor.key(end)
    elif kind == "wave":
        frames = max(12, int(round(number(action, "seconds", 2.4, 1.0, 12.0) * fps)))
        front = actor.axis if actor.axis is not None and actor.headed else Vector(FACES["camera"])
        rise = max(3, int(0.4 * fps))
        for i in range(1, frames + 1):
            on = ease(min(1.0, min(i, frames - i) / rise))
            beat = math.sin(2 * math.pi * 2.3 * i / fps)
            if not (actor.rig and actor.rig.greet(first + i, on, beat, front)):
                actor.key(first + i, roll=0.1 * on * beat)   # nothing to wave with: it sways
        end = first + frames
    elif kind in ("nod", "shake_head", "wag_tail", "look_at"):
        # What a head or a tail does. A figure without one does it with all it has.
        bones = actor.rig.role if actor.rig else {}
        front = actor.axis if actor.axis is not None and actor.headed else Vector(FACES["camera"])
        across, up = Vector((front.y, -front.x, 0.0)), Vector((0.0, 0.0, 1.0))
        times = int(number(action, "times", 2 if kind != "look_at" else 1, 1, 12))
        if kind == "nod":
            frames = max(6, int(round(number(action, "seconds", 0.55 * times, 0.3, 12.0) * fps)))
            for i in range(1, frames + 1):
                dip = 0.5 - 0.5 * math.cos(2 * math.pi * times * i / frames)
                if bones.get("head"):
                    actor.rig.turn(first + i, bones["head"][0], across, -math.radians(20.0) * dip)
                    for name in bones.get("neck", []):
                        actor.rig.turn(first + i, name, across, -math.radians(8.0) * dip)
                else:
                    actor.key(first + i, z=-0.03 * actor.height * dip, squash=1.0 - 0.06 * dip)
        elif kind == "shake_head":
            frames = max(6, int(round(number(action, "seconds", 0.5 * times, 0.3, 12.0) * fps)))
            for i in range(1, frames + 1):
                u = i / frames
                swing = math.sin(2 * math.pi * times * u) * min(1.0, 6 * min(u, 1 - u))
                if bones.get("head"):
                    actor.rig.turn(first + i, bones["head"][0], up, math.radians(24.0) * swing)
                else:
                    actor.key(first + i, roll=0.12 * swing)
        elif kind == "wag_tail":
            frames = max(6, int(round(number(action, "seconds", 1.5, 0.3, 12.0) * fps)))
            for i in range(1, frames + 1):
                u = i / frames
                on = min(1.0, 6 * min(u, 1 - u))
                for n, name in enumerate(bones.get("tail", [])):
                    actor.rig.turn(first + i, name, up, on * math.radians(20.0 + 10.0 * n) * math.sin(2 * math.pi * 3.2 * i / fps - 0.8 * n))
                if not bones.get("tail"):
                    actor.key(first + i, roll=0.07 * on * math.sin(2 * math.pi * 3.2 * i / fps))
        else:
            goal, _ = target_point()
            way = goal - actor.pos
            now = actor.front() if actor.headed else None
            if now is None or not bones.get("head"):
                end = first + turn(actor.turn_towards(way), number(action, "seconds", 0.6, 0.2, 10.0))   # it turns as a whole
                frames = 0
            else:
                angle = max(-math.radians(75.0), min(math.radians(75.0), math.atan2(now.x * way.y - now.y * way.x, now.dot(way))))
                frames = max(8, int(round(number(action, "seconds", 1.8, 0.5, 12.0) * fps)))
                share = [(name, 0.4) for name in bones.get("neck", [])] + [(bones["head"][0], 0.6 if bones.get("neck") else 1.0)]
                for i in range(1, frames + 1):
                    held = ease(min(1.0, 4 * min(i, frames - i) / frames))
                    for name, part in share:
                        actor.rig.turn(first + i, name, up, angle * part * held)
        if kind != "look_at" or frames:
            end = first + frames
    else:
        fail("Unbekannte Aktion: " + str(kind) + ". Es gibt walk_to, run_to, hop_to, sneak_to, turn_to, look_at, turn, spin, jump, wave, nod, "
             "shake_head, wag_tail, wobble, grow, shrink, wait.")
    actor.key(end)
    actor.clock = end / fps
    return end


def linear(obj):
    if obj.animation_data and obj.animation_data.action:
        for curve in getattr(obj.animation_data.action, "fcurves", []):
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"


def animate(actor, last):
    """Writes the samples of an actor as keyframes, one per frame."""
    obj = actor.obj
    obj.rotation_mode = "XYZ"
    frames = sorted(actor.keys)
    state = actor.keys[frames[0]]
    at = 0
    actor.track = []                                 # where it is in every frame: (x, y, reach)
    for frame in range(0, last + 1):
        while at < len(frames) and frames[at] <= frame:
            state = actor.keys[frames[at]]
            at += 1
        x, y, z, pitch, roll, yaw, squash, scale = state
        obj.location = (x, y, z)
        obj.rotation_euler = (pitch, roll, yaw)
        wide = scale / math.sqrt(squash)             # what is pressed down bulges out
        obj.scale = (wide, wide, scale * squash)
        for path in ("location", "rotation_euler", "scale"):
            obj.keyframe_insert(data_path=path, frame=frame)
        actor.track.append((x, y, 0.5 * math.hypot(actor.size[0], actor.size[1]) * scale, actor.size[2] * scale))
    linear(obj)
    if actor.rig:
        actor.rig.write(last)


# ---- the scene around the actors ---------------------------------------------
def principled(name, colour, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*colour, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    return material


def side_of(actors):
    """From where the action reads best: across the way the actors go, on the
    side the pictures were taken from. An angle around the scene."""
    travel = Vector((0.0, 0.0))
    for a in actors.values():
        travel += Vector((a.track[-1][0] - a.track[0][0], a.track[-1][1] - a.track[0][1]))
    side = math.atan2(travel.y, travel.x) - math.pi / 2 if travel.length > 1e-6 else math.pi
    return side + math.pi if math.cos(side) > 1e-6 else side


def lamp(name, around, height, energy, colour, only=None):
    """A light from far away: where it stands around the scene and how high
    (radians). `only`: the figures it is for; it lights nothing else and
    casts no shadow."""
    bpy.ops.object.light_add(type="SUN", rotation=(math.pi / 2 - height, 0, around + math.pi / 2))
    light = bpy.context.active_object
    light.name = name
    light.data.energy = energy
    light.data.color = colour
    light.data.angle = math.radians(6)
    if only is not None:
        light.data.use_shadow = False
        light.light_linking.receiver_collection = only
    return light


def dress(spec, centre, radius, side, figures):
    """Ground, sky and the lights."""
    name = spec.get("light") if spec.get("light") in LIGHTS else "day"
    light = LIGHTS[name]
    colour, roughness = GROUNDS.get(spec.get("ground"), GROUNDS["studio" if name == "studio" else "grass"])
    bpy.ops.mesh.primitive_circle_add(vertices=72, radius=radius, fill_type="NGON", location=(centre.x, centre.y, 0))
    ground = bpy.context.active_object
    ground.name = "Boden"
    ground.data.materials.append(principled("Boden", colour, roughness))
    scene = bpy.context.scene
    world = bpy.data.worlds.new("Welt")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputWorld")
    mix = nodes.new("ShaderNodeMixShader")
    seen = nodes.new("ShaderNodeLightPath")
    lit = nodes.new("ShaderNodeBackground")
    back = nodes.new("ShaderNodeBackground")
    lit.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    lit.inputs["Strength"].default_value = light["ambient"]
    back.inputs["Strength"].default_value = light["behind"]
    backdrop = light["backdrop"]
    path = os.path.join(bpy.utils.resource_path("LOCAL"), "datafiles", "studiolights", "world", light["sky"]) if light["sky"] else ""
    if os.path.exists(path):
        image = nodes.new("ShaderNodeTexEnvironment")
        image.image = bpy.data.images.load(path)
        image.image.pack()                           # the scene file is complete without this Blender
        links.new(image.outputs["Color"], lit.inputs["Color"])
        if backdrop is None:
            links.new(image.outputs["Color"], back.inputs["Color"])
    if backdrop is not None or not os.path.exists(path):
        back.inputs["Color"].default_value = (*(backdrop or (0.5, 0.6, 0.7)), 1.0)
    # The sky lights the scene; what the camera sees of it is chosen separately.
    links.new(seen.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(lit.outputs["Background"], mix.inputs[1])
    links.new(back.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    # Key over the camera's left shoulder: faces are lit, shadows fall away.
    # The fill opens what the key leaves dark, the rim draws the outline.
    # Both are for the figures alone: the ground keeps its colour and one shadow.
    cast = bpy.data.collections.new("Figuren")
    for figure in figures:
        cast.objects.link(figure)
    lamp("Sonne", side - math.radians(40), math.radians(40), light["key"], light["colour"])
    lamp("Aufhellung", side + math.radians(55), math.radians(20), light["fill"], (1.0, 1.0, 1.0), only=cast)
    lamp("Kante", side + math.radians(155), math.radians(30), light["rim"], light["colour"], only=cast)
    # Colours as they are, like the chat shows them: no film curve over the picture.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    return ground


def glow(objects, part):
    """Figures show a part of their own colour whatever the light: their
    texture is painted light, and so the chat shows them."""
    for material in {slot.material for obj in objects for slot in obj.material_slots if slot.material and slot.material.use_nodes}:
        shader = material.node_tree.nodes.get("Principled BSDF")
        if not shader or "Emission Color" not in shader.inputs:
            continue
        base = shader.inputs["Base Color"]
        if base.links:
            material.node_tree.links.new(base.links[0].from_socket, shader.inputs["Emission Color"])
        else:
            shader.inputs["Emission Color"].default_value = base.default_value
        shader.inputs["Emission Strength"].default_value = part


def calm(values, fps):
    """A value per frame, smoothed over about a second: a camera does not jerk."""
    width = max(3, int(1.2 * fps) | 1)
    window = np.hanning(width + 2)[1:-1]
    padded = np.concatenate([np.full(width // 2, values[0]), values, np.full(width // 2, values[-1])])
    return np.convolve(padded, window / window.sum(), mode="valid")


def film(actors, last, fps, side):
    """The camera of the film. It keeps everybody in the frame (so it comes
    closer when they do) and drifts a little."""
    cx, cy, wide, tall = (np.empty(last + 1) for _ in range(4))
    for frame in range(last + 1):
        spots = [a.track[frame] for a in actors.values()]
        lo = (min(x - r for x, _, r, _ in spots), min(y - r for _, y, r, _ in spots))
        hi = (max(x + r for x, _, r, _ in spots), max(y + r for _, y, r, _ in spots))
        cx[frame], cy[frame] = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        wide[frame] = 0.5 * math.hypot(hi[0] - lo[0], hi[1] - lo[1])
        tall[frame] = max(h for _, _, _, h in spots)
    cx, cy, wide, tall = calm(cx, fps), calm(cy, fps), calm(wide, fps), calm(tall, fps)
    bpy.ops.object.camera_add()
    camera = bpy.context.active_object
    camera.name = "Kamera"
    camera.data.lens = 50
    bpy.context.scene.camera = camera
    bpy.ops.object.empty_add()
    aim = bpy.context.active_object
    aim.name = "Blick"
    track = camera.constraints.new("TRACK_TO")
    track.target = aim
    track.track_axis, track.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    across, upright = 18 / 50, 18 / 50 * 9 / 16     # half the angle of view (50 mm lens, 36 mm film, 16:9), as tangents
    for frame in range(last + 1):
        distance = max(1.25 * wide[frame] / across, 0.85 * tall[frame] / upright)
        angle = side + math.radians(-11 + 22 * frame / max(1, last))
        aim.location = (cx[frame], cy[frame], 0.45 * tall[frame])
        # Low enough that the horizon and the sky behind it are in the picture.
        camera.location = (cx[frame] + math.cos(angle) * distance, cy[frame] + math.sin(angle) * distance, 0.45 * tall[frame] + 0.12 * distance)
        aim.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="location", frame=frame)
    linear(aim)
    linear(camera)
    return camera


def build(spec_path, out):
    spec = json.load(open(spec_path))
    fps = FPS
    actors, loaded = {}, {}
    wanted = spec.get("actors") or []
    if not wanted or len(wanted) > MAX_ACTORS:
        fail("Eine Szene braucht 1 bis %d Darsteller." % MAX_ACTORS)
    for entry in wanted:
        if entry["file"] not in loaded:
            loaded[entry["file"]] = load_model(entry["file"])
    used, studied = {}, {}
    for i, entry in enumerate(wanted):
        entry["name"] = str(entry.get("name") or "Darsteller %d" % (i + 1))[:40]
        parts = loaded[entry["file"]]
        index = int(number(entry, "part", 0, 0, 99)) or None
        if index is None and len(parts) > 1:
            which = "; ".join("part %d: %s, %s%s" % (d["part"], d["kind"], d["shape"], ", steht im Bild " + d["side"] if d["side"] else "") for d in describe(parts))
            fail("Das Modell %s zeigt %d Figuren. Gib bei jedem Darsteller part an (%s)." % (entry.get("model", "?"), len(parts), which))
        index = index or 1
        if index > len(parts):
            fail("Das Modell %s hat nur %d Teil(e)." % (entry.get("model", "?"), len(parts)))
        obj = parts[index - 1]
        copies = used.get((entry["file"], index), 0)
        if not copies:  # what it is: an animal, somebody on two legs, a thing
            studied[(entry["file"], index)] = study(obj, KINDS.get(entry.get("kind")))
        else:           # the same figure again: a twin
            twin = obj.copy()
            twin.data = obj.data
            bpy.context.collection.objects.link(twin)
            for key in ("mag_size", "mag_axis", "mag_head", "mag_long", "mag_at"):
                twin[key] = obj[key]
            obj = twin
        used[(entry["file"], index)] = copies + 1
        name = entry["name"]
        if find(actors, name):
            fail("Zwei Darsteller heißen " + name + ".")
        actor = Actor(name, obj, studied[(entry["file"], index)], weigh=not copies)
        actor.scale = number(entry, "size", 1.0, 0.2, 5.0)
        if entry.get("faces") in FACES and not actor.headed:
            # Where it looks in its picture, as the caller sees it. Of a figure
            # whose body already says "this way or the opposite", it picks one.
            seen = Vector(FACES[entry["faces"]])
            if actor.axis is not None and abs(actor.axis.dot(seen)) > 0.35:
                seen = actor.axis if actor.axis.dot(seen) > 0 else -actor.axis
            actor.axis, actor.headed = seen, True
        actor.pos.y -= copies * 2.4 * actor.radius    # a twin stands next to its original
        actors[name] = actor
    # Figures of one model stay where the picture put them. Models from
    # different pictures line up side by side as the camera sees them, the
    # first on the left, with room to move.
    files = []
    for entry in wanted:
        if entry["file"] not in files:
            files.append(entry["file"])
    cursor = 0.0
    for file in files:
        group = [actors[e["name"]] for e in wanted if e["file"] == file]
        if len(files) > 1 and group:
            lo = min(a.pos.y - a.radius for a in group)
            hi = max(a.pos.y + a.radius for a in group)
            for a in group:
                a.pos.y += cursor - hi
            cursor -= (hi - lo) + 1.2 * max(a.length for a in group)
    for entry in wanted:
        actor, at = actors[entry["name"]], pair(entry.get("at"))
        if at:
            actor.pos = Vector(at)
            actor.placed = True
    cast = [a.part for a in actors.values()]
    for obj in [o for parts in loaded.values() for o in parts if not any(o == c for c in cast)]:
        bpy.data.objects.remove(obj)  # figures of a model that play no part
    for actor in actors.values():
        actor.key(0)
    # The actions are a story: one after the other in the order given, unless
    # an action says that it starts together with the one before it (or when).
    last, clock, began = 0, 0.3, 0.3
    for action in spec.get("actions") or []:
        actor = find(actors, action.get("actor"))
        if not actor:
            fail("Aktion für unbekannten Darsteller: " + str(action.get("actor")) + ". Darsteller sind: " + ", ".join(actors))
        start = number(action, "start", -1.0, 0.0, MAX_SECONDS)
        if start < 0:
            start = max(began if action.get("together") is True else clock, actor.clock)
        end = play(actor, action, actors, fps, start)
        began, clock, last = start, max(clock, end / fps + 0.15), max(last, end)
    if last > MAX_SECONDS * fps:
        fail("Die Szene wäre %d Sekunden lang, höchstens %d sind möglich. Nimm weniger Aktionen oder gib ihnen kürzere seconds." % (last // fps + 1, MAX_SECONDS))
    last = min(MAX_SECONDS * fps, max(last + int(0.8 * fps), int(2.5 * fps), int(number(spec, "seconds", 0.0, 0.0, MAX_SECONDS) * fps)))
    for actor in actors.values():
        animate(actor, last)
    # The stage: everything every actor ever touches, with a margin.
    points = [Vector((k[0], k[1])) for a in actors.values() for k in a.keys.values()]
    lo = Vector((min(p.x for p in points), min(p.y for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points)))
    centre = (lo + hi) / 2
    reach = max(r for a in actors.values() for _, _, r, _ in a.track)
    radius = 0.5 * (hi - lo).length + 1.0 * reach
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 0, last
    scene.render.fps = fps
    side = side_of(actors)
    ground = dress(spec, centre, radius * 1.25, side, [a.part for a in actors.values()])
    film(actors, last, fps, side)
    os.makedirs(out, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for actor in actors.values():
        actor.obj.select_set(True)
        actor.part.select_set(True)
    ground.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(out, "scene.glb"), export_format="GLB", use_selection=True, export_yup=True,
                              export_animations=True, export_animation_mode="SCENE", export_anim_scene_split_object=False,
                              export_frame_range=True, export_force_sampling=True,
                              export_image_format="JPEG", export_jpeg_quality=88, export_cameras=False, export_lights=False)
    # The chat shows the figures unlit; in the film they keep a part of that.
    name = spec.get("light") if spec.get("light") in LIGHTS else "day"
    glow([a.part for a in actors.values()], LIGHTS[name]["glow"])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "scene.blend"), compress=True)
    facts = {"title": str(spec.get("title") or "Szene")[:80], "seconds": round(last / fps, 2), "frames": last + 1, "fps": fps,
             "actors": [{"name": a.name, "height": round(a.height, 3), "kind": rig.KINDS[a.kind], "notes": "; ".join(a.notes)} for a in actors.values()]}
    json.dump(facts, open(os.path.join(out, "scene.json"), "w"), ensure_ascii=False)
    print("MAG-BUILT " + json.dumps(facts, ensure_ascii=False), flush=True)


def render(out):
    bpy.ops.wm.open_mainfile(filepath=os.path.join(out, "scene.blend"))
    scene = bpy.context.scene
    quality = os.environ.get("MAG_SCENE_QUALITY", "normal")
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"                      # never a GPU: the MI50 belongs to photo-to-3D
    threads = int(os.environ.get("MAG_SCENE_THREADS", "0") or 0)
    if threads > 0:                                  # leave the other services their share of the cores
        scene.render.threads_mode, scene.render.threads = "FIXED", threads
    scene.cycles.samples = 10 if quality == "fast" else 14
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 4
    scene.render.use_persistent_data = True          # the models do not change between frames
    # A film for the chat, there within minutes: the scene itself can be turned in full detail.
    scene.render.resolution_x, scene.render.resolution_y = (640, 360) if quality == "fast" else (768, 432)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = os.path.join(out, "frames", "f")
    scene.render.use_overwrite = False               # a render that was interrupted goes on where it stopped
    scene.render.use_placeholder = False
    scene.cycles.adaptive_threshold = 0.05
    # In the chat the ground is a round stage; in the film it reaches the horizon.
    ground = bpy.data.objects.get("Boden")
    if ground:
        ground.scale = (12, 12, 1)
    total = scene.frame_end - scene.frame_start + 1

    def progress(scene_now, *_):
        print("MAG-FRAME %d/%d" % (scene_now.frame_current - scene_now.frame_start + 1, total), flush=True)

    bpy.app.handlers.render_post.append(progress)
    bpy.ops.render.render(animation=True)
    print("MAG-RENDERED %d" % total, flush=True)


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(args) == 3 and args[0] == "parts":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        json.dump(describe(load_model(args[1])), open(args[2], "w"), ensure_ascii=False)
        print("MAG-PARTS ok", flush=True)
    elif len(args) == 3 and args[0] == "build":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        build(args[1], args[2])
    elif len(args) == 2 and args[0] == "render":
        render(args[1])
    else:
        fail("usage: scene.py -- parts model.glb out.json | build spec.json outdir | render outdir")


try:
    main()
except SystemExit:
    raise
except Exception as exc:  # the caller gets words; the traceback is in the log
    import traceback
    traceback.print_exc()
    fail("Blender ist an dieser Szene gescheitert (" + type(exc).__name__ + ").")

"""MAG scenes: a face that moves, on a figure on two legs.

Runs inside Blender. A model made from a photo has no face rig: its mouth
and eyes are painted on one closed surface. The face is found once per
figure that is to show it: the front of its head is rendered (Cycles, CPU)
and MediaPipe's face landmarker (Apache-2.0, ops/scene/face/bootstrap.sh)
finds mouth, eyes and brows in the picture; each is carried back onto the
surface along the camera's ray. Where MediaPipe is not installed or sees no
face (a gnome's face may be too small or too strange), the face is placed
by the proportions of a head. Small bones under the head bone then carry
the skin around them: a jaw that opens, the corners of the mouth, the upper
lids, the brows. Expressions and speech are their movements over time:
speech goes from the letters of what is said to mouth shapes (visemes).
"""
import importlib.util
import math
import os
import re
import sys
import tempfile
import types

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

PLACES = [os.environ.get("MAG_SCENE_FACE", "/opt/mag-scene/face"), os.path.expanduser("~/.cache/mag-stack/face")]
# MediaPipe face mesh points: the lips' middle (upper, lower), the corners of the
# mouth, the chin, the upper and lower lids and the middle of the brows, of the
# figure's own right and left (the right is on the left of the picture).
POINTS = {"upper_lip": 13, "lower_lip": 14, "corner_r": 61, "corner_l": 291, "chin": 152,
          "lid_r": 159, "under_r": 145, "lid_l": 386, "under_l": 374, "brow_r": 105, "brow_l": 334}
FACIAL = ("say", "face", "smile", "laugh", "frown", "sad", "angry", "surprised", "blink", "talk", "speak")


def _landmarker():
    """MediaPipe's face landmarker, or None where it is not installed."""
    for place in PLACES:
        model = os.path.join(place, "face_landmarker.task")
        site = os.path.join(place, "site")
        if not (os.path.exists(model) and os.path.isdir(site)):
            continue
        try:
            if site not in sys.path:
                sys.path.insert(0, site)
            if "mediapipe" not in sys.modules:
                # Only its tasks: the package's own start would load drawing tools we do not have.
                spec = importlib.util.find_spec("mediapipe")
                package = types.ModuleType("mediapipe")
                package.__path__ = list(spec.submodule_search_locations)
                sys.modules["mediapipe"] = package
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core import base_options
            options = vision.FaceLandmarkerOptions(base_options=base_options.BaseOptions(model_asset_path=model),
                                                   running_mode=vision.RunningMode.IMAGE, num_faces=1,
                                                   min_face_detection_confidence=0.2, min_face_presence_confidence=0.2)
            return vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:
            print("MAG-NOTE face landmarker not usable (%s)" % type(exc).__name__, flush=True)
            return None
    return None


def _render(part, centre, front, size):
    """The front of the head as a picture: an orthographic camera `size`
    wide, looking at `centre` against `front` (all in the figure's own
    axes). Returns the file, or None."""
    scene = bpy.context.scene
    keep = {o: o.hide_render for o in scene.objects}
    for o in scene.objects:
        o.hide_render = o is not part
    world = scene.world
    if world is None:
        scene.world = bpy.data.worlds.new("Gesicht")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs["Strength"].default_value = 1.0
    camera = bpy.data.objects.new("Gesichtskamera", bpy.data.cameras.new("Gesichtskamera"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = size
    at = part.matrix_world @ Vector(centre)
    look = -Vector(front)
    camera.location = at - look * (4.0 * size)
    camera.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    camera.data.clip_end = 20.0 * size
    was = (scene.camera, scene.render.engine, scene.render.resolution_x, scene.render.resolution_y, scene.render.filepath, scene.render.image_settings.color_mode)
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 6
    scene.cycles.use_denoising = False
    threads = int(os.environ.get("MAG_SCENE_THREADS", "0") or 0)
    if threads > 0:
        scene.render.threads_mode, scene.render.threads = "FIXED", threads
    scene.render.resolution_x = scene.render.resolution_y = 384
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    path = os.path.join(tempfile.mkdtemp(prefix="mag-face-"), "face.png")
    scene.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as exc:
        print("MAG-NOTE face render failed (%s)" % type(exc).__name__, flush=True)
        path = None
    finally:
        (scene.camera, scene.render.engine, scene.render.resolution_x, scene.render.resolution_y, scene.render.filepath,
         scene.render.image_settings.color_mode) = was
        bpy.data.objects.remove(camera)
        for o, hidden in keep.items():
            o.hide_render = hidden
    return path


class Surface:
    """Where a ray meets the figure's surface (in its own axes)."""

    def __init__(self, part):
        mesh = part.data
        self.tree = BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices], [tuple(p.vertices) for p in mesh.polygons])

    def hit(self, start, direction):
        found = self.tree.ray_cast(Vector(start), Vector(direction))
        return None if found[0] is None else np.array(found[0][:])


def find(part, bones, front):
    """The face of a figure: its points (see POINTS) in the figure's own
    axes, and how they were found. `bones`: what rig.py found, `front`: where
    it looks (x, y)."""
    head = next(b for b in bones if b["role"] == "head")
    neck, crown = np.asarray(head["head"], dtype=float), np.asarray(head["tail"], dtype=float)
    tall = float(np.linalg.norm(crown - neck))
    forward = np.array([front[0], front[1], 0.0])
    forward /= np.linalg.norm(forward) or 1.0
    left = np.cross([0.0, 0.0, 1.0], forward)
    surface = Surface(part)
    centre = neck + 0.45 * (crown - neck)
    size = 1.15 * tall
    points, how = {}, "Kopfform"
    landmarker = _landmarker()
    picture = _render(part, centre, forward, size) if landmarker is not None else None
    if picture:
        try:
            from mediapipe.python._framework_bindings import image as mp_image
            result = landmarker.detect(mp_image.Image.create_from_file(picture))
            if result.face_landmarks:
                marks = result.face_landmarks[0]
                for name, index in POINTS.items():
                    u, v = marks[index].x, marks[index].y
                    # The picture's right is the figure's left; its top is up.
                    on_plane = centre + left * ((u - 0.5) * size) + np.array([0.0, 0.0, (0.5 - v) * size])
                    spot = surface.hit(on_plane + forward * 4.0 * size, -forward)
                    if spot is not None:
                        points[name] = spot
                if len(points) >= 8:
                    how = "MediaPipe"
                else:
                    points = {}
        except Exception as exc:
            print("MAG-NOTE face landmarks failed (%s)" % type(exc).__name__, flush=True)
            points = {}
        finally:
            try:
                os.remove(picture)
                os.rmdir(os.path.dirname(picture))
            except OSError:
                pass
    if not points:
        # As a head is built: the eyes halfway up the face, the mouth a third of the way.
        face = tall
        where = {"upper_lip": (0.0, 0.27), "lower_lip": (0.0, 0.22), "corner_r": (-0.12, 0.245), "corner_l": (0.12, 0.245), "chin": (0.0, 0.08),
                 "lid_r": (-0.17, 0.53), "under_r": (-0.17, 0.47), "lid_l": (0.17, 0.53), "under_l": (0.17, 0.47), "brow_r": (-0.18, 0.64), "brow_l": (0.18, 0.64)}
        for name, (aside, up) in where.items():
            start = neck + left * (aside * face) + np.array([0.0, 0.0, up * face]) + forward * 4.0 * size
            spot = surface.hit(start, -forward)
            if spot is not None:
                points[name] = spot
    return points, how


def rig(points, bones, front):
    """Bones for a face under the head bone: the jaw (from behind the mouth to
    the chin), the corners of the mouth, the upper lids and the brows. Adds
    them to `bones`; returns their names by what they move."""
    head = next(i for i, b in enumerate(bones) if b["role"] == "head")
    forward = np.array([front[0], front[1], 0.0])
    forward /= np.linalg.norm(forward) or 1.0
    named = {}
    need = ("upper_lip", "lower_lip", "chin")
    if all(k in points for k in need):
        mouth = (points["upper_lip"] + points["lower_lip"]) / 2
        width = float(np.linalg.norm(points["corner_l"] - points["corner_r"])) if "corner_l" in points and "corner_r" in points else 0.6 * float(np.linalg.norm(mouth - points["chin"])) + 1e-4
        hinge = mouth - forward * (1.4 * width)                  # the jaw turns in front of the ears
        bones.append({"name": "Kiefer", "head": hinge.tolist(), "tail": points["chin"].tolist(), "parent": head, "role": "jaw"})
        named["jaw"] = "Kiefer"
    small = 0.03 * float(np.linalg.norm(np.asarray(bones[head]["tail"]) - np.asarray(bones[head]["head"])))
    for key, name, role in (("corner_l", "Mundwinkel links", "mouth"), ("corner_r", "Mundwinkel rechts", "mouth"),
                            ("lid_l", "Lid links", "lid"), ("lid_r", "Lid rechts", "lid"), ("brow_l", "Braue links", "brow"), ("brow_r", "Braue rechts", "brow")):
        if key in points:
            bones.append({"name": name, "head": points[key].tolist(), "tail": (points[key] + forward * max(small, 1e-3)).tolist(), "parent": head, "role": role})
            named[key] = name
    return named


def weigh(part, points, named, front):
    """The skin around each face bone follows it, softly: the lower face and
    the chin the jaw, a little round each corner of the mouth, lid and brow
    those. What they take is taken from the head."""
    if not named:
        return
    forward = np.array([front[0], front[1], 0.0])
    forward /= np.linalg.norm(forward) or 1.0
    mesh = part.data
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    groups = {g.name: g.index for g in part.vertex_groups}
    pulls = {}
    width = float(np.linalg.norm(points["corner_l"] - points["corner_r"])) if "corner_l" in points and "corner_r" in points else None
    if "jaw" in named:
        mouth = (points["upper_lip"] + points["lower_lip"]) / 2
        chin = points["chin"]
        width = width or 0.6 * float(np.linalg.norm(mouth - chin))
        drop = max(1e-4, float(mouth[2] - chin[2]))
        below = np.clip((mouth[2] - co[:, 2]) / (0.35 * drop), 0.0, 1.0) * np.clip((co[:, 2] - (chin[2] - 0.6 * drop)) / (0.4 * drop), 0.0, 1.0)
        aside = np.linalg.norm((co - mouth)[:, :2] - np.outer((co - mouth)[:, :2] @ forward[:2], forward[:2]), axis=1)
        across = np.clip(1.0 - (aside - 0.9 * width) / (0.6 * width), 0.0, 1.0)
        ahead = np.clip(((co - mouth) @ forward + 1.6 * width) / (0.8 * width), 0.0, 1.0)
        pulls[named["jaw"]] = below * across * ahead
    for key in ("corner_l", "corner_r", "lid_l", "lid_r", "brow_l", "brow_r"):
        if key not in named:
            continue
        span = (0.35 * width if width else 0.02) * (1.0 if key.startswith("corner") else 0.8 if key.startswith("lid") else 1.2)
        d = np.linalg.norm(co - points[key], axis=1)
        w = np.clip(1.0 - (d / max(span, 1e-5)) ** 2, 0.0, 1.0) ** 2
        w *= ((co - points[key]) @ forward > -0.6 * span)            # the front of the face, not the back of the head
        pulls[named[key]] = w
    bm = bmesh.new()
    bm.from_mesh(mesh)
    layer = bm.verts.layers.deform.verify()
    total = np.minimum(1.0, sum(pulls.values()))
    scale = np.where(total > 0, total / np.maximum(sum(pulls.values()), 1e-9), 0.0)
    for v in bm.verts:
        t = float(total[v.index])
        if t <= 1e-3:
            continue
        held = v[layer]
        for which in list(held.keys()):
            held[which] = held[which] * (1.0 - t)
        for name, w in pulls.items():
            share = float(w[v.index] * scale[v.index])
            if share > 1e-3:
                held[groups[name]] = share
    bm.to_mesh(mesh)
    bm.free()


# ---- what a face does over time ---------------------------------------------------
# Mouth shapes for the letters of German speech: how far the jaw opens, how wide
# the mouth is drawn (negative: rounded), and how long the sound lasts (seconds).
VISEMES = {"a": (0.85, 0.15, 0.13), "e": (0.45, 0.55, 0.11), "i": (0.25, 0.75, 0.10), "o": (0.6, -0.55, 0.13), "u": (0.3, -0.85, 0.12),
           "m": (0.0, 0.0, 0.07), "f": (0.12, 0.2, 0.07), "s": (0.2, 0.35, 0.07), "c": (0.3, 0.1, 0.06), "_": (0.0, 0.0, 0.18)}
LETTERS = {"ä": "e", "ö": "o", "ü": "u", "y": "i", "b": "m", "p": "m", "m": "m", "f": "f", "v": "f", "w": "f", "s": "s", "z": "s", "ß": "s", "x": "s"}
EXPRESSIONS = {  # smile (negative: the corners go down), brows up, lids (positive: closed), jaw
    "smile": (0.9, 0.15, 0.1, 0.05), "laugh": (1.0, 0.35, 0.35, 0.45), "sad": (-0.7, 0.35, 0.25, 0.0), "frown": (-0.5, -0.6, 0.15, 0.0),
    "angry": (-0.4, -1.0, 0.2, 0.08), "surprised": (0.0, 1.0, -0.35, 0.65), "blink": (0.0, 0.0, 1.0, 0.0),
}


def visemes(text, seconds=None):
    """What is said, as mouth shapes in time: [(seconds from the start, jaw, wide)]."""
    text = str(text).lower()
    shapes = []
    i = 0
    while i < len(text):
        pair, c = text[i:i + 2], text[i]
        if pair in ("au", "ei", "ai", "eu", "äu"):
            shapes += [VISEMES["a"], VISEMES["u" if pair == "au" else "i"]]
            i += 2
            continue
        if pair in ("sch", "ch") or text[i:i + 3] == "sch":
            shapes.append(VISEMES["s"])
            i += 3 if text[i:i + 3] == "sch" else 2
            continue
        if c in "aeiou":
            shapes.append(VISEMES[c])
        elif c in LETTERS:
            shapes.append(VISEMES[LETTERS[c]])
        elif c.isalpha():
            shapes.append(VISEMES["c"])
        elif c in ".,;:!?\n":
            shapes.append(VISEMES["_"])
        elif c == " ":
            shapes.append((0.05, 0.0, 0.03))
        i += 1
    if not shapes:
        return []
    natural = sum(s[2] for s in shapes)
    rate = (seconds / natural) if seconds and natural > 0 else 1.0
    out, t = [], 0.0
    for jaw, wide, length in shapes:
        out.append((t + 0.5 * length * rate, jaw, wide))
        t += length * rate
    out.append((t + 0.1, 0.0, 0.0))
    return out


class Face:
    """The movements of one face, frame by frame: jaw (0 shut .. 1 wide open),
    wide (the corners drawn out; negative: rounded), smile (the corners up;
    negative: down), brows (up), lids (1 shut)."""

    def __init__(self, named, points, front, fps, seed=0):
        self.named, self.fps, self.seed = named, fps, seed
        forward = np.array([front[0], front[1], 0.0])
        self.forward = forward / (np.linalg.norm(forward) or 1.0)
        self.left = np.cross([0.0, 0.0, 1.0], self.forward)
        self.width = float(np.linalg.norm(points["corner_l"] - points["corner_r"])) if "corner_l" in points and "corner_r" in points else 0.0
        self.eye = {s: float(points["lid_" + s][2] - points["under_" + s][2]) if "lid_" + s in points and "under_" + s in points else 0.0 for s in "lr"}
        tall = max(self.width * 3.0, 1e-3)
        self.brow = 0.07 * tall
        self.speech, self.moods = [], []           # (start frame, visemes); (start, frames, expression)

    def say(self, text, start, seconds=None):
        """Speaks: returns how many frames it takes."""
        shapes = visemes(text, seconds)
        if not shapes:
            return 0
        self.speech.append((int(start), shapes))
        return int(math.ceil(shapes[-1][0] * self.fps))

    def show(self, expression, start, frames):
        if expression in EXPRESSIONS:
            self.moods.append((int(start), int(frames), expression))

    def channels(self, last):
        n = last + 1
        jaw, wide, smile, brows, lids = (np.zeros(n) for _ in range(5))
        for start, shapes in self.speech:
            times = np.array([start / self.fps + t for t, _, _ in shapes])
            now = np.arange(n) / self.fps
            inside = (now >= times[0] - 0.08) & (now <= times[-1])
            if inside.any():
                jaw[inside] = np.interp(now[inside], times, [j for _, j, _ in shapes])
                wide[inside] = np.interp(now[inside], times, [w for _, _, w in shapes])
        for start, frames, kind in self.moods:
            s, b, l, j = EXPRESSIONS[kind]
            for f in range(max(0, start), min(n, start + frames)):
                u = (f - start) / max(1, frames)
                on = min(1.0, 4.0 * min(u, 1.0 - u)) if kind != "blink" else math.sin(math.pi * u)
                smile[f] += s * on
                brows[f] += b * on
                lids[f] = max(lids[f], l * on) if l > 0 else lids[f] + l * on
                jaw[f] = max(jaw[f], j * on * (0.6 + 0.4 * math.sin(2 * math.pi * 4.0 * f / self.fps)) if kind == "laugh" else j * on)
        # It blinks now and then, every three to five seconds, quickly.
        rng = np.random.default_rng(1000 + self.seed)
        t = rng.uniform(0.5, 2.5)
        while t * self.fps < n:
            a = int(t * self.fps)
            for f in range(a, min(n, a + max(3, int(0.16 * self.fps)))):
                lids[f] = max(lids[f], math.sin(math.pi * (f - a) / max(3, int(0.16 * self.fps))))
            t += rng.uniform(2.8, 5.0)
        # Lips do not jump from sound to sound.
        k = np.array([0.25, 0.5, 0.25])
        jaw, wide = (np.convolve(np.pad(x, 1, mode="edge"), k, mode="valid") for x in (jaw, wide))
        return jaw, wide, np.clip(smile, -1, 1), np.clip(brows, -1, 1), np.clip(lids, -0.5, 1)

    def frames(self, last):
        """For every frame: the turns ({bone: Quaternion}) and moves ({bone: Vector}) of the face, in the figure's own axes."""
        jaw, wide, smile, brows, lids = self.channels(last)
        up = np.array([0.0, 0.0, 1.0])
        across = self.left
        out = []
        for f in range(last + 1):
            turns, moves = {}, {}
            if "jaw" in self.named:
                turns[self.named["jaw"]] = Quaternion(Vector(across), math.radians(20.0) * float(jaw[f]))
            for side, sign in (("l", 1.0), ("r", -1.0)):
                key = "corner_" + side
                if key in self.named and self.width > 0:
                    out_by = (0.12 * float(wide[f]) + 0.08 * max(0.0, float(smile[f]))) * self.width
                    up_by = 0.16 * float(smile[f]) * self.width - 0.1 * float(jaw[f]) * self.width
                    moves[self.named[key]] = Vector(across * sign * out_by + up * up_by - self.forward * 0.04 * self.width * max(0.0, -float(wide[f])))
                key = "lid_" + side
                if key in self.named and self.eye[side] > 0:
                    moves[self.named[key]] = Vector(-up * 0.9 * self.eye[side] * float(lids[f]))
                key = "brow_" + side
                if key in self.named:
                    moves[self.named[key]] = Vector(up * self.brow * float(brows[f]))
            out.append((turns, moves))
        return out


def wanted(spec):
    """The actors whose faces a scene uses."""
    return {str(a.get("actor") or "").strip().lower() for a in spec.get("actions") or [] if isinstance(a, dict) and str(a.get("do", "")).lower() in FACIAL}


def words(text):
    """How many words: a nod on every few."""
    return len(re.findall(r"\w+", str(text)))

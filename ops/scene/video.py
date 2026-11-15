"""MAG scenes: what the people in a video do, as motions of a scene.

A video of the chat becomes a scene: its first frame is a photo whose
objects become models standing where they stood ("Ganze Szene"), and here
everybody on two legs in it is followed through the whole video and turned
into a motion of its own, which the figure made from them plays in the scene.

    track(video, out)    MediaPipe's pose landmarker (Apache-2.0, on the CPU)
                         on every frame: for each person, where its 33 body
                         points are in the picture and, in metres around its
                         hips, in space. Written to `out` (npz).
    people(track)        who is in it, where each stood in the first frame.
    clip_of(...)         one person as a motion.Clip: seen from the body itself
                         (as motion.py stores the recorded motions), where
                         thighs, shins and arms point, how hips, chest and head
                         are turned, how high the hips are, which foot stands,
                         and the way it went, from how its size and place in
                         the picture change (a pinhole camera with the focal
                         length objects.py gives the first frame).

numpy and the standard library; MediaPipe only for track() (inside Blender's
Python, from the same place as the face landmarker, face.py).
"""
import importlib.util
import json
import math
import os
import subprocess
import sys
import types

import numpy as np

import motion

FILM = 26.0 / 36.0          # focal length over the longer side: a phone's main camera, as objects.py takes it
MOST = 4                    # people followed at most
SIDE = 960                  # frames are looked at no larger than this
# MediaPipe's body points
NOSE, EAR_L, EAR_R = 0, 7, 8
SHOULDER_L, SHOULDER_R, ELBOW_L, ELBOW_R, WRIST_L, WRIST_R = 11, 12, 13, 14, 15, 16
HIP_L, HIP_R, KNEE_L, KNEE_R, ANKLE_L, ANKLE_R, HEEL_L, HEEL_R, TOE_L, TOE_R = 23, 24, 25, 26, 27, 28, 29, 30, 31, 32
CHAINS = ((HIP_L, KNEE_L), (KNEE_L, ANKLE_L), (HIP_R, KNEE_R), (KNEE_R, ANKLE_R),
          (SHOULDER_L, ELBOW_L), (ELBOW_L, WRIST_L), (SHOULDER_R, ELBOW_R), (ELBOW_R, WRIST_R))   # as motion.LIMBS
BONES = CHAINS + ((SHOULDER_L, HIP_L), (SHOULDER_R, HIP_R), (SHOULDER_L, SHOULDER_R), (HIP_L, HIP_R))   # for how far away somebody is
PLACES = [os.environ.get("MAG_SCENE_FACE", "/opt/mag-scene/face"), os.path.expanduser("~/.cache/mag-stack/face")]


# ---- following the people of a video ------------------------------------------
def _landmarker():
    """MediaPipe's pose landmarker for a video, or None where it is not installed."""
    for place in PLACES:
        model, site = os.path.join(place, "pose_landmarker_heavy.task"), os.path.join(place, "site")
        if not (os.path.exists(model) and os.path.isdir(site)):
            continue
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
        options = vision.PoseLandmarkerOptions(base_options=base_options.BaseOptions(model_asset_path=model),
                                               running_mode=vision.RunningMode.VIDEO, num_poses=MOST,
                                               min_pose_detection_confidence=0.4, min_pose_presence_confidence=0.4,
                                               min_tracking_confidence=0.4)
        return vision.PoseLandmarker.create_from_options(options)
    return None


def frames_of(video):
    """The video's frames (RGB, its longer side at most SIDE), its rate and size."""
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                "stream=width,height,r_frame_rate", "-of", "json", video]))["streams"][0]
    num, _, den = probe["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    w, h = int(probe["width"]), int(probe["height"])
    k = min(1.0, SIDE / max(w, h))
    sw, sh = max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", video, "-vf", "scale=%d:%d" % (sw, sh), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, sh, sw, 3), fps, w, h


def track(video, out):
    """Follows everybody of a video and writes what was seen to `out` (npz).
    Returns how many people were followed (0: nobody, or no landmarker)."""
    landmarker = _landmarker()
    frames, fps, w, h = frames_of(video)
    step = max(1, int(round(fps / 15.0)))            # 15 looks a second are enough to follow a body
    frames, fps = frames[::step], fps / step
    n = len(frames)
    seen = []                                         # per person: picture points (n x 33 x 3: x, y as parts of the frame, visibility), world (n x 33 x 3)
    if landmarker is not None:
        from mediapipe.python._framework_bindings import image as mp_image, image_frame
        for i, frame in enumerate(frames):
            result = landmarker.detect_for_video(mp_image.Image(image_format=image_frame.ImageFormat.SRGB, data=np.ascontiguousarray(frame)),
                                                 int(round(i * 1000.0 / fps)))
            taken = set()
            for marks, world in zip(result.pose_landmarks, result.pose_world_landmarks):
                pic = np.array([[m.x, m.y, m.visibility] for m in marks], dtype=np.float32)
                hip = pic[[HIP_L, HIP_R], :2].mean(axis=0)
                # The same person as in the frames before: the one whose hips were nearest.
                best, near = None, 0.15
                for k, person in enumerate(seen):
                    if k in taken:
                        continue
                    last = person["last"]
                    if last is not None and np.linalg.norm(last - hip) < near:
                        best, near = k, float(np.linalg.norm(last - hip))
                if best is None:
                    if len(seen) >= MOST:
                        continue
                    seen.append({"pic": np.full((n, 33, 3), np.nan, np.float32), "world": np.full((n, 33, 3), np.nan, np.float32), "last": None})
                    best = len(seen) - 1
                taken.add(best)
                seen[best]["pic"][i] = pic
                seen[best]["world"][i] = [[m.x, m.y, m.z] for m in world]
                seen[best]["last"] = hip
    people = [p for p in seen if np.isfinite(p["pic"][:, 0, 0]).sum() >= max(8, 0.5 * n)]   # seen for most of the video
    np.savez_compressed(out, fps=fps, width=w, height=h, frames=n,
                        pic=np.stack([p["pic"] for p in people]) if people else np.zeros((0, n, 33, 3), np.float32),
                        world=np.stack([p["world"] for p in people]) if people else np.zeros((0, n, 33, 3), np.float32))
    return len(people)


def load(path):
    z = np.load(path)
    return {"fps": float(z["fps"]), "width": int(z["width"]), "height": int(z["height"]), "frames": int(z["frames"]),
            "pic": z["pic"].astype(np.float64), "world": z["world"].astype(np.float64)}


def _filled(a):
    """Frames a person was not seen in, filled in from those around them (along axis 0)."""
    a = a.copy()
    flat = a.reshape(len(a), -1)
    ok = np.isfinite(flat[:, 0])
    if ok.sum() == 0:
        return a
    idx = np.arange(len(a))
    for c in range(flat.shape[1]):
        flat[:, c] = np.interp(idx, idx[ok], flat[ok, c])
    return flat.reshape(a.shape)


def _resampled(a, fps, n):
    """`a` (at `fps`) at motion.FPS, n frames of it."""
    t_in = np.arange(len(a)) / fps
    t = np.arange(n) / motion.FPS
    flat = a.reshape(len(a), -1)
    out = np.stack([np.interp(t, t_in, flat[:, c]) for c in range(flat.shape[1])], axis=1)
    return out.reshape((n,) + a.shape[1:])


def calm(v, width):
    """Smoothed along axis 0 (a Hann window `width` frames wide)."""
    if width < 2 or len(v) < 3:
        return v
    k = np.hanning(width + 2)[1:-1]
    k /= k.sum()
    pad = width // 2
    flat = v.reshape(len(v), -1)
    padded = np.concatenate([np.repeat(flat[:1], pad, axis=0), flat, np.repeat(flat[-1:], width - 1 - pad, axis=0)])
    return np.stack([np.convolve(padded[:, i], k, mode="valid") for i in range(flat.shape[1])], axis=1).reshape(v.shape)


def people(seen):
    """Everybody of a track: where its hips were in the first frame it was
    seen, across the frame from its middle (parts of the longer side,
    right positive) and down, and that focal length."""
    w, h = seen["width"], seen["height"]
    side = max(w, h)
    found = []
    for k in range(len(seen["pic"])):
        pic = _filled(seen["pic"][k])
        hip = pic[0, [HIP_L, HIP_R], :2].mean(axis=0)
        found.append({"index": k, "across": float((hip[0] - 0.5) * w / side), "down": float((hip[1] - 0.5) * h / side), "focal": FILM})
    return found


def _frame(fore, left):
    """The turn of a body part whose front and left are these (n x 3 each, in the scene): x ahead, y left, z up."""
    left = motion.unit(left)
    fore = motion.unit(fore - (fore * left).sum(axis=-1, keepdims=True) * left)
    up = np.cross(fore, left)
    m = np.stack([fore, left, up], axis=-1)            # its own axes, as columns
    q = np.stack([motion.qmatrix(x) for x in m])
    for i in range(1, len(q)):                        # the same turn, written the near way
        if (q[i] * q[i - 1]).sum() < 0:
            q[i] = -q[i]
    return q


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def _standing(ankle, toe, leg):
    """Which foot stands in each frame, as motion.py wants it: 0 in the air,
    1 bears weight, 2 stands (ankles and toes in the scene, at motion.FPS)."""
    n = len(ankle[0])
    contact = np.zeros((n, 2), dtype=np.uint8)
    rate = motion.FPS
    floor_a = float(np.percentile(np.concatenate([ankle[0][:, 2], ankle[1][:, 2]]), 5))
    floor_t = float(np.percentile(np.concatenate([toe[0][:, 2], toe[1][:, 2]]), 5))

    def speed(p):
        v = np.zeros(len(p))
        if len(p) > 1:
            v[1:] = np.linalg.norm(np.diff(p, axis=0), axis=1) * rate
            v[0] = v[1]
        return v

    def tidy(on, least):
        for x, y in _runs(~on):                       # a gap of a moment is no step
            if y - x <= 0.1 * rate and x > 0 and y < n:
                on[x:y] = True
        for x, y in _runs(on):
            if y - x < least * rate:
                on[x:y] = False
        return on

    for k in range(2):
        a, t = ankle[k], toe[k]
        ha, ht = (a[:, 2] - floor_a) / leg, (t[:, 2] - floor_t) / leg
        low = np.minimum(ha, ht)
        v = np.where(ha < ht, speed(a[:, :2]), speed(t[:, :2])) / leg
        # A video is not a motion capture: looser than the recordings (mocap/build.py).
        bears = tidy((v < 1.2) & (np.abs(np.gradient(low)) * rate < 1.6) & (low < 0.16), 0.05)
        stands = tidy(bears & (speed(a[:, :2]) / leg < 0.7) & (np.abs(np.gradient(ha)) * rate < 0.7) & (ha < 0.2), 0.08) & bears
        contact[:, k] = bears.astype(np.uint8) + stands.astype(np.uint8)
    return contact


def clip_of(seen, index, depth, name="video"):
    """One person of a track as a motion.Clip that starts at the origin looking
    along x, and where it looked in the first frame, in the scene (radians;
    the scene's x is away from the camera, its y the camera's left).
    `depth`: how far from the camera it stood then, in metres (the depth
    estimate of the first frame, as the scene placed its figure)."""
    fps, w, h = seen["fps"], seen["width"], seen["height"]
    side = max(w, h)
    pic, world = _filled(seen["pic"][index]), _filled(seen["world"][index])
    n = max(2, int(round((len(pic) - 1) / fps * motion.FPS)) + 1)
    pic, world = _resampled(pic, fps, n), _resampled(world, fps, n)
    # MediaPipe's space: x to the right of the picture, y down, z away from the camera; the scene: x away, y left, z up.
    body = np.stack([world[..., 2], -world[..., 0], -world[..., 1]], axis=-1)
    body = calm(body, 5)
    P = lambda j: body[:, j]                           # noqa: E731
    leg = float(np.median(np.linalg.norm(P(HIP_L) - P(KNEE_L), axis=1) + np.linalg.norm(P(KNEE_L) - P(ANKLE_L), axis=1)
                          + np.linalg.norm(P(HIP_R) - P(KNEE_R), axis=1) + np.linalg.norm(P(KNEE_R) - P(ANKLE_R), axis=1)) / 2)
    leg = max(leg, 0.2)
    # How far away it is: its bones as long in the picture as they are across the camera's view.
    flat = np.stack([pic[..., 0] * w / side, pic[..., 1] * h / side], axis=-1)
    seen_len = sum(np.linalg.norm(flat[:, a] - flat[:, b], axis=1) for a, b in BONES)
    true_len = sum(np.linalg.norm(world[:, a, :2] - world[:, b, :2], axis=1) for a, b in BONES)
    far = FILM * true_len / np.maximum(seen_len, 1e-6)
    far = calm(far[:, None], 15)[:, 0]
    far = depth * far / max(float(np.median(far[:5])), 1e-6)
    # Where its hips are in the scene, from the camera at the origin.
    hip_pic = (flat[:, HIP_L] + flat[:, HIP_R]) / 2 - np.array([0.5 * w / side, 0.5 * h / side])
    place = np.stack([far, -hip_pic[:, 0] * far / FILM, -hip_pic[:, 1] * far / FILM], axis=1)
    place[:, :2] = calm(place[:, :2], 9)
    # Where it looks: across the line between the hips (and the shoulders), calmed.
    hips = P(HIP_L) - P(HIP_R)
    chest = P(SHOULDER_L) - P(SHOULDER_R)
    ahead = np.stack([hips[:, 1], -hips[:, 0]], axis=1) / np.maximum(np.linalg.norm(hips[:, :2], axis=1, keepdims=True), 1e-6)
    ahead += 0.5 * np.stack([chest[:, 1], -chest[:, 0]], axis=1) / np.maximum(np.linalg.norm(chest[:, :2], axis=1, keepdims=True), 1e-6)
    ahead = calm(ahead, 9)
    heading = np.unwrap(np.arctan2(ahead[:, 1], ahead[:, 0]))
    back = motion.qz(-heading)
    # Hips, chest and head, turned from standing straight and looking ahead, seen from the body itself.
    mid_hip, mid_sh = (P(HIP_L) + P(HIP_R)) / 2, (P(SHOULDER_L) + P(SHOULDER_R)) / 2
    spine = motion.unit(mid_sh - mid_hip)
    fore_of = lambda left: np.cross(left, spine)       # noqa: E731  (ahead of a body whose left and up are these)
    turns = {"hips": _frame(fore_of(hips), hips), "chest": _frame(fore_of(chest), chest)}
    ears = P(EAR_L) - P(EAR_R)
    nose = P(NOSE) - (P(EAR_L) + P(EAR_R)) / 2
    turns["head"] = _frame(nose, ears)
    for key in turns:
        q = motion.qmul(back, turns[key])
        for i in range(1, n):
            if (q[i] * q[i - 1]).sum() < 0:
                q[i] = -q[i]
        turns[key] = q
    limbs = np.stack([motion.unit(P(b) - P(a)) for a, b in CHAINS], axis=1)
    limbs = motion.qrot(back[:, None, :], limbs)
    # How high the hips are (over the lower ankle, in leg lengths), which foot stands, and a leap's lift.
    over = -np.minimum(P(ANKLE_L)[:, 2], P(ANKLE_R)[:, 2]) + mid_hip[:, 2]
    height = calm(over[:, None], 5)[:, 0] / leg
    at = lambda j: body[:, j] - mid_hip + place        # noqa: E731  (a point in the scene)
    contact = _standing((at(ANKLE_L), at(ANKLE_R)), (at(TOE_L), at(TOE_R)), leg)
    lift = np.zeros(n)
    for a, z in _runs((contact[:, 0] == 0) & (contact[:, 1] == 0)):
        if a > 0 and z < n and z - a <= motion.FPS:
            line = np.interp(np.arange(a, z), [a - 1, z], [place[a - 1, 2], place[z, 2]])
            lift[a:z] = np.clip((place[a:z, 2] - line) / leg, 0.0, 2.0)
    # The way, from where it began and as it looked then.
    h0 = float(heading[0])
    c, s = math.cos(-h0), math.sin(-h0)
    xy = (place[:, :2] - place[0, :2]) / leg
    root = np.stack([c * xy[:, 0] - s * xy[:, 1], s * xy[:, 0] + c * xy[:, 1], height, heading - h0], axis=1)
    arrays = {"root": root, "hips": turns["hips"], "chest": turns["chest"], "head": turns["head"], "limbs": limbs,
              "contact": contact, "lift": lift}
    return motion.Clip(name, arrays), h0, leg


def match(found, actors):
    """Which figure of the scene each person is: `actors` as (name, across)
    for the figures on two legs, where each stood in the first frame (as
    `across` of people()). Nearest first, each once; {person index: name}."""
    pairs = sorted((abs(p["across"] - a), p["index"], name) for p in found for name, a in actors)
    taken, names, out = set(), set(), {}
    for d, k, name in pairs:
        if d < 0.12 and k not in taken and name not in names:
            out[k] = name
            taken.add(k)
            names.add(name)
    return out

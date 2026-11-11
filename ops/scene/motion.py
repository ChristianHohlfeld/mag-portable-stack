"""MAG scenes: how a figure on two legs moves.

The motions are recorded ones (motions.npz, made by mocap/build.py from the
CMU Graphics Lab Motion Capture Database, see MOTIONS.md): for every frame
where the body is, how hips, chest and head are turned and where thighs,
shins and arms point, seen from the figure itself, and which foot stands.
A figure of any build takes them over by directions: its own bones point
where the recorded ones did, the way it covers follows the length of its own
legs, and a foot that stands stays where it stands (it is held there and the
leg is solved for it), also while one motion blends into the next and while
the figure is steered along a way.

Only numpy and the standard library: the same code runs inside Blender and
in the tests. Everything here happens in the figure's own frame: x is where
it looks, y its left, z up, lengths in leg lengths until the last step.
"""
import json
import re
import math
import os

import numpy as np

FPS = 30                      # the rate the motions are stored at
LIMBS = ("thigh_l", "shin_l", "thigh_r", "shin_r", "arm_l", "forearm_l", "arm_r", "forearm_r")
SWAP = (2, 3, 0, 1, 6, 7, 4, 5)   # the same limbs, left and right exchanged
UP = np.array([0.0, 0.0, 1.0])


# ---- turns ---------------------------------------------------------------------
# A turn is a quaternion (w, x, y, z); all of these take arrays of them too.
def qmul(a, b):
    if a.ndim == 1 and b.ndim == 1:                      # one turn after another, the quick way
        aw, ax, ay, az = a
        bw, bx, by, bz = b
        return np.array([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                         aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw])
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def qinv(q):
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def cross(a, b):
    if a.ndim == 1 and b.ndim == 1:
        return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])
    return np.stack([a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1], a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
                     a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]], axis=-1)


def qrot(q, v):
    """A vector, turned."""
    w, u = q[..., :1], q[..., 1:]
    t = 2.0 * cross(u, v)
    return v + w * t + cross(u, t)


def qaxis(axis, angle):
    axis = np.asarray(axis, dtype=float)
    if axis.ndim == 1 and np.ndim(angle) == 0:
        s = math.sin(0.5 * angle)
        return np.array([math.cos(0.5 * angle), axis[0] * s, axis[1] * s, axis[2] * s])
    half = 0.5 * np.asarray(angle, dtype=float)
    return np.concatenate([np.cos(half)[..., None], axis * np.sin(half)[..., None]], axis=-1)


def qz(angle):
    """A turn about the vertical."""
    if np.ndim(angle) == 0:
        return np.array([math.cos(0.5 * angle), 0.0, 0.0, math.sin(0.5 * angle)])
    half = 0.5 * np.asarray(angle, dtype=float)
    zero = np.zeros_like(half)
    return np.stack([np.cos(half), zero, zero, np.sin(half)], axis=-1)


def unit(v):
    if v.ndim == 1:
        n = math.sqrt(float(v @ v))
        return v / n if n >= 1e-12 else v
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n < 1e-12, 1.0, n)


def qarc(a, b):
    """The shortest turn that takes direction a to direction b."""
    a, b = unit(np.asarray(a, dtype=float)), unit(np.asarray(b, dtype=float))
    if a.ndim == 1 and b.ndim == 1:
        w = 1.0 + float(a @ b)
        if w < 1e-9:                                     # opposite: any half turn across a
            side = cross(a, np.array([1.0, 0.0, 0.0]))
            if float(side @ side) < 1e-12:
                side = cross(a, np.array([0.0, 1.0, 0.0]))
            side = unit(side)
            return np.array([0.0, side[0], side[1], side[2]])
        c = cross(a, b)
        n = math.sqrt(w * w + float(c @ c))
        return np.array([w / n, c[0] / n, c[1] / n, c[2] / n])
    w = 1.0 + (a * b).sum(axis=-1, keepdims=True)
    q = np.concatenate([w * np.ones(np.broadcast(a, b).shape[:-1] + (1,)), cross(a, b) * np.ones(np.broadcast(a, b).shape)], axis=-1)
    flat = q[..., 0] < 1e-9
    if flat.any():
        wide = np.broadcast_to(a, q.shape[:-1] + (3,))[flat]
        side = cross(wide, np.array([1.0, 0.0, 0.0]))
        thin = np.linalg.norm(side, axis=-1) < 1e-6
        side[thin] = cross(wide[thin], np.array([0.0, 1.0, 0.0]))
        q[flat] = np.concatenate([np.zeros((int(flat.sum()), 1)), unit(side)], axis=-1)
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def qangle(q):
    """A turn as axis and angle (0..pi)."""
    q = np.where(q[..., :1] < 0, -q, q)
    s = np.linalg.norm(q[..., 1:], axis=-1)
    angle = 2.0 * np.arctan2(s, q[..., 0])
    axis = q[..., 1:] / np.where(s < 1e-12, 1.0, s)[..., None]
    return axis, angle


def qpart(q, part):
    """A part of a turn: 0 none of it, 1 all."""
    axis, angle = qangle(q)
    return qaxis(axis, angle * part)


def qmix(a, b, t):
    """Between two turns that are close: straight through, made a turn again."""
    b = np.where((a * b).sum(axis=-1, keepdims=True) < 0, -b, b)
    q = a + (b - a) * t
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def qmatrix(m):
    """The turn of a 3x3 rotation matrix."""
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    return q / np.linalg.norm(q)


class Swings:
    """Limbs turned to where they point, frame after frame, without spinning
    about themselves. The least turn that takes a bone to a direction is
    anybody's guess when that is nearly the opposite of where it came from
    (an arm straight up, a thigh folded up): there the bone keeps the turn
    it had and only follows; everywhere else it settles on the least turn."""

    def __init__(self):
        self.before = {}

    def turn(self, key, carried, direction):
        """The turn that takes a bone from where its parent carries it (`carried`) to where it is to point."""
        least = qarc(carried, direction)
        was = self.before.get(key)
        turn = least
        if was is not None:
            followed = qmul(qarc(was[1], direction), qmul(was[2], qarc(carried, was[0])))
            more = qmul(followed, qinv(least))               # what it is turned about itself beyond the least turn
            about = 2.0 * math.atan2(float(more[1:] @ direction), float(more[0]))
            about = (about + math.pi) % (2 * math.pi) - math.pi
            apart = math.acos(max(-1.0, min(1.0, float(carried @ direction))))
            turn = qmul(qaxis(direction, about * (0.75 + 0.25 * fade((apart - 2.4) / 0.5))), least)
        self.before[key] = (carried, direction, turn)
        return turn


def turn2(angle, v):
    """A point on the ground, turned about the vertical."""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


# ---- the recorded motions ------------------------------------------------------
class Pose:
    """A figure at one moment, seen from itself."""
    __slots__ = ("hips", "chest", "head", "limbs", "height", "lift", "contact")

    def __init__(self, hips, chest, head, limbs, height, lift, contact):
        self.hips, self.chest, self.head, self.limbs = hips, chest, head, limbs
        self.height, self.lift, self.contact = height, lift, contact

    def copy(self):
        return Pose(self.hips.copy(), self.chest.copy(), self.head.copy(), self.limbs.copy(), self.height, self.lift, self.contact.copy())

    def mirrored(self):
        flip = np.array([1.0, -1.0, 1.0, -1.0])
        return Pose(self.hips * flip, self.chest * flip, self.head * flip, self.limbs[list(SWAP)] * np.array([1.0, -1.0, 1.0]),
                    self.height, self.lift, self.contact[::-1].copy())


class Clip:
    """One recorded motion. `loop`: it is a round that begins again where it
    ends (a stride), and `stride` is how far a round goes. `hand`: the arm
    it is made with ("l", "r" or ""). `air`: from when to when it is off the
    ground in its great leap (seconds), and how high that takes the hips.
    For every frame `contact` says of each foot: 0 it is in the air, 1 it
    bears weight (its toes are down, the heel is on its way), 2 it stands."""

    def __init__(self, name, arrays, loop=False, stride=None, hand="", air=None):
        self.name, self.loop, self.hand, self.air = name, loop, hand, air
        self.root = arrays["root"]                    # x, y, height of the hips (leg lengths), heading
        self.hips, self.chest, self.head = arrays["hips"], arrays["chest"], arrays["head"]
        self.limbs = arrays["limbs"]                  # frames x 8 directions
        self.contact, self.lift = arrays["contact"], arrays["lift"]
        self.frames = len(self.root)
        self.seconds = (self.frames if loop else self.frames - 1) / FPS
        self.stride = np.zeros(3) if stride is None else np.asarray(stride, dtype=float)   # x, y, turn of one round
        self.pace = float(np.hypot(self.stride[0], self.stride[1])) / self.seconds if loop else 0.0   # leg lengths a second

    def _at(self, t):
        """The two stored frames around a moment and how far between them."""
        f = max(0.0, t) * FPS
        if self.loop:
            f %= self.frames
            a = int(f)
            return a, (a + 1) % self.frames, f - a
        a = min(self.frames - 1, int(f))
        return a, min(self.frames - 1, a + 1), min(1.0, f - a)

    def pose(self, t, mirror=False):
        a, b, u = self._at(t)
        pose = Pose(qmix(self.hips[a], self.hips[b], u), qmix(self.chest[a], self.chest[b], u), qmix(self.head[a], self.head[b], u),
                    unit(self.limbs[a] + (self.limbs[b] - self.limbs[a]) * u), float(self.root[a, 2] + (self.root[b, 2] - self.root[a, 2]) * u),
                    float(self.lift[a] + (self.lift[b] - self.lift[a]) * u), (self.contact[a] if u < 0.5 else self.contact[b]).copy())
        return pose.mirrored() if mirror else pose

    def place(self, t):
        """Where the motion has got to at a moment: x, y, heading (as recorded, from its start)."""
        f = max(0.0, t) * FPS
        if self.loop:
            rounds, f = divmod(f, self.frames)
            a = int(f)
            here = self.root[a, [0, 1, 3]]
            there = self.root[a + 1, [0, 1, 3]] if a + 1 < self.frames else self.root[0, [0, 1, 3]] + self.stride
            at = here + (there - here) * (f - a)
            return np.array([at[0] + rounds * self.stride[0], at[1] + rounds * self.stride[1], at[2]])
        a = min(self.frames - 1, int(f))
        b = min(self.frames - 1, a + 1)
        return self.root[a, [0, 1, 3]] + (self.root[b, [0, 1, 3]] - self.root[a, [0, 1, 3]]) * min(1.0, f - a)

    def step(self, t0, t1, mirror=False):
        """How the body moves between two moments, seen from itself at the
        first: forwards, to its left, and how far it turns."""
        a, b = self.place(t0), self.place(t1)
        way = turn2(-a[2], b[:2] - a[:2])
        out = np.array([way[0], way[1], b[2] - a[2]])
        return out * np.array([1.0, -1.0, -1.0]) if mirror else out


# German words for motions, as Chris and the bots say them, in the words of the recordings' descriptions.
GERMAN = {"tanzt": "dance", "tanzen": "dance", "tanz": "dance", "boxen": "boxing", "boxt": "boxing", "treten": "kick", "tritt": "kick", "kickt": "kick",
          "schlagen": "punch", "schlag": "punch", "rennen": "run", "rennt": "run", "sprint": "run", "sprintet": "run", "laufen": "walk", "geht": "walk",
          "gehen": "walk", "springen": "jump", "springt": "jump", "sitzen": "sits", "setzt": "sits", "liegen": "lay", "hinlegen": "lay", "aufstehen": "get",
          "kriechen": "crawling", "kriecht": "crawling", "werfen": "throwing", "wirft": "throwing", "fangen": "catch", "winken": "wave", "winkt": "wave",
          "betrunken": "drunk", "traurig": "sad", "fröhlich": "happy", "lachen": "laughing", "lacht": "laughing", "weinen": "crying", "weint": "crying",
          "angst": "scared", "verbeugen": "bow", "verbeugt": "bow", "klettern": "climb", "klettert": "climb", "balancieren": "balance", "schwimmen": "swim",
          "fechten": "swordplay", "schwert": "swordplay", "fußball": "soccer", "basketball": "basketball", "golf": "golf", "yoga": "yoga",
          "strecken": "stretch", "dehnen": "stretch", "marschieren": "marching", "marschiert": "marching", "hinken": "limp", "humpeln": "limp",
          "stolpern": "stumble", "stolpert": "stumble", "fallen": "fall", "fällt": "fall", "rollen": "roll", "rollt": "roll", "aufheben": "pick",
          "zeigen": "point", "zeigt": "point", "klatschen": "clap", "klatscht": "clap", "jubeln": "cheer", "jubelt": "cheer", "drehen": "turn", "dreht": "turn",
          "schleichen": "sneak", "hüpfen": "hop", "hüpft": "hop", "affe": "monkey", "gorilla": "gorilla", "huhn": "chicken", "zombie": "zombie"}


class Library:
    """All recorded motions (motions.npz)."""

    def __init__(self, folder=None):
        folder = folder or os.path.dirname(os.path.abspath(__file__))
        data = np.load(os.path.join(folder, "motions.npz"))
        index = json.loads(bytes(data["index"]).decode("utf-8"))
        scale = 1.0 / 32767.0
        self.clips, self.labels = {}, {}
        for entry in index["clips"]:
            a, z = entry["start"], entry["start"] + entry["frames"]
            arrays = {"root": data["root"][a:z].astype(np.float64), "contact": data["contact"][a:z].astype(np.int8), "lift": data["lift"][a:z].astype(np.float64) / 1000.0}
            for key in ("hips", "chest", "head", "limbs"):
                arrays[key] = unit(data[key][a:z].astype(np.float64) * scale)
            self.clips[entry["name"]] = Clip(entry["name"], arrays, bool(entry.get("loop")), entry.get("stride"), entry.get("hand", ""), entry.get("air"))
            self.labels[entry["name"]] = entry.get("label", entry["name"])
        self.neutral = np.asarray(index["neutral"], dtype=float)      # where the limbs point when somebody just stands

    def __getitem__(self, name):
        return self.clips[name]

    def find(self, words):
        """The recorded motion whose description fits these words best (English
        or German, a few words: "boxing", "tanzt Salsa", "drunk walk"), or None."""
        asked = [w for w in re.findall(r"[a-zäöüß]+", str(words).lower()) if len(w) > 2]
        asked = [GERMAN.get(w, w) for w in asked]
        best, score = None, 0.0
        for name, label in self.labels.items():
            said = set(re.findall(r"[a-z]+", (label + " " + name).lower()))
            hit = sum(1.0 if w in said else 0.6 if any(s.startswith(w[:5]) or w.startswith(s[:5]) for s in said if len(s) > 4) else 0.0 for w in asked)
            hit /= max(1.0, len(said) ** 0.3)
            if hit > score:
                best, score = name, hit
        return best if score >= 0.5 else None

    def __contains__(self, name):
        return name in self.clips


def pack(clips, neutral, path):
    """Writes motions.npz. `clips`: [(name, arrays, more)], arrays as a Clip
    takes them, more: loop, stride, hand, air."""
    index, parts, at = {"clips": [], "neutral": np.asarray(neutral).round(4).tolist(), "fps": FPS}, {}, 0
    for name, arrays, more in clips:
        n = len(arrays["root"])
        entry = {"name": name, "start": at, "frames": n}
        if more.get("loop"):
            entry["loop"] = True
            entry["stride"] = [round(float(v), 5) for v in more["stride"]]
        if more.get("hand"):
            entry["hand"] = more["hand"]
        if more.get("label"):
            entry["label"] = more["label"]
        if more.get("air"):
            entry["air"] = [round(float(v), 4) for v in more["air"]]
        index["clips"].append(entry)
        at += n
        for key, value in arrays.items():
            parts.setdefault(key, []).append(value)
    whole = {key: np.concatenate(value) for key, value in parts.items()}
    np.savez_compressed(
        path, index=np.frombuffer(json.dumps(index, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        root=whole["root"].astype(np.float32),
        hips=np.rint(whole["hips"] * 32767).astype(np.int16), chest=np.rint(whole["chest"] * 32767).astype(np.int16),
        head=np.rint(whole["head"] * 32767).astype(np.int16), limbs=np.rint(whole["limbs"] * 32767).astype(np.int16),
        contact=whole["contact"].astype(np.uint8), lift=np.rint(np.clip(whole["lift"], 0, 60) * 1000).astype(np.uint16))
    return at


# ---- a figure's own build ------------------------------------------------------
class Body:
    """The skeleton rig.py found for a figure on two legs, seen from the
    figure: x is where it looks. Lengths are the figure's own (times `scale`)."""

    def __init__(self, bones, front, scale=1.0, neutral=None):
        self.bones, self.front, self.scale, self.neutral = bones, front, scale, neutral
        self.facing = math.atan2(front[1], front[0])      # where it looks, in the model's axes
        own = qz(-self.facing)
        self.names = [b["name"] for b in bones]
        self.parent = [b["parent"] for b in bones]
        self.head = [qrot(own, np.asarray(b["head"], dtype=float)) * scale for b in bones]
        self.tail = [qrot(own, np.asarray(b["tail"], dtype=float)) * scale for b in bones]
        self.dir = [unit(t - h) for h, t in zip(self.head, self.tail)]
        self.length = [float(np.linalg.norm(t - h)) for h, t in zip(self.head, self.tail)]
        role = {}
        for i, b in enumerate(bones):
            role.setdefault(b["role"], []).append(i)
        self.hips, self.chest, self.skull = role["hips"][0], role["chest"][0], role["head"][0]

        def lower(i, what):
            return next(k for k, b in enumerate(bones) if b["parent"] == i and b["role"] == what)

        legs = sorted(role["leg"], key=lambda i: not bones[i]["left"])          # left, right
        self.legs = [(i, lower(i, "shin")) for i in legs]
        self.arms, self.hands = {}, {}
        for i in role.get("arm", []):
            self.arms["l" if bones[i]["left"] else "r"] = (i, lower(i, "forearm"))
        for i in role.get("hand", []):
            self.hands["l" if bones[i].get("left") else "r"] = i
        self.pelvis = self.head[self.hips]
        self.mid = (self.head[self.legs[0][0]] + self.head[self.legs[1][0]]) / 2      # between the hip joints
        self.leg = float(np.mean([self.length[u] + self.length[s] for u, s in self.legs]))
        self.height = float(max(t[2] for t in self.tail))
        # Short legs take quicker steps: a gnome does not stride like a grown man in slow motion.
        self.quick = min(1.5, max(1.0, math.sqrt(0.5 * self.height / self.leg)))
        # A jump is as high as the figure is tall, not as its legs are long.
        self.air = min(2.4, max(1.0, 0.47 * self.height / self.leg))
        # An arm that hangs is free and does what the motion's arm does. One that is
        # held up or bent (it carries something) keeps its hold and only moves with the motion.
        self.free = {}
        for side, k in (("l", 4), ("r", 6)):
            if side in self.arms and neutral is not None:
                off = max(math.acos(max(-1.0, min(1.0, float(self.dir[bone] @ neutral[k + j])))) for j, bone in enumerate(self.arms[side]))
                self.free[side] = 1.0 - fade((off - math.radians(30.0)) / math.radians(35.0))

    def sized(self, scale):
        """The same figure, larger or smaller."""
        return Body(self.bones, self.front, scale, self.neutral)

    def hand(self, wanted):
        """The arm a gesture is made with, when the motion makes it with
        `wanted`: the other one if this one is missing, or if it hangs low
        while the other is up (what hangs low carries something)."""
        other = "r" if wanted == "l" else "l"
        if wanted not in ("l", "r") or other not in self.arms:
            return wanted
        if wanted not in self.arms:
            return other
        up = {side: self.tail[self.arms[side][1]][2] for side in (wanted, other)}
        reach = self.length[self.arms[wanted][0]] + self.length[self.arms[wanted][1]]
        return other if up[other] > up[wanted] + 0.25 * reach else wanted

    def turns(self, hips, chest, head, limbs, swings=None, hands=None):
        """The bones' turns (as rig poses take them: each from its rest, seen
        from the model, a child's after its parent's) for a pose given as
        turns of hips, chest and head and directions of the limbs.
        `swings`: what turns the limbs frame after frame (see Swings).
        `hands`: how far each hand is bent at the wrist (radians, "l"/"r"):
        curled in for a fist, back for an open hand."""
        out = {self.hips: hips, self.chest: qmul(qinv(hips), chest), self.skull: qmul(qinv(chest), head)}
        whole = {self.hips: hips, self.chest: chest, self.skull: head}

        def swing(i, parent, direction):
            carried = qrot(whole[parent], self.dir[i])
            whole[i] = qmul(swings.turn(i, carried, direction) if swings else qarc(carried, direction), whole[parent])
            out[i] = qmul(qinv(whole[parent]), whole[i])

        for k, (upper, shin) in enumerate(self.legs):
            swing(upper, self.hips, limbs[2 * k])
            swing(shin, upper, limbs[2 * k + 1])
        for side, k in (("l", 4), ("r", 6)):
            if side in self.arms:
                upper, fore = self.arms[side]
                swing(upper, self.chest, limbs[k])
                swing(fore, upper, limbs[k + 1])
                bend = (hands or {}).get(side, 0.0)
                if side in self.hands and abs(bend) > 1e-4:
                    # The hand turns in towards the body (its palm), about the line across the forearm.
                    i = self.hands[side]
                    along = qrot(whole[fore], self.dir[fore])
                    axis = cross(np.array([0.0, 1.0 if side == "l" else -1.0, 0.0]), along)
                    if np.linalg.norm(axis) < 1e-6:
                        axis = np.array([1.0, 0.0, 0.0])
                    whole[i] = qmul(qaxis(unit(axis), bend), whole[fore])
                    out[i] = qmul(qinv(whole[fore]), whole[i])
        to_model, back = qz(self.facing), qz(-self.facing)
        return {self.names[i]: qmul(qmul(to_model, q), back) for i, q in out.items()}, whole

    def joints(self, whole):
        """Where the bones' ends are for a pose (from the model's origin, seen from the figure)."""
        at, same = {}, np.array([1.0, 0.0, 0.0, 0.0])
        for i in range(len(self.names)):
            p = self.parent[i]
            start = self.head[i] if p < 0 else at[p][0] + qrot(whole.get(p, same), self.head[i] - self.head[p])
            at[i] = (start, start + qrot(whole.get(i, whole.get(p, same)), self.tail[i] - self.head[i]))
        return at


def reach(hip, foot, knee, upper, lower, fore, soft=0.9):
    """A leg solved for where its foot is to be: the directions of thigh and
    shin. The knee stays on the side it was on (`knee`: where it was); a
    leg that was straight bends the way the figure looks (`fore`)."""
    way = foot - hip
    d = float(np.linalg.norm(way))
    along = way / d if d > 1e-9 else np.array([0.0, 0.0, -1.0])
    # Near its full length a leg does not snap straight: the last part (from `soft` on) it gives way softly.
    whole, soft = upper + lower, soft * (upper + lower)
    if d > soft:
        d = soft + (whole - soft) * (1.0 - math.exp(-(d - soft) / (whole - soft)))
    d = min(max(d, abs(upper - lower) + 1e-6), 0.999 * whole)
    a = (upper * upper - lower * lower + d * d) / (2 * d)
    h = math.sqrt(max(0.0, upper * upper - a * a))
    bend = (knee - hip) - along * float((knee - hip) @ along) + 0.3 * upper * (fore - along * float(fore @ along))
    if np.linalg.norm(bend) < 1e-9:
        bend = np.cross(along, [0.0, 1.0, 0.0])
    joint = hip + along * a + unit(bend) * h
    return unit(joint - hip), unit(hip + along * d - joint)


def fade(u):
    """0..1, starting and ending gently."""
    u = min(1.0, max(0.0, u))
    return u * u * u * (u * (6 * u - 15) + 10)


def swell(i, n, edge):
    """0..1..0 over frames 0..n: up within `edge` frames, down within the last."""
    return fade(min(i, n - i) / max(1.0, min(edge, n / 2)))


def settle(x0, v0, span, t):
    """What is left at time t of a difference x0 that changes by v0 a second
    and is to be gone, and at rest, after `span` seconds (inertialization,
    D. Bollo 2018): the motion goes on smoothly instead of two being mixed."""
    if abs(x0) < 1e-9 or t >= span:
        return 0.0
    sign = 1.0 if x0 >= 0 else -1.0
    x0, v0 = abs(x0), min(0.0, sign * v0)                # a difference that still grows starts from rest
    if v0 < 0:
        span = min(span, -5.0 * x0 / v0)                 # so that it never swings past
    if t >= span:
        return 0.0
    a0 = (-8.0 * v0 * span - 20.0 * x0) / (span * span)
    A = -(a0 * span ** 2 + 6.0 * v0 * span + 12.0 * x0) / (2.0 * span ** 5)
    B = (3.0 * a0 * span ** 2 + 16.0 * v0 * span + 30.0 * x0) / (2.0 * span ** 4)
    C = -(3.0 * a0 * span ** 2 + 12.0 * v0 * span + 20.0 * x0) / (2.0 * span ** 3)
    return sign * max(0.0, ((((A * t + B) * t + C) * t + 0.5 * a0) * t + v0) * t + x0)


class Carry:
    """What is left of the motion before, after a change to another one:
    for every part of the pose how far it was from the new motion, and how
    fast that changed."""

    def __init__(self, last, before, new, new_before, pace, new_pace, dt, span):
        self.span, self.t, self.dt = span, 0.0, dt
        self.turns = {}
        for key in ("hips", "chest", "head"):
            self.turns[key] = self._turn(qmul(getattr(last, key), qinv(getattr(new, key))), qmul(getattr(before, key), qinv(getattr(new_before, key))))
        self.limbs = [self._turn(qarc(new.limbs[i], last.limbs[i]), qarc(new_before.limbs[i], before.limbs[i])) for i in range(len(LIMBS))]
        self.height = (last.height - new.height, ((last.height - new.height) - (before.height - new_before.height)) / dt)
        self.pace = pace - new_pace                       # forwards, to the left, turning: a second

    def _turn(self, now, earlier):
        axis, angle = qangle(now)
        earlier = earlier if earlier[0] >= 0 else -earlier
        was = 2.0 * math.atan2(float(earlier[1:] @ axis), float(earlier[0]))
        return axis, float(angle), (float(angle) - was) / self.dt

    def apply(self, pose, pace):
        """The new motion's pose and pace with what is left of the old one."""
        self.t += self.dt
        t = self.t
        for key, (axis, x0, v0) in self.turns.items():
            setattr(pose, key, qmul(qaxis(axis, settle(x0, v0, self.span, t)), getattr(pose, key)))
        for i, (axis, x0, v0) in enumerate(self.limbs):
            pose.limbs[i] = qrot(qaxis(axis, settle(x0, v0, self.span, t)), pose.limbs[i])
        pose.height += settle(self.height[0], self.height[1], self.span, t)
        return pose, pace + self.pace * (1.0 - fade(t / self.span))

    @property
    def over(self):
        return self.t >= self.span


# ---- a figure in a scene -------------------------------------------------------
class Foot:
    def __init__(self):
        self.at = None                                    # where it stands on the ground, or None: it is in the air
        self.looked = 0.0                                 # where the figure looked when it was put down
        self.slip, self.slip_t = np.zeros(3), 1.0         # after it let go: how far it was from where the motion has it
        self.step = None                                  # a step of its own to where the motion wants it: [from, seconds so far, seconds]
        self.rest = 0.0                                   # seconds since such a step
        self.bears = 0.0                                  # how much it is on its toes with nothing else to stand on


class Performer:
    """One figure through a scene, frame by frame. It always does something:
    a motion that was asked for, or it stands and breathes."""
    TOUCH, FAR, TWIST, DEEP, LET_GO = 0.08, 0.3, 0.6, 0.3, 0.15   # leg lengths, leg lengths, radians, leg lengths, seconds

    def __init__(self, body, library, fps, xy, heading, seed=0):
        self.body, self.lib, self.fps, self.dt = body, library, fps, 1.0 / fps
        self.xy = np.array(xy, dtype=float)               # the hips over the ground
        self.heading = float(heading)                     # where it looks
        self.clip, self.rate, self.mirror = library["idle"], 1.0, False
        self.t = (seed * 1.7) % self.clip.seconds         # two figures do not breathe in step
        self.carry = None
        self.last = self.before = self.clip.pose(self.t)
        self.pace = np.zeros(3)                           # forwards, to its left (leg lengths), turning: a second
        self.feet = [Foot(), Foot()]
        self.drop = 0.0
        self.air = body.air                               # how much higher than recorded it leaves the ground
        self.gives = {}                                   # how much an arm that holds something has given its hold up for a gesture
        self.sways, self.looks, self.heads = [], [], []   # what is laid over the motion: swaying, looking at something, nodding
        self.reaches = []                                 # a hand that is to be somewhere: (side, start, end, target)
        self.flails = []                                  # thrown through the air: (start, air, lie, rise) frames, see flail()
        self.grips = []                                   # how a hand is held: (side, start, end, shape), see grip()
        self.watch = False                                # keep the joints of every frame, to look at them
        self.frames = []                                  # per frame: where the model's origin is, where it looks, its pose

    # -- one frame --
    def entry(self, clip, mirror=False, within=0.35):
        """Where in a motion to come in: the moment in its first part (`within`
        of it) whose pose is nearest the one the figure has now, the feet that
        stand standing (so a step goes on with the foot that is free, and a
        loop is joined at its matching phase, not at its start)."""
        if not hasattr(self, "last") or clip.frames < 8:
            return 0.0
        best, at = None, 0.0
        for f in range(0, max(1, int(clip.frames * (1.0 if clip.loop else within))), 2):
            t = f / FPS
            pose = clip.pose(t, mirror)
            far = float(np.sum(1.0 - np.sum(pose.limbs * self.last.limbs, axis=1)))
            far += 2.0 * float(np.sum((np.asarray(pose.contact) > 0) != (np.asarray(self.last.contact) > 0)))
            far += 3.0 * abs(pose.height - self.last.height)
            far += 0.5 * t                                # what it skips is missed: earlier is better
            if best is None or far < best:
                best, at = far, t
        return at

    def change(self, name, at=0.0, mirror=False, rate=1.0, span=0.3):
        """Goes over to another motion, smoothly."""
        clip = self.lib[name]
        new = clip.pose(at, mirror)
        new_before = clip.pose(at - self.dt * rate, mirror) if at > 0 or clip.loop else new
        new_pace = clip.step(at, at + self.dt * rate, mirror) / self.dt
        self.carry = Carry(self.last, self.before, new, new_before, self.pace, new_pace, self.dt, span)
        self.clip, self.t, self.mirror, self.rate = clip, at, mirror, rate

    def tick(self, steer=None):
        """One frame further. `steer`: asked every frame for an extra turn
        (radians) and how much of the motion's pace is wanted."""
        body, dt, f = self.body, self.dt, len(self.frames)
        t0 = self.t
        self.t += self.rate * dt
        pose = self.clip.pose(self.t, self.mirror)
        pace = self.clip.step(t0, self.t, self.mirror) / dt
        if self.carry:
            pose, pace = self.carry.apply(pose, pace)
            if self.carry.over:
                self.carry = None
        self.before, self.last, self.pace = self.last, pose.copy(), pace
        for start, n, lean in self.sways:                 # it sways: the body leans to one side and the other
            if start <= f < start + n:
                u = (f - start) / n
                side = qaxis([1.0, 0.0, 0.0], lean * math.sin(u * math.pi * 6) * (1 - u) * min(1.0, 8 * u))
                pose.hips, pose.chest, pose.head = qmul(side, pose.hips), qmul(qpart(side, 1.4), pose.chest), qmul(qpart(side, 1.2), pose.head)
        turn, part = steer(self, pace) if steer else (0.0, 1.0)
        turned = pace[2] * dt + turn
        self.xy = self.xy + turn2(self.heading + 0.5 * turned, pace[:2] * part * body.leg * dt)
        self.heading += turned
        # where hips, knees and feet are, as the motion has them
        heading = qz(self.heading)
        root = np.array([self.xy[0], self.xy[1], (pose.height + (self.air - 1.0) * pose.lift) * body.leg - self.drop])
        limbs = self._arms(pose.limbs.copy())
        f = len(self.frames)
        thrown = self._thrown(f, pose, limbs)
        for side, start, end, target in self.reaches:
            if not start <= f <= end + int(0.4 * self.fps):
                continue
            # In and out in a third of a second; meanwhile the hand is where it is to be.
            part = fade(min((f - start) / (0.35 * self.fps), 1.0, (end + 0.4 * self.fps - f) / (0.4 * self.fps)))
            upper, lower = body.arms[side]
            k = 4 if side == "l" else 6
            shoulder = root + qrot(heading, qrot(pose.chest, body.head[upper] - body.mid))
            elbow = shoulder + qrot(heading, limbs[k]) * body.length[upper]
            goal = np.asarray(target(f), dtype=float)
            a, b = reach(shoulder, goal, elbow, body.length[upper], body.length[lower], np.array([0.0, 0.0, -1.0]), soft=0.97)
            back = qz(-self.heading)
            for j, want in ((0, qrot(back, a)), (1, qrot(back, b))):
                limbs[k + j] = qrot(qpart(qarc(limbs[k + j], want), part), limbs[k + j])
        hip, knee, foot = [], [], []
        for k, (upper, shin) in enumerate(body.legs):
            h = root + qrot(heading, qrot(pose.hips, body.head[upper] - body.mid))
            n = h + qrot(heading, limbs[2 * k]) * body.length[upper]
            hip.append(h)
            knee.append(n)
            foot.append(n + qrot(heading, limbs[2 * k + 1]) * body.length[shin])
        held = [self._foot(k, foot[k], 0 if thrown else int(pose.contact[k]), hip[k], turned / dt) for k in range(2)]
        # A foot that stands must be within reach: the hips come down to it, and up again slowly.
        need = 0.0
        for k, (upper, shin) in enumerate(body.legs):
            if held[k][0]:
                far = 0.995 * (body.length[upper] + body.length[shin])
                flat = float(np.hypot(*(held[k][1][:2] - hip[k][:2])))
                need = max(need, hip[k][2] + self.drop - held[k][1][2] - math.sqrt(max(0.0, far * far - flat * flat)))
        need = min(need, self.DEEP * body.leg)
        lower = (need - self.drop) * (0.6 if need > self.drop else 0.15)
        self.drop += lower
        root[2] -= lower
        fore = qrot(heading, qrot(pose.hips, np.array([1.0, 0.0, 0.0])))
        back = qz(-self.heading)
        for k, (upper, shin) in enumerate(body.legs):
            for point in (hip[k], knee[k], foot[k]):
                point[2] -= lower
            # On its toes with no other foot to stand on (running, tiptoeing), the leg's end is on the ground.
            bears = int(pose.contact[k]) == 1 and int(pose.contact[1 - k]) != 2
            self.feet[k].bears = min(1.0, max(0.0, self.feet[k].bears + (dt if bears else -dt) / 0.09))
            if held[k][0]:
                goal = held[k][1]
            else:
                goal = foot[k] + held[k][1]
                goal[2] *= 1.0 - fade(self.feet[k].bears)
            goal[2] = max(goal[2], 0.0)
            if np.linalg.norm(goal - foot[k]) > 1e-9:
                a, b = reach(hip[k], goal, knee[k], body.length[upper], body.length[shin], fore)
                limbs[2 * k], limbs[2 * k + 1] = qrot(back, a), qrot(back, b)
        origin = root - qrot(heading, body.pelvis + qrot(pose.hips, body.mid - body.pelvis))
        frame = {"origin": origin, "heading": self.heading, "root": root, "body": body, "hips": pose.hips, "chest": pose.chest, "head": pose.head, "limbs": limbs}
        if self.watch:
            frame["stands"] = [None if foot.at is None else foot.at.copy() for foot in self.feet]
            frame["doing"] = (self.clip.name, round(self.t, 3), [foot.step is not None for foot in self.feet], round(self.drop / body.leg, 3))
        self.frames.append(frame)

    # Where the limbs point while a figure is thrown (seen from itself, x ahead, y its left):
    # in the air it reaches forward, its knees drawn up; lying it is spread out on its back;
    # getting up it draws its legs in and pushes itself up with its arms.
    FLY = ((0.55, 0.15, -0.82), (-0.2, 0.08, -0.98), (0.55, -0.15, -0.82), (-0.2, -0.08, -0.98),
           (0.55, 0.5, 0.67), (0.8, 0.3, 0.52), (0.55, -0.5, 0.67), (0.8, -0.3, 0.52))
    LIE = ((0.05, 0.22, -0.97), (0.0, 0.12, -0.99), (0.05, -0.22, -0.97), (0.0, -0.12, -0.99),
           (0.05, 0.92, 0.38), (0.0, 0.9, 0.44), (0.05, -0.92, 0.38), (0.0, -0.9, 0.44))
    TUCK = ((0.75, 0.2, -0.63), (-0.65, 0.08, -0.76), (0.75, -0.2, -0.63), (-0.65, -0.08, -0.76),
            (0.55, 0.3, -0.78), (0.75, 0.1, -0.66), (0.55, -0.3, -0.78), (0.75, -0.1, -0.66))

    def flail(self, start, air, lie, rise):
        """It is thrown from frame `start`: `air` frames in the air, `lie`
        on the ground, `rise` getting up (scene.knock turns the whole figure)."""
        self.flails.append((int(start), int(air), int(lie), int(rise)))

    def _thrown(self, f, pose, limbs):
        """Lays the pose of a thrown figure over the motion (see flail).
        True while its feet are not on the ground."""
        for start, air, lie, rise in self.flails:
            i = f - start
            if not 0 <= i < air + lie + rise:
                continue
            edge = max(2.0, 0.12 * self.fps)
            if i < air:                                   # it is hit: limbs fly forward, the body curls
                target, w = self.FLY, fade(i / edge)
                curl = 0.35 * w
                if i > air - edge:                        # ... and spreads out as it comes down
                    target = [unit(np.asarray(a) + (np.asarray(b) - np.asarray(a)) * fade((i - air + edge) / edge)) for a, b in zip(self.FLY, self.LIE)]
                    curl *= 1.0 - fade((i - air + edge) / edge)
            elif i < air + lie:
                target, w, curl = self.LIE, 1.0, 0.0
            else:                                         # up: the legs come in, then it stands as the motion has it
                u = (i - air - lie) / max(1, rise)
                tuck = math.sin(math.pi * min(1.0, 1.4 * u))
                target = [unit(np.asarray(a) + (np.asarray(b) - np.asarray(a)) * max(0.0, tuck)) for a, b in zip(self.LIE, self.TUCK)]
                w, curl = 1.0 - fade((u - 0.6) / 0.4), 0.3 * max(0.0, tuck)
            for k in range(len(LIMBS)):
                want = unit(np.asarray(target[k], dtype=float))
                limbs[k] = qrot(qpart(qarc(limbs[k], want), w), limbs[k])
            bend = qaxis([0.0, 1.0, 0.0], curl)
            pose.chest, pose.head = qmul(bend, pose.chest), qmul(qpart(bend, 1.5), pose.head)
            return i < air + lie + 0.75 * rise
        return False

    def _arms(self, limbs):
        """The arms as this figure does them (see Body.free)."""
        body, neutral = self.body, self.lib.neutral
        for side, k in (("l", 4), ("r", 6)):
            free = body.free.get(side, 1.0)
            if side not in body.arms or free >= 1.0:
                continue
            # A big gesture is made as it is. The arm takes its time to give its hold up and to take it up again.
            away = max(math.acos(max(-1.0, min(1.0, float(limbs[k + j] @ neutral[k + j])))) for j in range(2))
            wish = max(free, fade((away - math.radians(40.0)) / math.radians(50.0)))
            part = self.gives[side] = self.gives.get(side, free) + min(3.0 * self.dt, max(-3.0 * self.dt, wish - self.gives.get(side, free)))
            for j, bone in enumerate(body.arms[side]):
                wanted = limbs[k + j]
                kept = qrot(qarc(neutral[k + j], wanted), body.dir[bone])           # its own hold, moved as the motion moves
                limbs[k + j] = qrot(qpart(qarc(kept, wanted), fade(part)), kept)
        return limbs

    def _foot(self, k, free, contact, hip, turning):
        """Where a foot is to be this frame: (True, the place it is held
        at) or (False, how far from where the motion has it). `free`: where
        the motion has it, `contact`: what the motion says it does (2: it
        stands), `hip`: where its leg begins, `turning`: how fast the figure
        turns (radians a second)."""
        foot, body, dt = self.feet[k], self.body, self.dt
        leg, down = body.leg, contact == 2
        foot.rest += dt
        if foot.step is not None:                         # on its way to where the motion wants it
            foot.step[1] += dt
            u = min(1.0, foot.step[1] / foot.step[2])
            # ... or will want it when it arrives: the body turns on meanwhile
            ahead = self.xy + turn2(turning * (1.0 - u) * foot.step[2] + 0.5 * turning * foot.step[2], free[:2] - self.xy)
            xy = foot.step[0] + (ahead - foot.step[0]) * fade(u)
            if u >= 1.0:
                foot.step, foot.at, foot.looked, foot.rest = None, xy.copy(), self.heading, 0.0
            return True, np.array([xy[0], xy[1], 0.12 * leg * math.sin(math.pi * u)])
        if foot.at is not None:
            if not down:                                  # it lifts off: it lets go of the ground, gently
                foot.slip, foot.slip_t, foot.at = np.array([foot.at[0] - free[0], foot.at[1] - free[1], -free[2]]), 0.0, None
            else:
                other = self.feet[1 - k]
                off = float(np.hypot(*(foot.at - free[:2])))
                twisted = abs(wrap(self.heading - foot.looked))
                if (off > self.FAR * leg or twisted > self.TWIST) and other.at is not None and other.step is None and other.rest > 0.04:
                    # it stands too far from where it belongs, or the body has turned away over it: a step of its own
                    foot.step, foot.at = [foot.at.copy(), 0.0, 0.2 + 0.15 * min(1.0, off / leg)], None
                    return True, np.array([foot.step[0][0], foot.step[0][1], 0.0])
                # What it cannot reach any more it does not hold: it is dragged along.
                upper, shin = body.legs[k]
                most = 0.97 * (body.length[upper] + body.length[shin])
                flat = foot.at - hip[:2]
                if float(np.hypot(*flat)) > most:
                    foot.at = hip[:2] + flat * (most / float(np.hypot(*flat)))
                return True, np.array([foot.at[0], foot.at[1], 0.0])
        if down and free[2] <= self.TOUCH * leg:
            foot.at, foot.looked = free[:2].copy(), self.heading   # it comes down: from now on it stands here
            return True, np.array([free[0], free[1], 0.0])
        if foot.slip_t < 1.0:
            foot.slip_t += dt / self.LET_GO
            return False, foot.slip * (1.0 - fade(foot.slip_t))
        return False, np.zeros(3)

    # -- what a scene asks of it --
    def wait(self, frame):
        """Goes on with what it does until a frame."""
        while len(self.frames) < frame:
            self.tick()
        return len(self.frames)

    def rest(self, span=0.35):
        """Back to standing: at the moment of standing nearest the pose it
        is in, and the further that is, the more time it takes."""
        idle = self.lib["idle"]
        if self.clip is not idle:
            at = self.entry(idle)
            new = idle.pose(at)
            far = float(np.sum(1.0 - np.sum(new.limbs * self.last.limbs, axis=1))) + 3.0 * abs(new.height - self.last.height)
            self.change("idle", at=at, span=max(span, min(0.9, span + 0.25 * far)))

    def perform(self, name, seconds=None, times=1, rate=1.0, span=0.3):
        """Does a motion: once (or `times`), a round for `seconds`. A
        gesture is made with the hand that is free. Returns the frame it is
        done at."""
        clip = self.lib[name]
        mirror = bool(clip.hand) and self.body.hand(clip.hand) != clip.hand
        self.change(name, mirror=mirror, rate=rate, span=span)
        length = clip.seconds * max(1, times) / rate
        if seconds:
            length = seconds if clip.loop else min(seconds, length)
        for _ in range(max(1, int(round(length * self.fps)))):
            self.tick()
        self.rest()
        return len(self.frames)

    def go(self, name, goal, stop=0.0, seconds=None, on=False, limit=40.0):
        """Goes somewhere in a gait. `goal`: asked every frame for the place
        (it may move), `stop`: how far from it the figure stops, `on`: it
        does not stop there but goes on (with what is asked next). Returns
        the frame it has arrived at."""
        clip, body, dt = self.lib[name], self.body, self.dt
        drift = math.atan2(clip.stride[1], clip.stride[0])    # the way the gait travels, seen from the figure (0: forwards)
        rate, part = body.quick, 1.0
        speed = clip.pace * body.leg * rate
        far = max(0.0, float(np.linalg.norm(np.asarray(goal(len(self.frames)), dtype=float) - self.xy)) - stop)
        if seconds:                                           # asked to take this long: quicker or slower steps, then longer or shorter ones
            wanted = far / max(0.3, seconds - 0.3) / speed
            quicker = min(1.3, max(0.75, wanted))
            rate, part = rate * quicker, min(1.25, max(0.6, wanted / quicker))
        else:                                                 # whole strides: it arrives as a stride ends
            whole = max(1.0, round(far / (clip.pace * clip.seconds * body.leg)))
            part = min(1.12, max(0.88, far / (whole * clip.pace * clip.seconds * body.leg)))
        turning = math.radians(200.0) / max(1.0, clip.pace * rate / 1.5)       # the faster, the wider the curve
        self.change(name, rate=rate, span=0.3)
        end = 0.35
        for _ in range(int(limit * self.fps)):
            way = np.asarray(goal(len(self.frames)), dtype=float) - self.xy
            ahead = float(np.linalg.norm(way))
            now = float(np.hypot(*self.pace[:2])) * body.leg * part
            if ahead <= stop + (0.0 if on else 0.5 * now * end):
                break
            off = wrap(math.atan2(way[1], way[0]) - drift - self.heading)

            def steer(_, pace, off=off):
                most = turning * dt
                return min(most, max(-most, off * 6.0 * dt)), part * max(0.3, math.cos(min(abs(off), 1.2)))

            self.tick(steer)
        if not on:
            self.rest(end)
        return len(self.frames)

    def leap(self, name, towards, over, span):
        """A great leap (after a run-up that `go` left on): so high that
        the feet clear `over`, and `span` far while in the air. Returns the
        frame it has landed and caught itself at."""
        clip, body = self.lib[name], self.body
        off, land = clip.air[0], clip.air[1]
        rise = max(body.air, (over + 0.25 * body.leg) / (clip.air[2] * body.leg))
        slow = min(2.2, max(1.0, math.sqrt(rise / body.air)))  # higher takes longer
        flown = float(np.linalg.norm(clip.place(land)[:2] - clip.place(off)[:2])) * body.leg
        far = min(6.0, max(0.6, span / max(1e-6, flown)))
        self.change(name, rate=body.quick, span=0.2)
        while self.t < clip.seconds - 1e-6:
            flying = off <= self.t < land
            self.rate, self.air = (body.quick / slow, rise) if flying else (body.quick, body.air)
            way = np.asarray(towards, dtype=float)
            turn = wrap(math.atan2(way[1], way[0]) - self.heading)
            self.tick(lambda _, pace, turn=turn, far=far if flying else 1.0: (min(0.1, max(-0.1, turn * 0.2)), far))
        self.air = body.air
        self.rest()
        return len(self.frames)

    def face(self, angle, seconds=None):
        """Turns on the spot to look in a direction; its feet follow in
        steps. Returns the frame it is done at."""
        off = wrap(angle - self.heading) if abs(angle - self.heading) <= math.pi else angle - self.heading
        body = self.body
        wide = float(np.linalg.norm((body.head[body.legs[0][0]] - body.head[body.legs[1][0]])[:2])) / (2 * body.leg)   # how far the hips are from the middle, in legs
        fastest = math.radians(170.0) * min(1.0, 0.3 / max(wide, 1e-6))        # as fast as the feet can follow
        n = max(2, int(round(max(seconds or 0.0, 0.25 + abs(off) / fastest) * self.fps)))
        start = self.heading
        for i in range(1, n + 1):                         # whatever the motion itself turns meanwhile is made up for
            self.tick(lambda _, pace, wanted=start + off * fade(i / n): (wanted - self.heading - pace[2] * self.dt, 1.0))
        return len(self.frames)

    def sway(self, seconds, lean=0.16):
        n = max(6, int(round(seconds * self.fps)))
        self.sways.append((len(self.frames), n, lean))
        return self.wait(len(self.frames) + n)

    def reach_to(self, side, target, start, end):
        """A hand is to be at a place from frame to frame (a pose: points at,
        touches, holds up). `target`: asked every frame for the place
        (x, y, z). The arm comes there and goes back gently."""
        if side in self.body.arms:
            self.reaches.append((side, int(start), int(end), target))

    # How far a hand bends at the wrist for each shape (radians; in, towards the palm).
    # Fingers that a model made from a photo has grown together cannot close one by
    # one: the hand as a whole curls into a fist, stretches out or opens back.
    GRIPS = {"fist": 0.8, "point": 0.0, "open": -0.35, "relaxed": 0.2}

    def grip(self, side, start, end, shape):
        """Holds a hand ("l", "r" or "both") in a shape from frame to frame
        (see GRIPS); it goes into it and out of it gently."""
        if shape in self.GRIPS:
            for one in (("l", "r") if side == "both" else (side,)):
                self.grips.append((one, int(start), int(end), shape))

    def _hands(self, f):
        """How each hand is bent in a frame: the shape held most firmly then."""
        out = {}
        for side in self.body.hands:
            best, value = 0.0, 0.0
            for one, start, end, shape in self.grips:
                if one == side and start <= f < end:
                    w = swell(f - start, end - start, 0.2 * self.fps)
                    if w > best:
                        best, value = w, self.GRIPS[shape] * w
            out[side] = value
        return out

    def look(self, target, start, end, part=1.0):
        """Looks at something from frame to frame. `target`: asked every frame for the place (x, y, z)."""
        self.looks.append((int(start), int(end), target, part))

    def nod(self, kind, start, frames, times):
        self.heads.append((int(start), int(frames), kind, times))

    def resize(self, scale):
        """Grows or shrinks where it stands."""
        at, ratio = self.origin, scale / self.body.scale
        self.body = self.body.sized(scale)
        self.air = self.body.air
        self.xy = at + turn2(self.heading, (self.body.pelvis + qrot(self.last.hips, self.body.mid - self.body.pelvis))[:2])
        for foot in self.feet:
            if foot.at is not None:
                foot.at = at + (foot.at - at) * ratio

    def move(self, by, past=True):
        """Puts the figure, and all it did so far, somewhere else on the
        ground. Not `past`: only from now on (it was carried there, see
        scene.knock; what it did so far stays where it was done)."""
        by = np.asarray(by, dtype=float)
        self.xy = self.xy + by
        for foot in self.feet:
            if foot.at is not None:
                foot.at = foot.at + by
            if foot.step is not None:
                foot.step[0] = foot.step[0] + by
        for frame in self.frames if past else ():
            for key in ("origin", "root"):
                frame[key] = frame[key] + np.array([by[0], by[1], 0.0])
            if "stands" in frame:
                frame["stands"] = [None if at is None else at + by for at in frame["stands"]]

    @property
    def origin(self):
        """Where the model's origin is on the ground now."""
        body = self.body
        return self.xy - turn2(self.heading, (body.pelvis + qrot(self.last.hips, body.mid - body.pelvis))[:2])

    def finish(self, last):
        """The scene is over at frame `last`: for every frame where the
        model's origin is, how far the model is turned, how large it is and
        the turns of its bones."""
        self.wait(last + 1)
        out, swings = [], Swings()
        for f, frame in enumerate(self.frames[:last + 1]):
            body = frame["body"]
            chest, head = self._head(f, frame)
            turns, whole = body.turns(frame["hips"], chest, head, frame["limbs"], swings, self._hands(f) if self.grips else None)
            out.append((frame["origin"], frame["heading"] - body.facing, body.scale, turns))
            if self.watch:
                turn = qz(frame["heading"])
                frame["joints"] = {body.names[i]: (frame["origin"] + qrot(turn, a), frame["origin"] + qrot(turn, b)) for i, (a, b) in body.joints(whole).items()}
        return out

    def _head(self, f, frame):
        """Chest and head of a frame with what is laid over them: where it looks, a nod, a shake of the head."""
        chest, head, body = frame["chest"], frame["head"], frame["body"]
        for start, end, target, part in self.looks:
            if not start <= f < end:
                continue
            at = target(f)
            if at is None:
                continue
            w = part * swell(f - start, end - start, 0.4 * self.fps)
            eye = frame["origin"] + np.array([0.0, 0.0, 0.9 * body.height])
            to = qrot(qz(-frame["heading"]), np.asarray(at, dtype=float) - eye)
            ahead, sees = qrot(chest, np.array([1.0, 0.0, 0.0])), qrot(head, np.array([1.0, 0.0, 0.0]))
            aside = min(1.3, max(-1.3, wrap(math.atan2(to[1], to[0]) - math.atan2(ahead[1], ahead[0]))))
            up = min(0.6, max(-0.7, math.atan2(to[2], max(1e-6, math.hypot(to[0], to[1])))))
            chest = qmul(qz(0.25 * aside * w), chest)
            wanted = math.atan2(ahead[1], ahead[0]) + aside
            head = qmul(qz(wrap(wanted - math.atan2(sees[1], sees[0])) * w), head)
            head = qmul(head, qaxis([0.0, 1.0, 0.0], -(up - math.asin(max(-1.0, min(1.0, float(sees[2]))))) * w))
        for start, n, kind, times in self.heads:
            if start <= f < start + n:
                u = (f - start) / n
                if kind == "talk":                    # who speaks nods a little with what it says
                    head = qmul(head, qaxis([0.0, 1.0, 0.0], math.radians(6.0) * (0.5 - 0.5 * math.cos(2 * math.pi * times * u))))
                    head = qmul(head, qz(math.radians(4.0) * math.sin(math.pi * times * u * 0.7)))
                elif kind == "nod":
                    head = qmul(head, qaxis([0.0, 1.0, 0.0], math.radians(22.0) * (0.5 - 0.5 * math.cos(2 * math.pi * times * u))))
                    chest = qmul(chest, qaxis([0.0, 1.0, 0.0], math.radians(5.0) * (0.5 - 0.5 * math.cos(2 * math.pi * times * u))))
                else:
                    head = qmul(head, qz(math.radians(26.0) * math.sin(2 * math.pi * times * u) * min(1.0, 6 * min(u, 1 - u))))
        return chest, head

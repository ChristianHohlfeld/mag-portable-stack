#!/usr/bin/env python3
"""Makes ops/scene/motions.npz from recorded motions.

    python3 ops/scene/mocap/build.py --bvh DIR [--only NAME ...]

The recordings are the CMU Graphics Lab Motion Capture Database in the BVH
conversion by B. Hahne (see ../MOTIONS.md for the terms and the thanks they
ask for). clips.json says which stretch of which recording is which motion.
DIR holds the BVH files; what is missing there is fetched from the pinned
mirror and checked against sources.lock. Each file is read once (DIR/cache).

A recording is turned into what a figure of any build can take over
(motion.py): seen from the body itself, how hips, chest and head are turned
from standing straight, where thighs, shins and arms point, how high the
hips are in leg lengths, and which foot stands.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import motion  # noqa: E402

MIRROR = "https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/09a07f54f3bbb58797325f009282d0b2048a2871/data/%03d/%s.bvh"
FPS = motion.FPS
# BVH: y up, the T pose looks along +z, its left is +x. Here: x forward, y left, z up.
AXES = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHAINS = (("LeftUpLeg", "LeftLeg"), ("LeftLeg", "LeftFoot"), ("RightUpLeg", "RightLeg"), ("RightLeg", "RightFoot"),
          ("LeftArm", "LeftForeArm"), ("LeftForeArm", "LeftHand"), ("RightArm", "RightForeArm"), ("RightForeArm", "RightHand"))


# ---- reading a recording -------------------------------------------------------
def parse(path):
    names, parents, offsets, channels, stack, frame_time = [], [], [], [], [], None
    with open(path) as f:
        lines = iter(f)
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            key = parts[0]
            if key in ("ROOT", "JOINT", "End"):
                names.append(parts[1] if key != "End" else names[stack[-1]] + "End")
                parents.append(stack[-1] if stack else -1)
                offsets.append((0.0, 0.0, 0.0))
                channels.append([])
                pending = len(names) - 1
            elif key == "{":
                stack.append(pending)
            elif key == "}":
                stack.pop()
            elif key == "OFFSET":
                offsets[stack[-1]] = tuple(float(x) for x in parts[1:4])
            elif key == "CHANNELS":
                channels[stack[-1]] = parts[2:]
            elif key == "Frame" and parts[1] == "Time:":
                frame_time = float(parts[2])
                break
        data = np.loadtxt(lines, dtype=np.float64, ndmin=2)
    return names, parents, np.array(offsets), channels, frame_time, data


def _about(axis, degrees):
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    out = np.zeros(a.shape + (3, 3))
    i, j, k = {"X": (0, 1, 2), "Y": (1, 2, 0), "Z": (2, 0, 1)}[axis]
    out[..., i, i] = 1
    out[..., j, j] = out[..., k, k] = c
    out[..., j, k], out[..., k, j] = -s, s
    return out


def world(names, parents, offsets, channels, data):
    """For every frame and joint: how it is turned (3x3) and where it is."""
    frames = len(data)
    R = np.zeros((frames, len(names), 3, 3))
    X = np.zeros((frames, len(names), 3))
    at = 0
    for j in range(len(names)):
        local = np.broadcast_to(np.eye(3), (frames, 3, 3)).copy()
        pos = np.broadcast_to(offsets[j], (frames, 3)).copy()
        for ch in channels[j]:
            col = data[:, at]
            at += 1
            if ch.endswith("position"):
                k = "XYZ".index(ch[0])
                pos[:, k] = col if parents[j] < 0 else pos[:, k] + col
            else:
                local = local @ _about(ch[0], col)
        if parents[j] < 0:
            R[:, j], X[:, j] = local, pos
        else:
            p = parents[j]
            R[:, j] = R[:, p] @ local
            X[:, j] = X[:, p] + np.einsum("fij,fj->fi", R[:, p], pos)
    return np.einsum("ij,fnjk,lk->fnil", AXES, R, AXES), X @ AXES.T


def fetch(trial, folder, lock):
    path = os.path.join(folder, trial + ".bvh")
    if not os.path.exists(path):
        url = MIRROR % (int(trial.split("_")[0]), trial)
        print("fetching", url, flush=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            body = r.read()
        with open(path + ".part", "wb") as f:
            f.write(body)
        os.replace(path + ".part", path)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if trial in lock and lock[trial] != digest:
        raise SystemExit("%s is not the recording sources.lock names (%s)" % (path, digest))
    lock[trial] = digest
    return path


def standing(names, X, leg, rate):
    """What every foot does in every frame (as recorded, `rate` frames a
    second): 0 it is in the air, 1 it bears weight (some part of it is on
    the ground and does not move), 2 it stands (its ankle is at rest too).
    A figure whose legs end without a foot stands only on a foot of the
    second kind: when the heel has left the ground, its leg is on its way."""
    n = len(X)
    width = max(1, int(round(rate / 30.0)))
    ankle0 = float(np.percentile(np.concatenate([X[:, names.index("LeftFoot"), 2], X[:, names.index("RightFoot"), 2]]), 5))
    toe0 = float(np.percentile(np.concatenate([X[:, names.index("LeftToeBase"), 2], X[:, names.index("RightToeBase"), 2]]), 5))
    contact = np.zeros((n, 2), dtype=np.uint8)

    def tidy(on, least):
        for x, y in runs(~on):                        # a gap of a moment is no step
            if y - x <= 0.1 * rate and x > 0 and y < n:
                on[x:y] = True
        for x, y in runs(on):
            if y - x < least * rate:
                on[x:y] = False
        return on

    for k, side in enumerate(("Left", "Right")):
        a, t = calm(X[:, names.index(side + "Foot")], width), calm(X[:, names.index(side + "ToeBase")], width)
        ha, ht = (a[:, 2] - ankle0) / leg, (t[:, 2] - toe0) / leg
        low = np.minimum(ha, ht)
        v = np.where(ha < ht, speed(a[:, :2], rate), speed(t[:, :2], rate)) / leg     # of the part of the foot that is down
        bears = tidy((v < 0.8) & (np.abs(np.gradient(low)) * rate < 1.2) & (low < 0.12), 0.04)
        for x, y in runs(bears):
            if float(np.median(low[x:y])) > 0.07:     # it rests on something that is not the floor
                bears[x:y] = False
        stands = tidy(bears & (speed(a[:, :2], rate) / leg < 0.45) & (np.abs(np.gradient(ha)) * rate < 0.45) & (ha < 0.16), 0.06) & bears
        contact[:, k] = bears.astype(np.uint8) + stands.astype(np.uint8)
    return contact, ankle0


class Recording:
    """One recording at 30 frames a second, the T pose it begins with apart."""

    def __init__(self, trial, folder, lock):
        cache = os.path.join(folder, "cache", trial + ".npz")
        if not os.path.exists(cache):
            names, parents, offsets, channels, frame_time, data = parse(fetch(trial, folder, lock))
            R, X = world(names, parents, offsets, channels, data)
            leg = float(np.linalg.norm(offsets[names.index("LeftLeg")]) + np.linalg.norm(offsets[names.index("LeftFoot")]))   # thigh and shin
            contact, ankle0 = standing(names, X[1:], leg, 1.0 / frame_time)
            pick = np.round(np.arange(1, len(X) - 1e-6, (1.0 / FPS) / frame_time)).astype(int)
            pick = pick[pick < len(X)]
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            np.savez(cache, names=np.array(names), R=R[pick].astype(np.float32), X=X[pick].astype(np.float32), R0=R[0], X0=X[0],
                     contact=contact[pick - 1], leg=leg, ankle0=ankle0)
        z = np.load(cache)
        self.trial = trial
        self.names = [str(n) for n in z["names"]]
        self.R, self.X, self.R0, self.X0 = z["R"].astype(np.float64), z["X"].astype(np.float64), z["R0"], z["X0"]
        self.contact, self.leg, ankle0 = z["contact"], float(z["leg"]), float(z["ankle0"])
        self.frames = n = len(self.X)
        # Where the body looks: across the line between the hips (and the shoulders), calmed.
        hips = self.P("LeftUpLeg") - self.P("RightUpLeg")
        chest = self.P("LeftArm") - self.P("RightArm")
        ahead = np.stack([hips[:, 1], -hips[:, 0]], axis=1) / np.linalg.norm(hips, axis=1, keepdims=True)
        ahead += 0.5 * np.stack([chest[:, 1], -chest[:, 0]], axis=1) / np.linalg.norm(chest, axis=1, keepdims=True)
        heading = np.unwrap(np.arctan2(*calm(ahead, 15).T[::-1]))
        back = motion.qz(-heading)
        mid = (self.P("LeftUpLeg") + self.P("RightUpLeg")) / 2
        self.root = np.stack([mid[:, 0] / self.leg, mid[:, 1] / self.leg, (mid[:, 2] - ankle0) / self.leg, heading], axis=1)
        self.turns = {}
        for key, joint in (("hips", "Hips"), ("chest", "Spine1"), ("head", "Head")):
            j = self.j(joint)
            q = np.stack([motion.qmatrix(m) for m in self.R[:, j] @ self.R0[j].T])     # from the T pose
            q = motion.qmul(back, q)
            for i in range(1, n):                         # the same turn, written the near way
                if (q[i] * q[i - 1]).sum() < 0:
                    q[i] = -q[i]
            self.turns[key] = q
        limbs = np.stack([motion.unit(self.P(b) - self.P(a)) for a, b in CHAINS], axis=1)
        self.limbs = motion.qrot(back[:, None, :], limbs)
        self.speed = speed(mid[:, :2], FPS) / self.leg
        # Off the ground: how far the hips are above the line from take-off to landing.
        self.lift = np.zeros(n)
        for a, z in runs((self.contact[:, 0] == 0) & (self.contact[:, 1] == 0)):
            if a > 0 and z < n:
                line = np.interp(np.arange(a, z), [a - 1, z], [self.root[a - 1, 2], self.root[z, 2]])
                self.lift[a:z] = np.maximum(0.0, self.root[a:z, 2] - line)

    def j(self, name):
        return self.names.index(name)

    def P(self, name):
        return self.X[:, self.j(name)]


def speed(p, rate):
    v = np.zeros(len(p))
    if len(p) > 1:
        v[1:] = np.linalg.norm(np.diff(p, axis=0), axis=1) * rate
        v[0] = v[1]
    return v


def runs(mask):
    """[from, to) of every run of True."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def calm(v, width):
    if width < 2:
        return v
    k = np.hanning(width + 2)[1:-1]
    k /= k.sum()
    pad = width // 2
    padded = np.concatenate([np.repeat(v[:1], pad, axis=0), v, np.repeat(v[-1:], width - 1 - pad, axis=0)])
    return np.stack([np.convolve(padded[:, i], k, mode="valid") for i in range(v.shape[1])], axis=1)


# ---- cutting a motion out of a recording ---------------------------------------
def apart(rec, a, b):
    """How different the figure is in two frames (0: the same pose and pace)."""
    d = np.linalg.norm(rec.limbs[a] - rec.limbs[b], axis=-1).sum()
    d += 4.0 * abs(rec.root[a, 2] - rec.root[b, 2]) + 2.0 * np.linalg.norm(rec.turns["hips"][a] - rec.turns["hips"][b])
    d += 2.0 * np.linalg.norm(rec.turns["chest"][a] - rec.turns["chest"][b]) + 0.5 * abs(rec.speed[a] - rec.speed[b])
    return float(d)


def stride_of(rec, a, z, rounds):
    """The best round of a gait between two frames: from one touch-down of
    the left foot to a later one where the figure is as it was."""
    ups = runs(rec.contact[:, 0] == 0)
    downs = [y for x, y in ups if (y - x >= 3 or x == 0) and a <= y < z and y < rec.frames]   # after a real swing through the air
    best = None
    for i in range(len(downs) - rounds):
        x, y = downs[i], downs[i + rounds]
        for dy in range(-2, 3):
            if y + dy <= z and (best is None or apart(rec, x, y + dy) < best[0]):
                best = (apart(rec, x, y + dy), x, y + dy)
    if best is None:
        raise SystemExit("%s: no stride between %d and %d" % (rec.trial, a, z))
    return best[1], best[2]


def cut(rec, a, z, loop=False, fade=8):
    """The frames a..z of a recording as a motion that starts at the origin,
    looking along x. A loop gets z's frame as its first again: the last
    frames are blended into what came before the first, a round turns
    nowhere, and one on the spot (`loop` is not "stride" or "travel") ends
    where it began. Returns the arrays and how far a round goes."""
    a, z = max(0, a), min(rec.frames - 1, z)
    idx = np.arange(a, z + 1)
    arrays = {key: rec.turns[key][idx].copy() for key in ("hips", "chest", "head")}
    arrays["limbs"], arrays["contact"], arrays["lift"] = rec.limbs[idx].copy(), rec.contact[idx].copy(), rec.lift[idx].copy()
    root = rec.root[idx].copy()
    stride = None
    if loop:
        n = z - a                                         # frames of a round; frame n is frame 0 again
        k = min(fade, a, n // 3)
        # how the body moves from each frame to the next, seen from itself
        ahead = np.diff(root[:, :2], axis=0)
        c, s = np.cos(-root[:-1, 3]), np.sin(-root[:-1, 3])
        move = np.stack([c * ahead[:, 0] - s * ahead[:, 1], s * ahead[:, 0] + c * ahead[:, 1], np.diff(root[:, 3])], axis=1)
        height = root[:n, 2].copy()
        for key in ("hips", "chest", "head", "limbs", "lift", "contact"):
            arrays[key] = arrays[key][:n]
        if k:
            before = np.arange(a - k, a)                  # what led into the first frame
            w = ((np.arange(k) + 1.0) / (k + 1.0))[:, None]
            w = w * w * (3 - 2 * w)
            tail = slice(n - k, n)
            for key in ("hips", "chest", "head"):
                arrays[key][tail] = motion.qmix(arrays[key][tail], rec.turns[key][before], w)
            arrays["limbs"][tail] = motion.unit(arrays["limbs"][tail] + (rec.limbs[before] - arrays["limbs"][tail]) * w[:, None, :])
            arrays["lift"][tail] += (rec.lift[before] - arrays["lift"][tail]) * w[:, 0]
            height[tail] += (rec.root[before, 2] - height[tail]) * w[:, 0]
            lead = np.diff(rec.root[a - k:a + 1, :2], axis=0)
            c, s = np.cos(-rec.root[before, 3]), np.sin(-rec.root[before, 3])
            lead = np.stack([c * lead[:, 0] - s * lead[:, 1], s * lead[:, 0] + c * lead[:, 1], np.diff(rec.root[a - k:a + 1, 3])], axis=1)
            move[tail] += (lead - move[tail]) * w
        move = move[:n]
        move[:, 2] -= move[:, 2].mean()                   # a round turns nowhere
        travels = loop in ("stride", "travel")
        if not travels:
            move[:, :2] -= move[:, :2].mean(axis=0)       # and one on the spot goes nowhere
        elif abs(math.atan2(move[:, 1].mean(), move[:, 0].mean())) < math.radians(25.0):
            move[:, 1] -= move[:, 1].mean()               # somebody who goes forwards goes straight on
        heading = np.concatenate([[0.0], np.cumsum(move[:, 2])])
        at = np.zeros((n + 1, 2))
        for i in range(n):
            at[i + 1] = at[i] + motion.turn2(heading[i], move[i, :2])
        if not travels:                                   # ... exactly
            at -= np.outer(np.arange(n + 1) / n, at[n])
        root = np.stack([at[:n, 0], at[:n, 1], height, heading[:n]], axis=1)
        stride = [at[n, 0], at[n, 1], 0.0]
    else:
        origin, h0 = root[0, :2].copy(), root[0, 3]
        c, s = math.cos(-h0), math.sin(-h0)
        xy = root[:, :2] - origin
        root = np.stack([c * xy[:, 0] - s * xy[:, 1], s * xy[:, 0] + c * xy[:, 1], root[:, 2], root[:, 3] - h0], axis=1)
    arrays["root"] = root
    return arrays, stride


def hand_of(arrays):
    """The arm a motion is made with: the one that moves much more than the other."""
    way = [float(np.linalg.norm(np.diff(arrays["limbs"][:, k:k + 2], axis=0), axis=-1).sum()) for k in (4, 6)]
    if max(way) < 1.5 or max(way) < 1.8 * min(way):
        return ""
    return "l" if way[0] > way[1] else "r"


def leap_of(arrays):
    """The great leap of a motion: when it leaves the ground, when it lands (seconds) and how high the hips go over the line between."""
    best = None
    for a, z in runs(arrays["lift"] > 0):
        top = float(arrays["lift"][a:z].max())
        if top > 0.05 and (best is None or top > best[2]):
            best = ((a - 0.5) / FPS, (z - 0.5) / FPS, top)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bvh", required=True, help="folder with the BVH files (and their cache)")
    ap.add_argument("--only", nargs="*", help="only these motions (for trying one out; writes nothing)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "motions.npz"))
    args = ap.parse_args()
    spec = json.load(open(os.path.join(HERE, "clips.json")))
    lock_path = os.path.join(HERE, "sources.lock")
    lock = dict(line.split() for line in open(lock_path)) if os.path.exists(lock_path) else {}
    recordings, clips, used = {}, [], set()
    for clip in spec["clips"]:
        if args.only and clip["name"] not in args.only:
            continue
        trial = clip["trial"]
        if trial not in recordings:
            recordings[trial] = Recording(trial, args.bvh, lock)
        rec = recordings[trial]
        used.add(trial)
        a, z = clip.get("frames", [0, rec.frames - 1])
        loop = clip.get("loop", False)
        if loop == "stride":
            a, z = stride_of(rec, a, z, int(clip.get("rounds", 1)))
        arrays, stride = cut(rec, a, z, loop)
        more = {"loop": bool(loop), "stride": stride, "hand": clip.get("hand", hand_of(arrays)), "air": None if loop or clip.get("air") is False else leap_of(arrays)}
        clips.append((clip["name"], arrays, more))
        n = len(arrays["root"])
        print("%-18s %-7s %4d-%-4d %5.2fs%s%s%s" % (
            clip["name"], trial, a, z, n / FPS,
            "  a round goes %.2f leg lengths, %.2f a second" % (np.hypot(*stride[:2]), np.hypot(*stride[:2]) / (n / FPS)) if loop else "",
            "  hand " + more["hand"] if more["hand"] else "", "  leap %.2f-%.2fs %.2f high" % more["air"] if more["air"] else ""))
    if args.only:
        return
    stand = next(arrays for name, arrays, _ in clips if name == spec["neutral"])
    total = motion.pack(clips, motion.unit(stand["limbs"].mean(axis=0)), args.out)
    with open(lock_path, "w") as f:
        for trial in sorted(used, key=lambda t: [int(x) for x in t.split("_")]):
            f.write("%s %s\n" % (trial, lock.get(trial) or hashlib.sha256(open(os.path.join(args.bvh, trial + ".bvh"), "rb").read()).hexdigest()))
    print("%d motions, %d frames (%.1f minutes), %d kB" % (len(clips), total, total / FPS / 60, os.path.getsize(args.out) // 1024))


if __name__ == "__main__":
    main()

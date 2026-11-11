"""MAG scenes: what a figure is, and a skeleton for it.

A generated model is a bare surface without bones, often with a second wall
inside. This module reads its shape as a solid: where it stands (its feet),
where its ends are (head, tail), whether it walks on four legs, on two, or
is a thing, and where its limbs join the body. From that it makes a skeleton
and says, for every vertex, which bones it follows. Only numpy and the
standard library: the same code runs inside Blender and in the tests.

The solid is a grid of small cubes. A leg is what rises from a foot until
it meets another leg or the body. A tail or a head is found from its tip:
the cubes at one distance from the tip are a slice through the limb, and
where the slice suddenly widens, the limb has reached the body.
"""
import heapq
import math

import numpy as np

CELLS = 44            # cubes along the longest side of the figure
NEAR = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]
FLAT = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
KINDS = {"quadruped": "Vierbeiner", "biped": "Zweibeiner", "bird": "Vogel", "many": "Vielbeiner (Insekt, Spinne)", "serpent": "Schlange oder Fisch",
         "vehicle": "Fahrzeug", "object": "Gegenstand"}
SEGMENTS = 8           # the bones of a body that is long and has no legs


class Solid:
    """The figure as cubes: its surface and everything it encloses."""

    def __init__(self, co, edges=None):
        lo = co.min(axis=0)
        self.lo, self.hi = lo, co.max(axis=0)
        self.step = float((self.hi - lo).max()) / CELLS or 1.0
        self.origin = lo - 1.5 * self.step            # the centre of cube (0, 0, 0)
        points = co if edges is None or not len(edges) else np.concatenate([co, (co[edges[:, 0]] + co[edges[:, 1]]) / 2])
        cells = np.rint((points - self.origin) / self.step).astype(np.int64)
        self.shape = tuple((cells.max(axis=0) + 3).tolist())
        shell = np.zeros(self.shape, dtype=bool)
        shell[cells[:, 0], cells[:, 1], cells[:, 2]] = True
        # What the air around the figure cannot reach is the figure.
        air = np.zeros(self.shape, dtype=bool)
        rim = np.zeros(self.shape, dtype=bool)
        rim[0, :, :] = rim[-1, :, :] = rim[:, 0, :] = rim[:, -1, :] = rim[:, :, 0] = rim[:, :, -1] = True
        air |= rim
        while True:
            grown = air.copy()
            for axis in range(3):
                for way in (-1, 1):
                    grown |= np.roll(air, way, axis=axis)
            grown &= ~shell
            grown |= rim
            if (grown == air).all():
                break
            air = grown
        self.solid = ~air
        self.cell_of = cells[:len(co)]                # the cube of every vertex
        self.cubes = {tuple(c) for c in np.argwhere(self.solid).tolist()}
        self.ground = min(c[2] for c in self.cubes)   # the lowest layer of cubes

    def where(self, cells):
        """The place of cubes in space."""
        return self.origin + np.asarray(cells, dtype=float) * self.step

    def far(self, seed):
        """How far the cubes of the figure are from one of them, going through the figure."""
        weights = [(o, self.step * math.sqrt(o[0] ** 2 + o[1] ** 2 + o[2] ** 2)) for o in NEAR]
        dist = {seed: 0.0}
        heap = [(0.0, seed)]
        cubes = self.cubes
        while heap:
            d, cell = heapq.heappop(heap)
            if d > dist[cell]:
                continue
            for (dx, dy, dz), w in weights:
                other = (cell[0] + dx, cell[1] + dy, cell[2] + dz)
                if other in cubes:
                    nd = d + w
                    if nd < dist.get(other, math.inf):
                        dist[other] = nd
                        heapq.heappush(heap, (nd, other))
        return dist

    def furthest(self, seed):
        dist = self.far(seed)
        return max(dist, key=dist.get), dist

    def slices(self, tip):
        """Cuts the figure into slices of equal distance from a tip and follows
        the slice the tip is in. Returns the slices, the tip's first, each with
        its centre, its width and its cubes."""
        dist = self.far(tip)
        thick = 1.5 * self.step
        bands = {}
        for cell, d in dist.items():
            bands.setdefault(int(d / thick), []).append(cell)
        out, last = [], None
        for k in range(max(bands) + 1):
            members = set(bands.get(k, ()))
            if last is None:
                found = members
            else:
                # Only what continues the slice before: another limb at the same distance is not this one.
                stack = [c for c in members if any((c[0] + o[0], c[1] + o[1], c[2] + o[2]) in last for o in NEAR)]
                found = set(stack)
                while stack:
                    c = stack.pop()
                    for o in NEAR:
                        other = (c[0] + o[0], c[1] + o[1], c[2] + o[2])
                        if other in members and other not in found:
                            found.add(other)
                            stack.append(other)
            if not found:
                break
            cells = np.array(sorted(found))
            points = self.where(cells)
            centre = points.mean(axis=0)
            out.append({"centre": centre, "width": float(np.sqrt(((points - centre) ** 2).sum(axis=1).mean())), "cells": cells})
            last = found
        return out

    def layer(self, z):
        """The connected patches of the figure in one layer, as sets of (x, y)."""
        todo = {(c[0], c[1]) for c in self.cubes if c[2] == z}
        out = []
        while todo:
            start = todo.pop()
            patch, stack = {start}, [start]
            while stack:
                x, y = stack.pop()
                for dx, dy in FLAT:
                    other = (x + dx, y + dy)
                    if other in todo:
                        todo.discard(other)
                        patch.add(other)
                        stack.append(other)
            out.append(patch)
        return out

    def legs(self):
        """What the figure stands on. Every patch of the ground layers is a
        foot; a leg is followed upwards from it, layer by layer, until it
        meets another leg or widens into the body. Returns for every leg its
        levels: (z, centre x, centre y, cubes of the level)."""
        top = max(c[2] for c in self.cubes)
        low = self.ground + max(1, int(round(0.07 * (top - self.ground))))
        feet = []
        flat = {}
        for c in self.cubes:
            if c[2] <= low:
                flat.setdefault((c[0], c[1]), []).append(c)
        todo = set(flat)
        while todo:
            start = todo.pop()
            patch, stack = {start}, [start]
            while stack:
                x, y = stack.pop()
                for dx, dy in FLAT:
                    other = (x + dx, y + dy)
                    if other in todo:
                        todo.discard(other)
                        patch.add(other)
                        stack.append(other)
            feet.append(patch)
        if not feet or len(feet) > 8:
            return []
        legs = [{"levels": [], "patch": patch, "thin": len(patch), "open": True} for patch in feet]
        for leg in legs:
            cubes = [c for xy in leg["patch"] for c in flat[xy]]
            leg["foot"] = self.where(cubes).mean(axis=0)
            leg["foot"][2] = self.origin[2] + (self.ground - 0.5) * self.step
        for z in range(low + 1, top + 1):
            if not any(leg["open"] for leg in legs):
                break
            for patch in self.layer(z):
                grown = {(x + dx, y + dy) for x, y in patch for dx, dy in FLAT} | patch
                under = [leg for leg in legs if leg["open"] and grown & leg["patch"]]
                if len(under) > 1:                    # legs meet: the body begins
                    for leg in under:
                        leg["open"] = False
                    continue
                if not under:
                    continue
                leg = under[0]
                leg.setdefault("next", set()).update(patch)
            for leg in legs:
                if not leg["open"]:
                    continue
                patch = leg.pop("next", None)
                # A leg that widens to several times its thinnest has reached the body.
                if not patch or (len(patch) > 2.6 * leg["thin"] and len(leg["levels"]) >= 2) or len(patch) > 4.5 * leg["thin"]:
                    leg["open"] = False
                    continue
                leg["thin"] = min(leg["thin"], len(patch))
                xy = np.array(sorted(patch), dtype=float)
                centre = self.origin[:2] + xy.mean(axis=0) * self.step
                leg["levels"].append((z, float(centre[0]), float(centre[1]), [(x, y, z) for x, y in patch]))
                leg["patch"] = patch
        for leg in legs:
            leg["low"] = [c for xy in feet[legs.index(leg)] for c in flat[xy]]
        return legs


def joins_at(slices, step):
    """The slice at which a limb has reached the body: it is much wider than
    the limb was at its thin part. None: it never is (this is no limb)."""
    if len(slices) < 5:
        return None
    thin = max(0.6 * step, float(np.median([s["width"] for s in slices[1:4]])))
    for k in range(3, len(slices)):
        if slices[k]["width"] > 1.75 * thin:
            return k
    return None


def serpent(co, height):
    """A body that is long, low and has no legs (a snake, a fish, a worm, an
    eel): a chain of bones along its length, each owning the slice of the
    body around it, so it can bend in a wave. None when the body is not so."""
    flat = co[:, :2] - co[:, :2].mean(axis=0)
    _, _, vt = np.linalg.svd(flat[:: max(1, len(flat) // 20000)], full_matrices=False)
    along = vt[0]
    t = flat @ along
    across = flat @ vt[1]
    length, width = float(np.ptp(t)), float(np.ptp(across))
    if length < 2.2 * height or length < 1.8 * width:
        return None
    edges = np.linspace(t.min(), t.max(), SEGMENTS + 1)
    middle = []
    for i in range(SEGMENTS + 1):
        near = np.abs(t - edges[i]) <= 0.6 * (edges[1] - edges[0])
        pick = co[near] if near.any() else co
        middle.append(np.array([*(co[:, :2].mean(axis=0) + along * edges[i]), float(np.median(pick[:, 2]))]))
    bones = [{"name": "Glied %d" % (i + 1), "head": middle[i].tolist(), "tail": middle[i + 1].tolist(), "parent": i - 1, "role": "spine"}
             for i in range(SEGMENTS)]
    labels = np.clip(((t - t.min()) / max(length, 1e-9) * SEGMENTS).astype(np.int64), 0, SEGMENTS - 1)
    return {"kind": "serpent", "bones": bones, "labels": labels, "front": None, "sure": False,
            "notes": ["Schlange oder Fisch: lang, ohne Beine, %d Glieder" % SEGMENTS]}


def segment_distance(points, a, b):
    """How far points are from the stretch a-b."""
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(1e-12, float(ab @ ab)), 0.0, 1.0)
    return np.linalg.norm(points - (a + t[:, None] * ab), axis=1)


def analyse(co, edges=None, axis=None, kind=None):
    """Reads a figure. Returns
        kind    "quadruped", "biped", "serpent" (long, no legs) or "object"
        bones   [{name, head, tail, parent, role}], parents first (empty for an object)
        labels  for every vertex the index of the bone it belongs to
        front   the direction it looks in on the ground, or None
        notes   what was found, in words
    `axis`: the direction the figure is long in on the ground (its sign does
    not matter). `kind`: what the caller knows it to be ("object": no
    skeleton, "quadruped": an animal even if it sits upright)."""
    co = np.asarray(co, dtype=float)
    plain = {"kind": "object", "bones": [], "labels": np.zeros(len(co), dtype=np.int64), "front": None, "sure": False, "notes": []}
    if len(co) < 50 or kind == "object":
        return plain
    body = Solid(co, edges)
    step = body.step
    size = body.hi - body.lo
    height, length = float(size[2]), float(max(size[0], size[1]))
    legs = [leg for leg in body.legs() if len(leg["levels"]) >= 2 and (leg["levels"][-1][0] - body.ground + 1) * step >= 0.12 * height]
    # Two legs walk like a person or a bird, three or four like an animal,
    # unless the figure is taller than long: four feet under something
    # upright are two people, or a chair (the caller may know better).
    upright = height > 1.15 * length
    if len(legs) == 2:
        found = "biped"
    elif 3 <= len(legs) <= 4 and (not upright or kind == "quadruped"):
        found = "quadruped"
    elif 5 <= len(legs) <= 10 and not upright:           # an insect, a spider, a centipede: legs along a long body
        found = "many"
    else:
        if len(legs) <= 1 and kind != "object":                # (one "leg" under a long body is its lowest bend)
            long = serpent(co, height)
            if long is not None:
                return long
        plain["notes"].append("kein Skelett: %s" % ("keine Beine zu erkennen" if not legs else "%d Beine" % len(legs)))
        return plain

    cells = np.array(sorted(body.cubes))
    points = body.where(cells)
    index = {tuple(c): i for i, c in enumerate(cells.tolist())}
    owner = np.full(len(cells), -1)                   # the bone of every cube
    bones = []

    def bone(name, head, tail, parent, role):
        tail = np.asarray(tail, dtype=float)
        head = np.asarray(head, dtype=float)
        if np.linalg.norm(tail - head) < 0.5 * step:  # a bone needs a length
            tail = head + np.array([0.0, 0.0, 0.5 * step])
        bones.append({"name": name, "head": head, "tail": tail, "parent": parent, "role": role})
        return len(bones) - 1

    def claim(cubes, which):
        for c in cubes:
            i = index.get(tuple(c))
            if i is not None:
                owner[i] = which

    def joints(leg, hip_z):
        """Hip, knee and foot of a leg whose hip is at this height: the leg
        is followed as far as it is a leg and goes straight up from there."""
        levels = leg["levels"]
        leg["hip"] = np.array([levels[-1][1], levels[-1][2], max(hip_z, body.origin[2] + (levels[-1][0] + 1.0) * step)])
        knee_z = (leg["hip"][2] + leg["foot"][2]) / 2
        knee = min(levels, key=lambda level: abs(body.origin[2] + level[0] * step - knee_z))
        leg["knee"] = np.array([knee[1], knee[2], min(knee_z, body.origin[2] + knee[0] * step)])
        leg["knee_z"] = int(round((leg["knee"][2] - body.origin[2]) / step))
        leg["length"] = float(leg["hip"][2] - leg["foot"][2])
        # The toes are in front of the ankle: where the foot points on the ground.
        low = body.where(leg["low"])[:, :2].mean(axis=0)
        leg["toes"] = low - np.array([levels[0][1], levels[0][2]])

    if found in ("quadruped", "many"):
        # Along the body: the direction the feet are spread in.
        feet = np.array([leg["foot"][:2] for leg in legs])
        if axis is None:
            spread = feet - feet.mean(axis=0)
            axis = np.linalg.eigh(spread.T @ spread)[1][:, 1]
        axis = np.asarray(axis, dtype=float)[:2]
        axis = axis / (np.linalg.norm(axis) or 1.0)
        along = points[:, :2] @ axis
        ends = [tuple(cells[int(along.argmax())]), tuple(cells[int(along.argmin())])]
        limbs = [body.slices(end) for end in ends]
        joins = [joins_at(limb, step) for limb in limbs]
        # The tail is a long thin end, the head a short thick one; failing that, the head is the higher end.
        thin = [j if j is not None else 0 for j in joins]
        high = [float(points[along > along.max() - 0.2 * (along.max() - along.min()), 2].max()),
                float(points[along < along.min() + 0.2 * (along.max() - along.min()), 2].max())]
        if thin[0] >= 7 and thin[0] >= 1.6 * max(1, thin[1]):
            head_end = 1
        elif thin[1] >= 7 and thin[1] >= 1.6 * max(1, thin[0]):
            head_end = 0
        else:
            head_end = 0 if high[0] >= high[1] else 1
        if head_end == 1:
            axis, along = -axis, -along
        head_limb, tail_limb = limbs[head_end], limbs[1 - head_end]
        tail_join = joins[1 - head_end]
        side = np.array([-axis[1], axis[0]])          # to the left of the way it looks
        order = sorted(legs, key=lambda leg: float(np.array(leg["levels"][-1][1:3]) @ axis))
        hind, fore = order[:len(order) // 2], order[len(order) // 2:]

        a_hind = float(np.mean([np.array(leg["levels"][-1][1:3]) @ axis for leg in hind]))
        a_fore = float(np.mean([np.array(leg["levels"][-1][1:3]) @ axis for leg in fore]))
        # Between the legs the body is only body: its underside there is the
        # belly. The legs turn in the body a little above it.
        between = np.abs(along - (a_hind + a_fore) / 2) <= max(1.5 * step, 0.15 * abs(a_fore - a_hind))
        belly, back = float(points[between, 2].min()), float(points[between, 2].max())
        hip_z = belly + 0.3 * (back - belly)
        for leg in legs:
            joints(leg, hip_z)

        def torso_at(a, reach=1.5):
            """The middle of the body where it is `a` along, above the belly."""
            pick = (np.abs(along - a) <= reach * step) & (points[:, 2] >= belly)
            if not pick.any():
                pick = np.abs(along - a) <= reach * step
            return points[pick].mean(axis=0) if pick.any() else points.mean(axis=0)

        hips, chest, mid = torso_at(a_hind), torso_at(a_fore), torso_at((a_hind + a_fore) / 2)
        nose = head_limb[0]["centre"]
        # The head begins where the muzzle has widened into the skull, at the latest halfway to the shoulders.
        a_nose = float(nose[:2] @ axis)
        skull = joins_at(head_limb, step) or 5
        while skull > 2 and float(head_limb[skull]["centre"][:2] @ axis) < a_fore + 0.45 * (a_nose - a_fore):
            skull -= 1
        neck_end = head_limb[skull]["centre"]
        thighs = []
        pelvis = bone("Becken", hips, mid, -1, "hips")
        ribs = bone("Brust", mid, chest, pelvis, "chest")
        neck = bone("Hals", chest, neck_end, ribs, "neck")
        skull_bone = bone("Kopf", neck_end, nose, neck, "head")
        for k in range(skull + 1):
            claim(head_limb[k]["cells"], skull_bone)
        if tail_join is not None and tail_join >= 4:
            root = tail_limb[tail_join - 1]["centre"]
            half = tail_limb[tail_join // 2]["centre"]
            first = bone("Schwanz 1", root, half, pelvis, "tail")
            second = bone("Schwanz 2", half, tail_limb[0]["centre"], first, "tail")
            for k in range(tail_join):
                claim(tail_limb[k]["cells"], second if k < tail_join // 2 else first)
        middle = float(np.mean([l["hip"][:2] @ side for l in legs]))
        for group, parent, where in ((hind, pelvis, "hinten"), (fore, ribs, "vorn")):
            for leg in group:
                left = float(leg["hip"][:2] @ side) >= (middle if found == "many" else float(np.mean([l["hip"][:2] @ side for l in group])))
                name = "Bein %s %s" % (where, "links" if left else "rechts")
                if any(b["name"] == name + " oben" for b in bones):
                    name += " 2"
                upper = bone(name + " oben", leg["hip"], leg["knee"], parent, "leg")
                lower = bone(name + " unten", leg["knee"], leg["foot"], upper, "shin")
                # Its place along the body among the legs of its side, from the back (the order a gait goes through them).
                rank = sum(1 for l in legs if (float(l["hip"][:2] @ side) >= middle) == left and float(l["hip"][:2] @ axis) < float(leg["hip"][:2] @ axis))
                bones[upper].update(fore=where == "vorn", left=left, length=leg["length"], rank=rank)
                claim(leg["low"], lower)
                for z, _, _, cubes in leg["levels"]:
                    claim(cubes, lower if z <= leg["knee_z"] else upper)
                thighs.append(upper)
        front = axis
        trunk = [pelvis, ribs, neck]
        if found == "many":
            notes = ["Vielbeiner: %d Beine, Kopf" % len(legs)]
        else:
            notes = ["Vierbeiner: %d Beine, Kopf%s" % (len(legs), " und Schwanz" if any(b["role"] == "tail" for b in bones) else "")]
    else:
        # On two legs: the body rises from between the hips to the top of the head.
        top = points[int(points[:, 2].argmax())]
        for leg in legs:
            joints(leg, 0.0)
        thighs = []
        hip_z = float(np.mean([leg["hip"][2] for leg in legs]))
        layers = {}
        for i, c in enumerate(cells.tolist()):
            layers.setdefault(c[2], []).append(i)

        def centre_at(z):
            layer = int(round((z - body.origin[2]) / step))
            for reach in range(0, 4):
                pick = [i for dz in range(-reach, reach + 1) for i in layers.get(layer + dz, ())]
                if pick:
                    return points[pick].mean(axis=0)
            return points.mean(axis=0)

        # The neck is where the figure is narrowest between chest and head; a figure without one has its head on top.
        z_lo, z_hi = hip_z + 0.5 * (top[2] - hip_z), hip_z + 0.85 * (top[2] - hip_z)
        areas = {z: len(layers.get(z, ())) for z in range(int(round((z_lo - body.origin[2]) / step)), int(round((z_hi - body.origin[2]) / step)) + 1)}
        neck_layer = min(areas, key=areas.get) if areas else None
        above = max((len(layers.get(z, ())) for z in range(neck_layer + 1, int(round((top[2] - body.origin[2]) / step)) + 1)), default=0) if neck_layer is not None else 0
        if neck_layer is not None and above >= 1.25 * areas[neck_layer]:
            neck_z = body.origin[2] + neck_layer * step
        else:
            neck_z = hip_z + 0.68 * (top[2] - hip_z)
        hips = centre_at(hip_z + step)
        hips[2] = hip_z + step
        waist = centre_at(hip_z + 0.5 * (neck_z - hip_z))
        neck_at = centre_at(neck_z)
        neck_at[2] = neck_z
        crown = np.array([neck_at[0], neck_at[1], top[2]]) * 0.5 + top * 0.5
        pelvis = bone("Becken", hips, waist, -1, "hips")
        ribs = bone("Brust", waist, neck_at, pelvis, "chest")
        skull_bone = bone("Kopf", neck_at, crown, ribs, "head")
        # It looks across the line between its hips, the way its toes point.
        # Where the toes do not say, the caller decides which way of the two.
        apart = legs[0]["hip"][:2] - legs[1]["hip"][:2]
        front, sure = None, False
        if np.linalg.norm(apart) > step:
            front = np.array([-apart[1], apart[0]]) / np.linalg.norm(apart)
            toes = float(sum(leg["toes"] @ front for leg in legs))
            sure = abs(toes) > 1.2 * step
            if toes < 0:
                front = -front
        side = np.array([1.0, 0.0]) if front is None else np.array([-front[1], front[0]])   # to its left
        middle = np.mean([leg["hip"][:2] for leg in legs], axis=0)
        for leg in sorted(legs, key=lambda leg: -float((leg["hip"][:2] - middle) @ side)):
            left = not any(b["role"] == "leg" for b in bones)
            name = "Bein links" if left else "Bein rechts"
            upper = bone(name + " oben", leg["hip"], leg["knee"], pelvis, "leg")
            lower = bone(name + " unten", leg["knee"], leg["foot"], upper, "shin")
            bones[upper].update(fore=False, left=left, length=leg["length"])
            claim(leg["low"], lower)
            for z, _, _, cubes in leg["levels"]:
                claim(cubes, lower if z <= leg["knee_z"] else upper)
        # Arms: thin limbs that end in a tip, away from the middle of the body,
        # and join it above the hips. An arm that lies against the body is body.
        taken = {tuple(c) for c in cells[owner >= 0].tolist()}
        field = body.far(tuple(cells[int(np.linalg.norm(points - waist, axis=1).argmin())]))
        tips = []
        for cell, d in field.items():
            z = body.origin[2] + cell[2] * step
            if cell in taken or z < hip_z or z > neck_z + 0.35 * (top[2] - neck_z):
                continue
            if any(field.get((cell[0] + o[0], cell[1] + o[1], cell[2] + o[2]), -1.0) > d for o in NEAR):
                continue
            if all(field.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), -1.0) <= d for dx in (-2, 2) for dy in (-2, 0, 2) for dz in (-2, 0, 2)):
                tips.append((d, cell))
        arms = 0
        for _, tip in sorted(tips, reverse=True)[:8]:
            limb = body.slices(tip)
            join = joins_at(limb, step)
            if join is None or join < 6:              # a bump, not a limb
                continue
            cubes = {tuple(c) for part in limb[:join] for c in part["cells"].tolist()}
            root = limb[join - 1]["centre"]
            if cubes & taken or root[2] < hip_z + 0.4 * (neck_z - hip_z):
                continue
            taken |= cubes
            spine = centre_at(root[2])
            out = root[:2] - spine[:2]
            out = out / (np.linalg.norm(out) or 1.0)
            left = not any(b["role"] == "arm" and b["left"] for b in bones) and float(out @ side) >= 0
            name = "Arm links" if left else "Arm rechts"
            if any(b["name"] == name + " oben" for b in bones):
                name = "Arm %d" % (arms + 1)
            shoulder = root + 0.35 * (np.array([spine[0], spine[1], root[2]]) - root)
            elbow, hand = limb[join // 2]["centre"], limb[0]["centre"]
            # The hand is the last part of the forearm: about a fifth of the arm, from the wrist to the tip.
            palm = max(1, int(round(0.2 * join)))
            wrist = limb[palm]["centre"] if palm < join // 2 else hand
            upper = bone(name + " oben", shoulder, elbow, ribs, "arm")
            lower = bone(name + " unten", elbow, wrist, upper, "forearm")
            bones[upper].update(left=left, out=[float(out[0]), float(out[1])], reach=(hand - shoulder).tolist())
            fist = bone(name.replace("Arm", "Hand"), wrist, hand, lower, "hand") if palm < join // 2 else None
            if fist is not None:
                bones[fist].update(left=left)
            for k in range(join):
                claim(limb[k]["cells"], fist if fist is not None and k < palm else lower if k < join // 2 else upper)
            arms += 1
            if arms == 2:
                break
        # A head sits right above the shoulders. A figure whose head and hat
        # (or beard) narrow without a neck would otherwise have its face on the
        # chest and only its hat on the head, and the hat bends when it looks.
        shoulders = [bones[b]["head"][2] for b in range(len(bones)) if bones[b]["role"] == "arm"]
        if shoulders and neck_z > max(shoulders) + 0.25 * (top[2] - max(shoulders)):
            neck_z = max(shoulders) + 0.25 * (top[2] - max(shoulders))
            neck_at = centre_at(neck_z)
            neck_at[2] = neck_z
            bones[ribs]["tail"] = neck_at
            bones[skull_bone]["head"] = neck_at
        trunk = [pelvis, ribs, skull_bone]
        notes = ["Zweibeiner: 2 Beine, Rumpf, Kopf" + (" und %d Arm%s" % (arms, "e" if arms > 1 else "") if arms else "; Arme liegen am Körper an und bewegen sich mit ihm")]
        # On two legs with a body that reaches far ahead and behind them, about as long as
        # it is tall (or the caller knows): a bird. What sticks out at its sides are wings.
        # Somebody who sits, or holds something out in front, is not as deep as that.
        deep = float(np.ptp(points[:, :2] @ front)) if front is not None else float(min(size[0], size[1]))
        if kind == "bird" or (kind != "biped" and deep > 0.9 * height):
            found = "bird"
            for b in bones:
                if b["role"] in ("arm", "forearm"):
                    b["role"] = "wing" if b["role"] == "arm" else "wingtip"
                    b["name"] = b["name"].replace("Arm", "Flügel")
                elif b["role"] == "hand":
                    b["role"], b["name"] = "wingtip", b["name"].replace("Hand", "Flügelspitze")
            notes = ["Vogel: 2 Beine, Körper, Kopf" + (" und %d Flügel" % arms if arms else "; Flügel liegen am Körper an")]

    # What is no limb belongs to the nearest stretch of the trunk, or, below
    # the hips and next to a leg, to the thigh that reaches up into the body.
    free = np.flatnonzero(owner < 0)
    if len(free):
        choice = list(trunk) + thighs
        gaps = np.stack([segment_distance(points[free], bones[b]["head"], bones[b]["tail"]) for b in choice], axis=1)
        for n, b in enumerate(choice):
            if b in thighs:
                gaps[points[free, 2] > bones[b]["head"][2], n] = np.inf
        owner[free] = np.array(choice)[gaps.argmin(axis=1)]
    grid = np.full(body.shape, -1, dtype=np.int64)
    grid[cells[:, 0], cells[:, 1], cells[:, 2]] = owner
    labels = grid[body.cell_of[:, 0], body.cell_of[:, 1], body.cell_of[:, 2]]
    labels[labels < 0] = trunk[0]
    return {"kind": found, "bones": bones, "labels": labels, "front": None if front is None else [float(front[0]), float(front[1])],
            "sure": found in ("quadruped", "many") or sure, "notes": notes}


def blend(labels, count, edges, rounds=12):
    """Every vertex follows its bone; near a joint it follows both. The
    borders between the bones are softened along the surface. Returns for
    every vertex up to four bones and their weights."""
    n = len(labels)
    weights = np.zeros((n, count))
    weights[np.arange(n), labels] = 1.0
    if edges is not None and len(edges):
        src = np.concatenate([edges[:, 0], edges[:, 1]])
        dst = np.concatenate([edges[:, 1], edges[:, 0]])
        order = np.argsort(src, kind="stable")
        src, dst = src[order], dst[order]
        degree = np.bincount(src, minlength=n)
        has = degree > 0
        starts = (np.cumsum(degree) - degree)[has]
        for _ in range(rounds):
            around = np.add.reduceat(weights[dst], starts, axis=0)
            weights[has] = (weights[has] + around) / (1.0 + degree[has])[:, None]
    top = np.argsort(-weights, axis=1)[:, :4]
    picked = np.take_along_axis(weights, top, axis=1)
    picked[picked < 0.02] = 0.0
    picked /= picked.sum(axis=1, keepdims=True)
    return top, picked

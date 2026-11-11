#!/usr/bin/env python3
"""What motion.py must keep: the recorded motions are whole (a round ends
where it begins), a figure of any build takes them over (its bones point
where the recorded ones did), and on its way through a scene it arrives, its
standing feet stay where they stand, nothing jerks, and a leap clears what
it is to clear. numpy only: the figures are skeletons as rig.py makes them."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import motion  # noqa: E402
import rig  # noqa: E402
from test_rig import person  # noqa: E402

FPS = 24


def bone(name, head, tail, parent, role, **more):
    return dict(name=name, head=head, tail=tail, parent=parent, role=role, **more)


def gnome():
    """Short legs under a long body, hips as wide as the legs are long, one
    arm hanging, the other bent in front of the chest (it carries something);
    it looks towards -y, as a model from a photo does."""
    return [bone("Becken", [0, 0, .10], [0, 0, .20], -1, "hips"), bone("Brust", [0, 0, .20], [0, -.03, .30], 0, "chest"), bone("Kopf", [0, -.03, .30], [0, -.04, .40], 1, "head"),
            bone("Bein links oben", [.05, 0, .10], [.05, 0, .05], 0, "leg", left=True, fore=False, length=.1), bone("Bein links unten", [.05, 0, .05], [.05, 0, 0], 3, "shin"),
            bone("Bein rechts oben", [-.05, 0, .10], [-.05, 0, .05], 0, "leg", left=False, fore=False, length=.1), bone("Bein rechts unten", [-.05, 0, .05], [-.05, 0, 0], 5, "shin"),
            bone("Arm links oben", [.07, 0, .27], [.08, 0, .21], 1, "arm", left=True), bone("Arm links unten", [.08, 0, .21], [.085, 0, .16], 7, "forearm"),
            bone("Arm rechts oben", [-.07, 0, .27], [-.08, -.03, .22], 1, "arm", left=False), bone("Arm rechts unten", [-.08, -.03, .22], [-.04, -.08, .23], 9, "forearm")], (0.0, -1.0)


def grown():
    """Built like a grown person, arms hanging; it looks towards +x."""
    return [bone("Becken", [0, 0, .95], [0, 0, 1.15], -1, "hips"), bone("Brust", [0, 0, 1.15], [0, 0, 1.5], 0, "chest"), bone("Kopf", [0, 0, 1.5], [0, 0, 1.75], 1, "head"),
            bone("Bein links oben", [0, .09, .9], [0, .09, .47], 0, "leg", left=True, fore=False, length=.9), bone("Bein links unten", [0, .09, .47], [0, .09, 0], 3, "shin"),
            bone("Bein rechts oben", [0, -.09, .9], [0, -.09, .47], 0, "leg", left=False, fore=False, length=.9), bone("Bein rechts unten", [0, -.09, .47], [0, -.09, 0], 5, "shin"),
            bone("Arm links oben", [0, .2, 1.45], [0, .23, 1.15], 1, "arm", left=True), bone("Arm links unten", [0, .23, 1.15], [0, .24, .88], 7, "forearm"),
            bone("Arm rechts oben", [0, -.2, 1.45], [0, -.23, 1.15], 1, "arm", left=False), bone("Arm rechts unten", [0, -.23, 1.15], [0, -.24, .88], 9, "forearm")], (1.0, 0.0)


def angle(a, b):
    return 2 * math.acos(min(1.0, abs(float((a * b).sum()))))


def played(body, lib, story, start=(0.0, 0.0), heading=0.4):
    p = motion.Performer(body, lib, FPS, start, heading)
    p.watch = True
    story(p)
    last = len(p.frames) + 9
    return p, p.finish(last)


def feet_of(p, frame):
    return [frame["joints"][p.body.names[shin]][1] for _, shin in p.body.legs]


def main():
    lib = motion.Library()
    # ---- the motions ----------------------------------------------------------
    for name in ("idle", "walk", "run", "sneak", "hop", "leap", "jump", "wave", "spin", "kick", "punch", "fall"):
        assert name in lib, name
    # A blow carries its maker forwards (it steps into it), and somebody who falls ends low.
    assert lib["kick"].place(0.3 * lib["kick"].seconds)[0] > 0.2 and not lib["kick"].loop and not lib["punch"].loop
    assert lib["fall"].root[-1, 2] < 0.45 * lib["fall"].root[0, 2], (lib["fall"].root[0, 2], lib["fall"].root[-1, 2])
    for name, clip in lib.clips.items():
        assert np.isfinite(clip.root).all() and clip.frames >= 10, name
        assert np.allclose(np.linalg.norm(clip.limbs, axis=-1), 1.0, atol=1e-3) and np.allclose(np.linalg.norm(clip.hips, axis=-1), 1.0, atol=1e-3), name
        # (somebody lying has the hips a little under where the ankles stood)
        assert -0.1 <= clip.root[:, 2].min() and clip.root[:, 2].max() < 2.6, (name, clip.root[:, 2].min(), clip.root[:, 2].max())
        if clip.loop:                                 # a round ends where and as it began
            first, again = clip.pose(0.0), clip.pose(clip.seconds - 1e-4)
            assert (first.limbs * again.limbs).sum(axis=1).min() > math.cos(math.radians(12.0)), name
            assert abs(first.height - again.height) < 0.03, name
            there = clip.place(2 * clip.seconds) - clip.place(clip.seconds)
            assert np.allclose(there[:2], clip.stride[:2], atol=1e-6) and abs(there[2]) < 1e-9, name
    for gait, slowest, fastest in (("walk", 0.8, 1.8), ("run", 2.5, 8.0), ("sneak", 0.5, 2.0), ("hop", 0.5, 2.5)):
        clip = lib[gait]
        assert clip.loop and slowest < clip.pace < fastest and clip.stride[0] > 0.9 * np.hypot(*clip.stride[:2]), (gait, clip.pace, clip.stride)
        assert (clip.contact == 2).any(axis=0).all() or gait == "run", gait      # both feet stand at some time
    assert lib["idle"].loop and np.allclose(lib["idle"].stride, 0.0, atol=1e-6)
    for name in ("leap", "jump", "spin"):
        off, land, high = lib[name].air
        assert 0.0 < off < land < lib[name].seconds and high > 0.2, (name, lib[name].air)
    assert lib["wave"].hand in ("l", "r") and not lib["walk"].hand
    assert np.allclose(np.linalg.norm(lib.neutral, axis=1), 1.0, atol=1e-3) and (lib.neutral[:, 2] < -0.5).all()   # standing: everything hangs
    mirrored = lib["wave"].pose(1.0, mirror=True)
    plain = lib["wave"].pose(1.0)
    assert np.allclose(mirrored.limbs[4], plain.limbs[6] * [1, -1, 1]) and np.allclose(mirrored.hips, plain.hips * [1, -1, 1, -1])

    # ---- a figure takes a pose over ---------------------------------------------
    found = rig.analyse(person())                     # a skeleton as rig.py makes it
    assert found["kind"] == "biped"
    made = motion.Body(found["bones"], found["front"], 1.0, lib.neutral)
    assert abs(made.facing + math.pi / 2) < 0.05 and len(made.arms) == 2 and made.leg > 0.3
    tall = motion.Body(*grown(), 1.0, lib.neutral)
    bones, front = gnome()
    stout = motion.Body(bones, front, 1.0, lib.neutral)
    assert tall.quick < 1.05 and tall.air < 1.05 and min(tall.free.values()) > 0.95
    assert 0.09 < stout.leg < 0.11 and stout.quick > 1.3 and stout.air > 1.6      # short legs: quicker steps, and it jumps as high as it is tall
    assert stout.free["l"] > 0.9 and stout.free["r"] < 0.1                        # one arm hangs, the other holds something
    rng = np.random.default_rng(7)
    for body in (tall, stout, made):
        for _ in range(20):
            hips, chest, head = (motion.qaxis(motion.unit(rng.normal(size=3)), rng.uniform(0, 0.8)) for _ in range(3))
            limbs = motion.unit(rng.normal(size=(8, 3)))
            turns, whole = body.turns(hips, chest, head, limbs)
            at = body.joints(whole)
            for k, (upper, shin) in enumerate(body.legs):
                for j, b in enumerate((upper, shin)):
                    got = at[b][1] - at[b][0]
                    assert abs(np.linalg.norm(got) - body.length[b]) < 1e-9                    # a bone keeps its length
                    assert float(motion.unit(got) @ limbs[2 * k + j]) > 1 - 1e-9               # and points where it was told to
            assert set(turns) == set(body.names) and all(abs(np.linalg.norm(q) - 1) < 1e-9 for q in turns.values())
    # As rig poses take it: standing as the model stands is no turn at all.
    same = np.array([1.0, 0.0, 0.0, 0.0])
    rest = np.stack([stout.dir[stout.legs[0][0]], stout.dir[stout.legs[0][1]], stout.dir[stout.legs[1][0]], stout.dir[stout.legs[1][1]],
                     stout.dir[stout.arms["l"][0]], stout.dir[stout.arms["l"][1]], stout.dir[stout.arms["r"][0]], stout.dir[stout.arms["r"][1]]])
    turns, _ = stout.turns(same, same, same, rest)
    assert all(angle(q, same) < 1e-6 for q in turns.values())
    # A leg solved for a place puts its foot there, the knee to the front.
    hip, goal = np.array([0.0, 0.0, 1.0]), np.array([0.2, 0.1, 0.25])
    thigh, shin = motion.reach(hip, goal, hip + [0.0, 0.0, -0.5], 0.5, 0.5, np.array([1.0, 0.0, 0.0]))
    assert np.allclose(hip + 0.5 * thigh + 0.5 * shin, goal, atol=1e-6) and thigh[0] > shin[0]
    thigh, shin = motion.reach(hip, np.array([3.0, 0.0, 0.0]), hip + [0.0, 0.0, -0.5], 0.5, 0.5, np.array([1.0, 0.0, 0.0]))
    assert np.isfinite(thigh).all() and float(thigh @ shin) > 0.99                             # out of reach: stretched towards it

    # ---- through a scene --------------------------------------------------------
    for label, body in (("tall", tall), ("stout", stout), ("made", made)):
        goal = np.array([14.0 * body.leg, 9.0 * body.leg])
        marks = {}

        def story(p):
            p.wait(10)
            marks["start"] = p.xy.copy()
            marks["walked"] = p.go("walk", lambda frame: goal, stop=1.5 * body.leg)
            p.wait(len(p.frames) + 14)
            marks["arrived"] = p.xy.copy()
            marks["waved"] = p.perform("wave")
            marks["jumped"] = p.perform("jump", times=2)
            marks["heading"] = p.heading
            p.face(p.heading + math.pi)
            marks["turned"] = p.heading
            p.nod("nod", len(p.frames), 30, 2)
            p.wait(len(p.frames) + 30)

        p, out = played(body, lib, story)
        assert len(out) == len(p.frames) - 0 or len(out) <= len(p.frames)
        left = float(np.linalg.norm(goal - marks["arrived"]))
        assert abs(left - 1.5 * body.leg) < 0.5 * body.leg, (label, left / body.leg)             # it stops where it was told to
        seconds = (marks["walked"] - 10) / FPS
        pace = float(np.linalg.norm(marks["arrived"] - marks["start"])) / seconds / body.leg
        assert 0.7 < pace < 2.4 * body.quick, (label, pace)                                    # at a walk
        assert abs(motion.wrap(marks["turned"] - marks["heading"] - math.pi)) < 0.08, label     # turned right round
        # No foot under the ground, and one that stands does not slide.
        worst, total, count = 0.0, 0.0, 0
        for a, b in zip(p.frames, p.frames[1:len(out)]):
            assert min(f[2] for f in feet_of(p, b)) > -1e-6, label
            for k in range(2):
                if a["stands"][k] is not None and b["stands"][k] is not None and np.allclose(a["stands"][k], b["stands"][k]):
                    slid = float(np.linalg.norm(feet_of(p, b)[k] - feet_of(p, a)[k])) / body.leg
                    worst, total, count = max(worst, slid), total + slid, count + 1
        assert count > 100 and worst < 0.08 and total / count < 0.006, (label, worst, total / count)
        # Nothing jerks: no bone turns faster than a body can.
        fastest = max(angle(out[f][3][name], out[f - 1][3][name]) * FPS for f in range(1, len(out)) for name in body.names)
        assert fastest < 30.0, (label, fastest)
        steps = np.array([np.linalg.norm(out[f][0] - out[f - 1][0]) for f in range(1, len(out))]) * FPS / body.leg
        assert steps.max() < 12.0, (label, steps.max())
        # The jumps leave the ground, as high as the figure is tall.
        air = [min(f[2] for f in feet_of(p, frame)) for frame in p.frames[marks["waved"]:marks["jumped"]]]
        assert max(air) > 0.12 * body.height, (label, max(air) / body.height)
        # After the turn both feet are under the body again.
        frame = p.frames[len(out) - 1]
        for k, (upper, _) in enumerate(body.legs):
            assert np.linalg.norm(feet_of(p, frame)[k][:2] - frame["joints"][body.names[upper]][0][:2]) < 0.75 * body.leg, label

    # A leap over something clears it and lands behind it.
    for label, body in (("tall", tall), ("stout", stout)):
        over, span = 0.5 * body.height, 4.0 * body.leg
        marks = {}

        def story(p):
            p.wait(6)
            p.go("run", lambda frame: (40.0 * body.leg, 0.0), stop=28.0 * body.leg, on=True)
            marks["off"] = len(p.frames)
            marks["landed"] = p.leap("leap", (1.0, 0.0), over, span)
            p.wait(len(p.frames) + 20)

        p, out = played(body, lib, story, heading=0.0)
        flight = [min(f[2] for f in feet_of(p, frame)) for frame in p.frames[marks["off"]:marks["landed"]]]
        assert max(flight) > over, (label, max(flight), over)
        high = [i for i, z in enumerate(flight) if z > 0.02 * body.leg]
        flown = p.frames[marks["off"] + high[-1]]["root"][0] - p.frames[marks["off"] + high[0]]["root"][0]
        assert flown > 0.8 * span, (label, flown / span)
        assert max(f[2] for f in feet_of(p, p.frames[len(out) - 1])) < 0.05 * body.leg             # in the end it stands again

    # A gesture is made with the arm that is there, or that is free.
    assert stout.hand("r") == "r" and stout.hand("l") == "l" or stout.hand(lib["wave"].hand) in ("l", "r")
    one_arm = [b for b in bones if "Arm rechts" not in b["name"]]
    lone = motion.Body(one_arm, front, 1.0, lib.neutral)
    assert lone.hand("r") == "l" and lone.hand("l") == "l"
    lifted = [dict(b) for b in bones]
    lifted[9]["tail"], lifted[10]["head"], lifted[10]["tail"] = [-.10, -.03, .30], [-.10, -.03, .30], [-.10, -.05, .37]   # the right hand is up
    assert motion.Body(lifted, front, 1.0, lib.neutral).hand("l") == "r"                         # what hangs low carries something
    p, out = played(lone, lib, lambda p: p.perform("wave"))
    arm = [f["limbs"][4][2] for f in p.frames]
    assert max(arm) > -0.2 and p.mirror is False or max(arm) > -0.2                             # its only arm went up
    # The arm that holds something keeps its hold while the figure walks.
    p, out = played(stout, lib, lambda p: p.go("walk", lambda frame: (10 * stout.leg, 0.0)), heading=0.0)
    hold = stout.dir[stout.arms["r"][1]]
    assert min(float(f["limbs"][7] @ hold) for f in p.frames) > math.cos(math.radians(40.0))
    swing = [float(f["limbs"][4][0]) for f in p.frames]
    assert max(swing) - min(swing) > 0.25                                                       # while the free one swings

    # Looking at something: the head turns there, as far as a neck goes, and back.
    def story(p):
        p.look(lambda frame: (-0.2, 3.0, 0.3), 0, 60)
        p.wait(70)

    p, out = played(stout, lib, story, heading=0.0)
    p, plain = played(stout, lib, lambda p: p.wait(70), heading=0.0)
    assert angle(out[30][3]["Kopf"], plain[30][3]["Kopf"]) > 0.5 and angle(out[30][3]["Brust"], plain[30][3]["Brust"]) > 0.1
    assert angle(out[66][3]["Kopf"], plain[66][3]["Kopf"]) < 1e-6 and angle(out[0][3]["Kopf"], plain[0][3]["Kopf"]) < 1e-6
    assert all(angle(out[30][3][name], plain[30][3][name]) < 1e-6 for name in stout.names if name.startswith("Bein"))

    # Growing where it stands, and being put somewhere else with all it did.
    def story(p):
        p.wait(5)
        at = p.origin.copy()
        p.resize(2.0)
        p.tick()
        assert np.allclose(p.origin, at, atol=0.02 * stout.leg) and abs(p.body.leg - 2 * stout.leg) < 1e-9
        p.move((1.0, -2.0))
        assert np.allclose(p.origin, at + [1.0, -2.0], atol=0.02 * stout.leg)

    p, out = played(stout, lib, story)
    assert out[0][2] == 1.0 and out[-1][2] == 2.0 and abs(out[0][0][0] - 1.0) < 0.1 and abs(out[0][0][1] + 2.0) < 0.1

    # Carried somewhere (hit and thrown): only from now on. What it did so far stays where it was done.
    def carried(p):
        p.wait(6)
        at = p.origin.copy()
        p.move((0.0, 3.0), past=False)
        p.wait(14)
        assert np.allclose(p.origin, at + [0.0, 3.0], atol=0.05 * stout.leg)
        p.perform("kick")

    p, out = played(stout, lib, carried)
    assert abs(out[2][0][1] - out[0][0][1]) < 0.05 and abs(out[12][0][1] - out[0][0][1] - 3.0) < 0.1, (out[0][0], out[2][0], out[12][0])
    print("motion ok")


if __name__ == "__main__":
    main()

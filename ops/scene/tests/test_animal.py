#!/usr/bin/env python3
"""What animal.py must keep: an animal's feet take turns in the order of its
gait and stand where they were put down, it arrives where it is sent and
stands on all four there, it turns, sits, lies, leaps and lands, and nothing
of it jerks or sinks into the ground. numpy only: the animals are skeletons
as rig.py makes them."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import animal  # noqa: E402
import motion  # noqa: E402
import rig  # noqa: E402
from test_rig import dog  # noqa: E402

FPS = 24
HL, HR, FL, FR = range(4)


def bone(name, head, tail, parent, role, **more):
    return dict(name=name, head=head, tail=tail, parent=parent, role=role, **more)


def fox():
    """Long and low, caught in the middle of a step as a model from a photo
    is (one fore leg far forward, one hind leg back); it looks towards +y."""
    return [bone("Becken", [0, -.04, .12], [0, .04, .13], -1, "hips"), bone("Brust", [0, .04, .13], [0, .12, .12], 0, "chest"),
            bone("Hals", [0, .12, .12], [0, .18, .125], 1, "neck"), bone("Kopf", [0, .18, .125], [0, .245, .1], 2, "head"),
            bone("Schwanz 1", [0, -.085, .11], [0, -.155, .08], 0, "tail"), bone("Schwanz 2", [0, -.155, .08], [0, -.24, .045], 4, "tail"),
            bone("Bein hinten links oben", [-.025, -.06, .107], [-.025, -.06, .05], 0, "leg", fore=False, left=True, length=.107),
            bone("Bein hinten links unten", [-.025, -.06, .05], [-.02, -.085, 0], 6, "shin"),
            bone("Bein hinten rechts oben", [.03, -.03, .107], [.03, -.03, .05], 0, "leg", fore=False, left=False, length=.107),
            bone("Bein hinten rechts unten", [.03, -.03, .05], [.035, -.026, 0], 8, "shin"),
            bone("Bein vorn rechts oben", [.01, .09, .107], [.01, .09, .05], 1, "leg", fore=True, left=False, length=.107),
            bone("Bein vorn rechts unten", [.01, .09, .05], [.01, .075, 0], 10, "shin"),
            bone("Bein vorn links oben", [-.02, .147, .107], [-.02, .147, .05], 1, "leg", fore=True, left=True, length=.107),
            bone("Bein vorn links unten", [-.02, .147, .05], [-.016, .192, 0], 12, "shin")], (0.0, 1.0)


def angle(a, b):
    return 2 * math.acos(min(1.0, abs(float((a * b).sum()))))


def played(build, story, heading=0.3):
    a = animal.Animal(build, FPS, (0.0, 0.0), heading)
    a.watch = True
    story(a)
    last = len(a.frames) + 5
    return a, a.finish(last)


def paw_of(a, frame, k):
    """Where the end of a leg is (from the bones)."""
    return frame["joints"][a.build.names[a.build.legs[k][1]]][1]


def sound(a, out, label, fastest=45.0, far=0.2, slip=0.1):
    """What holds whatever an animal does. `far`: how far a leg's end may be
    from where its foot is to be, `slip`: how far a standing foot may move in
    a frame (leg lengths; coming down from a leap both are larger for a moment)."""
    b = a.build
    slid, missed = 0.0, 0.0
    for f in range(1, len(out)):
        x, y = a.frames[f - 1], a.frames[f]
        for k in range(4):
            assert paw_of(a, y, k)[2] > -0.04 * b.leg, (label, f, k, paw_of(a, y, k)[2] / b.leg)   # no foot in the ground (a leg that is a frame late: by a hair)
            at, down, held = y["paws"][k]
            if not held:
                missed = max(missed, float(np.linalg.norm(paw_of(a, y, k) - at)) / b.leg)
            if x["stands"][k] is not None and y["stands"][k] is not None:
                assert np.allclose(x["stands"][k], y["stands"][k]), (label, f, k)   # a foot that stands is where it was put down
                slid = max(slid, float(np.linalg.norm(paw_of(a, y, k) - paw_of(a, x, k))) / b.leg)
    assert missed < far and slid < slip, (label, missed, slid)                    # and the leg reaches it
    speed = max(angle(out[f][3][name], out[f - 1][3][name]) * FPS for f in range(1, len(out)) for name in b.names)
    assert speed < fastest, (label, speed)                                        # nothing jerks
    for frame in a.frames[:len(out)]:
        assert 0.2 * b.stand[0] < frame["ends"][0] and 0.2 * b.stand[1] < frame["ends"][1], label
    for origin, yaw, scale, turns, moves in out:
        assert set(turns) == set(b.names) and all(abs(np.linalg.norm(q) - 1) < 1e-9 for q in turns.values())
        assert set(moves) == {b.names[upper] for upper, _ in b.legs}
        assert all(np.linalg.norm(v) * b.scale <= a.SOCKET * b.leg + 1e-9 for v in moves.values()), label


def downs(a, start, end):
    """Which feet were put down in which frame."""
    out = []
    for f in range(max(1, start), end):
        for k in range(4):
            if a.frames[f]["paws"][k][1] and not a.frames[f - 1]["paws"][k][1]:
                out.append((f, k))
    return out


def main():
    bones, front = fox()
    low = animal.Build(bones, front)
    found = rig.analyse(dog())                         # a skeleton as rig.py makes it
    assert found["kind"] == "quadruped"
    made = animal.Build(found["bones"], found["front"])
    for b in (low, made):
        # hind left, hind right, fore left, fore right: left is to the left of where it looks
        assert b.hip[HL][1] > b.hip[HR][1] and b.hip[FL][1] > b.hip[FR][1] and b.hip[FL][0] > b.hip[HL][0] + 0.5 * b.trunk
        assert b.trunk > b.leg * 0.8 and all(0.8 * b.leg < s <= 1.05 * b.leg for s in b.stand) and len(b.tails) == 2
    assert abs(low.facing - math.pi / 2) < 1e-9 and abs(low.leg - 0.11) < 0.01
    three = [dict(x) for x in bones if "vorn links" not in x["name"]]
    try:
        animal.Build(three, front)
        raise AssertionError("three legs")
    except ValueError:
        pass

    # ---- gaits ------------------------------------------------------------------
    for label, b in (("low", low), ("made", made)):
        goal = np.array([14.0 * b.leg, 6.0 * b.leg])
        for gait in ("walk", "stalk", "trot", "gallop", "bound"):
            marks = {}

            def story(a):
                a.wait(8)
                marks["start"] = len(a.frames)
                marks["there"] = a.go(gait, lambda frame: goal, stop=1.5 * b.leg)

            a, out = played(b, story)
            sound(a, out, (label, gait))
            left = float(np.linalg.norm(goal - a.xy)) / b.leg
            assert abs(left - 1.5) < 0.7, (label, gait, left)                                   # it stops where it was told to
            assert not a.steps and abs(a.speed) < 0.05 and all(p.down for p in a.paws), (label, gait)   # and stands on all four
            for k in range(4):
                assert np.linalg.norm(a.paws[k].at[:2] - a._home(k)) < 0.25 * b.leg, (label, gait, k)   # each foot under its place
            seconds = (marks["there"] - marks["start"]) / FPS
            pace = (float(np.linalg.norm(goal)) / b.leg - 1.5) / seconds
            assert 0.35 * animal.GAITS[gait]["pace"] < pace < 1.1 * animal.GAITS[gait]["pace"], (label, gait, pace)
            # the feet take turns as the gait has it
            order = downs(a, marks["start"] + int(0.25 * (marks["there"] - marks["start"])), marks["start"] + int(0.75 * (marks["there"] - marks["start"])))
            assert len(order) >= 4 and {k for _, k in order} == {0, 1, 2, 3}, (label, gait, order)
            if gait in ("walk", "stalk"):                                                      # one after the other: hind left, fore left, hind right, fore right
                ring = [HL, FL, HR, FR]
                for (_, k), (_, after) in zip(order, order[1:]):
                    assert after == ring[(ring.index(k) + 1) % 4], (label, gait, order)
                assert max(sum(1 for p in frame["paws"] if not p[1]) for frame in a.frames) <= 2, (label, gait)
            elif gait == "trot":                                                              # in pairs across the body
                for f, k in order:
                    assert any(g != k and abs(g_f - f) <= 1 and {k, g} in ({HL, FR}, {HR, FL}) for g_f, g in order), (label, order)
            else:                                                                             # at a gallop and in bounds all four are in the air at times
                assert any(all(not p[1] for p in frame["paws"]) for frame in a.frames), (label, gait)
        # ---- on the spot ----------------------------------------------------------
        marks = {}

        def story(a):
            a.wait(6)
            marks["before"] = a.heading
            a.face(a.heading + 2.4)
            marks["after"] = a.heading

        a, out = played(b, story)
        sound(a, out, (label, "turn"))
        assert abs(motion.wrap(marks["after"] - marks["before"] - 2.4)) < 0.06, (label, marks)
        assert np.linalg.norm(a.xy) < 0.05 * b.leg and all(np.linalg.norm(a.paws[k].at[:2] - a._home(k)) < 0.3 * b.leg for k in range(4)), label
        # ---- poses ----------------------------------------------------------------
        seen = {}

        def story(a):
            for pose in ("sit", "stand", "lie", "sleep", "stand", "bow", "beg", "crouch", "stand"):
                a.ease(pose, 0.4)
                seen.setdefault(pose, []).append((a.frames[-1]["ends"][0] / b.stand[0], a.frames[-1]["ends"][1] / b.stand[1]))

        a, out = played(b, story)
        sound(a, out, (label, "poses"), slip=0.15)
        assert seen["sit"][0][0] < 0.6 and seen["sit"][0][1] > 0.9, seen["sit"]                 # hips down, shoulders up
        assert max(seen["lie"][0]) < 0.5 and max(seen["sleep"][0]) < 0.5, seen["lie"]
        assert seen["bow"][0][0] > 0.9 and seen["bow"][0][1] < 0.6, seen["bow"]
        assert seen["beg"][0][0] < 0.65 and seen["beg"][0][1] > 1.5, seen["beg"]                 # upright on its haunches
        assert all(abs(v - 1.0) < 0.08 for pair in seen["stand"] for v in pair), seen["stand"]   # and up again
        assert np.linalg.norm(a.xy) < 0.05 * b.leg
        # ---- leaps -----------------------------------------------------------------
        marks = {}

        def story(a):
            a.wait(6)
            marks["off"] = len(a.frames)
            marks["down"] = a.hop()

        a, out = played(b, story)
        sound(a, out, (label, "hop"))
        high = max(min(paw_of(a, frame, k)[2] for k in range(4)) for frame in a.frames[marks["off"]:marks["down"]])
        assert high > 0.15 * b.height and all(p.down for p in a.paws), (label, high / b.height)
        over, span = 0.9 * b.height, 3.0 * b.long

        def story(a):
            a.wait(6)
            a.go("gallop", lambda frame: (30.0 * b.leg, 0.0), stop=12.0 * b.leg, on=True)
            marks["off"] = len(a.frames)
            marks["x"] = a.xy[0]
            marks["down"] = a.leap((1.0, 0.0), over, span)

        a, out = played(b, story, heading=0.0)
        sound(a, out, (label, "leap"), fastest=64.0, far=0.35, slip=0.2)
        flight = [f for f in range(marks["off"], marks["down"]) if all(p[2] for p in a.frames[f]["paws"])]
        assert max(min(paw_of(a, a.frames[f], k)[2] for k in range(4)) for f in flight) > over, label
        assert a.frames[flight[-1]]["root"][0] - a.frames[flight[0]]["root"][0] > 0.85 * span, label
        assert not a.steps and all(p.down for p in a.paws) and a.xy[0] > marks["x"] + span, label   # it lands, runs out and stands

    # ---- what else it does ----------------------------------------------------------
    marks = {}

    def story(a):
        a.wait(6)
        marks["a"] = len(a.frames)
        marks["b"] = a.greet(2.0)
        a.wags(1.0)
        a.shakes()
        a.sniffs()
        a.sniffs(eats=True)
        a.howls()
        a.digs()
        marks["c"] = len(a.frames)
        a.pounce()
        a.sway(1.0)
        a.nod("nod", len(a.frames), 24, 2)
        a.wait(len(a.frames) + 24)

    a, out = played(low, story)
    sound(a, out, "does", fastest=64.0, far=0.35, slip=0.2)
    lifted = max(paw_of(a, frame, FR)[2] for frame in a.frames[marks["a"]:marks["b"]])
    assert lifted > 0.3 * low.leg and all(frame["paws"][k][1] for frame in a.frames[marks["a"]:marks["b"]] for k in (HL, HR, FL)), lifted
    assert any(all(p[2] for p in frame["paws"]) for frame in a.frames[marks["c"]:]) and all(p.down for p in a.paws)   # the pounce left the ground and came down
    assert np.linalg.norm(a.xy) > 0.5 * low.long                                                # and went somewhere
    # Whatever it is told to do on its legs it gets up for first.
    def story(a):
        a.ease("lie", 0.2)
        marks["lying"] = a.frames[-1]["ends"][1] / low.stand[1]
        a.go("walk", lambda frame: (6.0 * low.leg, 0.0), stop=0.5 * low.leg)

    a, out = played(low, story, heading=0.0)
    sound(a, out, "gets up", slip=0.15)
    assert marks["lying"] < 0.5 and abs(a.frames[-1]["ends"][1] / low.stand[1] - 1.0) < 0.08 and a.xy[0] > 4.5 * low.leg

    # The tail wags with its own bones, the head looks where it is told to.
    plain, base = played(low, lambda a: a.wait(60))
    wagging, out = played(low, lambda a: a.wags(2.0))
    assert max(angle(out[f][3]["Schwanz 1"], base[f][3]["Schwanz 1"]) for f in range(10, 40)) > 0.3

    def story(a):
        a.look(lambda frame: (-0.3, 2.0, 0.6), 0, 50)
        a.wait(60)

    looking, out = played(low, story)
    assert angle(out[25][3]["Kopf"], base[25][3]["Kopf"]) + angle(out[25][3]["Hals"], base[25][3]["Hals"]) > 0.5
    assert angle(out[58][3]["Kopf"], base[58][3]["Kopf"]) < 1e-6 and all(angle(out[25][3][n], base[25][3][n]) < 1e-6 for n in low.names if n.startswith("Bein"))

    # Growing where it stands, and being put somewhere else with all it did.
    def story(a):
        a.wait(5)
        at = a.origin.copy()
        a.resize(2.0)
        a.tick()
        assert np.allclose(a.origin, at, atol=0.1 * low.leg) and abs(a.build.leg - 2 * low.leg) < 1e-9
        a.move((1.0, -2.0))
        assert np.allclose(a.origin, at + [1.0, -2.0], atol=0.1 * low.leg)

    a, out = played(low, story)
    assert out[0][2] == 1.0 and out[-1][2] == 2.0 and abs(out[0][0][0] - 1.0) < 0.2 and abs(out[0][0][1] + 2.0) < 0.2

    # Thrown somewhere: from now on it is there, lies down and gets up; before, it was where it was.
    def carried(a):
        a.wait(6)
        at = a.origin.copy()
        a.move((0.0, 3.0), past=False)
        a.ease("lie", -0.4)
        a.up()
        assert np.allclose(a.origin, at + [0.0, 3.0], atol=0.6 * low.leg), (a.origin, at)

    a, out = played(low, carried)
    assert abs(out[2][0][1] - out[0][0][1]) < 0.05 and abs(out[-1][0][1] - out[0][0][1] - 3.0) < 0.1, (out[0][0], out[-1][0])
    print("animal ok")


if __name__ == "__main__":
    main()

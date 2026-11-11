#!/usr/bin/env python3
"""What rig.py must tell apart: an animal on four legs, a figure on two, a
thing. The figures here are made of tubes and balls that overlap, so they
have walls inside like the generated models do."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import rig  # noqa: E402


def ball(centre, radius, grain=0.014):
    n = max(24, int(4 * math.pi * radius * radius / (grain * grain)))
    k = np.arange(n) + 0.5
    polar, turn = np.arccos(1 - 2 * k / n), math.pi * (1 + 5 ** 0.5) * k
    return np.asarray(centre) + radius * np.stack([np.cos(turn) * np.sin(polar), np.sin(turn) * np.sin(polar), np.cos(polar)], axis=1)


def tube(a, b, radius):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    steps = max(2, int(np.linalg.norm(b - a) / 0.016) + 1)
    return np.concatenate([ball(a + (b - a) * t, radius) for t in np.linspace(0, 1, steps)])


def dog():
    """Stands along y and looks towards +y; its tail is long and thin."""
    parts = [tube((0, -0.3, 0.45), (0, 0.3, 0.45), 0.12), tube((0, 0.3, 0.5), (0, 0.42, 0.6), 0.07), ball((0, 0.47, 0.64), 0.1),
             tube((0, -0.36, 0.5), (0, -0.7, 0.4), 0.03)]
    for x in (-0.08, 0.08):
        for y in (-0.24, 0.24):
            parts.append(tube((x, y, 0.4), (x, y, 0.035), 0.035))
    return np.concatenate(parts)


def person():
    """Stands upright, arms away from the body; its feet point towards -y."""
    parts = [tube((0, 0, 0.55), (0, 0, 0.95), 0.13), tube((0, 0, 0.95), (0, 0, 1.1), 0.05), ball((0, 0, 1.2), 0.11)]
    for x in (-0.08, 0.08):
        parts += [tube((x, 0, 0.55), (x, 0, 0.05), 0.05), tube((x, 0, 0.045), (x, -0.13, 0.045), 0.04)]
    for x in (-1, 1):
        parts.append(tube((0.16 * x, 0, 0.92), (0.36 * x, 0, 0.52), 0.04))
    return np.concatenate(parts)


def gnome():
    """No neck: a round head sits on the body and wears a tall pointed hat."""
    parts = [tube((0, 0, 0.45), (0, 0, 0.62), 0.15), ball((0, 0, 0.74), 0.12)]
    parts += [ball((0, 0, 0.82 + 0.25 * t), 0.12 * (1 - t) + 0.01) for t in np.linspace(0, 1, 16)]
    for x in (-0.08, 0.08):
        parts += [tube((x, 0, 0.45), (x, 0, 0.05), 0.05), tube((x, 0, 0.045), (x, -0.12, 0.045), 0.04)]
    for x in (-1, 1):
        parts.append(tube((0.16 * x, 0, 0.6), (0.34 * x, 0, 0.36), 0.04))
    return np.concatenate(parts)


def main():
    found = rig.analyse(dog())
    roles = [b["role"] for b in found["bones"]]
    assert found["kind"] == "quadruped", found["notes"]
    assert (roles.count("leg"), roles.count("shin"), roles.count("tail"), roles.count("head"), roles.count("neck")) == (4, 4, 2, 1, 1), roles
    assert found["front"][1] > 0.95 and found["sure"], found["front"]      # the thin end is the tail, the other the head
    names = [b["name"] for b in found["bones"]]
    assert {"Bein vorn links oben", "Bein vorn rechts oben", "Bein hinten links oben", "Bein hinten rechts oben"} <= set(names), names
    left = found["bones"][names.index("Bein vorn links oben")]
    assert left["head"][0] < 0 and left["head"][1] > 0 and left["fore"] and left["left"], left   # left of an animal that looks towards +y is -x
    assert all(b["parent"] < i for i, b in enumerate(found["bones"]))      # parents first
    co = dog()
    labels = found["labels"]
    assert len(labels) == len(co)
    paw = (co[:, 2] < 0.1) & (co[:, 0] < 0) & (co[:, 1] > 0)
    assert set(labels[paw]) == {names.index("Bein vorn links unten")}, set(labels[paw])
    tip = co[:, 1] < -0.62
    assert {roles[i] for i in set(labels[tip])} == {"tail"}
    assert roles[np.bincount(labels[co[:, 1] > 0.5]).argmax()] == "head"
    hips = [b for b in found["bones"] if b["role"] == "leg"]
    assert all(0.3 < b["head"][2] < 0.5 and b["length"] > 0.3 for b in hips), hips   # the legs turn in the body, not at the knee

    found = rig.analyse(person())
    roles = [b["role"] for b in found["bones"]]
    assert found["kind"] == "biped", found["notes"]
    assert (roles.count("leg"), roles.count("arm"), roles.count("forearm"), roles.count("head")) == (2, 2, 2, 1), (roles, found["notes"])
    assert found["front"][1] < -0.95 and found["sure"], found["front"]     # where the toes point
    arms = [b for b in found["bones"] if b["role"] == "arm"]
    assert sorted(b["left"] for b in arms) == [False, True] and all(b["reach"][2] < -0.2 for b in arms), arms
    assert [b["name"] for b in arms if b["left"]][0] == "Arm links oben" and [b for b in arms if b["left"]][0]["head"][0] > 0   # its left, as it looks towards -y, is +x
    head = [b for b in found["bones"] if b["role"] == "head"][0]
    assert 0.95 <= head["head"][2] <= 1.1, head                           # the neck stays where it is narrow

    # Without a neck the head (face and hat) starts right above the shoulders, so the hat does not bend when it looks.
    co = gnome()
    found = rig.analyse(co)
    roles = [b["role"] for b in found["bones"]]
    assert found["kind"] == "biped" and roles.count("arm") == 2, found["notes"]
    face = (co[:, 2] > 0.68) & (co[:, 2] < 0.78)
    assert {roles[i] for i in set(found["labels"][face])} == {"head"}, {roles[i] for i in set(found["labels"][face])}

    for thing in (ball((0, 0, 0.5), 0.5, 0.03), tube((0, 0, 0.1), (0, 0, 0.9), 0.1)):
        found = rig.analyse(thing)
        assert (found["kind"], found["bones"]) == ("object", []) and not found["labels"].any(), found["notes"]
    assert rig.analyse(dog(), kind="object")["kind"] == "object"            # the caller knows better
    assert rig.analyse(np.zeros((3, 3)))["kind"] == "object"

    # Weights: a vertex follows its bone, and its neighbours' bones a little near the border.
    labels = np.array([0] * 10 + [1] * 10)
    edges = np.array([[i, i + 1] for i in range(19)])
    top, weights = rig.blend(labels, 2, edges, rounds=4)
    assert top.shape == (20, 2) and np.allclose(weights.sum(axis=1), 1.0)   # at most four bones, here there are two
    assert weights[0, 0] > 0.98 and top[0, 0] == 0 and top[19, 0] == 1
    assert 0.3 < weights[9, 0] < 0.7 and top[9, 0] == 0 and top[9, 1] == 1 and weights[9, 1] > 0.3
    # A long body without legs (a snake, a fish) is a chain of bones along its length.
    rng = np.random.default_rng(5)
    t = rng.uniform(0, 1, 20000)
    a = rng.uniform(0, 2 * np.pi, 20000)
    r = 0.04 * (1.0 - 0.6 * t)
    snake = np.stack([1.2 * t, 0.1 * np.sin(5 * t) + r * np.cos(a), 0.04 + r * np.sin(a)], axis=1)
    found = rig.analyse(snake)
    assert found["kind"] == "serpent" and len(found["bones"]) == rig.SEGMENTS, found["notes"]
    assert [b["parent"] for b in found["bones"]] == list(range(-1, rig.SEGMENTS - 1)) and set(found["labels"].tolist()) == set(range(rig.SEGMENTS))
    # A ball is no snake.
    sphere = rng.normal(size=(5000, 3)); sphere /= np.linalg.norm(sphere, axis=1, keepdims=True)
    assert rig.analyse(sphere * 0.3 + [0, 0, 0.3])["kind"] == "object"
    print("rig ok")


if __name__ == "__main__":
    main()

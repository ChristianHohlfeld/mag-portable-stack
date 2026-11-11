#!/usr/bin/env python3
"""What photo.py must keep: the camera a photo was taken with is found again
from the points of the model alone, the side that camera sees takes the
photo's colours, the far side and what is hidden take none, and a photo of
something else is not used at all."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import photo  # noqa: E402


def main():
    rng = np.random.default_rng(3)
    # An egg with a pattern on it: no two views of it look alike.
    n = 60000
    around = rng.normal(size=(n, 3))
    around /= np.linalg.norm(around, axis=1, keepdims=True)
    points = around * np.array([0.5, 0.35, 0.25])
    normals = around / np.array([0.5, 0.35, 0.25])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    colours = np.stack([0.5 + 0.45 * np.sin(9 * points[:, 0] + 2 * points[:, 1]), 0.5 + 0.45 * np.sin(11 * points[:, 1] - 3 * points[:, 2]),
                        0.5 + 0.45 * np.cos(13 * points[:, 2] + points[:, 0])], axis=1).astype(np.float32)
    up = np.array([0.0, -1.0, 0.0])
    # Its photo, taken from a place the fit does not know.
    truth = photo.frame(np.array([0.55, -0.45, 0.7]), 0.12, 1.5, np.zeros(3), up)
    side = 512
    seen_only = (normals * (truth[0] - points)).sum(axis=1) > 0           # a photo shows the near side
    picture, covered, taken = photo.view(points[seen_only], colours[seen_only], truth, (130, 130, 380, 380), side)
    for _ in range(3):                                                    # the points drawn close, as a photo is
        grown = picture.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            moved = np.roll(picture, (dy, dx), axis=(0, 1))
            grown = np.where((grown.max(axis=2, keepdims=True) == 0), moved, grown)
        picture = grown
    camera = photo.fit(points, colours, picture, up=up, distance=1.5)
    assert camera is not None, "the camera of the photo is found again"
    assert camera.cover > 0.9 and camera.colour < 0.1, (camera.cover, camera.colour)
    direction = (camera.eye - 0.0) / np.linalg.norm(camera.eye)
    assert direction @ (truth[0] / np.linalg.norm(truth[0])) > 0.985, direction
    # The side it sees takes the photo's colours; the far side takes none.
    outline = picture.max(axis=2) > 0.04
    rgb, trust = photo.colours(camera, points.astype(np.float32), normals.astype(np.float32), picture, outline)
    near = (normals * (truth[0] - points)).sum(axis=1) / np.linalg.norm(truth[0] - points, axis=1)
    assert trust[near < -0.1].max() == 0.0, "nothing of the photo on the far side"
    front = near > 0.7
    assert trust[front].mean() > 0.9, trust[front].mean()
    assert np.abs(rgb[front] - colours[front]).mean() < 0.08, np.abs(rgb[front] - colours[front]).mean()
    # A point hidden behind the egg (as seen from the camera) takes none either, whichever way it looks.
    behind = (-0.9 * truth[0] / np.linalg.norm(truth[0]))[None, :].astype(np.float32)
    towards = (truth[0] / np.linalg.norm(truth[0]))[None, :].astype(np.float32)
    _, hidden = photo.colours(camera, np.vstack([points.astype(np.float32), behind]), np.vstack([normals.astype(np.float32), towards]), picture, outline)
    assert hidden[-1] == 0.0, "what is hidden takes nothing from the photo"
    # A photo of something else is not used: better the splat's own colours than wrong ones.
    other = np.zeros((side, side, 3), np.float32)
    other[100:420, 200:260] = (0.9, 0.9, 0.1)
    assert photo.fit(points, colours, other, up=up, distance=1.5) is None
    assert photo.fit(points, colours, np.zeros((side, side, 3), np.float32), up=up) is None
    print("photo ok")


if __name__ == "__main__":
    main()

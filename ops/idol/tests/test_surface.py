#!/usr/bin/env python3
"""What surface.py must keep: a Gaussian is written into the grid with its own
shape and turn (a flat one stays flat, the way it lies), and the body the
flood leaves is closed: pores are shut, what is inside is filled, loose bits
are gone, a second figure stays, and a real opening stays open."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import surface  # noqa: E402


def spread(field):
    """How far a field reaches along x, y and z (the root of its second moments, in cells)."""
    at = np.stack(np.meshgrid(*[np.arange(n) for n in field.shape], indexing="ij"), axis=-1).reshape(-1, 3).astype(float)
    w = field.ravel().astype(float)
    mean = (at * w[:, None]).sum(axis=0) / w.sum()
    return np.sqrt((((at - mean) ** 2) * w[:, None]).sum(axis=0) / w.sum())


def main():
    n, spacing = 48, 0.02
    lo = np.zeros(3)
    middle = np.array([[0.48, 0.48, 0.48]], np.float32)
    one = np.array([1.0], np.float32)
    flat = np.array([[0.08, 0.08, 0.004]], np.float32)            # a disc: wide in two directions, thin in the third
    same = np.array([[1.0, 0.0, 0.0, 0.0]], np.float32)
    # A flat Gaussian lies in its own plane ...
    field = surface.opacity_field(middle, one, flat, same, lo, spacing, n)
    assert field.shape == (n, n, n) and 0.9 < field.max() <= 1.0 and field.min() >= 0.0
    far = spread(field)
    assert far[0] > 2.5 * far[2] and abs(far[0] - far[1]) < 0.2, far
    assert far[2] >= 0.5                                         # ... and is never thinner than the grid can show
    # ... and turned a quarter about y, it stands up: wide along z, thin along x.
    quarter = np.array([[math.cos(math.pi / 4), 0.0, math.sin(math.pi / 4), 0.0]], np.float32)
    far = spread(surface.opacity_field(middle, one, flat, quarter, lo, spacing, n))
    assert far[2] > 2.5 * far[0] and far[1] > 2.5 * far[0], far
    # Many faint Gaussians together are opaque; one alone is not.
    faint = np.full(40, 0.1, np.float32)
    many = surface.opacity_field(np.repeat(middle, 40, axis=0), faint, np.repeat(flat, 40, axis=0), np.repeat(same, 40, axis=0), lo, spacing, n)
    lone = surface.opacity_field(middle, faint[:1], flat, same, lo, spacing, n)
    assert lone.max() < 0.11 and many.max() > 0.95
    # A Gaussian outside the grid, or half outside, harms nothing.
    out = surface.opacity_field(np.array([[5.0, 5.0, 5.0], [0.0, 0.48, 0.48]], np.float32), np.ones(2, np.float32), np.repeat(flat, 2, axis=0), np.repeat(same, 2, axis=0), lo, spacing, n)
    assert np.isfinite(out).all() and out[0, 24, 24] > 0.5

    # ---- the body -------------------------------------------------------------
    n = 64
    at = np.stack(np.meshgrid(*[np.arange(n)] * 3, indexing="ij"), axis=-1).astype(float)
    far = np.linalg.norm(at - 24.0, axis=-1)
    shell = ((far > 12.0) & (far < 14.5)).astype(np.float32)      # a hollow ball
    shell[23:26, 23:26, 30:40] = 0.0                              # with a pore in its wall
    shell[24, 24, 24] = 1.0                                       # a bubble inside it
    shell[60, 60, 60] = 1.0                                       # a speck far off
    other = np.linalg.norm(at - np.array([50.0, 24.0, 24.0]), axis=-1)
    shell[(other > 5.0) & (other < 7.5)] = 1.0                    # and a second, smaller hollow ball beside it
    body = surface.body_of(shell)
    assert body[24, 24, 24] and body[24, 24, 30] and body[24, 24, 36]      # filled, and shut under the pore (a dent as deep as the wall is left)
    assert body[50, 24, 24]                                               # the second figure is there, filled too
    assert not body[60, 60, 60] and not body[2, 2, 2]                     # the speck is gone; outside is outside
    assert abs(body.sum() / (4 / 3 * math.pi * (14.5 ** 3 + 7.5 ** 3)) - 1.0) < 0.08, body.sum()   # as large as the two balls, no larger
    # A real opening is no pore: a bowl stays a bowl.
    bowl = ((far > 12.0) & (far < 14.5) & (at[..., 2] < 30.0)).astype(np.float32)
    body = surface.body_of(bowl)
    assert not body[24, 24, 24] and body[24, 24, 11]
    # ---- a solid, whatever the photo did not show -------------------------------
    # The thing stands on its higher y (down=3). Nobody saw it from below: a
    # dome without a floor is closed flat, and filled.
    ball = (far > 12.0) & (far < 14.5)
    dome = (ball & (at[..., 1] < 30.0)).astype(np.float32)
    body, added = surface.body_of(dome, down=3, parts=True)
    assert body[24, 24, 24] and body[24, 28, 24] and added[24, 24, 24] and not body[24, 34, 24], "the dome is solid down to its rim"
    # The same shape the other way up is a bowl, and stays one.
    cup = (ball & (at[..., 1] > 18.0)).astype(np.float32)
    body = surface.body_of(cup, down=3)
    assert not body[24, 24, 24] and body[24, 37, 24], "a bowl stays open upwards"
    # A tear in the side, far too wide for a pore: the flood runs in and hollows
    # the ball out. It is filled all the same, and the tear is closed out to where the shell would run.
    torn = ball.astype(np.float32)
    torn[30:, 19:30, 19:30] = 0.0
    assert not (~surface.flooded(torn >= 0.5, surface.PORE))[24, 24, 24], "the flood got in"
    body = surface.body_of(torn, down=3)
    assert body[24, 24, 24] and body[32, 24, 24] and body[35, 24, 24] and not body[40, 24, 24], "hollow and tear are filled, no further"
    assert abs(body.sum() / (4 / 3 * math.pi * 14.5 ** 3) - 1.0) < 0.08, body.sum()
    # A hollow in the surface that leads nowhere stays: a solid ball with a dent keeps its dent.
    dent = (far < 14.5).astype(np.float32)
    dent[33:, 21:28, 21:28] = 0.0
    body = surface.body_of(dent, down=3)
    assert not body[36, 24, 24] and body[31, 24, 24], "a dent is no tear"
    # What the flood did not get into is not touched: a thing on four legs keeps
    # the room under its belly and between its legs, though nobody saw it from below.
    beast = np.zeros((n, n, n), np.float32)
    beast[14:50, 20:27, 18:36] = 1.0                              # a body (y grows downwards: its belly is at 27)
    for x in (15, 44):
        for z in (19, 31):
            beast[x:x + 5, 27:44, z:z + 4] = 1.0                  # four legs
    body, added = surface.body_of(beast, down=3, parts=True)
    assert not added.any() and not body[32, 35, 27] and not body[17, 35, 27] and not body[32, 35, 21], "legs stay apart"
    try:
        surface.body_of(np.zeros((8, 8, 8), np.float32))
        raise AssertionError("nothing there")
    except ValueError:
        pass
    print("surface ok")


if __name__ == "__main__":
    main()

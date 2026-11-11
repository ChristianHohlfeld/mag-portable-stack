#!/usr/bin/env python3
"""What fit_shape must keep: it finds a known turn, size and place of a
whole shape again from the near half of it, as a splat shows it. Runs in
Idol's environment (trimesh, scikit-image), not in CI."""
import pathlib, sys
import numpy as np, trimesh
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mesh as M
from scipy.spatial.transform import Rotation as R
shape = trimesh.creation.capsule(height=1.0, radius=0.35)
shape.vertices[:, 0] *= 0.6; shape.vertices[:, 2] += 0.2 * shape.vertices[:, 0] ** 2      # no symmetry
turn = R.from_euler("xyz", [90, 0, 180], degrees=True).as_matrix()
truth = shape.copy(); truth.vertices = 2.3 * shape.vertices @ turn.T + [0.4, -0.2, 1.1]
front = truth.sample(30000); front = front[front[:, 2] > np.median(front[:, 2])]          # only the side the photo saw
out = M.fit_shape(shape, front.astype(np.float32))
from scipy.spatial import cKDTree
assert out is not None, "the shape is used"
far = float(cKDTree(out.sample(20000)).query(truth.sample(20000))[0].mean())
assert far < 0.01 * 3.9, far
print("shape ok")

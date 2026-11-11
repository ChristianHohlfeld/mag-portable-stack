"""The photo itself as the colours of the side it shows.

The splat is what Idol made of the photo; its colours are as fine as its
Gaussians. The photo is finer. So the camera the photo was taken with is
found again (`fit`): the view of the splat that looks like the photo. Then
every point of the model's surface that this camera sees takes its colour
from the photo (`Camera.colours`); what it does not see keeps the splat's.

Only numpy and scipy here, so this is tested without the Idol environment.
"""
import math

import numpy as np
from scipy.ndimage import binary_dilation, binary_erosion
from scipy.optimize import minimize

SIDE = 160          # the photo and the views of the splat are compared this many pixels wide
GOOD = 0.90         # the view must cover this much of the photo's outline (and little else) ...
ALIKE = 0.08        # ... and its colours may differ by this much at most, or the photo is not used


class Camera:
    """A view of the model that matches the photo: where it stands, how it
    is turned, and where in the photo (in parts of its width) a point lands."""

    def __init__(self, eye, right, upward, forward, scale, shift):
        self.eye, self.right, self.upward, self.forward = eye, right, upward, forward
        self.scale, self.shift = scale, shift

    def see(self, points):
        """For points (n x 3): where they land in the photo (x and y, 0..1) and how far away they are."""
        off = points - self.eye
        depth = off @ self.forward
        safe = np.maximum(depth, 1e-6)
        x = (off @ self.right) / safe * self.scale + self.shift[0]
        y = -(off @ self.upward) / safe * self.scale + self.shift[1]
        return x, y, depth


def frame(direction, roll, distance, centre, up):
    """A camera `distance` away from `centre`, seen from `direction`, turned by `roll` about its own view."""
    direction = direction / np.linalg.norm(direction)
    eye = centre + distance * direction
    forward = -direction
    side = np.cross(forward, up)
    if np.linalg.norm(side) < 1e-6:
        side = np.cross(forward, np.roll(up, 1))
    side /= np.linalg.norm(side)
    upward = np.cross(side, forward)
    c, s = math.cos(roll), math.sin(roll)
    return eye, c * side + s * upward, c * upward - s * side, forward


def view(points, colours, cam, box, side):
    """The points as that camera sees them, fitted into `box` (left, top, right,
    bottom of the photo's outline, in pixels): a picture, what it covers, and the camera with its scale."""
    eye, right, upward, forward = cam
    off = points - eye
    depth = off @ forward
    front = depth > 1e-3
    if front.sum() < 16:
        return None
    x = (off @ right)[front] / depth[front]
    y = -(off @ upward)[front] / depth[front]
    wide = max(float(np.ptp(x)), float(np.ptp(y)), 1e-9)
    scale = max(box[2] - box[0], box[3] - box[1]) / wide
    shift = ((box[0] + box[2]) / 2 - scale * (x.max() + x.min()) / 2, (box[1] + box[3]) / 2 - scale * (y.max() + y.min()) / 2)
    px = np.rint(x * scale + shift[0]).astype(np.int64)
    py = np.rint(y * scale + shift[1]).astype(np.int64)
    inside = (px >= 0) & (px < side) & (py >= 0) & (py < side)
    order = np.argsort(-depth[front][inside])                     # the nearest is written last
    picture = np.zeros((side, side, 3), np.float32)
    covered = np.zeros((side, side), bool)
    picture[py[inside][order], px[inside][order]] = colours[front][inside][order]
    covered[py[inside][order], px[inside][order]] = True
    return picture, covered, Camera(eye, right, upward, forward, scale / side, (shift[0] / side, shift[1] / side))


def fit(points, colours, photo, up=(0.0, -1.0, 0.0), distance=None):
    """The camera from which the points (n x 3, with colours 0..1) look like
    the photo (h x w x 3, 0..1, black where nothing is). None: no view of
    them looks like it."""
    from PIL import Image
    small = np.asarray(Image.fromarray((np.clip(photo, 0, 1) * 255).astype(np.uint8)).resize((SIDE, SIDE), Image.BILINEAR), np.float32) / 255.0
    outline = small.max(axis=2) > 0.04
    if outline.sum() < 0.01 * SIDE * SIDE:
        return None
    ys, xs = np.nonzero(outline)
    box = (xs.min(), ys.min(), xs.max(), ys.max())
    if len(points) > 120000:
        pick = np.random.default_rng(7).choice(len(points), 120000, replace=False)
        points, colours = points[pick], colours[pick]
    points = points.astype(np.float64)
    colours = colours.astype(np.float32)
    up = np.asarray(up, np.float64)
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    size = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    distance = float(distance) if distance and distance > 0.6 * size else 1.6 * size

    def unlike(direction, roll, far):
        seen = view(points, colours, frame(direction, roll, far, centre, up), box, SIDE)
        if seen is None:
            return 2.0, None
        picture, covered, camera = seen
        covered = binary_dilation(covered)
        both = covered & outline
        cover = both.sum() / max(1, (covered | outline).sum())
        hit = (picture.max(axis=2) > 0) & outline
        colour = float(np.abs(picture[hit] - small[hit]).mean()) if hit.sum() > 50 else 1.0
        return (1.0 - cover) + 2.0 * colour, (cover, colour, camera)

    # From everywhere around it first, then closer and closer from the best places.
    count = 500
    index = np.arange(count) + 0.5
    polar, turn = np.arccos(1 - 2 * index / count), math.pi * (1 + 5 ** 0.5) * index
    around = np.stack([np.cos(turn) * np.sin(polar), np.sin(turn) * np.sin(polar), np.cos(polar)], axis=1)
    first = sorted(((unlike(d, 0.0, distance)[0], i) for i, d in enumerate(around)))[:4]
    best = None
    for _, i in first:
        start = around[i]

        def cost(v):
            direction = start + v[0] * np.cross(start, up) + v[1] * np.cross(start, np.cross(start, up))
            return unlike(direction, v[2], distance * math.exp(v[3]))[0]
        found = minimize(cost, np.zeros(4), method="Nelder-Mead", options={"maxfev": 220, "xatol": 2e-3, "fatol": 1e-4,
                                                                           "initial_simplex": np.vstack([np.zeros(4), 0.12 * np.eye(4)])})
        if best is None or found.fun < best[0]:
            v = found.x
            direction = start + v[0] * np.cross(start, up) + v[1] * np.cross(start, np.cross(start, up))
            best = (found.fun, unlike(direction, v[2], distance * math.exp(v[3]))[1])
    if best is None or best[1] is None:
        return None
    cover, colour, camera = best[1]
    camera.cover, camera.colour = float(cover), float(colour)
    return camera if cover >= GOOD and colour <= ALIKE else None


def colours(camera, points, normals, photo, outline):
    """For points of the surface (n x 3) with their outward normals: the
    photo's colour where this camera sees them (n x 3, 0..1), and how much
    it is to be trusted there (n, 0..1: 0 where the point is turned away,
    hidden behind another, or outside the photo's outline).

    `photo` (h x w x 3, 0..1) and `outline` (h' x w', True where the thing
    is) cover the same square; the photo may be finer than the outline."""
    x, y, depth = camera.see(points)
    towards = camera.eye - points
    towards /= np.maximum(np.linalg.norm(towards, axis=1, keepdims=True), 1e-9)
    facing = (normals * towards).sum(axis=1)
    # What is nearest in every pixel: the surface points themselves, drawn small.
    side = 1024
    px = np.clip(np.rint(x * side).astype(np.int64), -1, side)
    py = np.clip(np.rint(y * side).astype(np.int64), -1, side)
    inside = (px >= 0) & (px < side) & (py >= 0) & (py < side) & (depth > 1e-3)
    nearest = np.full(side * side, np.inf, np.float32)
    flat = py[inside] * side + px[inside]
    order = np.argsort(-depth[inside])
    nearest[flat[order]] = depth[inside][order]
    nearest = nearest.reshape(side, side)
    filled = np.where(np.isfinite(nearest), nearest, 0.0)
    for _ in range(2):                                             # ... and a pixel wider, so no point looks through a gap
        grown = filled.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            moved = np.roll(filled, (dy, dx), axis=(0, 1))
            grown = np.where((moved > 0) & ((grown == 0) | (moved < grown)), moved, grown)
        filled = grown
    reach = float(np.ptp(depth[inside])) if inside.any() else 1.0
    seen = inside.copy()
    seen[inside] = depth[inside] <= filled[py[inside], px[inside]] + 0.02 * reach
    # Not at the very rim of the outline: there the photo shows what is behind the thing.
    safe = binary_erosion(outline, iterations=max(1, outline.shape[0] // 256))
    oy = np.clip((y * outline.shape[0]).astype(np.int64), 0, outline.shape[0] - 1)
    ox = np.clip((x * outline.shape[1]).astype(np.int64), 0, outline.shape[1] - 1)
    trust = np.clip((facing - 0.15) / 0.3, 0.0, 1.0)
    trust = trust * trust * (3 - 2 * trust)
    trust = np.where(seen & safe[oy, ox], trust, 0.0).astype(np.float32)
    # The photo between its pixels.
    h, w = photo.shape[:2]
    fx = np.clip(x * w - 0.5, 0, w - 1.001)
    fy = np.clip(y * h - 0.5, 0, h - 1.001)
    x0, y0 = fx.astype(np.int64), fy.astype(np.int64)
    ax, ay = (fx - x0)[:, None].astype(np.float32), (fy - y0)[:, None].astype(np.float32)
    rgb = (photo[y0, x0] * (1 - ax) * (1 - ay) + photo[y0, x0 + 1] * ax * (1 - ay) +
           photo[y0 + 1, x0] * (1 - ax) * ay + photo[y0 + 1, x0 + 1] * ax * ay)
    return rgb.astype(np.float32), trust

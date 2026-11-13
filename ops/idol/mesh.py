#!/usr/bin/env python3
"""Idol splat -> a solid, coloured model (GLB) and a printable one (STL). CPU only.

A splat is a cloud of small flat Gaussians that lie on what the photo showed.
The model is made from them the way they are:

1. Every Gaussian with its own size and its own turn (none left out) is
   written into a grid as opacity: a cell is as opaque as the Gaussians in
   it together.
2. Where that is half opaque is the shell. The grid is flooded from outside:
   what the flood does not reach is the body, so it is closed, and shells
   and bubbles inside it are gone. Pores the size of a cell or two are
   closed before the flood. Where the flood got into the thing through a
   gap on a side the photo did not show, the rooms it left and the tear are
   filled; a thing it did not get into is not touched (surface.py). Loose
   bits fall away; what is big enough to be a figure of its own stays.
3. The surface is taken anew from that body (uniform triangles, no holes),
   smoothed, and brought down to the number of faces a model needs. It
   stays a solid through that: closed, every edge between two faces, faces
   outwards, no bubble inside, no corner where two sheets only touch.
4. That small mesh is unwrapped, and the texture is baked for it from the
   splat itself, every Gaussian with its own footprint: the colours are
   as sharp as the splat, whatever the faces. The side the photo shows
   takes its fine detail from the photo, which is finer still (photo.py
   finds the photo's camera again). The STL keeps
   the finer mesh.

Where the shape stage made the whole shape (shape.py), it takes the place
of the splat's shell, and the side the photo shows takes the photo's own
colours, as fine as it was taken: the shape was made from that picture and
lies exactly under it (front_of), so nothing has to be guessed there.
Where MV-Adapter painted the shape from six sides (paint.py, the texturing
TripoSG's authors use), the whole model takes those views instead
(painted). Without them the sides the photo did not see take its colours
carried over the surface (carried). The splat only says where the model
stands and from where the photo saw it.

The original splat.ply is never modified.
"""
import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from plyfile import PlyData
from scipy.ndimage import binary_closing, binary_dilation, distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
import trimesh

import photo as photo_of
from surface import OWN, PORE, body_of, opacity_field
from unwrap import unwrap

# Single source of truth for mesh preset parameters. "resolution": cells of
# the grid along its longest side. "faces": of the model that is shown,
# "print_faces": of the STL. "unwrap": seconds xatlas gets for the UVs of the
# small mesh before it is laid out plainly instead (see unwrap.py).
QUALITY_PRESETS = {
    "fast": {"resolution": 128, "faces": 40000, "print_faces": 150000, "texture": 2048, "smooth": 2, "crop": 0.5, "neighbors": 8, "unwrap": 30},
    "normal": {"resolution": 224, "faces": 80000, "print_faces": 400000, "texture": 4096, "smooth": 3, "crop": 0.1, "neighbors": 12, "unwrap": 60},
    "high": {"resolution": 320, "faces": 160000, "print_faces": 800000, "texture": 4096, "smooth": 2, "crop": 0.02, "neighbors": 16, "unwrap": 120},
}
def opacity_colors(vertices, names, indices):
    if all("f_dc_" + str(i) in names for i in range(3)):
        colors = np.column_stack([
            vertices["f_dc_" + str(i)][indices] for i in range(3)
        ]).astype(np.float32)
        return np.clip(colors * 0.28209479177387814 + 0.5, 0, 1)
    if all(k in names for k in ("red", "green", "blue")):
        return (np.column_stack([vertices[k][indices] for k in
                                  ("red", "green", "blue")]).astype(np.float32) / 255.0)
    return np.full((len(indices), 3), 185.0/255.0, np.float32)


def gaussian_albedo(points, tree, colors, alpha, shapes, spacing, neighbors):
    """The colour of the splat at points of the surface, the way a splat is
    drawn: every Gaussian near a point counts by how much of it lies there,
    with its own three widths and its own turn (`shapes`: for each Gaussian
    the matrix that says how many of its widths a step is). A flat disc
    colours its own plane and not what lies before or behind it, a long
    stroke colours along itself. So the texture is as sharp as the splat.

    Chunking is handled by the caller, including 4K UV texel baking.
    """
    distances, ids = tree.query(points, k=min(neighbors, len(colors)), workers=8)
    if np.ndim(ids) == 1:
        ids, distances = ids[:, None], distances[:, None]
    off = points[:, None, :] - tree.data[ids].astype(np.float32)
    apart = np.einsum("nki,nkij,nkj->nk", off, shapes[ids], off)
    weight = alpha[ids] * np.exp(-0.5 * np.minimum(apart, 80.0))
    total = weight.sum(axis=1)
    rgb = (weight[..., None] * colors[ids]).sum(axis=1)
    rgb /= np.maximum(total[:, None], 1e-12)
    weak = total < 1e-6
    if np.any(weak):
        # Far from every Gaussian (a side that was closed, a filled room): the colours around it, softly.
        fallback = alpha[ids[weak]] / np.maximum(distances[weak], spacing*0.25)**2
        fallback /= np.maximum(fallback.sum(axis=1, keepdims=True), 1e-12)
        rgb[weak] = (fallback[..., None] * colors[ids[weak]]).sum(axis=1)
    return np.clip(rgb, 0.0, 1.0)


def footprints(scales, turns, least):
    """For every Gaussian the matrix M with d' M d = how many of its own
    widths a step d is, squared. No width counts as less than `least`: a
    Gaussian thinner than that would colour nothing but its own middle."""
    widths = np.maximum(scales, least).astype(np.float32)
    q = turns / np.maximum(np.linalg.norm(turns, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    turn = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1),
                     np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1),
                     np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1)], axis=1).astype(np.float32)
    return np.einsum("nij,nj,nkj->nik", turn, 1.0 / (widths * widths), turn).astype(np.float32)


SEEN = {}   # "from": the direction from the model to the photo's camera, once it was found


def the_photo(job):
    """What the model was made from, for its colours: the square of the photo
    the model was shown (as fine as the photo was taken, when the sampling
    stage noted its cut), and its outline. None: this job has none."""
    import json
    shown = job / "outputs" / "input_input.png"
    if not shown.is_file():
        return None
    try:
        prepared = np.asarray(Image.open(shown).convert("RGB"), np.float32) / 255.0
        outline = prepared.max(axis=2) > 0.04
        fine = prepared
        note, original = job / "outputs" / "input_crop.json", job / "input.png"
        if note.is_file() and original.is_file():
            cut = json.loads(note.read_text())
            picture = Image.open(original)
            if list(picture.size) == cut["photo"]:
                kx, ky = cut["photo"][0] / cut["resized"][0], cut["photo"][1] / cut["resized"][1]
                left, top, right, bottom = cut["box"]
                side = int(min(4096, max(prepared.shape[0], round((right - left) * kx))))
                if side > prepared.shape[0]:
                    part = picture.convert("RGB").crop((int(round(left * kx)), int(round(top * ky)), int(round(right * kx)), int(round(bottom * ky))))
                    fine = np.asarray(part.resize((side, side), Image.LANCZOS), np.float32) / 255.0
        distance = None
        hint = job / "outputs" / "input_camera.json"
        if hint.is_file():
            distance = float(json.loads(hint.read_text()).get("radius", 0)) or None
        # What the photo has that the splat has not is its fine detail. The splat
        # keeps its own colours (they are all of one piece, around the whole
        # thing); the photo gives what is finer than a fiftieth of the thing.
        ys, xs = np.nonzero(outline)
        across = max(xs.max() - xs.min(), ys.max() - ys.min(), 8) * fine.shape[0] / prepared.shape[0]
        # (Blurred within the thing only: the black around it is no colour of it.)
        inside = np.asarray(Image.fromarray(outline).resize((fine.shape[1], fine.shape[0]), Image.NEAREST), np.float32)[..., None]
        sigma = (0.02 * across, 0.02 * across, 0)
        soft = gaussian_filter(fine * inside, sigma=sigma) / np.maximum(gaussian_filter(inside, sigma=sigma), 1e-3)
        return {"prepared": prepared, "fine": np.concatenate([fine, soft], axis=2), "outline": outline, "distance": distance}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def carried(verts, faces, camera, fine, outline):
    """The photo's colours carried over the whole surface: every corner of
    the mesh the photo saw well takes the photo's colour there, every other
    one the colour of the nearest such corner along the surface (the back of
    a head the hair's, the back of a shirt the shirt's), softened so that
    no edge runs where two of them meet. None: the photo saw too little.

    "Nearest" counts a step sideways (along the photo's left-right) three
    times: what is behind a thing is mostly what was above it, carried over
    the top, rather than what was beside it. The back of a head is reached
    over the crown from the hair, not around from the cheeks and ears; the
    back of a shirt is still the shirt's, from its shoulders and sides."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    mesh = trimesh.Trimesh(np.asarray(verts, np.float64), np.asarray(faces, np.int64), process=False)
    rgb, trust = photo_of.colours(camera, np.asarray(mesh.vertices, np.float32), np.asarray(mesh.vertex_normals, np.float32), fine, outline)
    seen = np.flatnonzero(trust > 0.6)
    if len(seen) < 50:
        return None
    edges = mesh.edges_unique
    pts = np.asarray(mesh.vertices, np.float64)
    side = np.asarray(camera.right, np.float64) / max(float(np.linalg.norm(camera.right)), 1e-12)
    length = mesh.edges_unique_length + 2.0 * np.abs((pts[edges[:, 1]] - pts[edges[:, 0]]) @ side)
    n = len(mesh.vertices)
    graph = coo_matrix((np.concatenate([length, length]), (np.concatenate([edges[:, 0], edges[:, 1]]), np.concatenate([edges[:, 1], edges[:, 0]]))), shape=(n, n)).tocsr()
    _, _, source = dijkstra(graph, directed=False, indices=seen, min_only=True, return_predecessors=True)
    out = rgb[:, :3].copy()
    reached = source >= 0
    out[reached] = rgb[source[reached], :3]
    free = trust <= 0.6
    neighbours = coo_matrix((np.ones(2 * len(edges)), (np.concatenate([edges[:, 0], edges[:, 1]]), np.concatenate([edges[:, 1], edges[:, 0]]))), shape=(n, n)).tocsr()
    count = np.maximum(np.asarray(neighbours.sum(axis=1)).ravel(), 1)[:, None]
    for _ in range(40):                                       # softened where it was not seen; what was seen stays
        out[free] = (neighbours @ out / count)[free]
    return out.astype(np.float32)


def bake_texture(verts, faces, xyz, alpha, shapes, colors, spacing, size, neighbors, seconds, source=None, front=None):
    """Unwrap, rasterize UV triangle IDs and bake at each texel.

    UVs sample the actual 3D surface, not interpolated per-vertex colors.
    Face IDs are rasterized using Pillow's C implementation. Barycentric
    interpolation and neighbourhood colour sampling run in bounded chunks.
    """
    vmapping, uv_faces, uvs = unwrap(verts, faces, size, seconds)

    uv_px = np.column_stack((
        uvs[:, 0] * (size-1),
        (1.0 - uvs[:, 1]) * (size-1))).astype(np.float32)
    index_image = Image.new("I", (size, size), 0)
    drawing = ImageDraw.Draw(index_image)
    for i, tri in enumerate(uv_faces):
        points = [tuple(float(x) for x in uv_px[j]) for j in tri]
        drawing.polygon(points, fill=i+1)

    face_map = np.asarray(index_image, dtype=np.int32)
    flat = face_map.reshape(-1)
    covered = flat > 0
    if not covered.any():
        raise RuntimeError("Kein UV-Texel abgedeckt")
    tex_flat = np.zeros((size*size, 3), dtype=np.uint8)
    all_pixels = np.flatnonzero(covered)
    # The side the photo shows takes its colours from the photo: find the photo's camera again.
    camera = None
    if front is not None:
        # The shape's own picture, laid on it the way TripoSG saw it: no camera to look for.
        camera = front["camera"]
        SEEN["from"] = camera.forward * -1.0
        print(f"Photo: laid onto the shape as it was made from it; {front['fine'].shape[0]} px across", flush=True)
    elif source is not None:
        strong = alpha > 0.3
        camera = photo_of.fit(xyz[strong], colors[strong], source["prepared"], distance=source["distance"])
        if camera is not None:
            SEEN["from"] = camera.forward * -1.0
        print("Photo: " + ("its camera was not found again; colours from the splat alone" if camera is None else
                           f"camera found again (outline {camera.cover:.2f}, colours apart {camera.colour:.3f}); {source['fine'].shape[0]} px across"), flush=True)
    where = np.zeros((len(all_pixels), 3), np.float32) if camera is not None else None
    facing = np.zeros((len(all_pixels), 3), np.float32) if camera is not None else None
    corners = np.asarray(verts, np.float32)[vmapping][uv_faces]
    outward = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    outward /= np.maximum(np.linalg.norm(outward, axis=1, keepdims=True), 1e-12)
    del corners
    tree = cKDTree(xyz) if xyz is not None else None                 # (no splat: the shape alone)
    if front is not None and not front.get("views"):
        spread = carried(verts, faces, camera, front["fine"], front["outline"])
        if spread is None and xyz is None:
            raise RuntimeError("Das Foto zeigt zu wenig von der Form")
        spread = spread[vmapping] if spread is not None else None
        beyond = np.zeros((len(all_pixels), 3), np.float32)            # the photo carried there
    else:
        spread = None
    verts = np.asarray(verts, np.float32)[vmapping]
    total = len(all_pixels)
    for start in range(0, total, 32768):
        samples = all_pixels[start:start+32768]
        fi = flat[samples].astype(np.int64) - 1
        tri = uv_faces[fi]
        uv_tri = uv_px[tri]
        px = (samples % size).astype(np.float32) + 0.5
        py = (samples // size).astype(np.float32) + 0.5
        p0, p1, p2 = uv_tri[:, 0], uv_tri[:, 1], uv_tri[:, 2]
        a = p1 - p0
        b = p2 - p0
        c = np.stack((px, py), axis=1) - p0
        det = a[:, 0]*b[:, 1] - a[:, 1]*b[:, 0]
        safe = np.where(np.abs(det) < 1e-8, 1.0, det)
        u = (c[:, 0]*b[:, 1] - c[:, 1]*b[:, 0]) / safe
        v = (a[:, 0]*c[:, 1] - a[:, 1]*c[:, 0]) / safe
        u = np.clip(u, 0, 1)
        v = np.clip(v, 0, 1)
        # Pixel coverage at triangle boundaries may be outside by <1 pixel.
        factor = np.maximum(u + v, 1.0)
        u /= factor
        v /= factor
        w = 1.0 - u - v
        tri_verts = verts[tri]
        positions = (w[:, None]*tri_verts[:, 0] + u[:, None]*tri_verts[:, 1]
                     + v[:, None]*tri_verts[:, 2])
        rgb = gaussian_albedo(positions, tree, colors, alpha, shapes, spacing, neighbors) if tree is not None else np.full((len(samples), 3), 0.5)
        tex_flat[samples] = np.rint(rgb*255).astype(np.uint8)
        if camera is not None:
            where[start:start+len(samples)] = positions
            facing[start:start+len(samples)] = outward[fi]
        if spread is not None:
            beyond[start:start+len(samples)] = w[:, None]*spread[tri[:, 0]] + u[:, None]*spread[tri[:, 1]] + v[:, None]*spread[tri[:, 2]]
    if camera is not None and front is not None and front.get("views"):
        # MV-Adapter painted the shape from six sides (paint.py), each view
        # fitting the shape: every texel takes what the views that see it
        # straight show there; one no view sees, its nearest painted one's.
        total = np.zeros(len(all_pixels), np.float32)
        paint = np.zeros((len(all_pixels), 3), np.float32)
        for view in front["views"]:
            rgb, trust = photo_of.colours(view["camera"], where, facing, view["image"], view["outline"])
            weight = trust * trust
            paint += weight[:, None] * rgb[:, :3]
            total += weight
        seen = total > 1e-3
        if not seen.any():
            raise RuntimeError("Die Ansichten zeigen nichts von der Form")
        paint[seen] /= total[seen, None]
        if not seen.all():
            _, near = cKDTree(where[seen]).query(where[~seen], workers=8)
            paint[~seen] = paint[seen][near]
        tex_flat[all_pixels] = np.rint(np.clip(paint, 0, 1) * 255).astype(np.uint8)
        print(f"Views: {100.0 * float(seen.mean()):.0f} % of the surface painted from MV-Adapter's six views", flush=True)
        del where, facing, paint, total
    elif camera is not None and front is not None:
        # The shape lies where the photo saw it, so what the photo saw takes the
        # photo's own colours (glasses, eyes, letters as sharp as they were
        # taken). What it did not see takes the photo's colours carried over
        # the surface: Idol's splat is often only the front, and where it
        # guessed a back its colours are smeared from the front.
        rgb, trust = photo_of.colours(camera, where, facing, front["fine"], front["outline"])
        splat = tex_flat[all_pixels].astype(np.float32) / 255.0
        sure = trust > 0.8
        if sure.sum() > 1000:
            ms, mp = splat[sure].mean(axis=0), rgb[sure].mean(axis=0)
            gain = np.clip(rgb[sure].std(axis=0) / np.maximum(splat[sure].std(axis=0), 1e-3), 0.7, 1.4)
            splat = (splat - ms) * gain + mp
        if spread is not None:
            splat = beyond                                             # (the splat only where the photo saw too little)
        mixed = splat * (1 - trust[:, None]) + rgb * trust[:, None]
        tex_flat[all_pixels] = np.rint(np.clip(mixed, 0, 1) * 255).astype(np.uint8)
        print(f"Photo: {100.0 * float((trust > 0.5).mean()):.0f} % of the surface takes its colours from it", flush=True)
        del where, facing, rgb, trust, mixed, splat, beyond
    elif camera is not None:
        rgb, trust = photo_of.colours(camera, where, facing, source["fine"], source["outline"])
        mixed = tex_flat[all_pixels].astype(np.float32) / 255.0
        mixed += trust[:, None] * np.clip(rgb[:, :3] - rgb[:, 3:], -0.3, 0.3)      # the photo's detail on the splat's colours
        tex_flat[all_pixels] = np.rint(np.clip(mixed, 0, 1) * 255).astype(np.uint8)
        print(f"Photo: {100.0 * float((trust > 0.5).mean()):.0f} % of the surface takes its fine detail from it", flush=True)
        del where, facing, rgb, trust, mixed
    texture = tex_flat.reshape(size, size, 3)
    # Extend colors 8 pixels beyond UV islands to avoid bilinear seam bleed.
    filled = covered.reshape(size, size)
    rim = binary_dilation(filled, iterations=8) & ~filled
    if rim.any():
        nearest = distance_transform_edt(~filled, return_distances=False,
                                         return_indices=True)
        texture[rim] = texture[nearest[0][rim], nearest[1][rim]]
    return vmapping, uv_faces, uvs, Image.fromarray(texture, mode="RGB")


def shell_of(xyz, alpha, scales, turns, lo, spacing, resolution):
    """The closed body the splat stands for (see the top of this file), as a mesh."""
    opaque = opacity_field(xyz, alpha, scales, turns, lo, spacing, resolution)
    body = body_of(opaque, down=3)                        # Idol's y points down: the thing stands on its higher y
    # The surface runs where the Gaussians are half opaque; where the body was
    # closed or filled it runs along the body.
    field = np.where(body, np.maximum(opaque, 0.62), np.minimum(opaque, 0.38))
    del opaque, body
    field = gaussian_filter(field, sigma=0.6, output=np.float32)
    verts, faces, _, _ = marching_cubes(field, level=0.5, spacing=(spacing,)*3, allow_degenerate=False)
    del field
    verts += lo
    if not len(faces):
        raise ValueError("Marching Cubes hat keine Oberfläche erzeugt")
    geometry = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    # A solid has an outside and nothing else: a bubble inside it is no part
    # of it, and neither is a crumb beside it.
    pieces = geometry.split(only_watertight=False)
    if len(pieces) > 1:
        largest = max(pieces, key=lambda piece: abs(piece.volume))
        kept = [piece for piece in pieces if piece.volume * largest.volume > 0 and abs(piece.volume) >= OWN * abs(largest.volume)]
        geometry = kept[0] if len(kept) == 1 else trimesh.util.concatenate(kept)
    return geometry


def fit_shape(shape, points, start=None):
    """The whole shape (shape.py, in TripoSG's frame) put where the splat is:
    turned, scaled and moved so that the splat's points lie on its surface.
    The splat covers the side the photo shows, so it is asked from the
    splat's side only (each point to the nearest place of the shape). All 24
    ways a box can lie are tried, each refined; the best is taken if most of
    the splat lies on it. Else, with a `start` (r, t: where the photo's
    camera puts it), it stands there; else None: the splat's own shell is
    used. The placed shape keeps its placement (r, t) in metadata["placed"]."""
    from scipy.spatial import cKDTree
    rng = np.random.default_rng(3)
    pts = points[rng.choice(len(points), min(len(points), 20000), replace=False)].astype(np.float64)
    surface = shape.sample(40000).astype(np.float64)
    pc, size = np.median(pts, axis=0), float(np.linalg.norm(np.percentile(pts, 98, axis=0) - np.percentile(pts, 2, axis=0)))
    turns = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for signs in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
            m = np.zeros((3, 3))
            for i, (j, sg) in enumerate(zip(perm, signs)):
                m[i, j] = sg
            if np.linalg.det(m) > 0:
                turns.append(m)
    def refine(r, t, rounds):
        for _ in range(rounds):                                       # trimmed nearest-point fitting, with scale
            placed = surface @ r.T + t
            dist, idx = cKDTree(placed).query(pts, workers=8)
            keep = dist <= np.percentile(dist, 85)
            a, b = pts[keep], placed[idx[keep]]
            ma, mb = a.mean(axis=0), b.mean(axis=0)
            u, sv, vt = np.linalg.svd((b - mb).T @ (a - ma))
            d = np.eye(3); d[2, 2] = np.sign(np.linalg.det(vt.T @ u.T))
            rot = vt.T @ d @ u.T
            grow = float(np.clip(np.sum(sv * np.diag(d)) / max(np.sum((b - mb) ** 2), 1e-12), 0.8, 1.25))
            # a ≈ grow·rot·(b − mb) + ma, so the shape's new place is that applied to where it was
            r, t = grow * rot @ r, grow * rot @ (t - mb) + ma
        dist, _ = cKDTree(surface @ r.T + t).query(pts, workers=8)
        return float(np.mean(dist < 0.02 * size)), r, t

    theirs = np.percentile(pts, 98, axis=0) - np.percentile(pts, 2, axis=0)
    wide, thin = np.argsort(-theirs)[:2], int(np.argmin(theirs))
    tries = []
    for turn in turns:
        cloud = surface @ turn.T
        span = np.percentile(cloud, 98, axis=0) - np.percentile(cloud, 2, axis=0)
        # The splat is flat where the photo looked from; the two widest of its sides say the size.
        scale = float(np.median(theirs[wide] / np.maximum(span[wide], 1e-9)))
        cloud = cloud * scale
        middle = (np.percentile(pts, 98, axis=0) + np.percentile(pts, 2, axis=0)) / 2 - (np.percentile(cloud, 98, axis=0) + np.percentile(cloud, 2, axis=0)) / 2
        # ... and it is the near half of the thing: its front lies on one end of the shape along that side.
        for end in (98, 2):
            t = middle.copy()
            t[thin] = np.percentile(pts[:, thin], end) - np.percentile(cloud[:, thin], end)
            tries.append((refine(turn * scale, t, 4), turn, scale, t))
    if start is not None:
        tries.append((refine(start[0], start[1], 4), None, None, None))
    tries.sort(key=lambda x: -x[0][0])
    best = None
    for (near, r, t), *_ in tries[:4]:                                # the most promising, fitted to the end
        found = refine(r, t, 30)
        if best is None or found[0] > best[0]:
            best = found
    near, r, t = best
    print(f"Shape: fits the splat at {100 * near:.0f} % of its points", flush=True)
    if near < float(os.environ.get("MAG_SHAPE_FIT", "0.5")):
        if start is None:
            return None
        print("Shape: placed where the photo's camera saw it", flush=True)
        r, t = start
    out = shape.copy()
    out.vertices = np.asarray(shape.vertices, np.float64) @ r.T + t
    out.metadata["placed"] = (np.asarray(r, np.float64), np.asarray(t, np.float64))
    return out


def front_of(shape, outline):
    """The camera of the picture the shape was made from, in the shape's own
    frame. TripoSG sees its picture straight from +z without perspective, the
    square from -1 to 1 a little larger than the picture; how much larger
    (and how far off the middle) is found where the shape's outline covers the
    thing's outline best. (How well they cover each other, the camera.)"""
    from PIL import Image as Img
    side = 256
    want = np.asarray(Img.fromarray(outline).resize((side, side), Img.BILINEAR), bool)
    if want.sum() < 64:
        return 0.0, None
    pts = np.asarray(shape.sample(200000), np.float64)
    pts = pts[np.argsort(-pts[:, 2])]

    def cover(s, dx, dy):
        px = np.rint(((pts[:, 0] * s + 1) / 2 + dx) * side - 0.5).astype(np.int64)
        py = np.rint(((-pts[:, 1] * s + 1) / 2 + dy) * side - 0.5).astype(np.int64)
        ok = (px >= 0) & (px < side) & (py >= 0) & (py < side)
        got = np.zeros((side, side), bool)
        got[py[ok], px[ok]] = True
        got = binary_closing(got, iterations=2)                           # closed between the points, not grown
        return (got & want).sum() / max(1, (got | want).sum())

    best = max((cover(s, 0, 0), s, 0.0, 0.0) for s in np.linspace(0.6, 1.4, 41))
    for step in (0.02, 0.008, 0.003):                                 # then nudged into place
        for _ in range(4):
            _, s, dx, dy = best
            best = max([best] + [(cover(s + a * step, dx + b * step, dy + c * step), s + a * step, dx + b * step, dy + c * step)
                                 for a, b, c in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))])
    found, s, dx, dy = best
    far = 1000.0                                                      # so far that it sees without perspective
    return found, photo_of.Camera(np.array([0.0, 0.0, far]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]),
                                  np.array([0.0, 0.0, -1.0]), (far * s / 2, far * s / 2), (0.5 + dx, 0.5 + dy))


def facing(shape, outline, quarters=range(4)):
    """The shape turned so that it looks at the camera of its picture, and
    that camera (front_of); (None, shape): its outline does not cover the
    picture's. TripoSG mostly sees its picture from +z, but it turns some
    things its own way (a car or an animal seen from the side lies along
    another axis), so the four turns about the upright axis are tried; +z
    wins a tie. The turn is in the shape's metadata ("quarter"); `quarters`
    gives the one paint.py took."""
    best = None
    for quarter in quarters:
        turned = shape.copy()
        if quarter:
            turned.apply_transform(trimesh.transformations.rotation_matrix(quarter * np.pi / 2, [0.0, 1.0, 0.0]))
        found, camera = front_of(turned, outline)
        if best is None or found + (0.02 if quarter == 0 else 0.0) > best[0]:
            best = (found + (0.02 if quarter == 0 else 0.0), found, camera, turned, quarter)
    _, found, camera, turned, quarter = best
    turned.metadata["quarter"] = quarter
    print(f"Shape: its picture's outline covered at {100 * found:.0f} %" + (f", turned by {90 * quarter}°" if quarter else ""), flush=True)
    if found < float(os.environ.get("MAG_SHAPE_COVER", "0.65")) or camera is None:
        return None, shape
    return camera, turned


def moved(camera, r, t):
    """The camera of the shape's frame where the shape was placed (r, t)."""
    grow = float(np.cbrt(np.linalg.det(r)))
    turn = r / grow
    out = photo_of.Camera(r @ camera.eye + t, turn @ camera.right, turn @ camera.upward, turn @ camera.forward,
                          camera.scale, camera.shift)
    out.cover, out.colour = 1.0, 0.0
    return out


def by_camera(shape, points, camera):
    """Where the shape stands when the photo's camera (found from the splat)
    sees it as TripoSG's picture saw it: its +z towards the camera, its +y up
    in the photo, as large as the splat across the view, its front where the
    splat's front is. (r, t), or None."""
    turn = np.column_stack([camera.right, camera.upward, -camera.forward]).astype(np.float64)
    pts = np.asarray(points, np.float64)
    local = (pts - pts.mean(axis=0)) @ turn                              # the splat in the camera's axes
    own = np.asarray(shape.vertices, np.float64)
    theirs = np.percentile(local, 98, axis=0) - np.percentile(local, 2, axis=0)
    mine = np.percentile(own, 98, axis=0) - np.percentile(own, 2, axis=0)
    grow = float(np.median(theirs[:2] / np.maximum(mine[:2], 1e-9)))
    if not np.isfinite(grow) or grow <= 0:
        return None
    middle = (np.percentile(local, 98, axis=0) + np.percentile(local, 2, axis=0)) / 2
    mid = (np.percentile(own, 98, axis=0) + np.percentile(own, 2, axis=0)) / 2 * grow
    shift = middle - mid
    shift[2] = np.percentile(local[:, 2], 98) - np.percentile(own[:, 2], 98) * grow    # front on front
    r = grow * turn
    return r, turn @ shift + pts.mean(axis=0)


def shape_picture(job):
    """The picture the shape was made from, as fine as the photo has it, and
    the thing's outline in it; None: the shape stage left none."""
    fine, plain = job / "outputs" / "shape_photo.png", job / "outputs" / "shape_input.png"
    try:
        if fine.is_file():
            picture = Image.open(fine).convert("RGBA")
            rgba = np.asarray(picture, np.float32) / 255.0
            return rgba[..., :3].copy(), rgba[..., 3] > 0.5
        if plain.is_file():                                         # made before the fine one was kept: on white
            from scipy.ndimage import label
            rgb = np.asarray(Image.open(plain).convert("RGB"), np.float32) / 255.0
            white, _ = label(rgb.min(axis=2) >= 0.97)
            around = np.unique(np.concatenate([white[0], white[-1], white[:, 0], white[:, -1]]))
            return rgb, ~np.isin(white, around[around > 0])           # white within the thing is the thing
    except (OSError, ValueError):
        pass
    return None


def painted(job):
    """MV-Adapter's six views of the shape (paint.py), each with its camera
    in the shape's frame (after its quarter turn) and where the shape is in
    it; and that quarter. (None, None): the job has none."""
    import json
    try:
        note = json.loads((job / "outputs" / "views.json").read_text())
        row = np.asarray(Image.open(job / "outputs" / "views.png").convert("RGB"), np.float32) / 255.0
        masks = np.asarray(Image.open(job / "outputs" / "views_mask.png").convert("L")) > 127
    except (OSError, ValueError):
        return None, None
    side, edge, far = row.shape[0], masks.shape[0], 1000.0           # the views may be finer than their masks
    grow = far * float(note["scale"]) / (2 * float(note["half"]))
    views = []
    for i, v in enumerate(note["views"]):
        look = np.asarray(v["look"], np.float64)
        camera = photo_of.Camera(-look * far, np.asarray(v["right"], np.float64), np.asarray(v["up"], np.float64), look,
                                 (grow, grow), (0.5, 0.5))
        views.append({"camera": camera, "image": row[:, i * side:(i + 1) * side], "outline": masks[:, i * edge:(i + 1) * edge]})
    return views, int(note.get("quarter", 0))


def solid(mesh):
    """Is this a solid: closed, every edge between exactly two faces that
    agree on which side is outside?"""
    return len(mesh.faces) > 0 and mesh.is_watertight and mesh.is_winding_consistent


def mended(mesh):
    """A mesh that lost its soundness when faces were taken out (an edge with
    three faces, a sliver, a loose flake): those go, and every gap they leave
    is closed again with a fan of faces around its middle."""
    whole = trimesh.Trimesh(np.asarray(mesh.vertices, np.float64), np.asarray(mesh.faces, np.int64), process=False)
    whole.merge_vertices()                       # two corners at one place are one corner
    faces = np.asarray(whole.faces, np.int64)
    verts = np.asarray(whole.vertices, np.float64)
    for _ in range(4):
        faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
        faces = faces[np.unique(np.sort(faces, axis=1), axis=0, return_index=True)[1]]
        edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        _, where, count = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        crowded = (count[where.ravel()] > 2).reshape(-1, 3).any(axis=1)
        if not crowded.any():
            break
        faces = faces[~crowded]
    # The open edges, each in the direction its face runs; a gap is a ring of them.
    directed = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    key = np.sort(directed, axis=1)
    _, where, count = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    open_edges = directed[count[where.ravel()] == 1]
    after = {}
    for start, end in open_edges:
        after.setdefault(int(end), []).append(int(start))            # a filling face runs the other way round
    extra_verts, extra_faces = [], []
    while after:
        first = next(iter(after))
        ring, at = [first], first
        while True:
            nexts = after.get(at)
            if not nexts:
                break
            at = nexts.pop()
            if not after[ring[-1]]:
                del after[ring[-1]]
            if at == first:
                break
            ring.append(at)
        if at != first or len(ring) < 3:
            continue
        if len(ring) == 3:
            extra_faces.append(ring)
            continue
        middle = len(verts) + len(extra_verts)
        extra_verts.append(verts[ring].mean(axis=0))
        extra_faces += [[ring[i], ring[(i + 1) % len(ring)], middle] for i in range(len(ring))]
    if extra_verts:
        verts = np.vstack([verts, np.asarray(extra_verts)])
    if extra_faces:
        faces = np.vstack([faces, np.asarray(extra_faces, np.int64)])
    out = trimesh.Trimesh(verts, faces, process=False)
    out.remove_unreferenced_vertices()
    # Flakes that hang on nothing are no part of the thing.
    parts = out.split(only_watertight=False)
    if len(parts) > 1:
        most = max(len(part.faces) for part in parts)
        out = trimesh.util.concatenate([part for part in parts if len(part.faces) >= 0.03 * most])
    return out


def unpinched(mesh):
    """The mesh without corners where two sheets of it only touch (a pinch:
    closed edge by edge, but no solid a printer or a rig can take). Every
    fan of faces around a corner gets a corner of its own."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    faces = np.asarray(mesh.faces, np.int64)
    count = len(faces)
    corner = np.arange(3 * count).reshape(count, 3)
    first, second = faces[:, [0, 1, 2]].ravel(), faces[:, [1, 2, 0]].ravel()
    here, there = corner[:, [0, 1, 2]].ravel(), corner[:, [1, 2, 0]].ravel()
    key = np.minimum(first, second) * (int(faces.max()) + 1) + np.maximum(first, second)
    order = np.argsort(key, kind="stable")
    pos = np.flatnonzero(key[order][:-1] == key[order][1:])
    one, two = order[pos], order[pos + 1]
    # At a shared edge a-b the two faces run opposite ways: one's a is the other's b.
    same = first[one] == first[two]
    a = np.concatenate([here[one], there[one]])
    b = np.concatenate([np.where(same, here[two], there[two]), np.where(same, there[two], here[two])])
    fans, label = connected_components(coo_matrix((np.ones(len(a), bool), (a, b)), shape=(3 * count, 3 * count)), directed=False)
    if fans == len(mesh.vertices):
        return mesh
    place = np.zeros(fans, np.int64)
    place[label] = faces.ravel()
    return trimesh.Trimesh(np.asarray(mesh.vertices)[place], label.reshape(count, 3), process=False)


def fewer(mesh, faces):
    """The mesh with about `faces` faces, and still a solid. Taking faces out
    can break one; then it is mended, and if that does not make it whole,
    fewer faces are taken out."""
    if len(mesh.faces) <= faces:
        return mesh
    # The tool that takes faces out measures its errors in plain numbers: a
    # thing one unit large is to it a speck, and it breaks it. So it gets
    # the thing a thousand units large.
    scale = 1000.0 / max(float(np.max(mesh.extents)), 1e-9)
    large = trimesh.Trimesh(np.asarray(mesh.vertices) * scale, mesh.faces, process=False)
    count = faces
    while count < len(mesh.faces):
        small = large.simplify_quadric_decimation(face_count=count)
        small = trimesh.Trimesh(np.asarray(small.vertices) / scale, small.faces, process=False)
        for _ in range(5):                       # closing a gap can crowd an edge again
            if solid(small):
                break
            small = mended(small)
        if solid(small):
            return small
        count = int(count * 1.3)
    return mesh


def reconstruct(input_path: Path, output_dir: Path, quality="normal"):
    if quality not in QUALITY_PRESETS:
        raise ValueError(f"Ungültige Qualitätsstufe: {quality}")
    settings = QUALITY_PRESETS[quality]
    resolution = settings["resolution"]
    output_dir.mkdir(parents=True, exist_ok=True)
    ply = PlyData.read(str(input_path))
    vertices = ply["vertex"].data
    names = vertices.dtype.names
    xyz = np.column_stack([vertices[k] for k in ("x", "y", "z")]).astype(np.float32)
    if len(xyz) < 32:
        raise ValueError("Zu wenige Gaussians")
    mask = np.isfinite(xyz).all(axis=1)
    if "opacity" in names:
        op = vertices["opacity"].astype(np.float32)
        alpha = 1.0 / (1.0 + np.exp(-np.clip(op, -15, 15)))
        mask &= alpha > 0.02                              # what cannot be seen is not there
    else:
        alpha = np.ones(len(xyz), dtype=np.float32)
    kept = np.flatnonzero(mask)                           # every Gaussian that can be seen: none is left out
    if len(kept) < 32:
        raise ValueError("Keine gültigen Gaussians")
    xyz = xyz[kept]
    alpha = alpha[kept]
    colors = opacity_colors(vertices, names, kept)
    # Every Gaussian has its own three widths and its own turn; a file without them has small balls.
    if all("scale_" + str(i) in names for i in range(3)):
        scales = np.exp(np.clip(np.column_stack([vertices["scale_" + str(i)][kept] for i in range(3)]), -12, 3)).astype(np.float32)
    else:
        scales = np.full((len(xyz), 3), 0.01, np.float32)
    if all("rot_" + str(i) in names for i in range(4)):
        turns = np.column_stack([vertices["rot_" + str(i)][kept] for i in range(4)]).astype(np.float32)
    else:
        turns = np.tile(np.array([1.0, 0.0, 0.0, 0.0], np.float32), (len(xyz), 1))
    # For the colours: no Gaussian is thinner than a third of the usual way to its neighbour.
    gap = cKDTree(xyz).query(xyz[::max(1, len(xyz) // 20000)], k=2, workers=8)[0][:, 1]

    lo = np.percentile(xyz, settings["crop"], axis=0)
    hi = np.percentile(xyz, 100-settings["crop"], axis=0)
    span = np.maximum(hi-lo, 1e-4)
    pad = 0.10 * float(span.max())
    lo -= pad
    hi += pad
    spacing = float(np.max(hi-lo)) / (resolution-1)
    lo -= (PORE + 3) * spacing                            # room around it for the flood
    resolution += 2 * (PORE + 3)
    print(f"Idol mesh {quality}: {len(xyz)} Gaussians; {resolution}³ cells", flush=True)
    # The whole shape, where the shape stage made one (shape.py): fitted onto
    # the splat, it takes the place of the shell the splat stands for.
    fitted, front = None, None
    if (output_dir / "shape.ply").is_file():
        try:
            whole = trimesh.load(output_dir / "shape.ply", force="mesh", process=True)
            # The camera of the picture it was made from, in its own frame: the photo is laid on with it.
            picture = shape_picture(output_dir)
            views, quarter = painted(output_dir)
            own, whole = facing(whole, picture[1], range(4) if views is None else (quarter,)) if picture is not None else (None, whole)
            strong = alpha > 0.3
            fitted = fit_shape(whole, xyz[strong])
            source = the_photo(input_path.parent) if fitted is None else None
            if source is not None:                                # does not fit: where the photo's camera saw it
                camera = photo_of.fit(xyz[strong], colors[strong], source["prepared"], distance=source["distance"])
                start = by_camera(whole, xyz[strong], camera) if camera is not None else None
                if start is not None:
                    fitted = fit_shape(whole, xyz[strong], start=start)
            if fitted is not None and own is not None:
                placed = fitted.metadata["placed"]
                front = {"camera": moved(own, *placed), "fine": picture[0], "outline": picture[1],
                         "views": [dict(v, camera=moved(v["camera"], *placed)) for v in views] if views else None}
        except Exception as exc:
            print(f"Shape: not used ({type(exc).__name__})", flush=True)
            fitted, front = None, None
    if fitted is not None:
        geometry = fitted
        for _ in range(5):                                # closed, whatever TripoSG's grid left open
            if solid(geometry):
                break
            geometry = mended(geometry)
    else:
        geometry = shell_of(xyz, alpha, scales, turns, lo, spacing, resolution)
    if geometry.volume < 0:
        geometry.invert()                                 # its faces look outwards
    if fitted is None:
        # Taubin lambda/mu smoothing (non-shrinking); fewer passes for thin details.
        trimesh.smoothing.filter_taubin(
            geometry, lamb=0.45, nu=0.48, iterations=settings["smooth"])
    geometry = unpinched(fewer(geometry, settings["print_faces"]))
    # The model that is shown has as many faces as a model needs. Its colours
    # do not come from its faces: they are baked from the splat at every texel.
    shown = unpinched(fewer(geometry, settings["faces"]))
    # The worker reads the two MAG-STEP lines to show where the job is.
    print("MAG-STEP texture", flush=True)
    vmapping, uv_faces, uvs, texture = bake_texture(
        shown.vertices, shown.faces, xyz, alpha, footprints(scales, turns, 0.35 * float(np.median(gap))), colors,
        spacing, settings["texture"], settings["neighbors"],
        float(os.environ.get("MAG_IDOL_UNWRAP_S", settings["unwrap"])), the_photo(input_path.parent), front)
    export(shown, geometry, vmapping, uv_faces, uvs, texture, output_dir, settings, idol_frame=True)


def export(shown, geometry, vmapping, uv_faces, uvs, texture, output_dir, settings, idol_frame):
    """mesh.glb (the shown mesh with its texture, y up, standing on y = 0),
    mesh.json (from where the photo looked, in the GLB's axes) and mesh.stl
    (the finer mesh, z up, 120 mm tall). `idol_frame`: the meshes are in
    Idol's frame (y down), else in TripoSG's (y up)."""
    print("MAG-STEP export", flush=True)
    turn = trimesh.transformations.rotation_matrix(np.pi, [0.0, 0.0, 1.0]) if idol_frame else np.eye(4)
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=texture, metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(
        vertices=np.asarray(shown.vertices)[vmapping], faces=uv_faces,
        visual=trimesh.visual.TextureVisuals(uv=uvs, material=material),
        process=False)
    # Idol Y-down -> GLB Y-up. Verified on the teapot: rotate about camera Z.
    mesh.apply_transform(turn)
    mesh.apply_translation([0.0, -float(mesh.bounds[0, 1]), 0.0])
    mesh.export(output_dir / "mesh.glb", file_type="glb")
    # From where the photo looked at the model, in the axes of the GLB (y up): a
    # scene made of several models of one photo turns each the way it stood.
    if "from" in SEEN:
        import json
        towards = turn[:3, :3] @ np.asarray(SEEN["from"], np.float64)
        (output_dir / "mesh.json").write_text(json.dumps({"seen_from": [round(float(v), 4) for v in towards]}))

    # Print STL, without colors: +90° X rotates Y-up to Z-up, then scale to mm.
    stl = geometry.copy()
    stl.apply_transform(turn)
    stl.apply_translation([0.0, -float(stl.bounds[0, 1]), 0.0])
    stl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1.0, 0.0, 0.0]))
    target_height = float(os.environ.get("MAG_IDOL_STL_HEIGHT_MM", "120"))
    if not np.isfinite(target_height) or target_height <= 0:
        raise ValueError("MAG_IDOL_STL_HEIGHT_MM must be positive")
    measured_height = float(stl.extents[2])
    if measured_height <= 0:
        raise ValueError("STL has no usable height")
    stl.apply_scale(target_height / measured_height)
    stl.apply_translation([0.0, 0.0, -float(stl.bounds[0, 2])])
    stl.export(output_dir / "mesh.stl", file_type="stl")
    print(f"Mesh: {len(shown.vertices)} vertices, {len(shown.faces)} faces (STL {len(geometry.faces)}), "
          f"{'solid' if solid(shown) and solid(geometry) else 'NOT SOLID'}; {settings['texture']}² PNG in GLB; STL height {target_height:g} mm",
          flush=True)


def reconstruct_shape(output_dir: Path, quality="normal"):
    """The model from the whole shape and the photo alone, without Idol's
    splat: TripoSG's shape lies exactly under the picture it was made from
    (front_of), so the side the photo shows takes the photo and the rest its
    colours carried over the surface. The splat would only say where the
    shape stands, and sampling it takes the MI50 a quarter of an hour. Raises
    when the shape or its picture does not do (the worker then samples)."""
    if quality not in QUALITY_PRESETS:
        raise ValueError(f"Ungültige Qualitätsstufe: {quality}")
    settings = QUALITY_PRESETS[quality]
    whole = trimesh.load(output_dir / "shape.ply", force="mesh", process=True)
    picture = shape_picture(output_dir)
    if picture is None:
        raise RuntimeError("Das Bild der Form fehlt")
    views, quarter = painted(output_dir)
    own, whole = facing(whole, picture[1], range(4) if views is None else (quarter,))
    if own is None:
        raise RuntimeError("Die Form deckt ihr Bild nicht")
    own.cover, own.colour = 1.0, 0.0
    geometry = whole
    for _ in range(5):                                    # closed, whatever TripoSG's grid left open
        if solid(geometry):
            break
        geometry = mended(geometry)
    if not solid(geometry):
        raise RuntimeError("Die Form ist nicht geschlossen")
    if geometry.volume < 0:
        geometry.invert()
    print(f"Shape alone: {len(geometry.faces)} faces, no splat", flush=True)
    geometry = unpinched(fewer(geometry, settings["print_faces"]))
    shown = unpinched(fewer(geometry, settings["faces"]))
    print("MAG-STEP texture", flush=True)
    vmapping, uv_faces, uvs, texture = bake_texture(
        shown.vertices, shown.faces, None, None, None, None, 1.0, settings["texture"], settings["neighbors"],
        float(os.environ.get("MAG_IDOL_UNWRAP_S", settings["unwrap"])), None,
        {"camera": own, "fine": picture[0], "outline": picture[1], "views": views})
    # As large as Idol's models are (about one unit at its largest), so it stands among them in a scene.
    grow = 1.0 / max(float(np.max(geometry.extents)), 1e-9)
    shown = trimesh.Trimesh(np.asarray(shown.vertices) * grow, shown.faces, process=False)
    geometry = trimesh.Trimesh(np.asarray(geometry.vertices) * grow, geometry.faces, process=False)
    export(shown, geometry, vmapping, uv_faces, uvs, texture, output_dir, settings, idol_frame=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--quality", choices=tuple(QUALITY_PRESETS), default="normal")
    parser.add_argument("--alone", action="store_true", help="from shape.ply and its picture, without the splat")
    args = parser.parse_args()
    if args.alone:
        reconstruct_shape(args.out, args.quality)
    else:
        reconstruct(args.ply, args.out, args.quality)

#!/usr/bin/env python3
"""Idol splat -> a solid, coloured model (GLB) and a printable one (STL). CPU only.

A splat is a cloud of small flat Gaussians that lie on what the photo showed.
The model is made from them the way they are:

1. Every Gaussian with its own size and its own turn (none left out) is
   written into a grid as opacity: a cell is as opaque as the Gaussians in
   it together.
2. Where that is half opaque is the shell. The grid is flooded from outside:
   what the flood does not reach is the body. A photo shows one side; where
   the Gaussians of the other sides leave a gap, the flood gets in. So what
   is walled in is body all the same, the tear is closed, and the underside
   nobody saw is closed flat (surface.py). Loose bits fall away; what is
   big enough to be a figure of its own (a photo may show two) stays.
3. The surface is taken from that body, placed between the cells by the
   Gaussians themselves (each knows its plane), so it has no steps. Then it
   is brought down to the number of faces a model needs, and it stays a
   solid through that: closed, every edge between two faces, faces outwards,
   no bubble inside, no corner where two sheets only touch.
4. That small mesh is unwrapped, and the texture is baked for it from the
   splat itself: the colours are as fine as the Gaussians, whatever the
   faces. The side the photo shows takes its colours from the photo, which
   is finer still (photo.py finds the photo's camera again). The STL keeps
   the finer mesh.

The original splat.ply is never modified.
"""
import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from plyfile import PlyData
from scipy.ndimage import binary_dilation, binary_erosion, distance_transform_edt, gaussian_filter
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
    "fast": {"resolution": 128, "faces": 40000, "print_faces": 150000, "texture": 2048, "smooth": 5, "crop": 0.5, "neighbors": 8, "unwrap": 30},
    "normal": {"resolution": 224, "faces": 80000, "print_faces": 400000, "texture": 4096, "smooth": 5, "crop": 0.1, "neighbors": 12, "unwrap": 60},
    "high": {"resolution": 320, "faces": 160000, "print_faces": 800000, "texture": 4096, "smooth": 4, "crop": 0.02, "neighbors": 16, "unwrap": 120},
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


def gaussian_albedo(points, tree, centers, colors, alpha, radii, spacing, neighbors):
    """The colour of the splat at points of the surface: the Gaussians around
    a point, each as far as it reaches itself (`radii`: its own width, and
    never less than half the way to its neighbour, so no gaps show). The
    colours are as fine as the Gaussians are, not as coarse as the grid the
    surface was taken from.

    Chunking is handled by the caller, including 4K UV texel baking.
    """
    distances, ids = tree.query(points, k=min(neighbors, len(centers)), workers=8)
    if np.ndim(ids) == 1:
        ids, distances = ids[:, None], distances[:, None]
    width = radii[ids]
    weight = alpha[ids] * np.exp(
        -0.5 * np.minimum((distances / width)**2, 80.0))
    # Prefer high-confidence near splats while blending neighbours.
    weight = np.maximum(weight, 0.0) ** 1.5
    total = weight.sum(axis=1)
    rgb = (weight[..., None] * colors[ids]).sum(axis=1)
    rgb /= np.maximum(total[:, None], 1e-12)
    weak = total < 1e-10
    if np.any(weak):
        # Never turn weak areas into nearest-neighbour vertex colours.
        # Far from every Gaussian (an underside that was closed, a filled room): the colours around it, softly.
        fallback = alpha[ids[weak]] / np.maximum(distances[weak], spacing*0.25)**2
        fallback /= np.maximum(fallback.sum(axis=1, keepdims=True), 1e-12)
        rgb[weak] = (fallback[..., None] * colors[ids[weak]]).sum(axis=1)
    return np.clip(rgb, 0.0, 1.0)


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
        return {"prepared": prepared, "fine": fine, "outline": outline, "distance": distance}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def bake_texture(verts, faces, xyz, alpha, radii, colors, spacing, size, neighbors, seconds, source=None):
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
    if source is not None:
        strong = alpha > 0.3
        camera = photo_of.fit(xyz[strong], colors[strong], source["prepared"], distance=source["distance"])
        print("Photo: " + ("its camera was not found again; colours from the splat alone" if camera is None else
                           f"camera found again (outline {camera.cover:.2f}, colours apart {camera.colour:.3f}); {source['fine'].shape[0]} px across"), flush=True)
    where = np.zeros((len(all_pixels), 3), np.float32) if camera is not None else None
    facing = np.zeros((len(all_pixels), 3), np.float32) if camera is not None else None
    corners = np.asarray(verts, np.float32)[vmapping][uv_faces]
    outward = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    outward /= np.maximum(np.linalg.norm(outward, axis=1, keepdims=True), 1e-12)
    del corners
    tree = cKDTree(xyz)
    # A Gaussian colours as far as it is wide, and at least half the way to the next one.
    radii = np.maximum(radii, 0.5 * tree.query(xyz, k=2, workers=8)[0][:, 1]).astype(np.float32)
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
        rgb = gaussian_albedo(positions, tree, xyz, colors, alpha, radii,
                              spacing, neighbors)
        tex_flat[samples] = np.rint(rgb*255).astype(np.uint8)
        if camera is not None:
            where[start:start+len(samples)] = positions
            facing[start:start+len(samples)] = outward[fi]
    if camera is not None:
        rgb, trust = photo_of.colours(camera, where, facing, source["fine"], source["outline"])
        mixed = tex_flat[all_pixels].astype(np.float32) / 255.0
        mixed += trust[:, None] * (rgb - mixed)
        tex_flat[all_pixels] = np.rint(np.clip(mixed, 0, 1) * 255).astype(np.uint8)
        print(f"Photo: {100.0 * float((trust > 0.5).mean()):.0f} % of the surface takes its colours from it", flush=True)
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


def surface_field(body, added, xyz, alpha, scales, turns, lo, spacing):
    """How far every cell is inside the body (in cells, negative: outside),
    exact to a fraction of a cell where it matters: next to the surface.

    The cells only know inside from outside, and a surface taken from that
    has steps as high as a cell. But the Gaussians lie on the surface, each
    a small flat disc that knows its own plane. So next to the body's edge a
    cell asks the Gaussians around it where their sheet runs, and takes its
    distance from that. Where the body was closed or filled (`added`) there
    are no Gaussians to ask; there the edge of the body is taken, rounded."""
    far = np.clip(distance_transform_edt(body) - distance_transform_edt(~body), -4.0, 4.0).astype(np.float32)
    far -= np.where(far > 0, 0.5, -0.5).astype(np.float32)     # the edge runs between the cells
    rounded = gaussian_filter(far, sigma=1.5)
    # The layers the surface runs in: the shell is a few cells thick around its sheet.
    edge = binary_dilation(body) & ~binary_erosion(body, iterations=3)
    if added.any():
        edge &= ~binary_dilation(added, iterations=2)
    cells = np.argwhere(edge)
    shift = 1.0
    deep = None
    # Only the Gaussians of the outer sheet are asked: below a gap in it the
    # nearest ones would be those of a sheet inside.
    home = np.clip(np.rint((xyz - lo) / spacing).astype(np.int64), 0, np.array(body.shape) - 1)
    outer = edge[home[:, 0], home[:, 1], home[:, 2]]
    xyz, alpha, scales, turns = xyz[outer], alpha[outer], scales[outer], turns[outer]
    if len(cells) and len(xyz) >= 16:
        points = (lo + cells * spacing).astype(np.float32)
        tree = cKDTree(xyz)
        apart, ids = tree.query(points, k=min(12, len(xyz)), workers=8, distance_upper_bound=3.0 * spacing)
        near = np.isfinite(apart[:, 0])
        cells, points, apart, ids = cells[near], points[near], apart[near], ids[near]
        found = np.isfinite(apart)
        ids = np.where(found, ids, 0)
        weight = np.where(found, alpha[ids] * np.exp(-0.5 * (np.where(found, apart, 0.0) / spacing) ** 2), 0.0).astype(np.float32)
        total = np.maximum(weight.sum(axis=1, keepdims=True), 1e-12)
        middle = (weight[..., None] * xyz[ids]).sum(axis=1) / total
        # A Gaussian's plane is across its thinnest width. Which way is in: where the body gets deeper.
        q = turns / np.maximum(np.linalg.norm(turns, axis=1, keepdims=True), 1e-12)
        w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        axes = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1),
                         np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1),
                         np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1)], axis=1)
        normal = axes[np.arange(len(xyz)), :, np.argmin(scales, axis=1)].astype(np.float32)
        inward = np.stack(np.gradient(rounded), axis=-1)[cells[:, 0], cells[:, 1], cells[:, 2]]
        inward /= np.maximum(np.linalg.norm(inward, axis=1, keepdims=True), 1e-9)
        each = normal[ids]
        each = each * np.sign((each * inward[:, None, :]).sum(axis=2, keepdims=True))
        plane = (weight[..., None] * each).sum(axis=1)
        length = np.linalg.norm(plane, axis=1)
        plane /= np.maximum(length, 1e-9)[:, None]
        # Where the Gaussians do not agree on a plane (an edge, a groove), the body says which way is in.
        sure = (length / total[:, 0] > 0.5) & ((plane * inward).sum(axis=1) > 0.3)
        plane = np.where(sure[:, None], plane, inward)
        deep = ((points - middle) * plane).sum(axis=1) / spacing
        # The sheet runs a little inside the body's edge, everywhere about as far. Where a cell's
        # answer is far from that (the Gaussians around it are few, or not of this surface), the
        # body's edge is taken, as far inside as the sheet runs elsewhere.
        here = rounded[cells[:, 0], cells[:, 1], cells[:, 2]]
        if sure.any():
            shift = float(np.clip(np.median((here - deep)[sure]), 0.0, 2.5))
        deep = np.where(np.abs(here - deep - shift) <= 0.75, deep, here - shift)
    field = np.clip(rounded - shift, -2.0, 2.0)
    if deep is not None:
        field[cells[:, 0], cells[:, 1], cells[:, 2]] = np.clip(deep, -2.0, 2.0)
    return gaussian_filter(field, sigma=0.5, output=np.float32)


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
    gaussian_radius = np.exp(np.log(scales).mean(axis=1))   # for the colours: how far a Gaussian's colour reaches

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
    opaque = opacity_field(xyz, alpha, scales, turns, lo, spacing, resolution)
    body, added = body_of(opaque, down=3, parts=True)     # Idol's y points down: the thing stands on its higher y
    del opaque
    field = surface_field(body, added, xyz, alpha, scales, turns, lo, spacing)
    del body, added
    verts, faces, _, _ = marching_cubes(field, level=0.0, spacing=(spacing,)*3, allow_degenerate=False)
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
    if geometry.volume < 0:
        geometry.invert()                                 # its faces look outwards
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
        shown.vertices, shown.faces, xyz, alpha, gaussian_radius, colors,
        spacing, settings["texture"], settings["neighbors"],
        float(os.environ.get("MAG_IDOL_UNWRAP_S", settings["unwrap"])), the_photo(input_path.parent))
    print("MAG-STEP export", flush=True)

    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=texture, metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(
        vertices=np.asarray(shown.vertices)[vmapping], faces=uv_faces,
        visual=trimesh.visual.TextureVisuals(uv=uvs, material=material),
        process=False)
    # Idol Y-down -> GLB Y-up. Verified on the teapot: rotate about camera Z.
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0.0, 0.0, 1.0]))
    mesh.apply_translation([0.0, -float(mesh.bounds[0, 1]), 0.0])
    mesh.export(output_dir / "mesh.glb", file_type="glb")

    # Print STL, without colors: +90° X rotates Y-up to Z-up, then scale to mm.
    stl = geometry.copy()
    stl.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0.0, 0.0, 1.0]))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--quality", choices=tuple(QUALITY_PRESETS), default="normal")
    args = parser.parse_args()
    reconstruct(args.ply, args.out, args.quality)

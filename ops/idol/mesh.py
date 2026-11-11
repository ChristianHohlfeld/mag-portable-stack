#!/usr/bin/env python3
"""Idol splat -> a closed, coloured model (GLB) and a printable one (STL). CPU only.

A splat is a cloud of small flat Gaussians that lie on what the photo showed.
The surface is made from them the way they are:

1. Every Gaussian with its own size and its own turn (none left out) is
   written into a grid as opacity: a cell is as opaque as the Gaussians in
   it together.
2. Where that is half opaque is the shell. The grid is flooded from outside:
   what the flood does not reach is the body, so it is closed, and shells
   and bubbles inside it are gone. Pores the size of a cell or two are
   closed before the flood. Loose bits fall away; what is big enough to be
   a figure of its own (a photo may show two) stays.
3. The surface is taken anew from that body (uniform triangles, no holes),
   smoothed, and brought down to the number of faces a model needs.
4. That small mesh is unwrapped, and the texture is baked for it from the
   splat itself: the colours are as fine as the atlas, whatever the faces.
   The STL keeps the finer mesh.

The original splat.ply is never modified.
"""
import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from plyfile import PlyData
from scipy.ndimage import binary_dilation, distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
import trimesh

from surface import PORE, body_of, opacity_field
from unwrap import unwrap

# Single source of truth for mesh preset parameters. "resolution": cells of
# the grid along its longest side. "faces": of the model that is shown,
# "print_faces": of the STL. "unwrap": seconds xatlas gets for the UVs of the
# small mesh before it is laid out plainly instead (see unwrap.py).
QUALITY_PRESETS = {
    "fast": {"resolution": 128, "faces": 40000, "print_faces": 150000, "texture": 1024, "smooth": 2, "crop": 0.5, "neighbors": 8, "unwrap": 30},
    "normal": {"resolution": 224, "faces": 80000, "print_faces": 400000, "texture": 2048, "smooth": 3, "crop": 0.1, "neighbors": 12, "unwrap": 60},
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


def gaussian_albedo(points, tree, centers, colors, alpha, radii, spacing, neighbors):
    """Gather multiple nearby splats per surface sample; opacity * Gaussian weight.

    Chunking is handled by the caller, including 4K UV texel baking.
    """
    distances, ids = tree.query(points, k=min(neighbors, len(centers)), workers=8)
    if np.ndim(ids) == 1:
        ids, distances = ids[:, None], distances[:, None]
    width = np.maximum(radii[ids]*1.5, spacing * 1.25)
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
        fallback = alpha[ids[weak]] / np.maximum(distances[weak], spacing*0.25)**2
        fallback /= np.maximum(fallback.sum(axis=1, keepdims=True), 1e-12)
        rgb[weak] = (fallback[..., None] * colors[ids[weak]]).sum(axis=1)
    return np.clip(rgb, 0.0, 1.0)


def bake_texture(verts, faces, xyz, alpha, radii, colors, spacing, size, neighbors, seconds):
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
    tree = cKDTree(xyz)
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
    texture = tex_flat.reshape(size, size, 3)
    # Extend colors 8 pixels beyond UV islands to avoid bilinear seam bleed.
    filled = covered.reshape(size, size)
    rim = binary_dilation(filled, iterations=8) & ~filled
    if rim.any():
        nearest = distance_transform_edt(~filled, return_distances=False,
                                         return_indices=True)
        texture[rim] = texture[nearest[0][rim], nearest[1][rim]]
    return vmapping, uv_faces, uvs, Image.fromarray(texture, mode="RGB")


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
    body = body_of(opaque)
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
    # Taubin lambda/mu smoothing (non-shrinking); fewer passes for thin details.
    trimesh.smoothing.filter_taubin(
        geometry, lamb=0.45, nu=0.48, iterations=settings["smooth"])
    if len(geometry.faces) > settings["print_faces"]:
        geometry = geometry.simplify_quadric_decimation(face_count=settings["print_faces"])
    # The model that is shown has as many faces as a model needs. Its colours
    # do not come from its faces: they are baked from the splat at every texel.
    shown = geometry
    if len(shown.faces) > settings["faces"]:
        shown = geometry.simplify_quadric_decimation(face_count=settings["faces"])
    # The worker reads the two MAG-STEP lines to show where the job is.
    print("MAG-STEP texture", flush=True)
    vmapping, uv_faces, uvs, texture = bake_texture(
        shown.vertices, shown.faces, xyz, alpha, gaussian_radius, colors,
        spacing, settings["texture"], settings["neighbors"],
        float(os.environ.get("MAG_IDOL_UNWRAP_S", settings["unwrap"])))
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
          f"{'closed' if geometry.is_watertight else 'open'}; {settings['texture']}² PNG in GLB; STL height {target_height:g} mm",
          flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--quality", choices=tuple(QUALITY_PRESETS), default="normal")
    args = parser.parse_args()
    reconstruct(args.ply, args.out, args.quality)

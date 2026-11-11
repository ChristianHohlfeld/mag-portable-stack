#!/usr/bin/env python3
"""Bounded, CPU-only Idol splat -> coloured UV/GLB and printable STL conversion.

Approximate surface reconstruction: high quality reduces aliasing, but an Idol
splat does not guarantee watertight geometry or accurate hidden surfaces.
The original splat.ply is never modified.
"""
import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from plyfile import PlyData
from scipy.ndimage import gaussian_filter, binary_dilation, distance_transform_edt
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
import trimesh

from unwrap import unwrap

# Single source of truth for mesh preset parameters.
QUALITY_PRESETS = {
    "fast": {"resolution": 128, "max_points": 150000, "max_faces": 150000,
             "texture": 1024, "smooth": 2, "percentile": 52, "floor": 0.035,
             "crop": 0.5, "neighbors": 8, "unwrap": 90},
    "normal": {"resolution": 224, "max_points": 400000, "max_faces": 400000,
               "texture": 2048, "smooth": 3, "percentile": 43, "floor": 0.03,
               "crop": 0.1, "neighbors": 12, "unwrap": 330},
    "high": {"resolution": 320, "max_points": 1000000, "max_faces": 800000,
             "texture": 4096, "smooth": 2, "percentile": 30, "floor": 0.02,
             "crop": 0.02, "neighbors": 16, "unwrap": 720},
}
# "unwrap": seconds xatlas gets for the UVs before the surface is laid out
# plainly instead.


def splat_density(xyz, alpha, radii, lo, spacing, resolution):
    """Float32 density, Gaussian-filtered in X slabs with a 4-sigma halo.

    Does not allocate three full resolution^3 impulse volumes. Slab halos
    give the same values as full-volume Gaussian filtering at each output
    voxel; peak buffers remain bounded even at the 320^3 high preset.
    """
    field = np.zeros((resolution,) * 3, dtype=np.float32)
    ijk = np.rint((xyz - lo) / spacing).astype(np.int32)
    valid = np.all((ijk >= 0) & (ijk < resolution), axis=1)
    ijk = ijk[valid]
    alpha = alpha[valid]
    radii = radii[valid]
    if len(ijk) < 32:
        raise ValueError("Zu wenige Gaussians innerhalb des Dichtegitters")

    # Use three scale bands, retaining small Gaussians that form handles/knobs.
    edges = np.quantile(radii, [0, 1/3, 2/3, 1])
    for group in range(3):
        selected = (radii >= edges[group]) & (
            (radii <= edges[group+1]) if group == 2 else (radii < edges[group+1])
        )
        if not np.any(selected):
            continue
        vox = ijk[selected]
        weights = alpha[selected]
        sigma = float(np.median(radii[selected]))
        halo = int(np.ceil(4.0 * sigma))
        for first in range(0, resolution, 32):
            last = min(resolution, first + 32)
            start = max(0, first - halo)
            end = min(resolution, last + halo)
            contributing = (vox[:, 0] >= start) & (vox[:, 0] < end)
            if not np.any(contributing):
                continue
            local = np.zeros((end-start, resolution, resolution), np.float32)
            points = vox[contributing]
            np.add.at(local, (points[:, 0] - start, points[:, 1], points[:, 2]),
                      weights[contributing])
            smoothed = gaussian_filter(local, sigma=sigma, mode="constant",
                                       output=np.float32, truncate=4.0)
            field[first:last] += smoothed[first-start:last-start]
    return field


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
    max_points = settings["max_points"]
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
        mask &= alpha > (0.04 if quality == "high" else 0.06)
    else:
        alpha = np.ones(len(xyz), dtype=np.float32)
    original_ids = np.flatnonzero(mask)
    if len(original_ids) < 32:
        raise ValueError("Keine gültigen Gaussians")
    if len(original_ids) > max_points:
        # Deterministic uniform sample, never accidentally dropping a whole range.
        chosen = np.linspace(0, len(original_ids)-1, max_points, dtype=np.int64)
        original_ids = original_ids[chosen]
    xyz = xyz[original_ids]
    alpha = alpha[original_ids]
    colors = opacity_colors(vertices, names, original_ids)

    # Keep each splat's geometric-mean radius, but never erase thin Gaussians.
    if all("scale_" + str(i) in names for i in range(3)):
        log_scale = np.column_stack([
            vertices["scale_" + str(i)][original_ids] for i in range(3)
        ])
        gaussian_radius = np.exp(np.clip(log_scale.mean(axis=1), -12, 3))
    else:
        gaussian_radius = np.full(len(xyz), 0.01, np.float32)

    lo = np.percentile(xyz, settings["crop"], axis=0)
    hi = np.percentile(xyz, 100-settings["crop"], axis=0)
    span = np.maximum(hi-lo, 1e-4)
    pad = 0.10 * float(span.max())
    lo -= pad
    hi += pad
    spacing = float(np.max(hi-lo)) / (resolution-1)
    radii_vox = np.clip(gaussian_radius / spacing, 0.7, 5.0)
    print(f"Idol mesh {quality}: {len(xyz)} Gaussians; {resolution}³ float32", flush=True)
    density = splat_density(xyz, alpha, radii_vox, lo, spacing, resolution)
    maximum = float(density.max())
    if maximum <= 0:
        raise ValueError("Leeres Dichtefeld")
    positive = density[density > maximum*0.005]
    if not len(positive):
        raise ValueError("Kein positives Dichtefeld")
    threshold = max(
        float(np.percentile(positive, settings["percentile"])),
        maximum*settings["floor"])
    if threshold >= maximum:
        threshold = maximum*0.5
    verts, faces, _, _ = marching_cubes(
        density, level=threshold, spacing=(spacing,)*3, allow_degenerate=False)
    del density
    verts += lo
    if not len(faces):
        raise ValueError("Marching Cubes hat keine Oberfläche erzeugt")
    geometry = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    # Taubin lambda/mu smoothing (non-shrinking); fewer passes for thin details.
    trimesh.smoothing.filter_taubin(
        geometry, lamb=0.45, nu=0.48, iterations=settings["smooth"])
    if len(geometry.faces) > settings["max_faces"]:
        geometry = geometry.simplify_quadric_decimation(
            face_count=settings["max_faces"])
        if len(geometry.faces) > settings["max_faces"]:
            raise RuntimeError("Dezimierung konnte Face-Limit nicht einhalten")

    # Preserve full colour resolution: bake using Gaussian neighborhoods at
    # each UV texel on the unrotated surface, before GLB/STL transforms.
    # The worker reads the two MAG-STEP lines to show where the job is.
    print("MAG-STEP texture", flush=True)
    vmapping, uv_faces, uvs, texture = bake_texture(
        geometry.vertices, geometry.faces, xyz, alpha, gaussian_radius, colors,
        spacing, settings["texture"], settings["neighbors"],
        float(os.environ.get("MAG_IDOL_UNWRAP_S", settings["unwrap"])))
    print("MAG-STEP export", flush=True)

    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=texture, metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(
        vertices=np.asarray(geometry.vertices)[vmapping], faces=uv_faces,
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
    print(f"Mesh: {len(geometry.vertices)} vertices, {len(geometry.faces)} faces; "
          f"{settings['texture']}² PNG in GLB; STL height {target_height:g} mm",
          flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--quality", choices=tuple(QUALITY_PRESETS), default="normal")
    args = parser.parse_args()
    reconstruct(args.ply, args.out, args.quality)

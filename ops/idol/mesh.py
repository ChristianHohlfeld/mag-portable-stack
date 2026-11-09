#!/usr/bin/env python3
"""Bounded, approximate Gaussian-splat-to-surface conversion (not lossless)."""
import argparse
from pathlib import Path

import numpy as np
from plyfile import PlyData
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
import trimesh

def reconstruct(input_path: Path, output_dir: Path, resolution=128, max_points=120000):
    ply = PlyData.read(str(input_path))
    vertices = ply["vertex"].data
    names = vertices.dtype.names
    xyz = np.column_stack([vertices[k] for k in ("x", "y", "z")]).astype(np.float32)
    if len(xyz) < 32: raise ValueError("Zu wenige Gaussians")
    mask = np.isfinite(xyz).all(axis=1)
    if "opacity" in names:
        op = vertices["opacity"].astype(np.float32)
        alpha = 1.0 / (1.0 + np.exp(-np.clip(op, -15, 15)))
        mask &= alpha > 0.06
    else:
        alpha = np.ones(len(xyz), dtype=np.float32)
    xyz, alpha = xyz[mask], alpha[mask]
    if len(xyz) < 32: raise ValueError("Keine gültigen Gaussians")
    if len(xyz) > max_points:
        # Stable, opacity-weighted, evenly spaced sampling to bound CPU/RAM.
        ids = np.linspace(0, len(xyz)-1, max_points, dtype=int)
        xyz, alpha = xyz[ids], alpha[ids]
        mask_indices = np.flatnonzero(mask)[ids]
    else: mask_indices = np.flatnonzero(mask)
    if all(k in names for k in ("f_dc_0", "f_dc_1", "f_dc_2")):
        colors = np.column_stack([vertices["f_dc_"+str(i)][mask_indices] for i in range(3)])
        colors = np.clip((colors * 0.28209479177387814 + 0.5)*255, 0, 255).astype(np.uint8)
    elif all(k in names for k in ("red", "green", "blue")):
        colors = np.column_stack([vertices[k][mask_indices] for k in ("red", "green", "blue")]).astype(np.uint8)
    else:
        colors = np.full((len(xyz), 3), 185, np.uint8)
    # 3DGS PLY stores log-scales. Use geometric-mean Gaussian radius per
    # component, grouped into three bounded kernels. Rotation/anisotropy are
    # intentionally approximated for an affordable CPU mesh.
    if all("scale_"+str(i) in names for i in range(3)):
        log_scale = np.column_stack([vertices["scale_"+str(i)][mask_indices] for i in range(3)])
        gaussian_radius = np.exp(np.clip(log_scale.mean(axis=1), -12, 3))
    else:
        gaussian_radius = np.full(len(xyz), 0.01)
    lo = np.percentile(xyz, 0.5, axis=0)
    hi = np.percentile(xyz, 99.5, axis=0)
    span = np.maximum(hi-lo, 1e-4)
    # Crop outliers and pad by 10% of the largest extent to leave a closed shell.
    pad = 0.10 * float(span.max())
    lo -= pad; hi += pad
    extent = np.max(hi-lo)
    spacing = float(extent) / (resolution-1)
    ijk = np.rint((xyz-lo)/spacing).astype(int)
    inside = np.all((ijk>=0)&(ijk<resolution), axis=1)
    ijk, xyz, alpha, colors = ijk[inside], xyz[inside], alpha[inside], colors[inside]
    radii = np.clip(gaussian_radius[inside]/spacing, 0.7, 5.0)
    blurred = np.zeros((resolution,)*3, dtype=np.float32)
    edges = np.quantile(radii, [0,1/3,2/3,1])
    for group in range(3):
        selection = (radii>=edges[group]) & ((radii<=edges[group+1]) if group==2 else (radii<edges[group+1]))
        if not np.any(selection): continue
        vox = ijk[selection]
        grid = np.zeros_like(blurred)
        np.add.at(grid, (vox[:,0], vox[:,1], vox[:,2]), alpha[selection])
        blurred += gaussian_filter(grid, sigma=float(np.median(radii[selection])), mode="constant")
    # Covariance rotations/aniso are approximated by scale-equivalent isotropic
    # kernels; raw PLY remains the authoritative splat representation.
    maximum = float(blurred.max())
    if maximum <= 0: raise ValueError("Leeres Dichtefeld")
    positive = blurred[blurred > maximum*0.01]
    threshold = max(float(np.percentile(positive, 55)), maximum*0.04)
    if threshold >= maximum: threshold = maximum*0.5
    verts, faces, _, _ = marching_cubes(blurred, level=threshold, spacing=(spacing,)*3)
    verts += lo
    if not len(faces): raise ValueError("Marching Cubes hat keine Oberfläche erzeugt")
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    # Smooth before colors. Keep conservative settings for thin structures.
    trimesh.smoothing.filter_laplacian(mesh, lamb=0.25, iterations=3, volume_constraint=False)
    # Optional decimation. Reproject colors AFTER simplification so GLB retains
    # vertex colors even when the simplification backend loses attributes.
    if len(mesh.faces) > 90000:
        try: mesh = mesh.simplify_quadric_decimation(face_count=90000)
        except (ImportError, ValueError, AttributeError) as exc:
            print(f"Decimation skipped ({type(exc).__name__}); original mesh retained")
    tree = cKDTree(xyz)
    dist, neighbors = tree.query(mesh.vertices, k=min(4, len(xyz)), workers=1)
    if neighbors.ndim == 1: neighbors = neighbors[:, None]; dist = dist[:, None]
    weights = 1.0 / np.maximum(dist, spacing*0.1)**2
    rgb = (colors[neighbors].astype(np.float32)*weights[...,None]).sum(axis=1)/weights.sum(axis=1)[:,None]
    mesh.visual.vertex_colors = np.column_stack([np.clip(rgb,0,255).astype(np.uint8), np.full(len(rgb),255,np.uint8)])
    # GLB includes vertex color; STL does not support vertex colors by design.
    mesh.export(output_dir / "mesh.glb", file_type="glb")
    mesh.export(output_dir / "mesh.stl", file_type="stl")
    print(f"Mesh: {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--resolution", type=int, choices=(96,128,160), default=128)
    args = parser.parse_args()
    reconstruct(args.ply, args.out, args.resolution)

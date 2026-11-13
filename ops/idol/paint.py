#!/usr/bin/env python3
"""The colours of the whole shape, from every side: MV-Adapter (Apache-2.0)
on SDXL, on the MI50, the way TripoSG's authors texture its shapes.

    paint.py JOB_FOLDER

TripoSG gives the shape alone. Its own texturing is MV-Adapter's
image-and-geometry pipeline (scripts/inference_ig2mv_sdxl.py): the shape is
seen from six sides (front, right, back, left, top, bottom; orthographic, as
its position and normal maps), and SDXL with the adapter paints those six
views at once from the photo, so that each fits the shape and they fit each
other. This runs it as it is, with its defaults (50 steps, guidance 3,
ShiftSNR 8, "high quality", its negative prompt, 768 px), and makes each view
twice as large with Real-ESRGAN x2plus as its texture script does
(scripts/texture_i2tex.py, view_upscale) before mesh.py lays it on.

The six views go to outputs/views.png (one row) with outputs/views_mask.png
(where the shape is in each) and outputs/views.json (the six cameras in the
shape's frame, and the quarter turn mesh.py gives the shape): mesh.py lays
them onto the model (bake_texture). Only the maps are made here differently
from MV-Adapter's script: nvdiffrast is CUDA's alone, so they are drawn on
the CPU from points of the surface.

Runs in TripoSG's environment; MAG-PAINT k/50 lines say how far it got,
MAG-ERROR in words when it cannot. Without views mesh.py colours the model
as before.
"""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import trimesh
from PIL import Image
from scipy.ndimage import binary_closing, distance_transform_edt

import mesh as mesh_of

IDOL_BASE = os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50"))
SOURCE = os.environ.get("MAG_MVADAPTER_SOURCE", os.path.join(IDOL_BASE, "mvadapter/src"))
WEIGHTS = Path(os.environ.get("MAG_PAINT_WEIGHTS", os.path.join(IDOL_BASE, "weights/paint")))
SIDE = 768
STEPS = 50
# MV-Adapter's six views and its orthographic box (scripts/inference_ig2mv_sdxl.py).
ELEVATION = (0.0, 0.0, 0.0, 0.0, 89.99, -89.99)
AZIMUTH = tuple(a - 90.0 for a in (0.0, 90.0, 180.0, 270.0, 180.0, 180.0))
DISTANCE, HALF = 1.8, 0.55
# load_mesh(rescale=True) with up +y, front +x: its frame (z up) from the shape's (y up).
TO_STD = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
CHUNK = int(float(os.environ.get("MAG_PAINT_ATTN_MB", "1024")) * 2**20)
_sdpa = F.scaled_dot_product_attention


def attention(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, enable_gqa=False):
    """Exact attention without the full score matrix: gfx906 has no flash or
    memory-efficient kernel, and six views of 96x96 latents attend to each other."""
    q, k, v = query, key, value
    if attn_mask is not None or dropout_p or is_causal or enable_gqa or not q.is_cuda or q.dim() != 4:
        return _sdpa(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, scale=scale)
    n, h, l, c = q.shape
    step = max(16, CHUNK // max(1, n * h * k.shape[2] * 6))
    kt = k.transpose(-1, -2)
    q = q * (scale if scale is not None else c ** -0.5)
    out = torch.empty(n, h, l, v.shape[-1], dtype=q.dtype, device=q.device)
    for i in range(0, l, step):
        scores = torch.matmul(q[:, :, i:i + step], kt).float()
        out[:, :, i:i + step] = torch.matmul(scores.softmax(-1).to(v.dtype), v)
        del scores
    return out


F.scaled_dot_product_attention = attention
UPSCALER = WEIGHTS / "realesrgan" / "RealESRGAN_x2plus.pth"


class Dense(torch.nn.Module):
    """Real-ESRGAN's residual dense block (basicsr RRDBNet)."""

    def __init__(self, nf=64, gc=32):
        super().__init__()
        self.conv1 = torch.nn.Conv2d(nf, gc, 3, 1, 1)
        self.conv2 = torch.nn.Conv2d(nf + gc, gc, 3, 1, 1)
        self.conv3 = torch.nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
        self.conv4 = torch.nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
        self.conv5 = torch.nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)

    def forward(self, x):
        x1 = F.leaky_relu(self.conv1(x), 0.2)
        x2 = F.leaky_relu(self.conv2(torch.cat((x, x1), 1)), 0.2)
        x3 = F.leaky_relu(self.conv3(torch.cat((x, x1, x2), 1)), 0.2)
        x4 = F.leaky_relu(self.conv4(torch.cat((x, x1, x2, x3), 1)), 0.2)
        return self.conv5(torch.cat((x, x1, x2, x3, x4), 1)) * 0.2 + x


class Block(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.rdb1, self.rdb2, self.rdb3 = Dense(), Dense(), Dense()

    def forward(self, x):
        return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x


class Upscaler(torch.nn.Module):
    """RRDBNet as Real-ESRGAN x2plus has it: 23 blocks, the picture folded into
    four quarters first, twice as large out."""

    def __init__(self):
        super().__init__()
        self.conv_first = torch.nn.Conv2d(12, 64, 3, 1, 1)
        self.body = torch.nn.Sequential(*[Block() for _ in range(23)])
        self.conv_body = torch.nn.Conv2d(64, 64, 3, 1, 1)
        self.conv_up1 = torch.nn.Conv2d(64, 64, 3, 1, 1)
        self.conv_up2 = torch.nn.Conv2d(64, 64, 3, 1, 1)
        self.conv_hr = torch.nn.Conv2d(64, 64, 3, 1, 1)
        self.conv_last = torch.nn.Conv2d(64, 3, 3, 1, 1)

    def forward(self, x):
        feat = self.conv_first(F.pixel_unshuffle(x, 2))
        feat = feat + self.conv_body(self.body(feat))
        feat = F.leaky_relu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")), 0.2)
        feat = F.leaky_relu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")), 0.2)
        return self.conv_last(F.leaky_relu(self.conv_hr(feat), 0.2))


def larger(images):
    """The views twice as large (Real-ESRGAN x2plus); as they are where its
    weights are missing or it fails."""
    if not UPSCALER.is_file():
        return images
    try:
        net = Upscaler()
        net.load_state_dict(torch.load(UPSCALER, map_location="cpu", weights_only=True)["params_ema"], strict=True)
        net = net.eval().half().to("cuda")
        out = []
        with torch.no_grad():
            for image in images:
                x = torch.from_numpy(np.asarray(image.convert("RGB"), np.float32) / 255.0).permute(2, 0, 1)[None]
                y = net(x.half().to("cuda")).float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
                out.append(Image.fromarray(np.rint(y * 255).astype(np.uint8)))
        del net
        torch.cuda.empty_cache()
        print(f"Views: twice as large ({out[0].width} px)", flush=True)
        return out
    except Exception as exc:
        print(f"Views: not made larger ({type(exc).__name__})", flush=True)
        return images


def cameras():
    """MV-Adapter's six cameras (get_c2w), in its frame: where each looks
    from, its right, its up and the way it looks."""
    out = []
    for e, a in zip(ELEVATION, AZIMUTH):
        e, a = math.radians(e), math.radians(a)
        eye = DISTANCE * np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
        look = -eye / np.linalg.norm(eye)
        right = np.cross(look, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, look)
        up /= np.linalg.norm(up)
        out.append((right, up, look))
    return out


def maps(points, normals, right, up, look):
    """Position and normal map of one view (SIDE x SIDE x 3, MV-Adapter's
    background where nothing is) and where the shape is: the nearest point
    of the surface in every pixel; a pixel no point fell into, inside the
    shape's outline, takes its nearest neighbour's."""
    col = np.floor((points @ right + HALF) / (2 * HALF) * SIDE).astype(np.int64)
    row = np.floor((HALF - points @ up) / (2 * HALF) * SIDE).astype(np.int64)
    depth = points @ look
    ok = (col >= 0) & (col < SIDE) & (row >= 0) & (row < SIDE)
    flat = row[ok] * SIDE + col[ok]
    order = np.argsort(-depth[ok])                                   # the nearest is written last
    hit = np.full(SIDE * SIDE, -1, np.int64)
    hit[flat[order]] = np.flatnonzero(ok)[order]
    hit = hit.reshape(SIDE, SIDE)
    got = hit >= 0
    mask = binary_closing(got, iterations=2) | got
    _, (iy, ix) = distance_transform_edt(~got, return_indices=True)
    hit = np.where(mask, hit[iy, ix], -1)
    pos = np.zeros((SIDE, SIDE, 3), np.float32)
    nrm = np.zeros((SIDE, SIDE, 3), np.float32)
    pos[mask] = points[hit[mask]]
    nrm[mask] = normals[hit[mask]]
    return pos, nrm, mask


def reference(path):
    """The photo as MV-Adapter's preprocess_image prepares it: the thing,
    its longer side 90 % of the view, in the middle, on grey."""
    image = np.asarray(Image.open(path).convert("RGBA"))
    alpha = image[..., 3] > 0
    ys, xs = np.nonzero(alpha)
    y0, y1 = max(ys.min() - 1, 0), min(ys.max() + 1, alpha.shape[0])
    x0, x1 = max(xs.min() - 1, 0), min(xs.max() + 1, alpha.shape[1])
    centre = image[y0:y1, x0:x1]
    h, w = centre.shape[:2]
    if h > w:
        w, h = int(w * SIDE * 0.9 / h), int(SIDE * 0.9)
    else:
        h, w = int(h * SIDE * 0.9 / w), int(SIDE * 0.9)
    centre = np.asarray(Image.fromarray(centre).resize((w, h)))
    out = np.zeros((SIDE, SIDE, 4), np.uint8)
    out[(SIDE - h) // 2:(SIDE - h) // 2 + h, (SIDE - w) // 2:(SIDE - w) // 2 + w] = centre
    out = out.astype(np.float32) / 255.0
    out = out[..., :3] * out[..., 3:4] + (1 - out[..., 3:4]) * 0.5
    return Image.fromarray((out * 255).clip(0, 255).astype(np.uint8))


def main():
    torch.set_num_threads(16)                                 # the unit counts its tasks
    job = Path(sys.argv[1])
    whole = trimesh.load(job / "shape.ply", force="mesh", process=True)
    picture = mesh_of.shape_picture(job)
    if picture is None:
        print("MAG-ERROR Das Bild der Form fehlt.", flush=True)
        sys.exit(2)
    own, whole = mesh_of.facing(whole, picture[1])
    if own is None:
        print("MAG-ERROR Die Form deckt ihr Bild nicht.", flush=True)
        sys.exit(2)
    quarter = int(whole.metadata.get("quarter", 0))
    # MV-Adapter's frame and size: z up, the farthest point half a unit from the middle.
    scale = 0.5 / float(np.abs(whole.vertices).max())
    points, faces = trimesh.sample.sample_surface(whole, 4000000)
    corners = whole.triangles[faces]
    weights = trimesh.triangles.points_to_barycentric(corners, points)
    normals = (whole.vertex_normals[whole.faces[faces]] * weights[:, :, None]).sum(axis=1)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    points = (np.asarray(points) @ TO_STD.T) * scale
    normals = normals @ TO_STD.T
    control, masks, views = [], [], []
    for right, up, look in cameras():
        pos, nrm, mask = maps(points, normals, right, up, look)
        control.append(np.concatenate([np.clip(pos + 0.5, 0, 1), np.clip(nrm / 2 + 0.5, 0, 1)], axis=2))
        masks.append(mask)
        back = TO_STD.T                                       # MV-Adapter's frame -> the shape's
        views.append({"right": (back @ right).tolist(), "up": (back @ up).tolist(), "look": (back @ look).tolist()})
    del points, normals
    print("MAG-STEP paint", flush=True)
    sys.path.insert(0, SOURCE)
    from diffusers import AutoencoderKL
    from mvadapter.models.attention_processor import DecoupledMVRowColSelfAttnProcessor2_0
    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
    vae = AutoencoderKL.from_pretrained(WEIGHTS / "sdxl-vae-fp16-fix")
    pipe = MVAdapterI2MVSDXLPipeline.from_pretrained(WEIGHTS / "stable-diffusion-xl-base-1.0", vae=vae, variant="fp16")
    pipe.scheduler = ShiftSNRScheduler.from_scheduler(pipe.scheduler, shift_mode="interpolated", shift_scale=8.0, scheduler_class=None)
    pipe.init_custom_adapter(num_views=6, self_attn_processor=DecoupledMVRowColSelfAttnProcessor2_0)
    pipe.load_custom_adapter(str(WEIGHTS / "mv-adapter"), weight_name="mvadapter_ig2mv_sdxl.safetensors")
    pipe.to(device="cuda", dtype=torch.float16)
    pipe.cond_encoder.to(device="cuda", dtype=torch.float16)
    pipe.enable_vae_slicing()

    def said(_pipe, i, _t, kwargs):
        print(f"MAG-PAINT {i + 1}/{STEPS}", flush=True)
        return kwargs

    with torch.no_grad():
        images = pipe("high quality", height=SIDE, width=SIDE, num_inference_steps=STEPS, guidance_scale=3.0,
                      num_images_per_prompt=6, control_image=torch.from_numpy(np.stack(control)).permute(0, 3, 1, 2).to("cuda"),
                      control_conditioning_scale=1.0, reference_image=reference(job / "outputs" / "shape_photo.png"),
                      reference_conditioning_scale=1.0, negative_prompt="watermark, ugly, deformed, noisy, blurry, low contrast",
                      cross_attention_kwargs={"scale": 1.0}, generator=torch.Generator(device="cuda").manual_seed(42),
                      callback_on_step_end=said).images
    del pipe
    torch.cuda.empty_cache()
    images = larger([image.convert("RGB").resize((SIDE, SIDE)) for image in images])
    side = images[0].width
    row = Image.new("RGB", (side * 6, side))
    for i, image in enumerate(images):
        row.paste(image.resize((side, side)), (i * side, 0))
    row.save(job / "outputs" / "views.png")
    Image.fromarray(np.concatenate(masks, axis=1).astype(np.uint8) * 255).save(job / "outputs" / "views_mask.png")
    (job / "outputs" / "views.json").write_text(json.dumps({"quarter": quarter, "scale": scale, "half": HALF, "views": views}))
    print("MAG-PAINTED 6 views", flush=True)


if __name__ == "__main__":
    main()

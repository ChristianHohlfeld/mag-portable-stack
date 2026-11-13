#!/usr/bin/env python3
"""The whole shape of the thing in a photo: TripoSG (VAST-AI, MIT) on the MI50.

    shape.py JOB_FOLDER

Idol makes a splat of what the photo shows; seen from the side it is a
relief, and the back is a guess of a few Gaussians. TripoSG is trained to
give the complete, closed shape of one object from one picture: a head is a
head from every side. It is given the same picture Idol was given (the
thing cut out, on white, with a margin) and writes `shape.ply` into the job
folder: a closed mesh in TripoSG's own frame (about -1..1, y up), which
sees the picture straight from +z without perspective. mesh.py fits it onto
the splat; the side the picture shows takes the photo itself (shape_photo.png,
as fine as the photo), the rest the splat's colours.

Runs in its own environment (~/idol-mi50/triposg/venv, which uses Idol's
ROCm torch without changing it). Prints MAG-STEP lines like the other
stages; MAG-ERROR in words when it cannot.
"""
import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import torch
from PIL import Image
# The Idol environment of this host's owner (ops/host.env; ~/idol-mi50).
IDOL_BASE = os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50"))

SOURCE = os.environ.get("MAG_TRIPOSG_SOURCE", os.path.join(IDOL_BASE, "triposg/src"))
WEIGHTS = os.environ.get("MAG_TRIPOSG_WEIGHTS", os.path.join(IDOL_BASE, "weights/triposg"))
IDOL = os.environ.get("MAG_IDOL_WEIGHTS", os.path.join(IDOL_BASE, "weights/idol"))
STEPS = int(os.environ.get("MAG_TRIPOSG_STEPS", "50"))


def cut_out(job):
    """The thing alone, as Idol saw it: the photo with its foreground as
    alpha (the photo's own alpha when it has one, else Idol's BiRefNet)."""
    photo = Image.open(job / "input.png")
    photo.load()
    if photo.mode == "RGBA" and np.asarray(photo.getchannel(3)).min() < 255:
        return photo
    sys.path.insert(0, os.path.join(IDOL_BASE, "idol-sdk"))
    from safetensors.torch import load_file
    from idol.models import BiRefNet
    net = BiRefNet()
    net.load_state_dict(load_file(os.path.join(IDOL, "birefnet.safetensors")), strict=True)
    net = net.eval().requires_grad_(False).to("cuda").half()
    side = 1024 / min(photo.size)
    small = photo.convert("RGB").resize((max(1, round(photo.width * side)), max(1, round(photo.height * side))), Image.LANCZOS)
    with torch.no_grad():
        alpha = net.remove_background(small).getchannel(3).resize(photo.size, Image.BILINEAR)
    del net
    torch.cuda.empty_cache()
    out = photo.convert("RGB")
    out.putalpha(alpha.point(lambda a: 255 if a >= 128 else 0))   # a hard mask, as TripoSG's own preparation makes it
    return out


def framed(image, margin=0.1, side=2000):
    """On white, square, the thing in the middle with `margin` around it (as
    TripoSG's own scripts/image_process.py: at most 2000 px, never enlarged); and
    the same square as fine as the photo has it (at most 4096 px), with the
    thing's outline as alpha: mesh.py lays it onto the shape."""
    alpha = np.asarray(image.getchannel(3))
    ys, xs = np.nonzero(alpha > 127)
    if len(xs) == 0:
        raise ValueError("leer")
    box = image.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    long = max(box.size)
    square = int(round(long * (1 + 2 * margin)))
    at = ((square - box.width) // 2, (square - box.height) // 2)
    white = Image.new("RGB", (square, square), (255, 255, 255))
    white.paste(box, at, box.getchannel(3))
    fine = Image.new("RGBA", (square, square), (0, 0, 0, 0))
    fine.paste(box.convert("RGBA"), at)
    if square > 4096:
        fine = fine.resize((4096, 4096), Image.LANCZOS)
    return (white.resize((side, side), Image.LANCZOS) if square > side else white), fine


def main():
    torch.set_num_threads(16)                                 # the unit counts its tasks
    job = Path(sys.argv[1])
    try:
        picture, fine = framed(cut_out(job))
    except Exception as exc:
        print("MAG-ERROR Im Foto ist kein Ding zu finden (%s)." % type(exc).__name__, flush=True)
        sys.exit(2)
    picture.save(job / "outputs" / "shape_input.png")
    fine.save(job / "outputs" / "shape_photo.png")
    # The mesh is taken with marching cubes on the CPU; the CUDA-only extractor is never imported.
    sys.modules.setdefault("diso", types.SimpleNamespace(DiffDMC=None))
    sys.path.insert(0, SOURCE)
    # The repository's own parts are imported before loading, so diffusers finds them by their names.
    import triposg.schedulers.scheduling_rectified_flow  # noqa: F401
    import triposg.models.transformers.triposg_transformer  # noqa: F401
    import triposg.models.autoencoders.autoencoder_kl_triposg  # noqa: F401
    from triposg.pipelines.pipeline_triposg import TripoSGPipeline
    print("MAG-STEP shape", flush=True)
    pipe = TripoSGPipeline.from_pretrained(WEIGHTS).to("cuda", torch.float16)
    with torch.no_grad():
        out = pipe(image=picture, generator=torch.Generator(device="cuda").manual_seed(42), num_inference_steps=STEPS,
                   guidance_scale=7.0, use_flash_decoder=False, dense_octree_depth=8, hierarchical_octree_depth=9).samples[0]
    import trimesh
    mesh = trimesh.Trimesh(np.asarray(out[0], np.float32), np.ascontiguousarray(out[1]), process=True)
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:                                        # specks in the air are no part of it
        most = max(len(p.faces) for p in parts)
        mesh = trimesh.util.concatenate([p for p in parts if len(p.faces) >= 0.02 * most])
    if len(mesh.faces) < 500:
        print("MAG-ERROR Die Form ließ sich nicht bilden.", flush=True)
        sys.exit(3)
    mesh.export(job / "shape.ply")
    print("MAG-SHAPE %d faces" % len(mesh.faces), flush=True)


if __name__ == "__main__":
    main()

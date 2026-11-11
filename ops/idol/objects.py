#!/usr/bin/env python3
"""Which object of a photo is meant: SAM 2 on the CPU.

    objects.py IMAGE OUT --point X Y     the object at a place (0..1 across, 0..1 down)
    objects.py IMAGE OUT --all           every object of the photo, each by itself

Writes OUT/mask-<n>.png (white where the object is, as large as the photo)
and OUT/masks.json: for every mask its file, its box (left, top, right,
bottom in pixels) and the part of the photo it covers. Idol makes one model
of one object; a photo cut out with such a mask (as alpha) goes through
Idol without its own guess of what the foreground is.
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from transformers import Sam2Model, Sam2Processor

WEIGHTS = os.environ.get("MAG_IDOL_SAM2", "/home/chris/idol-mi50/weights/sam2")
SIDE = 1536          # the photo is looked at no larger than this
GRID = 12            # --all asks at GRID x GRID places
MOST = 12            # ... and keeps at most this many objects


def masks_at(model, processor, image, points):
    """For every place the mask SAM is surest of: masks (n x h x w, bool) and how sure (n)."""
    found, sure = [], []
    for start in range(0, len(points), 48):
        part = points[start:start + 48]
        inputs = processor(images=image, input_points=[[[list(p)] for p in part]], input_labels=[[[1] for _ in part]], return_tensors="pt")
        with torch.no_grad():
            out = model(**inputs)
        masks = processor.post_process_masks(out.pred_masks, inputs["original_sizes"])[0]      # n x 3 x h x w
        scores = out.iou_scores[0]                                                             # n x 3
        best = scores.argmax(dim=1)
        found.append(masks[torch.arange(len(part)), best].numpy().astype(bool))
        sure.append(scores[torch.arange(len(part)), best].numpy())
    return np.concatenate(found), np.concatenate(sure)


def whole(mask):
    """A mask without specks: its largest piece, holes filled."""
    from scipy.ndimage import binary_fill_holes, label
    pieces, count = label(mask)
    if count > 1:
        sizes = np.bincount(pieces.ravel())
        sizes[0] = 0
        mask = pieces == sizes.argmax()
    return binary_fill_holes(mask)


def every(masks, sure):
    """The objects among many masks: no background, nothing twice, the largest first."""
    h, w = masks.shape[1:]
    kept = []
    for i in np.argsort(-sure):
        m = whole(masks[i])
        share = float(m.mean())
        if sure[i] < 0.75 or share < 0.004 or share > 0.6:
            continue
        edges = sum(bool(e.mean() > 0.5) for e in (m[0], m[-1], m[:, 0], m[:, -1]))
        if edges >= 2:                                    # it runs along the photo's edges: a wall, the floor
            continue
        if any((m & k).sum() / max(1, (m | k).sum()) > 0.6 or (m & k).sum() / max(1, m.sum()) > 0.85 for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda m: -int(m.sum()))[:MOST]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--point", nargs=2, type=float)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    torch.set_num_threads(min(12, len(os.sched_getaffinity(0))))
    torch.set_num_interop_threads(1)
    photo = ImageOps.exif_transpose(Image.open(args.image))      # as a browser shows it
    size = photo.size
    image = photo.convert("RGB")
    if max(size) > SIDE:
        image.thumbnail((SIDE, SIDE), Image.LANCZOS)
    model = Sam2Model.from_pretrained(WEIGHTS).eval()
    processor = Sam2Processor.from_pretrained(WEIGHTS)
    w, h = image.size
    if args.point:
        x, y = (min(1.0, max(0.0, v)) for v in args.point)
        masks, sure = masks_at(model, processor, image, [(x * w, y * h)])
        picked = [whole(masks[0])]
    else:
        places = [((i + 0.5) * w / GRID, (j + 0.5) * h / GRID) for j in range(GRID) for i in range(GRID)]
        picked = every(*masks_at(model, processor, image, places))
    args.out.mkdir(parents=True, exist_ok=True)
    listed = []
    for n, mask in enumerate(picked):
        if not mask.any():
            continue
        picture = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize(size, Image.NEAREST)
        picture.save(args.out / f"mask-{n}.png", optimize=True)
        ys, xs = np.nonzero(np.asarray(picture))
        listed.append({"file": f"mask-{n}.png", "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], "share": round(float(mask.mean()), 4)})
    (args.out / "masks.json").write_text(json.dumps({"width": size[0], "height": size[1], "masks": listed}))
    print(f"MAG-MASKS {len(listed)}", flush=True)


if __name__ == "__main__":
    main()

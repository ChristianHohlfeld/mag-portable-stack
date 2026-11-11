#!/usr/bin/env python3
"""Which object of a photo is meant: SAM 2 on the CPU.

    objects.py IMAGE OUT --point X Y     the object at a place (0..1 across, 0..1 down)
    objects.py IMAGE OUT --all           every object of the photo, each by itself
    objects.py IMAGE OUT --scene         ... and where each stands in the room the photo shows
    objects.py IMAGE OUT --pieces        what stands apart in the photo's foreground (two people, a gnome
                                         and a fox), each cut out; one piece only: nothing to tell apart

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
DEPTH = os.environ.get("MAG_IDOL_DEPTH", "/home/chris/idol-mi50/weights/depth")
IDOL = os.environ.get("MAG_IDOL_WEIGHTS", "/home/chris/idol-mi50/weights/idol")
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


def pieces_of(image):
    """What stands apart in the foreground of a photo: the foreground as Idol
    itself finds it (its BiRefNet, here on the CPU), cut where it does not
    hang together. Idol makes one model of one thing; shown two people in
    one picture it makes one lump of both, each a third as large as it could
    be. Returns the masks (h x w, bool), the largest first; a piece much
    smaller than the largest is a crumb and no thing."""
    from scipy.ndimage import binary_fill_holes, binary_opening, label
    from safetensors.torch import load_file
    from idol.models import BiRefNet
    net = BiRefNet()
    net.load_state_dict(load_file(os.path.join(IDOL, "birefnet.safetensors")), strict=True)
    net.eval().requires_grad_(False)
    with torch.no_grad():
        alpha = np.asarray(net.remove_background(image.convert("RGB")).getchannel(3).resize(image.size, Image.BILINEAR), np.float32) / 255.0
    front = binary_opening(alpha > 0.5, iterations=max(1, min(image.size) // 400))      # a thread between two things is no bond
    found, count = label(front)
    if count == 0:
        return []
    sizes = np.bincount(found.ravel())
    sizes[0] = 0
    kept = [binary_fill_holes(found == i) for i in np.argsort(-sizes)[:MOST] if sizes[i] >= max(0.12 * sizes.max(), 0.004 * front.size)]
    # Each piece takes back the soft rim the opening took (hair, fingers), where it is nearest.
    if len(kept) > 1:
        from scipy.ndimage import distance_transform_edt
        nearest = np.argmin(np.stack([distance_transform_edt(~piece) for piece in kept]), axis=0)
        kept = [(alpha > 0.5) & (nearest == i) & binary_dilation_of(piece, max(2, min(image.size) // 200)) | piece for i, piece in enumerate(kept)]
    return kept


def binary_dilation_of(mask, steps):
    from scipy.ndimage import binary_dilation
    return binary_dilation(mask, iterations=steps)


def far_of(image):
    """How far away every pixel of the photo is, in metres (Depth Anything V2,
    the small one for rooms): h x w."""
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    processor = AutoImageProcessor.from_pretrained(DEPTH)
    model = AutoModelForDepthEstimation.from_pretrained(DEPTH).eval()
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    return processor.post_process_depth_estimation(out, target_sizes=[(image.height, image.width)])[0]["predicted_depth"].numpy().astype(np.float32)


def focal_of(photo, width, height):
    """The focal length in pixels: from what the camera wrote into the photo,
    else that of a phone's main camera (26 mm on a 36 mm film)."""
    film = 26.0
    try:
        told = float(photo.getexif().get_ifd(0x8769).get(0xA405) or 0)        # FocalLengthIn35mmFilm
        if 10.0 <= told <= 200.0:
            film = told
    except Exception:
        pass
    return max(width, height) * film / 36.0


def room_of(image, far, masks, focal):
    """The room a photo shows, as seen from its camera, in metres: where every
    object stands (x to the right, z away from the camera, its foot's height
    above the floor), how tall and wide it is, and the room around them all."""
    h, w = far.shape
    us, vs = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    right = (us - w / 2) * far / focal
    below = (vs - h / 2) * far / focal                                  # downwards from the camera's height
    pixels = np.asarray(image, np.float32) / 255.0
    things = np.zeros((h, w), bool)
    for mask in masks:
        things |= mask
    floor = float(np.percentile(below, 98))                             # the lowest the photo shows
    placed = []
    for mask in masks:
        deep = float(np.median(far[mask]))
        ys, xs = np.nonzero(mask)
        left_px, right_px, top_px, bottom_px = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        placed.append({"x": round(float(((left_px + right_px) / 2 - w / 2) * deep / focal), 3), "z": round(deep, 3),
                       "tall": round(float((bottom_px - top_px) * deep / focal), 3), "wide": round(float((right_px - left_px) * deep / focal), 3),
                       "lift": round(max(0.0, floor - float((bottom_px - h / 2) * deep / focal)), 3)})
    free = ~things
    ground = free & (below > floor - 0.12)
    wall = free & (below < floor - 0.8)
    def colour(where, otherwise):
        return [round(float(c), 3) for c in (np.median(pixels[where], axis=0) if where.sum() > 200 else otherwise)]
    back = float(np.percentile(far, 98))
    room = {"left": round(float(np.percentile(right, 1)), 3), "right": round(float(np.percentile(right, 99)), 3), "back": round(back, 3),
            "height": round(float(np.clip(floor - np.percentile(below, 1), 2.2, 3.5)), 3), "camera": round(float(np.clip(floor, 0.3, 2.5)), 3),
            "floor": colour(ground, (0.55, 0.52, 0.48)), "wall": colour(wall, (0.85, 0.84, 0.82))}
    return placed, room


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--point", nargs=2, type=float)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--scene", action="store_true")
    ap.add_argument("--pieces", action="store_true")
    args = ap.parse_args()
    torch.set_num_threads(min(12, len(os.sched_getaffinity(0))))
    torch.set_num_interop_threads(1)
    raw = Image.open(args.image)
    photo = ImageOps.exif_transpose(raw)                         # as a browser shows it
    size = photo.size
    image = photo.convert("RGB")
    if max(size) > SIDE:
        image.thumbnail((SIDE, SIDE), Image.LANCZOS)
    w, h = image.size
    if not args.pieces:
        model = Sam2Model.from_pretrained(WEIGHTS).eval()
        processor = Sam2Processor.from_pretrained(WEIGHTS)
    if args.point:
        x, y = (min(1.0, max(0.0, v)) for v in args.point)
        masks, sure = masks_at(model, processor, image, [(x * w, y * h)])
        picked = [whole(masks[0])]
    elif args.pieces:
        picked = pieces_of(image)
        if len(picked) < 2:
            picked = []                                # one thing: there is nothing to tell apart
    else:
        places = [((i + 0.5) * w / GRID, (j + 0.5) * h / GRID) for j in range(GRID) for i in range(GRID)]
        picked = every(*masks_at(model, processor, image, places))
    args.out.mkdir(parents=True, exist_ok=True)
    picked = [mask for mask in picked if mask.any()]
    placed, room = (None, None)
    if (args.scene or args.pieces) and picked:
        placed, room = room_of(image, far_of(image), picked, focal_of(raw, w, h))
    listed = []
    for n, mask in enumerate(picked):
        picture = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize(size, Image.NEAREST)
        picture.save(args.out / f"mask-{n}.png", optimize=True)
        ys, xs = np.nonzero(np.asarray(picture))
        listed.append({"file": f"mask-{n}.png", "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], "share": round(float(mask.mean()), 4)})
        if args.pieces:
            # ... and the piece itself, cut out of the photo as it was taken: what Idol is shown.
            cut = photo.convert("RGB")
            cut.putalpha(picture)
            pad = int(0.03 * max(size))
            box = (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad), min(size[0], int(xs.max()) + 1 + pad), min(size[1], int(ys.max()) + 1 + pad))
            cut.crop(box).save(args.out / f"cut-{n}.png")
            listed[-1]["cut"] = f"cut-{n}.png"
        if placed:
            listed[-1]["place"] = placed[n]
    found = {"width": size[0], "height": size[1], "masks": listed}
    if room:
        found["room"] = room
    (args.out / "masks.json").write_text(json.dumps(found))
    print(f"MAG-MASKS {len(listed)}", flush=True)


if __name__ == "__main__":
    main()

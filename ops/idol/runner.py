#!/usr/bin/env python3
"""Run the VERIFIED locally patched idol-sdk, not a second encoder implementation.

This process has torch 2.7.1+rocm6.3 in ~/idol-mi50/venv; no system ROCm.
The local mi50.patch provides timm DINOv3, local VAE, FP16, SDPA and chunked
attention. Fail closed rather than silently using unpatched upstream code.
"""
import os
import runpy
import sys
from pathlib import Path


def check_photo(path):
    """A photo whose file ends in the middle of the picture (cut off on its
    way, or picked while the phone was still writing it): a little missing at
    the bottom is tolerated, more than that is no model worth 20 minutes on
    the GPU. Says so in words (MAG-ERROR) and ends the stage."""
    from PIL import Image, ImageFile, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            image.load()
        return
    except UnidentifiedImageError:
        print("MAG-ERROR Das ist kein Bild, das sich lesen lässt (JPEG, PNG oder WebP).", flush=True)
        sys.exit(2)
    except OSError:
        pass
    ImageFile.LOAD_TRUNCATED_IMAGES = True   # stays on for the pipeline in this process
    try:
        with Image.open(path) as image:
            image.load()
            grey = image.convert("L")
    except Exception:
        print("MAG-ERROR Das Bild ist beschädigt und lässt sich nicht lesen. Bitte noch einmal hochladen.", flush=True)
        sys.exit(2)
    # What was never decoded is one flat colour at the bottom.
    width, height = grey.size
    step, missing = max(1, height // 200), 0
    while missing < height:
        low, high = grey.crop((0, height - missing - step, width, height - missing)).getextrema()
        if low != high:
            break
        missing += step
    if missing > 0.02 * height:
        print("MAG-ERROR Das Foto ist unvollständig angekommen: unten fehlen etwa %d %% des Bildes. Bitte noch einmal aufnehmen oder auswählen und senden."
              % round(100 * missing / height), flush=True)
        sys.exit(2)
    print("MAG-IDOL: photo ends a little early; the missing rows are tolerated", flush=True)


def main():
    if "--image" in sys.argv:
        check_photo(sys.argv[sys.argv.index("--image") + 1])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["ATTN_BACKEND"] = "sdpa"
    os.environ.setdefault("IDOL_DINOV3", "/home/chris/idol-mi50/weights/dinov3-timm")
    os.environ.setdefault("IDOL_FLUX2_VAE", "/home/chris/idol-mi50/weights/flux2vae")
    os.environ["PYTHONUNBUFFERED"] = "1"
    for name in ("IDOL_DINOV3", "IDOL_FLUX2_VAE"):
        if not Path(os.environ[name]).is_dir():
            raise RuntimeError(name + " fehlt: " + os.environ[name])
    import torch
    import tensordict
    if torch.__version__.split("+")[0] != "2.7.1" or not torch.version.hip:
        raise RuntimeError("ROCm torch 2.7.1 erwartet, kein CUDA-/CPU-Torch installieren")
    if tensordict.__version__.split("+")[0] != "0.9.1":
        raise RuntimeError("tensordict muss 0.9.1 sein; kein automatisches Upgrade")
    if not torch.cuda.is_available():
        raise RuntimeError("MI50 nicht unter ROCm cuda:0 verfügbar; CPU-Fallback verboten")
    arch = str(getattr(torch.cuda.get_device_properties(0), "gcnArchName", ""))
    if "gfx906" not in arch:
        raise RuntimeError("Erwartet MI50/gfx906, erhalten " + arch)
    # The MI50 is Idol's alone (both bots are CPU-only): no VRAM fraction,
    # Idol may use the full card.
    print("MAG-IDOL: MI50 exclusive; no PyTorch VRAM cap", flush=True)

    # No duplicate ImageEncoder or modified checkpoint overlay. The locally
    # installed patched package must accept --dinov3_dtype fp16 and the two
    # IDOL_* variables. --no_optimize disables compile and cuDNN path.
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    runpy.run_module("idol.sample", run_name="__main__")

if __name__ == "__main__":
    main()

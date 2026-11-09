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

def main():
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
    # Lena is stopped and its GPU occupancy released by the root-controlled
    # GPU lease before this process starts. No arbitrary VRAM fraction: Idol
    # may use the full MI50. The worker fails closed if acquiring fails.
    print("MAG-IDOL: exclusive MI50 lease active; no PyTorch VRAM cap", flush=True)

    # No duplicate ImageEncoder or modified checkpoint overlay. The locally
    # installed patched package must accept --dinov3_dtype fp16 and the two
    # IDOL_* variables. --no_optimize disables compile and cuDNN path.
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    runpy.run_module("idol.sample", run_name="__main__")

if __name__ == "__main__":
    main()

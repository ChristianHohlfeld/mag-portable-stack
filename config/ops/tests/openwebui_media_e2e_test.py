#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

DB = "/home/chris/.open-webui/webui.db"
MODEL_ID = "hf.co/unsloth/Qwen3-30B-A3B-GGUF:UD-IQ2_XXS"
OUT = Path("/home/chris/ComfyUI/output")


def run(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def http_json(url: str, method: str = "GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def assert_true(ok: bool, msg: str):
    if not ok:
        raise RuntimeError(msg)


def wait_history(base: str, prompt_id: str, timeout_s: int):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            hist = http_json(f"{base}/history/{prompt_id}")
        except URLError:
            time.sleep(1)
            continue
        if prompt_id in hist:
            item = hist[prompt_id]
            st = item.get("status") or {}
            if st.get("completed") is True:
                return item
            if st.get("status_str") in {"success", "error"}:
                return item
        time.sleep(1)
    raise RuntimeError(f"timeout waiting history for {prompt_id}")


def first_filename(obj, suffixes):
    if isinstance(obj, dict):
        fn = obj.get("filename")
        if isinstance(fn, str) and fn.lower().endswith(tuple(suffixes)):
            return fn, obj.get("subfolder", ""), obj.get("type", "output")
        for v in obj.values():
            x = first_filename(v, suffixes)
            if x:
                return x
    elif isinstance(obj, list):
        for v in obj:
            x = first_filename(v, suffixes)
            if x:
                return x
    return None


def test_image():
    run_id = int(time.time())
    prefix = f"E2E_FLUX_LOCKED_{run_id}"
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q3_K_S.gguf"}},
        "2": {"class_type": "DualCLIPLoaderGGUF", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn.safetensors", "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip": ["2", 0], "clip_l": "a happy dog in a swimming pool", "t5xxl": "a happy dog in a swimming pool", "guidance": 3.5}},
        "5": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip": ["2", 0], "clip_l": "", "t5xxl": "", "guidance": 3.5}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 3.5}},
        "7": {"class_type": "FluxDisableGuidance", "inputs": {"conditioning": ["5", 0]}},
        "8": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 768, "height": 512, "batch_size": 1}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": run_id % (2**32 - 1), "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0], "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }
    res = http_json("http://127.0.0.1:8187/prompt", method="POST", payload={"prompt": wf})
    pid = res.get("prompt_id")
    assert_true(bool(pid), "image: missing prompt_id")
    print(f"image prompt_id={pid}")
    h = wait_history("http://127.0.0.1:8187", pid, 180)
    print(f"image status={h.get('status')}")
    item = first_filename(h.get("outputs", {}), [".png", ".jpg", ".webp"])
    if item is not None:
        fn, sub, _t = item
        p = OUT / sub / fn if sub else OUT / fn
    else:
        cands = sorted(OUT.glob(f"{prefix}*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
        assert_true(len(cands) > 0, f"image: no output file, outputs={json.dumps(h.get('outputs', {}))}, messages={json.dumps((h.get('status') or {}).get('messages', []))}")
        p = cands[0]
    assert_true(p.exists(), f"image: output missing on disk: {p}")
    return str(p)


def test_video():
    run_id = int(time.time())
    prefix = f"video/wan_locked_{run_id}"
    wf = {
        "1": {"class_type": "WanVideoModelLoader", "inputs": {"model": "wan2.2_ti2v_5B_fp16.safetensors", "base_precision": "fp16", "quantization": "disabled", "load_device": "main_device", "attention_mode": "sdpa"}},
        "2": {"class_type": "LoadWanVideoT5TextEncoder", "inputs": {"model_name": "umt5-xxl-enc-fp8_e4m3fn.safetensors", "precision": "bf16", "load_device": "offload_device", "quantization": "disabled"}},
        "3": {"class_type": "WanVideoTextEncode", "inputs": {"t5": ["2", 0], "positive_prompt": "a dog jumps over a river", "negative_prompt": "blurry, low quality, distorted, ugly", "force_offload": True, "model_to_offload": ["1", 0]}},
        "4": {"class_type": "WanVideoEmptyEmbeds", "inputs": {"width": 640, "height": 384, "num_frames": 49}},
        "5": {"class_type": "WanVideoVAELoader", "inputs": {"model_name": "wan2.2_vae.safetensors", "precision": "bf16"}},
        "6": {"class_type": "WanVideoSampler", "inputs": {"model": ["1", 0], "image_embeds": ["4", 0], "text_embeds": ["3", 0], "steps": 20, "cfg": 5.0, "shift": 5.0, "seed": run_id % (2**32 - 1), "scheduler": "unipc", "force_offload": True, "riflex_freq_index": 0}},
        "7": {"class_type": "WanVideoDecode", "inputs": {"vae": ["5", 0], "samples": ["6", 0], "enable_vae_tiling": True, "tile_x": 272, "tile_y": 272, "tile_stride_x": 144, "tile_stride_y": 128}},
        "8": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["7", 0], "frame_rate": 24, "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }
    res = http_json("http://127.0.0.1:8188/prompt", method="POST", payload={"prompt": wf})
    pid = res.get("prompt_id")
    assert_true(bool(pid), "video: missing prompt_id")
    print(f"video prompt_id={pid}")
    h = wait_history("http://127.0.0.1:8188", pid, 420)
    print(f"video status={h.get('status')}")
    st = (h.get("status") or {}).get("status_str", "")
    assert_true(st == "success", f"video: status not success ({st})")
    item = first_filename(h.get("outputs", {}), [".mp4", ".webm", ".gif", ".webp"])
    if item is not None:
        fn, sub, _t = item
        p = OUT / sub / fn if sub else OUT / fn
    else:
        cands = sorted((OUT / "video").glob(f"wan_locked_{run_id}*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)
        assert_true(len(cands) > 0, f"video: no output file, outputs={json.dumps(h.get('outputs', {}))}")
        p = cands[0]
    assert_true(p.exists(), f"video: output missing on disk: {p}")
    return str(p)


def main():
    preflight = Path("/home/chris/ops/scripts/openwebui_media_preflight.sh")
    assert_true(preflight.exists(), f"missing preflight script: {preflight}")
    run(f"bash {preflight}")

    # Services
    svc = run("systemctl is-active open-webui ollama comfyui comfyui-image nginx")
    assert_true(all(line.strip() == "active" for line in svc.splitlines()), "service not active")

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cfg = json.loads(cur.execute("select data from config where id=1").fetchone()[0])
    model = cur.execute("select meta,params from model where id=?", (MODEL_ID,)).fetchone()
    assert_true(model is not None, f"missing model {MODEL_ID}")
    meta = json.loads(model[0] or "{}")
    params = json.loads(model[1] or "{}")
    con.close()

    assert_true(cfg["image_generation"]["comfyui"]["base_url"] == "http://127.0.0.1:8187", "comfy image url mismatch")
    assert_true(cfg["rag"]["web"]["search"]["enable"] is False, "web search should be off")
    assert_true(cfg["ui"]["default_models"] == MODEL_ID, "default model mismatch")
    assert_true(cfg["ui"]["default_pinned_models"] == MODEL_ID, "default pinned mismatch")
    assert_true(cfg["ui"]["model_order_list"] == [MODEL_ID], "model order mismatch")
    assert_true(params.get("function_calling") == "native", "function_calling != native")
    assert_true(meta.get("capabilities", {}).get("image_generation") is True, "image_generation capability missing")
    assert_true(meta.get("capabilities", {}).get("web_search") is False, "web_search capability should be false")

    img = test_image()
    vid = test_video()
    print(f"PASS image={img}")
    print(f"PASS video={vid}")


if __name__ == "__main__":
    main()

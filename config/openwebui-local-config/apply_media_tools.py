#!/usr/bin/env python3
import json
import sqlite3
import time
from pathlib import Path

DB = Path('/home/chris/.open-webui/webui.db')
USER_ID = 'f5b66dde-f45a-40cc-84ab-11520561eecc'
TERMINAL_ID = '0820a1b8-01af-4a3c-b4e4-666071be14eb'

FILTER_CONTENT = r'''
"""
title: GPU Route Status + Model Tool Autoselect
author: chris+codex
version: 0.1.1
description: Shows the selected GPU at generation start and injects fixed model tools server-side so media/agent presets do not depend on browser state.
"""

class Filter:
    def _route(self, model_id: str):
        routes = {
            "chat-empfohlen:latest": {"status": "A3B LLM on GPU0 (Chat)"},
            "bild-generator:latest": {"status": "FLUX on GPU2 (Image)", "tool_ids": ["comfy_flux_image_generator"]},
            "video-generator:latest": {"status": "WAN on GPU1 (Video)", "tool_ids": ["comfy_wan_video_generator_tool"]},
            "agent-opencode:latest": {"status": "A3B LLM on GPU0 (OpenCode Agent)", "tool_ids": ["server:opencode"], "terminal_id": "0820a1b8-01af-4a3c-b4e4-666071be14eb"},
        }
        return routes.get(model_id, {"status": f"Generate on configured GPU ({model_id})"})

    def _merge_unique(self, existing, needed):
        values = []
        for item in existing or []:
            if item and item not in values:
                values.append(item)
        for item in needed or []:
            if item and item not in values:
                values.append(item)
        return values

    async def inlet(self, body: dict, __event_emitter__=None, __metadata__=None, __model__=None) -> dict:
        model_id = body.get("model") or (__metadata__ or {}).get("model_id")
        route = self._route(model_id)
        if route.get("tool_ids"):
            body["tool_ids"] = self._merge_unique(body.get("tool_ids"), route["tool_ids"])
        if route.get("terminal_id") and not body.get("terminal_id"):
            body["terminal_id"] = route["terminal_id"]
        if __event_emitter__ and route.get("status"):
            await __event_emitter__({"type": "status", "data": {"description": route["status"], "done": False}})
        return body

    async def outlet(self, body: dict, __event_emitter__=None, __metadata__=None, __model__=None) -> dict:
        model_id = body.get("model") or (__metadata__ or {}).get("model_id")
        route = self._route(model_id)
        if __event_emitter__ and route.get("status"):
            await __event_emitter__({"type": "status", "data": {"description": route["status"].replace("Generate", "Done"), "done": True}})
        return body
'''

IMAGE_TOOL = r'''
"""
description: Generate FLUX images via local ComfyUI (GPU1 22GB). Inputs are clamped to GPU-safe values.
requirements: aiohttp
"""

import asyncio
import json
import random
import time
from pathlib import Path
import aiohttp

class Tools:
    def _clamp_dim(self, value: int, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(value)
        except Exception:
            value = default
        value = max(minimum, min(maximum, value))
        return max(minimum, (value // 16) * 16)

    def _find_media(self, obj, exts):
        found = []
        def walk(x):
            if isinstance(x, dict):
                filename = x.get("filename")
                if isinstance(filename, str) and filename.lower().endswith(exts):
                    found.append(x)
                for value in x.values():
                    walk(value)
            elif isinstance(x, list):
                for value in x:
                    walk(value)
        walk(obj)
        return found[0] if found else None

    def _recent_file(self, root: str, started_at: float, exts):
        base = Path(root)
        candidates = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in exts and p.stat().st_mtime >= started_at - 2]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    async def _abort(self, session, base_url: str):
        for path, payload in [("/interrupt", None), ("/queue", {"clear": True}), ("/free", {"unload_models": True, "free_memory": True})]:
            try:
                if payload is None:
                    async with session.post(f"{base_url}{path}") as resp:
                        await resp.read()
                else:
                    async with session.post(f"{base_url}{path}", json=payload) as resp:
                        await resp.read()
            except Exception:
                pass

    async def generate_image(self, prompt: str, width: int = 512, height: int = 512, steps: int = 1, seed: int = -1, __request__=None, __user__=None, __event_emitter__=None, __metadata__=None) -> str:
        """Generate one FLUX image on GPU1. Width, height and steps are clamped for reliability."""
        chat_id = (__metadata__ or {}).get("chat_id")
        message_id = (__metadata__ or {}).get("message_id") or (__metadata__ or {}).get("assistant_message_id")
        base_url = "http://127.0.0.1:8187"
        requested = {"width": width, "height": height, "steps": steps}
        width = self._clamp_dim(width, 512, 256, 512)
        height = self._clamp_dim(height, 512, 256, 512)
        try:
            steps = max(1, min(2, int(steps)))
        except Exception:
            steps = 1
        if seed < 0:
            seed = random.randint(0, 2 ** 32 - 1)
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Generate FLUX on GPU1: {width}x{height}, {steps} step(s)", "done": False}})
        workflow = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q3_K_S.gguf"}},
            "2": {"class_type": "DualCLIPLoaderGGUF", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn.safetensors", "type": "flux"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "4": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip": ["2", 0], "clip_l": prompt, "t5xxl": prompt, "guidance": 3.5}},
            "5": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip": ["2", 0], "clip_l": "", "t5xxl": "", "guidance": 3.5}},
            "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 3.5}},
            "7": {"class_type": "FluxDisableGuidance", "inputs": {"conditioning": ["5", 0]}},
            "8": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "9": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0], "denoise": 1.0}},
            "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
            "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "OWUI"}}
        }
        started_at = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base_url}/prompt", json={"prompt": workflow}) as resp:
                    if resp.status >= 400:
                        raise Exception(f"ComfyUI error: {resp.status} {await resp.text()}")
                    result = await resp.json()
                prompt_id = result.get("prompt_id")
                if not prompt_id:
                    raise Exception("No prompt_id in response")
                history = None
                for _ in range(180):
                    await asyncio.sleep(1)
                    async with session.get(f"{base_url}/history/{prompt_id}") as resp:
                        if resp.status >= 400:
                            continue
                        history = await resp.json()
                    if prompt_id in history:
                        break
                else:
                    await self._abort(session, base_url)
                    raise Exception("Timed out waiting for FLUX image; Comfy job interrupted")
                item = self._find_media(history[prompt_id].get("outputs", {}), (".png", ".jpg", ".jpeg", ".webp"))
                img_bytes = None
                filename = None
                if item:
                    filename = item["filename"]
                    async with session.get(f"{base_url}/view", params={"filename": filename, "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")}) as img_resp:
                        if img_resp.status >= 400:
                            raise Exception(f"Failed to download image: {img_resp.status}")
                        img_bytes = await img_resp.read()
                else:
                    path = self._recent_file("/home/chris/ComfyUI/output", started_at, {".png", ".jpg", ".jpeg", ".webp"})
                    if path:
                        filename = path.name
                        img_bytes = path.read_bytes()
                if not img_bytes:
                    status = history[prompt_id].get("status", {})
                    raise Exception(f"No image found in Comfy output (status={status.get('status_str')}, output_nodes={list(history[prompt_id].get('outputs', {}).keys())})")
                url = await self._upload_file(__request__, __user__, img_bytes, filename or "flux.png", "image/png", chat_id, message_id)
                if __event_emitter__:
                    await __event_emitter__({"type": "chat:message:files", "data": {"files": [{"type": "image", "url": url}]}})
                    await __event_emitter__({"type": "status", "data": {"description": f"Done FLUX on GPU1: {width}x{height}", "done": True}})
                return json.dumps({"status": "success", "message": f"Generated FLUX image on GPU1 ({width}x{height}, {steps} step(s)).", "url": url, "filename": filename, "requested": requested}, ensure_ascii=False)
        except Exception as e:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": f"Failed FLUX on GPU1: {e}", "done": True}})
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(f"{base_url}/free", json={"unload_models": True, "free_memory": True})
            except Exception:
                pass

    async def _upload_file(self, request, user_dict, data, filename, content_type, chat_id, message_id):
        from open_webui.routers.images import upload_image
        from open_webui.models.users import UserModel
        user = UserModel(**user_dict) if user_dict else None
        metadata = {}
        if chat_id:
            metadata["chat_id"] = chat_id
        if message_id:
            metadata["message_id"] = message_id
        _, url = await upload_image(request, data, content_type, metadata, user)
        return url
'''

VIDEO_TOOL = r'''
"""
description: Generate WAN videos via local ComfyUI (GPU1 22GB). Inputs are hard-clamped to a reliable GPU-safe envelope.
requirements: aiohttp
"""

import asyncio
import json
import random
import time
from pathlib import Path
import aiohttp

class Tools:
    MAX_WIDTH = 320
    MAX_HEIGHT = 192
    MAX_DURATION_SECONDS = 3
    FPS = 8
    MAX_STEPS = 2

    def _safe_int(self, value, default):
        try:
            return int(value)
        except Exception:
            return default

    def _clamp_video_shape(self, width, height):
        width = self._safe_int(width, self.MAX_WIDTH)
        height = self._safe_int(height, self.MAX_HEIGHT)
        if width <= 0 or height <= 0:
            width, height = self.MAX_WIDTH, self.MAX_HEIGHT
        ratio = width / max(height, 1)
        max_ratio = self.MAX_WIDTH / self.MAX_HEIGHT
        if width > self.MAX_WIDTH or height > self.MAX_HEIGHT:
            if ratio >= max_ratio:
                width = self.MAX_WIDTH
                height = max(128, int(round((width / ratio) / 16)) * 16)
            else:
                height = self.MAX_HEIGHT
                width = max(128, int(round((height * ratio) / 16)) * 16)
        width = max(128, min(self.MAX_WIDTH, (width // 16) * 16))
        height = max(128, min(self.MAX_HEIGHT, (height // 16) * 16))
        return width, height

    def _frame_count(self, duration_seconds):
        duration_seconds = max(1, min(self.MAX_DURATION_SECONDS, self._safe_int(duration_seconds, 1)))
        frames = duration_seconds * self.FPS
        return ((frames + 3) // 4) * 4 + 1, duration_seconds

    def _find_media(self, obj, exts):
        found = []
        def walk(x):
            if isinstance(x, dict):
                filename = x.get("filename")
                if isinstance(filename, str) and filename.lower().endswith(exts):
                    found.append(x)
                for value in x.values():
                    walk(value)
            elif isinstance(x, list):
                for value in x:
                    walk(value)
        walk(obj)
        return found[0] if found else None

    def _recent_file(self, root: str, started_at: float, exts):
        base = Path(root)
        candidates = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in exts and p.stat().st_mtime >= started_at - 2]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    async def _abort(self, session, base_url: str):
        for path, payload in [("/interrupt", None), ("/queue", {"clear": True}), ("/free", {"unload_models": True, "free_memory": True})]:
            try:
                if payload is None:
                    async with session.post(f"{base_url}{path}") as resp:
                        await resp.read()
                else:
                    async with session.post(f"{base_url}{path}", json=payload) as resp:
                        await resp.read()
            except Exception:
                pass

    async def generate_video(self, prompt: str, width: int = 320, height: int = 192, steps: int = 2, duration_seconds: int = 1, seed: int = -1, __request__=None, __user__=None, __event_emitter__=None, __metadata__=None) -> str:
        """Generate a short WAN video on GPU1. Width, height, duration and steps are hard-clamped for reliability."""
        chat_id = (__metadata__ or {}).get("chat_id")
        message_id = (__metadata__ or {}).get("message_id") or (__metadata__ or {}).get("assistant_message_id")
        base_url = "http://127.0.0.1:8188"
        requested = {"width": width, "height": height, "duration_seconds": duration_seconds, "steps": steps}
        width, height = self._clamp_video_shape(width, height)
        num_frames, duration_seconds = self._frame_count(duration_seconds)
        steps = max(1, min(self.MAX_STEPS, self._safe_int(steps, self.MAX_STEPS)))
        if seed < 0:
            seed = random.randint(0, 2 ** 32 - 1)
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Generate WAN on GPU1: {width}x{height}, {num_frames} frames @ {self.FPS}fps, {steps} step(s)", "done": False}})
        workflow = {
            "37": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
            "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
            "39": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["38", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, static, distorted", "clip": ["38", 0]}},
            "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": 8}},
            "55": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["39", 0], "width": width, "height": height, "length": num_frames, "batch_size": 1}},
            "3": {"class_type": "KSampler", "inputs": {"model": ["48", 0], "seed": seed, "steps": steps, "cfg": 5, "sampler_name": "uni_pc", "scheduler": "simple", "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["55", 0], "denoise": 1}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
            "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": self.FPS}},
            "58": {"class_type": "SaveVideo", "inputs": {"video": ["57", 0], "filename_prefix": "video/wan", "format": "mp4", "codec": "h264"}}
        }
        started_at = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base_url}/prompt", json={"prompt": workflow}) as resp:
                    if resp.status >= 400:
                        raise Exception(f"ComfyUI error: {resp.status} {await resp.text()}")
                    result = await resp.json()
                prompt_id = result.get("prompt_id")
                if not prompt_id:
                    raise Exception("No prompt_id in response")
                history = None
                for _ in range(240):
                    await asyncio.sleep(1)
                    async with session.get(f"{base_url}/history/{prompt_id}") as resp:
                        if resp.status >= 400:
                            continue
                        history = await resp.json()
                    if prompt_id in history:
                        break
                else:
                    await self._abort(session, base_url)
                    raise Exception("Timed out waiting for WAN video; Comfy job interrupted")
                entry = history[prompt_id]
                status = entry.get("status", {})
                messages = status.get("messages", [])
                status_str = status.get("status_str")
                if status_str not in (None, "success", "completed") and not entry.get("outputs"):
                    raise Exception(f"ComfyUI status={status_str}: {messages[-1] if messages else 'no status detail'}")
                item = self._find_media(entry.get("outputs", {}), (".mp4", ".webm", ".gif"))
                vid_bytes = None
                filename = None
                content_type = "video/mp4"
                if item:
                    filename = item["filename"]
                    async with session.get(f"{base_url}/view", params={"filename": filename, "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")}) as vid_resp:
                        if vid_resp.status >= 400:
                            raise Exception(f"Failed to download video: {vid_resp.status}")
                        vid_bytes = await vid_resp.read()
                else:
                    path = self._recent_file("/home/chris/ComfyUI/output", started_at, {".mp4", ".webm", ".gif"})
                    if path:
                        filename = path.name
                        vid_bytes = path.read_bytes()
                        if path.suffix.lower() == ".gif":
                            content_type = "image/gif"
                        elif path.suffix.lower() == ".webm":
                            content_type = "video/webm"
                if not vid_bytes:
                    raise Exception(f"No video found in Comfy output (status={status_str}, output_nodes={list(entry.get('outputs', {}).keys())})")
                url = await self._upload_file(__request__, __user__, vid_bytes, filename or "wan.mp4", content_type, chat_id, message_id)
                if __event_emitter__:
                    await __event_emitter__({"type": "chat:message:files", "data": {"files": [{"type": "video", "url": url}]}})
                    await __event_emitter__({"type": "status", "data": {"description": f"Done WAN on GPU1: {width}x{height}, {num_frames} frames", "done": True}})
                return json.dumps({"status": "success", "message": f"Generated WAN video on GPU1 ({width}x{height}, {num_frames} frames @ {self.FPS}fps, {steps} step(s)).", "url": url, "filename": filename, "size_bytes": len(vid_bytes), "requested": requested}, ensure_ascii=False)
        except Exception as e:
            try:
                async with aiohttp.ClientSession() as session:
                    await self._abort(session, base_url)
            except Exception:
                pass
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": f"Failed WAN on GPU1: {e}", "done": True}})
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(f"{base_url}/free", json={"unload_models": True, "free_memory": True})
            except Exception:
                pass

    async def _upload_file(self, request, user_dict, data, filename, content_type, chat_id, message_id):
        from open_webui.routers.images import upload_image
        from open_webui.models.users import UserModel
        user = UserModel(**user_dict) if user_dict else None
        metadata = {}
        if chat_id:
            metadata["chat_id"] = chat_id
        if message_id:
            metadata["message_id"] = message_id
        _, url = await upload_image(request, data, content_type, metadata, user)
        return url
'''

MODEL_PATCHES = {
    'bild-generator:latest': {'name': '2) FLUX (Bild)', 'base_model_id': 'qwen3-fast:latest', 'toolIds': ['comfy_flux_image_generator'], 'description': 'Bildgenerator: FLUX via Comfy Tool, GPU1, harte Safe-Limits'},
    'video-generator:latest': {'name': '3) WAN (Video)', 'base_model_id': 'qwen3-fast:latest', 'toolIds': ['comfy_wan_video_generator_tool'], 'description': 'Videogenerator: WAN via Comfy Tool, GPU1, harte Safe-Limits'},
}

def patch_model(con, model_id, patch, now):
    row = con.execute('select meta, params from model where id=?', (model_id,)).fetchone()
    if not row:
        return
    meta = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    params = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
    caps = meta.setdefault('capabilities', {})
    caps.update({'builtin_tools': True, 'web_search': False, 'image_generation': False, 'code_interpreter': False, 'terminal': False, 'status_updates': True, 'usage': True})
    meta['description'] = patch['description']
    meta['toolIds'] = patch['toolIds']
    meta['defaultFeatureIds'] = []
    meta['hidden'] = False
    meta['enabled'] = True
    params.update({'function_calling': 'native', 'num_ctx': 4096, 'think': False, 'num_thread': 2, 'num_batch': 128})
    con.execute('update model set name=?, base_model_id=?, meta=?, params=?, is_active=1, updated_at=? where id=?', (patch['name'], patch['base_model_id'], json.dumps(meta, ensure_ascii=False), json.dumps(params, ensure_ascii=False), now, model_id))

def apply():
    now = int(time.time())
    backup = DB.with_name(f'{DB.name}.backup.{time.strftime("%Y%m%d-%H%M%S")}')
    backup.write_bytes(DB.read_bytes())
    con = sqlite3.connect(DB)
    try:
        meta = json.dumps({'description': 'Shows GPU route status and injects preset tools server-side.'}, ensure_ascii=False)
        if con.execute('select id from function where id=?', ('gpu_route_status_autoselect',)).fetchone():
            con.execute('update function set name=?, type=?, content=?, meta=?, is_active=1, is_global=1, updated_at=? where id=?', ('GPU Route Status + Tool Autoselect', 'filter', FILTER_CONTENT, meta, now, 'gpu_route_status_autoselect'))
        else:
            con.execute('insert into function (id,user_id,name,type,content,meta,valves,is_active,is_global,updated_at,created_at) values (?,?,?,?,?,?,?,?,?,?,?)', ('gpu_route_status_autoselect', USER_ID, 'GPU Route Status + Tool Autoselect', 'filter', FILTER_CONTENT, meta, None, 1, 1, now, now))
        for tool_id, name, content in [('comfy_flux_image_generator', 'Comfy FLUX Image Generator', IMAGE_TOOL), ('comfy_wan_video_generator_tool', 'Comfy WAN Video Generator', VIDEO_TOOL)]:
            if con.execute('select id from tool where id=?', (tool_id,)).fetchone():
                con.execute('update tool set name=?, content=?, specs=?, updated_at=? where id=?', (name, content, json.dumps({'description': name}), now, tool_id))
            else:
                con.execute('insert into tool (id,user_id,name,content,specs,meta,valves,updated_at,created_at) values (?,?,?,?,?,?,?,?,?)', (tool_id, USER_ID, name, content, json.dumps({'description': name}), json.dumps({'description': name}), None, now, now))
        for model_id, patch in MODEL_PATCHES.items():
            patch_model(con, model_id, patch, now)
        con.commit()
    finally:
        con.close()
    print(f'backup={backup}')
    print('applied media tools/filter/model config')

if __name__ == '__main__':
    apply()

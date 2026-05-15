#!/usr/bin/env python3
import json
import shutil
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("/home/chris/.open-webui/webui.db")
MODEL_ID = "hf.co/unsloth/Qwen3-30B-A3B-GGUF:UD-IQ2_XXS"
MODEL_SYSTEM_PROMPT = (
    "/no_think\n"
    "Du hast aktive Tools für Mediengenerierung. "
    "Wenn der Nutzer ein Bild möchte, rufe IMMER das Tool generate_image auf. "
    "Wenn der Nutzer ein Video möchte, rufe IMMER das Tool generate_video auf. "
    "Behaupte niemals, dass du keine Bild- oder Video-Fähigkeiten hast. "
    "Wenn ein Tool fehlschlägt, gib den echten Fehler kurz aus und starte sofort einen zweiten Versuch mit sicheren Defaults."
)


def load_json(raw: str, default):
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        return default if parsed is None else parsed
    except Exception:
        return default


def patch_flux_tool(content: str) -> str:
    replacements = [
        (
            'prompt_boosted = f"{prompt}, hochwertig, detailreich, scharf, professionelle Fotografie, schöne Beleuchtung, Meisterwerk, 8K"',
            "prompt_boosted = prompt",
        ),
        (
            'await __event_emitter__({"type": "status", "data": {"description": f"FLUX on GPU2: {width}x{height}, {steps} steps", "done": False}})',
            'await __event_emitter__({"type": "status", "data": {"description": f"FLUX Schnell (4-step) on GPU2: {width}x{height}", "done": False}})',
        ),
        ("w = self._clamp_dim(w, 768, 384, 1280)", "w = self._clamp_dim(w, 1024, 384, 1280)"),
        ("h = self._clamp_dim(h, 512, 384, 768)", "h = self._clamp_dim(h, 576, 384, 768)"),
        ("if area > 589824:", "if area > 786432:"),
        ("await __event_emitter__({\"type\": \"status\", \"data\": {\"description\": f\"FLUX Schnell (4-step) on GPU2: {width}x{height}\", \"done\": False}})", "await __event_emitter__({\"type\": \"status\", \"data\": {\"description\": f\"FLUX Schnell HQ on GPU2: {width}x{height} (4-step)\", \"done\": False}})"),
    ]
    for old, new in replacements:
        content = content.replace(old, new)
    return content


def patch_wan_tool(content: str) -> str:
    replacements = [
        ("MAX_W, MAX_H, MAX_DUR = 832, 480, 4", "MAX_W, MAX_H, MAX_DUR = 1280, 720, 5"),
        ("FPS = 24", "FPS = 16"),
        ("FPS = 16", "FPS = 16"),
        ("async def generate_video(self, prompt: str, width: int = 640, height: int = 384, steps: int = 20, duration_seconds: int = 3, seed: int = -1", "async def generate_video(self, prompt: str, width: int = 832, height: int = 480, steps: int = 28, duration_seconds: int = 5, seed: int = -1"),
        ("steps = max(5, min(30, self._safe_int(steps, 20)))", "steps = max(12, min(28, self._safe_int(steps, 20)))"),
        ("cfg = max(5.0, min(8.0, 4.0 + steps * 0.15))", "cfg = 5.0"),
        ("steps = max(12, min(28, self._safe_int(steps, 20)))", "steps = max(20, min(32, self._safe_int(steps, 28)))"),
        ("cfg = 5.0", "cfg = 5.8"),
        ("umt5_xxl_fp8_e4m3fn_scaled.safetensors", "umt5-xxl-enc-fp8_e4m3fn.safetensors"),
        (
            'await __event_emitter__({"type": "status", "data": {"description": f"WAN on GPU1: {width}x{height} {num_frames}f {steps}step", "done": False}})',
            'await __event_emitter__({"type": "status", "data": {"description": f"WAN2.2 5B HQ on GPU1: {width}x{height}, {num_frames}f@16fps, {steps} steps", "done": False}})',
        ),
        (
            'await __event_emitter__({"type": "status", "data": {"description": f"WAN2.2 5B official-style on GPU1: {width}x{height}, {num_frames}f@24fps, {steps} steps", "done": False}})',
            'await __event_emitter__({"type": "status", "data": {"description": f"WAN2.2 5B HQ on GPU1: {width}x{height}, {num_frames}f@16fps, {steps} steps", "done": False}})',
        ),
        (
            "positive_prompt\": prompt,",
            "positive_prompt\": f\"{prompt}, cinematic, physically plausible motion, coherent subject, sharp details, natural lighting, high quality\",",
        ),
    ]
    for old, new in replacements:
        content = content.replace(old, new)
    return content


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    backup = DB_PATH.with_name(f"webui.db.backup.fix-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(DB_PATH, backup)
    print(f"backup: {backup}")

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    cfg_row = cur.execute("select data from config where id=1").fetchone()
    if not cfg_row:
        raise SystemExit("config row id=1 not found")
    cfg = load_json(cfg_row[0], {})
    cfg.setdefault("ui", {})
    cfg.setdefault("auth", {})
    cfg.setdefault("image_generation", {})
    cfg["image_generation"].setdefault("comfyui", {})
    cfg.setdefault("rag", {}).setdefault("web", {}).setdefault("search", {})

    cfg["ui"]["default_models"] = MODEL_ID
    cfg["ui"]["default_pinned_models"] = MODEL_ID
    cfg["ui"]["model_order_list"] = [MODEL_ID]
    cfg["image_generation"]["engine"] = "comfyui"
    cfg["image_generation"]["comfyui"]["base_url"] = "http://127.0.0.1:8187"
    cfg["rag"]["web"]["search"]["enable"] = False
    cfg["auth"]["enable_api_keys"] = True

    cur.execute("update config set data=? where id=1", (json.dumps(cfg, ensure_ascii=False),))

    model_row = cur.execute(
        "select id,meta,params from model where id=?",
        (MODEL_ID,),
    ).fetchone()
    if not model_row:
        raise SystemExit(f"model not found: {MODEL_ID}")

    meta = load_json(model_row[1], {})
    params = load_json(model_row[2], {})
    caps = meta.setdefault("capabilities", {})
    caps["web_search"] = False
    caps["image_generation"] = True
    caps["video_generation"] = True
    caps["builtin_tools"] = False
    caps["code_interpreter"] = False
    caps["terminal"] = False
    meta["toolIds"] = ["comfy_flux_image_generator", "comfy_wan_video_generator_tool"]
    meta["defaultFeatureIds"] = ["image_generation"]
    params["function_calling"] = "native"
    params["system"] = MODEL_SYSTEM_PROMPT
    params["temperature"] = 0.2
    params["num_ctx"] = 16384
    params["think"] = False

    cur.execute("update model set name=?, meta=?, params=?, is_active=1 where id=?", (MODEL_ID, json.dumps(meta, ensure_ascii=False), json.dumps(params, ensure_ascii=False), MODEL_ID))
    cur.execute("delete from model where id='assistent:latest'")

    # Normalize per-user default model selection to the single supported profile
    user_rows = cur.execute("select id,settings from user").fetchall()
    for user_id, settings_raw in user_rows:
        settings = load_json(settings_raw, {})
        ui = settings.setdefault("ui", {})
        ui["models"] = [MODEL_ID]
        ui["pinnedModels"] = [MODEL_ID]
        cur.execute("update user set settings=? where id=?", (json.dumps(settings, ensure_ascii=False), user_id))

    # Ensure existing chats using this model keep tool flags enabled.
    rows = cur.execute("select id,chat from chat").fetchall()
    for chat_id, chat_raw in rows:
        chat = load_json(chat_raw, {})
        models = chat.get("models") or []
        model0 = models[0] if models else None
        model_field = chat.get("model")
        model_id = None
        if isinstance(model_field, dict):
            model_id = model_field.get("id")
        elif isinstance(model_field, str):
            model_id = model_field
        if MODEL_ID not in {model0, model_id}:
            continue
        chat["toolIds"] = [
            "comfy_flux_image_generator",
            "comfy_wan_video_generator_tool",
        ]
        features = chat.setdefault("features", {})
        features["builtin_tools"] = 0
        features["image_generation"] = 1
        features["video_generation"] = 1
        features["code_interpreter"] = 0
        params_chat = chat.setdefault("params", {})
        params_chat["function_calling"] = "native"
        params_chat["think"] = False
        cur.execute("update chat set chat=? where id=?", (json.dumps(chat, ensure_ascii=False), chat_id))

    # Auto-heal new/updated chats for this model (no app code patching required)
    cur.execute("DROP TRIGGER IF EXISTS chat_media_autofix_insert")
    cur.execute("DROP TRIGGER IF EXISTS chat_media_autofix_update")
    cur.execute(
        f"""
        CREATE TRIGGER chat_media_autofix_insert
        AFTER INSERT ON chat
        WHEN json_extract(NEW.chat, '$.models[0]') = '{MODEL_ID}'
        BEGIN
            UPDATE chat
            SET chat = json_set(
                json_set(
                    json_set(
                        json_set(
                            json_set(NEW.chat,
                                '$.toolIds', json('["comfy_flux_image_generator","comfy_wan_video_generator_tool"]')
                            ),
                            '$.features.builtin_tools', 0
                        ),
                        '$.features.image_generation', 1
                    ),
                    '$.features.video_generation', 1
                ),
                '$.params.function_calling', 'native'
            )
            WHERE id = NEW.id;
        END
        """
    )
    cur.execute(
        f"""
        CREATE TRIGGER chat_media_autofix_update
        AFTER UPDATE ON chat
        WHEN json_extract(NEW.chat, '$.models[0]') = '{MODEL_ID}'
        BEGIN
            UPDATE chat
            SET chat = json_set(
                json_set(
                    json_set(
                        json_set(
                            json_set(NEW.chat,
                                '$.toolIds', json('["comfy_flux_image_generator","comfy_wan_video_generator_tool"]')
                            ),
                            '$.features.builtin_tools', 0
                        ),
                        '$.features.image_generation', 1
                    ),
                    '$.features.video_generation', 1
                ),
                '$.params.function_calling', 'native'
            )
            WHERE id = NEW.id;
        END
        """
    )

    tool_rows = cur.execute(
        "select id,content from tool where id in ('comfy_flux_image_generator','comfy_wan_video_generator_tool')"
    ).fetchall()
    tool_map = {row[0]: row[1] for row in tool_rows}
    if "comfy_flux_image_generator" in tool_map:
        flux_specs = [
            {
                "name": "generate_image",
                "description": "Generate FLUX Schnell HQ images via ComfyUI (GPU2).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Text prompt for image"},
                        "width": {"type": "integer", "default": 1024, "description": "Width 384-1280"},
                        "height": {"type": "integer", "default": 576, "description": "Height 384-768"},
                        "steps": {"type": "integer", "default": 4, "description": "FLUX Schnell distilled steps"},
                        "seed": {"type": "integer", "default": -1, "description": "-1 for random"},
                    },
                    "required": ["prompt"],
                },
            }
        ]
        cur.execute(
            "update tool set content=?, specs=? where id='comfy_flux_image_generator'",
            (patch_flux_tool(tool_map["comfy_flux_image_generator"]), json.dumps(flux_specs, ensure_ascii=False)),
        )
    if "comfy_wan_video_generator_tool" in tool_map:
        wan_specs = [
            {
                "name": "generate_video",
                "description": "Generate WAN2.2 5B HQ video via ComfyUI (GPU1).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Text prompt for video"},
                        "width": {"type": "integer", "default": 832, "description": "Width up to 1280"},
                        "height": {"type": "integer", "default": 480, "description": "Height up to 720"},
                        "steps": {"type": "integer", "default": 28, "description": "HQ range 20-32"},
                        "duration_seconds": {"type": "integer", "default": 5, "description": "Duration 1-5 sec (81 frames @16fps for 5s)"},
                        "seed": {"type": "integer", "default": -1, "description": "-1 for random"},
                    },
                    "required": ["prompt"],
                },
            }
        ]
        cur.execute(
            "update tool set content=?, specs=? where id='comfy_wan_video_generator_tool'",
            (patch_wan_tool(tool_map["comfy_wan_video_generator_tool"]), json.dumps(wan_specs, ensure_ascii=False)),
        )

    con.commit()
    con.close()
    print("db patch: ok")


if __name__ == "__main__":
    main()

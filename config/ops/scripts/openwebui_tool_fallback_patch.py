#!/usr/bin/env python3
from pathlib import Path

TARGET = Path(
    "/home/chris/.local/share/uv/tools/open-webui/lib/python3.11/site-packages/open_webui/utils/middleware.py"
)

txt = TARGET.read_text(encoding="utf-8", errors="ignore")
orig = txt

needle1 = "    tool_ids = form_data.pop('tool_ids', None)\n"
patch1 = (
    "    tool_ids = form_data.pop('tool_ids', None)\n"
    "    if tool_ids is None:\n"
    "        # Frontends and some API clients send camelCase.\n"
    "        tool_ids = form_data.pop('toolIds', None)\n"
)
if patch1 not in txt and needle1 in txt:
    txt = txt.replace(needle1, patch1, 1)

needle2 = "        tool_ids = metadata.get('tool_ids', None)\n"
patch2 = (
    "        tool_ids = metadata.get('tool_ids', None)\n"
    "        if tool_ids is None:\n"
    "            tool_ids = metadata.get('toolIds', None)\n"
)
if patch2 not in txt and needle2 in txt:
    txt = txt.replace(needle2, patch2, 1)

needle3 = "        log.debug(f'{tool_ids=}')\n"
patch3 = (
    "        if not tool_ids:\n"
    "            # Final fallback: use model-attached tool ids when request payload\n"
    "            # omitted tool ids. This keeps model-level tool assignments usable\n"
    "            # across clients that do not send `tool_ids`.\n"
    "            model_meta = (model.get('info', {}).get('meta') or {})\n"
    "            tool_ids = model_meta.get('toolIds') or model_meta.get('tool_ids')\n"
    "\n"
    "        log.debug(f'{tool_ids=}')\n"
)
if patch3 not in txt and needle3 in txt:
    txt = txt.replace(needle3, patch3, 1)

if txt != orig:
    TARGET.write_text(txt, encoding="utf-8")
    print("patched")
else:
    print("already_patched")

#!/usr/bin/env python3
"""Get an independent Hugging Face SHA256 for the exact public GGUF.
Does not use the local download to establish trust. Output is a sha256sum manifest.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

REPO = "mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF"
FILE = "Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
def upstream_sha():
    url = "https://huggingface.co/api/models/" + REPO + "/tree/main?recursive=false&expand=true"
    with urlopen(Request(url, headers={"User-Agent": "MAG-qwen-pin/1"}), timeout=40) as r:
        records = json.load(r)
    if not isinstance(records, list):
        raise ValueError("Hugging Face response is not a file listing")
    for item in records:
        if item.get("path") != FILE:
            continue
        lfs = item.get("lfs") or {}
        digest = lfs.get("sha256") or lfs.get("oid") or ""
        if re.fullmatch(r"[a-f0-9]{64}", digest):
            return digest
        # HF can serve Xet blobs without the lfs field. Their resolve HEAD
        # returns x-linked-etag (the SHA256 of the file).
        from huggingface_hub import get_hf_file_metadata, hf_hub_url
        info = get_hf_file_metadata(hf_hub_url(REPO, FILE))
        digest = (info.etag or "").strip('"')
        if re.fullmatch(r"[a-f0-9]{64}", digest):
            return digest
        raise ValueError("No independently verifiable upstream SHA256")
    raise ValueError("Exact GGUF filename not present upstream")
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(Path.home() / "models/qwen3.8-flash-next-uncensored/upstream.sha256"))
    args = ap.parse_args()
    digest = upstream_sha()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{digest}  {FILE}\n", encoding="ascii")
    print(f"Upstream SHA256 pinned to {out} (from Hugging Face, not local GGUF)")
if __name__ == "__main__":
    main()

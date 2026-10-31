#!/usr/bin/env python3
"""Fetch the pinned six-shard manifest from the Hugging Face Hub tree API."""
import json
import pathlib
import re
import sys
import urllib.request

REPO = "unsloth/Mistral-Large-4-1T-A52B-GGUF"
REVISION = "main"
FOLDER = "UD-IQ1_S"
BASE = "Mistral-Large-4-1T-A52B-UD-IQ1_S"
EXPECTED_NAMES = [f"{BASE}-{i:05d}-of-00006.gguf" for i in range(1, 7)]
API = f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}/{FOLDER}?recursive=true&expand=true"

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: fetch-hf-sha256.py MODEL_DIRECTORY")
    model_dir = pathlib.Path(sys.argv[1])
    if not model_dir.is_dir() or model_dir.is_symlink():
        raise SystemExit("Mistral model directory missing or symlink")
    request = urllib.request.Request(API, headers={"User-Agent": "mag-mistral-hf-manifest/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        entries = json.load(response)
    by_name = {}
    for entry in entries:
        name = pathlib.PurePosixPath(entry.get("path", "")).name
        if name in EXPECTED_NAMES and entry.get("type") == "file":
            lfs = entry.get("lfs") or {}
            oid = lfs.get("oid") or lfs.get("sha256")
            if isinstance(oid, str) and oid.startswith("sha256:"):
                oid = oid[7:]
            if not isinstance(oid, str) or not re.fullmatch(r"[0-9a-f]{64}", oid):
                raise SystemExit(f"HF API did not return a SHA256 LFS oid for {name}: {lfs!r}")
            by_name[name] = oid
    if set(by_name) != set(EXPECTED_NAMES):
        raise SystemExit(f"HF API tree is incomplete or changed: {sorted(by_name)}")
    api_manifest = "".join(f"{by_name[name]}  {name}\n" for name in EXPECTED_NAMES)
    pinned = pathlib.Path(__file__).with_name("SHA256SUMS").read_text()
    if api_manifest != pinned:
        raise SystemExit("HF API LFS SHA256s differ from the reviewed ops/mistral/SHA256SUMS; refusing activation")
    target = model_dir / "SHA256SUMS"
    if target.exists():
        if target.is_symlink():
            raise SystemExit("model-directory SHA256SUMS must not be a symlink")
        # The operator may have recorded only shard 1 while the download is
        # running. Extend a correct partial manifest from the API, but never
        # silently repair or replace a conflicting checksum.
        for line in target.read_text().splitlines():
            fields = line.split()
            if len(fields) != 2:
                raise SystemExit("existing model-directory SHA256SUMS has an invalid row")
            digest, name = fields
            if name not in by_name or digest != by_name[name]:
                raise SystemExit(f"existing SHA256SUMS conflicts with HF API: {name}")
    temp = model_dir / ".SHA256SUMS.tmp"
    temp.write_text(api_manifest)
    temp.chmod(0o644)
    temp.replace(target)
    print("HF Hub API SHA256 manifest verified for all six UD-IQ1_S shards")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download only explicitly pinned coding GGUFs; never change live MAG services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MANIFEST = json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))


def verified_file(path, sha):
    if path.is_symlink():
        raise RuntimeError(f"symlink refused: {path}")
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == sha


def stage(name, root, download=False):
    spec = MANIFEST["models"][name]
    filename = spec["filename"]
    sha = spec["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", sha) or not re.fullmatch(r"[A-Za-z0-9_.-]+[.]gguf", filename):
        raise RuntimeError("invalid pinned model metadata")
    target_dir = root / name
    target = target_dir / filename
    if target_dir.is_symlink() or target.is_symlink():
        raise RuntimeError("refusing symlink model destination")
    if verified_file(target, sha):
        print(f"{name}: VERIFIED {target}")
        return
    if target.exists():
        raise RuntimeError(f"{name}: existing file SHA256 mismatch: {target} (preserved)")
    if not download:
        raise RuntimeError(f"{name}: not staged. Run with --download to fetch the pinned GGUF")
    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(filename + ".part")
    if temp.is_symlink():
        raise RuntimeError(f"refusing symlink download target: {temp}")
    url = f"https://huggingface.co/{spec['repo']}/resolve/main/{filename}"
    # Resumable. A changed upstream object is rejected by the pinned SHA256.
    subprocess.run(["curl", "--fail", "--location", "--retry", "5", "--continue-at", "-",
                    "--output", str(temp), url], check=True)
    if not verified_file(temp, sha):
        raise RuntimeError(f"{name}: downloaded SHA256 mismatch; .part retained, never activated")
    os.replace(temp, target)
    print(f"{name}: VERIFIED {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["general", "coder", "both"], default="both")
    parser.add_argument("--root", type=Path, default=Path("/home/chris/models/mag-coding"))
    parser.add_argument("--download", action="store_true", help="Fetch missing model files; never stops a service")
    args = parser.parse_args()
    try:
        for name in (list(MANIFEST["models"]) if args.model == "both" else [args.model]):
            stage(name, args.root, args.download)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

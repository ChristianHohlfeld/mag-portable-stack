#!/usr/bin/env python3
"""Evict only the six Mistral GGUF shard pages before NUMA0 mmap loading."""
import argparse
import os
import re
from pathlib import Path

MODEL_DIR = Path("/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S")
SHARD = re.compile(r"^Mistral-Large-4-1T-A52B-UD-IQ1_S-0000[1-6]-of-00006[.]gguf$")


def shard_names(manifest: Path) -> list[str]:
    rows = manifest.read_text(encoding="utf-8").splitlines()
    if len(rows) != 6:
        raise ValueError(f"expected exactly six shard rows, got {len(rows)}")
    names = []
    for row in rows:
        fields = row.split()
        if len(fields) != 2 or not re.fullmatch(r"[a-f0-9]{64}", fields[0]):
            raise ValueError(f"invalid SHA256 manifest row: {row!r}")
        name = fields[1]
        if not SHARD.fullmatch(name) or name in names:
            raise ValueError(f"unexpected or duplicate Mistral shard: {name!r}")
        names.append(name)
    expected = {
        f"Mistral-Large-4-1T-A52B-UD-IQ1_S-0000{i}-of-00006.gguf"
        for i in range(1, 7)
    }
    if set(names) != expected:
        raise ValueError("manifest must name each of the six Mistral shards exactly once")
    return names


def evict_shard_cache(model_dir: Path) -> list[str]:
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        raise RuntimeError("Python/kernel lacks POSIX_FADV_DONTNEED support")
    names = shard_names(model_dir / "SHA256SUMS")
    for name in names:
        path = model_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing, non-regular, or symlinked shard: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    args = parser.parse_args()
    names = evict_shard_cache(args.model_dir)
    print(f"Requested POSIX_FADV_DONTNEED for exactly {len(names)} Mistral GGUF shards")


if __name__ == "__main__":
    main()

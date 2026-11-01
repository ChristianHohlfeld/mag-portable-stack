#!/usr/bin/env python3
"""Evict inactive NUMA0 Qwen cache, then sequentially prefetch Mistral on NUMA0."""
import argparse
import os
import re
import subprocess
from pathlib import Path

MODEL_DIR = Path("/home/chris/models/Mistral-Large-4-1T-A52B-UD-IQ1_S")
QWEN_MODEL = Path("/home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf")
QWEN_UNITS = ("mag-llm-qwen-room.service", "mag-llm-qwen.service")
SHARD = re.compile(r"^Mistral-Large-4-1T-A52B-UD-IQ1_S-0000[1-6]-of-00006[.]gguf$")


def shard_names(manifest: Path) -> list[str]:
    rows = manifest.read_text(encoding="utf-8").splitlines()
    if len(rows) != 6:
        raise ValueError(f"expected exactly six Mistral shards, got {len(rows)}")
    names = []
    for row in rows:
        fields = row.split()
        if len(fields) != 2 or not re.fullmatch(r"[a-f0-9]{64}", fields[0]):
            raise ValueError(f"invalid Mistral SHA256 manifest row: {row!r}")
        name = fields[1]
        if not SHARD.fullmatch(name) or name in names:
            raise ValueError(f"unexpected or duplicate Mistral shard: {name!r}")
        names.append(name)
    expected = {
        f"Mistral-Large-4-1T-A52B-UD-IQ1_S-0000{i}-of-00006.gguf"
        for i in range(1, 7)
    }
    if set(names) != expected:
        raise ValueError("manifest must name all six Mistral shards exactly once")
    return names


def qwen_is_running() -> bool:
    """Fail closed: never evict Qwen pages if either known Qwen unit is active or unknown."""
    for unit in QWEN_UNITS:
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", "--quiet", unit],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        except OSError:
            return True
        if result.returncode == 0 or result.returncode not in (3, 4):
            return True
    try:
        for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                if QWEN_MODEL.name.encode() in cmdline.read_bytes():
                    return True
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    except OSError:
        return True
    return False


def evict_inactive_qwen_cache(qwen_model: Path, running: bool) -> bool:
    if running or not qwen_model.exists():
        return False
    if qwen_model.is_symlink() or not qwen_model.is_file():
        raise ValueError(f"Qwen model path is not a regular file: {qwen_model}")
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        raise RuntimeError("Python/kernel lacks POSIX_FADV_DONTNEED support")
    fd = os.open(qwen_model, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
    return True


def prefetch(model_dir: Path, qwen_model: Path = QWEN_MODEL, qwen_running: bool = False,
             runner=subprocess.run) -> list[str]:
    evicted = evict_inactive_qwen_cache(qwen_model, qwen_running)
    print("Qwen NUMA0 cache: " + ("targeted DONTNEED requested" if evicted else
                                  "left untouched (running or file absent)"), flush=True)
    names = shard_names(model_dir / "SHA256SUMS")
    for index, name in enumerate(names, 1):
        path = model_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing, non-regular, or symlinked Mistral shard: {path}")
        print(f"Mistral NUMA0 prefetch {index}/6: {name}", flush=True)
        runner([
            "/usr/bin/numactl", "--membind=0", "/usr/bin/dd",
            f"if={path}", "of=/dev/null", "bs=16M", "status=none",
        ], check=True)
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--qwen-model", type=Path, default=QWEN_MODEL)
    args = parser.parse_args()
    prefetch(args.model_dir, args.qwen_model, qwen_is_running())
    print("All six Mistral shards read sequentially with NUMA0 memory binding")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fail closed unless the pinned llama.cpp revision maps the expected GGUF arch to a loader."""
import argparse
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--gguf-architecture", required=True)
    args = parser.parse_args()

    arch_h = (args.source / "src/llama-arch.h").read_text(encoding="utf-8")
    arch_cpp = (args.source / "src/llama-arch.cpp").read_text(encoding="utf-8")
    model_cpp = (args.source / "src/llama-model.cpp").read_text(encoding="utf-8")

    expected = {
        "mistral-large4": "LLM_ARCH_MISTRAL_LARGE4",
    }
    enum = expected.get(args.gguf_architecture)
    if enum is None:
        raise SystemExit(f"unexpected GGUF general.architecture: {args.gguf_architecture!r}")
    if not re.search(r"\\b" + enum + r"\\b", arch_h):
        raise SystemExit(f"pinned llama-arch.h lacks {enum}")
    mapping = re.compile(
        r"\\{\\s*" + enum + r"\\s*,\\s*\\"" +
        re.escape(args.gguf_architecture) + r"\\"\\s*\\}"
    )
    if not mapping.search(arch_cpp):
        raise SystemExit(
            f"pinned llama-arch.cpp does not map {enum} to {args.gguf_architecture!r}"
        )
    if not re.search(r"case\\s+" + enum + r"\\s*:", model_cpp):
        raise SystemExit(f"pinned llama-model.cpp has no loader dispatch for {enum}")
    print(f"GGUF architecture {args.gguf_architecture!r} maps to {enum} and a model loader")


if __name__ == "__main__":
    main()

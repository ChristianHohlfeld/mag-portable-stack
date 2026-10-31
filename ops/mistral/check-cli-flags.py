#!/usr/bin/env python3
"""Compare every llama-server option in ops/mistral/start.sh with pinned binary help."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "ops/mistral/start.sh"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, type=Path)
    args = parser.parse_args()
    text = START.read_text(encoding="utf-8")
    marker = "exec /usr/bin/numactl"
    if marker not in text:
        raise SystemExit("Mistral launcher has no expected numactl exec")
    command = text.split(marker, 1)[1]
    if '"$BIN"' not in command:
        raise SystemExit("Mistral launcher does not execute its pinned BIN")
    command = command.split('"$BIN"', 1)[1]
    flags = sorted(set(re.findall(r"(?<![A-Za-z0-9_])--?[A-Za-z][A-Za-z0-9-]*", command)))
    if not flags:
        raise SystemExit("no llama-server flags extracted from Mistral launcher")
    proc = subprocess.run([str(args.server), "--help"], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False)
    help_text = proc.stdout or ""
    if not help_text.strip():
        raise SystemExit("pinned llama-server --help returned no output")
    missing = [flag for flag in flags
               if not re.search(r"(?<![A-Za-z0-9_-])" + re.escape(flag) +
                                r"(?=$|[\\s,=])", help_text)]
    if missing:
        raise SystemExit("Mistral launcher flags absent from pinned llama-server --help: " +
                         ", ".join(missing))
    print("Mistral launcher flags verified against pinned llama-server: " +
          ", ".join(flags))


if __name__ == "__main__":
    main()

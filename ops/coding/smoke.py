#!/usr/bin/env python3
"""Measure candidate coding output with SSE; does not touch the running MAG room."""
import argparse
import json
import time
import urllib.error
import urllib.request

PROMPT = (
    "Write a complete, self-contained Python 3 module implementing JSON Patch "
    "(RFC 6902 add/remove/replace/move/copy/test). Include exceptions, strict "
    "path parsing, usage, and a thorough unittest suite in the same answer. "
    "Give runnable source code, not an outline. Do not abbreviate functions."
)


def run(port, model, max_tokens):
    request = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": max_tokens,
        "cache_prompt": True,
        "id_slot": 0,
    }
    start = time.monotonic()
    first_visible = None
    text = []
    finish = None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3600) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            chunk = line[6:].strip()
            if chunk == b"[DONE]":
                break
            event = json.loads(chunk)
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            for choice in event.get("choices", []):
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                visible = choice.get("delta", {}).get("content")
                if visible:
                    if first_visible is None:
                        first_visible = time.monotonic() - start
                    text.append(visible)
    elapsed = time.monotonic() - start
    body = "".join(text)
    print(json.dumps({
        "model": model,
        "port": port,
        "seconds_total": round(elapsed, 2),
        "seconds_to_first_visible_text": None if first_visible is None else round(first_visible, 2),
        "visible_characters": len(body),
        "finish_reason": finish,
        "max_tokens_requested": max_tokens,
    }, indent=2))
    # Never misreport a token cap as a complete coding answer.
    if finish == "length" or not body.strip():
        raise RuntimeError("Coding answer was truncated or empty")
    if len(body) < 4000:
        raise RuntimeError("Coding response too short for this acceptance prompt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=["general", "coder"])
    parser.add_argument("--max-tokens", type=int, default=16384)
    args = parser.parse_args()
    if not 4096 <= args.max_tokens <= 32768:
        parser.error("--max-tokens must be 4096..32768")
    spec = json.loads(__import__("pathlib").Path(__file__).with_name("models.json").read_text())["models"][args.model]
    try:
        run(spec["candidate_port"], spec["model_id"], args.max_tokens)
    except (RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()

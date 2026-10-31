#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8191)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--health-only", action="store_true")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(5)
    else:
        raise SystemExit(f"Mistral :{args.port} health timeout")
    if args.health_only:
        print("Mistral health PASS")
        return
    payload = {
        "model": "mistral-large4",
        "messages": [
            {"role": "system", "content": "Du bist Mistral. Antworte kurz auf Deutsch."},
            {"role": "user", "content": "Antworte exakt mit: Mistral bereit."},
        ],
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 0,
        "min_p": 0.0,
        "reasoning_effort": "low",
        "chat_template_kwargs": {"reasoning_effort": "low"},
        "max_tokens": 48,
        "stream": False,
        "cache_prompt": True,
        "id_slot": 0,
    }
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer local-llama"},
    )
    with urllib.request.urlopen(req, timeout=args.timeout) as r:
        result = json.loads(r.read())
    text = result["choices"][0]["message"].get("content", "")
    if not isinstance(text, str) or not text.strip():
        raise SystemExit("Mistral returned an empty answer")
    print("Mistral chat PASS:", text.strip()[:160])

if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Mistral smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

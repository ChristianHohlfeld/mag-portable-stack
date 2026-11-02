#!/usr/bin/env python3
"""Real decoder test before MAG routes users to either coding model."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

MANIFEST = json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))
BY_PORT = {8191 + spec["numa"]: spec["model_id"] for spec in MANIFEST["models"].values()}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port", type=int, choices=sorted(BY_PORT), required=True)
    parser.add_argument("--model", choices=sorted(BY_PORT.values()))
    args=parser.parse_args()
    model=BY_PORT[args.port]
    if args.model and args.model != model:
        parser.error("model/port NUMA mismatch")
    prompt="Return only a valid Python function named add that returns a+b, without prose."
    # Flint always reasons before answering (no thinking switch in its template),
    # so the budget covers a short reasoning trace plus the function.
    data=json.dumps({
      "model":model,
      "messages":[{"role":"user","content":prompt}],
      "max_tokens":4096,"stream":False,"cache_prompt":True,"id_slot":0,
      "chat_template_kwargs":{"enable_thinking":False},
    }).encode()
    request=urllib.request.Request(
      f"http://127.0.0.1:{args.port}/v1/chat/completions",
      data=data,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=1800) as response:
            answer=json.load(response)
        choice=answer["choices"][0]
        content=choice["message"].get("content") or ""
        if choice.get("finish_reason")=="length":
            raise ValueError("output truncated at 4096 tokens")
        if "def add" not in content or "return a + b" not in content.replace("return a+b","return a + b"):
            raise ValueError("model did not generate the requested function")
        print(f"PASS: {model} on {args.port} decoded Python source")
        return 0
    except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        print(f"FAIL: {model} on {args.port}: {exc}",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())

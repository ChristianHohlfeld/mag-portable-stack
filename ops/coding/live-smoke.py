#!/usr/bin/env python3
"""Real decoder test before MAG routes users to either coding model."""
import argparse
import json
import sys
import urllib.error
import urllib.request

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port", type=int, choices=(8191,8192), required=True)
    parser.add_argument("--model", choices=("qwen3.6-35b-a3b","qwen3-coder-30b-a3b-instruct"),required=True)
    args=parser.parse_args()
    expected_port=8191 if args.model=="qwen3.6-35b-a3b" else 8192
    if args.port != expected_port:
        parser.error("model/port NUMA mismatch")
    prompt="Return only a valid Python function named add that returns a+b, without prose."
    data=json.dumps({
      "model":args.model,
      "messages":[{"role":"user","content":prompt}],
      "max_tokens":256,"stream":False,"cache_prompt":True,"id_slot":0,
      "chat_template_kwargs":{"enable_thinking":False},
    }).encode()
    request=urllib.request.Request(
      f"http://127.0.0.1:{args.port}/v1/chat/completions",
      data=data,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=300) as response:
            answer=json.load(response)
        choice=answer["choices"][0]
        content=choice["message"].get("content") or ""
        if choice.get("finish_reason")=="length":
            raise ValueError("output truncated at 256 tokens")
        if "def add" not in content or "return a + b" not in content.replace("return a+b","return a + b"):
            raise ValueError("model did not generate the requested function")
        print(f"PASS: {args.model} on {args.port} decoded Python source")
        return 0
    except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        print(f"FAIL: {args.model} on {args.port}: {exc}",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Qwen contract: chat, thinking, function calling, SSE over localhost only."""
import argparse
import json
import urllib.request

MODEL = "qwen3.8-flash-next-uncensored"

def call(port, *, thinking=False, tools=False, stream=False):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": (
            "Call echo with value READY; do not answer in prose." if tools
            else "Say READY." if not thinking else "Think carefully: what is 2+2?"
        )}],
        "temperature": 0,
        "max_tokens": 256 if thinking else 128,
        "chat_template_kwargs": {"enable_thinking": thinking},
        "stream": stream,
    }
    if tools:
        body["tools"] = [{
            "type": "function",
            "function": {"name": "echo", "description": "Return a value.",
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                               "required": ["value"], "additionalProperties": False}},
        }]
        body["tool_choice"] = "required"
    url = "http://127.0.0.1:%s/v1/chat/completions" % port
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as res:
        if stream:
            events, done = 0, False
            for line in res:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                if json.loads(data).get("choices"):
                    events += 1
            assert done and events > 0, "SSE missing data or [DONE]"
            return
        reply = json.load(res)
    assert reply.get("choices"), "OpenAI choices missing"
    msg = reply["choices"][0]["message"]
    if tools:
        tool_calls = msg.get("tool_calls") or []
        assert tool_calls, "No structured tool_calls; Qwen Jinja incompatible"
        fn = tool_calls[0]["function"]
        assert fn.get("name") == "echo", "wrong tool name"
        assert json.loads(fn["arguments"]).get("value"), "tool arguments missing"
    else:
        assert msg.get("content") or msg.get("reasoning_content"), "empty completion"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, choices=[8091, 8080], default=8091)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    call(args.port, thinking=False)
    print("PASS: Qwen chat, thinking off", flush=True)
    call(args.port, tools=True)
    print("PASS: Qwen structured function call", flush=True)
    if not args.quick:
        call(args.port, thinking=True)
        print("PASS: Qwen thinking on", flush=True)
        call(args.port, stream=True)
        print("PASS: Qwen SSE stream", flush=True)

if __name__ == "__main__":
    main()

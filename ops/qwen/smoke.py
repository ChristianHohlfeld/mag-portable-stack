#!/usr/bin/env python3
"""Qwen live acceptance: actual MAG-style auto tools, reasoning and streaming."""
import argparse
import json
import urllib.request

MODEL = "qwen3.8-flash-next-uncensored"
TOOL = {"type": "function", "function": {"name": "echo",
        "description": "Return the supplied string",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False}}}

def request(port, messages, thinking=False, choice=None, stream=False):
    body = {"model": MODEL, "messages": messages, "stream": stream,
            "max_tokens": 512 if thinking else 192, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": thinking},
            "cache_prompt": True}
    if choice:
        body["tools"] = [TOOL]
        body["tool_choice"] = choice
    data = json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port,
              data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as res:
        if not stream:
            answer = json.load(res)
            assert answer.get("choices"), "Missing OpenAI choices"
            return answer["choices"][0]["message"]
        seen = 0
        done = False
        calls = {}
        for raw in res:
            line = raw.decode("utf8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            for item in json.loads(data).get("choices") or []:
                seen += 1
                for t in (item.get("delta") or {}).get("tool_calls") or []:
                    v = calls.setdefault(t.get("index", 0), {"name": "", "arguments": ""})
                    fn = t.get("function") or {}
                    v["name"] += fn.get("name") or ""
                    v["arguments"] += fn.get("arguments") or ""
        assert done and seen, "SSE incomplete"
        return {"tool_calls": [{"function": c} for _, c in sorted(calls.items())]}

def tool(msg):
    calls = msg.get("tool_calls") or []
    assert calls, "Tool call emitted as text or absent"
    fn = calls[0]["function"]
    assert fn.get("name") == "echo", fn
    assert json.loads(fn.get("arguments") or "{}").get("value") == "READY", fn
    return calls[0]

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--port", choices=[8080, 8091], type=int, default=8091)
    a.add_argument("--quick", action="store_true")
    opt = a.parse_args()
    msg = request(opt.port, [{"role": "user", "content": "Reply READY."}])
    assert msg.get("content"), "Normal chat failed"
    print("PASS chat", flush=True)
    prompt = [{"role": "user", "content":
        "Call the echo tool with the value READY. You MUST call the tool and must not reply in prose."}]
    msg = request(opt.port, prompt, choice="auto")
    tc = tool(msg)
    assert tc.get("id"), "Missing tool call ID"
    print("PASS automatic structured tool_calls", flush=True)
    again = request(opt.port, prompt + [
        {"role": "assistant", "content": msg.get("content"),
         "tool_calls": msg["tool_calls"]},
        {"role": "tool", "tool_call_id": tc["id"], "name": "echo", "content": "READY"}])
    assert again.get("content") or again.get("reasoning_content"), "Tool result roundtrip failed"
    print("PASS tool result roundtrip", flush=True)
    if not opt.quick:
        tool(request(opt.port, prompt, choice="required"))
        print("PASS required tool call", flush=True)
        thought = request(opt.port, [{"role": "user", "content":
            "Think carefully how to calculate (13+29)*2, then answer."}], thinking=True)
        assert thought.get("reasoning_content"), "Thinking enabled but no parsed reasoning"
        print("PASS reasoning_content", flush=True)
        tool(request(opt.port, prompt, choice="required", stream=True))
        print("PASS streaming tool calls", flush=True)
    print("QWEN ACCEPTANCE PASS", flush=True)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""NUMA1 live acceptance (Qwen or GLM): MAG-style auto tools, reasoning, streaming."""
import argparse
import json
import urllib.request

MODEL = "qwen3.8-flash-next-uncensored"
TOOL = {"type": "function", "function": {"name": "echo",
        "description": "Return the supplied string",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False}}}

def request(port, messages, thinking=False, choice=None, stream=False):
    glm = request.model == "glm-5.3-flash"
    # GLM-5.3-Flash has no non-thinking mode. Its Jinja template ignores
    # enable_thinking, defaults to Reasoning Effort: Max and can exhaust 192
    # output tokens inside reasoning_content before reaching visible content.
    # The inexpensive GLM option is reasoning_effort=low (not "off").
    template_kwargs = ({"reasoning_effort": "high" if thinking else "low"}
                       if glm else {"enable_thinking": thinking})
    body = {"model": request.model, "messages": messages, "stream": stream,
            "max_tokens": (512 if thinking else 384) if glm else (512 if thinking else 192),
            "temperature": 0, "chat_template_kwargs": template_kwargs,
            "cache_prompt": True, "id_slot": 0}
    if choice:
        body["tools"] = [TOOL]
        body["tool_choice"] = choice
    data = json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port,
              data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as res:
        if not stream:
            answer = json.load(res)
            request.last_response = answer
            assert answer.get("choices"), "Missing OpenAI choices"
            if request.model == "glm-5.3-flash":
                # Synthetic test prompts only. Always expose parsed reasoning
                # vs visible text and finish reason so failures are actionable.
                report(answer, full=request.diagnose_only)
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

request.model = MODEL
request.diagnose_only = False

def report(answer, full=False):
    """Show actual GLM output classification, without dumping user conversations."""
    item = (answer.get("choices") or [{}])[0]
    message = item.get("message") or {}
    cap = 8192 if full else 320
    payload = {
        "finish_reason": item.get("finish_reason"),
        "usage": answer.get("usage"),
        "content": str(message.get("content") or "")[:cap],
        "reasoning_content": str(message.get("reasoning_content") or "")[:cap],
        "tool_calls": message.get("tool_calls"),
        "truncated_preview": any(len(str(message.get(k) or "")) > cap
                                  for k in ("content", "reasoning_content")),
    }
    print("GLM SMOKE RESPONSE " + json.dumps(payload, ensure_ascii=False), flush=True)


def require_visible_content(msg, label):
    if msg.get("content"):
        return
    response = request.last_response
    item = (response.get("choices") or [{}])[0]
    why = ("reasoning exhausted max_tokens" if item.get("finish_reason") == "length"
           and msg.get("reasoning_content") else
           "missing parsed visible answer")
    raise AssertionError(label + ": " + why +
                         " (see GLM SMOKE RESPONSE for actual generated output)")


def tool(msg):
    calls = msg.get("tool_calls") or []
    assert calls, "Tool call emitted as text or absent"
    fn = calls[0]["function"]
    assert fn.get("name") == "echo", fn
    assert json.loads(fn.get("arguments") or "{}").get("value") == "READY", fn
    return calls[0]

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--port", choices=[8080, 8091, 8192], type=int, default=8091)
    a.add_argument("--quick", action="store_true")
    a.add_argument("--model", choices=[MODEL, "glm-5.3-flash"], default=MODEL)
    a.add_argument("--diagnose-only", action="store_true",
                   help="single synthetic chat request, print parsed reasoning/content and exit; no PASS claim")
    opt = a.parse_args()
    request.model = opt.model
    request.diagnose_only = opt.diagnose_only
    msg = request(opt.port, [{"role": "user", "content": "Reply READY."}])
    if opt.diagnose_only:
        print("DIAGNOSE ONLY: no acceptance assertion or model switch", flush=True)
        return
    require_visible_content(msg, "Normal chat failed")
    print("PASS chat", flush=True)
    if opt.port == 8192:
        # Inspect actual server state, then repeat the identical cached request.
        with urllib.request.urlopen("http://127.0.0.1:8192/slots", timeout=10) as res:
            slots = json.load(res)
        n_past = max([int(s.get("n_past", s.get("n_prompt_tokens", 0))) for s in slots] or [0])
        assert n_past > 0, "%s has no warm prompt before request 2" % opt.model
        request(opt.port, [{"role": "user", "content": "Reply READY."}])
        timings = request.last_response.get("timings") or {}
        cached = timings.get("cache_n", request.last_response.get("tokens_cached"))
        if cached is not None:
            assert cached > 0, "%s request 2 did not reuse the prompt cache" % opt.model
        print("PASS warm request 2 n_past=%d" % n_past, flush=True)
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
    require_visible_content(again, "Tool result roundtrip failed")
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
    print("%s ACCEPTANCE PASS" % ("GLM" if opt.model.startswith("glm") else "QWEN"), flush=True)

if __name__ == "__main__":
    main()


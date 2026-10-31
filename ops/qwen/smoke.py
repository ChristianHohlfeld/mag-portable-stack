#!/usr/bin/env python3
"""NUMA1 live acceptance (Qwen or GLM): MAG-style auto tools, reasoning, streaming."""
import argparse
import json
import re
import urllib.request

MODEL = "qwen3.8-flash-next-uncensored"
TOOL = {"type": "function", "function": {"name": "echo",
        "description": "Return the supplied string",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False}}}

def request(port, messages, thinking=False, choice=None, stream=False):
    # Qwen uses its embedded GGUF Jinja template and official non-thinking
    # instruct sampler; GLM's existing smoke contract is left unchanged.
    qwen = request.model == "qwen3.8-flash-next-uncensored"
    kwargs = {"enable_thinking": False if qwen else thinking}
    if qwen:
        kwargs["preserve_thinking"] = False
    elif request.model.startswith("glm-5.3-flash"):
        kwargs.update({"reasoning_effort": "low", "clear_thinking": True})
    body = {"model": request.model, "messages": messages, "stream": stream,
            "max_tokens": 24 if getattr(request, "startup_only", False) else (512 if thinking else 192),
            "temperature": 0.7 if qwen else (1.0 if request.model.startswith("glm-5.3-flash") else 0),
            "top_p": 0.80 if qwen else (0.95 if request.model.startswith("glm-5.3-flash") else 1),
            "chat_template_kwargs": kwargs,
            "cache_prompt": True, "id_slot": 0}
    if qwen:
        body.update({"reasoning_effort": "none", "top_k": 20, "min_p": 0.0,
                     "presence_penalty": 1.5, "repeat_penalty": 1.0})
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
            if request.model.startswith("glm-5.3-flash"):
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



def require_glm_ready(msg, label, roundtrip=False):
    """Require a real readiness/echo marker at the start of a finished answer.

    GLM may append a brief helpful sentence after READY; this is not a failed
    completion. Reject token leaks, false positives and incomplete responses.
    Qwen retains its existing acceptance behaviour.
    """
    content = (msg.get("content") or "").strip()
    marker = r"(?:READY|ECHO:\s*READY)" if roundtrip else r"READY"
    prefix = re.match(marker + r"[.!]?(?=\s|$)", content, re.IGNORECASE)
    # A visible <think> / token delimiter is a failure even if READY came first.
    leaked = re.search(r"</?\s*think\s*>|<\|[^>]+\|>|\[(?:gMASK|sop|eop)\]",
                       content, re.IGNORECASE)
    response = getattr(request, "last_response", {})
    finish = ((response.get("choices") or [{}])[0]).get("finish_reason")
    if (not prefix or len(content) > 400 or leaked
            or msg.get("reasoning_content") or msg.get("tool_calls")
            or finish != "stop"):
        raise AssertionError(label + ": expected READY at start, clean content and "
                             "finish_reason=stop; got " + repr(content[:200]) +
                             ", finish_reason=" + repr(finish) +
                             " (see GLM SMOKE RESPONSE)")



def tool(msg):
    calls = msg.get("tool_calls") or []
    assert calls, "Tool call emitted as text or absent"
    fn = calls[0]["function"]
    assert fn.get("name") == "echo", fn
    assert json.loads(fn.get("arguments") or "{}").get("value") == "READY", fn
    return calls[0]

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--port", choices=[8080, 8091, 8191, 8192], type=int, default=8091)
    a.add_argument("--quick", action="store_true")
    a.add_argument("--model", choices=[MODEL, "glm-5.3-flash", "glm-5.3-flash-iq1-m"], default=MODEL)
    a.add_argument("--startup-only", action="store_true", help="one small synthetic response only")
    a.add_argument("--diagnose-only", action="store_true",
                   help="single synthetic chat request, print parsed reasoning/content and exit; no PASS claim")
    opt = a.parse_args()
    request.model = opt.model
    request.diagnose_only = opt.diagnose_only
    request.startup_only = opt.startup_only
    ready_prompt = [{"role": "user", "content": (
        "Reply with exactly the single word READY and nothing else."
        if opt.model.startswith("glm-5.3-flash") else "Reply READY.")}]
    msg = request(opt.port, ready_prompt)
    if opt.diagnose_only:
        print("DIAGNOSE ONLY: no acceptance assertion or model switch", flush=True)
        return
    require_visible_content(msg, "Normal chat failed")
    if opt.startup_only:
        print("PASS startup %s :%d" % (opt.model,opt.port), flush=True)
        return
    # Thinking is off: the answer must be plain content that ends on its own.
    finish = request.last_response["choices"][0].get("finish_reason")
    if opt.model.startswith("glm-5.3-flash"):
        require_glm_ready(msg, "Normal chat failed")
    else:
        assert "READY" in msg["content"].upper(), "Normal chat failed: no READY in %r" % msg["content"][:200]
    assert finish == "stop", "Normal chat failed: finish_reason=%s" % finish
    t = request.last_response.get("timings") or {}
    print("PASS chat %r (generation %.1f tok/s, prompt %.1f tok/s)" % (
        msg["content"].strip()[:40], t.get("predicted_per_second", 0), t.get("prompt_per_second", 0)), flush=True)
    if opt.port == 8192:
        # Inspect actual server state, then repeat the identical cached request.
        with urllib.request.urlopen("http://127.0.0.1:8192/slots", timeout=10) as res:
            slots = json.load(res)
        n_past = max([int(s.get("n_past", s.get("n_prompt_tokens", 0))) for s in slots] or [0])
        assert n_past > 0, "%s has no warm prompt before request 2" % opt.model
        cached_msg = request(opt.port, ready_prompt)
        if opt.model.startswith("glm-5.3-flash"):
            require_glm_ready(cached_msg, "Cached normal chat failed")
        timings = request.last_response.get("timings") or {}
        cached = timings.get("cache_n", request.last_response.get("tokens_cached"))
        if cached is not None:
            assert cached > 0, "%s request 2 did not reuse the prompt cache" % opt.model
        print("PASS warm request 2 n_past=%d" % n_past, flush=True)
    tool_prompt = (
        "Call the echo tool with the value READY. You MUST call the tool first, "
        "without replying in prose before the result. When the tool returns, "
        "begin your final reply with ECHO: READY. You may add a brief sentence."
        if opt.model.startswith("glm-5.3-flash") else
        "Call the echo tool with the value READY. You MUST call the tool and must not reply in prose.")
    prompt = [{"role": "user", "content": tool_prompt}]
    msg = request(opt.port, prompt, choice="auto")
    tc = tool(msg)
    assert tc.get("id"), "Missing tool call ID"
    print("PASS automatic structured tool_calls", flush=True)
    again = request(opt.port, prompt + [
        {"role": "assistant", "content": msg.get("content"),
         "tool_calls": msg["tool_calls"]},
        {"role": "tool", "tool_call_id": tc["id"], "name": "echo", "content": "READY"}])
    require_visible_content(again, "Tool result roundtrip failed")
    if opt.model.startswith("glm-5.3-flash"):
        require_glm_ready(again, "Tool result roundtrip failed", roundtrip=True)
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


#!/usr/bin/env python3
"""
Go/No-Go-Tests für DeepSeek-V4-Flash-0731 hinter dem Audit-Gateway.
Als chris ausführen (Mitglied von mag-audit-readers):

  python3 /opt/mag-stack/current/ops/deepseek/preflight-tests.py            # alle Tests
  python3 /opt/mag-stack/current/ops/deepseek/preflight-tests.py --quick    # ohne Langtests

Exit 0 = alle Pflichttests grün -> mag-120 darf wieder aktiviert werden.
Auf CPU dauert ein Durchlauf grob 10–30 Minuten (Reasoning ist an).
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

GW = os.environ.get("MAG_LLM_GATEWAY", "http://127.0.0.1:8080")
BACKEND_PORT = 8090
MODEL = os.environ.get("MAG_LLM_MODEL", "deepseek-v4-flash-0731")
LOG_DIR = os.environ.get("MAG_AUDIT_LOG_DIR", "/var/log/mag-audit")
QUICK = "--quick" in sys.argv
TIMEOUT = 3600

results = []
audit_ids = []
chat_ids = []  # nur LLM-Aufrufe (Statusabfragen loggt das Gateway bewusst nicht)


def req(path, body=None, stream=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(GW + path, data=data, method="POST" if data else "GET",
                               headers={"Content-Type": "application/json",
                                        "X-Mag-Bot": "preflight"})
    resp = urllib.request.urlopen(r, timeout=TIMEOUT)
    aid = resp.headers.get("X-Mag-Audit-Id")
    if aid:
        audit_ids.append(aid)
    if stream:
        return resp
    return json.loads(resp.read())


def chat(messages, **kw):
    body = {"model": MODEL, "messages": messages, "max_tokens": kw.pop("max_tokens", 2048)}
    body.update(kw)
    t = time.time()
    n = len(audit_ids)
    r = req("/v1/chat/completions", body)
    chat_ids.extend(audit_ids[n:])
    r["_wall"] = round(time.time() - t, 1)
    return r


def test(name, required=True):
    def deco(fn):
        def run():
            t = time.time()
            try:
                info = fn() or ""
                results.append((name, "OK", required, info))
                print(f"[ OK ] {name} ({time.time() - t:.0f}s) {info}", flush=True)
            except AssertionError as e:
                results.append((name, "FAIL", required, str(e)))
                print(f"[FAIL] {name}: {e}", flush=True)
            except Exception as e:
                results.append((name, "ERROR", required, repr(e)))
                print(f"[ERR ] {name}: {e!r}", flush=True)
        run.__name__ = fn.__name__
        TESTS.append(run)
        return run
    return deco


TESTS = []
WEATHER_TOOL = [{"type": "function", "function": {
    "name": "get_weather", "description": "Aktuelles Wetter für eine Stadt",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string"}, "unit": {"type": "string", "enum": ["c", "f"]}},
        "required": ["city"]}}}]
DS_MARKERS = ("<think>", "</think>", "DSML", "<｜", "|DSML|")


@test("Gateway /v1/models liefert deepseek-v4-flash-0731")
def t_models():
    ids = [m.get("id") for m in req("/v1/models").get("data", [])]
    assert MODEL in ids, f"Modelle: {ids}"
    return str(ids)


@test("Chat-Template ist das DeepSeek-V4-0731-Template (DSML, kein Harmony)")
def t_template():
    p = req("/props")
    tpl = p.get("chat_template", "")
    assert "DSML" in tpl, "kein DSML im Template – falsches/fehlendes Template"
    assert "<|channel|>" not in tpl, "Harmony-Template aktiv!"
    return f"n_ctx={p.get('default_generation_settings', {}).get('n_ctx')} build={p.get('build_info', '?')}"


@test("Einfacher Chat: Antwort in content, Reasoning sauber getrennt")
def t_chat():
    r = chat([{"role": "user", "content": "Antworte nur mit der Zahl: Was ist 17+25?"}])
    m = r["choices"][0]["message"]
    c, rc = m.get("content") or "", m.get("reasoning_content") or ""
    assert "42" in c, f"content={c!r}"
    assert rc.strip(), "reasoning_content leer (Thinking sollte per Default an sein)"
    for mk in DS_MARKERS + ("<|channel|>",):
        assert mk not in c, f"Steuer-Token {mk!r} im content: {c[:200]!r}"
    t = r.get("timings", {})
    return f"{r['_wall']}s, reasoning={len(rc)}Z, pp={t.get('prompt_per_second', 0):.1f} t/s, tg={t.get('predicted_per_second', 0):.2f} t/s"


@test("Thinking abschaltbar (chat_template_kwargs.enable_thinking=false)")
def t_nothink():
    r = chat([{"role": "user", "content": "Sag nur: Hallo"}],
             chat_template_kwargs={"enable_thinking": False}, max_tokens=64)
    m = r["choices"][0]["message"]
    assert not (m.get("reasoning_content") or "").strip(), "reasoning trotz enable_thinking=false"
    assert (m.get("content") or "").strip(), "leere Antwort"
    return f"{r['_wall']}s"


STATE = {}


@test("Tool-Call wird als tool_calls geparst (DSML-Parser)")
def t_tool():
    msgs = [{"role": "system", "content": "Du bist ein Assistent. Nutze Tools, wenn sinnvoll."},
            {"role": "user", "content": "Wie ist das Wetter gerade in Konstanz? Nutze das Tool."}]
    r = chat(msgs, tools=WEATHER_TOOL, tool_choice="auto")
    ch = r["choices"][0]
    m = ch["message"]
    tc = m.get("tool_calls") or []
    assert tc, f"keine tool_calls; finish={ch.get('finish_reason')} content={(m.get('content') or '')[:300]!r}"
    f = tc[0]["function"]
    assert f["name"] == "get_weather", f"name={f['name']}"
    args = json.loads(f["arguments"])
    assert "konstanz" in args.get("city", "").lower(), f"args={args}"
    assert "DSML" not in (m.get("content") or ""), "DSML-Rohtext im content -> Parser greift nicht"
    STATE["msgs"] = msgs + [m]
    STATE["tc_id"] = tc[0].get("id") or "call_0"
    return f"{r['_wall']}s args={args}"


@test("Tool-Ergebnis zurück -> finale Antwort + KV-Cache-Wiederverwendung")
def t_tool_result():
    assert "msgs" in STATE, "vorheriger Test fehlgeschlagen"
    msgs = STATE["msgs"] + [{"role": "tool", "tool_call_id": STATE["tc_id"],
                             "content": json.dumps({"city": "Konstanz", "temp_c": 12, "sky": "bewölkt"})}]
    r = chat(msgs, tools=WEATHER_TOOL)
    m = r["choices"][0]["message"]
    c = m.get("content") or ""
    assert "12" in c, f"Tool-Ergebnis nicht verwendet: {c[:300]!r}"
    t = r.get("timings", {})
    pn, cn = t.get("prompt_n"), t.get("cache_n")
    info = f"{r['_wall']}s prompt_n={pn} cache_n={cn}"
    if pn is not None and cn is not None and cn == 0:
        info += "  WARNUNG: kein Prompt-Cache-Treffer (Reasoning wird neu verarbeitet, s. PR #26398)"
    return info


@test("Streaming mit Tool-Call kommt vollständig durchs Gateway")
def t_stream():
    resp = req("/v1/chat/completions", {
        "model": MODEL, "stream": True, "max_tokens": 2048, "tools": WEATHER_TOOL,
        "messages": [{"role": "user", "content": "Wetter in Zürich? Nutze das Tool."}]}, stream=True)
    chat_ids.append(audit_ids[-1])
    name, args, rc, done = "", "", 0, False
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        d = line[5:].strip()
        if d == "[DONE]":
            done = True
            break
        for ch in json.loads(d).get("choices", []):
            dl = ch.get("delta", {})
            rc += len(dl.get("reasoning_content") or "")
            for tc in dl.get("tool_calls") or []:
                name += (tc.get("function") or {}).get("name") or ""
                args += (tc.get("function") or {}).get("arguments") or ""
    assert done, "kein [DONE]"
    assert name == "get_weather", f"name={name!r}"
    json.loads(args)
    return f"reasoning={rc}Z args={args}"


@test("Längerer Kontext (~8k Tokens) – Prompt-Durchsatz", required=False)
def t_long():
    if QUICK:
        return "übersprungen (--quick)"
    filler = ("Die WOBAK verwaltet Wohnungen in Konstanz. " * 900)
    r = chat([{"role": "user", "content": filler + "\nWie viele Wörter hat der erste Satz? Nur die Zahl."}],
             chat_template_kwargs={"enable_thinking": False}, max_tokens=16)
    t = r.get("timings", {})
    return f"prompt_n={t.get('prompt_n')} pp={t.get('prompt_per_second', 0):.1f} t/s (GPT-OSS alt: ~57 t/s)"


@test("Audit-Log enthält alle Testaufrufe (Request + Response)")
def t_audit():
    time.sleep(1)
    day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    p = os.path.join(LOG_DIR, day, "events.jsonl")
    assert os.access(p, os.R_OK), f"{p} nicht lesbar – Gruppe mag-audit-readers? (neu einloggen)"
    seen = {}
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("id") in chat_ids:
                seen.setdefault(r["id"], set()).add(r["type"])
    missing = [a for a in chat_ids if seen.get(a) != {"llm_request", "llm_response"}]
    assert chat_ids, "keine Chat-Aufrufe gelaufen"
    assert not missing, f"{len(missing)} von {len(chat_ids)} Aufrufen unvollständig protokolliert: {missing[:3]}"
    return f"{len(chat_ids)} Chat-Aufrufe vollständig protokolliert (Request + Response)"


@test("Direktzugriff auf llama serve :8090 ist für normale Nutzer gesperrt")
def t_bypass():
    if os.geteuid() == 0:
        return "als root nicht prüfbar – bitte als chris laufen lassen"
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", BACKEND_PORT))
        s.sendall(b"GET /health HTTP/1.0\r\n\r\n")
        data = s.recv(100)
        assert not data, "Backend direkt erreichbar -> Audit umgehbar (nftables geladen?)"
    except (ConnectionRefusedError, ConnectionResetError, socket.timeout):
        pass
    finally:
        s.close()
    return "gesperrt"


@test("Kein Harmony-Rest in einer Agenten-Antwort (Mehrschritt-Plan)", required=False)
def t_agentic():
    if QUICK:
        return "übersprungen (--quick)"
    r = chat([{"role": "user", "content": "Plane in 3 knappen Schritten, wie du eine Webseite auf "
               "tote Links prüfst. Keine Tools."}], max_tokens=3000)
    c = r["choices"][0]["message"].get("content") or ""
    assert c.strip(), "leere Antwort"
    assert not any(h in c for h in ("<|channel|>", "<|message|>")), "Harmony-Token in Antwort"
    return f"{r['_wall']}s, {len(c)} Zeichen"


def main():
    print(f"Preflight gegen {GW}, Modell {MODEL}\n", flush=True)
    for t in TESTS:
        t()
    req_fail = [r for r in results if r[2] and r[1] != "OK"]
    print("\n" + ("GO – alle Pflichttests grün." if not req_fail
                  else f"NO-GO – {len(req_fail)} Pflichttest(s) fehlgeschlagen."))
    return 0 if not req_fail else 1


if __name__ == "__main__":
    sys.exit(main())

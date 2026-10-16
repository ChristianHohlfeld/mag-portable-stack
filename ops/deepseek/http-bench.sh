#!/usr/bin/env bash
# HTTP-Benchmark gegen den LAUFENDEN mag-llm – kein zweiter Modellprozess, nichts wird neu geladen.
# Geht über das Audit-Gateway (:8080), jede Anfrage ist also protokolliert.
#
#   bash ops/deepseek/http-bench.sh                 Standard: pp 512/2048/4096 + tg 128, je 3 Läufe
#   bash ops/deepseek/http-bench.sh --quick         nur pp 512 + tg 128, 2 Läufe
#   LABEL="t28-tb56-interleave" bash ops/deepseek/http-bench.sh   Label für den A/B-Vergleich
#   SKIP_CACHE=1 …                                                ohne Prompt-Cache-Test (spart ~160 s)
# Ausgabe: Tabelle + /var/tmp/mag-httpbench-<zeit>-<label>.md (Median je Messung)
# Voraussetzung: Raum ruht (wird geprüft). Läuft als normaler Nutzer.
set -euo pipefail
URL=${URL:-http://127.0.0.1:8080/v1/chat/completions}
MODEL=${MODEL:-deepseek-v4-flash-0731}
LABEL=${LABEL:-$(date +%H%M)}
RUNS=3; SIZES="512 2048 4096"
[[ "${1:-}" == "--quick" ]] && { RUNS=2; SIZES="512"; }
OUT=/var/tmp/mag-httpbench-$(date +%Y%m%d-%H%M)-${LABEL}.md

busy=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/state 2>/dev/null \
  | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("busy"))' 2>/dev/null || echo "?")
[[ "$busy" == "True" ]] && { echo "Raum ist beschäftigt – später erneut."; exit 1; }
slots=$(curl -fsS --max-time 3 http://127.0.0.1:8080/slots 2>/dev/null || echo "[]")
python3 -I -c 'import json,sys; s=json.loads(sys.argv[1]); sys.exit(1 if any(x.get("is_processing") for x in s) else 0)' "$slots" \
  || { echo "llama rechnet gerade – später erneut."; exit 1; }

python3 -I - "$URL" "$MODEL" "$RUNS" "$SIZES" "$OUT" "$LABEL" <<'PY'
import json, statistics, sys, time, urllib.request
url, model, runs, sizes, out, label = sys.argv[1], sys.argv[2], int(sys.argv[3]), [int(x) for x in sys.argv[4].split()], sys.argv[5], sys.argv[6]
WORDS = ("Konstanz Bodensee Wohnung Prozess Planung Server Speicher Modell Messung Ergebnis "
         "Mieter Vertrag Heizung Wartung Termin Bericht Anlage Zähler Dach Fenster").split()

def prompt(n_tokens, salt):
    # ~1,3 Tokens pro Wort; Salt am Anfang verhindert Prompt-Cache-Treffer zwischen Läufen
    n_words = int(n_tokens / 1.3)
    body = " ".join(WORDS[(i * 7 + salt) % len(WORDS)] for i in range(n_words))
    return f"Lauf {salt} {time.time_ns()}: {body}\nFasse in einem Satz zusammen."

def call(content, max_tokens):
    req = {"model": model, "messages": [{"role": "user", "content": content}],
           "max_tokens": max_tokens, "temperature": 0, "stream": False,
           "cache_prompt": False, "ignore_eos": True,
           "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(req).encode(),
                               {"Content-Type": "application/json"}), timeout=3600)
    d = json.load(r); wall = time.time() - t0
    t = d.get("timings") or {}
    return {"prompt_n": t.get("prompt_n"), "pp": t.get("prompt_per_second"),
            "gen_n": t.get("predicted_n"), "tg": t.get("predicted_per_second"),
            "ttft": (t.get("prompt_ms") or 0) / 1000, "wall": wall}

rows = []
def bench(name, n, max_tokens):
    res = []
    for i in range(runs):
        r = call(prompt(n, i + 1), max_tokens); res.append(r)
        print(f"  {name} Lauf {i+1}: prompt_n={r['prompt_n']} pp={r['pp']:.2f} t/s  gen_n={r['gen_n']} tg={r['tg']:.2f} t/s  TTFT={r['ttft']:.1f} s", flush=True)
    med = lambda k: statistics.median(x[k] for x in res if x[k] is not None)
    rows.append((name, med("prompt_n"), med("pp"), med("gen_n"), med("tg"), med("ttft"), med("wall")))

print(f"== {label}: Generierung (kurzer Prompt, 128 Tokens)"); bench("tg128", 40, 128)
for n in sizes:
    print(f"== {label}: Prompt {n} Tokens"); bench(f"pp{n}", n, 16)

# Prompt-Cache: gleicher langer Prompt zweimal mit cache_prompt=true -> zweiter Lauf liest nur den Rest
content = prompt(2048, 99)
def cached():
    req = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 8,
           "temperature": 0, "stream": False, "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}}
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(req).encode(), {"Content-Type": "application/json"}), timeout=3600))
    t = d.get("timings") or {}; return t.get("prompt_n"), t.get("cache_n"), (t.get("prompt_ms") or 0) / 1000
import os
if os.environ.get("SKIP_CACHE") == "1":          # A/B-Läufe: Cache-Test kostet ~160 s und hängt nicht vom Build ab
    a = b = (None, None, 0.0)
else:
    a = cached(); b = cached()
    print(f"== Prompt-Cache: 1. Lauf prompt_n={a[0]} cache_n={a[1]} {a[2]:.1f} s  |  2. Lauf prompt_n={b[0]} cache_n={b[1]} {b[2]:.1f} s")

hdr = f"| {label} | prompt_n | pp t/s | gen_n | tg t/s | TTFT s | gesamt s |\n|---|---:|---:|---:|---:|---:|---:|\n"
tab = "".join(f"| {r[0]} | {r[1]:.0f} | {r[2]:.2f} | {r[3]:.0f} | {r[4]:.2f} | {r[5]:.1f} | {r[6]:.1f} |\n" for r in rows)
cache = "" if a[0] is None else f"\nPrompt-Cache (2.048-Token-Prompt doppelt): 1. Lauf {a[2]:.1f} s (cache_n={a[1]}), 2. Lauf {b[2]:.1f} s (cache_n={b[1]})\n"
open(out, "w").write(f"# mag HTTP-Bench {label} {time.strftime('%Y-%m-%d %H:%M')}\n\n" + hdr + tab + cache)
print("\n" + hdr + tab + cache + f"\nGespeichert: {out}")
PY

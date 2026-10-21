#!/usr/bin/env python3
"""Summarize observed MAG prompt prefill/cache by NUMA model (no prompt data).

Usage: journalctl -u mag-120.service --since '-3 hours' -o cat | python3 ops/perf/context-report.py
No network calls, LLM requests, model restart, or filesystem writes.
"""
import re
import statistics
import sys

LINE = re.compile(
    r"prompt-cache (?:model=(?P<model>[^ ]+) )?slot=(?P<slot>\d+) "
    r"tokens_cached=(?P<cached>-?\d+) tokens_evaluated=(?P<total>-?\d+)"
    r"(?: prompt_tps=(?P<tps>-?[\d.]+))?"
)
stats = {}
for line in sys.stdin:
    match = LINE.search(line)
    if not match:
        continue
    d = match.groupdict()
    name = d["model"] or "legacy-unknown"
    cached, total = int(d["cached"]), int(d["total"])
    tps = float(d["tps"]) if d["tps"] is not None else -1
    row = stats.setdefault(name, {"samples": 0, "ratios": [], "prefill": [],
                                  "cache_unknown": 0, "misses": 0, "tokens": []})
    row["samples"] += 1
    if cached >= 0 and total > 0 and cached <= total:
        row["ratios"].append(cached / total)
        row["tokens"].append(max(0, total-cached))
        if cached == 0:
            row["misses"] += 1
    else:
        row["cache_unknown"] += 1
    if tps > 0:
        row["prefill"].append(tps)

if not stats:
    print("Keine Prompt-Messwerte gefunden. Nach echten MAG-Anfragen erneut prüfen.")
    sys.exit(0)

for model, r in sorted(stats.items()):
    print(model, f"({r['samples']} Anfragen)")
    if r["ratios"]:
        median = statistics.median(r["ratios"]) * 100
        print(f"  Cache-Treffer median: {median:.1f}% | volle Misses: {r['misses']}/{len(r['ratios'])}")
        print(f"  Neu evaluierte Tokens median: {statistics.median(r['tokens']):.0f}")
    else:
        print("  Cache-Hitrate: nicht vom Modellserver gemeldet")
    if r["prefill"]:
        print(f"  Prompt-Speed median: {statistics.median(r['prefill']):.2f} Tokens/s")
    else:
        print("  Prompt-Speed: nicht gemeldet")
    if r["cache_unknown"]:
        print(f"  Fehlende Cache-Telemetrie: {r['cache_unknown']} Anfrage(n)")
    print()
print("Hinweis: Erstes Laden, neuer Bot/VM oder geänderte Tool-Schemas erzeugen zunächst einen Cache-Miss.")

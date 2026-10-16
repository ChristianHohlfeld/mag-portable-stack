#!/usr/bin/env bash
# Erzeugt die mag-llm-Unit für ik_llama.cpp (ab 3.8) aus der normalen Vorlage ops/mag-llm.service:
#  - alles außerhalb von ExecStart bleibt wörtlich (Gate, User, Speicher-/OOM-Schutz, post-load, Restart …)
#  - ExecStart wird durch den ik-Aufruf ersetzt; Modell, Alias, Kontext, Template, Sampling und Cache-RAM
#    werden aus der Vorlage übernommen (eine Quelle der Wahrheit)
#  - mag-Varianten über Environment= (MAG_NUMA_LOCAL, MAG_THP, MAG_NUMA_ALIGN – siehe ik-patches/0002)
#  - jedes Flag wird gegen `llama-server --help` des Binarys geprüft; bei Unbekanntem Abbruch, nichts geschrieben
#
#   render-ik-unit.sh TEMPLATE ZIEL|--check
# Umgebung (setzt llm-config.sh): IK_BIN THREADS THREADS_BATCH BATCH UBATCH NUMA_ARGS LLAMA_NUMA
#   IK_RTR=1|0 (Gewichte für AVX2 umsortieren: Q8_0->Q8_0_R8, MXFP4->MXFP4_R8; 0 = mmap, eine Kopie)
#   IK_ARGS="..." zusätzliche Flags (z.B. -muge)   IK_ENV="MAG_NUMA_LOCAL=16 MAG_THP=1"
#   HELP_FILE=<datei> Hilfetext statt `llama-server --help` (Tests)
set -euo pipefail
TPL=${1:?Vorlage fehlt}; DST=${2:?Ziel oder --check fehlt}
: "${IK_BIN:?IK_BIN fehlt}"
[[ "$IK_BIN" =~ ^/[A-Za-z0-9._/+-]+$ ]] || { echo "render-ik: IK_BIN '$IK_BIN' ist kein gültiger absoluter Pfad" >&2; exit 4; }
[[ -x "$IK_BIN" || -n "${HELP_FILE:-}" ]] || { echo "render-ik: IK_BIN '$IK_BIN' ist nicht ausführbar" >&2; exit 4; }
for v in THREADS THREADS_BATCH BATCH UBATCH; do
  [[ "${!v:-}" =~ ^[0-9]+$ ]] || { echo "render-ik: $v='${!v:-}' ungültig" >&2; exit 4; }
done
NUMA_ARGS=${NUMA_ARGS:---interleave=all}; LLAMA_NUMA=${LLAMA_NUMA:-distribute}; IK_RTR=${IK_RTR:-1}
IK_ARGS=${IK_ARGS:-}; IK_ENV=${IK_ENV:-}
export IK_BIN THREADS THREADS_BATCH BATCH UBATCH NUMA_ARGS LLAMA_NUMA IK_RTR IK_ARGS IK_ENV
[[ "$NUMA_ARGS" == --interleave=all || "$NUMA_ARGS" =~ ^--cpunodebind=[0-9]+([[:space:]]+--(preferred|membind)=[0-9]+)?$ ]] \
  || { echo "render-ik: NUMA_ARGS '$NUMA_ARGS' ungültig" >&2; exit 4; }
[[ "$LLAMA_NUMA" =~ ^(distribute|isolate|numactl)$ ]] || { echo "render-ik: LLAMA_NUMA '$LLAMA_NUMA' ungültig" >&2; exit 4; }
[[ "$IK_RTR" =~ ^[01]$ ]] || { echo "render-ik: IK_RTR '$IK_RTR' ungültig" >&2; exit 4; }
[[ -z "$IK_ARGS" || "$IK_ARGS" =~ ^-{1,2}[a-z0-9-]+([[:space:]]+-{0,2}[A-Za-z0-9._+/:=-]+)*$ ]] \
  || { echo "render-ik: IK_ARGS '$IK_ARGS' ungültig" >&2; exit 4; }
for kv in $IK_ENV; do
  [[ "$kv" =~ ^(MAG_NUMA_LOCAL=[0-9]{1,3}|MAG_THP=[01]|MAG_NUMA_ALIGN=[0-9]{4,9})$ ]] \
    || { echo "render-ik: IK_ENV-Eintrag '$kv' ungültig" >&2; exit 4; }
done
if [[ -n "${HELP_FILE:-}" ]]; then help=$(cat "$HELP_FILE")
elif [[ $EUID -eq 0 ]]; then help=$(runuser -u chris -- "$IK_BIN" --help 2>&1 || true)
else help=$("$IK_BIN" --help 2>&1 || true); fi
[[ -n "$help" ]] || { echo "render-ik: keine Ausgabe von '$IK_BIN --help'" >&2; exit 2; }

unit=$(python3 -I - "$TPL" "$help" <<'PY'
import os, re, sys
tpl, help_text = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
E = os.environ
lines = tpl.split("\n")
# ExecStart-Block finden (bis zur ersten Zeile ohne abschließenden Backslash)
try:
    i0 = next(i for i, l in enumerate(lines) if l.startswith("ExecStart="))
except StopIteration:
    sys.exit("render-ik: kein ExecStart= in der Vorlage")
i1 = i0
while lines[i1].rstrip().endswith("\\"):
    i1 += 1
block = " ".join(l.rstrip().rstrip("\\") for l in lines[i0:i1 + 1])
def take(flag, required=True):
    m = re.search(r"(?:^|\s)" + re.escape(flag) + r"\s+(\S+)", block)
    if not m and required:
        sys.exit(f"render-ik: {flag} fehlt in der Vorlage")
    return m.group(1) if m else None
keep = [("--model", True), ("--alias", True), ("--ctx-size", True), ("--cache-ram", False),
        ("--chat-template-file", True), ("--reasoning-format", False), ("--temp", False), ("--top-p", False), ("--min-p", False)]
vals = {f: take(f, r) for f, r in keep}
args = [
    ("--model", vals["--model"]), ("--alias", vals["--alias"]), ("--host", "127.0.0.1"), ("--port", "8090"),
    ("--ctx-size", vals["--ctx-size"]),
    ("--parallel", "1"),                        # V4: Prompt-Cache nur mit einem Slot zuverlässig (#25259)
    ("--threads", E["THREADS"]), ("--threads-batch", E["THREADS_BATCH"]),
    ("--numa", E["LLAMA_NUMA"]),
    ("--batch-size", E["BATCH"]), ("--ubatch-size", E["UBATCH"]),
    ("--mlock", None),
    ("--flash-attn", "on"),
    ("--cache-type-k", "f16"), ("--cache-type-v", "f16"),   # quantisierter K-Cache ist bei V4 kaputt (#25382)
]
if vals["--cache-ram"]: args.append(("--cache-ram", vals["--cache-ram"]))
args += [("--jinja", None), ("--chat-template-file", vals["--chat-template-file"]),
         ("--reasoning-format", vals["--reasoning-format"] or "deepseek")]
for f in ("--temp", "--top-p", "--min-p"):
    if vals[f]: args.append((f, vals[f]))
args += [("--metrics", None), ("--webui", "none")]
if E.get("IK_RTR", "1") == "1": args.append(("-rtr", None))
extra = E.get("IK_ARGS", "").split()
def has_flag(f): return re.search(r"(^|[\s,])" + re.escape(f) + r"([\s,=]|$)", help_text, re.M) is not None
missing = [f for f, _ in args if not has_flag(f)] + [x for x in extra if x.startswith("-") and not has_flag(x)]
if missing:
    sys.exit("render-ik: unbekannte Flags für dieses Binary: " + " ".join(missing))
numa = E.get("NUMA_ARGS", "--interleave=all")
out = [f"ExecStart=/usr/bin/numactl {numa} {E['IK_BIN']} \\"]
parts = [f"  {f}" + (f" {v}" if v is not None else "") for f, v in args]
if extra:
    parts.append("  " + " ".join(extra))
out += [p + " \\" for p in parts[:-1]] + [parts[-1]]
envs = [f"Environment={kv}" for kv in E.get("IK_ENV", "").split()]
new = lines[:i0] + out + lines[i1 + 1:]
# Environment-Zeilen direkt nach [Service]
j = next((i for i, l in enumerate(new) if l.strip() == "[Service]"), None)
if j is None:
    sys.exit("render-ik: kein [Service] in der Vorlage")
marker = "# ik_llama.cpp (ab 3.8, erzeugt von ops/deepseek/render-ik-unit.sh) – mag-Varianten:"
new = new[:j + 1] + ([marker] + envs if envs else []) + new[j + 1:]
new = [l.replace("Description=MAG Local LLM - DeepSeek-V4-Flash-0731 via llama.cpp (CPU)",
                 "Description=MAG Local LLM - DeepSeek-V4-Flash-0731 via ik_llama.cpp (CPU)") for l in new]
print("\n".join(new).rstrip("\n"))
PY
)
grep -q '@LOAD_ARGS@' <<<"$(grep -v '^#' <<<"$unit")" && { echo "render-ik: Platzhalter übrig" >&2; exit 4; }
grep -qF -- "ExecStart=/usr/bin/numactl $NUMA_ARGS $IK_BIN \\" <<<"$unit" || { echo "render-ik: ExecStart nicht erzeugt" >&2; exit 4; }
echo "render-ik: $IK_BIN, threads $THREADS/$THREADS_BATCH, batch $BATCH/$UBATCH, rtr=$IK_RTR${IK_ARGS:+, $IK_ARGS}${IK_ENV:+, $IK_ENV}" >&2
if [[ "$DST" == --check ]]; then exit 0; fi
tmp=$(mktemp "$(dirname "$DST")/.mag-llm.XXXXXX")
printf '%s\n' "$unit" > "$tmp"; chmod 0644 "$tmp"; mv -f "$tmp" "$DST"

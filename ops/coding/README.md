# MAG coding room: Laguna S 2.1 (NUMA0) + Flint (NUMA1)

Owner decision 2026-11-02 22:18 ("Zwei Top-Modelle, die schnell sind, auf dem
System gut funktionieren und intelligent genug, um langen Code zu schreiben,
ohne dabei abzubrechen ... nicht so Qwen-Code"), replacing the interim
Qwen3.8-Flash-Next + Qwen3-Coder-Next pair (#263-#267). Old GGUFs are kept; a
failed swap restores the previous pair from the transaction snapshot.

| NUMA | Port | Bot | GGUF | Size | SHA256 |
|---|---|---|---|---|---|
| 0 | 8191 | Laguna S 2.1 (118B, 8B active, `laguna`) | [ggml-org/Laguna-S-2.1-GGUF](https://huggingface.co/ggml-org/Laguna-S-2.1-GGUF) `Q4_K_M` | 67.7 GB | `2bf879f8…296a` |
| 1 | 8192 | Flint (39.8B, 2.5B active, `flint`) | [ggml-org/Flint-GGUF](https://huggingface.co/ggml-org/Flint-GGUF) `Q8_0` | 42.4 GB | `8a257b63…4f93` |

Why these two (vendor model cards; CPU-only host, ~5 tok/s floor per node, so
only low-active MoE models qualify):
* **Laguna S 2.1** ([card](https://huggingface.co/poolside/Laguna-S-2.1)):
  Terminal-Bench 2.1 70.2, SWE-bench Multilingual 78.5, SWE-Bench Pro 59.4 -
  ahead of DeepSeek-V4-Pro Max (1.6T-A49B) and level with Qwen 3.7 Max on the
  same table, at 8B active. Built for long-horizon agentic coding.
* **Flint** ([card](https://huggingface.co/RecursiveSI/flint)): SWE-bench
  Verified 74.2, Terminal-Bench 2.1 54.9, LiveCodeBench v6 80.3 at 2.5B active;
  the fastest strong non-Qwen coder, kept at Q8_0 because small active
  parameter counts lose the most under 4-bit quantization.

Runtime: pinned upstream llama.cpp release b12058 (`ops/coding/runtime.sha`,
built by `ops/coding/build-runtime.sh` into `~/.local/mag-coding-rt/<sha>`),
which contains `laguna` (ggml-org/llama.cpp#25165) and `flint`
(ggml-org/llama.cpp#30928), plus the existing MAG end-checkpoint patch. The
Qwen/GLM runtime of `ops/qwen/runtime.sha` is untouched (rollback keeps it).
Server flags follow the llama.cpp
[server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md):
`--jinja` (GGUF chat template), `--reasoning-format auto`, 64k context,
one slot, 14 physical cores per node via `numactl --physcpubind/--membind`.

Deviations from the model cards (with sources):
* Laguna runs non-thinking in the room (`enable_thinking=false`, supported by
  its [chat template](https://huggingface.co/poolside/Laguna-S-2.1-GGUF/blob/main/chat_template.jinja));
  the card recommends thinking for agentic tool loops, but on this CPU host
  thinking would delay every answer. Flint's template has no switch and always
  reasons; its trace stays in `reasoning_content`.
* Poolside's card points to its own llama.cpp fork for DFlash speculative
  decoding; base `laguna` support is upstream (#25165), and we use the
  official ggml-org GGUF conversion (67.7 GB instead of poolside's 96 GB file,
  [quantized.uk](https://quantized.uk/quant-hub/laguna-s-2-1-118b-a8b/)).

Room policy (`mag-room/native/magroom.cpp`, `coding_room_model`):
* every bot has its VM, browser and full tools (#269), one answer per bot and turn;
* sampling per model card: Laguna `temperature=1.0, top_p=1.0, top_k=20,
  min_p=0` ([generation_config](https://huggingface.co/poolside/Laguna-S-2.1/blob/main/generation_config.json));
  Flint `temperature=1.0, top_p=0.95, top_k=20`
  ([card](https://huggingface.co/RecursiveSI/flint));
* no total output cap and no wall-clock deadline: each round may emit
  `MAG_ROOM_MAX_TOKENS=16384`, a `finish_reason=length` round continues the same
  assistant prefix (#253) until the model stops; only Stop, the 10-minute
  no-byte stall, the context end and the loop guard end an answer.

## Switching the pair

`ops/coding/layout.sh` detects a changed `models.json` against the installed
`/usr/local/lib/mag-coding/models.json`. Only when both new GGUFs are
SHA-verified does it stop/start `mag-llm-coding@0` then `@1`, checking
`/health` and a real decode (`live-smoke.py`) on each; any failure restores
the previous files and restarts the previous pair.

## Stage models, preserving every currently running service

As user `chris`, from the checkout:

```bash
python3 -I ops/coding/stage.py --model both --download
python3 -I ops/coding/stage.py --model both
python3 -m unittest discover -s ops/coding/tests -p 'test_*.py'
```

Downloads about 110 GB from the two documented Hugging Face sources.
The exact GGUF SHA256s from the upstream file pages are pinned in
`models.json`. File hash mismatches abort and leave originals untouched.
A download may still compete for disk I/O/page cache. No unit is changed,
no previous model weight is deleted, and no deployment is run.

## Localhost pilot, after deliberately making room on the relevant NUMA node

`run-candidate.sh` does **not** stop a production model. It refuses if
the existing model service is active on the target node.
Provide a **separately verified, pinned** `llama-server` executable supporting
both `qwen35moe` and `qwen3moe`, with flags checked against its `--help`.
Do not reuse an old binary without checking the architecture and sampling
template. The candidate is bound to the node's 14 *physical* cores and
dedicated localhost ports (8291, 8292); it cannot accidentally claim
8191/8192. Keep candidate slots independent.

```bash
MAG_CODING_LLAMA_BIN=/path/to/verified/llama-server bash ops/coding/run-candidate.sh general
# In a second session, once /health is ready:
python3 -I ops/coding/smoke.py general --max-tokens 16384

MAG_CODING_LLAMA_BIN=/path/to/verified/llama-server bash ops/coding/run-candidate.sh coder
python3 -I ops/coding/smoke.py coder --max-tokens 16384
```

Record TTFT, wall time, actual throughput from server timing logs,
`finish_reason` and whether emitted code passes tests. Smoke aborts for
empty, short or length-truncated text. Neither low prompt size nor a 16k
requested maximum guarantees code quality or speed.

## Automatischer Live-Deploy (ab PR #257)

Der bereits vorhandene MAG-Auto-Updater zieht **nur den GitHub-Branch `green`**
nach vollständiger CI-Freigabe und ruft `ops/auto-update/apply.sh` auf.

Die Repo-Vorgabe `ops/room/layout=coding` wählt diesen Ablauf:

1. `ops/coding/desired.sh` respektiert eine explizite Operator-Pinnung. Eine
   bestehende `/etc/mag-stack/room-layout.pinned` wird **nicht** überschrieben.
2. `ops/coding/prepare.sh` lädt beide GGUF-Dateien resumierbar und prüft
   die gepinnten SHA256-Hashes. Ein dedizierter llama.cpp CPU-Build mit
   verifiziertem `qwen35moe`- und `qwen3moe`-Loader entsteht unter
   `/home/chris/.local/mag-coding/`. Die bisherigen Modell-Units und Gewichte
   bleiben währenddessen unverändert.
3. `ops/coding/layout.sh` ist eine transaktionale, root-eigene Umstellung.
   Nur aus einem aktiven `glm`-Layout und **nur**, wenn beide alten Bots gesund
   und neue Modelle vollständig vorbereitet sind. Es zeichnet aktive/enabled
   Units und frühere Konfigurationen in `$BACKUP/qwen-layout` auf.
4. Nach Drain stoppt es die beiden alten Units, wartet auf freie Ports 8191
   und 8192, startet die beiden neuen NUMA-gebundenen Units, prüft deren
   `/health` **und** führt mit jedem echten GGUF einen Python-Coding-Smoke
   aus. Erst danach wird `coding.env` aktiviert, der Audit-Gateway neu geladen,
   und der native Raum erhält **16.384 Ausgabetokens** pro normalem Coding-Turn.
5. Bricht der Switch oder ein späterer Deploy-Schritt ab, stellt der
   gesicherte Helper die alten Units und Konfigurationen wieder her. Dabei
   muss GLM-Q2 vollständig bereit sein, **bevor** Mistral startet. Eine
   fehlgeschlagene Identität wird für automatische Wiederholungen gesperrt.
   Alte GGUF-Dateien werden nie gelöscht.

Nach erfolgreichem Wechsel übernimmt `ops/boot/ensure-boot.sh` beide
Coding-Units für den nächsten Boot, ohne sie unnötig neu zu starten. Auf einem
bereits gesunden Coding-Layout ist `layout.sh` idempotent; Drift an
modellrelevanten Startparametern führt zum Fehler statt zum stillen Neustart.

### Grenzen der Verifikation

Die CI führt isolierte Regressionstests aus. Echte CPU-Geschwindigkeit und
tatsächlicher Live-Status werden ausschließlich im MAG-Host-Test sichtbar.
Ein Merge allein bestätigt **weder** fertige Downloads **noch** einen laufenden
Modellwechsel. Prüfen auf MAG:
```bash
systemctl status mag-auto-update.service mag-llm-coding@0.service mag-llm-coding@1.service --no-pager
cat /etc/mag-stack/room-layout
curl -fsS http://127.0.0.1:8080/v1/models
```

Bei vorhandener Operator-Pinnung auf ein anderes Layout greift weiterhin
diese Pinnung; `coding` wird nicht erzwungen.

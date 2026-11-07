# MAG coding room: Lena Qwen3.8-27B Q6_K + Noah Ornith Q4_K_M

**Live-Konfiguration (2026-11-07):** NUMA0/:8191: `Qwen3.8-27B-Q6_K.gguf`
(bartowski, SHA256 539041d2…c8d69a9db3), MI50 Vulkan, full GPU
(`--device Vulkan0 --n-gpu-layers 99 --no-host`), 65.536 Kontext,
q8_0 K/V, ubatch 1024, GGUF-eigenes Jinja-Template, Slot `general/qwen27b`.
Alias `ornith-numa0` bleibt für das Room-Routing erhalten. NUMA1/:8192:
Ornith-1.5-35B-A3B Q4_K_M auf CPU unverändert. Start über
`mag-llm-coding@0` / `ops/coding/start-live.sh 0`; kein stiller CPU-Fallback.
Das Manifest prüft die komplette GGUF-SHA256 vor Aktivierung; eine noch
laufende Dateiübertragung verhindert den Neustart.

Qwen3.8-Sampling im Room: Thinking 1.0/0.95/top-k20/presence0,
Non-Thinking 0.7/0.80/top-k20/presence1.5. Reasoning-Effort low;
der Room setzt Thinking pro Nachricht. Noah behält Ornith-Sampling.

## Ältere Layout- und Migrationsdokumentation (historisch)


Owner decision 2026-11-02 ("Die besten, ihr entscheidet"), replacing the slow
Mistral Large 4 (~1 tok/s) / GLM-Q2 (~3 tok/s) room pair. Old GGUFs are kept;
`ops/coding/layout.sh rollback` restores the `glm` layout.

| NUMA | Port | Bot | GGUF | Size | SHA256 |
|---|---|---|---|---|---|
| 0 | 8191 | Qwen3.8-Flash-Next (125B, 6B active, `qwen4exp`) | [mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF](https://huggingface.co/mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF) `IQ4_XS` (already on mag, hard-linked, no download) | 98.4 GB | `a1975fd7…dedf` |
| 1 | 8192 | Flint (`flint` arch, always reasons) | [ggml-org/Flint-GGUF](https://huggingface.co/ggml-org/Flint-GGUF) `Q4_K_M` | 23.4 GB | `c3c4d93f…a347` |

NUMA1 switched from Qwen3-Coder-Next to Flint on 2026-11-03 (owner: "Flint
installieren"). Later the same day NUMA1 moved from Flint `Q8_0` to the
same publisher's `Q4_K_M` (owner, 2026-11-03: Q8 decode ~8.6 tok/s is bound by
the single socket's memory bandwidth; Q4_K_M reads about half the bytes per
token; spanning both sockets is rejected because the NUMA interconnect is the
bottleneck). `Flint-Q8_0.gguf` (`8a257b63…4f93`) stays on disk. The Qwen3-Coder-Next GGUF stays on disk; a failed Flint
cutover rolls back to it (`layout.sh` restores the installed `models.json`,
`start-live.sh` and runtime pins, then restarts the previous units).
A models.json change on a live coding layout restarts **only** the NUMA node
whose entry changed, so NUMA0 Qwen3.8 stays resident.

Runtimes, one pin per node:
* NUMA0: the pinned llama.cpp build of `ops/qwen/runtime.sha`
  (`ops/qwen/build-runtime.sh`, `qwen4exp`, llama.cpp PR #27742).
* NUMA1: upstream llama.cpp release b12058 of `ops/coding/runtime.sha`
  (`ops/coding/build-runtime.sh`, `flint` arch from ggml-org/llama.cpp#30928),
  with the same `ops/qwen/end-checkpoint.patch`; installed as
  `/usr/local/lib/mag-coding/runtime-coder.sha`.

Prefix cache: both servers run `--cache-ram`, context checkpoints and
`--slot-save-path`; the room prewarms the ~12.5k-token tools prefix and
saves/restores slot 0 (`MAG_ROOM_PREWARM=1`, `MAG_ROOM_PREFIX_DISK=1`, server
README `/slots/{id}?action=save|restore`). Server flags follow the llama.cpp
[server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md):
`--jinja` (GGUF chat template), `--reasoning-format auto`, 64k context,
one slot, 14 physical cores per node via `numactl --physcpubind/--membind`.

Room policy (`mag-room/native/magroom.cpp`, `coding_room_model`):
* VM, browser and full tool set for every bot (#269), exactly one answer per bot and turn;
* sampling per model card: Qwen3.8 non-thinking `temperature=0.7, top_p=0.8,
  top_k=20, presence_penalty=1.5`; thinking (room toggle only) `1.0/0.95/20`
  with `reasoning_effort=low`
  ([card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next)); Flint
  `temperature=1.0, top_p=0.95, top_k=20`
  ([card](https://huggingface.co/RecursiveSI/flint)); Flint's template has no
  thinking switch, so its reasoning is always produced and kept in
  `reasoning_content` (`--reasoning-format auto`);
* no total output cap and no wall-clock deadline: each round may emit
  `MAG_ROOM_MAX_TOKENS=16384`, a `finish_reason=length` round continues the same
  assistant prefix (#253) until the model stops; only Stop, the 10-minute
  no-byte stall, the context end and the loop guard end an answer.

## Stage models, preserving every currently running service

As user `chris`, from the checkout:

```bash
python3 -I ops/coding/stage.py --model both --download
python3 -I ops/coding/stage.py --model both
python3 -m unittest discover -s ops/coding/tests -p 'test_*.py'
```

Downloads about 45 GB from the two documented Hugging Face sources.
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

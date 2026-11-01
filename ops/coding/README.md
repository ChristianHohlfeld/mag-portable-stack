# MAG: faster CPU coding candidates (stage + measure, **no production cutover**)

Goal: replace the CPU-heavy Mistral Large 4 / GLM-Q2 room pair with
* **NUMA0** Qwen3.6-35B-A3B, Unsloth UD-Q5_K_M (26.5 GB), strong project/coding generalist.
* **NUMA1** Qwen3-Coder-30B-A3B-Instruct, LM Studio Community Q4_K_M (18.6 GB), coding-only non-thinking.

Both are sparse ~3B-active MoEs with much smaller resident weight footprints.
The choice favors throughput, sufficient code quality and a coding budget
of **16,384 output tokens per turn** (not just 256/1,024/8,192).
This is an *intended room budget*, not yet enabled: production
`mag-120.service` currently has `MAG_ROOM_MAX_TOKENS=8192`.
GGUFs fit the host RAM, but **no MAG Broadwell CPU tok/s measurement exists
yet**. Do not confuse GPU/Mac performance figures with this host.

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

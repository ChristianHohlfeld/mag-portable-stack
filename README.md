# MAG

Ein privater Chatraum auf einem Heimserver: Chris (Handy und Desktop) und zwei
lokale KI-Assistenten, Lena und Noah. Alles läuft auf einer Maschine, nichts
verlässt das Haus.

| | |
| --- | --- |
| Raum | `magroom` (C++17): Chat, Ereignis-Stream, Gesprächs-Engine, Werkzeuge |
| Lena | Gemma 4 26B-A4B, llama.cpp, nur CPU, NUMA0, Port 8191 |
| Noah | gpt-oss 120B, llama.cpp, nur CPU, NUMA1, Port 8192 |
| Foto zu 3D | MAG Idol auf der MI50 (72 W), Ergebnis als normale Chat-Nachricht; Jobs überstehen Neustarts |
| Bot-Rechner | je Bot eine eigene KVM-VM mit Shell und echtem Chrome; im Bot-Panel Browser, Terminal und Dateien |
| Bot-Seiten | `<name>.bot.stardenburg.de` (nur Tailnet); HTML der Bots läuft von dort als Vorschau im Chat |
| Bilder | Lena sieht sie selbst (Vision-Projektor, CPU), Noah liest ihre Beschreibung |
| Zugang | Tailscale, nginx mit whois-Prüfung, nur der Besitzer |

Aufbau und Abläufe: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Regeln für Änderungen: [`AGENTS.md`](AGENTS.md).

## Bauen und prüfen

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
taskset -c 28-55 ninja -C build
bash ops/ci/check.sh                       # statische Verträge
python3 ops/models/tests/test_modeld.py    # Modell-Aufsicht
python3 ops/idol/tests/test_worker.py      # 3D-Jobs überstehen Neustarts
python3 mag-room/tests/test_native.py      # Raum mit Fake-Modellen
```

## Ausrollen

```bash
cd ~/mag-stack && git checkout -q main && git pull -q --ff-only origin main \
  && taskset -c 28-55 sudo -n bash ops/deploy.sh
```

Ein Deploy baut, legt ein Release unter `/opt/mag-stack/releases` an, schaltet
um, prüft `/readyz` und nginx und rollt bei einem Fehler zurück. Er wartet nie
auf ein Modell oder eine laufende Antwort. Zurück zur vorigen Version:
`sudo bash ops/rollback.sh <RELEASE_ID>`.

## Modelle

`ops/models/models.json` beschreibt beide Bots. `mag-modeld` prüft jede GGUF
gegen die Größe bei Hugging Face und einmal per SHA256, startet den Server,
sobald die Datei vollständig ist, und schreibt den Zustand nach
`/run/mag-models/status.json` (in der Oberfläche: „lädt herunter“, „wird
geprüft“, „lädt ins RAM“, „bereit“).

```bash
mag-modeld status                 # Zustand beider Bots
sudo mag-modeld ctl lena restart  # start | stop | restart | freeze | thaw
```

Modell-Dateien werden nie gelöscht.

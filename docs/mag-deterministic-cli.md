# MAG Deterministic CLI (C++20)

Standalone-Werkzeug im Repository-Root. **Es ist keine Antwort-Cache-Schicht und ersetzt nicht den laufenden MAG-C++-Dienst.** Für `--infer` wird jede Ausgabe neu vom konfigurierten Modellserver berechnet.

## Kompilieren

```bash
g++ -std=c++20 -O3 -Wall -Wextra -pthread mag_deterministic_engine.cpp -o mag_det
./mag_det --selftest
python3 test_mag_deterministic_engine.py
```

Der Selftest benutzt eine **synthetische lokale Matrix-Transportklasse**. Er überprüft Stateless-Payload, 20/20-Vergleich, Fail-Fast-Abbruch in Lauf 4, SHA-256-Testvektor und Fingerprint-Sperre. Er beweist nichts über reale Qwen-/DeepSeek-Gewichte.

## Reales Modell prüfen

```bash
./mag_det --verify --host 127.0.0.1 --port 8080 --model qwen2.5-72b --numa 0,1,2,3,4,5,6,7
./mag_det --infer "hi" --host 127.0.0.1 --port 8080 --model qwen2.5-72b --numa 0,1,2,3,4,5,6,7
```

`--verify`: 20 **neue sequenzielle HTTP-Inferenzanfragen** mit identischer Stateless-Nachricht und Parametern; echte generierte Token-IDs **und** Antworttext werden verglichen. Erste Abweichung führt sofort zum Abbruch (Exit-Code != 0). Ein erfolgreiches Ergebnis gilt nur für diese getestete Eingabe und Laufzeitkonfiguration, nicht universell für sämtliche möglichen Prompts.

`--infer "hi"`: prüft **den aktuellen Prompt** 20-mal mit maximal 32 Tokens und führt danach einen **21.** unabhängigen Lauf aus. Dieser muss ebenfalls exakt übereinstimmen, sonst Exit-Code != 0. Kein gespeicherter Antworttext wird als Antwort ausgegeben.

Stateless JSON: eine einzige `user`-Nachricht mit der Roh-Eingabe. `temperature=0`, `top_k=1`, `seed=42`, `cache_prompt=false`, kein Tool-Kontext und kein Verlauf.

**Backend-Voraussetzungen:** Der lokale Endpunkt muss `/v1/chat/completions` verstehen und **echte generierte Token-IDs** als `tokens` oder `token_ids` im JSON zurückgeben (die Requests setzen `return_tokens` und `return_token_ids`). Fehlen sie, verweigert der Verifikator die Freigabe; weder Textbytes noch Logprob-Strings werden als Token-IDs ausgegeben. Diese Zusatzoptionen werden nicht von jedem llama.cpp-/vLLM-Build auf dem OpenAI-kompatiblen Endpunkt unterstützt.

**Wichtige Genauigkeitsgrenzen:** 20 HTTP-Anfragen gegen einen bereits laufenden Server sind **keine 20 Gewichts-/Prozess-Kaltstarts**. `--numa` setzt nur die **CPU-Affinity dieses CLI-Prozesses**, nicht die des bereits laufenden Inferenzservers. NUMA-Zuordnung, Threadzahl, Batching, Gewichte und Version des Modellservers müssen separat dort festgelegt und überprüft werden. Der Fingerprint enthält deklarierte Konfigurationsdaten; ohne `--weights-sha256` sind Gewichte nicht kryptografisch attestiert. Der übergebene Hash wird nicht gegen die Serverdatei gegengeprüft.

Die Beispiele aus der Anforderung nennen `qwen2.5-72b`; **MAG verwendet derzeit andere Modell-IDs** (`deepseek-v4-flash-0731` bzw. `qwen3.8-flash-next-uncensored`). `--model` und `--port` müssen zum tatsächlich laufenden Inferenzendpunkt passen.

Optionen: `--endpoint /v1/chat/completions`, `--timeout 600`, `--numa none` (ohne Client-Pinning), `--weights-sha256 <hex>`, `--backend-build <version>`, `--template-id <id>`.

Das CLI schreibt einen lokalen Prüfbericht in `mag_determinism_state.json`. Ein gespeicherter Bericht wird **nicht blind als Autorisierung wiederverwendet**; `--infer` verifiziert selbst erneut. Das MAG-Frontend ist hiervon unabhängig und zeigt keine durch diese CLI bestätigte Plakette an.

## Radius-Abfrage im Raum (`/api/model-radius`)

Im Raum unter *Einstellungen → Modell → Radius-Abfrage* (nur Besitzer). Greedy-Decoding wählt bei jedem Schritt das Top-1-Token; die Radius-Abfrage zeigt die deterministischen **Geschwister-Antworten** um diesen Fixpunkt:

1. Eingabe wie im deterministischen Modus: genau eine `user`-Nachricht, kein Verlauf, keine Werkzeuge, Thinking aus (`/apply-template`, `/tokenize`).
2. Verteilung des **ersten** Antwort-Tokens (`/completion`, `n_probs = k`).
3. Jeder Kandidat mit `log p ≥ log p(Top-1) − radius` (Radius in nats; 2,0 ≙ mindestens 13,5 % der Top-1-Wahrscheinlichkeit) wird als erstes Token erzwungen (Prompt als Token-ID-Liste + Kandidat). Danach läuft die Antwort greedy weiter (`temperature 0`, `top_k 1`, `seed 42`, `cache_prompt false`).
4. Jede Geschwister-Antwort wird `repeat`-mal (Standard 2) neu berechnet und nur bei identischem Text als *reproduzierbar* markiert. Rang 1 ist die deterministische Antwort selbst.

ik_llama (DeepSeek) liefert Kandidaten nur als Text: die Token-ID wird über `/tokenize` aufgelöst und nur akzeptiert, wenn sie genau ein Token ist und per `/detokenize` exakt denselben Text ergibt; sonst wird der Kandidat mit Grund ausgewiesen statt geraten. llama.cpp (Qwen) liefert die IDs direkt.

Grenzen: verzweigt wird nur am ersten Token (keine Embedding-/Vektorraum-Suche). Eine Abfrage zur Zeit; abgelehnt (409), solange das Modell im Raum antwortet. Alle Aufrufe laufen über das Audit-Gateway (`X-Mag-Session: radius`).

```
POST /api/model-radius {"model":"deepseek-v4-flash-0731","input":"Nenne eine Farbe.","radius":2.0,"k":5,"max_tokens":48,"repeat":2}
```
Grenzen der Parameter: `radius` 0–10, `k` 2–8, `max_tokens` 8–256, `repeat` 1–3, Eingabe 1–8000 Bytes.

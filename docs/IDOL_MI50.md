# MAG Foto zu 3D – Idol / MI50 gfx906

Optionale GPU-Pipeline hinter bestehendem privatem MAG-nginx mit Tailscale-WhoIs. MAG-Raum bleibt C++, kein Docker, kein ComfyUI, keine Änderung an LLMs/Modellgewichten.

## Verwendung und API

Im MAG-Chat: Plus-Menü → **Foto zu 3D**. PNG/JPEG/WebP bis 20 MB. POST /api/idol/jobs mit rohen Bildbytes antwortet HTTP 202 mit id/state/stage. GET /api/idol/jobs/{id} zeigt queued, running, done oder error mit einer verständlichen Stufe/Fehlerangabe. Bei done: GET /api/idol/jobs/{id}/splat.ply, mesh.glb, mesh.stl. GLB wird lokal durch ein minimales WebGL2-Canvas angezeigt, ohne CDN. Keine Prozentangaben ohne Modelltelemetrie.

Ein Worker führt nur einen Job gleichzeitig aus; maximal acht weitere Jobs stehen in der Queue. Auf Neustart werden nicht fertige Jobs als Fehler markiert (keine heimliche Wiederholung). Die Job-Dateien liegen privat unter /var/lib/mag-idol/jobs und werden nicht automatisch gelöscht.

## Voraussetzungen

Lokale Python-Umgebung: /home/chris/idol-mi50/venv/bin/python, torch 2.7.1+rocm6.3, gfx906, lokal installiertes idol-sdk. Idol-Gewichte unter /home/chris/idol-mi50/weights/idol; DINO unter weights/dinov3-timm im von Transformers AutoModel lesbaren Format, FLUX.2 unter weights/flux2vae mit Diffusers-Unterordner vae/. Der Worker verwendet keine Online-Modell-Downloads. Für Mesh: scipy, scikit-image, trimesh und optional fast-simplification im Idol-venv. Benötigt rocm-smi mit JSON VRAM-Angaben und taskset.

Upstream-Idol-sample akzeptiert aktuell **kein** --device; der unveränderte FLUX.2-Encoder arbeitet mit BF16. Deshalb ruft MAG den originalen IdolPipeline-Code über einen eigenen FP16/SDPA-Adapter mit device cuda:0 auf. Der Adapter überschreibt den ImageEncoder **nur im eigenen Python-Prozess**, schreibt keine Änderungen in das Idol-SDK und verwendet keine FlashAttention. Beim Lauf werden lokale Modell-Konfigurationsreferenzen in einem privaten Overlay erzeugt; die Gewichte bleiben unverändert.

## Installation und Isolation

Das reguläre MAG-Release enthält ops/idol und die nginx-Include-Datei ops/nginx/mag-speech-idol.inc. Die vorhandene nginx-Site inkludiert mag-speech*.inc innerhalb des privaten HTTPS-Servers. Die Route /api/idol/ erbt die bereits vorhandene WhoIs-Authentisierung und Tailnet-ACL, der Worker lauscht nur auf 127.0.0.1:8793. Nach dem Release aktiviert ops/deploy.sh den optionalen mag-idol.service, wenn das lokale Idol-venv und Gewichte vorhanden sind.

CPU-Pinning taskset -c 28-55 und systemd AllowedCPUs=28-55. Lena nutzt dieselbe MI50. Unter 10000 MiB freiem VRAM verweigert der Worker den GPU-Job mit klarer Meldung, statt die LLMs zu entladen. Keine CPU-Ausweichroute. Eine VRAM-Vorabprüfung reserviert keinen Speicher; die GPU kann auch später belegt werden. Dienste der LLMs werden nicht vom Idol-Installer neu gestartet.

## Mesh-Grenzen

Das originale PLY enthält die Gaussians. Mesh-Konvertierung erstellt ein begrenztes 128³-Dichtefeld mit approximiertem gaußschem Weichzeichnen, nutzt Marching Cubes, Laplace-Glättung und optional Dezimierung auf 90000 Faces. GLB speichert interpolierte Vertexfarben; STL speichert keine Farben. Das Mesh ist nicht verlustfrei, nicht maßstabsgetreu und möglicherweise nicht wasserdicht; vor einem 3D-Druck reparieren und skalieren. Unsichtbare Rückseiten werden von Idol geschätzt.

## Betrieb

Nur Quellcode-/API-Vertrag-Checks in CI; keine Benchmarks oder Testphasen. Reale gfx906-Inferenz und lokale Encoder-Struktur sind erst auf MAG prüfbar. Diagnose: systemctl status mag-idol, journalctl -u mag-idol und privates /var/lib/mag-idol/jobs/<id>/idol.log. Nie Bilddaten oder Logdateien in Git einchecken.

## MAG-Chat: laufende Werkzeugaktivität (Zusatzauftrag)

Die bestehende C++-SSE-Verbindung überträgt Bot-Start, Schrittverlauf, echte Tool-Argumente, Tool-Ergebnis, Laufzeit und Erfolg/Fehler. Für den nativen VM-Befehl run_command wird der schon bestehende Subprocess-Pipe-Leser um einen optionalen Callback erweitert: tool_output veröffentlicht höchstens viermal pro Sekunde die letzten 2.000 Zeichen tatsächlich empfangener stdout/stderr-Ausgabe. Die Ausgabe kann durch das Programm selbst gepuffert sein; ohne empfangene Bytes wird nichts vorgetäuscht. Nach Abschluss bleibt tool_result die maßgebliche Quelle für Exitstatus und Ergebnis. Abbrechen verwendet unverändert /api/cancel mit Bot-ID und die vorhandene Stop-Synchronisierung.

Die Live-Karte sitzt direkt im Bot-Nachrichtenartikel unter der Antwort, standardmäßig als eine Zeile. Sie enthält eine aufklappbare Liste der bisherigen Tool-Aufrufe mit Befehl/Argumenten, stdout-Ende, Laufzeit und Zustand sowie den Schrittverlauf. Der Chat besitzt bereits scroll-pinning und stableScrollBefore/stableScrollAfter; die Karte erweitert weder Composer noch die Statusleiste. Kein separater Audit-Dienst und keine neue offene Schnittstelle. Tool-Ausgaben sind auf der privaten Besitzer-UI sichtbar und werden clientseitig bei gängigen Credential-Mustern maskiert; dies ersetzt keine sorgfältige Geheimnisbehandlung im Bot.

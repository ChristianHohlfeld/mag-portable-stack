# MAG Foto zu 3D – Idol / MI50 gfx906

Optionale GPU-Pipeline hinter bestehendem privatem MAG-nginx mit Tailscale-WhoIs. MAG-Raum bleibt C++, kein Docker, kein ComfyUI, keine Änderung an LLMs/Modellgewichten.

## Verwendung und API

Im MAG-Chat: Plus-Menü → **Foto zu 3D**. PNG/JPEG/WebP bis 20 MB. POST /api/idol/jobs mit rohen Bildbytes antwortet HTTP 202 mit id/state/stage. GET /api/idol/jobs/{id} zeigt queued, running, done oder error mit einer verständlichen Stufe/Fehlerangabe. Bei done: GET /api/idol/jobs/{id}/splat.ply, mesh.glb, mesh.stl. GLB wird lokal durch ein minimales WebGL2-Canvas angezeigt, ohne CDN. Der Worker liest die tatsächlich protokollierten Sampling-Schritte (1–50) und zeigt einen Balken ohne erfundene ETA; der Durchlauf auf der geteilten MI50 dauerte im bestätigten Betrieb etwa 33 Minuten.

Ein Worker führt nur einen Job gleichzeitig aus; maximal acht weitere Jobs stehen in der Queue. Auf Neustart werden nicht fertige Jobs als Fehler markiert (keine heimliche Wiederholung). Die Job-Dateien liegen privat unter /var/lib/mag-idol/jobs und werden nicht automatisch gelöscht.

## Voraussetzungen

Lokale Python-Umgebung: /home/chris/idol-mi50/venv/bin/python, torch 2.7.1+rocm6.3, gfx906, lokal installiertes idol-sdk. Idol-Gewichte unter /home/chris/idol-mi50/weights/idol; DINO unter weights/dinov3-timm als timm/vit_huge_plus_patch16_dinov3.lvd1689m und FLUX.2-VAE unter weights/flux2vae aus FLUX.2-klein-4B. Beide werden vom lokal gepatchten Idol über IDOL_DINOV3 und IDOL_FLUX2_VAE geladen; HF-Zugriff auf gated Repos ist nicht erforderlich. Der Worker verwendet keine Online-Modell-Downloads. Für Mesh: scipy, scikit-image, trimesh und optional fast-simplification im Idol-venv. Benötigt taskset und Zugriff auf die GPU über SupplementaryGroups=render video; System-ROCm wird nicht vorausgesetzt.

MAG benutzt die tatsächlich funktionierende lokale Idol-Installation unter ~/idol-mi50 mit dem bereits angewendeten ~/idol-mi50/mi50.patch. Der Runner startet idol.sample direkt (kein zweiter ImageEncoder und kein Checkpoint-Overlay), mit --dinov3_dtype fp16, --no_optimize, SDPA und HF_HUB_OFFLINE=1. Das vorhandene Patch übernimmt FP16 mit NaN-Abbruch, timm-DINOv3, lokalen Flux-VAE, Query-Chunking auf gfx906 und unterbindet FlashAttention/torch.compile/cuDNN. Der Worker setzt einen harten PyTorch-Allocator-Cap von 7,8 GiB vor dem Modellimport. torch 2.7.1+rocm6.3 bleibt unverändert, tensordict==0.9.1 wird ausschließlich mit --no-deps gepinnt und idol-sdk bei fehlender Installation --no-deps editable installiert.

## Installation und Isolation

Das reguläre MAG-Release enthält ops/idol und die nginx-Include-Datei ops/nginx/mag-speech-idol.inc. Die vorhandene nginx-Site inkludiert mag-speech*.inc innerhalb des privaten HTTPS-Servers. Die Route /api/idol/ erbt die bereits vorhandene WhoIs-Authentisierung und Tailnet-ACL, der Worker lauscht nur auf 127.0.0.1:8793. Nach dem Release aktiviert ops/deploy.sh den optionalen mag-idol.service, wenn das lokale Idol-venv und Gewichte vorhanden sind.

CPU-Pinning taskset -c 28-55 und systemd AllowedCPUs=28-55. Lena nutzt dieselbe MI50. Die MI50-Speicherangaben aus ROCm unterschätzen Lenas Vulkan-Belegung. Deshalb gibt es ausdrücklich **keine** rocm-smi-Freispeicherfreigabe; statt dessen wird die PyTorch-GPU-Speicherquote auf absolut 7,8 GiB begrenzt. Der bestätigte Peak betrug 6,94 GiB PyTorch und die gesamte Karte war mit 33,25/34,3 GB stark belegt. Zusätzliche Vulkan-Nutzung kann dennoch OOM verursachen; dies wird als Jobfehler ausgegeben. Kein CPU-Fallback, keine automatische LLM-Entladung. Dienste der LLMs werden nicht vom Idol-Installer neu gestartet.

## Mesh-Grenzen

Das originale PLY bleibt unverändert. Direkt vor der Bildauswahl bietet der Dialog und die Inline-Chat-Karte in derselben Aktionszeile **Qualität: Schnell · Normal · Hoch** (letzte Wahl in localStorage, Standard Normal). Die private API nimmt den URL-Parameter `quality=fast|normal|high` bei ihrem unveränderten Raw-Image-POST entgegen, prüft ihn und speichert die Auswahl in `status.json`. Die Inferenz mit Idol/MI50 bleibt identisch (50 Schritte); Qualität betrifft ausschließlich die CPU-Mesh-Rekonstruktion.

| Qualität | Dichtegitter | max. Gaussians | Face-Limit | UV-PNG | Mesh-Timeout |
|---|---:|---:|---:|---:|---:|
| Schnell | 128³ | 150.000 | 150.000 | 1024² | 300 s |
| Normal | 224³ | 400.000 | 400.000 | 2048² | 900 s |
| Hoch | 320³ | 1.000.000 | 800.000 | 4096² | 1800 s |

Die Dichtebildung erfolgt als Float32-X-Slabs mit Gaussian-Filter-Halo statt drei kompletten Zwischenvolumen; Marching Cubes, schwächerer Schwellwert bei Hoch, Taubin-Glättung und Face-Limit folgen. xatlas erzeugt UVs, danach werden die Texel anhand der geometrischen Oberfläche aus mehreren nahen, opacity-gewichteten Gaussians gebacken; 8 Pixel Dilatation an UV-Rändern, PNG im GLB. Der Canvas-Renderer zeigt diese PNG-Textur; STL bleibt farblos. Y-down-Idol-Mesh wird um Z um 180° gedreht (GLB Y-up), STL zusätzlich um X um 90° und in Millimeter auf Z=0 gestellt. Das Ergebnis ist weiterhin eine approximierte Oberfläche; ein wasserdichtes Druckmesh wird **nicht garantiert**, Drucker-Slicer und Wandstärken müssen geprüft werden. Unsichtbare Rückseiten bleiben Modellschätzungen.

## Betrieb

Nur Quellcode-/API-Vertrag-Checks in CI; keine Benchmarks oder Testphasen. Grok Bot hat die gepatchte Inferenz auf der echten MI50 bereits bestätigt. Dieser GitHub-PR übernimmt die verifizierte Laufzeitkonfiguration, kann den lokalen Patch aber in CI nicht ausführen oder automatisch beweisen. Diagnose: systemctl status mag-idol, journalctl -u mag-idol und privates /var/lib/mag-idol/jobs/<id>/idol.log. Nie Bilddaten oder Logdateien in Git einchecken.

## MAG-Chat: laufende Werkzeugaktivität (Zusatzauftrag)

Die bestehende C++-SSE-Verbindung überträgt Bot-Start, Schrittverlauf, echte Tool-Argumente, Tool-Ergebnis, Laufzeit und Erfolg/Fehler. Für den nativen VM-Befehl run_command wird der schon bestehende Subprocess-Pipe-Leser um einen optionalen Callback erweitert: tool_output veröffentlicht höchstens viermal pro Sekunde die letzten 2.000 Zeichen tatsächlich empfangener stdout/stderr-Ausgabe. Die Ausgabe kann durch das Programm selbst gepuffert sein; ohne empfangene Bytes wird nichts vorgetäuscht. Nach Abschluss bleibt tool_result die maßgebliche Quelle für Exitstatus und Ergebnis. Abbrechen verwendet unverändert /api/cancel mit Bot-ID und die vorhandene Stop-Synchronisierung.

Die Live-Karte sitzt direkt im Bot-Nachrichtenartikel unter der Antwort, standardmäßig als eine Zeile. Sie enthält eine aufklappbare Liste der bisherigen Tool-Aufrufe mit Befehl/Argumenten, stdout-Ende, Laufzeit und Zustand sowie den Schrittverlauf. Der Chat besitzt bereits scroll-pinning und stableScrollBefore/stableScrollAfter; die Karte erweitert weder Composer noch die Statusleiste. Kein separater Audit-Dienst und keine neue offene Schnittstelle. Tool-Ausgaben sind auf der privaten Besitzer-UI sichtbar und werden clientseitig bei gängigen Credential-Mustern maskiert; dies ersetzt keine sorgfältige Geheimnisbehandlung im Bot.

## Verifizierter Stand und Grenzen (09.11.2026)

- Torch 2.7.1+rocm6.3 im vorhandenen Venv, tensordict 0.9.1 exakt, keine System-ROCm-Laufzeit. Das lokale mi50.patch wird nicht aus GitHub rekonstruiert: Vorhandene, funktionierende Dateien bleiben bestehen.
- Gated Encoder werden offline aus DINOv3-timm und FLUX.2-klein VAE geladen. Die Python-Umgebung darf durch eine automatische pip-Abhängigkeitsauflösung nicht geändert werden.
- Die Sample-CLI braucht rund 33 Minuten (50 Schritte); MAG setzt 70 Minuten Inferenz-Timeout, zeigt die aus stdout gelesene Schrittzahl und erlaubt POST /api/idol/jobs/{id}/cancel. Abbruch beendet die laufende Prozessgruppe auch beim Mesh-Export. Wartende Jobs sind ebenfalls abbrechbar.
- Mesh-Konvertierung im PR bleibt die eigenständige CPU-Rekonstruktion unter ops/idol/mesh.py mit 200.000 Faces als Obergrenze. Die auf MAG bestätigte Datei ~/idol-mi50/idol2mesh.py ist nicht Teil des Repositories und wurde **nicht** unbesehen kopiert: deren Quellcode/Schnittstelle kann hier nicht gelesen werden. Es wird keine unbelegte Gleichheit oder Laufzeit von 6 Sekunden behauptet.


## Exklusive MI50-Belegung: Lena pausieren (Auftrag 09.11.)

Die neue, im Repository bestätigte Lena-Unit ist `mag-llm-coding@0.service` (Vulkan an :8191). **Nur** sie wird vor Idol gestoppt, Noah und MAG-Raum bleiben aktiv. Root installiert den unveränderlichen Helfer `/usr/local/lib/mag-idol/gpu-lease.sh`; die sudoers-Datei erlaubt dem Benutzer `chris` ausschließlich dessen feste `acquire`-/`restore`-Aktionen. Kein beliebiges `systemctl` über die Chat-API.

- `acquire`: Setzt zuerst einen Root-Marker in `/run/mag-idol/lena-paused`, stellt einen nicht-blockierenden Stop ein und beendet den Unit-Prozess ohne Drain-Warten. Der Idol-Job startet nur, wenn der Service nicht mehr aktiv ist und kein MainPID mehr gemeldet wird.
- Während Idol: **keine 7,8-GiB-PyTorch-Grenze**; Idol nutzt die freigegebene GPU. Die unveränderte lokale ROCm/FP16/SDPA-/Chunking-Patchinstallation ist weiterhin Voraussetzung.
- `finally` fordert nach Erfolg, Timeout, Jobfehler oder Abbruch die Wiederherstellung der Lena-Unit an. Es prüft bis zu 360 Sekunden den HTTP-200-Status von `127.0.0.1:8191/health`. Ein Fehlschlag wird als Warnung im Jobstatus gespeichert, nicht versteckt. Zusätzlich lösen `ExecStartPre` und `ExecStopPost` die Root-Wiederherstellung bei Worker-Restart/Stop aus.
- Der Chat zeigt während der Belegung **„Lena pausiert für Foto zu 3D“** und danach den Wiederanlauf. Der Job erscheint als kompakte Karte direkt im Verlauf (kein zusätzliches Composer-Element), einschließlich echter Sampling-Schritte 1–50, Abbrechen, GLB-WebGL2-Vorschau und PLY-/GLB-/STL-Downloads. Der Kartenknoten überlebt normale Chat-Re-Renders.

**Sicherheitsgrenze:** Ein physischer Hardwaredefekt oder eine fehlgeschlagene systemd-Neustart-Anforderung lässt sich nicht softwareseitig „garantiert“ beheben. Die Fehleranzeige und das separate `ExecStopPost` sind ausdrücklich Teil der Absicherung; der Worker darf die MI50 nicht beanspruchen, wenn Lena sich nicht sicher beenden lässt. Die übrigen MAG-Dienste bleiben unverändert.

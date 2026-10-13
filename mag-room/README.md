# MAG Room — Warmes Gruppenchat-Frontend mit VM-gestützten Bots

Warm laufender Gruppenchat auf dem MAG-Host. Eine einzige Modellinstanz
(llama.cpp, `gpt-oss:120b`) bedient mehrere Bots; jeder Bot besitzt eine eigene
KVM-VM mit eigenem Dateisystem und **eigenem Brave-Web-Zugriff (standardmäßig an)**.

## Komponenten

| Datei | Zweck |
|---|---|
| `app.py` | HTTP-Server, `/api/stream` (SSE), Tool-Loop, Autogespräch, Brave-Suche |
| `deploy.sh` | Installation auf MAG (Wrapper, sudoers, Env, Dienst) |
| `mag-120.service` | systemd-Unit für den Raumdienst (`magbot`-User, Port 8766) |
| `magbot-guest-run` | root-Wrapper: führt Befehle als `bot` in der Bot-VM aus |
| `magbot-guest.sudoers` | erlaubt dem Dienst genau diesen einen Wrapper-Befehl |
| `.env.example` | Vorlage für den Brave-Schlüssel (echter Schlüssel nur auf dem Host) |

## Bots und Werkzeuge

- Bots: `Arlo` → VM `agent1`, `Nova` → VM `agent2`.
- `run_command`: führt Shell-Befehle als unprivilegierter `bot` in der eigenen VM aus.
- `web_search`: Brave Search über den eigenen Schlüssel. **Standard: an.**

## Konfiguration (Umgebung)

| Variable | Default | Bedeutung |
|---|---|---|
| `MAG_ROOM_PORT` | `8766` | HTTP-Port |
| `MAG_ROOM_BACKEND` | `http://127.0.0.1:8080/v1/chat/completions` | llama.cpp-Endpunkt |
| `MAG_ROOM_MODEL` | `gpt-oss:120b` | Modellkennung |
| `MAG_ROOM_BRAVE_KEY` | – | Brave-API-Schlüssel (in `/etc/default/mag-room`) |
| `MAG_ROOM_GUEST_RUN` | `/usr/local/sbin/magbot-guest-run` | VM-Wrapper |

## Deployment

```bash
sudo bash deploy.sh
```

`deploy.sh` installiert Wrapper und sudoers-Eintrag, legt den Brave-Schlüssel
unter `/etc/default/mag-room` (root, 0600) ab, installiert `app.py` nach
`/opt/magbot/app.py` und startet `mag-120` neu. Danach ist der Raum über
Tailscale (`https://mag.tail3e02ae.ts.net:8446`) erreichbar.

## Hinweis

Der echte Brave-Schlüssel gehört **nicht** ins Repository. Siehe `.env.example`.

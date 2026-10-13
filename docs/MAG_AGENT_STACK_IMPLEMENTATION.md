# MAG Agent Stack — Umsetzungsstand (2026-10-13)

Ergänzt `docs/MAG_AGENT_STACK_CONTRACT.md` um den **tatsächlich umgesetzten und
verifizierten** Stand. Der Contract beschreibt den Plan; dieses Dokument den Ist-Zustand.

## Abweichungen vom Contract (Ist)

- **Modell:** Es läuft `gpt-oss:120b` über llama.cpp (`mag-llm.service`,
  `127.0.0.1:8080`), nicht DeepSeek. Ein Slot (`-np 1`); Anfragen serialisieren.
- **Golden Image:** Fertig. `/var/lib/libvirt/images/magbot/base/golden.qcow2`
  (read-only Basis + qcow2-Overlay je Bot). Spawn-Zeit ~28 s statt ~250 s.
- **Chat-Template:** `reasoning_effort: low` reduziert die Reasoning-Tokens stark.
  Tool-Calls laufen über den OpenAI-kompatiblen Endpunkt.

## Umgesetzt

### MAGBOT (KVM-Lebenszyklus)
`magbot/` — `spawn`/`despawn`/`status`/`exec`/`doctor`/`inspect`/`logs`,
Admission-Kontrolle, Golden-Image-Präferenz, idempotenter Bootstrap.
VMs `magbot-agent1`, `magbot-agent2` laufen (`ready`), Befehle als `bot`.

### MAG Room (Gruppenchat)
`mag-room/` — warmes Frontend mit zwei Bots (Arlo→agent1, Nova→agent2):

- **Systemzugriff:** `run_command` führt Befehle als `bot` in der eigenen VM aus
  (über `magbot-guest-run` + sudoers). Verifiziert.
- **Eigener Web-Zugriff:** `web_search` über Brave, **standardmäßig an**. Jeder Bot
  hat eigenen Schlüssel-Zugriff. Verifiziert (Arlo + Nova, mit Quell-URLs).
- **Autogespräch:** Endloser Bot-zu-Bot-Dialog, per UI-Schalter.
- **UI:** Bots wählbar, Websuche-Schalter (an), Autogespräch-Schalter (aus), `/api/stream` (SSE).

## Sicherheit / Geheimnisse

Der Brave-Schlüssel liegt nur auf dem Host (`/etc/default/mag-room`, root 0600) und
**nicht** im Repository (siehe `mag-room/.env.example`).

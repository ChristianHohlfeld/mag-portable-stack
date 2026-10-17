# Gastzugang zum Raum

Ein Raum, eine Unterhaltung: Chris, Gäste und DeepSeek. Nur über Tailscale,
nur `https://mag.stardenburg.de` (100.123.114.16, TCP 443). Kein SSH, keine
anderen Geräte.

## Einladen

1. Tailscale-Login des Gastes kennen (z. B. `ada@github`).
2. In der Tailscale-ACL `ops/tailscale/policy.hujson` einfügen: den Login in
   `group:mag-guests` eintragen. Alternativ ein Gerät mit `tag:mag-guest`
   teilen (Node Sharing nur für mag). Speichern.
3. Gast einladen: Tailscale-Admin → Users → Invite, **oder** mag per Node
   Sharing an genau diesen Nutzer teilen. Nicht ins Admin-Netz aufnehmen,
   keinen SSH-Zugang geben.
4. `access.json` auf mag muss Chris als Owner nennen (siehe Deploy). Der Gast
   steht dort nicht — unbekannte Logins sind Gäste.

## Was der Gast macht

1. Tailscale installieren (tailscale.com/download) und die Einladung annehmen.
2. Im Browser `https://mag.stardenburg.de` öffnen. Zertifikat ist gültig, die
   Adresse ist nur im Tailnet erreichbar.
3. Oben steht `Gast · Name`. Schreiben, Dateien, Sprache: ja. Offline, Stop,
   Jobs, Erinnerungen ändern, Raum leeren, Bots löschen: nein, solange Chris
   das nicht in `access.json` freigibt.
4. Jede Nachricht zeigt den echten Namen. DeepSeek sieht `[Name]:` im Verlauf
   und antwortet in denselben Raum. Alle offenen Seiten aktualisieren sich
   über die bestehende Live-Verbindung.

## Widerrufen

- Login aus `group:mag-guests` streichen oder das geteilte Gerät entfernen
  (Tailscale-Admin → Machines → Share → Remove, bzw. Users → entfernen).
- Die Verbindung bricht ab, sobald Tailscale die ACL zieht. Es gibt kein
  zweites Passwort im Raum.

## Für Ali (Deploy)

Der Gastzugang ist mit nginx, nicht Caddy, umgesetzt. Owner-/Gast-Rechte sind
bereits im C++-Backend. Den Live-Stand nicht neu aufbauen und **mag-llm niemals
neu starten**.

Die verbindliche Sicherheitsanleitung steht unter
[INSTALL.md](INSTALL.md) und [OPS.md](OPS.md).
Vor dem neuen Eingang `/var/lib/magbot/room/access.json` korrekt mit dem
per `tailscale whois` ermittelten Owner-Login konfigurieren; nicht raten,
nicht in Git einchecken. `preflight.sh`, cert `--no-reload`,
`install.sh` mit **180s automatischem Rollback**, Owner-Check von einem
anderen Tailnet-Gerät, dann `confirm.sh --owner-verified`, anschließend
Gastberechtigungen und SSE prüfen. Erst nach Erfolg `remove-caddy.sh`
ausführen. Unbekannter oder nicht authentifizierter externer Zugriff
bleibt gesperrt. Tailscale Serve :8446 bleibt unabhängig erhalten.

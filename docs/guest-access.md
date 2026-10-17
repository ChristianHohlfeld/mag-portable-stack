# MAG – Gäste sicher einladen

## Sicherheitsgrenze

Der Raum ist ausschließlich unter `https://mag.stardenburg.de` auf
`100.123.114.16:443` im Tailnet verfügbar. **Gäste werden niemals als
Vollmitglieder mit allgemeinen 22/443/3000-Rechten eingeladen.** Der OAuth-Client
stellt ausschließlich Einmal-Auth-Keys für `tag:mag-guest` aus. Für dieses Tag
erlaubt die Tailscale-Policy nur `100.123.114.16:443`.

Ein getaggtes Gerät hat keine `UserProfile`-Identität. C++ `whois` prüft
stattdessen `Node.ID` und `Node.Tags`, nginx übernimmt nur die vom lokalen
Authentifizierungs-Subrequest zurückgegebenen Identitätsfelder und verwirft
alle vom Browser eingesandten `X-Tailscale-*`-Header. Der C++-Raum prüft bei
**jeder Anfrage** zusätzlich die aktive, nicht abgelaufene Gerätezuordnung in
`/var/lib/magbot/room/access.json`. Ein neu getaggtes Gerät ohne Einladung
sieht ausschließlich die Aktivierungsseite, nicht den Chat.

Gast-Endpunkte werden über eine harte Allowlist begrenzt. Kein Zugriff auf
Jobs, Pages, Admin, Agentenverwaltung, Modellwahl, Reset, Bot-Speicher,
Connectors oder privilegierte Browserkontrolle, auch wenn `permissions` in
`access.json` versehentlich zu großzügig konfiguriert wird. SSE-Verbindungen
werden nach Entfernung auch ohne Browser-Neuladen innerhalb von höchstens
einer Sekunde geschlossen; neue Requests sind unmittelbar gesperrt.

## Einmalige Einrichtung (Chris / Ali)

1. In der Tailscale-Admin-Konsole unter **Trust credentials → OAuth clients**
   einen Client namens `MAG Guest Manager` erstellen. **Nur** die Scopes
   `auth_keys` und `devices:core`, jeweils nur `tag:mag-guest`. Keine
   `users`-, `policy_file`-, `all`- oder Owner-Scopes. Der Client ist nicht
   als normales Tailnet-Mitglied einzuladen. Die Policy in
   `ops/tailscale/policy.hujson` prüfen, insbesondere den Grant nur auf
   `100.123.114.16:443` und die Tag-Owner-Regel.
2. Client-ID/Secret direkt auf mag **außerhalb von Git** hinterlegen:
   ```bash
   sudo install -d -o root -g root -m 0700 /etc/credstore
   sudo sh -c 'umask 077; cat > /etc/credstore/mag-guest-oauth'
   # In diese Eingabe NUR folgendes JSON mit den echten Werten schreiben:
   # {"client_id":"...","client_secret":"..."}
   # Ende mit Ctrl-D. Niemals in die Shell-Befehlszeile eingeben!
   sudo chown root:root /etc/credstore/mag-guest-oauth
   sudo chmod 0600 /etc/credstore/mag-guest-oauth
   sudo systemctl daemon-reload
   ```
3. `mag-120.service` verwendet `LoadCredential=mag-guest-oauth` (systemd-Credstore).
   systemd kopiert die Datei geschützt nach `/run/credentials/...`, ohne sie
   per Environment-Variable oder Kommandozeilenargument zu veröffentlichen.
   Ohne Credential bleibt die Gäste-Einladung deaktiviert, der Raum
   selbst startet weiterhin. Die normalen Build-/Deploy-Schritte
   bleiben `ops/deploy.sh`. Nur `mag-120` neu starten, **niemals `mag-llm`**.
4. Besitzeridentität unverändert unter
   `/var/lib/magbot/room/access.json` konfigurieren:
   ```json
   {"owners":["chris.hohlfeld@gmail.com"],"owner_name":"Chris"}
   ```
   Bestehende Schlüssel und Permissions erhalten; das Backend ergänzt
   `guest_invites` atomar selbst. Die Datei muss für den Dienstnutzer
   `magbot` schreibbar sein und darf keine Welt-Lese-/Schreibrechte haben.

## Einladung im Raum

Nur der Besitzer sieht **Gäste → Einladen**. Name und Notiz sind optional,
das Ablaufdatum ist 1 Stunde bis 30 Tage in der Zukunft. Die API erstellt
einen nicht wiederverwendbaren, preauthorized, getaggten Auth-Key mit 24 Stunden
Schlüsselgültigkeit. Die Antwort zeigt **einmal** Schlüssel, Einladungslink
und QR-Code. Der QR-Code enthält ebenfalls den Schlüssel, ist also ein
Geheimnis; nur über einen geeigneten privaten Kanal weitergeben.
Niemals Screenshots öffentlich posten. Der Server speichert nur die
Schlüssel-ID zur Löschung und einen SHA-256-Hash des einmaligen
Aktivierungscodes, **niemals den Auth-Key oder OAuth-Secret im Klartext**.

Der Gast:
1. Tailscale für Windows/macOS/Linux/iOS/Android installieren.
2. In Tailscale (mobil: Anmeldeseite → Menü → **Use an auth key**) den
   Einmal-Schlüssel eingeben. Nicht mit einem normalen persönlichen
   Tailnet-Login als Vollmitglied beitreten.
3. Den erhaltenen Einladungslink auf demselben Gerät öffnen. Die
   Aktivierungsseite bestätigt den privaten Code gegen `Node.ID`, Tag und
   die zugehörige Tailscale-IP anhand der Geräte-API. Danach öffnet sich
   derselbe MAG-Raum. Der Link-Code ist ab Claim nicht wiederverwendbar.

**Wichtig:** Tailscale empfiehlt Tags primär für nicht-benutzergebundene
Geräte. Mit einem Auth-Key wird das Gerät zu einem tag-owned Device statt
einem persönlich zugeordneten Gerät. Darum verlangt MAG zusätzlich den
zweiten, einmaligen Aktivierungsnachweis.

## Übersicht, Widerruf, automatische Bereinigung

Unter **Gäste**: Name/Notiz, Gerät, online/zuletzt gesehen (falls API
erreichbar), erstellt, Ablauf, noch offene Einladungen. Der Besitzer
bestätigt **Entfernen** ausdrücklich. C++ sperrt zuerst den Eintrag in
`access.json`; dann werden unbenutzte Keys und Gerät via Tailscale-API
gelöscht. Falls Tailscale nicht erreichbar ist, bleibt die Sperre lokal
wirksam, die Löschung wird spätestens im nächsten minütlichen Tick
erneut versucht. Abgelaufene Schlüssel, offene Einladungen und
Geräte werden genauso abgeräumt.

Der C++-Scheduler verwendet keinen neuen Dienst. Ein revisionsfähiges,
geheimnisfreies Audit-Protokoll liegt unter
`/var/lib/magbot/room/guest-audit.log` (Ersteller, Zeitpunkt, Aktion,
Einladungs-ID). Das OAuth-Access-Token liegt nur kurz im Prozessspeicher.
Einladungs-/Claim-POSTs sind rate-limitiert; Admin-Mutationen erfordern
whois-Owner, `X-Mag-Token`, identische HTTPS-Origin und
`Sec-Fetch-Site: same-origin`.

## Notfall-Widerruf / Incident

- **Sofort:** In der Tailscale-Konsole Geräte mit `tag:mag-guest` löschen und
  zugehörige Auth-Keys widerrufen. Bei kompromittiertem OAuth-Client auch
  diesen unter Trust credentials widerrufen und das Secret auf mag erneuern.
- `guest_invites` in `access.json` mit fachkundiger Hilfe sperren
  (`revoked:true`), `mag-120` kontrolliert neu starten, wenn nötig.
  Es darf **nicht** das ganze `access.json` oder die Besitzerliste gelöscht
  werden. Entfernte Geräte bekommen dadurch sofort 403, bestehende
  SSE-Verbindungen werden beendet.
- `journalctl -u mag-120`, `guest-audit.log`, Tailnet-Geräteliste und
  `ops/tailscale/policy.hujson` prüfen. Im Audit dürfen keine Auth-Keys
  oder OAuth-Zugangsdaten stehen.
- Fail-closed: fehlt `access.json`, wertebedingt eine Zuordnung nicht mehr
  stimmt oder ist das Ablaufdatum überschritten, erhält ein Gast keinen
  Raumzugriff. Bereits angenommene Gäste werden nicht allein durch
  das Verfallen ihres **Einladungs-Keys** gelöscht, sondern durch das
  im Register gespeicherte Aufenthaltsende.

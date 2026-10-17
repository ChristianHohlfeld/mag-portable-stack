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


## Wiederherstellbare Raum-Löschung (ab diesem PR)

Alle Mutationen werden zentral im C++-Server geprüft (Default Deny).
Nur Besitzer dürfen Raum, Nachrichten, Dateien, Undo oder Sicherungen löschen
bzw. wiederherstellen. Ein Gast darf Chat-Nachrichten senden, reagieren und
Dateien hochladen, aber nicht löschen: Historische Dateieinträge enthalten
keine verlässlich verifizierbare Eigentümer-ID (nur einen Anzeigenamen).
Eine Autorisierung per Anzeigename wäre unsicher.

POST /api/room/clear erfordert eine per Tailscale-whois/HMAC signierte
Owner-Identität. Vor der Löschung wird ein konsistenter Snapshot aller
Raum-Metadaten und aktiven Dateiinhalte unter
/var/lib/magbot/room/snapshots/<zeit>-<zufall>/ gespeichert.
Die temporäre Sicherung wird erst nach erfolgreichem fsync atomar
umbenannt. Fehler beim Sichern (z. B. fehlende Dateien, Symlinks,
Speicherplatz oder Limit) geben HTTP 507 zurück: Der Raum bleibt bestehen.
Der bisherige 10-Sekunden-Undo bleibt zusätzlich erhalten.

Aufbewahrung: maximal 5 Sicherungen, höchstens 7 Tage und insgesamt
maximal 2 GiB; eine Sicherung maximal 512 MiB. Die neuesten Sicherungen
bleiben nach Browser-Neustart oder Raum-Neustart verfügbar. Die Daten
bleiben im geschützten State-Pfad und sind nicht statisch veröffentlicht.

Im Raum unter Daten -> Raum -> Sicherungen ansehen kann der Owner
einen Stand nach Bestätigung wiederherstellen. Technische Endpunkte:
GET /api/room/snapshots und POST /api/room/snapshots/{id}/restore.
Vor Restore wird der aktuelle Zustand ebenfalls gesichert und dessen
ID als previous_snapshot zurückgegeben. Während Bot-Arbeit: HTTP 409.
Fehlender Owner-Nachweis/CSRF: HTTP 403. Archiv defekt: HTTP 422.
Restore betrifft nur Chat-Dateien und Raumverlauf, nicht Bot-Memory/VMs.

Notfall: Zuerst Host-/Filesystem-Backups sichern; Snapshot-Dateien
nicht in Logs kopieren. Alte Daten vom um 07:16 gemeldeten ungeschützten
Clear sind NICHT rückwirkend durch diese Funktion wiederherstellbar.
Eventuell noch vorhandene Host-Backups/Undo-/Trash-Reste separat prüfen.


## Hotfix nach dem 403-Ausfall vom 17. Oktober 2026

**Ursache:** Das Room-Binary aus 8e2d045 prüft den signierten nativen
Tailscale-whois-Nachweis für Identitäten hinter nginx; der damalige
`ops/deploy.sh` installierte die dafür nötige nginx-vHost-Konfiguration
jedoch nicht. Ein bloßer localhost-`/readyz`-Check bemerkt diesen Fehler
nicht. Die fehlende OAuth-Credential hat **keinen Einfluss auf die
Whois-Prüfung**: Sie darf niemals als Umgehungsgrund verwendet werden.

**Ab diesem Hotfix:** `ops/deploy.sh` validiert vor der ersten
Release-Änderung Voraussetzungen, sichert die bisherige nginx-Site und deren
enabled-Symlink, installiert idempotent
`ops/private-access/mag-room.nginx.conf`, validiert `nginx -t/-T`,
lädt nginx neu und prüft **beide** Pfade `/` und `/api/state` über
einen *externen echten Owner-Tailscale-Client*. Eine fehlende OAuth-Credential
deaktiviert nur Einladungen. Fehler (einschließlich 403, 5xx, ungültige Ownerrolle
oder fehlender SSH-Probe) führen zu Abbruch **vor dem Umschalten** oder
automatischem Room- **und** nginx-Rollback. Weder Caddy noch `mag-llm`
werden angefasst.

### Schritt 1: unabhängiges Owner-Testgerät (für echten Smoke-Test)

Voraussetzung ist eine **zweite** Linux/macOS/Box-Maschine im Tailnet, die
mit *Chris' normaler Owner-Tailscale-Identität* verbunden ist und auf MAG via
SSH **nicht-interaktiv** erreichbar ist. Sie darf **nicht** das MAG-Gerät
selbst, ein `tag:mag-guest`-Gerät oder eine simulierte HTTP-Header-Identität
sein. Auf dieser Maschine müssen `curl`, normale Systemzertifikate und
Tailscale vorhanden sein. Von dieser Maschine prüfen:

```bash
curl -fsS https://mag.stardenburg.de/api/state | jq '.you'
# role=owner, login=chris.hohlfeld@gmail.com NACH dem nginx-Fix
```

Auf MAG das Root-only Probe-Profil einmalig anlegen:

```bash
sudo install -d -o root -g root -m 0700 /etc/mag-stack
sudo install -o root -g root -m 0600 /dev/null /etc/mag-stack/deploy-owner-probe.conf
sudoedit /etc/mag-stack/deploy-owner-probe.conf
```

Inhalt der Datei (Ziel passend zum SSH-erreichbaren **Owner-Gerät** ändern):

```ini
MAG_DEPLOY_OWNER_SSH=chris@owner-tailnet-host
MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com
```

`sudo ssh -o BatchMode=yes -o StrictHostKeyChecking=yes chris@owner-tailnet-host true`
muss ohne Passwort, Interaktion oder unbekannten Hostkey funktionieren.
Den SSH-Hostkey **vorher** auf MAG getrennt prüfen und in
`/root/.ssh/known_hosts` vertrauenswürdig hinterlegen. Kein
`StrictHostKeyChecking=no`. Wenn der Owner-Probe noch nicht verfügbar ist,
**nicht deployen**; der Preflight bricht ohne Serviceänderung ab. Das ist
Absicht und schützt gegen den erneuten Totalausfall.

### Schritt 2: nginx/TLS und Native Whois vorbereiten

Auf MAG aus dem neuen PR-Checkout:

```bash
sudo bash ops/private-access/deploy-hook.sh --no-reload
sudo nginx -t
sudo bash ops/private-access/deploy-nginx.sh preflight
```

Benötigt werden `tailscaled`, die IP `100.123.114.16`,
gültige Zertifikate unter `/etc/mag-nginx/tls/{cert,key}.pem`,
ein echtes `/var/lib/magbot/room/access.json` mit Owner-Login,
ein `magroom` mit `/_internal/tailscale/whois` sowie das extern
erreichbare Owner-Gerät. Der Preflight ist read-only. Er stellt zusätzlich
sicher, dass das bisher laufende Binary unbekannte Whois-Anfragen mit 403
verweigert. Fehlende OAuth-Credentials **blockieren nicht**.

### Schritt 3: Optionales OAuth-Secret nur für Gast-Einladungen

In Tailscale Admin → **Trust credentials → OAuth clients** einen Client
`MAG Guest Manager` mit ausschließlich `auth_keys` und `devices:core`,
auf `tag:mag-guest` beschränkt, erstellen. Credentials **nie**
in Git, Logfiles, Terminalargumente, Umgebungsvariablen oder Tickets
kopieren.

```bash
sudo install -d -o root -g root -m 0700 /etc/credstore
sudo install -o root -g root -m 0600 /dev/null /etc/credstore/mag-guest-oauth
sudoedit /etc/credstore/mag-guest-oauth
sudo chown root:root /etc/credstore/mag-guest-oauth
sudo chmod 0600 /etc/credstore/mag-guest-oauth
sudo stat -c '%U:%G %a %n' /etc/credstore/mag-guest-oauth
```

Dateiinhalt **reines JSON**, ohne Shell-Zitate, echte Werte nur auf MAG:

```json
{"client_id":"<TAILSCALE_CLIENT_ID>","client_secret":"<TAILSCALE_CLIENT_SECRET>"}
```

Die schon bestehende Unit-Zeile `LoadCredential=mag-guest-oauth` lädt
diesen Credential-Store-Namen sicher zur Dienstaktivierung in
`$CREDENTIALS_DIRECTORY/mag-guest-oauth`. **Wenn die Datei fehlt,
startet der Raum trotzdem.** Die Owner-Gästeliste zeigt „OAuth fehlt:
Einladen deaktiviert“; alle bestehenden normalen Raum-/Owner-/Box-/lokalen
Browseraktionen bleiben erreichbar. Es erfolgt kein Fallback mit
unsignierten Gast-Identitäten. Nach dem Anlegen genügt das nächste
`mag-120`-Deployment bzw. ein gezielter Restart **nur dieser Unit**;
`mag-llm` bleibt in Betrieb.

### Schritt 4: Rollout und Rückfall

```bash
# erst wenn Schritte 1+2 fehlerfrei sind:
sudo bash ops/deploy.sh
# optional: nur bereits betriebsbereites nginx idempotent reparieren:
sudo bash ops/private-access/deploy-nginx.sh standalone
```

Deploy-Reihenfolge: Preflight → Room/Unit/nginx-Snapshots →
Release-Staging/Queue-Drain → neues nginx-Config testen →
nginx reload → **echter Owner-Probe vor Raum-Restart** →
nur `mag-120` neu starten → `/readyz` → erneut **echter Owner-Probe**
(`/api/state` muss `you.role=owner` **und** genauen Login zeigen,
`/` muss 200 sein) → Commit. Bei 403/5xx/Timeout oder falscher Identität
wird automatisch die vorige Room-Version **samt ursprünglicher nginx-Site**
wiederhergestellt. Alle nginx-Vorgängerdaten bleiben im jeweiligen
`/var/backups/mag-stack/<release-id>/` für manuellen
`ops/rollback.sh <release-id>` erhalten. `mag-llm` wird **nie**
angehalten oder neu gestartet.

Bei geblocktem GitHub-Actions-Billing lokal prüfen:
```bash
bash -n ops/deploy.sh ops/rollback.sh ops/private-access/*.sh
python3 mag-room/tests/test_whois_deploy.py
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
python3 mag-room/tests/test_native.py
python3 mag-room/tests/test_tailscale_auth.py
python3 mag-room/tests/test_private_nginx.py
```
Diese Tests ersetzen den externen Owner-Smoke **nicht**. GitHub Actions
sind bei der Abrechnungssperre nicht ausführbar; **keine grünen
CI-Ergebnisse behaupten**, bis Actions wieder läuft.


## Tailscale-Key-Beschreibung (403/400-Folgebefund, 17.10.2026)

Die Tailscale-API erlaubt für `description` nur ASCII-Buchstaben,
Ziffern, Leerzeichen, `-` und `_` mit maximal 50 Zeichen.
`mag_guest_key_description()` entfernt/ersetzt in C++ Zeichen außerhalb
dieser Menge (u. a. Klammern, Umlaute, Emoji, Steuerzeichen) und verwendet
den Prefix `MAG guest ` und ggf. `guest` als Fallback. Der angezeigte
Gastname wird **nicht** umgeschrieben: Er bleibt getrennt im internen
Einladungsregister.

Wenn `POST /api/guests` fehlschlägt, protokolliert der C++-Prozess im
Journal den **Tailscale-HTTP-Status** und ausschließlich eine geprüfte
Fehlermeldung aus dem JSON-`message`-Feld. Unbekannte Upstream-Texte
werden als `unrecognized API message (redacted)` maskiert – keine
rohen HTTP-Bodies, Auth-Keys, OAuth-Tokens oder Gastnamen.

Regression (C++17, offline, ohne API-Credentials):

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4 --target test-guest-description
./build/test-guest-description
```
\n### Sichere Owner-Probe ohne Kernel-TUN\n\nWenn nur `grokbot-box-2` (100.99.255.98) mit Tailscale-Userspace verfügbar ist, unterstützt das Release einen **lokalen** SOCKS5-Tailscale-Proxy als Transport des externen Owner-Checks. Die Box muss dabei weiterhin ein persönliches, ungetaggtes Owner-Gerät und für MAG root sicher per SSH erreichbar sein; andernfalls **kein Deploy**. Vollständige Checkliste: [Owner-Probe Userspace](owner-probe-userspace.md).\n
## Tailscale API 503 and owner invitation email (17 October 2026)

If `POST /api/guests` returns 503 with `Tailscale HTTP 0`,
MAG's audit nftables policy may block `magbot` UID **993** from
`api.tailscale.com:443` even with correct OAuth credentials.

After this commit is deployed, Ali applies ONLY the narrow outbound
exception (v4 + v6), without replacing the MAG audit policy:

```sh
sudo bash ops/audit/install-tailscale-egress.sh
sudo systemctl status mag-tailscale-egress.service --no-pager
sudo nft list chain inet mag_audit output
sudo nft list set inet mag_audit mag_tailscale_api_v4
sudo nft list set inet mag_audit mag_tailscale_api_v6
sudo systemctl list-timers 'mag-tailscale-egress.timer'
sudo -u magbot curl --noproxy '*' -fsS --connect-timeout 5 \
  -o /dev/null -w '%{http_code}\n' https://api.tailscale.com/api/v2/oauth/token
```

An unauthenticated HTTP 4xx is acceptable as a **network** smoke
test; `HTTP 0`, timeout, or TLS failure is not. Ensure libcurl
validates the certificate/hostname. The firewall limits UID+port+IP;
it **cannot intrinsically verify an HTTPS hostname**, especially on
shared CDN addresses. The DNS-refresh oneshot runs every 10 minutes
and re-applies immediately after audit nftables reload.

### Guest invite

`POST /api/guests` creates a one-time tagged device auth key and a URL
`https://mag.stardenburg.de/#invite=…&key=…`. MAG does not send email
and does not call Tailscale `user-invites`. Chris copies `invite_url`
and delivers it himself. The URL contains the enrollment key. Key
lifetime is 24 hours. The device is limited by the `tag:mag-guest`
claim path.

libcurl does not read `CURL_CA_BUNDLE`. Guest OAuth and auth-key calls
pass that path, or `SSL_CERT_FILE` when the bundle variable is unset,
as `CURLOPT_CAINFO`, so the audit proxy CA is trusted. Failures log
`curl_easy_strerror` next to the HTTP status. HTTP 0 means TLS failed
before a status line.

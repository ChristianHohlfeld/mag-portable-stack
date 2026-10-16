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

Nichts davon startet `mag-llm` neu. Der Modellserver bleibt unangetastet.

1. Raum idle lassen. Release wie sonst mit `ops/deploy.sh` (nur `mag-120`,
   nicht `mag-llm`). Vor nginx-Umstellung bleibt der bisherige Eingang aktiv. Danach muss jeder externe Aufruf mag-whois durchlaufen.
2. Owner-Datei anlegen, **bevor** Caddy neu lädt. Login von Chris holen mit
   `tailscale whois <seine-tailscale-ip>` (Feld LoginName), nicht raten:

   ```json
   {
     "owners": ["LOGIN_AUS_WHOIS"],
     "owner_name": "Chris",
     "permissions": {
       "offline": ["owner"],
       "stop": ["owner"],
       "jobs": ["owner"],
       "memory": ["owner"],
       "reset": ["owner"],
       "delete": ["owner"],
       "autotalk": ["owner"],
       "connectors": ["owner"],
       "browser": ["owner"]
     }
   }
   ```

   Pfad: `/var/lib/magbot/room/access.json`, Besitzer `magbot`, Modus `0640`.
   Nicht ins Git. Vorlage: `ops/private-access/access.json.example`.
   Ein Recht für Gäste freigeben heißt `"guest"` in die Liste setzen.
   Die Datei wird bei Änderung neu gelesen, dafür ist kein Neustart nötig.
3. `sudo ops/private-access/install.sh` — installiert `mag-room.nginx.conf`, `mag-whois` und
   das Beispiel ab, startet `mag-whois` (127.0.0.1:8765, LocalAPI-Socket)
   und lädt **nur** nginx neu. Nicht `mag-llm`, nicht von Hand
   `mag-120` noch einmal, wenn `deploy.sh` das schon getan hat.
4. ACL aus `ops/tailscale/policy.hujson` in der Tailscale-Konsole mergen.
   Prüfen, dass Gäste weder `tcp:22` noch `*` als Ziel haben.
5. Kurz testen: Chris sieht `Besitzer · Chris` und die Admin-Knöpfe. Ein
   Gast sieht `Gast · …`, kann schreiben, bekommt auf Offline/Stop/Jobs 403.
   Fällt `mag-whois` aus, antwortet nginx mit einem Fehler (kein stiller
   Owner-Zugang von außen). Chris erreicht den Raum dann weiter per
   `curl http://127.0.0.1:8766/` auf mag.

Rollback des Eingangs: `sudo ops/private-access/rollback.sh` (vorige nginx-Site wiederherstellen; `tailscale serve :8446` bleibt). Room-Binary zurück mit
`ops/rollback.sh`. `access.json` bleibt liegen und enthält nur den Login.

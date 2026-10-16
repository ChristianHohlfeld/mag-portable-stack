# Secrets aus den Logs entfernen und rotieren

Befund: Browser-Token und die UDP-Schlüssel der Agent-VMs stehen im Klartext im Journal.
Wer das Journal lesen kann (Gruppen `systemd-journal`, `adm`, root), hat damit Zugriff auf die
Browser-Steuerung und kann sich in die VM-Verbindung einklinken. Deshalb: **rotieren, nicht nur löschen.**

Reihenfolge (alles als root, mag-120 gestoppt):

1. **Bestandsaufnahme (zeigt keine Werte)**
   - aktuelle (alte) Werte in `/etc/mag-audit/secrets.list` eintragen, eine Zeile pro Secret,
     z. B. `old_browser_token=…`, `old_udp_key_agent1=…`, `old_udp_key_agent2=…`
   - `sudo ops/security/secrets-scan.sh` → welche Unit wie oft, und welche Codestelle bzw.
     Unit-Zeile die Werte ausgibt (typisch: Token als Kommandozeilenargument oder ein
     `print`/`echo` im VM-Bootstrap in `ops/`).

2. **Ursache beheben** (dauerhaft, im Repo):
   - Secrets nie als Argument in `ExecStart=` oder als `Environment=` – argv ist für alle
     Nutzer per `ps` sichtbar und landet bei Fehlermeldungen im Journal.
     Stattdessen systemd-Credentials:
     ```ini
     # mag-120.service.d/30-credentials.conf
     [Service]
     LoadCredentialEncrypted=browser-token:/etc/mag-120/credentials/browser-token.cred
     LoadCredentialEncrypted=udp-key-agent1:/etc/mag-120/credentials/udp-key-agent1.cred
     LoadCredentialEncrypted=udp-key-agent2:/etc/mag-120/credentials/udp-key-agent2.cred
     ```
     Der Prozess liest sie aus `$CREDENTIALS_DIRECTORY/<name>` (nur für den Dienst lesbar).
     Erzeugen: `systemd-creds encrypt --name=browser-token - /etc/mag-120/credentials/browser-token.cred`
   - die von `secrets-scan.sh` gefundenen `print`/`echo`/Log-Zeilen entfernen bzw. maskieren
     (nur die letzten 4 Zeichen ausgeben).
   - Bis das im Code ist, verwirft `LogFilterPatterns` in `mag-120.service.d/20-audit.conf`
     verdächtige Zeilen (benötigt systemd ≥ 253, prüfen mit `systemctl --version`).

3. **Rotieren**
   - Browser-Token: neuen Wert erzeugen (`openssl rand -base64 32 | tr -d '=+/'`), im
     Browser-Dienst der VMs und im mag-120-Credential setzen.
   - UDP-Schlüssel der Agent-VM (magbot-agent1): neues Schlüsselpaar mit dem Werkzeug, das die Build-Skripte in
     `ops/` verwenden (bei WireGuard: `wg genkey | tee priv | wg pubkey > pub`), Peers auf beiden
     Seiten aktualisieren, alte Peers entfernen.
   - neue Werte zusätzlich in `/etc/mag-audit/secrets.list` eintragen (`browser_token=…`,
     `udp_key_agent1=…`) → das Audit-Gateway und der Web-Proxy ersetzen sie überall durch
     `[SECRET:name]`, falls ein Bot sie je in einen Prompt oder eine Web-Anfrage kopiert.

4. **Altlasten löschen**
   - `sudo ops/security/journal-purge.sh` (Export mit maskierten Werten, Vorschau)
   - `sudo ops/security/journal-purge.sh --yes` (Journal leeren)
   - Treffer aus dem letzten Abschnitt von `secrets-scan.sh` (z. B. `/var/log/syslog*`) ebenfalls
     bereinigen oder löschen.

5. **Kontrolle**: mag-120 starten, eine Stunde laufen lassen, `secrets-scan.sh` erneut → keine Treffer
   für die neuen Werte.

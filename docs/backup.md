# Security notice — 17 October 2026

Local restic now runs with `--insecure-no-password` (restic >=0.17).
**No password prompt or password file exists after migration.**
However, the encrypted master key is effectively unprotected: **anyone
who can read the repository can recover its contents**, including
Tailscale state and application secrets. Restrict repository access to
root and trusted physical host administrators; do not send this copy to
AWS (AWS remains disabled). This is an explicit reduction in security.

Existing password-protected repositories are NEVER destroyed/reinitialized
by `install.sh`. Before applying this release:

```sh
# Install pinned/restic SHA256-verified binary; does not modify backup data:
sudo bash ops/backup/install-restic-0181.sh
# Restic >=0.17 confirmed.
sudo bash ops/backup/migrate-passwordless.sh
# Once successful (old keys removed, integrity and restore verified):
sudo bash ops/backup/install.sh
# Only then securely remove old password file and backup copies on MAG.
sudo rm -f /etc/mag-backup/restic-password
```

The migration adds a zero-password key using
`restic key add --new-insecure-no-password`, tests the new key with
`restic --insecure-no-password`, removes the old key(s) by verified ID,
then performs `restic check` and a temporary restore before declaring
success. On any error it fails closed without deleting repository data.
A missing repository is initialized passwordlessly on the next backup.

Room clear is no longer held hostage by the full restic archive.
The native undo snapshot is written BEFORE the room clears, and
`mag-backup-clear.timer` coalesces repeated clears and archives that
snapshot asynchronously. During the interval before restic finishes,
the native undo directory is the ONLY recovery copy. Daily and
pre-deploy restic snapshots remain synchronous as before.

---

# Vollständige Einzelarchive (mag-stack 4.0)

Jeder Lauf legt ein **eigenes** passwortloses restic-Repository unter
`/var/backups/mag-full/<Zeit>-<daily|deploy|clear>/` an. Es gibt keine
inkrementelle Kette: dieses eine Verzeichnis genügt für einen Restore.
Enthalten sind Raum, Gitea-Datenbank und Repositories, Konfiguration,
Zertifikate und Pages, sofern die Pfade existieren. Nicht enthalten sind
GGUF, VM-Images und Build-Verzeichnisse. Vor dem Schreiben muss auf dem
Dateisystem mindestens `MAG_BACKUP_MIN_FREE_MIB` (Standard 4096) frei sein,
sonst bricht der Lauf ab und löscht nichts. Nach dem Snapshot folgen
`restic check`, ein Test-Restore und der SHA256-Vergleich. Erst danach
gilt das Archiv als verifiziert. Gelöscht wird dann höchstens so, dass die
neueste verifizierte Archiv (`MAG_BACKUP_KEEP`, Standard 1) und das
Pre-Deploy-Archiv des aktuellen Releases bleiben. Der letzte gute Stand bleibt immer. Ein Fehlschlag löscht
nur das unvollständige neue Verzeichnis.

Die Abschnitte weiter unten zur gemeinsamen restic-Kette (`forget`/`prune`,
7/4/6) beschreiben das alte Modell und gelten nicht mehr. AWS bleibt aus.
Das alte `/var/backups/mag-restic` und `/var/backups/restic` werden von
diesem Lauf nicht angefasst.

```bash
sudo bash ops/backup/restore.sh --part all --target /var/tmp/mag-restore
```

# MAG-Backup und Disaster Recovery (17. Oktober 2026)

## Umfang und Grenzen

**Werkzeug:** restic >=0.17, AES-256-kodiert (ohne wirksamen Passwortschutz), dedupliziert, keine neue
Server-Runtime. `mag-llm` wird weder gestoppt noch neu gestartet.

Sicherung: Raum unter `/var/lib/magbot/room` einschließlich Owner/Gast-Rollen,
Chats, Uploads, Queue, Jobs und Connector-Zugängen; Gitea-Daten/Repos inklusive
`magbot-agent1/memory.git` und SQLite-Online-Backup; Pages-Webroots und
Metadaten; `/etc/gitea`, `/etc/mag-pages`, `/etc/mag-acme` (lego,
Zertifikate, acme-dns), `/etc/mag-nginx`, `/etc/nginx`, `/etc/magbot`,
`/etc/default/mag-room`, `/etc/mag-audit`, `/var/lib/mag-audit`,
`/etc/mag-llm`, MAG-/Gitea-Systemd-Units samt Drop-ins,
`/var/lib/mag-deploy`, `/var/lib/mag-private-cutover`,
`/home/chris/llama-templates`, `/usr/local/lib/mag-deepseek`.
Crontabs (`/etc/crontab`, `/etc/cron.*`, `/var/spool/cron/crontabs`)
einschließlich S23-/chris-Aufgaben sowie Tailscale-Zustand
(`/var/lib/tailscale` einschließlich Node-Key) liegen **im lokalen
passwortlosen Restic-Repository**, niemals im Git-Repository. Zugriff darauf
muss mittels root-only Dateirechten geschützt werden.

Release-ID und `current`-Verweis sowie Modell-Verifikationsstempel werden
dokumentiert. Der Backup-Repository-Schlüssel liegt **nicht** im Backup
(selbstrettendes Passwort wäre keine Wiederherstellungsmethode).

**Ausgeschlossen:** `*.gguf`, Whisper-Modell-Binaries,
`/var/lib/mag-llm/slots` (KV), VM-QCOW2 (laufende VMs nicht konsistent online
kopierbar), alte
restic-/Gitea-Tar-Backups, alte Releases, Cache, Gitea-Indexer/Sessions/
Queues/TMP. Das ist **kein vollständiges Image der VM-Overlays**: für
Wiederanlauf müssen Bot-VMs neu provisioniert werden; persistente Gitea-Memory
ist gesichert. Für eine vollständige Bit-für-Bit-Wiederherstellung der
laufenden Browser-/VM-Profile braucht es zusätzlich ein geplantes,
quiesced Libvirt-Overlay-Backup; diese Inkonsistenz darf nicht verschwiegen
werden.

Gitea wird nicht gestoppt. SQLite wird mittels `.backup` gesichert; Repo-
Dateien werden kurz kopiert. Raum-Dateien werden vor dem restic-Lauf kurz in ein
privates Staging kopiert, bei Veränderung bis zu drei Mal versucht und bei
weiterer Abweichung **fail-closed** abgebrochen. Das ist dateikonsistent, aber
ohne gemeinsame Anwendungssperre keine globale Datenbanktransaktion zwischen
Chat, Uploads und VM-Status. Kein `fsfreeze` der einzigen Root-Platte.

## Standorte, Zeitplan, Aufbewahrung

MAG hat **nur eine SSD/HDD** (`/dev/sda`, ext4 `/`), keine zweite Platte.
`/var/backups/mag-restic` ist daher schneller **lokaler Wiederherstellungs-
Cache, kein zweites Ausfallmedium**. Für 3-2-1 muss S3 bzw. eine physisch
getrennte Platte aktiv sein.

Nur nach `MAG_BACKUP_OFFSITE_ENABLED=1` UND sicherer `/etc/mag-backup/aws.env` aktiviert sich
eine **immutable Offsite-Kopie** jedes vollständigen verschlüsselten restic-
Repositories nach
`s3://mag-backup-595505951010-euc1/archives/<UTC>-<snapshot>/`.
S3: `eu-central-1`, Bucket-Object-Lock **COMPLIANCE 30 Tage** als Default,
Versionierung, Block Public Access, SSE-S3, IAM nur für diesen Bucket.
Stabschef richtet Bucket und IAM-User im Konto 595505951010 erst
nach Chris' ausdrücklichem Ja ein; keine AWS-Ressourcen werden im PR erzeugt.
`latest.json` bezeichnet das letzte **fertig hochgeladene** Archiv.
**Wichtig:** Nicht `restic -r s3:... backup/forget/prune` in einen
30-Tage-Object-Lock-Bucket schreiben: restic benötigt kurzlebige Locks und
Löschrechte; mit COMPLIANCE würde es hängen oder korrumpiert wirkende
Sperren hinterlassen. Stattdessen immutable Kopie des *fertigen* lokalen
Restic-Repos; Offsite-Restore holt dieses Archiv herunter und benutzt dann
lokal `restic restore`. Das dupliziert deduplizierte Daten täglich; mit
großen Datenbeständen müssen Volumen und Kosten neu bemessen werden.

Tag `daily`: 7 Tages-/4 Wochen-/6 Monatsstände (`forget` + `prune`
wöchentlich). `hook-deploy` und `hook-clear`: je 14 Tage. S3 ist
WORM-geschützt für **mindestens 30 Tage**; Bucket-Lifecycle für
`archives/` aktuell etwa **35 Tage** plus nichtaktuelle Versionen später
ablaufen lassen. Lokale Langzeit-Monatsstände bleiben nach 35 Tagen nur
lokal; **S3 schützt sie nicht dauerhaft**. Die aktuelle monatliche 6-Monats-
Retention ist Offsite daher nur in den jeweils jüngsten Vollarchiven
mit enthalten. Das ist ein Risiko bei vollständigem lokalen Datenverlust
und einer unbemerkten mehrwöchigen Offsite-Störung.

`mag-backup-daily.timer` um **03:30 Europe/Berlin** (Persistent, 0–10 Min
Zufallsverzögerung); wöchentlich Montag 04:30 Retention/Metadaten-Check,
monatlich am 1. um 05:15 Daten-Check mit `--read-data-subset=10%`,
monatlich 06:30 verschlüsselter Test-Restore mit SHA256-Dateivergleich.
Zusätzlich `mag-backup-monitor.timer` stündlich: letzter Erfolg älter
als **26 Stunden** oder expliziter Fehler → Journal-Fehler, Owner-
`GET /api/backup/status` und sichtbare Warnung im Raum.

Die Oneshots laufen als root mit `nice=19`, idle-IO,
`CPUQuota=15%`, `MemoryHigh=512M`, `MemoryMax=2G`,
`MemorySwapMax=0`, `CPUAffinity=14-27 42-55`,
`NUMAPolicy=bind` / `NUMAMask=1` und
`numactl --cpunodebind=1 --membind=1`.
Der aktuelle Modell-Supervisor auf `main` wechselt Q4/IQ3 noch
sequenziell auf NUMA 0. Sollte künftig IQ3 parallel auf NUMA 1 laufen,
darf restic nur die begrenzte CPU-/RAM-/I/O-Kapazität beanspruchen;
ein Backup-Fehler blockiert den destruktiven Hook, nicht IQ3.
Keine Modell-Unit wird durch diese Sicherung geändert. Doppelläufe blockiert `flock`; bei fehlgeschlagenem
Backup scheitert Deploy bzw. Clear, bevor Daten verändert werden.

**Kostenbeispiel, Schätzung:** 30 immutable Archive à 50 MB ergeben ~1,5 GB
S3 Standard, bei illustrativen 0,025–0,03 EUR/GB-Monat
ca. 0,04–0,05 EUR/Monat Storage **zuzüglich** PUT/GET, Versionen,
Transfer und MwSt. Kein verlässlicher Tarif-/Nutzungswert: echte
monatliche Kosten mit S3-Metriken und AWS-Preisliste nach 30 Tagen prüfen.

## Installation/Cutover (Ali, nicht automatisch deployt)

```bash
sudo apt-get install restic sqlite3 jq util-linux numactl awscli
sudo bash ops/backup/install.sh
sudo systemctl list-timers 'mag-backup*'
sudo /usr/local/lib/mag-backup/maintenance.sh status
sudo systemctl start mag-backup-restore-test.service
sudo journalctl -u mag-backup-daily.service -u mag-backup-restore-test.service -n 80
```

`install.sh` erstellt **keine Passwortdatei**. Vorhandene Restic-Repositories
müssen zuerst mit `migrate-passwordless.sh` umgestellt werden; dann wird lokal
erstmals erfolgreich, aktiviert dann Timer und deaktiviert erst danach
`gitea-backup.timer`. Alte Daten **nicht löschen**: `/etc/mag-dr`,
`/var/backups/restic` (55 GB mit Mai-2026-Snapshots),
`/var/backups/gitea` (262 MB). Nach mehreren realen Backup-/Restore-
Erfolgen ausdrücklich archivieren/löschen; Alt-Hilfsskripte, die noch von
`mag-bootstrap-blank-ubuntu`, `mag-apply-iac` oder
`mag-collect-live-state` referenziert werden, dürfen erst nach Prüfung
umgestellt werden.

**AWS:** Stabschef erstellt erst nach Chris' Ja den S3-Bucket mit Object Lock bei Erstellung, Default
COMPLIANCE 30 Tage, Versioning, SSE-S3, Public-Access-Block und Lifecycle.
IAM-Zugriff auf Bucket und `archives/*`, `latest.json`: minimale
`ListBucket`, `GetObject`, `PutObject`,
`GetBucketObjectLockConfiguration`; **keine Delete-Rechte**.
Datei `/etc/mag-backup/aws.env` mit
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_DEFAULT_REGION=eu-central-1`, Rechte root:root 0600.
Ein erster Lauf muss `offsite_ok=true` melden. Fehlende/ungültige
Credentials dürfen **nie** als erfolgreicher Offsite-Lauf gelten.

**Offline für Chris:** Repo-/Datenträgerzugang (kein Restic-Passwort), Bucketname, AWS-Konto/Region,
Wiedererlangung der minimalen IAM-Zugangsdaten (getrennt und nicht im Repo),
GitHub-Repo-Zugang und Tailscale-Admin-Zugang. Offline-Schlüsselprobe durchführen.
Keine Passwörter/Kopien in GitHub oder Gitea committen.

## Teil-Restore und echter Wiederanlauf

Zuerst **nur in ein Zielverzeichnis**, ohne laufende Dienste zu verändern:

```bash
sudo bash ops/backup/restore.sh --part room --target /var/tmp/mag-restore
sudo bash ops/backup/restore.sh --part gitea --target /var/tmp/mag-gitea-test
sudo bash ops/backup/restore.sh --part pages --target /var/tmp/mag-pages-test
sudo bash ops/backup/restore.sh --part config --target /var/tmp/mag-config-test
sudo bash ops/backup/restore.sh --part cron --target /var/tmp/mag-cron-test
sudo bash ops/backup/restore.sh --part tailscale --target /var/tmp/mag-tailscale-test
sudo bash ops/backup/restore.sh --part all --target /var/tmp/mag-all-test
sudo bash ops/backup/restore.sh --repo offsite --part all --target /var/tmp/mag-offsite-test
```

Der Staging-Restore liegt im Ziel unter dem relativen Originalpfad
(`/var/tmp/mag-restore/var/lib/magbot/room`). Vor dem Live-Restore:
`mag-120` für Raum und `gitea` für Gitea stoppen, **nicht mag-llm**.
`--apply` schreibt ausgewählte Pfade an den Originalort und verweigert
Raum-/Gitea-Restore bei laufenden Units. Danach Besitzrechte, Service-
Konfiguration und Repositories prüfen, gezielt Dienste starten.
Niemals einen Zielordner aus dem Internet übernehmen.

**Bare Metal auf neuer Hardware:**

1. Ubuntu, Pakete, libvirt/KVM, nginx, Gitea, restic, sqlite3, jq, numactl
   installieren. Datenträger und separate Offsite-Verbindung prüfen.
2. GitHub-Repo `ChristianHohlfeld/mag-portable-stack` klonen und
   versionsgleich `install.sh`/Stack-Voraussetzungen herstellen.
   Nicht mit einem leeren Raum erfolgreich starten und danach überschreiben.
3. Kein Restic-Passwort nötig: `restic --insecure-no-password` (Version >=0.17).
   `aws.env` nur nach gesonderter Offsite-Freigabe; `ops/backup/install.sh`
   **noch nicht** ausführen (er würde auf frischer Hardware einen leeren Raum sichern).
   Stattdessen `/usr/local/lib/mag-backup`-Skripte aus dem Repo
   installieren oder direkt mit den lokalen Skripten und korrekt gesetzten
   `MAG_BACKUP_CONFIG`-Variablen arbeiten.
4. Offsite-Archiv laden oder lokales Repository einhängen; zuerst
   `--part all --target /var/tmp/recovered` und
   `sha256sum -c` aus dem Snapshot ausführen.
5. `--apply` erst nach bewusster Auswahl, dann `systemctl daemon-reload`.
   Gitea-DB/Repos/Secret-Key, Pages/TLS, Owner-Zugriff und
   `/opt/mag-stack/current`-Release-ID prüfen; Build aus Repo,
   neues Versioned-Release installieren und nginx -t/reload.
6. Modelle **nicht** im Backup: Q4 (fünf Shards) und IQ3 (vier Shards)
   werden auf frischer Hardware mit den gepinnten SHA256-Manifesten
   `ops/backup/models.sha256` und `ops/backup/models-iq3.sha256`
   wiederhergestellt. Nur wenn keine Modell-Unit läuft:
   `sudo bash ops/backup/bootstrap-models.sh download all`;
   anschließend `sudo bash ops/backup/bootstrap-models.sh verify all`.
   Quelle IQ3:
   https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF/tree/main/UD-IQ3_XXS.
   Das Skript startet keine Modelle.
   Whisper `.bin` separat downloaden und gegen vertrauenswürdigen
   Hersteller-Hash prüfen. `mag-llm` nur auf Ersatzhost nach Modellprüfung
   neu starten; auf bisherigem MAG niemals durch den Backup-Cutover.
7. Tailscale-Zustand ist optional wiederherstellbar: vor einem
   `--part tailscale --apply` `tailscaled` auf dem Ersatzgerät stoppen.
   Den alten MAG-Host endgültig außer Betrieb nehmen oder dessen Node-Key
   deaktivieren: niemals dieselbe Tailnet-Identität parallel benutzen.
   Alternativ neues Tailnet-Gerät per `tailscale up` freigeben.
   Cronjobs vor Aktivierung auf der neuen Hardware prüfen, insbesondere S23.
   Zertifikate und lego/acme-dns-Konten vor Live-Traffic prüfen.
8. Neuer Gitea-/Raum-/Pages-Smoketest und Test-Restore, dann Backup-Timer
   installieren/aktivieren und ersten Snapshot protokollieren.

Prüfprotokoll für Ali: Snapshot-ID, Bytes, Laufzeit, Status
`offsite_ok`, Monatstest `last_restore_test_ok`, Journal-Checks. Kein
CI-Test ist Ersatz für den echten Host-Probelauf.


### Recovery der Owner- und Gästezugänge

Das passwortlose Restic-Archiv enthält `/etc/mag-stack` für die Owner-Deploy-Probe
sowie, falls vorhanden, `/etc/credstore/mag-guest-oauth` für die Gästeverwaltung.
Es existiert **kein Restic-Passwort**; nach Bare-Metal-Restore den
Dateisystemzugriff auf das Repository strikt auf root begrenzen (0700).

## October 17 2026 – LOCAL-ONLY release gate

Chris has approved local encrypted backups; AWS remains stopped. The
default is `MAG_BACKUP_OFFSITE_ENABLED=0`, even if a stale `aws.env`
exists. Local `restic` needs no AWS CLI, bucket access, credentials,
`aws.env` or network. `ops/backup/install.sh` creates no password,
runs a local restic backup, then enables backup timers. The
generic `ops/deploy.sh` requires this local timer and synchronously
runs `mag-backup-deploy.service` before changing a release. Pages
directories are optional until Pages has been activated.

On MAG, configure `/etc/mag-backup/config.env` (0600) explicitly with
`MAG_BACKUP_OFFSITE_ENABLED=0`, then:
```sh
sudo bash ops/backup/install.sh
sudo systemctl is-enabled mag-backup-daily.timer
sudo systemctl start mag-backup-deploy.service
sudo /usr/local/lib/mag-backup/maintenance.sh status
```
Expect `healthy=true` and `offsite_ok=false`. Do NOT set
`MAG_BACKUP_OFFSITE_ENABLED=1` until Chris separately authorizes AWS.

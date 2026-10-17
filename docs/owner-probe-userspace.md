# MAG Owner-Probe ohne Kernel-TUN: grokbot-box-2

> **Stand nach #53:** Der externe Owner-Probe ist optional. Ohne
> `/etc/mag-stack/deploy-owner-probe.conf` prüft jeder Deploy lokal, ob nginx
> und Raum auf 443 antworten (200/401/403), und rollt sonst nginx und Raum
> zurück. Mit konfiguriertem Probe-Gerät bleibt die externe Owner-Prüfung
> Pflicht, sobald sich die nginx/whois-Konfiguration ändert. `grokbot-box-2`
> ist auf MAG nicht mehr als Probe vorgesehen (Datei entfernen). Der Rest
> dieses Dokuments beschreibt die Einrichtung, falls später wieder ein
> Probe-Gerät gewünscht ist. Es gibt weiterhin keine gefälschten
> Whois-Header und keinen `MAG_DEPLOY_FORCE` für diese Prüfung.

## Entscheidung / Sicherheitsregel

Der fehlende Owner-Probe darf **nicht** durch lokale HTTP-Aufrufe,
künstliche `X-Tailscale-*`-Header oder einen festen `owner`-Rollenwert
ersetzt werden. Auch eine getaggte Agenten-Box ist **kein**
Tailscale-Benutzergerät. Der einzige hier unterstützte Ersatz für einen
klassischen Owner-Desktop ist ein **tatsächlich mit
`chris.hohlfeld@gmail.com` angemeldeter, nicht getaggter** Tailscale-Client,
der die zwei Owner-Smoke-Requests selbst an MAG schickt.

Kandidat: `grokbot-box-2` mit Tailnet-IP `100.99.255.98`. Diese Adresse
ist **kein Beleg** für eine Owner-Identität. Prüfen, nicht umtaggen oder
mit einem Gast-Key versehen. Die Box darf nicht eigens für den Probe zu
einem Besitzergerät hochgestuft werden, wenn sie als unbetreuter
Agenten-Host nicht dafür geeignet ist. In diesem Fall bleibt der Deploy
**gesperrt**; Chris verwendet später einen persönlichen Desktop als Probe.

Der einzige Unterschied zu einem normalen Owner-Probe ist der Netzwerkweg:
Tailscale-Userspace stellt einen **lokal gebundenen SOCKS5-Proxy** bereit.
Der externe Besitzer-Smoke ruft über diesen Proxy den echten
`https://mag.stardenburg.de/api/state` und `/` auf. nginx und das
native C++-Whois prüfen unverändert den tatsächlich eintreffenden Peer.
Liefert `/api/state` nicht gleichzeitig `you.role=owner` und
`you.login=chris.hohlfeld@gmail.com`, bricht das Deployment ab
bzw. rollt zurück.

## 1. Read-only Box-Inventur

Die folgenden Befehle laufen auf **grokbot-box-2**, nicht auf MAG:

```bash
tailscale ip -4
tailscale status --json | jq -r '
  . as $ts |
  "online=\($ts.Self.Online) tags=\(($ts.Self.Tags // [])|join(",")) " +
  "login=\(([ $ts.User[] |
    select(.ID == $ts.Self.UserID) | .LoginName ][0]) // "<unknown>")"
'
ps -ef | grep '[t]ailscaled'
ss -ltnp | grep '127.0.0.1:1055' || true
```

**Vorbedingungen:** IP `100.99.255.98`, `Online=true`,
`Tags=[]`, persönlicher Login `chris.hohlfeld@gmail.com`. Ein
`tag:...` oder falscher Login ist **ein harter Stopp**. Außerdem muss
ein bestehender SSH-Zugriff **von root@mag zur Box** ohne Passworteingabe
möglich sein. Der SSH-Hostkey muss vorher unabhängig geprüft und unter
`/root/.ssh/known_hosts` gepinnt sein. `StrictHostKeyChecking=yes`
bleibt Pflicht. Ein SOCKS5-Proxy macht den Box-eigenen Tailnet-Port 22
**nicht automatisch** eingehend erreichbar: SSH kann über einen bereits
vorhandenen vertrauenswürdigen privaten Netzweg erfolgen. Keine
öffentlichen Ports öffnen, keine SSH-Policy lockern.

## 2. SOCKS5 auf der bestehenden Box-Tailscale-Instanz

In der bestehenden `tailscaled`-Konfiguration
`--tun=userspace-networking` **mit**
`--socks5-server=127.0.0.1:1055` kombinieren. Nicht eine zweite
Tailnet-Instanz starten, keine Auth-Keys erzeugen, und die laufende
Box-Verbindung nicht ohne vorheriges Betriebsfenster neu starten.

Beispiel für die Flag-Kombination (an die bestehende Unit/Startmethode
anpassen, **kein** blind auszuführender Neustartbefehl):

```text
tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055
```

Der Proxy muss ausschließlich auf `127.0.0.1` lauschen. Danach von
der **Box selbst**:

```bash
ss -ltn | grep -F '127.0.0.1:1055'
curl -q --silent --show-error --connect-timeout 3 --max-time 12 \
  --noproxy '' --socks5-hostname 127.0.0.1:1055 \
  -o /dev/null -w '%{http_code}\n' https://mag.stardenburg.de/api/state
```

Vor dem Whois-Fix sind 403 bzw. fehlendes korrektes Owner-JSON gerade
der Grund für das Deployment; einen gefälschten 200er **niemals**
als Erfolg werten. Für den automatischen Smoke wird erst nach
nginx-Cutover eine echte, vom C++-Whois bestätigte Owner-Antwort verlangt.

## 3. Root-only Konfiguration auf MAG

Die OAuth-Credential `/etc/credstore/mag-guest-oauth` existiert bereits;
**nicht** anfassen. Auf MAG ausschließlich die neue Probe-Konfiguration
anlegen:

```bash
sudo install -d -o root -g root -m 0700 /etc/mag-stack
sudo install -o root -g root -m 0600 /dev/null \
  /etc/mag-stack/deploy-owner-probe.conf
sudoedit /etc/mag-stack/deploy-owner-probe.conf
```

Inhalt (SSH-Ziel muss **wirklich** erreichbar sein; kein ungeprüfter
Hostname, kein S23):

```ini
MAG_DEPLOY_OWNER_SSH=chris@<bereits-ssh-erreichbarer-box-host>
MAG_DEPLOY_OWNER_LOGIN=chris.hohlfeld@gmail.com
MAG_DEPLOY_OWNER_IP=100.99.255.98
MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:1055
```

Nur `MAG_DEPLOY_OWNER_SOCKS5=127.0.0.1:PORT` ist erlaubt, keine
öffentliche Proxy-IP und keine zusätzlichen Shell-Argumente. Die
konfigurierte Box-IP muss mit `tailscale ip -4` auf dem SSH-Ziel
übereinstimmen. `tailscale status --json` muss den persönlichen
Owner-Account **und** fehlende Tags bestätigen. Der finale Beleg kommt
dennoch ausschließlich von den beiden HTTP-Anfragen durch nginx und
das C++-Whois.

```bash
sudo chmod 0600 /etc/mag-stack/deploy-owner-probe.conf
sudo ssh -o BatchMode=yes -o StrictHostKeyChecking=yes \
  chris@<bereits-ssh-erreichbarer-box-host> true
sudo bash ops/private-access/deploy-nginx.sh preflight
```

Fehlt die konfigurierte Datei, der sichere SSH-Zugriff, die richtige
Box-IP, das persönliche Tailscale-Login oder der lokale SOCKS5-Proxy,
bleibt der Deploy bewusst **nicht ausführbar** bzw. fällt automatisch
auf die vorherige nginx- und Room-Version zurück. Keine
`MAG_DEPLOY_FORCE`-Umgehung für diesen Gate.

## 4. Nach ausdrücklichem OK von Chris: Einspielen durch Ali

Nur aus einem bereits gemergten, **vollständig CI-grünen** Commit:

```bash
sudo bash ops/private-access/deploy-hook.sh --no-reload
sudo bash ops/private-access/deploy-nginx.sh preflight
sudo bash ops/deploy.sh
```

Der Deploy prüft `/` und `/api/state` sowohl **vor** als auch
**nach** dem `mag-120`-Restart. Fehler, 403, 5xx und falsche
Besitzeridentität führen zum Rollback der alten nginx-Site und
der alten Room-Binaries. `mag-llm` wird nie gestoppt oder
neu gestartet. Die bisherige Tailscale-Gast-Policy, OAuth-Scopes und
die C++-Gast-ACL werden nicht geändert.

## Regression

`python3 mag-room/tests/test_whois_deploy.py` simuliert in einem
Wegwerf-Dateibaum die unveränderte direkte Probe und den neuen
Userspace-SOCKS5-Pfad. Es prüft ausdrücklich ungültige/externe
Proxys, falsche Box-IP, getaggte Geräte, abweichende Gastrolle,
ausgefallenen Proxy und rollback-fähige nginx-Transaktionen.
Keine Python-/Node-Runtime in MAG selbst.

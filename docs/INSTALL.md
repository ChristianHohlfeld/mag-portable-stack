# Installing MAG on a blank host

This is the order that brings a blank Ubuntu 24.04 server to a running MAG.
It uses the scripts in this repository. Steps that no script does are
marked **By hand**. Architecture: [ARCHITECTURE.md](ARCHITECTURE.md). After
the install, day-to-day work is in [OPERATIONS.md](OPERATIONS.md).

Restoring a host from a backup follows the same order; where a step can take
data from a backup, it says so.

## 0. What the host must have

- Ubuntu 24.04 (noble), x86_64.
- Two NUMA nodes, 56 logical CPUs: the physical cores 0-27 run the two
  models, CPUs 28-55 everything else. `ops/backup/install.sh` refuses a host
  without NUMA node 1.
- Enough memory for both models at once (`ops/models/models.json`: about
  17 GB for Lena, 65 GB for Noah) plus 16 GiB per bot VM and 64 GiB that
  `magbot` keeps free (`/etc/magbot/config.json`, defaults in
  `mag-room/native/vm.cpp`).
- KVM (`/dev/kvm`).
- Optional: an AMD MI50 for the MI50 queue. `ops/idol/power-cap.sh`
  addresses it at PCI `0000:05:00.0`.
- A Tailscale tailnet, and the domains `mag.stardenburg.de` and
  `*.bot.stardenburg.de` pointing at the host's Tailscale address, with an
  acme-dns account for the DNS-01 challenge.

The scripts assume the owner account `chris` (model files in
`/home/chris/models`, the MI50 environment in `/home/chris/idol-mi50`, the
checkout in `/home/chris/mag-stack`) and run builds on CPUs 28-55.

## 1. Packages, users, checkout (By hand)

```bash
sudo apt-get install -y build-essential cmake ninja-build pkg-config git curl wget jq \
  libssl-dev libcurl4-openssl-dev libicu-dev \
  qemu-kvm qemu-utils libvirt-daemon-system virtinst cloud-image-utils \
  nginx sqlite3 numactl linux-tools-generic dnsutils openssl \
  poppler-utils tesseract-ocr tesseract-ocr-deu ffmpeg \
  python3-venv python3-numpy python3-scipy python3-pil nodejs
sudo snap install go --classic       # ops/deploy.sh builds mag-room/webrtc with it
```

- The room runs as the system user `magbot`. Create it
  (`sudo useradd --system --home-dir /var/lib/magbot --shell /usr/sbin/nologin magbot`).
  `ops/audit/install-tailscale-egress.sh` checks that its uid is 993, as on
  the current host; pick that uid or adjust the check.
- The libvirt network `default` (virbr0, 192.168.122.0/24) must exist and
  be active; the bot VMs use it.
- Clone the repository as `chris` to `/home/chris/mag-stack`.
- Create the room's state directory and owner list:

  ```bash
  sudo install -d -o magbot -g magbot -m 0750 /var/lib/magbot/room
  sudo install -o magbot -g magbot -m 0640 ops/private-access/access.json.example \
    /var/lib/magbot/room/access.json
  ```

  Put the owner's Tailscale login (as `tailscale whois` reports it) into
  `owners`. When restoring, use `ops/backup/restore.sh --part room` instead
  (step 13).
- Create `/etc/magbot` (root, 0755). `config.json` there is optional and
  overrides the VM limits in `vm.cpp`.
- Optional: `/etc/default/mag-room` (root, 0600) with `BRAVE_API_KEY=…` for
  the bots' `web_search`. Without it the tool reports that search is not set
  up.

## 2. Tailscale

```bash
sudo bash ops/tailscale/install.sh                # or: --state <restored stage>
```

Installs tailscale from `pkgs.tailscale.com` when absent and joins the
tailnet with a key in `/etc/credstore/tailscale.authkey` (root, 0600), or
leaves `tailscale up` to you. When the host is rebuilt from a backup, pass
`--state` with the restored `var/lib/tailscale`: the node keeps its address.
The script warns when the address differs from the one clients use
(`EXPECTED_IP`); `ops/private-access/deploy-nginx.sh` checks the same
address before every deploy.

## 3. Trust roots

```bash
sudo bash ops/trust/install-trust.sh
```

Adds the public ISRG roots to the host trust store and prepares the package
the VMs get. Idempotent.

## 4. Build

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
taskset -c 28-55 ninja -C build
```

The audit installer in the next step needs `build/mag-audit-gateway`.

## 5. Audit layer

```bash
sudo bash ops/audit/install-audit.sh             # dry run: shows every change
sudo bash ops/audit/install-audit.sh --apply
sudo bash ops/audit/install-tailscale-egress.sh
```

`install-audit.sh` creates the user `mag-audit`, detects the VM network
(`detect-vm-net.sh`), installs mitmproxy into `/opt/mag-audit/venv`, the
gateway binary into `/opt/mag-audit/bin`, the CA and VM package into
`/etc/mag-audit`, the units `mag-audit-gateway`, `mag-audit-webproxy`,
`mag-audit-nft` and the rules `/etc/nftables.d/mag-audit.nft`. The room unit
requires all three services. `install-tailscale-egress.sh` needs the
`mag_audit` table loaded and adds the narrow egress to `api.tailscale.com`.

## 6. TLS for the room

```bash
sudo bash ops/private-access/install-lego.sh       # pinned lego, SHA256-checked
sudo install -D -m 0644 ops/private-access/env.conf /etc/mag-acme/lego.env
sudo install -D -m 0755 -t /usr/local/lib/mag-acme \
  ops/private-access/issue-cert.sh ops/private-access/deploy-hook.sh
sudo /usr/local/lib/mag-acme/issue-cert.sh staging     # optional dry run against staging
sudo /usr/local/lib/mag-acme/issue-cert.sh production
```

**By hand:** the acme-dns account (`/etc/mag-acme/acme-dns.json`) and the
`_acme-challenge` CNAME are registered once; no script creates them. When
restoring, take `/etc/mag-acme` from the backup (`--part config`).

`issue-cert.sh production` obtains the certificate and its deploy hook puts
it into `/etc/mag-nginx/tls` and `/etc/nginx/mag-tls`, checks `nginx -t` and
reloads nginx.

Renewal (**By hand** on a blank host; `ops/private-access/install.sh` does
this only in its first-cutover path on a host that already runs a room):

```bash
sudo install -m 0644 ops/private-access/mag-acme-renew.service \
  ops/private-access/mag-acme-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mag-acme-renew.timer
```

nginx itself (**By hand**): `ops/nginx/nginx.conf` is the host's
`/etc/nginx/nginx.conf`, and `ops/nginx/nginx-restart.conf` is a drop-in
for `nginx.service` (`/etc/systemd/system/nginx.service.d/`) that restarts
nginx on failure. The room's vhost and its snippets are installed by every
deploy.

## 7. Bot site certificate

The bots' sites need a wildcard certificate for `*.bot.stardenburg.de` in
`/etc/mag-acme/lego-bot`. **By hand:** the first issuance with lego
(DNS-01, same acme-dns account). After that `ops/bots/issue-wildcard.sh
renew` renews it, and `ops/bots/install.sh` (run by every deploy) installs
the vhost, the publisher and the TLS pair. Without the certificate the
deploy skips the bot sites and says so.

## 8. Golden VM image

```bash
sudo bash ops/vm/build-golden.sh
```

Downloads the pinned Ubuntu 24.04 cloud image (checked by SHA256),
customises it once in a throwaway QEMU boot and writes
`/var/lib/libvirt/images/magbot/base/golden.qcow2`. It does nothing when the
image exists; `--force` rebuilds it and is refused while bot overlays exist.
Google Chrome and Ubuntu updates are not pinnable; their versions are
recorded in `golden.qcow2.manifest`.

## 9. Gitea and the runner

```bash
sudo bash ops/gitea/install.sh
sudo bash ops/runners/install.sh
```

`ops/gitea/install.sh` installs the pinned Gitea 28.1.0 binary, the user
`git`, `/var/lib/gitea`, `/etc/gitea/app.ini` from `app.ini.example` with
secrets generated on the host, and `gitea.service` (127.0.0.1:3000). When
restoring, stop Gitea and restore `--part gitea` before starting it.
`ops/runners/install.sh` installs the pinned Gitea Actions runner as
`mag-runner` and registers it against the local Gitea.

Optional:

```bash
sudo bash ops/gitea/mirror-github.sh   # pull mirror of the GitHub repository
```

It reads its tokens from `/etc/credstore/gitea-mirror.token` and
`/etc/credstore/github-mirror.token` (**By hand**).

## 10. Model runtime and one-time fetches (as `chris`)

```bash
bash ops/models/build-runtime.sh                # pinned CPU-only llama.cpp
taskset -c 28-55 bash ops/stt/bootstrap.sh       # transcribe.cpp and the German Parakeet model
bash ops/tts/bootstrap.sh                        # Supertonic 3, packed to float16
bash ops/scene/bootstrap.sh                      # pinned Blender
bash ops/scene/face/bootstrap.sh                 # MediaPipe face landmarker for Blender's Python
```

Each is idempotent, pinned by SHA256 and writes below `~/.cache/mag-stack`
(the STT model to `~/models/stt`). The deploy installs what they fetched;
a layer whose files are missing is skipped with a warning and the room
works without it (no microphone, the device's own voice, no scenes).

The GGUFs of the bots are not fetched here: `mag-modeld` downloads them
after the first deploy (step 11), one writer per file. A restored host can
copy them back into `/home/chris/models` instead; they are never in a
backup.

## 11. First deploy

```bash
cd /home/chris/mag-stack && taskset -c 28-55 sudo -n bash ops/deploy.sh
```

Preconditions checked by `ops/private-access/deploy-nginx.sh preflight`:
nginx and tailscaled active, the expected Tailscale address present, a
valid certificate in `/etc/mag-nginx/tls`, and `access.json` in the room
state. On a blank host there is no previous room to probe; the new one is
checked through nginx after the switch.

The deploy installs the release under `/opt/mag-stack/releases/<id>`,
`/usr/local/bin/magbot`, the sudo wrappers and their sudoers rule, the room
units, the VM firewall, the model layer (`mag-modeld` starts downloading
the GGUFs now), `mag-stt`, the voices, `mag-scene`, the bot sites, the
health and VM-save timers, and enables everything for boot
(`ops/boot/ensure-boot.sh`). See [OPERATIONS.md](OPERATIONS.md#deploy).

Watch the models come up:

```bash
/usr/local/lib/mag-models/mag-modeld status
```

## 12. Gitea on the tailnet and Funnel

After nginx serves the room:

```bash
sudo bash ops/tailscale/apply-gitea-funnel.sh --dry-run
sudo bash ops/tailscale/apply-gitea-funnel.sh
```

Sets the Tailscale serve config (port 443 as raw TCP with PROXY v2 to nginx
127.0.0.1:4443, Funnel for the host's `ts.net` name), installs the Gitea
vhost, `mag-tailscale-cert.timer` (the `ts.net` certificate) and
`mag-gitea-funnel.timer`, which re-asserts the serve config after a reboot.

## 13. Backups

```bash
sudo bash ops/backup/install-restic-0181.sh   # pinned restic 0.18.1
sudo bash ops/backup/install.sh
```

`install.sh` needs restic, jq, sqlite3 and numactl, NUMA node 1, the Gitea
database and `app.ini`, `/etc/magbot`, `/etc/mag-acme` and `/etc/nginx`. It
installs the scripts to `/usr/local/lib/mag-backup`, the units, takes a
first backup and enables the timers. From then on every deploy takes a
backup first. Restoring from an archive: [OPERATIONS.md](OPERATIONS.md#backups).

## 14. The MI50 queue (optional)

**By hand:** ROCm, and the environment `/home/chris/idol-mi50`: `venv` with
torch 2.7.1+rocm6.3, the Idol SDK in `idol-sdk` with its MI50 patch, and
the weights `weights/idol`, `weights/dinov3-timm`, `weights/flux2vae`. The
TripoSG source and its weights go to `idol-mi50/triposg` (its own
`fetch.sh` lives there, not in this repository).

Then, as `chris`:

```bash
bash ops/imagegen/fetch.sh     # FLUX.2 klein 4B, pinned, resumable
bash ops/video/fetch.sh        # Wan 2.2 I2V-A14B, about 37 GB, pinned, resumable
```

and as root `sudo bash ops/idol/install.sh` (every later deploy runs it
too). It checks the ROCm torch, adds missing pinned wheels without touching
torch, fetches the pinned SAM 2 and Depth Anything V2 weights, prepares the
TripoSG and video environments, installs `mag-idol.service` and its tmpfiles
rule, and starts the worker. The worker's `ExecStartPre` clamps the card's
power cap to 72 W.

## 15. The bots' VMs

Open `https://mag.stardenburg.de/` from a tailnet device. In each bot's
sheet the room creates its VM (`POST /api/bot/<id>/vm`, which runs
`magbot spawn` through `magbot-agent-ctl`): an overlay on the golden image,
its Gitea account, its browser sandbox, its audit proxy and its site.

## 16. Check

```bash
sudo systemctl start mag-stack-check.service && journalctl -u mag-stack-check -n 20
systemctl list-timers 'mag*'
```

`ops/health.sh` checks the room, nginx, tailscaled, the audit gateway and
mag-modeld, and prints each model's state. A model that is still
downloading is a normal state.

## Not covered by a script

- packages, the `magbot` user, the libvirt network, the checkout (step 1);
- the acme-dns account and the first wildcard certificate (steps 6, 7);
- the ACME renewal units and nginx's base configuration on a blank host
  (step 6);
- ROCm and the Idol environment (step 14);
- the phone control daemon `devices/s23` (Chris runs it himself);
- tokens in `/etc/credstore` for the GitHub mirror and Tailscale.

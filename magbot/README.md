# MAGBOT – Ephemeral KVM/Playwright Browser Sandboxes

Separate feature of MAG portable stack. Does **not** replace or restart Ollama, nginx, Open WebUI, ComfyUI or Docker. It runs on the **MAG Ubuntu host**, not inside a container.

## Requirements

- Ubuntu 24.04 with VT-x active, `/dev/kvm` present (KVM host)
- Internet to download the Ubuntu Noble cloud image and Playwright Chromium in each guest
- Root privileges for installation and VM lifecycle
- Approximately 500+ GiB RAM server and sufficient free SSD space. The provided defaults are deliberately conservative; inspect and adapt them to the actual resource headroom.
- `cloud-localds`, `virt-install`, `virsh`, `qemu-img`, Python 3

## Install (download ZIP on Windows and send to MAG)

```powershell
scp "$env:USERPROFILE\Downloads\magbot-stack.zip" mag:~/
```

Then on MAG:

```bash
unzip -o ~/magbot-stack.zip -d ~/
cd ~/magbot-stack
sudo bash install-magbot.sh
sudo magbot doctor
```

If a link or path to the downloaded ZIP differs, adjust the `scp` source.

## Lifecycle

```bash
sudo spawnbot first
sudo magbot status first                  # booting -> installing -> ready; initial setup downloads Chromium
sudo magbot exec first -- /opt/magbot/venv/bin/python /opt/magbot/health.py
sudo magbot logs first                    # bootstrap logs when QEMU guest agent is ready
sudo despawnbot first                     # delete VM definition, image, seed and metadata
sudo magbot status
```

Equivalent: `sudo magbot spawn first`, `sudo magbot despawn first`.

Spawn creates one fresh Ubuntu cloud-image qcow2 overlay, per-VM cloud-init seed ISO, unprivileged `bot` account, local Playwright Chromium installation and QEMU guest agent. No SSH keys or host directories are injected. The `bot` user has no sudo. The VM boots and installs asynchronously. `status=ready` only after a local browser launch succeeded.

## Admission and host protection

Defaults in `/etc/magbot/config.json`:

| Constraint | Default |
|---|---:|
| Concurrent bots | 4 |
| Total virtual CPUs | 8 |
| Total committed bot VM RAM | 24 GiB |
| Minimum host MemAvailable after new VM | 64 GiB |
| Minimum host disk reserve | 100 GiB |
| Bot default VM | 2 vCPUs, 4 GiB RAM, 20 GiB disk |
| Per-bot caps | 4 vCPUs, 8 GiB RAM, 32 GiB disk |
| VM disk I/O | 40 MB/s, 400 IOPS |
| New spawn blocked if 1m host load > 75% of logical CPUs | yes |

RAM is fixed per libvirt VM. CPU quotas and disk I/O limits must be accepted by libvirt or `spawn` rolls back the VM. One exclusive host lock protects admission and teardown against race conditions. Disk admission conservatively reserves the entire virtual disk size for every existing bot. Every config change is applied to new VMs; it does not retroactively modify existing VMs. Do not disable host swap or force overcommit.

The limits reduce resource-exhaustion risk but **cannot guarantee** that SSH/Tailscale remain reachable during unrelated host failures, severe kernel issues, excessive global I/O, guest vulnerability or hardware faults. For stronger guarantees, separate the management plane or use a watchdog/IPMI recovery mechanism.

## Networking and trust boundary

Guests use the standard libvirt `default` NAT network. It is not directly published to external interfaces and needs no host-port forwarding. **This does not provide private-network egress isolation:** guests may be able to reach the host, LAN or each other over NAT. Do **not** run untrusted autonomous browser/agent jobs with sensitive host services reachable until an audited egress policy has been added. Browser automation is unprivileged inside each KVM VM, but bot commands executed by an operator may access the guest network.

## Deletion semantics

`despawnbot ID` destroys the running domain, undefines it and removes only that verified bot's files under `/var/lib/libvirt/images/magbot/bots/ID`. It does not remove shared Ubuntu base image, libvirt network, other bots, Ollama, or anything outside the bot's dedicated directory. It is **logical deletion**; physical forensic irrecoverability of data on SSD, backups, swap or snapshots is not guaranteed.

## Diagnostics

```bash
sudo magbot doctor
sudo magbot status
sudo virsh -c qemu:///system list --all
sudo virsh -c qemu:///system net-list --all
sudo magbot logs first
```

Do not run `apt autoremove`, reboot or update the base cloud image while guests are running. The image is deliberately immutable because overlays reference it. For full stack regression, verify Ollama, SSH, and Tailscale independently.

## Tests

```bash
python3 -m unittest discover -s tests -v
bash -n install-magbot.sh
python3 -m py_compile magbot.py
```

Local unit tests use mocked libvirt calls. They cannot prove MAG's actual `/dev/kvm`, libvirt compatibility, network routing, or browser launch. Only on-host `spawn → ready → exec → despawn` completes that verification.

## One-command disposable on-host E2E

```bash
cd ~/magbot-stack
sudo bash smoke-test.sh
```

This temporarily provisions `magbot-smoke`, checks real browser startup via QEMU guest agent, and destroys the test VM and its files via an EXIT trap. Expect several minutes on a fresh install.

The shared Ubuntu cloud image is verified against the official HTTPS SHA256SUMS before use.

## Updating after the initial libvirt network error

An older installer could stop with `network is already active` on German-language Ubuntu.
Reupload the updated ZIP, unzip over `~/magbot-stack`, and rerun `sudo bash install-magbot.sh`.
The installer is idempotent: it keeps `/etc/magbot/config.json`, an existing verified cloud image and existing bots.
No host reboot is required.

## v1.0.2: diagnosing smoke-test failures

If an early status poll reports `Unknown bot`, this means the local registry marker
has disappeared; it does **not** prove the VM was never created. The E2E test now
captures `magbot inspect`, `virsh dominfo`, and recent libvirt logs before invoking
safe `despawn`. Logs are kept under `/var/log/magbot/smoke-*.log` (root-only).
`despawn` never deletes an orphaned domain without a matching ownership marker.

After installation, rerun the test once:

```bash
cd ~/magbot-stack
sudo bash smoke-test.sh
```

For manual inspection before another test:

```bash
sudo magbot status
sudo virsh -c qemu:///system list --all
sudo find /var/lib/libvirt/images/magbot/bots -maxdepth 2 -name metadata.json -print
```

Inspect an individual bot:

```bash
sudo magbot inspect agent1
```

### Updating an already installed MAGBOT (no apt/service restart)

For a host which already reported `MAGBOT INSTALLED`, use the code-only upgrade:

```bash
cd ~/magbot-stack
sudo bash upgrade-magbot.sh
sudo bash smoke-test.sh
```

The upgrade checks the 16 local tests and syntax, backs up the installed controller,
installs the replacement and runs `magbot doctor`. If the doctor fails, it restores
the original controller. It does not run apt, restart services, change network
settings or delete existing guest directories. No reboot.

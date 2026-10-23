#!/usr/bin/env bash
# Real end-to-end bootstrap test: a fresh Ubuntu 24.04 system container
# (podman, systemd as PID 1, own network namespace) runs
#   ops/bootstrap.sh --owner e2e@example.com --test-stubs
# from a clone of this checkout, then checks that the room answers /readyz,
# the stub model is reachable through the audit gateway (/readyz/model), the
# nginx Tailnet ingress answers, and that mag-llm was neither started nor
# restarted.
#
# What the harness provides (on a real host this comes from the archive or
# from Tailscale): a dummy interface with the Tailnet IP 100.123.114.16, a
# self-signed mag.stardenburg.de certificate in /etc/mag-nginx/tls (trusted
# by the container's CA store), and the TEST-HOST marker that unlocks
# --test-stubs (stub LLM on :8090/:8091, stub whisper model, no golden image,
# no GGUF download). Everything else is the production bootstrap path.
#
# Not covered: KVM bot VMs (no golden image), real models, ACME issuance,
# tailscale login, Owner SSH probe, two NUMA nodes.
#
#   sudo bash ops/tests/e2e-bootstrap.sh [--keep] [--jobs N] [--host-network]
# --host-network: for sandboxes whose egress only serves host processes
#   (bridged containers get no outbound network). The container then shares
#   the host network namespace, so its nftables tables (mag_audit,
#   mag_agents, libvirt), virbr0, tailscale0 and the dummy Tailnet interface
#   appear on the host; they are removed again on exit. Disposable hosts only.
# Uncommitted changes of the checkout are applied on top of HEAD.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
KEEP=0
HOSTNET=0
JOBS=${MAG_E2E_JOBS:-4}
while (( $# )); do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --jobs) JOBS=${2:?}; shift 2 ;;
    --host-network) HOSTNET=1; shift ;;
    *) echo "usage: $0 [--keep] [--jobs N] [--host-network]" >&2; exit 2 ;;
  esac
done
[[ $EUID == 0 ]] || { echo 'root required (rootful podman with systemd)' >&2; exit 1; }
command -v podman >/dev/null || { echo 'podman missing (apt-get install podman)' >&2; exit 1; }
# docker.io/library/ubuntu:24.04 as of 2026-10-02
BASE_IMAGE=docker.io/library/ubuntu@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc
IMAGE=localhost/mag-e2e-noble:latest
NAME=mag-e2e-$$
LOG=${MAG_E2E_LOG:-/var/tmp/mag-e2e-bootstrap.log}
work=$(mktemp -d /var/tmp/mag-e2e.XXXXXXXX)
cleanup() {
  local rc=$?
  if (( KEEP )); then
    echo "e2e: container kept: podman exec -it $NAME bash"
  else
    podman rm -f "$NAME" >/dev/null 2>&1 || true
    if (( HOSTNET )); then
      # Remove exactly what the test added to the shared namespace: nft
      # tables (mag_audit, libvirt's iptables-nft rules, ...), links
      # (virbr0, tailscale0, magts0) and the Tailnet address on lo.
      while read -r fam tab; do
        grep -Fxq "$fam $tab" <<<"$tables_before" || nft delete table "$fam" "$tab" 2>/dev/null || true
      done < <(nft list tables 2>/dev/null | awk '$1=="table"{print $2, $3}')
      while read -r l; do
        grep -Fxq "$l" <<<"$links_before" || ip link del "$l" 2>/dev/null || true
      done < <(ip -o link show | awk -F': ' '{sub(/@.*/,"",$2); print $2}')
      ip addr del 100.123.114.16/32 dev lo 2>/dev/null || true
    fi
  fi
  rm -rf -- "$work"
  exit "$rc"
}
tables_before=$(nft list tables 2>/dev/null | awk '$1=="table"{print $2, $3}')
links_before=$(ip -o link show | awk -F': ' '{sub(/@.*/,"",$2); print $2}')
trap cleanup EXIT
say() { echo "e2e: $*"; }

# A blank cloud server: systemd, sudo, git, curl, iproute2; nothing else.
cat > "$work/Containerfile" <<EOF
FROM $BASE_IMAGE
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
      systemd systemd-sysv dbus sudo ca-certificates git curl iproute2 kmod udev openssl \\
 && apt-get clean && rm -rf /var/lib/apt/lists/* \\
 && systemctl mask systemd-networkd-wait-online.service getty@tty1.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
EOF
say "building $IMAGE"
# Build steps resolve DNS through the host: in some sandboxes and CI hosts
# the build network namespace has no resolver. The test container itself
# runs on its own podman network.
podman build -q --network host -t "$IMAGE" -f "$work/Containerfile" "$work" >/dev/null

# The repo as committed, plus the working-tree diff.
git -c safe.directory="$ROOT" -C "$ROOT" bundle create "$work/repo.bundle" HEAD >/dev/null 2>&1
git -c safe.directory="$ROOT" -C "$ROOT" diff HEAD --binary > "$work/dirty.patch"
git -c safe.directory="$ROOT" -C "$ROOT" ls-files --others --exclude-standard -z > "$work/untracked.list"
tar -C "$ROOT" --null -T "$work/untracked.list" -cf "$work/untracked.tar"
chmod -R a+rX "$work"

say "starting container $NAME"
netargs=()
(( HOSTNET )) && netargs=(--network host)
podman run -d --name "$NAME" --hostname mag-e2e --privileged --systemd=always "${netargs[@]}" \
  --memory "${MAG_E2E_MEMORY:-6g}" -v "$work:/e2e:ro" "$IMAGE" >/dev/null
# Run a command in the test container ("in" is a bash reserved word).
cx() { podman exec "$NAME" "$@"; }
for _ in $(seq 1 60); do
  state=$(cx systemctl is-system-running 2>/dev/null || true)
  [[ $state == running || $state == degraded ]] && break
  sleep 1
done
# Never let a test touch the host's KVM (bot VMs are out of scope here).
cx rm -f /dev/kvm

cx bash -euo pipefail -c '
  install -d -m 0755 /etc/mag-stack
  touch /etc/mag-stack/TEST-HOST
  id chris >/dev/null 2>&1 || useradd --create-home --shell /bin/bash chris
  runuser -u chris -- git clone -q /e2e/repo.bundle /home/chris/mag-stack
  runuser -u chris -- git -C /home/chris/mag-stack checkout -q -B main
  if [ -s /e2e/dirty.patch ]; then runuser -u chris -- git -C /home/chris/mag-stack apply --whitespace=nowarn /e2e/dirty.patch; fi
  runuser -u chris -- tar -C /home/chris/mag-stack -xf /e2e/untracked.tar
  # Tailnet address the nginx vhost listens on (Tailscale itself stays logged out).
  # A dummy interface where the kernel has the driver, else lo (some
  # sandbox kernels lack "dummy"). Fails loudly either way.
  if ip link add magts0 type dummy 2>/dev/null; then dev=magts0; ip link set magts0 up; else dev=lo; fi
  ip addr add 100.123.114.16/32 dev "$dev"
  ip -4 addr show | grep -Fq 100.123.114.16/
  # TLS state normally restored from the archive.
  install -d -m 0750 /etc/mag-nginx/tls
  openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj /CN=mag.stardenburg.de \
    -addext subjectAltName=DNS:mag.stardenburg.de \
    -keyout /etc/mag-nginx/tls/key.pem -out /etc/mag-nginx/tls/cert.pem 2>/dev/null
  install -m 0644 /etc/mag-nginx/tls/cert.pem /usr/local/share/ca-certificates/mag-e2e.crt
  update-ca-certificates >/dev/null
'
say "running bootstrap (log: $LOG)"
start=$SECONDS
rc=0
cx env MAG_BUILD_JOBS="$JOBS" MAG_VOICE_JOBS="$JOBS" JOBS="$JOBS" \
  bash /home/chris/mag-stack/ops/bootstrap.sh --owner e2e@example.com --test-stubs > "$LOG" 2>&1 || rc=$?
say "bootstrap exit $rc after $(( SECONDS - start ))s"
if (( rc != 0 )); then
  tail -n 60 "$LOG"
  cx systemctl --failed --no-pager 2>/dev/null || true
  cx journalctl -n 60 --no-pager -u gitea -u mag-120 -u mag-audit-gateway -u nginx 2>/dev/null | tail -n 60 || true
  exit 1
fi

fail=0
check() { local what=$1; shift; if "$@"; then say "PASS $what"; else say "FAIL $what"; fail=1; fi; }
readyz=$(cx curl -fsS --max-time 5 http://127.0.0.1:8766/readyz || true)
check 'room /readyz' grep -q '"ready":true' <<<"$readyz"
model=$(cx curl -fsS --max-time 10 http://127.0.0.1:8766/readyz/model || true)
check 'stub model via audit gateway (/readyz/model)' grep -q '"ready":true' <<<"$model"
check 'audit gateway health' cx curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:8080/audit/v1/health
code=$(cx curl -sS --max-time 10 -o /dev/null -w '%{http_code}' --resolve mag.stardenburg.de:443:100.123.114.16 https://mag.stardenburg.de/ || true)
check "nginx Tailnet ingress (HTTP $code)" grep -Eq '^(200|401|403)$' <<<"$code"
for unit in mag-120 mag-audit-gateway mag-audit-webproxy mag-browser-webrtc nginx gitea tailscaled mag-backup-daily.timer mag-auto-update.timer; do
  check "$unit active" cx systemctl is-active --quiet "$unit"
done
check 'mag-llm not started by bootstrap' bash -c "! podman exec $NAME systemctl is-active --quiet mag-llm.service"
check 'console lockdown not enabled' bash -c "! podman exec $NAME systemctl is-enabled --quiet mag-console-only.service"
check 'release symlink' cx test -L /opt/mag-stack/current
check 'verified pre-deploy backup' cx sh -c 'ls /var/backups/mag-full/*/VERIFIED >/dev/null'
check 'whisper-cli pinned build' cx test -x /opt/mag-stack/current/bin/whisper-cli
check 'lego 5.5.2 installed' bash -c "podman exec $NAME /usr/local/bin/lego --version | grep -Fq 5.5.2"
# A second bootstrap must refuse instead of touching a running host.
again=0
cx bash /home/chris/mag-stack/ops/bootstrap.sh --owner e2e@example.com --test-stubs > "$work/again.log" 2>&1 || again=$?
check 'second bootstrap refuses on a running host' grep -Fq 'Use ops/deploy.sh' "$work/again.log"
(( again != 0 )) || fail=1
if (( fail )); then
  say 'FAILED'
  exit 1
fi
say "PASS (bootstrap $(( SECONDS - start ))s)"

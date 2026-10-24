#!/usr/bin/env bash
# Dry-run only. Proves the blank-host script lists every phase, calls every
# installer and stays idle on lockdown and mag-llm. The real end-to-end run is
# ops/tests/e2e-bootstrap.sh.
# shellcheck disable=SC2016  # literal $ patterns are intended
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/bootstrap.sh" "$root/mag" "$root/ops/gitea/install.sh" \
  "$root/ops/bootstrap-voice.sh" "$root/ops/vm/build-golden.sh" \
  "$root/ops/tailscale/install.sh" \
  "$root/ops/private-access/install-lego.sh" "$root/ops/gitea/mirror-github.sh" \
  "$root/ops/tests/e2e-bootstrap.sh"
out=$(bash "$root/ops/bootstrap.sh" --dry-run)
for phase in release-check packages restic users restore place-state config tailscale \
  lego gitea gitea-mirror build voice llm-runtime golden backup-install audit gateway \
  tailscale-egress acme models deploy private-access pages auto-update runners \
  start-llm console-lockdown-skipped; do
  grep -Fq "PHASE $phase" <<<"$out" || { echo "missing phase $phase" >&2; exit 1; }
done
# Order matters: audit (web proxy + CA) before deploy's free-MCP probe; the
# LLM runtime before units; voice before deploy (deploy needs whisper-cli).
order=$(grep -o '^PHASE [a-z-]*' <<<"$out" | cut -d' ' -f2 | tr '\n' ' ')
pos() { local i=0 p; for p in $order; do [[ $p == "$1" ]] && { echo "$i"; return; }; i=$((i + 1)); done; echo 999; }
before() { (( $(pos "$1") < $(pos "$2") )) || { echo "phase $1 must run before $2" >&2; exit 1; }; }
before release-check packages
before build voice
before voice deploy
before build llm-runtime
before audit deploy
before lego acme
before acme deploy
before deploy auto-update
before deploy runners
for needle in install-restic-0181.sh ops/gitea/install.sh ops/deploy.sh \
  ops/bootstrap-voice.sh ops/vm/build-golden.sh ops/qwen/build-runtime.sh \
  ops/tailscale/install.sh \
  ops/private-access/install-lego.sh 'ops/audit/install-audit.sh --apply' \
  ops/pages/install.sh ops/auto-update/install.sh ops/runners/install.sh \
  ops/gitea/mirror-github.sh issue-cert.sh mag-acme-renew.timer; do
  grep -Fq -- "$needle" <<<"$out" || { echo "dry run does not mention $needle" >&2; exit 1; }
done
grep -Fq 'bootstrap-models.sh' "$root/ops/bootstrap.sh"
grep -Fq 'ops/qwen/build-runtime.sh' "$root/ops/bootstrap.sh"
# DeepSeek is retired: a blank host never builds, installs, downloads or verifies it.
if grep -Eq 'deepseek/(bootstrap-runtimes|install-deepseek|build-ik|llm\.env)|mag-llm\.service' "$root/ops/bootstrap.sh"; then
  echo 'bootstrap still installs DeepSeek' >&2; exit 1
fi
if grep -Eqi 'deepseek-v4|models(-iq3)?\.sha256|UD-Q4|UD-IQ3' "$root/ops/backup/bootstrap-models.sh"; then
  echo 'bootstrap-models still fetches DeepSeek' >&2; exit 1
fi
grep -Fq 'ops/ornith/model.sha256' "$root/ops/backup/bootstrap-models.sh"
# The retired hot-swap catalog is not bootstrapped; paths may still contain legacy names.
if grep -v 'seed /var/lib/mag-numa' <<<"${out//$root/ROOT}" | grep -Eqi 'deepseek|mag-llm\.service|install-deepseek|8090'; then echo 'dry run mentions DeepSeek' >&2; exit 1; fi
grep -Fq 'install-tailscale-egress.sh' "$root/ops/bootstrap.sh"
if grep -Eq 'bash .*console-lockdown' "$root/ops/bootstrap.sh"; then echo 'bootstrap runs console lockdown' >&2; exit 1; fi
for f in "$root/ops/bootstrap.sh" "$root/ops/deepseek/bootstrap-runtimes.sh" "$root/ops/vm/build-golden.sh"; do
  if grep -Eq 'systemctl (restart|stop|kill|try-restart|reload-or-restart) (mag-llm|"?\$secondary)' "$f"; then
    echo "$f restarts or stops an LLM" >&2; exit 1
  fi
done
grep -Fq 'Does not restart a model' "$root/ops/bootstrap.sh" || grep -Fq 'not started or restarted' "$root/ops/bootstrap.sh"
# Test stubs are refused unless the TEST-HOST marker exists.
grep -Fq '/etc/mag-stack/TEST-HOST' "$root/ops/bootstrap.sh"
if [[ ! -e /etc/mag-stack/TEST-HOST && $EUID == 0 ]]; then
  if bash "$root/ops/bootstrap.sh" --test-stubs >/dev/null 2>&1; then echo 'stubs accepted without marker' >&2; exit 1; fi
fi
# Secondary model switch is read, never written.
if grep -Eq '>[[:space:]]*/etc/mag-stack/secondary-model.env' "$root/ops/bootstrap.sh" "$root/ops/deepseek/bootstrap-runtimes.sh"; then
  echo 'bootstrap writes secondary-model.env' >&2; exit 1
fi
# Dry-run must not require root and must not create the staging directory.
[[ ! -d /var/tmp/mag-bootstrap-restore ]]
# Every binary deploy.sh requires must be in the bootstrap/update build list.
source "$root/ops/build-targets.sh"
for binary in "${MAG_RELEASE_BINARIES[@]}"; do
  printf '%s\n' "${MAG_BUILD_TARGETS[@]}" | grep -Fxq "$binary"
done
grep -Fq 'mag-det' <<<"$out"
grep -Fq 'MAG_RELEASE_BINARIES' "$root/ops/deploy.sh"
grep -Fq 'MAG_BUILD_TARGETS' "$root/ops/update.sh"
# Pins: whisper model SHA256, cloud image SHA256, lego SHA256 (64 hex each).
grep -Eq '^MODEL_SHA256=[0-9a-f]{64}$' "$root/ops/bootstrap-voice.sh"
grep -Eq '^UBUNTU_SHA256=[0-9a-f]{64}$' "$root/ops/vm/build-golden.sh"
grep -Eq '^UBUNTU_SERIAL=[0-9]{8}$' "$root/ops/vm/build-golden.sh"
grep -Eq '^LEGO_SHA256=[0-9a-f]{64}$' "$root/ops/private-access/install-lego.sh"
# deploy.sh must not abort on a host without golden.qcow2.
grep -Fq 'if [[ -f /var/lib/libvirt/images/magbot/base/golden.qcow2 ]]' "$root/ops/deploy.sh"
# Paths bootstrap-voice.sh produces are the ones deploy.sh reads.
grep -Fq '/home/chris/.cache/mag-stack/whisper-build/bin/whisper-cli' "$root/ops/deploy.sh"
grep -Fq '/home/chris/models/whisper/ggml-large-v3-turbo.bin' "$root/ops/deploy.sh"
grep -Fq 'CACHE="${MAG_VOICE_CACHE:-$HOME/.cache/mag-stack}"' "$root/ops/bootstrap-voice.sh"
grep -Fq 'MODEL_DIR="${MAG_VOICE_MODELS:-$HOME/models/whisper}"' "$root/ops/bootstrap-voice.sh"
# Sub-installers have their own dry runs.
grep -Fq 'lego 5.5.2' <<<"$(bash "$root/ops/private-access/install-lego.sh" --dry-run)"
mirror_out=$(bash "$root/ops/gitea/mirror-github.sh" --dry-run)
grep -Fq 'pull mirror' <<<"$mirror_out"
grep -Fq 'cloud-images.ubuntu.com/releases/noble/release-' <<<"$(bash "$root/ops/vm/build-golden.sh" --print-pin)"
# magbot keeps uid 993 only if it exists before packages add system users.
users_at=$(grep -n '^PHASE users$' <<<"$out" | cut -d: -f1)
packages_at=$(grep -n '^PHASE packages$' <<<"$out" | cut -d: -f1)
(( users_at < packages_at ))
echo 'Bootstrap dry-run PASS'

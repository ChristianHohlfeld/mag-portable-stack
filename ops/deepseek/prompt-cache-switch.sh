#!/usr/bin/env bash
# Stage or revert ik_llama.cpp cache flags. NEVER restart mag-llm here.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ACTION=${1:-}
ENVFILE=${MAG_LLM_ENV:-/etc/mag-llm/llm.env}
UNITFILE=${MAG_LLM_UNIT:-/etc/systemd/system/mag-llm.service}
BACKUP=${MAG_PROMPT_CACHE_BACKUP_DIR:-/var/lib/mag-llm/prompt-cache-rollback}
[[ "$ACTION" == apply || "$ACTION" == rollback ]] || { echo "Usage: $0 apply|rollback" >&2; exit 2; }
[[ $EUID == 0 ]] || { echo "Root required" >&2; exit 2; }
[[ -f "$ENVFILE" && -f "$UNITFILE" ]] || { echo "Missing env or unit" >&2; exit 2; }
if [[ "$ACTION" == rollback ]]; then
  [[ -f "$BACKUP/READY" && -f "$BACKUP/llm.env" && -f "$BACKUP/mag-llm.service" ]] || { echo "No complete backup" >&2; exit 2; }
  install -m 0600 "$BACKUP/llm.env" "$ENVFILE"
  install -m 0644 "$BACKUP/mag-llm.service" "$UNITFILE"
  systemctl daemon-reload
  echo "Original env and unit restored. No mag-llm restart performed."
  exit 0
fi
[[ ! -e "$BACKUP/READY" ]] || { echo "Original snapshot already exists; refuse overwrite" >&2; exit 2; }
tmpenv=$(mktemp "$(dirname "$ENVFILE")/.cache-env.XXXXXX")
tmpunit=$(mktemp "$(dirname "$UNITFILE")/.cache-unit.XXXXXX")
trap 'rm -f -- "$tmpenv" "$tmpunit"' EXIT
# Copy without interpreting arbitrary env file lines as shell code.
python3 - "$ENVFILE" "$tmpenv" <<'PY'
import pathlib, sys
src=pathlib.Path(sys.argv[1]).read_text()
src="\n".join(line for line in src.splitlines() if not line.startswith(("IK_SLOT_SAVE=", "IK_SLOT_SAVE_PATH=", "IK_CRS=")))
pathlib.Path(sys.argv[2]).write_text(src.rstrip("\n")+"\nIK_SLOT_SAVE=1\nIK_SLOT_SAVE_PATH=/var/lib/mag-llm/slots\nIK_CRS=0.20\n")
PY
chmod 0600 "$tmpenv"
(
  unset IK_SLOT_SAVE IK_SLOT_SAVE_PATH IK_CRS
  export MAG_LLM_ENV="$tmpenv"
  source "$ROOT/ops/deepseek/llm-config.sh"
  llm_config_load
  [[ "$ENGINE" == ik ]] || { echo "Requires ENGINE=ik" >&2; exit 4; }
  render_unit "$tmpunit"
)
# ik binaries that omit optional flags are NOT eligible for activation.
grep -Fq -- '--slot-save-path /var/lib/mag-llm/slots' "$tmpunit" || { echo "Binary lacks --slot-save-path" >&2; exit 4; }
grep -Eq -- '(^|[[:space:]])(-crs|--cache-ram-similarity) 0[.]20([[:space:]]|$)' "$tmpunit" || { echo "Binary lacks -crs" >&2; exit 4; }
for flag in '--cache-ram 16384' '--parallel 1' '--cache-type-k f16' '--cache-type-v f16'; do
  grep -Fq -- "$flag" "$tmpunit" || { echo "Required inherited flag missing: $flag" >&2; exit 4; }
done
install -d -m 0700 "$BACKUP"
cp -p "$ENVFILE" "$BACKUP/llm.env"
cp -p "$UNITFILE" "$BACKUP/mag-llm.service"
chmod 0600 "$BACKUP/llm.env" "$BACKUP/mag-llm.service"
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots
touch "$BACKUP/READY"; chmod 0600 "$BACKUP/READY"
install -m 0600 "$tmpenv" "$ENVFILE"
install -m 0644 "$tmpunit" "$UNITFILE"
systemctl daemon-reload
echo "Cache flags staged. No mag-llm restart. Rollback: sudo bash ops/deepseek/prompt-cache-switch.sh rollback"

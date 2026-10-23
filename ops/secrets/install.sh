#!/usr/bin/env bash
# Creates the room's secret-store key once: /etc/credstore/mag-secrets-key
# (root 0600). systemd hands it to mag-120 via LoadCredential; the room
# encrypts every bot secret (AES-256-GCM) in /var/lib/magbot/room/secrets.json.
# The key is part of the encrypted backup (ops/backup) and restored by
# bootstrap; an existing key is never replaced (that would lose all secrets).
#   sudo bash ops/secrets/install.sh [--dry-run]
set -euo pipefail
DRY=0; [[ ${1:-} == --dry-run ]] && DRY=1
KEY=/etc/credstore/mag-secrets-key
if [[ -s $KEY ]]; then echo "mag-secrets-key present (kept)"; exit 0; fi
if (( DRY )); then echo "+ create $KEY (32 random bytes, root 0600)"; exit 0; fi
install -d -m 0700 /etc/credstore
umask 077
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY.tmp"
chmod 0600 "$KEY.tmp"; mv -f "$KEY.tmp" "$KEY"
echo "mag-secrets-key created. Restart mag-120 to use it."

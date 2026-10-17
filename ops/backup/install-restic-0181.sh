#!/usr/bin/env bash
# Pinned official restic x86_64 for MAG Broadwell, minimum 0.17.
# Does not touch a repository, password file, service or backup timer.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || exit 1
for cmd in curl sha256sum bunzip2 install mv; do command -v "$cmd" >/dev/null; done
artifact=restic_0.18.1_linux_amd64.bz2
sha=680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d
tmp="$(mktemp -d /var/tmp/mag-restic-bin.XXXXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fSL --retry 3 --max-time 120 \
 "https://github.com/restic/restic/releases/download/v0.18.1/$artifact" \
 -o "$tmp/$artifact"
printf '%s  %s\n' "$sha" "$tmp/$artifact" | sha256sum -c -
bunzip2 -c "$tmp/$artifact" > "$tmp/restic"
chmod 0755 "$tmp/restic"
"$tmp/restic" version | grep -F 'restic 0.18.1 '
# Atomic upgrade, leave the distro /usr/bin/restic untouched for rollback.
install -m 0755 -o root -g root "$tmp/restic" /usr/local/bin/.mag-restic-next
mv -f /usr/local/bin/.mag-restic-next /usr/local/bin/restic
/usr/local/bin/restic version
echo 'Pinned restic 0.18.1 installed; next run migrate-passwordless.sh.'

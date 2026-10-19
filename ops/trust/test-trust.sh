#!/usr/bin/env bash
# The shipped Root YE must verify Let's Encrypt YE2 with no system trust store.
# Guest install stays on the overlay: despawn deletes the disk, golden stays read-only.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
openssl verify -CAfile "$here/isrg-root-ye.pem" -no-CApath -no-CAstore "$here/testdata/ye2.pem" >/dev/null
openssl verify -CAfile "$here/isrg-roots.pem" -no-CApath -no-CAstore "$here/testdata/ye2.pem" >/dev/null
for name in isrg-root-x1 isrg-root-x2 isrg-root-ye isrg-root-yr; do
  openssl x509 -in "$here/$name.pem" -noout -subject | grep -q .
done
grep -q 'isrg-roots.pem' "$root/mag-room/native/vm.cpp"
grep -q 'remove_all' "$root/mag-room/native/vm.cpp"
grep -q 'golden image is never modified' "$root/mag-room/native/vm.cpp"
grep -q 'ops/trust/install-trust.sh' "$root/ops/deploy.sh"
grep -q 'mag-isrg-' "$root/ops/audit/vm-proxy-setup.sh"
grep -q 'overflow-wrap:anywhere' "$root/mag-room/web/index.html"
grep -q "el('span','',c.name)" "$root/mag-room/web/index.html"
bash -n "$here/install-trust.sh" "$root/ops/audit/vm-proxy-setup.sh" "$root/ops/deploy.sh"
echo 'trust chain OK'

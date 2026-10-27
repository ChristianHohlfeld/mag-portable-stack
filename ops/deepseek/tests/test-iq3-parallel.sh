#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
for n in 1 2 3 4;do
 printf -v file 'DeepSeek-V4-Flash-0731-UD-IQ3_XXS-%05d-of-00004.gguf' "$n"
 printf 'tiny fixture %s\n' "$n" > "$dir/$file"
done
(cd "$dir";sha256sum *.gguf > SHA256SUMS)
cp "$dir/SHA256SUMS" "$dir/reference.sha256"
verify(){ MAG_IQ3_DIR="$dir" MAG_IQ3_STAMP="$dir/stamp" MAG_IQ3_PINNED_MANIFEST="$dir/reference.sha256" bash "$root/ops/deepseek/verify-iq3.sh" "$@" >/dev/null 2>&1; }
verify || { echo "valid four-part SHA gate rejected tiny split";exit 1; }
mkdir -p "$dir/bin"
cat > "$dir/bin/sha256sum" <<'EOF'
#!/bin/sh
if [ "$1" = "--check" ]; then echo 'content hash ran' >&2; exit 9; fi
for a in "$@"; do case "$a" in *.gguf) echo 'gguf hash ran' >&2; exit 9;; esac; done
exec /usr/bin/sha256sum "$@"
EOF
chmod 0755 "$dir/bin/sha256sum"
PATH="$dir/bin:$PATH" verify || { echo "fresh stamp still rehashed"; exit 1; }
rm -f "$dir/stamp"
PATH="$dir/bin:$PATH" verify --adopt || { echo "adopt refused a pinned manifest"; exit 1; }
PATH="$dir/bin:$PATH" verify || { echo "adopted stamp did not skip the content hash"; exit 1; }
unset PATH
export PATH="/usr/bin:/bin"
printf 'corrupt\n' >> "$dir/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00002-of-00004.gguf"
if verify;then echo "corrupt model accepted";exit 1;fi
(cd "$dir";sha256sum *.gguf > SHA256SUMS)
if verify;then echo "self-signed corrupt manifest accepted";exit 1;fi
cp "$dir/reference.sha256" "$dir/SHA256SUMS"
rm "$dir/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00004-of-00004.gguf"
if verify;then echo "missing split accepted";exit 1;fi
printf 'tiny fixture 4\n' > "$dir/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00004-of-00004.gguf"
touch "$dir/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00002-of-00004.gguf.aria2"
if verify;then echo "active download accepted";exit 1;fi
u="$root/ops/deepseek/mag-llm-iq3.service"
grep -Fqx 'CPUAffinity=14-25 42-53' "$u"
grep -Fqx 'AllowedCPUs=14-25 42-53' "$u"
grep -Fqx 'NUMAPolicy=bind' "$u"
grep -Fqx 'NUMAMask=1' "$u"
grep -Fqx 'MemoryMax=176G' "$u"
! grep -Eq 'Conflicts=mag-llm[.]service|Requires=mag-llm[.]service' "$u"
grep -Fq -- '--cpunodebind=1 --membind=1' "$root/ops/deepseek/start-iq3.sh"
grep -Fq -- '--numa numactl' "$root/ops/deepseek/start-iq3.sh"
! grep -Eq -- '--interleave=all|--numa distribute' "$root/ops/deepseek/start-iq3.sh"
! grep -Eq 'systemctl (start|stop|restart|kill) mag-llm[.]service' "$root/ops/deepseek/iq3-parallel.sh"
! grep -Fq "/api/models" "$root/mag-room/web/index.html"
grep -Fq 'Modellwechsel deaktiviert' "$root/mag-room/native/routes.hpp"
grep -Fq 'selected="q4"' "$root/mag-room/native/mag_audit_gateway.cpp"
grep -Fq 'MAG_MODEL_IQ3_PORT' "$root/mag-room/native/mag_audit_gateway.cpp"
grep -Fq '/api/model' "$root/mag-room/native/routes.hpp"
grep -Fq 'id="model-choice"' "$root/mag-room/web/index.html"
grep -Fq 'data-model-id="deepseek-v4-flash-0731"' "$root/mag-room/web/index.html"
grep -Fq 'data-model-id="deepseek-v4-flash-0731-iq3"' "$root/mag-room/web/index.html"
grep -Fq '>0 · Q4</button>' "$root/mag-room/web/index.html"
grep -Fq '>1 · IQ3</button>' "$root/mag-room/web/index.html"
! grep -Fq 'id="model-pick"' "$root/mag-room/web/index.html"
! grep -Fq 'guest-email' "$root/mag-room/web/index.html"
! grep -Fq 'user-invites' "$root/mag-room/native/guest_access.hpp"
! grep -Fq 'mag_guest_email_valid' "$root/mag-room/native/guest_key_description.hpp"
grep -Fq 'mag-backup-daily' "$root/ops/deepseek/start-iq3.sh"
[[ $(wc -l < "$root/ops/deepseek/iq3.sha256") -eq 4 ]]
bash -n "$root/ops/deepseek/verify-iq3.sh" "$root/ops/deepseek/start-iq3.sh" "$root/ops/deepseek/iq3-parallel.sh"
echo "IQ3 parallel NUMA1/SHA/no-swap regression PASS"

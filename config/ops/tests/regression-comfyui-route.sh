#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-mag.tail3e02ae.ts.net}"
PASS=0
FAIL=0

pass() { PASS=$((PASS+1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }

tmp_header="$(mktemp)"
tmp_body="$(mktemp)"
cleanup() {
  rm -f "$tmp_header" "$tmp_body"
}
trap cleanup EXIT

echo "Regression test: /comfyui route on ${TARGET_HOST}"

code="$(curl -ksS -o /dev/null -D "$tmp_header" -w "%{http_code}" "https://${TARGET_HOST}/comfyui" || echo 000)"
location="$(awk -F': ' 'tolower($1)=="location" { gsub("\r", "", $2); print $2 }' "$tmp_header" | tail -n1)"
if [ "$code" = "301" ] || [ "$code" = "302" ]; then
  if echo "$location" | grep -q "/comfyui/"; then
    pass "/comfyui redirects to /comfyui/ ($code)"
  else
    fail "/comfyui redirect location unexpected: ${location:-<empty>}"
  fi
else
  fail "/comfyui returned HTTP $code (expected 301/302)"
fi

code="$(curl -ksS -o "$tmp_body" -w "%{http_code}" "https://${TARGET_HOST}/comfyui/" || echo 000)"
if [ "$code" = "200" ]; then
  if grep -qi '<title>ComfyUI</title>' "$tmp_body"; then
    pass "/comfyui/ serves ComfyUI HTML"
  else
    fail "/comfyui/ did not contain ComfyUI title marker"
  fi
else
  fail "/comfyui/ returned HTTP $code (expected 200)"
fi

code="$(curl -ksS -o "$tmp_body" -w "%{http_code}" "https://${TARGET_HOST}/comfyui/system_stats" || echo 000)"
if [ "$code" = "200" ]; then
  if grep -q '"comfyui_version"' "$tmp_body"; then
    pass "/comfyui/system_stats exposes comfyui_version"
  else
    fail "/comfyui/system_stats JSON missing comfyui_version"
  fi
else
  fail "/comfyui/system_stats returned HTTP $code (expected 200)"
fi

code="$(curl -ksS -o "$tmp_body" -w "%{http_code}" "https://${TARGET_HOST}/comfyui/queue" || echo 000)"
if [ "$code" = "200" ]; then
  if grep -q '"queue_' "$tmp_body"; then
    pass "/comfyui/queue returns queue JSON"
  else
    fail "/comfyui/queue response missing queue markers"
  fi
else
  fail "/comfyui/queue returned HTTP $code (expected 200)"
fi

echo "Result: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]

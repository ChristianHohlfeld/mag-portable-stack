#!/usr/bin/env bash
# Retired: the sequential Q4<->IQ3 switch is forbidden.
# A previously installed supervisor must be disabled during iq3-parallel staging.
echo "Sequential hot-swap has been retired. Use ops/deepseek/iq3-parallel.sh; Q4 stays active." >&2
exit 4

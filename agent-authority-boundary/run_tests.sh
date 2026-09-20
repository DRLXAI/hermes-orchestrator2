#!/bin/sh
# Complete suite. Zero dependencies; no network.
set -e
for t in tests/test_adversarial.py tests/test_jev_adapter.py tests/test_onboarding.py tests/test_persistence.py; do
  printf '%-34s ' "$t"
  python3 "$t" 2>&1 | tail -1
done

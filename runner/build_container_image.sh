#!/usr/bin/env bash

set -eu

echo "[+] Building runner build environment container image"
run_mkosi=$(readlink -f "$(dirname "$0")/../scripts")/run_mkosi.sh
"$run_mkosi" \
    runner/env \
    "$@"

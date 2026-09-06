#!/usr/bin/env bash

set -eu

profile=$1
shift

echo "[+] Building runner $profile build environment container image"
run_mkosi=$(readlink -f "$(dirname "$0")/../scripts")/run_mkosi.sh
"$run_mkosi" \
    runner/env \
    --profile "$profile" \
    "$@"

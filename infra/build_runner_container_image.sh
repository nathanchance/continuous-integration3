#!/usr/bin/env bash

set -eu

run_mkosi=$(readlink -f "$(dirname "$0")")/run_mkosi.sh
"$run_mkosi" \
    build-env \
    "$@"

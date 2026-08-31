#!/usr/bin/env bash

set -eu

echo "[+] Building mirror virtual machine image"
"$(readlink -f "$(dirname "$0")")"/build_vm_image.sh \
    --profile mirror \
    "$@"

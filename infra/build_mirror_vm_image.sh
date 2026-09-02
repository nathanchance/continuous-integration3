#!/usr/bin/env bash

set -eu

infra="$(readlink -f "$(dirname "$0")")"

echo "[+] Preparing mirrored assets"
"$infra"/vm/mkosi.profiles/mirror/mkosi.extra/opt/mirror.py setup

echo "[+] Building mirror virtual machine image"
"$infra"/build_vm_image.sh \
    --profile mirror \
    "$@"

#!/usr/bin/env bash

set -eu

infra="$(readlink -f "$(dirname "$0")")"

echo "[+] Preparing mirrored assets"
"$infra"/vm/mkosi.profiles/mirror/mkosi.extra/opt/mirror.py setup

echo "[+] Building mirror virtual machine image"
"$infra"/build_vm_image.sh \
    --environment HOST_MIRROR_MKOSI_EXTRA="$infra"/vm/mkosi.profiles/mirror/mkosi.extra \
    --profile mirror \
    "$@"

#!/usr/bin/env bash

set -eu

runner_root=$(readlink -f "$(dirname "$0")")
output=$runner_root/env/mkosi.output/cbl-ci3-build-env
ghcr_namespace=ghcr.io/nathanchance

if ! command -v skopeo &>/dev/null; then
    echo '[-] ERROR: skopeo not installed!'
    exit 1
fi

if [ ! -d "$output" ]; then
    "$runner_root"/build_container_image.sh "$@"
fi

if ! skopeo login --get-login ghcr.io &>/dev/null; then
    echo '[+] Logging into ghcr.io'
    skopeo login ghcr.io
fi

echo "[+] Uploading ${output##*/} to $ghcr_namespace"
skopeo copy oci:"$output" docker://"$ghcr_namespace"/"${output##*/}":latest

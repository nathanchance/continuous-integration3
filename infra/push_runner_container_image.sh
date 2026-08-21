#!/usr/bin/env bash

set -eu

infra_root=$(readlink -f "$(dirname "$0")")
output=$infra_root/build-env/mkosi.output/cbl-ci3-build-env

if ! command -v skopeo &>/dev/null; then
    echo '[-] ERROR: skopeo not installed!'
    exit 1
fi

if [ ! -d "$output" ]; then
    "$infra_root"/build_runner_container_image.sh "$@"
fi

if ! skopeo login --get-login ghcr.io &>/dev/null; then
    echo '[+] Logging into ghcr.io'
    skopeo login ghcr.io
fi

skopeo copy oci:"$output" docker://ghcr.io/nathanchance/"${output##*/}":latest

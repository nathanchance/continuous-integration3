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
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        skopeo_args=(-u "$GITHUB_ACTOR" -p "$GITHUB_TOKEN")
    else
        skopeo_args=()
    fi
    skopeo login "${skopeo_args[@]}" ghcr.io
fi

echo "[+] Uploading ${output##*/} to $ghcr_namespace"
ghcr_image=docker://"$ghcr_namespace"/"${output##*/}"
image_date=$(date +'%Y-%m-%d-%H-%M')
set -x
skopeo copy oci:"$output" "$ghcr_image":"$image_date"
skopeo copy "$ghcr_image":"$image_date" "$ghcr_image":latest

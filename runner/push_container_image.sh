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
skopeo copy oci:"$output" "$ghcr_image":latest
skopeo copy "$ghcr_image":latest "$ghcr_image":"$(date +'%Y-%m-%d-%H-%M')"

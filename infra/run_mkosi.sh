#!/usr/bin/env bash

set -eu

infra_root=$(readlink -f "$(dirname "$0")")
ci_root=$(readlink -f "$infra_root/..")
mkosi_cache=$ci_root/.mkosi.cache
mkosi_src=$ci_root/.mkosi.git
mkosi_tools=$ci_root/.mkosi.tools
mkosi=(uvx --from "$mkosi_src" mkosi)

if [ $# -lt 1 ]; then
    echo "[-] ERROR: Missing directory name as first argument!" 2>&1
    exit 1
fi
mkosi_dir="$infra_root/$1"
if [ ! -d "$mkosi_dir" ]; then
    echo "[-] ERROR: $mkosi_dir does not exist!"
    exit 1
fi
shift

if [ ! -d "$mkosi_src" ]; then
    git clone https://github.com/systemd/mkosi.git "$mkosi_src"
fi
git -C "$mkosi_src" pull -q || true

if [ ! -L "$mkosi_tools"/etc/resolv.conf ]; then
    "${mkosi[@]}" \
        --directory "$mkosi_src"/mkosi/resources/mkosi-tools \
        --format directory \
        --output "${mkosi_tools##*/}" \
        --output-directory "${mkosi_tools%/*}" \
        --profile misc,package-manager,runtime
fi

"${mkosi[@]}" \
    --directory "$mkosi_dir" \
    --package-cache-dir "$mkosi_cache" \
    --tools-tree "$mkosi_tools" \
    "$@"

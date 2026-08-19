#!/usr/bin/env bash

set -eu

run_mkosi=$(readlink -f "$(dirname "$0")")/run_mkosi.sh

ssh_key=$HOME/.ssh/id_ed25519
if [ ! -e "$ssh_key" ]; then
    old_umask=$(umask)
    umask 0077
    if [ ! -d "$HOME"/.ssh ]; then
        mkdir "$HOME"/.ssh
    fi
    ssh-keygen \
        -C "$(id -un)@$(uname -n)" \
        -f "$ssh_key" \
        -N "" \
        -t ed25519 \
        -q
    umask "$old_umask"
fi

"$run_mkosi" \
    vm \
    --environment HOST_SSH_PUB_KEY="\"$(<"$ssh_key.pub")\"" \
    "$@"

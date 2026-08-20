#!/usr/bin/env bash

set -eux

for item in "$@"; do
    virsh destroy "$item" || true
    virsh undefine --nvram --remove-all-storage "$item"
done

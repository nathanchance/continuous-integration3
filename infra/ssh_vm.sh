#!/usr/bin/env bash

ip_addr=$(virsh domifaddr "$1" | perl -nle 'print $1 if /([0-9.]+)\/\d+$/;')
if [ -z "$ip_addr" ]; then
    echo "Could not find IP address for $1?"
    exit 1
fi

ssh \
    -i /home/cbl-admin/.ssh/id_ed25519 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    root@"$ip_addr"

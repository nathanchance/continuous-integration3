#!/usr/bin/env bash

set -eu

echo $HOST_MIRROR_MKOSI_EXTRA
fdfind -t . $BUILDROOT/ -x "rg -l $HOST_MIRROR_MKOSI_EXTRA"

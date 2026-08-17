#!/bin/sh

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Not running as root?" 2>&1
    exit 1
fi

# shellcheck disable=SC1091
. /usr/lib/os-release
if [ "$ID" != "debian" ]; then
    echo "ERROR: Not running on Debian?" 2>&1
    exit 1
fi

apt update
apt upgrade -y
apt install -y \
    git \
    python3

if [ ! -x /usr/local/bin/uv ]; then
    wget -O - -q https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
uv self update

ci_three=/root/continuous-integration3
if [ ! -d $ci_three ]; then
    git clone https://codeberg.org/nathanchance/continuous-integration3.git $ci_three
fi
git -C $ci_three remote update --prune
git -C $ci_three reset --hard '@{u}'

$ci_three/infra/setup_host.py

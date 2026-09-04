#!/bin/sh

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "[!] Not running as root?" 2>&1
    exit 1
fi

# shellcheck disable=SC1091
. /usr/lib/os-release
if [ "$ID" != "debian" ]; then
    echo "[!] Not running on Debian?" 2>&1
    exit 1
fi

echo "[+] Updating machine"
apt update
apt upgrade -y

echo "[+] Installing base dependencies for bootstrapping"
apt install -y \
    git \
    python3

if [ ! -x /usr/local/bin/uv ]; then
    echo "[+] Installing uv into /usr/local/bin"
    wget -O - -q https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
echo "[+] Ensuring uv is up to date"
uv self update

ci_three=/root/continuous-integration3
if [ ! -d $ci_three ]; then
    echo "[+] Cloning continuous-integration3 for bootstrapping"
    git clone https://github.com/nathanchance/continuous-integration3.git $ci_three
fi
echo "[+] Ensuring continuous-integration3 is up to date"
git -C $ci_three remote update --prune
git -C $ci_three reset --hard '@{u}'

echo "[+] Calling setup_host.py"
$ci_three/infra/setup_host.py

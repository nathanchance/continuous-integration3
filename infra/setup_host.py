#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
# ]
# ///
# Sets up bare metal server from Hetzner

import getpass
import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import requests

ADMIN_NAME = 'cbl-admin'


def prechecks() -> None:
    print('[+] Ensuring we are root')
    if os.geteuid() != 0:
        msg = 'root access is required!'
        raise RuntimeError(msg)

    print('[+] Checking Debian version')
    os_rel_txt = Path('/usr/lib/os-release').read_text(encoding='utf-8')
    if not (match := re.search(r"VERSION_CODENAME=(.*)$", os_rel_txt, flags=re.MULTILINE)):
        msg = 'VERSION_CODENAME not found in /usr/lib/os-release?'
        raise RuntimeError(msg)

    if (deb_codename := match.groups()[0]) != 'trixie':
        msg = f"Running Debian {deb_codename} but support for it has not been validated!"
        raise RuntimeError(msg)


def configure_libvirt() -> None:
    # Ensure user has access to system session by default
    old_umask = os.umask(~0o644)
    bash_aliases = Path('/home', ADMIN_NAME, '.bash_aliases')
    print(f"[+] Configuring libvirt system access through {bash_aliases}")
    with bash_aliases.open('a', encoding='utf-8') as file:
        file.write('export LIBVIRT_DEFAULT_URI="qemu:///system"\n')
    shutil.chown(bash_aliases, user=ADMIN_NAME, group=ADMIN_NAME)
    os.umask(old_umask)

    # Enable libvirtd systemd service to ensure it is brought up on restart
    print('[+] Bringing up libvirtd')
    subprocess.run(['systemctl', 'enable', '--now', 'libvirtd.service'], check=True)

    # Make the default network come up automatically on restart
    print('[+] Enabling autostart for libvirt default network')
    subprocess.run(['virsh', 'net-autostart', 'default'], check=True)

    # Start network if it is not already started
    try:
        net_info = subprocess.run(
            ['virsh', 'net-info', 'default'], capture_output=True, check=True, text=True
        ).stdout
    except subprocess.CalledProcessError as err:
        if err.stdout:
            print(err.stdout)
        if err.stderr:
            print(err.stderr)
    if re.search(r'^Active.*no', net_info, flags=re.MULTILINE):
        print('[+] Starting libvirt default network')
        subprocess.run(['virsh', 'net-start', 'default'], check=True)

    # Adjust DHCP range so that mirror VM can be assigned a static IP address easily
    print(
        '[+] Adjusting libvirt default network DHCP range to allow mirror virtual machine to have static IP address'
    )
    net_dumpxml_res = subprocess.run(
        ['virsh', 'net-dumpxml', 'default'], capture_output=True, check=True, text=True
    )
    network = ElementTree.fromstring(net_dumpxml_res.stdout)
    if (ip_node := network.find('./ip')) is None:
        msg = '<ip> could not be found in net-dumpxml output!'
        raise RuntimeError(msg)
    default_subnet = '192.168.122'
    if (network_ip := ip_node.get('address')) != f"{default_subnet}.1":
        msg = f"Default network IP address ('{network_ip}') does not start with {default_subnet}!"
        raise RuntimeError(msg)
    if (range_node := ip_node.find('./dhcp/range')) is None:
        msg = '<range /> could not be found in net-dumpxml output!'
        raise RuntimeError(msg)
    if range_node.get('start') == (old_start := f"{default_subnet}.2"):
        old_range_node = f"<range start='{old_start}' end='{default_subnet}.254'/>"
        new_range_node = old_range_node.replace(
            f"start='{old_start}'", f"start='{default_subnet}.3'"
        )

        delete_range_cmd = [
            'virsh',
            'net-update',
            'default',
            'delete',
            'ip-dhcp-range',
            old_range_node,
            '--live',
            '--config',
        ]
        subprocess.run(delete_range_cmd, check=True)

        add_range_cmd = [
            'virsh',
            'net-update',
            'default',
            'add',
            'ip-dhcp-range',
            new_range_node,
            '--live',
            '--config',
        ]
        subprocess.run(add_range_cmd, check=True)

    if not (libvirt_store := Path('/home', ADMIN_NAME, 'libvirt')).exists():
        old_umask = os.umask(~0o755)
        libvirt_store.mkdir()
        os.umask(old_umask)
        shutil.chown(libvirt_store, user=ADMIN_NAME, group=ADMIN_NAME)

        setfacl_cmd = ['setfacl', '-m', 'u:libvirt-qemu:rx', libvirt_store.parent]
        subprocess.run(setfacl_cmd, check=True)

        print(f"[+] Configuring libvirt pool at {libvirt_store}")
        virsh_cmd = [
            'virsh',
            'pool-define-as',
            '--name',
            'default',
            '--type',
            'dir',
            '--target',
            libvirt_store,
        ]
        subprocess.run(virsh_cmd, check=True)
        subprocess.run(['virsh', 'pool-autostart', 'default'], check=True)
        subprocess.run(['virsh', 'pool-start', 'default'], check=True)


def configure_ssh() -> None:
    # Set up authorized_keys for both cbl-admin and root
    print('[+] Setting up authorized_keys')
    result = requests.get('https://github.com/nathanchance.keys', timeout=10)
    result.raise_for_status()
    write_ssh_authorized_keys('root', result.text)
    write_ssh_authorized_keys(ADMIN_NAME, result.text)

    # Restrict logins to public keys only for increased security
    print('[+] Configuring sshd')
    sshd_config_txt = (
        'AuthenticationMethods publickey\n'
        'PasswordAuthentication no\n'
        'PermitRootLogin without-password\n'
    )
    Path('/etc/ssh/sshd_config.d/20-cbl-builder.conf').write_text(sshd_config_txt, encoding='utf-8')


def create_user() -> None:
    try:
        pwd.getpwnam(ADMIN_NAME)
    except KeyError:
        user_exists = False
    else:
        user_exists = True
    if user_exists:
        print(f"[-] {ADMIN_NAME} account already exists, skipping creation...")
        return

    with TemporaryDirectory() as tmpdir:
        adduser_conf = Path(tmpdir, 'adduser.conf')
        adduser_conf.write_text('EXTRA_GROUPS="libvirt"\n', encoding='utf-8')

        print(f"[+] Creating {ADMIN_NAME} account")
        adduser_cmd = [
            '/usr/sbin/adduser',
            '--add-extra-groups',
            '--comment',
            'ClangBuiltLinux Administrator',
            '--conf',
            adduser_conf,
            '--disabled-password',
            '--shell',
            '/bin/bash',
            ADMIN_NAME,
        ]
        subprocess.run(adduser_cmd, check=True)


def install_packages() -> None:
    print('[+] Updating machine')
    subprocess.run(['apt', 'update'], check=True)
    subprocess.run(['apt', 'upgrade', '-y'], check=True)

    print('[+] Installing packages')
    packages = [
        # interactive usage
        'bat',
        'btop',
        'duf',
        'fd-find',
        'fish',
        'ripgrep',
        'tmux',
        'vim',
        # libvirt
        'acl',
        'libvirt-clients',
        'libvirt-daemon-system',
        'ovmf',
        'qemu-system',
        'virt-install',
        # mirror.py
        'curl',
        'grokmirror',
        # push_container_image.sh
        'skopeo',
        # vmm.py
        'fzf',
        'python3-requests',
    ]
    subprocess.run(['apt', 'install', '--no-install-recommends', '-y', *packages], check=True)


def write_ssh_authorized_keys(username: str, keys_txt: str) -> None:
    authorized_keys = Path(
        '/', 'root' if username == 'root' else f"home/{username}", '.ssh/authorized_keys'
    )

    file_exists = authorized_keys.exists()
    key_not_in_auth_keys = file_exists and keys_txt not in authorized_keys.read_text(
        encoding='utf-8'
    )
    if not file_exists or key_not_in_auth_keys:
        old_umask = os.umask(~0o700)
        if not authorized_keys.parent.exists():
            authorized_keys.parent.mkdir()
            shutil.chown(authorized_keys.parent, user=username, group=username)
        with authorized_keys.open('a', encoding='utf-8') as file:
            file.write(keys_txt)
        shutil.chown(authorized_keys, user=username, group=username)
        os.umask(old_umask)


def setup_systemd_creds() -> None:
    print('[+] Setting up systemd-creds')
    subprocess.run(['systemd-creds', 'setup'], check=True)
    if not (gh_token_cred := Path('/home', ADMIN_NAME, '.github_token.cred')).exists():
        github_token = getpass.getpass(
            prompt='[+] Provide GITHUB_TOKEN for managing self-hosted runners: '
        )
        print('[+] Encrypting GITHUB_TOKEN via systemd-creds')
        subprocess.run(
            ['systemd-creds', '--uid', ADMIN_NAME, 'encrypt', '-', gh_token_cred],
            check=True,
            input=github_token,
            text=True,
        )
        shutil.chown(gh_token_cred, user=ADMIN_NAME, group=ADMIN_NAME)


def clone_ci3_repo() -> None:
    git_url = 'https://github.com/nathanchance/continuous-integration3'
    git_dst = Path('/home', ADMIN_NAME, git_url.rsplit('/', 1)[1])
    if not git_dst.exists():
        print(f"[+] Cloning {git_url} to {git_dst}")
        git_clone_cmd = ['runuser', '-u', ADMIN_NAME, '--', 'git', 'clone', git_url, git_dst]
        subprocess.run(git_clone_cmd, check=True)


def main():
    prechecks()
    install_packages()
    create_user()
    configure_ssh()
    configure_libvirt()
    setup_systemd_creds()
    clone_ci3_repo()


if __name__ == '__main__':
    main()

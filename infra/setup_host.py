#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
# ]
# ///
# Sets up bare metal server from Hetzner

import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

ADMIN_NAME = 'cbl-admin'


def prechecks() -> None:
    if os.geteuid() != 0:
        msg = 'root access is required!'
        raise RuntimeError(msg)

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
    with bash_aliases.open('a', encoding='utf-8') as file:
        file.write('export LIBVIRT_DEFAULT_URI="qemu:///system"\n')
    shutil.chown(bash_aliases, user=ADMIN_NAME, group=ADMIN_NAME)
    os.umask(old_umask)

    # Enable libvirtd systemd service to ensure it is brought up on restart
    subprocess.run(['systemctl', 'enable', '--now', 'libvirtd.service'], check=True)

    # Make the default network come up automatically on restart
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
        subprocess.run(['virsh', 'net-start', 'default'], check=True)

    if not (libvirt_store := Path('/home', ADMIN_NAME, 'libvirt')).exists():
        old_umask = os.umask(~0o755)
        libvirt_store.mkdir()
        os.umask(old_umask)
        shutil.chown(libvirt_store, user=ADMIN_NAME, group=ADMIN_NAME)

        setfacl_cmd = ['setfacl', '-m', 'u:libvirt-qemu:rx', libvirt_store.parent]
        subprocess.run(setfacl_cmd, check=True)

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
    result = requests.get('https://github.com/nathanchance.keys', timeout=10)
    result.raise_for_status()
    write_ssh_authorized_keys('root', result.text)
    write_ssh_authorized_keys(ADMIN_NAME, result.text)

    # Restrict logins to public keys only for increased security
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
        return

    with TemporaryDirectory() as tmpdir:
        adduser_conf = Path(tmpdir, 'adduser.conf')
        adduser_conf.write_text('EXTRA_GROUPS="libvirt"\n', encoding='utf-8')

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
    subprocess.run(['apt', 'update'], check=True)
    subprocess.run(['apt', 'upgrade', '-y'], check=True)

    packages = [
        # administration
        'acl',
        'skopeo',
        # libvirt
        'libvirt-clients',
        'libvirt-daemon-system',
        'ovmf',
        'qemu-system',
        'virt-install',
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


def clone_ci3_repo() -> None:
    git_url = 'https://codeberg.org/nathanchance/continuous-integration3'
    git_dst = Path('/home', ADMIN_NAME, git_url.rsplit('/', 1)[1])
    if not git_dst.exists():
        git_clone_cmd = ['runuser', '-u', ADMIN_NAME, '--', 'git', 'clone', git_url, git_dst]
        subprocess.run(git_clone_cmd, check=True)


def main():
    prechecks()
    install_packages()
    create_user()
    configure_ssh()
    configure_libvirt()
    clone_ci3_repo()


if __name__ == '__main__':
    main()

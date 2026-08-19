#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
# ]
# ///
# Sets up a virtual machine as a GitHub Actions self-hosted runner

import getpass
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

RUNNER_NAME = 'gh-runner'


def get_github_token() -> str:
    if token := os.environ.get('GITHUB_TOKEN'):
        return token
    return getpass.getpass(prompt='[+] GITHUB_TOKEN not set in environment, please provide one: ')


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


def create_and_configure_user() -> None:
    try:
        pwd.getpwnam(RUNNER_NAME)
    except KeyError:
        pass
    else:
        print(f"[-] {RUNNER_NAME} account already exists")
        return

    print(f"[+] Creating {RUNNER_NAME} account")
    with TemporaryDirectory() as tmpdir:
        adduser_conf = Path(tmpdir, 'adduser.conf')
        adduser_conf.write_text('EXTRA_GROUPS="docker"\n', encoding='utf-8')

        adduser_cmd = [
            '/usr/sbin/adduser',
            '--add-extra-groups',
            '--comment',
            'GitHub Actions Runner',
            '--conf',
            adduser_conf,
            '--disabled-password',
            '--shell',
            '/bin/bash',
            RUNNER_NAME,
        ]
        subprocess.run(adduser_cmd, check=True)

    src_auth_keys = Path('/root/.ssh/authorized_keys')
    dst_auth_keys = Path('/home', RUNNER_NAME, '.ssh', 'authorized_keys')
    print(f"[+] Creating {dst_auth_keys.parent} and copying over {src_auth_keys}")

    old_umask = os.umask(~0o700)
    dst_auth_keys.parent.mkdir()
    shutil.chown(dst_auth_keys.parent, user=RUNNER_NAME, group=RUNNER_NAME)
    shutil.copy2(src_auth_keys, dst_auth_keys)
    shutil.chown(dst_auth_keys, user=RUNNER_NAME, group=RUNNER_NAME)
    os.umask(old_umask)


def register_gh_runner() -> None:
    gh_token = get_github_token()
    request_headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f"Bearer {gh_token}",
        'X-GitHub-Api-Version': '2026-03-10',
    }
    repo = 'nathanchance/continuous-integration3'
    runners_endpoint = f"https://api.github.com/repos/{repo}/actions/runners"

    # Make sure runner is not already registered
    runner_name = socket.gethostname()
    print(f"[+] Checking that {runner_name} has not been registered with GitHub")
    result = requests.get(runners_endpoint, headers=request_headers, timeout=10)
    result.raise_for_status()
    existing_runners = {item['name']: item['id'] for item in result.json()['runners']}
    if runner_name in existing_runners:
        answer = input(f"[-] {runner_name} is already registered! Force delete it? [y/N] ")
        if answer.lower() not in {'y', 'yes'}:
            sys.exit(1)
        requests.delete(
            f"{runners_endpoint}/{existing_runners[runner_name]}",
            headers=request_headers,
            timeout=10,
        ).raise_for_status()

    # Get latest runner application from API
    print('[+] Checking for latest runner application...', end='')
    result = requests.get(f"{runners_endpoint}/downloads", headers=request_headers, timeout=10)
    result.raise_for_status()
    apps = [
        item for item in result.json() if item['os'] == 'linux' and item['architecture'] == 'x64'
    ]
    if len(apps) != 1:
        print()
        msg = f"More than one runner application for Linux x64? {apps}"
        raise RuntimeError(msg)
    app = apps[0]
    print(f" {app['filename']}")

    # Get registration token from API
    print('[+] Getting registration token')
    result = requests.post(
        f"{runners_endpoint}/registration-token", headers=request_headers, timeout=10
    )
    result.raise_for_status()
    token = result.json()['token']

    # Download and setup GitHub Actions runner application
    print('[+] Downloading and installing actions-runner')
    if (workspace := Path('/home', RUNNER_NAME, 'actions-runner')).exists():
        shutil.rmtree(workspace)
    setup_cmds = [
        'set -x',
        f"mkdir {workspace}",
        f"cd {workspace}",
        f"curl -fLSs {app['download_url']} | tar xzf -",
        f"./config.sh --url https://github.com/{repo} --token {token} --unattended",
    ]
    runuser_setup_cmd = ['runuser', '-l', '-c', ' && '.join(setup_cmds), RUNNER_NAME]
    subprocess.run(runuser_setup_cmd, check=True)

    print('[+] Configuring clean-workspace.sh')
    cleanup_sh = Path('/home', RUNNER_NAME, 'clean-workspace.sh')
    cleanup_sh_txt = '#!/usr/bin/env bash\n\nrm -frv "${GITHUB_WORKSPACE%/*}"\n'
    cleanup_sh.write_text(cleanup_sh_txt, encoding='utf-8')
    cleanup_sh.chmod(0o755)
    shutil.chown(cleanup_sh, user=RUNNER_NAME, group=RUNNER_NAME)
    with workspace.joinpath('.env').open('a', encoding='utf-8') as file:
        file.write(f"ACTIONS_RUNNER_HOOK_JOB_COMPLETED={cleanup_sh}\n")

    print('[+] Configuring actions-runner service')
    svc_cmds = [
        'set -x',
        f"cd {workspace}",
        f"./svc.sh install {RUNNER_NAME}",
        './svc.sh start',
    ]
    runuser_svc_cmd = ['runuser', '-l', '-c', ' && '.join(svc_cmds)]
    subprocess.run(runuser_svc_cmd, check=True)


def main():
    prechecks()
    create_and_configure_user()
    register_gh_runner()

    Path(__file__).unlink()


if __name__ == '__main__':
    main()

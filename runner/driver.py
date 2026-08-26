#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
#     "tuxmake>=1.45.0",
# ]
# ///

import os
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path

import requests
import tuxmake.build

MIRROR_GIT = 'git://192.168.122.2'
MIRROR_HTTP = f"{MIRROR_GIT.replace('git', 'http')}:8080"


def print_git_checkout_info(git_repo: Path) -> None:
    info_cmd = ['git', '-C', git_repo, 'show', '-s', '--format=%h ("%s", %cs)']
    info_output = subprocess.run(
        info_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    branch_cmd = ['git', '-C', git_repo, 'rev-parse', '--abbrev-ref', 'HEAD']
    branch_output = subprocess.run(
        branch_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    print(f"[+] Successfully checked out {git_repo.name} at {branch_output}: {info_output}")


def main():
    parser = ArgumentParser(description='Build and boot driver')
    parser.add_argument('-a', '--arch', required=True, help='Architecture to build')
    parser.add_argument('-b', '--boot', action='store_true', help='Boot kernel after build')
    parser.add_argument(
        '-k', '--kconfigs', required=True, nargs='+', help='Kconfig values for tuxmake'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='Perform verbose build in tuxmake'
    )
    args = parser.parse_args()

    llvm_ver = '23'
    kernel_tree = 'linux'

    result = requests.get(f"{MIRROR_HTTP}/toolchains/latest_llvm_releases.json", timeout=15)
    result.raise_for_status()
    llvm_releases = result.json()

    if not (toolchain_tarball := llvm_releases.get(llvm_ver)):
        msg = f"LLVM {llvm_ver} requested but not in latest_llvm_releases.json?"
        raise RuntimeError(msg)

    if not (toolchain := Path('/', toolchain_tarball.replace('.tar.xz', ''))).exists():
        tar_url = f"{MIRROR_HTTP}/toolchains/{toolchain_tarball}"
        print(f"[+] Downloading {tar_url}")
        result = requests.get(tar_url, timeout=10)
        result.raise_for_status()
        print(f"[+] Extracting {toolchain_tarball} to {toolchain}")
        subprocess.run(
            ['tar', '-C', toolchain.parent, '-f', '-', '-J', '-x'], check=True, input=result.content
        )

    base_git_clone_cmd = ['git', 'clone', '--depth=1', '--quiet']

    if not (source := Path('/', kernel_tree)).exists():
        git_url = f"{MIRROR_GIT}/{kernel_tree}.git"
        print(f"[+] Cloning {git_url} to {source}")
        subprocess.run([*base_git_clone_cmd, git_url, source], check=True)
    print_git_checkout_info(source)

    if args.boot and not (boot_utils := Path('/boot-utils')).exists():
        git_url = f"{MIRROR_GIT}/boot-utils.git"
        print(f"[+] Cloning {git_url} to {boot_utils}")
        subprocess.run([*base_git_clone_cmd, git_url, boot_utils], check=True)
        print_git_checkout_info(boot_utils)

    print('[+] Calling tuxmake to build kernel')
    os.environ['PATH'] = f"{toolchain}/bin:{os.environ['PATH']}"
    output = Path('/output')
    result = tuxmake.build.build(
        build_dir=Path('/build'),
        kconfig=args.kconfigs[0],
        kconfig_add=args.kconfigs[1:],
        make_variables={'LLVM': '1', 'LLVM_IAS': '1'},
        output_dir=output,
        target_arch=args.arch,
        targets=['kernel' if args.boot else 'default'],
        toolchain='clang',
        tree=source,
        verbose=args.verbose,
    )
    if result.failed:
        sys.exit(1)

    if args.boot:
        gh_releases_json_url = f"{MIRROR_HTTP}/boot-utils/releases.json"
        gh_releases_json = Path(gh_releases_json_url.replace(MIRROR_HTTP, ''))
        (result := requests.get(gh_releases_json_url, timeout=15)).raise_for_status()
        gh_releases_json.write_bytes(result.content)

        boot_qemu_py = Path(boot_utils, 'boot-qemu.py')
        print(f"[+] Running {boot_qemu_py.name}")
        boot_qemu_py_cmd = [
            boot_qemu_py,
            '-a',
            args.arch,
            '--gh-json-file',
            gh_releases_json,
            '-k',
            output,
        ]
        print(f"$ {' '.join(str(x) for x in boot_qemu_py_cmd)}")
        subprocess.run(boot_qemu_py_cmd, check=True)


if __name__ == '__main__':
    main()

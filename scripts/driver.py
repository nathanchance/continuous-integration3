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


def main():
    parser = ArgumentParser(description='Build and boot driver')
    parser.add_argument('-a', '--arch', required=True, help='Architecture to build')
    parser.add_argument(
        '-k', '--kconfigs', required=True, nargs='+', help='Kconfig values for tuxmake'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='Perform verbose build in tuxmake'
    )
    args = parser.parse_args()

    toolchain = Path('/llvm-23.1.0-rc3-x86_64')
    tar_url = f"https://mirrors.kernel.org/pub/tools/llvm/files/{toolchain.name}.tar.xz"
    print(f"[+] Downloading {tar_url.rsplit('/', 1)[1]}")
    result = requests.get(tar_url, timeout=10)
    result.raise_for_status()
    print(f"[+] Extracting tarball to {toolchain}")
    subprocess.run(
        ['tar', '-C', toolchain.parent, '-f', '-', '-J', '-x'], check=True, input=result.content
    )

    source = Path('/linux')
    git_url = f"https://github.com/torvalds/{source.name}"
    print(f"[+] Cloning {git_url} to {source}")
    subprocess.run(['git', 'clone', '--depth=1', git_url, source], check=True)

    boot_utils = Path('/boot-utils')
    git_url = 'https://github.com/ClangBuiltLinux/boot-utils'
    print(f"[+] Cloning {git_url} to {boot_utils}")
    subprocess.run(['git', 'clone', '--depth=1', git_url, boot_utils], check=True)

    print('[+] Calling tuxmake to build kernel')
    os.environ['PATH'] = f"{toolchain}/bin:{os.environ['PATH']}"
    output = Path('/output')
    result = tuxmake.build.build(
        build_dir=Path('/build'),
        kconfig=args.kconfigs[0],
        kconfig_add=args.kconfigs[1:],
        make_variables={'LLVM': '1', 'LLVM_IAS': '1'},
        output_dir=output,
        quiet=(not args.verbose),
        target_arch=args.arch,
        targets=['kernel'],
        toolchain='clang',
        tree=source,
    )
    if result.failed:
        sys.exit(1)

    boot_qemu = Path(boot_utils, 'boot-qemu.py')
    print(f"[+] Calling {boot_qemu.name}")
    subprocess.run([boot_qemu, '-a', args.arch, '-k', output], check=True)


if __name__ == '__main__':
    main()

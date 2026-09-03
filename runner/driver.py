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
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path
from typing import Any

import requests
import tuxmake.build

MIRROR_GIT = 'git://192.168.122.2'
MIRROR_HTTP = f"{MIRROR_GIT.replace('git', 'http')}:8080"
VALID_LLVM_VERS = tuple(range(23, 21, -1))
VALID_TREES = ('linux', 'linux-next')


def clone_mirror_repo(remote_repo_name: str, local_repo_name: str = '') -> Path:
    if not local_repo_name:
        local_repo_name = remote_repo_name

    local_repo = Path('/', local_repo_name)
    remote_repo = f"{MIRROR_GIT}/{remote_repo_name}.git"

    print(f"[+] Cloning {remote_repo} to {local_repo}")
    subprocess.run(['git', 'clone', '--depth=1', '--quiet', remote_repo, local_repo], check=True)

    info_cmd = ['git', '-C', local_repo, 'show', '-s', '--format=%H ("%s", %cs)']
    info_output = subprocess.run(
        info_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    branch_cmd = ['git', '-C', local_repo, 'rev-parse', '--abbrev-ref', 'HEAD']
    branch_output = subprocess.run(
        branch_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    print(f"[+] Successfully checked out {local_repo.name}: {branch_output}@{info_output}")

    return local_repo


def parse_arguments():
    parser = ArgumentParser(
        description='Build and boot driver', formatter_class=ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-a', '--arch', required=True, help='Architecture to build')
    parser.add_argument('-b', '--boot', action='store_true', help='Boot kernel after build')
    parser.add_argument(
        '-k', '--kconfigs', required=True, nargs='+', help='Kconfig values for tuxmake'
    )
    parser.add_argument(
        '-l',
        '--llvm-version',
        choices=VALID_LLVM_VERS,
        default=VALID_LLVM_VERS[0],
        type=int,
        help='LLVM version to build with',
    )
    parser.add_argument(
        '-t', '--tree', choices=VALID_TREES, default=VALID_TREES[0], help='Tree to build'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='Perform verbose build in tuxmake'
    )
    return parser.parse_args()


class Runner:
    def __init__(self) -> None:
        self.arch: str = ''
        self.boot: bool = False
        self.kconfigs: list[str] = []
        self.llvm_version: int = 0
        self.tree: str = ''
        self.verbose: bool = False

        self._boot_utils_arch: str = ''
        self._boot_utils_path: Path = Path()
        self._tuxmake_kwargs: dict[str, Any] = {
            'build_dir': Path('/build'),
            'kconfig': '',
            'kconfig_add': [],
            'kernel_image': None,
            'make_variables': {'LLVM': '1', 'LLVM_IAS': '1'},
            'output_dir': Path('/output'),
            'target_arch': '',
            'targets': [],
            'toolchain': 'clang',
            'tree': Path(),
            'verbose': False,
        }
        self._toolchain_prefix: Path = Path()

    def _prepare_toolchain(self) -> None:
        # Fetch latest available toolchains from mirror VM
        result = requests.get(f"{MIRROR_HTTP}/toolchains/latest_llvm_releases.json", timeout=15)
        result.raise_for_status()
        if not (toolchain_tarball := result.json().get(str(self.llvm_version))):
            msg = f"LLVM {self.llvm_version} requested but not in latest_llvm_releases.json?"
            raise RuntimeError(msg)

        # Download and extract toolchain into build container
        self._toolchain_prefix = Path('/', toolchain_tarball.replace('.tar.xz', ''))
        tar_url = f"{MIRROR_HTTP}/toolchains/{toolchain_tarball}"
        print(f"[+] Downloading {tar_url}")
        result = requests.get(tar_url, timeout=15)
        result.raise_for_status()
        print(f"[+] Extracting {toolchain_tarball} to {self._toolchain_prefix}")
        subprocess.run(
            ['tar', '-C', self._toolchain_prefix.parent, '-f', '-', '-J', '-x'],
            check=True,
            input=result.content,
        )

    def _prepare_git(self) -> None:
        self._tuxmake_kwargs['tree'] = clone_mirror_repo(self.tree)
        if self.boot:
            self._boot_utils_path = clone_mirror_repo('boot-utils')

    def _build(self) -> None:
        # It would be nicer to use LLVM=<prefix>/bin/ here but tuxmake ensures
        # the compiler is in PATH
        os.environ['PATH'] = f"{self._toolchain_prefix}/bin:{os.environ['PATH']}"

        print('[+] Calling tuxmake to build kernel')
        self._tuxmake_kwargs['kconfig'] = self.kconfigs[0]
        self._tuxmake_kwargs['kconfig_add'] += self.kconfigs[1:]
        self._tuxmake_kwargs['target_arch'] = self.arch
        self._tuxmake_kwargs['targets'].insert(0, 'kernel' if self.boot else 'default')
        self._tuxmake_kwargs['verbose'] = self.verbose
        if tuxmake.build.build(**self._tuxmake_kwargs).failed:
            sys.exit(1)

    def _boot(self) -> None:
        if not self.boot:
            return

        if not self._boot_utils_arch:
            self._boot_utils_arch = self.arch

        gh_releases_json_url = f"{MIRROR_HTTP}/boot-utils/releases.json"
        gh_releases_json = Path(gh_releases_json_url.replace(MIRROR_HTTP, ''))
        (result := requests.get(gh_releases_json_url, timeout=15)).raise_for_status()
        gh_releases_json.write_bytes(result.content)

        output_dir = self._tuxmake_kwargs['output_dir']
        if (dtbs_tar := Path(output_dir, 'dtbs.tar.xz')).exists():
            print(f"[+] Extracting {dtbs_tar}")
            subprocess.run(['tar', '-C', output_dir, '-xJf', dtbs_tar], check=True)

        boot_qemu_py = Path(self._boot_utils_path, 'boot-qemu.py')
        print(f"[+] Running {boot_qemu_py.name}")
        boot_qemu_py_cmd = [
            boot_qemu_py,
            '-a',
            self._boot_utils_arch,
            '--gh-json-file',
            gh_releases_json,
            '-k',
            output_dir,
        ]
        print(f"$ {' '.join(str(x) for x in boot_qemu_py_cmd)}")
        subprocess.run(boot_qemu_py_cmd, check=True)

    def run(self) -> None:
        # download toolchain
        self._prepare_toolchain()

        # clone git repositories
        self._prepare_git()

        # build kernel
        self._build()

        # boot kernel if requested
        self._boot()


class ARMRunner(Runner):
    def _boot(self) -> None:
        if 'multi_v5_defconfig' in self.kconfigs:
            self._boot_utils_arch = 'arm32_v5'
        if 'aspeed_g5_defconfig' in self.kconfigs:
            self._boot_utils_arch = 'arm32_v6'
        super()._boot()

    def _build(self) -> None:
        if 'multi_v5_defconfig' in self.kconfigs or 'aspeed_g5_defconfig' in self.kconfigs:
            self._tuxmake_kwargs['targets'].append('dtbs')
        super()._build()


class I386Runner(Runner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'x86'
        super()._boot()


class MipsRunner(Runner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'mips' if 'CONFIG_CPU_BIG_ENDIAN=y' in self.kconfigs else 'mipsel'
        super()._boot()

    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = 'vmlinux'
        super()._build()


class PowerPCRunner(Runner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'ppc64' if 'ppc64_guest_defconfig' in self.kconfigs else 'ppc64le'
        super()._boot()

    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = (
            'vmlinux' if 'ppc64_guest_defconfig' in self.kconfigs else 'zImage.epapr'
        )
        super()._build()


class RISCVRunner(Runner):
    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = 'Image'
        super()._build()


def main() -> None:
    args = parse_arguments()

    try:
        subprocess.run(['systemd-detect-virt', '-c'], capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as err:
        msg = f"Not running driver.py in a container? systemd-detect-virt shows '{err.stdout.strip()}'"
        raise RuntimeError(msg) from err

    arch_runners = {
        'arm': ARMRunner,
        'i386': I386Runner,
        'mips': MipsRunner,
        'powerpc': PowerPCRunner,
        'riscv': RISCVRunner,
    }
    runner: Runner = arch_runners.get(args.arch, Runner)()
    runner.arch = args.arch
    runner.boot = args.boot
    runner.kconfigs = args.kconfigs
    runner.llvm_version = args.llvm_version
    runner.tree = args.tree
    runner.verbose = args.verbose

    runner.run()


if __name__ == '__main__':
    main()

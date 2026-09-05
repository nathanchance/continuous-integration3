#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
#     "tuxmake>=1.45.0",
# ]
# ///

import json
import os
import re
import subprocess
import sys
import time
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path
from typing import Any

import requests
import tuxmake.build

MIRROR_GIT = 'git://192.168.122.2'
MIRROR_HTTP = f"{MIRROR_GIT.replace('git', 'http')}:8080"

VALID_LLVM_VERS = tuple(range(23, 21, -1))
VALID_STABLE_VERS = ('7.2',)
VALID_TREES = ('linux', 'linux-next', *[f"linux-stable-{ver}" for ver in VALID_STABLE_VERS])


def clone_mirror_repo(remote_repo_name: str, local_repo_name: str = '', branch: str = '') -> Path:
    repo_name_to_path = {
        'boot-utils': '/boot-utils.git',
        'linux': '/pub/scm/linux/kernel/git/torvalds/linux.git',
        'linux-next': '/pub/scm/linux/kernel/git/next/linux-next.git',
        'linux-stable': '/pub/scm/linux/kernel/git/stable/linux.git',
    }
    if not (remote_repo_path := repo_name_to_path.get(remote_repo_name)):
        print(f"[!] Do not know how to clone {remote_repo_name} from mirror!")
        sys.exit(1)

    remote_repo = f"{MIRROR_GIT}{remote_repo_path}"
    local_repo = Path('/', local_repo_name or remote_repo_name)

    print(f"[+] Cloning {remote_repo} to {local_repo}", end='', flush=True)
    start = time.time()
    git_clone_args = ['--depth=1', '--quiet']
    if branch:
        git_clone_args.append(f"--branch={branch}")
    subprocess.run(['git', 'clone', *git_clone_args, remote_repo, local_repo], check=True)
    print(f" [duration: {get_duration(start)}]", flush=True)

    info_cmd = ['git', '-C', local_repo, 'show', '-s', '--format=%H ("%s", %cs)']
    info_output = subprocess.run(
        info_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    branch_cmd = ['git', '-C', local_repo, 'rev-parse', '--abbrev-ref', 'HEAD']
    branch_output = subprocess.run(
        branch_cmd, capture_output=True, check=True, text=True
    ).stdout.strip()
    print(
        f"[+] Successfully checked out {local_repo.name} -> {branch_output} @ {info_output}", flush=True
    )

    return local_repo


def get_duration(start_seconds: float, end_seconds: float | None = None) -> str:
    if not end_seconds:
        end_seconds = time.time()
    seconds = int(end_seconds - start_seconds)
    days, seconds = divmod(seconds, 60 * 60 * 24)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    return ' '.join(parts)


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


def register_problem_matchers() -> None:
    if 'GITHUB_ACTIONS' not in os.environ:
        return

    if not (work := Path('/work')).exists():
        print('[!] Running in GitHub Actions but GITHUB_WORKSPACE is not mounted in?', flush=True)
        sys.exit(1)

    for problem_matcher in work.glob('.github/problem-matchers/*'):
        print(
            f"::add-matcher::{str(problem_matcher).replace('/work', os.environ['GITHUB_WORKSPACE'])}",
            flush=True,
        )


def validate_config(config_file: Path, kconfig_add: list[str]) -> None:
    requested_syms = {}
    for item in kconfig_add:
        if not item.startswith('CONFIG_'):
            continue
        sym, val = item.split('=', 1)
        requested_syms[sym] = val
    if not requested_syms:  # no symbols to check
        return

    config_syms = {}
    config_txt = config_file.read_text(encoding='utf-8')
    for sym, val in re.findall(
        r"^(?:# )?(CONFIG_[^= ]+)(?: |=)(.*)$", config_txt, flags=re.MULTILINE
    ):
        normalized_val = 'n' if val == 'is not set' else val
        if (existing_val := config_syms.get(sym)) and existing_val != normalized_val:
            print(
                f"[-] symbol '{sym}' already processed (dict val: '{existing_val}', new val: '{normalized_val}')?",
                flush=True,
            )
            continue
        config_syms[sym] = normalized_val

    fail = False
    for sym, expected_val in requested_syms.items():
        if (actual_val := config_syms.get(sym, 'n')) == expected_val:
            print(
                f"[+] value of {sym} ('{actual_val}') matched expected value ('{expected_val}')",
                flush=True,
            )
        else:
            print(
                f"[!] value of {sym} ('{actual_val}') does not match expected value ('{expected_val}')!",
                flush=True,
            )
            fail = True
    if fail:
        sys.exit(1)


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
            'make_variables': {
                'LLVM': '1',
                # This can go away when 5.15 is the minimum supported version by this driver
                # due to commit f12b034afeb3 ("scripts/Makefile.clang: default to LLVM_IAS=1")
                'LLVM_IAS': '1',
            },
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
        print(f"[+] Downloading {tar_url}", end='', flush=True)
        start = time.time()
        result = requests.get(tar_url, timeout=15)
        result.raise_for_status()
        print(f" [duration: {get_duration(start)}]", flush=True)

        print(f"[+] Extracting {toolchain_tarball} to {self._toolchain_prefix}", end='', flush=True)
        start = time.time()
        subprocess.run(
            ['tar', '-C', self._toolchain_prefix.parent, '-f', '-', '-J', '-x'],
            check=True,
            input=result.content,
        )
        print(f" [duration: {get_duration(start)}]", flush=True)

    def _prepare_git(self) -> None:
        branch = ''
        if self.tree.startswith('linux-stable'):
            self.tree, stable_ver = self.tree.rsplit('-', 1)
            branch = f"linux-{stable_ver}.y"
        self._tuxmake_kwargs['tree'] = clone_mirror_repo(
            self.tree, local_repo_name='source', branch=branch
        )
        if self.boot:
            self._boot_utils_path = clone_mirror_repo('boot-utils')

    def _build(self) -> None:
        # It would be nicer to use LLVM=<prefix>/bin/ here but tuxmake ensures
        # the compiler is in PATH
        os.environ['PATH'] = f"{self._toolchain_prefix}/bin:{os.environ['PATH']}"

        print('[+] Calling tuxmake to build kernel', flush=True)
        self._tuxmake_kwargs['kconfig'] = self.kconfigs[0]
        self._tuxmake_kwargs['kconfig_add'] += self.kconfigs[1:]
        self._tuxmake_kwargs['target_arch'] = self.arch
        self._tuxmake_kwargs['targets'].insert(0, 'kernel' if self.boot else 'default')
        self._tuxmake_kwargs['verbose'] = self.verbose
        tuxmake_res = tuxmake.build.build(**self._tuxmake_kwargs)

        output_dir = self._tuxmake_kwargs['output_dir']
        metadata = json.loads(output_dir.joinpath('metadata.json').read_text(encoding='utf-8'))
        results = metadata['results']
        tuxmake_duration = get_duration(0, round(sum(results['duration'].values()), 2))
        if tuxmake_res.failed:
            print(
                f"[!] tuxmake failed [duration: {tuxmake_duration}, errors: {results['errors']}]",
                flush=True,
            )
            sys.exit(1)

        print(
            f"[+] tuxmake succeeded [duration: {tuxmake_duration}, warnings: {results['warnings']}]",
            flush=True,
        )

        validate_config(output_dir.joinpath('config'), metadata['build']['kconfig_add'])

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
            print(f"[+] Extracting {dtbs_tar}", flush=True)
            subprocess.run(['tar', '-C', output_dir, '-xJf', dtbs_tar], check=True)

        boot_qemu_py = Path(self._boot_utils_path, 'boot-qemu.py')
        print(f"[+] Running {boot_qemu_py.name}", flush=True)
        boot_qemu_py_cmd = [
            boot_qemu_py,
            '-a',
            self._boot_utils_arch,
            '--gh-json-file',
            gh_releases_json,
            '-k',
            output_dir,
        ]
        print(f"$ {' '.join(str(x) for x in boot_qemu_py_cmd)}", flush=True)
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

    register_problem_matchers()

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

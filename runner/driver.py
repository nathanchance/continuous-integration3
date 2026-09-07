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


class MirrorRepo:
    def __init__(self, tree: str, local_path: Path | None = None, revision: str = '') -> None:
        tree_to_repo = {
            'linux': {
                'url': '/pub/scm/linux/kernel/git/torvalds/linux.git',
            },
            'linux-next': {
                'url': '/pub/scm/linux/kernel/git/next/linux-next.git',
            },
            'linux-stable': {
                'url': '/pub/scm/linux/kernel/git/stable/linux.git',
            },
        } | {
            item: {'url': f"/{item}.git", 'branch': 'main'}
            for item in ('boot-utils', 'llvm-project', 'tc-build')
        }

        ci_root = work if (work := Path('/work')).exists() else Path(__file__).resolve().parents[1]

        # Set this before normalization below
        self.patches_dir = Path(ci_root, 'patches', tree)

        # Normalize 'linux-stable-x.y' into 'linux-stable' tree with 'linux-x.y' branch
        if tree.startswith('linux-stable'):
            tree, stable_ver = tree.rsplit('-', 1)
            tree_to_repo[tree]['branch'] = f"linux-{stable_ver}.y"

        if not (tree_data := tree_to_repo.get(tree)):
            print(f"[!] Provided tree ('{tree}') does not exist on mirror!")
            sys.exit(1)

        self.tree: str = tree
        self.branch: str = tree_data.get('branch', 'master')
        self.revision: str = revision

        self.remote_path: str = f"{MIRROR_GIT}{tree_data['url']}"
        self.local_path: Path = local_path or Path('/', self.tree)

    def _git_quiet(self, cmd: list[Path | str], **kwargs) -> subprocess.CompletedProcess:
        return self._git(cmd, capture_output=True, **kwargs)

    def _git(self, cmd: list[Path | str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(['git', '-C', self.local_path, *cmd], check=True, text=True, **kwargs)

    def clone(self) -> Path:
        print(f"[+] Cloning {self.remote_path} to {self.local_path}", end='', flush=True)
        start = time.time()
        git_clone_cmd = [
            'git',
            '-c', 'advice.detachedHead=false',
            'clone',
            '--depth=1',
            '--quiet',
        ]  # fmt: skip
        if self.revision:
            git_clone_cmd.append(f"--revision={self.revision}")
        elif self.branch:
            git_clone_cmd.append(f"--branch={self.branch}")
        subprocess.run([*git_clone_cmd, self.remote_path, self.local_path], check=True)
        print(f" [duration: {get_duration(start)}]", flush=True)

        head_info = self._git_quiet(['show', '-s', '--format=%H ("%s", %cs)']).stdout.strip()
        branch = self._git_quiet(['rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
        print(
            f"[+] Successfully checked out {self.local_path.name} -> {branch} @ {head_info}",
            flush=True,
        )

        return self.local_path

    def gen_revision(self) -> None:
        latest_revision = (
            subprocess.run(
                ['git', 'ls-remote', self.remote_path, self.branch],
                capture_output=True,
                check=True,
                text=True,
            )
            .stdout.splitlines()[0]
            .split('\t', 1)[0]
        )

        if 'GITHUB_ACTIONS' in os.environ:
            with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as f:
                f.write(f"revision={latest_revision}\n")
        else:
            print(latest_revision)

    def apply_patches(self) -> None:
        if not (patches := list(self.patches_dir.glob('*.patch'))):
            return

        if not self.local_path.exists():
            self.clone()

        # Ensure that we can always commit regardless of whether user.name or
        # user.email are set in whatever environment we are running in, as this is
        # a temporary tree.
        git_name = 'check-patch-application'
        git_email = f"{git_name}@{os.uname().nodename}.local"
        git_commit_env_vars = {
            **os.environ,   # clone the environment, as subprocess may need it
            'GIT_AUTHOR_NAME': git_name,
            'GIT_AUTHOR_EMAIL': git_email,
            'GIT_COMMITTER_NAME': git_name,
            'GIT_COMMITTER_EMAIL': git_email,
        }  # fmt: skip

        print(f"[+] Applying patches in {self.patches_dir} to {self.local_path}")
        self._git(['am', '-3', *patches], env=git_commit_env_vars)


def parse_arguments():
    parser = ArgumentParser(
        description='GitHub Actions driver', formatter_class=ArgumentDefaultsHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='action', help='Subcommands', required=True)

    kernel_build_parser = subparsers.add_parser(
        'kernel-build', help='Perform a kernel build / boot via tuxmake'
    )
    kernel_build_parser.add_argument('-a', '--arch', required=True, help='Architecture to build')
    kernel_build_parser.add_argument(
        '-b', '--boot', action='store_true', help='Boot kernel after build'
    )
    kernel_build_parser.add_argument(
        '-k', '--kconfigs', required=True, nargs='+', help='Kconfig values for tuxmake'
    )
    kernel_build_parser.add_argument(
        '-l',
        '--llvm-version',
        choices=VALID_LLVM_VERS,
        default=VALID_LLVM_VERS[0],
        type=int,
        help='LLVM version to build with',
    )
    kernel_build_parser.add_argument('-r', '--revision', help='Revision to clone repository at')
    kernel_build_parser.add_argument(
        '-t', '--tree', choices=VALID_TREES, default=VALID_TREES[0], help='Tree to build'
    )
    kernel_build_parser.add_argument(
        '-v', '--verbose', action='store_true', help='Perform verbose build in tuxmake'
    )

    subparsers.add_parser('llvm-build', help='Perform a LLVM build via build-llvm.py')

    gen_rev_parser = subparsers.add_parser(
        'generate-revision',
        help='Generate git sha to be used as consistent revision throughout build',
    )
    gen_rev_parser.add_argument('tree', choices=VALID_TREES, help='Tree to generate revision for')

    check_patch_apply_parser = subparsers.add_parser(
        'check-patch-application', help='Check that vendored patches apply to repository'
    )
    check_patch_apply_parser.add_argument(
        '-r', '--revision', help='Revision to clone repository at'
    )
    check_patch_apply_parser.add_argument(
        'tree', choices=VALID_TREES, help='Tree to apply patches to'
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


class KernelRunner:
    def __init__(self) -> None:
        self.arch: str = ''
        self.boot: bool = False
        self.kconfigs: list[str] = []
        self.llvm_version: int = 0
        self.revision: str = ''
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
        tree_repo = MirrorRepo(self.tree, local_path=Path('/source'), revision=self.revision)
        self._tuxmake_kwargs['tree'] = tree_repo.clone()
        tree_repo.apply_patches()
        if self.boot:
            self._boot_utils_path = MirrorRepo('boot-utils').clone()

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


class ARMKernelRunner(KernelRunner):
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


class I386KernelRunner(KernelRunner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'x86'
        super()._boot()


class MipsKernelRunner(KernelRunner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'mips' if 'CONFIG_CPU_BIG_ENDIAN=y' in self.kconfigs else 'mipsel'
        super()._boot()

    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = 'vmlinux'
        super()._build()


class PowerPCKernelRunner(KernelRunner):
    def _boot(self) -> None:
        self._boot_utils_arch = 'ppc64' if 'ppc64_guest_defconfig' in self.kconfigs else 'ppc64le'
        super()._boot()

    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = (
            'vmlinux' if 'ppc64_guest_defconfig' in self.kconfigs else 'zImage.epapr'
        )
        super()._build()


class RISCVKernelRunner(KernelRunner):
    def _build(self) -> None:
        self._tuxmake_kwargs['kernel_image'] = 'Image'
        super()._build()


class LLVMRunner:
    def __init__(self) -> None:
        self.build = Path('/build')
        self.source = Path('/source')
        self.tc_build = Path('/tc-build')

        check_targets = [
            'clang',
            'lld',
            'llvm',
            'llvm-unit',
        ]
        install_targets = [
            'clang-resource-headers',
            'compiler-rt',
            'libclang',
            'libclang-headers',
            'llvm-as',
            'llvm-driver',
            'llvm-dwarfdump',
            'llvm-link',
            'llvm-strings',
        ]
        projects = [
            'clang',
            'compiler-rt',
            'lld',
        ]
        self.base_build_llvm_cmd = [
            Path(self.tc_build, 'build-llvm.py'),
            '--build-folder', self.build,
            '--check-targets', *check_targets,
            '--install-targets', *install_targets,
            '--llvm-folder', self.source,
            '--multicall',
            '--no-ccache',
            '--projects', *projects,
            '--quiet-cmake',
            '--show-build-commands',
        ]  # fmt: skip

    def _stage_one(self) -> None:
        MirrorRepo('llvm-project', local_path=self.source).clone()
        MirrorRepo('tc-build').clone()

        print('[+] Building stage one toolchain for initial qualification')
        stage_one_tc_cmd = [*self.base_build_llvm_cmd, '--build-stage1-only']
        print(f"$ {' '.join(map(str, stage_one_tc_cmd))}")
        subprocess.run(stage_one_tc_cmd, check=True)

    def run(self) -> None:
        self._stage_one()


def assert_container_env() -> None:
    try:
        subprocess.run(['systemd-detect-virt', '-c'], capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as err:
        msg = f"Not running driver.py in a container? systemd-detect-virt shows '{err.stdout.strip()}'"
        raise RuntimeError(msg) from err


def main() -> None:
    args = parse_arguments()

    if args.action == 'kernel-build':
        assert_container_env()

        register_problem_matchers()

        arch_runners = {
            'arm': ARMKernelRunner,
            'i386': I386KernelRunner,
            'mips': MipsKernelRunner,
            'powerpc': PowerPCKernelRunner,
            'riscv': RISCVKernelRunner,
        }
        runner: KernelRunner = arch_runners.get(args.arch, KernelRunner)()
        runner.arch = args.arch
        runner.boot = args.boot
        runner.kconfigs = args.kconfigs
        runner.llvm_version = args.llvm_version
        runner.revision = args.revision
        runner.tree = args.tree
        runner.verbose = args.verbose

        runner.run()

    if args.action == 'llvm-build':
        assert_container_env()

        LLVMRunner().run()

    if args.action == 'generate-revision':
        MirrorRepo(args.tree).gen_revision()

    if args.action == 'check-patch-application':
        MirrorRepo(args.tree, revision=args.revision).apply_patches()


if __name__ == '__main__':
    main()

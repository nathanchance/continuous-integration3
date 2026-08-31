#!/usr/bin/env python3

import json
import operator
import re
import shutil
import subprocess
from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory

# If this file has more than three parents, it is being run from within the
# repository, so we need to pivot the root properly
ROOT = MIRROR_PY.parents[1] if len((MIRROR_PY := Path(__file__).resolve()).parts) > 3 else Path('/')
GIT_DIR = Path(ROOT, 'srv/git')
HTTP_DIR = Path(ROOT, 'srv/http')
MIRROR_IP = '192.168.122.2'
RAW_GH_URL = 'https://github.com/nathanchance/continuous-integration3/raw/refs/heads/main'


def parse_arguments():
    parser = ArgumentParser(description='ClangBuiltLinux continuous-integration3 mirror management')
    subparsers = parser.add_subparsers(dest='action', help='Subcommands', required=True)

    subparsers.add_parser('setup', help='Perform initial mirror setup steps')

    update_parser = subparsers.add_parser('update', help='Update various files')
    update_parser.add_argument(
        'item', choices=('korg-llvm', 'boot-utils-assets', 'self'), help='Item to update'
    )

    subparsers.add_parser('prune', help=f"Prune {HTTP_DIR} of old, unneeded artifacts")

    return parser.parse_args()


def setup_srv_git() -> None:
    print('[+] Preparing git mirror with grokmirror')
    with TemporaryDirectory() as tempdir:
        # Pivot grokmirror.conf paths to their location on the host so that it
        # can prepared in advanced
        src_grok_config = Path(ROOT, 'etc/grokmirror/grokmirror.conf')
        src_grok_config_text = src_grok_config.read_text(encoding='utf-8')
        grok_log = Path(ROOT, 'var/log/grokmirror/main.log')

        dst_grok_config = Path(tempdir, src_grok_config.name)
        dst_grok_config_text = src_grok_config_text.replace('/srv', f"{ROOT}/srv").replace(
            '/var', f"{ROOT}/var"
        )
        dst_grok_config.write_text(dst_grok_config_text, encoding='utf-8')

        print('[+] Running grok-pull')
        GIT_DIR.mkdir(exist_ok=True, parents=True)
        grok_log.parent.mkdir(exist_ok=True, parents=True)
        subprocess.run(['grok-pull', '-c', dst_grok_config, '-v'], check=True)

        if not GIT_DIR.joinpath('fsck.status.js').exists():
            print('[+] Running grok-fsck for first time')
            subprocess.run(['grok-fsck', '-c', dst_grok_config, '-f', '-v'], check=True)

    repo_urls = [
        'https://github.com/ClangBuiltLinux/boot-utils.git',
    ]
    for repo_url in repo_urls:
        if (repo := Path(GIT_DIR, Path(repo_url).name)).exists():
            print(f"[+] Running grok-dumb-pull for {repo.name}")
            subprocess.run(['grok-dumb-pull', repo], check=True)
            continue

        print(f"[+] Cloning {repo_url} to {repo}")
        git_clone_cmd = ['git', 'clone', '--mirror', repo_url, repo]
        subprocess.run(git_clone_cmd, check=True)


def curl_filechk(local_path: Path, url: str) -> None:
    with TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir, local_path.name)
        print(f"[+] Downloading {url} to {temp_path}")
        subprocess.run(['curl', '-fLSs', '-o', temp_path, url], check=True)

        cmp_proc = subprocess.run(['cmp', '-s', local_path, temp_path], check=False)
        if cmp_proc.returncode == 0:  # no change from local file, don't update
            print(f"[-] {temp_path} does not differ from {local_path}, not updating...")
            return

        # File did not exist or it was different, update it
        print(f"[+] Atomically moving {temp_path} to {local_path}")
        local_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.move(temp_path, local_path)


def update_korg_llvm() -> None:
    llvm_releases_json = Path(HTTP_DIR, 'toolchains', 'latest_llvm_releases.json.new')
    llvm_releases_url = f"{RAW_GH_URL}/runner/{llvm_releases_json.with_suffix('').name}"
    curl_filechk(llvm_releases_json, llvm_releases_url)

    llvm_releases = json.loads(llvm_releases_json.read_text(encoding='utf-8'))
    for tarball in llvm_releases.values():
        if (tarball_dst := llvm_releases_json.parent.joinpath(tarball)).exists():
            print(f"[-] {tarball} already downloaded, skipping...")
            continue
        tarball_url = f"https://mirrors.kernel.org/pub/tools/llvm/files/{tarball}"
        print(f"[+] Downloading {tarball_url} to {tarball_dst}")
        subprocess.run(['curl', '-fLSs', '-o', tarball_dst, tarball_url], check=True)

    shutil.move(llvm_releases_json, llvm_releases_json.with_suffix(''))


def update_boot_utils_assets() -> None:
    reduced_boot_utils_releases_json = Path(HTTP_DIR, 'boot-utils', 'releases.json')
    boot_utils_releases_json = reduced_boot_utils_releases_json.with_suffix('.json.new')
    boot_utils_releases_json_url = (
        'https://api.github.com/repos/ClangBuiltLinux/boot-utils/releases/latest'
    )
    curl_filechk(boot_utils_releases_json, boot_utils_releases_json_url)

    # Reduce full JSON down to just what boot-utils needs, mirroring assets at the same time
    releases_json = json.loads(boot_utils_releases_json.read_text(encoding='utf-8'))
    tag_name = releases_json['tag_name']
    print(f"[+] Mirroring boot-utils images from {tag_name}")
    boot_utils_tag_assets = Path(boot_utils_releases_json.parent, 'assets', tag_name)
    boot_utils_tag_assets.mkdir(exist_ok=True, parents=True)
    postprocessed_json = {
        'tag_name': tag_name,
        'url': f"http://{MIRROR_IP}:8080/{'/'.join(boot_utils_tag_assets.parts[-3:])}",
        'assets': [],
    }
    for asset in releases_json['assets']:
        if (local_dst := Path(boot_utils_tag_assets, asset['name'])).exists():
            print(f"[-] {local_dst.name} exists in {local_dst.parent} already, skipping...")
        else:
            curl_filechk(local_dst, asset['browser_download_url'])
        postprocessed_json['assets'].append(
            {
                'name': local_dst.name,
                'browser_download_url': f"{postprocessed_json['url']}/{local_dst.name}",
            }
        )
    print(f"[+] Generating {reduced_boot_utils_releases_json}")
    with reduced_boot_utils_releases_json.open('w', encoding='utf-8') as f:
        json.dump(postprocessed_json, f, indent=2)
    boot_utils_releases_json.unlink()


def setup_srv_http() -> None:
    update_korg_llvm()
    update_boot_utils_assets()


def prune_srv_http() -> None:
    cached_llvm_tarballs: dict[str, list[tuple[tuple[int, ...], Path]]] = {}
    llvm_ver_re = re.compile(r"llvm-([0-9.rc-]+)-x86_64\.tar\.xz")

    for tarball in sorted(Path(HTTP_DIR, 'toolchains').glob('*.tar.xz'), reverse=True):
        if not (match := llvm_ver_re.match(tarball.name)):
            msg = f"Could not parse version from {tarball.name}!"
            raise RuntimeError(msg)
        llvm_maj, llvm_min, llvm_patch = match.groups()[0].split('.')
        if '-' in llvm_patch:
            llvm_patch, llvm_rc = llvm_patch.split('-')
            llvm_rc = llvm_rc.replace('rc', '')
        else:
            llvm_rc = '99'
        llvm_tuple = (int(llvm_maj), int(llvm_min), int(llvm_patch), int(llvm_rc))
        if llvm_maj in cached_llvm_tarballs:
            cached_llvm_tarballs[llvm_maj].append((llvm_tuple, tarball))
        else:
            cached_llvm_tarballs[llvm_maj] = [(llvm_tuple, tarball)]

    for llvm_maj, tarball_list in cached_llvm_tarballs.items():
        if len(tarball_list) < 4:  # save latest three minor versions
            print(f"[-] Not enough LLVM {llvm_maj} tarballs cached to prune, skipping...")
            continue
        sorted_tarball_list = sorted(tarball_list, key=operator.itemgetter(0), reverse=True)
        for item in sorted_tarball_list[3:]:
            tarball = item[1]
            print(f"[+] Removing {tarball.name}")
            tarball.unlink()


def main():
    args = parse_arguments()

    if args.action == 'setup':
        if Path('/') == ROOT:
            msg = 'setup must be run on host system, not in virtual machine!'
            raise RuntimeError(msg)
        setup_srv_git()
        setup_srv_http()

    if args.action == 'update':
        if args.item == 'korg-llvm':
            update_korg_llvm()
        if args.item == 'boot-utils-assets':
            update_boot_utils_assets()
        if args.item == 'self':
            mirror_py = Path(__file__).resolve()
            mirror_py_url = f"{RAW_GH_URL}/infra/vm/mkosi.profiles/mirror/mkosi.extra{mirror_py}"
            curl_filechk(mirror_py, mirror_py_url)
            mirror_py.chmod(0o755)

    if args.action == 'prune':
        prune_srv_http()


if __name__ == '__main__':
    main()

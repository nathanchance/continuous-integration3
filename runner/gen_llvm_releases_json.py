#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests>=2.34.2",
# ]
# ///

import json
import re
from pathlib import Path

import requests


def main():
    # Fetch list of kernel.org files
    print('[+] Fetching list of kernel.org LLVM toolchains')
    result = requests.get(
        'https://mirrors.kernel.org/pub/tools/llvm/files/sha256sums.asc', timeout=30
    )
    result.raise_for_status()

    # Parse index.html to get LLVM versions
    print('[+] Sifting through index.html to find latest LLVM toolchains')
    llvm_versions: dict[str, dict[str, tuple[int, ...] | str]] = {}
    for match in re.finditer(
        r"[0-9a-f]{40}\s+(llvm-([0-9.rc-]+)-x86_64\.tar\.xz)$", result.text, flags=re.MULTILINE
    ):
        tarball, version_str = match.group(1, 2)

        # Create a tuple from LLVM version to make comparing versions easy
        major, minor, patch = version_str.split('.')
        if '-' in patch:  # -rc version
            patch, rc = patch.split('-')
            rc = rc.replace('rc', '')
        else:
            rc = '99'  # make sure release versions outrank RC versions
        version = tuple(map(int, (major, minor, patch, rc)))

        # Check if current release is newer than previous latest release
        if latest_release := llvm_versions.get(major):
            if latest_release['version'] < version:  # ty: ignore[unsupported-operator]
                latest_release['version'] = version
                latest_release['tarball'] = tarball
        else:
            llvm_versions[major] = {'version': version, 'tarball': tarball}

    # Serialize llvm_versions to llvm_releases.json in latest to older
    latest_llvm_tarballs = {
        key: value['tarball'] for key, value in sorted(llvm_versions.items(), reverse=True)
    }
    llvm_releases_json = Path(__file__).resolve().parent.joinpath('latest_llvm_releases.json')
    print(f"[+] Writing results to {llvm_releases_json.name}")
    with llvm_releases_json.open('w', encoding='utf-8') as f:
        json.dump(latest_llvm_tarballs, f, indent=2)


if __name__ == '__main__':
    main()

#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pyyaml>=6.0.3",
# ]
# ///

import hashlib
import tomllib
from pathlib import Path
from typing import Any

import yaml

GENERATOR_ROOT = Path(__file__).resolve().parent


class Workflow:
    def __init__(self, config: dict[str, Any]) -> None:
        self.tree: str = config['tree']
        self.llvm_version: str = config['llvm_version']
        self.builds: list[dict[str, Any]] = config['builds']

        self._pretty_workflow_name = config.get(
            'pretty-name', f"{self.tree} / LLVM {self.llvm_version}"
        )
        self._output_name = config.get('output-name', f"{self.tree}-llvm-{self.llvm_version}")

    def _generate_build_jobs(self) -> dict[str, dict[str, Any]]:
        jobs = {}
        for build in self.builds:
            arch = build['arch']
            boot = build.get('boot', True)
            kconfigs = build['kconfigs']

            pretty_job_name = build.get('pretty-name', f"{arch} {' + '.join(kconfigs)}")

            podman_run_cmd = [
                'podman', 'run',
                '--env', 'GITHUB_ACTIONS',
                '--env', 'GITHUB_WORKSPACE',
                '--pull', 'newer',
                '--rm',
                '--tty',
                '--volume', '$GITHUB_WORKSPACE:/work:ro',
                'ghcr.io/nathanchance/ci3-kernel-build-env:latest',
                '/work/runner/driver.py',
                '-a', arch,
                '-k', *kconfigs,
                '-l', self.llvm_version,
                '-t', self.tree
            ]  # fmt: skip
            if boot:
                podman_run_cmd.insert(podman_run_cmd.index('-k'), '-b')

            encoded_job_name = f"{self.tree} {self.llvm_version} {pretty_job_name}".encode()
            job_id = '_' + hashlib.sha256(encoded_job_name).hexdigest()

            machine_type = 'normal' if kconfigs[0].endswith(('allnoconfig', 'defconfig')) else 'big'

            jobs[job_id] = {
                'name': pretty_job_name,
                'runs-on': ['self-hosted', machine_type],
                'steps': [
                    {
                        'name': 'Clone continuous-integration3',
                        'uses': 'actions/checkout@v7',
                    },
                    {
                        'name': f"Build{' and boot' if boot else ''} {pretty_job_name}",
                        'run': ' '.join(podman_run_cmd),
                    },
                ],
            }
        return jobs

    def generate(self) -> None:
        workflow_dst = Path(
            GENERATOR_ROOT.parent,
            f".github/workflows/generated-{self._output_name}.yml",
        )
        print(f"[+] Generating {workflow_dst}")

        workflow = {
            'name': self._pretty_workflow_name,
            'on': 'workflow_dispatch',
            'permissions': 'read-all',
            'jobs': self._generate_build_jobs(),
        }
        workflow_text = yaml.dump(workflow, Dumper=yaml.Dumper, width=1000, sort_keys=False)

        header = '# DO NOT MODIFY MANUALLY!\n# Regenerate with:\n# $ generator/generate.py\n'

        workflow_dst.write_text(f"{header}{workflow_text}", encoding='utf-8')


def main():
    for file in GENERATOR_ROOT.glob('data/*.toml'):
        with file.open('rb') as f:
            Workflow(tomllib.load(f)).generate()


if __name__ == '__main__':
    main()

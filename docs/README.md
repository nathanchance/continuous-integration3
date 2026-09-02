## Overview

Designed to run on a bare-metal server, such as those offered by [Hetzner](https://hetzner.com), using virtual machine disk image built programmatically with [`mkosi`](https://mkosi.systemd.io) and run via [`libvirt`](https://libvirt.org).

Each build server runs...
- A virtual machine to...
    - Mirror git repositories (refreshed every 15 minutes)
        - kernel sources from `git.kernel.org`
        - [boot-utils](https://github.com/ClangBuiltLinux/boot-utils)
    - Mirror LLVM toolchain tarballs from kernel.org (refreshed every six hours)
    - Mirror boot-utils filesystems from GitHub releases (refreshed every six hours)
    - Host a local OCI registry to act as a pull-through cache for `ghcr.io` image pulls
- A various number of virtual machines to run builds from GitHub Actions (as self-hosted runners)
    - "Normal" build machines have 8 vCPUs
    - "Big" build machines have 12 vCPUs

## Repository layout

- `infra/`: Infrastructure (setting up host machine, building virtual machine images)
- `runner/`: Build environment and driver
- `scripts/`: Hodgepodge scripts

## Workflow overview

[Example run](https://github.com/nathanchance/continuous-integration3/actions/runs/33371056728)

1. Virtual machine accepts job
2. Download build environment container image
3. Run `runner/driver.py` in container image
    1. Download and install requested toolchain tarball from mirror VM
    2. Clone requested kernel source from mirror VM
    3. If boot testing, clone `boot-utils` from mirror VM
    4. Build kernel image using `tuxmake`
    5. If boot testing, call `boot-qemu.py`

## Setup

### Initial one-time configuration on GitHub

- Generate [a fine-grained access token](https://github.com/settings/personal-access-tokens/new) to allow programmatically adding and deleting self-hosted runners
    ```
    Token name: continuous-integration3 self-hosted runner administration
    Resource owner: ClangBuiltLinux
    Expiration: 365 days (limited by organization)
    Repository access: Only continuous-integration3
    Permissions: Administration (Read and write)
    ```

### Per-host configuration

1. Run `installimage`
    ```
    COMMAND TBD, HAVE NOT TESTED ON ACTUAL HETZNER SERVER YET
    ```
2. `ssh` in as `root` and run `bootstrap_host.sh`
    ```
    wget -O - -q https://github.com/nathanchance/continuous-integration3/raw/main/infra/bootstrap_host.sh | sh
    ```
3. Switch to freshly created `cbl-admin` user
    ```
    machinectl shell --uid=cbl-admin
    ```
4. Build virtual machine images
    ```
    cd continuous-integration3 &&
    infra/build_base_builder_vm_image.sh &&
    infra/build_mirror_vm_image.sh
    ```
    The mirror image may take some time to build, as the mirrored content will be downloaded to the host then included in the image during the build process so that the mirror image can easily be recreated without needing to download all mirrored content from scratch.
5. Create virtual machines using `infra/vmm.py`
    ```
    run0 infra/vmm.py create -m -n 2 -b 1
    ```
    The above command creates a mirror virtual machine, two normal virtual machines, and one big virtual machine

    To setup `cbl-builder-vm` instances, provide the fine-grained access token created above for runner administration when prompted.

### Demonstration video

[![asciicast](https://asciinema.org/a/tgmBDOgeBtyiCbVT.svg)](https://asciinema.org/a/tgmBDOgeBtyiCbVT)

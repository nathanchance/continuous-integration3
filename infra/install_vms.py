#!/usr/bin/env python3

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ADMIN_NAME = 'cbl-admin'
LIBVIRT_STORE = Path('/home', ADMIN_NAME, 'libvirt')
VM_DIR = Path(LIBVIRT_STORE.parent, 'continuous-integration3/infra/vm')
BASE_VM_NAME = 'cbl-builder-vm'


def main():
    # Prechecks
    if os.geteuid() != 0:
        msg = 'root access is required!'
        raise RuntimeError(msg)

    if not (base_image := Path(VM_DIR, BASE_VM_NAME).with_suffix('.raw')).exists():
        msg = f"Base VM image ('{base_image}') does not exist, run {VM_DIR.parent}/build_base_vm_image.sh!"
        raise RuntimeError(msg)

    if not LIBVIRT_STORE.exists():
        msg = f"libvirt storage ('{LIBVIRT_STORE}') does not exist, was host properly configured?"
        raise RuntimeError(msg)

    # VMs are created sequentially, get first free slot based on number of VMs
    num_vms = len(list(LIBVIRT_STORE.glob(f"{BASE_VM_NAME}-*.raw")))
    new_vm_name = f"{BASE_VM_NAME}-{num_vms + 1}"

    # Make sure we don't try to overwrite an existing image in case VMs were
    # not sequentially deleted
    if (dst_image := Path(LIBVIRT_STORE, new_vm_name).with_suffix('.raw')).exists():
        msg = f"destination image ('{dst_image}') already exists, was VM not deleted with 'virsh undefine --remove-all-storage'?"
        raise RuntimeError(msg)

    # Copy base image and customize using 'systemd-firstboot'
    print(f"[+] Copying {base_image.name} to {dst_image}...")
    shutil.copy2(base_image, dst_image)
    systemd_firstboot_cmd = [
        'systemd-firstboot',
        '--force',
        f"--hostname={new_vm_name}",
        f"--image={dst_image}",
        '--setup-machine-id',
    ]
    print(f"[+] Running systemd-firstboot to customize {dst_image.name}")
    subprocess.run(systemd_firstboot_cmd, check=True)
    shutil.chown(dst_image, user=ADMIN_NAME, group=ADMIN_NAME)

    # Install using 'virt-install'
    print(f"[+] Running virt-install for {new_vm_name}...")
    virt_install_cmd = [
        'virt-install',
        '--name',
        new_vm_name,
        '--vcpus',
        '8',
        '--memory',
        str(8 * 2048),
        '--cpu',
        'host-model',
        '--network',
        'network=default',
        '--boot',
        'uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no',
        '--osinfo',
        'debian13',
        '--disk',
        dst_image,
        '--import',
        '--virt-type',
        'kvm',
        '--console',
        'pty,target_type=serial',
        '--graphics',
        'none',
        '--autoconsole',
        'none',
        '--autostart',
    ]
    subprocess.run(virt_install_cmd, check=True)

    limit = 45
    interval = 5
    print(f"[+] Waiting up to {limit} seconds for networking to come up...")
    for _i in range(int(limit / interval)):
        time.sleep(interval)
        domifaddr_txt = subprocess.run(
            ['virsh', 'domifaddr', new_vm_name], capture_output=True, check=True, text=True
        ).stdout
        if match := re.search(r"([0-9.]+)/\d+$", domifaddr_txt, flags=re.MULTILINE):
            break
    else:
        msg = f"IP address could not be found for {new_vm_name}!"
        raise RuntimeError(msg)

    print('[+] Opening ssh session into virtual machine')
    ssh_cmd = [
        'ssh',
        '-i',
        f"/home/{ADMIN_NAME}/.ssh/id_ed25519",
        '-o',
        'StrictHostKeyChecking=no',
        '-o',
        'UserKnownHostsFile=/dev/null',
        f"root@{match.groups()[0]}",
    ]
    subprocess.run(ssh_cmd, check=True)


if __name__ == '__main__':
    main()

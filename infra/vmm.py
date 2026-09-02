#!/usr/bin/env python3
# This cannot be run via uv because virt-install is a Python program that does
# not run properly in the isolated virtual environment (which is the biggest
# benefit of uv)

import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from argparse import ArgumentParser
from pathlib import Path

import requests

HOSTNAME = socket.gethostname()
ADMIN_HOME = Path('/home/cbl-admin')
BASE_BUILDER_IMG_NAME = 'cbl-builder-vm'
BASE_BUILDER_VM_NAME = f"{BASE_BUILDER_IMG_NAME}-{HOSTNAME}"
LIBVIRT_STORE = Path(ADMIN_HOME, 'libvirt')
MKOSI_OUTPUT = Path(ADMIN_HOME, 'continuous-integration3/infra/vm/mkosi.output')
MIRROR_VM_NAME = 'cbl-mirror-vm'
MIRROR_VM_IP = '192.168.122.2'


def parse_arguments():
    parser = ArgumentParser(
        description='ClangBuiltLinux continuous-integration3 virtual machine manager'
    )
    subparsers = parser.add_subparsers(dest='action', help='Subcommands', required=True)

    create_parser = subparsers.add_parser('create', help='Create virtual machines from base image')
    create_parser.add_argument(
        '-b',
        '--big',
        default=0,
        help='Number of big builder VMs to create (default: none)',
        type=int,
    )
    create_parser.add_argument(
        '-m', '--mirror', action='store_true', help='Create mirroring virtual machine'
    )
    create_parser.add_argument(
        '-n',
        '--normal',
        default=0,
        help='Number of normal builder VMs to create (default: none)',
        type=int,
    )
    create_parser.add_argument(
        '--skip-ssh',
        action='store_true',
        help='Do not ssh into virtual machines automatically after creation',
    )

    delete_parser = subparsers.add_parser('delete', help='Delete virtual machines')
    delete_parser.add_argument(
        '-d',
        '--deregister',
        action='store_true',
        help='Deregister virtual machines with GitHub upon deletion',
    )
    delete_parser.add_argument('vms', nargs='*', help='Virtual machines to delete')

    recreate_parser = subparsers.add_parser('recreate', help='Recreate existing virtual machine')
    recreate_parser.add_argument('type', choices=('big', 'normal'), help='Type of virtual machine')
    recreate_parser.add_argument('num', help='Virtual machine number', type=int)

    ssh_parser = subparsers.add_parser('ssh', help='ssh into virtual machine')
    ssh_parser.add_argument('name', nargs='?', help='Name of machine to ssh into')

    list_parser = subparsers.add_parser('list', help='List virtual machines')
    list_parser.add_argument('-p', '--plain', action='store_true', help='Just show machine names')

    update_parser = subparsers.add_parser('update', help='Update virtual machine operating system')
    group = update_parser.add_mutually_exclusive_group()
    group.add_argument('-a', '--all', action='store_true', help='Update all machines')
    group.add_argument('-m', '--machines', nargs='+', help='Update specified machines')

    deregister_parser = subparsers.add_parser(
        'deregister', help='Deregister virtual machines as GitHub runners'
    )
    group = deregister_parser.add_mutually_exclusive_group()
    group.add_argument(
        '-a',
        '--all-local',
        action='store_true',
        help='Deregister all locally defined machines with GitHub',
    )
    group.add_argument(
        '-A',
        '--all-remote',
        action='store_true',
        help='Deregister all self-hosted runners on GitHub',
    )
    group.add_argument(
        '-m', '--machines', nargs='+', help='Deregister specified machines with GitHub'
    )

    args = parser.parse_args()

    if args.action == 'create' and not (args.mirror or args.normal or args.big):
        parser.error('At least one of [-b | -m | -n] must be specified when creating a VM!')

    return args


def check_libvirt_storage() -> None:
    if not LIBVIRT_STORE.exists():
        msg = f"libvirt storage ('{LIBVIRT_STORE}') does not exist, was host properly configured?"
        raise RuntimeError(msg)


def check_root() -> None:
    if os.geteuid() != 0:
        msg = 'root access is required!'
        raise RuntimeError(msg)


def fzf(header: str, fzf_input: str, fzf_args: list[str] | None = None) -> list[str]:
    fzf_cmd = ['fzf', '--header', header]
    if fzf_args:
        fzf_cmd += fzf_args
    with subprocess.Popen(
        fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as fzf_proc:
        fzf_output = fzf_proc.communicate(fzf_input)[0]
        if '--multi' in fzf_cmd:
            return fzf_output.splitlines()
        return [fzf_output.strip()]


def create_builder_vms(
    num_normal: int, num_big: int, skip_ssh: bool = False, base: int = 0
) -> None:
    if num_normal == 0 and num_big == 0:
        return

    # Prechecks
    check_root()
    check_libvirt_storage()

    if not (base_image := Path(MKOSI_OUTPUT, BASE_BUILDER_IMG_NAME).with_suffix('.raw')).exists():
        msg = f"Base VM image ('{base_image}') does not exist, run {MKOSI_OUTPUT.parents[1]}/build_base_builder_vm_image.sh!"
        raise RuntimeError(msg)

    if base > 0 and num_normal > 0 and num_big > 0:
        msg = "Non-zero base with non-zero number of both big and normal VMs requested is invalid!"
        raise RuntimeError(msg)

    # VMs are created sequentially, get first free slot based on number of VMs
    new_vms = []
    for vm_type, num_new_vms in (('normal', num_normal), ('big', num_big)):
        new_base_vm_name = f"{BASE_BUILDER_VM_NAME}-{vm_type}"
        if num_new_vms:
            if base > 0:
                first_free = base
            else:
                first_free = len(list(LIBVIRT_STORE.glob(f"{new_base_vm_name}-*.raw"))) + 1
            new_vms += [
                f"{new_base_vm_name}-{count}"
                for count in range(first_free, first_free + num_new_vms)
            ]

    for vm_name in new_vms:
        create_vm(vm_name, base_image, skip_ssh, initial_setup_cmd='/opt/setup_vm.py && exit')


def create_mirror_vm(skip_ssh: bool = False) -> None:
    # Prechecks
    check_root()
    check_libvirt_storage()

    if not (
        base_image := Path(MKOSI_OUTPUT, vm_name := 'cbl-mirror-vm').with_suffix('.raw')
    ).exists():
        msg = f"Mirror VM image ('{base_image}') does not exist, run {MKOSI_OUTPUT.parents[1]}/build_mirror_vm_image.sh!"
        raise RuntimeError(msg)

    create_vm(vm_name, base_image, skip_ssh)


def create_vm(
    vm_name: str, base_image: Path, skip_ssh: bool = False, initial_setup_cmd: str = ''
) -> None:
    # Make sure we don't try to overwrite an existing image
    if (dst_image := Path(LIBVIRT_STORE, vm_name).with_suffix('.raw')).exists():
        msg = f"destination image ('{dst_image}') already exists, was VM not deleted with 'virsh undefine --remove-all-storage'?"
        raise RuntimeError(msg)

    # Copy base image and customize using 'systemd-firstboot'
    print(f"[+] Copying {base_image.name} to {dst_image}...")
    subprocess.run(['cp', base_image, dst_image], check=True)
    if base_image.name != dst_image.name:
        systemd_firstboot_cmd = [
            'systemd-firstboot',
            '--force',
            f"--hostname={vm_name}",
            f"--image={dst_image}",
            '--setup-machine-id',
        ]
        print(f"[+] Running systemd-firstboot to customize {dst_image.name}")
        subprocess.run(systemd_firstboot_cmd, check=True)
        shutil.chown(dst_image, user=ADMIN_HOME.name, group=ADMIN_HOME.name)

    # Install using 'virt-install'
    print(f"[+] Running virt-install for {vm_name}...")
    num_cpus = 12 if vm_name.rsplit('-', 2)[1] == 'big' else 8
    virt_install_cmd = [
        'virt-install',
        '--name',
        vm_name,
        '--vcpus',
        str(num_cpus),
        '--memory',
        str(num_cpus * 2048),
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

    if skip_ssh:
        print('[-] Skipping ssh session')
    elif initial_setup_cmd:
        limit = 45
        interval = 5
        iterations = int(limit / interval)
        print(f"[+] Waiting up to {limit} seconds for networking to come up...")
        for i in range(1, iterations):
            time.sleep(interval)
            ip_addr = get_vm_ip_addr(vm_name, required=(i == iterations))
            if ip_addr and vm_name != MIRROR_VM_NAME:
                break
            if vm_name == MIRROR_VM_NAME:
                ping_res = subprocess.run(
                    ['ping', '-c', '1', ip_addr], capture_output=True, check=False
                )
                if ping_res.returncode == 0:
                    time.sleep(interval)  # make sure ssh has come up
                    break

        print(f"[+] Opening ssh session into {vm_name} for setup")
        call_ssh(ip_addr, initial_setup_cmd)


def virsh_list(plain: bool = True, running: bool = False, sift: bool = True) -> str:
    virsh_cmd = ['virsh', 'list', '--state-running' if running else '--all']
    if plain:
        virsh_cmd.append('--name')
    elif sift:
        print('[-] sifting requested without plain, ignoring...')
        sift = False
    try:
        res = subprocess.run(virsh_cmd, capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as err:
        if err.stdout:
            print(err.stdout)
        if err.stderr:
            print(err.stderr)
        raise
    if not sift:
        return res.stdout
    return '\n'.join(
        sorted(
            item
            for item in res.stdout.splitlines()
            if item.startswith((BASE_BUILDER_VM_NAME, MIRROR_VM_NAME))
        )
    )


def get_running_vms() -> set[str]:
    return {item for item in virsh_list(running=True).splitlines() if item.strip()}


def get_vm_ip_addr(vm_name: str, required: bool = False) -> str:
    # We special case this because 'virsh domifaddr' only works with DHCP
    # leases by default. The qemu-guest-agent integration takes too much to
    # setup just for this and ARP only works if the IP address has been pinged,
    # which means we must know what it is already...
    if vm_name == MIRROR_VM_NAME:
        return MIRROR_VM_IP

    domifaddr_txt = subprocess.run(
        ['virsh', 'domifaddr', vm_name], capture_output=True, check=True, text=True
    ).stdout
    if match := re.search(r"([0-9.]+)/\d+$", domifaddr_txt, flags=re.MULTILINE):
        return match.groups()[0]
    if required:
        msg = f"IP address could not be found for {vm_name}!"
        raise RuntimeError(msg)
    return ''


def call_ssh(ip_addr: str, cmd: str = '') -> None:
    ssh_cmd = [
        'ssh',
        '-i',
        f"{ADMIN_HOME}/.ssh/id_ed25519",
        '-o',
        'StrictHostKeyChecking=no',
        '-o',
        'UserKnownHostsFile=/dev/null',
        '-t',
        f"root@{ip_addr}",
    ]
    if cmd:
        ssh_cmd.append(cmd)
    subprocess.run(ssh_cmd, check=True)


def ssh_vm(vm_name: str, cmd: str = '') -> None:
    if vm_name not in (running_vms := get_running_vms()):
        msg = f"Supplied VM name ('{vm_name}') is not in list of running VMs ('{', '.join(running_vms)}')!"
        raise RuntimeError(msg)
    call_ssh(get_vm_ip_addr(vm_name, required=True), cmd)


def delete_vms(vms: list[str], check: bool = True, deregister: bool = False) -> None:
    for vm in vms:
        print(f"[+] Deleting {vm} using virsh")
        subprocess.run(['virsh', 'destroy', vm], check=False)
        subprocess.run(['virsh', 'undefine', '--nvram', '--remove-all-storage', vm], check=check)
    if deregister:
        deregister_vms(vms)


def deregister_vms(vms: list[str], deregister_all: bool = False) -> None:
    if not (gh_token := os.environ.get('GITHUB_TOKEN')):
        gh_token = getpass.getpass(
            prompt='[+] GITHUB_TOKEN not set in environment, please provide one: '
        )
    request_headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f"Bearer {gh_token}",
        'X-GitHub-Api-Version': '2026-03-10',
    }
    repo = 'nathanchance/continuous-integration3'
    runners_endpoint = f"https://api.github.com/repos/{repo}/actions/runners"

    print('[+] Getting list of runners from GitHub API')
    result = requests.get(runners_endpoint, headers=request_headers, timeout=10)
    result.raise_for_status()
    existing_runners = {item['name']: item['id'] for item in result.json()['runners']}

    if deregister_all:
        vms += existing_runners.keys()

    for vm in vms:
        if vm not in existing_runners:
            print(f"[+] {vm} not registered with GitHub, skipping!")
            continue

        print(f"[+] Deleting {vm} from {repo} via GitHub API")
        requests.delete(
            f"{runners_endpoint}/{existing_runners[vm]}",
            headers=request_headers,
            timeout=10,
        ).raise_for_status()


def list_vms(plain: bool = False) -> None:
    virsh_list_items = virsh_list(plain=plain, sift=False).splitlines()
    filtered_items = [
        item
        for item in virsh_list_items
        if item.startswith((' Id', '-----'))
        or BASE_BUILDER_VM_NAME in item
        or MIRROR_VM_NAME in item
    ]
    print('\n'.join(filtered_items))


def main():
    args = parse_arguments()

    if args.action == 'create':
        if args.mirror:
            create_mirror_vm(args.skip_ssh)
        create_builder_vms(args.normal, args.big, args.skip_ssh)

    if args.action == 'delete':
        if not (vms := args.vms):
            if len((machines := virsh_list()).splitlines()) < 1:
                msg = 'delete action specified without any machines defined!'
                raise RuntimeError(msg)
            if not (vms := fzf('Machines to delete', machines, fzf_args=['--multi'])):
                print('[-] No machines selected, exiting...')
                sys.exit(0)
        delete_vms(vms, deregister=args.deregister)

    if args.action == 'recreate':
        check_root()
        delete_vms([f"{BASE_BUILDER_VM_NAME}-{args.type}-{args.num}"], check=False)
        create_builder_vms(
            1 if args.type == 'normal' else 0, 1 if args.type == 'big' else 0, base=args.num
        )

    if args.action == 'ssh':
        if not (name := args.name):
            running_vms = virsh_list(running=True)
            if not (name := fzf('Machine to ssh into', running_vms)[0]):
                print('[-] No machine selected, exiting...')
                sys.exit(0)
        ssh_vm(name)

    if args.action == 'list':
        list_vms(args.plain)

    if args.action == 'update':
        if args.all:
            vms = get_running_vms()
        elif args.machines:
            vms = args.machines
        else:
            running_vms = virsh_list(running=True)
            if not (vms := fzf('Machines to delete', running_vms, fzf_args=['--multi'])):
                print('[-] No machines selected, exiting...')
                sys.exit(0)
        for vm in vms:
            print(f"[+] Updating {vm}")
            ssh_vm(vm, 'apt update && apt upgrade -y')

    if args.action == 'deregister':
        all_builder_vms = [vm for vm in virsh_list().splitlines() if BASE_BUILDER_VM_NAME in vm]
        if args.all_remote:
            deregister_vms([], deregister_all=True)
            return
        if args.all_local:
            vms_to_deregister = all_builder_vms
        elif args.machines:
            vms_to_deregister = args.machines
        elif not (
            vms_to_deregister := fzf(
                'Machines to deregister', '\n'.join(all_builder_vms), fzf_args=['--multi']
            )
        ):
            print('[-] No machines selected, exiting...')
            sys.exit(0)
        deregister_vms(vms_to_deregister)


if __name__ == '__main__':
    main()

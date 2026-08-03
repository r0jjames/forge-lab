"""Render and read the generated ansible inventory.

The Terraform multipass provider does not expose VM addresses, so the inventory
is rendered from live backend state after every apply.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import multipass
from .proc import die

_HOST_RE = re.compile(r"^(\S+)\s+ansible_host=(\S+)")


def render(cluster: str, mgmt, compute) -> str:
    """Build the .ini for a cluster from its mgmt and compute nodes."""
    lines = ["[mgmt]"]
    lines += [f"{n.name} ansible_host={multipass.lan_ip(n)}" for n in mgmt]
    lines += ["", "[compute]"]
    lines += [f"{n.name} ansible_host={multipass.lan_ip(n)}" for n in compute]
    lines += [
        "",
        "[all:vars]",
        "ansible_user=ubuntu",
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'",
        f"cluster_name={cluster}",
    ]
    return "\n".join(lines) + "\n"


def parse_hosts(text: str) -> list:
    """[(name, ip)] for every host line, in file order, across all groups."""
    hosts = []
    for line in text.splitlines():
        match = _HOST_RE.match(line)
        if match:
            hosts.append((match.group(1), match.group(2)))
    return hosts


def find_duplicate_ips(text: str) -> list:
    ips = [ip for _, ip in parse_hosts(text)]
    return sorted({ip for ip in ips if ips.count(ip) > 1})


def mgmt_ip(text: str) -> str:
    """The first mgmt-group host address, or "" when the group is empty."""
    in_mgmt = False
    for line in text.splitlines():
        if line.startswith("["):
            in_mgmt = line.startswith("[mgmt]")
            continue
        if in_mgmt and "ansible_host=" in line:
            return line.split("ansible_host=", 1)[1].split()[0]
    return ""


def assert_unique_ips(path):
    """Fail loudly if the backend handed out duplicate or no IPs.

    Concurrent `multipass launch` calls race in multipassd's MAC allocation and
    every VM of the batch can come up with the SAME MAC, so DHCP hands them all
    one lease (see terraform.apply_retry's -parallelism=1). Without this check
    the bad state only surfaces later as an opaque ansible "No route to host"
    against a node that looks fine in `multipass list`.
    """
    path = Path(path)
    text = path.read_text()
    if not parse_hosts(text):
        die(f"inventory {path} has no hosts (multipass reported no IPs)")
    dupes = find_duplicate_ips(text)
    if dupes:
        die(
            f"duplicate node IP(s) [{' '.join(dupes)}] in {path} — multipass "
            "assigned the same MAC to several VMs; deprovision and re-run"
        )

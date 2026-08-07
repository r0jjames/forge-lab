"""Render and read the generated ansible inventory.

The Terraform multipass provider does not expose VM addresses, so the inventory
is rendered from live backend state after every apply.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import multipass
from .clusterconfig import CONTROL_ROLE
from .proc import die

_HOST_RE = re.compile(r"^(\S+)\s+ansible_host=(\S+)")
_NUM_RE = re.compile(r"(\d+)")

# The ansible group the k8s and dcos roles take their control node from. A
# group is always its role with '-' replaced by '_' (see NodeSpec.group), so
# derive it the same way rather than assume the role has no dash to replace.
CONTROL_GROUP = CONTROL_ROLE.replace("-", "_")


def _natural_key(name: str):
    """Split on digit runs so 'data-10' sorts after 'data-2', not before.

    multipass hands nodes back in an arbitrary order; several things depend
    on being able to name the "first" node of a group (e.g. verify.py probes
    groups['opensearch'][0]), so that first element must be a deterministic
    function of the name alone, not backend enumeration order.
    """
    return [int(tok) if tok.isdigit() else tok for tok in _NUM_RE.split(name)]


def render(cluster: str, groups, children) -> str:
    """Build the .ini for a cluster from an ordered {group: [Node]} mapping.

    Empty groups are still emitted: a `hosts: hdfs_datanode` play must resolve
    to zero hosts rather than fail on a group ansible has never heard of. Group
    order is the caller's dict order, but nodes within a group are always sorted
    by natural-sort name — see `_natural_key`.

    `children` is the {child: [group]} mapping the cluster's config derives —
    k8s_nodes for the cluster nodes, <technology>_nodes for each enabled
    technology that owns VMs. Nothing here knows which technologies exist.
    """
    lines = []
    for group, nodes in groups.items():
        lines.append(f"[{group}]")
        ordered = sorted(nodes, key=lambda n: _natural_key(n.name))
        lines += [f"{n.name} ansible_host={multipass.lan_ip(n)}" for n in ordered]
        lines.append("")
    for child, members in children.items():
        lines += [f"[{child}:children]", *members, ""]
    lines += [
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


def group_ips(text: str, group: str) -> list:
    """Every host address in `group`, in file order. [] when empty or absent."""
    ips = []
    in_group = False
    for line in text.splitlines():
        if line.startswith("["):
            in_group = line.strip() == f"[{group}]"
            continue
        if in_group and "ansible_host=" in line:
            ips.append(line.split("ansible_host=", 1)[1].split()[0])
    return ips


def first_ip(text: str, group: str) -> str:
    """The first host address in `group`, or "" when the group is empty."""
    ips = group_ips(text, group)
    return ips[0] if ips else ""


def control_ip(text: str) -> str:
    """The first management-group host address, or "" when the group is empty."""
    return first_ip(text, CONTROL_GROUP)


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

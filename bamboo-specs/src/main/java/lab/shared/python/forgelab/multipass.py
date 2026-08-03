"""The VM backend seam — everything that knows multipass is here.

Mirrors `terraform/modules/multipass/` on the Terraform side: swapping the VM
backend means replacing this module and that one, nothing else.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from . import proc

# A node also advertises its pod-network address once the CNI is up (10.244.x by
# default, see k8s_pod_cidr) and that one is unreachable from the host, so it is
# never the address we hand to ansible or ssh.
POD_NETWORK_PREFIX = "10.244."


class Node(NamedTuple):
    name: str
    ips: list


def parse_list(json_text: str, prefix: str = "") -> list:
    """Parse `multipass list --format json`, keeping names starting with prefix."""
    data = json.loads(json_text)
    return [
        Node(vm["name"], list(vm.get("ipv4", [])))
        for vm in data.get("list", [])
        if vm.get("name", "").startswith(prefix)
    ]


def lan_ip(node: Node) -> str:
    """The node's host-reachable address, skipping the pod network."""
    for ip in node.ips:
        if not ip.startswith(POD_NETWORK_PREFIX):
            return ip
    return ""


def list_vms(prefix: str = "") -> list:
    return parse_list(proc.run_out("multipass", "list", "--format", "json"), prefix)


def delete_purge(names):
    """Best-effort removal of leftover VMs — failures here must not abort a teardown."""
    for name in names:
        proc.run("multipass", "delete", "--purge", name, check=False)
    proc.run("multipass", "purge", check=False)

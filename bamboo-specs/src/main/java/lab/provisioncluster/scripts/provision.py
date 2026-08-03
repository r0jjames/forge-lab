#!/usr/bin/env python3
"""Provision a lab cluster: validate, terraform apply, install, verify."""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, inventory, multipass, paths, proc, registry, sshconf, terraform,
)
from forgelab import tfvars as tfvars_mod  # noqa: E402
from forgelab.tfvars import ADDONS, CLUSTER_TYPES, resolve_addons  # noqa: E402

import install  # noqa: E402

CLUSTER_NAME_RE = re.compile(r"[a-z0-9-]+")


# Which addon owns which VM role. Keycloak owns none — it runs on the k8s
# cluster the mgmt/compute nodes already form.
ADDON_NODE_ROLES = {"hdfs": "data", "splunk": "splunk"}


def node_count_overrides(addons) -> list:
    """`-var <role>_count=0` for every VM role whose addon is off.

    Counts are configured in the cluster's tfvars and only ever turned *off*
    here, so the addon list and the sizing file cannot disagree. `-var` beats
    `-var-file` on the terraform command line, which is what makes this work.
    """
    args = []
    for addon, role in ADDON_NODE_ROLES.items():
        if addon not in addons:
            args += ["-var", f"{role}_count=0"]
    return args


def main(argv):
    cluster = argv[0] if argv else ""
    if not cluster:
        proc.die("usage: provision.py <cluster_name> [cluster_type] [addons]")
    type_override = argv[1] if len(argv) > 1 else ""
    addons_override = argv[2] if len(argv) > 2 else ""

    # Stage 1: Validate
    proc.require_tools("terraform", "multipass", "ansible-playbook", "ssh")
    if not CLUSTER_NAME_RE.fullmatch(cluster):
        proc.die("cluster_name must match ^[a-z0-9-]+$")
    if multipass.list_vms(f"{cluster}-"):
        proc.die(f"VMs with prefix '{cluster}-' already exist; deprovision first")

    tfvars = tfvars_mod.resolve(cluster)
    if type_override:
        cluster_type, type_source = type_override, "the TYPE override"
    else:
        cluster_type = tfvars_mod.parse_cluster_type(tfvars.read_text())
        type_source = str(tfvars)
    if cluster_type not in CLUSTER_TYPES:
        proc.die(
            f"cluster_type must be k8s or dcos "
            f"(got '{cluster_type}' from {type_source})"
        )
    addons = resolve_addons(addons_override, tfvars.read_text(), str(tfvars))
    print(
        f"==> provisioning '{cluster}' type={cluster_type} "
        f"addons={','.join(addons) or 'none'} config={tfvars.name}"
    )

    # Stage 2: Provision (workspace per cluster, tfvars-driven)
    terraform.init()
    terraform.workspace_select_or_new(cluster)
    terraform.apply_retry(
        f"-var-file={tfvars}",
        "-var", f"cluster_name={cluster}",
        *node_count_overrides(addons),
        "-input=false",
    )

    # Render inventory from live multipass state (provider does not expose IPs)
    paths.INV_DIR.mkdir(parents=True, exist_ok=True)
    inv = paths.INV_DIR / f"{cluster}.ini"
    inv.write_text(
        inventory.render(
            cluster,
            {
                "mgmt": multipass.list_vms(f"{cluster}-mgmt-"),
                "compute": multipass.list_vms(f"{cluster}-compute-"),
                "data": multipass.list_vms(f"{cluster}-data-"),
                "splunk": multipass.list_vms(f"{cluster}-splunk-"),
            },
        )
    )
    inventory.assert_unique_ips(inv)
    print(f"==> inventory: {inv}")
    sshconf.write(cluster, inventory.parse_hosts(inv.read_text()))

    # Stage 3: Install. The roles report what they installed into this file.
    report = install.run(cluster, cluster_type, addons)

    # Stage 4: Verify
    proc.run(
        sys.executable,
        Path(__file__).resolve().parent / "verify.py",
        cluster,
        cluster_type,
    )

    # Stage 5: Register. Last, so the registry only lists healthy clusters.
    registry.write(
        cluster,
        cluster_type,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        registry.nodes_from(
            inventory.parse_hosts(inv.read_text()), tfvars_mod.parse(tfvars.read_text())
        ),
        registry.read_components(report),
        credentials=(
            credentials.path(cluster) if credentials.path(cluster).is_file() else ""
        ),
    )
    print(f"==> cluster '{cluster}' provisioned and verified")


if __name__ == "__main__":
    proc.main(main)

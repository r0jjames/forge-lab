#!/usr/bin/env python3
"""Provision a lab cluster: validate, terraform apply, install, verify."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, inventory, multipass, paths, planvars, proc, registry, sshconf,
    terraform,
)

import install  # noqa: E402

USAGE = "usage: provision.py <cluster_name> [cluster_config]"


def write_tfvars(cluster: str, config) -> Path:
    """Write the cluster's generated .tfvars.json and return its path.

    Terraform reads .tfvars.json natively, so the whole config reaches it as one
    file rather than a command line of -var flags — and a failed run leaves that
    file behind to show exactly what was applied.

    Kept after the run rather than deleted: deprovision.py reads it, so a
    teardown never depends on the cluster's config file still existing.
    """
    generated = paths.TF_DIR / ".generated"
    generated.mkdir(parents=True, exist_ok=True)
    path = generated / f"{cluster}.tfvars.json"
    payload = {"cluster_name": cluster, "nodes": config.nodes_map(cluster)}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def group_nodes(cluster: str, config) -> dict:
    """{group: [multipass.Node]} for every role the config declares.

    Rendered from live backend state because the Terraform multipass provider
    does not expose VM addresses. Roles with no VMs still get their group, so a
    play targeting one resolves to zero hosts instead of failing.
    """
    return {
        spec.group: multipass.list_vms(f"{cluster}-{spec.role}-")
        for spec in config.roles()
    }


def main(argv):
    # Stage 1: Validate. The plan variables and the whole config first, before a
    # tool lookup or a multipass round trip, so a typo costs seconds. The
    # Validate stage of the PROV plan runs the same checks earlier still, on any
    # agent.
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    proc.require_tools("terraform", "multipass", "ansible-playbook", "ssh")
    if multipass.list_vms(f"{cluster}-"):
        proc.die(f"VMs with prefix '{cluster}-' already exist; deprovision first")

    print(
        f"==> provisioning '{cluster}' type={config.cluster_type} "
        f"technologies={','.join(config.enabled()) or 'none'} "
        f"config={Path(config.source).name}"
    )

    # Stage 2: Provision (workspace per cluster, one generated var-file)
    tfvars = write_tfvars(cluster, config)
    terraform.init()
    terraform.workspace_select_or_new(cluster)
    terraform.apply_retry(f"-var-file={tfvars}", "-input=false")

    # Render inventory from live multipass state (provider does not expose IPs)
    paths.INV_DIR.mkdir(parents=True, exist_ok=True)
    inv = paths.INV_DIR / f"{cluster}.ini"
    inv.write_text(
        inventory.render(cluster, group_nodes(cluster, config), config.children())
    )
    inventory.assert_unique_ips(inv)
    print(f"==> inventory: {inv}")
    sshconf.write(cluster, inventory.parse_hosts(inv.read_text()))

    # Stage 3: Install. The roles report what they installed into this file.
    report = install.run(cluster, config)

    # Stage 4: Verify
    proc.run(
        sys.executable,
        Path(__file__).resolve().parent / "verify.py",
        cluster,
        config.cluster_type,
        ",".join(config.enabled()),
    )

    # Stage 5: Register. Last, so the registry only lists healthy clusters.
    registry.write(
        cluster,
        config.cluster_type,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        registry.nodes_from(
            cluster, inventory.parse_hosts(inv.read_text()), config.sizing_by_role()
        ),
        registry.read_components(report),
        credentials=(
            credentials.path(cluster) if credentials.path(cluster).is_file() else ""
        ),
    )
    print(f"==> cluster '{cluster}' provisioned and verified")


if __name__ == "__main__":
    proc.main(main)

#!/usr/bin/env python3
"""Tear down a lab cluster: terraform destroy, backend sweep, clean generated files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, multipass, paths, planvars, proc, registry, sshconf, terraform,
)

USAGE = "usage: deprovision.py <cluster_name>"


def generated_tfvars(cluster: str) -> Path:
    return paths.TF_DIR / ".generated" / f"{cluster}.tfvars.json"


def destroy_args(cluster: str) -> list:
    """What to hand `terraform destroy` for its required `nodes` variable.

    provision.py leaves its generated var-file behind for exactly this. When it
    is missing — an older cluster, or a checkout that never provisioned this one
    — an empty map is enough: destroy works from state, not from variables.
    Deliberately not read from cluster_configs/, so renaming or deleting a
    config can never strand a running cluster.
    """
    varfile = generated_tfvars(cluster)
    if varfile.is_file():
        return [f"-var-file={varfile}"]
    return ["-var", "nodes={}"]


def main(argv):
    cluster = planvars.require_cluster_name(argv[0] if argv else "", USAGE)
    proc.require_tools("terraform", "multipass")

    # 1. Terraform destroy (if workspace exists)
    terraform.init()
    if terraform.workspace_select(cluster):
        terraform.destroy(
            *destroy_args(cluster), "-var", f"cluster_name={cluster}", "-input=false"
        )
        terraform.workspace_delete(cluster)
    else:
        print(f"no terraform workspace '{cluster}' — skipping destroy")

    # 2. Backend sweep: purge any leftover VMs with the prefix
    leftovers = [node.name for node in multipass.list_vms(f"{cluster}-")]
    if leftovers:
        multipass.delete_purge(leftovers)

    # 3. Remove generated inventory + ssh config + registry entry
    (paths.INV_DIR / f"{cluster}.ini").unlink(missing_ok=True)
    generated_tfvars(cluster).unlink(missing_ok=True)
    sshconf.remove(cluster)
    # Say which registry this cleared: PROV and DEPROV run from separate Bamboo
    # checkouts, so a silent no-op here used to mean the file lived elsewhere.
    info = registry.path(cluster)
    if registry.remove(cluster):
        print(f"==> removed cluster info: {info}")
    else:
        print(f"==> no cluster info to remove at {info}")
    credentials.remove(cluster)
    print(f"==> cluster '{cluster}' fully deprovisioned")


if __name__ == "__main__":
    proc.main(main)

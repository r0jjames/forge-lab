#!/usr/bin/env python3
"""Tear down a lab cluster: terraform destroy, backend sweep, clean generated files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, multipass, paths, planvars, proc, registry, sshconf, terraform,
)
from forgelab import tfvars as tfvars_mod  # noqa: E402

USAGE = "usage: deprovision.py <cluster_name>"


def main(argv):
    cluster = planvars.require_cluster_name(argv[0] if argv else "", USAGE)
    proc.require_tools("terraform", "multipass")
    tfvars = tfvars_mod.resolve(cluster)

    # 1. Terraform destroy (if workspace exists)
    terraform.init()
    if terraform.workspace_select(cluster):
        terraform.destroy(
            f"-var-file={tfvars}", "-var", f"cluster_name={cluster}", "-input=false"
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

#!/usr/bin/env python3
"""Check the PROV plan variables and print what the run resolved to.

Runs as its own Bamboo stage, ahead of Provision and with no agent.role
requirement: it needs Python and the checkout, nothing else, so a typo fails in
seconds on whatever agent is free rather than after a wait for the host agent.
provision.py repeats the same call, so a hand-run `make provision` gets the
identical checks and the identical message. The `_prov` suffix keeps this
importable alongside the DEPROV plan's validate_deprov.py under test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import paths, planvars, proc  # noqa: E402

USAGE = "usage: validate_prov.py <cluster_name> [cluster_type] [addons]"


def source_of(override: str, placeholder: str, tfvars: Path, label: str) -> str:
    """Where a resolved value came from, for the printed summary."""
    if planvars.is_unset(override, placeholder):
        return f"from {tfvars.name}"
    return f"from the {label} plan variable"


def report(cluster, cluster_type, addons, tfvars, type_override, addons_override):
    """The resolved run, as lines. Pure — main prints them."""
    lines = [
        f"==> cluster_name {cluster}",
        f"==> cluster_type {cluster_type} "
        f"({source_of(type_override, planvars.PLACEHOLDER_TYPE, tfvars, 'cluster_type')})",
        f"==> addons       {','.join(addons) or 'none'} "
        f"({source_of(addons_override, planvars.PLACEHOLDER_ADDONS, tfvars, 'addons')})",
        f"==> sizing       {tfvars}",
    ]
    # A cluster with no tfvars of its own is legitimate — it gets the default
    # size — but it is also what a typo'd cluster_name looks like, and the VMs
    # coming up the wrong size is a slow way to find that out.
    if tfvars.name == "defaults.tfvars":
        lines.insert(
            0,
            f"WARNING: no {paths.CLUSTERS_DIR.name}/{cluster}.tfvars — "
            f"using defaults.tfvars sizing",
        )
    return lines


def main(argv):
    type_override = argv[1] if len(argv) > 1 else ""
    addons_override = argv[2] if len(argv) > 2 else ""
    cluster, cluster_type, addons, tfvars = planvars.resolve(
        argv[0] if argv else "", type_override, addons_override, USAGE
    )
    for line in report(
        cluster, cluster_type, addons, tfvars, type_override, addons_override
    ):
        print(line)


if __name__ == "__main__":
    proc.main(main)

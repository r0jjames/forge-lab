#!/usr/bin/env python3
"""Run the install stage against an already-provisioned cluster's inventory.

Split out of provision.py so a role can be iterated on with `make addons`
instead of a full rebuild. provision.py imports `run` rather than shelling out,
so there is exactly one code path.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import credentials, paths, proc  # noqa: E402
from forgelab import tfvars as tfvars_mod  # noqa: E402
from forgelab.tfvars import ADDONS, CLUSTER_TYPES, resolve_addons  # noqa: E402,F401


def extra_vars(cluster_type: str, addons, report, secret_values: dict) -> dict:
    """The -e payload for site.yml. Pure — the caller writes it out."""
    return {
        "cluster_type": cluster_type,
        "addons": ",".join(addons),
        "component_report": str(report),
        **secret_values,
    }


def run(cluster: str, cluster_type: str, addons):
    """Install everything the cluster asks for. Returns the component report path."""
    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster} — provision it first")

    secret_values = credentials.ensure(cluster, addons)

    # mkdtemp is 0700, and the vars file is opened 0600 and deleted after the
    # run: passwords must never reach argv, which is world-readable in `ps`.
    workdir = Path(tempfile.mkdtemp(prefix="forgelab-"))
    report = workdir / "components.json"
    varsfile = workdir / "extra-vars.json"
    handle = os.open(varsfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w") as out:
        json.dump(extra_vars(cluster_type, addons, report, secret_values), out)

    try:
        proc.run(
            "ansible-playbook",
            paths.SITE_YML,
            "-i", inv,
            "-e", f"@{varsfile}",
            env=paths.ansible_env(os.environ),
        )
    finally:
        varsfile.unlink(missing_ok=True)
    return report


def main(argv):
    cluster = argv[0] if argv else ""
    if not cluster:
        proc.die("usage: install.py <cluster_name> [cluster_type] [addons]")
    tfvars_path = tfvars_mod.resolve(cluster)
    text = tfvars_path.read_text()

    type_override = argv[1] if len(argv) > 1 else ""
    if type_override:
        cluster_type, type_source = type_override, "the TYPE override"
    else:
        cluster_type = tfvars_mod.parse_cluster_type(text)
        type_source = str(tfvars_path)
    if cluster_type not in CLUSTER_TYPES:
        proc.die(
            f"cluster_type must be k8s or dcos "
            f"(got '{cluster_type}' from {type_source})"
        )

    addons_override = argv[2] if len(argv) > 2 else ""
    addons = resolve_addons(addons_override, text, str(tfvars_path))
    run(cluster, cluster_type, addons)


if __name__ == "__main__":
    proc.main(main)

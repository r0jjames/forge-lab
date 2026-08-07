#!/usr/bin/env python3
"""Check the PROV plan variables and print what the run resolved to.

Runs as its own Bamboo stage, ahead of Provision and with no agent.role
requirement: it needs Python and the checkout, nothing else, so a bad config
fails in seconds on whatever agent is free rather than after a wait for the host
agent. provision.py repeats the same call, so a hand-run `make provision` gets
the identical checks and the identical message. The `_prov` suffix keeps this
importable alongside the DEPROV plan's validate_deprov.py under test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import planvars, proc  # noqa: E402

USAGE = "usage: validate_prov.py <cluster_name> [cluster_config]"

_HEAD = f"{'ROLE':<14} {'N':>3} {'CPU':>3} {'MEM':<5} {'DISK':<5}"


def _memory_gb(size: str) -> int:
    """A multipass size as whole gigabytes. 512M rounds down to 0, which is
    only ever a rounding artefact in a total nobody sizes a host from."""
    return int(size[:-1]) if size.endswith("G") else int(size[:-1]) // 1024


def report(cluster: str, config) -> list:
    """The resolved run, as lines. Pure — main prints them.

    The sizing roll-up is here rather than left to the apply because an
    over-sized cluster is cheap to catch now and expensive to catch when the
    host runs out of memory eight VMs in.
    """
    specs = config.roles()
    lines = [
        f"==> cluster_name   {cluster}",
        f"==> cluster_type   {config.cluster_type}",
        f"==> technologies   {','.join(config.enabled()) or 'none'}",
        f"==> config         {Path(config.source).name}",
        "",
        _HEAD,
    ]
    for spec in specs:
        lines.append(
            f"{spec.role:<14} {spec.count:>3} {spec.cpu:>3} "
            f"{spec.memory:<5} {spec.disk:<5}"
        )
    vms = sum(s.count for s in specs)
    cpus = sum(s.count * s.cpu for s in specs)
    memory = sum(s.count * _memory_gb(s.memory) for s in specs)
    lines += ["", f"==> total          {vms} VMs, {cpus} vCPU, {memory}G RAM"]
    return lines


def main(argv):
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    for line in report(cluster, config):
        print(line)


if __name__ == "__main__":
    proc.main(main)

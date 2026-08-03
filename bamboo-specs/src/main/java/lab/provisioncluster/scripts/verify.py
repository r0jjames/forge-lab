#!/usr/bin/env python3
"""Poll a freshly provisioned cluster until it reports healthy."""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import inventory, paths, proc  # noqa: E402

ATTEMPTS = 30
INTERVAL_SECONDS = 10
SSH_OPTS = (
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
)


def nodes_ready(kubectl_output: str) -> bool:
    """True when every line of `kubectl get nodes --no-headers` says Ready."""
    lines = [line for line in kubectl_output.splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        len(cols) > 1 and cols[1] == "Ready"
        for cols in (line.split() for line in lines)
    )


def _ssh(mgmt_ip: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-i", str(paths.SSH_KEY), *SSH_OPTS, f"ubuntu@{mgmt_ip}", command],
        capture_output=True,
        text=True,
    )


def _verify_k8s(mgmt_ip: str):
    timeout = ATTEMPTS * INTERVAL_SECONDS
    print(f"==> verify: waiting for all nodes Ready (timeout {timeout}s)")
    for _ in range(ATTEMPTS):
        result = _ssh(mgmt_ip, "kubectl get nodes --no-headers")
        if result.returncode == 0 and nodes_ready(result.stdout):
            print(result.stdout, end="")
            return
        time.sleep(INTERVAL_SECONDS)
    proc.die("nodes not all Ready within timeout")


def _verify_dcos(mgmt_ip: str):
    url = f"http://{mgmt_ip}/"
    print(f"==> verify: DC/OS UI health on {url}")
    for _ in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=INTERVAL_SECONDS):
                print("DC/OS UI reachable")
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(INTERVAL_SECONDS)
    proc.die("DC/OS UI not reachable within timeout")


def main(argv):
    if len(argv) < 2:
        proc.die("usage: verify.py <cluster_name> <cluster_type>")
    cluster, cluster_type = argv[0], argv[1]

    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster}")
    mgmt_ip = inventory.mgmt_ip(inv.read_text())
    if not mgmt_ip:
        proc.die("no mgmt host in inventory")

    if cluster_type == "k8s":
        _verify_k8s(mgmt_ip)
    elif cluster_type == "dcos":
        _verify_dcos(mgmt_ip)
    else:
        proc.die(f"unknown cluster_type: {cluster_type}")


if __name__ == "__main__":
    proc.main(main)

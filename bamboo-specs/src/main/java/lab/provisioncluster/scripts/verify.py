#!/usr/bin/env python3
"""Poll a freshly provisioned cluster until it reports healthy."""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import credentials, inventory, paths, proc  # noqa: E402

ATTEMPTS = 30
INTERVAL_SECONDS = 10
SSH_OPTS = (
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
)

# Task 9's role must seed exactly these — verify and the role are one contract.
KEYCLOAK_PORT = 30080
KEYCLOAK_REALM = "forgelab"
KEYCLOAK_CLIENT = "app"
KEYCLOAK_USER = "labuser"


def nodes_ready(kubectl_output: str) -> bool:
    """True when every line of `kubectl get nodes --no-headers` says Ready."""
    lines = [line for line in kubectl_output.splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        len(cols) > 1 and cols[1] == "Ready"
        for cols in (line.split() for line in lines)
    )


def default_storage_class(text: str) -> str:
    """The default StorageClass name in `kubectl get sc --no-headers` output.

    kubectl renders the default-class annotation as a `(default)` suffix on the
    name, which lands in the second whitespace-separated column.
    """
    for line in text.splitlines():
        cols = line.split()
        if len(cols) > 1 and cols[1] == "(default)":
            return cols[0]
    return ""


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
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die("nodes not all Ready within timeout")

    result = _ssh(mgmt_ip, "kubectl get storageclass --no-headers")
    if not default_storage_class(result.stdout):
        proc.die("no default StorageClass — local-path-provisioner did not install")
    result = _ssh(mgmt_ip, "k9s version --short")
    if result.returncode != 0:
        proc.die("k9s is not installed on the control plane node")
    print("default StorageClass and k9s present")


def field_from(payload: str, key: str) -> str:
    """A string field out of a JSON object, or "" for anything else."""
    try:
        data = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def _http(url: str, form=None) -> str:
    """GET, or POST a form. Returns the body, or "" on any failure."""
    data = urllib.parse.urlencode(form).encode() if form else None
    try:
        with urllib.request.urlopen(url, data=data, timeout=INTERVAL_SECONDS) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, OSError):
        return ""


def _verify_keycloak(mgmt_ip: str, password: str):
    base = f"http://{mgmt_ip}:{KEYCLOAK_PORT}/realms/{KEYCLOAK_REALM}"
    print(f"==> verify: keycloak realm at {base}")
    for _ in range(ATTEMPTS):
        if field_from(_http(f"{base}/.well-known/openid-configuration"), "issuer"):
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"keycloak realm '{KEYCLOAK_REALM}' never published a discovery document")

    if not password:
        proc.die("no keycloak_app_user_password in the cluster's credentials file")
    body = _http(
        f"{base}/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT,
            "username": KEYCLOAK_USER,
            "password": password,
        },
    )
    if not field_from(body, "access_token"):
        proc.die(f"keycloak issued no access_token for '{KEYCLOAK_USER}'")
    print(f"keycloak issued a token for {KEYCLOAK_USER}@{KEYCLOAK_REALM}")


HDFS_APP_DIR = "/user/app"
_LIVE_DATANODES_RE = re.compile(r"Live datanodes \((\d+)\)")


def live_datanodes(report: str) -> int:
    """The count from `Live datanodes (N):` in `hdfs dfsadmin -report` output."""
    match = _LIVE_DATANODES_RE.search(report)
    return int(match.group(1)) if match else 0


def _verify_hdfs(data_ip: str, expected: int):
    print(f"==> verify: {expected} live datanodes on {data_ip}")
    for _ in range(ATTEMPTS):
        result = _ssh(data_ip, "hdfs dfsadmin -report")
        if result.returncode == 0 and live_datanodes(result.stdout) >= expected:
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"fewer than {expected} live datanodes within timeout")

    # A report can be healthy while writes fail — prove a roundtrip too.
    token = "forgelab-verify"
    result = _ssh(
        data_ip,
        f"printf '{token}' | hdfs dfs -put -f - {HDFS_APP_DIR}/verify.txt "
        f"&& hdfs dfs -cat {HDFS_APP_DIR}/verify.txt",
    )
    if result.returncode != 0 or token not in result.stdout:
        proc.die(f"could not write and read back {HDFS_APP_DIR}/verify.txt")
    print(f"hdfs roundtripped a file through {HDFS_APP_DIR}")


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
        proc.die("usage: verify.py <cluster_name> <cluster_type> [addons]")
    cluster, cluster_type = argv[0], argv[1]
    addons = [a for a in (argv[2] if len(argv) > 2 else "").split(",") if a]

    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster}")
    text = inv.read_text()
    mgmt_ip = inventory.mgmt_ip(text)
    if not mgmt_ip:
        proc.die("no mgmt host in inventory")

    if cluster_type == "k8s":
        _verify_k8s(mgmt_ip)
    elif cluster_type == "dcos":
        _verify_dcos(mgmt_ip)
    else:
        proc.die(f"unknown cluster_type: {cluster_type}")

    secrets_values = credentials.read(cluster)
    if "keycloak" in addons:
        _verify_keycloak(mgmt_ip, secrets_values.get("keycloak_app_user_password", ""))
    if "hdfs" in addons:
        data_ip = inventory.first_ip(text, "data")
        if not data_ip:
            proc.die("hdfs is enabled but the inventory has no data hosts")
        _verify_hdfs(data_ip, len(inventory.group_ips(text, "data")))


if __name__ == "__main__":
    proc.main(main)

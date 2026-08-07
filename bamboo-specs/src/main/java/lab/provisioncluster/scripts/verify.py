#!/usr/bin/env python3
"""Poll a freshly provisioned cluster until it reports healthy."""

import base64
import json
import re
import ssl
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


def _ssh(control_ip: str, command: str) -> subprocess.CompletedProcess:
    """Run `command` on the remote host.

    No `stdin` parameter: nothing here needs one currently. If a future
    verifier has to hand a secret to a remote command, pipe it over the SSH
    channel via `subprocess.run(..., input=...)` rather than interpolating it
    into `command` itself — argv is world-readable via `ps` to any user on
    the box, so interpolation is the one thing to avoid.
    """
    return subprocess.run(
        ["ssh", "-i", str(paths.SSH_KEY), *SSH_OPTS, f"ubuntu@{control_ip}", command],
        capture_output=True,
        text=True,
    )


def _verify_k8s(control_ip: str):
    timeout = ATTEMPTS * INTERVAL_SECONDS
    print(f"==> verify: waiting for all nodes Ready (timeout {timeout}s)")
    for _ in range(ATTEMPTS):
        result = _ssh(control_ip, "kubectl get nodes --no-headers")
        if result.returncode == 0 and nodes_ready(result.stdout):
            print(result.stdout, end="")
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die("nodes not all Ready within timeout")

    result = _ssh(control_ip, "kubectl get storageclass --no-headers")
    if not default_storage_class(result.stdout):
        proc.die("no default StorageClass — local-path-provisioner did not install")
    result = _ssh(control_ip, "k9s version --short")
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


def _verify_keycloak(control_ip: str, password: str):
    base = f"http://{control_ip}:{KEYCLOAK_PORT}/realms/{KEYCLOAK_REALM}"
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


def _verify_hdfs(namenode_ip: str, expected: int):
    print(f"==> verify: {expected} live datanodes on {namenode_ip}")
    for _ in range(ATTEMPTS):
        result = _ssh(namenode_ip, "hdfs dfsadmin -report")
        if result.returncode == 0 and live_datanodes(result.stdout) >= expected:
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"fewer than {expected} live datanodes within timeout")

    # A report can be healthy while writes fail — prove a roundtrip too.
    token = "forgelab-verify"
    result = _ssh(
        namenode_ip,
        f"printf '{token}' | hdfs dfs -put -f - {HDFS_APP_DIR}/verify.txt "
        f"&& hdfs dfs -cat {HDFS_APP_DIR}/verify.txt",
    )
    if result.returncode != 0 or token not in result.stdout:
        proc.die(f"could not write and read back {HDFS_APP_DIR}/verify.txt")
    print(f"hdfs roundtripped a file through {HDFS_APP_DIR}")


# Keep in sync with roles/common/defaults/main.yml's common_fluentbit_opensearch_port
# and roles/opensearch/defaults/main.yml's opensearch_http_port.
OPENSEARCH_PORT = 9200
# Keep in sync with roles/common/defaults/main.yml's common_fluentbit_index.
OPENSEARCH_INDEX = "forgelab-logs"


def cluster_nodes(payload: str) -> int:
    """`number_of_nodes` from OpenSearch's /_cluster/health, or 0."""
    try:
        data = json.loads(payload)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    value = data.get("number_of_nodes", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def cluster_status(payload: str) -> str:
    """`status` from /_cluster/health ("green"/"yellow"/"red"), or ""."""
    return field_from(payload, "status")


def doc_count(payload: str) -> int:
    """`count` from OpenSearch's /_count, or 0."""
    try:
        data = json.loads(payload)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    value = data.get("count", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _verify_opensearch(node_ip: str, expected_nodes: int):
    base = f"http://{node_ip}:{OPENSEARCH_PORT}"
    print(f"==> verify: {expected_nodes}-node opensearch cluster at {base}")
    for _ in range(ATTEMPTS):
        payload = _http(f"{base}/_cluster/health")
        if (
            cluster_nodes(payload) >= expected_nodes
            and cluster_status(payload) in ("green", "yellow")
        ):
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"opensearch cluster not healthy with {expected_nodes} nodes within timeout")

    # A green cluster with no data flowing into it is still a failure — prove
    # the index is actually receiving documents, not just that the nodes joined.
    for _ in range(ATTEMPTS):
        count = doc_count(_http(f"{base}/{OPENSEARCH_INDEX}*/_count"))
        if count > 0:
            print(f"opensearch indexed {count} documents into {OPENSEARCH_INDEX}*")
            return
        time.sleep(INTERVAL_SECONDS)
    proc.die(f"opensearch index '{OPENSEARCH_INDEX}*' has no documents within timeout")


# Keep in sync with roles/splunk/defaults/main.yml.
SPLUNK_WEB_PORT = 8000
SPLUNK_MGMT_PORT = 8089
# Keep in sync with roles/splunkforwarder/defaults/main.yml's
# splunkforwarder_index_os: this is the index every forwarder writes to, so it
# is the one that proves the forwarding path end to end.
SPLUNK_OS_INDEX = "lab_os"


def cluster_peers_up(payload: str) -> int:
    """How many indexer peers the cluster manager reports as Up.

    Splunk's REST layer wraps everything in `entry[].content`, and a peer that
    has registered but is down still appears — with a status that is not "Up".
    """
    try:
        data = json.loads(payload)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    entries = data.get("entry", [])
    if not isinstance(entries, list):
        return 0
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("content"), dict)
        and entry["content"].get("status") == "Up"
    )


def search_result_count(payload: str) -> int:
    """The `count` from a one-row `stats count` search export.

    The export endpoint streams one JSON object per line rather than a single
    document, so this reads the first line that carries a result and stops.
    Splunk returns the count as a string, since every search field is text.
    """
    for line in payload.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        result = data.get("result")
        if isinstance(result, dict) and "count" in result:
            try:
                return int(result["count"])
            except (TypeError, ValueError):
                return 0
    return 0


def _splunk_rest(url: str, password: str, form=None) -> str:
    """GET or POST against a Splunk management port. "" on any failure.

    The management port speaks TLS with a certificate Splunk generates for
    itself at first start, so verification is switched off deliberately rather
    than by oversight — there is no CA in this lab to trust it against.
    """
    data = urllib.parse.urlencode(form).encode() if form else None
    request = urllib.request.Request(url, data=data)
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=INTERVAL_SECONDS, context=context) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, OSError):
        return ""


def _verify_splunk(search_head_ip: str, manager_ip: str, indexers: int, password: str):
    if not password:
        proc.die("no splunk_admin_password in the cluster's credentials file")

    web = f"http://{search_head_ip}:{SPLUNK_WEB_PORT}"
    print(f"==> verify: splunk search head at {web}")
    for _ in range(ATTEMPTS):
        if _http(f"{web}/en-US/account/login"):
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die("splunk search head web interface never answered")

    peers_url = (
        f"https://{manager_ip}:{SPLUNK_MGMT_PORT}"
        "/services/cluster/manager/peers?output_mode=json"
    )
    print(f"==> verify: {indexers} indexer peers up on {manager_ip}")
    for _ in range(ATTEMPTS):
        if cluster_peers_up(_splunk_rest(peers_url, password)) >= indexers:
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"fewer than {indexers} indexer peers Up within timeout")

    # A cluster with no data in it is still a failure. This search only returns
    # a non-zero count if forwarders on other VMs delivered events through the
    # indexers, so it proves the whole path rather than any one hop.
    export = f"https://{search_head_ip}:{SPLUNK_MGMT_PORT}/services/search/jobs/export"
    form = {
        "search": f"search index={SPLUNK_OS_INDEX} earliest=-1h | stats count",
        "output_mode": "json",
    }
    for _ in range(ATTEMPTS):
        count = search_result_count(_splunk_rest(export, password, form))
        if count > 0:
            print(f"splunk indexed {count} events into {SPLUNK_OS_INDEX}")
            return
        time.sleep(INTERVAL_SECONDS)
    proc.die(f"index '{SPLUNK_OS_INDEX}' has no forwarded events within timeout")


def _verify_dcos(control_ip: str):
    url = f"http://{control_ip}/"
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
    control_ip = inventory.control_ip(text)
    if not control_ip:
        proc.die("no management host in inventory")

    if cluster_type == "k8s":
        _verify_k8s(control_ip)
    elif cluster_type == "dcos":
        _verify_dcos(control_ip)
    else:
        proc.die(f"unknown cluster_type: {cluster_type}")

    secrets_values = credentials.read(cluster)
    if "keycloak" in addons:
        _verify_keycloak(control_ip, secrets_values.get("keycloak_app_user_password", ""))
    if "hdfs" in addons:
        namenode_ip = inventory.first_ip(text, "hdfs_namenode")
        if not namenode_ip:
            proc.die("hdfs is enabled but the inventory has no namenode host")
        datanodes = len(inventory.group_ips(text, "hdfs_datanode"))
        if not datanodes:
            proc.die("hdfs is enabled but the inventory has no datanode hosts")
        _verify_hdfs(namenode_ip, datanodes)
    if "opensearch" in addons:
        node_ip = inventory.first_ip(text, "opensearch_master")
        if not node_ip:
            proc.die("opensearch is enabled but the inventory has no opensearch hosts")
        _verify_opensearch(node_ip, len(inventory.group_ips(text, "opensearch_master")))
    if "splunk" in addons:
        search_head_ip = inventory.first_ip(text, "splunk_search_head")
        manager_ip = inventory.first_ip(text, "splunk_cluster_manager")
        if not search_head_ip or not manager_ip:
            proc.die("splunk is enabled but the inventory has no search head or "
                     "cluster manager host")
        _verify_splunk(
            search_head_ip,
            manager_ip,
            len(inventory.group_ips(text, "splunk_indexer")),
            secrets_values.get("splunk_admin_password", ""),
        )


if __name__ == "__main__":
    proc.main(main)

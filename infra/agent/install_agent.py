#!/usr/bin/env python3
"""Install the host-local Bamboo remote agent.

The installer jar is only downloadable from Bamboo behind an authenticated
admin session — scraping it anonymously just returns the login page. Since this
lab runs Bamboo inside the local Kubernetes cluster, we copy the jar straight
out of the server pod with `kubectl cp` instead: no login, no scraping, and
always the exact version the server runs.
"""

import base64
import os
import socket
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "bamboo-specs" / "src" / "main" / "java" / "lab" / "shared" / "python"
    ),
)

from forgelab import proc  # noqa: E402

BAMBOO_URL = os.environ.get("BAMBOO_URL", "http://localhost:8085")
AGENT_HOME = Path(
    os.environ.get("AGENT_HOME", Path.home() / ".forgelab" / "bamboo-agent-home")
)
AGENT_DIR = Path.home() / ".forgelab" / "agent"
BAMBOO_NAMESPACE = os.environ.get("BAMBOO_NAMESPACE", "ci")
BAMBOO_CONTAINER = os.environ.get("BAMBOO_CONTAINER", "bamboo")
AGENT_TOKEN_SECRET = os.environ.get("AGENT_TOKEN_SECRET", "bamboo-agent-token")
BROKER_HOST = os.environ.get("BROKER_HOST", "localhost")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "54663"))
BROKER_TIMEOUT_SECONDS = 5


def read_agent_token() -> str:
    """The agent must present the same security token the server was configured
    with. `make bootstrap` stores it in the bamboo-agent-token secret and hands
    the same value to the server via unattendedSetup, so default to reading it
    from there; an explicit AGENT_TOKEN env var still wins for one-off installs.
    """
    token = os.environ.get("AGENT_TOKEN", "")
    if token:
        return token
    encoded = proc.run_out(
        "kubectl", "-n", BAMBOO_NAMESPACE, "get", "secret", AGENT_TOKEN_SECRET,
        "-o", "jsonpath={.data.security-token}",
        check=False,
    )
    if not encoded:
        proc.die(
            f"No agent token: set AGENT_TOKEN or run 'make bootstrap' "
            f"(creates secret '{AGENT_TOKEN_SECRET}')"
        )
    return base64.b64decode(encoded).decode().strip()


def find_bamboo_pod() -> str:
    names = proc.run_out(
        "kubectl", "-n", BAMBOO_NAMESPACE, "get", "pods", "-o", "name", check=False
    ).splitlines()
    for name in names:
        pod = name.removeprefix("pod/")
        if "bamboo" in pod.lower() and "agent" not in pod.lower():
            return pod
    proc.die(
        f"No Bamboo pod found in namespace '{BAMBOO_NAMESPACE}' (is 'make up' done?)"
    )


def check_broker_reachable():
    """Preflight the JMS broker port.

    The agent authenticates over HTTP (8085) and then connects to the JMS broker
    (54663) — both reached through the `make ui` port-forward. A bare GET of
    /agentServer/ 404s even on a healthy server (the real endpoint is
    GetFingerprint.action), so test the broker port directly instead. Unattended
    setup (`make bootstrap`) starts the broker automatically; if it's
    unreachable the port-forward almost certainly isn't running.
    """
    try:
        with socket.create_connection(
            (BROKER_HOST, BROKER_PORT), timeout=BROKER_TIMEOUT_SECONDS
        ):
            return
    except OSError:
        proc.die(
            f"Agent JMS broker {BROKER_HOST}:{BROKER_PORT} not reachable — "
            "is 'make ui' running? (it forwards 54663)"
        )


def find_installer_jar(pod: str) -> str:
    jar = proc.run_out(
        "kubectl", "-n", BAMBOO_NAMESPACE, "exec", pod, "-c", BAMBOO_CONTAINER, "--",
        "sh", "-c",
        'find /opt/atlassian/bamboo -name "atlassian-bamboo-agent-installer-*.jar" '
        "2>/dev/null | head -1",
        check=False,
    ).strip()
    if not jar:
        proc.die(f"Installer jar not found inside pod {pod}")
    return jar


def main(argv):
    proc.require_tools("kubectl", "java")
    token = read_agent_token()
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    pod = find_bamboo_pod()
    check_broker_reachable()

    # Drop the cached JMS truststore. The agent pins the broker's TLS cert in
    # configuration/jmsclient.ts, but every `make reset` regenerates the broker
    # keystore (shared-home/configuration/broker.ks), so a truststore left over
    # from a previous server makes the agent reject the new cert with a TLS
    # 'certificate_unknown' alert — registration then fails at the JMS step. The
    # agent rebuilds this file from the current server on the next connect.
    (AGENT_HOME / "configuration" / "jmsclient.ts").unlink(missing_ok=True)

    jar_in_pod = find_installer_jar(pod)
    local_jar = AGENT_DIR / "agent-installer.jar"
    print(
        f"Copying {Path(jar_in_pod).name} from {BAMBOO_NAMESPACE}/{pod} ..."
    )
    proc.run(
        "kubectl", "cp",
        f"{BAMBOO_NAMESPACE}/{pod}:{jar_in_pod}", local_jar,
        "-c", BAMBOO_CONTAINER,
    )
    if not local_jar.is_file() or local_jar.stat().st_size == 0:
        proc.die("Copied jar is empty")

    # Agent home is set via the -Dbamboo.home property (before -jar), not a flag.
    proc.run(
        "java", f"-Dbamboo.home={AGENT_HOME}", "-jar", local_jar,
        f"{BAMBOO_URL}/agentServer/", "install", "-t", token,
    )
    print("Installed. Run: make agent-run")


if __name__ == "__main__":
    proc.main(main)

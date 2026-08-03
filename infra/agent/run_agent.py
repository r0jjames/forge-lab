#!/usr/bin/env python3
"""Run the host-local Bamboo agent in console mode."""

import os
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

# Capabilities this host agent must offer for the provisioning plans to run.
REQUIRED_TOOLS = ("terraform", "ansible-playbook", "multipass", "java")

CAPABILITY_LINE = "agent.role=host"


def seed_capability(caps_file: Path):
    """Advertise agent.role=host so the multipass provisioning plans (which
    require it) only ever schedule here. Without it Bamboo is free to send them
    to the containerized k8s agent, which has no terraform/multipass and fails
    with "ERROR: missing tool: terraform". Mirrors the k8s agent's agent.role=ci.
    """
    caps_file.parent.mkdir(parents=True, exist_ok=True)
    existing = caps_file.read_text().splitlines() if caps_file.is_file() else []
    if CAPABILITY_LINE in existing:
        return
    with caps_file.open("a") as handle:
        handle.write(f"{CAPABILITY_LINE}\n")
    print(f"Seeded {CAPABILITY_LINE} in {caps_file}")


def main(argv):
    proc.require_tools(*REQUIRED_TOOLS)
    seed_capability(AGENT_HOME / "bin" / "bamboo-capabilities.properties")

    # Agent home is set via the -Dbamboo.home property (before -jar), not a flag.
    # exec so the JVM replaces this process — no python wrapper left holding on
    # to a long-lived agent's signals.
    os.execvp(
        "java",
        [
            "java",
            f"-Dbamboo.home={AGENT_HOME}",
            "-jar", str(AGENT_DIR / "agent-installer.jar"),
            f"{BAMBOO_URL}/agentServer/", "console",
        ],
    )


if __name__ == "__main__":
    proc.main(main)

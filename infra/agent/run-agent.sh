#!/usr/bin/env bash
set -euo pipefail
BAMBOO_URL="${BAMBOO_URL:-http://localhost:8085}"
AGENT_HOME="${AGENT_HOME:-$HOME/.forgelab/bamboo-agent-home}"
AGENT_DIR="$HOME/.forgelab/agent"
# Sanity: capabilities this host agent must offer
for tool in terraform ansible-playbook multipass jq java; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool"; exit 1; }
done

# Advertise agent.role=host so the multipass provisioning plans (which require
# it) only ever schedule here. Without it Bamboo is free to send them to the
# containerized k8s agent, which has no terraform/multipass and fails with
# "ERROR: missing tool: terraform". Mirrors the k8s agent's agent.role=ci.
CAPS_FILE="$AGENT_HOME/bin/bamboo-capabilities.properties"
mkdir -p "$(dirname "$CAPS_FILE")"
if ! grep -qx 'agent.role=host' "$CAPS_FILE" 2>/dev/null; then
  printf 'agent.role=host\n' >> "$CAPS_FILE"
  echo "Seeded agent.role=host in $CAPS_FILE"
fi

# Agent home is set via the -Dbamboo.home property (before -jar), not a flag.
exec java -Dbamboo.home="$AGENT_HOME" -jar "$AGENT_DIR/agent-installer.jar" \
  "${BAMBOO_URL}/agentServer/" console

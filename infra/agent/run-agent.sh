#!/usr/bin/env bash
set -euo pipefail
BAMBOO_URL="${BAMBOO_URL:-http://localhost:8085}"
AGENT_HOME="${AGENT_HOME:-$HOME/.forgelab/bamboo-agent-home}"
AGENT_DIR="$HOME/.forgelab/agent"
# Sanity: capabilities this host agent must offer
for tool in terraform ansible-playbook multipass jq java; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool"; exit 1; }
done
exec java -jar "$AGENT_DIR/agent-installer.jar" \
  "${BAMBOO_URL}/agentServer/" console --home "$AGENT_HOME"

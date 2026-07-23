#!/usr/bin/env bash
set -euo pipefail
BAMBOO_URL="${BAMBOO_URL:-http://localhost:8085}"
AGENT_HOME="${AGENT_HOME:-$HOME/.forgelab/bamboo-agent-home}"
AGENT_DIR="$HOME/.forgelab/agent"
: "${AGENT_TOKEN:?Set AGENT_TOKEN (Bamboo admin > Agents > Install remote agent)}"

mkdir -p "$AGENT_DIR"
# Server serves the matching installer jar; version-agnostic scrape of the admin page link:
JAR_PATH=$(curl -fsS "${BAMBOO_URL}/admin/agent/addRemoteAgent.action" \
  | grep -oE 'agentServer/agentInstaller/atlassian-bamboo-agent-installer-[0-9.]+\.jar' \
  | head -1) || { echo "Could not discover installer jar; check BAMBOO_URL / login"; exit 1; }
curl -fSLo "$AGENT_DIR/agent-installer.jar" "${BAMBOO_URL}/${JAR_PATH}"
java -jar "$AGENT_DIR/agent-installer.jar" \
  "${BAMBOO_URL}/agentServer/" install -t "${AGENT_TOKEN}" --home "$AGENT_HOME"
echo "Installed. Run: make agent-run"

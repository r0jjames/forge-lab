#!/usr/bin/env bash
set -euo pipefail
# Install the host-local Bamboo remote agent.
#
# The installer jar is only downloadable from Bamboo behind an authenticated
# admin session — scraping it anonymously just returns the login page. Since
# this lab runs Bamboo inside the local Kubernetes cluster, we copy the jar
# straight out of the server pod with `kubectl cp` instead: no login, no
# scraping, and always the exact version the server runs.
BAMBOO_URL="${BAMBOO_URL:-http://localhost:8085}"
AGENT_HOME="${AGENT_HOME:-$HOME/.forgelab/bamboo-agent-home}"
AGENT_DIR="$HOME/.forgelab/agent"
BAMBOO_NAMESPACE="${BAMBOO_NAMESPACE:-ci}"
BAMBOO_CONTAINER="${BAMBOO_CONTAINER:-bamboo}"
: "${AGENT_TOKEN:?Set AGENT_TOKEN (Bamboo > Agents > Install remote agent; security token must be enabled)}"

command -v kubectl >/dev/null || { echo "kubectl not found (Rancher Desktop provides it)"; exit 1; }
command -v java >/dev/null || { echo "java not found (need a JDK on PATH)"; exit 1; }

mkdir -p "$AGENT_DIR"

# Find the Bamboo server pod in the CI namespace.
POD=$(kubectl -n "$BAMBOO_NAMESPACE" get pods -o name 2>/dev/null \
  | grep -i bamboo | grep -vi agent | head -1 | sed 's|^pod/||') \
  || true
[ -n "$POD" ] || { echo "No Bamboo pod found in namespace '$BAMBOO_NAMESPACE' (is 'make up' done?)"; exit 1; }

# Locate the installer jar inside the pod (version is part of the filename).
JAR_IN_POD=$(kubectl -n "$BAMBOO_NAMESPACE" exec "$POD" -c "$BAMBOO_CONTAINER" -- \
  sh -c 'find /opt/atlassian/bamboo -name "atlassian-bamboo-agent-installer-*.jar" 2>/dev/null | head -1') \
  || true
[ -n "$JAR_IN_POD" ] || { echo "Installer jar not found inside pod $POD"; exit 1; }

echo "Copying $(basename "$JAR_IN_POD") from $BAMBOO_NAMESPACE/$POD ..."
kubectl cp "${BAMBOO_NAMESPACE}/${POD}:${JAR_IN_POD}" "$AGENT_DIR/agent-installer.jar" -c "$BAMBOO_CONTAINER"
[ -s "$AGENT_DIR/agent-installer.jar" ] || { echo "Copied jar is empty"; exit 1; }

# Agent home is set via the -Dbamboo.home property (before -jar), not a flag.
java -Dbamboo.home="$AGENT_HOME" -jar "$AGENT_DIR/agent-installer.jar" \
  "${BAMBOO_URL}/agentServer/" install -t "${AGENT_TOKEN}"
echo "Installed. Run: make agent-run"

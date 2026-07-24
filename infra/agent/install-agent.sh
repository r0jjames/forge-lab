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
AGENT_TOKEN_SECRET="${AGENT_TOKEN_SECRET:-bamboo-agent-token}"

command -v kubectl >/dev/null || { echo "kubectl not found (Rancher Desktop provides it)"; exit 1; }

# The agent must present the same security token the server was configured with.
# `make bootstrap` stores it in the bamboo-agent-token secret and hands the same
# value to the server via unattendedSetup, so default to reading it from there;
# an explicit AGENT_TOKEN env var still wins for one-off manual installs.
if [ -z "${AGENT_TOKEN:-}" ]; then
  AGENT_TOKEN=$(kubectl -n "$BAMBOO_NAMESPACE" get secret "$AGENT_TOKEN_SECRET" \
    -o jsonpath='{.data.security-token}' 2>/dev/null | base64 -d) || true
fi
[ -n "${AGENT_TOKEN:-}" ] || { echo "No agent token: set AGENT_TOKEN or run 'make bootstrap' (creates secret '$AGENT_TOKEN_SECRET')"; exit 1; }
command -v java >/dev/null || { echo "java not found (need a JDK on PATH)"; exit 1; }

mkdir -p "$AGENT_DIR"

# Find the Bamboo server pod in the CI namespace.
POD=$(kubectl -n "$BAMBOO_NAMESPACE" get pods -o name 2>/dev/null \
  | grep -i bamboo | grep -vi agent | head -1 | sed 's|^pod/||') \
  || true
[ -n "$POD" ] || { echo "No Bamboo pod found in namespace '$BAMBOO_NAMESPACE' (is 'make up' done?)"; exit 1; }

# Preflight: the agent authenticates over HTTP (port 8085) and then connects to
# the JMS broker (54663) — both reached through the `make ui` port-forward. A
# bare GET of /agentServer/ 404s even on a healthy server (the real endpoint is
# GetFingerprint.action), so test the broker port directly instead. Unattended
# setup (`make bootstrap`) starts the broker automatically; if it's unreachable
# the port-forward almost certainly isn't running.
BROKER_HOST="${BROKER_HOST:-localhost}"
BROKER_PORT="${BROKER_PORT:-54663}"
if ! (exec 3<>"/dev/tcp/${BROKER_HOST}/${BROKER_PORT}") 2>/dev/null; then
  echo "Agent JMS broker ${BROKER_HOST}:${BROKER_PORT} not reachable — is 'make ui' running? (it forwards 54663)"
  exit 1
fi
exec 3>&- 2>/dev/null || true

# Drop the cached JMS truststore. The agent pins the broker's TLS cert in
# configuration/jmsclient.ts, but every `make reset` regenerates the broker
# keystore (shared-home/configuration/broker.ks), so a truststore left over
# from a previous server makes the agent reject the new cert with a TLS
# 'certificate_unknown' alert — registration then fails at the JMS step. The
# agent rebuilds this file from the current server on the next connect.
rm -f "$AGENT_HOME/configuration/jmsclient.ts"

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

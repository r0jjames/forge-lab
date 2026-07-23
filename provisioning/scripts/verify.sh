#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091 # lib.sh is a sibling script, not a shellcheck-followable input
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CLUSTER="${1:?usage: verify.sh <cluster_name> <cluster_type>}"
TYPE="${2:?usage: verify.sh <cluster_name> <cluster_type>}"
INV="$INV_DIR/${CLUSTER}.ini"
[ -f "$INV" ] || die "no inventory for $CLUSTER"
MGMT_IP=$(awk '/^\[mgmt\]/{f=1;next} /^\[/{f=0} f && /ansible_host/{split($2,a,"="); print a[2]; exit}' "$INV")
[ -n "$MGMT_IP" ] || die "no mgmt host in inventory"
SSH="ssh -i $HOME/.forgelab/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@$MGMT_IP"

case "$TYPE" in
  k8s)
    echo "==> verify: waiting for all nodes Ready (timeout 300s)"
    for _ in $(seq 1 30); do
      if OUT=$($SSH "kubectl get nodes --no-headers" 2>/dev/null); then
        if [ -n "$OUT" ] && ! printf '%s\n' "$OUT" | awk '{print $2}' | grep -qv '^Ready$'; then
          printf '%s\n' "$OUT"
          exit 0
        fi
      fi
      sleep 10
    done
    die "nodes not all Ready within timeout"
    ;;
  dcos)
    echo "==> verify: DC/OS UI health on http://$MGMT_IP/"
    for _ in $(seq 1 30); do
      curl -fsS -o /dev/null "http://$MGMT_IP/" && { echo "DC/OS UI reachable"; exit 0; }
      sleep 10
    done
    die "DC/OS UI not reachable within timeout"
    ;;
  *) die "unknown cluster_type: $TYPE" ;;
esac

#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=provisioning/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { die "usage: provision.sh <cluster_name> [cluster_type]"; }
CLUSTER="${1:-}"; [ -n "$CLUSTER" ] || usage
TYPE_OVERRIDE="${2:-}"

### Stage 1: Validate
require_tools terraform multipass jq ansible-playbook ssh
[[ "$CLUSTER" =~ ^[a-z0-9-]+$ ]] || die "cluster_name must match ^[a-z0-9-]+$"
if multipass list --format json | jq -e --arg p "${CLUSTER}-" \
     '.list[] | select(.name | startswith($p))' >/dev/null; then
  die "VMs with prefix '${CLUSTER}-' already exist; deprovision first"
fi
TFVARS="$(resolve_tfvars "$CLUSTER")"
if [ -n "$TYPE_OVERRIDE" ]; then
  CLUSTER_TYPE="$TYPE_OVERRIDE"
  TYPE_SOURCE="the TYPE override"
else
  CLUSTER_TYPE="$(awk -F'"' '/^cluster_type/ {print $2}' "$TFVARS")"
  TYPE_SOURCE="$TFVARS"
fi
[[ "$CLUSTER_TYPE" =~ ^(k8s|dcos)$ ]] || die "cluster_type must be k8s or dcos (got '$CLUSTER_TYPE' from $TYPE_SOURCE)"
echo "==> provisioning '$CLUSTER' type=$CLUSTER_TYPE config=$(basename "$TFVARS")"

### Stage 2: Provision (workspace per cluster, tfvars-driven)
terraform -chdir="$TF_DIR" init -input=false >/dev/null
terraform -chdir="$TF_DIR" workspace select "$CLUSTER" 2>/dev/null \
  || terraform -chdir="$TF_DIR" workspace new "$CLUSTER"
tf_apply_retry -var-file="$TFVARS" -var "cluster_name=$CLUSTER" -input=false

### Render inventory from live multipass state (provider does not expose IPs)
mkdir -p "$INV_DIR"
INV="$INV_DIR/${CLUSTER}.ini"
{
  echo "[mgmt]"
  multipass list --format json | jq -r --arg p "${CLUSTER}-mgmt-" \
    '.list[] | select(.name | startswith($p)) | "\(.name) ansible_host=\(.ipv4[0])"'
  echo ""
  echo "[compute]"
  multipass list --format json | jq -r --arg p "${CLUSTER}-compute-" \
    '.list[] | select(.name | startswith($p)) | "\(.name) ansible_host=\(.ipv4[0])"'
  echo ""
  echo "[all:vars]"
  echo "ansible_user=ubuntu"
  echo "ansible_ssh_private_key_file=~/.forgelab/id_ed25519"
  echo "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'"
  echo "cluster_name=${CLUSTER}"
} > "$INV"
echo "==> inventory: $INV"

### Stage 3: Install
ansible-playbook "$REPO_ROOT/provisioning/ansible/site.yml" \
  -i "$INV" -e "cluster_type=${CLUSTER_TYPE}"

### Stage 4: Verify
"$REPO_ROOT/provisioning/scripts/verify.sh" "$CLUSTER" "$CLUSTER_TYPE"
echo "==> cluster '$CLUSTER' provisioned and verified"

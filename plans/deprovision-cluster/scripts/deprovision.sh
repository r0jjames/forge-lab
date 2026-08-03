#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=plans/shared/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../shared/scripts/lib.sh"

CLUSTER="${1:?usage: deprovision.sh <cluster_name>}"
[[ "$CLUSTER" =~ ^[a-z0-9-]+$ ]] || die "cluster_name must match ^[a-z0-9-]+$"
require_tools terraform multipass jq
TFVARS="$(resolve_tfvars "$CLUSTER")"

### 1. Terraform destroy (if workspace exists)
terraform -chdir="$TF_DIR" init -input=false >/dev/null
if terraform -chdir="$TF_DIR" workspace select "$CLUSTER" 2>/dev/null; then
  terraform -chdir="$TF_DIR" destroy -auto-approve \
    -var-file="$TFVARS" -var "cluster_name=$CLUSTER" -input=false || true
  terraform -chdir="$TF_DIR" workspace select default
  terraform -chdir="$TF_DIR" workspace delete "$CLUSTER" || true
else
  echo "no terraform workspace '$CLUSTER' — skipping destroy"
fi

### 2. Backend sweep: purge any leftover VMs with the prefix
LEFTOVERS=$(multipass list --format json | jq -r --arg p "${CLUSTER}-" \
  '.list[] | select(.name | startswith($p)) | .name')
if [ -n "$LEFTOVERS" ]; then
  echo "$LEFTOVERS" | xargs -n1 multipass delete --purge || true
  multipass purge || true
fi

### 3. Remove generated inventory + ssh config
rm -f "$INV_DIR/${CLUSTER}.ini"
remove_ssh_config "$CLUSTER"
echo "==> cluster '$CLUSTER' fully deprovisioned"

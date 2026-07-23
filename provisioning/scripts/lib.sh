#!/usr/bin/env bash
# Shared helpers; sourced by provision.sh / deprovision.sh
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$REPO_ROOT/provisioning/terraform"
# shellcheck disable=SC2034 # consumed by provision.sh after sourcing this file
INV_DIR="$REPO_ROOT/provisioning/ansible/inventory"

die() { echo "ERROR: $*" >&2; exit 1; }

require_tools() {
  local t
  for t in "$@"; do command -v "$t" >/dev/null || die "missing tool: $t"; done
}

# clusters/<name>.tfvars if present, else defaults.tfvars
resolve_tfvars() {
  local name="$1" f
  f="$REPO_ROOT/clusters/${name}.tfvars"
  [ -f "$f" ] || f="$REPO_ROOT/clusters/defaults.tfvars"
  [ -f "$f" ] || die "no tfvars found (need clusters/defaults.tfvars)"
  echo "$f"
}

# Retry apply once: multipass provider has transient failures (R4)
tf_apply_retry() {
  terraform -chdir="$TF_DIR" apply -auto-approve "$@" && return 0
  echo "terraform apply failed once — retrying in 10s (multipass flakiness)" >&2
  sleep 10
  terraform -chdir="$TF_DIR" apply -auto-approve "$@"
}

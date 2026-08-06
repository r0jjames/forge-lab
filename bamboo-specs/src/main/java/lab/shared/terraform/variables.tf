variable "cluster_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.cluster_name))
    error_message = "cluster_name must match ^[a-z0-9-]+$."
  }
}

# Every VM of the cluster, keyed by name, expanded from the cluster's YAML
# config by forgelab/clusterconfig.py. Deliberately has no default: a stray
# `terraform apply` without a var-file must fail rather than resolve this to an
# empty map and destroy the whole workspace.
variable "nodes" {
  type = map(object({
    cpus   = number
    memory = string
    disk   = string
  }))
}

variable "backend" {
  type    = string
  default = "multipass"
}

variable "image" {
  type    = string
  default = "noble"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.forgelab/id_ed25519.pub"
}

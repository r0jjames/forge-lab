variable "cluster_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.cluster_name))
    error_message = "cluster_name must match ^[a-z0-9-]+$."
  }
}
variable "cluster_type" {
  type    = string
  default = "k8s"
  validation {
    condition     = contains(["k8s", "dcos"], var.cluster_type)
    error_message = "cluster_type must be k8s or dcos."
  }
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
variable "mgmt_count" {
  type    = number
  default = 1
}
variable "compute_count" {
  type    = number
  default = 2
}
variable "mgmt_cpu" {
  type    = number
  default = 2
}
variable "mgmt_mem" {
  type    = string
  default = "4G"
}
variable "mgmt_disk" {
  type    = string
  default = "20G"
}
variable "compute_cpu" {
  type    = number
  default = 2
}
variable "compute_mem" {
  type    = string
  default = "3G"
}
variable "compute_disk" {
  type    = string
  default = "20G"
}
variable "addons" {
  type        = string
  default     = ""
  description = "Comma-separated addon names. provision.py derives node counts from it; terraform only records it."
}
variable "data_count" {
  type    = number
  default = 0
}
variable "data_cpu" {
  type    = number
  default = 2
}
variable "data_mem" {
  type    = string
  default = "4G"
}
variable "data_disk" {
  type    = string
  default = "40G"
}
variable "splunk_count" {
  type    = number
  default = 0
}
variable "splunk_cpu" {
  type    = number
  default = 2
}
variable "splunk_mem" {
  type    = string
  default = "6G"
}
variable "splunk_disk" {
  type    = string
  default = "40G"
}

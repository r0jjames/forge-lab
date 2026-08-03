terraform {
  required_version = ">= 1.5"
}

locals {
  mgmt_nodes = {
    for i in range(var.mgmt_count) :
    "${var.cluster_name}-mgmt-${i + 1}" => {
      cpus = var.mgmt_cpu, memory = var.mgmt_mem, disk = var.mgmt_disk
    }
  }
  compute_nodes = {
    for i in range(var.compute_count) :
    "${var.cluster_name}-compute-${i + 1}" => {
      cpus = var.compute_cpu, memory = var.compute_mem, disk = var.compute_disk
    }
  }
  data_nodes = {
    for i in range(var.data_count) :
    "${var.cluster_name}-data-${i + 1}" => {
      cpus = var.data_cpu, memory = var.data_mem, disk = var.data_disk
    }
  }
  splunk_nodes = {
    for i in range(var.splunk_count) :
    "${var.cluster_name}-splunk-${i + 1}" => {
      cpus = var.splunk_cpu, memory = var.splunk_mem, disk = var.splunk_disk
    }
  }
}

resource "local_file" "cloud_init" {
  filename = "${path.module}/.generated/${var.cluster_name}-cloud-init.yaml"
  content = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
  })
}

module "vms" {
  source = "./modules/multipass"
  nodes = merge(
    local.mgmt_nodes,
    local.compute_nodes,
    local.data_nodes,
    local.splunk_nodes,
  )
  image          = var.image
  cloudinit_file = local_file.cloud_init.filename
}

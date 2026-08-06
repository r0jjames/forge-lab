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
  # Non-HA HDFS has exactly one NameNode, so this is derived rather than a
  # variable that can only be set wrong. provision.py turns the hdfs addon off
  # with `-var datanode_count=0`, which takes the NameNode with it.
  namenode_count = var.datanode_count > 0 ? 1 : 0
  namenode_nodes = {
    for i in range(local.namenode_count) :
    "${var.cluster_name}-namenode-${i + 1}" => {
      cpus = var.namenode_cpu, memory = var.namenode_mem, disk = var.namenode_disk
    }
  }
  datanode_nodes = {
    for i in range(var.datanode_count) :
    "${var.cluster_name}-datanode-${i + 1}" => {
      cpus = var.datanode_cpu, memory = var.datanode_mem, disk = var.datanode_disk
    }
  }
  opensearch_nodes = {
    for i in range(var.opensearch_count) :
    "${var.cluster_name}-opensearch-${i + 1}" => {
      cpus = var.opensearch_cpu, memory = var.opensearch_mem, disk = var.opensearch_disk
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
    local.namenode_nodes,
    local.datanode_nodes,
    local.opensearch_nodes,
  )
  image          = var.image
  cloudinit_file = local_file.cloud_init.filename
}

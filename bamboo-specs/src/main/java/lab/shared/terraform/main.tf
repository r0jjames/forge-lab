terraform {
  required_version = ">= 1.5"
}

resource "local_file" "cloud_init" {
  filename = "${path.module}/.generated/${var.cluster_name}-cloud-init.yaml"
  content = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
  })
}

module "vms" {
  source         = "./modules/multipass"
  nodes          = var.nodes
  image          = var.image
  cloudinit_file = local_file.cloud_init.filename
}

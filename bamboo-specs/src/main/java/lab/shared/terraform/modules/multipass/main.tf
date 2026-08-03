terraform {
  required_providers {
    multipass = {
      source  = "larstobi/multipass"
      version = "~> 1.4"
    }
  }
}

resource "multipass_instance" "node" {
  for_each       = var.nodes
  name           = each.key
  cpus           = each.value.cpus
  memory         = each.value.memory
  disk           = each.value.disk
  image          = var.image
  cloudinit_file = var.cloudinit_file
}

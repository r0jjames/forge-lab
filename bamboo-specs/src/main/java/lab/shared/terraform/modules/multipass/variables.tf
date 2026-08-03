variable "nodes" {
  description = "Map of VM name => spec"
  type = map(object({
    cpus   = number
    memory = string
    disk   = string
  }))
}
variable "image" {
  type = string
}
variable "cloudinit_file" {
  type = string
}

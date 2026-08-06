output "node_names" {
  description = "All VM names, in config order"
  value       = keys(var.nodes)
}

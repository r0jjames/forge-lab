output "node_names" {
  description = "All VM names, mgmt nodes first"
  value       = concat(keys(local.mgmt_nodes), keys(local.compute_nodes))
}

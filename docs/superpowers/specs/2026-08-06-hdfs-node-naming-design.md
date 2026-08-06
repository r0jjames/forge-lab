# HDFS node naming: namenode and datanode VM roles

## Problem

A cluster with the `hdfs` addon builds three VMs named `<cluster>-data-1..3`.
All three run a DataNode; the first also runs the NameNode, chosen at play time
as `groups['data'][0]`. Nothing in the VM name, `multipass list`, the ssh config
or `cluster_registered/<cluster>_cluster_info.yml` says which node that is — the
role reads `data` for all three, and finding the NameNode means knowing the
sorting rule.

## Goal

Make the NameNode a distinct VM role, visible in the VM name and therefore in
the registry, and give it a machine of its own.

## Decisions

**Dedicated NameNode.** `<cluster>-namenode-1` runs the NameNode and no
DataNode; `<cluster>-datanode-1..N` run DataNodes only. A cluster with the full
addon set goes from 9 VMs to 10. The alternative — renaming node 1 while it
still stores blocks — would put a name on the box that only half describes it.

**One string, four places.** The `data` role disappears. Its name segment,
tfvars key prefix, ansible group name and registry `role:` value are the same
token today, and stay the same token after the rename: `registry.nodes_from`
derives the role from `<cluster>-<role>-<n>` and looks up `<role>_cpu` from the
tfvars, so the rename propagates to the registry with no renderer change.

**No `namenode_count` variable.** Non-HA HDFS has exactly one NameNode, so a
count is a knob that can only be set wrong. Terraform derives it:
`namenode_count = var.datanode_count > 0 ? 1 : 0`. `provision.py` already
zeroes a disabled addon's node count with `-var`, so `-var datanode_count=0`
removes the NameNode as well — enablement and sizing still cannot disagree.

**Registry: role rename only.** No per-node service list. The `hdfs` component
entry already publishes `namenode: hdfs://<ip>:8020` and the UI URL.

## Design

### Terraform (`lab/shared/terraform`)

Remove `data_count`, `data_cpu`, `data_mem`, `data_disk`. Add `datanode_count`,
`datanode_cpu`, `datanode_mem`, `datanode_disk`, and `namenode_cpu`,
`namenode_mem`, `namenode_disk`.

```hcl
locals {
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
}
```

Both maps merge into `module.vms`.

### Sizing (`lab/shared/clusters/*.tfvars`)

```hcl
datanode_count = 3
datanode_cpu   = 2
datanode_mem   = "4G"
datanode_disk  = "40G"
namenode_cpu   = 2
namenode_mem   = "4G"
namenode_disk  = "20G"
```

The NameNode holds metadata only, hence the smaller disk.

### Provision (`lab/provisioncluster/scripts/provision.py`)

`ADDON_NODE_ROLES` becomes `{"hdfs": "datanode", "opensearch": "opensearch"}`.
The inventory group mapping replaces `data` with two entries built from their
own VM prefixes:

```python
"namenode": multipass.list_vms(f"{cluster}-namenode-"),
"datanode": multipass.list_vms(f"{cluster}-datanode-"),
```

### Inventory (`forgelab/inventory.py`)

Add `HDFS_GROUPS = ("namenode", "datanode")` and emit `[hdfs_nodes:children]`
beside the existing `[k8s_nodes:children]`, so one play can still target every
Hadoop VM. Group order, natural sorting and empty-group emission are unchanged.

### Ansible (`lab/shared/ansible`)

`site.yml`: the HDFS play targets `hosts: hdfs_nodes`.

In the `hdfs` role:

- `install.yml` runs on every Hadoop host, but the systemd unit template loop
  becomes role-conditional — the namenode host gets `hdfs-namenode.service`,
  datanodes get `hdfs-datanode.service`.
- `namenode.yml` runs when `inventory_hostname in groups['namenode']`; the
  DataNode start runs when `inventory_hostname in groups['datanode']`.
- The "stop and disable the namenode unit on non-primary data nodes" task is
  deleted. It existed because the primary was one of N interchangeable `data`
  hosts and could move on a re-sort; a `datanode` VM can never become the
  NameNode now, and no longer receives the unit file at all.
- `groups['data'][0]` becomes `groups['namenode'][0]` in `core-site.xml.j2`,
  in the component fact, and in the namenode handler's guard. The datanode
  handler guards on `groups['datanode']`.
- `hdfs_replication` stays 2 — there are still three real DataNodes.

### Verify (`lab/provisioncluster/scripts/verify.py`)

The HDFS check targets `first_ip(text, "namenode")` and expects
`len(group_ips(text, "datanode"))` live DataNodes. This is stricter than today,
where the expected count included the host that was both NameNode and DataNode.
Missing namenode or datanode hosts with `hdfs` enabled is a hard failure.

### Registry

No code change. Nodes render as `role: namenode` / `role: datanode`, and the
per-role sizing lookup finds `namenode_cpu` / `datanode_mem` in the tfvars.

## Tests

- `test_inventory.py` — group names, the `[hdfs_nodes:children]` stanza, header
  order, natural sort over `datanode-2` / `datanode-10`.
- `test_provision.py` — the disabled-addon override is `datanode_count=0`.
- `test_verify.py` — `dfsadmin -report` fixtures use datanode hostnames.
- `test_tfvars.py` — the sizing keys parsed are the new ones.
- `test_registry.py` — a `<cluster>-namenode-1` host yields `role: namenode`
  with `namenode_*` sizing.

## Docs

`docs/provision-usage.md` (HDFS health checks, VM counts) and
`docs/using-cluster-addons.md` (node table, ssh examples, `fs.defaultFS`,
NameNode UI) reference `data-1` throughout; both move to the new names.

## Migration

A rename is destroy-and-create in Terraform, so an existing cluster must be
rebuilt: `make deprovision CLUSTER=lab1`, then provision again. Deprovision also
removes the stale `~/.forgelab/ssh_config.d/<cluster>.conf` and the registry
file, so no manual cleanup is needed.

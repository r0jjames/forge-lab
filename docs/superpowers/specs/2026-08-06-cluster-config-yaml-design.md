# Cluster configuration as YAML

Date: 2026-08-06
Status: approved, not yet implemented

## Problem

A cluster's shape lives in `lab/shared/clusters/<name>.tfvars` as twenty-odd flat
scalars:

```hcl
cluster_type     = "k8s"
mgmt_count       = 1
mgmt_cpu         = 2
...
datanode_count   = 3
opensearch_count = 3
addons           = "keycloak,hdfs,opensearch"
```

Three problems follow from the flatness.

Nothing in the file says that `datanode_*` belongs to HDFS and `opensearch_*`
does not belong to Kubernetes. The grouping exists only in the reader's head and
in `provision.py`'s `ADDON_NODE_ROLES` table.

Enablement and sizing are stated twice and can disagree. `addons` is a comma
string; the counts are separate numbers. `provision.py` papers over the gap by
appending `-var <role>_count=0` for every addon that is off.

Adding a technology touches four files. Terraform needs four new variables and a
`locals` block, `main.tf` needs a `merge` entry, `provision.py` needs an
`ADDON_NODE_ROLES` row, and `inventory.py` needs a group.

The fix is a per-cluster YAML file that states the whole shape once, in the shape
the reader already thinks in: a cluster has nodes, and technologies have nodes.

## Decisions

**A hand-written parser, not a YAML library.** The host agent has no venv and
these scripts are standard library only; `python3` on the host has no PyYAML.
`registry.py` already hand-*renders* YAML for exactly this reason. A strict
parser for the subset the config uses is roughly eighty lines and is fully
unit-testable, and anything outside the subset is a loud error rather than a
surprising parse.

**One `nodes` map into Terraform, not flat scalars.** The config layer expands
the YAML into `{"<cluster>-<role>-<n>": {cpus, memory, disk}}` and hands
Terraform that single map. Twenty variables and every `locals` block delete, and
adding a technology stops being a Terraform change at all.

**The YAML is the only source of truth.** The `cluster_type` and `addons` plan
variables — and with them the whole placeholder machinery in `planvars.py`,
`ProvisionClusterSpec.java`, and their mirror test — are removed. A run selects a
config; it does not patch one.

**No fallback and no field defaults.** A missing config file is an error naming
the path; a missing key is an error naming the key. `defaults.tfvars`' silent
substitution is gone. Reading one file tells you exactly what you will get.

**DEPROV does not read the config.** `provision.py` persists the generated
`.tfvars.json`, and `deprovision.py` uses it, falling back to `-var 'nodes={}'`.
Deleting or renaming a config can never strand a running cluster.

## Config format

Location: `cluster_configs/<name>_cluster.yaml` at the repository root. Tracked
and committed. Unlike `cluster_registered/`, which is generated output and needs
a host-level pointer to survive Bamboo's per-plan checkouts, this is committed
input: every checkout carries it, so the path is plain `REPO_ROOT /
"cluster_configs"`.

```yaml
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G

  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G

      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G

  opensearch:
    enabled: true
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G

  keycloak:
    enabled: true
```

### Names

A VM's role is still its name, and the role still keys the sizing and the Ansible
group — the rule in CLAUDE.md is unchanged, but the role is now derived from the
config rather than declared in four places.

| Config location | Role | VM name | Ansible group |
| --- | --- | --- | --- |
| `cluster_nodes.management` | `management` | `lab1-management-1` | `management` |
| `cluster_nodes.compute` | `compute` | `lab1-compute-1` | `compute` |
| `technologies.hdfs.nodes.namenode` | `hdfs-namenode` | `lab1-hdfs-namenode-1` | `hdfs_namenode` |
| `technologies.opensearch.nodes.master` | `opensearch-master` | `lab1-opensearch-master-1` | `opensearch_master` |

`cluster_nodes` keys are used verbatim. Technology node keys are prefixed with
their technology, so two technologies can each own a `master` without colliding
and a VM name says whose node it is.

The group is the role with `-` replaced by `_`, because Ansible warns on dashed
group names. The registry's `role` field keeps the dashed form, so it always
matches the VM name it sits next to.

This renames today's `mgmt` role to `management`, which touches `site.yml`, the
`k8s` and `dcos` roles, `inventory.mgmt_ip`, `sshconf`'s printed hint,
`outputs.tf`, and the docs. It is a mechanical rename, done once.

### Derived groups

`inventory.py` stops hardcoding `K8S_GROUPS` and `HDFS_GROUPS`. Children groups
come from the config:

- `k8s_nodes` — every `cluster_nodes` role. These are the VMs that receive
  kubelet; technology nodes never do.
- `<technology>_nodes` — every node group of that technology, for each enabled
  technology that declares nodes. `hdfs_nodes` keeps its current meaning;
  `opensearch_nodes` is new and replaces `site.yml`'s bare `opensearch`.

## `forgelab/clusterconfig.py`

Replaces `forgelab/tfvars.py`, which is deleted along with `test_tfvars.py`.

### Parsing

The accepted subset is: block mappings indented by two spaces per level,
`key: value` scalar lines, `key:` mapping openers, `#` comments (whole-line and
trailing), and blank lines. Values are read as strings and coerced by the
validator.

Everything else is rejected with a `<file>:<line>` message: tabs, `- ` sequence
items, flow collections (`{`, `[`), anchors and aliases (`&`, `*`), document
markers (`---`, `...`), and indentation that is not a multiple of two or that
jumps more than one level.

Rejecting rather than tolerating is the point. A parser that quietly handles half
of YAML teaches the reader that the whole of YAML works here.

### Validation

All of it runs before any tool lookup or Multipass round trip, so the PROV
Validate stage keeps its property of failing in seconds on any agent.

- `cluster.type` must be one of `k8s`, `dcos`.
- `cluster_nodes` must be present, non-empty, and contain `management` with a
  `count` of at least 1 — the `k8s` and `dcos` roles both take their control node
  from `groups['management'][0]`, and the Keycloak play targets `management[0]`.
- Every node block must declare `count`, `cpu`, `memory`, `disk`. `count` is an
  integer ≥ 0, `cpu` an integer ≥ 1, and `memory`/`disk` must match
  `^\d+[MG]$` — Multipass units, per the existing convention.
- `technologies` keys must be known: `keycloak`, `hdfs`, `opensearch`. Each needs
  an `enabled` key parsing as a boolean.
- An enabled `hdfs` or `opensearch` must declare a `nodes` block with at least
  one node group; those technologies have nowhere to run otherwise.
- An enabled technology's node blocks must be complete. A disabled technology's
  blocks are kept in the file and ignored entirely, which is what lets a
  technology be switched back on without retyping its sizing.
- `technologies.hdfs.nodes.namenode.count` must be exactly 1. Non-HA HDFS has one
  NameNode; this is the rule `main.tf` derives today, moved to where the error
  can name the file and the line.
- `technologies.keycloak` must not declare `nodes` — it runs as pods on the
  Kubernetes cluster the `cluster_nodes` already form.
- Any unrecognised key at any level is an error. Without this, `memmory: 4G`
  silently produces a VM with no memory setting.

Every message names the file and either the line or the dotted key path.

### API

```python
load(name) -> ClusterConfig       # resolve path, read, parse, validate
parse(text, source) -> dict       # the subset parser, exposed for tests
path_for(name) -> Path            # cluster_configs/<name>_cluster.yaml
```

`ClusterConfig` exposes:

- `cluster_type` — `"k8s"` or `"dcos"`.
- `enabled()` — enabled technology names, in file order. Rendered to the
  comma string that `install.py` still passes to Ansible as `addons`, so every
  `when:` clause in `site.yml` is unchanged.
- `roles()` — ordered records of `(role, group, count, cpu, memory, disk)`,
  covering `cluster_nodes` then each enabled technology's nodes.
- `nodes_map(cluster)` — `{"<cluster>-<role>-<n>": {"cpus": int, "memory": str,
  "disk": str}}` for Terraform. Roles with `count: 0` contribute nothing.
- `groups()` — ordered `{group: [role]}` including the derived `k8s_nodes` and
  `<technology>_nodes` children.
- `sizing_by_role()` — `{role: {"cpu", "mem", "disk"}}` for the registry.

## Terraform

`variables.tf` keeps `cluster_name`, `backend`, `image`, and
`ssh_public_key_path`, and gains:

```hcl
variable "nodes" {
  type = map(object({
    cpus   = number
    memory = string
    disk   = string
  }))
}
```

No default. A required variable means a stray `terraform apply` without a
var-file fails loudly instead of resolving `nodes` to `{}` and destroying every
VM in the workspace.

Deleted: all twenty per-role variables, and `addons` — Terraform only ever
recorded that string.

`main.tf` loses every `locals` block, including the derived `namenode_count`;
`module "vms"` takes `var.nodes` directly. `outputs.tf`'s `node_names` becomes
`keys(var.nodes)`.

`provision.py` writes `terraform/.generated/<cluster>.tfvars.json` — Terraform
reads `.tfvars.json` natively and `json` is standard library — containing
`cluster_name` and `nodes`, and applies with `-var-file=` pointing at it. The
directory is already gitignored. `node_count_overrides()` and `ADDON_NODE_ROLES`
delete: a disabled technology contributes no entries to the map, so enablement
and sizing cannot disagree.

## Ansible

`inventory.render` takes the config's groups rather than a dict `provision.py`
builds by hand; `K8S_GROUPS` and `HDFS_GROUPS` delete.

`site.yml` changes in two ways: `hosts: mgmt[0]` becomes `hosts: management[0]`,
and the OpenSearch play's `hosts: opensearch` becomes `hosts: opensearch_nodes`.
The `hdfs_nodes` play and every `when:` clause are untouched.

Role files change only in their group names — `groups['mgmt']` to
`groups['management']`, `groups['namenode']` and `groups['datanode']` to
`groups['hdfs_namenode']` and `groups['hdfs_datanode']`, `groups['opensearch']`
to `groups['opensearch_master']`. Roughly fifteen references across `k8s`,
`dcos`, `hdfs`, `opensearch`, and `common`, plus the `opensearch.yml.j2`,
`core-site.xml.j2`, `config.yaml.j2`, and `fluent-bit.conf.j2` templates.

## Plan variables and entrypoints

PROV's variables become:

| Variable | Default | Meaning |
| --- | --- | --- |
| `cluster_name` | `lab1` | Names every VM, the Terraform workspace, and the cluster info file |
| `cluster_config` | *(empty)* | Which config to build from; empty means use `cluster_name` |

`cluster_type` and `addons` are removed, and with them `PLACEHOLDER_TYPE`,
`PLACEHOLDER_ADDONS`, `is_unset`, `split_list`, `resolve_cluster_type`,
`resolve_addons`, and the `ProvisionClusterSpecTest` assertions that pinned the
placeholder strings to `planvars.py`. That mirror existed only to keep two
copies of a menu in step; there is no menu now.

`planvars.py` shrinks to `require_cluster_name` and:

```python
resolve(cluster, config_name, usage) -> (cluster, ClusterConfig)
```

`validate_prov.py` prints the resolved config path, the cluster type, the enabled
technologies, and a per-role sizing roll-up with totals — VM count, vCPU, and
RAM. The roll-up is cheap and catches an over-sized cluster before the apply
rather than when the host runs out of memory. The `defaults.tfvars` warning is
deleted along with the fallback it warned about.

`install.py` takes the config object, since it needs both the cluster type and
the enabled technology list.

Makefile:

```
make provision CLUSTER=lab1 [CONFIG=lab1]
make addons    CLUSTER=lab1 [CONFIG=lab1]
make deprovision CLUSTER=lab1
```

`TYPE=` and `ADDONS=` are removed. `make lint` drops the
`terraform -chdir=$(LAB)/shared/clusters fmt -check` line.

## Registry and deprovision

`registry.nodes_from(hosts, config)` derives a node's role from its name by
stripping the `<cluster>-` prefix and the trailing `-<n>`, which handles the
dashed technology roles that `parts[-2]` cannot. Sizing comes from
`config.sizing_by_role()`. The rendered
`cluster_registered/<cluster>_cluster_info.yml` format does not change.

`deprovision.py` drops its `tfvars` import. It passes
`-var-file=.generated/<cluster>.tfvars.json` when that file exists, and
`-var 'nodes={}'` when it does not — state, not variables, decides what
`terraform destroy` removes. DEPROV therefore never reads `cluster_configs/`.

## Testing

New `test_clusterconfig.py` covering:

- the accepted subset: nesting, comments, blank lines, trailing comments
- each rejection: tabs, sequences, flow collections, anchors, document markers,
  bad indentation — asserting the file and line appear in the message
- each validation rule, including the HDFS single-NameNode rule, the unknown-key
  rule, and the missing-`management` rule
- `nodes_map`, `groups`, `roles`, `sizing_by_role`, and that a disabled
  technology contributes nothing to any of them
- that every committed file in `cluster_configs/` parses and validates, so a bad
  config cannot reach `main`

Updated: `test_planvars`, `test_provision`, `test_validate_prov`, `test_install`,
`test_inventory`, `test_registry`, `test_deprovision`, `test_verify`,
`test_sshconf`, and `ProvisionClusterSpecTest`. Deleted: `test_tfvars.py`.

## Migration

The cutover is hard — no transition period where both formats work.

`lab1` is currently running with `lab1-data-*` VMs, which predate the
namenode/datanode split, so its Terraform state already diverges from `main`. It
must be deprovisioned before the cutover; the role renames would force a
destroy-and-recreate regardless, since the `nodes` map keys change.

Removed: `lab/shared/clusters/` (both `.tfvars` files),
`forgelab/tfvars.py`, `test_tfvars.py`.

Added: `cluster_configs/lab1_cluster.yaml`, reproducing today's `lab1.tfvars`
sizing under the new names.

Documentation updated: `CLAUDE.md` (Commands, Layout map, Conventions),
`docs/provision-usage.md`, `docs/using-cluster-addons.md`, `README.md`, and
`lab/README.md`.

## Out of scope

Making the technology list itself data-driven. `keycloak`, `hdfs`, and
`opensearch` stay a hardcoded set in `clusterconfig.py`, because each still
requires a hand-written Ansible role and a hand-written check in `verify.py`. The
config layer removes Terraform and inventory from that list; it does not pretend
a new technology is free.

# Cluster configs

One file per cluster: `<name>_cluster.yaml`. It states the cluster's type, its
node sizing, and which technologies it runs — and everything downstream is
derived from it, so the Terraform `nodes` map, the Ansible inventory groups and
the registry's sizing cannot disagree.

There is **no fallback file and no defaulting**. A missing config fails the run
naming the path it looked for, rather than quietly building something smaller.

```bash
make provision CLUSTER=lab1              # builds from lab1_cluster.yaml
make provision CLUSTER=lab2 CONFIG=lab1  # builds a cluster named lab2 from lab1's config
make addons    CLUSTER=lab1              # re-run the install stage only
```

Check the size before you build it — this runs on any agent in seconds:

```bash
bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py lab1
```

## What's here

| Config | Type | Technologies | VMs | RAM |
| --- | --- | --- | --- | --- |
| `lab1` | k8s | hdfs, keycloak, **splunk** | 11 | 48G |
| `opensearch1` | k8s | hdfs, keycloak, **opensearch** | 10 | 44G |
| `dcos1` | dcos | hdfs, opensearch | 8 | 34G |
| `reference` | k8s | *everything* | 14 | 66G — **won't fit a 64G host** |

`lab1` and `opensearch1` are deliberately separate files rather than one file
with a flag flipped. OpenSearch and Splunk fill the same slot in this lab, and
running both costs 40G of the host's RAM for two answers to the same question.

`reference_cluster.yaml` is documentation that happens to be valid: every
technology, every knob, with the RAM arithmetic in its header. Diff a real
config against it to see what you left out. Don't provision it here.

## Technologies

Enablement and sizing are the same block, so they cannot disagree: a disabled
technology contributes no VMs, no inventory groups and no ansible roles, and its
sizing stays in the file unvalidated so switching it back on isn't a retype.

| Technology | Node roles | Rules enforced by `clusterconfig.py` |
| --- | --- | --- |
| `keycloak` | *none* — runs as pods on the cluster | must declare no `nodes`; requires `cluster.type: k8s` |
| `hdfs` | `namenode`, `datanode` | exactly one NameNode (non-HA HDFS has one) |
| `opensearch` | `master` | node 1 also runs Dashboards |
| `splunk` | `cluster-manager`, `indexer`, `search-head` | exactly one manager, exactly one search head, ≥2 indexers |

Every rule exists because the alternative is a failure an hour into an install:

- **keycloak on dcos** — the role applies manifests with `kubectl --kubeconfig
  /etc/kubernetes/admin.conf`, which only a kubeadm control plane has.
- **one HDFS NameNode** — non-HA HDFS has exactly one, and it stores metadata
  only, no blocks.
- **two Splunk indexers** — the cluster manager runs `replication_factor = 2`,
  and a peer cannot replicate to itself.
- **one Splunk search head** — a search head *cluster* needs three members and a
  deployer, which this lab does not build.

k9s is **not** a technology. It ships with the k8s role on every cluster and
there is no flag that turns it off.

## Naming rules

A VM's role is its name: `<cluster>-<role>-<n>`.

- For `cluster_nodes`, the role is the key verbatim — `management`, `compute`.
- For a technology's nodes it is `<technology>-<node>` — `hdfs-namenode`,
  `splunk-indexer` — so two technologies can each own a node called `master`.
- The ansible group is the role with `-` replaced by `_`, because ansible warns
  on dashed group names.

Rename a key and the VM name, the group and the registry's `role` field all move
together. Two constraints follow, both rejected before Terraform runs:

- **No duplicate roles.** A `cluster_nodes` key and a technology's node cannot
  name the same role.
- **No role may prefix another.** `provision.py` finds a role's VMs by name
  prefix, so a `cluster_nodes` role called `hdfs` would swallow every
  `hdfs-namenode` and `hdfs-datanode` VM: `role 'hdfs-namenode' starts with role
  'hdfs', so their VM names collide — rename one`.

`management` is required — the k8s, dcos and keycloak roles all take their
control node from it.

## The file format

A deliberately small YAML subset, parsed by `forgelab/clusterconfig.py` (the
host agent has no venv, so the parser is hand-written and standard-library
only). It accepts two-space block mappings, `key: value`, `key:` openers,
comments and blank lines — and rejects everything else **by name**: sequences,
flow collections, anchors, aliases, document markers, tabs.

Sizes are multipass units: `4G`, `512M` — never `Gi`. Every value is required
once its block is enabled.

Errors name the file and the dotted key:

```
cluster_configs/lab1_cluster.yaml: unknown key 'technologies.hdfs.nodes.standby'; known: namenode datanode
cluster_configs/lab1_cluster.yaml: 'cluster_nodes.compute.memory' must look like 4G or 512M (got '3Gi')
```

## Adding a technology

The config is not the only place a new technology has to be declared — a node
role costs an ansible role too. In `forgelab/clusterconfig.py`, add it to
`TECHNOLOGIES` and give it an entry in `TECHNOLOGY_NODES` (empty tuple if it
owns no VMs, plus `NODELESS_TECHNOLOGIES`). Then write
`lab/shared/ansible/roles/<name>/`, add its play to `site.yml` gated on
`'<name>' in addons`, and — if it needs secrets — a `SECRET_KEYS` entry in
`forgelab/credentials.py`. Health checks belong in
`lab/provisioncluster/scripts/verify.py`.

Day-to-day usage of what's already here:
[`../docs/using-cluster-addons.md`](../docs/using-cluster-addons.md).

# Cluster addons: Keycloak, HDFS, Splunk (and k9s)

Date: 2026-08-03
Status: implemented, **with one substitution** — everything below that says
Splunk was built as OpenSearch instead. Splunk Enterprise ships no Linux arm64
build, which makes it a non-starter on Apple Silicon Multipass VMs; the swap
and what replaced it are described in `docs/using-cluster-addons.md`. Read the
Splunk sections here as the shape of the addon (three VMs, one query node, two
data nodes, its own VM role gated by the `addons` list), not as what is on
disk. The shipped addon names are `keycloak`, `hdfs`, `opensearch`.

## Problem

A provisioned `k8s` cluster is bare: kubeadm, containerd, flannel, nothing else.
To use the lab for anything resembling real work — running an application that
authenticates users, stores files, and ships logs somewhere searchable — the
cluster needs Keycloak, HDFS and Splunk, and the operator needs k9s to see what
is happening inside it.

Each of these can be installed and proven without deploying any application.
That matters: the goal is a lab you can exercise on its own, with application
integration as a later, separate concern.

| Technology | Standalone proof, no application involved |
| --- | --- |
| k9s | `k9s version` on a node holding a kubeconfig |
| Keycloak | admin console up; realm + client seeded; password grant returns a token |
| HDFS | `hdfs dfsadmin -report` lists live DataNodes; `put`/`cat` roundtrips a file |
| Splunk | search head lists its peers; a forwarder-sourced event is searchable |

## Scope

In scope: an addon mechanism in the Provision plan (PROV), plus the three
addons and k9s.

Out of scope, deliberately: deploying `worship-lineup` against Keycloak and
HDFS. That application is Supabase-backed today, so its integration decisions
are not settled. It gets its own spec. Nothing here is named for it — the
seeded realm and HDFS path are generic, so any application has a working target.

## Decisions

**Placement is hybrid.** Keycloak runs on Kubernetes, because it is an
application dependency and belongs next to the applications that use it. HDFS
and Splunk run natively on their own VMs under systemd, the way they are met
outside a lab, and the way that avoids putting a distributed filesystem and an
indexer on top of a single-control-plane kubeadm cluster with no storage.

**k9s is not an addon.** It is a kubectl TUI. It installs unconditionally as
part of the `k8s` role, on `mgmt` nodes only — they are the nodes with
`~/.kube/config`. It costs no VM, has no toggle, and is reported as a component
of every k8s cluster.

**Addons are opt-in per cluster, from one knob.** `addons` is a comma-separated
string in the cluster's tfvars, overridable by a Bamboo plan variable exactly as
`cluster_type` already is. It gates the ansible roles *and* derives the VM
counts, so there is no second setting to keep in sync.

**Splunk is a 3-VM distributed deployment**: one search head, two indexers, no
index clustering. Universal Forwarders on every other VM give the deployment
real data with no application deployed.

## Topology

Two new VM roles, both outside the Kubernetes cluster. Both use the existing
`<cluster>-<role>-<n>` naming, so `registry.py` derives their role and looks up
their sizing with no change to that module.

| Role | Count | Each | Runs |
| --- | --- | --- | --- |
| `mgmt` | 1 | 2cpu / 4G / 20G | control plane, k9s, Keycloak manifests |
| `compute` | 2 | 2cpu / 3G / 20G | kubelet |
| `data` | 3 | 2cpu / 4G / 40G | `data-1` NameNode; DataNode on all three |
| `splunk` | 3 | 2cpu / 6G / 40G | `splunk-1` search head; `splunk-2`/`-3` indexers |

A fully loaded cluster is 40G of the host's 64G, leaving roughly 16G for
Rancher Desktop and Bamboo. A cluster with `addons = ""` is unchanged from today
at 10G, so the existing quick smoke test stays quick.

## The addons knob

`lab/shared/clusters/<name>.tfvars` gains:

```hcl
addons       = "keycloak,hdfs,splunk"
data_count   = 3
data_cpu     = 2
data_mem     = "4G"
data_disk    = "40G"
splunk_count = 3
splunk_cpu   = 2
splunk_mem   = "6G"
splunk_disk  = "40G"
```

`addons` is a flat scalar, not an HCL list. That is deliberate: `tfvars.py`
parses `key = value` lines and explicitly does not handle lists, and a comma
string needs no parser work. `defaults.tfvars` sets `addons = ""`.

Resolution order in `provision.py`, mirroring `cluster_type`: the Bamboo plan
variable wins when non-empty, otherwise the value in the resolved tfvars file.
Unknown names are rejected with the file they came from named in the error.

VM counts follow from the addon list. `provision.py` overrides them on the
terraform command line, where `-var` beats `-var-file`:

```
hdfs   not in addons  ->  -var data_count=0
splunk not in addons  ->  -var splunk_count=0
```

`terraform/main.tf` gains `data_nodes` and `splunk_nodes` locals, merged into
the existing `module "vms"`. `modules/multipass` does not change — it is already
role-agnostic, and stays the swappable backend boundary.

## Install layer

Three new ansible roles under `lab/shared/ansible/roles/`, each gated in
`site.yml` on the same `addons` variable, delivered as `-e addons=...` on the
same channel as `cluster_type`:

```yaml
- name: Keycloak
  hosts: mgmt[0]
  roles: [{ role: keycloak, when: "'keycloak' in addons" }]

- name: HDFS
  hosts: data
  roles: [{ role: hdfs, when: "'hdfs' in addons" }]

- name: Splunk
  hosts: splunk
  roles: [{ role: splunk, when: "'splunk' in addons" }]
```

Every role appends to the `forgelab_components` fact that the last play already
collects. There is no central manifest to maintain: a role that installs
something declares it, and it appears in the cluster info file.

**`keycloak`** — Keycloak plus Postgres, applied as manifests from `mgmt-1`.
Exposed on NodePort 30080; the cluster has no ingress controller and no
LoadBalancer, so NodePort is the only option that works. Seeds realm
`forgelab`, public OIDC client `app`, and one test user via `kcadm.sh`.

Keycloak's Postgres needs a PersistentVolume, and the cluster has no
StorageClass at all — no PVC can bind on it today. The `k8s` role therefore
gains Rancher `local-path-provisioner` as the default StorageClass. This is a
defect fix in the base cluster, not Keycloak-specific, and is installed for
every k8s cluster regardless of addons.

**`hdfs`** — Hadoop 3.4.x from tarball, JRE 17, an `hdfs` service user, systemd
units. NameNode on `data-1`, DataNode on all three. `core-site.xml` and
`hdfs-site.xml` are templated from the inventory, so
`fs.defaultFS = hdfs://<data-1>:8020`. The NameNode format is guarded by
`creates:` so a re-run does not wipe the filesystem. Creates and chowns
`/user/app`.

**`splunk`** — Splunk Enterprise `.deb`, trial licence accepted
non-interactively, admin password generated per cluster. `splunk-1` becomes the
search head with `splunk-2` and `splunk-3` added as search peers; those two
enable `splunktcp:9997` receiving. `roles/common` installs the Universal
Forwarder on every non-Splunk VM, pointed at both indexers — gated on the same
`'splunk' in addons` condition, so a cluster without the addon gets no
forwarder.

The trial lasts 60 days and then reverts to Splunk Free, which has no
authentication and no distributed search. Lab clusters are rebuilt far more
often than that, but the cluster info file records the fact.

### Two fixes this exposes

1. `site.yml` runs the `k8s` role on `hosts: all`. With `data` and `splunk` VMs
   in the inventory that would install kubelet on them. It changes to
   `hosts: k8s_nodes`, a new inventory group whose children are `mgmt` and
   `compute`.
2. `roles/common` runs `swapoff` and loads `br_netfilter` on every host. That
   is Kubernetes-specific work and moves into the `k8s` role. `common` keeps
   base packages, and gains the conditionally-installed Universal Forwarder.

`inventory.py:render()` hardcodes the mgmt and compute groups. It becomes
group-driven — `render(cluster, groups)` — and emits the `[k8s_nodes:children]`
group. Its tests change with it. `deprovision.py` needs no change to tear the
new VMs down: it destroys by terraform workspace and sweeps by name prefix.

## Verification

`verify.py` gains one function per addon, run after the base cluster check and
only for addons that are on. Parsing stays pure and shelling out goes through
`proc.run`, matching the existing `nodes_ready()` shape, so every parser is unit
tested without a cluster.

| Addon | Pure parser | What it proves |
| --- | --- | --- |
| keycloak | `token_from(json)` | discovery document returns 200; password grant as the test user returns an `access_token` |
| hdfs | `live_datanodes(report)` | `hdfs dfsadmin -report` shows 3 live DataNodes; `put`/`cat` roundtrips through `/user/app` |
| splunk | `search_peers_up(text)` | the search head lists both peers; a forwarder-sourced event is searchable |
| k9s | — | `k9s version` on `mgmt-1`, folded into the existing k8s check |

Retry loops reuse the existing `ATTEMPTS` and `INTERVAL_SECONDS` constants.
Forwarder data is the slowest signal and drives the timeout.

Failure semantics are unchanged: the registry is written last, after verify, so
a cluster whose addon fails verification fails the build and gets no registry
entry. Its VMs survive for debugging, as they do today.

## Registry and credentials

`registry.render()` needs no change — it already emits whatever extra keys a
component reports, sorted after `name`. The roles simply report more:

```yaml
components:
  - name: hdfs
    namenode: "hdfs://192.168.64.21:8020"
    ui: "http://192.168.64.21:9870"
    version: 3.4.1
  - name: keycloak
    client_id: app
    realm: forgelab
    url: "http://192.168.64.11:30080"
    version: 26.0.7
  - name: splunk
    license: trial-60d
    ui: "http://192.168.64.31:8000"
    version: 9.3.2
credentials: "~/.forgelab/lab1-credentials.yml"
```

Secrets never enter that file — it is tracked. A new `forgelab/credentials.py`,
shaped like `registry.py` (pure render, then write), generates per-cluster
secrets with `secrets.token_urlsafe(18)` and writes
`~/.forgelab/<cluster>-credentials.yml` at mode 0600, outside the repository
entirely. The cluster info file carries only the pointer shown above.

Passwords reach ansible through `-e @<tmpfile>` in a 0600 temporary directory,
never through argv — argv is world-readable in `ps`. `deprovision.py` unlinks
the credentials file alongside the registry entry.

`sshconf` needs nothing: it already builds from `parse_hosts`, so
`ssh lab1-data-1` and `ssh lab1-splunk-1` work as soon as the inventory has
them.

## Interfaces

Each unit keeps one job and one way in:

- `tfvars.py` — resolve and parse the cluster's settings file. Gains nothing but
  callers; the `addons` scalar needs no new parsing.
- `credentials.py` — generate, render, write, remove per-cluster secrets. Knows
  nothing about which addon wants them.
- `inventory.py` — render and read the inventory. Becomes group-agnostic;
  callers name the groups.
- `registry.py` — render the cluster info file. Already component-agnostic;
  unchanged.
- ansible roles — install one thing, report one component. No role reads
  another's variables.
- `verify.py` — one entry point per addon, each a pure parser plus a retry loop.

## Plan and tooling changes

`ProvisionClusterSpec` gains `new Variable("addons", "")` and passes it as the
third argument to `provision.py`. The `agent.role=host` requirement already
covers the new work.

`make addons CLUSTER=lab1` re-runs the ansible install stage alone against the
existing inventory. Iterating on a Splunk role without it means a 30-minute
rebuild per attempt.

`CLAUDE.md` and `lab/README.md` gain the new roles, the `addons` knob, the
credentials file, and the new make target.

## Testing

All offline. No new tooling; `make lint` is unchanged.

- `test_inventory` — group-driven render, including `[k8s_nodes:children]`
- `test_tfvars` — `addons` parsing and rejection of unknown names
- `test_provision` — addon list to VM count derivation, both directions
- `test_verify` — `token_from`, `live_datanodes`, `search_peers_up`
- `test_credentials` — render, file mode, and that no secret reaches the registry
- `ansible-lint` over the three new roles
- `terraform fmt` and `validate` with the new variables
- `mvn test` for the new plan variable

## What this does not do

It does not deploy an application. It does not connect Keycloak to HDFS or to
Splunk — they are three independent addons that happen to share a cluster. It
does not cluster the Splunk indexes, and it does not make HDFS highly available;
both are single-NameNode, single-search-head lab deployments. Each of those is a
later spec if the lab ever needs it.

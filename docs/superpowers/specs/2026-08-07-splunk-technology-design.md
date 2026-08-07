# Splunk as a forge-lab cluster technology

**Date:** 2026-08-07
**Status:** approved, ready to implement

## Goal

Add `splunk` to the technologies a provisioned cluster can opt into, so the lab
runs a real distributed Splunk deployment — cluster manager, two indexers, one
search head — fed by Universal Forwarders on every other node. The purpose is
learning Splunk in all four of its usual shapes: SPL and dashboards, the admin
side (indexes, inputs, forwarders, licensing), ingesting real log sources, and
distributed architecture.

OpenSearch already covers the "search over logs" slot in this lab. Splunk does
not replace it; the two coexist in the config, and the shipped
`splunk1_cluster.yaml` simply parks OpenSearch (`enabled: false`) so the RAM
budget fits.

## The constraint that shapes everything

Splunk Enterprise has no ARM64 Linux build. The Splunk 10.4 system requirements
state it plainly: *"The ARM architecture is not supported for use with Splunk
Enterprise at this time."* Only the Universal Forwarder ships arm64. The lab
host is an Apple Silicon Mac, Multipass VMs are arm64, and Multipass uses QEMU
with no access to Apple Virtualization or Rosetta (canonical/multipass#2880).

So the Enterprise nodes run the amd64 build under `qemu-user-static` binfmt
translation inside otherwise-normal arm64 Ubuntu VMs. This was measured on a
throwaway VM before the design was accepted, not assumed:

| Measure (4 vCPU, 8G VM, Splunk 10.4.2 amd64) | Result |
| --- | --- |
| First start (instance creation, certs, KV store) | 2 min 05 s |
| Warm restart | 1 min 37 s |
| `add oneshot` of 200k events / 21 MB | 11 s |
| `stats count avg() by` over 200k events | 29 s (~5x native) |
| Web UI login page | HTTP 200 in 8 ms |
| Resident memory | ~2.0 G (splunkd 366 M, mongod 355 M) |

Roughly 5x native on search, a ~2 minute startup tax paid once per provision,
and a fully responsive UI. Acceptable for a learning lab at 10 GB/day.

Rejected alternatives:

- **Splunk pods on Rancher Desktop with Rosetta.** Near-native speed, but the
  Splunk instances then live outside the provisioned cluster, split across
  `infra/` and the pipeline, and require host-side RD reconfiguration.
- **A Lima VM backend with VZ + Rosetta.** Near-native and still VM-shaped, but
  it means a second VM backend beside `modules/multipass/`, with its own SSH,
  inventory and lifecycle wiring.
- **Splunk Cloud trial.** No local admin surface at all, and expires in 14 days.

## Licensing

A Splunk Developer Personal License (10 GB/day, expires 2027-02-03) is already
issued and installed successfully on the spike VM. It is stored at
`~/.forgelab/splunk-dev-license.xml`, mode 0600, and is never committed —
`CLAUDE.md` already forbids committing license keys, and the file sits outside
the repository entirely, like the Bamboo timebomb key.

This matters beyond convenience. Without it, instances fall back to the 60-day
Enterprise trial, which degrades into Splunk Free — and Free forbids distributed
search and forwarder authentication, which would break this exact topology. The
dev license grants `Auth`, `DistSearch`, `RcvData`, `DeployServer` and
`CanBeRemoteMaster`, so the split is legitimate for six months. The renewal
window opens 10 days before expiry.

Topology-wise, the cluster manager doubles as the **license manager**; the
indexers and search head are license peers pointed at it, so all four instances
draw from the one 10 GB pool. Universal Forwarders need no license.

When the license file is absent the play warns and continues: every instance
then runs its own 60-day trial, and a fresh clone of this repo still provisions.

## Architecture

```
Multipass <cluster> (all arm64 VMs)

  splunk technology VMs (amd64 Splunk Enterprise under qemu-user)
    <cluster>-splunk-cluster-manager-1   2 cpu  4G   RF=2 SF=2, license manager
    <cluster>-splunk-indexer-1..2        4 cpu  6G   S2S 9997, HEC 8088
    <cluster>-splunk-search-head-1       4 cpu  6G   Web UI 8000

  every other node (management, compute, hdfs-*, ...)
    splunkforwarder 10.4.2 arm64, NATIVE, systemd
      outputs.conf -> both indexers :9997, autoLB
```

Data flow: forwarders ship into the indexers, which index into `lab_*` indexes
defined once on the cluster manager and pushed as a cluster bundle. The search
head searches the peers through the manager's peer list. HEC on the indexers
accepts hand-pushed JSON.

Splunk Enterprise nodes do not run a forwarder — they index their own
`_internal` logs directly.

## Components

### 1. `forgelab/clusterconfig.py`

- `TECHNOLOGIES` gains `"splunk"`.
- `TECHNOLOGY_NODES["splunk"] = ("cluster-manager", "indexer", "search-head")`.

Roles therefore become `splunk-cluster-manager`, `splunk-indexer`,
`splunk-search-head`, and groups `splunk_cluster_manager`, `splunk_indexer`,
`splunk_search_head` — the existing `<technology>-<node>` convention, with the
existing dash-to-underscore group rule. `children()` already emits a
`splunk_nodes` child group for free.

One new validation block, mirroring the HDFS NameNode rule and phrased the same
way — the reason is in the message, not just the code:

- exactly one cluster manager (`count: 1`),
- exactly one search head (`count: 1`),
- at least two indexers, because `replication_factor = 2` needs two peers.

### 2. `forgelab/credentials.py`

`SECRET_KEYS["splunk"] = ("splunk_admin_password", "splunk_hec_token",
"splunk_pass4symmkey")`. No new file and no new mechanism: these land in the
existing `~/.forgelab/<cluster>-credentials.yml`, and `ensure()` already
preserves values across runs, which matters because the admin password is baked
into the instance at first start.

### 3. `roles/splunk` — the Enterprise nodes

Task files split by concern, following the hdfs role's shape:

- `emulation.yml` — `qemu-user-static` + `binfmt-support`, then amd64 multiarch:
  `dpkg --add-architecture amd64` plus an `Architectures: amd64` apt source
  pointing at `archive.ubuntu.com` (the arm64 image's `ports.ubuntu.com` carries
  no amd64 packages), then `libc6:amd64`, `libstdc++6:amd64`, `zlib1g:amd64`.
  Splunk ships every other library it needs under `/opt/splunk/lib`.
- `install.yml` — the `splunk` service account (Splunk 10 refuses to run as
  root without `--run-as-root`), the tarball, the systemd unit, `user-seed.conf`
  for the admin password (0600, removed after first start — it is cleartext and
  only consulted when no users exist).
- `license.yml` — copies the license to the cluster manager with
  `owner=splunk mode=0640`. The spike proved this is not optional: a file the
  `splunk` user cannot read fails with `cannot read "...": Permission denied`
  even though the CLI runs.
- `cluster_manager.yml`, `indexer.yml`, `search_head.yml` — per-role
  `server.conf`, `inputs.conf`, and the `master-apps/_cluster/local/indexes.conf`
  bundle plus `splunk apply cluster-bundle`.

Version pinning: `splunk_version: "10.4.2"` and `splunk_build: "33c3bf42cd73"`.
Enterprise and the Universal Forwarder share one build hash, so a single pair of
variables names both artifacts and the two can never drift apart.

Tarball caching: the Enterprise archive is 1.7 GB. It is fetched once to
`~/.forgelab/cache/` on the controller (`delegate_to: localhost`, `run_once`)
and unarchived to each VM from there, so re-provisioning and `make addons`
re-download nothing.

The systemd unit is a template (repo idiom) rather than `splunk enable
boot-start`: `Type=forking`, `User=splunk`, and `TimeoutStartSec=600` because an
emulated first start takes two minutes.

Indexes are defined only on the cluster manager, under
`master-apps/_cluster/local/indexes.conf`, with `repFactor = auto` so they
replicate, and `maxTotalDataSizeMB = 5120` per index. The size cap is a real
constraint, not decoration: the Mac has ~131 GB free and a runaway
`/var/log/pods` feed must hit Splunk bucket rotation rather than fill the host
disk.

### 4. `roles/splunkforwarder` — every other node

Native arm64 Universal Forwarder, installed on `all:!splunk_nodes`. Same
install shape (service account, cached tarball, templated unit, `user-seed.conf`).

`outputs.conf` is rendered from `groups['splunk_indexer']`, load-balanced across
both indexers. `inputs.conf` selects monitors by group membership, which is what
makes the ingested data varied enough to practise on:

| Source | Hosts | Index |
| --- | --- | --- |
| `/var/log/syslog`, `/var/log/auth.log` | all forwarder hosts | `lab_os` |
| `/var/log/pods/**/*.log`, kubelet | `k8s_nodes` | `lab_k8s` |
| HDFS NameNode/DataNode logs | `hdfs_nodes` | `lab_hdfs` |
| HEC pushes | n/a (direct to indexers) | `lab_hec` |

The HDFS and Kubernetes logs are deliberate: Java stack traces and container
logs are where multiline event breaking and field extraction actually have to be
learned.

### 5. `site.yml`

Two plays, both gated on `'splunk' in addons` exactly like the existing
technologies:

- `hosts: splunk_nodes` → `roles/splunk`
- `hosts: all:!splunk_nodes` → `roles/splunkforwarder`

### 6. `verify.py`

`_verify_splunk` follows the OpenSearch verifier's principle — a healthy
cluster with no data in it is still a failure — in three steps:

1. The search head's web UI answers 200.
2. The cluster manager reports both indexer peers `Up`
   (`/services/cluster/manager/peers`, basic auth over its self-signed 8089).
3. A search from the search head returns a non-zero count from `lab_os`, which
   only happens if forwarders on other VMs delivered data through the indexers.

Parsing stays in pure functions (`cluster_peers_up`, `search_result_count`) so
it is unit-testable offline, matching `live_datanodes` and `doc_count`.

### 7. Registry and docs

The role reports `forgelab_components` with the search head's UI URL, so
`cluster_registered/<cluster>_cluster_info.yml` lists Splunk like any other
technology. No registry code changes.

Docs: a Splunk section in `docs/using-cluster-addons.md` (login, the `lab_*`
indexes, starter SPL, a HEC `curl`, license and cluster-status commands), VM
counts and health checks in `docs/provision-usage.md`, and the technology list
in `CLAUDE.md`.

### 8. `cluster_configs/splunk1_cluster.yaml`

A committed config with Splunk enabled and OpenSearch parked:

```yaml
technologies:
  opensearch:
    enabled: false        # sizing preserved, unvalidated, per the existing rule
  splunk:
    enabled: true
    nodes:
      cluster-manager: { count: 1, cpu: 2, memory: 4G, disk: 20G }
      indexer:         { count: 2, cpu: 4, memory: 6G, disk: 60G }
      search-head:     { count: 1, cpu: 4, memory: 6G, disk: 30G }
```

RAM: 4 (management) + 6 (compute) + 16 (hdfs) + 22 (splunk) = 48 G of 64 G,
leaving room for Rancher Desktop's 6 G and macOS. Multipass disks are sparse, so
the 170 G of declared disk is a ceiling, not an allocation — and the index size
caps keep it a ceiling.

## Testing

Python tests are pure-function only, offline, in `bamboo-specs/src/test/python/`:

- `test_clusterconfig.py` — splunk is a known technology; its three node roles
  and derived groups; exactly one cluster manager; exactly one search head;
  fewer than two indexers rejected with a message naming `replication_factor`.
- `test_credentials.py` — enabling splunk mints its three secrets, and existing
  values survive a re-run.
- `test_verify.py` — `cluster_peers_up` and `search_result_count` against real
  payload shapes, including malformed input.

`ansible-lint` covers both new roles. `make lint` must pass unchanged
otherwise.

End-to-end verification is a real `make provision CLUSTER=splunk1`, whose
`Verify` stage is the check described above.

## Implementation order

1. `clusterconfig.py`, `credentials.py`, `splunk1_cluster.yaml`, and their tests.
2. `roles/splunk`: emulation, install, license, per-role config; `site.yml` play.
3. `roles/splunkforwarder` and the group-selected inputs.
4. `verify.py`, component reporting, docs.
5. Full provision of `splunk1`, then commit `cluster_registered/` by hand as usual.

## Risks

- **Startup time.** Three emulated first starts add roughly 8–10 minutes to a
  provision. Mitigated only by the tarball cache; the rest is the cost of
  emulation.
- **`server.conf` attribute naming.** Splunk 9+ renamed clustering attributes
  (`manager_uri`/`mode = manager`) while keeping the older `master_uri`/`master`
  as deprecated aliases. The implementation must confirm the accepted spelling
  on the spike VM with `splunk btool server list --debug` rather than trust
  either name.
- **Disk.** 131 GB free at design time. Index caps bound Splunk's share; HDFS
  and the images are the other consumers.

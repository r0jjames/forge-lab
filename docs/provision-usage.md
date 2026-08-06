# Running the Provision pipeline

This is the operational guide for provisioning a cluster — what to type into
the Bamboo plan variables, what the equivalent `make` invocation is, what
number of VMs to expect, and how to confirm afterwards that each addon
actually installed.

It assumes the lab itself is already up (`make bootstrap`, host agent
running and approved). For what to *do* with the addons once they're
verified — Keycloak realms, HDFS paths, Dashboards index patterns — see
[`using-cluster-addons.md`](using-cluster-addons.md). For the design behind
any of this, see
[`superpowers/specs/2026-07-23-forge-lab-design.md`](superpowers/specs/2026-07-23-forge-lab-design.md).

Throughout, `lab1` is the example cluster name.

## 1. The plan variables

The Provision plan (PROV) takes three variables. Run it manually from
Bamboo and fill them in:

| Variable       | Example value              | Meaning                                                                               |
|----------------|----------------------------|---------------------------------------------------------------------------------------|
| `cluster_name` | `lab1`                     | Names every VM (`lab1-mgmt-1`, …), the Terraform workspace, and the cluster info file |
| `cluster_type` | `k8s`                      | `k8s` or `dcos`                                                                       |
| `addons`       | `hdfs,keycloak,opensearch` | Any subset, comma-separated, in any order — or the single word `none`                 |

### The defaults are placeholders, not values

Bamboo models a plan variable as a key and a value; there is no description
field. So each variable's default value *is* its documentation — a menu of
what it accepts:

```
cluster_type   k8s | dcos
addons         hdfs,keycloak,opensearch (or none)
```

Neither string is a legal value, deliberately. Leaving one untouched means
**"no override"**, and the cluster's tfvars file wins. In particular,
leaving `addons` at its shipped default does *not* request those three
addons — it requests whatever the tfvars says, which for a cluster without
its own tfvars file is nothing at all.

**Replace the placeholder text entirely.** This is the single most common
way to get a cluster with no addons on it: submit the run with the menu
still sitting in the field, get three VMs, and wonder where the other six
went.

The strings live in `forgelab/planvars.py` (`PLACEHOLDER_TYPE`,
`PLACEHOLDER_ADDONS`), are mirrored in `ProvisionClusterSpec.java`, and are
pinned by `ProvisionClusterSpecTest` — change both or neither.

### Where the values actually come from

For each of `cluster_type` and `addons`, in order:

1. The plan variable, if it isn't empty and isn't the placeholder.
2. `bamboo-specs/src/main/java/lab/shared/clusters/<cluster_name>.tfvars`.
3. `clusters/defaults.tfvars`, if the cluster has no file of its own.

Step 3 is quiet, and worth watching: provisioning `lab2` when only
`lab1.tfvars` exists silently uses `defaults.tfvars`, whose `addons` is
empty. If you want a named cluster to carry addons by default, give it a
tfvars file.

### Legal addon values

```
keycloak   hdfs   opensearch   none
```

`none` means install nothing, and cannot be combined with a real name —
`none,hdfs` is a validation error rather than a guess at which you meant.
Anything unrecognised fails immediately:

```
unknown addon(s) [splunk] from the ADDONS override;
known: keycloak hdfs opensearch none
```

**There is no Splunk addon.** OpenSearch replaced it in this lab because
Splunk Enterprise has no Linux arm64 build, which makes it a non-starter on
Apple Silicon Multipass VMs. If you want log search, that's `opensearch`.

**k9s is not an addon** either. It ships with the k8s role on every mgmt
node; there is no flag that turns it off.

### Failing fast

Both PROV and DEPROV open with a `Validate` stage that carries **no**
`agent.role` requirement, so a bad variable fails in seconds on whatever
agent is free rather than queueing behind the host agent. The entrypoint
scripts call the same `planvars` code first thing, so `make provision`
fails identically and with the same message.

## 2. The same thing from the shell

```
make provision CLUSTER=lab1 TYPE=k8s ADDONS=hdfs,keycloak,opensearch
```

`TYPE=` and `ADDONS=` are the same overrides as the plan variables and
follow the same precedence — empty means "use the tfvars".

Related targets:

```
make deprovision CLUSTER=lab1                 # tear down, including the cluster info file
make addons CLUSTER=lab1 ADDONS=hdfs          # re-run the install stage only
```

`make addons` re-applies `site.yml` against the existing inventory for the
given addon set. It touches neither Terraform nor the VMs, which makes it
the fast loop for iterating on an Ansible role.

Provisioning refuses to start if VMs with the `<cluster>-` prefix already
exist, so deprovision before re-running the same name.

## 3. How many VMs to expect

The addon list gates the Ansible roles *and* zeroes the Terraform node count
of every disabled addon's VM role, so sizing and enablement cannot disagree.
Disabling `hdfs` doesn't build empty HDFS nodes — it builds none.

| Addon        | VM roles it owns       | Nodes (per `lab1.tfvars`)                                                  |
|--------------|------------------------|----------------------------------------------------------------------------|
| `hdfs`       | `namenode`, `datanode` | 1 + 3                                                                      |
| `opensearch` | `opensearch`           | 3                                                                          |
| `keycloak`   | *none*                 | 0 — it runs as pods on the k8s cluster the mgmt/compute nodes already form |

The NameNode count is not configurable: Terraform derives one NameNode
whenever `datanode_count` is above zero, because non-HA HDFS has exactly one.
Turning `hdfs` off zeroes `datanode_count`, which removes the NameNode too.

So with `lab1.tfvars` (1 mgmt + 2 compute):

| `addons`                   | VMs |
|----------------------------|-----|
| `none`                     | 3   |
| `keycloak`                 | 3   |
| `hdfs`                     | 7   |
| `hdfs,keycloak,opensearch` | 10  |

A full 10-VM `lab1` reserves 44G of RAM (4 + 2×3 + 4 + 3×4 + 3×6). Check the
host has it before starting.

Note that `kubectl get nodes` on a 10-VM cluster shows **3** nodes. Only mgmt
and compute join Kubernetes; the HDFS and opensearch VMs are plain hosts
running systemd services. That is correct, not a partial install.

## 4. What a successful run produces

The stages are Validate → Provision (Terraform) → Install (Ansible) →
Verify → Register, in that order. Register runs last so the registry only
ever lists clusters that passed verification.

A clean run ends with:

```
==> verify: waiting for all nodes Ready (timeout 300s)
==> verify: keycloak realm at http://192.168.252.98:30080/realms/forgelab
==> verify: 3 live datanodes on 192.168.252.100
==> verify: 3-node opensearch cluster at http://192.168.252.103:9200
==> inventory: .../ansible/inventory/lab1.ini
==> ssh config: /Users/roj/.forgelab/ssh_config.d/lab1.conf (try: ssh lab1-mgmt-1)
==> credentials: /Users/roj/.forgelab/lab1-credentials.yml
==> cluster info: .../cluster_registered/lab1_cluster_info.yml
==> cluster 'lab1' provisioned and verified
```

Four artifacts to know about:

- `cluster_registered/<cluster>_cluster_info.yml` — every node with role, IP
  and sizing, plus a `components` list with versions and URLs. Tracked but
  never committed by CI; committing it is yours to do.
- `~/.forgelab/ssh_config.d/<cluster>.conf` — included from `~/.ssh/config`,
  which is what makes `ssh lab1-mgmt-1` work as `ubuntu` with the lab key.
- `~/.forgelab/<cluster>-credentials.yml` — Keycloak passwords, mode `0600`,
  never in the repo. Absent if no addon needed secrets.
- `bamboo-specs/src/main/java/lab/shared/ansible/inventory/<cluster>.ini` —
  generated from live Multipass state, since the provider doesn't expose IPs.

The IPs below come from that cluster info file. Read it, or use the `ssh`
aliases and skip looking them up.

## 5. Checking what installed

### Everything at once

The one-shot check is the pipeline's own verify stage, re-run by hand:

```
bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py lab1 k8s hdfs,keycloak,opensearch
```

Exit 0 means all of it is healthy: every k8s node `Ready`, a default
StorageClass, k9s present, the Keycloak issuer answering, the expected
number of live HDFS datanodes, and a 3-node OpenSearch cluster.

To see what provision *recorded* rather than re-probing:

```
cat cluster_registered/lab1_cluster_info.yml
```

The `components:` block lists each installed piece with its version and
endpoints. If an addon isn't in there, it wasn't installed.

### Kubernetes

```
ssh lab1-mgmt-1 "kubectl get nodes"
```

```
NAME             STATUS   ROLES           AGE   VERSION
lab1-compute-1   Ready    <none>          34m   v1.30.14
lab1-compute-2   Ready    <none>          34m   v1.30.14
lab1-mgmt-1      Ready    control-plane   34m   v1.30.14
```

k9s, which only exists on mgmt (compute nodes have no kubeconfig):

```
ssh lab1-mgmt-1 "k9s version --short"
```

### Keycloak

Keycloak runs as pods, so `systemctl` will tell you nothing. Check the pods:

```
ssh lab1-mgmt-1 "kubectl get pods -n keycloak"
```

```
NAME                           READY   STATUS    RESTARTS   AGE
keycloak-db-69f87b4bc5-s8ndk   1/1     Running   0          33m
keycloak-ff944d649-8kscm       1/1     Running   0          33m
```

The definitive check — the realm answering over the NodePort, which is what
verify.py polls:

```
curl -s "http://<mgmt-ip>:30080/realms/forgelab/.well-known/openid-configuration" | head -c 120
```

```
{"issuer":"http://192.168.252.98:30080/realms/forgelab","authorization_endpoint":...
```

Admin console at `http://<mgmt-ip>:30080`, user `admin`:

```
grep keycloak_admin_password ~/.forgelab/lab1-credentials.yml
```

End-to-end, proving realm + client `app` + user `labuser` together. The
password goes over stdin rather than in a `-d` flag, because `ps` shows argv
to every user on the box:

```
PASSWORD=$(grep keycloak_app_user_password ~/.forgelab/lab1-credentials.yml | cut -d'"' -f2)

printf '%s' "$PASSWORD" | curl -s -X POST \
  http://<mgmt-ip>:30080/realms/forgelab/protocol/openid-connect/token \
  -d grant_type=password -d client_id=app -d username=labuser \
  --data-urlencode password@-
```

A JSON body containing `access_token` means the whole chain works.

### HDFS

The NameNode is `namenode-1` and runs no DataNode; blocks live on
`datanode-1` and up.

```
ssh lab1-namenode-1 "hdfs dfsadmin -report | head -12"
```

```
Configured Capacity: 121451827200 (113.11 GB)
DFS Used%: 0.00%
Missing blocks: 0
```

`WARN util.NativeCodeLoader: Unable to load native-hadoop library for your
platform` on the first line is normal and not a failure.

Filesystem responding, and the seeded path present:

```
ssh lab1-namenode-1 "hdfs dfs -ls /user/app"
```

A write/read round trip, if you want more than liveness:

```
ssh lab1-namenode-1 "echo hello | hdfs dfs -put -f - /user/app/hello.txt && hdfs dfs -cat /user/app/hello.txt"
```

Per-node daemons, when the report shows fewer datanodes than expected:

```
for n in 1 2 3; do ssh lab1-datanode-$n "systemctl is-active hdfs-datanode"; done
```

NameNode UI: `http://<namenode-1-ip>:9870`

### OpenSearch and Dashboards

Node 1 runs both OpenSearch and Dashboards; nodes 2 and 3 run OpenSearch
only. There is no password — the security plugin is disabled outright.

```
curl -s "http://<opensearch-1-ip>:9200/_cluster/health?pretty"
```

```
"cluster_name" : "forgelab",
"status" : "green",
"number_of_nodes" : 3,
"number_of_data_nodes" : 3,
```

Want `green` and the full node count. Yellow means replicas are unassigned;
red means data is missing.

Which nodes actually joined:

```
curl -s "http://<opensearch-1-ip>:9200/_cat/nodes?v"
```

Dashboards answering:

```
curl -s -o /dev/null -w "%{http_code}\n" "http://<opensearch-1-ip>:5601/api/status"
```

```
200
```

Per-node service, when a node is missing from `_cat/nodes`:

```
for n in 1 2 3; do ssh lab1-opensearch-$n "systemctl is-active opensearch"; done
```

Browser UI at `http://<opensearch-1-ip>:5601`. Note that Discover looks
empty on a fresh cluster until you create the `forgelab-logs*` index pattern
by hand — that's a first-run step, not a broken install. See
[`using-cluster-addons.md`](using-cluster-addons.md) for it.

In zsh, quote any URL containing `?` or `&`, or you get `no matches found`
from globbing before curl ever runs.

## 6. When the VM count is wrong

Symptom: the cluster comes up healthy, but only mgmt and compute VMs exist
and no addon shows up anywhere.

Work through it in this order — the first two account for nearly all of it:

1. **Was the `addons` plan variable left at its placeholder?** Then it meant
   "no override" and the tfvars decided. Check the run's first log line,
   which states exactly what was resolved and from which file:

   ```
   ==> provisioning 'lab1' type=k8s addons=hdfs,keycloak,opensearch config=lab1.tfvars
   ```

   `addons=none` there means nothing was requested.

2. **Does `<cluster>.tfvars` exist?** If not, `defaults.tfvars` was used, and
   its `addons` is empty. `config=defaults.tfvars` in that same line is the
   tell.

3. **Check `components:` in the cluster info file.** Only kubernetes,
   containerd, flannel, k9s and local-path-provisioner means the addon roles
   never ran — this is a resolution problem, not an installation failure.

4. **Is `~/.forgelab/<cluster>-credentials.yml` missing?** Keycloak
   generates it. No file, no Keycloak.

A genuine install failure looks different: the Ansible PLAY RECAP shows
non-zero `failed=` or `unreachable=`, or the verify stage dies with a
specific message (`nodes not all Ready within timeout`, and so on). Those
are worth debugging. A quiet, healthy, undersized cluster is almost always
the placeholder.

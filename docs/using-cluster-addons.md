# Using the cluster addons

This is the day-to-day guide for the optional technologies a provisioned
cluster can carry — Keycloak, HDFS, OpenSearch/Dashboards, and Splunk — plus
k9s, which isn't one of them but which you'll reach for constantly alongside
them.
It assumes a cluster is already up. For how provisioning itself works, see
`docs/superpowers/specs/2026-07-23-forge-lab-design.md`; for how the addons
were built, see `docs/superpowers/specs/2026-08-03-cluster-addons-design.md`.

Everywhere below, `<cluster>` is the cluster name (`lab1` in the examples)
and `<cluster>-management-1`, `<cluster>-hdfs-namenode-1`,
`<cluster>-opensearch-master-1` are SSH host aliases, not literal IPs — see
"Where things live" for how those resolve.

## 1. Enabling technologies

Which of these a cluster gets is controlled by the `technologies` block in
its config, `cluster_configs/<cluster>_cluster.yaml`. There is no fallback
file — a cluster with no config of its own fails provisioning naming the
path it looked for, rather than silently building something smaller.
`lab1_cluster.yaml` currently has all three enabled:

```yaml
technologies:
  hdfs:
    enabled: true
    nodes:
      # Non-HA HDFS has exactly one NameNode, and it stores metadata only.
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

To turn one off, set its `enabled` to `false` in the file — its sizing stays
in place, unvalidated, so switching it back on later doesn't mean retyping
it:

```yaml
technologies:
  opensearch:
    enabled: false        # sizing below is kept, unvalidated, and builds nothing
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
```

There is no per-run override: a technology's `enabled` flag in the config is
the only place that decides whether it gets built, so `make provision` and
the Bamboo plan always build the same thing for a given config. Editing the
file is the whole mechanism.

Anything unrecognised fails before Terraform runs — an unknown technology
name, or a node key a technology doesn't own, gets a validation error naming
the file and the exact dotted key, in the Validate stage, which runs on any
agent — you don't wait for the host agent to learn you typed it wrong.

To iterate on a technology's Ansible role without tearing down and
rebuilding the whole cluster, re-run just the install stage against the
existing inventory:

```
make addons CLUSTER=lab1
```

This re-applies `site.yml` for whatever the cluster's config currently has
enabled; it does not touch Terraform or re-provision VMs. (The install
stage still passes Ansible an `addons` extra-var — a comma-separated list of
enabled technology names — because that's what `site.yml`'s `when:` clauses
read; the word survives there as plumbing, not as a config concept.)

**k9s is not a technology.** It ships on every Kubernetes cluster's
management node regardless of what's enabled — there's no flag that turns it
off.

One consequence worth knowing before you disable something: disabling
`hdfs` or `opensearch` doesn't just skip their Ansible role, it removes their
nodes from the Terraform `nodes` map entirely (the VM roles `hdfs-namenode`
and `hdfs-datanode` for hdfs, `opensearch-master` for opensearch). A cluster
with only `keycloak` enabled builds no HDFS or OpenSearch VMs at all — not
empty ones, none. `clusterconfig` requires exactly one NameNode whenever hdfs
is enabled, so hdfs is one switch, not two. Keycloak owns no VM role of its
own; it runs on the k8s cluster the management/compute nodes already form.

One naming rule worth knowing if you add your own roles: VM names are
`<cluster>-<role>-<n>`, and `provision.py` finds a role's VMs by that name
prefix, so no role name may be a prefix of another — a `cluster_nodes` role
literally called `hdfs` would otherwise swallow every `hdfs-namenode` and
`hdfs-datanode` VM into its own group. `clusterconfig` rejects a config like
that outright, before Terraform ever runs: `role 'hdfs-namenode' starts with
role 'hdfs', so their VM names collide — rename one`.

## 2. Where things live

Node names are stable across provisions because the inventory is
natural-sorted (`hdfs-datanode-2` always sorts before `hdfs-datanode-10`), so
you can rely on these regardless of which run produced the cluster:

| Node | What's there |
| --- | --- |
| `<cluster>-management-1` | Kubernetes control plane, k9s, Keycloak (via a NodePort on the k8s cluster) |
| `<cluster>-hdfs-namenode-1` | HDFS NameNode, and nothing else — it stores metadata, not blocks |
| `<cluster>-hdfs-datanode-1..N` | HDFS DataNodes (blocks live here; the NameNode is not one of them) |
| `<cluster>-opensearch-master-1..N` | OpenSearch nodes + Dashboards (node 1 always runs Dashboards; the rest run OpenSearch only) |
| `<cluster>-splunk-cluster-manager-1` | Splunk cluster manager, which is also the licence manager |
| `<cluster>-splunk-indexer-1..N` | Splunk indexers: forwarder traffic (9997), HEC (8088), the indexes themselves |
| `<cluster>-splunk-search-head-1` | Splunk search head — the UI you actually search from, port 8000 |

To get the actual IPs for a cluster, either read
`cluster_registered/<cluster>_cluster_info.yml` (written by provision as its
last step — it lists every node, its role, and its address) or just SSH by
name. Provisioning writes `~/.forgelab/ssh_config.d/<cluster>.conf`
(included from `~/.ssh/config`), so `ssh lab1-management-1`, `ssh lab1-hdfs-namenode-1`,
`ssh lab1-opensearch-master-1`, etc. all work out of the box as the `ubuntu` user
with the lab's key — no need to look up an IP first. If you do want the raw
address for a `curl` from your own machine, `ssh <cluster>-management-1 "hostname
-I"` or the info file both have it.

## 3. k9s

```
ssh <cluster>-management-1
k9s
```

k9s reads `~/.kube/config`, which is only populated on the management node —
the node that runs `kubectl` against the cluster it's part of. It is **not**
installed on compute nodes; they have no kubeconfig and nothing to point k9s
at, so don't go looking for it there.

## 4. Keycloak

Console: `http://<cluster>-management-1:30080` (that's the NodePort — Keycloak
itself runs as pods on the k8s cluster, not as a systemd unit on any single
VM).

Log in as `admin`. The password is `keycloak_admin_password` in
`~/.forgelab/<cluster>-credentials.yml`, which is written on your own
machine (or wherever `make addons`/`make provision` ran), mode `0600`, and
**never committed to the repo**:

```
grep keycloak_admin_password ~/.forgelab/<cluster>-credentials.yml
```

The install seeds one realm, `forgelab`, with a public OIDC client `app`
(direct access grants enabled — that's what lets you do a password grant
instead of a browser redirect) and one test user, `labuser`, whose password
is `keycloak_app_user_password` in the same credentials file.

Discovery document, useful for pointing any OIDC library at the realm
without hand-building endpoint URLs:

```
curl -s http://<cluster>-management-1:30080/realms/forgelab/.well-known/openid-configuration
```

Fetch a token for `labuser` with the password grant. Keep the password out of
the command line the same way the rest of this branch does — `ps` shows argv
to any user on the box, so it's piped over stdin with `--data-urlencode
password@-` instead of interpolated into a `-d` flag:

```
PASSWORD=$(grep keycloak_app_user_password ~/.forgelab/<cluster>-credentials.yml | cut -d'"' -f2)

printf '%s' "$PASSWORD" | curl -s -X POST \
  http://<cluster>-management-1:30080/realms/forgelab/protocol/openid-connect/token \
  -d grant_type=password \
  -d client_id=app \
  -d username=labuser \
  --data-urlencode password@-
```

A successful response is a JSON blob with an `access_token`. Two things that
will bite you if you're setting this up from scratch for a different client
or user rather than using the seeded ones:

- The password grant only works because the `app` client has
  `directAccessGrantsEnabled=true`. Keycloak's default for a new client is
  `false`, and the grant fails silently different ways depending on client
  type — check this first if a token request 400s.
- Keycloak 24 and later require a user to have `email`, `firstName`, and
  `lastName` set (the declarative user profile), or the password grant fails
  with `invalid_grant` / "Account is not fully set up" even though the user
  looks perfectly normal in the admin console. The `labuser` seed sets all
  three for exactly this reason — if you add your own users by hand, do the
  same.

## 5. HDFS

```
ssh <cluster>-hdfs-namenode-1
hdfs dfs -ls /
```

The NameNode UI is at `http://<cluster>-hdfs-namenode-1:9870`. `fs.defaultFS` is
`hdfs://<cluster>-hdfs-namenode-1:8020` — that's the URI any client (including
`hdfs dfs` run from a different node) needs to talk to this filesystem.

`/user/app` already exists and is owned by `ubuntu`, ready for an
application to use without any setup:

```
# from hdfs-namenode-1, or any node with the hdfs client and this fs.defaultFS configured
echo "hello" | hdfs dfs -put -f - /user/app/hello.txt
hdfs dfs -cat /user/app/hello.txt
hdfs dfs -get /user/app/hello.txt ./hello.txt
hdfs dfs -ls /user/app
```

One permissions detail worth knowing before you go looking for why
`dfsadmin -report` behaves the way it does: the `ubuntu` login user is a
member of the `hdfs` group, and `dfs.permissions.superusergroup` is set to
`hdfs`. That's why running `hdfs dfsadmin -report` as `ubuntu` shows the
full DataNode listing (`Live datanodes (N):` with per-node capacity detail)
rather than just the cluster-wide capacity summary — HDFS shows that detail
only to a superuser, and group membership is how superuser status is
granted here. Without it you'd still get a report, just a much shorter one.

## 6. OpenSearch and Dashboards

OpenSearch entered this lab as the stand-in for Splunk, because Splunk
Enterprise has no Linux arm64 build. Splunk is available again (section 7)
by running its amd64 build under emulation, so the two now overlap: they
fill the same slot, and `cluster_configs/splunk1_cluster.yaml` parks
OpenSearch rather than paying 18G to run both.

- API: `http://<cluster>-opensearch-master-1:9200`
- Dashboards: `http://<cluster>-opensearch-master-1:5601`
- Transport (node-to-node, not for clients): port 9300

**There is no password.** The security plugin is disabled outright
(`plugins.security.disabled: true` in `opensearch.yml`) rather than
configured with credentials, because this is a disposable local lab on a
host-only network — there's nothing here worth protecting with auth, and
every extra moving part is one more thing to debug. Nothing for this
technology is written to `~/.forgelab/<cluster>-credentials.yml`; if you go looking
there for an OpenSearch password, you will not find one, because none is
ever generated. Both the API and Dashboards are open HTTP.

Fluent Bit ships logs into a daily index, `forgelab-logs-YYYY.MM.DD`, from
every management, compute and HDFS node (the opensearch nodes themselves are
deliberately excluded — see below). One index per day, all matching
`forgelab-logs*`.

### First-run Dashboards step

Nothing pre-creates an index pattern in Dashboards, so the first time you
open it on a fresh cluster it will look completely empty even though the
cluster is healthy and logs are flowing. You have to create the pattern
yourself, once, per cluster:

1. Open `http://<cluster>-opensearch-master-1:5601`.
2. Open the menu (the ☰ icon, top left) and go to **Dashboards Management**
   → **Index Patterns**.
3. Click **Create index pattern**, enter `forgelab-logs*`, and continue.
4. Pick `@timestamp` as the time field.
5. Click **Create index pattern**.

After that, **Discover** will show documents. If Discover still looks
empty, it's very likely the index pattern step above, not a real problem —
check `_cat/indices` (below) before assuming something's broken.

### OpenSearch's own logs aren't in OpenSearch

The three opensearch nodes ship no logs of their own by design (Fluent Bit
only runs on management/compute/hdfs-namenode/hdfs-datanode), so if
OpenSearch or Dashboards itself is
misbehaving, you will not find anything useful searching `forgelab-logs*` —
there's nothing there about the OpenSearch service itself. Its logs are
plain files on the node:

```
ssh <cluster>-opensearch-master-1 "ls /var/lib/opensearch/logs/"
ssh <cluster>-opensearch-master-1 "tail -f /var/lib/opensearch/logs/forgelab.log"
```

(`forgelab` there is the cluster name configured as `opensearch_cluster_name`,
which is what OpenSearch prefixes its own log filenames with — not the
lab cluster name `lab1`.)

### Record shape

Every document has exactly three fields:

| Field | Content |
| --- | --- |
| `@timestamp` | when Fluent Bit read the line |
| `log` | the raw line, unparsed |
| `source_file` | which file it came from (`/var/log/syslog` or `/var/log/auth.log`) |

There's no syslog parsing, so there's no structured `severity` or `program`
field to filter on — `source_file` is the only structured field besides the
timestamp. Fluent Bit tags each record `forgelab.<hostname>` internally for
its own routing, but that tag is **not** stored as a document field (the
index mapping has only the three fields above), so you can't filter by
hostname directly in a query. The originating hostname is visible only
inside the raw `log` text itself — rsyslog prefixes every line with it — so
to find one node's logs you're filtering on a substring of `log`, not a
dedicated field.

### Useful one-liners

```
curl -s "http://<cluster>-opensearch-master-1:9200/_cluster/health?pretty"
curl -s "http://<cluster>-opensearch-master-1:9200/_cat/indices/forgelab-logs*?v"
curl -s "http://<cluster>-opensearch-master-1:9200/forgelab-logs*/_search?q=source_file:%2Fvar%2Flog%2Fauth.log&size=5&pretty"
```

## 7. Splunk

Four VMs, a real distributed deployment: one cluster manager, two indexers,
one search head. Every *other* node in the cluster runs a Universal
Forwarder that ships its logs in.

- Search head UI: `http://<cluster>-splunk-search-head-1:8000`
- Cluster manager UI: `http://<cluster>-splunk-cluster-manager-1:8000`
  (the indexer-clustering pages live here, not on the search head)
- HEC: `http://<cluster>-splunk-indexer-1:8088`
- Management/REST on every instance: `https://<host>:8089` (self-signed)

Log in as `admin`; the password is `splunk_admin_password` in
`~/.forgelab/<cluster>-credentials.yml`, alongside `splunk_hec_token` and the
`splunk_pass4symmkey` the peers authenticate with.

### It runs emulated, and that is normal

Splunk Enterprise has no arm64 build, so the amd64 build runs under
`qemu-user` translation on these arm64 VMs. Expect roughly 5x native search
times and a two-minute service start. A search that would return instantly
on a real indexer taking twenty seconds here is the emulation, not a broken
cluster. The Universal Forwarders are native arm64 and pay none of this.

### The indexes

| Index | Fed by | Sourcetype |
| --- | --- | --- |
| `lab_os` | every forwarder | `syslog`, `linux_secure` |
| `lab_k8s` | management + compute nodes | `kube:container` |
| `lab_hdfs` | hdfs nodes | `hdfs:daemon` |
| `lab_hec` | your own HTTP pushes | whatever you send |

Each is capped at 5G (`maxTotalDataSizeMB`), so a runaway feed rotates its
own buckets instead of filling the lab host's disk.

Indexes are defined **only** on the cluster manager, in
`/opt/splunk/etc/manager-apps/_cluster/local/`, and pushed to the peers with
`splunk apply cluster-bundle`. Editing `indexes.conf` on an indexer directly
is how a clustered deployment drifts apart — change it on the manager and
push.

### Starter searches

```
index=lab_os | stats count by host, sourcetype
index=lab_k8s | timechart span=1m count by host
index=lab_hdfs "ERROR" | stats count by source
index=lab_os earliest=-15m | top limit=10 host
```

### Pushing your own events

```
TOKEN=$(grep splunk_hec_token ~/.forgelab/<cluster>-credentials.yml | cut -d'"' -f2)
curl -s http://<cluster>-splunk-indexer-1:8088/services/collector/event \
  -H "Authorization: Splunk $TOKEN" \
  -d '{"event": {"msg": "hello", "level": "INFO"}, "sourcetype": "_json"}'
```

A `{"text":"Success","code":0}` back means it is in `lab_hec`.

### Running CLI commands on a node

Splunk's CLI reasserts ownership of `$SPLUNK_HOME` against the account
recorded in `splunk-launch.conf`, so run it **as that account**, never as
your login user through a bare `sudo`:

```
ssh <cluster>-splunk-cluster-manager-1
sudo -u splunk /opt/splunk/bin/splunk list cluster-peers
sudo -u splunk /opt/splunk/bin/splunk list licenses
sudo -u splunk /opt/splunk/bin/splunk show cluster-bundle-status
```

Pass credentials through `SPLUNK_USERNAME` / `SPLUNK_PASSWORD` in the
environment rather than `-auth admin:...`, which keeps the password out of
`ps`:

```
PASSWORD=$(grep splunk_admin_password ~/.forgelab/<cluster>-credentials.yml | cut -d'"' -f2)
sudo -u splunk env SPLUNK_USERNAME=admin SPLUNK_PASSWORD="$PASSWORD" \
  /opt/splunk/bin/splunk list cluster-peers
```

The forwarder runs as `root` instead (it reads `/var/log/pods`, which
kubelet keeps root-only), so on any other node:

```
sudo env SPLUNK_USERNAME=admin SPLUNK_PASSWORD="$PASSWORD" \
  /opt/splunkforwarder/bin/splunk list forward-server
```

```
Active forwards:
	192.168.252.128:9997
	192.168.252.129:9997
```

Both indexers listed as *active* is what healthy looks like. Note the
forwarder has **no management port** — Splunk ships it disabled on the
Universal Forwarder — so there is no `:8089` to curl on those hosts, and its
CLI is the only way in.

### The licence

The cluster manager is also the licence manager, and the other three
instances draw from its pool. A Splunk Developer Personal License lives at
`~/.forgelab/splunk-dev-license.xml` on the lab host — never in this
repository — and is copied to the manager during install.

Without that file, every instance starts its own 60-day trial, which
expires into Splunk Free. Free forbids distributed search and forwarder
authentication, so this topology does not degrade gracefully — it stops
working. Check what you have with:

```
sudo -u splunk /opt/splunk/bin/splunk list licenses
```

`group_id: Enterprise` with a 10737418240 quota is the developer licence;
`group_id: Trial` means the file was missing at install time. Request a new
one at https://dev.splunk.com/enterprise/dev_license, drop it at that path,
and re-run `make addons CLUSTER=<cluster>`.

## 8. Using these from an application

These technologies are independent services that happen to share a cluster
— nothing here wires Keycloak, HDFS, OpenSearch and Splunk to each other, or
to whatever you deploy. Wiring an application to them is on the application.
For something like `worship-lineup` (or any app that needs auth, storage,
and logging), the shape is:

- **OIDC**: point the app's OIDC client config at issuer
  `http://<cluster>-management-1:30080/realms/forgelab`, client id `app`. Reuse
  the seeded client if a public client with direct-grant/browser-redirect
  is enough; create a new one in the realm if the app needs its own client
  id or a confidential client with a secret.
- **Storage**: write application data under
  `hdfs://<cluster>-hdfs-namenode-1:8020/user/app` (already there, already owned by
  `ubuntu`), or create a new path under `/user` for a dedicated identity.
- **Logging**: ship application logs to OpenSearch at
  `http://<cluster>-opensearch-master-1:9200` directly — an app doing this itself
  isn't limited to the `forgelab-logs*` shape or Fluent Bit's syslog-only
  scope; it can define its own index and mapping.

## 9. Troubleshooting

### Lost credentials file

This is the one that will cost you the most time if you don't know it in
advance, so it gets its own section.

`~/.forgelab/<cluster>-credentials.yml` holds the Keycloak admin and
`labuser` passwords, generated once and meant to persist for the life of
the cluster. If that file is deleted (accidentally, or because you're
running from a different machine/home directory than the one that
provisioned the cluster) and you then run `make addons` — or anything else
that re-runs the install stage — `credentials.ensure()` sees no existing
values and mints **brand-new** passwords. Those get written into Keycloak's
Kubernetes Secret, but the already-running Keycloak deployment (and its
Postgres backing store, which reuses the same password) was bootstrapped
with the old ones and doesn't pick up the new Secret value on its own.
Every subsequent Keycloak operation — the Ansible seed tasks, your own
login, `verify.py` — starts failing, and because the task that handles this
value is marked `no_log: true` (deliberately, so a password never lands in
a build log), the Ansible failure output that would normally tell you
"authentication failed" is suppressed. You just see a generic task failure
with no explanation.

If you hit this, the fix is to stop treating the credentials file as
recoverable and just rebuild Keycloak's install from nothing:

```
ssh <cluster>-management-1 "kubectl delete namespace keycloak"
make addons CLUSTER=<cluster>
```

Deleting the namespace removes the Deployment, the Secret, and the
Postgres PVC together, so the next `make addons` run starts Keycloak fresh
against whatever password is (or gets minted) in the credentials file, with
no stale state left to disagree with it.

### OpenSearch / Dashboards restart order

Dashboards' systemd unit declares `Requires=opensearch.service`, so if you
need to restart both, restart OpenSearch first:

```
ssh <cluster>-opensearch-master-1 "sudo systemctl restart opensearch"
ssh <cluster>-opensearch-master-1 "sudo systemctl restart opensearch-dashboards"
```

Restarting Dashboards alone is fine and doesn't touch OpenSearch.

### Forcing Fluent Bit to re-tail

Fluent Bit keeps a position database so it doesn't re-read lines it's
already shipped. To force it to start over — useful if you suspect it got
stuck, or you want to re-ingest from the beginning of the current log
files — stop it, delete that database, and start it again:

```
ssh <cluster>-management-1 "sudo systemctl stop fluent-bit && sudo rm -f /var/lib/fluent-bit/forgelab.db* && sudo systemctl start fluent-bit"
```

That path is confirmed live on a running cluster (Fluent Bit's SQLite
position store shows up there as `forgelab.db`, `forgelab.db-shm`, and
`forgelab.db-wal` — remove all three, the glob above catches them). Do this
on whichever node's Fluent Bit you're trying to reset — management, compute,
hdfs-namenode or hdfs-datanode — the same three files exist on each.

### `make provision` failing on an empty OpenSearch index

If a provision run dies at the verify stage with something like
"opensearch index 'forgelab-logs*' has no documents within timeout," that's
the verifier working as intended, not a flake. `verify.py` checks two
separate things for the opensearch technology: that the cluster reports
green/yellow with the expected node count, and separately that
`forgelab-logs*` actually has a non-zero document count — a healthy,
empty cluster is not considered a pass, because it usually means Fluent
Bit never started or never found anything to tail. Don't just retry the
provision; check whether Fluent Bit is running and whether `/var/log/syslog`
exists and is being written to on the non-opensearch nodes.

### Where the checks themselves live

All of the per-technology verification logic — the Keycloak token check, the HDFS
DataNode/roundtrip check, the OpenSearch health/document-count check — is
in one file:

```
bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py
```

If a `make provision` run fails in a way this guide doesn't explain,
that script is the first place to look at what's actually being asserted.

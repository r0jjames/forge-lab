# forge-lab

A personal, fully code-defined CI/CD infrastructure lab: a Bamboo server and
agent running on a Mac, driving pipelines that provision, install, and
deprovision named multi-node VM clusters (Kubernetes or DC/OS) for deliberate
DevOps practice.

Design source of truth: [`docs/superpowers/specs/2026-07-23-forge-lab-design.md`](docs/superpowers/specs/2026-07-23-forge-lab-design.md).

## What is this

Everything in this repo exists to answer one question repeatedly, on demand:
*"spin me up a fresh k8s or dcos cluster, let me practice on it, then tear it
down cleanly"* — driven entirely from CI pipelines defined as code, not
clicked together by hand.

- **CI engine:** Bamboo Data Center, running in the local Rancher Desktop
  Kubernetes cluster, licensed with a free 24h "timebomb" testing key
  (re-applied each session via `make relicense`).
- **Pipelines as code:** Bamboo Specs (Java + Maven) define the plans;
  `mvn test` validates them offline before publish.
- **CI-agnostic core:** all real provisioning logic lives in
  `provisioning/scripts/*.sh`. Bamboo Specs tasks, the Makefile, and (if
  Bamboo's DC line is ever pulled) a future Jenkinsfile all call the *same*
  scripts. This isolates the Bamboo licensing risk from the actual lab work.
- **Provisioned target:** Multipass VMs, wired up with Terraform (VM
  creation) + Ansible (OS-level install: kubeadm/containerd for k8s, or the
  DC/OS installer).

This is a personal learning lab, not production infrastructure. Design
choices favor simplicity, local-only operation, and clear boundaries over
robustness at scale.

## Architecture

```
Mac host (32Gi+)
├── Rancher Desktop K8s
│   ├── ns ci: bamboo-server   (Atlassian Helm chart, timebomb 24h license)
│   └── ns ci: postgres        (Bitnami chart, PVC)
├── bamboo-agent               (plain process on Mac — Phase 1)
│   └── has: JDK 17, terraform, ansible, multipass CLI (direct)
└── Multipass VMs              (created by pipelines only)
    ├── <name>-mgmt-1..N
    └── <name>-compute-1..N    (counts/sizes from clusters/<name>.tfvars)
```

**CI-agnostic core:** all real logic lives in `provisioning/scripts/*.sh`
(calling Terraform + Ansible) and runs from a plain shell. Bamboo Specs tasks
are one-liners calling those scripts; the Makefile calls the same scripts; a
future Jenkinsfile would too. This isolates the Bamboo licensing risk.

**Swappable VM backend:** the root Terraform module exposes a stable
contract (cluster_name + node specs in → node IPs + rendered inventory out)
and delegates to `provisioning/terraform/modules/multipass/`. Swapping to
UTM or libvirt later means writing a sibling module and flipping a `backend`
variable. Ansible only ever sees the generated inventory, never the backend.
Deprovision's backend sweep step is backend-scoped too.

### Key decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | CI engine | **Bamboo DC + 24h timebomb license** | Atlassian stopped new DC trial licenses 2026-03-30, but free 10-user 24h timebomb keys remain published on the Atlassian developer site for testing. Re-apply key per session; `bamboo-home` PVC persists everything else. Jenkins remains a paper fallback via the CI-agnostic core. |
| D2 | Host K8s | **Rancher Desktop** | Already installed, lighter than UTM kubeadm, easy localhost access. |
| D3 | Agent placement (Phase 1) | **Plain process on Mac host** | Direct access to `multipass`/`terraform`; zero SSH plumbing. Pod agent + SSH-to-host deferred to Phase 2+. |
| D4 | Per-cluster config | **tfvars file per cluster** (`clusters/<name>.tfvars`) | Node counts, cpu/mem/disk, cluster_type versioned in repo; `defaults.tfvars` fallback; Terraform-native, no glue parser. |
| D5 | TF state | Local backend + workspace per cluster | No cloud dependency; no state collisions. |
| D6 | K8s install method | kubeadm + containerd | Matches real-world and prior UTM learning. |
| D7 | VM backend | **Multipass, but swappable** | Backend isolated in a Terraform module; UTM/libvirt later = new module + one variable flip. Ansible only ever sees the generated inventory. |

## Prerequisites

Install on the Mac host before starting:

- **Rancher Desktop** — provides the local Kubernetes cluster Bamboo runs
  on, plus a working `kubectl`/`helm` context.
- **helm** (v3) — installs the Bamboo + Postgres charts.
- **terraform** — provisions Multipass VMs.
- **ansible** — installs k8s/dcos onto provisioned VMs.
- **multipass** — creates the local VMs pipelines target.
- **jq** — used by scripts to parse JSON (Terraform output, `multipass list --format json`, etc).
- **JDK 17 + maven** — builds and validates Bamboo Specs (`bamboo-specs/`).
- **shellcheck** — lints `provisioning/scripts/*.sh` (part of `make lint`).

Quick check that everything is on `PATH`:

```bash
for bin in kubectl helm terraform ansible multipass jq mvn shellcheck; do
  command -v "$bin" >/dev/null || echo "MISSING: $bin"
done
java -version   # expect 17.x
```

Rancher Desktop must be **running** (with Kubernetes enabled) before `make up`.

## Quick start

Target: working lab, from a clean checkout, in under an hour.

```bash
# 1. One-time setup checks/bootstrap (verifies prerequisites, prepares local state dirs)
make setup

# 2. Bring up the CI stack — Bamboo + Postgres into the `ci` namespace on
#    Rancher Desktop's Kubernetes
make up

# 3. Apply the timebomb license (see "License ritual" below), then open the UI
make ui
# → http://localhost:8085
```

Then, once Bamboo is licensed and reachable:

```bash
# 4. Install and start the host-local Bamboo agent
make agent-install
make agent-run

# 5. Publish the hello-world Bamboo Specs plan to prove the Specs → server
#    publish loop works end to end
make specs-publish
```

At this point you have a licensed Bamboo server, a connected local agent, and
a published plan — the CI up (Phase 1) definition of done. Provisioning a
cluster (Phase 2+) is covered next.

## Pipeline usage

### Provisioning a cluster

```bash
make provision CLUSTER=lab1              # uses clusters/lab1.tfvars if present
make provision CLUSTER=lab1 TYPE=dcos    # override cluster_type from the tfvars file
```

What happens, stage by stage (whether triggered via `make` or via the
Bamboo `ProvisionClusterPlan`, since both call the same
`provisioning/scripts/*.sh`):

1. **Validate** — checks `cluster_name` matches `[a-z0-9-]+`; refuses if
   `multipass list` already shows `<name>-` prefixed VMs; resolves config
   from `clusters/<name>.tfvars` if it exists, else `clusters/defaults.tfvars`.
   An explicit `TYPE=` overrides whatever `cluster_type` the tfvars file sets.
2. **Provision** — `terraform workspace select/new <name>`, then
   `apply -var-file=<resolved> -var cluster_name=<name>`; renders
   `provisioning/ansible/inventory/<name>.ini` from the Terraform output.
3. **Install** — `ansible-playbook site.yml -i inventory/<name>.ini -e cluster_type=<type>`.
   k8s path: kubeadm + containerd + CNI. dcos path: pinned installer version,
   cached locally.
4. **Verify** — k8s: all nodes reach `Ready`. dcos: UI health endpoint
   responds OK. The pipeline fails if verification doesn't pass.

### Deprovisioning a cluster

```bash
make deprovision CLUSTER=lab1
```

1. `terraform workspace select <name>` → `destroy -auto-approve`.
2. Backend sweep: `multipass list | grep '<name>-'` → `multipass delete --purge`
   any stragglers the backend's Terraform provider left behind.
3. Removes the generated inventory file and the Terraform workspace.

Deprovision is **idempotent** — safe to run twice, and it's also the
recovery path after a partial/failed provision (see Troubleshooting).

### Why tfvars-per-cluster

Each named cluster gets its own `clusters/<name>.tfvars` (node counts,
cpu/mem/disk per node, `cluster_type`), versioned in the repo. If a cluster
has no dedicated tfvars file, `clusters/defaults.tfvars` is used instead.
This keeps sizing decisions in git history, avoids inventing a custom
config-parsing layer (Terraform reads `.tfvars` natively), and lets you keep
multiple named clusters' configs side by side without collision — each
cluster is also its own Terraform workspace, so state never crosses streams.

Default VM sizing (tunable per cluster tfvars):
- mgmt: 2 CPU / 4G / 20G
- compute ×2: 2 CPU / 3G / 20G

DC/OS wants more than this; expect a degraded/minimal install under these
defaults on constrained hosts — bump the tfvars if you have the headroom.

## License ritual

Bamboo Data Center no longer issues new trial licenses (Atlassian stopped
2026-03-30), but a free, published **10-user, 24-hour timebomb key** for
testing Marketplace apps still works for standing up an instance. This lab
uses that key purely for local, personal, non-commercial learning — it is
not an official free tier, and the key **expires 24 hours after
installation**.

- **What:** a timebomb license key from developer.atlassian.com ("Timebomb
  licenses for testing server apps").
- **Where:** pasted into Bamboo's license admin screen on first setup, and
  re-applied there whenever it expires. The `bamboo-home` PVC persists
  everything else (plans, specs, configuration) across relicensing — only
  the license itself needs refreshing.
- **Expiry:** 24 hours after each application. When it lapses, Bamboo
  refuses to run builds until relicensed; the agent stays connected and
  reconnects unaffected once you're relicensed.
- **Ritual:** run `make relicense` — opens the license admin page (or hits
  the license REST endpoint) so you can paste the same key back in.

Context: the entire Bamboo Data Center product line sunsets, with all DC
licenses expiring March 2029. The CI-agnostic core (see Architecture) means
a Jenkins fallback is a straightforward drop-in if this licensing path ever
dries up sooner.

## VM backend swap boundary

Multipass is the only VM backend implemented, but the boundary is
deliberate: the root Terraform module (`provisioning/terraform/`) exposes a
fixed contract — cluster name + per-node specs in, node IPs + a rendered
Ansible inventory out — and delegates the actual VM lifecycle to
`provisioning/terraform/modules/multipass/`.

To add a different backend (UTM, libvirt, etc.) later:

1. Write a sibling module, e.g. `provisioning/terraform/modules/utm/`,
   implementing the same input/output contract.
2. Flip the `backend` variable to select it.
3. Nothing above the module boundary changes: Ansible only ever consumes the
   generated inventory file, never anything backend-specific, and the
   deprovision backend-sweep step is scoped per backend so cleanup logic
   doesn't need to change either.

## Troubleshooting

**License expired mid-session**
Builds refuse to start; nothing else is affected. Run `make relicense`,
paste the same timebomb key back in. The agent stays connected/reconnects
on its own — no need to restart it.

**Multipass flakes / provider errors**
The community-maintained Multipass Terraform provider is known to be a bit
flaky. Scripts pin a known-good provider version and retry `apply` once on
recognized transient errors. If it still fails, check `multipass list` for
stray VMs and consider a manual `multipass delete --purge <name>` before
retrying.

**Partial provision failure**
There is no automatic rollback. Run `make deprovision CLUSTER=<name>` — it's
idempotent and is the designated recovery path: it destroys Terraform state
for that workspace, sweeps any leftover `<name>-*` VMs directly via
`multipass`, and removes the generated inventory, leaving you clean to
provision again.

**`make up` succeeds but Bamboo UI won't load**
Confirm Rancher Desktop's Kubernetes is actually running (`kubectl get
nodes`), then check pod status in the `ci` namespace
(`kubectl -n ci get pods`) — Postgres or Bamboo may still be starting up;
give it a minute and check `kubectl -n ci logs` on the slow pod.

## Repository layout

```
forge-lab/
├── README.md                       # this file
├── CLAUDE.md                       # repo conventions, commands, layout map
├── Makefile                        # up / down / provision / deprovision / relicense / lint
├── infra/
│   ├── helm/                       # Bamboo + Postgres chart values
│   ├── agent/                      # host-local agent install/run scripts
│   └── README.md                   # bring-up runbook detail
├── bamboo-specs/                   # Java + Maven (Bamboo Specs plans-as-code)
├── clusters/                       # per-cluster tfvars (+ defaults.tfvars)
├── provisioning/
│   ├── terraform/                  # main.tf/variables/outputs, modules/multipass/
│   ├── ansible/                    # site.yml, generated inventory (gitignored), roles
│   └── scripts/                    # provision.sh / deprovision.sh — the CI-agnostic core
└── docs/                           # PRD, tech spec, design docs
```

See `CLAUDE.md` for the day-to-day command reference and conventions.

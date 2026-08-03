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
  (fetched via `make license`, re-applied each session via `make relicense`).
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
- **shellcheck** and **ansible-lint** — static checks, both part of `make lint`.

Quick check that everything is on `PATH`:

```bash
for bin in kubectl helm terraform ansible ansible-lint multipass jq mvn shellcheck; do
  command -v "$bin" >/dev/null || echo "MISSING: $bin"
done
java -version   # expect 17.x
```

Rancher Desktop must be **running** (with Kubernetes enabled) before `make up`.

**ansible-galaxy collections (one-time, needed for `make lint`)** — if
`ansible-lint` was installed via Homebrew, it runs in its own isolated venv
and does *not* see the collections bundled inside Homebrew's `ansible`
formula, even though `ansible-playbook` finds them fine. This surfaces as
`unknown-module: community.general.modprobe` (or `ansible.posix.sysctl`)
from `ansible-lint` alone. Fix once, for both tools:

```bash
ansible-galaxy collection install community.general ansible.posix
```

This installs into `~/.ansible/collections/`, the shared default path both
Homebrew `ansible-lint` and `ansible-playbook` search.

## Quick start

Target: working lab, from a clean checkout, in under an hour.

```bash
# 1. One-time setup checks/bootstrap (verifies prerequisites, prepares local state dirs)
make setup

# 2. Bring up the CI stack — Bamboo + Postgres into the `ci` namespace on
#    Rancher Desktop's Kubernetes
make up

# 3. Grab the 24h timebomb license key (copied to your clipboard), then open
#    the UI and paste it into the setup wizard (see "License ritual" below)
make license
make ui
# → http://localhost:8085
```

Then, once Bamboo is licensed and reachable:

```bash
# 4. Get an agent token, then install and start the host-local Bamboo agent
#    Bamboo UI → Administration → Agents → "Install remote agent" shows the
#    token (enable "security token verification" there first if prompted);
#    export it before running install-agent.sh:
export AGENT_TOKEN=<token from the UI>
make agent-install
make agent-run
```

`install-agent.sh` copies the installer jar straight out of the Bamboo
server pod with `kubectl cp` (the jar isn't downloadable without an admin
login, but it's right there in the pod), then runs it against
`/agentServer/` with your token. Overridable via `BAMBOO_NAMESPACE`
(default `ci`) and `BAMBOO_CONTAINER` (default `bamboo`); needs `kubectl`
(Rancher Desktop provides it) and a JDK on `PATH`.

Leave `make agent-run` running in its own terminal (or under `launchd` —
see `infra/agent/run-agent.sh`); it needs to stay up for plans to build.

`run-agent.sh` also seeds the capability `agent.role=host` into
`<agent-home>/bin/bamboo-capabilities.properties`. The Provision/Deprovision
plans declare a matching **requirement**, which is what keeps the multipass
toolchain jobs off the containerized k8s agent (`agent.role=ci`, no terraform
or multipass in that image). Bamboo only reads that properties file on an
agent's *first* startup, so for an agent that is already registered add the
capability once against the running server:

```bash
TOKEN=$(grep -m1 '^token=' bamboo-specs/.credentials | cut -d= -f2-)
AGENT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8085/rest/api/latest/agent | jq -r '.[0].id')
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"agent.role","value":"host"}' \
  "http://localhost:8085/rest/api/latest/agent/$AGENT_ID/capability"   # 204 = added
```

(Or by hand: Administration → Agents → *your host agent* → Capabilities → Add
capability, type **Custom**, key `agent.role`, value `host`.)

With security token verification enabled, a freshly started agent registers
but stays **pending until you approve it**: Administration → Agents →
Agent authentication → approve the new UUID (the agent log prints the exact
approval URL). Until then the agent retries every 60 seconds.

Before publishing Specs, two one-time Bamboo UI steps are required (both
persist in the `bamboo-home` PVC, so this is genuinely one-time per
install):

1. **Specs credentials** — `bamboo-specs/.credentials` is gitignored and
   not created for you. Bamboo Specs authenticates via a personal access
   token; create one in the Bamboo UI (profile → Personal Access Tokens),
   then write it as a Java properties file:
   ```bash
   echo "token=<your PAT>" > bamboo-specs/.credentials
   ```
   (`exec:java`'s working directory is the `bamboo-specs/` module root, so
   the file must live there, not at the repo root.)
2. **Linked repository** — Administration → Linked Repositories → add
   `git@github.com:r0jjames/forge-lab.git`. The three forge-lab specs
   (`HelloWorldSpec`, `ProvisionClusterSpec`, `DeprovisionClusterSpec`) call
   `defaultRepository()`, which resolves against this linked repo by name —
   publish fails without it. `BuildAgentImageSpec` needs no such step: it
   declares a plan-local repository for the public bamboo-agent repo.

```bash
# 5. Publish all Bamboo Specs plans, proving the Specs → server publish
#    loop works end to end
make specs-publish
```

This also publishes `AGENT-BUILD`, the plan that builds the containerized CI
agent image from the [bamboo-agent](https://github.com/r0jjames/bamboo-agent)
repo. It only runs on an agent with the `agent.role=ci` capability (that same
containerized agent), so it stays queued until one is deployed and approved.

At this point you have a licensed Bamboo server, a connected local agent, and
published plans — the CI up (Phase 1) definition of done. Provisioning a
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

### DC/OS: installer cache and the Apple Silicon blocker

DC/OS installs use a pinned installer version, downloaded once and cached
at `~/.forgelab/cache/dcos_generate_config.sh` (`~967MB`; the role's
`dcos_installer_local` default). Fetch it manually before a first dcos run,
or just let the role's bootstrap task fetch/cache it on demand:

```bash
mkdir -p ~/.forgelab/cache
curl -fSL -o ~/.forgelab/cache/dcos_generate_config.sh \
  https://downloads.dcos.io/dcos/stable/2.0.3/dcos_generate_config.sh
```

The role is pinned to **2.0.3**, not the newer 2.2.13 line — `2.2.13`
returns `403 Forbidden` from `downloads.dcos.io` (dead link), while `2.0.3`
is confirmed available and downloads cleanly.

**Known blocker — DC/OS does not run on Apple Silicon (arm64) hosts.** The
DC/OS 2.x `genconf` step runs an amd64-only Docker image; on an arm64 host
(any M-series Mac) it fails deterministically during install:

```
exec /installer_internal_wrapper: exec format error
```

This is an architecture mismatch, not a flaky failure — retrying reproduces
the identical error every time. The `dcos` Ansible role itself is
static-complete and lint-clean; a live `dcos` install requires genuine
amd64 hardware (or an amd64 VM/emulation layer outside this lab's scope).
`k8s` is unaffected and is the recommended path on Apple Silicon hosts.

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
  licenses for testing server apps"). You don't have to hunt for it —
  `make license` fetches the current key straight off that page and copies
  it to your clipboard.
- **Where:** pasted into Bamboo's license admin screen on first setup, and
  re-applied there whenever it expires. The `bamboo-home` PVC persists
  everything else (plans, specs, configuration) across relicensing — only
  the license itself needs refreshing.
- **Expiry:** 24 hours after each application. When it lapses, Bamboo
  refuses to run builds until relicensed; the agent stays connected and
  reconnects unaffected once you're relicensed.
- **First-time setup wizard:** run `make license` — it prints the key and
  copies it to your clipboard; paste it into the wizard's license field.
- **After expiry:** run `make relicense` — fetches + copies the current key
  **and** opens Bamboo's license admin page so you can paste and save.

Both commands wrap `infra/scripts/get-license.sh`, which scrapes the Bamboo
Data Center 24h key from the Atlassian developer page (needs `curl` +
`python3`, both stock on macOS). Override `LICENSE_LABEL` to pull a
different published key.

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
Builds refuse to start; nothing else is affected. Run `make relicense` — it
re-fetches the current timebomb key, copies it to your clipboard, and opens
the license admin page to paste it. The agent stays connected/reconnects on
its own — no need to restart it.

**`make license` prints nothing / errors**
It scrapes the live Atlassian developer page, so it needs network access plus
`curl` and `python3`. If Atlassian renames the license block, override the
label: `LICENSE_LABEL='10 user Bamboo Data Center license, expires in 24 hours' make license`.
As a fallback, open the page yourself (printed in the error) and copy the
"Bamboo Data Center" key by hand.

**`make agent-install` can't find the pod / jar**
It looks for a Bamboo pod in namespace `ci` (override `BAMBOO_NAMESPACE`) and
`kubectl cp`s the installer jar out of it. Make sure `make up` finished and
`kubectl -n ci get pods` shows `bamboo-0` Running. `kubectl cp` needs `tar`
in the pod (the Atlassian image ships it).

**Agent installed but no builds run**
With security token verification on, the agent must be **approved** after it
first connects: Administration → Agents → Agent authentication → approve its
UUID (the `agent-run` log prints the approval URL). It retries every 60s
until approved.

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

**`ansible-lint` fails with `unknown-module: community.general....`**
See "ansible-galaxy collections" under Prerequisites — Homebrew's
`ansible-lint` doesn't see collections bundled inside Homebrew's `ansible`
formula. `ansible-galaxy collection install community.general
ansible.posix` fixes it for both tools.

**`specs-publish` fails with `Couldn't find credentials file: .credentials`**
`bamboo-specs/.credentials` doesn't exist yet — see "Specs credentials" in
Quick start step 4. If it fails instead with a repository/`defaultRepository`
error, the Linked Repository step (same section) hasn't been done in the
Bamboo UI yet.

**Writing new Ansible `shell` tasks that pipe commands**
`ansible.builtin.shell` runs under `/bin/sh` (dash on Ubuntu) unless told
otherwise, and dash rejects bash-only syntax like `set -o pipefail`. Any
shell task relying on `set -eo pipefail` (or other bashisms) needs an
explicit `args: { executable: /bin/bash }` — this bit the `k8s` role's
containerd-config task live once already (fixed); keep it in mind if you
extend the roles.

**DC/OS install fails with `exec /installer_internal_wrapper: exec format
error`**
Expected on Apple Silicon — see "DC/OS: installer cache and the Apple
Silicon blocker" above. Not fixable from this repo; use `k8s` instead, or
run on amd64 hardware.

## Repository layout

```
forge-lab/
├── README.md                       # this file
├── CLAUDE.md                       # repo conventions, commands, layout map
├── Makefile                        # up / down / provision / deprovision / relicense / lint
├── infra/
│   ├── helm/                       # Bamboo + Postgres chart values
│   └── agent/                      # host-local agent install/run scripts
├── bamboo-specs/                   # Java + Maven (Bamboo Specs plans-as-code)
│   ├── src/main/java/lab/plans/    # forge-lab's own plans (FORGE-*)
│   └── src/main/java/lab/agent/    # bamboo-agent image build plan (AGENT-BUILD)
├── clusters/                       # per-cluster tfvars (+ defaults.tfvars)
├── provisioning/
│   ├── terraform/                  # main.tf/variables/outputs, modules/multipass/
│   ├── ansible/                    # site.yml, generated inventory (gitignored), roles
│   └── scripts/                    # provision.sh / deprovision.sh — the CI-agnostic core
└── docs/                           # PRD, tech spec, design docs
```

See `CLAUDE.md` for the day-to-day command reference and conventions.

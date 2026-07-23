# Design: forge-lab — Personal CI/CD Infrastructure Lab

**Status:** Approved v1 | **Date:** 2026-07-23 | **Owner:** Roj
**Sources:** PRD and Tech Spec drafts in second-brain (`2-Projects/Forge-Lab Personal CICD Infrastructure Lab/`)

## 1. Purpose

A personal, fully code-defined CI/CD lab on a Mac: Bamboo server + agent running locally, driving pipelines that provision, install, and deprovision named multi-node VM clusters (Kubernetes or DC/OS) for deliberate DevOps practice.

## 2. Decisions (resolved open questions)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | CI engine | **Bamboo DC + 24h timebomb license** | Atlassian stopped new DC trial licenses 2026-03-30, but free 10-user 24h timebomb keys remain published on the Atlassian developer site for testing. Re-apply key per session; `bamboo-home` PVC persists everything else. Jenkins remains a paper fallback via the CI-agnostic core. |
| D2 | Host K8s | **Rancher Desktop** | Already installed, lighter than UTM kubeadm, easy localhost access. |
| D3 | Agent placement (Phase 1) | **Plain process on Mac host** | Direct access to `multipass`/`terraform`; zero SSH plumbing. Pod agent + SSH-to-host deferred to Phase 2+. |
| D4 | Per-cluster config | **tfvars file per cluster** (`clusters/<name>.tfvars`) | Node counts, cpu/mem/disk, cluster_type versioned in repo; `defaults.tfvars` fallback; Terraform-native, no glue parser. |
| D5 | TF state | Local backend + workspace per cluster | No cloud dependency; no state collisions. |
| D6 | K8s install method | kubeadm + containerd | Matches real-world and prior UTM learning. |
| D7 | VM backend | **Multipass, but swappable** | Backend isolated in a Terraform module; UTM/libvirt later = new module + one variable flip. Ansible only ever sees the generated inventory. |

### Licensing detail (D1)

- Free 10-user Bamboo Data Center timebomb key, expires 24h after installation, published at developer.atlassian.com ("Timebomb licenses for testing server apps").
- Workflow: paste key at setup; when expired, re-apply the same key (`make relicense` helper opens the license UI or hits the REST endpoint).
- Intended for Marketplace app testing — acceptable for local, personal, non-commercial learning; not an official free tier. Documented in runbook.
- Context: entire DC product line sunsets — all DC licenses expire March 2029. Jenkins fallback path is insurance.

## 3. Architecture

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

**Key principle — CI-agnostic core:** all real logic lives in `provisioning/scripts/*.sh` (calling Terraform + Ansible) and runs from a plain shell. Bamboo Specs tasks are one-liners calling those scripts; the Makefile calls the same scripts; a future Jenkinsfile would too. This isolates the Bamboo licensing risk.

**Key principle — swappable VM backend:** root Terraform module exposes a stable contract (cluster_name + node specs in → node IPs + rendered inventory out) and delegates to `modules/multipass/`. Swapping to UTM or libvirt means writing a sibling module and flipping a `backend` variable. Deprovision sweep step is backend-scoped.

## 4. Repository layout

```
forge-lab/
├── README.md                       # detailed: what/why, architecture, full runbook,
│                                   # pipeline usage, license ritual, troubleshooting
├── CLAUDE.md                       # repo conventions, commands, layout map
├── Makefile                        # up / down / provision / deprovision / relicense / lint
├── infra/
│   ├── helm/
│   │   ├── bamboo-values.yaml      # Atlassian chart values, PVC, ~2CPU/4Gi
│   │   └── postgres-values.yaml
│   ├── agent/
│   │   ├── install-agent.sh        # download remote-agent jar, register to server
│   │   └── run-agent.sh            # launchd-friendly wrapper, capability list
│   └── README.md                   # bring-up runbook (linked from root README)
├── bamboo-specs/                   # Java + Maven (Bamboo Specs)
│   ├── pom.xml
│   └── src/main/java/lab/plans/
│       ├── HelloWorldPlan.java     # Phase 1: proves Specs→server publish loop
│       ├── ProvisionClusterPlan.java
│       └── DeprovisionClusterPlan.java
├── clusters/
│   ├── defaults.tfvars
│   └── lab1.tfvars                 # cluster_type, mgmt/compute counts, cpu/mem/disk
├── provisioning/
│   ├── terraform/
│   │   ├── main.tf  variables.tf  outputs.tf
│   │   ├── modules/multipass/      # backend module (swappable boundary)
│   │   └── templates/inventory.tftpl
│   ├── ansible/
│   │   ├── site.yml                # branches on cluster_type
│   │   ├── inventory/              # generated per cluster (gitignored)
│   │   └── roles/ common/ k8s/ dcos/
│   └── scripts/
│       ├── provision.sh            # validate → tf apply → ansible → verify
│       └── deprovision.sh          # tf destroy → backend sweep → cleanup
└── docs/                           # PRD, TECH_SPEC, this design
```

Deferred from v1: `infra/docker/agent.Dockerfile` (pod agent, Phase 2+), `cli/` Python typer CLI (P1, Phase 5).

## 5. Pipelines

### Provision plan

Plan variables: `cluster_name` (required, `[a-z0-9-]+`), `cluster_type` (optional override: `k8s` | `dcos`).

1. **Validate** — regex-check name; refuse if `multipass list` shows `<name>-` prefixed VMs; resolve config: `clusters/<name>.tfvars` if present, else `defaults.tfvars`; explicit `cluster_type` plan variable overrides the tfvars value.
2. **Provision** — `terraform workspace select/new <name>` → `apply -var-file=<resolved> -var cluster_name=<name>`; render `ansible/inventory/<name>.ini`.
3. **Install** — `ansible-playbook site.yml -i inventory/<name>.ini -e cluster_type=<type>`. k8s: kubeadm + containerd + CNI. dcos: pinned installer version, cached locally.
4. **Verify** — k8s: all nodes `Ready`; dcos: UI health endpoint OK. Plan fails otherwise.

### Deprovision plan

Plan variable: `cluster_name`.

1. `terraform workspace select <name>` → `destroy -auto-approve`
2. Backend sweep: `multipass list | grep '<name>-'` → `multipass delete --purge` leftovers
3. Remove generated inventory + workspace. Idempotent — safe to run twice; this is also the recovery path after partial provision failure.

### VM sizing defaults (tunable per cluster tfvars)

- mgmt: 2 CPU / 4G / 20G; compute ×2: 2 CPU / 3G / 20G
- DC/OS wants more; accept degraded/minimal install for learning, document limits.

## 6. Error handling

- All scripts `set -euo pipefail`; Validate stage fails fast with clear messages (bad name, cluster exists, missing tfvars keys).
- Multipass Terraform provider is community-maintained and flaky: pin version, retry apply once on known transient errors.
- Partial provision failure: no auto-rollback; run deprovision plan (idempotent cleanup).
- License expiry mid-session: builds refuse to start; run `make relicense`; agent reconnects unaffected.

## 7. Testing

- `make lint`: `terraform validate` + `fmt -check`, `ansible-lint`, `shellcheck`.
- Bamboo Specs: `mvn test` (offline Specs validation) before publish.
- Integration test = the pipeline's own Verify stage (nodes Ready / DC/OS health). No mocked unit tests for provisioning scripts — lab context, YAGNI.

## 8. Phasing

1. **Phase 1 — CI up:** scaffold, README + CLAUDE.md, Helm bring-up (Bamboo + Postgres), timebomb license flow, host agent registered, hello-world Specs plan publishing.
2. **Phase 2 — Provision:** Terraform multipass module + workspaces + tfvars resolution + inventory template; Provision stage green for `lab1`.
3. **Phase 3 — Install:** Ansible roles; k8s path first, then dcos (pinned + cached installer).
4. **Phase 4 — Deprovision + Verify stages + runbook polish.**
5. **Phase 5 (P1):** Python CLI, pod agent + SSH-to-host, ephemeral agents.

## 9. Definition of Done (v1)

- [ ] `make up` → Bamboo UI on localhost, agent online — **PENDING HUMAN**:
      Postgres + Bamboo Helm bring-up is live-proven on Rancher Desktop ns
      `ci` (`ingress.https: false` required for plain-HTTP port-forward,
      already committed/commented in bamboo-values), but the setup wizard,
      timebomb license application, and agent registration (needs
      `AGENT_TOKEN` minted from the post-wizard UI) all require a human at
      the browser. Agent install/run scripts are ready and untested live.
- [x] Provision `lab1` (tfvars-driven sizing) with `k8s` → 3 nodes Ready —
      **LIVE PROVEN**: `make provision CLUSTER=lab1 TYPE=k8s` reaches 3
      `Ready` nodes in ~4.5 min (Task 11). Required one live-run fix: Ansible
      `shell` tasks using `set -o pipefail` need `executable: /bin/bash`
      (dash rejects the flag) — fixed in the k8s role's containerd-config
      task.
- [ ] Same with `dcos` → UI reachable — **BLOCKED, deterministic, not
      transient**: the `dcos` role is static-complete and ansible-lint-clean
      (Task 13), and the pinned installer (2.0.3; 2.2.13 is a dead link,
      returns 403) downloads and caches correctly at
      `~/.forgelab/cache/dcos_generate_config.sh` (~967MB). Live install
      fails at the `genconf` step with `exec format error`: DC/OS 2.x's
      `genconf` Docker image is amd64-only, and this host (and any
      Apple Silicon Mac) is arm64. Not fixable within this repo — requires
      real amd64 hardware. Known open design risk noted alongside this:
      port 8080 double-use on mgmt[0] (bootstrap nginx vs Marathon) is
      untested and amd64-only-hardware-only to verify.
- [x] Deprovision `lab1` → zero VMs, state, or inventory left — **LIVE
      PROVEN**: `make deprovision CLUSTER=lab1` cleans Terraform state,
      sweeps stray VMs, and removes the generated inventory; confirmed
      idempotent (safe to run twice) and confirmed unrelated pre-existing
      VMs (`tf-*`) are left untouched by the backend sweep (Task 12).
- [ ] Fresh-machine bring-up < 1h from README alone — **PENDING HUMAN**: the
      code-defined path (`make setup` → `make up` → `make provision` →
      `make deprovision`) is live-proven end to end for k8s and is fast
      (~4.5 min for provision alone); the < 1h estimate holds for the
      scripted portions. What remains unverified end-to-end on a genuinely
      fresh machine is the manual, one-time human portion: Bamboo setup
      wizard, timebomb license paste, `AGENT_TOKEN` mint + agent install,
      Specs `.credentials` file, and the Linked Repository registration —
      all now documented step-by-step in the README's Quick start.
- [x] Backend swap boundary documented (multipass module) — true: the root
      Terraform module's fixed contract (cluster_name + node specs in →
      node IPs + rendered inventory out) delegating to
      `provisioning/terraform/modules/multipass/` is documented in both the
      README ("VM backend swap boundary") and this design doc (§3, D7);
      Ansible only ever consumes the generated inventory and the
      deprovision sweep step is backend-scoped.

## 10. Risks

| Risk | Mitigation |
|------|-----------|
| Timebomb key withdrawn / DC line EOL (2029) | CI-agnostic core; Jenkins path drop-in |
| Host resources (CI + 3 VMs ≈ 8+ CPU / 14+ Gi) | 32Gi machine; per-cluster tfvars sizing; deprovision discipline |
| DC/OS EOL, installers aging | Pin known-good version, cache installer locally |
| Multipass provider maturity | Pin version, retry wrapper |

# Plan-centric repository structure

**Date:** 2026-08-03
**Status:** approved

## Problem

Code for a single Bamboo plan is scattered across three top-level trees. To
answer "what does the Provision plan actually run?" you must read
`bamboo-specs/src/main/java/lab/plans/ProvisionClusterSpec.java`, then
`provisioning/scripts/provision.sh`, then `provisioning/scripts/lib.sh`, then
`provisioning/terraform/`, then `provisioning/ansible/`, then `clusters/`.
Nothing in the layout says which of those belong to which plan, and
`infra/agent/*.sh` (host-operator scripts, never run by a plan) sits next to
`infra/helm/` as if it were the same kind of thing.

The `HelloWorldSpec` plan has served its purpose — it proved the
Specs-to-server publish loop in Phase 1 — but it now carries the shared
`BAMBOO_URL` and `REPO_NAME` constants that the real plans import, so it
cannot simply be deleted.

## Goal

One directory per plan, holding every piece of code that plan executes.
A single obvious home for code shared between plans. A written contract so
every future plan lands in the same shape.

## Design

### Target tree

```
forge-lab/
├── plans/                          # everything a Bamboo plan executes
│   ├── README.md                   # the structure contract
│   ├── shared/                     # used by 2+ plans
│   │   ├── scripts/lib.sh
│   │   ├── terraform/              # was provisioning/terraform/
│   │   ├── ansible/                # was provisioning/ansible/
│   │   └── clusters/               # was clusters/
│   ├── provision-cluster/
│   │   └── scripts/{provision.sh,verify.sh}
│   ├── deprovision-cluster/
│   │   └── scripts/deprovision.sh
│   └── agent-image/
│       └── README.md               # build script lives in the bamboo-agent repo
├── infra/                          # lab operations, NOT plan-executed (unchanged)
│   ├── helm/                       # Bamboo + Postgres chart values
│   ├── agent/                      # host-local agent install/run
│   └── scripts/                    # license fetch/relicense
├── bamboo-specs/src/main/java/lab/
│   ├── shared/SpecConstants.java   # BAMBOO_URL, REPO_NAME
│   ├── provisioncluster/ProvisionClusterSpec.java
│   ├── deprovisioncluster/DeprovisionClusterSpec.java
│   └── agentimage/BuildAgentImageSpec.java
├── docs/
├── Makefile
├── README.md
└── CLAUDE.md
```

`provisioning/` and the top-level `clusters/` directory cease to exist.

### The contract for every new plan

Recorded in `plans/README.md` and in CLAUDE.md so it is enforced on future work:

1. A plan gets `plans/<plan-id>/` — kebab-case, named after the plan, one
   directory per spec.
2. Its scripts go in `plans/<plan-id>/scripts/`. No other plan references them.
3. Its spec is `bamboo-specs/src/main/java/lab/<planid>/<Name>Spec.java`, where
   `<planid>` is the plan-id with dashes stripped. One test class beside it.
4. Anything reused by 2+ plans goes in `plans/shared/`. A plan never reaches
   into another plan's directory.
5. Anything not executed by a plan (helm values, license fetch, host-agent
   install/run) belongs in `infra/`, not `plans/`.
6. The spec class is registered in the Makefile's `SPEC_CLASSES`.

### Why Java specs stay in the Maven module

Maven requires sources under `src/main/java/<package-path>`. Putting the `.java`
next to its scripts costs either a `build-helper-maven-plugin` source root per
plan or a Maven module per plan — a pom edit for every new plan, in exchange for
adjacency. Instead the package name mirrors the plan directory name, giving a
1:1 mapping that is obvious in both directions with no build configuration.

Package names cannot contain dashes, so the plan-id's dashes are stripped:
`plans/provision-cluster/` ↔ `lab.provisioncluster`.

### Why terraform and ansible live in `plans/shared/`

Provision and deprovision are two separate plans, but they operate on the same
Terraform state directory, the same Ansible tree, and the same per-cluster
tfvars. Duplicating those would create two sources of truth for one cluster's
state; assigning them to `provision-cluster/` would make `deprovision-cluster/`
reach across a plan boundary. `plans/shared/` is the correct home under rule 4.

This preserves the CI-agnostic core principle from the original design: real
logic still lives in shell scripts callable from a plain terminal, and Bamboo
spec tasks remain one-liners that invoke them.

### Hello-world removal

`HelloWorldSpec` and `HelloWorldSpecTest` are deleted. Their two constants move
to a new `lab.shared.SpecConstants` (`public static final BAMBOO_URL`,
`REPO_NAME`), which the cluster specs import instead of reaching into another
plan's class. `lab.plans.HelloWorldSpec` is dropped from `SPEC_CLASSES`.

Deleting the spec does not delete the plan already published on the Bamboo
server. `FORGE-HELLO` must be deleted once by hand in the Bamboo UI
(Administration → Plans, or the plan's own Actions → Delete plan).

### Path rewrites

| Location | Change |
|---|---|
| `ProvisionClusterSpec` inlineBody | `plans/provision-cluster/scripts/provision.sh` |
| `DeprovisionClusterSpec` inlineBody | `plans/deprovision-cluster/scripts/deprovision.sh` |
| `lib.sh` | `REPO_ROOT` climbs three levels; `TF_DIR`, `INV_DIR`, `ANSIBLE_CONFIG`, `resolve_tfvars` repointed under `plans/shared/` |
| `provision.sh`, `deprovision.sh`, `verify.sh` | source `../../shared/scripts/lib.sh` relatively — `REPO_ROOT` does not exist until lib is sourced; `shellcheck source=` directives updated |
| `provision.sh` | calls `$REPO_ROOT/plans/shared/ansible/site.yml` and `$REPO_ROOT/plans/provision-cluster/scripts/verify.sh` |
| Makefile | `provision`/`deprovision` targets; `lint` globs `plans/*/scripts/*.sh` and `plans/shared/scripts/*.sh`, `terraform -chdir=plans/shared/terraform`, `cd plans/shared/ansible` |
| `.gitignore` | `plans/shared/terraform/.generated/`, `plans/shared/ansible/inventory/*.ini` |

Moves use `git mv` so history follows. Untracked `.terraform/` and
`terraform.tfstate.d/` move alongside, so `terraform init` need not re-run from
scratch. There are no live clusters at the time of this change
(`terraform.tfstate.d` is empty, `multipass list` reports no instances), so the
state move carries no risk to running infrastructure.

### Documentation

Updated as part of this change, not after it:

- **`README.md`** — repository-layout block, every `provisioning/scripts/*.sh`
  and `clusters/` reference, the linked-repository paragraph (two forge-lab
  specs now, not three), all hello-world mentions, and a pointer to the plan
  contract.
- **`CLAUDE.md`** — layout map rewritten to the new tree, command paths, and the
  six-rule plan contract added so future plans follow it.
- **`plans/README.md`** — new; the contract in-tree, where someone adding a plan
  will see it.
- **`docs/superpowers/specs/*` (older)** — left untouched. They are dated
  records of past decisions, not live documentation.

## Verification

`make lint` is the gate: shellcheck over `infra/**` and `plans/**` scripts,
`terraform fmt -check` + `validate`, `ansible-lint`, and `mvn test` (which
offline-validates all three remaining specs).

Additionally, `grep -rn 'provisioning/\|HelloWorld' -- ':!docs'` over tracked
files must return nothing: the only surviving mentions belong to the dated
design documents under `docs/`.

A live `make provision CLUSTER=lab1` smoke run is out of scope for this change.

## Non-goals

- No behavior change. Every script does exactly what it did before; only its
  path and its `lib.sh` source path change.
- No change to `infra/` contents.
- No Terraform or Ansible refactoring.
- No new plans.

## Follow-up

After merging, run `make specs-publish` to re-publish the plans with their new
script paths, then delete the stale `FORGE-HELLO` plan in the Bamboo UI.

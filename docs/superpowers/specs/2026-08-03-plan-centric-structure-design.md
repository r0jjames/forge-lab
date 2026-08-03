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

The plan root is `bamboo-specs/src/main/java/lab/` (`$(LAB)` in the Makefile).
Each directory under it is one Bamboo plan, holding its spec and every piece of
code that plan executes.

```
forge-lab/
├── bamboo-specs/src/main/java/lab/   # PLAN ROOT
│   ├── README.md                     # the structure contract
│   ├── shared/                       # used by 2+ plans
│   │   ├── SpecConstants.java        # BAMBOO_URL, REPO_NAME
│   │   ├── scripts/lib.sh
│   │   ├── terraform/                # was provisioning/terraform/
│   │   ├── ansible/                  # was provisioning/ansible/
│   │   └── clusters/                 # was clusters/
│   ├── provisioncluster/
│   │   ├── ProvisionClusterSpec.java
│   │   └── scripts/{provision.sh,verify.sh}
│   ├── deprovisioncluster/
│   │   ├── DeprovisionClusterSpec.java
│   │   └── scripts/deprovision.sh
│   └── agentimage/
│       ├── BuildAgentImageSpec.java
│       └── README.md                 # build script lives in the bamboo-agent repo
├── infra/                            # lab operations, NOT plan-executed (unchanged)
│   ├── helm/                         # Bamboo + Postgres chart values
│   ├── agent/                        # host-local agent install/run
│   └── scripts/                      # license fetch/relicense
├── docs/
├── Makefile
├── README.md
└── CLAUDE.md
```

`provisioning/` and the top-level `clusters/` directory cease to exist.

Shell, Terraform, and Ansible sit inside a Maven source root. That is the
deliberate cost of the design goal: one lookup gets you everything a plan runs,
with no directory anywhere else to keep in sync. Maven compiles `.java` and
ignores every other file, so the build is unaffected.

### The contract for every new plan

Recorded in `lab/README.md` and in CLAUDE.md so it is enforced on future work:

1. A plan gets `lab/<planid>/` — lowercase, no dashes, because it is also a
   Java package name. One directory per spec.
2. Its spec is `lab/<planid>/<Name>Spec.java` (package `lab.<planid>`), with one
   test at `src/test/java/lab/<planid>/<Name>SpecTest.java`.
3. The code it executes goes in `lab/<planid>/scripts/`. No other plan
   references it.
4. Anything reused by 2+ plans goes in `lab/shared/`. A plan never reaches into
   another plan's directory.
5. Anything not executed by a plan (helm values, license fetch, host-agent
   install/run) belongs in `infra/`, not `lab/`.
6. The spec class is registered in the Makefile's `SPEC_CLASSES`.
7. `ScriptTask` bodies are repo-relative from Bamboo's checkout root:
   `bamboo-specs/src/main/java/lab/<planid>/scripts/<script>.sh`.

### Why the plan root is the Java package tree

Maven requires sources under `src/main/java/<package-path>`, so the spec cannot
move. The alternatives for co-locating scripts with their spec were a
`build-helper-maven-plugin` source root per plan, or a Maven module per plan —
both charge a pom edit for every new plan. Moving the scripts into the package
tree instead costs nothing at build time and needs no configuration at all.

The plan directory *is* the Java package, so plan ids are lowercase with no
dashes: `lab/provisioncluster/` ↔ `lab.provisioncluster`.

### Why terraform and ansible live in `lab/shared/`

Provision and deprovision are two separate plans, but they operate on the same
Terraform state directory, the same Ansible tree, and the same per-cluster
tfvars. Duplicating those would create two sources of truth for one cluster's
state; assigning them to `provisioncluster/` would make `deprovisioncluster/` reach
across a plan boundary. `lab/shared/` is the correct home under rule 4.

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
| `ProvisionClusterSpec` inlineBody | `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.sh` |
| `DeprovisionClusterSpec` inlineBody | `bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.sh` |
| `lib.sh` | `REPO_ROOT` dropped — nothing used it once every path hung off `SHARED_DIR`, which is now derived from the file's own location (`dirname/..`), immune to how deep the tree is |
| `provision.sh`, `deprovision.sh`, `verify.sh` | source `../../shared/scripts/lib.sh` relatively — `SHARED_DIR` does not exist until lib is sourced; `shellcheck source=` directives updated |
| `provision.sh` | calls `$SHARED_DIR/ansible/site.yml` and its sibling `verify.sh` via `dirname` |
| Makefile | new `LAB := bamboo-specs/src/main/java/lab` keeps the long path in one place; `provision`/`deprovision` targets and every `lint` path use `$(LAB)` |
| `.gitignore` | `bamboo-specs/src/main/java/lab/shared/terraform/.generated/`, `.../shared/ansible/inventory/*.ini` |

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
  plan contract added so future plans follow it.
- **`lab/README.md`** — new; the contract in-tree, at the plan root, where
  someone adding a plan will see it.
- **`docs/superpowers/specs/*` (older)** — left untouched. They are dated
  records of past decisions, not live documentation.

## Verification

`make lint` is the gate: shellcheck over `infra/**` and `$(LAB)/*/scripts/*.sh`,
`terraform fmt -check` + `validate`, `ansible-lint`, and `mvn test` (which
offline-validates all three remaining specs).

Additionally, `grep -rn 'provisioning/\|^plans/\|HelloWorld'` over tracked
files outside `docs/` must return nothing — the surviving mentions are the
before-and-after descriptions in this document.

Beyond `make lint`, a path smoke test: source `lib.sh` and run all three script
entrypoints from an unrelated working directory, confirming each resolves its
paths and fails with its own usage error rather than a missing-file error.

A live `make provision CLUSTER=lab1` smoke run is out of scope for this change.

## Non-goals

- No behavior change. Every script does exactly what it did before; only its
  path changes (the relative `lib.sh` source path happens to stay identical —
  both trees put plan scripts two levels below the plan root).
- No change to `infra/` contents.
- No Terraform or Ansible refactoring.
- No new plans.

## Follow-up

After merging, run `make specs-publish` to re-publish the plans with their new
script paths, then delete the stale `FORGE-HELLO` plan in the Bamboo UI.

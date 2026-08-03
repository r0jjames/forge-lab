# lab/ — one directory per Bamboo plan

Each directory here is one plan: its spec (Java) and every piece of code that
plan executes (shell, Terraform, Ansible), side by side. Nothing about a plan
lives anywhere else.

```
lab/
├── shared/                     # used by 2+ plans
│   ├── SpecConstants.java      # BAMBOO_URL, REPO_NAME
│   ├── scripts/lib.sh          # bash helpers every plan script sources
│   ├── terraform/              # VM lifecycle (modules/multipass = backend seam)
│   ├── ansible/                # site.yml + roles, generated inventory (gitignored)
│   └── clusters/               # per-cluster tfvars (+ defaults.tfvars)
├── provisioncluster/           # FORGE-PROV
│   ├── ProvisionClusterSpec.java
│   └── scripts/{provision.sh,verify.sh}
├── deprovisioncluster/         # FORGE-DEPROV
│   ├── DeprovisionClusterSpec.java
│   └── scripts/deprovision.sh
└── agentimage/                 # AGENT-BUILD
    ├── BuildAgentImageSpec.java
    └── README.md               # build script lives in the bamboo-agent repo
```

Yes, shell and Terraform live inside a Maven source root. That is deliberate:
one lookup gets you everything a plan runs. Maven compiles `.java` and ignores
the rest, so nothing here affects the build.

## Adding a plan

1. Create `lab/<planid>/` — lowercase, no dashes (it is a Java package name).
   One directory per plan spec.
2. Write the spec as `lab/<planid>/<Name>Spec.java`, package `lab.<planid>`.
   Add one test at `src/test/java/lab/<planid>/<Name>SpecTest.java` calling
   `EntityPropertiesBuilders.build(...)` — that is the offline validation
   `make lint` runs.
3. Put the code it executes in `lab/<planid>/scripts/`. No other plan
   references it.
4. Reused by 2+ plans? It goes in `lab/shared/`. A plan never reaches into
   another plan's directory.
5. Not executed by a plan — helm values, license fetch, host-agent install/run
   — it belongs in `infra/`, not here.
6. Register the spec class in the Makefile's `SPEC_CLASSES` so
   `make specs-publish` picks it up.

## Conventions

- Scripts take their inputs as positional arguments and run from any working
  directory. Source `lib.sh` by relative path
  (`"$(dirname "${BASH_SOURCE[0]}")/../../shared/scripts/lib.sh"`) and derive
  every other path from the `SHARED_DIR` it exports — Bamboo runs these from
  its own build directory, never from the repo root.
- A spec's `ScriptTask` body stays a one-liner calling a script in this tree.
  The logic lives in shell so it runs identically from a plain terminal
  (`make provision`) and from CI — the CI-agnostic core.
- Script task paths are repo-relative from Bamboo's checkout root, so they read
  `bamboo-specs/src/main/java/lab/<planid>/scripts/<script>.sh`.
- Scripts use bash strict mode and stay shellcheck-clean (`make lint`).

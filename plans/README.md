# plans/

Everything a Bamboo plan executes. One directory per plan, named after the
plan, holding all the code that plan runs.

```
plans/
├── shared/                     # used by 2+ plans
│   ├── scripts/lib.sh          # bash helpers every plan script sources
│   ├── terraform/              # VM lifecycle (modules/multipass is the backend seam)
│   ├── ansible/                # site.yml + roles, generated inventory (gitignored)
│   └── clusters/               # per-cluster tfvars (+ defaults.tfvars)
├── provision-cluster/          # FORGE-PROV
│   └── scripts/{provision.sh,verify.sh}
├── deprovision-cluster/        # FORGE-DEPROV
│   └── scripts/deprovision.sh
└── agent-image/                # AGENT-BUILD
    └── README.md               # build script lives in the bamboo-agent repo
```

## Adding a plan

1. Create `plans/<plan-id>/` — kebab-case, named after the plan. One directory
   per plan spec.
2. Put its scripts in `plans/<plan-id>/scripts/`. No other plan references them.
3. Write the spec as
   `bamboo-specs/src/main/java/lab/<planid>/<Name>Spec.java`, where `<planid>`
   is the plan-id with dashes stripped (`provision-cluster` ↔
   `lab.provisioncluster`; Java packages cannot contain dashes). Add one test
   class beside it that calls `EntityPropertiesBuilders.build(...)`.
4. Reused by 2+ plans? It goes in `plans/shared/`. A plan never reaches into
   another plan's directory.
5. Not executed by a plan — helm values, license fetch, host-agent install/run
   — it belongs in `infra/`, not here.
6. Register the spec class in the Makefile's `SPEC_CLASSES` so
   `make specs-publish` picks it up.

## Conventions

- Scripts take their inputs as positional arguments and run from any working
  directory: source `lib.sh` by relative path
  (`"$(dirname "${BASH_SOURCE[0]}")/../../shared/scripts/lib.sh"`) and derive
  every other path from the `REPO_ROOT` / `SHARED_DIR` it exports. Bamboo runs
  them from its own build directory.
- Spec `ScriptTask` bodies stay one-liners that call a script in this tree —
  the logic lives in shell so it runs identically from a plain terminal
  (`make provision`) and from CI.
- Scripts use bash strict mode and stay shellcheck-clean (`make lint`).

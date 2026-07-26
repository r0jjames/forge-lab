# Migrate the bamboo-agent build plan into forge-lab

Date: 2026-07-26

## Goal

All Bamboo plans-as-code live in forge-lab. The bamboo-agent repo keeps only
what defines the agent image — Dockerfile, VERSION, kaniko template, build
script, Helm chart — and no longer carries a Bamboo Specs module.

## End state

```
forge-lab/bamboo-specs/src/main/java/lab/
  plans/  HelloWorldSpec, ProvisionClusterSpec, DeprovisionClusterSpec   (FORGE-*)
  agent/  BuildAgentImageSpec                                            (AGENT-BUILD)

bamboo-agent/bamboo-agent-deployment/
  Dockerfile  VERSION  .hadolint.yaml  kaniko/  scripts/build-image.sh  README.md
  (specs/ removed)
```

## Decisions

**Project key stays `AGENT`.** Only the source of the spec moves; the published
plan keeps project `AGENT` / plan key `AGENT-BUILD`, so republishing overwrites
in place and build history survives. Folding it into `FORGE` would have meant
deleting and re-creating the plan.

**Specs only — build mechanics stay put.** `build-image.sh` and the kaniko Job
template describe how *that* image is built (its Dockerfile, its VERSION) and
sit next to the sources they read. The plan checks the bamboo-agent repo out
anyway, so nothing is gained by splitting them across repos and much is lost:
the script would need a second checkout and a hand-passed context path.

**The Validate stage is dropped.** It ran `cd bamboo-agent-deployment/specs &&
mvn -q test`, i.e. it validated the spec — which now lives in forge-lab and is
gated by `make lint` before publish. The plan is a single `Build+Push` stage.
Linting the image sources instead (hadolint/shellcheck) would require adding
both tools to the agent image; not worth it now.

**Plan-local repository is kept.** `BuildAgentImageSpec` declares a plan-local
`GitRepository` for the public bamboo-agent URL rather than a linked
repository, so no one-time Administration step is needed. The forge-lab plans
continue to use the `forge-lab` linked repo.

**`BAMBOO_URL` is duplicated, deliberately.** The agent spec carries its own
constant instead of reaching across packages into `HelloWorldSpec`. Hoisting
the URL into a shared class would touch all four specs to de-duplicate one
string.

## Changes

forge-lab:

- add `lab/agent/BuildAgentImageSpec.java` (Validate stage removed) and its
  offline-validation test
- `Makefile`: `SPEC_CLASSES += lab.agent.BuildAgentImageSpec` — `make
  specs-publish` publishes it, `make lint` already runs the new test
- `README.md` / `CLAUDE.md`: document the two spec packages and note that the
  agent plan needs no linked repository

bamboo-agent:

- delete `bamboo-agent-deployment/specs/` (pom, publish.sh, sources)
- `README.md`, `bamboo-agent-deployment/README.md`, `CLAUDE.md`,
  `docs/LEARNING-GUIDE.md`: publishing now happens from forge-lab via
  `make specs-publish`
- `.gitignore`: add `.idea/` and `*.iml`

## Verification

1. `mvn -f bamboo-specs/pom.xml -q test` in forge-lab — offline plan validation
   for all four specs.
2. `make specs-publish` with Bamboo up (`make ui`) — `AGENT-BUILD` shows a
   single `Build+Push` stage in the UI.
3. `git grep -n "specs/"` in bamboo-agent returns only prose pointing at
   forge-lab.

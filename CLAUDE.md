# forge-lab

Personal CI/CD lab: Bamboo on Rancher Desktop K8s + pipelines that provision
Multipass VM clusters (k8s or dcos). Design: docs/superpowers/specs/2026-07-23-forge-lab-design.md

## Commands

- `make up` / `make down` — CI stack (Bamboo + Postgres) on namespace `ci`
- `make ui` — port-forward Bamboo to http://localhost:8085
- `make relicense` — open license admin page (24h timebomb key ritual)
- `make agent-install` / `make agent-run` — host-local Bamboo agent
- `make provision CLUSTER=lab1 [TYPE=k8s|dcos]` / `make deprovision CLUSTER=lab1`
- `make lint` — shellcheck + terraform fmt/validate + ansible-lint + mvn test

## Layout map

- `provisioning/scripts/` — CI-agnostic core; Bamboo Specs and Makefile both call these
- `provisioning/terraform/modules/multipass/` — swappable VM backend boundary
- `clusters/<name>.tfvars` — per-cluster sizing; `defaults.tfvars` fallback
- `bamboo-specs/` — Java plans-as-code (mvn test validates offline)

## Conventions

- Commits: Roj's git identity ONLY — no Claude co-author/footers
- Scripts: bash strict mode, shellcheck-clean
- Never commit: license keys, generated inventories, tfstate
- Multipass units: "4G"/"20G", not Gi

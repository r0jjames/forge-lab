# forge-lab

Personal CI/CD lab: Bamboo on Rancher Desktop K8s + pipelines that provision
Multipass VM clusters (k8s or dcos). Design: docs/superpowers/specs/2026-07-23-forge-lab-design.md

## Commands

- `make bootstrap` — one-shot first-run: namespace, secrets, Postgres + Bamboo,
  and Bamboo's UNATTENDED setup (no wizard). Skips the manual license/admin
  steps and starts the remote-agent JMS broker automatically. Login `admin/admin`
- `make up` / `make down` — CI stack (Bamboo + Postgres) on namespace `ci`
  (`up` needs the secrets from `make bamboo-secrets`; `down` keeps PVCs)
- `make reset` — DESTRUCTIVE: wipe Bamboo PVCs + DB and reinstall; unattended
  setup re-runs on the fresh DB (re-licensed, broker up — no wizard). Use when
  boot fails with "Shared configuration ... does not exist" (stale DB vs empty
  shared-home)
- `make bamboo-secrets` — create/refresh the unattended-setup secrets: license
  (24h timebomb, runtime-only, never committed), sysadmin `admin/admin`, and the
  40-hex agent security token (created once, shared server<->agent)
- `make ui` — port-forward Bamboo to http://localhost:8085
- `make license` / `make relicense` — fetch the 24h timebomb key to clipboard
  (only needed for manual wizard / post-expiry; `bootstrap` handles it via secret)
- `make agent-install` / `make agent-run` — host-local Bamboo agent. Token is
  auto-read from the `bamboo-agent-token` secret; approve the agent once in
  Administration > Agents. Unattended setup already started the broker (54663)
- `make provision CLUSTER=lab1 [TYPE=k8s|dcos]` / `make deprovision CLUSTER=lab1`
- `make lint` — shellcheck + terraform fmt/validate + ansible-lint + mvn test

## Layout map

- `provisioning/scripts/` — CI-agnostic core; Bamboo Specs and Makefile both call these
- `provisioning/terraform/modules/multipass/` — swappable VM backend boundary
- `clusters/<name>.tfvars` — per-cluster sizing; `defaults.tfvars` fallback
- `bamboo-specs/` — Java plans-as-code (mvn test validates offline); `lab.plans`
  = forge-lab's own plans, `lab.agent` = the bamboo-agent image build plan
  (sources live in the bamboo-agent repo; only the pipeline lives here)

## Conventions

- Commits: Roj's git identity ONLY — no Claude co-author/footers
- Scripts: bash strict mode, shellcheck-clean
- Never commit: license keys, generated inventories, tfstate
- Multipass units: "4G"/"20G", not Gi

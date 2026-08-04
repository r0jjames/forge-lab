# forge-lab

Personal CI/CD lab: Bamboo on Rancher Desktop K8s + pipelines that provision
Multipass VM clusters (k8s or dcos). Design: docs/superpowers/specs/2026-07-23-forge-lab-design.md.
For how to actually use a provisioned cluster's addons (Keycloak, HDFS,
OpenSearch/Dashboards) and k9s day-to-day, see docs/using-cluster-addons.md.

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
  Administration > Agents. Unattended setup already started the broker (54663).
  `agent-run` seeds capability `agent.role=host`; the Provision/Deprovision
  plans *require* it so they never schedule on the containerized k8s agent
  (`agent.role=ci`), which has no terraform/multipass. It also points the
  cluster registry at `<this clone>/cluster_registered`, via both
  `FORGELAB_REGISTRY_DIR` and `~/.forgelab/registry_dir`
- `make provision CLUSTER=lab1 [TYPE=k8s|dcos] [ADDONS=keycloak,hdfs,opensearch]`
  / `make deprovision CLUSTER=lab1` — provision also writes
  `~/.forgelab/ssh_config.d/<cluster>.conf` (included from `~/.ssh/config`) so
  `ssh lab1-mgmt-1` / `ssh <node-ip>` work as `ubuntu` with the lab key;
  deprovision removes it. `ADDONS=` overrides the cluster's tfvars for this
  run; an empty value disables all addons. Provision also writes
  `cluster_registered/<cluster>_cluster_info.yml` (tracked, uncommitted) as its
  last step; deprovision deletes it
- `make addons CLUSTER=lab1 [TYPE=k8s] [ADDONS=hdfs]` — re-run the install stage
  only, against an existing cluster's inventory. Use it to iterate on an ansible
  role without a full rebuild
- `make lint` — pytest + terraform fmt/validate + ansible-lint + mvn test
  (pytest is a host tool like shellcheck was: `uv tool install pytest`)

## Layout map

Plan root is `bamboo-specs/src/main/java/lab/` (`$(LAB)` in the Makefile) —
one directory per Bamboo plan, holding its spec AND the code it runs:

- `lab/<planid>/` — `<Name>Spec.java` + `scripts/` for that plan alone;
  the scripts are the CI-agnostic core, called by both the spec and the Makefile
- `lab/shared/` — used by 2+ plans: `SpecConstants.java`,
  `python/forgelab/` (the lab's one library, stdlib only; `credentials.py` writes
  `~/.forgelab/<cluster>-credentials.yml`, referenced by the registry, never inlined),
  `terraform/` (`modules/multipass/` = swappable VM backend boundary),
  `ansible/`, `clusters/<name>.tfvars` (sizing; `defaults.tfvars` fallback)
- `infra/` — lab operations NOT run by any plan: `helm/` chart values,
  `agent/` host-agent install+run, `scripts/` license fetch
- `cluster_registered/` — generated output, tracked, owned by PROV/DEPROV:
  `<cluster>_cluster_info.yml` (IPs, sizing, ssh hints, installed components)
  written by provision after verify passes, deleted by deprovision. The
  pipeline never commits it — you do. Location resolves
  `$FORGELAB_REGISTRY_DIR` → `~/.forgelab/registry_dir` (one line, seeded by
  `agent-run`) → this checkout. Bamboo gives each plan its own throwaway
  checkout, so a repo-relative path strands PROV's file where DEPROV can never
  delete it; the pointer file is read per run, so a long-running agent picks up
  a change without a restart
- Python and Terraform inside a Maven source root is deliberate — one lookup
  per plan. Maven compiles `.java` and ignores the rest.
- `infra/` may import `forgelab` and nothing else under `lab/`; the dependency
  direction is one-way and no plan reaches into another plan's directory
- Python tests live in `bamboo-specs/src/test/python/`

## Adding a plan

Full contract in `bamboo-specs/src/main/java/lab/README.md`. In short:

1. `lab/<planid>/` — lowercase, no dashes (it is a Java package name)
2. Spec `lab/<planid>/<Name>Spec.java` + one offline-validation test at
   `src/test/java/lab/<planid>/<Name>SpecTest.java`
3. Its scripts in `lab/<planid>/scripts/`; nothing outside that plan uses them
4. Shared by 2+ plans → `lab/shared/`; never reach into another plan's dir
5. Not plan-executed → `infra/`, not `lab/`
6. Register the class in the Makefile's `SPEC_CLASSES`
7. `ScriptTask` bodies are repo-relative from Bamboo's checkout root:
   `bamboo-specs/src/main/java/lab/<planid>/scripts/<script>.py`

## Conventions

- Commits: Roj's git identity ONLY — no Claude co-author/footers
- Scripts are Python 3, standard library only (the host agent has no venv).
  Entrypoint filenames use underscores so tests can import them; wrap
  `main(argv)` in `proc.main`; parsing/rendering stays pure, shelling out goes
  through `proc.run`
- Never commit: license keys, generated inventories, tfstate
- Multipass units: "4G"/"20G", not Gi
- Terraform applies run `-parallelism=1` (`terraform.apply_retry`): concurrent
  `multipass launch` races give every VM in the batch the same MAC, hence one
  shared DHCP lease. Do not drop the flag
- Cluster addons are opt-in per cluster: `addons = "keycloak,hdfs,opensearch"` in
  the cluster's tfvars, overridable by the `addons` Bamboo plan variable. The
  list gates the ansible roles AND zeroes the VM roles of disabled addons, so
  sizing and enablement cannot disagree. k9s is not an addon — it ships with the
  k8s role. Addon secrets live in `~/.forgelab/<cluster>-credentials.yml` (0600)
  and never in `cluster_registered/`

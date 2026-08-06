# forge-lab

Personal CI/CD lab: Bamboo on Rancher Desktop K8s + pipelines that provision
Multipass VM clusters (k8s or dcos). Design: docs/superpowers/specs/2026-07-23-forge-lab-design.md.
For how to actually use a provisioned cluster's addons (Keycloak, HDFS,
OpenSearch/Dashboards) and k9s day-to-day, see docs/using-cluster-addons.md.
For running the Provision plan itself — plan variable values, expected VM
counts per addon set, and the per-addon health checks — see
docs/provision-usage.md.

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
  deprovision removes it. `TYPE=`/`ADDONS=` override the cluster's tfvars for
  this run; empty or left at the plan-variable placeholder means "not set" and
  the tfvars wins. `ADDONS=none` installs no addons at all and cannot be
  combined with a real name. Provision also writes
  `cluster_registered/<cluster>_cluster_info.yml` (tracked, uncommitted) as its
  last step; deprovision deletes it
- `make addons CLUSTER=lab1 [TYPE=k8s] [ADDONS=hdfs]` — re-run the install stage
  only, against an existing cluster's inventory. Use it to iterate on an ansible
  role without a full rebuild
- `make lint` — pytest + terraform fmt/validate + ansible-lint + mvn test
  (pytest is a host tool like shellcheck was: `uv tool install pytest`)
- `make hooks-install` — one-time: `git config core.hooksPath infra/githooks`,
  so a push to `main` republishes every plan. The hook skips quietly when
  Bamboo is unreachable and never blocks a push
- `make specs-publish` — publish every plan now. Needs `make ui` running and
  a Bamboo PAT in `~/.forgelab/bamboo_pat` (chmod 600); `FORGELAB_BAMBOO_PAT`
  overrides it for one run. `bamboo-specs/.credentials` is generated from it,
  never hand-maintained

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
6. Nothing to register — the publisher discovers `lab/*/*Spec.java` and
   derives the class from the path; your spec needs a `main()` calling
   `BambooServer.publish`
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
- A VM's role is its name: `<cluster>-<role>-<n>`, and `<role>` is also the
  ansible group and the `<role>_cpu`/`_mem`/`_disk` prefix in the tfvars, which
  is how `registry.nodes_from` fills in `cluster_registered/`. Rename a role and
  all four move together. hdfs owns two — `namenode` (metadata only, no
  DataNode) and `datanode` — but only `datanode_count` is configurable:
  terraform derives one NameNode from it, since non-HA HDFS has exactly one
- Plan variables are validated in `forgelab/planvars.py` and nowhere else. Their
  Bamboo defaults are placeholders documenting the legal values (Bamboo has no
  hint field), so each default is deliberately not a legal value and means "no
  override". The strings are mirrored in `ProvisionClusterSpec.java` and pinned
  by `ProvisionClusterSpecTest`; change both or neither
- PROV and DEPROV each open with a `Validate` stage carrying NO `agent.role`
  requirement, so a bad variable fails in seconds on any agent instead of
  queueing behind the host agent. Entrypoints call `planvars` first as well, so
  `make provision` fails identically
- Plans are published two ways and both call
  `lab/specspublish/scripts/publish_specs.py`: the `pre-push` hook on `main`,
  and the FORGE-SPECS plan polling `main` every 3 minutes. There is no list of
  spec classes anywhere — the publisher globs `lab/*/*Spec.java`, so adding a
  plan directory is the whole registration step

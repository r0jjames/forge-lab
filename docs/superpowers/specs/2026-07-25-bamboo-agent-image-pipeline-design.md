# Design: bamboo-agent build pipeline + k8s remote agent

Date: 2026-07-25
Status: Approved (design)
Related: [forge-lab design](2026-07-23-forge-lab-design.md)

## Purpose

Stand up a **containerized Bamboo remote agent** for CI / image-build work,
built by a Bamboo pipeline and deployed into the local Kubernetes Bamboo via
Helm. It lives in its own sibling repository, `~/Dev/projects/bamboo-agent`,
and **coexists** with the existing host-local agent — which keeps ownership of
the multipass provisioning jobs (a container in k8s cannot drive the host
multipass daemon).

### Why a separate agent

- The host-local agent (`infra/agent/install-agent.sh`, `make agent-run`) is
  tied to the host: it drives `multipass` (host hypervisor), `terraform`, and
  `ansible`. That path stays.
- CI / image-build jobs (build a container image, run `mvn test`, lint) are
  container-friendly and belong on a reproducible, versioned, in-cluster agent.
- Running the CI agent **in-cluster** removes the broker/port-forward
  workarounds the host agent needs (see "In-cluster registration win").

## Scope

In scope:

- New sibling repo `bamboo-agent` with two modules.
- `bamboo-agent-deployment`: agent Dockerfile, capabilities, and a Bamboo Specs
  (Java) plan that builds and pushes the image.
- `bamboo-agent-helm`: Helm chart deploying the image as a remote agent.

Out of scope (this spec):

- Migrating existing forge-lab plans onto the new agent.
- Retiring the host-local agent. It stays; this is additive.
- Multipass / provisioning work on the containerized agent.

## Architecture

Two modules, two responsibilities:

```
bamboo-agent/                          # separate git repo, sibling of forge-lab
  README.md
  bamboo-agent-deployment/             # build the image
    Dockerfile
    capabilities/bamboo-capabilities.properties   # agent.role=ci
    kaniko/kaniko-job.yaml.tmpl
    scripts/build-image.sh
    specs/                             # maven bamboo-specs module (Java plans-as-code)
      pom.xml
      src/main/java/lab/agent/BuildAgentImageSpec.java
      src/test/java/lab/agent/BuildAgentImageSpecTest.java
  bamboo-agent-helm/                   # deploy the image as a remote agent
    Chart.yaml
    values.yaml
    templates/
      deployment.yaml
      serviceaccount.yaml
      rbac.yaml
      _helpers.tpl
    README.md
```

### Module 1 — `bamboo-agent-deployment`

**Dockerfile.** `FROM atlassian/bamboo-agent-base` (official). Layers only the
CI/build capability set:

- `kubectl` — to launch and observe kaniko Jobs.
- `git`, `jq`, `maven` (JDK comes from the base image).
- Explicitly **not** `terraform`, `ansible`, or `multipass` — those stay on the
  host agent.

Pin the base image to a specific tag/digest (matches forge-lab's version-pin
convention); do not float `:latest`.

**Agent capability.** The image ships a capability
`agent.role=ci` (via `capabilities/bamboo-capabilities.properties` merged into
the agent's `bamboo-capabilities.properties`). Plans that should run here add a
matching **requirement** (see "Coexistence guard").

**Build plan** (Bamboo Specs, Java — matches forge-lab's `bamboo-specs/`).
New project `AGENT`, plan "Build Agent Image":

- **Stage `Validate`**: `mvn test` the specs offline (same offline-validation
  gate as forge-lab), plus optional `hadolint` on the Dockerfile.
- **Stage `Build+Push`**: `scripts/build-image.sh`:
  1. Renders `kaniko/kaniko-job.yaml.tmpl` with `destination =
     docker.io/<user>/bamboo-agent:<git-sha>` and build context = the checked-out
     repo.
  2. `kubectl apply` the Job into ns `ci`.
  3. Waits for completion, tails the kaniko logs, fails the task on Job failure.

**Kaniko model.** Kaniko unpacks the build's base image over its own container
root filesystem, so it must run in a **throwaway pod**, never in-process on a
persistent agent. Each build therefore templates a fresh kaniko `Job`. The
kaniko Job mounts the Docker Hub push credential at
`/kaniko/.docker/config.json`.

**Bootstrapping (no chicken/egg).** Because the real build runs in a kaniko
pod, the *orchestrating* agent only needs `kubectl`. The **host agent already
has `kubectl`**, so it runs the first "Build Agent Image" build and produces the
first image before the containerized agent exists. After that, either agent can
orchestrate rebuilds.

### Module 2 — `bamboo-agent-helm`

Helm chart deploying the image as a remote agent in ns `ci`:

- **`Deployment`** (the agent is effectively stateless for this lab).
- **`ServiceAccount` + `Role` + `RoleBinding`**: allow the agent to
  `create`/`get`/`list`/`watch`/`delete` `Jobs` and `get`/`list` `pods` and
  `pods/log` in ns `ci` — the minimum to launch kaniko Jobs and read their logs.
- **Env / config**:
  - Server URL: `http://bamboo.ci.svc.cluster.local:8085` (in-cluster).
  - Security token: read from the **existing `bamboo-agent-token` secret** — the
    same token the server was configured with, so the agent registers as
    trusted.
  - Broker URL: the server's **natively advertised in-cluster FQDN**,
    `ssl://bamboo-0.bamboo.ci.svc.cluster.local:54663`.
- **`values.yaml`**: `image.repository = docker.io/<user>/bamboo-agent`,
  `image.tag` **pinned to a git-sha** (immutable), `imagePullPolicy: IfNotPresent`.

Install: `helm upgrade --install bamboo-agent ./bamboo-agent-helm -n ci`. The
agent then appears in *Administration > Agents* and is **approved once**, exactly
like the host agent.

## Data flow

**Build:**

```
commit in bamboo-agent repo
  -> Bamboo "Build Agent Image" plan (AGENT project)
  -> Validate stage (mvn test + hadolint)
  -> build-image.sh renders + applies kaniko Job in ns ci
  -> kaniko builds from Dockerfile, pushes docker.io/<user>/bamboo-agent:<git-sha>
```

**Deploy:**

```
bump image.tag in bamboo-agent-helm/values.yaml (a commit)
  -> helm upgrade --install bamboo-agent -n ci
  -> agent pod starts, reads bamboo-agent-token secret
  -> registers over in-cluster JMS (bamboo-0.bamboo.ci.svc:54663)
  -> approve once in Administration > Agents
  -> runs CI / image-build jobs (agent.role=ci)
```

## In-cluster registration win

The host agent needs, in `infra/helm/bamboo-values.yaml`, a localhost broker
override (`-Dbamboo.jms.broker.client.uri=ssl://localhost:54663...`),
`socket.verifyHostName=false`, and a live `make ui` port-forward of 54663 — all
because a host process cannot resolve the in-cluster broker FQDN.

The containerized agent runs **inside the cluster**, so it resolves and reaches
`bamboo-0.bamboo.ci.svc.cluster.local:54663` directly. No broker override, no
`verifyHostName=false`, no port-forward. This is a concrete simplification the
new agent gets for free.

## Coexistence guard (capability / requirement matching)

To keep jobs on the right agent:

- The containerized agent declares capability `agent.role=ci`.
- The "Build Agent Image" plan (and any future CI plan) adds a **requirement**
  `agent.role=ci` → schedules only on the containerized agent.
- **Follow-up in forge-lab (separate, non-blocking change):** the
  `ProvisionCluster` / `DeprovisionCluster` plans get a host-only requirement
  (e.g. an existing host capability such as `system.builder.command.multipass`)
  so they never land on the containerized agent, which has no multipass.

## Secrets

Runtime-only, never committed (forge-lab convention):

- **`bamboo-agent-token`** — already exists (created by `make bamboo-secrets`).
  Reused unchanged so the containerized agent presents the same trusted token.
- **`dockerhub-push`** — new. A `kubernetes.io/dockerconfigjson` secret holding a
  Docker Hub PAT, mounted into the kaniko Job at `/kaniko/.docker/config.json`.
  Created via a documented `kubectl create secret docker-registry ...` command
  (or a make target in the new repo). The PAT never lands in git.

## Testing

- **Specs**: `mvn test` in `bamboo-agent-deployment/specs/` — offline validation,
  same gate as forge-lab's `bamboo-specs`.
- **Helm**: `helm lint` + `helm template` render check.
- **Dockerfile / scripts**: `hadolint` (optional) and `shellcheck` on
  `build-image.sh`.
- **Smoke**: after `helm upgrade`, the agent shows online in
  *Administration > Agents*; a test CI job (and one "Build Agent Image" run)
  executes on it end to end, producing a pushed `:<git-sha>` image.

## Decisions

- **Registry**: Docker Hub (`docker.io/<user>/bamboo-agent`).
- **In-pod build tool**: kaniko, launched as a per-build throwaway `Job`.
- **Tagging**: immutable `:<git-sha>`; Helm pins the tag; bumping = a commit.
- **Pipeline format**: Bamboo Specs (Java), matching forge-lab.
- **Repo**: separate sibling repo `~/Dev/projects/bamboo-agent`.
- **Bamboo project**: new project `AGENT` (clean boundary for the separate repo).

## Open / follow-up

- Add the host-only requirement to forge-lab's provision/deprovision plans
  (separate change in forge-lab, tracked but not part of this spec).
- Decide whether the containerized agent later takes over any forge-lab CI-style
  plans (out of scope now).

# bamboo-agent Image Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a separate `bamboo-agent` repo that builds a containerized Bamboo CI/image-build agent (Docker Hub via kaniko) and deploys it into the local k8s Bamboo as a remote agent via Helm, coexisting with the host-local agent.

**Architecture:** Two modules in one repo. `bamboo-agent-deployment` holds the agent Dockerfile, its `agent.role=ci` capability, a kaniko-Job build script, and a Bamboo Specs (Java) plan that orchestrates the build. `bamboo-agent-helm` deploys the pushed image as an in-cluster remote agent with an RBAC ServiceAccount that can launch kaniko Jobs. The agent registers over in-cluster JMS (no port-forward), reusing the existing `bamboo-agent-token` secret.

**Tech Stack:** Docker (`atlassian/bamboo-agent-base:12.1.8`), kaniko, Kubernetes (ns `ci`), Helm, Bamboo Specs Java (`bamboo-specs-parent:12.1.8`, JUnit), bash (strict mode, shellcheck-clean).

## Global Constraints

- Bamboo server version is **12.1.8** — agent base image tag and `bamboo-specs-parent` version MUST be `12.1.8`.
- Registry: **`docker.io/rojcarranza/bamboo-agent`**. Image tag is an **immutable git-sha**; never float `:latest` in Helm.
- Namespace: **`ci`**. Reuse existing secret **`bamboo-agent-token`** (key `security-token`).
- Remote repo: **`git@github.com:r0jjames/bamboo-agent.git`**. Local path: **`~/Dev/projects/bamboo-agent`**.
- Never commit secrets, PATs, license keys, or generated manifests with credentials.
- Scripts: bash strict mode (`set -euo pipefail`), shellcheck-clean.
- Commits: Roj's git identity ONLY — no Claude co-author/footers (forge-lab convention carries to the new repo).
- Kaniko runs ONLY as a throwaway `Job` — never baked in-process into the persistent agent.
- Multipass/terraform/ansible are NOT installed in this image — CI/build capabilities only.

---

### Task 1: Repo scaffold + remote

**Files:**
- Create: `~/Dev/projects/bamboo-agent/.gitignore`
- Create: `~/Dev/projects/bamboo-agent/README.md`

**Interfaces:**
- Produces: an initialized git repo on `main` with remote `origin = git@github.com:r0jjames/bamboo-agent.git`, first commit pushed.

- [ ] **Step 1: Create dir and init git**

```bash
mkdir -p ~/Dev/projects/bamboo-agent
cd ~/Dev/projects/bamboo-agent
git init -b main
git remote add origin git@github.com:r0jjames/bamboo-agent.git
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
# maven
target/
# rendered manifests (may embed values); regenerate from templates
*.rendered.yaml
# local env
.env
*.local
# os
.DS_Store
```

- [ ] **Step 3: Write `README.md`**

```markdown
# bamboo-agent

Containerized Bamboo remote agent for CI / image-build jobs, plus its build
pipeline and Helm deployment. Companion to the [forge-lab](https://github.com/r0jjames/forge-lab)
CI/CD lab; coexists with forge-lab's host-local agent (which keeps the
multipass provisioning jobs).

## Modules

- `bamboo-agent-deployment/` — agent Dockerfile, `agent.role=ci` capability,
  kaniko build script, and the Bamboo Specs (Java) plan that builds and pushes
  the image to `docker.io/rojcarranza/bamboo-agent:<git-sha>`.
- `bamboo-agent-helm/` — Helm chart deploying the image into the local k8s
  Bamboo (namespace `ci`) as a remote agent.

## Prerequisites

- forge-lab's Bamboo running in namespace `ci` (server version 12.1.8).
- Secret `bamboo-agent-token` present in `ci` (created by forge-lab's `make bamboo-secrets`).
- Secret `dockerhub-push` in `ci` — see `bamboo-agent-deployment/README.md`.

## Quick start

```bash
# 1. Build + push the image (host agent or any kubectl-capable shell)
bamboo-agent-deployment/scripts/build-image.sh

# 2. Deploy the agent, pinning the tag the build printed
helm upgrade --install bamboo-agent ./bamboo-agent-helm -n ci \
  --set image.tag=<git-sha>

# 3. Approve the agent once: Bamboo > Administration > Agents
```
```

- [ ] **Step 4: Commit and push**

```bash
cd ~/Dev/projects/bamboo-agent
git add .gitignore README.md
git commit -m "chore: scaffold bamboo-agent repo"
git push -u origin main
```

Expected: push succeeds, `main` tracks `origin/main`.

---

### Task 2: Agent Dockerfile + capability

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/Dockerfile`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/capabilities/bamboo-capabilities.properties`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/.hadolint.yaml`

**Interfaces:**
- Produces: an image buildable from this Dockerfile that runs the Bamboo agent and carries capability `agent.role=ci`, with `kubectl`, `git`, `jq`, `maven` on PATH.

- [ ] **Step 1: Write the capability file**

`capabilities/bamboo-capabilities.properties`:
```properties
# Custom capability so CI/image-build plans (requirement agent.role=ci) schedule
# on this containerized agent, and multipass/provisioning plans do not.
agent.role=ci
```

- [ ] **Step 2: Write `.hadolint.yaml`**

```yaml
# DL3008: pin apt versions — skipped; base image is already version-pinned and
# this is a lab agent, not a released artifact.
ignored:
  - DL3008
```

- [ ] **Step 3: Write the Dockerfile**

`Dockerfile`:
```dockerfile
# Agent version MUST match the Bamboo server (forge-lab runs 12.1.8).
FROM atlassian/bamboo-agent-base:12.1.8

USER root

# CI/build capabilities only. NO terraform/ansible/multipass — those stay on
# the host-local agent. kubectl is needed to launch and observe kaniko Jobs.
ARG KUBECTL_VERSION=v1.30.5
RUN apt-get update \
    && apt-get install -y --no-install-recommends git jq maven ca-certificates curl \
    && curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Merge the custom capability into the agent's capability file. The base image
# runs the agent as user "bamboo" with BAMBOO_AGENT_HOME=/var/atlassian/application-data/bamboo-agent.
COPY capabilities/bamboo-capabilities.properties /tmp/extra-capabilities.properties

USER bamboo
```

Note: the capability merge into the agent home is done at deploy time by the Helm chart (Task 6) via an init step, because the agent home is a runtime volume — see Task 6. The file is baked into the image at `/tmp/extra-capabilities.properties` for the chart to copy.

- [ ] **Step 4: Lint the Dockerfile**

Run: `cd ~/Dev/projects/bamboo-agent/bamboo-agent-deployment && hadolint Dockerfile`
Expected: no errors (DL3008 ignored via `.hadolint.yaml`). If `hadolint` is not installed, skip with a note; not a hard gate in this lab.

- [ ] **Step 5: Build to verify it assembles**

Run: `cd ~/Dev/projects/bamboo-agent/bamboo-agent-deployment && docker build -t bamboo-agent:test .`
Expected: build completes; `docker run --rm --entrypoint sh bamboo-agent:test -c 'kubectl version --client && jq --version && mvn -v && git --version'` prints versions.

- [ ] **Step 6: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-deployment/Dockerfile bamboo-agent-deployment/capabilities bamboo-agent-deployment/.hadolint.yaml
git commit -m "feat: agent image — bamboo-agent-base 12.1.8 + kubectl/git/jq/maven + agent.role=ci"
```

---

### Task 3: Kaniko build script + Job template

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/kaniko/kaniko-job.yaml.tmpl`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/scripts/build-image.sh`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/README.md`

**Interfaces:**
- Consumes: secret `dockerhub-push` (type `kubernetes.io/dockerconfigjson`) in ns `ci`; the Dockerfile from Task 2.
- Produces: `scripts/build-image.sh` that pushes `docker.io/rojcarranza/bamboo-agent:<git-sha>` and prints the tag on stdout as `IMAGE_TAG=<git-sha>`.

- [ ] **Step 1: Write the kaniko Job template**

`kaniko/kaniko-job.yaml.tmpl` (placeholders `__TAG__`, `__CONTEXT__` substituted by the script):
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: kaniko-build-__TAG__
  namespace: ci
  labels:
    app: kaniko-build
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: kaniko
          image: gcr.io/kaniko-project/executor:v1.23.2
          args:
            - "--context=__CONTEXT__"
            - "--dockerfile=bamboo-agent-deployment/Dockerfile"
            - "--destination=docker.io/rojcarranza/bamboo-agent:__TAG__"
          volumeMounts:
            - name: docker-config
              mountPath: /kaniko/.docker
      volumes:
        - name: docker-config
          secret:
            secretName: dockerhub-push
            items:
              - key: .dockerconfigjson
                path: config.json
```

- [ ] **Step 2: Write `build-image.sh`**

`scripts/build-image.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
# Build and push the bamboo-agent image via a throwaway kaniko Job in ns ci.
# Kaniko must run in its own pod (it unpacks the base image over the container
# root fs), so we template a Job per build, wait, and tail its logs. The
# orchestrating shell needs only kubectl — the host agent already has it.
NS="${NS:-ci}"
GIT_SHA="$(git rev-parse --short HEAD)"
CONTEXT="${KANIKO_CONTEXT:-git://github.com/r0jjames/bamboo-agent.git#refs/heads/main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPL="$SCRIPT_DIR/../kaniko/kaniko-job.yaml.tmpl"

command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
kubectl -n "$NS" get secret dockerhub-push >/dev/null 2>&1 \
  || { echo "secret 'dockerhub-push' missing in ns $NS — see bamboo-agent-deployment/README.md"; exit 1; }

JOB="kaniko-build-${GIT_SHA}"
# Idempotent: clear a prior Job of the same sha before re-applying.
kubectl -n "$NS" delete job "$JOB" --ignore-not-found

MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT
sed -e "s|__TAG__|${GIT_SHA}|g" -e "s|__CONTEXT__|${CONTEXT}|g" "$TMPL" > "$MANIFEST"
kubectl -n "$NS" apply -f "$MANIFEST"

echo "Waiting for $JOB to complete..."
# Wait for either success or failure, then surface logs regardless.
kubectl -n "$NS" wait --for=condition=complete "job/$JOB" --timeout=600s &
wait_ok=$!
kubectl -n "$NS" wait --for=condition=failed "job/$JOB" --timeout=600s && job_failed=1 || job_failed=0 &
wait "$wait_ok" 2>/dev/null || true

kubectl -n "$NS" logs "job/$JOB" || true

if kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.succeeded}' | grep -q 1; then
  echo "IMAGE_TAG=${GIT_SHA}"
  echo "Pushed docker.io/rojcarranza/bamboo-agent:${GIT_SHA}"
else
  echo "kaniko build failed"; exit 1
fi
```

- [ ] **Step 3: shellcheck the script**

Run: `shellcheck ~/Dev/projects/bamboo-agent/bamboo-agent-deployment/scripts/build-image.sh`
Expected: clean (no warnings). Fix any SC findings inline.

- [ ] **Step 4: Write `bamboo-agent-deployment/README.md`**

```markdown
# bamboo-agent-deployment

Builds the CI/image-build agent image and pushes it to
`docker.io/rojcarranza/bamboo-agent:<git-sha>` via a kaniko Job.

## One-time: Docker Hub push secret

Create a Docker Hub PAT (Account Settings > Security), then:

```bash
kubectl -n ci create secret docker-registry dockerhub-push \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=rojcarranza \
  --docker-password=<DOCKERHUB_PAT>
```

The PAT never lands in git.

## Build

```bash
scripts/build-image.sh          # pushes :<git-sha>, prints IMAGE_TAG=<sha>
```

The build runs entirely in a throwaway kaniko Job in namespace `ci`; the shell
that runs the script needs only `kubectl`.
```

- [ ] **Step 5: Make executable and commit**

```bash
cd ~/Dev/projects/bamboo-agent
chmod +x bamboo-agent-deployment/scripts/build-image.sh
git add bamboo-agent-deployment/kaniko bamboo-agent-deployment/scripts bamboo-agent-deployment/README.md
git commit -m "feat: kaniko build script + Job template + push-secret docs"
```

---

### Task 4: Bamboo Specs module — pom + failing test

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/specs/pom.xml`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/specs/src/test/java/lab/agent/BuildAgentImageSpecTest.java`

**Interfaces:**
- Produces: a maven module `bamboo-agent-specs` with `bamboo-specs`/`bamboo-specs-api` deps and a JUnit test asserting the plan is offline-valid.
- Consumes (next task): `BuildAgentImageSpec.plan()` returning `com.atlassian.bamboo.specs.api.builders.plan.Plan`.

- [ ] **Step 1: Write `pom.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>com.atlassian.bamboo</groupId>
    <artifactId>bamboo-specs-parent</artifactId>
    <version>12.1.8</version>
    <relativePath/>
  </parent>
  <groupId>lab</groupId>
  <artifactId>bamboo-agent-specs</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <packaging>jar</packaging>

  <dependencies>
    <dependency>
      <groupId>com.atlassian.bamboo</groupId>
      <artifactId>bamboo-specs-api</artifactId>
    </dependency>
    <dependency>
      <groupId>com.atlassian.bamboo</groupId>
      <artifactId>bamboo-specs</artifactId>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
```

- [ ] **Step 2: Write the failing test**

`src/test/java/lab/agent/BuildAgentImageSpecTest.java`:
```java
package lab.agent;

import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class BuildAgentImageSpecTest {

    @Test
    public void planIsOfflineValid() {
        Plan plan = new BuildAgentImageSpec().plan();
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(plan);
    }
}
```

- [ ] **Step 3: Run test to verify it fails to compile**

Run: `cd ~/Dev/projects/bamboo-agent/bamboo-agent-deployment/specs && mvn -q test`
Expected: FAIL — `cannot find symbol: class BuildAgentImageSpec`.

- [ ] **Step 4: Commit the failing scaffold**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-deployment/specs/pom.xml bamboo-agent-deployment/specs/src/test
git commit -m "test: offline-validity test for BuildAgentImageSpec (red)"
```

---

### Task 5: Bamboo Specs — BuildAgentImageSpec plan

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/specs/src/main/java/lab/agent/BuildAgentImageSpec.java`

**Interfaces:**
- Consumes: the test from Task 4.
- Produces: `BuildAgentImageSpec.plan()` — project `AGENT` "bamboo-agent", plan "Build Agent Image" key `BUILD`, with a Validate stage (`mvn -q test` in `specs/`) and a Build+Push stage (`scripts/build-image.sh`), plus requirement `agent.role=ci` and default-repo checkout.

- [ ] **Step 1: Write the plan**

`src/main/java/lab/agent/BuildAgentImageSpec.java`:
```java
package lab.agent;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;

@BambooSpec
public class BuildAgentImageSpec {

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("AGENT")).name("bamboo-agent"),
                "Build Agent Image", new BambooKey("BUILD"))
            .description("Build + push the containerized Bamboo CI agent via kaniko")
            .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
            .stages(
                new Stage("Validate").jobs(
                    new Job("Validate", new BambooKey("VAL"))
                        .requirements(new Requirement("agent.role").matchValue("ci").matchType(Requirement.MatchType.EQUALS))
                        .tasks(
                            new VcsCheckoutTask().description("checkout")
                                .checkoutItems(new CheckoutItem().defaultRepository()),
                            new ScriptTask().description("validate specs")
                                .inlineBody("cd bamboo-agent-deployment/specs && mvn -q test"))),
                new Stage("Build+Push").jobs(
                    new Job("BuildPush", new BambooKey("BP"))
                        .requirements(new Requirement("agent.role").matchValue("ci").matchType(Requirement.MatchType.EQUALS))
                        .tasks(
                            new VcsCheckoutTask().description("checkout")
                                .checkoutItems(new CheckoutItem().defaultRepository()),
                            new ScriptTask().description("kaniko build + push")
                                .inlineBody("bamboo-agent-deployment/scripts/build-image.sh"))));
    }

    public static void main(String[] args) {
        // Published via `mvn compile exec:java` against a running server; the
        // BambooServer URL is supplied at publish time (see repo README).
        throw new UnsupportedOperationException(
            "Publish with the forge-lab specs-publish pattern; plan() is unit-validated offline.");
    }
}
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd ~/Dev/projects/bamboo-agent/bamboo-agent-deployment/specs && mvn -q test`
Expected: PASS — `planIsOfflineValid` green.

- [ ] **Step 3: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-deployment/specs/src/main
git commit -m "feat: BuildAgentImageSpec — Validate + Build+Push stages, agent.role=ci"
```

---

### Task 6: Helm chart — scaffold + values + lint

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/Chart.yaml`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/values.yaml`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/templates/_helpers.tpl`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/.helmignore`

**Interfaces:**
- Produces: a lintable chart named `bamboo-agent` whose values expose `image.repository`, `image.tag`, `image.pullPolicy`, `bamboo.server`, `bamboo.brokerUrl`, `tokenSecret`, `rbac.namespace`.

- [ ] **Step 1: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: bamboo-agent
description: Containerized Bamboo CI/image-build remote agent for the forge-lab cluster
type: application
version: 0.1.0
appVersion: "12.1.8"
```

- [ ] **Step 2: Write `values.yaml`**

```yaml
image:
  repository: docker.io/rojcarranza/bamboo-agent
  # Immutable git-sha; set at install time with --set image.tag=<sha>.
  tag: ""
  pullPolicy: IfNotPresent

bamboo:
  # In-cluster service — no port-forward needed (agent runs inside the cluster).
  server: "http://bamboo.ci.svc.cluster.local:8085"
  # Server's natively advertised broker FQDN; reachable in-cluster, so no
  # localhost override / verifyHostName workaround (unlike the host agent).
  brokerUrl: "ssl://bamboo-0.bamboo.ci.svc.cluster.local:54663"

# Existing secret created by forge-lab's `make bamboo-secrets`.
tokenSecret:
  name: bamboo-agent-token
  key: security-token

rbac:
  # ns the agent may create kaniko Jobs in.
  namespace: ci

resources:
  requests:
    cpu: "500m"
    memory: 1Gi
  limits:
    cpu: "1"
    memory: 2Gi
```

- [ ] **Step 3: Write `.helmignore`**

```
.git
*.md
```

- [ ] **Step 4: Write `_helpers.tpl`**

```
{{- define "bamboo-agent.name" -}}
bamboo-agent
{{- end -}}

{{- define "bamboo-agent.labels" -}}
app.kubernetes.io/name: {{ include "bamboo-agent.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
```

- [ ] **Step 5: Lint (expected to warn about no templates yet is fine; chart is valid)**

Run: `helm lint ~/Dev/projects/bamboo-agent/bamboo-agent-helm`
Expected: `1 chart(s) linted, 0 chart(s) failed` (may note no templates rendering resources yet).

- [ ] **Step 6: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-helm/Chart.yaml bamboo-agent-helm/values.yaml bamboo-agent-helm/.helmignore bamboo-agent-helm/templates/_helpers.tpl
git commit -m "feat: helm chart scaffold + values"
```

---

### Task 7: Helm chart — RBAC (ServiceAccount + Role + RoleBinding)

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/templates/serviceaccount.yaml`
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/templates/rbac.yaml`

**Interfaces:**
- Consumes: `values.rbac.namespace`, `_helpers.tpl` labels.
- Produces: ServiceAccount `bamboo-agent` + Role/RoleBinding granting Job create/watch/delete and pod/log read in ns `ci`, consumed by the Deployment in Task 8 (`serviceAccountName: bamboo-agent`).

- [ ] **Step 1: Write `serviceaccount.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: bamboo-agent
  namespace: {{ .Values.rbac.namespace }}
  labels:
    {{- include "bamboo-agent.labels" . | nindent 4 }}
```

- [ ] **Step 2: Write `rbac.yaml`**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: bamboo-agent-kaniko
  namespace: {{ .Values.rbac.namespace }}
  labels:
    {{- include "bamboo-agent.labels" . | nindent 4 }}
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: bamboo-agent-kaniko
  namespace: {{ .Values.rbac.namespace }}
  labels:
    {{- include "bamboo-agent.labels" . | nindent 4 }}
subjects:
  - kind: ServiceAccount
    name: bamboo-agent
    namespace: {{ .Values.rbac.namespace }}
roleRef:
  kind: Role
  name: bamboo-agent-kaniko
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 3: Render to verify**

Run: `helm template bamboo-agent ~/Dev/projects/bamboo-agent/bamboo-agent-helm -n ci --set image.tag=test | grep -E "kind: (ServiceAccount|Role|RoleBinding)"`
Expected: prints `ServiceAccount`, `Role`, `RoleBinding`.

- [ ] **Step 4: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-helm/templates/serviceaccount.yaml bamboo-agent-helm/templates/rbac.yaml
git commit -m "feat: agent RBAC — ServiceAccount + Role/RoleBinding for kaniko Jobs"
```

---

### Task 8: Helm chart — agent Deployment

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/templates/deployment.yaml`

**Interfaces:**
- Consumes: `values.image.*`, `values.bamboo.*`, `values.tokenSecret.*`, `values.resources`, ServiceAccount `bamboo-agent` (Task 7), the image's `/tmp/extra-capabilities.properties` (Task 2).
- Produces: a running agent Deployment that registers over in-cluster JMS with capability `agent.role=ci`.

- [ ] **Step 1: Write `deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bamboo-agent
  namespace: {{ .Values.rbac.namespace }}
  labels:
    {{- include "bamboo-agent.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "bamboo-agent.name" . }}
  template:
    metadata:
      labels:
        {{- include "bamboo-agent.labels" . | nindent 8 }}
    spec:
      serviceAccountName: bamboo-agent
      initContainers:
        # Seed the agent's custom capability into its home before the agent
        # starts. The agent reads bamboo-capabilities.properties from
        # BAMBOO_AGENT_HOME on boot; append our agent.role=ci line.
        - name: seed-capabilities
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["sh", "-c"]
          args:
            - |
              mkdir -p "$BAMBOO_AGENT_HOME/bin"
              cat /tmp/extra-capabilities.properties >> "$BAMBOO_AGENT_HOME/bamboo-capabilities.properties"
          env:
            - name: BAMBOO_AGENT_HOME
              value: /var/atlassian/application-data/bamboo-agent
          volumeMounts:
            - name: agent-home
              mountPath: /var/atlassian/application-data/bamboo-agent
      containers:
        - name: bamboo-agent
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          env:
            # Base image entrypoint reads these to register the remote agent.
            - name: BAMBOO_SERVER
              value: {{ .Values.bamboo.server | quote }}
            - name: SECURITY_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.tokenSecret.name }}
                  key: {{ .Values.tokenSecret.key }}
            - name: BAMBOO_JMS_BROKER_CLIENT_URI
              value: {{ .Values.bamboo.brokerUrl | quote }}
            - name: BAMBOO_AGENT_HOME
              value: /var/atlassian/application-data/bamboo-agent
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            - name: agent-home
              mountPath: /var/atlassian/application-data/bamboo-agent
      volumes:
        - name: agent-home
          emptyDir: {}
```

- [ ] **Step 2: Guard against an unset tag**

Add at the very top of `deployment.yaml` (before `apiVersion`):
```yaml
{{- if not .Values.image.tag }}
{{- fail "image.tag is required — set it to the pushed git-sha (--set image.tag=<sha>)" }}
{{- end }}
```

- [ ] **Step 3: Render to verify**

Run: `helm template bamboo-agent ~/Dev/projects/bamboo-agent/bamboo-agent-helm -n ci --set image.tag=abc123 | grep -E "image:|SECURITY_TOKEN|serviceAccountName"`
Expected: shows the pinned image `:abc123`, the `SECURITY_TOKEN` secret ref, and `serviceAccountName: bamboo-agent`.

- [ ] **Step 4: Verify the tag guard fires**

Run: `helm template bamboo-agent ~/Dev/projects/bamboo-agent/bamboo-agent-helm -n ci`
Expected: FAIL with `image.tag is required`.

- [ ] **Step 5: helm lint**

Run: `helm lint ~/Dev/projects/bamboo-agent/bamboo-agent-helm --set image.tag=abc123`
Expected: `0 chart(s) failed`.

- [ ] **Step 6: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-helm/templates/deployment.yaml
git commit -m "feat: agent Deployment — in-cluster registration, token secret, capability seed"
```

---

### Task 9: Helm README + end-to-end smoke

**Files:**
- Create: `~/Dev/projects/bamboo-agent/bamboo-agent-helm/README.md`

**Interfaces:**
- Consumes: a pushed image tag from Task 3's build; a running forge-lab Bamboo in ns `ci`.
- Produces: documented install + verified online agent running a build.

- [ ] **Step 1: Write `bamboo-agent-helm/README.md`**

```markdown
# bamboo-agent-helm

Deploys the containerized Bamboo CI agent into the local k8s Bamboo (ns `ci`)
as a remote agent.

## Prerequisites

- forge-lab Bamboo running in ns `ci`; secret `bamboo-agent-token` present.
- An image pushed by `bamboo-agent-deployment/scripts/build-image.sh`
  (note the `IMAGE_TAG=<sha>` it prints).

## Install

```bash
helm upgrade --install bamboo-agent ./bamboo-agent-helm -n ci \
  --set image.tag=<git-sha>
```

Then approve the agent once: **Bamboo > Administration > Agents**.

## Why no broker workaround

The host-local agent needs a localhost broker override and a port-forward
because it runs off-cluster. This agent runs **in** the cluster, so it reaches
`bamboo-0.bamboo.ci.svc.cluster.local:54663` directly — no override needed.

## Upgrade to a new image

Rebuild (new sha), then re-run the install with the new `--set image.tag`.
```

- [ ] **Step 2: Build + push a real image**

Run: `~/Dev/projects/bamboo-agent/bamboo-agent-deployment/scripts/build-image.sh`
Expected: ends with `IMAGE_TAG=<sha>` and `Pushed docker.io/rojcarranza/bamboo-agent:<sha>`.
(Requires the `dockerhub-push` secret from Task 3's README.)

- [ ] **Step 3: Deploy**

Run: `helm upgrade --install bamboo-agent ~/Dev/projects/bamboo-agent/bamboo-agent-helm -n ci --set image.tag=<sha>`
Expected: release deployed; `kubectl -n ci get pod -l app.kubernetes.io/name=bamboo-agent` shows `Running`.

- [ ] **Step 4: Verify registration**

Run: `kubectl -n ci logs deploy/bamboo-agent | grep -i "agent.*registered\|capabilit"`
Expected: agent connects to the broker and reports capabilities. Then approve it in Administration > Agents; confirm `agent.role=ci` appears under the agent's capabilities.

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/projects/bamboo-agent
git add bamboo-agent-helm/README.md
git commit -m "docs: helm install + smoke runbook"
git push
```

---

## Notes for forge-lab (separate follow-up, not this repo)

- Add a host-only requirement (e.g. `system.builder.command.multipass`) to
  `ProvisionClusterSpec` / `DeprovisionClusterSpec` so provisioning never
  schedules on the containerized `agent.role=ci` agent. Small change, tracked
  in the design's "Open / follow-up".

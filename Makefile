SHELL := /bin/bash
CLUSTER ?=
TYPE ?=
SPEC_CLASSES := lab.plans.HelloWorldSpec lab.plans.ProvisionClusterSpec lab.plans.DeprovisionClusterSpec

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: setup
setup: ## One-time: namespace, db secret, ssh keypair, helm repos
	kubectl get ns ci >/dev/null 2>&1 || kubectl create ns ci
	kubectl -n ci get secret bamboo-db-creds >/dev/null 2>&1 || \
	  kubectl -n ci create secret generic bamboo-db-creds \
	    --from-literal=username=bamboo \
	    --from-literal=password="$$(openssl rand -hex 16)"
	[ -f ~/.forgelab/id_ed25519 ] || \
	  (mkdir -p ~/.forgelab && ssh-keygen -t ed25519 -N '' -f ~/.forgelab/id_ed25519)
	helm repo add atlassian-data-center https://atlassian.github.io/data-center-helm-charts >/dev/null
	helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null
	helm repo update >/dev/null

.PHONY: up
up: ## Install/upgrade Postgres + Bamboo into ns ci
	helm upgrade --install postgres bitnami/postgresql -n ci -f infra/helm/postgres-values.yaml --version 18.8.0
	helm upgrade --install bamboo atlassian-data-center/bamboo -n ci -f infra/helm/bamboo-values.yaml --version 2.0.14
	@echo "Run 'make ui' then open http://localhost:8085 (first boot: paste timebomb license in wizard)"

.PHONY: down
down: ## Uninstall CI stack (PVCs survive)
	helm uninstall bamboo -n ci || true
	helm uninstall postgres -n ci || true

.PHONY: status
status: ## Pods in ns ci
	kubectl -n ci get pods,pvc,svc

.PHONY: ui
ui: ## Port-forward Bamboo to localhost:8085
	kubectl -n ci port-forward svc/bamboo 8085:80

.PHONY: license
license: ## Fetch + copy the 24h Bamboo timebomb key (for the setup wizard)
	@key="$$(infra/scripts/get-license.sh)" || exit 1; \
	  printf '%s\n\n' "$$key"; \
	  if command -v pbcopy >/dev/null; then printf '%s' "$$key" | pbcopy; \
	    echo "(copied to clipboard — paste into the Bamboo setup wizard license field)"; \
	  fi

.PHONY: relicense
relicense: ## Fetch + copy the 24h key and open Bamboo license admin (after expiry)
	infra/scripts/relicense.sh

.PHONY: agent-install
agent-install: ## Install host-local Bamboo agent (needs AGENT_TOKEN)
	infra/agent/install-agent.sh

.PHONY: agent-run
agent-run: ## Run host-local Bamboo agent in console mode
	infra/agent/run-agent.sh

.PHONY: specs-publish
specs-publish: ## Publish all Bamboo Specs plans to the server
	@for c in $(SPEC_CLASSES); do \
	  echo "==> publishing $$c"; \
	  mvn -f bamboo-specs/pom.xml -q compile exec:java -Dexec.mainClass=$$c || exit 1; \
	done

.PHONY: provision
provision: ## Provision cluster: make provision CLUSTER=lab1 [TYPE=k8s|dcos]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	provisioning/scripts/provision.sh $(CLUSTER) $(TYPE)

.PHONY: deprovision
deprovision: ## Tear down cluster: make deprovision CLUSTER=lab1
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	provisioning/scripts/deprovision.sh $(CLUSTER)

.PHONY: lint
lint: ## All static checks
	shellcheck infra/scripts/*.sh infra/agent/*.sh provisioning/scripts/*.sh
	terraform -chdir=provisioning/terraform fmt -check -recursive
	terraform -chdir=provisioning/terraform init -backend=false -input=false >/dev/null
	terraform -chdir=provisioning/terraform validate
	cd provisioning/ansible && ansible-lint
	mvn -f bamboo-specs/pom.xml -q test

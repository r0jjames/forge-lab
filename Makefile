SHELL := /bin/bash
CLUSTER ?=
TYPE ?=

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  %-16s %s\n", $$1, $$2}'

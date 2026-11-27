# Strict shell. The flags go on SHELL because GNU Make 3.81 ignores .SHELLFLAGS.
SHELL := bash -eu -o pipefail
.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules --warn-undefined-variables
.DEFAULT_GOAL := help

UV ?= uv

.PHONY: help
help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.venv: pyproject.toml uv.lock
	$(UV) sync --locked --all-extras
	$(UV) run playwright install chromium
	touch $@

.PHONY: install
install: .venv ## Install the dependencies and Chromium for the browser tests

.PHONY: test
test: .venv ## Run the tests
	$(UV) run pytest

.PHONY: lint
lint: .venv ## Lint the code and check its formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: fmt
fmt: .venv ## Format the code
	$(UV) run ruff format .

.PHONY: check
check: lint test ## Run what CI runs

.PHONY: clean
clean: ## Remove what the targets made
	rm -rf .venv dist

# ─────────────────────────────────────────────────────────────────────────
# enten monorepo — cross-language build orchestrator.
#
# Conventions:
#   * `make` (no target) prints the help.
#   * All targets are phony and aggregate over the Python (uv) and
#     TypeScript (pnpm) workspaces.
#   * Each target documents the languages it touches in a header comment.
# ─────────────────────────────────────────────────────────────────────────

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# Use forward slashes everywhere — works on Linux and macOS equally.
RM       := rm -rf
MKDIR    := mkdir -p

# Workspace roots.
ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PY_DIR   := $(ROOT_DIR)/packages/python/enten
TS_DIRS  := $(wildcard $(ROOT_DIR)/packages/ts/*)

# Tooling discovery — fall back to PATH lookups for portability.
PNPM ?= $(shell command -v pnpm 2>/dev/null || echo pnpm)
UV   ?= $(shell command -v uv   2>/dev/null || echo uv)
RUFF := $(UV)x --from ruff==0.15.18 ruff
RUFF_PATHS ?= .
PYTEST_PATHS ?= tests packages/python/enten/tests
EVAL_CORPUS ?= tests/fixtures/prs/smoke
EVAL_REPORT ?= /tmp/enten-eval-report.json

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help (default target).
	@printf "\nenten monorepo — available targets:\n\n"
	@awk 'BEGIN {FS = ":.*##"; printf "  \033[36m%-14s\033[0m %s\n", "target", "description"} \
		/^[a-zA-Z0-9_.-]+:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n"

# ─── install ─────────────────────────────────────────────────────────────
.PHONY: install
install: ## [py+ts] Install every workspace's dependencies.
	@echo ">> uv sync (root + python workspace)"
	@cd $(ROOT_DIR) && $(UV) sync --project $(PY_DIR) --all-groups --extra test --locked
	@echo ">> pnpm install (root + ts workspace)"
	@cd $(ROOT_DIR) && $(PNPM) install --frozen-lockfile

# ─── test ────────────────────────────────────────────────────────────────
.PHONY: test
test: test-py test-ts ## [py+ts] Run every test suite.

.PHONY: test-py
test-py: ## [py] Run pytest across the Python workspace.
	@cd $(ROOT_DIR) && $(UV) run --project $(PY_DIR) --extra test pytest \
		--junitxml=/tmp/enten-pytest.xml $(PYTEST_PATHS)

.PHONY: test-ts
test-ts: ## [ts] Run vitest across every TypeScript package.
	@cd $(ROOT_DIR) && $(PNPM) -r --filter './packages/ts/*' test

# ─── lint / format ───────────────────────────────────────────────────────
.PHONY: lint
lint: lint-py ## [py] Run the declared Python linter.

.PHONY: lint-py
lint-py: ## [py] ruff check (configured in pyproject.toml).
	@cd $(ROOT_DIR) && $(RUFF) check $(RUFF_PATHS)

.PHONY: format
format: format-py ## [py] Auto-format the Python workspace.

.PHONY: format-py
format-py: ## [py] ruff format.
	@cd $(ROOT_DIR) && $(RUFF) format .

# ─── typecheck ──────────────────────────────────────────────────────────
.PHONY: typecheck typecheck-ts
typecheck: typecheck-ts ## [ts] Run the declared TypeScript compiler gate.

typecheck-ts: ## [ts] Type-check without emitting build output.
	@cd $(ROOT_DIR) && $(PNPM) -r --filter './packages/ts/*' typecheck

# ─── build / dev ─────────────────────────────────────────────────────────
.PHONY: build build-ts
build: build-ts ## [ts] Compile the TypeScript workspace.

build-ts: ## [ts] Emit the TypeScript distribution artifacts.
	@cd $(ROOT_DIR) && $(PNPM) -r --filter './packages/ts/*' build

.PHONY: dev
dev: ## [ts] Run every TS package in watch mode.
	@cd $(ROOT_DIR) && $(PNPM) -r --parallel --filter './packages/ts/*' dev

# ─── coverage ────────────────────────────────────────────────────────────
.PHONY: coverage
coverage: coverage-py coverage-ts ## [py+ts] Generate coverage for every workspace.

.PHONY: coverage-py
coverage-py: ## [py] pytest --cov.
	@cd $(ROOT_DIR) && ENTEN_COVERAGE_SCOPE=python bash scripts/check-coverage.sh

.PHONY: coverage-ts
coverage-ts: ## [ts] vitest --coverage.
	@cd $(ROOT_DIR) && ENTEN_COVERAGE_SCOPE=typescript bash scripts/check-coverage.sh

# ─── eval ────────────────────────────────────────────────────────────────
.PHONY: eval
eval: ## [py] Run the agent evaluation harness.
	@cd $(ROOT_DIR) && $(UV) run --project $(PY_DIR) enten eval \
		--corpus $(EVAL_CORPUS) \
		--report $(EVAL_REPORT) \
		--stub-llm
	@cd $(ROOT_DIR) && $(UV) run scripts/eval-threshold-check.py $(EVAL_REPORT)

# ─── release policy ─────────────────────────────────────────────────────
.PHONY: permissions security audit
permissions: ## Audit requested GitHub App permissions.
	@cd $(ROOT_DIR) && bash scripts/check-permissions.sh

security: ## Scan the release tree and history for secrets.
	@cd $(ROOT_DIR) && bash scripts/check-release-secrets.sh

audit: ## Fail on any published JavaScript dependency vulnerability.
	@cd $(ROOT_DIR) && $(PNPM) audit --audit-level=low

# ─── aggregate gate ─────────────────────────────────────────────────────
.PHONY: check
check: lint typecheck build coverage eval permissions security audit ## Run every release gate.

# ─── clean ───────────────────────────────────────────────────────────────
.PHONY: clean
clean: clean-py clean-ts ## Remove all build artifacts, caches, venvs.

.PHONY: clean-py
clean-py: ## [py] Remove .venv, __pycache__, .pytest_cache, .ruff_cache, dist.
	@find $(ROOT_DIR) -type d \( \
		-name '.venv' -o -name '__pycache__' -o -name '.pytest_cache' \
		-o -name '.ruff_cache' -o -name '.mypy_cache' -o -name '*.egg-info' \
		\) -prune -exec $(RM) {} +
	@find $(ROOT_DIR) -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

.PHONY: clean-ts
clean-ts: ## [ts] Remove node_modules, dist, coverage, .tsbuildinfo.
	@find $(TS_DIRS) -type d \( -name 'node_modules' -o -name 'dist' -o -name 'coverage' \) -prune -exec $(RM) {} +
	@find $(TS_DIRS) -type f -name '*.tsbuildinfo' -delete

# ─── doctor (convenience) ───────────────────────────────────────────────
.PHONY: doctor
doctor: ## Print tool versions and workspace layout for debugging.
	@echo "pnpm:  $$($(PNPM) --version 2>/dev/null || echo 'missing')"
	@echo "uv:    $$($(UV) --version 2>/dev/null || echo 'missing')"
	@echo "ts pkgs:"
	@for d in $(TS_DIRS); do echo "  $$(basename $$d)"; done
	@echo "py pkg: $$(basename $$(dirname $(PY_DIR)))"

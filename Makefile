# Ancora developer entrypoints. Cross-platform-ish: assumes `uv` and `pnpm`.
.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-py test-web build up down logs clean docs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install Python + web dependencies
	uv sync --all-packages
	cd web && pnpm install

lint: ## Lint Python (ruff) and web (eslint)
	uv run ruff check .
	cd web && pnpm run lint

format: ## Auto-format Python and web
	uv run ruff format .
	uv run ruff check --fix .
	cd web && pnpm run format

typecheck: ## Type-check Python (mypy) and web (tsc)
	uv run mypy
	cd web && pnpm run typecheck

test: test-py test-web ## Run all tests

test-py: ## Run Python tests
	uv run pytest

test-web: ## Run web smoke tests (Playwright)
	cd web && pnpm run test

build: ## Build web production bundle
	cd web && pnpm run build

up: ## Start the full local stack (Temporal, Postgres, Redis, API, web)
	docker compose -f deploy/docker/docker-compose.yml up --build

down: ## Stop the local stack
	docker compose -f deploy/docker/docker-compose.yml down

logs: ## Tail stack logs
	docker compose -f deploy/docker/docker-compose.yml logs -f

e2e: ## Run the Phase 1 end-to-end smoke check against a running stack
	uv run python scripts/e2e_phase1.py

docs: ## Serve the docs site locally
	uv run --with mkdocs-material mkdocs serve

clean: ## Remove caches and build artifacts
	rm -rf .ruff_cache .mypy_cache .pytest_cache web/.next web/node_modules

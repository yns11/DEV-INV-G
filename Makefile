# =============================================================================
# Campagnes Inventaire — developer entry points
# Run `make help` for the list.
# =============================================================================

SHELL := /bin/bash
PYTHON ?= python3
PROFILE ?= DEFAULT
TARGET ?= dev

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- Setup --------------------------------------------------------------------
.PHONY: install
install: ## Install backend and frontend dependencies
	$(PYTHON) -m pip install -r app/requirements.txt
	$(PYTHON) -m pip install pytest ruff mypy
	cd frontend && npm install

# --- Quality ------------------------------------------------------------------
.PHONY: test
test: ## Run the test suite (no database required)
	$(PYTHON) -m pytest tests/ -v

.PHONY: lint
lint: ## Lint the Python and TypeScript sources
	$(PYTHON) -m ruff check app tests jobs
	cd frontend && npx tsc --noEmit

.PHONY: format
format: ## Auto-format the Python sources
	$(PYTHON) -m ruff format app tests jobs
	$(PYTHON) -m ruff check --fix app tests jobs

.PHONY: check
check: lint test ## Lint then test

# --- Build --------------------------------------------------------------------
.PHONY: build-frontend
build-frontend: ## Build the SPA into app/static
	cd frontend && npm run build

.PHONY: build
build: build-frontend ## Build everything the app payload needs

# --- Local run ----------------------------------------------------------------
.PHONY: dev-api
dev-api: ## Run the API with reload (expects a local Postgres, see docs)
	cd app && INV_ENV=local $(PYTHON) -m uvicorn main:app --reload --port 8000

.PHONY: dev-ui
dev-ui: ## Run the Vite dev server (proxies /api to port 8000)
	cd frontend && npm run dev

.PHONY: run
run: build ## Serve the built SPA and the API from one process, like production
	cd app && INV_ENV=local DATABRICKS_APP_PORT=8000 $(PYTHON) main.py

# --- Databricks ---------------------------------------------------------------
.PHONY: uc
uc: ## Create the Unity Catalog schema, volume, tables and views
	@test -n "$(WAREHOUSE_ID)" || { echo "WAREHOUSE_ID=<id> requis"; exit 2; }
	$(PYTHON) scripts/apply_unity_catalog.py \
		--warehouse-id $(WAREHOUSE_ID) --profile $(PROFILE)

.PHONY: validate
validate: build ## Validate the asset bundle
	databricks bundle validate -t $(TARGET) --profile $(PROFILE)

.PHONY: deploy
deploy: build ## Deploy and start the app
	databricks apps deploy -t $(TARGET) --profile $(PROFILE)

.PHONY: logs
logs: ## Stream the deployed app's logs (OAuth auth required)
	databricks apps logs campagnes-inventaire --follow --profile $(PROFILE)

.PHONY: status
status: ## Show the deployed app's status and URL
	databricks apps get campagnes-inventaire --profile $(PROFILE) -o json

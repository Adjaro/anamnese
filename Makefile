# ============================================================================ #
# anamnese — NL2SQL sur MIMIC-IV demo
# `make help` liste les cibles. Toute cible est idempotente.
# ============================================================================ #
.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

UV        ?= uv
DBT       ?= $(UV) run --group dbt dbt
PY        ?= $(UV) run
DBT_DIR   := transform
WAREHOUSE := data/warehouse/anamnese.duckdb

.PHONY: help setup hooks data build docs eval serve api app test lint fmt sql-lint sql-fix check clean

# --------------------------------------------------------------------------- #
help: ## Affiche cette aide
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# Installation
# --------------------------------------------------------------------------- #
setup: ## Installe les dependances et les hooks git
	$(UV) sync --all-groups
	@test -f .env || { cp .env.example .env; echo ">> .env cree : renseigner MISTRAL_API_KEY"; }
	$(MAKE) hooks

hooks: ## Installe les hooks git (pre-commit, commit-msg, pre-push)
	$(PY) pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
	git config commit.template .gitmessage
	@echo ">> hooks git installes"

sql-lint: ## Lint des modeles dbt (sqlfluff, dialecte duckdb)
	$(PY) sqlfluff lint $(DBT_DIR)/models

sql-fix: ## Corrige automatiquement ce que sqlfluff sait corriger
	$(PY) sqlfluff fix --force $(DBT_DIR)/models

# --------------------------------------------------------------------------- #
# Donnees et transformation
# --------------------------------------------------------------------------- #
data: ## Telecharge MIMIC-IV demo 2.2 et inventorie les colonnes reelles
	$(PY) python scripts/download_mimic.py

build: ## Reconstruit le warehouse DuckDB (fichier temporaire + swap atomique)
	$(PY) python scripts/swap_warehouse.py

docs: ## Regenere le manifest dbt (contexte du LLM)
	cd $(DBT_DIR) && $(DBT) docs generate

# --------------------------------------------------------------------------- #
# Qualite
# --------------------------------------------------------------------------- #
lint: ## Lint Python + verification du formatage
	$(PY) ruff check .
	$(PY) ruff format --check .

fmt: ## Reformate le code
	$(PY) ruff check --fix .
	$(PY) ruff format .

test: ## Tests unitaires (hors LLM et hors warehouse)
	$(PY) pytest -m "not needs_llm and not needs_warehouse"

check: lint test ## Porte de sortie locale : ce que la CI verifiera

eval: ## Rejoue le jeu d'evaluation NL2SQL et ecrit un rapport
	$(PY) python eval/run_eval.py

# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
api: ## Lance l'API FastAPI seule (port 8000)
	$(PY) uvicorn anamnese.api:app --host 0.0.0.0 --port 8000 --reload

app: ## Lance l'interface Streamlit seule (port 8501)
	$(PY) streamlit run app/streamlit_app.py

serve: ## Lance l'API et l'interface via Docker Compose
	docker compose up --build

# --------------------------------------------------------------------------- #
clean: ## Supprime les artefacts de build (jamais data/raw)
	rm -rf $(DBT_DIR)/target $(DBT_DIR)/logs $(DBT_DIR)/dbt_packages
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	rm -f $(WAREHOUSE) $(WAREHOUSE).tmp $(WAREHOUSE).wal
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo ">> artefacts supprimes ; data/raw intact"

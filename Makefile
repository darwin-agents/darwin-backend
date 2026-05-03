# Darwin backend — uv-based workflows (see README.md).
# Override tools or ports: `make dev PORT=8080`  `make test PYTEST_ARGS="-v -k finance"`

UV ?= uv
PORT ?= 8000
HOST ?= 0.0.0.0
PYTEST_ARGS ?=

.DEFAULT_GOAL := help

.PHONY: help sync dev run prod worker beat test lint format typecheck check clean docker-build

help: ## Show this help
	@echo "Darwin backend (uv)"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

sync: ## Install/update deps from uv.lock (includes dev group by default)
	$(UV) sync

dev: ## FastAPI with hot reload (default PORT=8000; override: PORT=8080)
	$(UV) run uvicorn main:app --reload --port $(PORT)

run: dev ## Alias for dev (same server)

prod: ## Uvicorn without reload; set HOST and PORT (defaults 0.0.0.0:8000)
	$(UV) run uvicorn main:app --host $(HOST) --port $(PORT)

worker: ## Celery worker (webhooks, wake queues)
	$(UV) run celery -A workers.app worker --loglevel=info --concurrency=50 -P gevent

beat: ## Celery Beat (wake-ups, health checks)
	$(UV) run celery -A workers.app beat --loglevel=info

test: ## Run pytest (optional: PYTEST_ARGS="-v tests/test_finance.py")
	$(UV) run pytest $(PYTEST_ARGS)

lint: ## Ruff check
	$(UV) run ruff check .

format: ## Ruff format
	$(UV) run ruff format .

typecheck: ## Mypy (strict; see pyproject.toml)
	$(UV) run mypy .

check: lint typecheck test ## Lint, typecheck, and test (CI-style)

clean: ## Remove local caches (__pycache__, .pytest_cache, ruff, mypy; skips .venv/.git)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . \( -path './.venv' -o -path './.git' \) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

docker-build: ## Build API image (Dockerfile default CMD = uvicorn)
	docker build -t darwin-backend:local .

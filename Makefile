.PHONY: install lint format typecheck test run down restart ci clean audit help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install contributor dependencies, including legacy compatibility tests
	uv sync --extra legacy-traceforge
	cd frontend && npm ci

lint: ## Run linters (ruff + eslint)
	uv run ruff check backend/
	cd frontend && npm run lint

format: ## Auto-format backend code
	uv run ruff format backend/

typecheck: ## Run type checkers (mypy + tsc)
	uv run --extra legacy-traceforge mypy backend/
	cd frontend && npm run typecheck

test: ## Run backend and frontend tests with coverage
	uv run --extra legacy-traceforge pytest --cov=backend --cov-report=term-missing
	cd frontend && npm run test:coverage

run: ## Build frontend (if changed) and start server with remote access
	@if [ -f backend/web/index.html ] && \
	    [ "$$(find frontend/src -newer backend/web/index.html -print -quit 2>/dev/null)" = "" ] && \
	    [ frontend/package.json -ot backend/web/index.html ]; then \
	  echo "Frontend unchanged — skipping build"; \
	else \
	  cd frontend && npm run build; \
	fi
	uv run cpl up --remote

down: ## Gracefully pause sessions and stop the server
	uv run cpl down

restart: ## Restart the server (pause sessions, stop, then start)
	@if [ -f backend/web/index.html ] && \
	    [ "$$(find frontend/src -newer backend/web/index.html -print -quit 2>/dev/null)" = "" ] && \
	    [ frontend/package.json -ot backend/web/index.html ]; then \
	  echo "Frontend unchanged — skipping build"; \
	else \
	  cd frontend && npm run build; \
	fi
	uv run cpl restart --remote

ci: lint format typecheck test ## Run full CI pipeline

audit: ## Audit dependencies for known vulnerabilities
	uv run pip-audit --desc

clean: ## Remove build artifacts and caches
	rm -rf frontend/dist frontend/node_modules/.vite
	find backend -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf htmlcov .coverage

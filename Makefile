.PHONY: install lint format typecheck test run ci clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	uv sync
	cd frontend && npm ci

lint: ## Run linters (ruff + eslint)
	uv run ruff check backend/
	cd frontend && npm run lint

format: ## Auto-format backend code
	uv run ruff format backend/

typecheck: ## Run type checkers (mypy + tsc)
	uv run mypy backend/
	cd frontend && npm run typecheck

test: ## Run backend and frontend tests with coverage
	uv run pytest --cov=backend --cov-report=term-missing
	cd frontend && npm run test:coverage

run: ## Build frontend and start server with tunnel
	cd frontend && npm run build
	uv run cpl up --tunnel

ci: lint format typecheck test ## Run full CI pipeline

clean: ## Remove build artifacts and caches
	rm -rf frontend/dist frontend/node_modules/.vite
	find backend -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf htmlcov .coverage

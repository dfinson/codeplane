.PHONY: help install dev lint format test clean

PYTHON ?= python3
VENV := .venv

help:
	@echo "CodePlane Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install     Install package in editable mode"
	@echo "  make dev         Install with dev dependencies"
	@echo ""
	@echo "Development:"
	@echo "  make lint        Run ruff linter"
	@echo "  make format      Format code with ruff"
	@echo "  make typecheck   Run mypy type checker"
	@echo "  make test        Run tests with coverage"
	@echo "  make test-fast   Run tests without coverage"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean       Remove build artifacts"

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip

install: $(VENV)/bin/activate
	$(VENV)/bin/pip install -e .

dev: $(VENV)/bin/activate
	$(VENV)/bin/pip install -e ".[dev]"
	$(VENV)/bin/pre-commit install

lint:
	$(VENV)/bin/ruff check src tests

format:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

typecheck:
	$(VENV)/bin/mypy src

test:
	$(VENV)/bin/pytest

test-fast:
	$(VENV)/bin/pytest --no-cov -q

clean:
	rm -rf .venv
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf .coverage
	rm -rf htmlcov
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	rm -rf src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

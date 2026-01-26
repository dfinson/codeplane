# CodePlane

Local repository control plane for AI coding agents. Exposes repository operations via MCP tools over HTTP.

## Status

Design + scaffolding phase. See `SPEC.md` and `src/codeplane/*/DESIGN.md`.

## Dev Commands

```bash
make dev         # Install with dev deps
make lint        # Ruff
make typecheck   # Mypy
make test        # Pytest
```

## Key Choices

- CLI: Typer
- HTTP: Starlette + Uvicorn
- Index: Tantivy + SQLite + Symbol Graph
- Refactor: LSP-only (no regex)
- Logging: structlog

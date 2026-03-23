# Contributing to CodePlane

## Prerequisites

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Git

## Setup

```bash
git clone https://github.com/dfinson/codeplane.git
cd codeplane
make install
cp .env.sample .env          # optional — set CPL_DEVTUNNEL_PASSWORD for tunnel auth
```

## Development

```bash
# Start server (builds frontend, starts Dev Tunnels)
make run

# Backend-only work (skip frontend build)
uv run cpl up --dev
```

The backend serves the built frontend from `frontend/dist/` on `http://localhost:8080`.

## Code Quality

```bash
make lint        # ruff check + eslint
make format      # ruff format (backend)
make typecheck   # mypy + tsc
make test        # pytest + vitest with coverage
make ci          # all of the above
```

## Database Migrations

Alembic manages schema migrations:

```bash
uv run alembic upgrade head                            # apply migrations
uv run alembic revision --autogenerate -m "description" # create new
```

Migrations run automatically on `cpl up`.

## Project Structure

```
codeplane/
├── backend/
│   ├── main.py               # App factory + CLI (`cpl`)
│   ├── config.py             # Configuration loading
│   ├── api/                  # Thin route handlers
│   ├── mcp/                  # MCP orchestration server
│   ├── services/             # Business logic + agent adapter
│   ├── models/               # Domain, DB, and API schema models
│   ├── persistence/          # Repository-pattern DB access
│   └── tests/                # pytest (unit + integration)
├── frontend/
│   └── src/
│       ├── api/              # Generated types from OpenAPI
│       ├── components/       # React components
│       ├── hooks/            # Custom React hooks
│       ├── lib/              # Utilities
│       └── store/            # Zustand state management
├── alembic/                  # Database migrations
├── tools/                    # Developer scripts (dev_restart.py)
├── docs/                     # Documentation assets
├── Makefile                  # Build / run / test targets
├── .env.sample               # Environment variable template
├── SPEC.md                   # Full product specification
└── pyproject.toml            # Python project + tool config
```

## Conventions

- **API routes** are thin — validate input, delegate to a service, return the result
- **Database access** goes through repository classes in `persistence/` — never raw SQLAlchemy in services
- **API schemas** — Pydantic models are the single source of truth; frontend types are generated from OpenAPI
- **State management** — Zustand store is the single source of truth; components read via selectors
- **Agent SDK** is wrapped behind `AgentAdapterInterface` — never import SDK types outside the adapter
- **Strict typing** — mypy strict mode (backend), TypeScript strict mode (frontend)
- **Linting** — ruff (backend), ESLint (frontend)

## Commit Messages

Use [conventional commits](https://www.conventionalcommits.org/):

```
feat: add job creation endpoint
fix: handle worktree creation failure
docs: update spec section 14
test: add state machine transition tests
chore: update dependencies
```

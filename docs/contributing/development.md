# Development

This page covers the development workflow for contributors working from source.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | ≥ 3.11 | [python.org](https://www.python.org/) |
| Node.js | ≥ 20 | [nodejs.org](https://nodejs.org/) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Git | any | [git-scm.com](https://git-scm.com/) |

## Setup

```bash
git clone https://github.com/dfinson/codeplane.git
cd codeplane
make install          # uv sync + npm ci
cp .env.sample .env   # optional: set CPL_PASSWORD for remote auth
```

## Running the Server

```bash
# Full stack (builds frontend + starts with tunnel)
make run

# Backend only (skip frontend build)
uv run cpl up --dev

# Custom port
uv run cpl up --port 9090

# Cloudflare Tunnel
uv run cpl up --remote --provider cloudflare

# Stop server
uv run cpl down

# Restart (preserves sessions)
uv run cpl restart
```

The server runs on `http://localhost:8080` and serves the built frontend from `backend/web/`.

## Code Quality

```bash
make lint        # ruff check (backend) + eslint (frontend)
make format      # ruff format (backend)
make typecheck   # mypy (backend) + tsc (frontend)
make test        # pytest (70% coverage) + vitest
make ci          # all of the above in sequence
```

## Testing

### Backend

```bash
uv run pytest                              # all tests
uv run pytest backend/tests/unit/          # unit tests only
uv run pytest backend/tests/integration/   # integration tests only
uv run pytest -k "test_name"               # specific test
uv run pytest --cov=backend --cov-report=term-missing  # with coverage report
```

Backend tests require **70% coverage** to pass.

### Frontend

```bash
cd frontend
npm test                  # vitest in watch mode
npm run test:coverage     # with coverage report
```

### E2E

```bash
cd frontend
npx playwright install --with-deps chromium   # first time only
npx playwright test                           # run E2E tests
```

## Database Migrations

```bash
uv run alembic upgrade head                            # apply pending migrations
uv run alembic revision --autogenerate -m "description" # create new migration
```

Migrations run automatically on `cpl up`.

## Frontend Type Generation

When backend API schemas change:

```bash
# Start the server first
uv run cpl up --dev

# In another terminal
cd frontend
npm run generate:api    # generates src/api/schema.d.ts
```

The generated `schema.d.ts` is gitignored — import types via `src/api/types.ts` aliases instead.

## Developer Tools

### `tools/dev_restart.py`

Graceful server restart that preserves running jobs:

1. Pauses active jobs
2. Stops the server
3. Rebuilds the frontend
4. Restarts the server
5. Resumes paused jobs

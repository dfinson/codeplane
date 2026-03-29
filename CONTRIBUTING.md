# Contributing to CodePlane

This guide is for contributors working from source. If you just want to use CodePlane, see the [documentation](https://dfinson.github.io/codeplane).

## Prerequisites

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Git

## Setup from Source

```bash
git clone https://github.com/dfinson/codeplane.git
cd codeplane
make install                  # uv sync + npm ci
cp .env.sample .env           # optional — set CPL_DEVTUNNEL_PASSWORD for tunnel auth
```

### Build & Run

```bash
make run                      # build frontend + start server with remote access
# or manually:
uv run cpl up --dev           # backend-only (skip frontend build)
uv run cpl up                 # full stack (builds frontend on startup)
```

The server runs on `http://localhost:8080` and serves the built frontend from `backend/web/`.

## Code Quality

```bash
make lint        # ruff check + eslint
make format      # ruff format (backend)
make typecheck   # mypy + tsc
make test        # pytest (70% coverage threshold) + vitest
make ci          # all of the above
```

## Testing

### Backend Tests

```bash
uv run pytest                              # run all tests
uv run pytest backend/tests/unit/          # unit tests only
uv run pytest backend/tests/integration/   # integration tests only
uv run pytest -k "test_job_creation"       # run specific test by name
uv run pytest --cov=backend --cov-report=term-missing  # with coverage
```

### Frontend Tests

```bash
cd frontend
npm test                    # run vitest in watch mode
npm run test:coverage       # run with coverage report
```

### E2E Tests

```bash
cd frontend
npx playwright install --with-deps chromium  # first time only
npx playwright test                          # run E2E tests
```

## Database Migrations

Alembic manages schema migrations:

```bash
uv run alembic upgrade head                            # apply migrations
uv run alembic revision --autogenerate -m "description" # create new
```

Migrations run automatically on `cpl up`.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CPL_DEVTUNNEL_PASSWORD` | Password for Dev Tunnels remote access | _(auto-generated with --remote)_ |
| `CPL_CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Tunnel token | _(none)_ |
| `CPL_CLOUDFLARE_HOSTNAME` | Cloudflare public hostname | _(none)_ |
| `OTEL_EXPORTER_ENDPOINT` | OTLP exporter endpoint | _(none — local only)_ |

Additional configuration is managed via `config.yaml` and per-repo `.codeplane.yml` overrides. See [SPEC.md §10](SPEC.md) for details.

## Project Structure

```
codeplane/
├── backend/
│   ├── main.py                        # App factory + CLI (`cpl`)
│   ├── config.py                      # Configuration loading
│   ├── api/                           # Thin route handlers
│   │   ├── approvals.py               #   Approval endpoints
│   │   ├── artifacts.py               #   Artifact download/view
│   │   ├── events.py                  #   SSE event streaming
│   │   ├── health.py                  #   Health check
│   │   ├── jobs.py                    #   Job CRUD + lifecycle
│   │   ├── settings.py               #   Settings + repos + SDKs/models
│   │   ├── terminal.py               #   Terminal session management
│   │   ├── voice.py                   #   Voice transcription
│   │   └── workspace.py              #   Workspace file browsing
│   ├── mcp/                           # MCP orchestration server
│   │   └── server.py                  #   MCP tool definitions
│   ├── services/                      # Business logic
│   │   ├── agent_adapter.py           #   AgentAdapterInterface (ABC)
│   │   ├── adapter_registry.py        #   SDK adapter registry
│   │   ├── copilot_adapter.py         #   GitHub Copilot SDK adapter
│   │   ├── claude_adapter.py          #   Claude Code SDK adapter
│   │   ├── runtime_service.py         #   Job execution orchestration
│   │   ├── job_service.py             #   Job CRUD operations
│   │   ├── approval_service.py        #   Approval workflow
│   │   ├── artifact_service.py        #   Artifact management
│   │   ├── git_service.py             #   Git operations
│   │   ├── merge_service.py           #   Branch merging + PR creation
│   │   ├── diff_service.py            #   Diff generation
│   │   ├── event_bus.py               #   Internal event bus
│   │   ├── sse_manager.py             #   SSE event broadcasting
│   │   ├── terminal_service.py        #   Terminal session management
│   │   ├── voice_service.py           #   Whisper transcription
│   │   ├── telemetry.py               #   Token/cost metrics collection
│   │   ├── permission_policy.py       #   Permission mode enforcement
│   │   ├── platform_adapter.py        #   GitHub/Azure DevOps/GitLab
│   │   ├── naming_service.py          #   AI-powered job naming
│   │   ├── progress_tracking_service.py #  Agent progress tracking
│   │   ├── summarization_service.py   #   AI summarization
│   │   ├── retention_service.py       #   Job retention/cleanup
│   │   ├── setup_service.py           #   First-time setup + doctor
│   │   ├── tunnel_service.py          #   Dev Tunnels management
│   │   ├── tool_formatters.py         #   Tool call display formatting
│   │   ├── utility_session.py         #   Utility AI sessions
│   │   └── auth.py                    #   Authentication
│   ├── models/                        # Domain, DB, and API schemas
│   │   ├── domain.py                  #   Domain models (Job, Approval, etc.)
│   │   ├── db.py                      #   SQLAlchemy ORM models
│   │   ├── api_schemas.py             #   Pydantic request/response schemas
│   │   └── events.py                  #   Domain event definitions
│   ├── persistence/                   # Repository-pattern DB access
│   │   ├── database.py                #   Database engine + session factory
│   │   ├── repository.py              #   Base repository class
│   │   ├── job_repo.py                #   Job repository
│   │   ├── approval_repo.py           #   Approval repository
│   │   ├── artifact_repo.py           #   Artifact repository
│   │   ├── event_repo.py              #   Event repository
│   │   └── metrics_repo.py           #   Metrics repository
│   └── tests/                         # pytest (unit + integration)
│       ├── unit/                      #   Fast isolated tests
│       └── integration/               #   Tests with real DB + HTTP
├── frontend/
│   └── src/
│       ├── api/                       # API client + generated types
│       │   ├── client.ts              #   REST API client functions
│       │   ├── types.ts               #   Friendly type aliases
│       │   └── schema.d.ts            #   Generated from OpenAPI (gitignored)
│       ├── components/                # React components
│       ├── hooks/                     # Custom React hooks (SSE, mobile, etc.)
│       ├── lib/                       # Utilities
│       └── store/                     # Zustand state management
├── alembic/                           # Database migrations
├── tools/
│   └── dev_restart.py                 # Graceful server restart (preserves jobs)
├── docs/                              # Documentation + assets
├── Makefile                           # Build / run / test targets
├── .env.sample                        # Environment variable template
├── SPEC.md                            # Full product specification
└── pyproject.toml                     # Python project + tool config
```

## Conventions

- **API routes** are thin — validate input, delegate to a service, return the result
- **Database access** goes through repository classes in `persistence/` — never raw SQLAlchemy in services
- **API schemas** — Pydantic models in `api_schemas.py` are the single source of truth; frontend types are generated from OpenAPI
- **State management** — Zustand store is the single source of truth; components read via selectors
- **Agent SDKs** are wrapped behind `AgentAdapterInterface` — never import SDK types outside the adapter. Both Copilot and Claude adapters follow this pattern
- **Domain events** — All runtime activity is represented as domain events published to the internal event bus
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

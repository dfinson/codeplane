# Contributing to CodePlane

## Prerequisites

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Git

## Setup

```bash
# Clone the repo
git clone <repo-url>
cd codeplane

# Backend
uv sync

# Frontend
cd frontend
npm install
cd ..
```

## Development

```bash
# Start backend in dev mode
uv run cpl up --dev

# Start frontend dev server (separate terminal)
cd frontend
npm run dev
```

The backend runs on `http://localhost:8080`. The frontend dev server runs on `http://localhost:5173` and proxies API requests to the backend.

## Code Quality

### Backend

```bash
# Lint + format
uv run ruff check backend/
uv run ruff format backend/

# Type check
uv run mypy backend/

# Tests
uv run pytest
```

### Frontend

```bash
cd frontend

# Lint
npm run lint

# Type check
npm run typecheck

# Tests
npm run test

# Format
npm run format
```

## Project Structure

```
codeplane/
├── backend/                  # Python FastAPI backend
│   ├── main.py               # App factory + CLI
│   ├── config.py             # Configuration loading
│   ├── api/                  # Route handlers
│   ├── services/             # Business logic
│   ├── models/               # Domain models, DB models, schemas
│   ├── persistence/          # Repository pattern DB access
│   └── tests/                # pytest tests
├── frontend/                 # React + TypeScript frontend
│   ├── src/
│   │   ├── api/              # Generated types + API client
│   │   ├── components/       # React components
│   │   ├── store/            # Zustand state management
│   │   └── main.tsx          # Entry point
│   └── package.json
├── SPEC.md                   # Product specification
├── ROADMAP.md                # Implementation plan
└── pyproject.toml            # Python project config
```

## Conventions

- **Backend**: Python 3.11+, strict mypy, ruff for linting/formatting
- **Frontend**: TypeScript strict mode, ESLint + Prettier
- **API schemas**: Pydantic models are the single source of truth; TypeScript types are generated from OpenAPI
- **State management**: Zustand store is the single source of truth in the frontend
- **Database**: All access through repository classes, never raw SQLAlchemy in services
- **API routes**: Thin handlers that delegate to services — no orchestration logic in routes

## Commit Messages

Use conventional commits:

```
feat: add job creation endpoint
fix: handle worktree creation failure
docs: update roadmap phase 3
test: add state machine transition tests
chore: update dependencies
```

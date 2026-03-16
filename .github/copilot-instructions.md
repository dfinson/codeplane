# Copilot Instructions for CodePlane

## Project Overview

CodePlane is a control plane for running and supervising coding agents. It has a FastAPI (Python) backend and a React + TypeScript frontend.

## Architecture

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, SQLite, structured as services/repositories
- **Frontend**: React 18, TypeScript, Vite, Zustand for state management
- **Communication**: REST for commands/queries, SSE for live event streaming

## Key Conventions

### Backend

- API route handlers are **thin** — validate input, delegate to a service, return the result. No orchestration logic in routes.
- All database access goes through repository classes in `backend/persistence/`. Never use SQLAlchemy sessions directly in services.
- The agent SDK is wrapped behind `AgentAdapterInterface` in `backend/services/agent_adapter.py`. Never import SDK types outside the adapter.
- Git operations are isolated in `GitService` (`backend/services/git_service.py`).
- All runtime activity is represented as domain events (`backend/models/events.py`), published to the internal event bus.
- Job state transitions follow the explicit state machine in `SPEC.md` §12.2.
- Use `structlog` for logging with structured context fields.
- Pydantic models in `backend/models/api_schemas.py` are the single source of truth for the API contract.
- Response models use `CamelModel` base class for camelCase serialization.

### Frontend

- Application state lives in a Zustand store — components read from the store via selectors, never maintain local copies of job state.
- SSE events are processed through a single central event dispatcher that updates the store.
- All TypeScript domain types are generated from the backend's OpenAPI schema. Never hand-write types that duplicate `schema.d.ts`.
- Import types from `src/api/types.ts` (friendly aliases), never from `schema.d.ts` directly.
- Large lists (logs, transcripts) must use virtualized rendering (`@tanstack/react-virtual`).

### General

- Read `SPEC.md` for detailed requirements before implementing any feature.
- Read `ROADMAP.md` to understand the implementation phasing.
- Keep changes minimal and focused — don't refactor surrounding code or add speculative features.
- Use conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.

# Tower — Implementation Roadmap

Work is broken into phases. Each phase produces a usable increment. Later phases build on earlier ones.

---

## Phase 1: Foundation

> Backend skeleton, database, domain models, dev tooling.

- [ ] FastAPI app factory (`backend/main.py`) with health endpoint
- [ ] CLI entry point (`tower up`, `tower init`, `tower version`) via Click
- [ ] Global config loading and validation (`~/.tower/config.yaml`)
- [ ] SQLAlchemy models and SQLite schema (`jobs`, `events`, `approvals`, `artifacts`, `diff_snapshots`)
- [ ] Alembic migration setup and initial migration
- [ ] Pydantic API schemas (`models/api_schemas.py`) — all request/response models
- [ ] Domain dataclasses and event types (`models/domain.py`, `models/events.py`)
- [ ] Repository pattern persistence layer (`persistence/`)
- [ ] CI pipeline (lint, type-check, test)
- [ ] Frontend Vite + React + TypeScript skeleton with health check fetch

---

## Phase 2: Git & Job CRUD

> Worktree management, repository registration, job creation, state machine.

- [ ] `GitService` — worktree creation (main vs secondary), branch creation, cleanup
- [ ] Worktree creation error handling (catch failures, transition job to failed)
- [ ] Repository registration — add local repos by path, clone remote repos by URL via `git` subprocess
- [ ] Repository registration API (`POST /api/settings/repos`, `DELETE /api/settings/repos/{repo_path}`)
- [ ] Job CRUD API (`POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`)
- [ ] Job state machine (all transitions from §12.2)
- [ ] Repository allowlist validation
- [ ] Job cancel endpoint (`POST /api/jobs/{id}/cancel`)
- [ ] Job rerun endpoint (`POST /api/jobs/{id}/rerun`)
- [ ] Cursor-based pagination for job list
- [ ] Integration tests for git operations and concurrent worktrees

---

## Phase 3: Event Bus & SSE

> Internal pub/sub, SSE streaming, reconnection.

- [ ] Internal event bus (async in-process pub/sub)
- [ ] SSE endpoint (`GET /api/events`, `GET /api/events?job_id=`)
- [ ] SSE manager — connection tracking, broadcast, cleanup
- [ ] Event persistence subscriber (write all events to SQLite)
- [ ] Reconnection and event replay (`Last-Event-ID`, snapshot fallback)
- [ ] Replay bounds (max 500 events, 5-minute window)
- [ ] SSE scaling constraint — selective streaming beyond 20 jobs (§5.6)
- [ ] Frontend SSE client with exponential backoff reconnection
- [ ] Zustand store with central event dispatcher

---

## Phase 4: Agent Integration

> Adapter interface, Copilot SDK integration, runtime service.

- [ ] `AgentAdapterInterface` — abstract base class
- [ ] `FakeAgentAdapter` — test double emitting scripted events
- [ ] `CopilotAdapter` — wraps Python Copilot SDK
- [ ] Callback-to-iterator bridge (SDK callbacks → `AsyncIterator[SessionEvent]`)
- [ ] `SessionConfig` resolution from job + repo config
- [ ] `RuntimeService` — asyncio task management, capacity enforcement, queueing
- [ ] `ExecutionStrategy` interface + `SingleAgentExecutor`
- [ ] Strategy registry and selection
- [ ] Session heartbeat generation (30s interval, 90s warning, 5-min timeout)
- [ ] MCP server discovery (`.vscode/mcp.json` + global config merge)

---

## Phase 5: Frontend Core

> Dashboard, job detail, job creation screens.

- [ ] OpenAPI type generation pipeline (`openapi-typescript`)
- [ ] Type aliases (`src/api/types.ts`)
- [ ] API client module (REST calls)
- [ ] Dashboard — Kanban board (Active, Sign-off, Failed, History columns)
- [ ] Dashboard — mobile job list with filter tabs
- [ ] Responsive breakpoints (1024px, 768px)
- [ ] Job Detail screen — metadata header, state badge, timestamps
- [ ] Job Detail — transcript panel (virtualized, auto-scroll)
- [ ] Job Detail — logs panel (virtualized, level filtering)
- [ ] Job Detail — execution timeline (grouped by phase)
- [ ] Job Creation screen — repo selector, prompt input, advanced options
- [ ] Repository Detail view — resolved MCP/tool config table (local/global/disabled), repo config, active + recent jobs

---

## Phase 6: Diffs, Workspace & Artifacts

> Diff generation, file browsing, artifact collection.

- [ ] `DiffService` — run `git diff base_ref...HEAD`, parse unified diff
- [ ] Diff hunk parser (file status, hunk headers, line classification)
- [ ] Per-job diff throttling (5-second window)
- [ ] Final diff snapshot at job completion
- [ ] DiffViewer component (Monaco DiffEditor, file list, hunk navigation)
- [ ] Workspace browser API (`GET /api/jobs/{id}/workspace`, `/workspace/file`)
- [ ] Workspace browser component (`react-arborist` file tree)
- [ ] `ArtifactService` — collection from `.tower/artifacts/`, diff snapshots, agent summaries
- [ ] Artifact list + download endpoints
- [ ] Artifact viewer component

---

## Phase 7: Approvals & Operator Controls

> Approval routing, operator messages, protected paths.

- [ ] `ApprovalService` — persist requests, await resolution, route decisions to adapter
- [ ] Approval REST endpoints (`GET /api/jobs/{id}/approvals`, `POST /api/approvals/{id}/resolve`)
- [ ] Approval banner component (description, proposed action, approve/reject buttons)
- [ ] Concurrent approval notifications (toasts, badge on Sign-off tab)
- [ ] Operator message injection (`POST /api/jobs/{id}/messages` → `adapter.send_message`)
- [ ] Protected paths configuration → SDK permission rules
- [ ] Aging warning badge for approvals older than 30 minutes

---

## Phase 8: Voice Input

> Local transcription with faster-whisper.

- [ ] `VoiceService` — load model, transcribe audio bytes
- [ ] Voice endpoint (`POST /api/voice/transcribe`)
- [ ] Frontend audio capture (MediaRecorder, WebM/Opus)
- [ ] Voice input button component (press-and-hold)
- [ ] Client-side size limit enforcement
- [ ] "Local transcription" indicator in UI
- [ ] Voice config (`voice.enabled`, `voice.model`, `voice.max_audio_size_mb`)

---

## Phase 9: Operational Hardening

> Graceful shutdown, restart recovery, retention, remote access.

- [ ] Graceful shutdown (SIGTERM/SIGINT handler, cancel running jobs, close SSE)
- [ ] Restart recovery (transition orphaned running/waiting jobs to failed)
- [ ] Retention policy — artifact cleanup, worktree cleanup, daily background task
- [ ] Settings API (`GET/PUT /api/settings/global`, `GET /api/settings/repos`, `GET /api/settings/repos/{repo_path}`)
- [ ] Settings screen (global config editor, repo config list, cleanup action)
- [ ] Dev Tunnel integration (`tower up --tunnel`)
- [ ] Dynamic CORS for tunnel origin
- [ ] Startup warning for `0.0.0.0` binding
- [ ] `rich` terminal status display for `tower up`

---

## Phase 10: Post-Completion & Polish

> PR creation, performance, comprehensive testing.

- [ ] PR creation after successful job (GitHub MCP tools or `gh` CLI)
- [ ] PR URL in `JobSucceeded` payload and Job Detail screen
- [ ] Virtualized rendering for logs and transcript panels (`@tanstack/react-virtual`)
- [ ] Memoized Kanban column selectors (prevent full-board re-renders)
- [ ] Large diff lazy loading in Monaco
- [ ] Comprehensive unit tests (state machine, diff parser, config, approval logic)
- [ ] Integration tests (git service, concurrent jobs, approval flow, SSE replay, restart recovery)
- [ ] End-to-end tests (Playwright)

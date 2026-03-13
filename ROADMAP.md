# Tower — Implementation Roadmap

Work is broken into phases. Each phase produces a usable increment. Later phases build on earlier ones.

---

## Phase 1: Foundation

> Backend skeleton, database, domain models, dev tooling.

- [x] FastAPI app factory (`backend/main.py`) with health endpoint
- [x] CLI entry point (`tower up`, `tower init`, `tower version`) via Click
- [x] Global config loading and validation (`~/.tower/config.yaml`)
- [x] SQLAlchemy models and SQLite schema (`jobs`, `events`, `approvals`, `artifacts`, `diff_snapshots`)
- [x] Alembic migration setup and initial migration
- [x] Pydantic API schemas (`models/api_schemas.py`) — all request/response models
- [x] Domain dataclasses and event types (`models/domain.py`, `models/events.py`)
- [x] Repository pattern persistence layer (`persistence/`)
- [x] CI pipeline (lint, type-check, test)
- [x] Frontend Vite + React + TypeScript skeleton with health check fetch

---

## Phase 2: Git & Job CRUD

> Worktree management, repository registration, job creation, state machine.

- [x] `GitService` — worktree creation (main vs secondary), branch creation, cleanup
- [x] Worktree creation error handling (catch failures, transition job to failed)
- [x] Repository registration — add local repos by path, clone remote repos by URL via `git` subprocess
- [x] Repository registration API (`POST /api/settings/repos`, `DELETE /api/settings/repos/{repo_path}`)
- [x] Job CRUD API (`POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`)
- [x] Job state machine (all transitions from §12.2)
- [x] Repository allowlist validation
- [x] Job cancel endpoint (`POST /api/jobs/{id}/cancel`)
- [x] Job rerun endpoint (`POST /api/jobs/{id}/rerun`)
- [x] Cursor-based pagination for job list
- [x] Integration tests for git operations and concurrent worktrees

---

## Phase 3: Event Bus & SSE

> Internal pub/sub, SSE streaming, reconnection.

- [x] Internal event bus (async in-process pub/sub)
- [x] SSE endpoint (`GET /api/events`, `GET /api/events?job_id=`)
- [x] SSE manager — connection tracking, broadcast, cleanup
- [x] Event persistence subscriber (write all events to SQLite)
- [x] Reconnection and event replay (`Last-Event-ID`, snapshot fallback)
- [x] Replay bounds (max 500 events, 5-minute window)
- [x] SSE scaling constraint — selective streaming beyond 20 jobs (§5.6)
- [x] Frontend SSE client with exponential backoff reconnection
- [x] Zustand store with central event dispatcher

---

## Phase 4: Agent Integration

> Adapter interface, Copilot SDK integration, runtime service.

- [x] `AgentAdapterInterface` — abstract base class
- [x] `FakeAgentAdapter` — test double emitting scripted events
- [x] `CopilotAdapter` — wraps Python Copilot SDK
- [x] Callback-to-iterator bridge (SDK callbacks → `AsyncIterator[SessionEvent]`)
- [x] `SessionConfig` resolution from job + repo config
- [x] `RuntimeService` — asyncio task management, capacity enforcement, queueing
- [x] `ExecutionStrategy` interface + `SingleAgentExecutor`
- [x] Strategy registry and selection
- [x] Session heartbeat generation (30s interval, 90s warning, 5-min timeout)
- [x] MCP server discovery (`.vscode/mcp.json` + global config merge)

---

## Phase 5: Frontend Core

> Dashboard, job detail, job creation screens.

- [x] OpenAPI type generation pipeline (`openapi-typescript`)
- [x] Type aliases (`src/api/types.ts`)
- [x] API client module (REST calls)
- [x] Dashboard — Kanban board (Active, Sign-off, Failed, History columns)
- [x] Dashboard — mobile job list with filter tabs
- [x] Responsive breakpoints (1024px, 768px)
- [x] Job Detail screen — metadata header, state badge, timestamps
- [x] Job Detail — transcript panel (virtualized, auto-scroll)
- [x] Job Detail — logs panel (virtualized, level filtering)
- [x] Job Detail — execution timeline (grouped by phase)
- [x] Job Creation screen — repo selector, prompt input, advanced options
- [x] Repository Detail view — resolved MCP/tool config table (local/global/disabled), repo config, active + recent jobs

---

## Phase 6: Diffs, Workspace & Artifacts

> Diff generation, file browsing, artifact collection.

- [x] `DiffService` — run `git diff base_ref...HEAD`, parse unified diff
- [x] Diff hunk parser (file status, hunk headers, line classification)
- [x] Per-job diff throttling (5-second window)
- [x] Final diff snapshot at job completion
- [x] DiffViewer component (Monaco DiffEditor, file list, hunk navigation)
- [x] Workspace browser API (`GET /api/jobs/{id}/workspace`, `/workspace/file`)
- [x] Workspace browser component (`react-arborist` file tree)
- [x] `ArtifactService` — collection from `.tower/artifacts/`, diff snapshots, agent summaries
- [x] Artifact list + download endpoints
- [x] Artifact viewer component

---

## Phase 7: Approvals & Operator Controls

> Approval routing, operator messages, protected paths.

- [x] `ApprovalService` — persist requests, await resolution, route decisions to adapter
- [x] Approval REST endpoints (`GET /api/jobs/{id}/approvals`, `POST /api/approvals/{id}/resolve`)
- [x] Approval banner component (description, proposed action, approve/reject buttons)
- [x] Concurrent approval notifications (toasts, badge on Sign-off tab)
- [x] Operator message injection (`POST /api/jobs/{id}/messages` → `adapter.send_message`)
- [x] Protected paths configuration → SDK permission rules
- [x] Aging warning badge for approvals older than 30 minutes

---

## Phase 8: Voice Input

> Local transcription with faster-whisper.

- [x] `VoiceService` — load model, transcribe audio bytes
- [x] Voice endpoint (`POST /api/voice/transcribe`)
- [x] Frontend audio capture (MediaRecorder, WebM/Opus)
- [x] Voice input button component (press-and-hold)
- [x] Client-side size limit enforcement
- [x] "Local transcription" indicator in UI
- [x] Voice config (`voice.enabled`, `voice.model`, `voice.max_audio_size_mb`)

---

## Phase 9: Operational Hardening

> Graceful shutdown, restart recovery, retention, remote access.

- [x] Graceful shutdown (SIGTERM/SIGINT handler, cancel running jobs, close SSE)
- [x] Restart recovery (transition orphaned running/waiting jobs to failed)
- [x] Retention policy — artifact cleanup, worktree cleanup, daily background task
- [x] Settings API (`GET/PUT /api/settings/global`, `GET /api/settings/repos`, `GET /api/settings/repos/{repo_path}`)
- [x] Settings screen (global config editor, repo config list, cleanup action)
- [x] Dev Tunnel integration (`tower up --tunnel`)
- [x] Dynamic CORS for tunnel origin
- [x] Startup warning for `0.0.0.0` binding
- [x] `rich` terminal status display for `tower up`

---

## Phase 10: Post-Completion & Polish

> PR creation, performance, comprehensive testing.

- [x] PR creation after successful job (GitHub MCP tools or `gh` CLI)
- [x] PR URL in `JobSucceeded` payload and Job Detail screen
- [x] Virtualized rendering for logs and transcript panels (`@tanstack/react-virtual`)
- [x] Memoized Kanban column selectors (prevent full-board re-renders)
- [x] Large diff lazy loading in Monaco

---

## Phase 11: MCP Orchestration Server

> Expose Tower's full functionality as an MCP server so external agents can use it as an orchestration layer.

- [x] MCP server transport — Streamable HTTP on `/mcp`, mounted in FastAPI app
- [x] MCP tool handlers for Job management (`tower_job_create`, `tower_job_list`, `tower_job_get`, `tower_job_cancel`, `tower_job_rerun`, `tower_job_message`)
- [x] MCP tool handlers for Approvals (`tower_approval_list`, `tower_approval_resolve`)
- [x] MCP tool handlers for Workspace & Artifacts (`tower_workspace_list`, `tower_workspace_read`, `tower_artifact_list`, `tower_artifact_get`)
- [x] MCP tool handlers for Configuration (`tower_settings_get`, `tower_settings_update`, `tower_repo_list`, `tower_repo_get`, `tower_repo_register`, `tower_repo_remove`)
- [x] MCP tool handlers for Observability (`tower_health`, `tower_cleanup_worktrees`)
- [x] Server-to-client notifications via event bus subscription (`tower/job_state_changed`, `tower/approval_requested`, `tower/job_completed`, `tower/agent_message`)
- [x] Schema derivation from existing Pydantic models
- [x] MCP server configuration (`mcp_server.enabled`, `mcp_server.path`)
- [x] Dev Tunnel authentication for remote MCP connections
- [x] AGENT_TOWER_HOME env var support for custom data directory
- [x] `tower setup` interactive onboarding CLI (deps check, auth, devtunnel, config)
- [x] `tower doctor` non-interactive dependency check
- [x] WSL-aware headless auth flows for gh CLI and devtunnel
- [x] Integration tests for MCP tool calls end-to-end
- [x] Comprehensive unit tests (state machine, diff parser, config, approval logic)
- [x] Integration tests (git service, concurrent jobs, approval flow, SSE replay, restart recovery)
- [ ] End-to-end tests (Playwright)
